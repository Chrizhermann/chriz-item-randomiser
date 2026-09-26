# Creature fixture acceptance, 26 September 2026

This closes a bounded continuation of Claude's local handoff at `1151182`.
It proves three synthetic weapons delivered to two script-free creatures in
BG2600, followed by held-item save/load, controlled death after delivery, actual
pickup and another save/load. It does not accept general encounter delivery,
death during an in-flight operation, or automatic verification of death drops.

## Handoff and preparation

Codex fast-forwarded `codex/mode1-postinstall-profiles` from `3068a79` to
`1151182`, incorporating `ee04e54` and `3b730ab` without a merge commit or push.
The inherited offline suite passed all 662 tests locally (12.909 seconds).
A separate review found no blocker under the closed-fixture assumptions.

The creature fixture was rebuilt from the preserved `fixture-input-v2`, producing
the handoff's exact revision:

`5c41781045d88881207731401341286d591908699eee1ac03bf7eebcf4da9790`

The save inspector gained `--variant creatures`, explicit FLPRC1/C2 coverage,
complete FLPRX1 observations, and decoding of stored delivery value 5 as held.
Source value 5 remains invalid. Ground-pile output remains compatible. Three
additional synthetic tests cover phase decoding, complete inventory observations,
duplicates and missing/external target inventories. All **665 tests** passed
in 14.619 seconds; the suite itself accessed no game or installation.

## Native results

Only `C:\Users\chris\Games\EET-IR-Mode1-20260922\game` and its private
`EET IR Mode1 Test - 20260922` profile were used. Before staging, the accepted
fixture outputs were hash-checked and all changed files backed up. Older named
test saves, manifest companions, engine profile selection and UI were hashed.

| Check | Observed result |
| --- | --- |
| Old A regression | `000000043-IR QA A Looted` loaded with the new modules. Revision, seed 1850285926, clear journal, confirmed phases and the three collected items were preserved. Seed allocation remained zero. |
| One new creature campaign | Seed 715864367. FLPRI1 assigned to FLPRC2; FLPRI2 and FLPRI3 assigned to FLPRC1. All sources suppressed, deliveries held, journal clear, seed allocation one. |
| Multiple rewards | FLPRC1 held FLPRI2/3 in internal slots 18/19. FLPRC2 retained unrelated FLPRX1 in slot 18 and received FLPRI1 in slot 19. Each had charges 0/0/0, wear 0 and flags 1. |
| Initial autosave | Prologue Start contained each of the four tracked items exactly once on the expected actor; a copy is preserved outside the game. |
| Named held save | `000000048-IR Creatures Held 0926` preserved the same ownership and payloads. Every source phase was verified and every delivery phase held (stored value 5). |
| Held reload | The named held save loaded with identical assignments, seed, inventory slots and payloads; no new allocation or replay occurred. Runtime pointers changed on load, as expected for new objects. |
| Controlled recipient death | After all delivery had finished, one parsed `Kill(Myself)` action (ID 131) was queued on each test cow. Both later became dead, with the three rewards and FLPRX1 appearing once in two type-4 ground piles. |
| Dropped save | `000000049-IR Creatures Dropped 0926` contained the four items on the ground, none on sources, targets or party. All campaign globals matched the held save. |
| Actual pickup | One checked six-action MoveToPoint/PickUpItem response collected the three rewards. Each exact observed ground pointer moved into party inventory; flags became 3, charges and wear stayed zero. FLPRX1 stayed on the ground. |
| Looted save and reload | `000000050-IR Creatures Looted 0926` preserved one copy of each reward in the party and FLPRX1 on the ground. It reloaded with the same state and no recapture. |

The source removal and creature queue path completed without weakening flags,
slot, payload, owner or message checks. This provides native evidence for the
previously untested `CMessageAddItem` cast/fields and instant `GiveItemCreate`
execution on this BG2:EE 2.6.6 / EEex 1.2 fixture.

All three named saves were made through queued normal button events. No native
Save/Load function was invoked from rendering. None encountered the earlier
manual-save refusal; this does not diagnose or resolve that other campaign's
NPC-related refusal.

## Important limit: death changes item pointers

All four inventory pointers before death differed from their corresponding
ground-pile pointers afterward. The named, unique fixture resources and complete
before/after ownership observations establish this controlled transfer, but do
not give the general adapter durable operation provenance across arbitrary
death or transformation.

The runtime therefore still leaves creature deliveries **held**. It does not
automatically promote them to a lootable receipt when a matching item appears
near a dead creature. A production death-drop verifier needs separate transport
or lifecycle evidence. Pending reloads remain stopped with
`PENDING_RELOAD_UNPROVEN`; death during delivery, missing/full/replaced targets,
multiplayer, distant sources and shops have not gained native acceptance here.

The controlled death used the documented
[`Kill` action](https://gibberlings3.github.io/iesdp/scripting/actions/bgeeactions.htm#131),
not a full combat encounter. Recipient scripts and alternate death routes were
not exercised. No production target, loot pool or fallback policy was selected.

## Cleanup and evidence

The shared reservation was acquired at 10:05:40 UTC and released, after cleanup,
at 10:15:09 UTC. The game was launched on a separate non-input desktop, with no
desktop switch, mouse or keyboard automation. The desktop/child inheritance probe
passed before launch. Normal Quit stopped the owned game and loader; the loader
and desktop guard both exited zero. No console request remained pending.

The previous BG2600 area and next-run pointer were restored byte for byte. The
updated Core, Host, Nat, bootstrap and QA modules remain in the disposable install
so its new held-state saves remain readable. New immutable creature companions
and unused synthetic resources were retained for those saves. The older saves,
manifest files, UI and engine profile selection were checked unchanged. No normal
player installation or saves were accessed.

Generated evidence is under `work/mode1-creature-acceptance-20260926/`, excluded
from Git. `verify_acceptance.py` independently checks the saved observations and
live wrappers, counts, complete payloads, coverage, frozen globals, reloads and
pointer transitions. It produced `acceptance-summary.json` with bounded acceptance
and explicit false claims for automatic drop receipts, death during delivery and
general creature support. `stage-receipt.json` and `released.json` record cleanup.

Three preparatory mistakes are retained in the evidence: an output-name collision
stopped before IPC; an incorrect QA method name failed before any load action;
and the first read-only world observer encountered a non-inventory object without
a script-name field. Each was corrected before its dependent mutation. No item
creation, death, pickup or campaign-capture action was resubmitted.

Next engineering work is the death-drop receipt boundary and the unvisited-source
feasibility gate. Real content selection remains Christopher's decision.
