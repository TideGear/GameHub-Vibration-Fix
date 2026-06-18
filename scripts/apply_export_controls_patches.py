#!/usr/bin/env python3
"""
VJoy on-screen-controls export/import to/from local files.

Replaces GameHub's cloud-only "share-by-code" flow for on-screen controller
layouts with portable local `.gtheme` files. No cloud account, no HTTP.
Supports stock 6.0.9 only.

The four bytecode hooks are URL-fragment-anchored (not R8-letter-anchored),
so they re-discover their methods unchanged on 6.0.9 (the VJoy share repo
moved Lrqn; -> Lkkm; -> Lqkm; -> Laun; but the script locates it by the
vcontroller/* URL strings). Only the resolver dependency moved: the short-circuit installed by
apply_menu_patches.py is now on Ly99;->c0 (was Lxd3;->l1), and the sibling
resolver variants extended here are Ly99;->d0/J/K (was Lxd3;->m1/P0/Q0).

Port of bannerhub-revanced's ExportControlsPatch + ExportControlsManifestPatch
+ ExportControlsResourcesPatch (commit ab43968) — translated from ReVanced
Kotlin / dexlib2 introspection to apktool-tree text edits to fit this fork's
Python+apktool pipeline.

EXPORT
------
  uploadGtheme hook (the /vcontroller/uploadGtheme POST), fired BEFORE the
  layout is uploaded to Tencent COS. BhVjoyShareHook.interceptUpload reflects
  the DTO graph for the okio.Path of the freshly-serialized .gtheme, reads
  those PRISTINE pre-CDN bytes (full UTF-8 fidelity), and saves them via SAF
  (ACTION_CREATE_DOCUMENT). The user-typed name from the "Name Profile"
  dialog is captured at the head of the share-name method and used as the
  SAF suggested filename.

  shareMap hook (the /vcontroller/shareMap publish): interceptShare THROWS,
  which the host catches — it deletes its temp file and treats it as a
  failure, so there's no cloud upload, no "Cloud Backup Code" dialog, and no
  navigation to the cloud-share tab.

IMPORT
------
  The "Import Layout" share-code dialog is skipped entirely: the shared
  composition-time string resolver (Ly99;->c0, hooked by
  apply_menu_patches.py) detects the dialog title key and fires a SAF file
  picker (ACTION_OPEN_DOCUMENT) immediately — see
  BhMenuRowClick.maybeResolveCustomLabel, which calls
  BhVjoyShareHook.kickImportFromDialogOpen(). getMapByShareCode is also
  hooked as a defensive fallback in case the resource key is renamed by a
  future host update.

ANCHORING STRATEGY
------------------
Unlike the sibling scripts, this one does NOT hardcode R8-mangled class
letters. It locates the four hook sites by SERVER-STABLE URL fragments
(`vcontroller/shareMap`, `/getMapByShareCode`, `/uploadGtheme`) and by the
call-relationship between the share-name method and the upload method. On
6.0.7 the VJoy share repo resolves to Lkkm; (was Lrqn; on 6.0.4) with i/d/j
the share/apply/upload methods, but the script never hardcodes those: the
URL fragments survive R8 reshuffles and the dex-collapse (6->5), so the same
locator code finds the methods regardless of the regenerated letters.

Register choices (p1 for share/upload, p3 for the name capture) are the same
device-verified offsets the upstream Kotlin patch uses: the repo methods are
instance suspend methods (p0 = this), so declared param 0 is p1; the
share-name method's first declared param is a wide `long` gameId (two slots),
putting the String typedName at p3.

Java helpers required (compiled+dex'd in the same workflow step that handles
the other extension/ files):

  com.xj.winemu.exportcontrols.BhVjoyShareHook
      interceptShare(Object), interceptApply(), interceptUpload(Object),
      captureShareName(String), kickImportFromDialogOpen()
  com.xj.winemu.exportcontrols.BhSafProxyActivity   (manifest activity)
  com.xj.winemu.exportcontrols.BhVjoyImporter
  com.xj.winemu.exportcontrols.BhVjoyJson

Depends on apply_menu_patches.py having installed the Ly99;->c0 resolver
short-circuit (the import-dialog skip and all label relabels ride on it).
"""
import base64
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# IO helpers — same shape as the sibling scripts.
# ---------------------------------------------------------------------------

def read(path):
    p = Path(path)
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return p.read_text(encoding="latin-1")


def write(path, content):
    # newline="" forces LF on every platform. The .cvr bundles are
    # LF-delimited with base64 values; on Windows the default write_text()
    # translates \n -> \r\n, leaving a stray \r after each base64 value that
    # crashes the host's decoder ("prohibited after the pad character"). LF is
    # also correct for smali, matching apktool's output.
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Version detection (parity with the sibling scripts — 6.0.4 only).
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
        die("could not detect GameHub version — none of the known smali "
            "layout probes matched.")
    return matches[0]


# ---------------------------------------------------------------------------
# Smali descriptor parsing + method iteration.
# ---------------------------------------------------------------------------

METHOD_RE = re.compile(r"^\.method\b[^\n]*\n", re.M)
CONST_STRING_RE = re.compile(
    r'const-string(?:/jumbo)?\s+[vp]\d+,\s*"((?:[^"\\]|\\.)*)"'
)
# invoke-{kind}[/range] {regs}, LClass;->name(...)Ret   → capture "LClass;->name"
INVOKE_RE = re.compile(
    r"invoke-[a-z-]+(?:/range)?\s+\{[^}]*\},\s*(L[^;]+;->[^(]+)\("
)
CLASS_RE = re.compile(r"^\.class\b[^\n]*?(L[^;\s]+;)\s*$", re.M)


def class_of(src: str):
    m = CLASS_RE.search(src)
    return m.group(1) if m else None


def split_descriptor(header_line: str):
    """Return (param_descriptor_str, return_type) from a .method header."""
    lp = header_line.index("(")
    rp = header_line.index(")", lp)
    return header_line[lp + 1:rp], header_line[rp + 1:].strip()


def method_name(header_line: str) -> str:
    return header_line[:header_line.index("(")].split()[-1]


def parse_param_types(params_str: str):
    """Split a JVM param descriptor into a list of type strings. Wide types
    (J/D) count as a single declared param — matching dexlib2's
    parameterTypes.size used by the upstream Kotlin predicates."""
    types = []
    i = 0
    n = len(params_str)
    while i < n:
        c = params_str[i]
        if c == "[":
            j = i
            while j < n and params_str[j] == "[":
                j += 1
            if j < n and params_str[j] == "L":
                k = params_str.index(";", j)
                types.append(params_str[i:k + 1])
                i = k + 1
            else:
                types.append(params_str[i:j + 1])
                i = j + 1
        elif c == "L":
            k = params_str.index(";", i)
            types.append(params_str[i:k + 1])
            i = k + 1
        else:
            types.append(c)
            i += 1
    return types


def iter_methods(src: str):
    """Yield (header_line, start, end) for each method with a body. start is
    the offset of the .method line; end is just past the matching
    .end method."""
    for m in METHOD_RE.finditer(src):
        start = m.start()
        end_marker = src.find("\n.end method", m.end())
        if end_marker < 0:
            continue
        yield src[m.start():m.end()].rstrip("\n"), start, end_marker + len("\n.end method")


def smali_files(root: Path):
    """All .smali files across every smali* dir, sorted for determinism."""
    out = []
    for d in sorted(root.glob("smali*")):
        if d.is_dir():
            out.extend(sorted(d.rglob("*.smali")))
    return out


# ---------------------------------------------------------------------------
# Method locators (URL fragment + call-relationship anchors).
#
# Anchors record the method's STABLE header line (e.g.
# ".method public final i(Lsrn;Lci3;)Ljava/lang/Object;"), not byte offsets,
# so injection re-finds the method in the current file content — multiple
# hooks into the same file (all three URL methods live on the repo class)
# compose correctly without stale offsets.
# ---------------------------------------------------------------------------

class Anchor:
    __slots__ = ("path", "cls", "header", "params", "ret")

    def __init__(self, path, cls, header, params, ret):
        self.path = path
        self.cls = cls
        self.header = header
        self.params = params
        self.ret = ret

    def ref(self):
        return f"{self.cls}->{method_name(self.header)}"


def _assert_unique(hits, label, criteria):
    if not hits:
        die(f"{label}: no method matched ({criteria}).")
    if len(hits) > 1:
        locs = "\n  ".join(f"{h.ref()}  in {h.path.name}" for h in hits)
        die(f"{label}: expected exactly 1 match for ({criteria}) but found "
            f"{len(hits)}:\n  {locs}")


def locate_url_anchors(files, specs):
    """Single pass over `files`. `specs` is a list of (fragment, want_params,
    label). Returns {fragment: Anchor}. Each fragment must resolve to exactly
    one Object-returning method (with `want_params` declared params) whose body
    emits a const-string containing the fragment."""
    needles = [(s[0], s[0].encode("utf-8")) for s in specs]
    hits = {frag: [] for frag, _, _ in specs}
    for f in files:
        data = f.read_bytes()
        present = [frag for frag, nb in needles if nb in data]
        if not present:
            continue
        src = read(f)  # newline-normalized (must match the inject-time read)
        cls = class_of(src)
        for header, start, end in iter_methods(src):
            body = src[start:end]
            const_vals = [m.group(1) for m in CONST_STRING_RE.finditer(body)]
            if not const_vals:
                continue
            params_str, ret = split_descriptor(header)
            params = parse_param_types(params_str)
            for frag, want_params, _ in specs:
                if frag not in present:
                    continue
                if ret != "Ljava/lang/Object;" or len(params) != want_params:
                    continue
                if any(frag in v for v in const_vals):
                    hits[frag].append(Anchor(f, cls, header, params, ret))
    out = {}
    for frag, want_params, label in specs:
        _assert_unique(hits[frag], label,
                       f"const-string ~ {frag!r}, returns Object, "
                       f"{want_params} params")
        out[frag] = hits[frag][0]
    return out


def locate_caller(files, callee_ref, label):
    """Single pass. Find the unique Object-returning method whose first
    declared param is a wide `long` (the gameId) and whose second declared
    param is a String (the typed profile name), and whose body invokes
    `callee_ref` (LClass;->name). This is the share/export entry that builds
    the .gtheme then uploads it.

    The (long gameId, String name) shape — NOT the total param count — is the
    device-verified invariant that pins this method AND justifies the p3
    register for captureShareName (a wide long occupies p1+p2, so the String
    name lands at p3). 6.0.4 had 4 declared params here; 6.0.9's Lsun;->j has
    5 (J, String, Z, Lvin;, Continuation) — the extra params trail the String,
    so p3 still holds. The other caller of the upload method (a 1-param
    coroutine SuspendLambda) is excluded by the param-shape requirement."""
    needle = callee_ref.encode("utf-8")
    hits = []
    for f in files:
        data = f.read_bytes()
        if needle not in data:
            continue
        src = read(f)  # newline-normalized (must match the inject-time read)
        cls = class_of(src)
        for header, start, end in iter_methods(src):
            body = src[start:end]
            if not any(m.group(1) == callee_ref for m in INVOKE_RE.finditer(body)):
                continue
            params_str, ret = split_descriptor(header)
            params = parse_param_types(params_str)
            if (ret == "Ljava/lang/Object;" and len(params) >= 2
                    and params[0] == "J" and params[1] == "Ljava/lang/String;"):
                hits.append(Anchor(f, cls, header, params, ret))
    _assert_unique(hits, label, f"invokes {callee_ref}, returns Object, "
                                f"param[0]=long gameId, param[1]=String name")
    return hits[0]


# ---------------------------------------------------------------------------
# Index-0 instruction injector (locates by stable header line; handles
# .locals or .registers; skips any leading .param / .annotation / .prologue
# directive block so injected instructions land at instruction index 0).
# ---------------------------------------------------------------------------

REG_DIRECTIVE_RE = re.compile(r"^[ \t]*\.(?:locals|registers)\b[^\n]*\n", re.M)


def inject_at_entry(anchor: Anchor, body: str, label: str) -> None:
    src = read(anchor.path)
    start = src.find(anchor.header)
    if start < 0:
        die(f"{label}: method header vanished from {anchor.path.name}: "
            f"{anchor.header!r}")
    end_marker = src.find("\n.end method", start)
    if end_marker < 0:
        die(f"{label}: unclosed method {anchor.ref()}")
    end = end_marker + len("\n.end method")

    reg = REG_DIRECTIVE_RE.search(src, src.find("\n", start) + 1, end)
    if not reg:
        die(f"{label}: no .locals/.registers line in {anchor.ref()}")
    pos = reg.end()
    # Skip any leading .param / .annotation block / .prologue before the first
    # instruction or .line. Inserting instructions amid those directives is
    # invalid smali; obfuscated methods usually have none, but be safe.
    while pos < end:
        nl = src.find("\n", pos)
        if nl < 0 or nl > end:
            break
        line = src[pos:nl].strip()
        if line == "" or line.startswith(".prologue") or line.startswith(".param"):
            pos = nl + 1
            continue
        if line.startswith(".annotation"):
            ae = src.find(".end annotation", pos)
            if ae < 0 or ae > end:
                break
            pos = src.find("\n", ae) + 1
            continue
        break
    # Idempotency: bail if our first meaningful (non-blank, non-comment) line
    # already appears in the method body.
    marker = next((ln for ln in body.splitlines()
                   if ln.strip() and not ln.strip().startswith("#")), None)
    if marker and marker in src[pos:end]:
        print(f"OK: {label} (already injected)")
        return
    write(anchor.path, src[:pos] + body + src[pos:])
    print(f"OK: {label}  [{anchor.ref()}]")


# ---------------------------------------------------------------------------
# Bytecode hooks.
# ---------------------------------------------------------------------------

HANDLER = "Lcom/xj/winemu/exportcontrols/BhVjoyShareHook;"

SHARE_FRAG = "vcontroller/shareMap"
APPLY_FRAG = "vcontroller/getMapByShareCode"
UPLOAD_FRAG = "vcontroller/uploadGtheme"


def patch_bytecode(root: Path) -> None:
    files = smali_files(root)
    if not files:
        die("no .smali files found under the apktool tree")

    # Pass 1: the three URL-anchored repo methods (share/apply/upload).
    anchors = locate_url_anchors(files, [
        (SHARE_FRAG, 2, "shareMap (interceptShare)"),
        (APPLY_FRAG, 2, "getMapByShareCode (interceptApply)"),
        (UPLOAD_FRAG, 3, "uploadGtheme (interceptUpload)"),
    ])
    share, apply_, upload = (anchors[SHARE_FRAG], anchors[APPLY_FRAG],
                             anchors[UPLOAD_FRAG])
    # Pass 2: the share-name method is the unique caller of the upload method.
    share_name = locate_caller(files, upload.ref(), "share-name capture")

    # --- Hook 1: shareMap → interceptShare (throws to abort cloud publish).
    # p1 = layout (p0 = repo `this`). Use /range — .locals is high.
    inject_at_entry(share, (
        "    # BH VJoy export: abort the cloud shareMap publish (the local\n"
        "    # .gtheme is already saved by interceptUpload). interceptShare\n"
        "    # throws; the host catches, deletes its temp file, no cloud orphan.\n"
        f"    invoke-static/range {{p1 .. p1}}, {HANDLER}->"
        "interceptShare(Ljava/lang/Object;)Ljava/lang/Object;\n"
        "    move-result-object v0\n"
        "    if-eqz v0, :bh_share_fallthrough\n"
        "    return-object v0\n"
        "    :bh_share_fallthrough\n"
    ), "shareMap: interceptShare")

    # --- Hook 2: getByShareCode → interceptApply (defensive import fallback).
    inject_at_entry(apply_, (
        "    # BH VJoy import (defensive fallback): present a SAF file picker\n"
        "    # instead of a cloud share-code lookup. Normally redundant — the\n"
        "    # Lxd3;->l1 resolver fires SAF at dialog composition first.\n"
        f"    invoke-static {{}}, {HANDLER}->interceptApply()Ljava/lang/Object;\n"
        "    move-result-object v0\n"
        "    if-eqz v0, :bh_apply_fallthrough\n"
        "    return-object v0\n"
        "    :bh_apply_fallthrough\n"
    ), "getByShareCode: interceptApply")

    # --- Hook 3: uploadGtheme → interceptUpload (read pristine bytes, SAF save).
    # Return value ignored (no move-result) → host upload to CDN still runs.
    inject_at_entry(upload, (
        "    # BH VJoy export: read the pristine pre-CDN .gtheme bytes from the\n"
        "    # upload DTO and launch a SAF save. The host upload to CDN still\n"
        "    # runs (the cloud copy is an orphan; harmless).\n"
        f"    invoke-static/range {{p1 .. p1}}, {HANDLER}->"
        "interceptUpload(Ljava/lang/Object;)Ljava/lang/Object;\n"
    ), "uploadGtheme: interceptUpload")

    # --- Hook 4: share-name method head → captureShareName(typedName).
    # p3 = typedName String (p0=this, p1+p2=wide long gameId, p3=String).
    inject_at_entry(share_name, (
        "    # BH VJoy export: capture the user-typed profile name from the\n"
        "    # \"Name Profile\" dialog for the SAF suggested filename.\n"
        f"    invoke-static/range {{p3 .. p3}}, {HANDLER}->"
        "captureShareName(Ljava/lang/String;)V\n"
    ), "share-name: captureShareName")

    # --- Hooks 5-7: extend the Ly99 resource-resolver short-circuit to the
    # non-Compose and format-args variants. apply_menu_patches.py installs the
    # hook on Ly99;->c0 (Compose stringResource, single key; 6.0.4 Lxd3;->l1);
    # but the host also fetches resource strings via three sibling methods,
    # all taking the same Llok; descriptor (6.0.4 Lell;):
    #
    #   a0(Llok;[Ljava/lang/Object;Lgm3;I)Ljava/lang/String;  Compose + args (was m1)
    #   G(Llok;Lov3;)Ljava/lang/Object;                        suspend        (was P0)
    #   H(Llok;[Ljava/lang/Object;Lov3;)Ljava/lang/Object;     suspend + args (was Q0)
    #
    # The host's "Share failed: %1$s" toast (and other catch-site error
    # toasts) is fetched from a coroutine catch via Q0 — bypassing the l1
    # hook entirely. Without this, our maybeResolveCustomLabel override of
    # e.g. features_vjoy_main_toast_share_failed silently does nothing.
    #
    # Same body for all three: pass p0 (the Lell descriptor) to the shared
    # BhMenuRowClick.maybeResolveCustomLabel; if it returns non-null, early-
    # return that String (works as both Ljava/lang/String; and Ljava/lang/Object;
    # since String is an Object). Suspend re-entry has p0=null → resolver
    # returns null → fallthrough.
    patch_extra_resolvers(root)


RESOLVER_HANDLER = "Lcom/xj/winemu/vibration/BhMenuRowClick;"

EXTRA_RESOLVERS = (
    (".method public static final a0(Llok;[Ljava/lang/Object;Lgm3;I)Ljava/lang/String;",
     "bh_m1_fallthrough",
     "y99.d0 (Compose stringResource with format args; was xd3.m1)"),
    (".method public static final G(Llok;Lov3;)Ljava/lang/Object;",
     "bh_p0_fallthrough",
     "y99.J (suspend getString; was xd3.P0)"),
    (".method public static final H(Llok;[Ljava/lang/Object;Lov3;)Ljava/lang/Object;",
     "bh_q0_fallthrough",
     "y99.K (suspend getString with format args; was xd3.Q0)"),
)


def patch_extra_resolvers(root: Path) -> None:
    p = root / "smali_classes3" / "y99.smali"
    if not p.is_file():
        die("smali_classes3/y99.smali not found (apply_menu_patches.py must have run)")
    for header, label, what in EXTRA_RESOLVERS:
        body = (
            f"    # BH: short-circuit non-Compose/format-args resource lookups\n"
            f"    # the same way Ly99;->c0 is short-circuited by the menu patch.\n"
            f"    invoke-static {{p0}}, {RESOLVER_HANDLER}->"
            f"maybeResolveCustomLabelNoKick(Ljava/lang/Object;)Ljava/lang/String;\n"
            f"    move-result-object v0\n"
            f"    if-eqz v0, :{label}\n"
            f"    return-object v0\n"
            f"    :{label}\n"
        )
        anchor = Anchor(path=p, cls="Ly99;", header=header, params=[], ret="")
        inject_at_entry(anchor, body, what)


# ---------------------------------------------------------------------------
# Manifest — register BhSafProxyActivity (translucent, internal-only,
# multiprocess so it launches in the caller's process; the import
# CompletableFuture can't cross the main↔:wine process boundary).
# ---------------------------------------------------------------------------

ACTIVITY_FQCN = "com.xj.winemu.exportcontrols.BhSafProxyActivity"
ACTIVITY_LINE = (
    f'        <activity android:name="{ACTIVITY_FQCN}" '
    f'android:exported="false" '
    f'android:theme="@android:style/Theme.Translucent.NoTitleBar" '
    f'android:configChanges="orientation|screenSize|keyboardHidden" '
    f'android:multiprocess="true"/>'
)


def patch_manifest(manifest_path: Path) -> None:
    src = read(manifest_path)
    if f'android:name="{ACTIVITY_FQCN}"' in src:
        print("OK: BhSafProxyActivity already registered")
        return
    if "    </application>" not in src:
        die("could not find </application> close tag in AndroidManifest.xml")
    src = src.replace(
        "    </application>",
        ACTIVITY_LINE + "\n    </application>",
        1,
    )
    write(manifest_path, src)
    print(f"OK: registered <activity {ACTIVITY_FQCN}>")


# ---------------------------------------------------------------------------
# CVR resources — sentinel label entries for the bh_vjoy_*_label keys.
#
# These ride on the shared Lxd3;->l1 resolver short-circuit (installed by
# apply_menu_patches.py); the resolver returns the label by key without
# needing a CVR entry, so this is belt-and-braces (a missing CVR entry can
# still trigger an Lhc6 lookup attempt elsewhere). The bytecode side that
# would point host buttons at these keys is deferred upstream — the live
# relabels override the host's own keys in maybeResolveCustomLabel.
# ---------------------------------------------------------------------------

CVR_DIRS = (
    "assets/composeResources/com.xiaoji.egggame.features.vjoy",
    "assets/composeResources/com.xiaoji.egggame.common.vjoy",
    "assets/composeResources/com.xiaoji.egggame.features.winemu",
)
CVR_LABELS = {
    "bh_vjoy_export_label": "Export to file",
    "bh_vjoy_import_label": "Import from file",
}


def patch_cvr_locales(root: Path) -> None:
    lines = [
        f"string|{key}|{base64.b64encode(val.encode('utf-8')).decode('ascii')}\n"
        for key, val in CVR_LABELS.items()
    ]
    touched = 0
    for cvr_dir in CVR_DIRS:
        d = root / cvr_dir
        if not d.is_dir():
            continue
        for locale_dir in sorted(d.iterdir()):
            if not locale_dir.is_dir() or not locale_dir.name.startswith("values"):
                continue
            cvr = locale_dir / "strings.commonMain.cvr"
            if not cvr.is_file():
                continue
            existing = read(cvr)
            to_add = [ln for ln in lines
                      if f"|{ln.split('|', 2)[1]}|" not in existing]
            if not to_add:
                continue
            terminator = "" if existing.endswith("\n") else "\n"
            write(cvr, existing + terminator + "".join(to_add))
            touched += 1
            short = "/".join(cvr.parts[-3:])
            print(f"OK: added VJoy labels to {short}")
    if not touched:
        print("OK: CVR label entries already present (or no VJoy bundles found)")


# ---------------------------------------------------------------------------
# Hide the stock "Upload original" checkbox row in the repurposed share dialog.
#
# We relabel GameHub's "Publish to Cloud" dialog to "Name Profile" for local
# export, but its stock "Upload original" checkbox (a cloud-only control that
# feeds the publish path interceptShare aborts) shows through and is confusing.
# The dialog's sub-sections are each a separate Compose ComposableLambda
# (Ldl7;->invoke); the "Upload original" row is the packed-switch branch whose
# body carries the testTags "vjoy_share_upload"/"vjoy_share_upload_check" and
# the prepare_share_upload_original string. That branch opens with Compose's
# own skip check `Ljy8;->Y(IZ)Z` + `if-eqz vN, :cond_X`; the skip target runs
# `Ljy8;->b0()` (skipToGroupEnd) and returns — the codegen-standard "this
# composable was skipped" path. Forcing the branch ALWAYS to that skip path
# (`if-eqz` -> `goto`) hides the row while keeping Compose's group/slot
# accounting perfectly balanced (identical to a legitimate skip). The title,
# name field, and Cancel/Confirm buttons are sibling composables, untouched.
#
# Fragile by nature (Compose layout, R8 letters): Ldl7; and the registers are
# 6.0.9-specific and will shift on a base bump — fails loudly if the anchor
# moves. The unique `if-eqz v1, :cond_6` + the `xor-int/lit8 v18, v11, 0x1`
# trailer pin it unambiguously within dl7.smali.
# ---------------------------------------------------------------------------

def patch_hide_upload_original(root: Path) -> None:
    p = root / "smali_classes4" / "dl7.smali"
    if not p.is_file():
        die("smali_classes4/dl7.smali not found (Upload-original row hide)")
    src = read(p)
    old = (
        "    move-result v1\n"
        "\n"
        "    if-eqz v1, :cond_6\n"
        "\n"
        "    xor-int/lit8 v18, v11, 0x1\n"
    )
    new = (
        "    move-result v1\n"
        "\n"
        "    # BH VJoy export: hide the stock \"Upload original\" checkbox row in\n"
        "    # the repurposed \"Name Profile\" dialog. Force this sub-section\n"
        "    # composable down Compose's own skip-to-group-end path so the row\n"
        "    # never renders (group-balanced — identical to a legitimate skip).\n"
        "    goto :cond_6\n"
        "\n"
        "    xor-int/lit8 v18, v11, 0x1\n"
    )
    if old not in src:
        if new in src or "    goto :cond_6\n\n    xor-int/lit8 v18, v11, 0x1\n" in src:
            print("OK: Upload-original row already hidden")
            return
        die("Upload-original hide anchor not found in dl7.smali (Ljy8;->Y skip "
            "check / if-eqz v1, :cond_6 / xor-int/lit8 v18, v11, 0x1)")
    write(p, src.replace(old, new, 1))
    print("OK: dl7 (share dialog): hide \"Upload original\" checkbox row")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    root = Path(sys.argv[1])
    if not root.is_dir():
        die(f"{root} is not a directory")

    version = detect_version(root)
    print(f"Detected GameHub base version: {version}")
    print()

    print("=== Manifest (BhSafProxyActivity) ===")
    patch_manifest(root / "AndroidManifest.xml")
    print()

    print("=== CVR resource labels ===")
    patch_cvr_locales(root)
    print()

    print("=== Bytecode hooks (URL-anchored) ===")
    patch_bytecode(root)
    print()

    print("=== Share dialog: hide stock 'Upload original' row ===")
    patch_hide_upload_original(root)
    print()

    print("All VJoy export/import patches applied successfully.")


if __name__ == "__main__":
    main()
