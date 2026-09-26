# Next Mode 1 engineering slice

Prepared 2026-09-26 after the [bounded native acceptance](native-acceptance-20260926.md).
This is the engineering backlog and acceptance contract, not evidence that the
new adapters work. Christopher does not need to select real content for this work.

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
