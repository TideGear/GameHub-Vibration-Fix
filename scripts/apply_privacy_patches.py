#!/usr/bin/env python3
"""
Apply privacy patches to a decompiled GameHub apktool tree. Supports stock
6.1.1 only.

6.0.9 -> 6.1.1 is the largest drift this script has absorbed. Beyond the usual
R8 re-lettering and dex-count change (5 -> 4, so classes moved buckets again):

  * `.line` debug directives are BACK in app code (6.0.7-6.0.9 stripped them).
    Every multi-instruction exact-text anchor the old script used would fail on
    the interleaved `.line N` lines, so index-0 stubs now go in via
    prepend_to_method() (locate the method, insert after `.locals`) and invoke
    removals via remove_invoke_in_method() (locate by callee inside a method).
    Neither bakes a line number or a register into an anchor.
  * the network layer was rebuilt around repository suspend methods instead of
    per-endpoint SuspendLambdas, so the stubs return the repo's own Either
    success wrapper (Ldd7;) rather than a bare kotlin.Unit.
  * the XiaoJi heartbeat surface SHRANK: heartbeat/game/update and
    heartbeat/game/end no longer exist in the APK at all. Two stubs cover what
    took three on 6.0.9.
  * the /events/device-performance-config channel is GONE (6.0.9 had renamed it
    device-performance-session-summary); that stub is retired, not re-anchored.
  * kotlin.Unit is no longer obfuscated, so `Lkotlin/Unit;->INSTANCE` resolves
    (6.0.7-6.0.9 needed the R8 letter Lx6m;->a).
  * the app class kept its name but its methods shifted again
    (Firebase bootstrap a() -> b(); Mob bootstrap b() -> c()).

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
    Lvho;->a stubs the surviving heartbeat/game/start POST (returning the
    Ldd7;(Unit) success wrapper its own early-out path builds) and Lby9;->e
    stubs the heartbeat/game/getUserPlayTimeList GET (returning an empty
    wrapped list). heartbeat/game/update and heartbeat/game/end are absent
    from 6.1.1 entirely, so two stubs replace 6.0.9's three. UX trade-off:
    the in-app playtime UI renders empty. Steam's own playtime on your Steam
    profile is unaffected (Steam tracks playtime independently via the
    Steam client running inside Wine).

  statistic-gamehub-api.vgabc.com /events (bytecode stub)
    Ll88;->a early-returns a synthetic success instance. Zero coroutine
    state machine, zero URL allocation, zero HTTP, zero radio wake. The
    perf-config sibling endpoint no longer exists in 6.1.1.

  XiaoJi OTA URL (bytecode register overwrite)
    The firmware-update URL is assembled at runtime ("https://" + host + "/"
    + "firmware/update/x1" via a 4-arg join helper) rather than being a single
    literal, and the host is branch-selected (www.xiaoji.com /
    ota-test.xiaoji.com). We overwrite the assembled-URL register with
    "http://127.0.0.1" right after the join, so the HTTP client fails with
    connection-refused regardless of which host branch was taken. JieLi
    gamepad-firmware native libs are also stripped from lib/*/.

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


# 6.1.1 keeps `.line` debug directives in app code (6.0.7-6.0.9 stripped them),
# so the old style of anchor — method header + `.locals N` + a blank line + the
# first instruction, matched as one exact string — no longer matches: there is a
# `.line 1` in between. Rather than bake `.line` numbers into anchors (they
# shift on any upstream edit), locate the method by its header and insert right
# after the `.locals`/`.registers` directive.
REG_DIRECTIVE_RE = re.compile(r"^[ \t]*\.(?:locals|registers)[ \t]+\d+[ \t]*\n", re.M)


def prepend_to_method(path, header: str, body: str, label: str) -> None:
    """Insert `body` at instruction index 0 of the method whose header line is
    `header`. Fails loudly if the method or its register directive is missing,
    or if the header is non-unique."""
    p = Path(path)
    if not p.is_file():
        print(f"ERROR: {path} not found for: {label}", file=sys.stderr)
        sys.exit(1)
    src = read(p)
    count = src.count(header)
    if count == 0:
        print(f"ERROR: method not found for: {label}\n  header={header!r}\n"
              f"  in {path}", file=sys.stderr)
        sys.exit(1)
    if count != 1:
        print(f"ERROR: method header is non-unique ({count} matches) for: "
              f"{label} — refusing to guess.", file=sys.stderr)
        sys.exit(1)
    start = src.index(header)
    end = src.find("\n.end method", start)
    if end < 0:
        print(f"ERROR: unclosed method for: {label}", file=sys.stderr)
        sys.exit(1)
    reg = REG_DIRECTIVE_RE.search(src, start, end)
    if not reg:
        print(f"ERROR: no .locals/.registers directive for: {label}",
              file=sys.stderr)
        sys.exit(1)
    # Idempotency marker: our own leading "# BH:" comment, NOT the first
    # instruction. The stub bodies deliberately reuse instructions the host's
    # own success paths already contain (e.g. `sget-object v0,
    # Lkotlin/Unit;->INSTANCE` inside Lvho;->a), so keying on the first
    # instruction silently reports "already applied" and skips the patch.
    marker = next((ln for ln in body.splitlines()
                   if ln.strip().startswith("# BH")), None)
    if marker is None:
        print(f"ERROR: stub body for {label} has no '# BH' marker comment; "
              f"refusing to patch without an idempotency anchor.",
              file=sys.stderr)
        sys.exit(1)
    if marker in src[reg.end():end]:
        print(f"OK: {label} (already applied)")
        return
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(src[:reg.end()] + body + src[reg.end():])
    print(f"OK: {label}")


def write(path, content):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Version detection (shared probe shape with apply_vibration_patches.py)
# ---------------------------------------------------------------------------

# Same probe pair as the sibling scripts: the app class (which moved to
# smali_classes2 in 6.1.1) plus the PC-engine plugin host activity, which only
# exists from 6.1.1.
ANDROID_APP_SMALI = "smali_classes2/com/xiaoji/egggame/AndroidApp.smali"

VERSION_PROBES = {
    "6.1.1": (
        ANDROID_APP_SMALI,
        "smali/com/xiaoji/egggame/plugin/pcengine/host/PcEnginePluginHostActivity.smali",
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

# --- 6.1.1 result-wrapper types -------------------------------------------
# The network layer returns an Either-shaped pair, both extending Led7; with a
# single (Ljava/lang/Object;)V ctor:
#   Ldd7;  SUCCESS  (built where exceptionOrNull == null, and where the decoded
#                    payload is wrapped)
#   Lcd7;  FAILURE  (wraps the mapped error object)
# Verified by reading Lby9;->e: the decoded ArrayList is wrapped in Ldd7; and
# the Throwable path builds Lcd7;. Getting these backwards would make callers
# treat a stub as an error and surface a toast, so re-verify on a base bump.
# NOTE: includes the trailing ';' — do not append another when interpolating.
EITHER_SUCCESS = "Ldd7;"

# Heartbeat POST short-circuit. 6.1.1 collapsed the heartbeat surface: only
# heartbeat/game/start survives (as a Lazy<String> in Loe0;->a, consumed by
# Lvho;->a), and its repo method returns Ldd7;(kotlin.Unit.INSTANCE) on its own
# success paths — so returning exactly that is indistinguishable from a
# no-op-but-successful beat. 6.1.1 leaves kotlin.Unit unobfuscated, so the
# literal Lkotlin/Unit;->INSTANCE resolves (on 6.0.7-6.0.9 it was Lx6m;->a and
# a literal kotlin.Unit reference threw NoClassDefFoundError).
HEARTBEAT_UNIT_PREPEND = (
    "    # BH: privacy patch — short-circuit the heartbeat POST. Returns the\n"
    "    # same Ldd7;(Unit) success wrapper the method's own early-out path\n"
    "    # builds, so callers see a successful no-op beat and no HTTP happens.\n"
    "    sget-object v0, Lkotlin/Unit;->INSTANCE:Lkotlin/Unit;\n"
    f"    new-instance v1, {EITHER_SUCCESS}\n"
    f"    invoke-direct {{v1, v0}}, {EITHER_SUCCESS}-><init>(Ljava/lang/Object;)V\n"
    "    return-object v1\n"
    "\n"
)

# Synthetic Lh88 success (6.0.9 Lh76;, 6.0.4 Lyw5;) — 4-field data class
# (Z, Integer, String, Throwable) + int default-mask; the 6.1.1 ctor signature
# is byte-for-byte identical to 6.0.9's. Constructor takes 6 args including the
# implicit `this`, which exceeds the 5-register cap of invoke-direct (format
# 35c), so we use invoke-direct/range. (35c silently truncates at assembly time
# without flagging an error in some baksmali builds — bannerhub-revanced hit
# this exact pitfall on first attempt.)
EVENTS_SUCCESS_PREPEND = (
    "    # BH: privacy patch — early-return synthetic success before any\n"
    "    # URL string is allocated or HTTP client is touched.\n"
    "    new-instance v0, Lh88;\n"
    "    const/4 v1, 0x1\n"
    "    const/4 v2, 0x0\n"
    "    const/4 v3, 0x0\n"
    "    const/4 v4, 0x0\n"
    "    const/4 v5, 0x0\n"
    "    invoke-direct/range {v0 .. v5}, Lh88;-><init>(ZLjava/lang/Integer;"
    "Ljava/lang/String;Ljava/lang/Throwable;I)V\n"
    "    return-object v0\n"
    "\n"
)

# Empty playtime list. getUserPlayTimeList returns the Either success wrapper
# around the decoded ArrayList of entries, so an empty ArrayList makes the UI
# iterator run zero passes instead of crashing (6.0.9 Lyi5;, 6.0.4 Ln55;).
PLAYTIME_EMPTY_PREPEND = (
    "    # BH: privacy patch — return empty playtime list wrapper.\n"
    "    new-instance v0, Ljava/util/ArrayList;\n"
    "    invoke-direct {v0}, Ljava/util/ArrayList;-><init>()V\n"
    f"    new-instance v1, {EITHER_SUCCESS}\n"
    f"    invoke-direct {{v1, v0}}, {EITHER_SUCCESS}-><init>(Ljava/lang/Object;)V\n"
    "    return-object v1\n"
    "\n"
)


def patch_heartbeat(root: Path) -> None:
    """Stub the heartbeat POST + getUserPlayTimeList.

    6.1.1 SHRANK this surface. The heartbeat/game/{update,end} endpoints are
    gone from the APK entirely — those string literals do not appear anywhere —
    so where 6.0.9 needed three stubs (start+update fused in Lzco;, end in
    Lvco;, playtime in Ljk7;->c) 6.1.1 needs two:

      Lvho;->a(String, Continuation)   the surviving heartbeat/game/start POST
      Lby9;->e(Continuation)           heartbeat/game/getUserPlayTimeList

    The network layer also changed shape: these are ordinary repository suspend
    methods now, not compiler-generated SuspendLambdas, so we early-return the
    repo's own success wrapper instead of a bare Unit.

    DELIBERATELY NOT STUBBED: Ld80;->invoke() holds the literal
    "heartbeat/game/start", but it is a merged constant/endpoint provider whose
    packed-switch also serves Compose composition locals and kotlinx
    serializers app-wide (:pswitch_e is the heartbeat case). Stubbing it would
    corrupt unrelated resolution — same trap 6.0.9 documented for Ln10. We
    stub its CONSUMER (Lvho;->a) instead, which is why that consumer was worth
    tracing through Loe0;->a rather than pattern-matching on the URL string."""
    # vho.a — the heartbeat/game/start POST.
    prepend_to_method(
        root / "smali_classes3/vho.smali",
        ".method public final a(Ljava/lang/String;"
        "Lkotlin/coroutines/jvm/internal/ContinuationImpl;)Ljava/lang/Object;\n",
        HEARTBEAT_UNIT_PREPEND,
        "vho.a: stub heartbeat/game/start POST",
    )
    # by9.e — getUserPlayTimeList. Returns the Either success wrapper around an
    # empty ArrayList so the UI iterator runs zero passes instead of crashing
    # (a bare Unit here would ClassCastException in the caller).
    prepend_to_method(
        root / "smali_classes3/by9.smali",
        ".method public final e("
        "Lkotlin/coroutines/jvm/internal/ContinuationImpl;)Ljava/lang/Object;\n",
        PLAYTIME_EMPTY_PREPEND,
        "by9.e: stub heartbeat/game/getUserPlayTimeList",
    )


def patch_analytics_events(root: Path) -> None:
    """Stub Ll88;->a — the general statistic-gamehub-api /events POST
    (6.0.9 Ll76;->a, 6.0.4 Lcx5;->a). Callers consume the concrete 4-field
    result type, so we early-return a synthetic success of that type before any
    URL string is allocated or the HTTP client is touched.

    The perf-config channel is not in the BASE APK on 6.1.1, so the 6.0.9
    Lqv4;->b stub and its synthetic Lk9m; snapshot are retired here (the shape
    is in git history if a future base reintroduces it).

    !!! IT IS NOT GONE — IT MOVED INTO THE PC-ENGINE PLUGIN AND IS NOT YET
    KILLED. Device-log evidence from stock 6.1.1: `DevicePerformanceReporter`
    (tag WinEmuModule, :pcengine process) starts a per-session UUID on game
    launch, samples fps / power draw / RAM MB+percent+total / GPU percent every
    ~10 s, and on activity-destroy `DevicePerfSessionSummaryUploader` POSTs a
    summary batch carrying event_type=device_perf_session_summary, user_id,
    gameId and sourceGameId ("upload success" observed). It logs
    `summaryOnly=true legacyUpload=false`, i.e. this is the SUCCESSOR to the
    6.0.9 endpoint — the old one is disabled and this replaced it.

    In the plugin tree: Lxjp/bv1; is the DTO (device_perf_session_summary),
    Lxjp/gv1; the repository (device_perf_session_summary_v1), Lxjp/hv1; and
    Lxjp/iv1; the upload log lambdas.

    Killing it needs a plugin-side stub delivered through the shadow dex — add
    the uploader to SHADOW_CLASSES in build_plugin_shadow_dex.py and stub it in
    apply_plugin_rumble_patches.py (which already patches plugin classes without
    touching base.apk). Not done yet; see the README privacy section."""
    prepend_to_method(
        root / "smali_classes3/l88.smali",
        ".method public final a(Ljava/util/Collection;"
        "Lkotlin/coroutines/jvm/internal/ContinuationImpl;)Ljava/lang/Object;\n",
        EVENTS_SUCCESS_PREPEND,
        "l88.a: stub statistic-gamehub-api/events",
    )


OTA_METHOD_HEADER = (
    ".method public final d(ILjava/lang/String;Ljava/lang/String;"
    "Lkotlin/coroutines/jvm/internal/ContinuationImpl;)Ljava/lang/Object;\n"
)
# 6.1.1 assembles the URL with a 4-arg joiner:
#   Lwzq;->n("https://", host, "/", trimStart("firmware/update/x1", '/'))
# (6.0.9 used the 3-arg Llu2;->q). Capture the result register from the match
# rather than hardcoding it (6.0.9 hardcoded p2).
OTA_CONCAT_RE = re.compile(
    r"    invoke-static \{[^}]*\}, L\w+;->\w+\(Ljava/lang/String;"
    r"Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;\)"
    r"Ljava/lang/String;\n"
    r"(?:\s*\.line \d+\n|\n)*"
    r"    move-result-object (v\d+|p\d+)\n"
)


def patch_ota_url(root: Path) -> None:
    """Overwrite the assembled OTA firmware-update URL with loopback so the
    HTTP client fails with connection-refused.

    The URL is not a single literal: Lej6;->d assembles it from "https://" + a
    branch-selected host (www.xiaoji.com / ota-test.xiaoji.com) + "/" +
    "firmware/update/x1". Overwriting the register that holds the FINAL
    assembled string catches both host branches with one injection, mirroring
    the 6.0.4 'overwrite the URL register' semantics.

    (6.0.9: Lmw4;->d via Llu2;->q, 3 args, result in p2.)"""
    p = root / "smali_classes3/ej6.smali"
    if not p.is_file():
        print("ERROR: smali_classes3/ej6.smali not found (OTA URL)",
              file=sys.stderr)
        sys.exit(1)
    src = read(p)
    if OTA_METHOD_HEADER not in src:
        print("ERROR: OTA method Lej6;->d not found — re-anchor.",
              file=sys.stderr)
        sys.exit(1)
    start = src.index(OTA_METHOD_HEADER)
    end = src.find("\n.end method", start)
    body = src[start:end]
    matches = list(OTA_CONCAT_RE.finditer(body))
    if len(matches) != 1:
        print(f"ERROR: expected exactly 1 four-arg URL joiner inside Lej6;->d, "
              f"found {len(matches)} — refusing to guess which register holds "
              f"the assembled OTA URL.", file=sys.stderr)
        sys.exit(1)
    m = matches[0]
    reg = m.group(1)
    inject = (
        "\n"
        "    # BH: privacy patch — overwrite the assembled OTA URL with loopback\n"
        "    # so the firmware-update phone-home fails with connection-refused.\n"
        f'    const-string {reg}, "http://127.0.0.1"\n'
    )
    if 'const-string %s, "http://127.0.0.1"' % reg in body:
        print("OK: ej6.d OTA URL already overwritten")
        return
    abs_pos = start + m.end()
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(src[:abs_pos] + inject + src[abs_pos:])
    print(f"OK: ej6.d: overwrite assembled OTA URL register {reg} with "
          f"http://127.0.0.1")


def remove_invoke_in_method(path, header: str, callee: str, label: str) -> None:
    """Replace a single void invoke line inside a method with a comment.

    Anchoring on just the callee reference (plus the enclosing method) is both
    .line-proof and letter-light: the Mob SDK entry points we neutralise keep
    their real names (com.mob.*), so only the enclosing method's header carries
    R8 letters. Requires the invoke to be unique within the method, and refuses
    to touch anything that would leave a dangling move-result."""
    p = Path(path)
    if not p.is_file():
        print(f"ERROR: {path} not found for: {label}", file=sys.stderr)
        sys.exit(1)
    src = read(p)
    if header not in src:
        print(f"ERROR: method not found for: {label}\n  header={header!r}",
              file=sys.stderr)
        sys.exit(1)
    start = src.index(header)
    end = src.find("\n.end method", start)
    body = src[start:end]
    pat = re.compile(r"^[ \t]*invoke-\w+(?:/range)?[^\n]*"
                     + re.escape(callee) + r"[^\n]*\n", re.M)
    matches = list(pat.finditer(body))
    if not matches:
        if f"# BH: privacy patch — {callee}" in body:
            print(f"OK: {label} (already removed)")
            return
        print(f"ERROR: invoke of {callee} not found inside the method for: "
              f"{label}", file=sys.stderr)
        sys.exit(1)
    if len(matches) != 1:
        print(f"ERROR: invoke of {callee} is non-unique ({len(matches)}) inside "
              f"the method for: {label} — refusing to guess.", file=sys.stderr)
        sys.exit(1)
    m = matches[0]
    trailing = body[m.end():m.end() + 200]
    if re.match(r"(?:\s*\.line \d+\n|\s*\n)*\s*move-result", trailing):
        print(f"ERROR: {label}: the invoke has a move-result — removing it "
              f"would leave a dangling register. Refusing.", file=sys.stderr)
        sys.exit(1)
    replacement = f"    # BH: privacy patch — {callee} invoke removed.\n"
    abs_start, abs_end = start + m.start(), start + m.end()
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(src[:abs_start] + replacement + src[abs_end:])
    print(f"OK: {label}")


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
    # AndroidApp.c() — first policy-grant invoke (6.0.9 AndroidApp.b(),
    # 6.0.4 BaseAndroidApp.a()). The `const/4 v2, 0x1` that sets up the call's
    # arg stays — later code in the method uses it too.
    remove_invoke_in_method(
        root / ANDROID_APP_SMALI,
        ".method public final c()V\n",
        "Lcom/mob/MobSDK;->submitPolicyGrantResult(Z)V",
        "AndroidApp.c: strip MobSDK.submitPolicyGrantResult",
    )

    # AndroidApp.c() — addPushReceiverInMain invoke, interior to a
    # :try_start_0 .. :try_end_0/:catchall_0 block. Void-returning, no
    # move-result, and strictly interior (the try-boundary labels are not
    # adjacent), so removal is label-safe.
    remove_invoke_in_method(
        root / ANDROID_APP_SMALI,
        ".method public final c()V\n",
        "Lcom/mob/pushsdk/MobPush;->addPushReceiverInMain",
        "AndroidApp.c: strip MobPush.addPushReceiverInMain",
    )

    # jku.E(Context)V — second policy-grant invoke (6.0.9 Ldy8;->D,
    # 6.0.4 nt5.N). In a non-try branch, so removal is label-safe. The
    # downstream setClickNotificationToLaunchMainActivity call and the
    # const/4 that feeds it stay (see the docstring above).
    remove_invoke_in_method(
        root / "smali_classes3/jku.smali",
        ".method public static E(Landroid/content/Context;)V\n",
        "Lcom/mob/MobSDK;->submitPolicyGrantResult(Z)V",
        "jku.E: strip MobSDK.submitPolicyGrantResult",
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

    Anchor: the first `monitor-enter p0` in b()V (6.0.9 a()V), reached right
    after the check-cast to the FirebaseApp data-collection state holder
    (6.1.1 Len5;, 6.0.9 La84;). We locate that check-cast by looking backwards
    from the unique "firebase_data_collection_default_enabled" string literal in
    the same method, so the R8 letter is read out of the code rather than
    hardcoded — the literal is the SDK's own preference key and is stable."""
    p = root / ANDROID_APP_SMALI
    src = read(p)
    header = ".method public final b()V\n"
    if header not in src:
        print("ERROR: AndroidApp.b()V not found (Firebase auto-init kill)",
              file=sys.stderr)
        sys.exit(1)
    start = src.index(header)
    end = src.find("\n.end method", start)
    body = src[start:end]

    if "# BH: privacy patch — Firebase/Crashlytics auto-init kill" in body:
        print("OK: AndroidApp.b: Firebase auto-init already killed")
        return

    key_pos = body.find('"firebase_data_collection_default_enabled"')
    if key_pos < 0:
        print("ERROR: firebase_data_collection_default_enabled literal not "
              "found in AndroidApp.b()V — re-anchor.", file=sys.stderr)
        sys.exit(1)
    # Last check-cast before the preference write is the state holder.
    cc = [m for m in re.finditer(r"    check-cast p0, (L\w+;)\n", body)
          if m.end() <= key_pos]
    if not cc:
        print("ERROR: no `check-cast p0, L…;` precedes the Firebase preference "
              "key in AndroidApp.b()V — re-anchor.", file=sys.stderr)
        sys.exit(1)
    m = cc[-1]
    holder = m.group(1)
    # The monitor-enter guarding the re-enable block follows it.
    me = re.compile(r"(?:\s*\.line \d+\n|\n)*    monitor-enter p0\n")
    mm = me.match(body, m.end())
    if not mm:
        print(f"ERROR: `monitor-enter p0` does not follow `check-cast p0, "
              f"{holder}` in AndroidApp.b()V — re-anchor.", file=sys.stderr)
        sys.exit(1)
    inject = (
        "    # BH: privacy patch — Firebase/Crashlytics auto-init kill. Return\n"
        "    # after FirebaseApp init but before the monitor-guarded block that\n"
        "    # writes firebase_data_collection_default_enabled=true into the SDK\n"
        "    # SharedPreferences, which would override the manifest false flags.\n"
        "    return-void\n"
        "\n"
    )
    abs_pos = start + mm.end() - len("    monitor-enter p0\n")
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(src[:abs_pos] + inject + src[abs_pos:])
    print(f"OK: AndroidApp.b: kill Firebase/Crashlytics runtime auto-init "
          f"re-enable [state holder {holder}]")


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
