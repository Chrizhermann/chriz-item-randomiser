# Native death-drop provenance diagnostic

On 2026-09-26, the isolated fixture proved the exact transfer of four items from
creature inventory through queued engine messages to ground items, followed by
actual pickup of its three rewards. This answers how to observe the pointer
replacement. It does **not** implement automatic held-to-lootable receipts,
durable transfer recovery, or general encounter support.

## Scope and inputs

Baseline `7a9f03d`, following the [creature acceptance](native-creature-acceptance-20260926.md).
Only `%USERPROFILE%\Games\EET-IR-Mode1-20260922\game` and its private profile
`EET IR Mode1 Test - 20260922` were used. The existing
`000000048-IR Creatures Held 0926` was loaded; no character/campaign was created
and no installer ran. Revision:
`5c41781045d88881207731401341286d591908699eee1ac03bf7eebcf4da9790`;
seed `715864367`; **zero** new seed allocations in this process.

Executable SHA256:
`fc821a4806a0305b84fd85f1aad2bd472c8db642ed34b4494ae62351cae1c580`.
Profiler-name database SHA256:
`d270e5c92cf5dfe7294c4944dacbf2ce69ca6dde26c630da9fea28e31e3710e6`.
Both were copied under a brief read-only reservation for offline disassembly.
A separate reservation covered the native run. Competing BG tasks were idle;
no game/installer was running. The existing BG Radar Overlay was left alone.

## Native path and diagnostic

In this executable, `CGameSprite::DropInventory` (`0x1403924B0`) checks resource
droppability and instance flags, obtains the ground pile, and unequips eligible
items. Its call at `0x1403925DA` passes the exact old item pointer to
`CGameContainer::PlaceItemInBlankSlot`, with append slot `0x7FFF`, compression
and message transport enabled. It clears the creature slot after the call.

`PlaceItemInBlankSlot` constructs `CMessageContainerAddItem` at call site
`0x1401942B1`. Its embedded item copies the payload; both message source/target
IDs identify the **container**, not the dying creature. It queues the message
and registers the original item as disposable. A later queue snapshot alone
therefore loses the creature/slot link.

`CMessageContainerAddItem::Run` makes a new heap item from the embedded copy,
then calls `CGameContainer::SetItem` at `0x140210B03`. Subsequent compression
removes null list entries. Function names were navigation aids, not assumed
Lua-callable APIs. Hook helpers were checked against the pinned official
[EEex assembly implementation](https://github.com/Bubb13/EEex/blob/a0502166ea7aaeacc39c6349efe2fb40bbbe203e/EEex/copy/EEex_scripts/EEex_Assembly_x86-64.lua).

`tools/probes/FLPRDrop.lua` is an **opt-in diagnostic**, not an M_ module or TP2
component. It validates all three 37-byte instruction signatures before any
installation, uses the engine image base, reads back each resulting jump, and
restores code protection on errors. Partial installation cannot be retried.
Modified registers/flags are preserved. No Lua callback or engine call executes
inside a hook; no engine arguments, results, items, or campaign variables change.

Its initially disabled, one-shot, 256-row buffer records:

| Kind | Capture point | Exact relationship |
| --- | --- | --- |
| 1 | Before DropInventory placement | Creature pointer, slot, old item pointer, destination container |
| 2 | After placement message constructor | Old item pointer, message pointer, same container |
| 3 | After SetItem in message consumer | Message pointer, new item pointer, same container |

Each row copies resref, three charges, wear, and flags while the item is valid.
Tickets and active-writer counts are atomic; row kind is published last.
Acceptance requires a stopped, stable snapshot without overflow or unpublished
rows. Only copied values are exported, without retaining freed userdata.
The hooks and buffer end with the owned process.

## Observed result

All 12 expected events were captured, with no extras, overflow, or active writer.
The verifier linked every stage to before/after world observations:

| Item | Held by / slot | Old item pointer | Message pointer | Ground item pointer |
| --- | --- | --- | --- | --- |
| FLPRI1 | FLPRC2 / 19 | 1631610144 | 219996656 | 1631613312 |
| FLPRI2 | FLPRC1 / 18 | 1631612256 | 219997280 | 1631614896 |
| FLPRI3 | FLPRC1 / 19 | 1631613312 | 220003312 | 1631610672 |
| FLPRX1 | FLPRC2 / 18 | 1631608912 | 220000192 | 1631608208 |

FLPRI3's freed address was reused for FLPRI1's new item. Pointer equality across
an unobserved lifetime is insufficient even in this fixture; ordered transfer
events disambiguate it.

Two guarded ordinary `Kill(Myself)` actions caused death **after** completed
delivery. All four items dropped exactly once, preserving charges `0/0/0`, wear
`0`, and flags `1`. Normal movement/pickup actions then moved the three observed
ground pointers into party inventory. Flags became `3`, as in earlier pickup
evidence; charges/wear stayed unchanged. FLPRX1 remained on the ground.
Seed, assignments, held phases, and empty journal stayed unchanged.

### Save limitation

The normal-button attempt `IR Drop Trace Looted 0926` was refused before SAVE
opened: popup `15729528`, "You cannot save at this time." No named save was
created; no request was retried or save restriction/NPC action forced clear.
A read-only observation found one unnamed actor with last action `8` (Dialogue)
and current action `0`, with area flags `0`. The known native save check can
reject that last-action value with this message. This is consistent with the
[earlier refusal](native-acceptance-20260926.md#save-refusal-and-qa-correction),
but the actual failing return was not instrumented; the cause is not confirmed.

After normal popup dismissal, the **previously accepted**
`000000050-IR Creatures Looted 0926` reloaded with three party rewards, FLPRX1 on
the ground, and no recapture/replay. That is a regression check, not persistence
proof for this new traced sequence. The product runtime still records held
deliveries; the diagnostic never manufactures a confirmed receipt.

## Evidence and cleanup

`tools/verify_fixture_drop_trace.py` validates successful remote wrappers, exact
ownership/payloads, later game tick, unchanged held assignments, all ordered
links and absence of unexplained events. It supports a loaded campaign's zero
seed allocations and allows message-address reuse only after a chain closes.
Its output explicitly does not claim an automatic runtime receipt.

Full offline suite: **681 tests passed** in 10.393 seconds. The 16 added tests
cover probe guard/failure handling and evidence-chain rejection cases; guard
cases run in Lua 5.1, Lua 5.3, and LuaJIT. Independent review reproduced the real
trace result and checked pickup pointer continuity. These synthetic tests do
not substitute for the native observations above.

Ignored evidence directory: `work/mode1-drop-provenance-20260926/`.

- `inputs.json`, `drop-disassembly.txt`, `drop-consumer-disassembly.txt`.
- `03-before-death.json`, `04-install-probe.json`, `05-arm-and-kill.json`,
  `06-stop-trace.json`, `07-after-death.json`, `trace-verification.json`.
- `08-pickup.json`, `09-after-pickup.json`.
- `10-save-looted-complete.json` (**failed**), `11-save-block-state.json`.
- `14-load-previous-looted-complete.json`, `15-previous-save-reloaded.json`.
- `launch-held.json`, `protected-native-before.json`, `native-released.json`.

The game ran on an independently checked non-input Windows desktop and exited
via normal Quit. Game/loader were absent afterwards; loader and desktop guard
exited 0. All 70 protected installed fixture files, immutable manifests, UI/profile
selection, and named saves retained their hashes. No installed module was
replaced. No command remained pending; the owned reservation was released.

## Remaining gates

1. Connect the chain to held assignments in a runtime observer with session and
   object-lifetime identity, lost-event detection, and persisted completion.
2. Prove save/reload recovery around that transition, including unfinished
   transfers. The current trace is per-process, not durable evidence.
3. Test death during delivery, competing operations, full/changed targets,
   replacements, and transformations without replaying unproved deliveries.
4. Isolate ambient dialogue interference in the test fixture for reliable saves,
   without bypassing the engine's save checks.
5. Independently resolve unvisited-original suppression. The inspected pinned
   EEex APIs still provide no established campaign-local pre-access hook. Shared
   resource demand and loaded area/store arrays do not prove that capability.
   Native first-materialization/load ordering needs separate evidence, including
   excluded-original preservation and A/B campaign isolation.

No real item pools, enemies, capacities, curses, or fallback policies were chosen.
