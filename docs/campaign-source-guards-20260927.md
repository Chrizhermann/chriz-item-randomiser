# Mode 1: reward before visiting its original area

The integrated fixture passed native New Game, early reward pickup, source
suppression, selected/excluded campaign reload, returned-item and interrupted
confirmation checks on 27 September. A second configured source also passed.
This is a working prototype for one configured container on the pinned Windows build;
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
  One guard and one selected revision per process remain the prototype limit,
  along with the pinned BG2EE executable.
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

## Continue

Build the authored `unvisited-creatures` fixture with `build_mode1_fixture.py`
using the existing copied inputs in `work/mode1-native-slice/fixture-input-v2`.
The prepared selected/excluded profiles are in
`work/campaign-source-guard-20260927/`. The existing test installation, private
profile, remote console and character helper are sufficient; rebuild the test
copy if necessary. No installer or game may overlap another BG task.

The completed one-shot staging helpers under `work/` show the setup used; do not
rerun consumed requests. The existing lab and helpers are sufficient. Next extend
the single configured guard to the selected container-source list, keeping each
source tied to its saved campaign. This does not yet support arbitrary selected
revisions in one process. Creature/store/script interception remains separate.
The fixture observes physical pickup but keeps delivery phase 4 (held); the
general lifetime observer is not integrated. Real items, targets, capacities and
fallback policies remain Christopher's choices.
