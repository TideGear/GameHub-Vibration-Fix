#!/usr/bin/env python3
"""
Kill the PC-engine plugin's device-performance telemetry (GameHub 6.1.1).

WHY THIS EXISTS
---------------
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

WHAT THIS PATCHES
-----------------
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

Delivered through the SAME shadow-dex mechanism as the rumble hooks, so the
plugin's base.apk is never modified and its SHA-256 identity record stays valid.
Run this alongside apply_plugin_rumble_patches.py, before
build_plugin_shadow_dex.py.

Usage:
    python3 apply_plugin_privacy_patches.py <decompiled_plugin_dir>
"""
import re
import sys
from pathlib import Path

UPLOADER = "smali/xjp/mv1.smali"
UPLOADER_METHOD = (
    ".method public final c(JLkotlin/coroutines/jvm/internal/ContinuationImpl;)"
    "Ljava/lang/Object;\n"
)
RESULT_TYPE = "Lxjp/jv1;"

REG_DIRECTIVE_RE = re.compile(r"^[ \t]*\.(?:locals|registers)[ \t]+(\d+)[ \t]*\n", re.M)

STUB = (
    "\n"
    "    # BH: privacy patch — drop the device-performance session-summary\n"
    "    # upload. Returns the host's own \"nothing uploaded\" result, which this\n"
    "    # method already builds on its empty-batch / missing-summary / failure\n"
    "    # paths, so the reporter just logs uploadedBatches=0. Sampling and local\n"
    "    # summary storage are untouched; only the network send is removed.\n"
    f"    new-instance v0, {RESULT_TYPE}\n"
    f"    invoke-direct {{v0}}, {RESULT_TYPE}-><init>()V\n"
    "    return-object v0\n"
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

    p = root / UPLOADER
    if not p.is_file():
        die(f"{UPLOADER} not found — point this at a decompiled "
            f"com.xiaoji.egggame.plugin.pcengine tree, and re-anchor if the "
            f"plugin version changed.")
    src = p.read_text(encoding="utf-8", errors="replace")

    n = src.count(UPLOADER_METHOD)
    if n == 0:
        die("perf uploader method c(J,Continuation) not found — re-anchor.")
    if n != 1:
        die(f"perf uploader method anchor is non-unique ({n} matches).")

    start = src.index(UPLOADER_METHOD)
    end = src.find("\n.end method", start)
    if end < 0:
        die("unclosed perf uploader method")
    reg = REG_DIRECTIVE_RE.search(src, start, end)
    if not reg:
        die("no .locals/.registers directive in the perf uploader method")
    if int(reg.group(1)) < 1:
        die(f"perf uploader declares .locals {reg.group(1)}; the stub needs v0")

    # Confirm this really is the perf uploader before touching it. The log
    # strings live in the LAMBDAS, not here, so identify the method the same way
    # it was originally located: it is the one that builds the "upload start"
    # log lambda Lxjp/hv1;, and it must return the Lxjp/jv1; result we stub with.
    # (An earlier version of this check looked for the log string in this file
    # and correctly refused to patch — keep the signal on real instructions.)
    body = src[start:end]
    if f"new-instance" not in body or "Lxjp/hv1;" not in body:
        die(f"{UPLOADER} method c() does not construct the upload-start log "
            f"lambda Lxjp/hv1; — wrong method/class after a plugin bump; "
            f"re-anchor (find the unique method in the perf uploader class that "
            f"builds that lambda).")
    if f"{RESULT_TYPE}-><init>()V" not in body:
        die(f"{UPLOADER} method c() never constructs {RESULT_TYPE} with the "
            f"no-arg ctor, so returning one is not a shape its callers already "
            f"handle — re-anchor before stubbing.")

    if "# BH: privacy patch" in src[reg.end():end]:
        print("OK: perf-summary upload already stubbed")
        return

    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(src[:reg.end()] + STUB + src[reg.end():])
    print("OK: Lxjp/mv1;->c: drop device_perf_session_summary upload")
    print()
    print("NOTE: add smali/xjp/mv1.smali to SHADOW_CLASSES in")
    print("      build_plugin_shadow_dex.py so this stub ships in the shadow dex.")


if __name__ == "__main__":
    main()
