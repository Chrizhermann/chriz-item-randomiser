# Chriz Item Randomiser packaging

**Date:** 2026-10-14
**Status:** Implemented and verified on synthetic games only; nothing here has run in the
game. A public release stays gated on the blockers below.

## Scope

Install the campaign runtime (`prototype/`) through `cir.tp2`, the installer of Chriz
Item Randomiser (CIR), build a reproducible release ZIP, and record what Chriz Easy BG
(CEBG) needs to offer it. Until 2026-10-17 the runtime was component 1500 of Item
Randomiser's `randomiser.tp2`; it became CIR's main component unchanged, and the optional
tweaks Christopher and CEBG use followed it (see Decisions). The extended pools' reward routes, original special-option parity, automatic
equipment placement and the reward-specific clue route remain separate work. The
default configuration was decided on 2026-10-14 (see Decisions); the configurator's
preset dialog opens on it, and the installer does not apply it yet.

The branch starts from a snapshot of `codex/mode1-postinstall-profiles` (head
`39b665b` plus its uncommitted work as of 2026-10-12), taken without touching that
worktree.

## Package contents

The ZIP holds one folder, `cir/`. The exact list is `tools/release-package.json`;
nothing outside it is published.

| Group | Paths | Used by |
| --- | --- | --- |
| Installer | `cir.tp2`, `cir.ini`, `readme-cir.html`, `README.md`, `LICENSE`, `lib/campaign_runtime.tpa`, `lib/campaign_patches.tpa`, `lib/fl#bg1pal.tpa`, `languages` | `cir.tp2` |
| Optional tweaks | `lib/tweaks.tpa`, `d/botsmith.d`, `d/udduer01.d`, `baf/botsmith.baf`, their `languages/*/botsmith.tra` and `udduer01.tra` | `cir.tp2` components 9000-10300 |
| Item Randomiser data | `lists/` (items, locations, sources, endpoints, groups), `copy/` (`flbhcryp.itm`, `flrbelt1.itm`, `ward.itm`, `flrward1.bam`) | the configurator; `campaign_patches.tpa` copies two items |
| Campaign runtime | `prototype/M_FLPR.lua`, `M_FLPRUI.lua`, `FLPRCore`, `FLPRGlob`, `FLPRNat`, `FLPRHost`, `FLPRSpell`, `FLPRGat`, `FLPRCur`, `FLPRScript`, `FLPRShop` (`.lua`), `FLPRRun.menu`; `tools/probes/FLPRGateAsm.lua`, `FLPRCampaignPolicy.lua`, `FLPRInventoryAsm.lua` | `cir.tp2` copies them to `override` |
| Configurator | `tools/randomiser_config.py`, `tools/flir_config/` (including `ui/`) | run from the mod folder after installation |

Not shipped: `docs`, `tests`, fixture/QA helpers in `tools/`, the lab automation in
`tools/lab/` (`FLPRQA.lua`, `M_FLPRQA.lua`), the unused `FLPRDrop`/`FLPRHeld`/`FLPRLife` modules
and the `tools/probes/FLPRDrop.lua` diagnostic.

Item Randomiser's own installer (Mode 1, Mode 2 and the components CIR does not port),
its scripts, dialogues, readmes and the lists only it reads are not part of CIR. They
stay with Item Randomiser: in its 8.1.1 release and tag and in this repository's history.

## Installation: `cir.tp2`

The randomiser is `DESIGNATED 0`, `LABEL ~cir-randomiser~`, no prompts; the optional
tweaks follow below. Preconditions of the randomiser, each with its reason:

- `GAME_IS ~bgee bg2ee eet~`: BG:EE (with or without Siege of Dragonspear), BG2:EE and
  EET (see "Campaign starts"); the classic games have no EEex. All in-game evidence so
  far is EET on BG2:EE 2.6.6 with EEex 1.2. The native hooks themselves are found in
  the running executable (see "Engine versions").
- No unmerged DLC (@101506): a non-empty `sod-dlc.zip` in the game folder or a non-empty
  ZIP file in `dlc/`. The engine loads such an archive on top of the game's own files;
  WeiDU 249 does not read it, so the patched `UI.MENU` and `BALDUR.GAM` would come from
  the game's own copies and hide the DLC's (Siege of Dragonspear's start menus and its
  campaign). DLC Merger leaves an empty `sod-dlc.zip` behind, which passes. WeiDU cannot
  look inside a ZIP file, so other ZIP files in the game folder (downloaded mods) and
  DLC in the user folder (`home:/dlc`) are not checked.
- EEex present (`override/M___EEex.lua`, present in every EEex version).
- LuaJIT active (exactly one `LuaPatchMode=REPLACE_INTERNAL_WITH_EXTERNAL` line in
  `InfinityLoader.ini`): the runtime uses the `io` and `os` libraries. Checked in the
  component body so a missing prerequisite fails the run visibly.
- No Item Randomiser Mode 1 or Mode 2 install (`flrandomiser1.mrk`, `flrandomiser2.mrk`).
  Those remove originals at install time and run their own assignment.
- Not together with Item Randomiser's component 560. It raises the Duergar Spell Shield stock at install
  time, so a campaign with the configurator's `more_spell_shield` option off could no
  longer have the original stock.
- No separately staged runtime: an `override/M_FLPR.lua` is refused unless WeiDU.log
  records `cir.tp2` itself, so a development lab (or Item Randomiser's unreleased
  component 1500) is never overwritten while a re-install over CIR still works. A guard that only tested for the file would
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
   `FLPRUI_NewGame(`, every `startEngine:OnImportCharacterButtonClick(` becomes
   `FLPRUI_ImportCharacter(` and every `createCharScreen:AcceptCharacter(` becomes
   `FLPRUI_AcceptCharacter(`. New Game and character acceptance must exist at least
   once; the import is optional (a UI without an import button starts no game that
   way). Any other call shape of the three fails the install, because an unhooked path
   would start an unrandomised campaign silently. A `UI.MENU` that already calls
   `FLPRUI_` functions also fails: an uninstall would restore those calls without the
   adapter. So does one that defines a hooked method (`function
   startEngine:OnNewGameButtonClick(`, with any whitespace after `function`): the patch
   would rename the definition into a global `FLPRUI_` function that replaces the
   adapter. Multiplayer `connectionScreen:OnNewGameButtonClick` and character creation's
   own `createCharScreen:OnImportCharacterButtonClick` (a character for a game already
   begun) are left alone. Verified call shapes: the reference EET `UI.MENU`, LeUI 4.9.1,
   Infinity UI 1.17 and the EEUITweaks 4.0.7 chargen fragments.
4. Add the GLOBAL `FLPR_FRESH = 1` to the `BALDUR.GAM` new-game template by moving its
   variable table to the end of the file and appending the marker. This is the exact
   layout the native fixture used in every live test. An existing `FLPR_FRESH = 1` is
   accepted unchanged; another value or a duplicate fails. Every start of the game
   (each campaign, the tutorial, the Black Pits) shares this template; the runtime
   captures only a start that the applied configuration names, so the marker stays inert
   in the others.
5. Write `override/flrandomiser3.mrk`.

Until a configuration is applied (`randomiser-campaigns/next.lua` absent), the adapter
passes New Game, character import and character acceptance straight to the engine: games
start unrandomised, as before. Once applied, New Game and character import go through
`FLPR.NewGame` and `FLPR.ImportCharacter` (see "Campaign starts"). Whenever the
new adventure cannot be randomised, the engine's two-button popup says why and offers
"Start without randomiser" or Cancel; an adventure never starts unrandomised silently
(see "Player messages"). The adapter detects the
configuration with `io.open`; the engine's internal Lua has no `io`, so it falls back to
`loadfile`, and a configuration that exists but does not compile still counts. Without
both functions it cannot see a configuration and passes through; LuaJIT, which the
component requires, provides both.

### Optional tweaks (9000-10300)

Item Randomiser 8.1.1's tweaks that Christopher and CEBG's default recipe use, under their
original numbers and labels `cir-cespenar-soa-items`, `cir-undead-scrolls-all`,
`cir-undead-scrolls-most`, `cir-duergar-merchants` and `cir-watchers-keep-statues`. They
need neither EEex nor the randomiser. The BG2 ones require `GAME_IS ~bg2ee eet~` (their
files and string references are Baldur's Gate II's); the scroll one `ENGINE_IS ~bgee
bg2ee~`. Classic games keep Item Randomiser 8.1.1. Gameplay is unchanged from 8.1.1,
except that Cespenar no longer offers a second recipe for a dragon scale he already has
one for; the implementation changed where 8.1.1 relied on file shapes:

- **9000 Cespenar can forge SoA items** (`d/botsmith.d`, `baf/botsmith.baf`). Same
  recipes, texts and `flItemMaker` global. His closing state now has `WEIGHT #-1`
  instead of 8.1.1's `ADD_STATE_TRIGGER` on his greeting (state 2), so the greeting
  stays untouched. The item-giving blocks go to the top of `BOTSMITH.BCS`, where a block
  another mod adds can never run before them. State 4 (looking through belongings), 10
  (gold short) and 11 (the forging cutscene) are the original game's own; the way back
  to his recipes copies state 4 as installed before this component, so recipes another
  mod adds later are offered only when no SoA part is carried. On the Enhanced
  Editions Cespenar forges Shadow Dragon Scale himself, and 8.1.1 offered its own recipe
  for it first. Now the installer looks for a recipe that takes a dragon scale
  (`TakePartyItem…` or `DestroyItem` of `scaler` or `scaleb` among the actions of
  `BOTSMITH.DLG` as installed before this component); for such a scale it compiles
  `d/botsmith.d` with that scale's test set to `False()` and prints a note, so only his
  recipe remains (Christopher, 2026-10-17: drop the duplicate, decided by the installed
  game). On the Enhanced Editions that is the Shadow Dragon's; the Red Dragon's stays.
- **10100 / 10200 Protection from Undead scrolls** (`lib/tweaks.tpa`). Removes `SCRL09`
  from every store that sells it, or from each with a chance of nine in ten (8.1.1's
  `RANDOM(1 100) < 90` was 89 in 100), and prints how many stores it changed. It uses
  WeiDU's built-in `REMOVE_STORE_ITEM`: WeiDU 249's `DELETE_STORE_ITEM`, which 8.1.1
  used, leaves the drinks table's offset unchanged when that table follows the sale
  table. In the lab's EET stores that sell the scroll, that table is empty there
  (`25SPELL`, `25SPELL2`, `HIGHHEDG`, `STO0703`) or comes first (`RIBALD`), so 8.1.1
  did no visible damage; a mod store with drinks after its wares would be corrupted.
  Files that cannot be read as STOR V1.0 stores are left alone and named.
- **10210 Duergar merchants** (`d/udduer01.d`). A guest of the drow who is not
  disguised gets the new greeting with both stores, as in 8.1.1 (state 12 gets
  `Global("DrowGuest","GLOBAL",0)`). The "nothing new" answer for guests is now a copy
  of state 14 with both stores, instead of 8.1.1's `ALTER_TRANS` by transition number
  and `EXTEND_TOP` on state 14, which would have hit other transitions after another mod
  extended it. State 14 itself stays as it is.
- **10300 Watcher's Keep statues** (`lib/tweaks.tpa`). 8.1.1 replaced every
  `Global("SetDifficulty","AR3001",0)` with `False()`, which also stops EE Fixpack's
  spawn activation: EE Fixpack (Alpha 3 to Beta 2) switches off every spawn point in `AR3001.ARE`
  and switches four of them on in a script block that tests the same variable. By that
  reading of both mods' code (not checked in the game), 8.1.1's 10300 installed after EE
  Fixpack leaves the level without spawns. The port disables only the difficulty adjustments: blocks that use
  `SetDifficulty` and destroy a `gorsta` statue, by adding `False()` as their first
  condition. Other statue-destroying blocks stay and are counted in a note. If every
  such adjustment already has a top-level `False()` or none exists, it changes nothing
  and says so; 8.1.1's predicates on SCS markers and classic SCSII/Tactics components
  are therefore gone. SCS 35.21's component 8040 ("Tie difficulty of level-dependent
  monster groupings to the difficulty slider") makes 8.1.1's whole-script replacement
  itself, so with EE Fixpack the level has no spawns whatever CIR does; that is between
  those two mods.

CIR refuses 9000 and 10210 when Item Randomiser's own copy is installed (both would add
their dialogue and, for 9000, hand over items twice). The scroll and statue components
need no such guard: a second run finds nothing left to change.

## Configuration, uninstall and re-install

After the whole mod stack is installed, the player scans the game (`scan`, `world`) and
applies a configuration from the editor with the game closed (`ui ... --apply-game`). Apply
writes `randomiser-campaigns/` data, campaign-aware dialogue/script/spell resources and the
`BGEE.LUA` journal in `override`, and appends strings to `lang/<language>/dialog.tlk`
(and to its `dialogF.tlk` under the same numbers, where the language has one).
WeiDU does not track these writes. `activation.validate_runtime` accepts CIR's
installation as well as the hand-staged lab layout.

The quest and dialogue changes of the default configuration are made by `cir.tp2` itself
(`lib/campaign_patches.tpa`): the pool, altar and book reward scripts (BLPOOL, TROALT,
PPBOOK), Unshey's conversation and journal entry, Glaicus's flail-heads conversation and
the TotSC tome stand-in. Each is found by meaning (the block that creates the item, the
reply that takes it, the state that hands over the first head) and split under a GLOBAL
that only the runtime sets, so other adventures and other mods' edits keep working.
Glaicus's redirected replies keep their original text, action, journal entry and flags.
His other branches that give the first head (when the troll leader is already dead) keep
their text and, in an adventure that randomises the heads, hand over every head he holds
(Chris, 2026-10-17: no new text).
A file that cannot be changed unambiguously stays as installed; the installer prints a
note (setup.tra @101520-@101528) and `override/FLPRPAT.lua` records the reason, which the
editor shows instead of offering the option.

Uninstalling CIR restores `UI.MENU`, `BALDUR.GAM` and those quest files byte for byte
and removes every file it created. WeiDU keeps the appended game-text
strings, unused, as it does for every mod; a re-install finds and reuses them. WeiDU does
not track folders, so the `randomiser-campaigns` folder it created stays behind empty.
Uninstall cannot remove what the configurator wrote; see B2. Re-installing over the installed component
and an uninstall and install in one WeiDU run leave the game byte-identical, also with an
applied `randomiser-campaigns/next.lua`;
so does WeiDU's temporary uninstall and re-install of CIR behind a reinstalled earlier
mod.

## Campaign starts

The configurator identifies the game as WeiDU's `GAME_IS` does
(`tools/flir_config/platform.py`): EET by `override/EET.flag`, BG2:EE by `OH6000.ARE`,
BG:EE by `OH1000.ARE`. It refuses any other game, as the installer does. It offers the
presets whose tables the game has (the BG1 presets on BG:EE, with Siege of Dragonspear's
areas also the SoD ones; the BG2 ones on BG2:EE; all on EET; see
[SoD lists](../sod-lists.md)) and recommends that game's `-original` preset. BG:EE and BG2:EE keep the
`BGEE.LUA` journal table in their archives, EET in `override`; the export reads either.

At New Game or character import `M_FLPR.lua` reads the start the engine selected: the
campaign name when `startEngine:GetCampaign()` is 3 (`CInfGame`'s `m_sCurrentCampaign`),
else the Black Pits, the tutorial or Throne of Bhaal by their flags, else the game's
main campaign. It looks that start up in `randomiser-campaigns/next.lua`, which returns
this table (written with every string escaped byte by byte):

```lua
{main="BG1", schema="flir-next-v2", starts={BG1="<revision>", SOD="<revision>"}}
```

| Game | Starts with a revision | `main` (the start without a campaign row) |
| --- | --- | --- |
| EET | BG1, SOD, SOA, TOB (all `CAMPAIGN.2DA` rows) | none |
| BG:EE | BG1, SOD (a row) | BG1 |
| BG2:EE | SOA, TOB (by its flag) | SOA |

A start without a revision (the Black Pits, the tutorial, a start the configuration
reaches nothing in) asks before it starts unrandomised (@1702). A pointer written
before campaign starts were configurable holds one revision, which the runtime reads as
a BG1 start. New Game records the start and character creation's mode
(`createCharScreen:GetEngineState()`: 1 New Game, 7 import); character acceptance that
another start or mode reached (the tutorial, pre-generating a character, party creation)
after a cancelled New Game does not capture.

Apply writes one revision for each start (`tools/flir_config/starts.py`). The start that
reaches the whole game keeps the whole configuration; a later start keeps only the
rewards and places it reaches:

| Game | Start | Reaches |
| --- | --- | --- |
| EET | BG1 | everything |
| EET | SOD | Siege of Dragonspear, Shadows of Amn, Watcher's Keep, Throne of Bhaal |
| EET | SOA | Shadows of Amn, Watcher's Keep, Throne of Bhaal |
| EET | TOB | Watcher's Keep, Throne of Bhaal |
| BG:EE | BG1 | everything |
| BG:EE | SOD | Siege of Dragonspear |
| BG2:EE | SOA | everything |
| BG2:EE | TOB | Watcher's Keep, Throne of Bhaal |

An area tells its part by its name. On EET `BG####` and BG:EE's own `OH` areas belong
to BG1, `BD####` to SoD, `AR30##` to Watcher's Keep, `AR4000`-`AR6999` to ToB and other
`AR` and `OH` areas to SoA. On BG:EE `AR` and `OH` areas belong to BG1 and `BD` to SoD;
on BG2:EE the BG2 rules apply and `BG` and `BD` names are unknown. A creature template
is where the areas that place it are. Any other record (a shop, a script, a
conversation, an added item) is where the table that lists it puts it: BG1, SoD, or
all of BG2, so a ToB start leaves BG2's shops and scripts as they are. A record
whose part is unknown stays out of later starts. What remains is checked as the whole
configuration is: a reward whose tier has no place left (without tiers, rewards beyond
the places' capacity) and a row the runtime check names are left out too. A reward left
out stays as the game has it; nothing is put where the start cannot go. A start with
nothing left gets no revision. Apply's message names the randomised starts and why
any other is not.

## Player messages

Texts are `in-game.tra` @1700-@1706 (see Installation, step 2); `M_FLPRUI.lua` keeps the
English text as a fallback. Failure details go to `randomiser-campaigns/last-error.txt`
(latest failure only) and the EEex console; the messages name that file.

| Situation | What the player sees |
| --- | --- |
| Configuration applied, game started without InfinityLoader/EEex | New Game popup: not running, start with `InfinityLoader.exe` |
| The runtime could not start (a native hook location was not found exactly once, its code differs, or the executable could not be read) | New Game popup: this game version is not supported yet |
| The runtime could not start (missing or damaged configuration files, or Item Randomiser's Mode 1 or Mode 2 installed after CIR) | New Game popup: could not randomise this adventure |
| A New Game or character import of a start the applied settings have no revision for (Black Pits, tutorial, a start they reach nothing in) | New Game popup: the settings have nothing to randomise in this campaign |
| Capture fails at character acceptance (for example a later mod replaced `BALDUR.GAM`) | Popup: could not randomise; Cancel keeps character creation open for a retry |
| Capture succeeded | Message log, once, when the world first appears: items are randomised |
| The runtime stops in a randomised adventure, or one is loaded while it cannot run | Message log, once per loaded game: stopped working, unplaced items may be missing |

Each popup offers "Start without randomiser" (the engine's own New Game or character
acceptance, unrandomised) and Cancel. Capture is atomic: every GLOBAL written while
capturing is restored and the source gate is disarmed when any step fails, so starting
without randomiser leaves no partial campaign. Messages appear in the engine's
`POPUP_TWOBUTTON`, whose text area holds about four lines; translations should stay
similarly short.

## Translations

The installer offers English and Polish (Item Randomiser's own translation; CIR's new
lines, the player messages @1700-@1706 and the journal texts @1650-@1653, fall back to
English there). The configurator writes text into the game's language (`lang/<code>`),
whatever language CIR was installed in:

- **Reward texts of the extended pools** (2026-10-18): the 20 lines Item Randomiser
  rewords where a BG2 quest's text names its reward, read by the game's language from
  `languages/<folder>/` (`tools/flir_config/translations.py`), with a translation's
  female wording in `dialogF.tlk`. English, Polish and German (written for this; Chris
  reviews it). In other languages those routes are left out with the reason. Open: the
  other BG2:EE languages (Czech, Spanish, French, Italian, Korean, Brazilian Portuguese,
  Russian, Ukrainian, Chinese). Call for translators, with the exact lines:
  [translating-reward-texts.md](../translating-reward-texts.md).
- **Still English only:** the Kangaxx option (`hlskull.tra`, `in-game.tra`
  @1500-@1504; Polish has them) and the cursed disguise option's names and
  descriptions. In another language Apply refuses them ("Original quest options
  currently require English game text", "Original cursed-disguise text currently
  requires English"); the configuration check does not leave them out first. Next: read
  them through `translations.py` as well and leave them out in the check where a
  language has no translation.

## EEex dependency

Required: EEex with LuaJIT (CEBG's `feature:eeex:mandatory-components`, EEex v1.2
components 0-8). Tested in game: EEex 1.2 on BG2:EE 2.6.6.0 only. EEex v0.11's Lua
sources do not define `EEex_GetImageBase`, which the hook search calls; the installer
does not refuse v0.11 because no minimum version has been established, and the runtime
fails closed when an API or the executable's code does not match.

## Engine versions

`prototype/FLPRSite.lua` holds every native hook location as a byte pattern ("??" for
call targets and addresses that move between builds) plus the bytes the hook expects.
Once per session it reads the executable InfinityLoader started (`Baldur.exe`, else the
loader's other `ExeNames`), confirms its PE headers are the ones mapped in memory,
searches the code section (about 0.1 s) and keeps the result in
`FLPR_ENGINE_SITES`. A location is used only when its pattern occurs exactly once and
the bytes at the hook are unchanged in memory (no other mod hooked the same
instructions); call targets and RIP-relative globals are taken from that code, and
struct offsets are checked against EEex's bindings (`EEex_OffsetOf`). Anything else
stops the runtime with `<PREFIX>_CODE_MISMATCH:<location>:<reason>` and the "not
supported yet" popup; the game and EEex keep running.

EEex's `extra_pattern_dbs` are not used on purpose: InfinityLoader treats a pattern it
cannot find in any of those files as an initialisation failure and then does not start
EEex at all, so an unknown executable would break every EEex mod.

`tools/engine_patterns.py <Baldur.exe> ...` checks the same patterns offline. Every
location matches exactly once with the expected bytes in BG:EE and BG2:EE 2.6.6.0 and
2.7.3.0 (Steam builds and the CEBG reference game); BG:EE and BG2:EE share the code
layout within a version. Checking a new game version starts there.

## Relation to Item Randomiser

| Item Randomiser components | With CIR |
| --- | --- |
| 1100, 1200 (Mode 1), 1300, 1400 (Mode 2) | Exclusive: `cir.tp2` refuses to install after them. |
| 560 More Spell Shield scrolls | Exclusive: `cir.tp2` refuses to install after it; the configurator offers it per campaign. |
| 500, 510, 520, 530, 540, 570 | Marker-only inputs to 1100-1400; inert with CIR and not blocked. Their configurator equivalents are `totsc_tome`, `unrandomized_percent`, `kangaxx`, `scrolls`, `flail_heads`, `cursed_disguise`. |
| 5005-5085 | Require Mode 2, so unavailable. |
| 9000, 10100, 10200, 10210, 10300 | Ported into CIR under the same numbers; CIR refuses its 9000 and 10210 after Item Randomiser's. |
| 9050 Gromnir | Not ported (unused by CEBG); independent of CIR. |

Item Randomiser 8.1.1 does not know CIR, so WeiDU does not refuse its Mode 1, Mode 2 or
560 when they are installed after CIR. CIR refuses them itself: the configurator does not
apply settings while any of them is installed (the Mode 1 and Mode 2 markers, or
`randomiser.tp2` component 560 in `WeiDU.log`), and the runtime does not start while a Mode 1
or Mode 2 marker exists, so New Game shows the "could not randomise" popup and
`last-error.txt` names the marker. A 560 installed after Apply is not detected in play; it
only adds Spell Shield scrolls to the Duergar store, and the next Apply refuses.

## Release ZIP

`tools/build_release.py` builds `<archive root>-v<VERSION>.zip` (`cir-v1.0.0.zip`) and a
JSON manifest from a
commit's Git objects, never the working tree. Output is byte-reproducible: sorted file
entries without directory entries, the commit time as every timestamp, Unix regular-file
attributes (mode 644), Deflate level 9 and the commit ID as the archive comment. Text
files get the CRLF conversion that Git's `core.autocrlf=true` checkout applies, which is
what the published 8.1.1 asset contains (the configurator also reads `.tra` text with
its authored line endings). `.gitattributes`, symlinks and submodules are refused rather
than guessed. `--verify` rebuilds and compares an existing ZIP byte for byte.

The builder was checked against the published 8.1.1 asset: built from `f4a9dfb` with the
8.1.1 file list, the 662 files match it byte for byte; only the ZIP container differs.
The CIR package built from `c80c17e` (`cir-v1.0.0.zip`) has 92 files, 3,924,122 bytes
uncompressed, a largest entry of 1,472,788 bytes
(`languages/iconv/libiconv-1.9.2-1-src.7z`), a maximum compression ratio of 11.9, a
path depth of five and one TP2 (`cir/cir.tp2`). That is within the archive limits CEBG
uses for the 8.1.1 asset (1,024 entries, 4 MiB per entry, 16 MiB in total, ratio 100,
depth 8). `VERSION` 1.0.0 stands until the first release; nothing is published.

`tools/build_standalone.py` writes that ZIP unchanged together with the Windows
standalone package `cir-v<VERSION>-windows.zip` from the same commit: the `cir/` folder,
`cir/cir-configurator.exe` and `setup-cir.exe`. The executable is the Rust configurator,
built by cargo from the commit's export with the static C runtime; the same tools build
the same bytes, and `--verify` compares. `setup-cir.exe` is WeiDU 249.00 for x86-64, the
installer tests' build, pinned by size and SHA-256 as CEBG pins it. Players unpack the
package into the game folder, install with `setup-cir.exe` after their other mods and
double-click `cir/cir-configurator.exe` (`configurator/README.md`, "Windows package").
The builder refuses a mod ZIP beyond CEBG's limits; the executable (5,016,576 bytes) is
over the 4 MiB entry limit, so it is only in the package. Built from `4ecbd03`, the mod
ZIP has 119 files, 4,230,043 bytes uncompressed (2,663,858 as a ZIP) and a path depth of
seven (`cir/lists/presets/<preset>/items/<pools>/<campaign>.2da`; CEBG allows eight);
the package has 121 files (5,812,381 bytes).

## What CEBG needs

- A new mod: component `0` of `cir/cir.tp2`, label `cir-randomiser`, language 0, no
  prompts; its own release asset with SHA-256 and length.
- Requires `GAME_IS ~bgee bg2ee eet~` (CEBG's EET passes) and
  `feature:eeex:mandatory-components`.
- Excludes Item Randomiser 1100, 1200, 1300, 1400 and 560. Presets with CIR should drop
  Item Randomiser's 500-570, which would only be inert.
- The tweaks of the default recipe from `cir.tp2` instead of 8.1.1: 9000, 10200, 10210
  and 10300, by the labels above, each without prompts. The recipe's exclusion of 10300
  with Stratagems can stay; without it, 10300 installs and reports that SCS already
  keeps the statues.
- Order: after every component that writes `UI.MENU` or `BALDUR.GAM`; the end of the
  BG2 run is simplest. The installer cannot see later overwrites, so after the stack
  completes CEBG should check that `override/UI.MENU` contains `FLPRUI_NewGame(` and
  `FLPRUI_AcceptCharacter(` (and `FLPRUI_ImportCharacter(` where the UI has a character
  import button), `override/BALDUR.GAM` contains `FLPR_FRESH`, and
  `override/flrandomiser3.mrk` exists, and that no Item Randomiser Mode 1, Mode 2 or 560
  was installed after CIR.
- A post-install step: scan, configure and apply with the game closed (needs B1).
- Uninstall or recovery: remove applied settings first (needs B2). Re-installing CIR
  itself is safe.

## Blockers before a public release

- **B1 Python.** The scanner, editor and exporter are Python 3.10+ (standard library
  only). Players must not need Python. Decided (2026-10-17): a CEBG version, and a
  standalone version with its own executable written in Rust for everyone else; the
  Rust configurator is being built in a separate session. The standalone version is
  the package of `tools/build_standalone.py` (see "Release ZIP").
- **B2 Uninstall after Apply.** Closed by decision (2026-10-15): a game that used the
  randomiser and then uninstalled it is not ours to keep working. The default
  configuration's quest and dialogue changes moved into the installer anyway (see
  "Configuration, uninstall and re-install"), so its Apply writes only
  `randomiser-campaigns/`, and Apply copies only the files its latest export lists. The
  extended pools' reward scripts, dialogues and containers, the Deck of Many Things key
  spell (`SPIN621.SPL`) and the Kangaxx option are still written by Apply. Since
  2026-10-18 Apply recognises each reward route by its meaning in the game's own files
  (BG2:EE's and EET's layouts, other mods' added replies and blocks) instead of by the
  hash of the reviewed EET file, and the configuration check runs the same recognition:
  a route it cannot prepare is left out with its reason, so Apply never refuses a route
  the check accepted. Copies of a route's reward replies in other dialogues (party
  members' interjections) are gated like the route's own. A container quest binds the
  container of its area that holds its whole treasure; revisions exported before keep
  what they recorded, and Apply refuses only where the runtime's start check of such a
  revision would fail because the game changed a file it needs. The old pins bound Samia's places to the container before her chest
  in `AR1202`, named Safana's area as EET does (so it did not bind on BG:EE) and took the
  table's `AR1302` for the golems, whose treasure is in `AR1303` (so they never bound).
  Still pinned to the reviewed EET bytes: the Kangaxx dialogue and
  the creature files and records of the rewards an NPC carries (`SCYARRYL`, `TRGENI01`,
  `LOUTCAPT`, `AMCLER01` and `UDLESA`).
- **B3 Failure messages.** Resolved; see "Player messages".
- **B4 Test hooks in the runtime.** Resolved: the Remote Console poll wrapper, the chapter
  screen render listener, the QA driver and its `qa-status.json` moved to the lab-only
  `tools/lab/M_FLPRQA.lua` and `tools/lab/FLPRQA.lua`, which neither the TP2 nor the
  package contains; a lab stages both into `override`. The runtime exposes read-only
  `FLPR.Core` and `FLPR.Storage` for them. The data folder is now `randomiser-campaigns/`.
- **B5 Coverage.** Resolved offline (2026-10-18): CIR installs on BG:EE (with or
  without Siege of Dragonspear, merged), BG2:EE and EET, and every campaign start gets
  its own revision, also the direct SoD, SoA and ToB starts on EET (see "Campaign
  starts"). Open: the in-game check on 2.7.3.0 with the first lab test: the import
  starts (SoD on BG:EE, SoA on BG2:EE), the engine bindings the start detection uses
  (`GetCampaign`, `m_sCurrentCampaign`, the mission-pack flags, `GetEngineState`), and
  that the tutorial stays unrandomised. Not covered: a UI that starts a game through
  `startEngine:OnPregenSelectButtonClick` (absent from the stock UIs) starts it
  without asking.
- **B6 Engine 2.7.3.0.** Ported (2026-10-18): the hooks no longer use fixed 2.6.6.0
  addresses but find their locations in the running executable (see "Engine
  versions"), verified offline on BG:EE and BG2:EE 2.6.6.0 and 2.7.3.0. Open: the
  first in-game check on 2.7.3.0 (EET, BG:EE, BG2:EE).

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
  `totsc_tome`); every other option off. It is not an EET-only preset: the configurator
  recommends each game's own (`bg1-original`, `bg2-original`, `eet-original`).
- **The extended pools' reward routes** (the quest rewards of Item Randomiser's
  install-time Mode 2) are built in this mod.

On 2026-10-15:

- **A new mod.** Item Randomiser is Wisp's mod; the new randomiser becomes a new mod
  built on it, not a v9.0 of it. This fork stays the maintained original. The packaging
  above (component 1500 in `randomiser.tp2`, the "New/Old randomiser" labels) moves to
  the new mod.
- **Uninstall**: breakage after a used randomiser is uninstalled is not ours to prevent
  (B2 closed).

On 2026-10-17:

- **Name**: Chriz Item Randomiser (CIR), in its own repository under Chrizhermann.
- **Glaicus when TorGal is already dead**: keep it simple, no new or dynamic text. His
  other branches that give the first head keep their line and hand over every head he
  holds, only in adventures that randomise the heads.
- **Repository and licence**: public, with a reuse licence, and named
  `chriz-item-randomiser`, this fork's name. CIR's own code and texts are under the MIT
  licence; the material from Item Randomiser is credited and keeps its author's terms
  (`LICENSE`).
- **Repository takeover**: CIR takes over this repository. Planned for CIR's first
  release: `main` becomes CIR and the Item Randomiser 8.x code moves to a branch. The
  published 8.x releases and tags stay, because CEBG's current recipes install from 8.1.1.
- **Item Randomiser's other components**: the ones he and other players use move into
  CIR: 9000, 10200 (with its alternative 10100), 10210 and 10300, the components of
  CEBG's default recipe. The others (9050 Gromnir, the Mode 2 creature-group unequipping)
  are discussed later. Wisp is credited as the author of the groundwork, without making
  the licence more complicated.

Open:

- **B1 route** (options above), now constrained to Python-free for both versions.
- **Default presets**: a friend of Christopher's has distribution ideas for BG1 and
  partly BG2, to be analysed and built in, at least as a template.

## Verification

- `tests/Test-CampaignRuntimeInstaller.ps1` (WeiDU 249, synthetic EET, BG:EE and BG2:EE
  games; 202 checks for the randomiser, from the working tree and from the built ZIP with
  `-PackagePath`): BG:EE with a merged DLC, BG2:EE, a UI without character import, the
  refusals of another game, of unmerged DLC (in the game folder and in `dlc/`) and of
  unhookable import calls, install, uninstall,
  re-install, re-install over the installed component, uninstall and install in one run,
  re-install behind a reinstalled earlier mod (also from the install list alone),
  already-patched inputs, override-resident and archived `UI.MENU`/`BALDUR.GAM`, UI-mod
  call shapes, redefined UI methods, an applied `next.lua`, the exclusion of Item
  Randomiser's Mode 1, Mode 2 and 560, missing prerequisites, rollback of failed installs
  and the Polish language (on an Enhanced Edition game WeiDU converts its texts to UTF-8
  and uninstalling restores them; the English texts stay as written), with KEY, BIFF,
  TLK and unrelated override files byte-checked, and the seven
  messages appended to the game text with matching references in `M_FLPRTXT.lua`. The
  quest changes run on hand-written scripts and dialogues that WeiDU compiles with
  minimal IDS tables (`tests/campaign_patch_fixtures.py`), archived and in override:
  each split script equals its expected compiled form, the dialogues are checked by
  structure, and files that give the reward twice or not at all, were already split,
  ask for another item, erase an unknown journal entry or hand over no flail head stay
  byte-identical, with the reason recorded and printed. No game resource is used.
- The optional tweaks in the same suite (`tests/tweak_fixtures.py`; 26 of 228 checks):
  9000, 10100, 10210 and 10300 installed together on EET and on BG2:EE, with
  Cespenar's and the merchant's dialogues checked by structure (Cespenar's state 4 with
  another mod's recipe, the closing state first in trigger order, the merchant's state
  14 untouched), Cespenar's script and AR3001 equal to their expected compiled forms
  (an EE Fixpack style block and another mod's statue block untouched, a split
  adjustment whose `False()` sits inside `OR()` still disabled), stores with their
  tables in different orders and a store that cannot be read, the texts added, the
  notes printed, uninstall restoring every byte and a reinstall identical. Also 9000 on a
  Cespenar who already makes Shadow Dragon armour (only the Red Dragon's scale leads to
  the component's recipe, with a note), 10200 on 30 stores, 10300 on a script SCS
  already changed and on one without the adjustment,
  the refusal after Item Randomiser's own 9000 and 10210, and Polish texts converted on
  an Enhanced Edition game. The installer layouts were taken from the reference EET lab's
  `BOTSMITH`, `UDDUER01`, `AR3001` and stores, read without changing them.
- WeiDU `--parse-check` of `cir.tp2`, the four `lib/*.tpa` and the two `d/*.d` (a BAF
  needs a game's IDS files; the suite compiles `baf/botsmith.baf`).
- `tests/test_release_package.py`: EOL parity with Git, determinism, refused inputs,
  manifest integrity, coverage of every file `cir.tp2` installs or the configurator
  reads, and English texts in UTF-8; for the standalone package its layout, the
  unchanged mod ZIP, WeiDU's pin, the configurator's source export and cargo call, the
  executables it refuses (Visual C++ runtime, build folders) and CEBG's limits on the
  mod ZIP.
- `configurator/parity/package_check.py` on the package built from `4ecbd03`: unpacked
  into the installer suite's synthetic BG2:EE, BG:EE and EET games, its `setup-cir.exe`
  installs component 0, and its `cir/cir-configurator.exe`, started without arguments,
  scans and edits as the reference does and applies (32 checks).
- `tests/test_runtime_ui_adapter.py`: adapter routing and the not-running popup in Lua 5.1,
  Lua 5.3 and LuaJIT; `tests/test_campaign_native_host.py`: atomic capture, every player
  message and its start/cancel choice, also when Item Randomiser's Mode 1 or Mode 2 was
  installed after CIR, the revision each start picks from `next.lua` (old and new
  pointers), the import start, and character acceptance reached by another start or
  creation mode; `tests/test_lab_qa_module.py`: the lab-only module.
- `tests/test_campaign_starts.py`: the game told apart as WeiDU does, presets per game,
  area parts, and the revisions Apply writes for each start on EET, BG:EE and BG2:EE
  (only reached rewards and places, a start short of places, starts with nothing left,
  the exported `next.lua`).
- `tests/test_runtime_activation.py`: the configurator accepts the installed runtime and
  refuses Item Randomiser's Mode 1, Mode 2 or 560 installed after CIR.
- The full Python suite, with `keystone-engine` 0.9.2 and `unicorn` 2.1.4 from a local
  test environment on `PYTHONPATH` and `FL_IR_TEST_TEMP_ROOT=%TEMP%\flir-t`: 1372 tests
  pass; 2 skip because optional lab caches and evidence are not in the source tree.
- Not verified: any in-game behavior of the TP2-installed runtime, the adapter in the
  engine's own Lua, UI mods in a running game, EEex versions other than 1.2, and
  anything on BG:EE or BG2:EE or in a start other than BG1.
