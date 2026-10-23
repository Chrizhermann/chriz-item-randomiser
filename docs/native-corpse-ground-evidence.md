# Rewards of a dead target: the ground at its corpse (2.7.3.0)

A creature target that dies before its reward arrives can never hold it, and a
reward must not be lost (Chris, 2026-10-22). Until CIR 1.0.2 such a reward waited
forever (`TARGET_NOT_ALIVE`). `GiveItemCreate` cannot help: on a corpse it puts the
item into the backpack, where nobody can take it. Since 1.0.3 the reward goes to the
ground where the corpse lies, once the party is in that area.

## What the game does (EET 2.7.3.0 lab, 2026-10-24)

Checked in a BG1 area of a lab game with CIR 1.0.2 running, on a spawned cat and rat
that were killed with `Kill(Myself)`. Every action ran instantly on the corpse
(`EEex_Action_ExecuteResponseStringOnAIBaseInstantly`).

1. `CreateItem("RES",c1,c2,c3)` on the corpse stores the item at once, in slot 34,
   as the [creature transport evidence](native-creature-transport-evidence.md) says
   for a sprite outside the party: the highest empty slot from 34 down to 15. It queues
   no message. A stackable item's usage counts of 0 become 1 (POTN08 0/0/0 was
   created 1/1/1), as for every other creation.
2. `DropItem("RES",[-1.-1])` on the corpse takes that instance out of the corpse at
   once and queues exactly one `CMessageContainerAddItem` (type 67, subtype 12) with
   slot 32767 and compress 1. Its target and source are the same ground pile
   (container type 4), and it carries the item as the corpse held it.
   - Without a pile there, the engine creates one. The new pile is not yet in the
     area's lists; it joins them as the message runs.
   - With a pile there, it reuses it.
3. On the next update the message has run, and the pile holds the item as a new
   instance (a new pointer), after the pile's earlier items.
4. A second item of the same name, created and dropped the same way, became its own
   entry in the pile: the add does not merge stacks.
5. The pile and its items stayed the same after saving and loading.

This is the container add that CIR's container targets already prove.

## In a running campaign (EET 2.7.3.0 lab, 2026-10-24)

With this change staged over CIR 1.0.2's runtime, in a BG1 campaign of the lab:

1. **A campaign target.** Tarnesh (Friendly Arm Inn) still had one reward due, a
   scroll. The lab held that delivery in the host's own deferral table, travelled to
   the area and killed him. His death drop made a pile at his corpse; the corpse kept
   only his fist. Released, CIR delivered the scroll into that pile, after its seven
   earlier items, which kept their instances. The reward was confirmed, with the
   source's flags (unidentified), and the campaign kept running without an error.
   After saving and loading, the pile and the scroll were still there, the reward
   still confirmed. The loaded game showed the scroll identified, as it already did
   the scrolls of his death drop that CIR had given him unidentified.
2. **A new pile.** CIR's adapter, run by hand with one target on a spawned rat
   that was killed and had nothing to drop: the drop queued its add into a new pile
   that was not yet in the area. On the next update the pile was there with the
   potion, identified again by the add; CIR set the source's flags back, pinned that
   instance and gave a lootable receipt one update later.

## How CIR delivers it (`prototype/FLPRNat.lua`)

- **Inspection** (`Adapter:_inspect_ground`). The target's creature is dead, its
  corpse is the one sprite with the target's script name in the visible area of the
  target, and the delivery knows its reward. The reward waits instead
  (a deferral, retried later) when:
  - the corpse is a party member's, who keeps its inventory and may be raised, or a
    familiar's (`TARGET_NOT_ALIVE`);
  - the corpse holds another item of the reward's name, which `DropItem` could take
    instead (`CORPSE_ITEM_AMBIGUOUS`);
  - slots 15 to 34 of the corpse are all taken, so `CreateItem` would not store it
    there (`TARGET_INVENTORY_FULL`);
  - a queued message is addressed to the corpse or carries an item of the campaign
    (`TARGET_QUEUE_BUSY`).
- **Execution** (`Adapter:_execute_ground`). `CreateItem` must add exactly one
  instance, with the reward's payload, in one of the empty slots and queue nothing.
  `DropItem` must then leave the corpse exactly as before and queue exactly one new
  container add with slot 32767, the same target and source, and that instance's
  payload. No other queued item may reach that pile. The pile must be a type-4
  container of the area or not be listed yet. CIR binds the message to the
  delivery's nonce and sets the source's flags (identified, unstealable) on it.
- **Observation** (`Adapter:_observe_ground`). While the message waits, a pile that
  was there must keep its items. Once it has run, the pile with the message's target
  id is in the area, and exactly one new item follows its earlier items, with the
  reward's payload. Its add can identify a zero-lore item again, so CIR sets the
  source's flags on that one instance, as for a container target. CIR pins that
  instance and publishes the receipt one game tick later: `lootable = 1`, holder
  `ground`, so the reward is confirmed.

Anything unexpected after an action stops the campaign without another attempt, as
for every other delivery. A dead target's reward is never given to another
recipient and never put at its original place.

## Not covered

- A target removed from the game without a corpse (`OWNER_MISSING`), for example one
  that leaves the area for good, cannot be told apart from one that has not appeared
  yet. Its reward still waits.
- A creature that dies in the one or two ticks between `GiveItemCreate` and its
  receipt stops the campaign (`TARGET_DIED_DURING_DELIVERY`), as before.
- Checked on EET only so far (its BG1 part); BG:EE and BG2:EE run the same engine
  version.
