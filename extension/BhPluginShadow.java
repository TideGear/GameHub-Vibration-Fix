package com.xj.winemu.vibration;

import android.content.Context;
import android.content.res.AssetManager;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import android.widget.Toast;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import java.util.concurrent.atomic.AtomicBoolean;

import dalvik.system.DexClassLoader;

/**
 * Prepends GameScrub's "shadow dex" to the PC-engine plugin's classloader so our
 * patched copies of the plugin's rumble classes win over the plugin's own.
 *
 * Why this exists
 * ---------------
 * GameHub 6.1.1 moved the Wine/PC engine into a separately-downloaded plugin, so
 * the dual-motor dispatch hooks no longer have a target in the base APK. The
 * plugin APK is SHA-256-committed to a local identity record and re-verified on
 * every load, so patching it in place would mean forging that record.
 *
 * We don't touch it. ComboLite's PluginClassLoader extends DexClassLoader, whose
 * {@code dexPath} is a {@code :}-separated LIST searched in order. A single
 * base-APK injection at the head of
 * {@code PluginClassLoader.<init>(String pluginId, String dexPath, ...)}
 * (installed by scripts/apply_plugin_shadow_patches.py) routes that argument
 * through {@link #dexPath(String, String)} below, which returns
 *
 *     &lt;our shadow dex&gt;:&lt;the untouched plugin base.apk&gt;
 *
 * so base.apk stays byte-identical and its integrity check keeps passing. The
 * shadow classes can still call back into com.xj.winemu.* because
 * PluginClassLoader.loadClass falls back to super.loadClass (parent-first) on a
 * local miss.
 *
 * Safety gates — all of these fall back to the stock dexPath, i.e. stock rumble
 * behaviour, rather than risking a broken engine:
 *
 *   1. pluginId must be the PC-engine plugin. Other plugins are untouched.
 *   2. The installed plugin must be compatible. Our shadow classes are copies of
 *      a SPECIFIC plugin build's classes, and letting a stale shadow win over a
 *      newer plugin is exactly how you get NoSuchFieldError / verifier faults
 *      deep inside Wine. Two ways to satisfy this:
 *        a. its versionCode equals {@link #EXPECTED_PLUGIN_VERSION_CODE} (read
 *           from the host's own identity record) — that IS the build we were cut
 *           from, so there is nothing to check; or
 *        b. {@link #probeShadowCompatible} confirms at load time that every class
 *           we override still has the same shape in the installed plugin. The
 *           plugin contract has bumped on every base so far, so refusing on the
 *           version number alone meant losing dual-motor AND the plugin-side
 *           privacy stubs on every single release, usually needlessly.
 *   3. The asset must extract and be markable read-only (see below).
 *
 * Android 14+ requires dex files to be read-only before they can be loaded, so
 * the extracted shadow dex is chmod'd via {@link File#setReadOnly()} — the same
 * reason ComboLite leaves the plugin's own base.apk as {@code -r--r--r--}.
 */
public final class BhPluginShadow {

    private static final String TAG = "BhPluginShadow";

    /** The only plugin we shadow. */
    private static final String PC_ENGINE_PLUGIN_ID =
            "com.xiaoji.egggame.plugin.pcengine";

    /**
     * Plugin versionCode the shadow classes were cut against. See gate 2 above.
     * Bump together with a re-run of apply_plugin_rumble_patches.py +
     * apply_plugin_privacy_patches.py + build_plugin_shadow_dex.py against the
     * new plugin.
     *
     * 101 = the GameHub 6.1.1-era plugin (schemaVersion 2).
     * 102 = the 6.1.2-era plugin (schemaVersion 3).
     * 103 = the 6.2.0-era plugin (schemaVersion 4).
     * 104 = the 6.2.1-era plugin (schemaVersion 5).
     *
     * Every base so far has hard-required its own schemaVersion, so an older
     * plugin is not merely stale — the host refuses to load it at all
     * ("PC engine plugin schema N is not supported") and downloads a new one.
     * In practice that means this constant moves on every base bump.
     *
     * Note the read below can legitimately return -1 on the FIRST load after a
     * plugin install: the host writes its identity record around the same moment
     * the classloader is built, so the record may not be there yet. That
     * degrades (refuses the shadow) for that one launch and self-heals on the
     * next, which is the safe direction — but it is why a fresh install shows
     * "updated to v?" once before settling.
     */
    private static final long EXPECTED_PLUGIN_VERSION_CODE = 104L;

    /** Written into the APK's assets/ by the build. */
    private static final String SHADOW_ASSET = "bh_pcengine_shadow.dex";
    private static final String SHADOW_DIR = "bh_plugin_shadow";

    /** The host's plaintext trust-on-first-use record for the active plugin. */
    private static final String IDENTITY_FILE = "pc_engine_active_plugin_identity";

    private static final AtomicBoolean LOGGED_SKIP = new AtomicBoolean(false);
    private static final AtomicBoolean TOASTED = new AtomicBoolean(false);

    /**
     * Why dual-motor dispatch is unavailable, or null when the shadow is active.
     *
     * A degraded-but-working build is easy to miss — you just notice months later
     * that rumble feels flat — so a mismatch is surfaced two ways: a one-shot
     * Toast on the game-launch path (see {@link #warnUser}) and this string,
     * which BhVibrationSettingsActivity shows as a banner. Read it from anywhere
     * via {@link #getStatusMessage()}.
     */
    private static volatile String sStatus;

    /** Installed plugin versionCode as last observed, or -1 if unknown. */
    private static volatile long sInstalledVersionCode = -1L;

    private BhPluginShadow() { }

    /**
     * Cross-process mirror of {@link #sStatus}, as a plain file.
     *
     * The plugin — and therefore this class's decision — loads in the
     * ":pcengine" process, while the settings dialog that displays the warning
     * runs in the main UI process. Statics don't cross that boundary, so the
     * status has to be mirrored through storage.
     *
     * This deliberately does NOT use SharedPreferences. Each process keeps its
     * own in-memory SharedPreferencesImpl per file, populated once and never
     * refreshed from disk, so a write in ":pcengine" is invisible to a main
     * process that has already read the file. (MODE_MULTI_PROCESS was the opt-in
     * for re-reading and was deprecated in API 23 for being unreliable.) That
     * asymmetry is survivable for a write-once value like BhMenuGameId's
     * captured id, but not here: this status must be able to go back to
     * "clear" — otherwise the settings banner latches on and keeps warning that
     * dual-motor is off long after it started working.
     *
     * A file re-read on every access has no such cache, and this is read once
     * when a settings screen opens and written once per plugin load, so the cost
     * is irrelevant.
     */
    private static final String STATUS_FILE = "bh_plugin_shadow_status.txt";

    /**
     * Human-readable reason dual-motor rumble is off, or null if it is active.
     * In-process only — prefer {@link #getStatusMessage(Context)} from the UI
     * process, which also reads the cross-process mirror.
     */
    public static String getStatusMessage() {
        return sStatus;
    }

    /**
     * Same as {@link #getStatusMessage()} but falls back to the cross-process
     * mirror, so the main/UI process can report a decision made in ":pcengine".
     */
    public static String getStatusMessage(Context ctx) {
        // The mirror is authoritative, not the static: this process may be the
        // main one, whose sStatus is always null because the decision is made in
        // ":pcengine". Checking sStatus first would be harmless, but reading the
        // file unconditionally keeps one code path for both processes.
        String s = readMirror(ctx);
        if (s != null) return s;
        return sStatus;
    }

    private static String readMirror(Context ctx) {
        try {
            if (ctx == null) return null;
            File f = new File(ctx.getFilesDir(), STATUS_FILE);
            if (!f.isFile() || f.length() == 0L) return null;
            byte[] buf = new byte[(int) Math.min(f.length(), 4096L)];
            int n;
            try (InputStream in = new java.io.FileInputStream(f)) {
                n = in.read(buf);
            }
            if (n <= 0) return null;
            String s = new String(buf, 0, n, "UTF-8").trim();
            return s.isEmpty() ? null : s;
        } catch (Throwable t) {
            return null;
        }
    }

    /**
     * Persist (or clear) the mirror. Best-effort.
     *
     * Clearing deletes the file rather than truncating it, so a reader can never
     * observe a half-written record as a live warning.
     */
    private static void mirrorStatus(Context ctx, String message) {
        try {
            if (ctx == null) return;
            File f = new File(ctx.getFilesDir(), STATUS_FILE);
            if (message == null || message.isEmpty()) {
                if (f.isFile() && !f.delete()) {
                    Log.w(TAG, "could not clear " + f);
                }
                return;
            }
            File tmp = new File(f.getPath() + ".tmp");
            try (OutputStream os = new FileOutputStream(tmp)) {
                os.write(message.getBytes("UTF-8"));
                os.flush();
                ((FileOutputStream) os).getFD().sync();
            }
            if (!tmp.renameTo(f)) {
                tmp.delete();
                Log.w(TAG, "could not move shadow status into place");
            }
        } catch (Throwable t) {
            Log.w(TAG, "could not mirror shadow status", t);
        }
    }

    /** Installed PC-engine plugin versionCode, or -1 if not yet determined. */
    public static long getInstalledPluginVersionCode() {
        return sInstalledVersionCode;
    }

    /** Plugin versionCode this build's shadow classes were cut against. */
    public static long getExpectedPluginVersionCode() {
        return EXPECTED_PLUGIN_VERSION_CODE;
    }

    /**
     * Smali entry point, injected at index 0 of
     * {@code PluginClassLoader.<init>(String, String, String, String,
     * ClassLoader, lnc, PluginClassLoadingPolicy)}.
     *
     * @param pluginId the plugin being loaded (ctor p1)
     * @param dexPath  the plugin APK path the host wants to load (ctor p2)
     * @return dexPath, optionally with our shadow dex prepended
     */
    public static String dexPath(String pluginId, String dexPath) {
        try {
            if (dexPath == null || dexPath.isEmpty()) return dexPath;
            if (!PC_ENGINE_PLUGIN_ID.equals(pluginId)) return dexPath;

            Context ctx = currentApplication();
            if (ctx == null) {
                degrade(null, "GameScrub: dual-motor rumble is off — no app "
                        + "context when the PC engine loaded.");
                return dexPath;
            }
            long installed = readInstalledPluginVersionCode(ctx);
            sInstalledVersionCode = installed;

            File shadow = ensureShadowDex(ctx);
            if (shadow == null) {
                degrade(ctx, "GameScrub: dual-motor rumble is off — could not "
                        + "prepare the plugin shadow dex (see logcat "
                        + TAG + ").");
                return dexPath;
            }

            // Fast path: this is exactly the plugin build the shadow was cut
            // against, so there is nothing to verify.
            if (installed != EXPECTED_PLUGIN_VERSION_CODE) {
                // Different (or unreadable) plugin. Rather than refuse outright —
                // which is what every base bump used to trigger, taking the
                // plugin-side privacy stubs down with dual-motor — check whether
                // the classes we actually override are still shaped the same.
                String sha = readInstalledPluginSha(ctx);
                File cache = sha == null ? null : compatCacheFile(ctx, sha, shadow);
                String cached = cache == null ? null : readCachedVerdict(cache);
                boolean ok;
                if ("ok".equals(cached) || "bad".equals(cached)) {
                    ok = "ok".equals(cached);
                    Log.i(TAG, "compat probe: cached verdict for this plugin: "
                            + cached);
                } else {
                    ok = probeShadowCompatible(ctx, shadow, dexPath);
                    if (cache != null) writeCachedVerdict(cache, ok ? "ok" : "bad");
                }
                if (!ok) {
                    degrade(ctx, "GameScrub: dual-motor rumble is OFF — the PC "
                            + "engine plugin updated to v"
                            + (installed < 0 ? "?" : String.valueOf(installed))
                            + " and its internals no longer match this build (cut "
                            + "against v" + EXPECTED_PLUGIN_VERSION_CODE
                            + "). Sustained rumble still works. Update GameScrub "
                            + "to restore dual-motor.");
                    return dexPath;
                }
                Log.i(TAG, "plugin v" + installed + " differs from the pinned v"
                        + EXPECTED_PLUGIN_VERSION_CODE
                        + " but is structurally compatible — shadowing anyway");
            }

            String combined = shadow.getAbsolutePath() + File.pathSeparator + dexPath;
            sStatus = null;
            // Clear any stale warning from a previous plugin/app version so the
            // settings banner disappears once dual-motor is working again.
            mirrorStatus(ctx, null);
            Log.i(TAG, "dual-motor ACTIVE — shadowing PC engine plugin v"
                    + installed + ": " + combined);
            return combined;
        } catch (Throwable t) {
            Log.w(TAG, "dexPath failed; leaving it unchanged", t);
            degrade(null, "GameScrub: dual-motor rumble is off — plugin shadow "
                    + "setup failed (" + t.getClass().getSimpleName() + ").");
            return dexPath;
        }
    }

    /**
     * Record a user-visible reason dual-motor is unavailable, log it once, and
     * Toast it once per process.
     *
     * Toasting from here is deliberate: this runs on the game-launch path, which
     * is exactly when a user would otherwise be left wondering why rumble feels
     * different. It is best-effort — a failed Toast must never break a launch —
     * and the message is also retained in {@link #getStatusMessage()} for the
     * settings screen, so the information survives a missed Toast.
     */
    private static void degrade(Context ctx, String message) {
        sStatus = message;
        if (LOGGED_SKIP.compareAndSet(false, true)) Log.w(TAG, message);
        // Callers that failed before they had a Context still deserve a banner;
        // retry the lookup here rather than dropping the mirror write, which
        // would leave a previous run's stale message on disk.
        if (ctx == null) ctx = currentApplication();
        if (ctx != null) {
            mirrorStatus(ctx, message);
            warnUser(ctx, message);
        }
    }

    private static void warnUser(final Context ctx, final String message) {
        if (!TOASTED.compareAndSet(false, true)) return;
        try {
            final Context app = ctx.getApplicationContext() != null
                    ? ctx.getApplicationContext() : ctx;
            Runnable show = new Runnable() {
                @Override public void run() {
                    try {
                        Toast.makeText(app, message, Toast.LENGTH_LONG).show();
                    } catch (Throwable t) {
                        Log.w(TAG, "toast failed", t);
                    }
                }
            };
            if (Looper.myLooper() == Looper.getMainLooper()) {
                show.run();
            } else {
                // The classloader is built off the main thread; Toast needs it.
                new Handler(Looper.getMainLooper()).post(show);
            }
        } catch (Throwable t) {
            Log.w(TAG, "could not surface the dual-motor warning", t);
        }
    }

    /**
     * Parse {@code versionCode=} out of the host's identity record. Reading the
     * host's own committed record (rather than, say, stat'ing the APK) means we
     * agree with whatever the host is about to validate and load.
     *
     * @return the installed plugin versionCode, or -1 if unreadable
     */
    /**
     * The plugin's SHA-256 as recorded by the host, or null.
     *
     * Used only as the compat-probe cache key. The hash is the right key rather
     * than the versionCode: a re-published plugin can reuse a version number, and
     * reusing a stale verdict for different bytes is exactly the mistake this
     * whole probe exists to avoid.
     */
    private static String readInstalledPluginSha(Context ctx) {
        String v = readIdentityField(ctx, "sha256=");
        return (v == null || v.isEmpty()) ? null : v;
    }

    /** One {@code key=value} line out of the host's identity record. */
    private static String readIdentityField(Context ctx, String prefix) {
        try {
            File f = new File(ctx.getFilesDir(), IDENTITY_FILE);
            if (!f.isFile()) return null;
            byte[] buf = new byte[(int) Math.min(f.length(), 8192L)];
            try (InputStream in = new java.io.FileInputStream(f)) {
                int n = in.read(buf);
                if (n <= 0) return null;
                String[] rows = new String(buf, 0, n, "UTF-8").split("\n");
                for (String line : rows) {
                    line = line.trim();
                    if (line.startsWith(prefix)) {
                        return line.substring(prefix.length()).trim();
                    }
                }
            }
        } catch (Throwable t) {
            Log.w(TAG, "could not read " + prefix + " from the identity record", t);
        }
        return null;
    }

    private static long readInstalledPluginVersionCode(Context ctx) {
        try {
            File f = new File(ctx.getFilesDir(), IDENTITY_FILE);
            if (!f.isFile()) return -1L;
            byte[] buf = new byte[(int) Math.min(f.length(), 8192L)];
            try (InputStream in = new java.io.FileInputStream(f)) {
                int n = in.read(buf);
                if (n <= 0) return -1L;
                for (String line : new String(buf, 0, n, "UTF-8").split("\n")) {
                    line = line.trim();
                    if (line.startsWith("versionCode=")) {
                        return Long.parseLong(
                                line.substring("versionCode=".length()).trim());
                    }
                }
            }
        } catch (Throwable t) {
            Log.w(TAG, "could not read plugin identity record", t);
        }
        return -1L;
    }

    /**
     * Extract assets/{@value #SHADOW_ASSET} to app-private storage and mark it
     * read-only (required to load a dex on Android 14+).
     *
     * The filename carries the expected plugin versionCode so a bump can never
     * silently reuse a stale extraction.
     */
    private static synchronized File ensureShadowDex(Context ctx) {
        try {
            File dir = new File(ctx.getFilesDir(), SHADOW_DIR);
            if (!dir.isDirectory() && !dir.mkdirs()) {
                Log.w(TAG, "could not create " + dir);
                return null;
            }
            File out = new File(dir,
                    "shadow_v" + EXPECTED_PLUGIN_VERSION_CODE + ".dex");

            AssetManager am = ctx.getAssets();
            long assetLen;
            try (InputStream probe = am.open(SHADOW_ASSET)) {
                assetLen = probe.available();
            }
            // Re-extract if absent or size-mismatched (e.g. after an app update
            // that shipped a rebuilt shadow for the same plugin version).
            if (out.isFile() && out.length() == assetLen) {
                if (!out.canWrite()) return out;
                if (out.setReadOnly()) return out;
            }

            File tmp = new File(dir, out.getName() + ".tmp");
            try (InputStream in = am.open(SHADOW_ASSET);
                 OutputStream os = new FileOutputStream(tmp)) {
                byte[] b = new byte[64 * 1024];
                int n;
                while ((n = in.read(b)) > 0) os.write(b, 0, n);
                os.flush();
                ((FileOutputStream) os).getFD().sync();
            }
            if (out.isFile() && !out.delete()) {
                // Read-only from a previous run; clear the bit so we can replace.
                out.setWritable(true, true);
                out.delete();
            }
            if (!tmp.renameTo(out)) {
                Log.w(TAG, "could not move shadow dex into place");
                tmp.delete();
                return null;
            }
            // Android 14+ refuses to load a writable dex.
            if (!out.setReadOnly()) {
                Log.w(TAG, "could not mark shadow dex read-only; refusing to load "
                        + "it (Android 14+ rejects writable dex files)");
                return null;
            }
            Log.i(TAG, "extracted shadow dex: " + out.getAbsolutePath()
                    + " (" + out.length() + " bytes)");
            return out;
        } catch (Throwable t) {
            Log.w(TAG, "ensureShadowDex failed", t);
            return null;
        }
    }

    /** ActivityThread.currentApplication(), reflectively (no host deps). */
    private static Context currentApplication() {
        try {
            Class<?> at = Class.forName("android.app.ActivityThread");
            Method m = at.getMethod("currentApplication");
            Object app = m.invoke(null);
            return (app instanceof Context) ? (Context) app : null;
        } catch (Throwable t) {
            return null;
        }
    }
    /**
     * Companion manifest written by build_plugin_shadow_dex.py: the FQNs of
     * exactly the classes the shadow dex overrides, one per line.
     */
    private static final String SHADOW_CLASSES_ASSET = "bh_pcengine_shadow.classes";

    /**
     * Bump whenever the probe's logic changes.
     *
     * The verdict cache is keyed by plugin hash, which correctly invalidates when
     * the PLUGIN changes — but not when WE change. Without this, a build shipping
     * a smarter probe would keep serving verdicts reached by the older, dumber
     * one, and a plugin wrongly judged incompatible would stay judged forever.
     * That is not hypothetical: revision 1 refused everything because it loaded
     * the shadow without the plugin behind it on the classpath, so nothing linked.
     */
    private static final int PROBE_REVISION = 2;

    /**
     * Decide whether a shadow built for one plugin is safe to use on a
     * different one, by comparing the classes it overrides against the
     * installed plugin's own copies.
     *
     * Why this exists: the plugin contract has bumped on every base so far
     * (schemaVersion 2/3/4/5 across 6.1.1/6.1.2/6.2.0/6.2.1), and each bump ships
     * a new plugin. A pure versionCode gate therefore refuses on every release —
     * which costs not just dual-motor but the plugin-side privacy stubs, because
     * they ride the same dex. Most of those bumps do not actually change the four
     * classes we override, so refusing was usually over-cautious. This checks the
     * thing we actually care about instead of a version number.
     *
     * What "safe" means here is narrow and deliberate: for every class in the
     * shadow, the plugin's same-named class must declare the SAME constructors,
     * methods and fields, and sit on the same superclass and interfaces. Exact
     * equality, not a subset:
     *
     *   - a member MISSING from the plugin means our copy references something
     *     that no longer exists;
     *   - a member ADDED by the plugin is just as fatal, because our copy REPLACES
     *     the class wholesale, so any caller of the new member would hit a
     *     NoSuchMethodError against our older copy.
     *
     * This directly targets the failure the version pin was protecting against:
     * on plugin 102 the letter `fi3` stopped being the Physical vibrator and
     * became an unrelated kotlinx serializer, and `jv1` kept its name but changed
     * constructor. Both are caught here as shape mismatches, by name, without
     * knowing anything about what those classes are supposed to be.
     *
     * Cost: one throwaway DexClassLoader over the plugin APK. Classes are loaded
     * with initialize=false so no static initialiser runs. The verdict is cached
     * per plugin SHA-256, so this happens once per plugin build rather than once
     * per launch — see {@link #compatCacheFile}.
     */
    private static boolean probeShadowCompatible(Context ctx, File shadowDex,
                                                 String pluginDexPath) {
        long t0 = SystemClock.uptimeMillis();
        try {
            List<String> classes = readShadowClassList(ctx);
            if (classes.isEmpty()) {
                Log.w(TAG, "compat probe: no " + SHADOW_CLASSES_ASSET
                        + " asset, cannot verify — refusing");
                return false;
            }
            ClassLoader parent = BhPluginShadow.class.getClassLoader();
            // "ours" gets the SAME path the real PluginClassLoader will get:
            // shadow first, plugin behind it. The shadow holds only the four
            // overridden classes, so in isolation their superclasses, field types
            // and method parameter types — all plugin-internal R8 letters — do not
            // resolve, and merely reflecting on them throws
            // NoClassDefFoundError. Loading them the way production will is also
            // the more honest test: it asks whether our copies still LINK against
            // this plugin, not just whether their signatures look similar.
            ClassLoader ours = new DexClassLoader(
                    shadowDex.getAbsolutePath() + File.pathSeparator + pluginDexPath,
                    null, null, parent);
            ClassLoader theirs = new DexClassLoader(
                    pluginDexPath, null, null, parent);

            for (String fqn : classes) {
                Class<?> a, b;
                try {
                    a = Class.forName(fqn, false, ours);
                } catch (Throwable t) {
                    Log.w(TAG, "compat probe: " + fqn + " missing from our own "
                            + "shadow dex (manifest/dex disagree)");
                    return false;
                }
                try {
                    b = Class.forName(fqn, false, theirs);
                } catch (Throwable t) {
                    Log.w(TAG, "compat probe: " + fqn + " absent from the "
                            + "installed plugin — shadow would define a class the "
                            + "plugin no longer has");
                    return false;
                }
                String why;
                try {
                    why = shapeMismatch(a, b);
                } catch (Throwable t) {
                    // Reflecting on a class resolves its members' types. A failure
                    // here means one side references something the other plugin no
                    // longer has, which is itself the answer: not compatible.
                    Log.w(TAG, "compat probe: " + fqn + " does not link against "
                            + "this plugin (" + t.getClass().getSimpleName() + ": "
                            + t.getMessage() + ")");
                    return false;
                }
                if (why != null) {
                    Log.w(TAG, "compat probe: " + fqn + " differs — " + why);
                    return false;
                }
            }
            Log.i(TAG, "compat probe: all " + classes.size()
                    + " shadow class(es) match the installed plugin ("
                    + (SystemClock.uptimeMillis() - t0) + " ms)");
            return true;
        } catch (Throwable t) {
            Log.w(TAG, "compat probe failed; refusing the shadow", t);
            return false;
        }
    }

    /** Null when the two classes are structurally interchangeable. */
    private static String shapeMismatch(Class<?> ours, Class<?> theirs) {
        Class<?> sa = ours.getSuperclass(), sb = theirs.getSuperclass();
        String na = sa == null ? "-" : sa.getName();
        String nb = sb == null ? "-" : sb.getName();
        if (!na.equals(nb)) return "superclass " + na + " vs " + nb;
        if (!signatures(ours.getInterfaces()).equals(signatures(theirs.getInterfaces())))
            return "interfaces differ";

        Set<String> a = new TreeSet<>(), b = new TreeSet<>();
        describe(ours, a);
        describe(theirs, b);
        if (a.equals(b)) return null;
        Set<String> missing = new TreeSet<>(a); missing.removeAll(b);
        Set<String> added = new TreeSet<>(b); added.removeAll(a);
        StringBuilder sb2 = new StringBuilder();
        if (!missing.isEmpty()) sb2.append("plugin lacks ").append(missing);
        if (!added.isEmpty()) {
            if (sb2.length() > 0) sb2.append("; ");
            sb2.append("plugin adds ").append(added);
        }
        return sb2.toString();
    }

    private static Set<String> signatures(Class<?>[] cs) {
        Set<String> out = new TreeSet<>();
        for (Class<?> c : cs) out.add(c.getName());
        return out;
    }

    private static void describe(Class<?> c, Set<String> into) {
        for (Constructor<?> ctor : c.getDeclaredConstructors())
            into.add("<init>" + signatures(ctor.getParameterTypes()));
        for (Method m : c.getDeclaredMethods())
            into.add(m.getName() + signatures(m.getParameterTypes())
                    + ":" + m.getReturnType().getName());
        for (Field f : c.getDeclaredFields())
            into.add("#" + f.getName() + ":" + f.getType().getName());
    }

    private static List<String> readShadowClassList(Context ctx) {
        List<String> out = new ArrayList<>();
        try (InputStream in = ctx.getAssets().open(SHADOW_CLASSES_ASSET);
             BufferedReader r = new BufferedReader(
                     new InputStreamReader(in, "UTF-8"))) {
            String line;
            while ((line = r.readLine()) != null) {
                line = line.trim();
                if (!line.isEmpty()) out.add(line);
            }
        } catch (Throwable t) {
            Log.w(TAG, "could not read " + SHADOW_CLASSES_ASSET, t);
        }
        return out;
    }

    /**
     * Where the probe verdict for one plugin build is remembered.
     *
     * Keyed by the plugin's SHA-256 (from the host's own identity record), not by
     * versionCode: the hash changes whenever the APK does, so a re-published
     * plugin under the same version can never reuse a stale verdict.
     */
    private static File compatCacheFile(Context ctx, String pluginSha, File shadow) {
        // The verdict is a function of THREE things, and the key has to cover all
        // of them: the plugin's bytes, the probe's logic, and the shadow we are
        // asking about. Leaving the shadow out was a real bug found in testing —
        // a GameScrub update shipping a shadow rebuilt for the new plugin kept
        // being told "bad" by the verdict cached for the PREVIOUS shadow, so the
        // rebuild appeared not to work.
        return new File(new File(ctx.getFilesDir(), SHADOW_DIR),
                        "compat_r" + PROBE_REVISION
                        + "_s" + shadowFingerprint(shadow)
                        + "_" + pluginSha.substring(0, Math.min(16, pluginSha.length()))
                        + ".verdict");
    }

    /** Cheap content fingerprint of the shadow dex (it is ~17 KB). */
    private static String shadowFingerprint(File shadow) {
        try {
            java.security.MessageDigest md =
                    java.security.MessageDigest.getInstance("SHA-256");
            byte[] buf = new byte[16 * 1024];
            try (InputStream in = new java.io.FileInputStream(shadow)) {
                int n;
                while ((n = in.read(buf)) > 0) md.update(buf, 0, n);
            }
            StringBuilder sb = new StringBuilder();
            byte[] d = md.digest();
            for (int i = 0; i < 6; i++) sb.append(String.format("%02x", d[i]));
            return sb.toString();
        } catch (Throwable t) {
            // Unknown fingerprint must not collide with a real one, and must not
            // be reused across runs: fall back to size, which at least changes
            // whenever the shadow does in practice.
            return "len" + shadow.length();
        }
    }

    private static String readCachedVerdict(File f) {
        try {
            if (!f.isFile()) return null;
            byte[] buf = new byte[16];
            int n;
            try (InputStream in = new java.io.FileInputStream(f)) { n = in.read(buf); }
            if (n <= 0) return null;
            return new String(buf, 0, n, "UTF-8").trim();
        } catch (Throwable t) {
            return null;
        }
    }

    private static void writeCachedVerdict(File f, String verdict) {
        try {
            File dir = f.getParentFile();
            if (dir != null && !dir.isDirectory() && !dir.mkdirs()) return;
            try (OutputStream os = new FileOutputStream(f)) {
                os.write(verdict.getBytes("UTF-8"));
                os.flush();
                ((FileOutputStream) os).getFD().sync();
            }
        } catch (Throwable t) {
            Log.w(TAG, "could not cache the compat verdict", t);
        }
    }
}
