# Next Mode 1 engineering slice

Prepared 2026-09-26 after the [bounded native acceptance](native-acceptance-20260926.md).
This is the engineering backlog and acceptance contract, not evidence that the
new adapters work. Christopher does not need to select real content for this work.

## Codex continuation after handoff (2026-09-26)

Integrated the handoff through `1151182` and completed the bounded
[native creature sequence](native-creature-acceptance-20260926.md): older A
regression, two rewards on FLPRC1 and one on FLPRC2, preservation of FLPRX1,
held save/reload, controlled death after delivery, ground pickup and looted
save/reload. The extended save inspector and full offline suite pass 665 tests.

All owned test processes stopped normally and the shared reservation was released.
The old New Game area/pointer were restored; compatible updated modules and the
new immutable creature revision remain in the disposable installation for its
new named saves 48-50. No production content choices or remote pushes were made.

The follow-on [native death-drop diagnostic](native-death-drop-provenance-20260926.md)
captured all four exact inventory-to-message-to-ground transfers and verified
pickup of the three rewards. It also observed a freed item address reused for
a different reward. **Automatic runtime receipts and durable recovery remain
the next gate**; the runtime correctly keeps its held phase. The diagnostic's
new manual save was refused, while the older accepted looted save reloaded.
Death during delivery and the other native edge cases below remain untested.
Unvisited-source feasibility is still a separate open gate. These are engineering
work before real content review.

## Handoff: Claude to Codex (2026-09-26)

Claude continued this slice while Codex was out of tokens. The local branch
`claude/randomizer-improvements-expansion-1a3ef3` builds on Codex's checkpoint
`3068a79`. That checkpoint is Codex's own pending work, committed without
`work/`, which is now ignored. On top of it:

- `ee04e54`: synthetic creature delivery with held rewards (adapter, core
  phase 4, host deferral, [transport evidence](native-creature-transport-evidence.md)).
- `3b730ab`: the creature-target fixture variant.
- This documentation update.

Nothing was pushed or installed, no game was launched, and the isolated install
and profile were not touched. Both worktrees share the repository, so Codex
continues with `git merge --ff-only claude/randomizer-improvements-expansion-1a3ef3`
in its own worktree. The next step is staging the creature fixture, described
under the creature item below.

## Starting evidence

The three synthetic weapons in one BG1 starting area have verified source
removal, ground-pile delivery, pickup in campaign A, independent A/B reloads,
excluded-original preservation, and fresh B assignments without reinstalling.
The first additional B campaign's manual-save refusal remains a separate limit;
its initial autosave does not prove that refused operation succeeded.

The full installation scan is also complete. Its malformed records are preserved
and quarantined, not discarded. Discovery is not proof of runtime eligibility.
See [installation scan evidence](installation-scan-20260924.md).

## Work that needs no creative choices

### Creature delivery and several rewards on one target

**Status at Claude's handoff:** the transport question was answered statically,
the synthetic side implemented, and the disposable creature fixture prepared.
The Codex continuation above records the subsequent bounded native acceptance.

- [Creature transport evidence](native-creature-transport-evidence.md):
  `GiveItemCreate` queues `CMessageAddItem` (type 67, subtype 2) addressed to the
  recipient. `Run` has no death check. It places the item in the first empty
  backpack slot (internal 18–33), and when the backpack is full it displaces an
  existing item to the ground.
- `FLPRNat.lua` accepts `kind = "creature"` targets. It preflights a free backpack
  slot and an exclusive queue, binds the exact message, and pins the resulting
  pointer and slot across two later ticks. The result is **held**, not lootable.
  A death during delivery is terminal, with no receipt and no replay.
- `FLPRCore.lua` records held deliveries as phase 4, for creature-kind targets
  only. `FLPRHost.lua` defers unavailable targets instead of disabling.
- `tests/test_campaign_native_creature.py` covers every row of the table below
  that synthetic tests can reach, in Lua 5.1, Lua 5.3 and LuaJIT.
- `tools/build_mode1_fixture.py --variant creatures` builds the disposable
  creature fixture. FLPRC1 (capacity 2) starts empty. FLPRC2 (capacity 1) holds
  an unrelated disposable `FLPRX1` in CRE file slot 21, which Codex's live
  source probe showed loads into internal slot 18. Both are killable,
  script-free neutral cows standing on accepted pile positions 1 and 3.
  Rebuilt from `fixture-input-v2`, the default variant matches all 13 outputs of
  the accepted `payload-v2` byte for byte. From the same inputs, the creature
  revision is `5c41781045d88881207731401341286d591908699eee1ac03bf7eebcf4da9790`.
  A synthetic end-to-end run of that output through the real Core, Host and Nat
  ends with all three rewards held. The full offline suite passes 662 tests.

Remaining for this item: automatic drop provenance, death timing, and native
runs of the table's remaining edge cases. The held save/reload, controlled
post-delivery drop, and pickup have passed as recorded above.

Use newly authored disposable actors and items. Do not pick a real boss, change a
real encounter, or infer a default capacity for production content.

The legacy `copy/M_FLDLV.lua` supplies useful unique-owner resolution, live/full
inventory preflight and instantaneous `GiveItemCreate` transport. Its
`copy/FLDLVCor.lua` verifies a later resref/charge count increase. That is not the
new protocol's proof of a particular created item, full payload and lootability.
The historical [Azamantes test](plans/2026-08-31-synchronous-eeex-delivery-design.md)
proved recipient queues can discard delivery actions; it did not establish a
general corpse-replacement problem.

Before extending `FLPRNat.lua`, establish the creature transport's actual native
item/message ownership and timing. The proven `CMessageContainerAddItem` path is
specific to a container; neither its type codes nor its append behavior may be
reused as assumptions for a creature. Then exercise:

| Scenario | Required result |
| --- | --- |
| One reward to a living actor | One new operation-bound instance, correct charges/flags/wear, unchanged unrelated inventory, and later observation. |
| Two rewards to one actor | Each assignment has its own verified instance and state; no overwrite or duplicate after save/load. |
| Actor absent or not yet spawned | No creation, no free fallback, and no completed delivery. |
| Same-name duplicate or replacement | Ambiguous identity or changed owner prevents verification; never infer continuity from name alone. |
| Full inventory | No creation or loss. A production fallback policy is not invented for this test. |
| Death before execution or between execution and observation | Record actual engine behavior and follow the exact item into lootable ownership; a count on a dead CRE alone is insufficient. |
| Transformation or scripted replacement | Verify the physical item and holder across the transition, or report unsupported rather than award again. |
| Queued competing creation or matching pre-existing item | Neither can be mistaken for this operation's receipt. |
| Save/load with an unfinished operation | No blind retry or invented success; recovery remains disabled until durable provenance is demonstrated. |

Actual pickup and saved ownership must close the successful drop cases. Logical
and synthetic tests can guard the decision rules but cannot establish native
death, transport, or inventory behavior.

#### Staging the creature fixture

These steps were used for the bounded continuation. A new test needs fresh
backups, process checks and the reservation described below; do not rerun the
consumed evidence scripts or overwrite the previous run's outputs.

1. Build offline from the recorded pristine inputs:
   `python tools/build_mode1_fixture.py --game work/mode1-native-slice/fixture-input-v2 --output work/mode1-native-slice/payload-creatures --variant creatures`.
   The reported revision must be the one above. A different value means the
   inputs or code differ: investigate before staging.
2. Back up every installed file you will replace. At minimum, keep the current
   `BG2600.ARE`, which is the only way back to ground-pile New Games.
3. Copy the payload's override files. `BG2600.ARE` is replaced, and
   `FLPRC1.CRE`, `FLPRC2.CRE` and `FLPRX1.ITM` are new. The other outputs
   should match v2's staged bytes; check their hashes rather than assume it.
4. Add the revision's `.json` and `.lua`, and `fixture-native.lua` as
   `<revision>.native.lua`, to `flpr-fixture/campaign-store/revisions/`. Keep
   the older revisions, because existing saves resolve them.
5. Stage all four changed prototype files: `FLPRCore.lua` (the held phase),
   `FLPRHost.lua` (deferral), `FLPRNat.lua` (the creature path) and `M_FLPR.lua`
   (the ready text). `stage_v2.py` copied only three of these. Without the new
   core, a held receipt is quarantined.
6. Point `flpr-fixture/next.lua` at the creature revision. While this ARE is
   installed, the ground-pile revisions cannot complete a New Game, because
   their piles do not exist. Their deliveries would defer indefinitely.

Then, in order:

- **Regression first:** load an accepted A save, such as `IR QA A Looted`, with
  the new modules. It must restore without recapture or replay.
- **One New Game:** expect the text "Randomiser test ready: 3 test weapons
  have been given to the two test cows near the Candlekeep entrance." Expect two
  rewards in FLPRC1's slots 18 and 19, FLPRX1 unchanged in FLPRC2's slot 18 with
  one reward in 19, empty sources, and every delivery phase held (GLOBAL value
  5). This run also settles three open questions: the live
  `EEex_CastUD(message, "CMessageAddItem")` binding, instant `GiveItemCreate`
  execution, and the final item flags. The adapter requires flags 1. Any
  mismatch stops with `DELIVERY_PAYLOAD_CHANGED` and no replay. Record it
  rather than relaxing the check.
- **Then the table's native rows:** save/reload with two held rewards; death of
  a recipient after its reward is held, following the exact item into its drop
  pile through pickup and a saved game (record whether its pointer survives the
  drop); and death between queueing and `Run`, if a trustworthy way to cause it
  exists. The recipients have 6 HP and zero XP value.

### An original in an unvisited area

This is a separate feasibility gate. `FLPRHost.lua` currently verifies all fixture
sources before delivery, and `FLPRNat.lua` resolves only the visible area. Merely
waiting until the player visits a distant original would violate the intended
workflow: its assigned reward must be obtainable without that visit.

Investigate a campaign-local way to remove or prevent access to the exact selected
original before making the reward available. Do not mutate shared demanded
resource bytes, reinterpret legacy tokens, or change the current source receipt
to success just because an area is not loaded. A guaranteed interception before
access would need its own evidence and explicit protocol representation.

The pinned official source still exposes only the currently visible area through
[`EEex_Area_GetVisible`](https://github.com/Bubb13/EEex/blob/a0502166ea7aaeacc39c6349efe2fb40bbbe203e/EEex/copy/EEex_scripts/EEex_Area.lua#L1-L9).
[`EEex_Resource_Fetch` and `Demand`](https://github.com/Bubb13/EEex/blob/a0502166ea7aaeacc39c6349efe2fb40bbbe203e/EEex/copy/EEex_scripts/EEex_Resource.lua#L91-L155)
use the shared resource table. These APIs do not themselves provide a private
campaign overlay. The documented
[`CInfGame` area and store arrays](https://github.com/Bubb13/EEex-Docs/blob/35445db362f56095156e3b43aa8f6f0f50f728a0/source/EE%20Game%20Structures%20%28x64%29/CI/index.rst#L541)
are investigation leads, not proof of access to unvisited resources or callable
mutation methods. These are the previously pinned revisions, not a new claim
about current upstream HEAD.

Once a mechanism is evidenced, the disposable two-area acceptance sequence is:

1. Capture A, receive and collect its reward while the original area has never
   been visited. Preserve evidence of that ordering.
2. First-enter the original area and prove its selected original is unavailable;
   every unrelated item and excluded original remains correct.
3. Save and reload before and after first entry; verify exact quantities and
   frozen assignments without replay.
4. Start B excluding that item, then switch A/B in one process. B must retain its
   original while A retains its relocated copy. Repeat in a fresh process.

Do not generalize success on an area container to creature templates or mutable
shop stock. Shops additionally require exact finite stock, purchase, save-cache
and reopen coverage. Repeated-spawn sources need an explicit occurrence rule.

## Coordination and stopping points

### 27 September continuation

The [quiet fixture receipt run](held-drop-receipts-20260926.md) passed native
death/drop/pickup and confirmed/looted reload checks. The next
[item-lifetime diagnostic](native-item-lifetime-20260927.md) passes 812 offline
tests and a bounded native capture: both destructor hooks executed, two normal
drops were accepted, and one deliberately destroyed/recreated original was
rejected without changing campaign receipts. The game closed normally and all
staging was restored. General continuous coverage, unfinished-save recovery,
and unvisited-original suppression remain open. All further game work remains
isolated and subject to the reservation below.

The [unvisited-container loading diagnostic](unvisited-container-loading-20260927.md)
then passed first entry and same-process save/reload, with 18 exact raw events and
836 offline tests. Saving reordered containers and repacked item indexes, so
original physical indexes cannot identify saved originals by themselves. All
staging was restored and the game closed. Campaign-local suppression, pre-access
decision availability and early reward delivery remain unproven; the next bounded
test uses only the authored FLPRU1 area and its test items.

The [synthetic source gate](unvisited-source-gate-20260927.md) implements that
bounded test, with 854 passing offline tests including generated-instruction
emulation. It captures an explicit saved choice before area loading and skips
only the exact authored original. Native acceptance is pending; it does not
change source receipts or enable early deliveries.

Offline research, diagnostics, synthetic adapters/tests and test-fixture preparation
can continue autonomously. Before installation access or a game test, check other
tasks and processes and acquire the shared reservation. No desktop control is
needed. Keep every game change in the named disposable install/private profile,
and never reuse an old process ID or a consumed mutation request.

The next planned owner review is a concrete proposal for the first real item pool,
destinations and per-target reward counts, with technical eligibility and remaining
limitations shown. Christopher chooses those, optional pack defaults, progression
rules, curses and crossovers. No creative choice is required to run the synthetic
tests above. Pause earlier if an engineering limitation would require changing
player-visible behavior, narrowing the agreed product, or choosing a fallback
reward policy. An unexplained persistent access conflict is a separate reason to
ask for help, not permission to interfere with another task.
