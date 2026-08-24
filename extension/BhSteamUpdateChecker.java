package com.xj.winemu.update;

import android.app.Activity;
import android.app.Application;
import android.content.Context;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.SystemClock;
import android.util.Log;

import java.io.File;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * BhSteamUpdateChecker — keeps the per-game "Online Update" red-dot badge
 * fresh for every INSTALLED Steam game, without needing the user to launch
 * the game first.
 *
 * Why this exists
 * ---------------
 * Stock GameHub only runs the Steam update check on a couple of lazy code
 * paths (an event handler Lp1a;->o dispatching Lvi0;->o(appId, flag) and a few
 * detail/refresh flows), and the More-Menu / library badges just read the last
 * cached result. So after an update ships upstream, the dot often doesn't
 * appear until the user has opened/launched the game a few times. 6.1.1 added
 * per-app cooldown maps (a 30 min / 60 s throttle inside Lwvo;->w) but STILL
 * has no periodic sweep — every check is event-driven, so the
 * trigger-frequency gap this class exists to close is unchanged.
 *
 * The check itself is a network query to Steam's Content Manager servers via
 * GameHub's embedded native Steam client bridge (SteamBridgeClient); it does
 * NOT require the game (Wine) to be running — only that the app is open and
 * the Steam session is connected. So a background sweep is entirely viable.
 *
 * What this does
 * --------------
 * A singleton background worker that periodically (and on app-foreground)
 * enumerates installed Steam appIds from the on-disk manifests and re-runs
 * the host's OWN update check for each one. Each check, on finding an update,
 * broadcasts the appId through the host's badge flow exactly as the launch path
 * does — so live home/library badges refresh instantly and the game-detail dot
 * is correct on the next menu open. No restart needed.
 *
 * Invocation reproduces the host's own per-app dispatch verbatim. 6.1.1's
 * Lvi0;->j(appId, flag, cont) does:
 *   withContext(dispatcher, new Lo0a(appId, null, flag), cont)
 * and Lo0a;->invokeSuspend calls Ljao;->x(appId, flag, self) — the real
 * per-app check + badge broadcast. Lo0a; is a compiler-generated suspend
 * lambda extending kotlin.coroutines.jvm.internal.SuspendLambda AND
 * implementing kotlin.jvm.functions.Function2, ctor
 * (int appId, Continuation completion, boolean flag).
 *
 * THE FLAG IS NOT AN APPLY SWITCH IN 6.1.1 (it was in 6.0.9 — see below).
 * The chain is Ljao;->x -> Lwvo;->w -> Lwvo;->l -> Lwvo;->b -> Lwvo;->I
 * ("getLatestUpdateInfoFromBridge"), which is check-only BY CONSTRUCTION: the
 * apply path (`startLatestSteamUpdate`) lives in a SIBLING method Lwvo;->J
 * that this chain never invokes. Unlike 6.0.9's Lwke;, Lo0a; passes its
 * boolean STRAIGHT THROUGH (no `xor 1`), and in Lwvo;->w that boolean selects
 * the *throttled* variant: TRUE = respect the 30 min / 60 s per-app cooldown
 * maps and record a timestamp; FALSE = unthrottled, always query, leave the
 * host's cooldown bookkeeping untouched. The bridge query itself runs before
 * any flag test, so FALSE still performs a real check. We pass FALSE: we do
 * our own cadence (PERIOD_MS) and must not be silently throttled away nor
 * mutate host state. This mirrors the host's own Lm0a; call site, which
 * likewise passes false.
 *
 * HISTORY — on 6.0.9 the equivalent boolean DID select apply-vs-report
 * (Loaj;->J's flag flowed to ppg.invoke(updateInfo, flag)), and an early build
 * of this checker passed the applying value, telling GameHub to apply updates
 * across the whole installed library unprompted — flipping installed games
 * into a re-acquire state ("Get Game"). That failure mode is structurally
 * impossible on 6.1.1's chain, but re-verify on every base bump that the
 * check chain still cannot reach Lwvo;->J before trusting a flag value.
 *
 * We drive it through the SAME withContext reflection bridge BhVjoyImporter
 * uses for the host's save coroutine: hand our Lo0a block + a synthetic
 * Continuation proxy (on the kotlin.coroutines.Continuation INTERFACE, which a
 * Proxy CAN implement — unlike Ljao;->x's abstract ContinuationImpl param,
 * which is why we never touch jao.x directly) to BuildersKt.withContext, then
 * block on a CompletableFuture the proxy completes.
 *
 * Enumeration is a plain filesystem scan of
 *   <filesDir>/Steam/steamapps/appmanifest_<appId>.acf
 * (version-independent; no DI, no obfuscated types).
 *
 * All paths fail-soft: no installed games, no Steam login, a hung bridge, or a
 * missing reflection anchor just logs and is retried on the next sweep.
 *
 * Reflection anchors. GameHub 6.1.1 no longer obfuscates the Kotlin/coroutines
 * runtime (kotlin.*, kotlinx.coroutines.* keep their real names), so only the
 * one app-owned class below is R8-mangled and needs re-deriving on a base bump:
 *   CHECK_BLOCK_CLASS   "o0a"  ctor (int, Continuation, boolean); its
 *                              invokeSuspend calls Ljao;->x = check + badge.
 * The rest are stable, real names:
 *   kotlinx.coroutines.BuildersKt#withContext(CoroutineContext,Function2,Continuation)
 *   kotlinx.coroutines.Dispatchers#getIO()   (NOTE: use the METHOD — the static
 *       field `a` on Dispatchers is Default, not IO, so reading the field would
 *       silently dispatch the check on the wrong pool.)
 *   kotlin.coroutines.Continuation           (INTERFACE — Proxy-able)
 *   kotlin.coroutines.CoroutineContext, kotlin.jvm.functions.Function2
 */
public final class BhSteamUpdateChecker {

    private static final String TAG = "BhSteamUpdate";

    // === Anchors (GameHub 6.1.1). Shared with BhVjoyImporter where noted;
    //     keep the two in sync on a base bump. 6.1.1 leaves the Kotlin and
    //     kotlinx.coroutines runtime UNOBFUSCATED, so only CHECK_BLOCK_CLASS
    //     is an R8 letter. ===
    private static final String WITH_CONTEXT_CLASS     = "kotlinx.coroutines.BuildersKt";
    private static final String WITH_CONTEXT_METHOD    = "withContext";
    private static final String COROUTINE_CONTEXT_IF   = "kotlin.coroutines.CoroutineContext";
    private static final String FUNCTION2_IF           = "kotlin.jvm.functions.Function2";
    private static final String CONTINUATION_INTERFACE = "kotlin.coroutines.Continuation";
    private static final String DISPATCHER_HOLDER      = "kotlinx.coroutines.Dispatchers";
    // Dispatchers.getIO() — a METHOD, not a field. Dispatchers' static field `a`
    // is Default in 6.1.1; reading it would dispatch the network+file-IO check
    // on the CPU pool.
    private static final String DISPATCHER_IO_METHOD   = "getIO";
    // Host suspend-lambda wrapping the per-app update check + badge broadcast.
    // ctor (int appId, Continuation completion, boolean flag); invokeSuspend ->
    // <steamRepo>->x(appId, flag, self) = check-only (the apply path
    // startLatestSteamUpdate is a sibling method, never reached from here).
    //
    // Re-derive by ctor shape — `(ILkotlin/coroutines/Continuation;Z)V` is
    // unique across every dex on both bases:
    //   grep -rl '^\.method public constructor <init>(ILkotlin/coroutines/Continuation;Z)V'
    // Confirm by checking the host's own dispatcher still constructs it
    // (Lvi0;->j(IZLContinuationImpl;) on both 6.1.1 and 6.1.2) and that the
    // block calls two letter-methods mirroring 6.1.1's Ljao;->x + Lm43;->o
    // (6.1.2: Lqao;->x + Ll43;->o).
    private static final String CHECK_BLOCK_CLASS      = "bna";   // 6.1.2 t0a, 6.1.1 o0a
    // The block passes this flag STRAIGHT THROUGH (no `xor 1` as on 6.0.9's
    // wke) — re-verified on 6.1.2 at the Lvi0;->j construction site.
    // In Lwvo;->w it selects the throttled variant: TRUE = respect the host's
    // 30 min / 60 s per-app cooldowns and record a timestamp; FALSE =
    // unthrottled, always query, don't touch host bookkeeping. We own our
    // cadence (PERIOD_MS), so FALSE. See the class header before changing this.
    private static final boolean CHECK_UNTHROTTLED_FLAG = false;

    // Steam layout under the app's private files dir. Version-independent.
    private static final String STEAM_APPS_SUBPATH = "Steam/steamapps";
    private static final String ACF_PREFIX = "appmanifest_";
    private static final String ACF_SUFFIX = ".acf";

    // Cadence. First sweep shortly after launch (let the Steam session settle),
    // then periodically while the app is alive, plus a debounced sweep whenever
    // the app returns to the foreground so a badge is fresh when the user looks.
    private static final long INITIAL_DELAY_MS      = 30_000L;
    private static final long PERIOD_MS             = 30L * 60_000L;   // 30 min
    private static final long FOREGROUND_MIN_GAP_MS = 5L * 60_000L;    // debounce
    // Per-app check ceiling. The bridge query is network-bound; if it hangs
    // (e.g. Steam not reachable) we abandon this app and move on.
    private static final long PER_APP_TIMEOUT_SECONDS = 30L;
    // Be gentle: one check at a time, with a small gap, and a hard cap so a
    // pathological steamapps dir can never spin the worker forever.
    private static final long INTER_APP_GAP_MS = 250L;
    private static final int  MAX_APPS_PER_SWEEP = 256;

    private static final AtomicBoolean STARTED = new AtomicBoolean(false);
    private static volatile BhSteamUpdateChecker INSTANCE;

    private final HandlerThread workerThread;
    private final Handler worker;
    private volatile Context appContext;
    private final AtomicBoolean sweepInProgress = new AtomicBoolean(false);
    private volatile long lastSweepStartUptime = 0L;

    private BhSteamUpdateChecker(Context ctx) {
        if (ctx != null) this.appContext = ctx.getApplicationContext();
        workerThread = new HandlerThread("BhSteamUpdateWorker");
        workerThread.start();
        worker = new Handler(workerThread.getLooper());
    }

    // ─────────────────────────────────────────────────────────────────────
    // Smali entry: AndroidApp.onCreate() (injected by
    // scripts/apply_update_check_patches.py). Runs once per app process, at
    // app start — NOT on game launch.
    // ─────────────────────────────────────────────────────────────────────
    public static void start(Context ctx) {
        try {
            // AndroidApp.onCreate() runs in EVERY process, so this used to start
            // in ":pcengine" too, where the check can never succeed: the Koin
            // graph there has no binding for the repository we reflect into, and
            // every sweep threw
            //   No definition found for type '<repo>' on scope '['_root_']'
            // once per installed Steam game. Caught and logged, so it was only
            // ever noise -- but it was noise on ":pcengine"'s main thread during
            // the game-launch window, doing reflection and Koin lookups that
            // could not possibly produce a result. Main process only.
            if (!isMainProcess(ctx)) return;
            if (!STARTED.compareAndSet(false, true)) return;
            BhSteamUpdateChecker self = new BhSteamUpdateChecker(ctx);
            INSTANCE = self;
            self.scheduleInitial();
            self.registerForegroundTrigger(ctx);
            Log.i(TAG, "started");
        } catch (Throwable t) {
            Log.w(TAG, "start failed", t);
        }
    }

    /**
     * True when we are in the app's main process, i.e. the process name has no
     * ":suffix". GameHub's other processes (":pcengine", and whatever else it
     * adds later) don't carry the DI graph this checker needs.
     *
     * Errs toward TRUE on an unknown process name: failing to start in the main
     * process would silently drop the update badges, whereas an extra start in
     * some future process is only the harmless noise this gate removes.
     */
    private static boolean isMainProcess(Context ctx) {
        try {
            String proc = Application.getProcessName();
            if (proc == null || proc.isEmpty()) return true;
            if (proc.indexOf(':') >= 0) {
                Log.i(TAG, "not starting in process " + proc);
                return false;
            }
            return true;
        } catch (Throwable t) {
            return true;
        }
    }

    private void scheduleInitial() {
        worker.postDelayed(periodicRunnable, INITIAL_DELAY_MS);
    }

    private final Runnable periodicRunnable = new Runnable() {
        @Override public void run() {
            try {
                runSweep("periodic");
            } catch (Throwable t) {
                Log.w(TAG, "periodic sweep failed", t);
            } finally {
                worker.postDelayed(this, PERIOD_MS);
            }
        }
    };

    /**
     * Kick a sweep when the app returns to the foreground, debounced so
     * flipping between activities can't trigger a storm. Registered via
     * ActivityLifecycleCallbacks; entirely best-effort.
     */
    private void registerForegroundTrigger(Context ctx) {
        try {
            Context app = ctx != null ? ctx.getApplicationContext() : appContext;
            if (!(app instanceof Application)) return;
            ((Application) app).registerActivityLifecycleCallbacks(
                new Application.ActivityLifecycleCallbacks() {
                    @Override public void onActivityResumed(Activity a) {
                        long now = SystemClock.uptimeMillis();
                        if (now - lastSweepStartUptime < FOREGROUND_MIN_GAP_MS) return;
                        worker.post(new Runnable() {
                            @Override public void run() {
                                try { runSweep("foreground"); }
                                catch (Throwable t) { Log.w(TAG, "fg sweep failed", t); }
                            }
                        });
                    }
                    @Override public void onActivityCreated(Activity a, Bundle b) { }
                    @Override public void onActivityStarted(Activity a) { }
                    @Override public void onActivityPaused(Activity a) { }
                    @Override public void onActivityStopped(Activity a) { }
                    @Override public void onActivitySaveInstanceState(Activity a, Bundle b) { }
                    @Override public void onActivityDestroyed(Activity a) { }
                });
        } catch (Throwable t) {
            Log.w(TAG, "foreground trigger registration failed", t);
        }
    }

    // ─────────────────────────────────────────────────────────────────────
    // Sweep
    // ─────────────────────────────────────────────────────────────────────

    private void runSweep(String reason) {
        // Only one sweep at a time; a slow sweep must not overlap the next
        // periodic tick or a foreground kick.
        if (!sweepInProgress.compareAndSet(false, true)) {
            Log.i(TAG, "sweep (" + reason + ") skipped — one already running");
            return;
        }
        lastSweepStartUptime = SystemClock.uptimeMillis();
        try {
            Context ctx = ensureContext();
            if (ctx == null) { Log.i(TAG, "sweep skipped — no context"); return; }

            List<Integer> appIds = enumerateInstalledSteamAppIds(ctx);
            if (appIds.isEmpty()) {
                Log.i(TAG, "sweep (" + reason + "): no installed Steam manifests");
                return;
            }
            Log.i(TAG, "sweep (" + reason + "): checking " + appIds.size()
                    + " installed Steam game(s)");

            int checked = 0, updates = 0, failed = 0;
            for (Integer appId : appIds) {
                if (checked >= MAX_APPS_PER_SWEEP) {
                    Log.w(TAG, "sweep hit MAX_APPS_PER_SWEEP=" + MAX_APPS_PER_SWEEP
                            + "; " + (appIds.size() - checked) + " app(s) deferred to next sweep");
                    break;
                }
                checked++;
                try {
                    // On 6.1.1 this is null (unknown) on success — see
                    // interpretCheckResult; updatesFound stays 0 by design.
                    Boolean hasUpdate = checkOne(appId);
                    if (Boolean.TRUE.equals(hasUpdate)) updates++;
                } catch (Throwable t) {
                    failed++;
                    Log.w(TAG, "check failed for appId=" + appId, t);
                }
                if (INTER_APP_GAP_MS > 0) {
                    try { Thread.sleep(INTER_APP_GAP_MS); } catch (InterruptedException ignored) { }
                }
            }
            Log.i(TAG, "sweep (" + reason + ") done: checked=" + checked
                    + " failed=" + failed + " updatesFound=" + updates);
        } catch (Throwable t) {
            Log.w(TAG, "sweep (" + reason + ") failed", t);
        } finally {
            sweepInProgress.set(false);
        }
    }

    /**
     * Enumerate installed Steam appIds by listing
     * {@code <filesDir>/Steam/steamapps/appmanifest_<appId>.acf}. A manifest's
     * presence is the host's own definition of "installed" (its update check
     * reads the same file for the installed build id), so this is exactly the
     * set we want to keep fresh.
     */
    private List<Integer> enumerateInstalledSteamAppIds(Context ctx) {
        List<Integer> out = new ArrayList<>();
        try {
            File files = ctx.getFilesDir();
            if (files == null) return out;
            File steamApps = new File(files, STEAM_APPS_SUBPATH);
            if (!steamApps.isDirectory()) return out;
            File[] entries = steamApps.listFiles();
            if (entries == null) return out;
            for (File f : entries) {
                String name = f.getName();
                if (!name.startsWith(ACF_PREFIX) || !name.endsWith(ACF_SUFFIX)) continue;
                String idStr = name.substring(
                        ACF_PREFIX.length(), name.length() - ACF_SUFFIX.length());
                try {
                    int appId = Integer.parseInt(idStr);
                    if (appId > 0) out.add(appId);
                } catch (NumberFormatException ignored) {
                    // appmanifest_<non-numeric>.acf — skip.
                }
            }
        } catch (Throwable t) {
            Log.w(TAG, "enumerateInstalledSteamAppIds failed", t);
        }
        return out;
    }

    /**
     * Run the host's per-app update check for one appId. Dispatch:
     *   withContext(Dispatchers.IO, new Lo0a(appId, null, FALSE), continuation)
     * so the block's invokeSuspend calls Ljao;->x(appId, FALSE, self) — the
     * host's own check, which broadcasts the appId to the badge flow when an
     * update exists. Check-only: the apply path is the sibling Lwvo;->J.
     *
     * Returns TRUE if the result indicates an available update, FALSE if it
     * indicates none, or null when the result shape isn't recognised (the badge
     * broadcast is the real effect; this value only feeds the sweep log).
     */
    private Boolean checkOne(int appId) throws Exception {
        Class<?> withContextCls  = Class.forName(WITH_CONTEXT_CLASS);
        Class<?> coroutineCtxCls = Class.forName(COROUTINE_CONTEXT_IF);
        Class<?> function2Cls    = Class.forName(FUNCTION2_IF);
        Class<?> continuationCls = Class.forName(CONTINUATION_INTERFACE);
        Class<?> dispatchersCls  = Class.forName(DISPATCHER_HOLDER);
        Class<?> checkBlockCls   = Class.forName(CHECK_BLOCK_CLASS);

        // Dispatchers.getIO() — the check does network + file IO.
        Method getIO = dispatchersCls.getDeclaredMethod(DISPATCHER_IO_METHOD);
        getIO.setAccessible(true);
        Object dispatcher = getIO.invoke(null);
        if (dispatcher == null) {
            throw new IllegalStateException(DISPATCHER_HOLDER + "." + DISPATCHER_IO_METHOD
                    + "() returned null");
        }

        // new Lo0a(appId, null /*completion*/, FALSE /*unthrottled check*/).
        Constructor<?> blockCtor = checkBlockCls.getDeclaredConstructor(
                int.class, continuationCls, boolean.class);
        blockCtor.setAccessible(true);
        Object block = blockCtor.newInstance(appId, null, CHECK_UNTHROTTLED_FLAG);

        // withContext(IO, block, ourContinuation).
        Method withContext = withContextCls.getDeclaredMethod(
                WITH_CONTEXT_METHOD, coroutineCtxCls, function2Cls, continuationCls);
        withContext.setAccessible(true);

        CompletableFuture<Object> done = new CompletableFuture<>();
        Object continuation = makeContinuation(continuationCls, done, dispatcher);

        Object immediate = withContext.invoke(null, dispatcher, block, continuation);
        Object result = isCoroutineSuspended(immediate)
                ? done.get(PER_APP_TIMEOUT_SECONDS, TimeUnit.SECONDS)
                : immediate;
        return interpretCheckResult(result);
    }

    /**
     * Best-effort read of the check's return value, for logging only.
     *
     * On 6.1.1 Lo0a;->invokeSuspend returns a boxed kotlin.Result wrapping the
     * host's update-info object (Lbwo;) rather than 6.0.9's plain Boolean, and
     * a failed Result carries the Throwable.
     *
     * A successful Result does NOT by itself mean an update exists: the host
     * decides that by testing whether the update-info's ArrayList field is
     * non-empty (see Lvi0;->j). We deliberately do not reflect into that type —
     * its R8 name and field layout are volatile, and the badge broadcast, not
     * this value, is the point of the sweep. So on 6.1.1 a successful check
     * reports "unknown" (null) and the sweep log's updatesFound stays 0; a
     * failed Result also reports null but is already logged by the caller.
     */
    private static Boolean interpretCheckResult(Object result) {
        if (result instanceof Boolean) return (Boolean) result;   // legacy shape
        if (result == null) return null;
        try {
            Class<?> resultCls = Class.forName("kotlin.Result");
            if (resultCls.isInstance(result)) {
                Method isFailure = resultCls.getDeclaredMethod("isFailure-impl", Object.class);
                isFailure.setAccessible(true);
                Method unbox = resultCls.getDeclaredMethod("unbox-impl");
                unbox.setAccessible(true);
                Object inner = unbox.invoke(result);
                if (Boolean.TRUE.equals(isFailure.invoke(null, inner))) return null;
                if (inner instanceof Boolean) return (Boolean) inner;
                // Success, but availability lives inside the host's update-info
                // object; not worth reflecting into. Unknown.
                return null;
            }
        } catch (Throwable ignored) {
            // Shape changed; fall through to "unknown".
        }
        return null;
    }

    /**
     * Proxy implementing the host's Continuation INTERFACE (Lov3;): getContext()
     * returns the dispatcher (a CoroutineContext.Element), resumeWith(Object)
     * completes our future. Mirrors BhVjoyImporter#makeContinuation.
     */
    private static Object makeContinuation(
            Class<?> continuationCls, CompletableFuture<Object> done, Object dispatcher)
            throws Exception {
        final Object contextHolder = dispatcher;
        return Proxy.newProxyInstance(
                continuationCls.getClassLoader(),
                new Class<?>[]{ continuationCls },
                (proxy, method, args) -> {
                    String name = method.getName();
                    if ("getContext".equals(name)) return contextHolder;
                    if ("resumeWith".equals(name)) {
                        done.complete(args != null && args.length > 0 ? args[0] : null);
                        return null;
                    }
                    if ("equals".equals(name)) return proxy == args[0];
                    if ("hashCode".equals(name)) return System.identityHashCode(proxy);
                    if ("toString".equals(name)) return "BhSteamUpdateContinuation";
                    return null;
                });
    }

    /** Detect Kotlin's COROUTINE_SUSPENDED sentinel. Mirrors BhVjoyImporter. */
    private static boolean isCoroutineSuspended(Object o) {
        if (o == null) return false;
        try {
            String simple = o.getClass().getSimpleName();
            return "CoroutineSingletons".equals(simple)
                    || "COROUTINE_SUSPENDED".equals(o.toString());
        } catch (Throwable t) {
            return false;
        }
    }

    /** Application context, resolved lazily via ActivityThread if start() had none. */
    private Context ensureContext() {
        Context ctx = appContext;
        if (ctx != null) return ctx;
        try {
            Class<?> at = Class.forName("android.app.ActivityThread");
            Method m = at.getMethod("currentApplication");
            Object app = m.invoke(null);
            if (app instanceof Context) {
                ctx = ((Context) app).getApplicationContext();
                appContext = ctx;
            }
        } catch (Throwable t) {
            Log.w(TAG, "ensureContext failed", t);
        }
        return ctx;
    }
}
