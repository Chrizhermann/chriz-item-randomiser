# Mode 1: reward before visiting its original area

The integrated fixture passed native New Game, early reward pickup, source
suppression, selected/excluded campaign reload, returned-item and interrupted
confirmation checks on 27 September. Several configured sources across two areas
also passed, followed by three existing BG1 rewards across three real source areas.
This is a working prototype for container sources on the pinned Windows build;
general source support and the player-facing configuration UI remain unfinished.

## Implementation

- `FLPRCore.lua`: source phase 5 (saved value 6) means protected before access.
  Phase 2 still means observed removal. A live guard authorizes early delivery;
  save restoration requires rebinding it. Existing assignments never reroll.
- `FLPRHost.lua`: prepares the guard between campaign capture and character
  acceptance, delivers rewards, then observes the original without replaying
  delivery. Ordinary visible sources retain their existing removal flow.
- `FLPRGat.lua`, `FLPRCampaignPolicy.lua`, and `FLPRGateAsm.lua`: install before
  area loading and select policy from the full saved campaign revision and source
  phase. Area, container, item record and payload now come from the campaign
  manifest and pristine ARE; they are no longer restricted to FLPRU1/FLPRI1.
  Each source has separate protection/removal state. They share five hook entry
  points and a pristine copy per area. Several saved revisions can coexist;
  each uses its own source indexes and protection state. The pinned BG2EE
  executable remains a prototype limit.
- The guard compares the complete authored ARE input and ties the result to the
  same area-load invocation and constructor inputs. Saved area records can move;
  matching an item name or original index alone would delete a returned copy.
  Confirmed removal disables further suppression. A save between removal and
  confirmation remains protected; absence alone never creates a removal receipt.
- `FLPRNat.lua`: creates the declared reward and observes removal. It does not
  demand or mutate the unvisited resource. Payload, owner and quantity checks
  concern player items, not the integrity of the disposable lab.
  Bootstrap checks item delivery eligibility before installing suppression.

## Native result

Selected campaign A (revision `1fff717d...`, seed `1724153183`) gave three rewards
to two synthetic creatures while FLPRU1 was unvisited. A ready save reloaded with
the same assignments. Ordinary creature deaths and pickup put all three rewards
in the player's inventory before first entry to FLPRU1. On entry, the guard
suppressed exactly the original FLPRI1, preserved FLPRX1 in its container and
FLPRI2 in the other container, and confirmed source removal on a later host tick.
The looted save reloaded with all three rewards and no restored original.

Changing only the next-run configuration produced excluded campaign B (revision
`e0ff63e8...`, seed `772708706`): two creature-source rewards, with FLPRI1 retained
in its original container. A/B switching in one process and reloading both in a
fresh process preserved their respective seeds, assignments and item ownership.
No reinstall was needed between campaigns.

The first two launches found a real bootstrap bug before hooks were installed:
`EEex_WriteLString` truncates binary data at NUL; `EEex_Write8` requires signed
bytes. The corrected copy preserves every byte, including NUL and values above
127. The focused regression uses those actual native semantics. One ready-load
request also used a display name instead of the required save-folder name; the
corrected request completed without changing mod code.

Evidence: `work/campaign-guard-native3-20260927/` (New Games and same-process
checks), `work/campaign-guard-native4-20260927/` (fresh process). Named saves are
59 (A ready), 60 (A looted), and 61 (B original). All game sessions quit normally
on the non-input desktop. The 25 staged files were restored and the reservation
released. The new saves require this fixture and runtime to be staged again;
their immutable campaign artifacts were retained.

After the binary-copy fix, all 893 Python checks passed with the declared test
dependencies. The twelve available PowerShell suites, Lua suite and 35 WeiDU
parse checks also passed. The legacy sentinel dependency check remains unverified
because its old decompiled-script fixture was removed with its lab; see
[repository checks](../tests/README.md).

### Returned items and interrupted confirmation

Campaign A's returned FLPRI1 survived save/reload in its original container after
confirmed removal (save 62). For the interruption case, host confirmation was
temporarily held while the native gate suppressed the original. Save 63 therefore
contains no original but still records protected source state 6. Reloading it
preserved all three collected rewards without replaying suppression or delivery.
A player-returned FLPRI1 saved in that protected state also survived (save 64).
Both interrupted saves passed again in a fresh process. Absence was never
misreported as a confirmed removal receipt.

Evidence: `work/campaign-guard-return-20260927/` and
`work/campaign-guard-return-reload-20260927/`. These use ordinary death, pickup
and drop actions on synthetic content; the held host poll is test instrumentation.

### Configured container

Campaign C (`8f516963...`, seed `1490592260`) binds
`are:flprv2/container/1/item/3`: FLPRX2 in the type-1 `FLPRV2 chest` at 1200,650.
It delivered all three rewards before visiting FLPRV2. Ordinary death and pickup
put them in the player's inventory. First entry removed exactly FLPRX2 from the
configured chest, preserved both FLPRI1 and FLPRX1 in the other container, and
confirmed removal. Save 65 (`IR Configured Source 0927`) reloaded with the same
seed, assignments and items, without repeating suppression.

The first launch exposed a wrong ARE item-count offset before hooks installed:
the count is a word at 0x76, not the vertex offset at 0x7C. The parser and its
synthetic fixture were corrected before the successful run. Evidence:
`work/configured-container-native-20260927/` (bootstrap failure) and
`work/configured-container-native2-20260927/` (successful run). All sessions quit
normally; all 28 staged paths were restored and the reservation released.
After the offset fix, the complete Python suite passed (903 tests). No new lab
tooling was added; the changes extend the runtime and its focused regressions.

### Several sources in one campaign

Revision `88e96509...` relocates FLPRI1 and FLPRI2 from two FLPRU1 containers,
plus FLPRX2 from FLPRV2. Seed `448563856` delivered all three before either area
was visited. Pickup succeeded. Visiting FLPRV2 first confirmed only source 3;
save 66 (`IR Multi Partial 0927`) preserved source states 6,6,3 and all rewards.
Reloading and visiting FLPRU1 removed the other two originals while preserving
FLPRX1. Save 67 (`IR Multi Complete 0927`) preserved states 3,3,3 and ownership.
Both saves passed in a fresh process, including first visiting the still-unvisited
area from save 66. A second New Game used seed `1900851915` and passed the reverse
visit order; this small pool happened to produce the same target mapping.

Evidence: `work/multiple-containers-native-20260927/` and
`work/multiple-containers-reload-20260927/`. Both sessions quit normally; all 28
staged paths were restored. The offline suite passed 910 tests. Review caught a
missing group reset in the New Game path; the fix and focused regression passed
before live testing. Full-catalogue performance has not been established.

### Changing the next-run selection

Revision `04283af3...` excludes FLPRI2 from the preceding three-source profile.
Its new campaign (seed `2056225973`) delivered and allowed pickup of the two
selected rewards, suppressed their originals, and left FLPRI2 in FLPRU1B.
Loading old save 66 in the same process restored its three-source selection,
original seed and collected rewards; first visiting FLPRU1 removed both of its
selected originals. Loading new save 68 (`IR Profile B 0927`) restored only its
two assignments and preserved the excluded original. Save 68 passed again in a
fresh process, without another suppression or delivery.

The fixture's `source-gate.lua` now accepts a list of immutable revisions to
preload (the previous single string still works). `next.lua` selects the next
New Game; saved campaigns select their own revision. Keep all revisions needed
by saves, and restart the game after adding one to the preload list. All profiles
must describe the same installed area resources. This removes the one-revision
restriction without a TP2 reinstall; automatic export and player UI remain open.

Evidence: `work/profile-switch-native-20260927/` and
`work/profile-switch-reload-20260927/`. Both sessions quit normally; all 28 staged
paths were restored and the reservation released. The full offline suite passed
917 tests. This change reuses the existing lab helpers.

### Configurator-produced settings

The CLI now exports reviewed campaign bindings into the runtime configuration
layout, retaining earlier exported revisions. Scanning the copied fixture
resources, preparing A/B profiles and exporting them produced revision
`5d6f5d37...` for the two-item selection. Its New Game (seed `669081018`) passed
early delivery, ordinary creature death/pickup, both original-source removals
and preservation of the excluded FLPRI2. No endpoint files were hand-edited.
Evidence is in `work/configurator-export-20260927/` and
`work/configurator-export-native-20260927/`. The game quit normally and all 28
staged paths were restored. The offline suite passed 921 tests; a subsequent
review reproduced a same-container duplicate-copy limitation and added an export
rejection with a passing focused regression. This remains candidate configuration
export for the supported subset, not a general mod installer.

### Existing BG1 content

The configurator exported three existing Randomiser items and two existing targets
from the installation scan, checking the selected current resources. No runtime
changes were needed. Revision `27b1c683...`, seed `1969154220`, assigned Flail +1
(`blun03`) and Chain Mail +1 (`chan02`) to Zargal, and Halberd +1 (`halb02`) to
Teyngan. This is a technical sample, not an approved new gameplay preset.

Both recipients received their rewards before any source area was visited.
Ordinary death and pickup actions collected all three. First visits removed the
selected originals from BG4701 container 0, BG5001 container 3 and BG5202
container 0, leaving their other loot present. Save 69 (`IR Real Early 0927`)
reloaded with the same seed, collected items and still-protected sources. Save 70
(`IR Real Complete 0927`) passed in a fresh process with all three collected
rewards, confirmed removals, and no repeated suppression or delivery. Reading
its saved areas independently confirmed the originals absent and other loot
retained. Unrelated record bytes are not claimed identical: the engine normalizes
some item flags and charges on load.

Evidence: `work/existing-bg1-sample-20260927/`,
`work/existing-bg1-native-20260927/`, and `work/existing-bg1-reload-20260927/`.
Area travel and death were scripted; this verifies delivery and pickup, not combat
balance. The test character died during the first source inspection, after the
early checkpoint. Reloading that checkpoint and temporarily enabling Story Mode
allowed inspection to finish; Story Mode was restored to 0 before save 70.
Both sessions quit normally, all 28 staged paths were restored, and the reservation
was released. No new lab tooling or production code was added for this sample.

The private test profile now has all five audio volumes at 0, persisted across
restart. Hidden desktops still play audio: when rebuilding this profile, mute
ambient, SFX, voices, music and movies with `Infinity_ChangeOption(id, 0, 7)` for
IDs 1, 2, 3, 4 and 22; verify with `Infinity_GetOption(id, 7)` before world testing.

## Continue

Build the authored `unvisited-creatures` fixture with `build_mode1_fixture.py`
using the existing copied inputs in `work/mode1-native-slice/fixture-input-v2`.
The prepared selected/excluded profiles are in
`work/campaign-source-guard-20260927/`. The existing test installation, private
profile, remote console and character helper are sufficient; rebuild the test
copy if necessary. No installer or game may overlap another BG task.

The completed one-shot staging helpers under `work/` show the setup used; do not
rerun consumed requests. The existing lab and helpers are sufficient.
Creature/store/script interception remains separate.
The fixture observes physical pickup but keeps delivery phase 4 (held); the
general lifetime observer is not integrated. Choose representative existing
content autonomously for technical tests. New gameplay selections, capacities
and fallback policies remain Christopher's choices.
