#!/usr/bin/env python3
"""
Assemble the GameScrub "shadow dex" for GameHub 6.1.1's PC-engine plugin.

Takes a plugin tree already patched by apply_plugin_rumble_patches.py and
assembles ONLY the patched classes into a standalone dex. That dex is prepended
to the plugin's DexClassLoader dexPath at runtime (see
apply_plugin_shadow_patches.py + extension/BhPluginShadow.java), so our copies
of these classes win while the plugin's base.apk stays byte-identical and its
SHA-256 identity check keeps passing.

Only the classes listed in SHADOW_CLASSES are included — currently ~7 KB, versus
the plugin's multi-MB dex. Everything they reference (Lxjp/hi3;, Lxjp/jl3;, the
Kotlin runtime, …) resolves at load time from the plugin APK that follows ours on
the dexPath, so those classes must NOT be duplicated here: shadowing more than
necessary is how you get subtle identity/`instanceof` mismatches.

Mechanism: build a minimal apktool tree (manifest + apktool.yml + just those
smali files) and run `apktool b` on it, then lift classes.dex out of the result.
This reuses the apktool already present in the build rather than adding a
separate smali CLI.

Usage:
    python3 build_plugin_shadow_dex.py <patched_plugin_dir> <out.dex> [apktool.jar]

apktool.jar defaults to $APKTOOL_JAR, else "apktool" on PATH is used via
`apktool b`.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

# The shadow set is DISCOVERED, not listed: it is exactly the classes the patch
# scripts touched, i.e. the files carrying PATCH_MARKER.
#
# It used to be a hardcoded list of R8 letters, which meant every plugin bump
# needed a manual edit here in lockstep with the two patch scripts — and getting
# it wrong fails silently in the worst way. Plugin 101 -> 102 drifted every one of
# them (fi3 -> ji3 dual-motor, mv1 -> qv1 perf uploader, jg4 -> kg4 heartbeat
# bridge), and a stale entry would either crash the build or, worse, ship a shadow
# missing a privacy stub while still reporting success.
#
# Deriving from the marker keeps this in sync by construction and preserves the
# "shadow only what was patched" rule the docstring argues for: anything the
# scripts did not touch is not included, so it still resolves from the plugin APK.
#
# How many classes to expect, as a tripwire against a patch script silently
# no-oping: 2 for rumble (GamepadServerManager + the Physical vibrator) and 2 for
# privacy (perf uploader + heartbeat bridge).
EXPECTED_SHADOW_CLASSES = 4

# Marker proving the sources were patched; refuse to ship an unpatched shadow
# (it would be a pure no-op that silently costs us the fix it was built for).
# A generic "# BH" comment rather than a specific handler reference, because not
# every shadow class calls back into our extension — the telemetry stub just
# early-returns the host's own result type.
PATCH_MARKER = "# BH"

MANIFEST = ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android"'
            ' package="com.xj.winemu.pluginshadow"/>\n')

APKTOOL_YML = """!!brut.androlib.meta.MetaInfo
apkFileName: shadow.apk
isFrameworkApk: false
packageInfo:
  forcedPackageId: '127'
  renameManifestPackage: null
sdkInfo:
  minSdkVersion: '29'
  targetSdkVersion: '36'
sharedLibrary: false
sparseResources: false
usesFramework:
  ids:
  - 1
  tag: null
version: 2.9.3
versionInfo:
  versionCode: '1'
  versionName: '1.0'
"""


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    if len(sys.argv) not in (3, 4):
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    plugin_dir = Path(sys.argv[1])
    out_dex = Path(sys.argv[2])
    jar = sys.argv[3] if len(sys.argv) == 4 else os.environ.get("APKTOOL_JAR")

    if not plugin_dir.is_dir():
        die(f"{plugin_dir} is not a directory")

    work = Path(tempfile.mkdtemp(prefix="bh_shadow_"))
    try:
        smali_root = plugin_dir / "smali"
        if not smali_root.is_dir():
            die(f"{plugin_dir}/smali not found — point this at a decompiled "
                f"PC-engine plugin tree.")
        patched = sorted(
            f for f in smali_root.rglob("*.smali")
            if PATCH_MARKER in f.read_text(encoding="utf-8", errors="replace")
        )
        if not patched:
            die(f"no class in {plugin_dir} carries the {PATCH_MARKER!r} marker — "
                f"run apply_plugin_rumble_patches.py and "
                f"apply_plugin_privacy_patches.py first; refusing to build an "
                f"unpatched shadow dex (it would be a pure no-op that silently "
                f"costs the fixes it was built for).")
        if len(patched) != EXPECTED_SHADOW_CLASSES:
            rel = ", ".join(p.relative_to(plugin_dir).as_posix() for p in patched)
            die(f"expected {EXPECTED_SHADOW_CLASSES} patched classes, found "
                f"{len(patched)}: {rel}\n"
                f"  Too few means a patch script no-oped; too many means one "
                f"touched something unintended. Both need a look — refusing to "
                f"guess which.")

        staged = 0
        for src in patched:
            rel = src.relative_to(plugin_dir).as_posix()
            dst = work / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            print(f"    shadowing {rel}")
            staged += 1
        (work / "AndroidManifest.xml").write_text(MANIFEST, encoding="utf-8",
                                                  newline="")
        (work / "apktool.yml").write_text(APKTOOL_YML, encoding="utf-8",
                                          newline="")
        print(f"staged {staged} patched class(es) into a minimal apktool tree")

        apk = work / "shadow.apk"
        cmd = (["java", "-jar", jar] if jar else ["apktool"]) + \
              ["b", str(work), "-o", str(apk)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not apk.is_file():
            die("apktool b failed while assembling the shadow dex:\n"
                + (proc.stdout or "") + (proc.stderr or ""))

        with zipfile.ZipFile(apk) as z:
            names = [n for n in z.namelist() if n.endswith(".dex")]
            if names != ["classes.dex"]:
                die(f"expected exactly one classes.dex in the assembled apk, "
                    f"got {names}")
            data = z.read("classes.dex")

        out_dex.parent.mkdir(parents=True, exist_ok=True)
        out_dex.write_bytes(data)
        print(f"OK: wrote {out_dex} ({len(data)} bytes) containing "
              f"{staged} shadow class(es)")

        # Companion manifest: the fully-qualified names of exactly the classes
        # this dex shadows, one per line.
        #
        # BhPluginShadow's runtime compatibility probe reads it to know which
        # classes to compare against the installed plugin. It has to come from
        # the build rather than be hardcoded in Java, because the whole point of
        # the probe is to judge a plugin the shadow was NOT cut against — at
        # which point the only trustworthy source for "what do we override" is
        # the shadow itself. A dex has no public class-enumeration API on
        # Android, hence a side file.
        manifest = out_dex.with_suffix(".classes")
        fqns = sorted(
            p.relative_to("smali").with_suffix("").as_posix().replace("/", ".")
            for p in (pathlib.PurePosixPath(r) for r in
                      (x.relative_to(plugin_dir).as_posix() for x in patched))
        )
        manifest.write_text("\n".join(fqns) + "\n", encoding="utf-8",
                            newline="")
        print(f"OK: wrote {manifest} listing {len(fqns)} class(es):")
        for f in fqns:
            print(f"      {f}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
