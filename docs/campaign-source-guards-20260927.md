# Mode 1: reward before visiting its original area

The integrated authored fixture passed native New Game, early reward pickup,
source suppression and selected/excluded campaign reload checks on 27 September.
This is a working prototype for one container on the pinned Windows build;
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
  phase. The implementation is restricted to one authored FLPRU1 container and
  the pinned BG2EE executable; this is not general source support yet.
- The guard compares the complete authored ARE input and ties the result to the
  same area-load invocation and constructor inputs. Saved area records can move;
  matching an item name or original index alone would delete a returned copy.
  Confirmed removal disables further suppression. A save between removal and
  confirmation remains protected; absence alone never creates a removal receipt.
- `FLPRNat.lua`: creates the declared reward and observes removal. It does not
  demand or mutate the unvisited resource. Payload, owner and quantity checks
  concern player items, not the integrity of the disposable lab.

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

## Continue

Build the authored `unvisited-creatures` fixture with `build_mode1_fixture.py`
using the existing copied inputs in `work/mode1-native-slice/fixture-input-v2`.
The prepared selected/excluded profiles are in
`work/campaign-source-guard-20260927/`. The existing test installation, private
profile, remote console and character helper are sufficient; rebuild the test
copy if necessary. No installer or game may overlap another BG task.

The completed one-shot staging helper is
`work/campaign-guard-native-20260927/prepare.py`; do not rerun consumed requests.
Next verify a player-returned copy survives reload and a save between suppression
and host confirmation cannot cause a duplicate reward. These native cases remain
unverified. This fixture observes physical pickup but keeps delivery phase 4
(held); the general lifetime observer is not integrated. Creature/store/script
source interception and real content choices are subsequent work. No fallback
or gameplay policy is chosen here.
