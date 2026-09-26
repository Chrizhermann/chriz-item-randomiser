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
`0d3ede4e2caa0665b9753645169fbc5ce0f681870709da66d90bc08ba5e83877`.
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
table and disables its area script, spawns, and rest encounters. It retains the
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

Fresh native receipt/save acceptance is pending. The prepared evidence and
bounded launch/stage/restore helpers are in ignored
`work/mode1-held-receipts-20260926/`. No installation was accessed or process
launched while the conflicting script-engine chat was active.

## Remaining gates

- Native acceptance of the quiet fixture and confirmed receipt through save/load.
- Continuous item-lifetime tracking before death, including replacement and
  transformation; pointer/name/payload snapshots alone are insufficient.
- Recovery of unfinished transfers and held bindings after loading. Saving after
  death but before all confirmations may retain a mixed held/confirmed state;
  the session trace is lost and the held remainder is intentionally not replayed.
- Campaign-local suppression of unvisited originals before player access, with
  excluded originals preserved and two campaigns isolated in the same process.

No real item pools, enemies, capacities, curse content, or fallback policies were
chosen. Those choices remain with the user.
