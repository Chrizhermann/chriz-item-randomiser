# Mode 1 campaign runtime packaging

**Date:** 2026-10-14
**Status:** Implemented and verified on synthetic games only; nothing here has run in the
game. A public release stays gated on the blockers below.

## Scope

Install the post-install Mode 1 campaign runtime (`prototype/`) through the normal
`randomiser.tp2`, build a reproducible release ZIP, and record what Chriz Easy BG
(CEBG) needs to offer it. Mode 2 routes, original special-option parity, automatic
equipment placement and the reward-specific clue route remain separate work. The
default configuration was decided on 2026-10-14 (see Decisions); the configurator's
preset dialog opens on it, and the installer does not apply it yet.

The branch starts from a snapshot of `codex/mode1-postinstall-profiles` (head
`39b665b` plus its uncommitted work as of 2026-10-12), taken without touching that
worktree.

## Package contents

The ZIP keeps the 8.1.1 layout under `randomiser/` and adds two groups. The exact list
is `tools/release-package.json`; nothing outside it is published.

| Group | Paths | Installed by |
| --- | --- | --- |
| Existing installer | `randomiser.tp2`, `randomiser.ini`, readmes, `baf`, `copy`, `d`, `languages`, `lib`, `lists`, `ssl`, `style` | components as in 8.1.1 |
| Campaign runtime | `prototype/M_FLPR.lua`, `M_FLPRUI.lua`, `FLPRCore`, `FLPRGlob`, `FLPRNat`, `FLPRHost`, `FLPRSpell`, `FLPRGat`, `FLPRCur`, `FLPRScript`, `FLPRShop` (`.lua`), `FLPRRun.menu`; `tools/probes/FLPRGateAsm.lua`, `FLPRCampaignPolicy.lua`, `FLPRInventoryAsm.lua` | component 1500 |
| Configurator | `tools/randomiser_config.py`, `tools/flir_config/` (including `ui/`) | run from the mod folder after installation |

Not shipped: `docs`, `research`, `tests`, fixture/QA helpers in `tools/`, the lab automation in
`tools/lab/` (`FLPRQA.lua`, `M_FLPRQA.lua`), the unused `FLPRDrop`/`FLPRHeld`/`FLPRLife` modules
and the `tools/probes/FLPRDrop.lua` diagnostic.

## Installation: component 1500

`DESIGNATED 1500`, `LABEL ~flir-campaign-runtime~`, no prompts. It is the first choice of
the "Randomise items" group (`SUBCOMPONENT @101000`), shown as "New randomiser
(recommended)"; the four 8.1.1 modes follow as "Old randomiser (stable)". The group offers
one choice; the predicates below also keep the two apart for install lists and later
runs. Preconditions, each with its reason:

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

1. Copy the runtime modules, `FLPRRun.menu`, the UI adapter `M_FLPRUI.lua` and the three
   gate/policy modules to `override`, where the runtime loads them from, and create
   `randomiser-campaigns/`, the folder the configurator applies settings to.
2. Add the seven player messages (`languages/<language>/in-game.tra` @1700-@1706) to the
   game text and write `override/M_FLPRTXT.lua`, which maps each message to its string
   reference, so the messages appear in the installation language. Polish falls back to
   English until translated.
3. Patch `UI.MENU`: every `startEngine:OnNewGameButtonClick(` becomes
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
4. Add the GLOBAL `FLPR_FRESH = 1` to the `BALDUR.GAM` new-game template by moving its
   variable table to the end of the file and appending the marker. This is the exact
   layout the native fixture used in every live test. An existing `FLPR_FRESH = 1` is
   accepted unchanged; another value or a duplicate fails. EET's SoA, tutorial and
   Black Pits starts share this template; the runtime only captures BG1 starts, so the
   marker stays inert there.
5. Write `override/flrandomiser3.mrk`.

Until a configuration is applied (`randomiser-campaigns/next.lua` absent), the adapter
passes New Game and character acceptance straight to the engine: games start
unrandomised, as before. Once applied, New Game goes through `FLPR.NewGame`. Whenever the
new adventure cannot be randomised, the engine's two-button popup says why and offers
"Start without randomiser" or Cancel; an adventure never starts unrandomised silently
(see "Player messages"). The adapter detects the
configuration with `io.open`; the engine's internal Lua has no `io`, so it falls back to
`loadfile`, and a configuration that exists but does not compile still counts. Without
both functions it cannot see a configuration and passes through; LuaJIT, which the
component requires, provides both.

## Configuration, uninstall and re-install

After the whole mod stack is installed, the player scans the game (`scan`, `world`) and
applies a configuration from the editor with the game closed (`ui ... --apply-game`). Apply
writes `randomiser-campaigns/` data, campaign-aware dialogue/script/spell resources and the
`BGEE.LUA` journal in `override`, and appends strings to `lang/<language>/dialog.tlk`.
WeiDU does not track these writes. `activation.validate_runtime` now accepts this
component's installation as well as the hand-staged lab layout.

The quest and dialogue changes of the default configuration are made by 1500 itself
(`lib/campaign_patches.tpa`): the pool, altar and book reward scripts (BLPOOL, TROALT,
PPBOOK), Unshey's conversation and journal entry, Glaicus's flail-heads conversation and
the TotSC tome stand-in. Each is found by meaning (the block that creates the item, the
reply that takes it, the state that hands over the first head) and split under a GLOBAL
that only the runtime sets, so other adventures and other mods' edits keep working.
Glaicus's redirected replies keep their original text, action, journal entry and flags.
A file that cannot be changed unambiguously stays as installed; the installer prints a
note (setup.tra @101520-@101528) and `override/FLPRPAT.lua` records the reason, which the
editor shows instead of offering the option.

Uninstalling 1500 restores `UI.MENU`, `BALDUR.GAM` and those quest files byte for byte
and removes every file the component created. WeiDU keeps the appended game-text
strings, unused, as it does for every mod; a re-install finds and reuses them. WeiDU does
not track folders, so the `randomiser-campaigns` folder it created stays behind empty.
Uninstall cannot remove what the configurator wrote; see B2. Re-installing over the installed component
and an uninstall and install in one WeiDU run leave the game byte-identical, also with an
applied `randomiser-campaigns/next.lua`;
so does WeiDU's temporary uninstall and re-install of 1500 behind a reinstalled earlier
mod.

## Player messages

Texts are `in-game.tra` @1700-@1706 (see Installation, step 2); `M_FLPRUI.lua` keeps the
English text as a fallback. Failure details go to `randomiser-campaigns/last-error.txt`
(latest failure only) and the EEex console; the messages name that file.

| Situation | What the player sees |
| --- | --- |
| Configuration applied, game started without InfinityLoader/EEex | New Game popup: not running, start with `InfinityLoader.exe` |
| The runtime could not start (a native hook found other engine code) | New Game popup: this game version is not supported yet |
| The runtime could not start (missing or damaged configuration files) | New Game popup: could not randomise this adventure |
| A New Game outside BG1 (SoD, SoA, ToB, Black Pits starts) | New Game popup: only BG1 starts so far |
| Capture fails at character acceptance (for example a later mod replaced `BALDUR.GAM`) | Popup: could not randomise; Cancel keeps character creation open for a retry |
| Capture succeeded | Message log, once, when the world first appears: items are randomised |
| The runtime stops in a randomised adventure, or one is loaded while it cannot run | Message log, once per loaded game: stopped working, unplaced items may be missing |

Each popup offers "Start without randomiser" (the engine's own New Game or character
acceptance, unrandomised) and Cancel. Capture is atomic: every GLOBAL written while
capturing is restored and the source gate is disarmed when any step fails, so starting
without randomiser leaves no partial campaign. Messages appear in the engine's
`POPUP_TWOBUTTON`, whose text area holds about four lines; translations should stay
similarly short.

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
asset byte for byte; only the ZIP container differs. The package built from `ce10fda`
has 711 files, 8,360,947 bytes uncompressed, a largest entry of 1,472,788 bytes
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
  runtime (`FLPR_Transfer` through EEex) and break once it is uninstalled. Chosen route:
  move the static campaign-aware patches into the WeiDU component. Done for the default
  configuration's changes (see "Configuration, uninstall and re-install"); Apply now
  copies only the files its latest export lists. Still written by Apply and left behind
  by an uninstall: the Mode 2 reward scripts and dialogues (their selected branches call
  `FLPR_Transfer`), the Mode 2 container conditions, the Deck of Many Things key spell
  (`SPIN621.SPL`, which calls the runtime's `FLPRKEY`), the Kangaxx option and the
  `BGEE.LUA` journal it extends. The default configuration uses none of them, so its
  Apply writes only `randomiser-campaigns/`. These move into 1500 next, the same way;
  the Mode 2 patches also drop their exact-hash checks then.
- **B3 Failure messages.** Resolved; see "Player messages".
- **B4 Test hooks in the runtime.** Resolved: the Remote Console poll wrapper, the chapter
  screen render listener, the QA driver and its `qa-status.json` moved to the lab-only
  `tools/lab/M_FLPRQA.lua` and `tools/lab/FLPRQA.lua`, which neither the TP2 nor the
  package contains; a lab stages both into `override`. The runtime exposes read-only
  `FLPR.Core` and `FLPR.Storage` for them. The data folder is now `randomiser-campaigns/`.
- **B5 Coverage.** BG1 starts on EET only; direct SoD, BG2 and ToB starts are
  unsupported, as is the remaining parity work listed above. BG:EE (with SoD) and BG2:EE
  without EET must work before a release (decided 2026-10-14); the runtime has to tell
  the platforms apart rather than assume EET.
- **B6 Engine 2.7.3.0.** The native hooks are pinned to the BG2:EE 2.6.6.0 executable
  (relative addresses and code bytes in `FLPRGat.lua` and `FLPRNat.lua`). The current
  Steam BG:EE and BG2:EE and the CEBG reference game are 2.7.3.0, which EEex 1.2 also
  supports. There the runtime refuses every New Game with the "not supported yet" popup.
  Every native site needs a 2.7.3.0 port and an in-game check on that version.

Resolved here: in the snapshot, 15 native host tests (five in each Lua runtime) failed
in `M_FLPR.lua`'s campaign preparation, which reads each source's
`reward_selected_global` from the native fixture. The tests' fixtures had no `sources`
endpoints; every version of the exporter and the fixture builder writes one for each
manifest source, so no supported bundle lacks them. The fixtures now have that shape,
and the runtime stops the capture with `FIXTURE_SOURCES_MISSING` or
`SOURCE_ENDPOINT_MISSING` instead of an index error, so a missing endpoint can never
skip a reward quest's selection flag silently.

## Decisions

Christopher decided on 2026-10-14:

- **Names and placement**: "New randomiser (recommended)", the default choice and
  exclusive with the old one; the 8.1.1 modes are "Old randomiser (stable)". No
  "experimental" label. Implemented as the first choice of the "Randomise items" group.
- **Releases**: normal mod releases, no prereleases, once the blockers are solved.
- **Distribution**: a standalone and a CEBG version; neither needs Python (B1).
- **Default configuration**: the original Mode 1 "no items are lost" (1100) plus the Flail
  of Ages heads (540, `flail_heads`) and the randomised Tales of the Sword Coast tome (500,
  `totsc_tome`); every other option off. It is not an EET-only preset (B5).
- **Mode 2 routes** are built in this mod.

Open:

- **Version and packaging**: both randomisers in this mod's one installer as v9.0
  (proposed), or the new one as a separate mod.
- **B1 route** (options above), now constrained to Python-free for both versions.

## Verification

- `tests/Test-CampaignRuntimeInstaller.ps1` (WeiDU 249, synthetic EET games; 153 checks,
  from the working tree and from the built ZIP with `-PackagePath`): install, uninstall,
  re-install, re-install over the installed component, uninstall and install in one run,
  re-install behind a reinstalled earlier mod (also from the install list alone),
  already-patched inputs, override-resident and archived `UI.MENU`/`BALDUR.GAM`, UI-mod
  call shapes, redefined UI methods, an applied `next.lua`, exclusions in both
  directions, missing prerequisites, rollback of failed installs and the Polish
  language, with KEY, BIFF, TLK and unrelated override files byte-checked, and the seven
  messages appended to the game text with matching references in `M_FLPRTXT.lua`. The
  quest changes run on hand-written scripts and dialogues that WeiDU compiles with
  minimal IDS tables (`tests/campaign_patch_fixtures.py`), archived and in override:
  each split script equals its expected compiled form, the dialogues are checked by
  structure, and files that give the reward twice or not at all, were already split,
  ask for another item, erase an unknown journal entry or hand over no flail head stay
  byte-identical, with the reason recorded and printed. No game resource is used.
- `tests/Test-Mode2Parity.ps1` (32 checks) and the other 14 PowerShell suites pass, with a
  Lua 5.3 interpreter for `Manifest` and `RuntimeProbes`. `SentinelDependencies` and
  `run-unit.ps1` need the sentinel BAFs of a lab that no longer exists and were not run.
- WeiDU `--parse-check` of `randomiser.tp2` and every `lib/*.tpa`.
- `tests/test_release_package.py`: EOL parity with Git, determinism, refused inputs,
  manifest integrity and coverage of every file the TP2 component copies.
- `tests/test_runtime_ui_adapter.py`: adapter routing and the not-running popup in Lua 5.1,
  Lua 5.3 and LuaJIT; `tests/test_campaign_native_host.py`: atomic capture, every player
  message and its start/cancel choice; `tests/test_lab_qa_module.py`: the lab-only module.
- `tests/test_runtime_activation.py`: the configurator accepts the installed runtime.
- The full Python suite, with `keystone-engine` 0.9.2 and `unicorn` 2.1.4 from a local
  test environment on `PYTHONPATH` and `FL_IR_TEST_TEMP_ROOT=%TEMP%\flir-t`: 1310 tests
  pass; 2 skip because optional lab caches and evidence are not in the source tree.
- Not verified: any in-game behavior of the TP2-installed runtime, the adapter in the
  engine's own Lua, UI mods in a running game and EEex versions other than 1.2.
