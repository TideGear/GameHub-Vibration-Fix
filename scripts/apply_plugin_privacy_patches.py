#!/usr/bin/env python3
"""
Kill the PC-engine plugin's telemetry channels (GameHub 6.1.1).

Two channels live in the downloaded PC-engine plugin rather than the base APK,
so apply_privacy_patches.py cannot reach them. Both are stubbed here and shipped
through the shadow dex.

=== CHANNEL 1: device-performance session summary ==========================

6.0.9 had a device-performance channel that apply_privacy_patches.py stubbed at
Lqv4;->b. On 6.1.1 that endpoint is absent from the BASE APK, which made it look
retired — it was not. It moved into the downloaded PC-engine plugin and got a
successor. Device logs from STOCK 6.1.1 show, per game session:

  DevicePerformanceReporter session start sessionId=<uuid> gameId=… sourceGameId=…
  DevicePerformanceReporter summary sample captured … fps=59 pwr=1.93 ramMb=1667
      ramPercent=15.0 ramTotalMb=11113.258 gpuPercent=4        (every ~10 s)
  DevicePerfSessionSummaryUploader upload start batchSize=1 sessionIds=[…]
      payloadBytes=878 payloadPreview=[{"event_type":"device_perf_session_summary",
      "user_id":…
  DevicePerfSessionSummaryUploader upload success batchSize=1

and log `summaryOnly=true legacyUpload=false`, i.e. the OLD endpoint is disabled
and this replaced it. So a per-session hardware/performance profile tied to a
user id and game id leaves the device on every play session.

Patched:
  Lxjp/mv1;->c(JLkotlin/coroutines/jvm/internal/ContinuationImpl;)Ljava/lang/Object;

That is the uploader: it is the unique method in Lxjp/mv1; that builds the
"upload start" log lambda Lxjp/hv1;, and Lxjp/mv1;->b(IJ…) (the bootstrap/retry
wrapper) delegates to it — so one stub covers both entry points.

We early-return `new Lxjp/jv1;()` — the host's OWN "nothing was uploaded" result,
which it already constructs at three bail-out sites in this very method (empty
batch, missing summary, upload failure). Callers therefore see a shape they
already handle: the reporter logs `uploadedBatches=0` and moves on. Sampling
still runs and summaries are still written to local storage; nothing is sent.

Related plugin classes, for future re-anchoring:
  Lxjp/bv1;  DTO (serialName device_perf_session_summary)
  Lxjp/gv1;  repository (device_perf_session_summary_v1, readSummaryLocked)
  Lxjp/hv1;  "upload start" log lambda      Lxjp/iv1;  "upload success" lambda
  Lxjp/jv1;  uploader result (fields a:Z, b:Set; synthetic no-arg ctor)

=== CHANNEL 2: playtime heartbeat (leaks the user's Steam ID64) ============

Confirmed live on stock 6.1.1: with a game running, the :pcengine process POSTs
https://landscape-api-oversea.vgabc.com/heartbeat/game/update every 30 seconds
with params

  game_id=…&source_game_id=…&source_type=1&source_user_id=76561197968189945

where source_user_id is the user's real Steam ID64. heartbeat/game/start and
heartbeat/game/end fire around it. The base-APK stub (Lvho;->a) only covers a
base-APK heartbeat *start*; the plugin carries its own start/update/end trio.

Owner: Lxjp/kx9; = WineGameUsageTracker (getValue() returns that literal;
local MMKV key prefix "wine_usage:" built in d()). Its methods are NOT the
senders — that mattered, because the audit's start/update/end mapping was
unconfirmed. Read out of the smali:

  Lxjp/kx9;->a(Lxjp/kx9;,Cont)  builds the request DTO `new Lxjp/fs9;(Integer,
      String, String, String)` = (game_id, source_game_id, source_type,
      source_user_id). Not a sender.
  Lxjp/kx9;->b(Cont)  memoises field Z from Lxjp/ap2;->e — the source_user_id
      used when source_type == "2".
  Lxjp/kx9;->c(Cont)  memoises field X from Lxjp/ty6;->a:J — the source_user_id
      used when source_type == "1", i.e. THE Steam ID64 seen on the wire.
  Lxjp/kx9;->d()     sha256("wine_usage:"+a+":"+b) — the local MMKV key.
  Lxjp/kx9;->e(String,Cont)  packed-switch on source_type: "1"->c(), "2"->b(),
      "3"->"". So e() is the id RESOLVER, not a network call.
  Lxjp/kx9;->f()     session stop: cancels the heartbeat Job (field A), then
      accumulates elapsed seconds into MMKV.

The three POSTs live in the tracker's suspend lambdas, each of which builds the
DTO via kx9->a() and then hands it to ONE shared HTTP bridge:

  Lxjp/uk8;->invokeSuspend  const-string "heartbeat/game/start"   -> jg4->e(...)
  Lxjp/x06;->invokeSuspend  const-string "heartbeat/game/update"  -> jg4->e(...)
  Lxjp/gx9;->invokeSuspend  const-string "heartbeat/game/end"     -> jg4->e(...)

(Lifecycle, read from Lxjp/wv9;: on session start it launches Lxjp/x06;(kx9,
null,0x18) when kx9->q is set else Lxjp/uk8;(kx9,null), then launches
Lxjp/gx9;(kx9,null,1) into kx9->A — that is the `while (isActive) { delay(0x7530
= 30_000 ms); launch x06(update); accumulate MMKV }` loop, which matches the
observed 30 s cadence. Lxjp/gx9; with selector 0 is the end POST, reached from
kx9->f() via Lxjp/l86;.)

WHAT THIS PATCHES

  Lxjp/jg4;->e(Lxjp/jg4;Ljava/lang/String;Lxjp/fs9;
               Lkotlin/coroutines/jvm/internal/SuspendLambda;)Ljava/lang/Object;

That is the single funnel for all three beats: an R8-synthesised static bridge
that supplies the default request-config block (Lxjp/k3;(0xb)) and delegates to
the generic POST helper Lxjp/jg4;->d(...). Two independent facts make it the
right anchor rather than the URL-literal holders:

  * its third parameter is typed Lxjp/fs9; — the heartbeat DTO, which exists for
    nothing else in the plugin (Lxjp/ds9;/Lxjp/es9; are just its serialiser and
    Companion);
  * tree-wide it has exactly THREE call sites, one per heartbeat path, and every
    caller file carries exactly one heartbeat/game/* literal. assert_heartbeat_
    funnel() re-checks that at patch time, so a future plugin that routes some
    other endpoint through e() fails the build instead of silently losing it.

Lxjp/jg4;'s other methods (a/b/c/d/f — vcontroller/deleteMap,
vcontroller/uploadGtheme and the generic GET/POST helpers used by ~30 other
plugin classes) are copied into the shadow dex verbatim and keep working. This
mirrors why the base APK stubs the CONSUMER Lvho;->a rather than the URL
provider Ld80;->invoke: the literal holders Lxjp/x06;, Lxjp/gx9;, Lxjp/uk8; and
Lxjp/w7; are merged synthetics serving unrelated purposes app-wide (Lxjp/w7;
:pswitch_d returns "heartbeat/game/start" from a Function0 that also throws the
LocalAppAdaptiveInfo error and yields Compose/serializer values), so stubbing
them would corrupt unrelated resolution.

RETURN SHAPE. e() has no bail-out path of its own to copy, so the value was read
out of the three callers instead — all three provably tolerate null:

  start  (Lxjp/uk8;)  move-result-object v0; compared only against
                      COROUTINE_SUSPENDED, then overwritten with the kx9 ref.
                      The value is never read.
  update (Lxjp/x06;)  `check-cast p1, Lxjp/zy3;` (abstract ktor response) then
                      Result.constructor-impl(p1) and only exceptionOrNull-impl
                      is consulted. check-cast on null always succeeds, null is
                      a valid Result success value, exceptionOrNull is null, so
                      the error-log branch is skipped and the value is dropped.
  end    (Lxjp/gx9;)  compared only against COROUTINE_SUSPENDED, then dropped.

So `return null` is indistinguishable from a successful beat to every caller,
and no HTTP request, URL string or radio wake happens.

WHAT STAYS LOCAL. Only the network send is removed, exactly as the perf stub
does. The 30 s loop still runs and still writes elapsed seconds into MMKV under
the sha256("wine_usage:…") key (Lxjp/gx9; default branch and Lxjp/kx9;->f()),
kx9->a()/b()/c()/e() still resolve ids for local use, and the session Job
lifecycle is untouched — so nothing that reads local playtime changes
behaviour. (The in-app playtime SCREEN is already empty on this build: that is
the documented trade-off of the base getUserPlayTimeList stub in
apply_privacy_patches.py, not a new regression from this patch.)

===========================================================================

Both stubs are delivered through the SAME shadow-dex mechanism as the rumble
hooks, so the plugin's base.apk is never modified and its SHA-256 identity
record stays valid. Run this alongside apply_plugin_rumble_patches.py, before
build_plugin_shadow_dex.py.

Usage:
    python3 apply_plugin_privacy_patches.py <decompiled_plugin_dir>
"""
import os
import re
import sys
from pathlib import Path

# --- channel 1: device-performance session-summary uploader ----------------
#
# Every letter here drifts on a plugin bump (101 -> 102: uploader Lxjp/mv1; ->
# Lxjp/qv1;, result Lxjp/jv1; -> Lxjp/nv1;), and both stale letters still resolve
# to REAL classes in 102 — 102's mv1 is the "upload success" log lambda and its
# jv1 has an entirely different constructor. So nothing here is pinned by name.
#
# The uploader is identified by its method pair: the batch upload c(J, Cont) plus
# the bootstrap/retry sibling b(I, J, Cont). Unique in both 101 and 102.
UPLOADER_METHOD = (
    ".method public final c(JLkotlin/coroutines/jvm/internal/ContinuationImpl;)"
    "Ljava/lang/Object;\n"
)
UPLOADER_SIBLING = (
    ".method public final b(IJLkotlin/coroutines/jvm/internal/ContinuationImpl;)"
    "Ljava/lang/Object;\n"
)

# The "nothing uploaded" result is whatever the uploader itself builds with a
# no-arg ctor on its empty-batch / missing-summary / failure paths. Deriving it
# from the method body is what caught jv1 -> nv1; hardcoding it would have shipped
# a NoSuchMethodError exactly like the base APK's Ldd7; did.
NOARG_CTOR_RE = re.compile(
    r"invoke-direct \{[pv]\d+\}, (L[\w$/]+;)-><init>\(\)V")

def uploader_stub(result_type: str) -> str:
    return (
        "\n"
        "    # BH: privacy patch — drop the device-performance session-summary\n"
        "    # upload. Returns the host's own \"nothing uploaded\" result, which\n"
        "    # this method already builds on its empty-batch / missing-summary /\n"
        "    # failure paths, so the reporter just logs uploadedBatches=0.\n"
        "    # Sampling and local summary storage are untouched; only the network\n"
        "    # send is removed.\n"
        f"    new-instance v0, {result_type}\n"
        f"    invoke-direct {{v0}}, {result_type}-><init>()V\n"
        "    return-object v0\n"
    )

# --- channel 2: WineGameUsageTracker playtime heartbeat --------------------
#
# Same story: the bridge class drifts (101 Lxjp/jg4; -> 102 Lxjp/kg4;) and so does
# the heartbeat DTO in its signature (Lxjp/fs9; -> Lxjp/us9;). What is stable is
# the shape — a static synthetic `e` taking (self, String, <DTO>, SuspendLambda)
# — so match that with the self type and DTO wildcarded, then read the real names
# back out of the match.
HEARTBEAT_BRIDGE_METHOD_RE = re.compile(
    r"\.method public static synthetic e\((L[\w$/]+;)Ljava/lang/String;"
    r"(L[\w$/]+;)Lkotlin/coroutines/jvm/internal/SuspendLambda;\)"
    r"Ljava/lang/Object;\n"
)
# The generic POST helper this bridge delegates to — proof we have the right
# method after a plugin bump. Deliberately NOT stubbed: ~30 other plugin
# classes post through it. {cls} is filled in with the derived bridge type.
HEARTBEAT_BRIDGE_DELEGATE_FMT = (
    "{cls}->d(Ljava/lang/String;Ljava/lang/Object;"
    "Lkotlin/jvm/functions/Function1;"
    "Lkotlin/coroutines/jvm/internal/ContinuationImpl;)Ljava/lang/Object;"
)
HEARTBEAT_PATHS = ("heartbeat/game/start", "heartbeat/game/update",
                   "heartbeat/game/end")

HEARTBEAT_STUB = (
    "\n"
    "    # BH: privacy patch — drop the WineGameUsageTracker playtime heartbeat\n"
    "    # (heartbeat/game/start + /update every 30 s + /end). Its params carry\n"
    "    # source_user_id = the user's Steam ID64. This static bridge is the sole\n"
    "    # funnel for all three beats (3 call sites tree-wide, one per path, all\n"
    "    # verified by assert_heartbeat_funnel below) and its Lxjp/fs9; parameter\n"
    "    # type exists for nothing else, so the generic POST helper jg4->d() that\n"
    "    # every other plugin endpoint uses stays live.\n"
    "    # null is what all three callers already tolerate: start and end only\n"
    "    # compare the result against COROUTINE_SUSPENDED and then drop it, and\n"
    "    # update does `check-cast Lxjp/zy3;` (succeeds on null) then reads only\n"
    "    # Result.exceptionOrNull. Local MMKV playtime bookkeeping is untouched.\n"
    "    const/4 v0, 0x0\n"
    "    return-object v0\n"
)

REG_DIRECTIVE_RE = re.compile(r"^[ \t]*\.(?:locals|registers)[ \t]+(\d+)[ \t]*\n", re.M)
MARKER = "# BH: privacy patch"


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def locate_method(src: str, rel: str, header: str, what: str):
    """Return (method_start, insertion_point, method_end) for a uniquely named
    method, failing loud on absence / non-uniqueness / missing register
    directive. Never bakes a line number or register into the anchor: 6.1.1
    keeps `.line` directives in app code."""
    n = src.count(header)
    if n == 0:
        die(f"{what} method not found in {rel} — re-anchor.")
    if n != 1:
        die(f"{what} method anchor is non-unique ({n} matches) in {rel}.")
    start = src.index(header)
    end = src.find("\n.end method", start)
    if end < 0:
        die(f"unclosed {what} method in {rel}")
    reg = REG_DIRECTIVE_RE.search(src, start, end)
    if not reg:
        die(f"no .locals/.registers directive in the {what} method ({rel})")
    if int(reg.group(1)) < 1:
        die(f"{what} declares .locals {reg.group(1)}; the stub needs v0")
    return start, reg.end(), end


def smali_files(root: Path):
    for d in sorted(os.listdir(root)):
        if not d.startswith("smali"):
            continue
        sub = root / d
        if not sub.is_dir():
            continue
        for dirpath, _dirs, names in os.walk(sub):
            for name in names:
                if name.endswith(".smali"):
                    yield Path(dirpath) / name


def assert_heartbeat_funnel(root: Path, bridge_cls: str) -> None:
    """Prove the bridge's e() is still heartbeat-only before stubbing it.

    Three separate wrong privacy claims in this project's history came from
    grepping a string literal and concluding a channel was gone, so this does
    the opposite: it refuses to patch unless the CALL GRAPH still looks the way
    the audit found it. Requirements, all fail-loud:

      * exactly three files invoke the bridge;
      * each of them invokes it exactly once;
      * each of them carries exactly one heartbeat/game/* literal;
      * the three literals are start, update and end — one each.

    If a future plugin routes a non-heartbeat endpoint through the bridge, or
    splits the heartbeat across more call sites, this fails instead of silently
    over- or under-reaching."""
    callee = f"{bridge_cls}->e("
    callers = {}
    for path in smali_files(root):
        text = read(path)
        n = text.count(callee)
        if not n:
            continue
        rel = path.relative_to(root).as_posix()
        found = [p for p in HEARTBEAT_PATHS if f'"{p}"' in text]
        callers[rel] = (n, found, sum(text.count(f'"{p}"') for p in HEARTBEAT_PATHS))

    if len(callers) != 3:
        die(f"expected exactly 3 call sites for {callee}, found "
            f"{len(callers)}: {sorted(callers)} — the heartbeat funnel changed "
            f"shape; re-anchor before stubbing (a non-heartbeat caller would be "
            f"broken by this stub, a missing one would leak the Steam ID64).")

    seen = []
    for rel, (n_calls, found, n_literals) in sorted(callers.items()):
        if n_calls != 1:
            die(f"{rel} invokes {callee} {n_calls} times (expected 1) "
                f"— re-verify the funnel.")
        if len(found) != 1 or n_literals != 1:
            die(f"{rel} carries {n_literals} heartbeat/game/* literal(s) "
                f"{found} (expected exactly 1) — it may no longer be a pure "
                f"heartbeat sender; re-anchor.")
        seen.append((found[0], rel))

    paths = sorted(p for p, _ in seen)
    if paths != sorted(HEARTBEAT_PATHS):
        die(f"the 3 bridge callers cover {paths}, not the expected "
            f"start/update/end trio — re-anchor.")
    for path, rel in sorted(seen):
        print(f"  funnel ok: {rel} -> {path}")


def stub_method(root: Path, rel: str, header: str, stub: str, what: str,
                evidence, ok_msg: str) -> None:
    """Insert `stub` at instruction index 0 of a method, after confirming the
    method really is the target via `evidence` — a list of (substring, why)
    pairs that must all appear in the method body."""
    p = root / rel
    if not p.is_file():
        die(f"{rel} not found — point this at a decompiled "
            f"com.xiaoji.egggame.plugin.pcengine tree, and re-anchor if the "
            f"plugin version changed.")
    src = read(p)
    start, insert_at, end = locate_method(src, rel, header, what)
    body = src[start:end]

    for needle, why in evidence:
        if needle not in body:
            die(f"{rel} {what}: expected {needle} in the method body ({why}) — "
                f"wrong method/class after a plugin bump; re-anchor before "
                f"stubbing.")

    if MARKER in src[insert_at:end]:
        print(f"OK: {what} already stubbed")
        return

    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(src[:insert_at] + stub + src[insert_at:])
    print(f"OK: {ok_msg}")


def locate_uploader(root: Path) -> str:
    """Relative path of the perf uploader, found by its method pair."""
    hits = []
    for path in smali_files(root):
        text = read(path)
        if UPLOADER_METHOD in text and UPLOADER_SIBLING in text:
            hits.append(path.relative_to(root).as_posix())
    if not hits:
        die("perf uploader not found — no class declares both\n"
            f"  {UPLOADER_METHOD.strip()}\n  {UPLOADER_SIBLING.strip()}\n"
            "  (re-anchor; the upload/retry method pair changed shape.)")
    if len(hits) > 1:
        die(f"perf uploader shape is non-unique ({len(hits)}): {hits}")
    print(f"    perf uploader: {hits[0]}")
    return hits[0]


def derive_nothing_uploaded(root: Path, rel: str, header: str) -> str:
    """The result type the uploader itself builds with a no-arg ctor.

    Derived, never hardcoded: on plugin 102 the 101 letter (Lxjp/jv1;) still
    exists but takes (Lxjp/kv1;, ContinuationImpl) — returning it would assemble
    fine and then NoSuchMethodError at runtime.
    """
    src = read(root / rel)
    start = src.index(header)
    end = src.find("\n.end method", start)
    found = set(NOARG_CTOR_RE.findall(src[start:end]))
    if not found:
        die("could not derive the 'nothing uploaded' result type — the uploader "
            "builds nothing with a no-arg ctor. Re-anchor by hand.")
    if len(found) > 1:
        die(f"ambiguous 'nothing uploaded' result type {sorted(found)}.")
    result = found.pop()
    # Belt and braces: the ctor we are about to emit must actually exist.
    # NOTE the `synthetic` variant — this result type's no-arg ctor is
    # `public synthetic constructor` in both plugin 101 and 102, and a pattern
    # requiring plain `public constructor` rejects the CORRECT class. Same trap
    # as the 3-dot menu row and the Physical vibrator sibling.
    name = result[1:-1].split("/")[-1]
    hits = list((root / "smali").rglob(f"{name}.smali"))
    if not hits:
        die(f"{result} does not exist in this plugin — re-derive.")
    text = read(hits[0])
    if not any(v in text for v in (".method public constructor <init>()V",
                                   ".method public synthetic constructor <init>()V")):
        die(f"{result} has no no-arg constructor in this plugin — re-derive.")
    print(f"    derived {result} as the 'nothing uploaded' result")
    return result


def patch_perf_uploader(root: Path) -> None:
    # The log strings live in the LAMBDAS, not in the uploader, so the method is
    # identified by its own instructions: the upload/retry method pair, plus the
    # result type it builds on its bail-out paths. (An earlier version looked for
    # the log string in this file and correctly refused to patch — keep the
    # signal on real instructions.)
    rel = locate_uploader(root)
    result_type = derive_nothing_uploaded(root, rel, UPLOADER_METHOD)
    stub_method(
        root, rel, UPLOADER_METHOD, uploader_stub(result_type),
        f"perf uploader {rel}",
        [(f"{result_type}-><init>()V",
          "returning this type is only safe if the method's own bail-out paths "
          "already construct it")],
        "drop device_perf_session_summary upload",
    )


def locate_heartbeat_bridge(root: Path):
    """Return (relative path, method header, bridge type) for the heartbeat
    bridge, matched by shape with the self type and DTO wildcarded."""
    hits = []
    for path in smali_files(root):
        m = HEARTBEAT_BRIDGE_METHOD_RE.search(read(path))
        if m:
            hits.append((path.relative_to(root).as_posix(),
                         m.group(0), m.group(1), m.group(2)))
    if not hits:
        die("heartbeat bridge not found — no class declares a static synthetic\n"
            "  e(<self>, String, <dto>, SuspendLambda) -> Object\n"
            "  (re-anchor; the funnel signature changed shape.)")
    if len(hits) > 1:
        die(f"heartbeat bridge shape is non-unique ({len(hits)}): "
            + ", ".join(h[0] for h in hits))
    rel, header, cls, dto = hits[0]
    print(f"    heartbeat bridge: {rel}  ({cls}, DTO {dto})")
    return rel, header, cls


def patch_heartbeat(root: Path) -> None:
    rel, header, cls = locate_heartbeat_bridge(root)
    assert_heartbeat_funnel(root, cls)
    stub_method(
        root, rel, header, HEARTBEAT_STUB,
        f"heartbeat bridge {cls}->e",
        [(HEARTBEAT_BRIDGE_DELEGATE_FMT.format(cls=cls),
          "the bridge must still delegate to the generic POST helper ->d, "
          "which is what makes it the send path")],
        "drop heartbeat/game/{start,update,end} POSTs (Steam ID64 leak)",
    )


def main():
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    root = Path(sys.argv[1])
    if not root.is_dir():
        die(f"{root} is not a directory")

    print("=== Device-performance session summary ===")
    patch_perf_uploader(root)
    print()

    print("=== WineGameUsageTracker playtime heartbeat ===")
    patch_heartbeat(root)
    print()

    print("NOTE: smali/xjp/mv1.smali and smali/xjp/jg4.smali must both be in")
    print("      SHADOW_CLASSES in build_plugin_shadow_dex.py so these stubs")
    print("      ship in the shadow dex.")


if __name__ == "__main__":
    main()
