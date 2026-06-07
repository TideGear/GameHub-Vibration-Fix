# GameScrub

The idea of GameScrub is to fix controller vibration, remove privacy
issues, and still allow login to GameHub for the actually useful
features like recommended per-game settings, default driver list,
and library sync (so install/uninstall and library
state across devices keep working).

It is built on GameHub v6.x and heavily uses the work of
[@The412Banner](https://github.com/The412Banner) as well as others. It
also includes my own PC-accurate controller vibration fixes.

What you get over stock GameHub:

- **Privacy patches** — Login-friendly port of bannerhub-revanced's privacy
  patch set. Kills Firebase Analytics, Google Play Services Measurement, Mob
  Push SDK, the XiaoJi heartbeat / playtime tracker, both vgabc.com /events
  endpoints, and the JieLi OTA phone-home. Steam / GOG / Epic / Wine /
  account login are untouched. Full channel list in
  [scripts/apply_privacy_patches.py](scripts/apply_privacy_patches.py).
- **Dual-motor low/high dispatch.** Wine games calling `XInputSetState(slot,
  low, high)` get the two motors driven independently via Android
  `CombinedVibration.startParallel` on ≥ 2-motor controllers. Stock GameHub
  blends both motors into a single haptic pulse; this preserves the heavy
  / light distinction the way the game intended.
- **Sustained rumble holds past 1 s.** SDL2's internal 1 s
  `rumble_expiration` auto-stops sustained rumble on stock. The APK's
  launch-time Java hook patches every app-owned `winebus.so` on disk so
  the two non-zero `SDL_JoystickRumble` call sites pass `0xffffffff` as
  the SDL duration; zero-duration stop calls still stop immediately. No
  `LD_PRELOAD`, no extra `.so` mapped into the Wine subprocess address
  space. The offline helper
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
  inside a running game (the `:wine` process) too.

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
once per app process, immediately before the env builder hands off to the
Wine launcher. The Java side scans the files tree for every `winebus.so`
and rewrites the duration loads in place; an `AtomicBoolean` gates against
repeat scans.

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
the eventual launch consumer runs in the `:wine` process). The row's
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

**Import.** The "Import Layout" share-code dialog is skipped entirely. The
shared `Lok8;->c0` resolver short-circuit (the same one that carries the menu
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

**Cross-process / in-game.** `BhSafProxyActivity` is registered
`android:multiprocess="true"` so it launches in the caller's process — the
import `CompletableFuture` can't bridge the main↔`:wine` boundary, and the
export path passes its bytes via Intent extras so it is process-agnostic.

`scripts/apply_export_controls_patches.py` anchors the four bytecode hooks by
**server-stable URL fragments** (`vcontroller/shareMap`, `/getMapByShareCode`,
`/uploadGtheme`) and the upload call-relationship rather than R8-mangled class
letters — both survive R8 reshuffles, and the stock-APK dex numbering differs
from the patched APK the upstream letter map was cut against. It fails loudly
if any anchor is missing or non-unique.

## Build

CI workflow: `.github/workflows/build.yml` — triggers on `workflow_dispatch`
or push of a `v*-6.0.7*` tag.

One-time setup: upload the original GameHub APK as an asset on a release
tagged `base-apk-6.0.7` in this repo (e.g.
`GameHub_6.0.7_62a84f7541a4c334b1e0b02279d5af41.apk`). The workflow
`gh release download`s from there.

`scripts/apply_vibration_patches.py` patches stock 6.0.7 using the
ProGuard names `nz7` (Physical: rumble `h(II)V`, stop `g()V`) and `bqn`
(env builder; the winebus disk-patch trigger rides its `<init>` ctor where
the Context is still live, since 6.0.7 moved the env-var join out of the
builder). Full anchor set and rename map are at the top of the script. Note
6.0.7 strips `.line` debug directives from app code, so anchors are built
from instruction sequences only.

`scripts/apply_privacy_patches.py` is a port of the bannerhub-revanced
privacy patch set. Manifest layer adds Firebase kill-switch meta-data,
disables Google Play Services Measurement components, strips ad-ID
permission declarations, disables every Mob / cn.fly component, and
removes the JieLi gamepad-firmware native libs. Smali layer stubs the
two `statistic-gamehub-api.vgabc.com` event endpoints, the four
heartbeat/playtime methods, the OTA URL register, and the three Mob SDK
bootstrap invokes. Anchors and the full deliberate-skip list live at the
top of the script. Trade-off worth flagging: GameHub's in-app per-game
playtime UI renders empty (Steam's own playtime on your Steam profile
is unaffected — Steam tracks playtime independently).

`scripts/apply_menu_patches.py` injects the per-game "PC Vibration
Settings" row into the three per-game menu surfaces (`Lc37;->a` game
detail, `Ly7c;->f` library-tile popup, `Levb;->b0` library-list 3-dot),
short-circuits the Compose resource resolver (`Lok8;->c0`) for our label key, registers
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
relabels and the import-dialog skip reuse the `Lok8;->c0` resolver
short-circuit installed by `apply_menu_patches.py`. Fails loudly if any anchor
is missing or non-unique.

The pipeline:

1. `apktool d` the base APK
2. Strip `android:usesPermissionFlags` and
   `android:enableOnBackInvokedCallback` from the manifest (apktool 2.9.3's
   bundled aapt2 doesn't know them; cosmetic, harmless)
3. `python3 scripts/apply_vibration_patches.py` — four smali hooks
4. `python3 scripts/apply_privacy_patches.py` — manifest + smali +
   native-lib strip
5. `python3 scripts/apply_menu_patches.py` — per-game menu row + manifest
   activity + CVR resource + resolver short-circuit + 3× gameId capture
6. `python3 scripts/apply_export_controls_patches.py` — VJoy export/import
   manifest activity + 4 URL-anchored bytecode hooks + CVR labels
7. `apktool b`
8. `javac + d8` of the `extension/Bh*.java` files → next free
   `classesN.dex` slot (classes6 on 6.0.7's 5 stock dex files; computed
   dynamically), inject into the APK
9. `zipalign + apksigner` with `testkey.pk8` / `testkey.x509.pem`
10. Upload as `GameScrub-6.0.7.apk`

## Project layout

```
extension/
  BhMenuGameId.java                per-game id capture for injected menu
                                   rows; SharedPreferences mirror crosses
                                   the main↔":wine" process boundary.
  BhMenuRowClick.java              Compose menu-row reflection helpers
                                   (Ltyc / Lg6c / Lstc ctors) + Lok8.c0
                                   resolver short-circuit + click handler
                                   that launches BhVibrationSettingsActivity
                                   scoped to BhMenuGameId.getCaptured().
  BhVibrationController.java       singleton dispatcher (smali entry points,
                                   per-game settings, keepalive thread,
                                   in-process winebus.so disk patcher).
  BhVibrationSettingsActivity.java Mode/Intensity dialog UI.
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

scripts/
  apply_vibration_patches.py       smali hooks against a decompiled
                                   GameHub 6.0.7 apktool tree.
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
  patch_winebus_rumble_duration.py offline preload-free patch for
                                   extracted winebus.so (aarch64 + x86_64).

.github/workflows/build.yml        CI build pipeline.
```

## What this is not

- Not a continuation of BannerHub. The Amazon / Epic / GOG / Component
  Manager / RTS touch / etc. patches that BannerHub layered on top of
  GameHub are gone.
