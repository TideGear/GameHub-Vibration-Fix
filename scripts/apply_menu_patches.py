#!/usr/bin/env python3
"""
Inject the "PC Vibration Settings" row into GameHub's three per-game menu
surfaces, plus the supporting per-game gameId capture and Compose-resource
label resolver. Supports stock 6.0.9 only.

Port of bannerhub-revanced's VibrationManifestPatch + VibrationMenuLabelPatch
+ MenuGameIdCapturePatch + VibrationMenuRowPatch — translated from ReVanced
Kotlin / dexlib2 introspection to apktool-tree text edits to fit this
fork's Python+apktool pipeline.

Menu surfaces patched (6.0.9 letters; 6.0.4 names in parentheses)
---------------------

  Llc7;->a(Lpa7;ILr47;Lrq7;Lgm3;I)V                      (game detail More Menu; was Lx57;->a)
  Lqqc;->f(Lrqc;Lt47;Lr47;ZLfhd;Lgm3;I)V                 (library-tile popup; was Lted;->f)
  Lxdc;->b0(Ljhb;Z…)Ljava/util/List;                     (library-list 3-dot popup; was Lpzc;->j0)

6.0.7 drift handled below: R8 letters regenerated; .line directives stripped;
the More-Menu/3-dot list builder is Lbmc; (was Lx9d;) and finalize moved to a
STATIC helper Lv33;->u (was instance Lx9d;->i); the tile-popup listOf helper
is Lxq0;->a0 returning ArrayList (was Lqs2;->H returning List); the resolver
class is Ly99; (was Lxd3;). The Composable signatures also gained params (an
int + extra callbacks), but the captured menu-data param is still p0.

Supporting patches
------------------

  Ly99;->Z(Llok;Lgm3;I)Ljava/lang/String;   (was Lxd3;->l1(Lell;Lv83;I)…)
      Resource-resolver short-circuit. Detects our sentinel key
      "string:bh_pc_vibration_label" and returns "PC Vibration Settings"
      before the Compose Multiplatform lookup runs. Required because
      appending to a .cvr alone isn't enough — the CMP runtime needs a
      manifest registration we don't easily get from apktool. Bannerhub
      documented this as a multi-day debugging journey; we mirror their
      final solution.

  Index-0 captureGameId(p0) in all 3 menu builders (Llc7.a, Lqqc.f, Lxdc.b0)
      Reads the per-game id from the menu-data param and stashes it in
      BhMenuGameId so the click handler scopes BhVibrationSettingsActivity
      to the right game. Cross-process via SharedPreferences mirror
      because the main UI process and the ":wine" launch process don't
      share statics.

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


def patch_menu_gameid_capture(root: Path) -> None:
    # 1. Game-details More Menu — Llc7;->a(Lpa7;ILr47;Lrq7;Lgm3;I)V (was
    #    Lx57;->a(Lf37;Lpo7;Lv83;I)V). p0 is still the menu-data param.
    p = root / "smali_classes4/lc7.smali"
    src = read(p)
    src = inject_at_method_entry(
        src,
        ".method public static final a(Lpa7;ILr47;Lrq7;Lgm3;I)V\n",
        CAPTURE_GAME_ID,
        "lc7.a: captureGameId(menuData)",
    )
    write(p, src)

    # 2. Library-tile popup — Lqqc;->f(Lrqc;Lt47;Lr47;ZLfhd;Lgm3;I)V (was
    #    Lted;->f(Lued;Lpw6;Lnw6;ZLt9e;Lv83;I)V).
    p = root / "smali_classes4/qqc.smali"
    src = read(p)
    src = inject_at_method_entry(
        src,
        ".method public static final f(Lrqc;Lt47;Lr47;ZLfhd;Lgm3;I)V\n",
        CAPTURE_GAME_ID,
        "qqc.f: captureGameId(menuData)",
    )
    write(p, src)

    # 3. Library-list 3-dot popup — Lxdc;->b0(Ljhb;Z…)Ljava/util/List; (was
    #    Lpzc;->j0(...); also moved smali_classes5 -> smali_classes4).
    p = root / "smali_classes4/xdc.smali"
    src = read(p)
    src = inject_at_method_entry(
        src,
        ".method public static final b0(Ljhb;ZLobc;Lobc;Lgj8;Lgj8;Lplb;Ltz;"
        "Lnbc;Lobc;)Ljava/util/List;\n",
        CAPTURE_GAME_ID,
        "xdc.b0: captureGameId(menuData)",
    )
    write(p, src)


# ---------------------------------------------------------------------------
# Menu row injections (3 surfaces)
# ---------------------------------------------------------------------------

VIB_HANDLER = "Lcom/xj/winemu/vibration/BhMenuRowClick;"


def patch_menu_rows(root: Path) -> None:
    # ----- Injection 1: Llc7;->a (6.0.4 Lx57;->a) — append a row to the
    # game-details More Menu by handing the list builder (v3) to the Java
    # helper.
    #
    # Anchor: the LAST `invoke-virtual {v3, v?}, Lbmc;->add(Object)Z` call
    # within the method body. The whole-menu Composable contains a chain of
    # such add() calls (one per row); appending after the last one puts our
    # row at the bottom of the menu.
    #
    # Why hand off to Java instead of constructing the row item (Ltyc;,
    # 6.0.4 Liae;) inline: a first attempt that built the row directly in
    # smali hit an ART verifier failure at a Compose merge point (the
    # Function1 type Lt47; vs BhMenuRowClick proxy type unification). The
    # single-instruction invoke-static is verifier-invisible at the
    # surrounding type-flow level.
    p = root / "smali_classes4/lc7.smali"
    src = read(p)
    # Anchor: the LAST `invoke-virtual {vN, vM}, Lbmc;->add(Object)Z` line in
    # the method body (Lbmc; is the 6.0.7 list builder, was Lx9d;). The list
    # builder lives in v3 in 6.0.7 (was v4 in 6.0.4); the static finalize
    # Lv33;->u(List) follows shortly after the last add, so appending after
    # the last add() puts our row at the bottom of the menu.
    src = inject_after_anchor(
        src,
        ".method public static final a(Lpa7;ILr47;Lrq7;Lgm3;I)V\n",
        r"    invoke-virtual \{v\d+, v\d+\}, Lbmc;->add\(Ljava/lang/Object;\)Z\n",
        "\n"
        "    # BH menu row: append PC Vibration Settings via reflection helper.\n"
        f"    invoke-static {{v3}}, {VIB_HANDLER}->"
        "appendVibrationRowTo(Ljava/lang/Object;)V\n",
        "lc7.a: append PC Vibration Settings to More Menu list",
        last=True,
    )
    write(p, src)

    # ----- Injection 2: Lqqc;->f (6.0.4 Lted;->f) — replace the ArrayList
    # returned by Lxq0;->a0([Object;)ArrayList with an augmented ArrayList.
    # The next instruction after the static is `move-result-object vN` where
    # vN is the list register; we capture our helper's return into the same
    # register so downstream code (which calls ArrayList.size()/get(I))
    # iterates the augmented list.
    #
    # Anchor pattern in source:
    #     invoke-static {v0}, Lxq0;->a0([Ljava/lang/Object;)Ljava/util/ArrayList;
    #     <move-result-object vN>
    # Inject our helper call + matching move-result after the move-result.
    p = root / "smali_classes4/qqc.smali"
    src = read(p)
    # 6.0.7: the listOf helper is Lxq0;->a0([Object;) returning ArrayList (was
    # Lqs2;->H([Object;) returning List). The result register is consumed
    # downstream as an ArrayList (size()/get(I)), so appendScdRowToTedList
    # returns ArrayList and we keep the register ArrayList-typed (returning a
    # List here would fail dex verification at the downstream ArrayList calls).
    listof_call = (
        "    invoke-static {v1}, Lxq0;->a0([Ljava/lang/Object;)Ljava/util/ArrayList;\n"
    )
    start, end = find_method(
        src,
        ".method public static final f(Lrqc;Lt47;Lr47;ZLfhd;Lgm3;I)V\n",
    )
    if start < 0:
        print("ERROR: qqc.f method not found", file=sys.stderr)
        sys.exit(1)
    body = src[start:end]
    listof_rel = body.find(listof_call)
    if listof_rel < 0:
        print("ERROR: qqc.f Lxq0;->a0 call not found", file=sys.stderr)
        sys.exit(1)
    # Find the move-result-object on the next line.
    cursor = listof_rel + len(listof_call)
    move_result_pat = re.compile(r"    move-result-object (v\d+|p\d+)\n")
    mo = move_result_pat.search(body, cursor)
    if not mo or mo.start() - cursor > 200:
        print("ERROR: no move-result-object after qqc.f Lxq0;->a0 within window",
              file=sys.stderr)
        sys.exit(1)
    list_reg = mo.group(1)
    after_move = start + mo.end()
    inject = (
        "\n"
        "    # BH menu row: replace the listOf ArrayList with an augmented "
        f"ArrayList that includes our row.\n"
        f"    invoke-static {{{list_reg}}}, {VIB_HANDLER}->"
        f"appendScdRowToTedList(Ljava/lang/Object;)Ljava/util/ArrayList;\n"
        f"    move-result-object {list_reg}\n"
    )
    if VIB_HANDLER + "->appendScdRowToTedList" in src[after_move:after_move + 400]:
        print("OK: qqc.f (already injected)")
    else:
        src = src[:after_move] + inject + src[after_move:]
        write(p, src)
        print("OK: qqc.f: augment Lxq0;->a0 list with PC Vibration Settings row")

    # ----- Injection 3: Lxdc;->b0 — augment the list right before the
    # method's final return-object. 6.0.7 finalize moved from the instance
    # call Lx9d;->i()Lx9d; to the STATIC helper Lv33;->u(Ljava/util/List;)Lbmc;.
    # The pattern is:
    #     invoke-static {vN}, Lv33;->u(Ljava/util/List;)Lbmc;
    #     move-result-object pN  (or vN)
    #     return-object pN       (or vN)
    # Inject between the move-result-object and the return-object.
    p = root / "smali_classes4/xdc.smali"
    src = read(p)
    b0_header = (
        ".method public static final b0(Ljhb;ZLobc;Lobc;Lgj8;Lgj8;Lplb;Ltz;"
        "Lnbc;Lobc;)Ljava/util/List;\n"
    )
    start, end = find_method(src, b0_header)
    if start < 0:
        print("ERROR: xdc.b0 method not found", file=sys.stderr)
        sys.exit(1)
    body = src[start:end]
    # Last `invoke-static {vN}, Lv33;->u(Ljava/util/List;)Lbmc;` in the body
    # (the finalize just before return-object; Lv33;->u appears elsewhere in
    # the file but exactly once inside b0).
    finalize_pat = re.compile(
        r"    invoke-static \{v\d+\}, Lv33;->u\(Ljava/util/List;\)Lbmc;\n"
    )
    finalize_matches = list(finalize_pat.finditer(body))
    if not finalize_matches:
        print("ERROR: xdc.b0 Lv33;->u finalize call not found", file=sys.stderr)
        sys.exit(1)
    finalize_end = finalize_matches[-1].end()
    # The next move-result-object captures the list into a register.
    after_finalize = body[finalize_end:]
    mo = re.search(r"    move-result-object (v\d+|p\d+)\n", after_finalize)
    if not mo:
        print("ERROR: no move-result-object after xdc.b0 Lv33;->u finalize",
              file=sys.stderr)
        sys.exit(1)
    list_reg = mo.group(1)
    inject_rel = finalize_end + mo.end()
    inject_abs = start + inject_rel
    inject = (
        "\n"
        "    # BH menu row: augment library-list popup with PC Vibration Settings.\n"
        f"    invoke-static {{{list_reg}}}, {VIB_HANDLER}->"
        f"appendLibraryPopupRow(Ljava/lang/Object;)Ljava/util/List;\n"
        f"    move-result-object {list_reg}\n"
    )
    if VIB_HANDLER + "->appendLibraryPopupRow" in src[inject_abs:inject_abs + 400]:
        print("OK: xdc.b0 (already injected)")
    else:
        src = src[:inject_abs] + inject + src[inject_abs:]
        write(p, src)
        print("OK: xdc.b0: augment library-list popup with PC Vibration row")


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

    6.0.7: the resolver class moved Lxd3; (smali/) -> Ly99;
    (smali_classes3/) and the method l1(Lell;Lv83;I) -> c0(Llok;Lgm3;I).
    The injected body is unchanged (v0 is free under .locals 6)."""
    p = root / "smali_classes3/y99.smali"
    src = read(p)
    header = (
        ".method public static final Z(Llok;Lgm3;I)Ljava/lang/String;\n"
    )
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
        src, header, body, "y99.c0: short-circuit sentinel key resolution"
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
    print(f"Detected GameHub base version: {version}")
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
