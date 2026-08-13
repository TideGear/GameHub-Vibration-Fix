package com.xj.winemu.exportcontrols;

import android.util.Log;

import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

/**
 * Local-save side of the VJoy file-import pipeline. Ported to GameHub 6.1.1.
 *
 * Pipeline:
 *   1. Caller hands us the raw bytes of a {@code .gtheme} file (a ZIP
 *      archive containing a single {@code layout.json} entry — Xiaoji's
 *      own shareable format, produced by our export).
 *   2. We ZIP-unwrap to get the layout JSON bytes.
 *   3. Deserialize JSON to a host VJoyLayout ({@code Ltvr;} on 6.1.1) via
 *      the host's OWN configured kotlinx Json instance — the plain
 *      {@code Json.Default} cannot decode it (InputMapping / ControlAction
 *      are polymorphic and only the host's SerializersModule registers
 *      them; see {@code Lzzr;-><clinit>}).
 *   4. Use the JSON's own {@code id} as the layoutId (see below).
 *   5. Run the host's save coroutine block directly through
 *      {@code kotlinx.coroutines.BuildersKt.withContext(Dispatchers.IO, block, cont)}.
 *      The host's own facade is {@code Lc0s;->i(String, Ltvr;, ContinuationImpl)},
 *      but its declared 3rd-arg type is the ABSTRACT ContinuationImpl class,
 *      which Java reflection will not accept our Continuation Proxy for — so
 *      we reproduce its two-line body instead (see saveLayoutLocal).
 *   6. Block on a {@link CompletableFuture} that our synthetic
 *      Continuation completes from {@code resumeWith()}.
 *   7. Insert the {@code virtual_key_layout} row so My Layouts shows it,
 *      then nudge Room's invalidation tracker for a live refresh.
 *
 * ── Reflection anchors, GameHub 6.1.1 ─────────────────────────────────
 * 6.1.1 no longer obfuscates the Kotlin / kotlinx.coroutines /
 * kotlinx.serialization / androidx.room3 / androidx.sqlite runtimes, so
 * everything below is a real name EXCEPT the four app-owned R8 letters,
 * which must be re-derived on every base bump:
 *
 *   {@link #SAVE_BLOCK_CLASS} = "l8n" + {@link #SAVE_BLOCK_CASE} = 0x14
 *       The save coroutine. Since 6.1.1 R8 HORIZONTALLY MERGES ~29 unrelated
 *       suspend lambdas into one class discriminated by an int field `a`,
 *       so the class alone is not enough — the case number is part of the
 *       anchor. The cheapest way to re-derive BOTH on a base bump is to read
 *       the host's own call site, which is the `i()` method of the Json holder
 *       ({@link #VJOY_JSON_HOLDER}). On 6.1.2 that is
 *       smali_classes3/o0s.smali, byte-for-byte the 6.1.1 shape:
 *           sget-object v0, Lkj0;->a           ; = Dispatchers.getIO() cache
 *           new-instance v1, Ll8n;             ; <- SAVE_BLOCK_CLASS
 *           const/16 v3, 0x14                  ; <- SAVE_BLOCK_CASE
 *           invoke-direct {v1, p0, p1, v2, v3}, Ll8n;-><init>(
 *               Ljava/lang/Object;Ljava/lang/Object;
 *               Lkotlin/coroutines/Continuation;I)V
 *           BuildersKt->withContext(v0, v1, p2)
 *       Its second parameter is also the VJoyLayout type, so this one method
 *       independently confirms {@link #VJOY_LAYOUT_CLASS} too — a useful
 *       cross-check against the serializer route described below.
 *       The block's body is still the 1:1 port of 6.0.9's
 *       `Lqpm;->invokeSuspend`: same "assets"/"layout.json"/"preview.png"/
 *       "vjoy_layouts/" strings, same FNV-1a constants
 *       0x14650fb0739d0383 / 0x100000001b3, same 13-arg save-receipt ctor.
 *       (6.1.1 "f8n"; 6.0.9 "qpm"; 6.0.4 "m0n". The CASE has been 0x14
 *       throughout, so don't assume it moves with the letter.)
 *
 *   {@link #VJOY_LAYOUT_CLASS} = "ewr"
 *       The VJoyLayout data class. The 6.0.9 FQN
 *       com.xiaoji.egggame.common.ui.vjoy.model.VJoyLayout is gone — that
 *       whole package is obfuscated away from 6.1.1 on. PROOF: its generated
 *       serializer (6.1.1 smali_classes2/rvr.smali, 6.1.2
 *       smali_classes2/cwr.smali) still carries the descriptor serialName
 *       string "com.xiaoji.egggame.common.ui.vjoy.model.VJoyLayout" plus the
 *       element names formatVersion/id/name/description/meta/settings/
 *       controls/layers/activeLayerIndex/nextLayerIndex. Field order in the
 *       data class `<init>(I,String,…,Map,…,List,List,I,I)` matches 6.0.9's
 *       VJoyLayout ctor exactly, so a=formatVersion, b=id, c=name,
 *       d=description, …
 *
 *       Re-derive: grep for that serialName literal to get the SERIALIZER,
 *       then read the data class out of it. The serializer allocates exactly
 *       two types — itself and the data class — so
 *           grep -o 'new-instance [pv][0-9]*, L[a-z0-9]*;' <serializer>.smali
 *       yields {itself, VJoyLayout}; take the one that is not itself. Verified
 *       on both bases: rvr -> tvr (6.1.1), cwr -> ewr (6.1.2). The
 *       {@link #SAVE_BLOCK_CLASS} call site above confirms it independently.
 *       (6.1.1 "tvr")
 *
 *   {@link #VJOY_JSON_HOLDER} = "o0s" (static field "a") — the host's
 *       configured Json for layouts (6.0.9 VJoyLayoutJson.Default). Evidence:
 *       smali_classes3/o0s.smali `<clinit>` does `sput Ll0s;->b -> Lo0s;->a`,
 *       and BOTH the save block (l8n case 0x14) and the host's own pack-import
 *       (`Lo0s;->a(L…;)`) use `Lo0s;->a` for encodeToString /
 *       decodeFromString. Fallback anchor {@link #VJOY_JSON_HOLDER_ALT}
 *       "l0s" field "b" is the same object one hop upstream; `Ll0s;->a`
 *       is the polymorphic SerializersModule it is built from.
 *
 *       Re-derive by SHAPE — the pair is the only class in the tree with two
 *       `public static final <f>:Lkotlinx/serialization/json/Json;` fields that
 *       also touches `Lkotlinx/io/files/Path;`, and its `<clinit>` names the
 *       upstream holder for free:
 *           6.1.1  c0s.a <- zzr.b   c0s.b <- zzr.d
 *           6.1.2  o0s.a <- l0s.b   o0s.b <- l0s.d
 *       This class is worth finding first on any base bump: its `i()` method is
 *       also how {@link #SAVE_BLOCK_CLASS}, {@link #SAVE_BLOCK_CASE} and
 *       {@link #VJOY_LAYOUT_CLASS} get confirmed. (6.1.1 "c0s"/"zzr")
 *
 *   {@link #APP_DATABASE_CLS} — still a kept FQN on 6.1.1
 *       (smali_classes2/com/xiaoji/egggame/core/database/AppDatabase.smali),
 *       but its DAO getters are now single letters c()..o() and the
 *       generated DAO impls have NO methods at all (R8 moved every query
 *       into per-query merged suspend lambdas), so the 6.0.9 DAO-reflection
 *       nudge is dead. See nudgeRoomInvalidation for the replacement, which
 *       uses only kept androidx.room3 / androidx.sqlite names.
 *
 * All paths fail-soft: on any reflection / parse / save error we log and
 * return null/false. The caller (BhVjoyShareHook#interceptApply) toasts a
 * generic error.
 */
public final class BhVjoyImporter {

    private static final String TAG = "BhVjoyImporter";

    // === Coroutine bridge (GameHub 6.1.1 keeps the Kotlin runtime
    //     unobfuscated — real names. Shared with BhSteamUpdateChecker;
    //     keep the two in sync on a base bump.) ===
    private static final String WITH_CONTEXT_CLASS     = "kotlinx.coroutines.BuildersKt";
    private static final String WITH_CONTEXT_METHOD    = "withContext";
    private static final String COROUTINE_CONTEXT_IF   = "kotlin.coroutines.CoroutineContext";
    private static final String FUNCTION2_IF           = "kotlin.jvm.functions.Function2";
    // Continuation INTERFACE (getContext()+resumeWith) — what the Proxy must
    // implement. NOT kotlin.coroutines.jvm.internal.ContinuationImpl (an
    // abstract class; a Proxy can't implement it).
    private static final String CONTINUATION_INTERFACE = "kotlin.coroutines.Continuation";
    private static final String DISPATCHER_HOLDER      = "kotlinx.coroutines.Dispatchers";
    // Dispatchers.getIO() — a METHOD, not a field. On 6.1.1 the static field
    // `Dispatchers.a` is Default, so the old field read would silently
    // dispatch the file-IO save on the CPU pool. The host itself caches
    // getIO() in Lkj0;->a (verified: kj0.<clinit> calls Dispatchers.getIO()).
    private static final String DISPATCHER_IO_METHOD    = "getIO";

    // === App-owned R8 letters (re-derive every base bump; see class doc) ===
    /** Merged suspend-lambda class holding the VJoy save coroutine. */
    private static final String SAVE_BLOCK_CLASS = "l8n";   // 6.1.1 f8n
    /**
     * Which merged case inside SAVE_BLOCK_CLASS is the save coroutine.
     * Unchanged 6.1.1 -> 6.1.2 — do not assume it tracks the class letter.
     */
    private static final int    SAVE_BLOCK_CASE  = 0x14;
    /** The VJoyLayout data class (6.0.9: an FQN; 6.1.1+: obfuscated). */
    private static final String VJOY_LAYOUT_CLASS = "ewr";   // 6.1.1 tvr
    /** Holder of the host's layout Json; static field VJOY_JSON_FIELD. */
    private static final String VJOY_JSON_HOLDER  = "o0s";   // 6.1.1 c0s
    private static final String VJOY_JSON_FIELD   = "a";
    /** Same Json one hop upstream, used if the primary anchor moves. */
    private static final String VJOY_JSON_HOLDER_ALT = "l0s";  // 6.1.1 zzr
    private static final String VJOY_JSON_FIELD_ALT  = "b";

    // === Kept host / library FQNs ===
    private static final String APP_DATABASE_CLS =
        "com.xiaoji.egggame.core.database.AppDatabase";
    private static final String ROOM_DATABASE_CLS = "androidx.room3.RoomDatabase";
    private static final String ROOM_DBUTIL_CLS   = "androidx.room3.util.DBUtil";
    private static final String ROOM_TRACKER_CLS  = "androidx.room3.InvalidationTracker";
    private static final String SQLITE_CONN_CLS   = "androidx.sqlite.SQLiteConnection";
    private static final String SQLITE_KT_CLS     = "androidx.sqlite.SQLite";
    /**
     * Koin's Java interop entrypoint. NB: on 6.1.1 R8 stripped every
     * overload except {@code getOrNull$default(Class, Qualifier, Function0,
     * int mask, Object)} — the 6.0.9 {@code get(Class)} no longer exists
     * (verified: smali_classes3/org/koin/java/KoinJavaComponent.smali has
     * exactly one method). mask 0x2|0x4 = "use defaults for qualifier and
     * parameters".
     */
    private static final String KOIN_JAVA_COMPONENT = "org.koin.java.KoinJavaComponent";
    private static final String KOIN_GET_OR_NULL    = "getOrNull$default";
    private static final int    KOIN_DEFAULTS_MASK  = 0x2 | 0x4;

    /** Save coroutine can take a while (file IO + index update). */
    private static final long SAVE_TIMEOUT_SECONDS = 30L;

    private BhVjoyImporter() { }

    /** Returns true and toasts success on local save. */
    public static boolean importFromGthemeBytes(byte[] fileBytes) {
        try {
            // The exported `.gtheme` files contain a JSON layout payload
            // wrapped in a ZIP, BUT the ZIP's binary headers are corrupted
            // by Xiaoji's upload pipeline (`tencent-cos` CDN serves
            // already-mangled bytes — UTF-8 replacement chars where binary
            // bytes ≥ 0x80 should be). The good news: the JSON content is
            // valid UTF-8 so it survives intact inside the broken
            // container. Skip ZIP parsing and just byte-scan for the JSON
            // object. Works on clean `.json` files too.
            String json = extractJsonPayload(fileBytes);
            if (json == null) {
                Log.w(TAG, "could not extract JSON payload from file");
                return false;
            }
            Log.i(TAG, "extracted layout JSON, " + json.length() + " chars");
            // Diagnostic: log the first 240 chars and the last 80 chars so we
            // can see whether we picked the right brace-block. Layout JSON
            // starts with {"formatVersion":... or {"id":... — anything else
            // means we matched a sub-object inside a ZIP's metadata.
            int head = Math.min(240, json.length());
            int tail = Math.min(80, json.length());
            Log.i(TAG, "JSON head: " + json.substring(0, head));
            Log.i(TAG, "JSON tail: " + json.substring(json.length() - tail));

            Object layout = decodeLayout(json);
            if (layout == null) {
                Log.w(TAG, "could not deserialize VJoyLayout from JSON");
                return false;
            }
            Log.i(TAG, "decoded layout: " + layout.getClass().getName());

            // Use the JSON's own `id` field as the layoutId. The host's
            // visible-layouts list (My Layouts) appears to match folder
            // name against layout.json's "id" field — a fresh UUID
            // folder containing a layout with a different `id` doesn't
            // show up in the UI. Confirmed empirically 2026-05-24: 6
            // import-test folders with UUID names + foreign ids were
            // all invisible; the only on-disk layout with matching
            // folder+id (`a9813ab58d304ad98365ad77c94d0241_copy_2`)
            // was rendered.
            //
            // Side-effect: re-importing the same .gtheme twice
            // overwrites the previous import (same id → same folder).
            // Acceptable; matches the host's "copy" suffix convention
            // for duplicates.
            String layoutId = readLayoutId(json, layout);
            if (layoutId == null || layoutId.isEmpty()) {
                layoutId = UUID.randomUUID().toString();
                Log.w(TAG, "no id in layout JSON; falling back to UUID " + layoutId);
            } else {
                Log.i(TAG, "using layout id from JSON: " + layoutId);
            }
            Object receipt = saveLayoutLocal(layoutId, layout);
            if (receipt == null) {
                Log.w(TAG, "save returned null receipt");
                return false;
            }
            Log.i(TAG, "save success: " + receipt);

            // The save coroutine writes layout.json + assets/ to disk but
            // does NOT register the layout in egggame.db's virtual_key_layout
            // table — the host's Create flow registers separately, through
            // its own ViewModel's insert command. Without that row, the
            // layout is invisible in My Layouts.
            //
            // Direct DB insert mirrors what the Create flow writes:
            //   source=local, catalog=local, acquire=created, etc.
            // Column set verified against 6.1.1's CREATE TABLE in
            // smali_classes3/yi0.smali (see registerInDatabase).
            boolean registered = registerInDatabase(layoutId, json, layout, receipt);
            if (!registered) {
                Log.w(TAG, "DB insert failed (layout still on disk, " +
                    "may be picked up later by host's rebuild path)");
                // Still report success — disk write succeeded.
            }
            return true;
        } catch (Throwable t) {
            Log.w(TAG, "import failed", t);
            return false;
        }
    }

    /**
     * Deserialize the layout JSON into a host layout instance.
     *
     * MUST use the host's own configured Json ({@link #VJOY_JSON_HOLDER}),
     * not {@code Json.Default}: InputMapping and ControlAction are
     * polymorphic sealed hierarchies whose subclass registrations live in
     * the host's SerializersModule ({@code Lzzr;->a}), and a bare Json
     * throws SerializationException on the {@code "class"} discriminator.
     *
     * Falls back to {@link BhVjoyJson#decodeLayout(String)} (the 6.0.9-era
     * path, which resolves the layout by its old FQN) so this keeps working
     * if a future base restores the unobfuscated names.
     */
    private static Object decodeLayout(String json) {
        try {
            Class<?> layoutCls = Class.forName(VJOY_LAYOUT_CLASS);
            Field companionField = layoutCls.getDeclaredField("Companion");
            companionField.setAccessible(true);
            Object companion = companionField.get(null);
            if (companion == null) {
                throw new IllegalStateException(VJOY_LAYOUT_CLASS + ".Companion is null");
            }
            // Ltvr;->Companion is Lsvr;, whose single method serializer() kept
            // its real name (kotlinx keep-rules).
            Method serializerM = companion.getClass().getMethod("serializer");
            serializerM.setAccessible(true);
            Object serializer = serializerM.invoke(companion);

            Object hostJson = hostLayoutJson();
            if (hostJson == null) {
                throw new IllegalStateException("host layout Json not resolvable");
            }
            // kotlinx.serialization.json.Json#decodeFromString(
            //     DeserializationStrategy, String): Object — real names on 6.1.1.
            Class<?> jsonCls = Class.forName("kotlinx.serialization.json.Json");
            Class<?> deserCls = Class.forName("kotlinx.serialization.DeserializationStrategy");
            Method decode = jsonCls.getDeclaredMethod(
                "decodeFromString", deserCls, String.class);
            decode.setAccessible(true);
            Object layout = decode.invoke(hostJson, serializer, json);
            if (layout != null) return layout;
            Log.w(TAG, "decodeFromString returned null");
        } catch (Throwable t) {
            Log.w(TAG, "direct decode failed, trying BhVjoyJson fallback", t);
        }
        return BhVjoyJson.decodeLayout(json);
    }

    /**
     * The host's configured layout Json instance. Primary anchor is
     * {@code Lc0s;->a} (what the save block and the host's own pack-import
     * both use); {@code Lzzr;->b} is the identical object one hop upstream.
     */
    private static Object hostLayoutJson() {
        Object v = staticField(VJOY_JSON_HOLDER, VJOY_JSON_FIELD);
        if (v != null) return v;
        Log.w(TAG, VJOY_JSON_HOLDER + "." + VJOY_JSON_FIELD
            + " unavailable; trying " + VJOY_JSON_HOLDER_ALT);
        return staticField(VJOY_JSON_HOLDER_ALT, VJOY_JSON_FIELD_ALT);
    }

    /** Read a static field, or null if the class/field/value is missing. */
    private static Object staticField(String clsName, String fieldName) {
        try {
            Field f = Class.forName(clsName).getDeclaredField(fieldName);
            f.setAccessible(true);
            return f.get(null);
        } catch (Throwable t) {
            return null;
        }
    }

    /**
     * Insert a row into egggame.db's virtual_key_layout table so the
     * imported layout appears in My Layouts. Mirrors the columns the
     * host's Create flow writes (source=local, catalog=local,
     * acquire=created).
     *
     * The 32-column virtual_key_layout schema is BYTE-IDENTICAL between
     * 6.0.9 and 6.1.1 (diffed the CREATE TABLE in 6.0.9
     * smali_classes3/l90.smali against 6.1.1 smali_classes3/yi0.smali), so
     * the column set below is unchanged by the 6.1.1 port. Every NOT NULL
     * column is written; `id` is left to AUTOINCREMENT.
     *
     * The host has the DB open in WAL mode; opening a second read-write
     * connection via SQLiteDatabase.openDatabase() is fine — SQLite is
     * multi-connection safe, and Room's invalidation triggers are TEMP
     * (connection-local, see nudgeRoomInvalidation) so our connection
     * never trips a missing-temp-table error. The flip side is that Room
     * cannot see our write, hence the nudge.
     */
    private static boolean registerInDatabase(
            String layoutId, String layoutJson, Object layout, Object saveReceipt) {
        android.database.sqlite.SQLiteDatabase db = null;
        long insertedRowId = -1;
        String insertedUserId = null;
        try {
            // Resolve the on-disk DB path via the app's Context.
            android.content.Context ctx = currentApplicationContext();
            if (ctx == null) {
                Log.w(TAG, "no Application context for DB path");
                return false;
            }
            java.io.File dbFile = ctx.getDatabasePath("egggame.db");
            if (!dbFile.exists()) {
                Log.w(TAG, "egggame.db not found at " + dbFile.getAbsolutePath());
                return false;
            }

            // CRITICAL: the host opens egggame.db in WAL mode via Room.
            // Opening our second connection in default (journal) mode
            // and then writing corrupted the DB file (SQLITE_CORRUPT
            // code 11 on the host's next access — verified pre10f). Use
            // OpenParams with journalMode=WAL so our writes go to the
            // shared -wal file the host already has open instead of
            // forcing a journal-mode switch on the main DB file.
            android.database.sqlite.SQLiteDatabase.OpenParams params =
                new android.database.sqlite.SQLiteDatabase.OpenParams.Builder()
                    .setOpenFlags(android.database.sqlite.SQLiteDatabase.OPEN_READWRITE)
                    .setJournalMode("WAL")
                    .setSynchronousMode("NORMAL")
                    .build();
            db = android.database.sqlite.SQLiteDatabase.openDatabase(dbFile, params);

            // Detect the logged-in user's id from the host's own existing rows
            // (upstream hardcoded "99999" — a sentinel from their guest test
            // session — but My Layouts filters by the real account id, so a
            // hardcoded value renders the imported row invisible to a logged-in
            // user). Diagnostic-log the distribution + retroactively re-stamp
            // any orphaned rows we ourselves inserted with the sentinel.
            String userId = pickUserIdFor(db);
            logUserIdDistribution(db);
            Log.i(TAG, "user_id chosen for import: " + userId);
            repairOrphanedImports(db, userId);

            // Pull values for the row.
            long now = System.currentTimeMillis();
            String name = readLayoutName(layoutJson, layout);
            if (name == null || name.isEmpty()) name = "Imported Layout";
            String titleI18n = "{\"default\":" + jsonString(name) + "}";
            String configHash = readConfigHash(saveReceipt);

            android.content.ContentValues v = new android.content.ContentValues();
            v.put("user_id",              userId);
            v.put("folder_key",           layoutId);
            v.put("folder_path",          "vjoy_layouts/" + layoutId + "/");
            v.put("title_i18n_json",      titleI18n);
            // desc_i18n_json: nullable
            v.put("title_search",         name);
            v.put("layout_type",          "common");
            // game_id: nullable
            v.put("source",               "local");
            v.put("catalog",              "local");
            v.put("acquire",              "created");
            v.put("source_key",           "local:" + layoutId);
            // upstream_key, remote_id, share_code, author_name: nullable
            v.put("apply_count",          0);
            // recommend_rank: nullable
            v.put("publish_status",       "none");
            // publish_name: nullable
            v.put("last_upload_result",   "none");
            v.put("last_download_result", "none");
            // last_error, last_upload_at, last_download_at: nullable
            v.put("index_mtime",          now);
            if (configHash != null) v.put("index_hash", configHash);
            v.put("broken",               0);
            v.put("created_at",           now);
            v.put("updated_at",           now);
            // deleted_at: nullable

            long rowId = db.insertWithOnConflict(
                "virtual_key_layout",
                null,
                v,
                android.database.sqlite.SQLiteDatabase.CONFLICT_REPLACE);
            if (rowId == -1) {
                Log.w(TAG, "INSERT returned -1");
                return false;
            }
            Log.i(TAG, "registered layout in virtual_key_layout (row id=" +
                rowId + ", folder_key=" + layoutId + ")");
            insertedRowId = rowId;
            insertedUserId = userId;
        } catch (Throwable t) {
            Log.w(TAG, "registerInDatabase failed", t);
            return false;
        } finally {
            if (db != null) try { db.close(); } catch (Throwable ignored) { }
        }
        // Our raw write is committed and our second connection is closed.
        // Route a real write through the host's own Room (androidx.room3 on
        // 6.1.1) so its connection-scoped invalidation tracker fires and the
        // live My Layouts list refreshes immediately. 6.0.4's older Room
        // observed our external raw write; 6.0.7+ does not (the regression the
        // user hit) because the tracker's bookkeeping table is TEMP.
        nudgeRoomInvalidation(insertedUserId, insertedRowId);
        return true;
    }

    /**
     * Make the host's Room invalidate the virtual_key_layout table so the
     * live My Layouts list (a Room Flow on observeMyLayoutsRevision) re-emits
     * right after an import — instead of only when the screen is re-entered.
     *
     * WHY A NUDGE IS NEEDED AT ALL. Room's trigger-based tracker keeps its
     * bookkeeping in a TEMP table: {@code CREATE TEMP TABLE IF NOT EXISTS
     * room_table_modification_log} (verified in smali/m4r.smali). TEMP objects
     * are per-connection, so the row we insert on our OWN SQLiteDatabase
     * connection can never flip Room's `invalidated` flag — calling
     * refreshAsync() alone would find nothing. A real write on ROOM's
     * connection is required.
     *
     * 6.0.9 did that through the generated DAO (findById + upsert). That is
     * impossible on 6.1.1: the layout DAO impl {@code Ldet;} (returned by
     * {@code AppDatabase->n()}) declares ONLY a constructor — R8 moved every
     * query into per-query merged suspend lambdas such as
     * smali_classes2/cet.smali, whose ctor is (String, J, Ldet;, Continuation, I).
     * Both {@code VirtualKeyLayoutDao} and {@code VirtualKeyLayoutEntity} FQNs
     * are gone.
     *
     * Replacement uses only KEPT library names — no R8 letters:
     *   androidx.room3.util.DBUtil#performSuspending(
     *       RoomDatabase, boolean isReadOnly, boolean inTransaction,
     *       Function2&lt;SQLiteConnection, Continuation, Object&gt;, Continuation)
     * with isReadOnly=false and inTransaction=TRUE. Those two flags are not
     * cosmetic: in the block Room finally runs (smali_classes3/ms3.smali,
     * merged case a=1) the `inTransaction` field gates the whole path —
     * `if-eqz` on it jumps to a branch with NO invalidation — while
     * `isReadOnly` gates the trailing
     *     RoomDatabase->a()  (= getInvalidationTracker)
     *     InvalidationTracker->b()  (= refreshAsync, the coroutine named
     *                                 "Room Invalidation Tracker Refresh")
     * so only (false, true) both writes through Room's connection AND
     * refreshes. Our Function2 Proxy receives the real
     * androidx.sqlite.SQLiteConnection (chain verified through smali/ej5.smali
     * case a=0: `check-cast p1, Landroidx/sqlite/SQLiteConnection;`), and
     * androidx.sqlite.SQLite#execSQL(SQLiteConnection, String) is a plain
     * non-suspend call, so the block completes synchronously.
     *
     * The UPDATE has to touch a real row: SQLite fires an AFTER UPDATE
     * trigger per matched row, so a 0-row statement would invalidate nothing.
     *
     * Best-effort: any failure just logs and leaves the prior behavior
     * (layout still saved; visible after re-entering My Layouts).
     */
    private static void nudgeRoomInvalidation(String userId, long rowId) {
        if (userId == null || userId.isEmpty() || rowId <= 0) return;
        try {
            Object appDb = resolveAppDatabase();
            if (appDb == null) {
                Log.w(TAG, "nudge: AppDatabase not resolvable from Koin");
                return;
            }

            Class<?> roomDbCls   = Class.forName(ROOM_DATABASE_CLS);
            Class<?> dbUtilCls   = Class.forName(ROOM_DBUTIL_CLS);
            Class<?> connCls     = Class.forName(SQLITE_CONN_CLS);
            Class<?> sqliteKtCls = Class.forName(SQLITE_KT_CLS);
            Class<?> function2Cls = Class.forName(FUNCTION2_IF);
            Class<?> contCls     = Class.forName(CONTINUATION_INTERFACE);

            final Method execSQL = sqliteKtCls.getDeclaredMethod(
                "execSQL", connCls, String.class);
            execSQL.setAccessible(true);
            Method performSuspending = dbUtilCls.getDeclaredMethod(
                "performSuspending", roomDbCls, boolean.class, boolean.class,
                function2Cls, contCls);
            performSuspending.setAccessible(true);

            // Values are longs we produced ourselves — no injection surface.
            final String sql = "UPDATE virtual_key_layout SET updated_at = "
                + System.currentTimeMillis() + " WHERE id = " + rowId;
            final Object unit = staticField("kotlin.Unit", "INSTANCE");

            Object block = Proxy.newProxyInstance(
                function2Cls.getClassLoader(),
                new Class<?>[]{ function2Cls },
                (proxy, method, args) -> {
                    String mn = method.getName();
                    if ("invoke".equals(mn) && args != null && args.length == 2) {
                        execSQL.invoke(null, args[0], sql);
                        return unit;   // completed without suspending
                    }
                    if ("equals".equals(mn)) return proxy == args[0];
                    if ("hashCode".equals(mn)) return System.identityHashCode(proxy);
                    if ("toString".equals(mn)) return "BhVjoyImporterNudgeBlock";
                    return null;
                });

            // Dispatcher only supplies our Continuation Proxy's getContext();
            // performSuspending picks the connection pool's own context.
            Object dispatcher = ioDispatcher();
            CompletableFuture<Object> done = new CompletableFuture<>();
            Object continuation = makeContinuation(contCls, done, dispatcher);
            Object immediate = performSuspending.invoke(
                null, appDb, Boolean.FALSE, Boolean.TRUE, block, continuation);
            if (isCoroutineSuspended(immediate)) {
                done.get(SAVE_TIMEOUT_SECONDS, TimeUnit.SECONDS);
            }
            Log.i(TAG, "nudge: re-touched row " + rowId
                + " through Room (invalidation fired)");
        } catch (Throwable t) {
            Log.w(TAG, "nudgeRoomInvalidation failed (live refresh skipped)", t);
        }
    }

    /**
     * Resolve the host's singleton AppDatabase from Koin. 6.1.1 only keeps
     * {@code getOrNull$default}; the 6.0.9 {@code get(Class)} is gone.
     */
    private static Object resolveAppDatabase() {
        try {
            Class<?> appDbCls = Class.forName(APP_DATABASE_CLS);
            Class<?> koinCls = Class.forName(KOIN_JAVA_COMPONENT);
            Class<?> qualifierCls = Class.forName("org.koin.core.qualifier.Qualifier");
            Class<?> function0Cls = Class.forName("kotlin.jvm.functions.Function0");
            Method getOrNull = koinCls.getDeclaredMethod(KOIN_GET_OR_NULL,
                Class.class, qualifierCls, function0Cls, int.class, Object.class);
            getOrNull.setAccessible(true);
            return getOrNull.invoke(
                null, appDbCls, null, null, KOIN_DEFAULTS_MASK, null);
        } catch (Throwable t) {
            Log.w(TAG, "resolveAppDatabase failed", t);
            return null;
        }
    }

    /** kotlinx.coroutines.Dispatchers.getIO(). */
    private static Object ioDispatcher() throws Exception {
        Method getIO = Class.forName(DISPATCHER_HOLDER)
            .getDeclaredMethod(DISPATCHER_IO_METHOD);
        getIO.setAccessible(true);
        Object dispatcher = getIO.invoke(null);
        if (dispatcher == null) {
            throw new IllegalStateException(
                DISPATCHER_HOLDER + "." + DISPATCHER_IO_METHOD + "() returned null");
        }
        return dispatcher;
    }

    /**
     * Pick a user_id for our INSERT. Strategy: take the most recent
     * non-deleted row's user_id — that's the value the host's own Create
     * flow uses, which is also what My Layouts filters by.
     *
     * MUST exclude "99999" — that's upstream's sentinel which earlier
     * builds wrote and which our own orphan rows still carry; without
     * the exclusion the picker prefers our own (newest) bad rows over
     * legitimate older host rows. Falls back to "99999" only if there's
     * no non-sentinel row to copy from.
     */
    private static String pickUserIdFor(android.database.sqlite.SQLiteDatabase db) {
        try (android.database.Cursor c = db.rawQuery(
                "SELECT user_id FROM virtual_key_layout "
                + "WHERE deleted_at IS NULL "
                + "AND user_id IS NOT NULL AND user_id != '' "
                + "AND user_id != '99999' "
                + "ORDER BY created_at DESC LIMIT 1", null)) {
            if (c != null && c.moveToFirst()) {
                String id = c.getString(0);
                if (id != null && !id.isEmpty()) return id;
            }
        } catch (Throwable t) {
            Log.w(TAG, "pickUserIdFor failed", t);
        }
        Log.w(TAG, "pickUserIdFor: no non-sentinel user_id found; "
            + "falling back to '99999' (imported layout will be invisible "
            + "until the host creates a layout first)");
        return "99999";
    }

    /** Diagnostic: log (user_id, count) groupings for virtual_key_layout. */
    private static void logUserIdDistribution(android.database.sqlite.SQLiteDatabase db) {
        try (android.database.Cursor c = db.rawQuery(
                "SELECT user_id, COUNT(*) FROM virtual_key_layout "
                + "GROUP BY user_id ORDER BY COUNT(*) DESC", null)) {
            StringBuilder sb = new StringBuilder("user_id distribution:");
            if (c != null) {
                while (c.moveToNext()) {
                    sb.append(' ').append(c.getString(0)).append('=')
                      .append(c.getLong(1));
                }
            }
            Log.i(TAG, sb.toString());
        } catch (Throwable t) {
            Log.w(TAG, "logUserIdDistribution failed", t);
        }
    }

    /**
     * Re-stamp any rows we previously inserted under the "99999" sentinel
     * (orphaned because they don't match the logged-in user's id). Only
     * touches rows that match our own source_key marker ("local:%") so we
     * never overwrite a host-created row whose user_id legitimately is 99999.
     */
    private static void repairOrphanedImports(
            android.database.sqlite.SQLiteDatabase db, String correctUserId) {
        if (correctUserId == null || "99999".equals(correctUserId)) return;
        try {
            android.content.ContentValues v = new android.content.ContentValues();
            v.put("user_id", correctUserId);
            int n = db.update(
                "virtual_key_layout",
                v,
                "user_id = ? AND source_key LIKE 'local:%'",
                new String[]{ "99999" });
            if (n > 0) {
                Log.i(TAG, "repaired " + n + " orphaned import row(s): "
                    + "user_id 99999 -> " + correctUserId);
            }
        } catch (Throwable t) {
            Log.w(TAG, "repairOrphanedImports failed", t);
        }
    }

    /**
     * The layout's display name.
     *
     * Primary source is the JSON itself ({@code name.locales.default}) — the
     * serial names are stable across bases (the 6.1.1 descriptor in
     * smali_classes2/rvr.smali still spells formatVersion/id/name/... and the
     * LocalizedString descriptor still spells "locales"), and org.json is
     * platform code so nothing here can be obfuscated away.
     *
     * The reflection fallback covers both shapes: 6.0.9's
     * {@code getName().getLocales()} and 6.1.1's {@code Ltvr;->c()} returning
     * an {@code Lr2g;} whose single java.util.Map field is the locales map
     * (found by type, so no letter is hard-coded).
     */
    private static String readLayoutName(String layoutJson, Object layout) {
        try {
            org.json.JSONObject root = new org.json.JSONObject(layoutJson);
            org.json.JSONObject name = root.optJSONObject("name");
            if (name != null) {
                org.json.JSONObject locales = name.optJSONObject("locales");
                if (locales != null) {
                    String v = locales.optString("default", null);
                    if (v != null && !v.isEmpty()) return v;
                    java.util.Iterator<String> it = locales.keys();
                    while (it.hasNext()) {
                        String k = it.next();
                        String any = locales.optString(k, null);
                        if (any != null && !any.isEmpty()) return any;
                    }
                }
            }
        } catch (Throwable t) {
            Log.i(TAG, "readLayoutName: JSON path failed (" + t + "), reflecting");
        }
        if (layout == null) return null;
        try {
            Object localized = invokeNoArg(layout, "getName");   // 6.0.9
            if (localized == null) localized = invokeNoArg(layout, "c"); // 6.1.1 Ltvr;->c()
            if (localized == null) return null;
            Object locales = invokeNoArg(localized, "getLocales");
            if (!(locales instanceof java.util.Map)) locales = soleMapField(localized);
            if (!(locales instanceof java.util.Map)) return null;
            Object v = ((java.util.Map<?, ?>) locales).get("default");
            return v == null ? null : v.toString();
        } catch (Throwable t) {
            Log.w(TAG, "readLayoutName failed", t);
            return null;
        }
    }

    /** Invoke a declared no-arg method, or null if absent/failing. */
    private static Object invokeNoArg(Object target, String name) {
        try {
            Method m = target.getClass().getDeclaredMethod(name);
            if (m.getParameterTypes().length != 0) return null;
            m.setAccessible(true);
            return m.invoke(target);
        } catch (Throwable t) {
            return null;
        }
    }

    /** Value of the object's only java.util.Map instance field, else null. */
    private static Object soleMapField(Object target) {
        try {
            Field found = null;
            for (Field f : target.getClass().getDeclaredFields()) {
                if (java.lang.reflect.Modifier.isStatic(f.getModifiers())) continue;
                if (!java.util.Map.class.isAssignableFrom(f.getType())) continue;
                if (found != null) return null;   // ambiguous — refuse to guess
                found = f;
            }
            if (found == null) return null;
            found.setAccessible(true);
            return found.get(target);
        } catch (Throwable t) {
            return null;
        }
    }

    /**
     * The save receipt's configHash (FNV-1a of layout.json, written to
     * index_hash so the host's rebuild sees the layout as unchanged).
     *
     * 6.0.9's VJoyLayoutSaveReceipt had getConfigHash(); 6.1.1's {@code Lk0s;}
     * has no getters at all (only equals/hashCode/toString), so read the
     * backing field. Field order == ctor order — verified in
     * smali_classes2/k0s.smali's `<init>(String x9, Z, J, String, J)` body
     * (`iput-object p13 -> l`), and the 13-arg order itself is unchanged from
     * 6.0.9 (layoutId, folderAbs, folderRel, configAbs, configRel, assetsAbs,
     * assetsRel, previewAbs, previewRel, hasPreview, configSizeBytes,
     * configHash, savedAt), so configHash is the 12th arg = field `l`.
     *
     * Nullable column: if this can't be read we just omit index_hash.
     */
    private static String readConfigHash(Object receipt) {
        if (receipt == null) return null;
        Object v = invokeNoArg(receipt, "getConfigHash");     // 6.0.9
        if (v == null) {                                     // 6.1.1 Lk0s;->l
            try {
                Field f = receipt.getClass().getDeclaredField("l");
                if (f.getType() == String.class) {
                    f.setAccessible(true);
                    v = f.get(receipt);
                }
            } catch (Throwable ignored) { }
        }
        if (v == null) Log.i(TAG, "configHash unavailable; index_hash omitted");
        return v == null ? null : v.toString();
    }

    /** Minimal JSON string-encoder for the name field. */
    private static String jsonString(String s) {
        StringBuilder sb = new StringBuilder("\"");
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '\\': sb.append("\\\\"); break;
                case '"':  sb.append("\\\""); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
                    else sb.append(c);
            }
        }
        return sb.append('"').toString();
    }

    /** Get the current Application Context via ActivityThread reflection. */
    private static android.content.Context currentApplicationContext() {
        try {
            Class<?> at = Class.forName("android.app.ActivityThread");
            Method m = at.getMethod("currentApplication");
            Object app = m.invoke(null);
            return (android.content.Context) app;
        } catch (Throwable t) {
            return null;
        }
    }

    /**
     * Extract the layout JSON from a .gtheme byte buffer.
     *
     * Try in order:
     *   1. Proper ZIP parse: a fresh .gtheme is a valid ZIP (the host
     *      writes layout.json DEFLATE-compressed). java.util.zip handles
     *      both STORED and DEFLATED entries — this is the fast path for
     *      anything exported from a recent build.
     *   2. Byte-scan fallback for the first {...} block: older / hand-
     *      built / corrupted files (e.g. a pre-pre18 export with CDN-
     *      mangled headers but STORED JSON) won't ZIP-parse cleanly.
     *      Brace-matching the raw bytes recovers JSON IF it happens to
     *      be uncompressed. Useless on DEFLATE bytes — but we only get
     *      here when the ZIP path failed.
     */
    private static String extractJsonPayload(byte[] bytes) {
        if (bytes == null) return null;
        String fromZip = tryExtractFromZip(bytes);
        if (fromZip != null) return fromZip;
        return tryExtractByBraceMatch(bytes);
    }

    private static String tryExtractFromZip(byte[] bytes) {
        try (java.io.ByteArrayInputStream bin = new java.io.ByteArrayInputStream(bytes);
             java.util.zip.ZipInputStream zip = new java.util.zip.ZipInputStream(bin)) {
            java.util.zip.ZipEntry e;
            byte[] best = null;
            while ((e = zip.getNextEntry()) != null) {
                if (e.isDirectory()) { zip.closeEntry(); continue; }
                String name = e.getName();
                java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
                byte[] buf = new byte[8192];
                int n;
                while ((n = zip.read(buf)) > 0) out.write(buf, 0, n);
                zip.closeEntry();
                byte[] data = out.toByteArray();
                // Prefer layout.json by name; fall back to any entry that
                // looks like JSON (starts with '{').
                if ("layout.json".equals(name) ||
                    name.toLowerCase().endsWith("layout.json")) {
                    return new String(data, "UTF-8");
                }
                if (best == null && data.length > 0 && data[0] == '{') {
                    best = data;
                }
            }
            if (best != null) return new String(best, "UTF-8");
        } catch (Throwable t) {
            Log.i(TAG, "tryExtractFromZip: not a valid ZIP, will brace-scan: " + t.getMessage());
        }
        return null;
    }

    private static String tryExtractByBraceMatch(byte[] bytes) {
        int start = -1;
        for (int i = 0; i < bytes.length; i++) {
            if (bytes[i] == '{') { start = i; break; }
        }
        if (start < 0) return null;

        int depth = 0;
        boolean inString = false;
        boolean escape = false;
        int end = -1;
        for (int i = start; i < bytes.length; i++) {
            byte b = bytes[i];
            if (escape) { escape = false; continue; }
            if (inString) {
                if (b == '\\') escape = true;
                else if (b == '"') inString = false;
            } else {
                if (b == '"') inString = true;
                else if (b == '{') depth++;
                else if (b == '}') {
                    depth--;
                    if (depth == 0) { end = i; break; }
                }
            }
        }
        if (end < 0) return null;
        try {
            return new String(bytes, start, end - start + 1, "UTF-8");
        } catch (Throwable t) {
            Log.w(TAG, "brace-match UTF-8 decode failed", t);
            return null;
        }
    }

    /**
     * The layout's `id`. Read from the JSON first (platform org.json, nothing
     * to obfuscate); reflection fallback handles 6.0.9's {@code getId()} and
     * 6.1.1's {@code Ltvr;->b()} / field {@code b} (proven to be `id` by the
     * serializer descriptor element order in smali_classes2/rvr.smali and the
     * ctor field order in smali_classes2/tvr.smali).
     */
    private static String readLayoutId(String layoutJson, Object vJoyLayout) {
        try {
            String id = new org.json.JSONObject(layoutJson).optString("id", null);
            if (id != null && !id.isEmpty()) return id;
        } catch (Throwable t) {
            Log.i(TAG, "readLayoutId: JSON path failed (" + t + "), reflecting");
        }
        if (vJoyLayout == null) return null;
        try {
            Object id = invokeNoArg(vJoyLayout, "getId");    // 6.0.9
            if (id == null) id = invokeNoArg(vJoyLayout, "b"); // 6.1.1 Ltvr;->b()
            if (id == null) {
                Field f = vJoyLayout.getClass().getDeclaredField("b");
                if (f.getType() == String.class) {
                    f.setAccessible(true);
                    id = f.get(vJoyLayout);
                }
            }
            return id == null ? null : id.toString();
        } catch (Throwable t) {
            Log.w(TAG, "readLayoutId failed", t);
            return null;
        }
    }

    /**
     * Run the host's save coroutine via
     * {@code kotlinx.coroutines.BuildersKt.withContext(CoroutineContext,
     * Function2, Continuation)}.
     *
     * We reproduce the host facade {@code Lc0s;->i} rather than calling it,
     * because its declared third-arg type is the ABSTRACT
     * {@code kotlin.coroutines.jvm.internal.ContinuationImpl} — Java
     * reflection will not accept our Continuation Proxy as that type even
     * though it is fine at JVM bytecode level. {@code BuildersKt.withContext}
     * declares the {@code kotlin.coroutines.Continuation} INTERFACE, which our
     * Proxy satisfies.
     *
     * Call shape, verbatim from smali_classes3/c0s.smali `i()`:
     *   sget-object v0, Lkj0;->a          ; = Dispatchers.getIO()
     *   new-instance v1, Lf8n;
     *   const/4 v2, 0x0                  ; completion = null
     *   const/16 v3, 0x14                ; merged-case discriminator
     *   invoke-direct {v1, layoutId, layout, v2, v3},
     *       Lf8n;-><init>(Ljava/lang/Object;Ljava/lang/Object;Lkotlin/coroutines/Continuation;I)V
     *   invoke-static {v0, v1, cont}, BuildersKt->withContext(...)
     *
     * Note the ctor takes the layout as a bare Object, so the (obfuscated)
     * layout class is NOT needed here — only for decoding.
     */
    private static Object saveLayoutLocal(String layoutId, Object vJoyLayout)
            throws Exception {
        Class<?> continuationCls = Class.forName(CONTINUATION_INTERFACE);
        Class<?> coroutineCtxCls = Class.forName(COROUTINE_CONTEXT_IF);
        Class<?> function2Cls = Class.forName(FUNCTION2_IF);
        Class<?> withContextCls = Class.forName(WITH_CONTEXT_CLASS);
        Class<?> saveBlockCls = Class.forName(SAVE_BLOCK_CLASS);              // f8n (6.0.9 qpm)

        // 1. Dispatchers.getIO() — the save is file IO. Must be the METHOD:
        //    on 6.1.1 the static field Dispatchers.a is Default.
        Object dispatcher = ioDispatcher();

        // 2. new Lf8n;(layoutId, layout, null, 0x14) — field b = layoutId,
        //    field c = layout, matching invokeSuspend's :pswitch_8 block.
        Constructor<?> blockCtor = saveBlockCls.getDeclaredConstructor(
            Object.class, Object.class, continuationCls, int.class);
        blockCtor.setAccessible(true);
        Object saveBlock = blockCtor.newInstance(
            layoutId, vJoyLayout, null, SAVE_BLOCK_CASE);

        // 3. Build a Continuation proxy and bridge to a CompletableFuture.
        CompletableFuture<Object> done = new CompletableFuture<>();
        Object continuation = makeContinuation(continuationCls, done, dispatcher);

        // 4. BuildersKt.withContext(CoroutineContext, Function2, Continuation).
        Method withContext = withContextCls.getDeclaredMethod(
            WITH_CONTEXT_METHOD, coroutineCtxCls, function2Cls, continuationCls);
        withContext.setAccessible(true);

        Object immediate = withContext.invoke(null, dispatcher, saveBlock, continuation);

        if (isCoroutineSuspended(immediate)) {
            Log.i(TAG, "save suspended; awaiting Continuation.resumeWith");
            return done.get(SAVE_TIMEOUT_SECONDS, TimeUnit.SECONDS);
        } else {
            Log.i(TAG, "save completed synchronously");
            return immediate;
        }
    }

    /**
     * Build a Proxy that implements kotlin.coroutines.Continuation (a real,
     * unobfuscated name on 6.1.1). Two methods:
     *   - getContext() -> CoroutineContext
     *   - resumeWith(Object) -> Unit
     *
     * For getContext() we return the IO dispatcher itself — every
     * Dispatcher implements CoroutineContext via CoroutineContext.Element,
     * so returning a Dispatcher singleton satisfies the return type and
     * gives the coroutine machinery a usable context.
     *
     * resumeWith(Object) receives kotlin.Result-wrapped value. The raw
     * Object IS the Result (Result is an inline class erased to its
     * wrapped value), so we just hand it to the CompletableFuture.
     */
    private static Object makeContinuation(
            Class<?> continuationCls,
            CompletableFuture<Object> done,
            Object dispatcher
    ) throws Exception {
        final Object contextHolder = dispatcher; // captured by lambda below

        return Proxy.newProxyInstance(
            continuationCls.getClassLoader(),
            new Class<?>[]{ continuationCls },
            (proxy, method, args) -> {
                String name = method.getName();
                if ("getContext".equals(name)) {
                    // Return the dispatcher as the CoroutineContext.
                    return contextHolder;
                }
                if ("resumeWith".equals(name)) {
                    Object result = args != null && args.length > 0 ? args[0] : null;
                    Log.i(TAG, "Continuation.resumeWith fired, result=" +
                        (result == null ? "null" : result.getClass().getName()));
                    done.complete(result);
                    return null; // Unit / void
                }
                if ("equals".equals(name)) return proxy == args[0];
                if ("hashCode".equals(name)) return System.identityHashCode(proxy);
                if ("toString".equals(name)) return "BhVjoyImporterContinuation";
                return null;
            }
        );
    }

    /**
     * Detect Kotlin's COROUTINE_SUSPENDED sentinel. Kotlin defines this
     * as {@code kotlin.coroutines.intrinsics.IntrinsicsKt.getCOROUTINE_SUSPENDED}
     * — a singleton enum constant of CoroutineSingletons. We sniff it by
     * simple name / toString ("COROUTINE_SUSPENDED") rather than calling the
     * intrinsic, so this stays correct whether or not the base obfuscates
     * kotlin.* (6.1.1 does not).
     *
     * This is brittle but cheap. If the sentinel detection fails the
     * symptom is just a save timeout (we'll log and toast).
     */
    private static boolean isCoroutineSuspended(Object o) {
        if (o == null) return false;
        try {
            String name = o.getClass().getSimpleName();
            return "CoroutineSingletons".equals(name) ||
                   "COROUTINE_SUSPENDED".equals(o.toString());
        } catch (Throwable t) {
            return false;
        }
    }
}
