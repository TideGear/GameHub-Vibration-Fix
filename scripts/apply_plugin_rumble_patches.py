#!/usr/bin/env python3
"""
Restore dual-motor rumble on GameHub 6.1.1 by patching the PC-engine PLUGIN's
rumble classes — without modifying the plugin APK.

WHY THIS EXISTS
---------------
6.1.1 moved the Wine/PC engine into a separately-downloaded plugin
(com.xiaoji.egggame.plugin.pcengine), so the three dispatch hooks that
apply_vibration_patches.py used to place in the base APK now have no target
there. See the README section "PC engine plugin".

The plugin APK is SHA-256-committed to a local identity record and re-verified
on every load, so rewriting it in place would mean forging that record. We don't
touch it. Instead we exploit how ComboLite builds its classloader:

    com/combo/core/runtime/loader/PluginClassLoader extends DexClassLoader
    ... and DexClassLoader's dexPath is a ':'-separated LIST searched in order.

apply_plugin_shadow_patches.py patches the BASE APK so that dexPath becomes

    <our tiny shadow dex>:<the untouched plugin base.apk>

Classes we define in the shadow dex therefore win over the plugin's own copies,
while base.apk stays byte-identical and its integrity check keeps passing.
Plugin code can still reach com.xj.winemu.* because PluginClassLoader.loadClass
falls back to super.loadClass (parent-first) on a local miss.

WHAT THIS SCRIPT DOES
---------------------
Applies the three dispatch hooks to a decompiled plugin tree. Only the two
patched classes are then assembled into the shadow dex by
build_plugin_shadow_dex.py (~7 KB, not the plugin's multi-MB dex).

  1. com.winemu.core.gamepad.GamepadServerManager.onRumble(III)V
         entry hook -> BhVibrationController.onRumble(III)Z
  2. Lxjp/fi3;->g(II)V     per-controller rumble -> dispatchToController(III)Z
  3. Lxjp/fi3;->f()V       stop -> onStop(I)V (keepalive-map cleanup)

Plugin anchor map (v101; verify on a plugin bump — the script fails loudly):

  | 6.0.9 base APK              | 6.1.1 plugin              |
  |-----------------------------|---------------------------|
  | GamepadServerManager.onRumble (+@Keep) | same, no @Keep   |
  | Physical Ly98; (ext Laa8;)   | Lxjp/fi3; (ext Lxjp/hi3;) |
  | rumble h(II)V               | g(II)V                    |
  | stop g()V                   | f()V                      |
  | deviceId field f:I          | f:I (same letter)         |

Lxjp/fi3;-><init>(ILjava/lang/String;ZIIILjava/lang/String;Z)V is byte-for-byte
the 6.0.9 Ly98; ctor, and g(II)V still opens `const v0, 0xffff` then blends both
motor values into one per-vibrator float — exactly the behaviour dual-motor
dispatch replaces.

Usage:
    python3 apply_plugin_rumble_patches.py <decompiled_plugin_dir>
"""
import re
import sys
from pathlib import Path

HANDLER = "Lcom/xj/winemu/vibration/BhVibrationController;"

GAMEPAD_SERVER = "smali/com/winemu/core/gamepad/GamepadServerManager.smali"
DEVICE_ID_FIELD = "f:I"

# The Physical-vibrator class is an R8 letter and drifts on every plugin bump
# (6.1.1 plugin 101 Lxjp/fi3; -> plugin 102 Lxjp/ji3;, and 102's fi3 is an
# unrelated kotlinx serializer, so a stale pin finds a real-but-wrong class).
#
# It has a same-shaped sibling — the phone/device vibrator, which also declares
# f()V and g(II)V and also masks with 0xffff — so "has the right methods" is NOT
# enough to tell them apart. Patching the sibling would route controller rumble
# to the phone.
#
# What separates them is the constructor. Physical is built from a device
# descriptor (deviceId int, name, flags…) and its ctor has been byte-identical
# since the 6.0.9 base APK's Ly98;; the device sibling takes an Activity, because
# a phone vibrator needs a Context. Verified unique in both plugin 101 and 102.
PHYSICAL_CTOR = ".method public constructor <init>(ILjava/lang/String;ZIII" \
                "Ljava/lang/String;Z)V"


def locate_physical(root: Path):
    """Return (relative smali path, `Lxjp/xxx;` type) for the Physical vibrator."""
    hits = [f for f in (root / "smali").rglob("*.smali")
            if PHYSICAL_CTOR in f.read_text(encoding="utf-8", errors="replace")]
    if not hits:
        die("Physical vibrator class not found — no class declares\n"
            f"  {PHYSICAL_CTOR}\n"
            "  (the device-descriptor ctor changed shape; re-anchor. Do NOT fall "
            "back to matching on f()V/g(II)V alone — the phone-vibrator sibling "
            "has both.)")
    if len(hits) > 1:
        rel = ", ".join(h.relative_to(root).as_posix() for h in hits)
        die(f"Physical vibrator ctor is non-unique ({len(hits)}: {rel}).")
    p = hits[0]
    rel = p.relative_to(root).as_posix()
    typ = "L" + rel[len("smali/"):-len(".smali")] + ";"
    print(f"    Physical vibrator: {rel}  ({typ})")
    return rel, typ

REG_DIRECTIVE_RE = re.compile(r"^[ \t]*\.(?:locals|registers)[ \t]+(\d+)[ \t]*\n", re.M)


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def prepend(root: Path, rel: str, header: str, body: str, label: str,
            min_locals: int = 1) -> None:
    """Insert `body` at instruction index 0 of the method with `header`."""
    p = root / rel
    if not p.is_file():
        die(f"{rel} not found (is this a decompiled PC-engine plugin tree?)")
    src = p.read_text(encoding="utf-8", errors="replace")
    n = src.count(header)
    if n == 0:
        die(f"{label}: method not found\n  header={header!r}\n  in {rel}")
    if n != 1:
        die(f"{label}: method header is non-unique ({n} matches) in {rel}")
    start = src.index(header)
    end = src.find("\n.end method", start)
    if end < 0:
        die(f"{label}: unclosed method in {rel}")
    reg = REG_DIRECTIVE_RE.search(src, start, end)
    if not reg:
        die(f"{label}: no .locals/.registers directive in {rel}")
    have = int(reg.group(1))
    if have < min_locals:
        die(f"{label}: method declares .locals {have} but the injected body "
            f"needs {min_locals} — bump the directive before injecting.")
    marker = next(ln for ln in body.splitlines() if ln.strip().startswith("# BH"))
    if marker in src[reg.end():end]:
        print(f"OK: {label} (already applied)")
        return
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(src[:reg.end()] + body + src[reg.end():])
    print(f"OK: {label}")


def main():
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    root = Path(sys.argv[1])
    if not root.is_dir():
        die(f"{root} is not a directory")

    # Sanity: this must be the plugin tree, not the base APK tree. In the base
    # APK GamepadServerManager is a gutted shell whose onRumble is `return-void`.
    gsm = root / GAMEPAD_SERVER
    if not gsm.is_file():
        die(f"{GAMEPAD_SERVER} not found — point this at a decompiled "
            f"com.xiaoji.egggame.plugin.pcengine tree, not the base APK.")
    if "nativeSetRumbleCallback" not in gsm.read_text(encoding="utf-8",
                                                      errors="replace"):
        die("GamepadServerManager has no nativeSetRumbleCallback — this looks "
            "like the BASE APK's gutted shell, not the plugin's real class.")

    print("=== Plugin dual-motor rumble hooks ===")
    physical, physical_type = locate_physical(root)

    # Hook 1 — entry point. Short-circuits the whole dispatch when our
    # controller handled it. .locals 1 already gives us v0.
    prepend(
        root, GAMEPAD_SERVER,
        ".method private final onRumble(III)V\n",
        "\n"
        "    # BH: PC-accurate rumble dispatcher hook (plugin shadow class).\n"
        f"    invoke-static {{p1, p2, p3}}, {HANDLER}->onRumble(III)Z\n"
        "    move-result v0\n"
        "    if-eqz v0, :bh_rumble_fallthrough\n"
        "    return-void\n"
        "    :bh_rumble_fallthrough\n",
        "GamepadServerManager.onRumble(III)V: dispatcher entry hook",
    )

    # Hook 2 — per-controller dual-motor dispatch. Returning true skips the
    # stock per-vibrator path, which averages low/high into a single float.
    prepend(
        root, physical,
        ".method public final g(II)V\n",
        "\n"
        "    # BH: PC-accurate controller dispatch (dual-motor low/high).\n"
        f"    iget v0, p0, {physical_type}->{DEVICE_ID_FIELD}\n"
        f"    invoke-static {{v0, p1, p2}}, {HANDLER}->"
        "dispatchToController(III)Z\n"
        "    move-result v0\n"
        "    if-eqz v0, :bh_phys_fallthrough\n"
        "    return-void\n"
        "    :bh_phys_fallthrough\n",
        f"{physical_type}g(II)V: dual-motor dispatch",
        min_locals=3,
    )

    # Hook 3 — stop. Clears our keepalive map when the host routes (0,0) to a
    # stop instead of a rumble. Injected before the method's first instruction,
    # while p0 is still `this` (f() reassigns p0 immediately after).
    prepend(
        root, physical,
        ".method public final f()V\n",
        "\n"
        "    # BH: notify the keepalive map that this device stopped, then fall\n"
        "    # through to the stock per-vibrator cancel loop.\n"
        f"    iget v0, p0, {physical_type}->{DEVICE_ID_FIELD}\n"
        f"    invoke-static {{v0}}, {HANDLER}->onStop(I)V\n",
        f"{physical_type}f()V: keepalive-map cleanup",
    )

    print()
    print("All plugin rumble hooks applied. Next: build_plugin_shadow_dex.py")


if __name__ == "__main__":
    main()
