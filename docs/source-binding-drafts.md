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
Unsupported/truncated structures fail instead of producing partial discoveries.
Nothing reads recursive mod backups or saves to reconstruct missing originals.
In particular, sources already removed by the legacy Randomiser installer remain
absent; this tool cannot retroactively recover them. The new runtime path must
preserve originals at install time, as required by the migration design.

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
whose external creature template is missing also fails. Compatibility at this stage
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
