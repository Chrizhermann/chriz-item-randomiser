# Experimental campaign protocol

The per-campaign runtime is playable in the disposable EET lab;
it is **not yet installed by the normal TP2 or ready for release**. The original
Mode 1 preset has passed a live 321-reward campaign test, fresh-game randomization,
and save/load preservation. The full original preset collection is still unfinished:
remaining Mode 2 quest handlers and original special options are being migrated.

**Persistent playtest, 2026-10-12:** the lab at
`%USERPROFILE%\Games\EET-IR-Mode1-20260922` now keeps the runtime installed.
Its `Configure Randomiser` shortcut enables the editor's **Apply for next game**;
`Play Randomiser Test` launches EEex. The original EET Mode 1 configuration is
already active. Start a new **BG1** campaign, or load **Mode1 Ready to Try**.
Direct SoD/BG2/ToB starts remain unsupported by this prototype.

The refreshed scan and original preset compiled 321 reward units. The editor's
real HTTP apply completed, a cold hidden/muted game reached Candlekeep, and save
`000000089-Mode1 Ready to Try` reloaded with all 321 assignments and seed unchanged.
This verifies setup, capture and persistence, not a full playthrough of every
reward. The game was closed normally afterward. The old three-item fixture
actors/piles were removed by reversing their exact known addition to BG2600;
other area data and existing saves were preserved. Current scans/configurations
live beside the game; evidence is under ignored `work/playtest-20261012/`.
The editor/activation/export batch passed 31 tests, including revision retention,
saved-choice reopening, and refusing a different workspace that omits old guards.
The private test profile has audible volumes for manual play; mute it again
before future automated launches. No new encounter decisions were made.

Latest live evidence (2026-09-29/30), under ignored
`work/original-preset-runtime-20260929/live-store-console/`:

- Two fresh games used different seeds with the same full Mode 1 configuration.
  Loading the earlier full-preset save preserved all 321 assignments and states.
- One shop accepted two identical randomized swords alongside an ordinary sword.
  Buying either kind preserved the other rows and their individual prices; saving
  and loading retained purchases without replenishing stock. Randomized items used
  120% pricing; ordinary stock retained its normal price.
- The extra Spell Shield option provided three scrolls on a fresh game. Buying one,
  saving, starting another game, and reloading the save gave counts 2, 3, and 2.
  Its randomized-source variant remains to be checked live.
- The original cursed-disguise option passed a subsequent lab test in
  `work/original-preset-runtime-20260929/live-cursed2/`: two fresh campaigns chose
  different appearances, and loading the first save restored all eight choices.
  Every demanded item matched its exported variant; engine-owned original bytes
  were unchanged. Option-off and Item Revisions behavior have offline coverage;
  equipping each curse and a cold-process reload remain untested.
- The next session, `work/original-preset-runtime-20260929/live-mode2-routes/`,
  exercised the real ASYSTA01 reward script: the assigned ring appeared once with
  its charges intact, alongside the ordinary puzzle key. PPChest's assigned item
  disappearing set its saved quest flag; save/load kept it absent without a
  respawn. This used the engine's DestroyItem action, not a manual pickup. SCYARRYl's
  alternate inventory copy stayed suppressed after loading a consumed reward state;
  the BHOISIG dialogue itself was not played. The first test character died on area
  entry; a fresh private character with temporary survival effects completed this
  test. No production gameplay protection was added.
- TRGENI01, LOUTCAPT, AMCLER01, and UDLESA retain carried rewards for dialogue,
  theft, or combat. In `work/original-preset-runtime-20260929/live-inventory-parser-fixed/`,
  the real reward conversations delivered exactly one RING43 from the genie,
  SW1H23 from Dennis, and DAGG11 from the Amkethran priest. Original quest items
  were taken and the speakers departed normally. Tests used prepared quest-entry
  conditions, not complete playthroughs of those quests. UDLESA has focused
  offline coverage; its duel has not been exercised live.
- The genie test also preserved the ordinary BLUN05 replacement and passed
  save/load in `000000085-IR NPC Reward 0930`: seed 702817910, assignments,
  completed quest, departed genie, and exactly one party ring remained intact.
  The priest result is saved separately as `IR NPC Priest 0930`.
- Those tests exposed and fixed two dialogue issues: the engine strips spaces
  and colons from embedded Lua commands, and instant DestroySelf could remove
  the genie before his queued handover. Callbacks now use parenthesized returns
  with numeric target rows; the genie's own departure waits behind his handover.
  A separate engine identification change during NPC delivery is normalized
  only after the new item is bound to its exact delivered inventory slot.
- The combined nine-reward batch in `live-quest-batch-fixed/` passed the actual
  SPIN621 Key spell, Unshey's plain-belt turn-in, BHOISIG's reward conversation,
  two wards placed on and picked up from one altar, and Kangaxx's key-recognition
  dialogue. Prepared quest conditions were used. Save `000000087-IR Quest Batch
  0930` preserved all nine assignments and exact party item payloads on reload.
  UDLESA's replacement weapon and assigned ring were present, but the prepared
  post-duel conversation did not open; crypt ward consumption was not exercised
  by the attempted script click. Both remain unverified.
- That batch reproduced two defects. Party GiveItemCreate queues a separate
  gains-item notification (type 67/subtype 16) alongside the item message; the
  adapter now counts new item messages instead of unrelated feedback. The Key
  spell and temple reward passed after this correction. Flail conditions and
  grants now use separate CRLF lines: space-joined commands produced dialogue
  parser errors and Glaicas left without handing over the heads. The Flail fix
  still needs its prepared cold-session check in `live-quest-batch-cold/`.
  The follow-up offline check used the copied real dialogue/crypt resources:
  all eight Flail conditions and three grant branches have separate commands,
  and all four option patches recover their exact original resources.
- The fresh-process follow-up in `live-quest-batch-cold/` passed the Key spell
  and Glaicas's complete three-head handover without hot-loading either fix.
  UDLESA's original surrender dialogue also transferred the assigned RING41
  once and retained his ordinary replacement weapon. Its prepared quest entry
  needed both `NumDead("uddrow37",4)` and HP below 6, alongside the duel globals;
  the earlier setup had left his higher-priority wandering block eligible.
  Save `000000088-IR Corrected Batch` retained all nine assignments, every item,
  and their slots on reload. RING41's unused first charge field changed 1 to 0;
  the actual ITM has zero ability headers. Other payload fields were unchanged.
  The crypt click did not advance `UsedCrypt` or consume a ward, so that engine
  interaction remains unverified. The game was closed normally, 39 temporary
  files and three ward-support files restored, and the shared lock released.

TOLGER's place (row 50) has a route now. The original installer puts the same
assignment on both of Tolgerias's templates and relies on his class script,
which destroys the one's copy once the other has lost his (`CDRing33Gone`).
Here his payment hands the place's reward to his payee, and until then it waits
for TOLGER2's corpse in the planar sphere (`corpse_only`, above); his ring's
destroy runs only while the ring stays. The two Sahuagin king
templates' places have routes now: the prince carries row 60's reward, and row
58's waits for the king's payment or his corpse (`corpse_only`, above). SAHTREAS also removes and re-grants its
reward on a quest branch, so the simple container loot latch is insufficient. The
treasury route now gates those destroys and has the king's reward reply hand the
treasury's reward to his payee ([configurator](post-install-configurator.md)).
These remain implementation work, as does original automatic equipment placement.
GORLUM's LUM3 clue also remains: reuse the existing campaign item-view hook to
select the original reward-specific description (or @1099 fallback), changing
only the ITM description field at 0x54. A generic-only text replacement would
lose original behavior. No separate hook or note framework is needed.

The game was closed, all temporarily staged files restored, and the shared game
test reservation released. The sections below include earlier protocol milestones;
their historical test counts and limitations are not the current acceptance status.

## Prepare experimental input

Create the catalogue, world scan, profile, and binding plan using the
[configurator](post-install-configurator.md) and [binding interface](source-binding-drafts.md).
Then, from this repository:

```powershell
python tools/randomiser_config.py prototype prepare --catalog 'D:/RandomiserDrafts/catalog.json' --world 'D:/RandomiserDrafts/world.json' --profile 'D:/RandomiserDrafts/profile.json' --plan 'D:/RandomiserDrafts/bindings.json' --store 'D:/RandomiserDrafts/prototype'
python tools/randomiser_config.py prototype inspect --store 'D:/RandomiserDrafts/prototype'
```

Inspection also accepts `--revision <sha256>` for an earlier revision. Every
artifact has schema `flir-campaign-prototype-v1` and state `prototype-not-playable`.
Preparation produces immutable `revisions/<sha256>.json` and `.lua` companions,
then updates the separate `prototype-next.json` pointer. Reading verifies the
content hash, schema, and exact generated Lua bytes. It never falls back from a
missing requested revision to the next-run pointer. Keep revisions needed by saves.

A Lua companion is one table constructor unless the game's LuaJIT would refuse
it: LuaJIT compiles at most 65536 string, table and function constants and
65536 number constants into one function. Larger data (extended pools of a
whole EET campaign reach about 80,000) is built by several functions, each
holding at most about 32,768 constants. LuaJIT and Lua 5.1 to 5.4 load it as
the same table. Earlier versions wrote one constructor for such data too,
which no game could load. Reading accepts that refused rendering, and
preparation, export and Apply replace it with the loadable one of the same
data. Every other existing file must still match byte for byte.

Only finite physical relocation is accepted: creature inventory records, embedded
creatures, containers, and finite shop stock. This is format coverage, not proof
of runtime support for those sources. Script registrations and deliberate additions
are rejected in this prototype. It preserves the full selected record, including
all charges, flags, expiration, owner, and stock quantity. Source IDs and target
IDs are sorted before hashing. Limits are 4,096 reward units and 4,096 target slots;
oversized input fails without truncation. Tier selection and overlap resolution
are still future work. Christopher continues to own all creative selections.

The generated Lua contains data only. Strings use fixed-width escaped UTF-8 bytes;
configuration text cannot become Lua expressions. Integer values must be exactly
representable in Lua 5.1. JSON null becomes Lua nil, so null object fields are absent
in Lua. The prototype's required source/target arrays do not permit null entries.
Outputs remain outside the scanned game tree. Preparation neither invokes WeiDU
nor changes resources or saves.

## Controller and host responsibilities

`prototype/FLPRCore.lua` exports `Core.new(storage, resolve_revision)`. Storage
provides numeric `get`/`set` methods. The resolver must return the **validated exact
immutable revision**, not an arbitrary Lua file or the next-run configuration.
The Python reader validates generated companions; there is no native runtime
artifact loader yet. The core checks its logical shape but does not calculate SHA-256
or replace the compiler's full schema validation.

`prototype/FLPRGlob.lua` is an explicit adapter for the supported EEex numeric
GLOBAL read/write functions. It registers no listeners. Tests substitute ordinary
Lua tables for both the adapter's game API and the save store.

| Host call | Contract |
| --- | --- |
| `capture(manifest, seed, "confirmed-new-game")` | Requires a trusted New Game handoff and an unmarked campaign. Copies the revision, draws assignments with a private seeded generator, writes all fields, then publishes the committed marker. The caller supplies a seed in 1..2,147,483,646. |
| `restore()` | Resolves the saved revision and validates the saved assignment and journal state. An unmarked save remains inactive; a missing revision or incomplete capture never triggers a fresh draw. |
| `begin_source(index, witness)` | Requires exactly one resolved occurrence with the selected source ID, quantity, and full payload. Persists an in-flight journal before returning a logical removal command. |
| `begin_delivery(unit, witness)` | Requires confirmed suppression of that unit's original source and an available assigned target. Persists a journal before returning a logical creation command. |
| `on_observation_boundary()` / `observe(receipt)` | Confirmation must come from a later host observation, identify the same transaction and session, and prove the requested outcome. The boundary call itself is not evidence of an engine tick. |
| `pending_observation()` | After restore, requests fresh inspection of an in-flight transaction. Its `observe-only` result never permits replaying removal or creation. |
| `destroy()` | Invalidates cached state and pending callback identity when the host proves session destruction. Saved GLOBALs are unchanged. |

Capture stores the complete 256-bit revision as 16 bounded numeric chunks, the
seed, explicit per-unit target slots, and per-source/per-unit phases. A revision
and seed are not a unique campaign ID: progress belongs to each save's own GLOBALs.
Assignments use a private Park-Miller generator and Fisher-Yates shuffle, leaving
the game's random generator untouched. The same seed and revision reproduce the
same assignment across the tested Lua runtimes. Automatic fresh seed selection is
a future host responsibility.

All writes are read back. The campaign marker is committed last; a partial capture
cannot be resumed as a fresh run. Missing phase variables are invalid, not fresh
attempts. Only one mutation can be in flight. A partial journal disables progress
rather than guessing whether a command ran. If confirmation committed just before
journal clearing was interrupted, restore clears the journal without replaying it.
Host callback boundaries must catch storage/resolver errors and disable this path;
the core deliberately raises on invalid GLOBAL values or failed writes.

Witness identities must describe an exact runtime occurrence, not just an item
resref or owner template. The core validates the receipt's shape and identity;
the future host must obtain the actual evidence. Source confirmation proves the
selected quantity was removed and none of that selected occurrence remains.
Delivery confirmation must prove this transaction's new instance, exact payload,
and actual lootability. A matching pre-existing item or an increased count is not
sufficient. Bad outcome evidence quarantines the transaction without automatic
replay. Missing targets defer delivery and do not create a fallback pile.

A creature-kind target (`actor` or `cre-template`) may instead report the exact
instance as **held**: `created = 1`, `nonce_bound_instance = 1`, `lootable = 0`,
`held = 1`. The core stores delivery phase 4 (GLOBAL value 5). This counts as
attempted and is never delivered again, but it is deliberately distinct from
confirmed lootability. A held receipt for another target kind, a receipt
claiming both states, or a held source phase is quarantined or rejected on
restore. Proving that the creature's death drop makes the item lootable is a
separate native check. See [creature transport evidence](native-creature-transport-evidence.md).

The fixture host defers a delivery when target inspection reports current
unavailability: absent, elsewhere, ambiguous, party-owned, a familiar, a
full backpack, a busy message queue, or a non-empty pile. Inspection happens
before the core journals anything. A deferred reward is retried after
`Host.DEFER_TICKS` game ticks, other rewards continue, and the campaign stays
running rather than complete. Deferral never places anything elsewhere. Any
other inspection failure still disables the host.

A creature target that is dead when its reward is due gets it on the ground at
its corpse, once the corpse is in the visible area: `CreateItem` on the corpse,
then `DropItem`, bound to the delivery like a container add and confirmed as
lootable. A dead party member or familiar, or a corpse that holds another item
of the reward's name, still defers. See
[corpse ground evidence](native-corpse-ground-evidence.md).

A creature target that died and left no body gets its reward at the feet of a
party member instead. Its body is gone when no sprite of its script name is in
the visible area of its own, and the engine has counted its death in
`SPRITE_IS_DEAD<script name>`. Bodhi's vampires leave none, so the chapter 3
places need this. The member is the first in portrait order who is alive in that
area, has an empty backpack slot, holds no item of the reward's name and has no
message queued. `CreateItem` and `DropItem` run on that member, as at a corpse.
The receipt carries a notice with the item's name and the member's. The host
passes it to `deps.notice`, and M_FLPR shows it in the message log and, one at a
time, in the info popup (in-game.tra @1707). While no member can drop it, the
reward waits (`TARGET_INVENTORY_FULL`, `PARTY_ITEM_AMBIGUOUS`,
`TARGET_QUEUE_BUSY`). A missing creature whose death the engine has not counted
(not there yet, or gone alive) still waits with `OWNER_MISSING`.

A creature target marked `corpse_only` receives only that way: while it lives,
inspection reports `TARGET_ALIVE` and the reward waits. Such a target always has
a `handover`: once its reward reply has set the event, the reward goes to the
payee the reply marked instead, alive or dead the creature. This is the Sahuagin
king's own place, whichever of the two kings the area has, and Tolgerias's, who
pays in his house while TOLGER2 fights in the planar sphere
([configurator](post-install-configurator.md)).

A creature target with `inventory_reward` carries its reward in its backpack
until its reward reply hands it over: the reply's `FLPR_Transfer` (with the
place's row, and for a row of the base table its campaign and table) moves it
to the one the creature talks to, and the dialogue waits until that one holds
it. These are the carried rewards' places, the Sahuagin prince's and Adalon's.
The reply waits only while CIR runs or has not restored the adventure yet after
a load. Once CIR has stopped in the adventure (the player saw in-game.tra @1706),
the reply goes on at once and the reward stays in the creature's backpack. The
engine repeats a waiting action, so the creature would otherwise never finish
its reply, nor its quest.
A revision exported before its place had that route (Adalon's in CIR 1.0.2) has
a plain creature target there, whose reward is in the same backpack, so the
reply hands it over too; any other kind of target there stops the host
(`NATIVE_INVENTORY_REWARD_INVALID`).

A `first-of` target is a list of groups of creatures, each a creature target of
its own area under the place's identity; a group may be `corpse_only`. For each
reward a group with `eligible` candidates gives one member, drawn from the
campaign seed and the reward's unit as a group target draws its recipient; a
group without them gives every member in order. Inspection takes the first of
these creatures that can receive the reward now; while none can, it reports the
reason of the first one in the visible area (else `AREA_UNAVAILABLE`), so the
reward waits. Delivery goes to the creature inspection reserved. These are Item
Randomiser's chapter 3 places: the drawn vampire alive, else the drawn shadow
thief or Aran at his corpse ([configurator](post-install-configurator.md)).

Each command also carries an opaque Lua session table. A receipt must preserve
that exact reference; it cannot serialize or deep-copy it. Restore/destroy/new
capture renew the reference, rejecting callbacks left over from another session
even when its revision, index, and nonce happen to match. Reload recovery must
reacquire world evidence and use the new observation request's session reference.

## Remaining engine gates

[Pinned EEex source evidence](runtime-feasibility-evidence.md) establishes numeric
GLOBAL access but does not establish a reliable New Game completion callback or
safe exact-copy removal. Lua initialization is process startup, and sprite-loaded
callbacks run inside unmarshalling. Neither is sufficient to activate this path.

Before a playable slice, prove the lifecycle handoff; persist/reload real campaign
state; resolve exact, nonoverlapping owner instances; suppress originals before
player access; inspect mutable shop stock; and verify lootable delivery. Also prove
that source suppression does not require the player to visit the source before a
distant assigned reward can become available. Preserve excluded originals and
independence when switching between two campaigns in one process.

The legacy installer removes originals at install time. The new opt-in path must
preserve those originals and avoid reinterpreting existing legacy tokens. Swapping
a shared manifest or mutating shared demanded resource bytes cannot establish
campaign isolation. None of these migration or native adapters is enabled here.

## Validation boundary

```powershell
python -m unittest discover -s tests -p 'test_postinstall_*.py' -v
python -m unittest discover -s tests -p 'test_campaign_runtime.py' -v
```

The first suite needs Python 3.10+ only. The second uses the separately installed
test dependency `lupa` (validated with 2.8), including Lua 5.1, Lua 5.3, and LuaJIT
2.1. It fails if a requested runtime is missing. It exercises synthetic save stores,
exact-copy ambiguity, changed next-run settings, interrupted writes, reloads,
quarantine, delayed observations, stale callbacks, and compiler-to-Lua integration.
These checks establish the logical protocol, not game-engine acceptance. No live
game, installed mod, or player save was modified or used to claim persistence.

### Separate native fixture

The opt-in disposable BG1 fixture now has bounded native delivery and serialized
save evidence. Its current results, remaining checks, and QA save-render crash
are tracked in [native fixture status](native-fixture-status.md). Those results
do not activate the general adapter through the normal installer or establish
full-game acceptance.

The [26 September follow-up](native-acceptance-20260926.md) separately verified
B reload in a fresh process and two new campaigns under unchanged B settings.
It also records the first fresh campaign's manual-save refusal, the QA diagnostic
fix, the accepted initial autosave evidence, and complete test-process cleanup.

The later [creature continuation](native-creature-acceptance-20260926.md) verified
multiple held rewards, save/load, controlled death after delivery and actual
pickup. Death changed item pointers; the runtime still records those deliveries
as held rather than inventing an automatic lootable receipt.

The [controlled receipt continuation](held-drop-receipts-20260926.md) adds saved
delivery identities and an explicitly armed observer for a new quiet fixture.
Its 777 offline checks pass. A fresh native run verified three automatic
held-to-ground confirmations, ordinary pickup, serialized saves, and reload of
the confirmed and looted states without duplication or a new seed. The test
game closed normally and all staged files were restored. These results remain
limited to the explicitly controlled fixture; general held-item lifetime
tracking and unfinished-save recovery remain open. The later
[campaign source guard](campaign-source-guards-20260927.md) separately verifies
early reward pickup and unvisited-original suppression for one authored container.

### Scanner status and installation check

A read-only scan of the isolated EET installation on 2026-09-22 indexed 3,544
effective items. Resource access also read all 8,209 CREs and 1,016 AREs, with its
installation snapshot checks passing. These are item-index and resource-access
results, **not a completed world/source catalogue**.

The resource reader and world resource-identity validator now retain legitimate
internal spaces such as `MONKTU 8.DLG` in KEY entries and override filenames.
Leading/trailing spaces, control characters, path separators, and unsafe filename
metacharacters remain rejected.

The earlier world scan stopped at `BDABIS1D.CRE`: its physical inventory record 0
contains an all-NUL item resref, although slot 0 references that record. Record 1
contains `S1-4` and is referenced by slot 9. The parser raised
`Invalid world resource reference.` On 2026-09-23 the offline scanner was changed
to preserve empty physical records explicitly, including their payload and
ownership, and reject them for binding/compilation. Valid subsequent records keep
their original indexes. See [the representation contract](source-binding-drafts.md).

The authorized read-only rescan ran on 2026-09-24 after the other local BG tasks
were idle and no game or installer process was running. The item scan succeeded
with 3,544 items and explicit `en_US` localization. Direct raw-byte comparison
also confirmed the real `BDABIS1D.CRE` preserves both physical records, their
payloads, indexes, and slots under the new parser.

That first world scan **failed**: `BDCORPSE.CRE` has zero item records, while slots
21 and 22 reference indexes 0 and 1. Unlike an existing record with an empty
resref, those records do not exist. The scanner retained its strict rejection;
no resource was skipped and no world catalogue was published. The subsequent
offline change now preserves these references as owner diagnostics and excludes
affected sources/targets from binding and compilation. The retry passed that
resource but stopped at a malformed item name in `OHDFaer.CRE`. CRE discovery now
preserves those raw name bytes and likewise quarantines the owner. The last newly
observed shape, incomplete embedded actor storage, is also retained as unresolved
and excluded from target selection. The final scan completed with 41,577 source
records and 24,305 target candidates; the installation-snapshot check passed.
This completes physical discovery, including explicit diagnostics, not runtime
acceptance or interpretation of all scripted grants.
See [the rescan evidence](installation-scan-20260924.md) for byte-level details.

All results were saved outside the installation. This check launched no game or
installer and changed no installed resource or save. A successful parse remains
discovery evidence, not proof of safe general runtime suppression or lootability.
