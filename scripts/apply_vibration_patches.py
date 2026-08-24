#!/usr/bin/env python3
"""
Apply PC-accurate vibration patches to a decompiled GameHub apktool tree.
Supports stock 6.0.9 only.

6.1.1: PARTIAL — winebus trigger only
-------------------------------------
GameHub 6.1.1 moved the PC/Wine engine out of the base APK into a
separately-downloaded plugin (com.xiaoji.egggame.plugin.pcengine, loaded via the
ComboLite framework's PluginClassLoader). In a 6.1.1 base APK:

  * lib/ no longer contains libwinemu.so / libxserver.so / libvfs.so
  * the com.winemu.* engine classes are gone; Lcom/winemu/core/gamepad/
    GamepadServerManager survives only as a gutted shell whose onRumble(III)V is
    `.locals 0 / return-void` and whose native methods were deleted
  * the features.winemu Compose resource bundle is gone
  * there is no Vibrator / CombinedVibration reference anywhere in the dex

So hooks 1-3 below (the onRumble entry point and the Physical dispatch/stop
methods) have no target and are NOT applied on 6.1.1 — restoring dual-motor
dispatch there needs the plugin dex patched, which is out of this script's
scope.

Hook 4 IS still applicable, and that matters: `winebus.so` did NOT move into the
plugin. Verified on-device (GameHub 6.1.1, versionCode 123): the Wine runtime is
still a downloaded component under the app's own files dir —

    files/usr/opt/wine_proton11.0-arm64x/...            <- winebus.so lives here
    files/usr/home/components/{Fex_*, dxvk-*, turnip_*, vkd3d-proton-*, ...}

which is exactly the tree BhVibrationController.ensureWinebusDurationPatchOnce()
already walks, so the Java side needs no change. Only the TRIGGER moved: the
6.0.9 site was the EnvBuilder constructor, which is now plugin code. We inject
instead into the base APK's own
`PcEnginePluginHostActivity.onCreate(Bundle)` — host code that runs in the
`:pcengine` process on every game launch, before the plugin's Wine activity is
created and therefore before Wine starts.

Net effect on 6.1.1: **sustained rumble past SDL's ~1 s rumble_expiration is
restored**; dual-motor low/high split is not.

Hooks:

  1. GamepadServerManager.onRumble(III)V  — entry hook for the dispatcher
  2. <Physical>.h(II)V                     — per-controller rumble dispatch
  3. <Physical>.g()V                       — stop hook for keepalive cleanup
  4. <EnvBuilder>.<init>(Context,...) hook — call BhVibrationController to
                                             patch every winebus.so on disk
                                             once per app process (preload-
                                             free SDL rumble keepalive)

Per-version ProGuard rename maps are baked into RENAMES_6X below.

6.0.7 vs 6.0.4 drift baked into these anchors: R8 letters were fully
regenerated and .line debug directives are stripped from the app/obfuscated
code, so anchors are built from instruction sequences + method headers only
(no .line lines). The Physical class Lab8;->Lpz7; was re-lettered
(rumble g(II)V->h(II)V, stop f()V->g()V) and the per-vibrator list access
moved into a new accessor i()Ljava/util/List;, so the stop hook now anchors
on g()V's opener rather than the old inline k-field load. The EnvBuilder
Lbg5;->Liqn; (now in smali/, not smali_classes3/) no longer joins env vars
inline and no longer retains a Context at the join site, so the winebus
disk-patch trigger rides the EnvBuilder CONSTRUCTOR (where the live Context
is still in p1) instead of the joinToString call site.

Per-game settings UI insertion is intentionally out of scope (the 6.0.x
popup-menu architecture is Compose-based and Tencent's R8 obfuscation
makes the entry points hard to reach reliably). Global mode/intensity
work fine via BhVibrationSettingsActivity launched directly.

Usage:
    python3 apply_vibration_patches.py <apktool_decompile_dir>

Fails fast: if any anchor isn't found it exits non-zero before mutating
anything else.
"""
import re
import sys
from pathlib import Path


# --- Real base version, read from the tree rather than inferred ------------
# The structural probes below cannot tell 6.1.1 from 6.1.2: both ship the
# plugin host activity and the same smali layout. apktool.yml carries the
# actual versionName, so report that and keep the probes for the *family*
# decision (plugin-era vs base-APK-engine).
SUPPORTED_BASES = ("6.1.1", "6.1.2", "6.2.0")


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
# Patch primitive
# ---------------------------------------------------------------------------

def patch(path, old, new, label):
    p = Path(path)
    try:
        content = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = p.read_text(encoding="latin-1")
    if old not in content:
        print(f"ERROR: anchor not found in {path} for: {label}", file=sys.stderr)
        sys.exit(1)
    # newline="" forces LF on every platform (Windows write_text() otherwise
    # translates \n -> \r\n, which corrupts the LF-delimited .cvr bundles
    # written by the sibling scripts; keep all patch output LF for parity).
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(content.replace(old, new, 1))
    print(f"OK: {label}")


# ---------------------------------------------------------------------------
# Version detection
# ---------------------------------------------------------------------------

# Each entry maps version -> a (path, must_exist) probe whose presence is a
# reliable signature for that base version's apktool layout.
# 6.0.7's R8 map happens to REUSE the letters ab8/bg5 for unrelated classes,
# so the old 6.0.4 probe would false-match. Anchor instead on the non-
# obfuscated app class renamed in 6.0.7 (BaseAndroidApp -> AndroidApp), which
# is absent in 6.0.4, plus the stable gamepad manager path.
VERSION_PROBES = {
    "6.0.9": (
        "smali_classes3/com/xiaoji/egggame/AndroidApp.smali",
        "smali_classes3/com/winemu/core/gamepad/GamepadServerManager.smali",
    ),
}


PLUGIN_HOST_SMALI = ("smali/com/xiaoji/egggame/plugin/pcengine/host/"
                     "PcEnginePluginHostActivity.smali")
PLUGIN_HOST_ONCREATE = ".method public onCreate(Landroid/os/Bundle;)V\n"

WINEBUS_TRIGGER = (
    "\n"
    "    # BH: preload-free SDL rumble keepalive — patch every winebus.so on\n"
    "    # disk once per process, here at the PC-engine plugin host activity's\n"
    "    # onCreate. This runs in the \":pcengine\" process on every game launch,\n"
    "    # before the plugin's Wine activity is created and therefore before\n"
    "    # Wine starts. p0 is the Activity (a Context).\n"
    "    #\n"
    "    # 6.1.1 moved the engine into a downloaded plugin, so the 6.0.9 trigger\n"
    "    # site (the EnvBuilder ctor) is gone — but winebus.so itself did NOT\n"
    "    # move: it is still a Wine component under <filesDir>/usr, which is the\n"
    "    # tree the Java patcher walks. AtomicBoolean inside the Java method\n"
    "    # gates against repeat scans. No LD_PRELOAD changes.\n"
    "    invoke-static {p0}, Lcom/xj/winemu/vibration/BhVibrationController;->"
    "ensureWinebusDurationPatchOnce(Landroid/content/Context;)V\n"
)


def apply_611_winebus_trigger(root: Path) -> None:
    """6.1.1 path: inject only the winebus disk-patch trigger (hook 4)."""
    p = root / PLUGIN_HOST_SMALI
    src = p.read_text(encoding="utf-8")
    if "ensureWinebusDurationPatchOnce" in src:
        print("OK: winebus trigger already injected")
        return
    count = src.count(PLUGIN_HOST_ONCREATE)
    if count != 1:
        print(f"ERROR: expected exactly 1 onCreate(Bundle) in "
              f"{PLUGIN_HOST_SMALI}, found {count} — re-anchor.",
              file=sys.stderr)
        sys.exit(1)
    start = src.index(PLUGIN_HOST_ONCREATE)
    reg = re.search(r"^[ \t]*\.locals[ \t]+\d+[ \t]*\n", src[start:], re.M)
    if not reg:
        print("ERROR: no .locals directive in PcEnginePluginHostActivity."
              "onCreate — re-anchor.", file=sys.stderr)
        sys.exit(1)
    pos = start + reg.end()
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(src[:pos] + WINEBUS_TRIGGER + src[pos:])
    print("OK: PcEnginePluginHostActivity.onCreate: winebus disk-patch trigger")
    print()
    print("NOTE: on 6.1.1 only the winebus keepalive (sustained rumble past")
    print("      SDL's ~1 s expiration) is restored. The dual-motor low/high")
    print("      dispatch hooks target GamepadServerManager / the Physical")
    print("      vibrator class, which now live in the downloaded PC-engine")
    print("      plugin and are not patchable from the base APK.")


def detect_version(root: Path) -> str:
    matches = [
        ver
        for ver, probes in VERSION_PROBES.items()
        if all((root / p).is_file() for p in probes)
    ]
    if not matches:
        print(
            "ERROR: could not detect GameHub version — none of the known "
            "smali layout probes matched.\n"
            "Looked under "
            f"{root}/ for one of:\n  "
            + "\n  ".join(
                f"{ver}: " + " + ".join(VERSION_PROBES[ver])
                for ver in VERSION_PROBES
            ),
            file=sys.stderr,
        )
        sys.exit(1)
    if len(matches) > 1:
        print(
            f"ERROR: ambiguous version detection — matched {matches}. "
            "Two known versions share class names; refusing to guess.",
            file=sys.stderr,
        )
        sys.exit(1)
    return matches[0]


# ---------------------------------------------------------------------------
# 6.0.x patches (parameterised over rename map)
# ---------------------------------------------------------------------------

# Rename map for 6.0.7. Same structural patches as the 6.0.4 set but with
# R8 letters regenerated and anchors rewritten .line-free (6.0.7 strips line
# debug info from app code). Method letters on the Physical class shifted
# (rumble g(II)V -> h(II)V, stop f()V -> g()V) and the EnvBuilder moved to
# the primary smali/ dir as Liqn; with the winebus trigger now on its ctor.
RENAMES_6X = {
    "6.0.9": {
        "physical": "y98",          # 6.0.8 Lpz7; -> 6.0.9 Ly98; (smali_classes3)
        "physical_rumble": "h",     # g(II)V -> h(II)V (unchanged)
        "physical_stop": "g",       # f()V  -> g()V (unchanged)
        "envbuilder": "f1p",        # 6.0.8 Liqn; -> 6.0.9 Lf1p; (moved smali/ -> smali_classes3/)
    },
}


def apply_6x(root: Path, version: str) -> None:
    names = RENAMES_6X[version]
    phys = names["physical"]
    rumble = names["physical_rumble"]
    stop = names["physical_stop"]
    env = names["envbuilder"]
    print(f"  Physical class:    {phys} (rumble {rumble}(II)V, stop {stop}()V, deviceId f:I)")
    print(f"  EnvBuilder class:  {env} (ctor winebus trigger)")
    print()

    # Patch 1: GamepadServerManager.onRumble(III)V — short-circuit hook.
    # Anchor is the @Keep annotation block + the first if-ltz guard. 6.0.7
    # has .locals 1 (was 2) and the guard label is :cond_0 (was :cond_4),
    # and there are no .line directives.
    patch(
        root / "smali_classes3/com/winemu/core/gamepad/GamepadServerManager.smali",
        ".method private final onRumble(III)V\n"
        "    .locals 1\n"
        "    .annotation build Landroidx/annotation/Keep;\n"
        "    .end annotation\n"
        "\n"
        "    if-ltz p1, :cond_0\n",
        ".method private final onRumble(III)V\n"
        "    .locals 1\n"
        "    .annotation build Landroidx/annotation/Keep;\n"
        "    .end annotation\n"
        "\n"
        "    # BH: PC-accurate rumble dispatcher hook\n"
        "    invoke-static {p1, p2, p3}, Lcom/xj/winemu/vibration/BhVibrationController;->onRumble(III)Z\n"
        "\n"
        "    move-result v0\n"
        "\n"
        "    if-eqz v0, :bh_rumble_fallthrough\n"
        "\n"
        "    return-void\n"
        "\n"
        "    :bh_rumble_fallthrough\n"
        "\n"
        "    if-ltz p1, :cond_0\n",
        "GamepadServerManager.onRumble(III)V: inject BhVibrationController entry hook"
    )

    # Patch 2: <Physical>.h(II)V — controller dispatch delegate (was g(II)V
    # in 6.0.4). Reads deviceId from <Physical>.f:I and hands
    # (deviceId, low, high) to the extension. Returns true → skip stock
    # per-vibrator fallback (which always blends to single-motor). The
    # method opener "const v0, 0xffff" is unique within the class.
    patch(
        root / f"smali_classes3/{phys}.smali",
        f".method public final {rumble}(II)V\n"
        "    .locals 3\n"
        "\n"
        "    const v0, 0xffff\n",
        f".method public final {rumble}(II)V\n"
        "    .locals 3\n"
        "\n"
        "    # BH: PC-accurate controller dispatch (dual-motor)\n"
        f"    iget v0, p0, L{phys};->f:I\n"
        "\n"
        "    invoke-static {v0, p1, p2}, Lcom/xj/winemu/vibration/BhVibrationController;->dispatchToController(III)Z\n"
        "\n"
        "    move-result v0\n"
        "\n"
        "    if-eqz v0, :bh_phys_fallthrough\n"
        "\n"
        "    return-void\n"
        "\n"
        "    :bh_phys_fallthrough\n"
        "\n"
        "    const v0, 0xffff\n",
        f"{phys}.{rumble}(II)V: inject BhVibrationController.dispatchToController"
    )

    # Patch 3: <Physical>.g()V — stop hook (was f()V in 6.0.4). Lets our
    # keepalive map clear when stock GameHub routes (0,0) -> stop instead of
    # rumble. In 6.0.7 the per-vibrator list access moved out of the stop
    # method into a new accessor i()Ljava/util/List;, so g()V now opens with
    # "invoke-virtual {p0}, L<phys>;->i()Ljava/util/List;" rather than the
    # 6.0.4 inline k-field load. We inject our onStop(deviceId) call at the
    # very top (p0 is still `this`, v0 is free under .locals 1) and fall
    # through to the stock list-cancel loop.
    patch(
        root / f"smali_classes3/{phys}.smali",
        f".method public final {stop}()V\n"
        "    .locals 1\n"
        "\n"
        f"    invoke-virtual {{p0}}, L{phys};->i()Ljava/util/List;\n",
        f".method public final {stop}()V\n"
        "    .locals 1\n"
        "\n"
        "    # BH: notify our keepalive map that this device stopped, then\n"
        "    # fall through to stock per-vibrator cancel loop.\n"
        f"    iget v0, p0, L{phys};->f:I\n"
        "    invoke-static {v0}, Lcom/xj/winemu/vibration/BhVibrationController;->onStop(I)V\n"
        "\n"
        f"    invoke-virtual {{p0}}, L{phys};->i()Ljava/util/List;\n",
        f"{phys}.{stop}(): inject BhVibrationController.onStop keepalive-map cleanup"
    )

    # Patch 4: <EnvBuilder>.<init>(Context,...) — fire the BhVibrationController
    # disk patcher exactly once per app process, at EnvBuilder construction
    # (which happens on the launch path, just before the env is built and the
    # Wine launcher is invoked). The Java side scans the app's files tree for
    # every winebus.so and rewrites the two non-zero SDL_JoystickRumble call
    # sites to pass 0xffffffff as the duration so SDL's ~1 s rumble_expiration
    # never fires; zero-duration stop calls are separate sites and stay
    # untouched. No LD_PRELOAD modification — this is the preload-free path
    # that avoids the Wine-preloader address-space sensitivity that silently
    # exits a small set of games (Shotgun King is the canonical case) whenever
    # any extra .so is mapped into their Wine subprocess address space.
    #
    # WHY THE CTOR (6.0.7 divergence): in 6.0.4 the trigger rode the inline
    # ":"-joinToString site inside the env-builder build method, reusing the
    # builder's own Context field. In 6.0.7 that join moved out of the builder
    # into a separate env-map class and the EnvBuilder no longer retains a
    # Context, so there is no Context reachable at the join site. The ctor
    # Liqn;-><init>(Landroid/content/Context;Lrj3;Ltn3;Ljava/lang/String;Lgb5;)V
    # still receives the live Context in p1, so we inject there — immediately
    # after the super-<init> call and before p1 is reused as the Lhj5; env
    # map. p0 is initialized post-super, v0 holds the synthetic switch-id and
    # is untouched. (In 6.0.9 the EnvBuilder moved smali/ -> smali_classes3/
    # but still retains .line directives, so the .line 680 anchor holds.)
    patch(
        root / f"smali_classes3/{env}.smali",
        "    invoke-direct {p0}, Ljava/lang/Object;-><init>()V\n"
        "\n"
        "    .line 680\n"
        f"    iput-object p2, p0, L{env};->b:Ljava/lang/Object;\n",
        "    invoke-direct {p0}, Ljava/lang/Object;-><init>()V\n"
        "\n"
        "    # BH: preload-free SDL rumble keepalive — patch every winebus.so\n"
        "    # on disk once per app process, at EnvBuilder construction (p1 is\n"
        "    # the live Context). AtomicBoolean inside the Java method gates\n"
        "    # against repeat scans. No LD_PRELOAD changes.\n"
        "    invoke-static {p1}, Lcom/xj/winemu/vibration/BhVibrationController;->ensureWinebusDurationPatchOnce(Landroid/content/Context;)V\n"
        "\n"
        "    .line 680\n"
        f"    iput-object p2, p0, L{env};->b:Ljava/lang/Object;\n",
        f"{env}.<init>(Context,...): EnvBuilder-ctor winebus disk-patch trigger (preload-free)"
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

    # 6.1.1+ ships the engine as a downloaded plugin, so only the winebus
    # trigger (hook 4) has a target in the base APK. Detect that layout first —
    # its probe is the presence of the plugin host activity — and take the
    # reduced path rather than failing on the three missing dispatch hooks.
    if (root / PLUGIN_HOST_SMALI).is_file():
        report_version(root, "6.1.1+ / PC engine is a downloaded plugin")
        print()
        apply_611_winebus_trigger(root)
        return

    version = detect_version(root)
    version = report_version(root, version)

    if version in RENAMES_6X:
        apply_6x(root, version)
    else:
        # Unreachable: detect_version already validated.
        print(f"ERROR: no patch path implemented for {version}", file=sys.stderr)
        sys.exit(1)

    print("\nAll vibration smali patches applied successfully.")


if __name__ == "__main__":
    main()
