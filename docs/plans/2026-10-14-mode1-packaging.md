# Mode 1 campaign runtime packaging

**Date:** 2026-10-14
**Status:** Implemented and verified on synthetic games only; nothing here has run in the
game. A public release stays gated on the blockers below.

## Scope

Install the post-install Mode 1 campaign runtime (`prototype/`) through the normal
`randomiser.tp2`, build a reproducible release ZIP, and record what Chriz Easy BG
(CEBG) needs to offer it. Mode 2 routes, original special-option parity, automatic
equipment placement and the reward-specific clue route remain separate work. No
default configuration, preset or seed is chosen here.

The branch starts from a snapshot of `codex/mode1-postinstall-profiles` (head
`39b665b` plus its uncommitted work as of 2026-10-12), taken without touching that
worktree.

## Package contents

The ZIP keeps the 8.1.1 layout under `randomiser/` and adds two groups. The exact list
is `tools/release-package.json`; nothing outside it is published.

| Group | Paths | Installed by |
| --- | --- | --- |
| Existing installer | `randomiser.tp2`, `randomiser.ini`, readmes, `baf`, `copy`, `d`, `languages`, `lib`, `lists`, `ssl`, `style` | components as in 8.1.1 |
| Campaign runtime | `prototype/M_FLPR.lua`, `M_FLPRUI.lua`, `FLPRCore`, `FLPRGlob`, `FLPRNat`, `FLPRHost`, `FLPRQA`, `FLPRSpell`, `FLPRGat`, `FLPRCur`, `FLPRScript`, `FLPRShop` (`.lua`), `FLPRRun.menu`; `tools/probes/FLPRGateAsm.lua`, `FLPRCampaignPolicy.lua`, `FLPRInventoryAsm.lua` | component 1500 |
| Configurator | `tools/randomiser_config.py`, `tools/flir_config/` (including `ui/`) | run from the mod folder after installation |

Not shipped: `docs`, `research`, `tests`, fixture/QA helpers in `tools/`, the unused
`FLPRDrop`/`FLPRHeld`/`FLPRLife` modules and the `tools/probes/FLPRDrop.lua` diagnostic.

## Installation: component 1500

`DESIGNATED 1500`, `LABEL ~flir-campaign-runtime~`, no prompts. Preconditions, each
with its reason:

- `GAME_IS ~eet~`. The runtime captures a campaign only on a BG1 New Game, and its
  native source gate checks code bytes of the BG2:EE 2.6.6.0 executable. EET is the
  platform with both; all native evidence is EET on BG2:EE 2.6.6 with EEex 1.2.
- EEex present (`override/M___EEex.lua`, present in every EEex version).
- LuaJIT active (exactly one `LuaPatchMode=REPLACE_INTERNAL_WITH_EXTERNAL` line in
  `InfinityLoader.ini`): the runtime uses the `io` and `os` libraries. Checked in the
  component body so a missing prerequisite fails the run visibly.
- No legacy Mode 1 or Mode 2 install (`flrandomiser1.mrk`, `flrandomiser2.mrk`). Those
  remove originals at install time and run their own assignment.
- Not together with component 560. It raises the Duergar Spell Shield stock at install
  time, so a campaign with the configurator's `more_spell_shield` option off could no
  longer have the original stock.
- No separately staged runtime: an `override/M_FLPR.lua` is refused unless WeiDU.log
  records component 1500 itself, so a development lab is never overwritten while a
  re-install over the component still works. A guard that only tested for the file would
  turn false after the component's own install; WeiDU 249 then asks its "[R]e-Install"
  question even in a `--force-install-list` run and, without input, repeats it forever.

Actions:

1. Copy the runtime modules, `FLPRRun.menu` and the UI adapter `M_FLPRUI.lua` to
   `override`, and the three gate/policy modules to `flpr-fixture/`, the paths the
   runtime loads them from.
2. Patch `UI.MENU`: every `startEngine:OnNewGameButtonClick(` becomes
   `FLPRUI_NewGame(` and every `createCharScreen:AcceptCharacter(` becomes
   `FLPRUI_AcceptCharacter(`. At least one of each must exist; any other call shape of
   either function fails the install, because an unhooked path would start an
   unrandomised campaign silently. A `UI.MENU` that already calls `FLPRUI_` functions
   also fails: an uninstall would restore those calls without the adapter. So does one
   that defines either method (`function startEngine:OnNewGameButtonClick(`, with any
   whitespace after `function`): the patch would rename the definition into a global
   `FLPRUI_` function that replaces the adapter. Multiplayer
   `connectionScreen:OnNewGameButtonClick` is left alone. Verified call shapes: the
   reference EET `UI.MENU`, LeUI 4.9.1, Infinity UI 1.17 and the EEUITweaks 4.0.7
   chargen fragments.
3. Add the GLOBAL `FLPR_FRESH = 1` to the `BALDUR.GAM` new-game template by moving its
   variable table to the end of the file and appending the marker. This is the exact
   layout the native fixture used in every live test. An existing `FLPR_FRESH = 1` is
   accepted unchanged; another value or a duplicate fails. EET's SoA, tutorial and
   Black Pits starts share this template; the runtime only captures BG1 starts, so the
   marker stays inert there.
4. Write `override/flrandomiser3.mrk`.

Until a configuration is applied (`flpr-fixture/next.lua` absent), the adapter passes
New Game and character acceptance straight to the engine: games start unrandomised, as
before. Once applied, a BG1 New Game goes through `FLPR.NewGame`. If the runtime is not
running then (game started without EEex), the New Game button fails with an error
instead of silently starting an unrandomised campaign. The adapter detects the
configuration with `io.open`; the engine's internal Lua has no `io`, so it falls back to
`loadfile`, and a configuration that exists but does not compile still counts. Without
both functions it cannot see a configuration and passes through; LuaJIT, which the
component requires, provides both.

## Configuration, uninstall and re-install

After the whole mod stack is installed, the player scans the game (`scan`, `world`) and
applies a configuration from the editor with the game closed (`ui ... --apply-game`). Apply
writes `flpr-fixture/` data, campaign-aware dialogue/script/spell resources and the
`BGEE.LUA` journal in `override`, and appends strings to `lang/<language>/dialog.tlk`.
WeiDU does not track these writes. `activation.validate_runtime` now accepts this
component's installation as well as the hand-staged lab layout.

Uninstalling 1500 restores `UI.MENU` and `BALDUR.GAM` byte for byte and removes every
file the component created. WeiDU does not track folders, so the `flpr-fixture` folder
it created stays behind empty. Uninstall cannot remove what the configurator wrote; see
B2. Re-installing over the installed component and an uninstall and install in one
WeiDU run leave the game byte-identical, also with an applied `flpr-fixture/next.lua`;
so does WeiDU's temporary uninstall and re-install of 1500 behind a reinstalled earlier
mod.

## EEex dependency

Required: EEex with LuaJIT (CEBG's `feature:eeex:mandatory-components`, EEex v1.2
components 0-8). Tested: EEex 1.2 on BG2:EE 2.6.6.0 only. EEex v0.11's Lua sources do
not define `EEex_GetImageBase`, which the source gate calls; the installer does not
refuse v0.11 because no minimum version has been established, and the runtime fails
closed when an API or the executable's code bytes do not match.

## Relation to the 8.1.1 components

| Components | With 1500 |
| --- | --- |
| 1100, 1200 (Mode 1), 1300, 1400 (Mode 2) | Mutually exclusive, enforced in both directions. |
| 560 More Spell Shield scrolls | Mutually exclusive, both directions; the configurator offers it per campaign. |
| 500, 510, 520, 530, 540, 570 | Marker-only inputs to 1100-1400; inert with 1500 and not blocked. Their configurator equivalents are `totsc_tome`, `unrandomized_percent`, `kangaxx`, `scrolls`, `flail_heads`, `cursed_disguise`. |
| 5005-5085 | Require Mode 2, so unavailable. |
| 9000, 9050, 10100, 10200, 10210, 10300 | Independent; install them before the scan. |

The exclusion adds one `REQUIRE_PREDICATE` line to each of 1300 and 1400. The Mode 2
parity check allows exactly that line and still compares the rest of each component
body with the `b600e94` baseline.

## Release ZIP

`tools/build_release.py` builds `randomiser-v<VERSION>.zip` and a JSON manifest from a
commit's Git objects, never the working tree. Output is byte-reproducible: sorted file
entries without directory entries, the commit time as every timestamp, Unix regular-file
attributes (mode 644), Deflate level 9 and the commit ID as the archive comment. Text
files get the CRLF conversion that Git's `core.autocrlf=true` checkout applies, which is
what the published 8.1.1 asset contains (the configurator also reads `.tra` text with
its authored line endings). `.gitattributes`, symlinks and submodules are refused rather
than guessed. `--verify` rebuilds and compares an existing ZIP byte for byte.

Built from `f4a9dfb` with the 8.1.1 file list, the 662 files match the published 8.1.1
asset byte for byte; only the ZIP container differs. The package built from `913ffef`
has 711 files, 8,359,908 bytes uncompressed, a largest entry of 1,472,788 bytes
(`languages/iconv/libiconv-1.9.2-1-src.7z`), a maximum compression ratio of 14.8, a
path depth of five and one TP2 (`randomiser/randomiser.tp2`). That is within the
archive limits CEBG uses for the 8.1.1 asset (1,024 entries, 4 MiB per entry, 16 MiB in
total, ratio 100, depth 8). Its name still says 8.1.1 because `VERSION` is unchanged; it
must not be published until the version is decided.

## What CEBG needs

- Component `1500` of `randomiser/randomiser.tp2`, label `flir-campaign-runtime`,
  language 0, no prompts; a new release asset with its SHA-256 and length.
- Requires EET and `feature:eeex:mandatory-components`.
- Excludes Randomiser 1100, 1200, 1300, 1400 and 560. Presets choosing 1500 should drop
  500-570, which would only be inert.
- Order: after every component that writes `UI.MENU` or `BALDUR.GAM`; the end of the
  BG2 run is simplest. The installer cannot see later overwrites, so after the stack
  completes CEBG should check that `override/UI.MENU` contains `FLPRUI_NewGame(` and
  `FLPRUI_AcceptCharacter(`, `override/BALDUR.GAM` contains `FLPR_FRESH`, and
  `override/flrandomiser3.mrk` exists.
- A post-install step: scan, configure and apply with the game closed (needs B1).
- Uninstall or recovery: remove applied settings first (needs B2). Re-installing 1500
  itself is safe.

## Blockers before a public release

- **B1 Python.** The scanner, editor and exporter are Python 3.10+ (standard library
  only). Players must not need Python. Options: (a) CEBG ships the pinned CPython
  embeddable distribution and hosts the editor; standalone users still need Python;
  (b) a frozen executable (PyInstaller or Nuitka; new build toolchain, antivirus false
  positives); (c) port scan/export/apply to Rust inside CEBG (largest; standalone users
  need CEBG); (d) an in-game EEex configuration screen (Lua port of the scanner and
  exporter plus UI work).
- **B2 Uninstall after Apply.** Patched dialogues and scripts left in `override` call the
  runtime (`FLPR_Transfer` through EEex) and break once it is uninstalled. Needs a
  configurator "remove applied settings" with pre-apply receipts, run before uninstall,
  or moving the static campaign-aware patches into the WeiDU component.
- **B3 Failure messages.** A configured runtime that cannot capture (EEex inactive,
  missing revision, executable mismatch, a later `UI.MENU`/`BALDUR.GAM` overwrite) stops
  character acceptance with a Lua error and no player-facing message.
- **B4 Test hooks in the runtime.** `M_FLPR.lua` wraps `EEexRemote.Poll`, adds a chapter
  screen render listener, runs the QA driver and writes `flpr-fixture/qa-status.json`.
  CEBG can install EEex Remote Console, so these affect players. The `flpr-fixture`
  directory name is also a development name.
- **B5 Coverage.** BG1 starts on EET only; direct SoD, BG2 and ToB starts are
  unsupported, as is the remaining parity work listed above.

Resolved here: in the snapshot, 15 native host tests (five in each Lua runtime) failed
in `M_FLPR.lua`'s campaign preparation, which reads each source's
`reward_selected_global` from the native fixture. The tests' fixtures had no `sources`
endpoints; every version of the exporter and the fixture builder writes one for each
manifest source, so no supported bundle lacks them. The fixtures now have that shape,
and the runtime stops the capture with `FIXTURE_SOURCES_MISSING` or
`SOURCE_ENDPOINT_MISSING` instead of an index error, so a missing endpoint can never
skip a reward quest's selection flag silently.

## Decisions for Christopher

- **Component name and description** shown to players. The TRA strings are provisional.
- **Placement**: separate component (current) or a fifth choice in the "Randomise items"
  group.
- **Release timing and version**: hold until B1-B3 are solved (recommended), publish as
  an experimental component for Python users, or a CEBG-only prerelease; 8.2.0 or 9.0.0.
- **Default configuration**: none (current), the original EET Mode 1 preset, or a
  curated selection.
- **CEBG preset**: whether 1500 replaces 1100 by default, and dropping 500-570 then.
- **B1 route** (options above).

## Verification

- `tests/Test-CampaignRuntimeInstaller.ps1` (WeiDU 249, synthetic EET games; 111 checks,
  from the working tree and from the built ZIP with `-PackagePath`): install, uninstall,
  re-install, re-install over the installed component, uninstall and install in one run,
  re-install behind a reinstalled earlier mod (also from the install list alone),
  already-patched inputs, override-resident and archived `UI.MENU`/`BALDUR.GAM`, UI-mod
  call shapes, redefined UI methods, an applied `next.lua`, exclusions in both
  directions, missing prerequisites, rollback of failed installs and the Polish
  language, with KEY, BIFF, TLK and unrelated override files byte-checked.
- `tests/Test-Mode2Parity.ps1` (32 checks) and the other PowerShell suites that run
  without a Lua 5.3 interpreter or sentinel BAFs pass as on the snapshot. `Manifest`,
  `RuntimeProbes` and `SentinelDependencies` need those and were not run.
- WeiDU `--parse-check` of `randomiser.tp2` and every `lib/*.tpa`.
- `tests/test_release_package.py`: EOL parity with Git, determinism, refused inputs,
  manifest integrity and coverage of every file the TP2 component copies.
- `tests/test_runtime_ui_adapter.py`: adapter routing in Lua 5.1, Lua 5.3 and LuaJIT.
- `tests/test_runtime_activation.py`: the configurator accepts the installed runtime.
- The full Python suite, with `keystone-engine` 0.9.2 and `unicorn` 2.1.4 from a local
  test environment on `PYTHONPATH` and `FL_IR_TEST_TEMP_ROOT=%TEMP%\flir-t`: 1293 tests
  pass; 3 skip because optional lab caches and evidence are not in the source tree.
- Not verified: any in-game behavior of the TP2-installed runtime, the adapter in the
  engine's own Lua, UI mods in a running game and EEex versions other than 1.2.
