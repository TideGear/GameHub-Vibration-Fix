# GameScrub

The idea of GameScrub is to fix controller vibration, remove privacy
issues, and still allow login to GameHub for the actually useful
features like recommended per-game settings, default driver list,
and library sync (so install/uninstall and library
state across devices keep working).

It is built on GameHub v6.x and heavily uses the work of
[@The412Banner](https://github.com/The412Banner) as well as others. It
also includes my own PC-accurate controller vibration fixes.

> ### ⚠️ 6.2.1 — base APK ported and building; the plugin half needs a schema-5 plugin
>
> GameHub **6.1.1 moved the PC/Wine engine out of the APK** into a
> separately-downloaded plugin, which splits the vibration work in two. GameScrub
> patches the plugin half **without touching the plugin**, by prepending its own
> small "shadow" dex to the plugin's `DexClassLoader` path — see
> [PC engine plugin](#pc-engine-plugin-611).
>
> **The plugin contract has bumped on every single base**: `schemaVersion` 2
> (6.1.1), 3 (6.1.2), 4 (6.2.0), **5 (6.2.1, versionCode 138)**. The host rejects
> anything else outright — *"PC engine plugin schema N is not supported"* — and
> downloads a replacement. The schema-5 plugin has not been pulled yet, so this
> build still ships the plugin-103 shadow, which the version gate correctly
> refuses.
>
> | | 6.2.1 |
> |---|---|
> | Privacy (base APK): Firebase, GMS Measurement, Mob Push, OEM push fleet, `/events`, heartbeat/playtime, OTA | **working** |
> | Sustained rumble past 1 s | **working** — `winebus.so` stayed in the Wine component tree |
> | Layout export + import | **working** |
> | "Online Update" badges | **working** |
> | Dual-motor low/high dispatch | **off** until the schema-5 plugin is pulled |
> | Plugin-side privacy: heartbeat (Steam ID64 every 30 s) + device-perf telemetry | **off — those trackers are live again** |
>
> That last row is the one to care about: the dual-motor hooks and the
> plugin-side privacy stubs ride the **same** shadow dex, so a refused shadow
> disables both. The refusal is deliberate and loud (Toast + settings banner +
> logcat) — see
> [What happens when the plugin updates](#what-happens-when-the-plugin-updates).
>
> **To finish:** pull
> `<filesDir>/plugins/com.xiaoji.egggame.plugin.pcengine/base.apk` from a device
> running stock 6.2.1, upload it as a `pcengine-plugin-<versionCode>` release
> asset, re-run `apply_plugin_rumble_patches.py` +
> `apply_plugin_privacy_patches.py` + `build_plugin_shadow_dex.py` against it, and
> raise `EXPECTED_PLUGIN_VERSION_CODE` in
> [BhPluginShadow](extension/BhPluginShadow.java).

What you get over stock GameHub:

- **Privacy patches** — Login-friendly port of bannerhub-revanced's privacy
  patch set. Kills Firebase Analytics, Google Play Services Measurement, Mob
  Push SDK, the XiaoJi heartbeat / playtime tracker, the vgabc.com /events
  endpoint, and the JieLi OTA phone-home. Steam / GOG / Epic / Wine /
  account login are untouched. Full channel list in
  [scripts/apply_privacy_patches.py](scripts/apply_privacy_patches.py).
  (6.1.1 shrank part of this surface upstream: `heartbeat/game/update` and
  `heartbeat/game/end` no longer exist.) It also kills a channel that only
  appears in 6.1.1 — see below.
- **Device-performance telemetry kill.** The perf channel 6.0.9 stubbed
  in the base APK did not go away; it moved into the downloaded PC-engine plugin
  and got a successor. On stock 6.1.1, every game session assigns a UUID and
  samples **fps, power draw, RAM (MB/percent/total) and GPU percent every ~10 s**,
  then uploads a summary carrying `event_type=device_perf_session_summary`,
  `user_id`, `gameId` and `sourceGameId` when the game closes (verified from
  device logs, which also show `summaryOnly=true legacyUpload=false` — i.e. the
  old endpoint is off and this replaced it). GameScrub stubs the uploader
  through the same shadow dex used for dual-motor, so the plugin APK is never
  modified. The uploader's class letter drifts every plugin build
  (101 `Lxjp/mv1;` → 102 `Lxjp/qv1;` → 103 `Lxjp/b12;`), so the script locates it by its
  upload/retry method pair rather than by name. Sampling and local summary storage still run;
  nothing is sent.
- **Dual-motor low/high dispatch.**
  Wine games calling `XInputSetState(slot, low, high)` get the two motors
  driven independently via Android `CombinedVibration.startParallel` on
  ≥ 2-motor controllers. Stock GameHub blends both motors into a single haptic
  pulse; this preserves the heavy / light distinction the way the game intended.
- **Sustained rumble holds past 1 s.** SDL2's internal 1 s
  `rumble_expiration` auto-stops sustained rumble on stock. The APK's
  launch-time Java hook patches every app-owned `winebus.so` on disk so
  the two non-zero `SDL_JoystickRumble` call sites pass `0xffffffff` as
  the SDL duration; zero-duration stop calls still stop immediately. No
  `LD_PRELOAD`, no extra `.so` mapped into the Wine subprocess address
  space. (On 6.1.1 the trigger is
  `PcEnginePluginHostActivity.onCreate` instead of the old EnvBuilder ctor;
  the patcher and its target tree are unchanged.) The offline helper
  [scripts/patch_winebus_rumble_duration.py](scripts/patch_winebus_rumble_duration.py)
  applies the same patch to extracted components for offline use.
- **Instant release** when the game stops rumble — no phantom-suppression
  timer extending the motor past the actual stop call.
- **Local export/import of on-screen control layouts.** The on-screen-controls
  "Share" / "Apply share code" flow is rerouted from XiaoJi's cloud to portable
  local `.gtheme` files via the Storage Access Framework — no cloud account, no
  HTTP. Export captures the pristine pre-CDN layout bytes (full UTF-8 fidelity);
  import skips the share-code dialog, fires a file picker, and registers the
  layout straight into `egggame.db` so it shows up in My Layouts. Works from
  inside a running game (a separate process — `:pcengine` on 6.1.1, `:wine`
  before it) too.
- **Reliable "Online Update" badges.** Stock GameHub only runs the Steam
  update check on a couple of lazy paths (the launch resolver and a few
  detail/refresh flows) and the badges just read the last cached result, so
  the red dot lags reality — you can launch a game several times before an
  available update shows up. GameScrub adds a background worker
  ([BhSteamUpdateChecker](extension/BhSteamUpdateChecker.java)) that
  periodically, and whenever the app returns to the foreground, re-runs the
  host's own per-game update check for every installed Steam game and
  refreshes the badge flow. The check is a network query to Steam's Content
  Manager via GameHub's embedded native Steam client — it does **not** need
  the game (Wine) running, only that the app is open and the Steam session
  is connected. Live home/library badges refresh instantly; the game-detail
  dot is correct on the next menu open. No restart.

### PC engine plugin (6.1.1+)

GameHub 6.1.1 (versionCode 123) extracted the whole
PC/Wine engine from the base APK into a plugin that the app downloads at
runtime. Verified against the decompiled APK:

- `libwinemu.so`, `libxserver.so`, `libvfs.so` and `libgpuinfo.so` are **gone**
  from `lib/arm64-v8a` (which is now arm64-only).
- The `com.winemu.*` engine implementation is gone.
  `com.winemu.core.gamepad.GamepadServerManager` survives as a gutted shell:
  `onRumble(III)V` is `.locals 0 / return-void` and the `native*` rumble /
  gamepad-buffer methods are deleted. There is no `Vibrator` or
  `CombinedVibration` reference anywhere in the dex.
- The `features.winemu` Compose resource bundle is gone, and `WineActivity` is
  now an `activity-alias` onto
  `com.xiaoji.egggame.plugin.pcengine.host.LegacyPcEngineActivityTrampoline`.
- The plugin is fetched from `game/mobile/v1/plugin/latest`, installed as
  `<filesDir>/plugins/<pluginId>/base.apk` (native libs extracted to
  `.../lib/arm64-v8a/`), and loaded through the ComboLite framework
  (`com.combo.core.runtime.loader.PluginClassLoader`, a `DexClassLoader` whose
  parent is the host classloader).

**Integrity model.** ComboLite's own `ValidationStrategy` is set to `Insecure`
by the host (`pyi.j()` → `PluginManager.setValidationStrategy(Insecure)`), so
the framework does **not** verify the plugin's APK signature — a `Strict` mode
exists but is off. XiaoJi's only gate is trust-on-first-use: `pyi.w(File)`
SHA-256-hashes the installed `base.apk`, `pyi.J0()` writes a **plaintext**
record to `<filesDir>/pc_engine_active_plugin_identity`
(`format`/`pluginId`/`versionCode`/`sha256`/`schemaVersion`/`abi`/`installedPath`),
and `pyi.A()` re-hashes on load and compares, throwing *"does not match its
committed record"* on mismatch. There is no server signature and no embedded
key, so a patched plugin can be re-validated by recomputing that record.

**On-device layout.** Confirmed from the app's own logs under
`/sdcard/Android/data/com.xiaoji.egggame/files/logs/` (paths verified on both
6.1.1/plugin 101, 6.1.2/plugin 102 and 6.2.0/plugin 103):

```
files/plugins/com.xiaoji.egggame.plugin.pcengine/base.apk        the plugin (v103, ~24.1 MB)
files/plugins/com.xiaoji.egggame.plugin.pcengine/lib/arm64-v8a   its extracted native libs
files/usr/opt/wine_proton11.0-arm64x/…                           the Wine tree — winebus.so
files/usr/home/components/{Fex_*, dxvk-*, turnip_*, vkd3d-proton-*, …}
```

The `pluginId` is the literal string `com.xiaoji.egggame.plugin.pcengine`, so the
install path is deterministic rather than a UUID.

**The two halves land very differently.**

- *Sustained rumble (winebus)* — **done, no plugin access needed.** The critical
  fact is in the layout above: `winebus.so` lives in the **Wine component tree**,
  not in the plugin, so it was never behind the plugin's integrity check at all.
  The only thing that moved was the trigger. `apply_vibration_patches.py` now
  injects `BhVibrationController.ensureWinebusDurationPatchOnce(this)` into
  `PcEnginePluginHostActivity.onCreate` — base-APK host code that runs in the
  `:pcengine` process on every launch, before Wine starts. The Java patcher walks
  `getFilesDir()` recursively and so finds the Wine tree unchanged. That site is
  the main thread, unlike 6.0.9's env-builder site, so the walk is handed to a
  worker; see [Preload-free architecture](#preload-free-architecture) below.
- *Dual-motor dispatch* — **done, via classloader shadowing.** Its hooks target
  `GamepadServerManager` and the Physical vibrator class, which are inside the
  plugin dex — versioned independently of the base APK and hashed into the
  identity record above. The way in is that ComboLite's `PluginClassLoader`
  extends `DexClassLoader`, whose `dexPath` is a `:`-separated **list searched in
  order**. A single base-APK injection at the head of its constructor
  ([apply_plugin_shadow_patches.py](scripts/apply_plugin_shadow_patches.py))
  routes that argument through
  [BhPluginShadow.dexPath()](extension/BhPluginShadow.java), which returns
  `<our shadow dex>:<the untouched plugin base.apk>`. Our copies of those classes
  win on lookup, the plugin's `base.apk` is never written, and its SHA-256 record
  keeps verifying. The shadow holds **only** the patched classes (~17 KB —
  everything they reference resolves from the plugin APK that follows us on the
  path), because shadowing more than necessary is how you get subtle identity and
  `instanceof` mismatches.

Patched plugin code *can* call back into the GameScrub extension classes:
`PluginClassLoader.loadClass` falls back to `super.loadClass` (parent-first) on a
local miss, so `com.xj.winemu.*` resolves from the host dex without editing
ComboLite's delegation prefix list.

#### What happens when the plugin updates

Nothing overwrites the fix — it lives entirely in *our* APK (the shadow dex asset
plus the `PluginClassLoader` hook), and the plugin's `base.apk` is never touched.
The real risk is the reverse: our shadow classes are copies of a **specific**
plugin build, and R8 re-letters every build, so letting a stale shadow win over a
newer plugin is how you get `NoSuchFieldError` or verifier faults deep inside
Wine.

`BhPluginShadow` therefore gates on the installed plugin's `versionCode` (read
from the host's own identity record) matching
`EXPECTED_PLUGIN_VERSION_CODE` (103 for the 6.2.0-era plugin). On a mismatch it
returns the dexPath unchanged:
dual-motor turns **off**, the engine keeps working. Degraded, never broken.

That degradation is deliberately **not silent** — it is reported three ways:

1. a one-shot **Toast on the game-launch path**, naming both versions and saying
   what to do ("Update GameScrub to restore dual-motor");
2. a **warning banner** at the top of the PC Vibration Settings dialog;
3. a `BhPluginShadow` **logcat** line.

Because the plugin loads in the `:pcengine` process while the settings dialog runs
in the main UI process, the status is mirrored through storage — as a **plain
file**, deliberately not SharedPreferences. Each process keeps its own
`SharedPreferencesImpl` per file, populated once and never refreshed from disk, so
a write in `:pcengine` is invisible to a main process that has already read it
(`MODE_MULTI_PROCESS` was the opt-in for re-reading and was deprecated in API 23
for being unreliable). This was a real shipped bug: the banner kept warning that
dual-motor was off for sessions after it had started working, because the
clear-on-success never crossed the process boundary. `BhMenuGameId` still uses
SharedPreferences and is fine — its captured game id is write-once and never has
to return to "cleared", which is exactly the case prefs can't carry. The file
mirror is cleared (deleted, not truncated) as soon as a load succeeds, so the
banner disappears once dual-motor is working.

**A refused shadow is also a privacy regression.** The same shadow dex carries the
plugin-side privacy stubs — the playtime heartbeat (which posts the user's Steam
ID64 every 30 s) and the device-perf session summary. A version mismatch disables
those along with dual-motor, so the Toast and banner above should be read as
"trackers are live again", not merely "rumble feels flat". They deliberately say
dual-motor, because that is the user-visible symptom; this is the part that isn't.

**Sustained rumble is immune to plugin updates** — it patches the Wine component
tree, not the plugin, and its trigger doesn't touch plugin internals. It is the
only rumble half that survives a mismatch.

**Re-pinning is cheap by design.** Every anchor in
`apply_plugin_rumble_patches.py`, `apply_plugin_privacy_patches.py` and
`build_plugin_shadow_dex.py` is derived from the plugin tree rather than pinned to
an R8 letter, because *every* letter drifted between plugin 101 and 102 (Physical
`fi3`→`ji3`→`ds3`, perf uploader `mv1`→`qv1`→`b12`, heartbeat bridge
`jg4`→`kg4`→`gs4`, and all three heartbeat call sites every time). Re-running the
three scripts
against a new plugin and bumping `EXPECTED_PLUGIN_VERSION_CODE` should be the
whole job.

Two traps to know before hand-editing anything here:

- A stale letter usually still resolves to a **real but wrong** class. Plugin 102's
  `fi3` is an unrelated kotlinx serializer, and its `jv1` exists with a different
  constructor. That is why the guards check the exact *constructor*, not just that
  the class exists — a class-existence check passes in both cases and fails at
  runtime instead.
- The Physical vibrator has a same-shaped **sibling** (the phone vibrator) that
  also declares `f()V` and `g(II)V` and also masks with `0xffff`. Matching on
  methods alone would route controller rumble to the phone. They are separated by
  the constructor: Physical takes a device descriptor, the sibling takes an
  `Activity`.

To restore dual-motor after a plugin update: re-run
`apply_plugin_rumble_patches.py` and `build_plugin_shadow_dex.py` against the new
plugin, bump `EXPECTED_PLUGIN_VERSION_CODE`, rebuild. Both scripts fail loudly on
a missing anchor, and the shadow builder refuses to assemble an unpatched tree,
so a re-anchor can't silently produce a no-op build.

**Extracting the plugin is not currently possible on a stock install.** All four
routes are closed on a retail device: `run-as` fails (the release APK is not
debuggable, `flags=0x0`), there is no root, `adb backup` returns an empty
47-byte archive (Android 12+ excludes app data for non-debuggable apps), and the
`plugin/latest` endpoint returns HTTP 402 without the app's auth token. Getting
the plugin therefore needs either a rooted/emulator device or a debuggable build
installed fresh — and since stock is signed `CN=gamesir` and GameScrub with the
repo testkey, installing over it requires an uninstall, which wipes installed
games, the Steam session, and layouts.

### Reliable "Online Update" badges

The check itself is the host's own code, not a reimplementation. The Steam
update repository (`Lwvo;`, "SteamGameRepositoryScope") compares the locally
installed build id (from `steamapps/appmanifest_<appId>.acf`) against the
target build id fetched over the embedded `SteamBridgeClient`, and on a newer
target broadcasts the appId through the badge flow — exactly
what the menu/library dots consume. Stock triggers that check only from
`Ljgk;->r` (launch) and a few detail flows; there is no periodic sweep and no
time-throttle, which is the entire cause of the lag.

`BhSteamUpdateChecker` closes the gap without reimplementing anything:

- **Main process only.** Its smali entry is `AndroidApp.onCreate()`, which runs in
  *every* process, so it also started in `:pcengine` — where it can never succeed,
  because that process's Koin graph has no binding for the repository it reflects
  into. Every sweep there threw `No definition found for type '<repo>' on scope
  '['_root_']'`, once per installed Steam game, on `:pcengine`'s main thread during
  the game-launch window. Caught and logged, so only ever noise, but pointless
  noise at the worst moment. It now gates on a process name with no `":suffix"`,
  erring toward starting when the name is unknown: failing to start in the main
  process would silently drop the badges, whereas a spurious start somewhere new
  is just the noise this removes.
- **Enumeration is a version-independent filesystem scan** of
  `<filesDir>/Steam/steamapps/appmanifest_<appId>.acf` — a manifest's presence
  is the host's own definition of "installed", so this is precisely the set
  worth keeping fresh. No DI, no obfuscated types.
- **Each check reproduces the host's own bulk-sweep dispatch byte-for-byte.**
  GameHub's own per-app dispatch (`Lvi0;->j`) builds `new Lo0a(appId, null, flag)` — a
  compiler-generated suspend-lambda whose `invokeSuspend` calls
  `Ljao;->x(appId, …)` (check + badge broadcast) — and runs it via
  `Lg8i;->L(Dispatchers.IO, block, continuation)` == `withContext(IO) { … }`.
  We drive that same `Lo0a;` block through the same `withContext` reflection
  bridge [BhVjoyImporter](extension/BhVjoyImporter.java) already uses for the
  host's save coroutine (a synthetic `Continuation` proxy on the `Lov3;`
  interface — never on `jao.x`'s abstract `ContinuationImpl` param). Because `Lo0a;` *is*
  the `Lpv3;` handed to `oaj.J`, no proxy touches the abstract type.
- **Cadence:** first sweep ~30 s after launch (let the Steam session settle),
  then every 30 min while the app is alive, plus a debounced sweep on app
  foreground so a badge is fresh when you actually look. One check at a time,
  each with a 30 s ceiling, so a hung bridge or logged-out session just logs
  and is retried next sweep.

The only smali edit is a single `start(Context)` call injected into the
Application's `onCreate` ([scripts/apply_update_check_patches.py](scripts/apply_update_check_patches.py));
everything else lives in the extension class.

### Preload-free architecture

Earlier builds preloaded `libevshim.so` (and later a tiny gate library
`libevgate.so`) into every Wine subprocess to interpose `SDL_JoystickRumble`
at runtime. A small set of games silently exit at launch when *any* extra
`.so` is mapped into their Wine subprocess address space — verified to be
pure mmap presence rather than symbol exports or constructor side effects.
Wine's preloader is famously fussy about address-space layout, and the
canonical case here is **Shotgun King: The Final Checkmate** (GameMaker
Studio 2), which exited ~700 ms after `boot job completed` with
`normalExit=true` and no tombstone whenever an extra preload was present.

The current build avoids that entire failure mode by patching `winebus.so`
on disk and adding nothing to `LD_PRELOAD`. The smali envbuilder patch
([scripts/apply_vibration_patches.py](scripts/apply_vibration_patches.py))
calls [BhVibrationController.ensureWinebusDurationPatchOnce()](extension/BhVibrationController.java)
once per app process, before Wine starts. The Java side scans the files tree for
every `winebus.so` and rewrites the duration loads in place; an `AtomicBoolean`
gates against repeat scans, and releases itself when no `winebus.so` was found so
a scan that ran before the Wine tree existed can be retried.

The call site moved in 6.1.1 and that changed its threading. On 6.0.9 the only
caller was the Wine env builder, already on a background thread, so the scan ran
inline. 6.1.1 moved that builder into the plugin, leaving
`PcEnginePluginHostActivity.onCreate` as the base-APK site — the **main thread**.
A recursive walk of ~16 k files does not belong there, so the trigger now runs
inline only when already off the main thread (preserving the strong
"patched before Wine starts" ordering for any background caller) and hands off to a
worker otherwise. The async path still has plugin load plus engine boot ahead of
any `dlopen` of `winebus.so`, and losing that race would only cost sustained rumble
for a single session, since the on-disk patch persists and applies from the next
launch on.

Both aarch64-unix and x86_64-unix `winebus.so` variants are patched. The
aarch64 path rewrites `ldur w3,[x29,#-0x14]; blr x8` to `mov w3,#-1; blr x8`.
The x86_64 path matches an 11-byte clang/NDK-r26 codegen window (`mov ecx,
[rbp+disp8]; movzwl si,esi; movzwl dx,edx; call *%rax`) and replaces the
3-byte duration load with `or ecx, -1`. If the x86_64 pattern ever misses
on a future proton build, the patcher writes the file to
`<externalFilesDir>/winebus_dump_x86_64.so` so the new codegen can be
inspected with `adb pull` and the pattern refined.

The PC Vibration Settings dialog only controls Mode and Intensity.

### Per-game menu UI

Ported from bannerhub-revanced's `VibrationMenu*Patch` set: GameScrub
injects a **"PC Vibration Settings"** row into all three per-game library
menu surfaces — the game detail More Menu, the library-tile popup, and
the library-list 3-dot popup. Tapping it opens
[BhVibrationSettingsActivity](extension/BhVibrationSettingsActivity.java)
scoped to the right game (per-game Mode/Intensity persisted to the
stock `pc_g_setting<gameId>` SharedPreferences file).

The per-game scoping works even from pre-launch menus where no
`WineActivity` is on the stack: an index-0 `captureGameId(p0)` call
injected into each of the three menu builders reads the game id from
the menu-data parameter and mirrors it to a SharedPreferences file
(cross-process because the menu builders run in the main UI process and
the eventual launch consumer runs in a separate process). The row's
click handler reads back via
[BhMenuGameId.getCaptured()](extension/BhMenuGameId.java).

The label text itself is carried by a Compose Multiplatform string-
resource short-circuit:
[BhMenuRowClick.maybeResolveCustomLabel](extension/BhMenuRowClick.java)
is invoked at the top of the host's resource resolver, returns
`"PC Vibration Settings"` when our sentinel key
`string:bh_pc_vibration_label` is requested, and returns `null` for
everything else so the stock lookup path runs unchanged.

### Local control-layout export/import

Ported from bannerhub-revanced's `ExportControls*Patch` set: GameScrub
hijacks the four host VJoy share-repository entry points and reroutes them to
local files instead of XiaoJi's cloud.

**Export.** A hook at the head of the `/vcontroller/uploadGtheme` method
([BhVjoyShareHook.interceptUpload](extension/BhVjoyShareHook.java)) fires
*before* the layout is uploaded to Tencent COS. It reflects the upload DTO
graph for the `okio.Path` of the freshly-serialized `.gtheme` on disk, reads
those pristine bytes, and hands them to
[BhSafProxyActivity](extension/BhSafProxyActivity.java) for an
`ACTION_CREATE_DOCUMENT` save. Pre-CDN capture matters because the CDN
round-trip used to mangle every byte ≥ 0x80, corrupting non-ASCII layouts.
The user-typed name from the "Name Profile" dialog is captured at the head of
the share-name method and used as the SAF suggested filename. A second hook at
the head of `/vcontroller/shareMap` (`interceptShare`) *throws* — the host
catches it, deletes its temp file, and treats the publish as failed, so there
is no cloud upload, no "Cloud Backup Code" dialog, and no navigation to the
cloud-share tab.

**Import.** The "Import Layout" share-code dialog
is skipped entirely. The
shared `StringResourcesKt.stringResource` resolver short-circuit (the same one that carries the menu
labels) detects the dialog's title resource key at composition time and calls
[BhVjoyShareHook.kickImportFromDialogOpen](extension/BhVjoyShareHook.java),
which fires an `ACTION_OPEN_DOCUMENT` picker and dismisses the briefly-composed
dialog with a synthetic BACK keypress.
[BhVjoyImporter](extension/BhVjoyImporter.java) parses the picked `.gtheme`,
deserializes the layout via the host's polymorphic `VJoyLayoutJson` (reached
through the reflection bridge in [BhVjoyJson](extension/BhVjoyJson.java)), saves
it through the host's own save coroutine, and inserts a `virtual_key_layout`
row into `egggame.db` (opened in WAL mode to match Room) so it appears in My
Layouts. The `/vcontroller/getMapByShareCode` method is also hooked
(`interceptApply`) as a defensive fallback in case the dialog title key is ever
renamed.

**Import on 6.1.1 — what had to be re-anchored.** The four bytecode hooks
re-discovered themselves unchanged (they are anchored on the `vcontroller/*` URL
fragments, not R8 letters — the share repo has drifted
`Lrqn;`→`Lkkm;`→`Lqkm;`→`Laun;`→`Lpat;` across bases and the locator has never
needed editing). What 6.1.1 broke was the *import* half of
[BhVjoyImporter](extension/BhVjoyImporter.java), which relied on kept FQNs that R8
now obfuscates. Import works again; the re-derived anchors are:

| what | 6.1.1 | how it was found |
| --- | --- | --- |
| `VJoyLayout` | `tvr` | its `$serializer` keeps the original `serialName` string |
| host save-coroutine block | `f8n` case `0x14` | horizontally merged — synthetic `<init>(…I)V` plus an int switch (6.0.9: `qpm`) |
| `VJoyLayoutJson` holder | `c0s` field `a` | resolved from a live instance, not by name |

The `serialName` trick is the reusable one: kotlinx.serialization's generated
`$serializer` classes embed the *original* class name as a string literal, so any
`@Serializable` type stays findable no matter how R8 re-letters it.

The Room write went the other way — it dropped an anchor rather than re-deriving
one. 6.0.9 nudged Room's invalidation log through the generated DAO
(`findById` + `upsert`). That is impossible on 6.1.1: the layout DAO impl
(`Ldet;`, returned by `AppDatabase->n()`) declares **only** a constructor, because
R8 moved every query into per-query merged suspend lambdas, and both
`VirtualKeyLayoutDao` and `VirtualKeyLayoutEntity` FQNs are gone. The replacement
therefore uses **only kept library names, no R8 letters at all**:
`androidx.room3.util.DBUtil#performSuspending(db, isReadOnly=false,
inTransaction=true, block)`. Both flags matter — a TEMP-table write has to land on
*Room's own* connection, since `room_table_modification_log` is per-connection and
a write on our own `SQLiteDatabase` handle can never flip Room's `invalidated`
flag.

The `virtual_key_layout` schema itself did **not** change — its `CREATE TABLE`
string is byte-identical to 6.0.9 (32 columns in both).

Export needs none of these anchors, so save-to-file was never affected.

**Cross-process / in-game.** `BhSafProxyActivity` is registered
`android:multiprocess="true"` so it launches in the caller's process — the
import `CompletableFuture` can't bridge the cross-process boundary, and the
export path passes its bytes via Intent extras so it is process-agnostic.

`scripts/apply_export_controls_patches.py` anchors the four bytecode hooks by
**server-stable URL fragments** (`vcontroller/shareMap`, `/getMapByShareCode`,
`/uploadGtheme`) and the upload call-relationship rather than R8-mangled class
letters — both survive R8 reshuffles, and the stock-APK dex numbering differs
from the patched APK the upstream letter map was cut against. It fails loudly
if any anchor is missing or non-unique.

## Build

CI workflow: `.github/workflows/build.yml` — triggers on `workflow_dispatch`
or push of a `v*-6.2.1*` tag.

One-time setup: upload the original GameHub APK as an asset on a release
tagged `base-apk-6.2.1` in this repo (e.g.
`GameHub_6.2.1_6f6ce124ce7e3ff8e05000d85cbdf51b.apk`). The workflow
`gh release download`s from there.

`scripts/apply_vibration_patches.py` **is** run on 6.1.1, but does less than it
used to. It detects a 6.1.1 tree by the presence of
`PcEnginePluginHostActivity.smali` and then applies **only** the winebus trigger —
the dual-motor hooks it used to install now live in the downloaded plugin and are
handled by the four plugin scripts instead (see
[PC engine plugin](#pc-engine-plugin-611)). Its base-APK anchor set is preserved at
the top of the script for whenever the engine returns — `y98` Physical rumble
`h(II)V` / stop `g()V` and `f1p` env builder ctor for 6.0.9, which had themselves
drifted from 6.0.8's `pz7` / `iqn`.

`scripts/apply_privacy_patches.py` is a port of the bannerhub-revanced
privacy patch set. Manifest layer adds Firebase kill-switch meta-data,
disables Google Play Services Measurement components, strips ad-ID
permission declarations, disables every Mob / cn.fly component, and
removes the JieLi gamepad-firmware native libs. Smali layer stubs the
`statistic-gamehub-api.vgabc.com` /events endpoint (`Ll88;->a`), the two
surviving heartbeat/playtime methods (`Lvho;->a` start POST, `Lby9;->e`
getUserPlayTimeList), the OTA URL register (`Lej6;->d`), and the three Mob SDK
bootstrap invokes (`AndroidApp.c` ×2, `Ljku;->E`), plus the Firebase
auto-init re-enable in `AndroidApp.b`. Anchors and the full deliberate-skip
list live at the top of the script.

6.1.1 notes: `.line` debug directives are back in app code (6.0.7–6.0.9
stripped them), so index-0 stubs are inserted after the `.locals` directive and
invoke removals are located by callee — no line numbers or registers are baked
into anchors. Upstream also shrank this surface: `heartbeat/game/update`,
`heartbeat/game/end` and the `/events` perf-config sibling are gone from the
APK, so two heartbeat stubs replace 6.0.9's three and the perf-config stub is
retired. Trade-off worth flagging: GameHub's in-app per-game playtime UI
renders empty (Steam's own playtime on your Steam profile is unaffected —
Steam tracks playtime independently).

`scripts/apply_menu_patches.py` injects the per-game "PC Vibration
Settings" row into the three per-game menu surfaces (`Lbk9;->a` game
detail, `Le1g;->f` library-tile popup, `Lfel;->o` library-list 3-dot),
short-circuits the Compose resource resolver
(`org.jetbrains.compose.resources.StringResourcesKt.stringResource` — a real
name on 6.1.1, no longer an R8 letter) for our label key, registers
[BhVibrationSettingsActivity](extension/BhVibrationSettingsActivity.java)
in the manifest, and appends the CVR resource entry to each features.home
locale bundle. Heavier R8 fragility than the other scripts — fails loudly
on missing anchors so a future base bump doesn't silently ship a broken
menu.

`scripts/apply_export_controls_patches.py` reroutes the on-screen-controls
cloud-share flow to local `.gtheme` files. Registers
[BhSafProxyActivity](extension/BhSafProxyActivity.java) in the manifest
(`android:multiprocess="true"`), appends the `bh_vjoy_*_label` CVR entries,
and injects four bytecode hooks (`interceptShare` at `shareMap`,
`interceptApply` at `getMapByShareCode`, `interceptUpload` at `uploadGtheme`,
`captureShareName` at the share-name method). Anchors by server-stable URL
fragments and the upload call-relationship rather than R8 letters; the label
relabels and the import-dialog skip reuse the `StringResourcesKt` resolver
short-circuit installed by `apply_menu_patches.py`. Fails loudly if any anchor
is missing or non-unique.

The pipeline:

1. `apktool d` the base APK
2. Strip `android:usesPermissionFlags` and
   `android:enableOnBackInvokedCallback` from the manifest (apktool 2.9.3's
   bundled aapt2 doesn't know them; cosmetic, harmless)
3. ~~`python3 scripts/apply_vibration_patches.py`~~ — skipped on 6.1.1; the
   engine it hooks moved into the downloaded plugin
4. `python3 scripts/apply_privacy_patches.py` — manifest + smali +
   native-lib strip
5. `python3 scripts/apply_menu_patches.py` — per-game menu row + manifest
   activity + CVR resource + resolver short-circuit + 3× gameId capture
6. `python3 scripts/apply_export_controls_patches.py` — VJoy export/import
   manifest activity + 4 URL-anchored bytecode hooks + CVR labels
7. `python3 scripts/apply_update_check_patches.py` — one `start(Context)`
   call injected into `AndroidApp.onCreate` for the background update-badge
   checker
8. `apktool b`
9. `javac + d8` of the `extension/Bh*.java` files → next free
   `classesN.dex` slot (classes5 on 6.1.1's 4 stock dex files; computed
   dynamically), inject into the APK
10. `zipalign + apksigner` with `testkey.pk8` / `testkey.x509.pem`
11. Upload as `GameScrub-6.1.1.apk`

## Project layout

```
extension/
  BhMenuGameId.java                per-game id capture for injected menu
                                   rows; SharedPreferences mirror crosses
                                   the cross-process boundary.
  BhMenuRowClick.java              Compose menu-row reflection helpers
                                   (Ll2h / Lizf / Lovg ctors) +
                                   StringResourcesKt
                                   resolver short-circuit + click handler
                                   that launches BhVibrationSettingsActivity
                                   scoped to BhMenuGameId.getCaptured().
  BhPluginShadow.java              prepends our shadow dex to the PC-engine
                                   plugin's PluginClassLoader dexPath;
                                   versionCode gate + asset extraction +
                                   Toast/banner/file-mirror on degrade.
  BhVibrationController.java       singleton dispatcher (smali entry points,
                                   per-game settings, keepalive thread,
                                   in-process winebus.so disk patcher).
  BhVibrationSettingsActivity.java Mode/Intensity dialog UI; shows the
                                   dual-motor degrade banner.
  BhVjoyShareHook.java             smali entry points for the VJoy share/
                                   apply/upload hijack: interceptShare
                                   (throws), interceptUpload (pre-CDN byte
                                   capture + SAF save), interceptApply +
                                   kickImportFromDialogOpen (SAF import),
                                   captureShareName.
  BhSafProxyActivity.java          translucent SAF host (CREATE_DOCUMENT /
                                   OPEN_DOCUMENT) for export/import; raw-fd
                                   IO to avoid the ContentResolver UTF-8
                                   mangling. multiprocess=true.
  BhVjoyImporter.java              .gtheme parse → host save coroutine
                                   (reflection) → virtual_key_layout INSERT
                                   into egggame.db (WAL mode).
  BhVjoyJson.java                  reflection bridge to the host's
                                   polymorphic VJoyLayoutJson for layout
                                   JSON <-> object conversion.
  BhSteamUpdateChecker.java        background worker (started from
                                   AndroidApp.onCreate, main process only)
                                   that enumerates
                                   installed Steam appIds from
                                   steamapps/appmanifest_*.acf and re-runs
                                   the host's own per-app update check
                                   (Lo0a; via withContext) periodically +
                                   on app foreground to keep the "Online
                                   Update" badges fresh.

scripts/
  apply_vibration_patches.py       smali hooks against a decompiled
                                   GameHub apktool tree. On 6.1.1 (detected
                                   by PcEnginePluginHostActivity.smali) it
                                   applies ONLY the winebus trigger — the
                                   dual-motor hooks moved into the plugin
                                   and are applied by the scripts below.
  apply_privacy_patches.py         manifest + smali + native-lib edits
                                   that kill Firebase / GMS Measurement
                                   / Mob Push / XiaoJi events + heartbeat
                                   / JieLi OTA.
  apply_menu_patches.py            per-game "PC Vibration Settings" row
                                   in all 3 menu surfaces + CVR label +
                                   resolver short-circuit + gameId capture.
  apply_export_controls_patches.py VJoy export/import: SAF proxy activity +
                                   4 URL-anchored bytecode hooks + CVR
                                   labels. Reroutes cloud share to local
                                   .gtheme files.
  apply_update_check_patches.py    injects one BhSteamUpdateChecker.start()
                                   call into AndroidApp.onCreate for the
                                   background update-badge checker.
  patch_winebus_rumble_duration.py offline preload-free patch for
                                   extracted winebus.so (aarch64 + x86_64).

  — PC engine plugin (6.1.1+), see "PC engine plugin" above —
  apply_plugin_rumble_patches.py   dual-motor hooks inside a decompiled
                                   plugin tree: GamepadServerManager
                                   onRumble/g(II)V/f()V + the Physical
                                   vibrator class (located by ctor shape,
                                   NOT by letter — it has a same-shaped
                                   phone-vibrator sibling).
  apply_plugin_privacy_patches.py  kills the plugin-side telemetry the
                                   base-APK patches can't reach: perf
                                   summary upload and the heartbeat funnel.
                                   Both located structurally; every letter
                                   here drifted 101 -> 102.
  build_plugin_shadow_dex.py       assembles ONLY the patched plugin
                                   classes into a standalone shadow dex
                                   (refuses to build without the "# BH"
                                   marker, i.e. a no-op shadow).
  apply_plugin_shadow_patches.py   the single base-APK injection that
                                   routes PluginClassLoader's dexPath
                                   through BhPluginShadow.dexPath().

.github/workflows/build.yml        CI build pipeline.
```

## Known stock bugs (not ours)

### Second Wine session in a reused `:pcengine` process aborts (6.1.1)

Symptom: after playing a game and exiting, the *next* "Play Now" closes
instantly; the one after that works. It alternates, and it looks exactly like a
GameScrub regression.

It isn't. The `:pcengine` process survives a session teardown, and setting up a
second Wine session inside that same process double-frees on the render thread:

    AdrenoVK-0: Shader compilation failed for shaderType: 4
    AdrenoVK-0: Pipeline create failed
    scudo: ERROR: invalid chunk state when deallocating address 0x2000075e5ccd6b0
    libc:  Fatal signal 6 (SIGABRT) in tid NNNNN (RenderThread), pid NNNNN (:pcengine)

Android then respawns `:pcengine`, so the following launch gets a fresh process
and succeeds — hence the alternation. Watch the `:pcengine` pid across launches
to see it: one pid serves exactly one session.

Confirmed stock by two A/Bs on 2026-08-06, both reproducing the identical crash:

1. **Shadow disabled.** `EXPECTED_PLUGIN_VERSION_CODE` forced to 999 so
   BhPluginShadow's gate refuses and the plugin loads completely unpatched (log
   shows the "only supports v999" degrade and no "dual-motor ACTIVE"). This
   clears all four shadow classes — dual-motor hooks *and* plugin telemetry
   kills — since they all ride in the shadow dex.
2. **Pure stock.** The untouched stock APK with only its signature replaced by
   the repo testkey, so it installs in place over GameScrub with no data loss and
   contains zero GameScrub code (4 dex, no `classes5.dex`, no `bh_` assets; zero
   `Bh*` log lines during the run). This clears every base-APK patch at once —
   privacy stubs, menus, export controls, update check, winebus trigger.
   See `scratchpad/strip_sig.py` pattern: drop `META-INF/*.{RSA,SF,MF}`, zipalign,
   `apksigner sign` with testkey. Stock 6.1.1 is v2/v3-signed, so there are no
   signature *entries* to drop and the payload stays byte-identical.

Scudo is the platform allocator and the fault is in the Adreno Vulkan path,
unreachable from any of our Java/smali patches.

Not covered by either A/B: the winebus.so duration patch, which lives in app data
rather than the APK and therefore persists under stock too. It is a Wine input
driver in the Wine process tree, not Android's render thread, and the crash fires
during setup before the game runs — but it is the one item still argued rather
than tested. Closing it would need a debuggable build to restore the stock bytes.

Do not chase this in GameScrub. If it ever needs masking, the only lever from the
base APK is recycling `:pcengine` at session end so every launch gets a fresh
process — but that has to happen *after* Steam's exit-time cloud upload
("steam cloud exit upload intent sent"), or it costs cloud saves.

Diagnostic note: `PcEnginePluginHostActivity` logs nothing here. Its
`finish because PC engine plugin runtime is unavailable: reason=…` path is a
*different* failure (plugin runtime genuinely missing) and is absent in this one —
the activity dies with its process instead.

## What this is not

- Not a continuation of BannerHub. The Amazon / Epic / GOG / Component
  Manager / RTS touch / etc. patches that BannerHub layered on top of
  GameHub are gone.
