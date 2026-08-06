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
            try {
                // 6.1.1 keeps kotlin.Unit unobfuscated, so this is the live path.
                Class<?> c = Class.forName("kotlin.Unit");
                u = c.getField("INSTANCE").get(null);
            } catch (Throwable keptNameMissing) {
                // 6.0.9 and earlier: R8 obfuscated kotlin.Unit -> Lx6m;
                // (INSTANCE = a). Kept as a fallback so the helper still works
                // if a future base re-enables stdlib obfuscation.
                Class<?> c = Class.forName("x6m");
                u = c.getDeclaredField("a").get(null);
            }
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

    // ─────────────────────────────────────────────────────────────────────
    // Host types for the three menu surfaces (GameHub 6.1.1).
    //
    // 6.1.1 stopped obfuscating the Kotlin function interfaces and the Compose
    // Multiplatform resource classes, so only the three row data classes are
    // R8 letters now:
    //   Ll2h;(DrawableResource icon, String label, Function1 onClick[, Z])
    //       game-detail More Menu row      (6.0.9 Luhd;, 6.0.4 Liae;)
    //   Lizf;(String actionId, DrawableResource icon, String label,
    //         Function0 onClick)
    //       library-tile popup row         (6.0.9 Lxoc;, 6.0.4 Lscd;)
    //   Lovg;(StringResource label, Function0 onClick, int)
    //       library-list 3-dot popup row   (6.0.9 Lpcd;, 6.0.4 Lz4e;)
    // If a future R8 map shifts these, each appender logs and no-ops, leaving
    // the stock rows untouched.
    // ─────────────────────────────────────────────────────────────────────
    private static final String ROW_MORE_MENU  = "l2h";
    private static final String ROW_TILE_POPUP = "izf";
    private static final String ROW_THREE_DOT  = "ovg";

    private static final String FUNCTION0 = "kotlin.jvm.functions.Function0";
    private static final String FUNCTION1 = "kotlin.jvm.functions.Function1";
    private static final String DRAWABLE_RESOURCE =
            "org.jetbrains.compose.resources.DrawableResource";
    private static final String STRING_RESOURCE =
            "org.jetbrains.compose.resources.StringResource";
    // Abstract base of every CMP resource descriptor; still an R8 letter
    // because it is CMP-internal (6.0.9 "o4h", 6.0.4 "tdi").
    private static final String RESOURCE_DESCRIPTOR_BASE = "ull";

    private static final String ROW_LABEL = "PC Vibration Settings";

    // Cached row instances, one per surface. Compose uses the row list as a
    // `remember` key (Composer.changed(list)), so handing back a freshly
    // allocated row on every composition would defeat that memoization and
    // re-run the downstream map on every frame. Building each row once keeps
    // the augmented list content-equal across compositions.
    private static volatile Object CACHED_MORE_MENU_ROW;
    private static volatile Object CACHED_TILE_ROW;
    private static volatile Object CACHED_THREE_DOT_ROW;

    /**
     * Build a Proxy for the host's Function0 / Function1 interface that
     * delegates invoke() to a fresh BhMenuRowClick.
     *
     * A Proxy is required rather than a Java `implements Function1`: even with
     * 6.1.1 keeping the real interface names, this module is compiled without
     * kotlin-stdlib on the classpath (see the class header), so the interface
     * only exists at runtime.
     */
    private static Object newClickProxy(Class<?> fnIface, int argCount, String name) {
        final BhMenuRowClick handler = new BhMenuRowClick();
        return java.lang.reflect.Proxy.newProxyInstance(
            fnIface.getClassLoader(),
            new Class<?>[]{ fnIface },
            (proxy, method, args) -> {
                if ("invoke".equals(method.getName())
                        && method.getParameterCount() == argCount) {
                    return handler.invoke(
                            args != null && args.length > 0 ? args[0] : null);
                }
                if ("equals".equals(method.getName())) return proxy == args[0];
                if ("hashCode".equals(method.getName())) return System.identityHashCode(proxy);
                if ("toString".equals(method.getName())) return name;
                return null;
            }
        );
    }

    /**
     * Read the icon out of a row the host itself built, rather than reaching
     * into a ComposableSingletons field.
     *
     * 6.0.9 resolved the icon from Lyc5;->x (a Lazy holding an Lqd5;), which was
     * both an extra R8 anchor and a correctness trap — an earlier build picked
     * Lyc5;->b0, an icon belonging to a different surface, which faulted Compose
     * at render time. Copying the DrawableResource from an existing sibling row
     * is anchor-free and guaranteed to be a resource this surface can render.
     *
     * @param rows      the surface's current row list
     * @param rowClass  the row data class for this surface
     * @param iconField the row field holding the DrawableResource ("a" on
     *                  Ll2h;, "b" on Lizf;)
     */
    private static Object borrowIconFrom(List<?> rows, Class<?> rowClass,
                                         String iconField) throws Exception {
        Field f = rowClass.getDeclaredField(iconField);
        f.setAccessible(true);
        for (Object row : rows) {
            if (row == null || !rowClass.isInstance(row)) continue;
            Object icon = f.get(row);
            if (icon != null) return icon;
        }
        return null;
    }

    /**
     * Game detail More Menu row appender (6.1.1 Lbk9;->a, 6.0.9 Llc7;->a).
     *
     * Called from a single-instruction smali injection immediately before
     * CollectionsKt.build() seals the row ListBuilder — the builder is still
     * mutable there, so we append in place and the bytecode patch needs no
     * register juggling and carries no verifier risk.
     */
    public static void appendVibrationRowTo(Object menuList) {
        try {
            if (!(menuList instanceof List)) return;
            @SuppressWarnings("unchecked")
            List<Object> list = (List<Object>) menuList;

            Class<?> rowCls = Class.forName(ROW_MORE_MENU);
            Class<?> iconCls = Class.forName(DRAWABLE_RESOURCE);
            Class<?> fn1Cls = Class.forName(FUNCTION1);

            Object row = CACHED_MORE_MENU_ROW;
            if (row == null) {
                Object icon = borrowIconFrom(list, rowCls, "a");
                if (icon == null) {
                    Log.w(TAG, "no sibling More Menu row to borrow an icon from");
                    return;
                }
                Constructor<?> ctor = rowCls.getDeclaredConstructor(
                        iconCls, String.class, fn1Cls);
                ctor.setAccessible(true);
                row = ctor.newInstance(icon, ROW_LABEL,
                        newClickProxy(fn1Cls, 1, "BhMoreMenuRowClick"));
                CACHED_MORE_MENU_ROW = row;
            }
            if (!list.contains(row)) list.add(row);
        } catch (Throwable t) {
            Log.w(TAG, "appendVibrationRowTo failed", t);
        }
    }

    /**
     * Library-tile popup variant (6.0.9 Lqqc.f, 6.0.4 Lted.f). Rows use
     * Lxoc(String actionId, Lqd5 icon, String label, Lr47 onClick) (6.0.4
     * Lscd / Lo05 / Lnw6) with a Function0 click handler (no args), and the
     * rows are collected into an ArrayList via the host's arrayListOf helper
     * (6.0.9 Llp0;->R, 6.0.4 Lqs2;->H).
     *
     * The smali injection replaces that list with a new ArrayList containing
     * the original rows plus our PC Vibration Settings row. Returns an
     * ArrayList (NOT a bare List): in 6.0.9 the host threads the result
     * register through ArrayList.size()/get(I), so the return type must be
     * Ljava/util/ArrayList; or dex verification fails. The smali captures the
     * return value and reassigns it to the list register.
     */
    public static List<Object> appendTilePopupRow(Object original) {
        try {
            if (!(original instanceof List)) return safeReturn(original);
            List<?> origList = (List<?>) original;

            Class<?> rowCls = Class.forName(ROW_TILE_POPUP);
            Class<?> iconCls = Class.forName(DRAWABLE_RESOURCE);
            Class<?> fn0Cls = Class.forName(FUNCTION0);

            Object row = CACHED_TILE_ROW;
            if (row == null) {
                // Icon field on Lizf; is `b` (a=actionId, b=icon, c=label,
                // d=onClick).
                Object icon = borrowIconFrom(origList, rowCls, "b");
                if (icon == null) {
                    Log.w(TAG, "no sibling tile-popup row to borrow an icon from");
                    return safeReturn(original);
                }
                Constructor<?> ctor = rowCls.getDeclaredConstructor(
                        String.class, iconCls, String.class, fn0Cls);
                ctor.setAccessible(true);
                row = ctor.newInstance(
                        "local_detail_menu_pc_vibration",
                        icon,
                        ROW_LABEL,
                        newClickProxy(fn0Cls, 0, "BhTilePopupRowClick"));
                CACHED_TILE_ROW = row;
            }
            if (origList.contains(row)) return safeReturn(original);
            ArrayList<Object> augmented = new ArrayList<>(origList);
            augmented.add(row);
            return augmented;
        } catch (Throwable t) {
            Log.w(TAG, "appendTilePopupRow failed", t);
            return safeReturn(original);
        }
    }

    /**
     * Library-list 3-dot popup variant (6.1.1 Lfel.o, 6.0.9 Lxdc.b0, 6.0.4
     * Lpzc.j0). Uses a third row data class:
     *   Lovg;(StringResource label, Function0 onClick, int)  [synthetic ctor]
     *     (6.0.9: Lpcd(Llok, Lr47, int); 6.0.4: Lz4e(Lell, Lnw6, int))
     *
     * Unlike the other two surfaces this row carries a Compose Multiplatform
     * resource descriptor rather than a plain String label, so the label text
     * is produced at render time by the resolver short-circuit
     * (maybeResolveCustomLabel, patched into StringResourcesKt.stringResource
     * by apply_menu_patches.py) — the Compose runtime therefore doesn't need a
     * matching CVR entry to render "PC Vibration Settings".
     *
     * Appends in place: the caller hands us the still-mutable ListBuilder from
     * CollectionsKt.createListBuilder(), just before build() seals it.
     */
    public static void appendLibraryPopupRowInPlace(Object menuList) {
        try {
            if (!(menuList instanceof List)) return;
            @SuppressWarnings("unchecked")
            List<Object> list = (List<Object>) menuList;

            Object row = CACHED_THREE_DOT_ROW;
            if (row == null) {
                Class<?> rowCls = Class.forName(ROW_THREE_DOT);
                Class<?> srCls = Class.forName(STRING_RESOURCE);
                Class<?> fn0Cls = Class.forName(FUNCTION0);

                // 6.1.1 keeps a real StringResource(String id, String key,
                // Set<ResourceItem> items) constructor, so the label descriptor
                // can just be constructed. 6.0.9's Llok; declared no ctor of its
                // own (it only inherited Lo4h;'s), which forced an ugly
                // sun.misc.Unsafe.allocateInstance + reflect-set of the
                // inherited fields; that hack is gone.
                //
                // The id carries the "string:" prefix — that is what the
                // resolver short-circuit (maybeResolveCustomLabel, patched into
                // StringResourcesKt.stringResource) matches on, and what the
                // base class Lull; stores in field `a`.
                Constructor<?> srCtor = srCls.getDeclaredConstructor(
                        String.class, String.class, java.util.Set.class);
                srCtor.setAccessible(true);
                Object label = srCtor.newInstance(
                        "string:bh_pc_vibration_label",
                        "bh_pc_vibration_label",
                        Collections.emptySet());

                // Lovg;(StringResource, Function0, int) synthetic ctor
                // (6.0.9 Lpcd(Llok;Lr47;I)). The real ctor is
                // (StringResource, boolean, Function0); the synthetic 3-arg form
                // is what the host's own call sites use, and int=0 maps to the
                // boolean `false` default.
                Constructor<?> rowCtor = rowCls.getDeclaredConstructor(
                        srCls, fn0Cls, int.class);
                rowCtor.setAccessible(true);
                row = rowCtor.newInstance(label,
                        newClickProxy(fn0Cls, 0, "BhLibPopupRowClick"), 0);
                CACHED_THREE_DOT_ROW = row;
            }
            if (!list.contains(row)) list.add(row);
        } catch (Throwable t) {
            Log.w(TAG, "appendLibraryPopupRowInPlace failed", t);
        }
    }

    @SuppressWarnings("unchecked")
    private static List<Object> safeReturn(Object o) {
        if (o instanceof List) return (List<Object>) o;
        return new ArrayList<>();
    }

    /**
     * Patched into StringResourcesKt.stringResource (6.0.9 Ly99.Z, 6.0.4
     * Lxd3.l1) to short-circuit our
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
     * non-Compose / suspend resolver hooks (the StringResourcesKt
     * stringResource-with-args and getString overloads; 6.0.9 Ly99;->d0/J/K,
     * 6.0.4 Lxd3;->m1/P0/Q0): those paths
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
            // Lull; is the abstract base of the Compose Multiplatform resource
            // descriptors (6.0.9 Lo4h;, 6.0.4 Ltdi;); field `a` holds the
            // "string:<key>" id and `b` the ResourceItem set. StringResource
            // extends it, so reading the base field covers every descriptor
            // subtype the resolver overloads receive.
            Field aField = Class.forName(RESOURCE_DESCRIPTOR_BASE)
                    .getDeclaredField("a");
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
            // 6.0.7: the VJoy share/import strings moved from the
            // `features_vjoy_*` namespace to the `common.vjoy` bundle with
            // `common_vjoy_layout_*` keys (re-verified from the 6.0.9
            // CVR). The keys below are the 6.0.9 names; the resource-id
            // format is still "string:" + key (proven by the menu-row label
            // working). Falls through to the stock label on any non-match.
            else if ("string:common_vjoy_layout_func_share".equals(key)) {
                label = "Export";                       // was "Share"
            } else if ("string:common_vjoy_layout_dialog_prepare_share_title".equals(key)) {
                label = "Name Profile";                 // was "Publish to Cloud"
            } else if ("string:common_vjoy_layout_dialog_prepare_share_placeholder".equals(key)) {
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
            } else if ("string:common_vjoy_layout_dialog_import_share_code_title".equals(key)) {
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
            } else if ("string:common_vjoy_layout_dialog_import_share_code_placeholder".equals(key)) {
                label = "Opening file picker…";
            }
            // Suppress the "Operation failed, please try again." toast that
            // fires after interceptShare throws to abort the cloud publish.
            // 6.0.9 dropped the share-specific failure string and shows this
            // generic key instead; overriding to "" makes Android skip the
            // (now empty) toast. NOTE: this key is generic to layout ops, so
            // genuine non-share "operation failed" toasts are also swallowed
            // — acceptable trade for a clean local-export UX, but revisit if
            // it hides a real error elsewhere.
            else if ("string:common_vjoy_layout_toast_operation_failed".equals(key)) {
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
