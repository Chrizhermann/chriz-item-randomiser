# Rewards of a dead target: the ground at its corpse (2.7.3.0)

A creature target that dies before its reward arrives can never hold it, and such
a reward needs a fallback that always works (Chris, 2026-10-22). Until CIR 1.0.2 it
waited forever (`TARGET_NOT_ALIVE`). `GiveItemCreate` cannot help: on a corpse it puts the
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
- **Observation** (`Adapter:_observe_ground`). CIR reads only the items of the
  reward's name in that pile: anything of any mod may lie on the ground (since 1.0.3).
  While the message waits, nothing new of that name may come into a pile that was
  there. Once it has run, the pile with the message's target id is in the area, and
  the reward is its first item of that name that was not in it before, with the
  reward's payload. The handler runs queued messages in order and an
  add appends its item, so an add queued after the reward's lands after it, and an
  item taken out of the pile meanwhile is not the reward (since 1.0.3; before, either
  stopped the campaign). The engine may give a message's address to a new message as
  soon as it has run, as in the death drops of the
  [held-drop receipts](held-drop-receipts-20260926.md). Since 1.0.3 such a message
  no longer stops the campaign either: only the reward's instance counts. Its add can
  identify a zero-lore item again, so CIR sets the
  source's flags on that one instance, as for a container target. CIR pins that
  instance and publishes the receipt one game tick later, as long as that instance is
  still in the pile: `lootable = 1`, holder `ground`, so the reward is confirmed.

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

## A creature that dies while its reward is on its way (since 1.0.3)

`GiveItemCreate` queues the reward's message, and its `Run` puts the reward into the
creature on the next handler update. CIR confirms it one game tick after it has seen
the reward there. Until 1.0.3, CIR stopped for the rest of the adventure when anything
happened to the creature in these one or two ticks: a message for it
(`DELIVERY_QUEUE_CONFLICT`), its death (`TARGET_DIED_DURING_DELIVERY`) or another change
of its items. A fight or a scene easily does that.

Seen in the BG2:EE 2.7.3.0 lab on 2026-10-26, with an adventure started with 1.0.2:
CIR gave uddoor05 at Adalon's exit (AR2402) its ring04, and Adalon's scene killed it
one tick later, as the party arrived. CIR stopped with `DELIVERY_QUEUE_CONFLICT`. Nothing
was lost: its death drop had put the ring into its pile once, among its other items in
slot order (POTN31, MISC21, SCRL9D, RING04, STAF01). The corpse kept only its fist.

Chris's rule applies (2026-10-22): a reward that cannot arrive needs a fallback that
always works; that of a dead recipient goes to the ground. Since 1.0.3 CIR follows the
reward's own instance (`Adapter:_observe_creature`): the one new item of the reward's
name in a backpack slot
(see the [transport evidence](native-creature-transport-evidence.md#while-the-reward-is-on-its-way-2026-10-27)).
Messages for the creature and changes of its other items do not stop the delivery. Nor
does a new message at the address of the reward's message once that has run: a death
drop's own adds may get it.

- **Alive.** As before: pinned, then held one tick later.
- **Dead, the reward out of its body after CIR saw it there.** Its death drop took it
  along with the creature's other items, as in the lab. The reward reached its creature,
  so it is held, as the reward of any creature that dies later.
- **Dead, the reward in its body.** `Run` has no death check, so a message that runs on
  a corpse puts its item there. The death drop takes it along if it comes later. Where
  the drop came first, the reward would stay in the body for good, where nobody can take
  it. CIR waits `DEATH_DROP_TICKS` (15 game ticks, one second) after it saw the death,
  then drops the reward at the corpse itself, as above (`DropItem`, `Adapter:_drop`).
  It waits longer while the corpse holds another item of the reward's name, which
  `DropItem` could take instead, or while any container add is queued.
- **Gone, its object still there.** A creature that is no longer found where it was
  (it left the area alive, say) is followed by its object while that is still the same
  one (`Adapter:_tracked`). CIR reads its items there and runs no action on it.
- **Dead or gone, and CIR never saw the reward in it.** The message ran and the death
  drop took the reward between two looks. It must be on the ground: the one new item
  like it in a pile of the area (`Adapter:_find_dropped`). New means not in that pile
  when the delivery began; CIR notes every pile of the area then. Like it means its
  payload, or that payload identified again by the add, which CIR then sets back. While
  such an item is on its way in a queued add, CIR waits. Found, it is pinned and
  confirmed one tick later with holder `ground`. CIR reads only the items of the
  reward's name in the piles, and looks at most once in `DROP_SCAN_TICKS` (5 game
  ticks): each look reads every object of the area.
- **Gone from the game, and the reward nowhere.** Its message ran when the creature was
  gone (`Run` then finds no creature, and the item is lost), or the reward went with it.
  The creature's object no longer exists, nothing like the reward has come to the
  ground, and nothing like it is on its way there. `DEATH_DROP_TICKS` after CIR saw the
  object gone, the reward falls at a party member's feet, with the message of a
  [creature that left no body](#a-creature-that-left-no-body-since-103)
  (`Adapter:_lost_reward`), whether or not its death counted.
  - The member is chosen, and the drop is made and followed, as there.
  - While no member can drop it, it waits. It has a settling time of its own, from when
    CIR found it nowhere.
  - Only the piles of the area the delivery began in can tell where the reward is, and
    only while the party has not left that area: an area's objects leave memory with it
    and come back as other objects. After the party left, the reward parks
    ([below](#when-the-party-leaves-while-a-reward-is-on-its-way-since-103)).
- **Gone after CIR saw the reward in it.** Held, as for a corpse.
- **A party member** (the recipient of a quest target) keeps its items when it dies:
  its reward is confirmed in its backpack, alive or dead. One that leaves no body (a
  chunked death, say) is followed as any creature above, and so is one that leaves the
  party while its reward is on its way. Since 1.0.3 a quest reward goes to a party
  member only: one marked who left the party gives way to the protagonist
  ([details](campaign-runtime-prototype.md)). A marked member that died before the
  delivery began still waits, as before.

What still stops CIR, since it cannot be told apart or followed:

- another item of the reward's name on its way to the creature;
- a new item of the reward's name outside the backpack, two new ones, or one that a
  stack merge changed (see the transport evidence);
- a creature that carried an item just like the reward and died before CIR saw the
  reward in it, or a second item like it that comes to the ground in the area at the
  same moment (`DROPPED_REWARD_AMBIGUOUS`);
- a reward that cannot be found or placed within the settling time
  (`DELIVERY_SETTLING_TIMEOUT`): one whose creature or corpse is still there without
  it, or a lost one that no party member can drop;
- a creature target that joins the party or becomes a familiar in those ticks.

## When the party leaves while a reward is on its way (since 1.0.3)

Seen twice in the BG2:EE 2.7.3.0 lab on 2026-10-27, with a 1.0.3 candidate whose code
there was still that of 1.0.2: the party
arrived in AR0907 and was sent away at once while CIR's reward for hlmafer was on its
way, and the same in AR0516 with pthral05's. One tick later the area was gone. CIR
stopped for the rest of the adventure (`AREA_UNAVAILABLE`): the objects of an area
leave memory with it, so nothing there could be read any more. The reward had arrived
all the same: pthral05 had no wand12 before and one after, six items then seven. A
save made while a reward was on its way stopped CIR the same way once it was loaded
(`PENDING_RELOAD_UNPROVEN`).

The fix, which Chris approved for 1.0.3 (2026-10-27): CIR remembers the half
hand-over. On the next visit of the place it checks whether the reward arrived, and
delivers it otherwise. The other rewards go on. Where the creature or the container
has one item of that name more than before, the reward arrived; otherwise CIR gives
it then.

- **What was proven stands.** A reward pinned in its creature is held, one pinned in
  its container confirmed, one found or pinned on the ground confirmed there, with
  its message to the player where it lies at a party member's feet
  (`Adapter:_left`). A quest recipient is followed into any area, but a reward
  found on the ground lies in the area its delivery began in: once the party has
  left that area, what was found stands too (`Adapter:_ground_left`).
- **Anything else parks** (`Core:park`). The witness of each delivery carries the
  counts before it (`settle_value`): the items of the reward's name in its creature
  or container, those in the ground piles of its area (none for a party member,
  who keeps its items wherever it dies), the first-of member it went to, and
  whether the reply had paid it to the payee. A save made while a reward was on
  its way parks it the same way when it is loaded (`Core:park_unproven`).
- **Until it settles, its holder takes no other item of its name**
  (`Adapter:set_parked`): its creature or container, from any place there, or the
  party for a quest recipient. Other names, other holders and other members of its
  place go on. The treasury pays nothing of that name out of its holder meanwhile;
  its hand-over of other names goes on, and the rest is handed over once the
  reward has settled.
- **The next visit settles it** (`Adapter:settle_parked`, `Core:confirm_parked`),
  before anything else is done there. Exactly one item of that name more than
  before is the reward; none, or two more (something else of its name came too),
  mean it did not arrive:
  - a living creature and a party member, alive or dead: in its items;
  - a dead creature: in its body and the ground piles of its area together; held in
    its body, else on the ground;
  - a creature gone without a body, its death counted: in the ground piles, where
    its death drop also put what it had of that name before;
  - a container: in it;
  - a store: its stock row, which the reward's insertion wrote (FLPRShop).

  What arrived is held (a creature target) or confirmed. Otherwise it is delivered
  again, where it would have gone: a dead creature's at its corpse, a vanished one's
  at a party member's feet. A quest recipient is followed into any area, and its
  parked reward is counted at the recipient that would get it now: the marked party
  member, else the protagonist (`Adapter:_recipient`).
- **It waits** while an item message of that name is queued anywhere, while the
  place is not in the visible area, and while its creature is missing alive
  (`OWNER_MISSING`), as a pending delivery does.

What still stops CIR, or is not covered:

- a save made while a source's original was being removed, or while a delivery that
  CIR 1.0.2 began was on its way (`PENDING_RELOAD_UNPROVEN`): CIR does not repair
  state of the earlier version (Chris, 2026-10-27);
- a save made after CIR stopped: the world went on without CIR, so the counts of
  the reward on its way and of every parked one are dropped (`Core:forfeit_settle`),
  and that save stops on loading. Where CIR never runs for a save (EEex or its files
  missing, or its revision not found), nothing drops them;
- an item of the reward's name that someone else adds to or takes from that place
  between the start of the delivery (when its counts were read) and the next visit
  makes the counts wrong. One taken, or one added along with the reward, looks like
  a reward that did not arrive, so a second copy comes; one added instead of it
  looks like the reward;
- a reward whose death drop CIR never saw: if the drop ran before the message and the
  party left within `DEATH_DROP_TICKS`, the reward stays in the body where nobody can
  take it, and the settle counts it there as arrived;
- no message for a reward at a party member's feet that only the settle finds: it
  cannot tell whose feet;
- a parked reward whose creature is gone from the game alive waits for it, as a
  pending one does;
- a parked quest reward whose recipient changed between the start of its delivery
  and its settle is counted at the new one: its marked member left the party or the
  game in the ticks the reward was on its way, while the party left the area. Where
  the reward had reached the old one, the new one gets a second copy; where the new
  one happened to have one item of its name more than the old one had, the reward
  counts as arrived. A save made meanwhile and loaded keeps the party as it was, so
  its settle counts the same recipient.

## Not covered

- A target removed from the game alive (`OWNER_MISSING`, death not counted), for
  example one that leaves the area for good or destroys itself, cannot be told apart
  from one that has not appeared yet. Its reward still waits. While its reward is on
  its way, such a target is followed as [above](#a-creature-that-dies-while-its-reward-is-on-its-way-since-103).
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
- An item of the reward's name put into a reused pile while the reward's add still
  waits, other than by a queued add, stops the campaign (`DELIVERY_EARLY_ITEM`): it
  would come before the reward. So does the reward taken out of the pile within one
  tick of its arrival, and
  a new add of the very same item into that pile that gets the address of the reward's
  add right after it ran: it looks like that add still waiting.
- A corpse that keeps another item of the reward's name (an undroppable copy, say)
  makes the reward wait for as long as it keeps it (`CORPSE_ITEM_AMBIGUOUS`). For a
  creature that died while its reward was on its way, it stops CIR once the settling
  time is over.
- Not yet checked in a 2.7.3.0 lab: when the engine's death drop runs after the death,
  and so whether `DEATH_DROP_TICKS` lets it take a reward that reached the body.
- Not yet checked in a 2.7.3.0 lab: a creature that leaves the game while its reward is
  on its way. Whether its object goes at once (in a chunked death, a party member's
  too), and whether its death drop's adds are queued within `DEATH_DROP_TICKS` of that.
- Checked on EET only so far (its BG1 part); BG:EE and BG2:EE run the same engine
  version.
