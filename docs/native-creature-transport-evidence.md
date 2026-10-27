# Native creature item transport evidence

Prepared 2026-09-26 for the [next Mode 1 slice](next-native-slice.md). This is
**static, read-only evidence** from the isolated fixture's executable. It is not
live engine acceptance. No game or installer was launched, and no installed file,
profile or save was changed.

The later [native acceptance](native-creature-acceptance-20260926.md) verified the
message binding, transport, final flags, held save/reload and a controlled death
after delivery. This document retains the earlier static evidence and its original
limits; automatic death-drop provenance and death during delivery remain open. A
death during delivery is handled since 1.0.3
([below](#while-the-reward-is-on-its-way-2026-10-27)).

## Sources

- A scratch copy of the fixture's `Baldur.exe`, SHA-256
  `fc821a4806a0305b84fd85f1aad2bd472c8db642ed34b4494ae62351cae1c580`. This is
  the same BG2:EE 2.6.6 binary as the [container evidence](native-item-message-evidence.md).
- Function names from that installation's `EEex_scripts/profiler/function_names.db`.
  A name locates code; it is not evidence that Lua can call the function.
- Structure layouts from EEex-Docs revision
  [`35445db`](https://github.com/Bubb13/EEex-Docs/tree/35445db362f56095156e3b43aa8f6f0f50f728a0/source/EE%20Game%20Structures%20%28x64%29),
  and EEex Lua at the previously pinned revision
  [`a050216`](https://github.com/Bubb13/EEex/blob/a0502166ea7aaeacc39c6349efe2fb40bbbe203e/EEex/copy/EEex_scripts/EEex_GameObject.lua#L240-L245).

The method was checked against known evidence first. The static constants read for
`CMessageContainerAddItem` give message type 67, subtype 12 and communication
type 1. Those match the values Codex observed live in the fixture.

## Message identity

| Item | Image address | Observation |
| --- | --- | --- |
| `CMessageAddItem` constructor | `0x140164AD0` | `(item, source, target)`: stores the source at `+0x0C` and the target at `+0x08`, builds an embedded `CItem` at `+0x10`, then copies its resref, three use counts, wear and flags. The message is `0xB8` bytes with no slot field. |
| Vtable | `0x14058F248` | Destructor, `GetCommType`, `GetMsgType`, `GetMsgSubType`, `Marshal`, `Unmarshal`, `Run`. |
| `GetCommType` | `0x140117790` (shared) | Returns the `.rdata` word at `0x14058D968`: **1**. |
| `GetMsgType` | `0x140117B80` (shared) | Returns the `.rdata` byte at `0x14059928E`: **67**. |
| `GetMsgSubType` | `0x14016E3E0` | Returns the `.rdata` byte at `0x140599292`: **2**. |
| `Run` | `0x14020FC20` | Places the item; described below. |

`CMessageAddItem` shares type 67 and communication type 1 with the container
message, so the subtype (2 vs 12) is what tells them apart. Never cast a type 67
message to `CMessageAddItem` without also checking subtype 2.

## Producer: `GiveItemCreate`

`CGameAIBase::GiveItemCreate` (`0x14016E5B0`):

1. Builds a temporary `CItem` from the action's resref and three charge
   parameters, with wear 0 and flags 0 going into the constructor. It uses the
   same `CItem` constructor (`0x1401FA750`) as `CreateItem`. The container path
   through that constructor produced live flags 1 for the identified fixture item,
   so final flags must still be observed rather than assumed to be 0.
2. Only when the resolved recipient is a sprite (object type `0x31`), and the
   action is not the separate `0x116` variant that builds a
   `CMessageReplaceItem`:
   - builds `CMessageAddItem(temp, source = executor id, target = recipient id)`;
   - calls `CMessageHandler::AddMessage(message, 0)`;
   - registers the temporary item with `CInfGame::AddDisposableItem`;
   - shows a "party gains item" feedback string only for party members.
3. `AddMessage` dispatches on communication type 1 to `CMessageHandler::Send`.
   `Send` deletes a message whose target id no longer resolves. In single player
   it appends to `CMessageHandler::m_messageList` (`CObList::AddTail`). That is
   the list the container adapter already inspects.

Executing `GiveItemCreate("RES",Myself,c1,c2,c3)` on the recipient therefore
queues exactly one type 67/subtype 2 message with source and target both equal to
that recipient. The backpack is not changed until the message runs.

The constructor also makes every usage count 0 of a stackable item 1 (BG:EE
2.7.3.0, `CreateItem` on a party member, 2026-10-20: SCRL75 0/0/0 became 1/1/1,
SCRL75 2/0/0 2/1/1, POTN08 4/0/0 4/1/1, AROW01 0/0/0 1/1/1; WAND10, SW1H01,
PLAT01 and RING05 kept their zeros). A source record's 0 asks for that default,
so the adapter accepts exactly this difference from the reward and binds the
counts the engine created. Before CIR 1.0.2 it demanded the reward's counts,
and the first stackable reward (a scroll from a container for Gorion) stopped
CIR for the rest of that game.

## Consumer: `CMessageHandler::AsynchronousUpdate`

The local loop at the start of `AsynchronousUpdate` (`0x140204E00`) repeatedly
removes the head of `m_messageList`, calls `Run`, and deletes the message until
the list is empty. That includes messages appended while it runs. A queued
creature message is therefore consumed on the next handler update, as with the
container message. The only other `CMessageAddItem` constructor call inside
`AsynchronousUpdate` belongs to network unmarshalling (multiplayer receive).

## `CMessageAddItem::Run`

1. Resolves the target with `CGameObjectArray::GetDeny` and requires object type
   `0x31`. **There is no death check.** A message that runs after the recipient
   died places the item in the corpse. This is the engine side of legacy failure
   class 7 in the [handover](https://github.com/Chrizhermann/chriz-item-randomiser/blob/172872dad6496c1d6eaf1a09b52bc3189da23b96/docs/handover.md).
2. If the target id is in `CInfGame::m_familiars` (`+0x6728`), it additionally
   sends a copy from the familiar to the protagonist, then continues with local
   placement. Familiars are therefore excluded as targets.
3. Scans the 16 backpack slots, internal indexes 18–33 (the count is the
   `.rdata` word `0x1405A3D34` = 16). `m_equipment` is at sprite `+0xFC0`, so
   slot 18 is `+0x1050`. The first slot that passes either test wins:
   - **Empty:** allocates a heap copy of the embedded item (`CItem` copy
     constructor `0x1401FA3B0`), stores it, and calls
     `CGameSprite::Equip(slot, 0)`. The inventory pointer is never the message's
     embedded item.
   - **Stack merge:** the slot holds the same resref, maximum stack size > 1, and
     the combined first-ability usage is strictly below that maximum. The existing
     item's usage is increased and **no new instance is created**.
4. After a placement, it broadcasts `CMessageSpriteEquipment` and refreshes the UI.
5. **Full backpack:** it does not fail. If the new item is critical (item flag
   bit 0), it replaces the first non-critical droppable backpack item. Otherwise,
   or if no such item exists, it replaces the first droppable backpack item, or
   slot 18 if nothing is droppable. The displaced item goes to the ground through
   `CGameAIBase::PutItemGround`, with feedback.

## Why not `CreateItem` on a creature

`CGameAIBase::CreateItem` (`0x140168480`), executed as a creature:

- **Party member:** stores the new item directly in the first empty backpack slot.
  If the backpack is full, it queues a container add into that member's ground pile.
- **Other sprite:** stores the item directly in the *highest-numbered* empty
  internal slot, scanning 34 down to 15. That covers the quick slots, the backpack
  and slot 34. It sends no message and makes no `Equip` call. With no free slot
  in that range it falls back to a ground pile at the creature's position.
- **Container:** queues `CMessageContainerAddItem` with slot `0x7FFF`. This is
  the proven fixture path.

The non-party creature path is synchronous but fills unusual slots without
equipping them, and it has a silent ground-pile fallback. `GiveItemCreate`
matches the legacy transport, places items in the ordinary backpack with
`Equip`, and produces a queued message that can be bound to the operation the
same way as the container message.

## Rules for the creature adapter

These follow from the evidence above and the closed-fixture assumptions of the
container adapter:

- Target: exactly one living sprite with the fixture script name in the visible
  area (`EEex_GameObject_IsSprite(object, false)`). It must not be a party member
  (`EEex_Sprite_GetPortraitIndex(object) == -1`) or a familiar
  (`m_typeAI.m_EnemyAlly ~= 3`), and must have the same object id and pointer at
  inspection, execution and observation.
- Preflight before the core journals anything: at least one empty backpack slot
  (18–33), and no queued message addressed to the target other than a
  `CMessageSetDialogWait` ([below](#dialog-wait-messages-2026-10-24)). The full-backpack
  displacement would otherwise change unrelated inventory, and a queued producer
  could take the free slot first. Also no queued item-add message of either type
  carrying the fixture resource.
- Items are nonstackable (maximum stack 1) and droppable, as in the container
  fixture, so the stack-merge branch cannot apply.
- After the single `GiveItemCreate`: the inventory must be unchanged, and exactly
  one new type 67/subtype 2 message must exist, with source = target = the
  creature, full expected payload, and communication type 1.
- On later ticks: the exact message must be gone, the creature still the same
  and alive, and exactly one previously empty backpack slot must now hold the full
  payload, with every other slot's pointer and payload unchanged. Pin that
  pointer and require one further later tick before any receipt.
- A dead or changed recipient, a lost message, an unexpected slot, or any other
  inventory change stops verification. Nothing is ever re-created.
- A placed item on a living creature is **held**, not proven lootable. Death-drop
  lootability, including whether the exact pointer moves into a ground pile, needs
  native evidence before a receipt may claim it.

## Dialog-wait messages (2026-10-24)

The rule above first counted *every* message addressed to the target. In the
BG2:EE 2.7.3.0 lab the Sahuagin king's reward for Player1 (a quest route, so a
party recipient) therefore never arrived: all 68 checks at the host's retries,
two seconds apart, refused it with `TARGET_QUEUE_BUSY`, and each found exactly
one queued message, type 67/subtype 40 from Player1 to itself. Read from the
console at other moments, the queue held the same message for Player1 or for
Imoen. The checks after `GiveItemCreate` and while observing used the same rule
and would have stopped CIR had the delivery started.

Static evidence, with names from each 2.7.3.0 lab's
`EEex_scripts/profiler/function_names.db`:

- Type 67/subtype 40 is `CMessageSetDialogWait` (vtable `0x1405912D8`).
  Its `Run` (`0x140214B80`) resolves the target with `CGameObjectArray::GetDeny`,
  requires object type `0x31`, copies the message's `+0x10` and `+0x14` into the
  sprite's `+0x4CAC` and `+0x4CB0`, and returns. It touches no inventory and
  sends no other message.
- `CGameSprite::ProcessAI` (`+0x1139`, `+0x116A`) builds one for every sprite
  that has a portrait number (`CInfGame::GetCharacterPortraitNum` ≠ −1), with
  source and target that sprite, and queues it with `AddMessage` on each of its
  AI updates. It skips this only while `CInfGame` `+0x7978` is `0x502` and the
  sprite's `+0x4CAC` is at least 2. `CGameSprite::OnActionButton` and
  `CGameDialogReply::Pick` also build it.
- The same `Run` code, with the same offsets, is in BG:EE 2.7.3.0
  (`Baldur.exe` SHA-256 `187f89e1…`), BG2:EE and EET 2.7.3.0 (`b51093a4…`, the
  same addresses), BG:EE 2.6.6.0 (`9634c3e6…`) and BG2:EE 2.6.6.0
  (`fc821a48…`, the fixture binary above; vtable `0x140590298`, `Run`
  `0x140214920`). For 2.6.6.0, without a names file, the class was found as the
  type-67 vtable whose `GetMsgSubType` returns 40. The 2.6.6 container test
  above also saw one such message before its action.

Live, in the same lab: `GiveItemCreate` on Player1 queued exactly one new
67/2 (`CMessageAddItem`, source and target Player1) and one new 67/16
(`CMessageDisplayText`, source and target 0: the "party gains item" feedback);
the dialog-wait message stayed queued.

Since then the adapter marks a queued 67/40 as inert. Every other message
addressed to the target still blocks it before the action and right after it.
Since 1.0.3 such messages no longer stop an observation
([below](#while-the-reward-is-on-its-way-2026-10-27)). With that change the lab loaded a save made while the king's reward
was waiting: before the first check after loading, HALB09B was in Player1's
first free backpack slot with the source's flags 0 and the delivery was
confirmed, while a dialog-wait message for Player1 stayed queued. After a QA
save and reload it was there exactly once and nothing was delivered again.

## While the reward is on its way (2026-10-27)

The rules above stopped CIR when anything happened to the creature between
`GiveItemCreate` and the receipt: any message for it, its death, or a change of any of
its items. A fight or a scene does that easily. In the BG2:EE 2.7.3.0 lab a scene killed
the recipient one tick after the reward had arrived, and CIR stopped for the rest of
the adventure ([details](native-corpse-ground-evidence.md#a-creature-that-dies-while-its-reward-is-on-its-way-since-103)).

Since 1.0.3 the observation follows the reward's own instance (`arrived` in
`prototype/FLPRNat.lua`): the one item of the reward's name in a backpack slot (18–33)
with a pointer that no item of that name had when the delivery began. `Run` places its
item there and nowhere else. The other items may change:

- An item of that name the creature had is not new where it moved, nor where it
  changed in its own slot outside the backpack, by a charge used in a fight, say.
- One that changed in its own backpack slot is what a stack merge leaves, where `Run`
  makes no new instance. It is no copy of the reward, so CIR stops
  (`DELIVERY_PAYLOAD_CHANGED`) rather than guess.
- A new item of that name outside the backpack, or two new ones, still stop CIR
  (`DELIVERY_PLACEMENT_UNEXPECTED`).

A message for the creature no longer blocks the
observation, but another item message of the reward's name for it does
(`DELIVERY_QUEUE_CONFLICT`), and so does a change of the bound message. Once that
message has run, the engine may give its address to a new message at once, as in the
death drops of the [held-drop receipts](held-drop-receipts-20260926.md); that no
longer stops CIR (`DELIVERY_MESSAGE_REUSED` before 1.0.3). Where the creature dies,
the reward follows Chris's rule and goes to the ground; how is described with the
ground fallbacks. Where the party leaves the area first, the reward parks, and the
next visit tells whether it arrived: exactly one item of its name more than before
([details](native-corpse-ground-evidence.md#when-the-party-leaves-while-a-reward-is-on-its-way-since-103)).
A quest reward's recipient is followed into any area. It is a party member (since
1.0.3: [the marked one, else the protagonist](campaign-runtime-prototype.md)), who
keeps its items wherever it dies, so its counts take no ground piles. One that
leaves the party while its reward is on its way is followed still; it drops its
items where it dies, as a creature target does.

## Still unproven

- Lua access to the message: `EEex_CastUD(message, "CMessageAddItem")` and its
  `m_item` binding in the running build. The container type's binding was
  confirmed live; this one was not.
- Live instant execution of `GiveItemCreate` through
  `EEex_Action_ExecuteResponseStringOnAIBaseInstantly` on this EEex build. The
  fixture's `INSTANT.IDS` (from `DATA/DEFAULT.BIF`, not overridden) lists
  `140 GiveItemCreate` alongside `82 CreateItem` and `169 DestroyItem`, which
  the accepted container path already executes this way. The legacy transport
  used the parsed-response API with EEex v0.11.
- The creature's final item flags after `Run`, and save/reload persistence of the
  held item.
- Death behavior: a death between queueing and `Run` (corpse placement is
  expected from the code), and the death drop of a held item into lootable
  ownership, including pickup. Seen once (BG2:EE 2.7.3.0, 2026-10-26): a death one
  tick after the reward arrived; the death drop put it into the corpse's pile once.
- That a party member's object and its items' pointers stay the same through an area
  transition, which following a quest recipient into another area relies on.
- Scripted replacement, transformation, and multiplayer are unsupported.
