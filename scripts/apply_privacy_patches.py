#!/usr/bin/env python3
"""
Apply privacy patches to a decompiled GameHub apktool tree. Supports stock
6.0.9 only.

6.0.7 vs 6.0.4: R8 letters were fully regenerated, .line debug directives are
stripped from app code (anchors are instruction-only), and the dex count
dropped 6->5 so several classes moved smali_classesN dirs. Notable structural
drift handled below: the heartbeat start+update lambdas were FUSED into one
class (Lfeo;+Lheo; -> Lzco;, one stub kills both); the app class was renamed
(BaseAndroidApp -> com/xiaoji/egggame/AndroidApp, a() -> b()); the OTA URL is
now assembled at runtime instead of a single literal.

Port of the bannerhub-revanced privacy patch set, translated from ReVanced
Kotlin/dexlib2 to apktool-tree text edits to fit this fork's Python+apktool
pipeline. The honest list of channels killed (and the ones knowingly left
in place) is mirrored from upstream PRIVACY.md so anyone running a DNS
recorder against the build can verify both halves.

What this kills
---------------

  Firebase Analytics (manifest meta-data kill switch)
    Adds firebase_analytics_collection_deactivated=true plus AD-ID/SSAID
    disables to <application>. Firebase SDK never initialises so no events
    reach app-measurement.com.

  Google Play Services Measurement (manifest android:enabled=false)
    Flips the three AppMeasurement* components off. GMS Measurement runs
    independently of Firebase Analytics and is unaffected by the meta-data
    kill switch above, so this complements it.

  Ad-ID permissions (manifest <uses-permission> strip)
    Removes the three declarations (AD_ID + the two AdServices perms) so
    privacy scanners don't flag the build as trackers-permission-requesting,
    and an OS-level permission audit no longer reports ad-tracking intent.

  Mob Push SDK (manifest android:enabled=false + bytecode init removal)
    Strips two invoke-statics in BaseAndroidApp.a() (the policy-grant gate
    + addPushReceiverInMain) and one in the obfuscated config helper
    nt5.N(Context)V (second policy-grant). Manifest layer flips every
    com.mob.* / cn.fly.* provider/service/receiver/activity to disabled,
    so Mob's ContentProvider auto-init can't fire even before the bytecode
    paths would.

  XiaoJi heartbeat / playtime tracker (bytecode stubs)
    The SuspendLambdas that POST to heartbeat/game/{start,update,end}
    and the GET that reads heartbeat/game/getUserPlayTimeList are stubbed
    to return Unit.INSTANCE / an empty wrapped list. In 6.0.7 R8 fused the
    start and update lambdas into a single class, so this is now 3 stubs
    (start+update merged, end, playtime) not 4. UX trade-off: the
    in-app playtime UI renders empty. Steam's own playtime on your Steam
    profile is unaffected (Steam tracks playtime independently via the
    Steam client running inside Wine).

  statistic-gamehub-api.vgabc.com /events + /events/device-performance-config
    (bytecode stubs) — the two public entry points (cx5.a, oh4.b) early-
    return synthetic success instances. Zero coroutine state machine, zero
    URL allocation, zero HTTP, zero radio wake.

  XiaoJi OTA URL (bytecode register overwrite)
    In 6.0.7 the firmware-update URL is assembled at runtime
    ("https://" + host + "/firmware/update/x1" via a String-concat helper)
    rather than a single literal, and the host is branch-selected
    (www.xiaoji.com / ota-test.xiaoji.com). We overwrite the assembled-URL
    register with "http://127.0.0.1" right after the concat, so the HTTP
    client fails with connection-refused regardless of which host branch
    was taken. JieLi gamepad-firmware native libs are also stripped from
    lib/*/.

What this deliberately doesn't touch
------------------------------------

  Firebase Crashlytics — the SDK settings-config probe to
    firebase-settings.crashlytics.com still fires on startup (vestigial
    init); upstream PRIVACY.md notes this is partial-gain-only and not
    worth the bytecode work in 6.0.4.
  Steam, GOG, Epic, anti-cheat — all out of scope. They run inside Wine
    and talk to their own vendors over their own network plane.
  bigeyes.com / steamstatic CDN — image-only fetches, no PII payload.
"""
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Patch primitives
# ---------------------------------------------------------------------------

def patch(path, old, new, label):
    """Apply a single text-level smali edit. Fails fast if the anchor is
    not present, so a future base bump that reshuffles the anchor is
    surfaced loudly instead of silently shipping unpatched code."""
    p = Path(path)
    try:
        content = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = p.read_text(encoding="latin-1")
    if old not in content:
        print(f"ERROR: anchor not found in {path} for: {label}", file=sys.stderr)
        sys.exit(1)
    # newline="" forces LF on every platform (Windows write_text() otherwise
    # translates \n -> \r\n; harmless for smali but corrupts the LF-delimited
    # .cvr base64 bundles, so keep all writes LF).
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(content.replace(old, new, 1))
    print(f"OK: {label}")


def read(path):
    p = Path(path)
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return p.read_text(encoding="latin-1")


def write(path, content):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Version detection (shared probe shape with apply_vibration_patches.py)
# ---------------------------------------------------------------------------

# See apply_vibration_patches.py for why the 6.0.4 ab8/bg5 probe is unusable
# on 6.0.7 (letters reused). Anchor on the renamed app class instead.
VERSION_PROBES = {
    "6.0.9": (
        "smali_classes3/com/xiaoji/egggame/AndroidApp.smali",
        "smali_classes3/com/winemu/core/gamepad/GamepadServerManager.smali",
    ),
}


def detect_version(root: Path) -> str:
    matches = [
        ver
        for ver, probes in VERSION_PROBES.items()
        if all((root / p).is_file() for p in probes)
    ]
    if not matches:
        print(
            "ERROR: could not detect GameHub version — none of the known "
            "smali layout probes matched.",
            file=sys.stderr,
        )
        sys.exit(1)
    if len(matches) > 1:
        print(f"ERROR: ambiguous version match: {matches}", file=sys.stderr)
        sys.exit(1)
    return matches[0]


# ---------------------------------------------------------------------------
# Manifest patches
# ---------------------------------------------------------------------------

FIREBASE_FLAGS = [
    # Firebase's documented kill switch — stops Analytics SDK init entirely
    # (no session_start / screen_view / first_open / app_update /
    # in_app_purchase auto-collection, no custom events reach
    # app-measurement.com).
    ("firebase_analytics_collection_deactivated", "true"),
    # Belt-and-braces: even if the flag above is ignored or its semantics
    # shift in a future Firebase SDK version, also force-disable Google
    # Ads ID collection.
    ("google_analytics_adid_collection_enabled", "false"),
    # Disable Analytics SSAID (Settings.Secure.ANDROID_ID) collection too.
    ("google_analytics_ssaid_collection_enabled", "false"),
]

GMS_MEASUREMENT_COMPONENTS = [
    "com.google.android.gms.measurement.AppMeasurementReceiver",
    "com.google.android.gms.measurement.AppMeasurementService",
    "com.google.android.gms.measurement.AppMeasurementJobService",
]

AD_ID_PERMISSIONS = [
    "com.google.android.gms.permission.AD_ID",
    "android.permission.ACCESS_ADSERVICES_ATTRIBUTION",
    "android.permission.ACCESS_ADSERVICES_AD_ID",
]


def _split_attrs(tag: str) -> dict:
    """Extract attribute name → value from a single XML tag string. Order-
    preserving via Python dict semantics (3.7+). Not a full XML parser —
    apktool's manifest is always single-line tags with simple attributes,
    so a regex pass is sufficient and avoids pulling in lxml just for two
    flips."""
    out = {}
    for m in re.finditer(r'(\w+:\w+)="([^"]*)"', tag):
        out[m.group(1)] = m.group(2)
    return out


def _has_mob_namespace(name: str) -> bool:
    return name.startswith("com.mob.") or name.startswith("cn.fly.")


def patch_manifest(manifest_path: Path) -> None:
    src = read(manifest_path)

    # 1. Strip Ad-ID permission declarations. Match the whole line so the
    #    surrounding indentation+newline goes too.
    for perm in AD_ID_PERMISSIONS:
        pattern = re.compile(
            r'\s*<uses-permission android:name="'
            + re.escape(perm)
            + r'"\s*/>',
        )
        n_before = len(src)
        src = pattern.sub("", src, count=1)
        if len(src) == n_before:
            print(
                f"WARN: ad-id permission not present (already stripped?): {perm}",
                file=sys.stderr,
            )
        else:
            print(f"OK: stripped uses-permission {perm}")

    # 2. Inject Firebase kill-switch meta-data right before </application>.
    #    Skip individual entries already present (idempotent for repeat
    #    apktool->patch->apktool runs).
    insertions = []
    for name, value in FIREBASE_FLAGS:
        already = re.search(
            r'<meta-data android:name="' + re.escape(name) + r'"',
            src,
        )
        if already:
            print(f"OK: firebase flag already present: {name}")
            continue
        insertions.append(
            f'        <meta-data android:name="{name}" android:value="{value}"/>'
        )
    if insertions:
        block = "\n".join(insertions) + "\n    </application>"
        if "    </application>" not in src:
            print("ERROR: could not find </application> close tag", file=sys.stderr)
            sys.exit(1)
        src = src.replace("    </application>", block, 1)
        for name, _ in FIREBASE_FLAGS:
            print(f"OK: injected firebase flag {name}")

    # 3. Flip GMS Measurement components to disabled. They're driven by
    #    GMS's own service registration (bound service + broadcast
    #    receiver, not ContentProvider auto-init), so a manifest disable
    #    is sufficient — GMS respects android:enabled="false" when other
    #    GMS code queries component registration via PackageManager.
    for fqcn in GMS_MEASUREMENT_COMPONENTS:
        pattern = re.compile(
            r'(<(?:receiver|service)\s+)(android:enabled="true"\s+)?'
            r'(android:exported="false"\s+android:name="'
            + re.escape(fqcn)
            + r'"[^/]*?)(/>)',
        )

        def _disable(m):
            head, _enabled, body, close = m.group(1), m.group(2), m.group(3), m.group(4)
            return f'{head}android:enabled="false" {body}{close}'

        new_src, n = pattern.subn(_disable, src, count=1)
        if n:
            src = new_src
            print(f"OK: disabled GMS measurement {fqcn}")
        else:
            print(f"WARN: GMS measurement component not found: {fqcn}", file=sys.stderr)

    # 4. Mob neutralisation in the manifest. Two passes:
    #    (a) flip android:enabled="false" on every provider/service/
    #        receiver/activity whose android:name starts with com.mob. or
    #        cn.fly.; (b) remove <meta-data> entries in the same namespaces
    #        outright (meta-data has no enabled attribute).
    #
    # The match is line-by-line because apktool emits each tag on its own
    # line. Re-emit with the same indentation the line came in with.
    out_lines = []
    skipped_meta = 0
    flipped = 0
    for line in src.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("<meta-data"):
            attrs = _split_attrs(stripped)
            if _has_mob_namespace(attrs.get("android:name", "")):
                skipped_meta += 1
                continue
        for tag in ("provider", "service", "receiver", "activity"):
            if stripped.startswith(f"<{tag} ") or stripped.startswith(f"<{tag}\n"):
                attrs = _split_attrs(stripped)
                if _has_mob_namespace(attrs.get("android:name", "")):
                    if attrs.get("android:enabled") == "false":
                        break  # already disabled
                    if 'android:enabled="' in line:
                        line = re.sub(
                            r'android:enabled="[^"]*"',
                            'android:enabled="false"',
                            line,
                            count=1,
                        )
                    else:
                        # Insert android:enabled="false" right after the
                        # opening tag name. Avoid touching attribute order
                        # of anything else; apktool re-emits in the same
                        # order on rebuild.
                        line = re.sub(
                            r'^(\s*<' + tag + r')(\s)',
                            r'\1 android:enabled="false"\2',
                            line,
                            count=1,
                        )
                    flipped += 1
                break
        out_lines.append(line)
    src = "\n".join(out_lines)
    if flipped:
        print(f"OK: disabled {flipped} Mob/cn.fly manifest components")
    if skipped_meta:
        print(f"OK: removed {skipped_meta} Mob/cn.fly <meta-data> entries")

    write(manifest_path, src)


# ---------------------------------------------------------------------------
# Native lib strip (JieLi gamepad firmware — vendor-fingerprint dead weight
# on a phone install).
# ---------------------------------------------------------------------------

JIELI_NATIVE_LIBS = ("libJieLiUsbOta.so", "libjl_ota_auth.so")


def strip_native_libs(root: Path) -> None:
    lib_dir = root / "lib"
    if not lib_dir.is_dir():
        print("OK: no lib/ dir (nothing to strip)")
        return
    removed = 0
    for arch_dir in lib_dir.iterdir():
        if not arch_dir.is_dir():
            continue
        for libname in JIELI_NATIVE_LIBS:
            target = arch_dir / libname
            if target.is_file():
                target.unlink()
                removed += 1
                print(f"OK: removed lib/{arch_dir.name}/{libname}")
    if not removed:
        print("OK: JieLi native libs already absent")


# ---------------------------------------------------------------------------
# Smali patches — pure-stub neutralization shapes
# ---------------------------------------------------------------------------

# Unit.INSTANCE prepend — short-circuits a SuspendLambda's invokeSuspend
# at index 0 so the coroutine state machine never runs. The original method
# body remains as unreachable dead code (the verifier walks reachable code
# from the entry point only). Every heartbeat invokeSuspend has .locals >= 5,
# so v0 is always safe to reuse.
UNIT_RETURN_PREPEND = (
    "    # BH: privacy patch — short-circuit heartbeat lambda. Return the\n"
    "    # host's kotlin.Unit singleton: R8 renamed kotlin.Unit to Lx6m; on\n"
    "    # 6.0.7 (its INSTANCE field is Lx6m;->a:Lx6m;), so a literal\n"
    "    # Lkotlin/Unit;->INSTANCE does NOT resolve at runtime and throws\n"
    "    # NoClassDefFoundError. The original invokeSuspend returns this same\n"
    "    # Lx6m;->a on its normal completion path.\n"
    "    sget-object v0, Lx6m;->a:Lx6m;\n"
    "    return-object v0\n"
    "\n"
)

# Synthetic Lh76 success (6.0.4 Lyw5) — 4-field data class (Z, Integer,
# String, Throwable) + int default-mask. Constructor takes 6 args including
# the implicit `this`, which exceeds the 5-register cap of invoke-direct
# (format 35c), so we use invoke-direct/range. (35c silently truncates
# at assembly time without flagging an error in some baksmali builds —
# bannerhub-revanced hit this exact pitfall on first attempt.) The 6.0.7
# ctor signature is byte-for-byte identical to 6.0.4 Lyw5.
VY5_SUCCESS_PREPEND = (
    "    # BH: privacy patch — early-return synthetic success before any\n"
    "    # URL string is allocated or HTTP client is touched.\n"
    "    new-instance v0, Lh76;\n"
    "    const/4 v1, 0x1\n"
    "    const/4 v2, 0x0\n"
    "    const/4 v3, 0x0\n"
    "    const/4 v4, 0x0\n"
    "    const/4 v5, 0x0\n"
    "    invoke-direct/range {v0 .. v5}, Lh76;-><init>(ZLjava/lang/Integer;"
    "Ljava/lang/String;Ljava/lang/Throwable;I)V\n"
    "    return-object v0\n"
    "\n"
)

# Synthetic Lk9m (6.0.4 Lxnm) — 2-field data class (I, Set). Caller does
# check-cast Lk9m; on the result, so the concrete return type matters. The
# 6.0.7 ctor signature is identical to 6.0.4 Lxnm.
J0L_EMPTY_PREPEND = (
    "    # BH: privacy patch — early-return empty perf-config snapshot.\n"
    "    new-instance v0, Lk9m;\n"
    "    const/4 v1, 0x0\n"
    "    new-instance v2, Ljava/util/LinkedHashSet;\n"
    "    invoke-direct {v2}, Ljava/util/LinkedHashSet;-><init>()V\n"
    "    invoke-direct {v0, v1, v2}, Lk9m;-><init>(ILjava/util/LinkedHashSet;)V\n"
    "    return-object v0\n"
    "\n"
)

# Synthetic Lyi5(empty ArrayList) (6.0.4 Ln55) — getUserPlayTimeList returns
# an Either.Right-style wrapper around the playtime list (super = the sealed
# base Lpa5;, the caller check-casts to it). The UI iterator runs zero passes
# on the empty list instead of crashing. ctor (Ljava/lang/Object;)V identical
# to 6.0.4 Ln55.
OA5_EMPTY_PREPEND = (
    "    # BH: privacy patch — return empty playtime list wrapper.\n"
    "    new-instance v0, Ljava/util/ArrayList;\n"
    "    invoke-direct {v0}, Ljava/util/ArrayList;-><init>()V\n"
    "    new-instance v1, Lyi5;\n"
    "    invoke-direct {v1, v0}, Lyi5;-><init>(Ljava/lang/Object;)V\n"
    "    return-object v1\n"
    "\n"
)


def patch_heartbeat(root: Path) -> None:
    """Stub the heartbeat POST lambdas + getUserPlayTimeList. In 6.0.7 R8
    FUSED the start and update lambdas into one class Lzco; (a single
    invokeSuspend dispatches both via a packed-switch on the synthetic
    instance field a:I), so an index-0 Unit early-return on zco.invokeSuspend
    kills BOTH start and update. End is the separate lambda Lvco;.
    getUserPlayTimeList moved to Ljk7;->c(Lpv3;). The string anchors
    (heartbeat/game/...) pinned each class even though R8 reshuffled the
    letters and dex dirs; anchors are .line-free (6.0.7 strips them).

    DELIBERATELY NOT STUBBED: Ln10 also contains the literal
    "heartbeat/game/start", but it is a constant-provider lambda that merely
    RETURNS the URL/key string (no HTTP); stubbing it would corrupt unrelated
    URL/serializer resolution app-wide."""
    # zco.invokeSuspend — heartbeat/game/start AND heartbeat/game/update
    # (merged). The Unit early-return short-circuits the packed-switch before
    # either branch's POST helper (Lzua;->e) runs.
    patch(
        root / "smali_classes3/zco.smali",
        ".method public final invokeSuspend(Ljava/lang/Object;)Ljava/lang/Object;\n"
        "    .locals 9\n"
        "\n"
        "    iget v0, p0, Lzco;->a:I\n",
        ".method public final invokeSuspend(Ljava/lang/Object;)Ljava/lang/Object;\n"
        "    .locals 9\n"
        "\n"
        + UNIT_RETURN_PREPEND
        + "    iget v0, p0, Lzco;->a:I\n",
        "zco.invokeSuspend: stub heartbeat/game/start + heartbeat/game/update",
    )
    # vco.invokeSuspend — heartbeat/game/end
    patch(
        root / "smali_classes3/vco.smali",
        ".method public final invokeSuspend(Ljava/lang/Object;)Ljava/lang/Object;\n"
        "    .locals 8\n"
        "\n"
        "    iget v0, p0, Lvco;->a:I\n",
        ".method public final invokeSuspend(Ljava/lang/Object;)Ljava/lang/Object;\n"
        "    .locals 8\n"
        "\n"
        + UNIT_RETURN_PREPEND
        + "    iget v0, p0, Lvco;->a:I\n",
        "vco.invokeSuspend: stub heartbeat/game/end",
    )
    # jk7.c — getUserPlayTimeList. Returns Lyi5(emptyList) so the UI
    # iterator runs zero passes instead of crashing (the caller check-casts
    # the result to the sealed base Lpa5;, so Unit would ClassCastException).
    patch(
        root / "smali_classes4/jk7.smali",
        ".method public final c(Lpv3;)Ljava/lang/Object;\n"
        "    .locals 17\n"
        "\n"
        "    move-object/from16 v0, p0\n",
        ".method public final c(Lpv3;)Ljava/lang/Object;\n"
        "    .locals 17\n"
        "\n"
        + OA5_EMPTY_PREPEND
        + "    move-object/from16 v0, p0\n",
        "jk7.c: stub heartbeat/game/getUserPlayTimeList",
    )


def patch_analytics_events(root: Path) -> None:
    """Stub Ll76;->a (general /events POST, 6.0.4 Lcx5;) and Lqv4;->b
    (perf-config POST, 6.0.4 Loh4;). Both anchor on the method header +
    .locals + the first instruction (move-object/from16 v0, p0); on a base
    bump the class letters reshuffle but the URL strings and signature
    shapes do not, so failure surfaces loudly. Continuation Lci3; -> Lpv3;
    in 6.0.7; anchors are .line-free."""
    # l76.a — /events. Caller does check-cast Lh76; (6.0.4 Lyw5) on the
    # result, so we early-return a synthetic success of that type.
    patch(
        root / "smali_classes3/l76.smali",
        ".method public final a(Ljava/util/Collection;Lpv3;)Ljava/lang/Object;\n"
        "    .locals 27\n"
        "\n"
        "    move-object/from16 v0, p0\n",
        ".method public final a(Ljava/util/Collection;Lpv3;)Ljava/lang/Object;\n"
        "    .locals 27\n"
        "\n"
        + VY5_SUCCESS_PREPEND
        + "    move-object/from16 v0, p0\n",
        "l76.a: stub statistic-gamehub-api/events",
    )
    # qv4.b — /events/device-performance-config. The URL string itself lives
    # in the perf-config lambda body (Lb34;), but stubbing here at the outer
    # public method is safer: callers do check-cast Lk9m; (6.0.4 Lxnm) on the
    # result, so we must return a concrete Lk9m;. qv4.b is the outer retry
    # loop that delegates the real POST to qv4.c; returning a synthetic Lk9m;
    # short-circuits before qv4.c is reached.
    patch(
        root / "smali_classes4/qv4.smali",
        ".method public final b(IJLpv3;)Ljava/lang/Object;\n"
        "    .locals 26\n"
        "\n"
        "    move-object/from16 v0, p0\n",
        ".method public final b(IJLpv3;)Ljava/lang/Object;\n"
        "    .locals 26\n"
        "\n"
        + J0L_EMPTY_PREPEND
        + "    move-object/from16 v0, p0\n",
        "qv4.b: stub statistic-gamehub-api/events/device-performance-config",
    )


def patch_ota_url(root: Path) -> None:
    """Overwrite the assembled OTA firmware-update URL with loopback so the
    HTTP client fails with connection-refused. In 6.0.7 the URL is no longer
    a single literal: Lmw4;->d assembles it via
    Llu2;->q("https://", host, "/firmware/update/x1") where host is branch-
    selected (www.xiaoji.com / ota-test.xiaoji.com). We overwrite the
    move-result-object register that holds the FINAL assembled URL (p2),
    which catches both host branches with one injection and mirrors the
    6.0.4 'overwrite the URL register' semantics. The Llu2;->q concat call
    is unique within the file, so the anchor is unambiguous; it is .line-free."""
    patch(
        root / "smali_classes3/mw4.smali",
        '    invoke-static {v1, p2, v8}, Llu2;->q(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;\n'
        '\n'
        '    move-result-object p2\n',
        '    invoke-static {v1, p2, v8}, Llu2;->q(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;\n'
        '\n'
        '    move-result-object p2\n'
        '\n'
        '    # BH: privacy patch — overwrite the assembled OTA URL with loopback\n'
        '    # so the firmware-update phone-home fails with connection-refused.\n'
        '    const-string p2, "http://127.0.0.1"\n',
        'mw4.d: overwrite assembled OTA URL register with http://127.0.0.1',
    )


def patch_mob_bytecode(root: Path) -> None:
    """Remove the three Mob init invokes that XiaoJi's bootstrap code
    fires before onCreate would otherwise reach steady state. Manifest
    layer already disables Mob's ContentProvider auto-init; this layer
    removes the call sites that would have fired in code if the SDK had
    bootstrapped some other way.

    Downstream calls in the helper method (setClickNotificationToLaunchMainActivity,
    getRegistrationId, restartPush) are intentionally LEFT in place.
    Without the policy grant the SDK stays dormant and these calls
    either no-op or throw an NPE that the existing try/catchall around
    restartPush already catches — surgically removing them would break
    the try-label structure."""
    # AndroidApp.b() — first policy-grant invoke (6.0.4 BaseAndroidApp.a()).
    # The const/4 v2, 0x1 setting up the call's arg stays — it's used by
    # later code in the method too. Anchor on the unique Lcom/mob/MobSDK;->init
    # call immediately above (one init in the file); .line-free in 6.0.7.
    patch(
        root / "smali_classes3/com/xiaoji/egggame/AndroidApp.smali",
        "    invoke-static {p0}, Lcom/mob/MobSDK;->init(Landroid/content/Context;)V\n"
        "\n"
        "    const/4 v2, 0x1\n"
        "\n"
        "    invoke-static {v2}, Lcom/mob/MobSDK;->submitPolicyGrantResult(Z)V\n"
        "\n"
        "    sget-boolean v3, Ldy8;->d:Z\n",
        "    invoke-static {p0}, Lcom/mob/MobSDK;->init(Landroid/content/Context;)V\n"
        "\n"
        "    const/4 v2, 0x1\n"
        "\n"
        "    # BH: privacy patch — Mob policy-grant invoke removed.\n"
        "\n"
        "    sget-boolean v3, Ldy8;->d:Z\n",
        "AndroidApp.b: strip MobSDK.submitPolicyGrantResult",
    )

    # AndroidApp.b() — addPushReceiverInMain invoke, interior to a
    # :try_start_0 .. :try_end_0/:catchall_0 block. Void-returning, no
    # move-result, and strictly interior (the try-boundary labels are not
    # adjacent), so removal is label-safe.
    patch(
        root / "smali_classes3/com/xiaoji/egggame/AndroidApp.smali",
        "    move-result-object p0\n"
        "\n"
        "    invoke-static {p0, v1}, Lcom/mob/pushsdk/MobPush;->"
        "addPushReceiverInMain(Landroid/content/Context;"
        "Lcom/mob/pushsdk/MobPushReceiver;)V\n"
        "\n"
        "    sput-boolean v2, Lbm0;->b:Z\n",
        "    move-result-object p0\n"
        "\n"
        "    # BH: privacy patch — Mob addPushReceiverInMain invoke removed.\n"
        "\n"
        "    sput-boolean v2, Lbm0;->b:Z\n",
        "AndroidApp.b: strip MobPush.addPushReceiverInMain",
    )

    # dy8.D(Context)V — second policy-grant invoke (6.0.4 nt5.N(Context)V).
    # Anchor on the submitPolicyGrantResult/setClickNotification pair (the
    # combination is unique to D() within dy8.smali), in a non-try branch
    # after :cond_5, so removal is label-safe. The const/4 v2, 0x0 feeds the
    # setClickNotification call and stays.
    patch(
        root / "smali/dy8.smali",
        "    const/4 p0, 0x1\n"
        "\n"
        "    invoke-static {p0}, Lcom/mob/MobSDK;->submitPolicyGrantResult(Z)V\n"
        "\n"
        "    const/4 v2, 0x0\n"
        "\n"
        "    invoke-static {v2}, Lcom/mob/pushsdk/MobPush;->setClickNotificationToLaunchMainActivity(Z)V\n",
        "    const/4 p0, 0x1\n"
        "\n"
        "    # BH: privacy patch — Mob policy-grant invoke removed.\n"
        "\n"
        "    const/4 v2, 0x0\n"
        "\n"
        "    invoke-static {v2}, Lcom/mob/pushsdk/MobPush;->setClickNotificationToLaunchMainActivity(Z)V\n",
        "dy8.D: strip MobSDK.submitPolicyGrantResult",
    )


def patch_firebase_autoinit(root: Path) -> None:
    """Kill the runtime Firebase/Crashlytics data-collection RE-ENABLE.

    The manifest already ships firebase_*_collection flags = false, but
    AndroidApp.a()V (the Firebase bootstrap, anchored on the kept string
    "FirebaseCrashlytics component is not present.") re-enables collection at
    runtime: after initialising FirebaseApp it enters a monitor-guarded block
    that writes firebase_data_collection_default_enabled=true (and the
    Crashlytics equivalents) into the SDK's SharedPreferences, overriding the
    manifest. We inject a return-void immediately AFTER FirebaseApp init but
    BEFORE that monitor-enter, so init still completes (no crash) and the
    re-enable never runs. Mirrors upstream bannerhub-revanced
    DisableFirebaseAutoInitPatch.

    Anchor: the first `monitor-enter p0` in a()V, reached right after
    `check-cast p0, La84;` (the FirebaseApp data-collection state holder). The
    check-cast is unique within AndroidApp.smali; La84; is an R8 letter that
    will shift on a base bump, so this fails loudly if it moves."""
    patch(
        root / "smali_classes3/com/xiaoji/egggame/AndroidApp.smali",
        "    check-cast p0, La84;\n"
        "\n"
        "    monitor-enter p0\n",
        "    check-cast p0, La84;\n"
        "\n"
        "    # BH: privacy patch — Firebase/Crashlytics auto-init kill. Return\n"
        "    # after FirebaseApp init but before the monitor-guarded block that\n"
        "    # writes firebase_data_collection_default_enabled=true into the SDK\n"
        "    # SharedPreferences, which would override the manifest false flags.\n"
        "    return-void\n"
        "\n"
        "    monitor-enter p0\n",
        "AndroidApp.a: kill Firebase/Crashlytics runtime auto-init re-enable",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    root = Path(sys.argv[1])
    if not root.is_dir():
        print(f"ERROR: {root} is not a directory", file=sys.stderr)
        sys.exit(2)

    version = detect_version(root)
    print(f"Detected GameHub base version: {version}")
    print()

    print("=== Manifest ===")
    patch_manifest(root / "AndroidManifest.xml")
    print()

    print("=== Native libs ===")
    strip_native_libs(root)
    print()

    print("=== Heartbeat ===")
    patch_heartbeat(root)
    print()

    print("=== Analytics events ===")
    patch_analytics_events(root)
    print()

    print("=== OTA URL ===")
    patch_ota_url(root)
    print()

    print("=== Mob bytecode ===")
    patch_mob_bytecode(root)
    print()

    print("=== Firebase auto-init ===")
    patch_firebase_autoinit(root)
    print()

    print("All privacy patches applied successfully.")


if __name__ == "__main__":
    main()
