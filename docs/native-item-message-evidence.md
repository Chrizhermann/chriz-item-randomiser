# Native fixture item-message evidence

This evidence concerns the isolated EET Mode 1 fixture on BG2:EE 2.6.6 with
EEex 1.2. It does not establish a general delivery backend for arbitrary items,
concurrent script producers, multiplayer, or targets with existing contents.

## Why the synchronous inventory check failed

The first native test confirmed all three source removals on later engine ticks.
The first `CreateItem` on a container returned before its inventory changed.
The subsequent inventory observation found the expected item, so treating an
empty immediate observation as a failed delivery was incorrect. Retrying that
action would have duplicated the reward.

The separate, one-shot diagnostic in the discarded test campaign recorded:

- Before: one unrelated message of type 67/subtype 40.
- After: the same message plus exactly one new type 67/subtype 12, communication
  type 1, addressed from and to the selected container.
- The new message contained `FLPRI2`, charges `0,0,0`, wear `0`, flags `1`, and
  slot `32767`. The container was still empty immediately after the action.

The local diagnostic artifact is
`work/mode1-native-slice/container-message-probe.json`. It is an observation
from an already consumed diagnostic action, not a script to rerun.

## Read-only implementation evidence

The inspected installation's `Baldur.exe` SHA-256 is
`fc821a4806a0305b84fd85f1aad2bd472c8db642ed34b4494ae62351cae1c580`.
Function names came from its installed
`EEex_scripts/profiler/function_names.db`. The following image virtual
addresses are disassembly references, not addresses used by Lua at runtime:

| Function or instruction | Image address | Observed behavior |
| --- | --- | --- |
| `CMessageContainerAddItem` constructor | `0x140190940` | Stores target at `+8`, source at `+12`, an embedded `CItem` at `+16`, slot at `+184`, and compression flag at `+186`. |
| Constructor vtable assignment | `0x140190976` | References the class vtable at `0x14058FC38`. |
| Vtable `GetCommType` | `0x140117790` | Returns the stored word value `1`. |
| Vtable `GetMsgType` | `0x140117B80` | Returns the stored byte value `67`. |
| Vtable `GetMsgSubType` | `0x140192C60` | Returns the stored byte value `12`. |
| `CMessageContainerAddItem::Run` | `0x140210A80` | Resolves the target and requires a container. |
| Allocation and copy in `Run` | `0x140210AD4`–`0x140210AEA` | Allocates `0xA8` bytes, then calls `CItem` copy constructor `0x1401FA3B0` with the message's embedded item. |
| Installation in `Run` | `0x140210AF6`–`0x140210B03` | Passes the allocated item pointer and message slot to `CGameContainer::SetItem`. |
| `CGameContainer::SetItem` | `0x140195240` | Calls `CPtrList::FindIndex` (`0x140406880`); when no node exists, calls `CObList::AddTail` (`0x140406720`). |

Consequently, the message's embedded item pointer is **not** the resulting
inventory pointer. For the fixture's empty destination, slot `32767` cannot
resolve an existing list element and therefore appends the sole first item.
The adapter supports this observed append sentinel only; it does not infer
other slot semantics.

EEex's x64 structure documentation agrees that the message owns an embedded
168-byte `CItem`, and exposes `CMessageHandler.m_messageList`. The running
installation's read-only binding inspection confirmed the base-message virtual
type methods, message fields, `EEex_CastUD`, and typed message list. Neither
`CGameContainer::PlaceItemInBlankSlot` nor message `Run` appeared as a directly
callable method on the inspected derived Lua surfaces. Their profiler names
alone are not used as evidence of callable bindings.

## Bounded receipt contract

The adapter permits only distinct fixture resources `FLPRI1`–`FLPRI3`, each
with one source, and named empty type-4 destination piles. The fixture owns
those resources and destinations, with no other scripted producer. This
exclusivity is essential; the protocol is not offered for general game loot.

Before issuing `CreateItem`, the adapter rejects an existing container-add
message targeting the destination or carrying the fixture resource. After the
single action, it requires precisely one newly queued container-add message
with the expected source and target IDs, append slot, and complete payload.
The copied message observation is held under the core's nonce and session.

On a later game tick, the exact message must be consumed and the same
destination must contain exactly one matching item. The adapter records that
actual inventory pointer and requests another later tick. Only when that
pointer and full payload remain stable does it return the delivery receipt.
Queue conflicts, changed messages, wrong payloads, lost messages, changed
owners, or changed item pointers stop verification. Pending reloads do not
replay the creation.

Settling is bounded to 300 elapsed engine game ticks, conservatively allowing
20 game seconds for a transport normally consumed on the next update. Paused
time does not advance this limit. Reaching the limit permanently stops that
pending observation; a subsequently materialized item cannot resume the
transaction, and creation is never retried. The synchronous fallback also
rejects destination or fixture-item queue conflicts appearing before its later
receipt. Both paths retain the same closed-fixture exclusivity assumptions.

Queue inspection copies plain values while each message is alive. No stored
message userdata is dereferenced after consumption. The engine retains full
ownership of queue execution and disposal: the adapter never invokes `Run`,
removes messages, rewrites item fields, or installs native hooks.

The two-stage observation behavior has synthetic coverage across Lua 5.1,
Lua 5.3, and LuaJIT. The one-shot live diagnostic proves the queued transport
shape; complete native-host acceptance must be reported separately after the
new adapter has run through its full delivery and save/reload checks.
