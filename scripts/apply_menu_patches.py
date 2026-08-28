#!/usr/bin/env python3
"""
Inject the "PC Vibration Settings" row into GameHub's three per-game menu
surfaces, plus the supporting per-game gameId capture and Compose-resource
label resolver. Supports stock 6.1.1 and 6.1.2 (surfaces are resolved by literal
+ signature shape, so R8 re-lettering no longer requires edits here).

Port of bannerhub-revanced's VibrationManifestPatch + VibrationMenuLabelPatch
+ MenuGameIdCapturePatch + VibrationMenuRowPatch — translated from ReVanced
Kotlin / dexlib2 introspection to apktool-tree text edits to fit this
fork's Python+apktool pipeline.

Menu surfaces patched (6.1.1 letters; 6.0.9 names in parentheses)
---------------------

  Lbk9;->a(Lfh9;ILkotlin/jvm/functions/Function0;ZLp1a;
           Landroidx/compose/runtime/Composer;I)V        (game detail More Menu; was Llc7;->a)
  Le1g;->f(Lf1g;Lkotlin/jvm/functions/Function1;
           Lkotlin/jvm/functions/Function0;ZLandroidx/compose/ui/Modifier;
           Landroidx/compose/runtime/Composer;I)V        (library-tile popup; was Lqqc;->f)
  Lfel;->o(Lfme;ZLoof;Loof;Lbpa;Lbpa;Lwff;Lu40;Lnof;Loof;)
           Ljava/util/List;                              (library-list 3-dot popup; was Lxdc;->b0)

Row data classes (6.1.1):
  Ll2h;(DrawableResource icon, String label, Function1 onClick[, Z])  More Menu (was Luhd;)
  Lizf;(String actionId, DrawableResource icon, String label,
        Function0 onClick)                                           tile popup (was Lxoc;)
  Lovg;(StringResource label, Function0 onClick, int)                 3-dot     (was Lpcd;)

6.0.9 -> 6.1.1 drift handled below. Beyond the usual R8 re-lettering, 6.1.1
STOPS OBFUSCATING the Kotlin stdlib, kotlinx.coroutines, Compose runtime and
Compose-Multiplatform resources, which removes most of the fragile anchors
this script used to carry:

  * list building is now the real kotlin.collections.CollectionsKt
    createListBuilder() / java.util.List.add() / build() (was the obfuscated
    Lbmc; builder + the Lv33;->u finalize). Both the More Menu and the 3-dot
    popup use it, so we inject immediately BEFORE the unique build() call and
    mutate the still-mutable ListBuilder in place — no register retyping and
    no move-result dance.
  * the tile popup builds its rows with CollectionsKt.listOfNotNull (was
    Lxq0;->a0), whose result IS immutable, so that one surface still replaces
    the register with an augmented list.
  * the resource resolver is the real
    org.jetbrains.compose.resources.StringResourcesKt (was Ly99;), and the
    resource descriptor is org.jetbrains.compose.resources.StringResource
    extending Lull; (was Llok; extending Lo4h;), whose field `a` still holds
    the "string:<key>" id.

Supporting patches
------------------

  Lorg/jetbrains/compose/resources/StringResourcesKt;->stringResource(
      Lorg/jetbrains/compose/resources/StringResource;
      Landroidx/compose/runtime/Composer;I)Ljava/lang/String;   (was Ly99;->Z)
      Resource-resolver short-circuit. Detects our sentinel key
      "string:bh_pc_vibration_label" and returns "PC Vibration Settings"
      before the Compose Multiplatform lookup runs. Required because
      appending to a .cvr alone isn't enough — the CMP runtime needs a
      manifest registration we don't easily get from apktool. Bannerhub
      documented this as a multi-day debugging journey; we mirror their
      final solution. (.locals 7, so v0 is free at index 0.)

  Index-0 captureGameId(p0) in all 3 menu builders (Lbk9.a, Le1g.f, Lfel.o)
      Reads the per-game id from the menu-data param and stashes it in
      BhMenuGameId so the click handler scopes BhVibrationSettingsActivity
      to the right game. Cross-process via SharedPreferences mirror
      because the main UI process and the launch process (":pcengine" on
      6.1.1, was ":wine") don't share statics.

NOTE ON SCOPE (6.1.1): the row itself opens BhVibrationSettingsActivity, whose
Mode/Intensity settings are consumed by BhVibrationController inside the Wine
process. On 6.1.1 the PC engine moved into a separately-downloaded plugin, so
until the rumble subsystem is re-established there the row is a UI stub. The
resolver short-circuit installed here is NOT optional either way — the VJoy
export/import patches depend on it.

  Manifest: registers com.xj.winemu.vibration.BhVibrationSettingsActivity
  (android:exported="false", no <intent-filter> — internal-only).

  CVR resources: appends "bh_pc_vibration_label" → "PC Vibration Settings"
  to each features.home Compose-resource locale bundle as a belt-and-
  braces fallback. The resolver short-circuit above is what actually
  carries the label at render time, but a missing CVR entry can still
  trigger an Lhc6 lookup attempt elsewhere, so we add it everywhere.

Java helpers required (compiled+dex'd in the same workflow step that
handles the other extension/ files):

  com.xj.winemu.common.BhMenuGameId
      captureGameId(Object), getCaptured()

  com.xj.winemu.vibration.BhMenuRowClick
      appendVibrationRowTo(Object), appendScdRowToTedList(Object),
      appendLibraryPopupRow(Object), maybeResolveCustomLabel(Object)
"""
import base64
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
# Patch primitive — same shape as apply_vibration_patches.py.
# ---------------------------------------------------------------------------

def _write_lf(path, content):
    # newline="" disables newline translation so \n is written as LF on every
    # platform. Critical for the .cvr bundles (LF-delimited with base64
    # values): on Windows the default write_text() translates \n -> \r\n,
    # leaving a stray \r after each base64 value that crashes the host's
    # decoder ("prohibited after the pad character"). LF is also correct for
    # smali/manifest, matching apktool's own output.
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)


def patch(path, old, new, label):
    p = Path(path)
    try:
        content = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = p.read_text(encoding="latin-1")
    if old not in content:
        print(f"ERROR: anchor not found in {path} for: {label}", file=sys.stderr)
        sys.exit(1)
    _write_lf(p, content.replace(old, new, 1))
    print(f"OK: {label}")


def read(path):
    p = Path(path)
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return p.read_text(encoding="latin-1")


def write(path, content):
    _write_lf(path, content)


# ---------------------------------------------------------------------------
# Version detection
# ---------------------------------------------------------------------------

# Anchor on the app class (whose dex bucket moved to smali_classes2 in 6.1.1)
# plus the PC-engine plugin host activity, which only exists from 6.1.1 — that
# pair distinguishes 6.1.1 from every earlier base this repo has supported.
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
# Manifest patch — register BhVibrationSettingsActivity
# ---------------------------------------------------------------------------

ACTIVITY_FQCN = "com.xj.winemu.vibration.BhVibrationSettingsActivity"
ACTIVITY_LINE = (
    f'        <activity android:name="{ACTIVITY_FQCN}" '
    f'android:exported="false" '
    f'android:theme="@android:style/Theme.Translucent.NoTitleBar" '
    f'android:configChanges="orientation|screenSize|keyboardHidden"/>'
)


def patch_manifest(manifest_path: Path) -> None:
    src = read(manifest_path)
    if f'android:name="{ACTIVITY_FQCN}"' in src:
        print("OK: BhVibrationSettingsActivity already registered")
        return
    if "    </application>" not in src:
        print("ERROR: could not find </application> close tag", file=sys.stderr)
        sys.exit(1)
    src = src.replace(
        "    </application>",
        ACTIVITY_LINE + "\n    </application>",
        1,
    )
    write(manifest_path, src)
    print(f"OK: registered <activity {ACTIVITY_FQCN}>")


# ---------------------------------------------------------------------------
# CVR resource patch — append "bh_pc_vibration_label" to features.home
# locale bundles.
# ---------------------------------------------------------------------------

LABEL_KEY = "bh_pc_vibration_label"
LABEL_VALUE = "PC Vibration Settings"
LABEL_B64 = base64.b64encode(LABEL_VALUE.encode("utf-8")).decode("ascii")
CVR_DIR = "assets/composeResources/com.xiaoji.egggame.features.home"


def patch_cvr_locales(root: Path) -> None:
    """Append the sentinel label line to every locale that exists. Locales
    not present in the APK are skipped; bannerhub's source listed 6 but
    only 5 are present in stock 6.0.4 (no values-en)."""
    cvr_dir = root / CVR_DIR
    if not cvr_dir.is_dir():
        print(f"WARN: {cvr_dir} not found — skipping CVR entries", file=sys.stderr)
        return
    line = f"string|{LABEL_KEY}|{LABEL_B64}\n"
    touched = 0
    for locale_dir in sorted(cvr_dir.iterdir()):
        if not locale_dir.is_dir() or not locale_dir.name.startswith("values"):
            continue
        cvr = locale_dir / "strings.commonMain.cvr"
        if not cvr.is_file():
            continue
        existing = read(cvr)
        if f"|{LABEL_KEY}|" in existing:
            continue
        terminator = "" if existing.endswith("\n") else "\n"
        write(cvr, existing + terminator + line)
        touched += 1
        print(f"OK: added {LABEL_KEY} to {locale_dir.name}/strings.commonMain.cvr")
    if not touched:
        print("OK: all CVR locales already had the label entry")


# ---------------------------------------------------------------------------
# Smali method locator + index-0 injector
# ---------------------------------------------------------------------------

def find_method(src: str, header_line: str) -> tuple[int, int]:
    """Return (start_idx, end_idx) of a method body in `src`. start_idx is
    the position of the .method line; end_idx is the position immediately
    after the .end method line. Fails fast if not found or unclosed."""
    start = src.find(header_line)
    if start < 0:
        return -1, -1
    end_marker = "\n.end method"
    end = src.find(end_marker, start)
    if end < 0:
        return -1, -1
    return start, end + len(end_marker)


def inject_at_method_entry(src: str, header_line: str, body: str, label: str) -> str:
    """Inject `body` immediately after the method's .locals line (so the
    injected instructions run at index 0). `body` should end with a
    trailing newline."""
    start, end = find_method(src, header_line)
    if start < 0:
        print(f"ERROR: method not found for: {label}\n  header={header_line!r}",
              file=sys.stderr)
        sys.exit(1)
    # Skip over the .method line and the following .locals line.
    after_header = src.find("\n", start) + 1
    locals_line_end = src.find("\n", after_header)
    if locals_line_end < 0 or ".locals" not in src[after_header:locals_line_end]:
        print(f"ERROR: no .locals line after method header: {label}",
              file=sys.stderr)
        sys.exit(1)
    # Inject after .locals + the following blank line (smali emits .locals
    # followed by exactly one blank line before the first instruction).
    insert_pos = locals_line_end + 1
    # Idempotency: if our marker is already present in this method, skip.
    if body.strip() and body.strip().splitlines()[0] in src[insert_pos:end]:
        print(f"OK: {label} (already injected)")
        return src
    new_src = src[:insert_pos] + "\n" + body + src[insert_pos:]
    print(f"OK: {label}")
    return new_src


def inject_after_anchor(
    src: str,
    header_line: str,
    anchor_pattern: str,
    body: str,
    label: str,
    *,
    last: bool = False,
) -> str:
    """Inject `body` immediately after a regex-matched `anchor_pattern`
    within the method identified by `header_line`. If `last`, match the
    LAST occurrence; otherwise the first. The match must be a single
    complete line (the trailing newline is included in the injection
    point)."""
    start, end = find_method(src, header_line)
    if start < 0:
        print(f"ERROR: method not found for: {label}\n  header={header_line!r}",
              file=sys.stderr)
        sys.exit(1)
    method_body = src[start:end]
    matches = list(re.finditer(anchor_pattern, method_body))
    if not matches:
        print(f"ERROR: anchor pattern not found inside method for: {label}\n"
              f"  pattern={anchor_pattern!r}", file=sys.stderr)
        sys.exit(1)
    m = matches[-1] if last else matches[0]
    anchor_abs_end = start + m.end()
    # Advance to the end of the matched line.
    if not method_body[m.end() - 1] == "\n":
        nl = src.find("\n", anchor_abs_end)
        if nl < 0:
            print(f"ERROR: no newline after anchor for: {label}", file=sys.stderr)
            sys.exit(1)
        anchor_abs_end = nl + 1
    # Idempotency: skip if body's first meaningful (non-blank, non-comment)
    # line is already immediately after the anchor.
    trailing = src[anchor_abs_end:anchor_abs_end + len(body) + 200]
    first_meaningful = next(
        (ln for ln in body.splitlines()
         if ln.strip() and not ln.strip().startswith("#")),
        None,
    )
    if first_meaningful and first_meaningful in trailing.split("\n", 8)[0:8]:
        print(f"OK: {label} (already injected)")
        return src
    new_src = src[:anchor_abs_end] + body + src[anchor_abs_end:]
    print(f"OK: {label}")
    return new_src


# ---------------------------------------------------------------------------
# captureGameId injection (3 methods, all index 0, all `{p0 .. p0}`)
# ---------------------------------------------------------------------------

CAPTURE_GAME_ID = (
    "    # BH menu-id capture — pin per-game scope for the injected "
    "PC Vibration Settings row.\n"
    "    invoke-static/range {p0 .. p0}, "
    "Lcom/xj/winemu/common/BhMenuGameId;->captureGameId(Ljava/lang/Object;)V\n"
)


# --- Menu-surface coordinates, shared by the capture and row passes. -------
#
# These are Compose builders whose class name, method name AND parameter types
# are all R8 letters, and every one of them drifted 6.1.1 -> 6.1.2:
#
#   More Menu    Lbk9;->a(Lfh9;I…Lp1a;…)   ->  Lgk9;->a(Ljh9;I…Lu1a;…)
#   tile popup   Le1g;->f(Lf1g;…)          ->  Lk1g;->f(Ll1g;…)
#   three-dot    Lfel;->o(Lfme;Z…)         ->  Liml;->i(Llme;Z…)   (name too)
#
# So nothing here can be pinned by name. What IS stable is the shape: each
# signature interleaves its letters with framework types the compiler cannot
# rename (Composer, Modifier, Function0/1, java.util.List), and two of the three
# classes hold a distinctive analytics/action-id string literal. Resolve on both
# and the letters stop mattering.
#
# The three-dot builder has no usable literal — it lives in a huge merged class
# (AES, DESUtils, protobuf helpers) — but its 10-parameter
# `(letter, Z, letter x8) -> java.util.List` shape is unique across every dex in
# both 6.1.1 and 6.1.2, so shape alone pins it.
MENU_SURFACES = {
    "more_menu": {
        "needles": ['"game_detail_more_menu"'],
        "sig": re.compile(
            r"^\.method public static final \w+\("
            r"L[\w$/]+;ILkotlin/jvm/functions/Function0;ZL[\w$/]+;"
            r"Landroidx/compose/runtime/Composer;I\)V$", re.M),
        "label": "game-details More Menu",
    },
    "tile_popup": {
        "needles": ['"local_detail_menu_settings"'],
        "sig": re.compile(
            r"^\.method public static final \w+\("
            r"L[\w$/]+;Lkotlin/jvm/functions/Function1;"
            r"Lkotlin/jvm/functions/Function0;ZLandroidx/compose/ui/Modifier;"
            r"Landroidx/compose/runtime/Composer;I\)V$", re.M),
        "label": "home tile long-press popup",
    },
    "three_dot": {
        "needles": [],
        "sig": re.compile(
            r"^\.method public static final \w+\("
            r"L[\w$/]+;Z(?:L[\w$/]+;){8}\)Ljava/util/List;$", re.M),
        "label": "library three-dot menu",
    },
}

# Filled in by resolve_menu_surfaces() before any pass runs.
MORE_MENU_FILE = MORE_MENU_HEADER = None
TILE_POPUP_FILE = TILE_POPUP_HEADER = None
THREE_DOT_FILE = THREE_DOT_HEADER = None


def resolve_menu_surfaces(root: Path) -> None:
    """Find each menu builder by literal + signature shape, and publish the
    results into the module-level coordinates the passes below consume.

    Fails loudly per surface on zero or multiple matches — with all three
    surfaces sharing one row-injection body, quietly patching the wrong builder
    would be worse than not building at all.
    """
    global MORE_MENU_FILE, MORE_MENU_HEADER
    global TILE_POPUP_FILE, TILE_POPUP_HEADER
    global THREE_DOT_FILE, THREE_DOT_HEADER

    resolved = {}
    for key, spec in MENU_SURFACES.items():
        hits = []
        for d in sorted(root.glob("smali*")):
            if not d.is_dir():
                continue
            for f in d.rglob("*.smali"):
                text = read(f)
                if not all(n in text for n in spec["needles"]):
                    continue
                for m in spec["sig"].finditer(text):
                    hits.append((f, m.group(0)))
        if not hits:
            print(f"ERROR: {spec['label']}: no method matching the expected "
                  f"signature shape"
                  + (f" in a class containing {spec['needles']}"
                     if spec["needles"] else "")
                  + " — re-anchor (the builder's parameter list changed, not "
                    "just its letters).", file=sys.stderr)
            sys.exit(1)
        if len(hits) > 1:
            print(f"ERROR: {spec['label']}: signature shape is non-unique "
                  f"({len(hits)} matches) — refusing to guess:\n"
                  + "\n".join(f"  {f.relative_to(root).as_posix()}: {sig}"
                              for f, sig in hits), file=sys.stderr)
            sys.exit(1)
        f, sig = hits[0]
        rel = f.relative_to(root).as_posix()
        resolved[key] = (rel, sig + "\n")
        print(f"    {spec['label']}: {rel}  {sig.split('final ', 1)[1]}")

    MORE_MENU_FILE, MORE_MENU_HEADER = resolved["more_menu"]
    TILE_POPUP_FILE, TILE_POPUP_HEADER = resolved["tile_popup"]
    THREE_DOT_FILE, THREE_DOT_HEADER = resolved["three_dot"]


def patch_menu_gameid_capture(root: Path) -> None:
    # 1. Game-details More Menu — Lbk9;->a (was Llc7;->a). p0 is still the
    #    menu-data param.
    p = root / MORE_MENU_FILE
    src = read(p)
    src = inject_at_method_entry(
        src, MORE_MENU_HEADER, CAPTURE_GAME_ID,
        "bk9.a: captureGameId(menuData)",
    )
    write(p, src)

    # 2. Library-tile popup — Le1g;->f (was Lqqc;->f). Confirmed by the stock
    #    action ids it builds: local_detail_launch / local_detail_menu_remove /
    #    local_detail_menu_settings / ...
    p = root / TILE_POPUP_FILE
    src = read(p)
    src = inject_at_method_entry(
        src, TILE_POPUP_HEADER, CAPTURE_GAME_ID,
        "e1g.f: captureGameId(menuData)",
    )
    write(p, src)

    # 3. Library-list 3-dot popup — Lfel;->o (was Lxdc;->b0). Same 10-param
    #    shape as b0 (obj, Z, X, X, Y, Y, Z, W, V, X).
    p = root / THREE_DOT_FILE
    src = read(p)
    src = inject_at_method_entry(
        src, THREE_DOT_HEADER, CAPTURE_GAME_ID,
        "fel.o: captureGameId(menuData)",
    )
    write(p, src)


# ---------------------------------------------------------------------------
# Menu row injections (3 surfaces)
# ---------------------------------------------------------------------------

VIB_HANDLER = "Lcom/xj/winemu/vibration/BhMenuRowClick;"

# Compose Multiplatform's resource resolver. Real (unobfuscated) names as of
# 6.1.1 — apply_export_controls_patches.py extends the sibling overloads in the
# same file, so keep these two in sync with that script's EXTRA_RESOLVERS.
# Compose Multiplatform keeps its real package name, but R8 shuffles which dex
# bucket it lands in on every build (6.1.1/6.1.2 smali_classes4 -> 6.2.0
# smali_classes2), so the bucket is discovered rather than hardcoded.
RESOLVER_REL = "org/jetbrains/compose/resources/StringResourcesKt.smali"


def resolver_path(root: Path) -> Path:
    hits = sorted(root.glob(f"smali*/{RESOLVER_REL}"))
    if not hits:
        print(f"ERROR: {RESOLVER_REL} not found in any smali* bucket — the Compose "
              f"Multiplatform resource runtime moved or was renamed; re-anchor.",
              file=sys.stderr)
        sys.exit(1)
    if len(hits) > 1:
        print(f"ERROR: {RESOLVER_REL} is non-unique: "
              f"{[h.relative_to(root).as_posix() for h in hits]}", file=sys.stderr)
        sys.exit(1)
    return hits[0]
RESOLVER_HEADER = (
    ".method public static final stringResource("
    "Lorg/jetbrains/compose/resources/StringResource;"
    "Landroidx/compose/runtime/Composer;I)Ljava/lang/String;\n"
)


BUILD_CALL_RE = re.compile(
    r"    invoke-static \{(v\d+)\}, Lkotlin/collections/CollectionsKt;->"
    r"build\(Ljava/util/List;\)Ljava/util/List;\n"
)
LISTOF_NOTNULL_RE = re.compile(
    r"    invoke-static \{v\d+\}, Lkotlin/collections/CollectionsKt;->"
    r"listOfNotNull\(\[Ljava/lang/Object;\)Ljava/util/List;\n"
)


def _inject_before_build(root: Path, rel_file: str, header: str,
                         java_method: str, label: str) -> None:
    """Both the More Menu and the 3-dot popup assemble their rows with
    CollectionsKt.createListBuilder() / List.add(...) / CollectionsKt.build(...).
    The builder is still MUTABLE right before build(), so we hand it to Java and
    let the helper append in place — a void call, so no register is retyped and
    dex verification sees nothing new at the surrounding merge points. (6.0.9
    had to replace registers because its finalize returned a fresh Lbmc;.)

    Anchoring on the unique build() call also pins the list register for us,
    instead of hardcoding it as the 6.0.9 script did (v3/v4)."""
    p = root / rel_file
    src = read(p)
    start, end = find_method(src, header)
    if start < 0:
        print(f"ERROR: {label}: method not found\n  header={header!r}",
              file=sys.stderr)
        sys.exit(1)
    body = src[start:end]
    matches = list(BUILD_CALL_RE.finditer(body))
    if len(matches) != 1:
        print(f"ERROR: {label}: expected exactly 1 CollectionsKt.build() inside "
              f"the method, found {len(matches)} — refusing to guess the menu "
              f"list.", file=sys.stderr)
        sys.exit(1)
    m = matches[0]
    list_reg = m.group(1)
    inject = (
        "    # BH menu row: append PC Vibration Settings to the still-mutable\n"
        "    # ListBuilder, immediately before CollectionsKt.build() seals it.\n"
        f"    invoke-static {{{list_reg}}}, {VIB_HANDLER}->"
        f"{java_method}(Ljava/lang/Object;)V\n"
        "\n"
    )
    if f"{VIB_HANDLER}->{java_method}" in body:
        print(f"OK: {label} (already injected)")
        return
    abs_pos = start + m.start()
    write(p, src[:abs_pos] + inject + src[abs_pos:])
    print(f"OK: {label}  [list register {list_reg}]")


def patch_menu_rows(root: Path) -> None:
    # ----- Injection 1: Lbk9;->a (was Llc7;->a) — game-details More Menu.
    #
    # Why hand off to Java instead of constructing the row (Ll2h;, 6.0.9
    # Luhd;) inline: an early 6.0.4-era attempt that built the row directly in
    # smali hit an ART verifier failure at a Compose merge point (host
    # Function1 type vs the BhMenuRowClick proxy type). The single-instruction
    # invoke-static is verifier-invisible at the surrounding type-flow level.
    _inject_before_build(
        root, MORE_MENU_FILE, MORE_MENU_HEADER,
        "appendVibrationRowTo",
        "bk9.a: append PC Vibration Settings to More Menu list",
    )

    # ----- Injection 2: Le1g;->f (was Lqqc;->f) — library-tile popup.
    # This surface builds its rows with
    #     filled-new-array {...}, [Lizf;
    #     invoke-static {vN}, CollectionsKt;->listOfNotNull([Object;)List;
    #     move-result-object vM
    # and listOfNotNull's result is IMMUTABLE, so this is the one surface where
    # we still have to replace the register with an augmented list. vM is
    # consumed as Composer.changed(Object) + check-cast Iterable, so returning
    # Ljava/util/List; verifies (6.0.9 had to return ArrayList because the host
    # called ArrayList.size()/get(I) on it).
    p = root / TILE_POPUP_FILE
    src = read(p)
    start, end = find_method(src, TILE_POPUP_HEADER)
    if start < 0:
        print("ERROR: e1g.f method not found", file=sys.stderr)
        sys.exit(1)
    body = src[start:end]
    listof_matches = list(LISTOF_NOTNULL_RE.finditer(body))
    if len(listof_matches) != 1:
        print("ERROR: e1g.f: expected exactly 1 CollectionsKt.listOfNotNull "
              f"inside the method, found {len(listof_matches)}", file=sys.stderr)
        sys.exit(1)
    cursor = listof_matches[0].end()
    mo = re.compile(r"    move-result-object (v\d+|p\d+)\n").search(body, cursor)
    if not mo or mo.start() - cursor > 200:
        print("ERROR: no move-result-object after e1g.f listOfNotNull within "
              "window", file=sys.stderr)
        sys.exit(1)
    list_reg = mo.group(1)
    after_move = start + mo.end()
    inject = (
        "\n"
        "    # BH menu row: replace the immutable listOfNotNull result with an\n"
        "    # augmented list that includes our row.\n"
        f"    invoke-static {{{list_reg}}}, {VIB_HANDLER}->"
        f"appendTilePopupRow(Ljava/lang/Object;)Ljava/util/List;\n"
        f"    move-result-object {list_reg}\n"
    )
    if VIB_HANDLER + "->appendTilePopupRow" in body:
        print("OK: e1g.f (already injected)")
    else:
        src = src[:after_move] + inject + src[after_move:]
        write(p, src)
        print(f"OK: e1g.f: augment listOfNotNull row list  [register {list_reg}]")

    # ----- Injection 3: Lfel;->o (was Lxdc;->b0) — library-list 3-dot popup.
    # Same createListBuilder/add/build shape as the More Menu, so the same
    # in-place append applies.
    _inject_before_build(
        root, THREE_DOT_FILE, THREE_DOT_HEADER,
        "appendLibraryPopupRowInPlace",
        "fel.o: append PC Vibration Settings to library-list popup",
    )


# ---------------------------------------------------------------------------
# Resource-resolver short-circuit — Lxd3;->l1(Lell;Lv83;I)Ljava/lang/String;
# ---------------------------------------------------------------------------

def patch_resolver(root: Path) -> None:
    """Inject a head-block in Lxd3.l1 that checks our sentinel key BEFORE
    the Compose Multiplatform lookup runs. The lookup would otherwise
    throw "Resource with ID='string:bh_pc_vibration_label' not found"
    because CMP requires a manifest registration alongside the .cvr,
    and apktool can't produce that registration cleanly. Bannerhub
    documented this as a multi-day debugging journey (pre14 / pre15);
    we mirror their final solution: short-circuit at the resolver.

    The :bh_resolve_fallthrough label trick at the END of the snippet
    is the documented workaround for an upstream patcher bug with
    addInstructionsWithLabels at non-zero indices; injecting at index 0
    with the label trailing means the label resolves to "the first
    original instruction" — which is what we want.

    6.1.1: R8 no longer obfuscates Compose Multiplatform's resource runtime, so
    the resolver is the REAL
    org.jetbrains.compose.resources.StringResourcesKt#stringResource(
        StringResource, Composer, int)
    (6.0.9 Ly99;->Z(Llok;Lgm3;I), 6.0.4 Lxd3;->l1(Lell;Lv83;I)). Being a real
    library name rather than an R8 letter, this anchor should now survive base
    bumps. The injected body is unchanged (v0 is free under .locals 7)."""
    p = resolver_path(root)
    src = read(p)
    header = RESOLVER_HEADER
    body = (
        "    # BH resource-resolver short-circuit: return our PC Vibration\n"
        "    # Settings label string before the CMP lookup would throw.\n"
        f"    invoke-static {{p0}}, {VIB_HANDLER}->"
        "maybeResolveCustomLabel(Ljava/lang/Object;)Ljava/lang/String;\n"
        "    move-result-object v0\n"
        "    if-eqz v0, :bh_resolve_fallthrough\n"
        "    return-object v0\n"
        "    :bh_resolve_fallthrough\n"
    )
    src = inject_at_method_entry(
        src, header, body,
        "StringResourcesKt.stringResource: short-circuit sentinel key resolution",
    )
    write(p, src)


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

    print("=== Menu surfaces (resolved by literal + signature shape) ===")
    resolve_menu_surfaces(root)
    print()

    print("=== Manifest ===")
    patch_manifest(root / "AndroidManifest.xml")
    print()

    print("=== CVR resource label ===")
    patch_cvr_locales(root)
    print()

    print("=== captureGameId at menu builder entry ===")
    patch_menu_gameid_capture(root)
    print()

    print("=== Resolver short-circuit ===")
    patch_resolver(root)
    print()

    print("=== Menu row injections ===")
    patch_menu_rows(root)
    print()

    print("All per-game menu patches applied successfully.")


if __name__ == "__main__":
    main()
