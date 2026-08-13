#!/usr/bin/env python3
"""
Base-APK side of the PC-engine plugin shadow (GameHub 6.1.1).

One injection, at the head of ComboLite's PluginClassLoader constructor, routes
the plugin's dexPath through BhPluginShadow so our shadow dex is searched before
the plugin APK:

  Lcom/combo/core/runtime/loader/PluginClassLoader;-><init>(
      Ljava/lang/String;      p1 pluginId
      Ljava/lang/String;      p2 dexPath      <-- rewritten
      Ljava/lang/String;      p3 optimizedDirectory
      Ljava/lang/String;      p4 librarySearchPath
      Ljava/lang/ClassLoader; p5 parent
      L…;                    p6 pluginFinder (R8 letter; wildcarded)
      Lcom/combo/core/runtime/loader/PluginClassLoadingPolicy;)V

Why here and not at the construction site (Ltmf;): PluginClassLoader has TWO
constructors, and the File-based one just calls getAbsolutePath() and delegates
to this String-based one — so this is the single choke point every load funnels
through, and it is anchored on a real (unobfuscated) framework class name rather
than an R8 letter. The ctor is `.locals 0`; we write the result back into the
parameter register p2, which is legal and needs no .locals bump.

BhPluginShadow.dexPath() returns its input unchanged for any plugin other than
the PC engine, and whenever the installed plugin's versionCode doesn't match the
build the shadow classes were cut against — so a plugin update degrades to stock
rumble behaviour rather than loading stale shadow classes. See
extension/BhPluginShadow.java for the full gate list.

Usage:
    python3 apply_plugin_shadow_patches.py <apktool_decompile_dir>
"""
import re
import sys
from pathlib import Path

PCL_SMALI = "smali/com/combo/core/runtime/loader/PluginClassLoader.smali"

# Every type in this signature is a real framework/JDK name except the
# pluginFinder, which is an R8 letter and duly drifted Llnc; (6.1.1) -> Lrnc;
# (6.1.2) while the structure stayed identical. Wildcard just that one parameter
# rather than re-pinning a letter each release.
#
# The pattern still can't collide with the class's three other constructors:
# the File-based one takes Ljava/io/File; in slot 2, and both synthetic bridges
# are "public synthetic constructor" and append ILkotlin/…/DefaultConstructorMarker;
# after the policy — so requiring "public constructor" and Policy;)V right at the
# end pins us to the String-based one.
PCL_CTOR_RE = re.compile(
    r"\.method public constructor <init>\("
    r"Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;"
    r"Ljava/lang/ClassLoader;L[\w$/]+;"
    r"Lcom/combo/core/runtime/loader/PluginClassLoadingPolicy;\)V\n"
)
HANDLER = "Lcom/xj/winemu/vibration/BhPluginShadow;"

REG_DIRECTIVE_RE = re.compile(r"^[ \t]*\.(?:locals|registers)[ \t]+\d+[ \t]*\n", re.M)

BODY = (
    "\n"
    "    # BH: prepend the GameScrub shadow dex to this plugin's dexPath so our\n"
    "    # patched copies of the PC-engine rumble classes win over the plugin's.\n"
    "    # Returns p2 unchanged for non-PC-engine plugins and on any version\n"
    "    # mismatch, so a plugin update degrades to stock behaviour. base.apk is\n"
    "    # never modified, so its SHA-256 identity check keeps passing.\n"
    f"    invoke-static {{p1, p2}}, {HANDLER}->"
    "dexPath(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;\n"
    "    move-result-object p2\n"
)


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    root = Path(sys.argv[1])
    if not root.is_dir():
        die(f"{root} is not a directory")

    p = root / PCL_SMALI
    if not p.is_file():
        die(f"{PCL_SMALI} not found — this base APK does not ship the ComboLite "
            f"plugin framework, so there is no plugin classloader to shadow.")
    src = p.read_text(encoding="utf-8", errors="replace")

    hits = PCL_CTOR_RE.findall(src)
    if len(hits) == 0:
        die("PluginClassLoader's String-based constructor was not found with the "
            "expected signature — re-anchor (the parameter list changed shape, "
            "not just the obfuscated pluginFinder type, which is wildcarded).")
    if len(hits) != 1:
        die(f"PluginClassLoader constructor anchor is non-unique "
            f"({len(hits)} matches): {hits}")

    m = PCL_CTOR_RE.search(src)
    print(f"    anchored on {m.group(0).strip()}")
    start = m.start()
    end = src.find("\n.end method", start)
    if end < 0:
        die("unclosed PluginClassLoader constructor")
    reg = REG_DIRECTIVE_RE.search(src, start, end)
    if not reg:
        die("no .locals/.registers directive in the PluginClassLoader ctor")

    if f"{HANDLER}->dexPath" in src[reg.end():end]:
        print("OK: PluginClassLoader.<init>: shadow dexPath hook already injected")
        return

    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(src[:reg.end()] + BODY + src[reg.end():])
    print("OK: PluginClassLoader.<init>(String,String,…): shadow dexPath hook")
    print()
    print("NOTE: the shadow dex itself must be present at assets/"
          "bh_pcengine_shadow.dex")
    print("      (built by build_plugin_shadow_dex.py from a patched plugin tree).")


if __name__ == "__main__":
    main()
