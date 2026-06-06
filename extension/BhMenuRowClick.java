package com.xj.winemu.vibration;

import android.app.Activity;
import android.content.Intent;
import android.util.Log;

import com.xj.winemu.common.BhMenuGameId;

import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * Onclick handler for the "PC Vibration Settings" row injected into the
 * three per-game library menu surfaces (game detail More Menu, library-tile
 * popup, library-list 3-dot popup).
 *
 * The host APK ships kotlin-stdlib so all Function0/Function1 / Unit
 * references work at runtime. We deliberately don't `implements Function1`
 * here so this fork's javac step doesn't need kotlin-stdlib on the
 * classpath — the host's R8 rename means a direct Java `implements
 * Function1` wouldn't satisfy the host's type check anyway. All Function0
 * / Function1 contracts are fulfilled by {@link java.lang.reflect.Proxy}
 * instances created at row-construction time inside the appendXxxRow
 * helpers below.
 *
 * The Context is resolved at click time by reflectively walking
 * ActivityThread.mActivities to find the currently-resumed Activity. This
 * avoids needing a captured Context at construction time, which would
 * otherwise require the bytecode patch to find an appropriate Context
 * register inside the heavily-obfuscated Compose Composables.
 *
 * The per-game id is read from {@link BhMenuGameId#getCaptured()} (set by
 * the index-0 captureGameId(p0) injection in each of the three menu
 * builders), so the dialog opens scoped to the right game even from a
 * pre-launch menu where no WineActivity is on the stack yet.
 */
public final class BhMenuRowClick {

    private static final String TAG = "BhMenuRowClick";

    /** Cached kotlin.Unit.INSTANCE resolved via reflection — runtime-only
     *  so this Java module compiles without kotlin-stdlib on the classpath. */
    private static volatile Object UNIT;

    private static Object kotlinUnit() {
        Object u = UNIT;
        if (u != null) return u;
        try {
            Class<?> c = Class.forName("kotlin.Unit");
            Field f = c.getField("INSTANCE");
            u = f.get(null);
            UNIT = u;
            return u;
        } catch (Throwable t) {
            return null;
        }
    }

    public Object invoke(Object ignoredFromCompose) {
        try {
            Activity host = resolveTopActivity();
            if (host == null) {
                Log.w(TAG, "no top Activity resolvable; cannot launch settings");
                return kotlinUnit();
            }
            Intent intent = new Intent(host, BhVibrationSettingsActivity.class);
            String gameId = BhMenuGameId.getCaptured();
            if (gameId == null || gameId.isEmpty()) gameId = sniffGameIdFromStack();
            if (gameId != null && !gameId.isEmpty()) {
                // BhVibrationSettingsActivity reads EXTRA_GAME_ID
                // ("bh_vibration.gameId"), not "gameId" — using the
                // wrong key here is why per-game never took effect.
                intent.putExtra(BhVibrationSettingsActivity.EXTRA_GAME_ID, gameId);
            }
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
            host.startActivity(intent);
        } catch (Throwable t) {
            Log.w(TAG, "menu click failed", t);
        }
        return kotlinUnit();
    }

    /** Walk ActivityThread.mActivities to find the most-recently-resumed Activity. */
    private static Activity resolveTopActivity() {
        try {
            Class<?> atCls = Class.forName("android.app.ActivityThread");
            Method cur = atCls.getMethod("currentActivityThread");
            Object at = cur.invoke(null);
            if (at == null) return null;
            Field fActs = atCls.getDeclaredField("mActivities");
            fActs.setAccessible(true);
            Object acts = fActs.get(at);
            if (!(acts instanceof Map)) return null;
            Activity best = null;
            for (Object record : ((Map<?, ?>) acts).values()) {
                if (record == null) continue;
                Field fAct = record.getClass().getDeclaredField("activity");
                fAct.setAccessible(true);
                Object a = fAct.get(record);
                if (!(a instanceof Activity)) continue;
                Activity activity = (Activity) a;
                if (activity.isFinishing()) continue;
                try {
                    Field fPaused = record.getClass().getDeclaredField("paused");
                    fPaused.setAccessible(true);
                    Object paused = fPaused.get(record);
                    if (paused instanceof Boolean && !((Boolean) paused)) {
                        return activity;
                    }
                } catch (NoSuchFieldException ignored) { }
                best = activity;
            }
            return best;
        } catch (Throwable t) {
            Log.w(TAG, "resolveTopActivity failed", t);
            return null;
        }
    }

    /**
     * Game detail More Menu row appender. Constructs a Ltyc; 3-arg row
     * (6.0.4 Liae;) via reflection and adds it to the passed list builder.
     * Called from a single-instruction smali injection inside Lc37.a (6.0.4
     * Lx57.a) — keeps the bytecode patch trivial (no register juggling, no
     * verifier risk) at the cost of a runtime reflection lookup.
     *
     * The obfuscated class names tyc/n55/gv6/v45 are the GameHub 6.0.7
     * letters (6.0.4: iae/o05/pw6/zz4); if a future R8 map shifts them the
     * helper silently no-ops (logged) and the menu falls back to the
     * original rows.
     */
    public static void appendVibrationRowTo(Object menuList) {
        try {
            if (!(menuList instanceof List)) return;
            @SuppressWarnings("unchecked")
            List<Object> list = (List<Object>) menuList;

            Class<?> iaeCls = Class.forName("tyc");
            Class<?> o05Cls = Class.forName("n55");
            Class<?> pw6Cls = Class.forName("gv6");

            // Resolve a gear/settings icon. Lv45 (6.0.4 Lzz4) is the
            // ComposableSingletons class for menu-row icons; the `b0`
            // field holds an Lu3k (6.0.4 Lxrl) Lazy wrapper whose
            // getValue() returns an Ln55 (6.0.4 Lo05; Painter/vector ref).
            // NOTE: v45.b0 in 6.0.7 is a finger/gesture drawable, not a
            // gear — cosmetic; repoint to another v45 icon field if needed.
            Class<?> zz4Cls = Class.forName("v45");
            Field iconHolderField = zz4Cls.getDeclaredField("b0");
            iconHolderField.setAccessible(true);
            Object xrlWrapper = iconHolderField.get(null);
            if (xrlWrapper == null) {
                Log.w(TAG, "zz4.b0 is null; cannot resolve icon");
                return;
            }
            Object iconValue = xrlWrapper.getClass().getMethod("getValue").invoke(xrlWrapper);
            if (!o05Cls.isInstance(iconValue)) {
                Log.w(TAG, "zz4.b0.getValue() did not return Lo05");
                return;
            }

            // R8 renamed kotlin.jvm.functions.Function1 to Lgv6 (6.0.4
            // Lpw6) in the host APK, so our Java `implements
            // Function1<Object, Object>` IS a different JVM class from the
            // host's Lgv6. Ltyc's ctor requires Lgv6 specifically — direct
            // Java implements doesn't satisfy the type check. Fix: a Proxy
            // that implements Lgv6 at runtime and delegates invoke to
            // our BhMenuRowClick.
            final BhMenuRowClick handler = new BhMenuRowClick();
            Object click = java.lang.reflect.Proxy.newProxyInstance(
                pw6Cls.getClassLoader(),
                new Class<?>[]{ pw6Cls },
                (proxy, method, args) -> {
                    if ("invoke".equals(method.getName()) && method.getParameterCount() == 1) {
                        return handler.invoke(args != null && args.length > 0 ? args[0] : null);
                    }
                    if ("equals".equals(method.getName())) return proxy == args[0];
                    if ("hashCode".equals(method.getName())) return System.identityHashCode(proxy);
                    if ("toString".equals(method.getName())) return "BhMenuRowClickProxy";
                    return null;
                }
            );

            Constructor<?> ctor =
                iaeCls.getDeclaredConstructor(o05Cls, String.class, pw6Cls);
            ctor.setAccessible(true);

            Object row = ctor.newInstance(iconValue, "PC Vibration Settings", click);
            list.add(row);
        } catch (Throwable t) {
            Log.w(TAG, "appendVibrationRowTo failed", t);
        }
    }

    /**
     * Library-tile popup variant (6.0.7 Ly7c.f, 6.0.4 Lted.f). Rows use
     * Lg6c(String actionId, Ln55 icon, String label, Lev6 onClick) (6.0.4
     * Lscd / Lo05 / Lnw6) with a Function0 click handler (no args), and the
     * rows are collected into an ArrayList via the host's arrayListOf helper
     * (6.0.7 Llp0;->R, 6.0.4 Lqs2;->H).
     *
     * The smali injection replaces that list with a new ArrayList containing
     * the original rows plus our PC Vibration Settings row. Returns an
     * ArrayList (NOT a bare List): in 6.0.7 the host threads the result
     * register through ArrayList.size()/get(I), so the return type must be
     * Ljava/util/ArrayList; or dex verification fails. The smali captures the
     * return value and reassigns it to the list register.
     */
    public static ArrayList<Object> appendScdRowToTedList(Object original) {
        try {
            if (!(original instanceof List)) return safeReturnArrayList(original);
            List<?> origList = (List<?>) original;
            ArrayList<Object> augmented = new ArrayList<>(origList);

            Class<?> scdCls = Class.forName("g6c");
            Class<?> o05Cls = Class.forName("n55");
            Class<?> nw6Cls = Class.forName("ev6");
            Class<?> zz4Cls = Class.forName("v45");

            Field iconField = zz4Cls.getDeclaredField("b0");
            iconField.setAccessible(true);
            Object xrlWrapper = iconField.get(null);
            if (xrlWrapper == null) return safeReturnArrayList(original);
            Object iconValue = xrlWrapper.getClass().getMethod("getValue").invoke(xrlWrapper);
            if (!o05Cls.isInstance(iconValue)) return safeReturnArrayList(original);

            // Function0 onClick via Proxy implementing Lnw6.
            final BhMenuRowClick handler = new BhMenuRowClick();
            Object click = java.lang.reflect.Proxy.newProxyInstance(
                nw6Cls.getClassLoader(),
                new Class<?>[]{ nw6Cls },
                (proxy, method, args) -> {
                    if ("invoke".equals(method.getName()) && method.getParameterCount() == 0) {
                        return handler.invoke(null);
                    }
                    if ("equals".equals(method.getName())) return proxy == args[0];
                    if ("hashCode".equals(method.getName())) return System.identityHashCode(proxy);
                    if ("toString".equals(method.getName())) return "BhMenuRowClickProxy0";
                    return null;
                }
            );

            Constructor<?> ctor =
                scdCls.getDeclaredConstructor(String.class, o05Cls, String.class, nw6Cls);
            ctor.setAccessible(true);

            Object row = ctor.newInstance(
                "local_detail_menu_pc_vibration",
                iconValue,
                "PC Vibration Settings",
                click
            );
            augmented.add(row);
            return augmented;
        } catch (Throwable t) {
            Log.w(TAG, "appendScdRowToTedList failed", t);
            return safeReturnArrayList(original);
        }
    }

    /**
     * Library-list 3-dot popup variant (6.0.7 Levb.b0, 6.0.4 Lpzc.j0). Uses
     * a third row data class:
     *   Lstc(Ldwj label, Lev6 onClick, int)  [synthetic 3-arg ctor]
     *     (6.0.4: Lz4e(Lell, Lnw6, int))
     *     - Ldwj extends Lshg(String key, Set<String> locales) (6.0.4
     *       Lell extends Ltdi), a Compose Multiplatform string-resource
     *       descriptor; resolved at render time by Lok8.c0 (6.0.4 Lxd3.l1).
     *     - Lev6 is Function0 (no-arg lambda) (6.0.4 Lnw6).
     *
     * Our label key "bh_pc_vibration_label" is also patched into the
     * resolver Lok8.c0 via maybeResolveCustomLabel below, so the
     * Compose runtime doesn't need a matching CVR entry to render
     * "PC Vibration Settings".
     */
    public static List<Object> appendLibraryPopupRow(Object original) {
        try {
            if (!(original instanceof List)) return safeReturn(original);
            List<?> origList = (List<?>) original;
            ArrayList<Object> augmented = new ArrayList<>(origList);

            Class<?> z4eCls = Class.forName("stc");
            Class<?> ellCls = Class.forName("dwj");
            Class<?> tdiCls = Class.forName("shg");
            Class<?> nw6Cls = Class.forName("ev6");

            // Ldwj (6.0.4 Lell) is a Kotlin empty subclass of abstract
            // Lshg(String, Set<String>) (6.0.4 Ltdi) — at bytecode level the
            // host does `new-instance Ldwj; invoke Lshg.<init>`, but
            // ellCls.getDeclaredConstructor(String.class, Set.class)
            // returns nothing because Ldwj declares no ctor itself.
            // Workaround: allocate Ldwj via sun.misc.Unsafe (skips
            // ctor entirely) and reflect-set the inherited Lshg
            // fields a (key) and b (locales).
            Class<?> unsafeCls = Class.forName("sun.misc.Unsafe");
            Field theUnsafe = unsafeCls.getDeclaredField("theUnsafe");
            theUnsafe.setAccessible(true);
            Object unsafe = theUnsafe.get(null);
            Object label = unsafeCls.getMethod("allocateInstance", Class.class)
                .invoke(unsafe, ellCls);
            Field aField = tdiCls.getDeclaredField("a");
            aField.setAccessible(true);
            aField.set(label, "string:bh_pc_vibration_label");
            Field bField = tdiCls.getDeclaredField("b");
            bField.setAccessible(true);
            bField.set(label, Collections.emptySet());

            final BhMenuRowClick handler = new BhMenuRowClick();
            Object click = java.lang.reflect.Proxy.newProxyInstance(
                nw6Cls.getClassLoader(),
                new Class<?>[]{ nw6Cls },
                (proxy, method, args) -> {
                    if ("invoke".equals(method.getName()) && method.getParameterCount() == 0) {
                        return handler.invoke(null);
                    }
                    if ("equals".equals(method.getName())) return proxy == args[0];
                    if ("hashCode".equals(method.getName())) return System.identityHashCode(proxy);
                    if ("toString".equals(method.getName())) return "BhLibPopupRowClick";
                    return null;
                }
            );

            // Lstc(Ldwj;Lev6;I)V synthetic ctor (6.0.4 Lz4e(Lell;Lnw6;I)V)
            // — int=0 should be a safe default group/category marker.
            Constructor<?> z4eCtor =
                z4eCls.getDeclaredConstructor(ellCls, nw6Cls, int.class);
            z4eCtor.setAccessible(true);
            Object row = z4eCtor.newInstance(label, click, 0);

            augmented.add(row);
            return augmented;
        } catch (Throwable t) {
            Log.w(TAG, "appendLibraryPopupRow failed", t);
            return safeReturn(original);
        }
    }

    @SuppressWarnings("unchecked")
    private static List<Object> safeReturn(Object o) {
        if (o instanceof List) return (List<Object>) o;
        return new ArrayList<>();
    }

    /** ArrayList-typed fallback for appendScdRowToTedList — its 6.0.7 caller
     *  (Ly7c.f) consumes the result via ArrayList.size()/get(I), so the
     *  return must be a concrete ArrayList, never a bare List. */
    @SuppressWarnings("unchecked")
    private static ArrayList<Object> safeReturnArrayList(Object o) {
        if (o instanceof ArrayList) return (ArrayList<Object>) o;
        if (o instanceof java.util.Collection) {
            return new ArrayList<>((java.util.Collection<Object>) o);
        }
        return new ArrayList<>();
    }

    /**
     * Patched into the resolver Lok8.c0 (6.0.4 Lxd3.l1) to short-circuit our
     * sentinel key BEFORE it hits the Compose Multiplatform resource lookup
     * (which throws "Resource with ID='string:bh_pc_vibration_label'
     * not found" because the runtime expects a manifest registration
     * alongside the .cvr entry, and just appending to the .cvr isn't
     * enough).
     *
     * Returns the row label when our sentinel key matches; returns
     * null otherwise so the stock resolver path runs unchanged.
     */
    public static String maybeResolveCustomLabel(Object ell) {
        return resolveCustomLabel(ell, true);
    }

    /**
     * Same as {@link #maybeResolveCustomLabel} but with the
     * kickImportFromDialogOpen side effect suppressed. Used by the
     * non-Compose / suspend resolver hooks (Lok8;->d0/J/K, 6.0.4
     * Lxd3;->m1/P0/Q0): those paths
     * exist to surface resource strings outside composition (e.g. toast
     * format strings), so we want our label overrides to apply but we
     * absolutely do not want a stray non-Compose lookup of the import-
     * dialog-title key to launch a SAF file picker behind the user's back.
     */
    public static String maybeResolveCustomLabelNoKick(Object ell) {
        return resolveCustomLabel(ell, false);
    }

    private static String resolveCustomLabel(Object ell, boolean fireSideEffects) {
        try {
            Field aField = Class.forName("shg").getDeclaredField("a");
            aField.setAccessible(true);
            Object key = aField.get(ell);
            if (key == null) return null;

            String label = null;
            if ("string:bh_pc_vibration_label".equals(key)) {
                label = "PC Vibration Settings";
            } else if ("string:bh_vjoy_export_label".equals(key)) {
                label = "Export to file";
            } else if ("string:bh_vjoy_import_label".equals(key)) {
                label = "Import from file";
            }
            // VJoy share-flow stock-string OVERRIDES (not new entries; we
            // hijack the host's own keys before its CVR lookup runs). The
            // share button now exports to a local .gtheme file, so relabel
            // the user-visible strings to match.
            //
            // 6.0.7 CAVEAT: the VJoy share/import UI was redesigned into a new
            // `features_vjoy_main2_*` resource namespace, so the 6.0.4 keys
            // below (item_fun_share, dialog_prepare_share_*, dialog_import_
            // share_code_*, main_toast_share_failed) NO LONGER EXIST in 6.0.7
            // — these branches simply never match and fall through to the
            // stock label (graceful no-op). The load-bearing export/import
            // bytecode hooks (interceptShare/Upload/Apply on Lkkm;->i/j/d) are
            // method-anchored and unaffected, and interceptApply on
            // getMapByShareCode is the designed fallback that fires SAF when
            // the dialog-skip below can't trigger. To restore the cosmetic
            // relabels + dialog-skip, re-map these keys to their 6.0.7
            // main2 equivalents once confirmed on-device.
            else if ("string:features_vjoy_item_fun_share".equals(key)) {
                label = "Export";                       // was "Share"
            } else if ("string:features_vjoy_dialog_prepare_share_title".equals(key)) {
                label = "Name Profile";                 // was "Publish to Cloud"
            } else if ("string:features_vjoy_dialog_prepare_share_placeholder".equals(key)) {
                label = "Profile name";                 // was "Share name"
            }
            // NOTE: the post-export "Cloud Backup Code" dialog
            // (features_vjoy_dialog_share_code_*) is no longer relabeled or
            // dismissed here — BhVjoyShareHook.interceptShare (the shareMap
            // hook) THROWS to abort the cloud publish before that dialog is
            // ever composed, so those resource keys never resolve.
            // Import: relabel the entry point and use the import-dialog title
            // resolution as the composition-time signal to fire the SAF file
            // picker and skip the share-code dialog entirely.
            else if ("string:features_vjoy_main_action_import".equals(key)) {
                label = "Import Layout from File";  // was "Import Layout"
            } else if ("string:features_vjoy_dialog_import_share_code_title".equals(key)) {
                label = "Import Layout";
                // This resource ONLY resolves when the import dialog is being
                // composed, so use it as the "dialog opening" signal and fire
                // SAF immediately. SAF takes focus over the briefly-composed
                // dialog; after the user picks/cancels we dismiss the leftover
                // dialog via a programmatic BACK. IMPORT_IN_FLIGHT (in the hook)
                // gates against the dozens of recompositions per dialog show.
                if (fireSideEffects) {
                    try {
                        com.xj.winemu.exportcontrols.BhVjoyShareHook.kickImportFromDialogOpen();
                    } catch (Throwable t) {
                        Log.w(TAG, "kickImportFromDialogOpen threw", t);
                    }
                }
            } else if ("string:features_vjoy_dialog_import_share_code_placeholder".equals(key)) {
                label = "Opening file picker…";
            }
            // Suppress the host's "Share failed: %1$s" toast that fires after
            // interceptShare throws. The stock format string interpolates our
            // exception message ("Share failed: bh_export_local_only"), which
            // is jarring noise on top of the success toast from
            // BhSafProxyActivity. Overriding to "" makes String.format produce
            // an empty string, and Android skips effectively-empty toasts.
            else if ("string:features_vjoy_main_toast_share_failed".equals(key)) {
                label = "";
            }

            if (label != null) {
                Log.i(TAG, "maybeResolveCustomLabel key=" + key + " → '" + label + "'");
                return label;
            }
        } catch (Throwable t) {
            Log.w(TAG, "maybeResolveCustomLabel error", t);
        }
        return null;
    }

    /** If a WineActivity is in the stack, grab its gameId Intent extra. */
    private static String sniffGameIdFromStack() {
        try {
            Class<?> atCls = Class.forName("android.app.ActivityThread");
            Method cur = atCls.getMethod("currentActivityThread");
            Object at = cur.invoke(null);
            if (at == null) return null;
            Field fActs = atCls.getDeclaredField("mActivities");
            fActs.setAccessible(true);
            Object acts = fActs.get(at);
            if (!(acts instanceof Map)) return null;
            for (Object record : ((Map<?, ?>) acts).values()) {
                if (record == null) continue;
                Field fAct = record.getClass().getDeclaredField("activity");
                fAct.setAccessible(true);
                Object a = fAct.get(record);
                if (!(a instanceof Activity)) continue;
                String clsName = a.getClass().getName();
                if (!clsName.endsWith(".WineActivity")) continue;
                Intent it = ((Activity) a).getIntent();
                if (it == null) continue;
                String gid = it.getStringExtra("gameId");
                if (gid != null && !gid.isEmpty()) return gid;
            }
        } catch (Throwable ignored) { }
        return null;
    }
}
