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

### Native acceptance: 27 September local time

The corrected quiet fixture passed a fresh native run on the isolated non-input
desktop after the potentially conflicting BG chats became idle. The character
helper completed all 15 steps, including the chapter screen, without desktop
input. The campaign used seed `1281232896` and retained the saved delivery
identities `[5,6,7]` throughout the run.

Two ordinary `Kill(Myself)` actions produced a stopped, stable 12-event trace
without overflow: three rewards plus the unrelated control. The observer linked
each original owner/slot/item through its transfer message to the exact ground
item/container and payload, checked the ground items again on a later tick, and
confirmed all three deliveries. A real reused heap address was handled in order:
FLPRI1's former held pointer became FLPRI2's ground pointer after FLPRI1 had been
copied into its message. Both message addresses were also reused only after
their earlier consumption. Pickup then used ordinary `MoveToPoint`/`PickUpItem`
actions; all three rewards reached the party and the control stayed on the
ground. The fixture harness issued each mutation once.

| New named save | Delivery state | Three rewards | Control |
| --- | --- | --- | --- |
| `000000051-IR Quiet Held 0927` | held | recipients, one copy each | recipient |
| `000000052-IR Quiet Confirmed 0927` | confirmed | ground, one copy each | ground |
| `000000053-IR Quiet Looted 0927` | confirmed | party, one copy each | ground |

Read-only inspection of each exact named save verified all three original source
inventories contained no fixture rewards, both recipient inventories were observed, and each of
the four fixture item types had exactly one physical record across the party
and saved BG2600. Comparing FLPR GLOBALs from held to confirmed found only the
three delivery-phase changes; the confirmed and looted FLPR GLOBALs were equal.
The immutable revision, seed, assignments, source phases, and nonces persisted.

Both confirmed and looted saves were loaded through the ordinary menu path in
the same process. Each restored the expected item placement and confirmed
phases with no attached observer, no additional seed, no recreated originals,
and no duplicate rewards. This validates already-confirmed recovery only: the
held save was inspected but was not reloaded in this run.

The game exited through its normal Quit button. The owned game and loader were
absent before the desktop guard was released; all 17 staged paths were restored,
all 73 protected files retained their hashes, and the reservation was removed.
The three new saves and their immutable manifest remain in the isolated test
profile/store. No installer ran or ordinary desktop was selected.

Evidence is under `work/mode1-held-receipts-20260927/`: numbered console responses,
the 12-row trace, the three serialized save observations, `acceptance-check.json`
(independent artifact review), `saved-acceptance.json` (saved-state/reload checks
with input hashes), and `native-released.json`. The full offline suite on the
same implementation passed **777 tests** before this run.

### Earlier failed startup and correction

The initial native attempt, after competing chats became idle, captured a new
campaign but stalled after character acceptance. The chapter-finish command
remained unclaimed; it was not resent. No receipt hooks, death action, pickup,
or new named save ran.

That first quiet revision (`0d3ede4e...`) had cleared BG2600's area script.
Inspection of the installed script established that it performs EET startup
initialization and `IncrementChapter("Chptxt0")`; removing it was a fixture
contract error. The corrected revision above preserves those bytes, with a
regression test using a nonempty script. The full suite still passes 777 tests,
and the corrected fixture completed startup in the accepted run above. That
successful retry does not independently establish the old polling stall's
complete causal mechanism.

The stalled process did not finish after a standard close request. A diagnostic
minidump and the unclaimed command were preserved, then the owned desktop job
was closed. Game/loader were absent, the exact saved command was removed only
after process exit, all 17 staged files were restored, all 70 protected files
retained their hashes, and the reservation was released.

Offline inspection of that minidump found one saved thread instruction pointer
inside `NtGdiDdDDIWaitForVerticalBlankEvent` in `win32u.dll`, with raw stack
addresses in the OpenGL presentation path. The local DLL matches the dump's
image size, timestamp, checksum, and version. This is one graphics-path snapshot,
not an unwound stack, measured wait duration, or proof of a renderer deadlock.
It does not establish the cause of the polling stall. The startup-script defect
above is a separate resource-level finding.

Failed-attempt evidence and bounded stage/launch/restore helpers remain in
ignored `work/mode1-held-receipts-20260926/`. The corrected run used fresh
helpers, reservation, process, requests, and evidence under the 27 September
directory; no old job or ambiguous request was rerun.

## Remaining gates

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
