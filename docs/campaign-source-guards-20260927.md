# Mode 1: reward before visiting its original area

The integrated prototype is implemented and passes offline tests. Its New Game
and save/reload flow still needs in-game acceptance. The earlier standalone
[source-gate diagnostic](unvisited-source-gate-20260927.md) passed native tests;
that result does not establish acceptance of the integrated implementation.

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

## Test and continue

Build the authored `unvisited-creatures` fixture with `build_mode1_fixture.py`
using the existing copied inputs in `work/mode1-native-slice/fixture-input-v2`.
The prepared selected/excluded profiles are in
`work/campaign-source-guard-20260927/`. The existing test installation, private
profile, remote console and character helper are sufficient; rebuild the test
copy if necessary. No installer or game may overlap another BG task.

Next, run the selected New Game, obtain its reward before visiting FLPRU1, enter
that area, and check the original is gone while unrelated contents remain.
Save/reload before and after entry, then check the excluded configuration retains
its original. Use the same assignments after reload and repeat in a fresh process.
The current native staging helper is `work/campaign-guard-native-20260927/prepare.py`.
It has not been run. Live ordering, native hook integration and the returned-item
case remain unverified. Creature/store/script sources and real content choices
are subsequent work; no fallback or gameplay policy is chosen here.
