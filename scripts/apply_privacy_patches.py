#!/usr/bin/env python3
"""
Apply privacy patches to a decompiled GameHub apktool tree. Supports stock
6.1.1 and 6.1.2.

6.1.1 -> 6.1.2 needed no new patches, only re-anchoring, and the anchors that
drifted were converted to structural locators rather than re-pinned:

  * locate_class() finds a class by text the compiler must keep — a Kotlin
    cast-failure message naming a model FQN, or the endpoint literal itself —
    instead of by filename. That covers the heartbeat start POST
    (Lvho; -> Lgio;), getUserPlayTimeList (Lby9; -> Lgy9;), the /events sender
    (Ll88; -> Lo88;) and the OTA URL assembler (Lej6; -> Lhj6;).
  * locate_method_containing() finds a method by what it CALLS, because the
    Firebase and Mob bootstraps in AndroidApp swap letters nearly every release
    (a() -> b() -> c() -> b()). On 6.1.2 b() is the *Mob* bootstrap, so the old
    hardcoded Firebase pin would have patched the wrong method rather than
    failing loudly — which is exactly why this is now derived.
  * the Mob push-config helper moved dex bucket as well as letter
    (smali_classes3/jku -> smali/mnu), so it is located by being the one
    APP-code caller of submitPolicyGrantResult, excluding the vendor SDK's own
    copies.

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
  * the XiaoJi heartbeat surface only LOOKS smaller: heartbeat/game/update and
    heartbeat/game/end are absent from the BASE APK, so two base stubs replace
    6.0.9's three. !!! THEY ARE NOT GONE — they moved into the PC-engine plugin
    and FIRED there (update every 30 s during play, log-confirmed), carrying the
    user's Steam ID64 as source_user_id. That plugin-side trio is killed by
    apply_plugin_privacy_patches.py (one stub at the shared bridge
    Lxjp/jg4;->e), not by this script. See the heartbeat note below.
  * the /events/device-performance-config channel left the BASE APK, so its
    6.0.9 stub is retired here — but it too moved into the plugin (endpoint
    literal .../events/device-performance-session-summary in Lxjp/n2;) and is
    killed there by apply_plugin_privacy_patches.py, not by this script.
  * NEW and NOT covered by this script: 6.1.1 added a Firebase Analytics
    logEvent mirror (base Lcr1;->b/c, plugin Lxjp/kz;) carrying device_id and
    gh_uid. 6.0.9 had zero app-code logEvent call sites. It should be inert
    because of the manifest kill-switch below, but the app calls
    setAnalyticsCollectionEnabled(true) plus consent grants on every startup
    (Ly6k;->a, log-confirmed "applyToSdk enabled=true"), so the manifest flag is
    now load-bearing for a live event stream rather than vestigial. Needs a
    pcap/DNS check against app-measurement.com to confirm the flag wins.
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

  Chinese-OEM push fleet (manifest android:enabled=false + meta-data strip)
    6.1.1 newly registers the vendor SDKs behind Mob's push plugins —
    Xiaomi MiPush, Huawei HMS Push, Meizu, vivo — OUTSIDE the com.mob. /
    cn.fly. namespaces, so the 6.0.9 predicate left them all enabled.
    Several were exported, and com.xiaomi.mipush.sdk.
    NotificationClickedActivity was exported with NO permission (any
    installed app could launch it). All 13 are now disabled, and the
    OEM credential <meta-data> (Huawei appid, vivo api_key/app_id,
    HiHonor app_id, MIPUSH_SDK_VERSION_*) is stripped. See
    OEM_PUSH_COMPONENT_PREFIXES for the exact prefixes and for what is
    deliberately left enabled (generic HMS Core UI, AGConnect, FCM).

  XiaoJi heartbeat / playtime tracker (bytecode stubs)
    Lvho;->a stubs the surviving heartbeat/game/start POST (returning the
    Ldd7;(Unit) success wrapper its own early-out path builds) and Lby9;->e
    stubs the heartbeat/game/getUserPlayTimeList GET (returning an empty
    wrapped list). heartbeat/game/update and heartbeat/game/end are absent
    from the BASE APK, so two base stubs replace 6.0.9's three — but they are
    NOT gone: they live in the PC-engine plugin, where they fired every 30 s
    during play carrying the user's Steam ID64. That trio is killed plugin-side
    by apply_plugin_privacy_patches.py (shadow-dex stub at the shared bridge
    Lxjp/jg4;->e, the sole funnel for start/update/end). UX trade-off:
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


# --- Real base version, read from the tree rather than inferred ------------
# The structural probes below cannot tell 6.1.1 from 6.1.2: both ship the
# plugin host activity and the same smali layout. apktool.yml carries the
# actual versionName, so report that and keep the probes for the *family*
# decision (plugin-era vs base-APK-engine).
SUPPORTED_BASES = ("6.1.1", "6.1.2", "6.2.0", "6.2.1")


def apktool_version(root: Path):
    """versionName from apktool.yml, or None if unreadable."""
    y = Path(root) / "apktool.yml"
    if not y.is_file():
        return None
    try:
        m = re.search(r"^\s*versionName:\s*'?([0-9.]+)'?\s*$",
                      y.read_text(encoding="utf-8", errors="replace"), re.M)
        return m.group(1) if m else None
    except OSError:
        return None


def report_version(root: Path, family: str) -> str:
    """Print the real base version and flag anything we have not been run on."""
    actual = apktool_version(root)
    if actual is None:
        print(f"Detected GameHub base version: {family} (family probe; "
              f"apktool.yml unreadable)")
        return family
    note = "" if actual in SUPPORTED_BASES else "  [UNTESTED on this base]"
    print(f"Detected GameHub base version: {actual} "
          f"({family}-family layout){note}")
    return actual



# ---------------------------------------------------------------------------
# Patch primitives
# ---------------------------------------------------------------------------

def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


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


# Constructor invocations in an injected body: invoke-direct or
# invoke-direct/range on L<letter>;-><init>(<sig>)V. Only letter-shaped classes
# are checked — framework references (Lkotlin/Unit;, Ljava/util/ArrayList;) have
# no file in the tree.
INJECTED_CTOR_RE = re.compile(
    r"invoke-direct(?:/range)? \{[^}]*\}, "
    r"(L[a-z][a-z0-9]{1,5};)-><init>\(([^)]*)\)V"
)


def assert_ctors_resolve(root: Path, body: str, label: str) -> None:
    """Refuse to inject smali whose constructor calls don't exist in this base.

    This exists because of a real shipped bug. The Either success wrapper and the
    /events result type were hardcoded R8 letters (Ldd7;, Lh88;). On 6.1.2 BOTH
    classes still exist, but neither declares the constructor we were calling —
    dd7 became an unrelated class with only a no-arg ctor, and the /events result
    moved to Lk88;. So the anchors all verified, the patches reported OK, the APK
    assembled, and the app died at runtime with

        NoSuchMethodError: No direct method <init>(Ljava/lang/Object;)V in Ldd7;

    the first time a stubbed call ran. The lesson is specifically that checking
    the class EXISTS is not enough — a stale letter usually still resolves to
    *some* class. Verify the exact constructor.
    """
    problems = []
    for cls, sig in INJECTED_CTOR_RE.findall(body):
        name = cls[1:-1]
        hits = list(root.glob(f"smali*/{name}.smali"))
        if not hits:
            problems.append(f"{cls} does not exist in this base")
            continue
        # Accept the `synthetic` variant: R8 emits some constructors that way,
        # and requiring plain `public constructor` would reject a CORRECT class.
        want = [f".method public constructor <init>({sig})V",
                f".method public synthetic constructor <init>({sig})V"]
        if not any(w in read(hits[0]) for w in want):
            have = [ln.strip() for ln in read(hits[0]).splitlines()
                    if ln.startswith(".method") and "<init>" in ln]
            problems.append(
                f"{cls} exists ({hits[0].relative_to(root).as_posix()}) but "
                f"declares no <init>({sig})V; it has: "
                + (", ".join(h.replace('.method ', '') for h in have) or "none"))
    if problems:
        die(f"{label}: injected body would not resolve at runtime:\n"
            + "\n".join(f"  - {p}" for p in problems)
            + "\n  These are R8 letters and drift every release — re-derive "
              "them from the tree rather than hardcoding.")


def locate_class_by_ctor(root: Path, ctor_sig: str, label: str) -> str:
    """Find the one class declaring `ctor_sig`, returned as `Lname;`.

    Used for result/wrapper types we CONSTRUCT rather than call into, where the
    constructor signature is the stable part and the class name is a letter
    (/events result: Lyw5; 6.0.4 -> Lh76; 6.0.9 -> Lh88; 6.1.1 -> Lk88; 6.1.2).
    """
    needle = f".method public constructor <init>({ctor_sig})V"
    hits = [f for d in sorted(root.glob("smali*")) if d.is_dir()
            for f in d.rglob("*.smali") if needle in read(f)]
    if not hits:
        die(f"{label}: no class declares <init>({ctor_sig})V — re-anchor.")
    if len(hits) > 1:
        rel = ", ".join(h.relative_to(root).as_posix() for h in hits)
        die(f"{label}: ctor signature is non-unique ({len(hits)}: {rel}).")
    name = hits[0].stem
    print(f"    located L{name}; for {label}")
    return f"L{name};"


# The repo's Either: two sibling classes over one base, one wrapping a decoded
# payload and one wrapping a mapped error. Both are letters that drift (6.1.1
# success Ldd7; / failure Lcd7; over Led7;; 6.1.2 success Lgd7; / failure Lfd7;
# over Lhd7;), and getting them BACKWARDS is worse than failing: callers would
# treat every stubbed call as an error and surface a toast.
#
# So derive Success from the method being stubbed, using the host's own
# discriminator — the branch taken when Result.exceptionOrNull() is null:
#
#     invoke-static {vN}, Lkotlin/Result;->exceptionOrNull-impl(...)
#     move-result-object vM
#     if-nez vM, :cond_X          <- non-null = failure, so the fall-through...
#     new-instance vK, L<Success>;   <- ...builds Success
EITHER_SUCCESS_RE = re.compile(
    r"exceptionOrNull-impl\([^\n]*\n"
    r"(?:[ \t]*\.line \d+\n|[ \t]*\n)*"
    r"[ \t]*move-result-object ([vp]\d+)\n"
    r"(?:[ \t]*\.line \d+\n|[ \t]*\n)*"
    r"[ \t]*if-nez \1, :\w+\n"
    r"(?:[ \t]*\.line \d+\n|[ \t]*\n)*"
    r"[ \t]*new-instance [vp]\d+, (L[\w$/]+;)\n"
)


def either_success_in_method(path, header: str, label: str) -> str:
    """Derive the Either Success class from the body of the method we stub.

    Requires every occurrence in the method to agree, which doubles as a check
    that the pattern still means what we think it does.
    """
    src = read(Path(path))
    if header not in src:
        die(f"{label}: method not found while deriving the Either success type")
    start = src.index(header)
    end = src.find("\n.end method", start)
    # group(1) is the register (needed only for the backreference); group(2) is
    # the class. findall() would hand back (register, class) tuples here, so use
    # finditer and take the class explicitly.
    found = {m.group(2) for m in EITHER_SUCCESS_RE.finditer(src[start:end])}
    if not found:
        die(f"{label}: could not derive the Either success type — no "
            f"exceptionOrNull/if-nez/new-instance sequence in the method. "
            f"Re-derive by hand and check the Either shape did not change.")
    if len(found) > 1:
        die(f"{label}: ambiguous Either success type {sorted(found)} — the "
            f"method builds more than one wrapper on its success path.")
    success = found.pop()
    print(f"    derived {success} as the Either success wrapper for {label}")
    return success


def locate_class(root: Path, needles, method_header, label: str,
                 exclude=()) -> Path:
    """Find the one smali file containing every string in `needles` AND the
    method `method_header`.

    Why not just name the file: R8 re-letters these classes every release
    (heartbeat start Lvho; on 6.1.1 -> Lgio; on 6.1.2, playtime Lby9; -> Lgy9;),
    so a hardcoded filename is a re-anchoring chore each base bump. What does NOT
    move is text the compiler is obliged to keep — a Kotlin cast-failure message
    naming a model FQN, or the endpoint literal itself. Anchor on that, and
    require the method signature too so a same-string sibling (the model class,
    its $serializer) can't be picked by mistake.

    Fails loudly on zero or multiple matches rather than guessing.
    """
    if isinstance(needles, str):
        needles = [needles]
    hits = []
    for d in sorted(root.glob("smali*")):
        if not d.is_dir():
            continue
        for f in d.rglob("*.smali"):
            rel = f.relative_to(root).as_posix()
            if any(x in rel for x in exclude):
                continue
            text = read(f)
            if all(n in text for n in needles):
                if method_header is None or method_header in text:
                    hits.append(f)
    if not hits:
        shape = ("" if method_header is None
                 else f" together with\n  {method_header.strip()}")
        die(f"{label}: no smali file contains {needles!r}{shape}\n"
            f"  (the model FQN or the method shape changed — re-anchor.)")
    if len(hits) > 1:
        rel = ", ".join(str(h.relative_to(root)) for h in hits)
        die(f"{label}: anchor is non-unique ({len(hits)} files: {rel}) — "
            f"refusing to guess.")
    print(f"    located {hits[0].relative_to(root)} for {label}")
    return hits[0]


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


# 6.1.1 newly registers the Chinese-OEM push fleet that Mob Push pulls in as
# transport plugins. Those components sit OUTSIDE com.mob./cn.fly., so the
# predicate above left them enabled — several exported, and
# com.xiaomi.mipush.sdk.NotificationClickedActivity exported with NO permission,
# i.e. any installed app could launch it. Mob's own plugin shims
# (com.mob.pushsdk.plugins.{xiaomi,huawei,meizu,vivo,oppo,honor,fcm}) were
# already disabled; these are the vendor SDKs behind them.
#
# These are PUSH-SPECIFIC PACKAGE prefixes, not whole vendor namespaces. The
# distinction matters most for Huawei: com.huawei.hms.* is HMS Core, shared by
# every HMS kit, so only com.huawei.hms.support.api.push.* is listed here.
# Deliberately left ENABLED, with reasons:
#   com.huawei.hms.activity.BridgeActivity / EnableServiceActivity — generic HMS
#     Core resolution UI, both android:exported="false", so zero external
#     surface. They are only ever started from HMS code, which on this build
#     exists solely to serve the (now dead) Mob Huawei push channel — the base
#     APK contains no other HMS kit (no ads/maps/location/IAP/scan; the only
#     non-com.huawei.* callers of HmsInstanceId are R8-relettered HMS SDK
#     internals: Lkw;, Lkeu;, Lveu;, Lwau;). Disabling them would buy no
#     privacy and could break HMS availability UI if a future base APK adopts
#     another HMS kit.
#   com.huawei.agconnect.core.ServiceDiscovery — AGConnect core bootstrap,
#     exported="false", not a push component.
#   com.google.firebase.messaging.* / FirebaseInstanceIdReceiver — untouched, as
#     upstream. Firebase is handled by the manifest kill switch above and the
#     auto-init stub below; nothing here has shown FCM to be push-only+unused.
OEM_PUSH_COMPONENT_PREFIXES = (
    "com.xiaomi.push.",                  # XMPushService, XMJobService,
                                         # service.receivers.PingReceiver
    "com.xiaomi.mipush.",                # PushMessageHandler (exported),
                                         # MessageHandleService,
                                         # NotificationClickedActivity (exported,
                                         # no permission)
    "com.huawei.hms.support.api.push.",  # PushReceiver + PushMsgReceiver (both
                                         # exported, directBootAware),
                                         # service.HmsMsgService (exported,
                                         # directBootAware), PushProvider
                                         # (exported), TransActivity
    "com.meizu.cloud.pushsdk.",          # MzPushSystemReceiver
    "com.vivo.push.",                    # sdk.service.CommandClientService
                                         # (exported)
    "com.hihonor.push.",                 # no components in 6.1.1 (meta-data
    "com.heytap.msp.push.",              # only); listed so a future base that
    "com.heytap.mcs.",                   # registers them is covered without
    "com.coloros.mcs.",                  # another re-anchoring pass
)

# Extra <meta-data> names carrying live OEM push credentials/config that do not
# match a prefix above. Exact names only — a com.huawei.hms.client. PREFIX would
# also swallow com.huawei.hms.client.service.name:{base,opendevice,push}, which
# are HMS kit descriptors rather than credentials.
# (com.mob.push.{xiaomi,oppo,meizu}.* are already removed by _has_mob_namespace.)
OEM_PUSH_METADATA_NAMES = (
    "com.huawei.hms.client.appid",   # 117218631
    "MIPUSH_SDK_VERSION_CODE",
    "MIPUSH_SDK_VERSION_NAME",
)


def _is_oem_push_component(name: str) -> bool:
    return name.startswith(OEM_PUSH_COMPONENT_PREFIXES)


def _is_oem_push_metadata(name: str) -> bool:
    # com.vivo.push.{api_key,app_id,support_monitor} and
    # com.hihonor.push.{app_id,sdk_version} come in via the component prefixes.
    return _is_oem_push_component(name) or name in OEM_PUSH_METADATA_NAMES


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

    # 4. Push neutralisation in the manifest. Two passes:
    #    (a) flip android:enabled="false" on every provider/service/
    #        receiver/activity whose android:name is in the com.mob. / cn.fly.
    #        namespaces OR in one of the OEM push packages above;
    #        (b) remove <meta-data> entries in the same namespaces outright
    #        (meta-data has no enabled attribute), plus the OEM credential
    #        entries listed in OEM_PUSH_METADATA_NAMES.
    #
    # The match is line-by-line because apktool emits each tag on its own
    # line. Re-emit with the same indentation the line came in with.
    out_lines = []
    skipped_meta = 0
    skipped_oem_meta = 0
    flipped = 0
    flipped_oem = 0
    for line in src.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("<meta-data"):
            name = _split_attrs(stripped).get("android:name", "")
            if _has_mob_namespace(name):
                skipped_meta += 1
                continue
            if _is_oem_push_metadata(name):
                skipped_oem_meta += 1
                print(f"OK: removed OEM push <meta-data> {name}")
                continue
        for tag in ("provider", "service", "receiver", "activity"):
            if stripped.startswith(f"<{tag} ") or stripped.startswith(f"<{tag}\n"):
                attrs = _split_attrs(stripped)
                name = attrs.get("android:name", "")
                is_mob = _has_mob_namespace(name)
                is_oem = (not is_mob) and _is_oem_push_component(name)
                if is_mob or is_oem:
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
                    if is_oem:
                        flipped_oem += 1
                        print(f"OK: disabled OEM push {tag} {name}")
                    else:
                        flipped += 1
                break
        out_lines.append(line)
    src = "\n".join(out_lines)
    if flipped:
        print(f"OK: disabled {flipped} Mob/cn.fly manifest components")
    if skipped_meta:
        print(f"OK: removed {skipped_meta} Mob/cn.fly <meta-data> entries")
    if flipped_oem:
        print(f"OK: disabled {flipped_oem} OEM push manifest components")
    if skipped_oem_meta:
        print(f"OK: removed {skipped_oem_meta} OEM push <meta-data> entries")
    if not flipped_oem and not skipped_oem_meta:
        print("OK: OEM push components/meta-data already neutralised "
              "(or absent from this base)")

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
# The network layer returns an Either-shaped pair, both extending one base with a
# single (Ljava/lang/Object;)V ctor: one wraps the decoded payload (SUCCESS), the
# other the mapped error (FAILURE). Both are R8 letters and BOTH DRIFT:
#   6.1.1  success Ldd7;  failure Lcd7;  over Led7;
#   6.1.2  success Lgd7;  failure Lfd7;  over Lhd7;
# This used to be a hardcoded EITHER_SUCCESS constant, which is exactly how 6.1.2
# shipped broken: dd7 became an unrelated class with a no-arg ctor, so the stubs
# assembled fine and the app died at runtime with NoSuchMethodError. It is now
# derived per-method by either_success_in_method() — see that function for the
# discriminator, and assert_ctors_resolve() for the guard that would have caught
# the stale letter at build time.

# Heartbeat POST short-circuit. 6.1.1 collapsed the heartbeat surface: only
# heartbeat/game/start survives (as a Lazy<String> in Loe0;->a, consumed by
# Lvho;->a), and its repo method returns Ldd7;(kotlin.Unit.INSTANCE) on its own
# success paths — so returning exactly that is indistinguishable from a
# no-op-but-successful beat. 6.1.1 leaves kotlin.Unit unobfuscated, so the
# literal Lkotlin/Unit;->INSTANCE resolves (on 6.0.7-6.0.9 it was Lx6m;->a and
# a literal kotlin.Unit reference threw NoClassDefFoundError).
def heartbeat_unit_prepend(success: str) -> str:
    """Unit in the repo's own success wrapper, i.e. exactly what the method's own
    early-out path returns — indistinguishable from a no-op-but-successful beat.

    6.1.1+ leaves kotlin.Unit unobfuscated so the literal resolves (on
    6.0.7-6.0.9 it was Lx6m;->a and a literal kotlin.Unit reference threw
    NoClassDefFoundError).
    """
    return (
        "    # BH: privacy patch — short-circuit the heartbeat POST. Returns the\n"
        "    # same success wrapper the method's own early-out path builds, so\n"
        "    # callers see a successful no-op beat and no HTTP happens.\n"
        "    sget-object v0, Lkotlin/Unit;->INSTANCE:Lkotlin/Unit;\n"
        f"    new-instance v1, {success}\n"
        f"    invoke-direct {{v1, v0}}, {success}-><init>(Ljava/lang/Object;)V\n"
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
EVENTS_RESULT_CTOR = ("ZLjava/lang/Integer;Ljava/lang/String;"
                      "Ljava/lang/Throwable;I")


def events_success_prepend(result_cls: str) -> str:
    """Synthetic 4-field success result; `result_cls` is derived, not hardcoded
    (6.0.4 Lyw5; -> 6.0.9 Lh76; -> 6.1.1 Lh88; -> 6.1.2 Lk88;).

    The ctor takes 6 registers including `this`, which exceeds invoke-direct's
    5-register cap (format 35c), so this uses invoke-direct/range — 35c silently
    truncates at assembly time in some baksmali builds rather than erroring.
    """
    return (
        "    # BH: privacy patch — early-return synthetic success before any\n"
        "    # URL string is allocated or HTTP client is touched.\n"
        f"    new-instance v0, {result_cls}\n"
        "    const/4 v1, 0x1\n"
        "    const/4 v2, 0x0\n"
        "    const/4 v3, 0x0\n"
        "    const/4 v4, 0x0\n"
        "    const/4 v5, 0x0\n"
        f"    invoke-direct/range {{v0 .. v5}}, {result_cls}-><init>("
        f"{EVENTS_RESULT_CTOR})V\n"
        "    return-object v0\n"
        "\n"
    )

# Empty playtime list. getUserPlayTimeList returns the Either success wrapper
# around the decoded ArrayList of entries, so an empty ArrayList makes the UI
# iterator run zero passes instead of crashing (6.0.9 Lyi5;, 6.0.4 Ln55;).
def playtime_empty_prepend(success: str) -> str:
    """Empty list in the repo's own success wrapper. A bare Unit here would
    ClassCastException in the caller, which iterates the payload."""
    return (
        "    # BH: privacy patch — return empty playtime list wrapper.\n"
        "    new-instance v0, Ljava/util/ArrayList;\n"
        "    invoke-direct {v0}, Ljava/util/ArrayList;-><init>()V\n"
        f"    new-instance v1, {success}\n"
        f"    invoke-direct {{v1, v0}}, {success}-><init>(Ljava/lang/Object;)V\n"
        "    return-object v1\n"
        "\n"
    )


def patch_heartbeat(root: Path) -> None:
    """Stub the heartbeat POST + getUserPlayTimeList.

    !!! THIS COVERS THE BASE APK ONLY. heartbeat/game/{update,end} literals are
    absent from the base APK because they live in the PC-engine PLUGIN, and they
    DID fire: verified on-device with a game running, the :pcengine process
    POSTed landscape-api-oversea.vgabc.com/heartbeat/game/update every 30 s with
    game_id, source_game_id, source_type and source_user_id = the user's
    Steam ID64. The owning plugin class is Lxjp/kx9; (WineGameUsageTracker,
    local MMKV key prefix "wine_usage:"), but its own methods only build the
    request DTO and resolve ids — the three POSTs are emitted from its suspend
    lambdas Lxjp/uk8; (start), Lxjp/x06; (update) and Lxjp/gx9; (end), all of
    which funnel through the static bridge Lxjp/jg4;->e(…Lxjp/fs9;…). That
    bridge is what apply_plugin_privacy_patches.py stubs, via the shadow dex.
    The URL-literal holders Lxjp/x06;/Lxjp/uk8;/Lxjp/w7; are merged synthetics
    serving unrelated purposes app-wide, so they are NOT stubbed — the same
    reasoning that makes us stub Lvho;->a instead of Ld80;->invoke here.

    What the two stubs below DO cover, in the base APK:

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
    # The heartbeat/game/start POST — Lvho; on 6.1.1, Lgio; on 6.1.2. Located by
    # the Kotlin cast-failure message, which the compiler emits with the model's
    # real FQN even though the enclosing class is a letter. Its two siblings that
    # carry the same string (the HeartbeatStartCheckData data class and its
    # $serializer) are excluded by requiring the method signature.
    start_header = (
        ".method public final a(Ljava/lang/String;"
        "Lkotlin/coroutines/jvm/internal/ContinuationImpl;)Ljava/lang/Object;\n"
    )
    start_path = locate_class(
        root,
        "null cannot be cast to non-null type "
        "com.xiaoji.egggame.core.network.model.BaseResult<"
        "com.xiaoji.egggame.launcher.interceptor.HeartbeatStartCheckData>",
        start_header,
        "heartbeat/game/start POST",
    )
    start_body = heartbeat_unit_prepend(
        either_success_in_method(start_path, start_header,
                                 "heartbeat/game/start POST"))
    assert_ctors_resolve(root, start_body, "heartbeat/game/start stub")
    prepend_to_method(start_path, start_header, start_body,
                      "stub heartbeat/game/start POST")
    # getUserPlayTimeList — Lby9; on 6.1.1, Lgy9; on 6.1.2; located by the
    # endpoint literal it holds. Returns the Either success wrapper around an
    # empty ArrayList so the UI iterator runs zero passes instead of crashing
    # (a bare Unit here would ClassCastException in the caller).
    playtime_header = (
        ".method public final e("
        "Lkotlin/coroutines/jvm/internal/ContinuationImpl;)Ljava/lang/Object;\n"
    )
    playtime_path = locate_class(
        root,
        "heartbeat/game/getUserPlayTimeList",
        playtime_header,
        "heartbeat/game/getUserPlayTimeList",
    )
    playtime_body = playtime_empty_prepend(
        either_success_in_method(playtime_path, playtime_header,
                                 "heartbeat/game/getUserPlayTimeList"))
    assert_ctors_resolve(root, playtime_body, "playtime stub")
    prepend_to_method(playtime_path, playtime_header, playtime_body,
                      "stub heartbeat/game/getUserPlayTimeList")


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

    It is killed plugin-side by apply_plugin_privacy_patches.py, which stubs the
    uploader Lxjp/mv1;->c and ships it through the shadow dex (see
    SHADOW_CLASSES in build_plugin_shadow_dex.py) — not by this script."""
    events_header = (
        ".method public final a(Ljava/util/Collection;"
        "Lkotlin/coroutines/jvm/internal/ContinuationImpl;)Ljava/lang/Object;\n"
    )
    # Sender: Ll88; on 6.1.1, Lo88; on 6.1.2 — located by the endpoint literal it
    # holds. Result type: located by its ctor signature, since we construct it.
    events_path = locate_class(
        root,
        "https://statistic-gamehub-api.vgabc.com/events",
        events_header,
        "statistic-gamehub-api/events POST",
    )
    events_body = events_success_prepend(
        locate_class_by_ctor(root, EVENTS_RESULT_CTOR, "/events result type"))
    assert_ctors_resolve(root, events_body, "/events stub")
    prepend_to_method(events_path, events_header, events_body,
                      "stub statistic-gamehub-api/events")


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

    The URL is not a single literal: the method assembles it from "https://" + a
    branch-selected host (www.xiaoji.com / ota-test.xiaoji.com) + "/" +
    "firmware/update/x1". Overwriting the register that holds the FINAL
    assembled string catches both host branches with one injection, mirroring
    the 6.0.4 'overwrite the URL register' semantics.

    The class is a letter that drifts (6.0.9 Lmw4;->d via Llu2;->q with 3 args
    and the result in p2; 6.1.1 Lej6;->d; 6.1.2 Lhj6;->d), so it is located by a
    Kotlin cast-failure message naming the kept FQN GameFirmwareData — the
    firmware channel's own model — rather than by filename."""
    p = locate_class(
        root,
        "null cannot be cast to non-null type "
        "com.xiaoji.egggame.core.network.model.BaseFirmwareResult<"
        "kotlin.collections.List<"
        "com.xiaoji.egggame.core.device.entity.GameFirmwareData>>",
        OTA_METHOD_HEADER,
        "OTA firmware-update URL",
    )
    src = read(p)
    start = src.index(OTA_METHOD_HEADER)
    end = src.find("\n.end method", start)
    body = src[start:end]
    matches = list(OTA_CONCAT_RE.finditer(body))
    if len(matches) != 1:
        print(f"ERROR: expected exactly 1 four-arg URL joiner inside the OTA method, "
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
        print("OK: OTA URL already overwritten")
        return
    abs_pos = start + m.end()
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(src[:abs_pos] + inject + src[abs_pos:])
    print(f"OK: overwrite assembled OTA URL register {reg} with "
          f"http://127.0.0.1")


METHOD_HEADER_RE = re.compile(r"^\.method[^\n]*\n", re.M)


def locate_method_containing(path, callee: str, label: str) -> str:
    """Return the header line of the one method in `path` that references
    `callee`.

    R8 renames these bootstrap methods on nearly every release — the Firebase
    and Mob initialisers in AndroidApp went a() -> b() -> c() and back to b()
    across 6.0.9/6.1.1/6.1.2 — while the SDK entry points they call keep their
    real com.mob.* / com.google.* names. So locate the method by what it calls
    instead of by its letter, and let the caller anchor on the returned header.

    Fails loudly if zero or several methods reference it, since 'which of these
    is the bootstrap' is exactly the guess that must not be made silently.
    """
    p = Path(path)
    if not p.is_file():
        die(f"{path} not found for: {label}")
    src = read(p)
    owners = []
    for m in METHOD_HEADER_RE.finditer(src):
        end = src.find("\n.end method", m.end())
        if end < 0:
            continue
        if callee in src[m.end():end]:
            owners.append(m.group(0))
    if not owners:
        die(f"{label}: no method in {Path(path).name} references {callee} — "
            f"re-anchor (the SDK call site moved or was removed upstream).")
    if len(owners) > 1:
        die(f"{label}: {len(owners)} methods in {Path(path).name} reference "
            f"{callee} — refusing to guess:\n"
            + "\n".join(f"  {o.strip()}" for o in owners))
    print(f"    {label}: found in {owners[0].strip()}")
    return owners[0]


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
    # AndroidApp's Mob bootstrap — c() on 6.1.1, b() on 6.1.2, b() on 6.0.9,
    # BaseAndroidApp.a() on 6.0.4. Located by the SDK call it makes rather than
    # by the letter, since both invokes we strip live in the same method.
    mob_bootstrap = locate_method_containing(
        root / ANDROID_APP_SMALI,
        "Lcom/mob/MobSDK;->submitPolicyGrantResult(Z)V",
        "Mob bootstrap",
    )

    # First policy-grant invoke. The `const/4 v2, 0x1` that sets up the call's
    # arg stays — later code in the method uses it too.
    remove_invoke_in_method(
        root / ANDROID_APP_SMALI,
        mob_bootstrap,
        "Lcom/mob/MobSDK;->submitPolicyGrantResult(Z)V",
        "strip MobSDK.submitPolicyGrantResult",
    )

    # addPushReceiverInMain invoke, same method, interior to a
    # :try_start_0 .. :try_end_0/:catchall_0 block. Void-returning, no
    # move-result, and strictly interior (the try-boundary labels are not
    # adjacent), so removal is label-safe.
    remove_invoke_in_method(
        root / ANDROID_APP_SMALI,
        mob_bootstrap,
        "Lcom/mob/pushsdk/MobPush;->addPushReceiverInMain",
        "strip MobPush.addPushReceiverInMain",
    )

    # The obfuscated push-config helper holds a second policy-grant invoke
    # (6.0.4 nt5.N, 6.0.9 Ldy8;->D, 6.1.1 Ljku;->E, 6.1.2 Lmnu;->B — it also
    # changed dex bucket, smali_classes3 -> smali). In a non-try branch, so
    # removal is label-safe. The downstream
    # setClickNotificationToLaunchMainActivity call and the const/4 that feeds it
    # stay (see the docstring above).
    #
    # Located as: the one APP-code class that calls submitPolicyGrantResult,
    # excluding the vendor SDK's own copies (com/mob/**, cn/fly/**) and
    # AndroidApp, which is the other call site handled above.
    helper = locate_class(
        root,
        "Lcom/mob/MobSDK;->submitPolicyGrantResult(Z)V",
        None,
        "Mob push-config helper",
        exclude=("com/mob/", "cn/fly/", "com/xiaoji/egggame/AndroidApp.smali"),
    )
    remove_invoke_in_method(
        helper,
        locate_method_containing(
            helper,
            "Lcom/mob/MobSDK;->submitPolicyGrantResult(Z)V",
            "Mob push-config helper method",
        ),
        "Lcom/mob/MobSDK;->submitPolicyGrantResult(Z)V",
        "strip MobSDK.submitPolicyGrantResult (push-config helper)",
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
    # The bootstrap method's own name is an R8 letter that moves every release
    # (6.0.9 a(), 6.1.1 b(), 6.1.2 a() — and 6.1.2's b() is the *Mob* bootstrap,
    # so pinning a letter here risks patching the wrong method rather than
    # failing). Locate it by the SDK preference key it writes.
    header = locate_method_containing(
        p,
        '"firebase_data_collection_default_enabled"',
        "Firebase bootstrap",
    )
    start = src.index(header)
    end = src.find("\n.end method", start)
    body = src[start:end]

    if "# BH: privacy patch — Firebase/Crashlytics auto-init kill" in body:
        print("OK: Firebase auto-init already killed")
        return

    key_pos = body.find('"firebase_data_collection_default_enabled"')
    if key_pos < 0:
        print("ERROR: firebase_data_collection_default_enabled literal not "
              "found in the Firebase bootstrap — re-anchor.", file=sys.stderr)
        sys.exit(1)
    # Last check-cast before the preference write is the state holder.
    cc = [m for m in re.finditer(r"    check-cast p0, (L\w+;)\n", body)
          if m.end() <= key_pos]
    if not cc:
        print("ERROR: no `check-cast p0, L…;` precedes the Firebase preference "
              "key in the Firebase bootstrap — re-anchor.", file=sys.stderr)
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
    print(f"OK: kill Firebase/Crashlytics runtime auto-init "
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
    version = report_version(root, version)
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
