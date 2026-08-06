#!/usr/bin/env python3
"""
Wire the background Steam update-badge checker into GameHub 6.1.1.

Stock GameHub only runs the Steam "Online Update" check on a couple of lazy
paths (an event handler Lp1a;->o dispatching Lvi0;->o(appId, flag) + a few
detail/refresh flows) and the badges just read the last cached result, so the
red dot lags behind reality — users can launch a game several times before an
available update shows up. 6.1.1 added per-app cooldown maps (30 min / 60 s,
inside Lwvo;->w) but still has no periodic sweep, so the trigger-frequency gap
is unchanged. The check is really a network query to Steam's Content Manager
via GameHub's embedded native Steam client bridge; it does NOT require the game
(Wine) to be running, only that the app is open and the Steam session is
connected. So a background sweep is viable.

6.1.1 note: the PC engine moved into a runtime-downloaded plugin and now runs
in a ":pcengine" process. onCreate runs in every process, so start() is called
there too; it is fail-soft (the Steam bridge and its reflection anchors simply
aren't resolvable there, which logs and retries) and the Steam session lives in
the main process regardless.

This script injects a single call into the Application's onCreate so
com.xj.winemu.update.BhSteamUpdateChecker starts its background worker once
per app process. Everything else (enumerating installed appIds, running the
host's own per-app check via the withContext reflection bridge, refreshing
the badge flow) lives in the Java extension class — no other smali edits.

Injection
---------
  Lcom/xiaoji/egggame/AndroidApp;->onCreate()V   (Application subclass)

We anchor on the `sput-object v0, Lrs1;->s:...AndroidApp;` line that stock
onCreate runs immediately after super.onCreate() — at that point super has
run and v0 holds the Application (a Context). We insert our start(Context)
call right after it. onCreate is .locals 71, so there is no register
pressure and no .locals bump. Fails loudly if the anchor is missing or the
Application class moved (base bump) so a broken build never ships silently.

Note there are TWO `sput-object v0, L<letter>;->...:AndroidApp;` lines in
6.1.1's onCreate (Lrs1;->s and Lp44;->a). We pin the first one — the one
directly after super.onCreate() — by full text, and assert it is unique.

The BhSteamUpdateChecker Java is compiled + dex'd in the same workflow step
as the other extension/ classes.
"""
import sys
from pathlib import Path


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def write_lf(path: Path, content: str) -> None:
    # LF on every platform, matching apktool's own smali output.
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Version detection (mirrors apply_menu_patches.py)
# ---------------------------------------------------------------------------

VERSION_PROBES = {
    "6.1.1": (
        "smali_classes2/com/xiaoji/egggame/AndroidApp.smali",
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
    return matches[0]


# ---------------------------------------------------------------------------
# App-start hook
# ---------------------------------------------------------------------------

ANDROID_APP_SMALI = "smali_classes2/com/xiaoji/egggame/AndroidApp.smali"

# The line stock onCreate() runs right after super.onCreate(); v0 = Application.
ONCREATE_ANCHOR = (
    "    sput-object v0, Lrs1;->s:Lcom/xiaoji/egggame/AndroidApp;\n"
)

START_CALL = (
    "\n"
    "    # BH: start the background Steam update-badge checker (once per\n"
    "    # process). v0 is the Application (a Context); super.onCreate has run.\n"
    "    invoke-static {v0}, "
    "Lcom/xj/winemu/update/BhSteamUpdateChecker;->start(Landroid/content/Context;)V\n"
)


def patch_app_start(root: Path) -> None:
    p = root / ANDROID_APP_SMALI
    if not p.is_file():
        print(f"ERROR: {ANDROID_APP_SMALI} not found", file=sys.stderr)
        sys.exit(1)
    src = read(p)

    if "Lcom/xj/winemu/update/BhSteamUpdateChecker;->start(" in src:
        print("OK: BhSteamUpdateChecker.start already injected")
        return

    count = src.count(ONCREATE_ANCHOR)
    if count == 0:
        print(
            "ERROR: onCreate anchor not found in AndroidApp.smali:\n"
            f"  {ONCREATE_ANCHOR.strip()}\n"
            "  (Application class or its onCreate layout changed — re-anchor.)",
            file=sys.stderr,
        )
        sys.exit(1)
    if count != 1:
        print(
            f"ERROR: onCreate anchor is non-unique ({count} matches) in "
            "AndroidApp.smali — refusing to guess the injection site.",
            file=sys.stderr,
        )
        sys.exit(1)

    src = src.replace(ONCREATE_ANCHOR, ONCREATE_ANCHOR + START_CALL, 1)
    write_lf(p, src)
    print("OK: AndroidApp.onCreate: start BhSteamUpdateChecker")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
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

    print("=== Background Steam update-badge checker ===")
    patch_app_start(root)
    print()

    print("Update-check patch applied successfully.")


if __name__ == "__main__":
    main()
