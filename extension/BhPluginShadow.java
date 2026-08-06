package com.xj.winemu.vibration;

import android.content.Context;
import android.content.res.AssetManager;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.widget.Toast;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.lang.reflect.Method;
import java.util.concurrent.atomic.AtomicBoolean;

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
 *   2. The installed plugin's versionCode must equal
 *      {@link #EXPECTED_PLUGIN_VERSION_CODE}, read from the host's own identity
 *      record. Our shadow classes are copies of a SPECIFIC plugin build's
 *      classes; letting a stale shadow win over a newer plugin is exactly how
 *      you get NoSuchFieldError / verifier faults deep inside Wine. On a plugin
 *      bump, re-run the plugin patch scripts and raise this constant.
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
     * build_plugin_shadow_dex.py against the new plugin.
     */
    private static final long EXPECTED_PLUGIN_VERSION_CODE = 101L;

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
     * SharedPreferences mirror of {@link #sStatus}.
     *
     * The plugin — and therefore this class's decision — loads in the
     * ":pcengine" process, while the settings dialog that displays the warning
     * runs in the main UI process. Statics don't cross that boundary, so the
     * status is mirrored to a (cross-process readable) prefs file, exactly as
     * BhMenuGameId does for the captured game id.
     */
    private static final String STATUS_PREFS = "bh_plugin_shadow_status";
    private static final String KEY_STATUS = "status";

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
        String local = sStatus;
        if (local != null) return local;
        try {
            if (ctx == null) return null;
            String s = ctx.getSharedPreferences(STATUS_PREFS, Context.MODE_PRIVATE)
                    .getString(KEY_STATUS, null);
            return (s == null || s.isEmpty()) ? null : s;
        } catch (Throwable t) {
            return null;
        }
    }

    /** Persist (or clear) the mirror. Best-effort. */
    @SuppressWarnings("deprecation")
    private static void mirrorStatus(Context ctx, String message) {
        try {
            if (ctx == null) return;
            ctx.getSharedPreferences(STATUS_PREFS, Context.MODE_PRIVATE)
                    .edit().putString(KEY_STATUS, message == null ? "" : message)
                    .commit();
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
            if (installed != EXPECTED_PLUGIN_VERSION_CODE) {
                degrade(ctx, "GameScrub: dual-motor rumble is OFF — the PC engine "
                        + "plugin updated to v"
                        + (installed < 0 ? "?" : String.valueOf(installed))
                        + " but this build only supports v"
                        + EXPECTED_PLUGIN_VERSION_CODE
                        + ". Sustained rumble still works. Update GameScrub to "
                        + "restore dual-motor.");
                return dexPath;
            }
            File shadow = ensureShadowDex(ctx);
            if (shadow == null) {
                degrade(ctx, "GameScrub: dual-motor rumble is off — could not "
                        + "prepare the plugin shadow dex (see logcat "
                        + TAG + ").");
                return dexPath;
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
}
