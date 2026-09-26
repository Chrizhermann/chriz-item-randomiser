# Controlled held-to-ground receipts

This continuation connects the [native death-drop trace](native-death-drop-provenance-20260926.md)
to campaign completion in a deliberately controlled synthetic scene. The normal
TP2 does not install or activate these modules. This is not general encounter
support or durable recovery of an unfinished death drop.

## Saved protocol

New captures write provenance version `flpr_prov=1` and one numeric
`flpr_nNNNN` delivery identity per reward. When a creature delivery is observed,
the Core writes its nonce before publishing held phase and clearing the mutation
journal. Restore validates nonce range, uniqueness, creature target, phase, and
agreement with any still-open delivery journal before clearing that journal.

`held_request(unit)` authorizes observation only, retaining the current opaque
session and request token. `confirm_held(receipt)` requires a later observation
boundary, the exact request binding/full payload, a proved transferred instance,
lootability, and no pending world mutation. Confirmation writes only the delivery
phase. It never creates an item or resets delivery to pending.

Write-cut tests cover every held-commit write and both sides of confirmation.
An interrupted record is in-flight, held, or confirmed; no cut grants permission
to deliver again. Legacy saves without provenance globals remain readable, but
their held rewards cannot acquire a made-up delivery identity.

## Fixture observer

`FLPRNat` retains the exact owner, inventory slot, heap item, payload, and delivery
nonce after a successful creature delivery. These bindings are session-only.
They are discarded on world destruction and cannot be rebuilt on reload merely
by finding a similar item. A fresh binding check rejects changed owners, slots,
payloads, duplicate fixture items, and conflicting message-queue operations.

`FLPRDrop` is a pure Lua correlator for the three copied native hook event kinds.
It checks numbered events, full payload, owner/slot, message, container, and item
lifetimes. A freed original address can be reused after its constructor copy;
overlap with a still-live watched item, message, or ground candidate is rejected.
Opaque request tokens are retained by reference. Only a stopped, stable,
non-overflowed snapshot with complete watched chains returns candidates.

`FLPRHeld` accepts only the quiet synthetic revision
`c0acc859837a30315637e16d73c427393ca2694d0cd54eeb7d45ed82eee78e91`.
It arms the existing guarded diagnostic once, observes the 12 events produced by
three rewards plus a control item, stops the capture, validates actual snapshot
metadata, and checks each exact ground heap item twice on later engine ticks.
Only then does it submit Core confirmations. It times out without re-awarding.
The bootstrap stops an attached observer when its host/world ends or changes.
Arming remains an explicit diagnostic operation; no ordinary new game arms it.

The harness must permit only the two ordinary death actions while armed, with
no replacement, transformation, transfer, or pickup until confirmation. This
bounded assumption is essential: three death hooks cannot prove a held CItem
was not destroyed and replaced at the same address before the first hook.
Similarly, an unobserved replacement between the two ground observations is
outside this controlled fixture's claim.

The new `quiet-creatures` fixture removes ambient actors from its active actor
table and disables spawns and rest encounters. It preserves the area script
because EET uses that script for new-game setup and the chapter screen. It retains the
three sources, two recipients, unrelated control item, and original non-actor
sections. Exact actor indexes and immutable identities are recomputed. The two
older fixture variants remain byte-identical to their earlier outputs.

## Validation

The offline suite passed **777 tests**. This includes Core interruption and
legacy-save cases, three-runtime event correlation, native-adapter simulations,
observer lifecycle/metadata rejection, and quiet fixture binary/index checks.
The earlier recorded 12-row native trace was also replayed offline through the
new correlator, producing the three reward candidates with the recorded ground
item/container pointers. This replay is not a new engine test.

Fresh native receipt/save acceptance is pending. The initial native attempt,
after competing chats became idle, captured a new campaign but stalled after
character acceptance. The chapter-finish command remained unclaimed; it was not
resent. No receipt hooks, death action, pickup, or new named save ran.

That first quiet revision (`0d3ede4e...`) had cleared BG2600's area script.
Inspection of the installed script established that it performs EET startup
initialization and `IncrementChapter("Chptxt0")`; removing it was a fixture
contract error. The corrected revision above preserves those bytes, with a
regression test using a nonempty script. The full suite still passes 777 tests.
Whether this also explains the complete polling stall awaits native retry.

The stalled process did not finish after a standard close request. A diagnostic
minidump and the unclaimed command were preserved, then the owned desktop job
was closed. Game/loader were absent, the exact saved command was removed only
after process exit, all 17 staged files were restored, all 70 protected files
retained their hashes, and the reservation was released. The corrected attempt
must use a fresh process and campaign, not replay the failed request.

Evidence and bounded stage/launch/restore helpers are in ignored
`work/mode1-held-receipts-20260926/`; the corrected retry is prepared separately
under `work/mode1-held-receipts-20260927/`.

## Remaining gates

- Native acceptance of the quiet fixture and confirmed receipt through save/load.
- Continuous item-lifetime tracking before death, including replacement and
  transformation; pointer/name/payload snapshots alone are insufficient. New
  [offline destruction-path evidence](native-item-lifetime-evidence.md) identifies
  two candidate endpoints, but no lifetime hook has been executed.
- Recovery of unfinished transfers and held bindings after loading. Saving after
  death but before all confirmations may retain a mixed held/confirmed state;
  the session trace is lost and the held remainder is intentionally not replayed.
- Campaign-local suppression of unvisited originals before player access, with
  excluded originals preserved and two campaigns isolated in the same process.

No real item pools, enemies, capacities, curse content, or fallback policies were
chosen. Those choices remain with the user.
