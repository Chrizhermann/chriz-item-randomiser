# Rewards of a dead target: the ground at its corpse (2.7.3.0)

A creature target that dies before its reward arrives can never hold it, and a
reward must not be lost (Chris, 2026-10-22). Until CIR 1.0.2 such a reward waited
forever (`TARGET_NOT_ALIVE`). `GiveItemCreate` cannot help: on a corpse it puts the
item into the backpack, where nobody can take it. Since 1.0.3 the reward goes to the
ground where the corpse lies, once the party is in that area. Where the creature left
no body, it goes to the ground at a party member's feet
([below](#a-creature-that-left-no-body-since-103)).

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
3. **A stack.** The same by hand, with this change and the stolen-flag rule, on a
   spawned rat killed with two rewards in its backpack: its death drop made a pile, and
   a reward of 10 darts (`DART11`, flags 5) went into that pile as one entry of 10,
   with its flags. The receipt was lootable; after a QA save and load the pile still
   held the 10 darts with flags 5.

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
    (`TARGET_QUEUE_BUSY`);
  - any container add is queued, anywhere (`TARGET_QUEUE_BUSY`). `DropItem` may
    reuse a pile near the corpse, and another drop or a death drop on its way into
    that pile would stop the campaign after the drop. Such an add runs on the next
    update, so this wait is short.
- **Execution** (`Adapter:_execute_ground`). It checks the queue again before acting.
  `CreateItem` must add exactly one
  instance, with the reward's payload, in one of the empty slots and queue nothing.
  `DropItem` must then leave the corpse exactly as before and queue exactly one new
  container add with slot 32767, the same target and source, and that instance's
  payload. So no other queued add can reach that pile. The pile must be a type-4
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

## A creature that left no body (since 1.0.3)

Some creatures leave no corpse when they die. Bodhi's vampires in chapter 3 are an
example (BG2:EE 2.7.3.0, read from the game files only):

- Durst (BODVAM01), Gellal (BODVAM02) and Lassal (LASSAL02) each cast a spell on
  themselves when they die: `Die()`, then `ReallyForceSpellDead(Myself,…)`.
- That spell is SPIN903, SPIN902 or SPIN904. Its opcode 151 replaces the body with a
  mist ("Gaseous Form": BODMIST1, BODMIST2, LASMIST).
- The mists have the script name None, so the runtime never mistakes them for the
  vampires.
- The vampires' items fall when they die, as for every creature. A reward that had
  not reached its vampire yet had nowhere to go.

A chunked death leaves no body either.

The engine counts each death of a creature in GLOBAL `SPRITE_IS_DEAD<script name>`,
the variable `Dead()` reads. The game's own scripts read it too, for example:

- BG2:EE AR0607.BCS reads `SPRITE_IS_DEADQuayle`;
- AR0801.BCS checks `Dead("lassal02")`.

Variable names match in any letter case, and a name holds at most 32 characters.

Chris decided on 2026-10-26 that such a reward goes to the ground with a clear message:
"Einfach das Item auf dem Boden droppen mit einer Nachricht", so clear that nobody
overlooks it.

- **Inspection** (`Adapter:_inspect_party_ground`). It applies to a creature target
  when all of these hold:
  - no sprite of its script name is in the visible area of its own (`OWNER_MISSING`);
  - its death variable is 1 or more (`Adapter:_vanished`);
  - the delivery knows its reward.

  The reward then falls at the feet of a party member there: the first in portrait
  order who is alive in that area, has an empty backpack slot (18 to 33), holds no
  item of the reward's name in any slot, and has no message queued.

  It waits instead while none can:
  - every member's backpack is full (`TARGET_INVENTORY_FULL`);
  - every member holds an item of that name (`PARTY_ITEM_AMBIGUOUS`);
  - a message is queued for every member (`TARGET_QUEUE_BUSY`);
  - any container add is queued (`TARGET_QUEUE_BUSY`);
  - no member is there (`OWNER_MISSING`).

  When several reasons apply, it reports the first member's. A creature that is
  missing but not counted dead still waits (`OWNER_MISSING`): it has not appeared
  yet, or it left alive.
- **Execution** (`Adapter:_execute_ground`, as for a corpse). The member must still
  be the same one, alive and in the area, and the creature still missing and counted
  dead. Otherwise the result is `OWNER_CHANGED`, and a corpse that appears takes the
  corpse path at the next inspection.
  - `CreateItem` on a party member stores the item in the first empty backpack slot
    at once ([creature transport evidence](native-creature-transport-evidence.md)).
  - `DropItem` moves it into a pile at the member, under the same checks as at a
    corpse.
- **Observation** (`Adapter:_observe_ground`), as for a corpse. The receipt also
  carries a notice: the item's name as the player sees it, the member's name, and
  the item's resref. The item's name is the identified one only for an identified
  reward.
- **Message**. `FLPRHost` passes the notice to `deps.notice` once the core has
  confirmed the reward. `M_FLPR.lua` puts both names into in-game.tra @1707, for
  example: "Cloak of Mirroring falls to the ground at Minsc's feet. Whoever carried
  it left no body behind."
  - It shows that line in the message log at once.
  - It also shows it in the engine's info popup once the world screen is up and no
    other info popup is open, one message at a time. A line in the log is easily
    missed in a fight.
  - A failure there is only recorded (`last-error.txt`): the reward is placed already.

The fake-engine tests cover each of these cases (`tests/test_campaign_native_creature.py`,
`tests/test_chapter3_runtime.py`, `tests/test_campaign_native_host.py`).

## Not covered

- A target removed from the game alive (`OWNER_MISSING`, death not counted), for
  example one that leaves the area for good or destroys itself, cannot be told apart
  from one that has not appeared yet. Its reward still waits.
- A missing creature whose script name another creature shares (the three Arans
  share `aran`) gets its reward at the party's feet once any of them has died, even
  if the place's own one only appears later. The game's own `Dead()` treats them as
  one person, too. The reward is not lost, but it can arrive early.
- While no party member in that area can drop it, the reward waits there. If the
  party leaves the area first, it waits until the party comes back.
- Not yet checked in a 2.7.3.0 lab:
  - that the engine counts these vampires' deaths;
  - `CreateItem` and `DropItem` on a living party member;
  - the info popup during a fight.
- A creature that dies in the one or two ticks between `GiveItemCreate` and its
  receipt stops the campaign (`TARGET_DIED_DURING_DELIVERY`), as before.
- A reused pile that changes in the one or two updates after the drop (an item
  dropped there or taken out right then) stops the campaign: CIR could no longer
  tell which item is the reward.
- A corpse that keeps another item of the reward's name (an undroppable copy, say)
  makes the reward wait for as long as it keeps it (`CORPSE_ITEM_AMBIGUOUS`).
- Checked on EET only so far (its BG1 part); BG:EE and BG2:EE run the same engine
  version.
