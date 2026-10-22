# Post-install configurator

This development tool reads installed EE item definitions and creates reusable
configurations with explicit source and target bindings. It can export the supported
container, creature, shop and authored quest bindings for the separate
[native prototype](campaign-source-guards-20260927.md). Runtime installation and
general source coverage remain unfinished.

For an already installed disposable test runtime, start the editor with
`--apply-game <same-game-path-as-the-scan>` to enable **Apply for next game**.
With the game closed, this saves the configuration, exports it and deploys the
settings. Each new game gets a fresh draw, in every campaign start of the game
(BG1 and SoD on BG:EE, SoA and ToB on BG2:EE, all four on EET; a later start only
with the rewards and places it reaches, see the packaging plan's
[Campaign starts](plans/2026-10-14-mode1-packaging.md#campaign-starts)); existing saves
keep their revision. Apply first takes the revisions the game already holds into the
workspace's `native` folder, byte for byte, so a new or another workspace keeps them for
older saves and checks them as their own workspace would; it refuses when the workspace holds
another version of one of them. The editor reopens the last applied choice.
Without that explicit option, preparation still writes only outside the game. Apply
refuses a game where Item Randomiser's Mode 1, Mode 2 or More Spell Shield scrolls is
installed, which CIR cannot be combined with.
This is a development playtest workflow, not the normal TP2 installation.

The intended player workflow is: finish installing mods, scan/customize once, and
start any number of fresh randomized adventures with those settings. Later changes
to the next adventure's configuration will not require reinstalling CIR.

## Run the tooling

Requires Python 3.10 or later with the standard library. Run from this repository.
Without Python, use the standalone executable built from `configurator/` (see
[its README](../configurator/README.md)): `cir-configurator` takes the same commands
and options as `python tools/randomiser_config.py` and gives the same results.
Started without arguments in the game's `cir` folder (a double-click), it scans that
game into the player's own folder and opens the editor with Apply for it; its console
names any game file the world scan could not read and left out.
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
`cir-configurator ui` serves the same editor and also opens it in the browser;
`--no-browser` only prints the address.

Search all scanned items by name or resource ID; results show each item's
type (ring, large sword, ...). A sole original is selected
automatically. Check several original records to relocate several copies of the
same item; unchecked originals stay in place. Each source shows its copy count;
a finite shop record moves its full stock quantity. Recipient
search includes the scanned names and script/resource aliases, with filters for
creatures, containers and shops. Creature templates without a bound spawn and
unfinished destination types are labelled as such. Reward slots allow several
items per recipient. The selected item and recipient sections collapse separately.

Each selected item and recipient shows the parts of the game its places are in
(BG1, SoD, BG2, Watcher's Keep, ToB, as the scanned area names tell them; a
creature template counts every area that places it) and, in a preset draft, the
tier of each chosen original record and reward slot. A record whose place does
not tell its part, such as a shop's stock, shows the part of its tier's table
(BG1, SoD or BG2). **Find in this configuration** filters both lists by name,
resource ID, item type, game part or tier, and offers one game part and one tier
to choose; the counts then say how many items and reward slots match. Recipient
search in the library finds game parts too (`tob`, `watcher's keep`).
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
In draft-only mode it does not deploy or activate anything. Export still checks selected installation
resources before they can be staged for a test. Collection integration, general
runtime support and ordinary mod installation remain separate work.

Without `--apply-game`, the editor reads saved scan files and writes configuration files. It
serves on `127.0.0.1`; close the process when finished. Keep configurations and
real scan data outside Git.

Verified with the saved EET scan (3,544 items): search, exact source selection,
recipient capacities, save/copy/reload, and preparation of the previously tested
three-item BG1 campaign. The prepared revision matches the existing sample.
This editor check did not launch or modify the game.

## Scan semantics

- Scans the union of root KEY item entries and override-only item files.
- The effective override item wins over its archive copy.
- Override files with names longer than eight characters are left out and named in
  the scan's warnings: game data names resources with at most eight characters, and
  mods keep WeiDU templates under such names (SCS's `dw#marker_file.itm`). A shorter
  name that is not ASCII stops the scan, since the game's code page decides it.
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
python tools/randomiser_config.py prototype export --store 'D:/RandomiserDrafts/prototype' --catalog 'D:/RandomiserDrafts/catalog.json' --world 'D:/RandomiserDrafts/world.json' --out 'D:/RandomiserDrafts/export'
```

This reads the prepared next revision, derives native endpoints, and copies pristine
source ARE bytes into the output. It reuses the existing resource reader and checks
selected resource hashes against the scan. `--revision <sha256>` selects a specific
prepared revision. Exporting another configuration into the same output keeps earlier
revisions available for saves and changes only which configuration New Game selects.
Keep that output between exports; restart the test game after deploying a new revision.

Current runtime coverage includes static container originals, selected items on
uniquely bound static actors, finite shop stock, and the quest rewards whose files
`cir.tp2` prepares when it is installed: the pool (BLPOOL,
BLUN12), the altar (TROALT, BLUN23), the book (PPBOOK, ring09), Unshey's girdle and
Glaicus's flail heads. The installer finds each reward by meaning (the block that
creates the item, the reply that takes the quest item), so other mods' edits to the
same files stay, and splits it under saved campaign GLOBALs: an adventure that
randomises the reward gets the runtime's item, every other adventure keeps the
original grant. It lists what it prepared in `override/FLPRPAT.lua`. The scan and
the editor offer such a reward only when that record says `applied`, and export
records the installed script so the runtime can confirm it is still in place. The
configurator never writes these files, so uninstalling CIR restores
them. A file the installer cannot change unambiguously (it gives the reward twice,
no longer gives it, or an earlier configurator already changed it) stays as
installed; the installer and the editor say which and why. This is a fixed set of
authored adapters, not general BCS/DLG grant coverage.

The quest rewards and container quests of the extended pools (Item Randomiser's
`mode2` tables) are prepared by Apply, also from the game's own files. Each route is
recognised by its meaning, never by the bytes of one game: the replies or script
blocks that give the reward item, the recipient they name and, for some routes, the
text that names the item. Compiled spacing, line ends and letter case, other strrefs
and state numbers, and replies or blocks that another mod added stay as they are;
every reply or block that gives the item is gated, so BG2:EE's and EET's own files
both work. The configuration check runs the same recognition as Apply. A route it
cannot prepare (nothing gives the item any more, one reply gives it twice, a reply
gives it another way such as through `ActionOverride`, the text that names it is
gone, or its patch was changed afterwards) is left out with that reason; actions that
only take, show or use the item stay as they are, and Apply never refuses a route the check
accepted. Routes that share one reward event, such as the egg deal's two dialogues
or the three sculptors, are left out together. A party member's interjection
(WeiDU's `INTERJECT_COPY_TRANS`, which many NPC mods use) can copy a quest's
reward replies into the party member's dialogue, where the player can take the
reward too. The check follows each route dialogue's links into other
dialogues, through chained interjections, and Apply gates those copies like the
route's own replies, in the same file. A route is left out when a copy cannot be
gated: the item would come from another speaker than the one that carries it, CIR
changes that dialogue for something else, or another reply there gives the item in
another way ("XNPCJ.DLG state 0 also gives SHLD26, but not with a copy of a reward
reply of UHOGRE01.DLG, so the route cannot tell whether it is the same reward").
Routes that show Item Randomiser's reward text (the BG2 quests that name their reward,
the golems' chests) need `BGEE.LUA` and that text in the game's language: English,
Polish and German so far (`tools/flir_config/translations.py`). In another language they
are left out with the reason ("Item Randomiser's reward texts have no Spanish
translation yet"), never shown in English; translators can add a language with
[translating-reward-texts.md](translating-reward-texts.md). Apply appends the texts to
`lang/<language>/dialog.tlk` and, where the language has one, to `dialogF.tlk` under
the same numbers, with the translation's female wording, which the game shows to a
female protagonist. A game whose `dialogF.tlk` holds another number of strings than its
`dialog.tlk` leaves those routes out, since a new text could not get the same number
in both. Each check run reads these files again when the game folder changed
since the last one, so a reward file, or a dialogue the route's copies were looked
for in, that changed after the scan stops that check until the game is scanned
again. Saved games keep the revision they started with, and its files never
change. When the check now leaves out a route or container quest that an earlier
revision prepared, or a file that revision uses changed since, Apply keeps that
revision's files as they were exported and says so in a note, instead of refusing
the new revision. At every start the runtime checks each revision's patched
dialogues and scripts against the game's override files, and the areas of its
guarded sources against their pristine copies; it would stop for every game when
one of them changed. Apply then refuses and names the file ("Saved games of
revision … need UHOGRE01.DLG as it was when they were exported, but it changed in
the game since then; …"); install mods that change such files before the first
Apply. A container quest's place is the one container in the quest's area that
holds the quest's whole treasure (the
golems' weapons in the De'Arnise Keep cellar `AR1303`, Samia's sword and shield in
`AR1202`), found in the scan wherever the game keeps it; when no container or more
than one holds it, because another mod moved or copied the treasure, that place is
left out ("The extended reward container of AX1H13 cannot be used: AR1303 has no
container that holds AX1H13").
Destinations can be uniquely named ARE actors, static containers (including
stocked chests), or shops allowing purchases. Container delivery appends rewards
without changing existing contents, locks or traps. The current item restrictions remain:
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

## Original preset drafts

The `templates` command reads the original checked-in item/location tables and
authored endpoint catalogs. It offers presets for BG1, BG2 and EET, all drawn for
each new game; Chriz Item Randomiser has no modes. Siege of Dragonspear lists come with
Chris's design ([SoD lists](sod-lists.md)). The editor offers those whose
tables the scanned game has: BG1 presets on BG:EE, BG2 presets on BG2:EE, all on EET. Each preset has a standard and
an extended pool of items and of recipients, chosen separately: the standard pools
read the `base` tables, the extended pools also the `mode2` tables, the larger lists
that Item Randomiser used for its install-time Mode 2 (more items from creatures,
shops and containers, quest rewards, and more places).

```powershell
python tools/randomiser_config.py templates
python tools/randomiser_config.py templates --id eet-original --out 'D:/RandomiserDrafts/original.json'
python tools/randomiser_config.py templates --id eet-original --items extended --targets standard --out 'D:/RandomiserDrafts/extended-items.json'
```

`--options path.json` accepts the Boolean choices `scrolls`, `kangaxx`,
`flail_heads`, `totsc_tome`, `more_spell_shield`, `cursed_disguise`, plus
`loss_mode` (`none`/`some`), `loss_percent` and `unrandomized_percent` (0–100).
These preserve intended component choices; they do not execute installer edits.
Optional components default off here. The editor's "Start from a preset" dialog
opens on the recommended default instead: the game's own `-original` preset
(`bg1-original`, `bg2-original` or `eet-original`) with the standard pools,
`flail_heads` and `totsc_tome` (Item Randomiser's lists without item loss, plus the
Flail of Ages heads and the randomised Tales of the Sword Coast tome); every choice
stays editable.
Disabled rows remain visible with `enabled:false`.
Loss excludes the original Flail/Kangaxx tiers; unrandomized percentage concerns
items with original sources. These draft choices still need automatic binding to
the runtime configuration.

The complete EET standard pools contain 505 item rows and 527 location rows; the
extended pools 926 and 971. Repeated items, source replacements,
tiers, tokens, identifiers, chances and row provenance are retained. These counts
precede component and installation-availability filtering.

Preset list sets in `lists/presets/<name>/` replace the tables of the campaigns they
cover; an EET preset takes the original tables for the other campaign. The first is
Boss Loot, aShuN's BG1 lists (`bg1-boss-loot`, `eet-boss-loot`; see
[its notes](../lists/presets/boss-loot/README.md)). Its row identities are
`bg1:boss-loot:<family>:<kind>:<line>`. A place of its standard pool takes the
authored endpoint of the same place in the original tables; ten places that the
original standard tables do not use have none and are bound by name like the places
of the extended pool. A place of its extended pool at the place of one of the
original tables' container quests (Safana's treasure) is that quest. Its
Rogue Rebalancing (`rr#…`) and IWDification (`cdia…`) items are optional: a game
without such an item leaves its rows out and the preset dialog names the mod. It says
the mod is not installed only when the game has no item of it at all; otherwise it
says how many rewards need items of the mod that the game does not have (a part of
the mod that is not installed). On a reference EET scan, `eet-boss-loot` passes the
configuration check like `eet-original`.

On EET, BG1 and SoD resource names follow EET's own rename lists
(`EET/lib/pcu_dict.tph`, `game_names.eet_names`). Without them, or when they cannot be
read, the older copy in `lib/fl#bg1pal.tpa` is used and the preset dialog says so
([SoD lists](sod-lists.md#names-on-eet)).

A preset draft leaves out each row this game cannot use, instead of refusing the
whole draft, and the preset dialog says how many rewards and places it left out; a
left-out reward stays as the game has it. These are rows whose item, area, creature
or shop the game lacks or the world scan cannot read, whose record is not where the tables put it or is there more
than once, places on a creature that never appears (each of its placements in the area
has appearance schedule 0), and rows the runtime check rejects, such as a quest reward whose files no
longer give it ("The extended reward route for MGKHOL01 cannot be used: MGKHOL01.DLG
has no reply that gives BELT02") or an item its creature cannot drop. The draft is checked with the
same runtime check as "Check configuration", which then passes. `/api/template`
also returns the left-out rows with their table line and reason (`not_used_rows`).

Some rows of the extended pools are left out in every game, and the preset dialog
names them apart ("… belong to quests that Chriz Item Randomiser cannot change yet"):
quest rewards and places that Item Randomiser handled with its own conversation and
script patches (the `.fl` places of its `mode2` tables, and rewards that a conversation
or script gives) for which CIR has no route yet. Their reason is
`route-not-supported`. In the original tables these are Aldeth's and Nemphre's
rewards and places (BG1); two scrolls that `ppbook.bcs` gives, and the
places of the Sahuagin treasury, the Sahuagin prince, the
king or the prince, the trolls of the Spellhold painting, Tolgerias, TorGal and a
vampire or shadow thief of chapter 3 (BG2). `tests/test_original_catalog.py` lists
them; a route for one of them takes its rows off that list.

BG1's quest routes (Varci's payment, `VARCI.DLG`) work as BG2's do, with flags of
their own (`FLPR_QB1_<row>` beside BG2's `FLPR_QM2_<row>`). They are registered with
BG:EE's names, and on EET they follow EET's rename lists: there Varci gives
`SHLD07A`, which EET's lists name for BG:EE's `SHLD07`. Without those lists (CIR's
older copy) EET's shield is not found, and the route is left out with its reason.

The Sahuagin treasury's rod (`RODS02` in AR2300) moves like any container reward,
but the king's reward reply (SAHKNG01.DLG) creates another one. Where the game's
dialogue gives the rod, that reply follows the rod's row, as Lesa's conversation
does for her sword: where the plan moves the rod, the king no longer creates one. A
game without that dialogue, or whose king gives no rod, needs no link. Where the
reply gives the rod in a way CIR cannot gate, or AR2300 holds a second rod that the
reply could stand for, the rod's row is left out with its reason.

Rewards an NPC carries: the genie in Trademeet (`TRGENI01`), Captain Dennis
(`LOUTCAPT`), the Amkethran priest (`AMCLER01`), the sage Jermien (`JUGJER01`, whose
sword keeps its daily charge) and the sculptor Yarryl (`SCYARRYL`, his copy of the
temple's reward) carry the item their conversation hands over. When
that reward moves, the creature's copies go with it; the configuration finds them in
the scanned creature file, whatever their place, flags or charges there and whatever
else another mod gave the creature, and the runtime checks each copy's bytes against
the original file before it removes it (the genie keeps a `BLUN05` instead). Lesa's
sword (`UDLESA`) is a reward of its own, and her conversation follows its row where
her dialogue gives the sword, as the king's does for the rod. A row is left out with
its reason where the creature does not carry its reward ("TRGENI01.CRE does not carry
SW1H23, which the reward of TRGENI01.DLG hands over"), where its copies differ ("…
carries BLUN20 in records that differ"), or where the creature holds a second sword
that Lesa's conversation could stand for. A Yarryl without the club simply has no
copy to remove.

The world scan leaves out a creature, area or store file it cannot read (another
mod's damaged or unusual file) instead of refusing the game. `world scan` names such
files with their reason in its `warnings`, and the preset dialog names them first
among its notes; their items stay as the game has them
([Unreadable files](source-binding-drafts.md#unreadable-files)).

Bound plans now accept optional `randomization` metadata: source tiers and loss
exemptions, a tier for each recipient slot, loss/keep percentages, and ident-paired
slots excluded when an original is kept. Each New Game draws within those tiers;
saves retain their assignments. Kept originals remain available normally, while
lost rewards have their originals removed and no replacement delivered. Mixed
keep settings currently require enough spare capacity for every exclusion outcome;
the compiler uses a conservative bound until original overflow allocation is wired.
The editor does not yet expose these advanced settings.

**The full original drafts are not playable configurations yet.** Remaining work
includes original overflow/token expansion and bindings for
virtual/scripted sources and grouped recipients, plus installation-specific filters. Drafts
are kept separate from prepared campaign settings so they cannot silently become
one flat loot pool. New packs remain review material in
[item-pack candidates](item-pack-candidates.md); no new tiers or inclusions are approved.

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
The checks that compare the Rust executable with this reference are described in
the [configurator README](../configurator/README.md#parity-with-the-python-reference).
