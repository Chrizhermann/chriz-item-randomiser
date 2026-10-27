# Mode 1: reward before visiting its original area

The integrated fixture passed native New Game, early reward pickup, source
suppression, selected/excluded campaign reload, returned-item and interrupted
confirmation checks on 27 September. Several configured sources across two areas
also passed, followed by three existing BG1 rewards across three real source areas.
Container, static creature and finite shop sources now pass representative samples
on the pinned Windows build. Shop destinations and purchase pricing are described
below. General source support and normal installer integration remain unfinished.

## Implementation

- `FLPRCore.lua`: source phase 5 (saved value 6) means protected before access.
  Phase 2 still means observed removal. A live guard authorizes early delivery;
  save restoration requires rebinding it. Existing assignments never reroll.
- `FLPRHost.lua`: prepares the guard between campaign capture and character
  acceptance, delivers rewards, then observes the original without replaying
  delivery. Ordinary visible sources retain their existing removal flow.
- `FLPRGat.lua`, `FLPRCampaignPolicy.lua`, and `FLPRGateAsm.lua`: install before
  area loading and select policy from the full saved campaign revision and source
  phase. Area, container, item record and payload now come from the campaign
  manifest and pristine ARE; they are no longer restricted to FLPRU1/FLPRI1.
  Each source has separate protection/removal state. They share five hook entry
  points and a pristine copy per area. Several saved revisions can coexist;
  each uses its own source indexes and protection state. The pinned BG2EE
  executable remains a prototype limit.
- Item Randomiser's second copies of moved rewards (the revision's `duplicates`,
  see [post-install configurator](post-install-configurator.md)) belong to their
  source and have a policy kind of their own: the gate removes a copy wherever
  its holder loads while the source is in any phase but kept; a kept source
  keeps it. A creature or shop holding one is rewritten as a whole from its
  exported original (`<revision>.duplicate-<n>.original.cre|sto`) together with
  any source in it, and a shop keeps the stock a saved game restored; an area's
  container item is skipped once per load of the pristine area. Each copy is
  checked against the reviewed table rows and its holder's bytes. New Game
  enrols the copies without an event row. New Game and loading check every
  copy's state; each later host step checks at most four in turn
  (`Gate.COPY_CHECKS`).
- The guard compares the complete authored ARE input and ties the result to the
  same area-load invocation and constructor inputs. Saved area records can move;
  matching an item name or original index alone would delete a returned copy.
  Confirmed removal disables further suppression. A save between removal and
  confirmation remains protected; absence alone never creates a removal receipt.
- `FLPRNat.lua`: creates the declared reward and observes removal. It does not
  demand or mutate the unvisited resource. Payload, owner and quantity checks
  concern player items, not the integrity of the disposable lab.
  Bootstrap checks item delivery eligibility before installing suppression.
- Removal is corroborated by counting the original's name in its holder.
  Rewards are delivered before that count, and the holder may get one of that
  name: the draw can give a place its own item back, and many scrolls share a
  name (12 of 678 rows of one BG2:EE campaign, 27 October; the kuo-toa
  prince's BRAC16 stopped CIR on 2.7.3 with `CAMPAIGN_GATE_WORLD_MISMATCH`, as
  it would have in 1.0.2). The count leaves out each reward instance CIR put
  into that holder in this session (`Adapter.placed`: creature, container or
  ground pile). The gate's witness is this session's too, so a reload proves
  nothing and needs no such record.
- The count needs the holder the gate saw, in the area load it saw: the witness
  names both. When it cannot be made now, the proof waits (`SOURCE_NOT_LOADED`),
  as after a reload: the source stays protected and no reward waits for it.
  That is when the area is not the visible one, when the party left it and it
  was loaded again (`AREA_CHANGED`), and when the holder is gone, replaced or
  doubled (`OWNER_MISSING`, `OWNER_CHANGED`, `OWNER_AMBIGUOUS`). Only a holder
  that still has another item of the original's name stops CIR
  (`CAMPAIGN_GATE_WORLD_MISMATCH`). Before, each of these stopped CIR, as in
  1.0.2. The BG2:EE 2.7.3 lab saw both on 27 October: a creature removed by
  `DestroySelf` before its proof ran (`OWNER_MISSING`), and a party that left an
  area at once and came back after other areas (`AREA_CHANGED`). A proof runs
  only in a host step with no reward on its way, and each step proves 16
  sources in turn (`Host.REMOVAL_CHECKS`). So it can come seconds after the
  area loaded.

## Native result

Selected campaign A (revision `1fff717d...`, seed `1724153183`) gave three rewards
to two synthetic creatures while FLPRU1 was unvisited. A ready save reloaded with
the same assignments. Ordinary creature deaths and pickup put all three rewards
in the player's inventory before first entry to FLPRU1. On entry, the guard
suppressed exactly the original FLPRI1, preserved FLPRX1 in its container and
FLPRI2 in the other container, and confirmed source removal on a later host tick.
The looted save reloaded with all three rewards and no restored original.

Changing only the next-run configuration produced excluded campaign B (revision
`e0ff63e8...`, seed `772708706`): two creature-source rewards, with FLPRI1 retained
in its original container. A/B switching in one process and reloading both in a
fresh process preserved their respective seeds, assignments and item ownership.
No reinstall was needed between campaigns.

The first two launches found a real bootstrap bug before hooks were installed:
`EEex_WriteLString` truncates binary data at NUL; `EEex_Write8` requires signed
bytes. The corrected copy preserves every byte, including NUL and values above
127. The focused regression uses those actual native semantics. One ready-load
request also used a display name instead of the required save-folder name; the
corrected request completed without changing mod code.

Evidence: `work/campaign-guard-native3-20260927/` (New Games and same-process
checks), `work/campaign-guard-native4-20260927/` (fresh process). Named saves are
59 (A ready), 60 (A looted), and 61 (B original). All game sessions quit normally
on the non-input desktop. The 25 staged files were restored and the reservation
released. The new saves require this fixture and runtime to be staged again;
their immutable campaign artifacts were retained.

After the binary-copy fix, all 893 Python checks passed with the declared test
dependencies. The twelve available PowerShell suites, Lua suite and 35 WeiDU
parse checks also passed. The legacy sentinel dependency check remains unverified
because its old decompiled-script fixture was removed with its lab; see
[repository checks](../tests/README.md).

### Returned items and interrupted confirmation

Campaign A's returned FLPRI1 survived save/reload in its original container after
confirmed removal (save 62). For the interruption case, host confirmation was
temporarily held while the native gate suppressed the original. Save 63 therefore
contains no original but still records protected source state 6. Reloading it
preserved all three collected rewards without replaying suppression or delivery.
A player-returned FLPRI1 saved in that protected state also survived (save 64).
Both interrupted saves passed again in a fresh process. Absence was never
misreported as a confirmed removal receipt.

Evidence: `work/campaign-guard-return-20260927/` and
`work/campaign-guard-return-reload-20260927/`. These use ordinary death, pickup
and drop actions on synthetic content; the held host poll is test instrumentation.

### Configured container

Campaign C (`8f516963...`, seed `1490592260`) binds
`are:flprv2/container/1/item/3`: FLPRX2 in the type-1 `FLPRV2 chest` at 1200,650.
It delivered all three rewards before visiting FLPRV2. Ordinary death and pickup
put them in the player's inventory. First entry removed exactly FLPRX2 from the
configured chest, preserved both FLPRI1 and FLPRX1 in the other container, and
confirmed removal. Save 65 (`IR Configured Source 0927`) reloaded with the same
seed, assignments and items, without repeating suppression.

The first launch exposed a wrong ARE item-count offset before hooks installed:
the count is a word at 0x76, not the vertex offset at 0x7C. The parser and its
synthetic fixture were corrected before the successful run. Evidence:
`work/configured-container-native-20260927/` (bootstrap failure) and
`work/configured-container-native2-20260927/` (successful run). All sessions quit
normally; all 28 staged paths were restored and the reservation released.
After the offset fix, the complete Python suite passed (903 tests). No new lab
tooling was added; the changes extend the runtime and its focused regressions.

### Several sources in one campaign

Revision `88e96509...` relocates FLPRI1 and FLPRI2 from two FLPRU1 containers,
plus FLPRX2 from FLPRV2. Seed `448563856` delivered all three before either area
was visited. Pickup succeeded. Visiting FLPRV2 first confirmed only source 3;
save 66 (`IR Multi Partial 0927`) preserved source states 6,6,3 and all rewards.
Reloading and visiting FLPRU1 removed the other two originals while preserving
FLPRX1. Save 67 (`IR Multi Complete 0927`) preserved states 3,3,3 and ownership.
Both saves passed in a fresh process, including first visiting the still-unvisited
area from save 66. A second New Game used seed `1900851915` and passed the reverse
visit order; this small pool happened to produce the same target mapping.

Evidence: `work/multiple-containers-native-20260927/` and
`work/multiple-containers-reload-20260927/`. Both sessions quit normally; all 28
staged paths were restored. The offline suite passed 910 tests. Review caught a
missing group reset in the New Game path; the fix and focused regression passed
before live testing. Full-catalogue performance has not been established.

### Changing the next-run selection

Revision `04283af3...` excludes FLPRI2 from the preceding three-source profile.
Its new campaign (seed `2056225973`) delivered and allowed pickup of the two
selected rewards, suppressed their originals, and left FLPRI2 in FLPRU1B.
Loading old save 66 in the same process restored its three-source selection,
original seed and collected rewards; first visiting FLPRU1 removed both of its
selected originals. Loading new save 68 (`IR Profile B 0927`) restored only its
two assignments and preserved the excluded original. Save 68 passed again in a
fresh process, without another suppression or delivery.

The fixture's `source-gate.lua` now accepts a list of immutable revisions to
preload (the previous single string still works). `next.lua` selects the next
New Game; saved campaigns select their own revision. Keep all revisions needed
by saves, and restart the game after adding one to the preload list. All profiles
must describe the same installed area resources. This removes the one-revision
restriction without a TP2 reinstall; automatic export and player UI remain open.

Evidence: `work/profile-switch-native-20260927/` and
`work/profile-switch-reload-20260927/`. Both sessions quit normally; all 28 staged
paths were restored and the reservation released. The full offline suite passed
917 tests. This change reuses the existing lab helpers.

### Configurator-produced settings

The CLI now exports reviewed campaign bindings into the runtime configuration
layout, retaining earlier exported revisions. Scanning the copied fixture
resources, preparing A/B profiles and exporting them produced revision
`5d6f5d37...` for the two-item selection. Its New Game (seed `669081018`) passed
early delivery, ordinary creature death/pickup, both original-source removals
and preservation of the excluded FLPRI2. No endpoint files were hand-edited.
Evidence is in `work/configurator-export-20260927/` and
`work/configurator-export-native-20260927/`. The game quit normally and all 28
staged paths were restored. The offline suite passed 921 tests; a subsequent
review reproduced a same-container duplicate-copy limitation and added an export
rejection with a passing focused regression. This remains candidate configuration
export for the supported subset, not a general mod installer.

### Existing BG1 content

The configurator exported three existing Randomiser items and two existing targets
from the installation scan, checking the selected current resources. No runtime
changes were needed. Revision `27b1c683...`, seed `1969154220`, assigned Flail +1
(`blun03`) and Chain Mail +1 (`chan02`) to Zargal, and Halberd +1 (`halb02`) to
Teyngan. This is a technical sample, not an approved new gameplay preset.

Both recipients received their rewards before any source area was visited.
Ordinary death and pickup actions collected all three. First visits removed the
selected originals from BG4701 container 0, BG5001 container 3 and BG5202
container 0, leaving their other loot present. Save 69 (`IR Real Early 0927`)
reloaded with the same seed, collected items and still-protected sources. Save 70
(`IR Real Complete 0927`) passed in a fresh process with all three collected
rewards, confirmed removals, and no repeated suppression or delivery. Reading
its saved areas independently confirmed the originals absent and other loot
retained. Unrelated record bytes are not claimed identical: the engine normalizes
some item flags and charges on load.

Evidence: `work/existing-bg1-sample-20260927/`,
`work/existing-bg1-native-20260927/`, and `work/existing-bg1-reload-20260927/`.
Area travel and death were scripted; this verifies delivery and pickup, not combat
balance. The test character died during the first source inspection, after the
early checkpoint. Reloading that checkpoint and temporarily enabling Story Mode
allowed inspection to finish; Story Mode was restored to 0 before save 70.
Both sessions quit normally, all 28 staged paths were restored, and the reservation
was released. No new lab tooling or production code was added for this sample.

The private test profile now has all five audio volumes at 0, persisted across
restart. Hidden desktops still play audio: when rebuilding this profile, mute
ambient, SFX, voices, music and movies with `Infinity_ChangeOption(id, 0, 7)` for
IDs 1, 2, 3, 4 and 22; verify with `Infinity_GetOption(id, 7)` before world testing.

### Creature originals: native backpack sample passed

The exporter and campaign guard now support a selected backpack item on a named
static ARE actor, using either an external CRE or an embedded CRE. An external
template must map to exactly one scanned static actor. This does not establish
scripted-spawn coverage or a general occurrence policy. Current limits are one
selected original per actor, one copy of its resref, and the existing nonstackable,
unexpired, droppable-item restrictions. Equipped originals require an explicit
replacement choice; the staff example below now passes in game.

The existing ARE load binding is reused. On the pinned executable, constructor
calls at RVAs `18C448` and `18C591` receive raw CRE bytes in `rdx`, their size in
`r8d`, actor record in `r13`, and area in `r14`. The hook substitutes an owned copy
with the selected record removed. It never edits shared demanded CRE bytes.
Inventory compaction follows the original mod's slot-remapping behavior; the
private edit helper also supports replacements while preserving charges/flags.
Weapon selection is a separate slot selector, with engine value 1000 for fists.

Focused export, inventory, Core/Host, native adapter, and instruction-emulation
checks pass. The complete synthetic path produces a removal witness accepted by
the campaign controller, preserves the input CRE, and leaves other actors and
saved AREs alone. Existing container checks still pass.

With Christopher's renewed test authorization, the configurator exported Leather
Armor +1 (`leat02`) from `cre:delsvir/item/6` to Zargal
(`are:bg3700/actor/39`), revision `dd7c0d54...`. New Game seed `1299614590`
delivered it before BG1000 was visited. Ordinary death and pickup actions collected
it. First entry to BG1000 suppressed exactly that original and confirmed removal.
Save 71 (`IR Creature Complete 0927`) reloaded with the same seed and assignment,
one collected copy, and confirmed source state. Independent saved-area inspection
found Delsvir's original nine other item records, with only `leat02` absent.

Evidence: `work/creature-source-native-20260927/`. The game ran muted on the
non-input desktop and quit normally; all 26 staged paths were restored and the
reservation released. Story Mode was temporarily enabled for inspection and
restored to 0 before saving. The reused observation helpers needed single-source
reporting and completed-host handling; no production fix was necessary. This
checks the external-CRE constructor branch and same-process reload. Embedded-CRE
native acceptance and a fresh-process reload of this creature sample remain
unverified; their offline checks are not substituted for those results.

## Continue

Equipped originals now use optional binding-plan `replacements`, mapping an exact
source ID to a substitute resref or `blank`. The campaign freezes that choice and
the substitute's resource hash. Replacement changes only the eight resref bytes,
matching `del_cre_itm`; slot, charges, flags and selected weapon remain intact.
The source compiler, exporter and native constructor checks pass (72 focused
tests across the affected suites; no full-suite rerun).

The original BG1 list's `staf02 -> staf01` rule for Silke passed natively as
revision `9c596cf8...`, seed `350731521`. Zargal received and dropped the enchanted
staff before BG3300 was visited, and pickup succeeded. First entry replaced only
Silke's selected staff; live equipment showed `staf01` selected in engine slot 35.
Save 72 (`IR Equipped Complete 0927`) reloaded with the same campaign and collected
reward. Independent saved-CRE inspection confirmed the normal staff in CRE slot 9,
weapon selector 0, and her other slotted item present. The authored CRE also has
an unslotted `leat01` record; the private replacement preserves its bytes, but the
engine does not retain that unreferenced row in the saved inventory.

Evidence: `work/equipped-source-native-20260927/`. The muted, non-input-desktop
session quit normally; all 26 staged paths were restored, Story Mode returned to
0 before saving, and the reservation was released. This verifies one ordinary
replacement, not special replacement rules or complete source-family coverage.
Shop sources and recipients are covered below; scripted rewards still need runtime support.

Finite shop sources use the original Mode 1 rule: remove the exact sale row and
retain its quantity as reward units. The private STO rewrite preserves all other
rows and sections. On the pinned executable, `CStore::SetResRef` at RVA `250765`
receives the private bytes; `250967` verifies the actual constructed sale list.
The SAV member hook at `405295` distinguishes already saved stores, so authored
stock is not reapplied to saved inventory. Current export limits are one selected
sale row per shop, no duplicate selected resref, and the existing item restrictions.

Live sample `sto:inn3351/item/8` (`chan02`) -> Zargal passed with revision
`5965da30...`, seed `1625428348`. Delivery initially exposed a real bug:
`GiveItemCreate` determines identification from the item definition rather than
the original inventory flag. The adapter now preserves that flag on its exactly
identified new message before the engine consumes it; other flag bits remain
unsupported. A corrected run delivered identified armour, ordinary death dropped
it, and pickup succeeded. Opening INN3351 removed only that row. Save 73
(`IR Shop Complete 0927`) and same-process reload preserved the campaign and
collected item. Reopening recognized the saved STO and did not repeat removal.
Independent SAV inspection found all 15 other sale rows byte-for-byte unchanged.

Evidence: failed reproduction in `work/store-sources-native-20260927/`, accepted
run and saved evidence in `work/store-sources-native-20260927b/`. The former was
aborted after finding that the remote reader did not cover the shop menu; the
latter temporarily added that menu to the existing console, then quit normally.
All 27 staged paths were restored, Story Mode returned to 0 before saving, and
the reservation was released. No real game or saves were changed. INN3351 is a
type-2 inn resource: this checks its stock loading and persistence, not ordinary
buy/sell transactions. The later shop-destination sample checks those separately.
Focused export, stock rewrite, emulated store lifecycle,
creature regression and identification tests passed; no full-suite rerun.

### Shop destinations and campaign purchase prices — 28 September

`FLPRShop.lua` now delivers through native `AddStoreItem` and normal store saving.
The six pinned hooks preserve the appended item's payload, quote its campaign base
price before ordinary merchant modifiers, keep resale copies separate while the
reward remains for sale, and consume the surcharge when that reward leaves stock.
`shop_pricing` defaults to 120% with optional per-item base prices; campaign manifests
freeze these choices. Old manifests and their native exports retain their original
field set and hashes. The UI exposes both controls.

The representative campaign (`1b73dc84...`, seed `649613330`) moves identified
`chan02` from INN3351 row 8 into RIBALD before visiting the source. A test override
of 2,000 multiplied by 120% yields a 2,400 base and a 3,960 quoted purchase price
with this party's merchant modifier. Temporarily disabling only the surcharge
quoted 2,640, and all 139 ordinary stock prices and records stayed unchanged.
Selling a separate control copy left two distinct rows priced 3,960 and 2,640.
Save 74 (`IR Shop Stock 0928`) retained both across same- and fresh-process loading.

The corrected purchase took exactly 3,960 gold (100,520 -> 96,560), delivered one
identified armour, and left the ordinary copy at 2,640. Resale still offered 400.
The engine temporarily sets zero charge slots to one on purchase, then serializes
this uncharged armour with zeros; this is ordinary `CStore::GetItem` behavior.
Opening INN3351 removed exactly the selected original. Save 75 (`IR Shop Bought
0928`) independently contains one party copy, `FLPR_P0001=1`, confirmed source and
delivery phases, all 15 other source rows, and all 139 original Ribald rows unchanged.
Fresh-process loading of save 75 also passed: the same campaign, completed source
and delivery state, ordinary 2,640 stock price and 400 resale quote were retained.
Selling the bought reward merged it into the ordinary stock at quantity two, with
no restored surcharge. A final purchase from save 74 using the completed removal
hook again charged exactly 3,960 and consumed the campaign price flag. Save 76
(`IR Shop Final 0928`) contains the same campaign globals, party reward and exact
saved source/destination STO bytes as save 75.

The duplicate-copy sample exposed an engine selection bug: refreshing stock copied
one selected row to both equivalent items. The hook now matches quoted price and
consumes each old selection once. An initial hook placement caused a reproduced
crash because `2E7E08` is an existing branch landing; the corrected hook goes between
the CMP and CMOVE at `2E7E00`, leaving that landing intact. Accepted session `d`
then selected and bought only the intended reward. Failed evidence was retained.
The removal hook at `25052A` compares the actual store resref, item resref and
priced row pointer before consuming the adjustment, covering the shared Buy/Steal
removal path without relying on a stale pointer alone. The final Buy path passed
natively; Steal transactions have not yet been tested.

Evidence is under `work/shop-recipients-native-20260928` and suffixes `b` through `e`.
All sessions used the muted non-input desktop; the last quit normally, restored
28 staged files and released the reservation. These checks cover one finite armour
reward, not multiplayer, every item family, or ordinary installer integration.
Current exports reject a destination already stocking a selected item and a shop
used as both selected source and target. Multiple distinct items may share a shop;
multi-copy sources currently require shop capacity one.
All 337 focused profile/compiler/export/editor/native adapter/shop/host checks
passed across the available Lua runtimes where applicable. This is not a full-suite
rerun. Browser inspection confirmed the pricing controls and legacy configuration
loading; the editor is serving the existing saved scans and workspace.

### Several selected originals of one item — 28 September

The editor now uses independent source checkboxes, preserves selections through
paging/save/load, and shows finite shop quantities. The native adapter accepts
repeated item resrefs across distinct source owners and tracks creature rewards
by their separate pointers, slots and payloads. A shop with capacity greater than
one still rejects a pool that could assign it two identical rewards.

The browser-created two-Chain-Mail sample (`9fdcd340...`, seed `1183639817`)
relocated BG0504 container 8/item 17 and BG5001 container 3/item 5 to Zargal.
Both arrived in separate inventory slots, dropped on normal death and were picked
up. Each source guard suppressed exactly one original; all other item IDs and counts
in both containers matched the scan. The unchecked BG0606 container 1 original
remained present. This is not raw payload equality: a remaining potion's runtime
flags/charges differed from its raw ARE record, so both observations are retained
without claiming that stricter comparison passed.
Evidence: `work/multiple-originals-20260928/acceptance.json`.
The muted hidden game quit normally, 28 staged files were restored, and the
reservation was released. This sample was not saved/reloaded; the general lifetime
observer remains unfinished. Focused editor/export/adapter/host/gate tests passed;
no full-suite rerun. The saved two-original configuration remains in the editor.

### Authored BLPOOL quest reward — 28 September

The first scripted original is implemented and tested: `bcs:blpool/grant/pool-reward`
relocates BLUN12 through the same campaign/delivery path as physical originals.
The adapter follows the original mod's [`baf/blpool.baf`](https://github.com/Chrizhermann/chriz-item-randomiser/blob/172872dad6496c1d6eaf1a09b52bc3189da23b96/baf/blpool.baf)
and special-delivery code.
A small static BCS patch branches on the campaign's saved `FLPR_QPOOL` flag.
Selected campaigns replace the grant with an `FLPR_XPOOL` event witness; excluded
campaigns keep the original grant. Both retain Clicked/Range/Pool conditions, the
bite spell and Pool progression. Selected campaigns use the existing empty-pool
display instead of falsely announcing a mace. No action interception hook is used.

In the hidden, muted test session, campaign `561f093f...` (seed `355730995`)
delivered the mace to Zargal before the source area was visited. It dropped on
death and was picked up. The actual BLPOOL region then advanced Pool 0 → 1 → 2,
set its consumed witness and confirmed source removal without adding a second
mace. A repeat interaction left one mace. Interactions used the region's native
`virtual_OnActionButton` after positioning the test character; this tests the
engine's quest script, not OS mouse input.

A second fresh campaign (`8d0e16f5...`, seed `1104926441`) excluded BLUN12 while
randomising two physical originals. Its pool gave exactly one normal mace, left
the relocation flags zero and gave no second copy on repeat interaction. Loading
save `000000077-IR Pool Selected 0928` restored the first campaign's seed, selected
and consumed flags, confirmed source phase and one party mace. Its serialized GAM
independently contains those values. Evidence: `work/scripted-reward-20260928/`.
Returning to the saved pool and interacting again retained Pool 2 and one mace.
The game quit normally; 30 staged files were restored and the reservation released.

Coverage is deliberately one reviewed BCS resource, not arbitrary BCS/DLG grants,
scripted recipient rewards, modified versions of BLPOOL, or general quest safety.
The bite action is preserved in compiled code; exact damage was not asserted on
the protected test character. Death/drop/pickup are observed, while the existing
general lifetime-observer integration remains unfinished (delivery still held).
The editor exposes this supported quest original and preserves its registration.
The normal TP2 still does not install the prototype.
Focused compiler/export/editor and Lua guard tests passed, including a second
export after installing the static patch. Browser selection, save and validation
retained the reviewed registration. Tests use portable fixtures, not private scans.

### Tiered draws and stocked container recipients — 28 September

Bound campaigns now draw independently within named tiers and support optional
item loss and keeping originals. The latter also excludes explicitly paired
recipient slots. Source guards leave kept originals alone; loss still removes
the originals. Static container recipients append rewards to existing contents
through native CreateItem, including multiple rewards in one container.

A four-item sample from the original BG1 tables used tiers 24/25 and three stocked
containers. Two New Games produced different assignments (`1667914810` and
`420430067`) within those tiers. All four originals were suppressed in campaign A;
campaign B also exercised two rewards sharing one chest. Saves 78/79 independently
contain the expected assignments, source phases and chest inventories. Loading
save 78 after two later configurations restored its original revision and draw;
revisiting BG1401 retained all 14 items and their live payloads without duplicates.
Ordinary unused charge fields normalize during serialization; exact serialized
payload equality was checked for the selected noncharged rewards only.

Separate 100% keep/loss campaigns passed at all four sources: keep left originals
present and added nothing; loss removed originals and added nothing. These two
variants were not saved/reloaded live. The sample did not test ordinary chest
pickup, and lock/trap preservation is covered synthetically. A review reproduced
a mixed-keep capacity shortfall; compiler/runtime preflight now reject insufficient
capacity before capture, with a focused regression. That preflight correction was
tested offline after the live session.

Evidence: `work/tier-containers-20260928/acceptance.json`. The muted non-input
desktop session quit normally, Story Mode returned to 0, 30 staged paths were
restored, and the reservation was released. Full original presets still need
binding, groups, remaining scripted/virtual rewards, token expansion and overflow
allocation. The existing original tables and rules remain the source for that work.

The existing test installation, private profile, remote console and character
helper are sufficient; rebuild the test copy if necessary. Before live work,
check actual game/installer activity and the shared reservation. Another chat
doing offline code or synthetic tests is not itself a live-game conflict.

The completed one-shot staging helpers under `work/` show the setup used; do not
rerun consumed requests. The existing lab and helpers are sufficient.
General shop coverage and script interception are unfinished.
The fixture observes physical pickup but keeps delivery phase 4 (held); the
general lifetime observer is not integrated. Choose representative existing
content autonomously for technical tests. New gameplay selections, capacities
and fallback policies remain Christopher's choices.
