# Experimental post-install configurator

This development tool reads installed EE item definitions and creates reusable
**drafts** with explicit source and target bindings. It can export the supported
supported container, creature and shop bindings for the separate
[native prototype](campaign-source-guards-20260927.md). It does not install or
activate that runtime. General source coverage and collection UI remain open.

The intended player workflow is: finish installing mods, scan/customize once, and
start any number of fresh randomized adventures with those settings. Later changes
to the next adventure's configuration will not require a Randomiser TP2 reinstall.

## Run the tooling

Requires Python 3.10 or later with the standard library. Run from this repository.
Choose an explicit game and a separate output directory. The scanner never discovers
or opens a game implicitly. Do not run this development tool against the owner's
live game as part of automated validation.

```powershell
python tools/randomiser_config.py scan --game 'D:/DisposableEET' --language en_US --out 'D:/RandomiserDrafts/catalog.json'
python tools/randomiser_config.py search --catalog 'D:/RandomiserDrafts/catalog.json' --query 'sword' --limit 20
python tools/randomiser_config.py profile create --catalog 'D:/RandomiserDrafts/catalog.json' --id next-adventure --name 'Next adventure' --out 'D:/RandomiserDrafts/profile.json'
```

Use an item resource ID returned by search when editing the draft:

```powershell
python tools/randomiser_config.py profile edit --catalog 'D:/RandomiserDrafts/catalog.json' --profile 'D:/RandomiserDrafts/profile.json' --include <resource-id> --out 'D:/RandomiserDrafts/profile.json'
python tools/randomiser_config.py profile validate --catalog 'D:/RandomiserDrafts/catalog.json' --profile 'D:/RandomiserDrafts/profile.json'
python tools/randomiser_config.py prepare --catalog 'D:/RandomiserDrafts/catalog.json' --profile 'D:/RandomiserDrafts/profile.json' --store 'D:/RandomiserDrafts/prepared'
python tools/randomiser_config.py inspect --store 'D:/RandomiserDrafts/prepared'
```

Replace `<resource-id>` with a real result; it is a placeholder, not literal shell
syntax. Repeat `--include` and `--exclude` to edit several selections. Search accepts
`--category`, `--offset`, and `--limit` (1-100). A query or category is required so the
default action does not dump every installed item. Unicode name searches and resource
ID searches are case-insensitive. No source/destination spoilers are included.

The output interface is JSON. Successful commands return `ok: true` and a `result`;
data/file failures return `ok: false` with an `error` on stderr and a nonzero exit code.
Command-line usage errors follow standard argparse behavior. This interface is
experimental and is not yet a stable collection integration contract.

## Local configuration editor

Use the saved scans to open the standalone editor:

```powershell
python tools/randomiser_config.py ui --catalog 'D:/RandomiserDrafts/catalog.json' --world 'D:/RandomiserDrafts/world.json' --workspace 'D:/RandomiserDrafts/configurations'
```

Open the local URL printed at startup. Optional `--profile` and `--plan` load an
existing draft pair; `--port` chooses a fixed port. The workspace is a dedicated
folder outside the game. The editor needs only Python's standard library and
does not open a browser automatically, launch the game, rescan, or run WeiDU.

Search all scanned items by name or resource ID; a sole original is selected
automatically. Check several original records to relocate several copies of the
same item; unchecked originals stay in place. Each source shows its copy count;
a finite shop record moves its full stock quantity. Recipient
search includes the scanned names and script/resource aliases, with filters for
creatures, containers and shops. Creature templates without a bound spawn and
unfinished destination types are labelled as such. Reward slots allow several
items per recipient. The selected item and recipient sections collapse separately.
Selecting a saved configuration loads it immediately, confirming before unsaved
edits are discarded. Save named configurations or use **Save a copy**; incomplete
choices can be saved for later. Draft files contain the existing profile and binding plan
under `profile` and `plan`. Existing files and CLI formats remain supported.

**Randomized shop prices** defaults to 120% (20% higher). Each selected item can
also override its base price; blank uses the scanned item's price. The multiplier
applies to that base, before the game's usual merchant markup and discounts.
Only randomized sale stock receives this adjustment. Ordinary stock, shared item
definitions and resale values are unchanged. Pricing is saved with the configuration
and frozen into each campaign, so editing the next run does not reprice older saves.

**Check configuration** uses the existing campaign compiler and native endpoint
checks. Unsupported source families remain editable drafts, with an explanation.
**Prepare test settings** writes the existing campaign JSON/Lua format to the
workspace's `prepared` folder, ready for the experimental export command below.
It does not deploy or activate anything. Export still checks selected installation
resources before they can be staged for a test. Collection integration, general
runtime support and ordinary mod installation remain separate work.

The editor reads only saved scan files and writes only configuration files. It
serves on `127.0.0.1`; close the process when finished. Keep configurations and
real scan data outside Git.

Verified with the saved EET scan (3,544 items): search, exact source selection,
recipient capacities, save/copy/reload, and preparation of the previously tested
three-item BG1 campaign. The prepared revision matches the existing sample.
This editor check did not launch or modify the game.

## Scan semantics

- Scans the union of root KEY item entries and override-only item files.
- The effective override item wins over its archive copy.
- Reads the selected EE language's names from dialog.tlk; multiple installed language
  directories require an explicit language selection. A root dialog.tlk is also
  supported.
- Indexes resource ID, names, category, flags, price, stack size, enchantment,
  proficiency, and the effective ITM's SHA-256.
- Detects malformed structures, ambiguous case collisions, unsafe archive paths,
  compressed BIFFs, and unmerged DLC rather than quietly omitting those resources.
- Keep the installation idle while scanning. Input file identities and directory
  inventories are checked again before success; ordinary concurrent changes abort
  the scan. This is not an atomic filesystem snapshot.
- The basic `scan` command does not scan save files, mod backups, or world sources.
  The separate `world scan` command indexes physical CRE/ARE/STO records and BCS/DLG
  resource hashes. Mod ownership and gameplay eligibility are never inferred from
  resource names. See [source binding](source-binding-drafts.md) for its explicit
  coverage and limitations.
- Its fingerprint covers item content and resolved search metadata. It is not a
  compatibility fingerprint for the complete installation or a running campaign.

If mods change, run scan again. A profile referring to an older catalogue cannot be
prepared against the new catalogue without an explicit `profile rebase`. Rebase
retains selected IDs that still exist; missing selected items cause a clear error
and are never silently dropped. Review changes before later gameplay activation.

## Prepared draft storage

`prepare` writes a content-addressed record under `revisions/<sha256>.json`, then
atomically updates `next-draft.json`. Earlier revisions remain intact. Repeating
preparation with unchanged content is idempotent. Reads verify the revision hash.
There is deliberately no runtime `active`/`ready` flag: these records are marked
`draft-needs-source-binding` and are not read by the released mod.

All catalog/profile/preparation outputs must be outside the scanned game directory.
Nothing calls WeiDU or mutates installed resources. Do not distribute a real scan or
personal configuration in the source repository.

## Export experimental runtime settings

After reviewing bindings and running `prototype prepare`, export the configuration
for the existing native test runtime:

```powershell
python tools/randomiser_config.py prototype export --store 'D:/RandomiserDrafts/prototype' --catalog 'D:/RandomiserDrafts/catalog.json' --world 'D:/RandomiserDrafts/world.json' --out 'D:/RandomiserDrafts/flpr-fixture'
```

This reads the prepared next revision, derives native endpoints, and copies pristine
source ARE bytes into the output. It reuses the existing resource reader and checks
selected resource hashes against the scan. `--revision <sha256>` selects a specific
prepared revision. Exporting another configuration into the same output keeps earlier
revisions available for saves and changes only which configuration New Game selects.
Keep that output between exports; restart the test game after deploying a new revision.

Current runtime coverage includes static container originals, selected items on
uniquely bound static actors, and finite shop stock. Destinations can be uniquely
named ARE actors or shops allowing purchases. The current item restrictions remain:
unexpired, nonstackable, visible droppable items. Several originals of the same
item can be relocated from separate owners, including to one creature recipient.
Ambiguous identical copies within a single source owner remain unsupported.
Equipped originals need an explicit replacement. Export reports unsupported
selections instead of silently omitting them; it does not certify an encounter.

Shop destinations currently cannot also be selected source shops, or already stock
a selected item. Multiple distinct rewards can share a shop; for multi-copy sources,
shop capacity must be one until per-copy stock attribution is supported. These are
experimental runtime limits, not a reduction of the intended configuration features.

The output contains configuration only. It does **not** install the experimental
runtime, activate the released mod, or modify the game. Collection UI, ordinary mod
packaging, and general scripted source interception remain unfinished.
See [native source results](campaign-source-guards-20260927.md) for current evidence.

## Validation

```powershell
python -m unittest discover -s tests -p 'test_postinstall_*.py' -v
```

These tests construct synthetic game archives in temporary directories. They cover
resource precedence, localized names, binary bounds, bounded search, stale profiles,
concurrent scan changes, exact physical inventory records, explicit source/target
choices, immutable revisions, command workflows, and unchanged game bytes. They do not prove
live New Game behavior; that belongs to the isolated runtime milestone in the
[approved design](plans/2026-09-22-mode1-postinstall-profiles-design.md).
