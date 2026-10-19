# Source and target binding drafts

This is milestone 2 of the [approved Mode 1 design](plans/2026-09-22-mode1-postinstall-profiles-design.md).
It reads installed resources and freezes explicit choices for later runtime work.
It does not patch the game, suppress originals, assign random rewards, or activate
a profile. The runtime does not consume these drafts yet.

Christopher owns the creative choices. No production item pool, target list,
reward count, campaign crossover, or curse design is added by this tooling.

## Discovery

First create an item catalogue using the [post-install configurator](post-install-configurator.md).
Then scan the same idle installation:

```powershell
python tools/randomiser_config.py world scan --catalog 'D:/RandomiserDrafts/catalog.json' --out 'D:/RandomiserDrafts/world.json'
python tools/randomiser_config.py world sources --catalog 'D:/RandomiserDrafts/catalog.json' --world 'D:/RandomiserDrafts/world.json' --item <resource-id> --limit 20
python tools/randomiser_config.py world targets --catalog 'D:/RandomiserDrafts/catalog.json' --world 'D:/RandomiserDrafts/world.json' --query <search-term> --limit 20
```

Replace angle-bracket placeholders. These commands expose source/destination
identities and therefore location information. Ordinary item search remains free
of locations. The scan response reports counts, not a spoiler list. Sources require
one item identity; targets require a search term or `--kind` (`cre-template`,
`actor`, `container`, `store`). Results are paginated, with limits of 1-100.

The effective override resource wins over the archive copy. The scan covers:

- CRE V1 inventories, including slot references and unslotted/aliased records.
- ARE V1 container inventories and actor records, including embedded creatures.
- STO V1 stock, quantities, charges, and raw infinite-supply flags.
- The content hashes of effective BCS and DLG resources, for explicit registered
  grant rules. Their contents are not decompiled or interpreted.

External ARE actors refer to a CRE template. The scanner does not duplicate that
template's inventory as another discovered physical record. A template can be used
by multiple actors or repeatedly spawned by scripts; its number of world instances
is not inferred. Actor appearance, transformations, lootability, and access remain
unverified. A single discovered record is not a guarantee of one obtainable copy.

The scanner preserves expiration, all three charge/stack words, flags, record
indexes, and byte offsets. Embedded offsets refer to the containing ARE. Container
ownership, creature slots, and store stock remain explicit. Unlimited/unknown stock,
empty stock, missing item definitions, orphan records and shared ownership cannot
be selected for ordinary finite relocation. T2 dynamic containers are marked;
their generated contents are outside this physical scan.

An all-NUL item reference is retained as an empty physical record, with `item`
and `record.resref` equal to `""` and concern `empty-item-reference`. Its original
index, byte offset, payload, slots/container ownership, and stock fields remain
in the catalogue and its fingerprint. Later records are never renumbered. Empty
records cannot be selected for relocation or compiled into campaign rewards;
they do not appear in item search. The `world scan` response includes their
aggregate `empty_item_records` count within `source_records`, without exposing
locations. A nonempty reference with a missing definition retains the separate
`missing-item-definition` concern. Malformed nonempty references fail parsing by
default, and an area or store with one is left out ([Unreadable files](#unreadable-files));
CRE discovery alone can preserve them for the quarantine described below.

This is structural preservation, not a claim about how the engine handles an
empty record. Resource identities, selected items, and compiled item payloads
still require nonempty names. Existing v1 catalogues without empty records retain
the same content and fingerprints; older readers reject the newly representable
empty-record catalogues rather than silently treating them as loot.

A dangling CRE slot is different: its raw word points beyond the declared item
table, so there is no physical item record to retain or relocate. Discovery keeps
the complete 40-word slot table and original header offsets in
`selector.inventory_diagnostics` on the creature target, including creatures
with zero items. `cre_offset` locates an embedded CRE in its ARE; the item and
slot offsets in the diagnostic stay relative to that CRE. Physical item rows keep
their original indexes, payloads, and valid slot references. No missing rows are
invented and no dangling word is converted to `0xFFFF`.

Affected source records also carry `record.inventory_diagnostics`. External ARE
actors inherit their template's data as `selector.template_inventory_diagnostics`.
All affected sources and targets have concern `dangling-inventory-reference` and
cannot be bound or compiled for relocation/delivery. The world validator checks
the raw table, owner offsets, item count and rows, valid slot ownership, inherited
data, and derived concern. Removing only the concern or recomputing a fingerprint
does not enable an affected record. The campaign artifact validator independently
rejects both the concern and diagnostic fields.

Worlds with these diagnostics use `flir-world-catalog-v2`; older readers therefore
reject them. Worlds without them retain their existing v1 representation and
fingerprint behavior. The CLI reports `dangling_inventory_owners` and
`dangling_inventory_slots`, counting physical owners once rather than counting
their external actor references as additional inventories. Binding unrelated,
valid sources and targets in the same catalogue remains possible. A creature, area
or store with truncated or overlapping structures is left out. Fixture builders and
save inspectors retain strict default parsing; only discovery opts into this
diagnostic representation.

Malformed CRE item names are likewise preserved after the table's bounds and
section ownership pass validation. The row retains its exact eight name bytes
as `raw_resref_hex`, plus every normal payload field, index, and valid slot. Its
`resref`/`item` is `""` because no valid item identity can be assigned. The row's
concern is `invalid-item-reference`, distinct from a genuine all-NUL
`empty-item-reference`. Its owner diagnostic adds `invalid_item_indexes`, matching
exactly those malformed rows. The owner, all its source records, and external
actor references receive `invalid-inventory-reference` and cannot be selected.
The compiler also rejects raw-name evidence even if its concern was removed.

The CLI reports `invalid_item_records` and `invalid_inventory_owners` separately
from empty rows and dangling slot counts. This behavior covers standalone and
embedded CRE inventories; an area with malformed container item names and a store
with malformed stock names cannot be read and are left out whole.

Area actors with only one of the embedded CRE offset/size fields populated are
also retained in v2 discovery as `storage_diagnostics` with reason
`incomplete-embedded-cre`. Their original actor fields, indexes, and byte offsets
remain intact. They carry `unresolved-creature-storage`; no external template
fallback or invented inventory is assigned to them. The CLI reports their
`unresolved_actor_storage` count. These candidates cannot receive rewards, and
the validator rejects physical sources attributed to them. Imported campaigns
also reject unequal zero/nonzero offset/size even if the diagnostic was stripped.
Strict parsing remains the default. Conflicting storage flags, overlapping
nonempty CRE blobs, and truncated actor tables make their area unreadable; it is
left out.

Item identities and effective ITM hashes must match the earlier item catalogue.
The world fingerprint additionally covers all scanned resources, source records,
and target candidates. It ignores archive packaging and the absolute game path.
Localization is the earlier catalogue's display snapshot. Rescan item names if the
TLK changes. Relevant files and directory inventories are checked again after
parsing, so ordinary concurrent installation changes abort without publishing.

This is not a complete installation or script-behavior fingerprint. Lua, SPL/EFF
grants, dynamic 2DA grants, spawn logic, saved world state, and engine behavior are
outside this coverage. An integrity hash is not an authenticity check. Imported
catalogues are development inputs, not a substitute for scanning a trusted install.

The reader supports root KEY/uncompressed BIFF resources and a merged DLC resource
set. Conflicting or shared embedded creature storage is explicitly unsupported.
Unsupported/truncated structures produce no partial discoveries: such a creature,
area or store is left out whole.
Nothing reads recursive mod backups or saves to reconstruct missing originals.
In particular, sources already removed by the legacy Randomiser installer remain
absent; this tool cannot retroactively recover them. The new runtime path must
preserve originals at install time, as required by the migration design.

### Unreadable files

Another mod's damaged or unusual creature, area or store does not stop the scan
for the whole game. A CRE, ARE or STO that cannot be parsed (a truncated or
overlapping structure, another format's header, another version) is left out, and
the scan finishes:

- Its resource row and hash stay in `resources`: the file is in the game. Game
  detection (`OH1000.ARE`, `OH6000.ARE`) and Siege of Dragonspear's areas count it,
  and a table row naming it is not reported as absent.
- It yields no sources and no targets. The world's `unreadable` rows, sorted by
  identity, name it with the parser's reason, for example
  `{"id": "are:ar0602", "reason": "Truncated AREA header."}`. The list exists only
  when the scan left something out, so other worlds keep their content and
  fingerprint; when it exists, the fingerprint covers it. Earlier readers reject
  such a world (an unknown field) rather than reading it as complete.
- An area actor whose external CRE was left out has the concern
  `unreadable-creature-template` and cannot receive rewards.
- The validator accepts only scanned CRE, ARE and STO resources in the list, and
  no source or target in a listed file.
- `world scan` names the left-out files in its `warnings`, and a preset names them
  first among its notes: "AR0602.ARE (Truncated AREA header) cannot be read, so
  Chriz Item Randomiser leaves it out: its items stay as the game has them and no
  reward goes there". A preset's rows that need such a file are left out with
  reason `source-resource-unreadable` (a reward) or `area-resource-unreadable`,
  `creature-resource-unreadable` or `store-resource-unreadable` (a place).
- Binding a required target in a left-out file fails and names the file; an
  optional one is skipped with `optional-resource-unreadable`. Original sources
  and targets in such a file are refused. The native export neither addresses nor
  hashes such a file; a group recipient's members in one are dropped like absent
  members. More Spell Shield scrolls needs a readable `UDDUER01.STO`.

Only these three formats' parse errors are left out. A damaged KEY or BIFF, a file
that cannot be opened, installed items that differ from the item catalogue and
changes during the scan still stop it. The item catalogue scan still stops at a
malformed ITM: item identities and hashes are what profiles, worlds and saved
configurations are checked against. A rescan after the file is repaired reads it
again.

## Explicit choices

Create an empty plan against a selected-item profile:

```powershell
python tools/randomiser_config.py binding create --catalog 'D:/RandomiserDrafts/catalog.json' --world 'D:/RandomiserDrafts/world.json' --profile 'D:/RandomiserDrafts/profile.json' --out 'D:/RandomiserDrafts/bindings.json'
```

This development interface uses editable JSON until the collection/standalone UI
exists. The command copies both catalogue fingerprints and deliberately chooses
no sources or targets. The plan has schema `flir-binding-plan-v1` and the fields
`catalog_fingerprint`, `world_fingerprint`, `sources`, `registrations`, `targets`.

Each selected item needs exactly one source choice. For example, using synthetic
identities only:

```json
{
  "item": "synth001",
  "policy": "relocate",
  "source_ids": ["are:area/container/0/item/0"],
  "registration_ids": []
}
```

Select exact source IDs returned by discovery. Multiple IDs mean intentionally
relocating several records, not removing all items with a matching name. The
explicit alternative `{"item":"synth001","policy":"add","copies":1}` requests
additional copies and selects no original for suppression. Runtime initialization
of added copies still needs implementation; the configurator invents no charges.

A target choice is `{"id":"are:area/actor/1","capacity":2,"optional":false}`.
Capacity is a chosen maximum reward-record count, not an inferred creature inventory
limit. Several rewards may share a target. Missing optional targets are reported and
excluded before checking capacity; missing required targets fail. A present actor
whose external creature template is missing or unreadable also fails. Compatibility at this stage
means structurally matching the scan, not proven encounter eligibility or lootability.

One creature/container record counts as one reward unit, preserving its charge or
stack payload. A finite store row contributes its stock quantity, each with that
payload. No stock or charges are silently truncated to fit available targets.
The report checks total planned capacity; tier eligibility and distinct runtime
instance resolution remain future gates. A template and an actor referencing it
can overlap at runtime, so their capacities are not a proven number of distinct
reward recipients.

Script/dialogue grants require an explicit registration; an item name appearing in
a script is never automatically treated as an obtainable source. A registration has
exact fields `id`, `item`, `resource` (`bcs:<resref>` or `dlg:<resref>`),
`resource_sha256`, `adapter_id`, `selector`, `copies`, `optional`. The selector names
the grant site for an authored adapter. The hash must match the scanned resource.
The adapter identifier and selector are data, not executable code, and the tool does
not claim the adapter exists or that the grant is reachable. Registration IDs appear
in the corresponding relocation's `registration_ids`. Duplicate grant sites and
unused registrations fail. Missing optional registered resources contribute no reward.

## Validation and immutable storage

```powershell
python tools/randomiser_config.py binding validate --catalog 'D:/RandomiserDrafts/catalog.json' --world 'D:/RandomiserDrafts/world.json' --profile 'D:/RandomiserDrafts/profile.json' --plan 'D:/RandomiserDrafts/bindings.json'
python tools/randomiser_config.py binding prepare --catalog 'D:/RandomiserDrafts/catalog.json' --world 'D:/RandomiserDrafts/world.json' --profile 'D:/RandomiserDrafts/profile.json' --plan 'D:/RandomiserDrafts/bindings.json' --store 'D:/RandomiserDrafts/prepared'
python tools/randomiser_config.py binding inspect --store 'D:/RandomiserDrafts/prepared'
```

Preparation freezes the profile, selected item metadata, source instance payloads,
resource hashes, chosen capacities, explicit additions, registrations, and optional
omissions. It writes `revisions/<sha256>.json` before updating `next-bound-draft.json`.
Earlier revisions remain readable with `binding inspect --revision <sha256>`.
Identical preparation is idempotent; altered revision content is rejected. Item-only
drafts and `next-draft.json` retain their previous behavior.

Every bound output remains **`draft-needs-runtime-adapter`**. Neither pointer is a
runtime activation switch. Before activation, the next milestone must prove campaign
revision persistence, exact source suppression, fresh New Game assignment,
nonoverlapping runtime selectors, and verified lootable delivery. Source bytes must
not be changed globally per campaign, because older saves must retain their choices.

All output remains outside the scanned game tree. Tests use temporary synthetic
resources only and do not establish live engine acceptance.

Format evidence: [IESDP CRE V1](https://gibberlings3.github.io/iesdp/file_formats/ie_formats/cre_v1.htm),
[ARE V1](https://gibberlings3.github.io/iesdp/file_formats/ie_formats/are_v1.htm),
[STO V1](https://gibberlings3.github.io/iesdp/file_formats/ie_formats/sto_v1.htm),
and [resource types](https://gibberlings3.github.io/iesdp/file_formats/general.htm).
