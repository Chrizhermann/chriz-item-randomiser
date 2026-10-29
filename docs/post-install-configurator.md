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
names any game file the world scan could not read and left out. After the game's mods
changed, it first carries the saved configurations over to the new scan (see
[Saved configurations after the mods change](#saved-configurations-after-the-mods-change)).
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
- Files the configurator's Apply wrote are no change of the game's mods. While such a
  file holds exactly what Apply wrote last, `scan`, `world scan` and every start of the
  executable read it as the game had it before Apply first wrote it, and item names do
  not use the texts Apply appended to the game text. Applying a configuration therefore
  leaves the scans as they were, and the next start reopens the configuration applied
  last. A file another mod or the player changed since is read as it is, as is every
  other file; see [Apply's own files](#applys-own-files).

If mods change, run scan again. A profile referring to an older catalogue cannot be
prepared against the new catalogue without an explicit `profile rebase`. Rebase
retains selected IDs that still exist; missing selected items cause a clear error
and are never silently dropped. Review changes before later gameplay activation.

### Saved configurations after the mods change

A configuration names the scans it was made with, and the editor opens only the
configurations of its own scans. The start without arguments scans the game every
time; after the game's mods changed (a new version of CIR among them, whose installer
prepares other dialogues and scripts), it carries each saved configuration over to the
new scans before the editor opens (`rebase.rebase_drafts`, in the executable
`rebase::rebase_drafts`):

- A configuration saved unchanged from a preset becomes that preset as it fits the new
  scans, with the same pools and options: rewards and places the preset gained (in a
  new version of CIR or with the new mods) are added, and what it can no longer use is
  left out. A preset's fit records what it made in the plan (`original_fit_sha256`,
  the SHA-256 of the configuration but for its identity, its name and the record), and
  the editor saves the record with it; a configuration that still matches it was
  changed in the editor at most by its name. Pools and options are the preset choice
  (choosing them fits the preset again); any change to items, rewards, places, reward
  slots, tiers or prices makes it a changed one. Configurations saved by CIR 1.0.3 or
  earlier have no record and count as changed.
- Of any other configuration, what the player chose stays: the items, which of their
  records move, the places and their reward slots, a preset's pools and options, tiers
  and shop prices.
- What the scans decide comes from the new ones, as the editor would offer it now. A
  preset's rewards and places come from the preset fitted again with the same pools
  and options; a row of its lists stays the same choice when the new version moves it
  in another way (another dialogue, another record of the same creature). A special
  place comes from those the game can deliver now, the pool's reward from the prepared
  pool script, a record or recipient chosen in the editor from the new world scan.
- A creature or container of an area, and a record in one, is found as the game finds
  it: a container by its name, a creature by its script name (by its name and CRE
  when it has none), as the scan the configuration was made with (the previous start's
  `world.json`) shows them, wherever it stands now and whatever its number in the area
  file. Only among several of one name does the place where it stood choose the
  nearest; two as near cannot be told apart, and the choice is left out. A record is
  the same one of its holder's records of that item. Without the scan it was made
  with, a creature or container chosen in an area is left out, as it cannot be checked.
- What no longer fits is left out, with the reasons a preset's fit gives (a creature
  that can go missing, a quest CIR cannot change yet, a route the game no longer
  has) and those of the carry-over (an item or record no longer in the game, a place
  that is gone, cannot be told apart or cannot be used, a tier left without a place).
  Nothing is added to a changed configuration. When its preset, fitted now, has rewards
  or places it does not have, one note says so, and that choosing the preset again in
  the editor gives them.
- A configuration that cannot be read, whose preset no longer fits the game (or that
  this version no longer has), or of which no reward remains is not carried over.

The previous start's `world.json` is replaced only once every configuration is carried
over, so a start that ends sooner (its window closed) leaves it for the next start.
The console names each configuration carried over, with the note of its preset, and,
for one carried over in part, what was left out in plain words; its earlier version is
kept in `configurations\earlier scans\`, copied there before the carried-over version
takes its place in one step. A read-only configuration stays read-only (when it cannot be
made read-only again, it is carried over all the same and the console says so). One
that is not carried over (also when it cannot be written, or the carry-over of it runs
into an unexpected error) is moved there, and the console says why; the others are
carried over all the same. When that is the configuration applied last, the start says so
once and the editor starts with a new configuration; it is no longer the one to
reopen. Configurations that an earlier version of CIR already moved to
`configurations\earlier scans\` stay there: the configurator does not bring them back.
When one of them is the configuration applied last, the start says so once. Moved
back into `configurations\` by hand, the next start carries them over (an
editor-chosen creature or container in an area is then left out, as the scan they
were made with is gone).

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
Routes that show Item Randomiser's reward text (the quests that name their reward,
such as Aldeth's in BG1, the golems' chests) need `BGEE.LUA` and that text in the game's language: English,
Polish and German so far (`tools/flir_config/translations.py`). In another language they
are left out with the reason ("Item Randomiser's reward texts have no Spanish
translation yet"), never shown in English; translators can add a language with
[translating-reward-texts.md](translating-reward-texts.md). Apply appends the texts to
`lang/<language>/dialog.tlk` and, where the language has one, to `dialogF.tlk` under
the same numbers, with the translation's female wording, which the game shows to a
female protagonist. A game whose `dialogF.tlk` holds another number of strings than its
`dialog.tlk` leaves those routes out, since a new text could not get the same number
in both. Where the reward reply's journal entry names the reward (Aldeth's), the
selected copy of the reply adds Item Randomiser's entry instead: it keeps the title
line of the game's own entry, in the game's words, and Apply files it in `BGEE.LUA`
under the quest that files the original (a copy of each `createEntry` line that
lists it). EET numbers BG1's texts 200000 higher than BG:EE does; the routes look
for them under EET's numbers there. Each check run reads these files again when the game folder changed
since the last one, so a reward file, or a dialogue the route's copies were looked
for in, that changed after the scan stops that check until the game is scanned
again. Saved games keep the revision they started with, and its files never
change. When the check now leaves out a route or container quest that an earlier
revision prepared, or a file that revision uses changed since, Apply keeps that
revision's files as they were exported and says so in a note, instead of refusing
the new revision. Whenever one of a revision's games is loaded, the runtime checks
that revision's patched dialogues and scripts against the game's override files,
and the areas of its guarded sources against their pristine copies; it would stop
CIR in that game when one of them changed. Apply then refuses and names the file
("Saved games of revision … need UHOGRE01.DLG as it was when they were exported,
but it changed in the game since then; …"); install mods that change such files
before the first Apply. A newer version of CIR may patch a reward dialogue with
more routes than an earlier revision was exported with (CIR 1.0.3 adds Adalon's
place to UDSILVER.DLG, which CIR 1.0.2 patched for her crossbow alone, and marks the
crossbow's recipient at once there). Where the
newer patch is CIR's own patch of the same dialogue, carries every route the
earlier one does, and each route it adds leaves that revision's saves alone, its
saves read the newer one: the added route's flags are its own, which no endpoint
of that revision names, and a route that hands over what its speaker carries finds
there no target, or a plain creature whose reward it can hand over. Apply then keeps
its version as `<NAME>.applied.dlg` and lists the revision in `superseded.lua`
(both in randomiser-campaigns), and the runtime accepts that version, exactly, in
those saves. Otherwise Apply refuses as above ("… but Apply would now replace it
with another version; …"). A container quest's place is the one container in the quest's area that
holds the quest's whole treasure (the
golems' weapons in the De'Arnise Keep cellar `AR1303`, Samia's sword and shield in
`AR1202`), found in the scan wherever the game keeps it; when no container or more
than one holds it, because another mod moved or copied the treasure, that place is
left out ("The extended reward container of AX1H13 cannot be used: AR1303 has no
container that holds AX1H13").
Destinations can be uniquely named ARE actors, static containers (including
stocked chests), or shops allowing purchases. Container delivery appends rewards
without changing existing contents, locks or traps. The game gives a container no
lasting id (object ids change with every area load), so the runtime finds it by its
script name, which export takes only where one container of the area file has it.
That container is the place wherever the game puts it and whatever its type: the
engine moves a ground pile without a shape at area load into the 16x12 search-map
cell that holds its location (2.7.3.0; 127 of EET's 3783 containers), and mods may
move or change a container. Only when a game has several containers of that name (a
mod or script added one) does the location export recorded choose the nearest; two
equally near are ambiguous, and the reward waits. The current item restrictions remain:
unexpired, visible droppable items. A reward keeps its original's flags (identified,
unstealable, stolen, and bits another mod set), so Baeloth's stolen items stay stolen;
an original flagged undroppable (8) is left out, since it would never leave a creature
recipient. Several originals of the same
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

## Apply's own files

Outside `randomiser-campaigns/`, Apply writes only what a configuration prepares: in
`override` the extended pools' route dialogues and scripts and the copies of their
reward replies in other dialogues, the Kangaxx option's dialogue and scripts, the Deck
of Many Things key spell `SPIN621.SPL` and the `BGEE.LUA` journal table, and the texts
it appends to `lang/<language>/dialog.tlk` and `dialogF.tlk`. Each Apply records them
in `randomiser-campaigns/applied-files.json` (schema `flir-applied-files-v1`), by
their path in the game as the export names them (`override/SAFANJ.DLG`,
`lang/en_us/dialog.tlk`): `written` is the SHA-256 of the bytes Apply wrote last,
`before` what the game had there before CIR first wrote it (null where it had no such
file, else its SHA-256 and size, and for game text the number of strings it held).
`randomiser-campaigns/before-apply/<path>` keeps a copy of each earlier file; game
text needs none, since Apply only appends to it. Apply writes the copies first, the
record after the files it names and the next-game pointer last, and puts everything
back when a write fails. An Apply that writes nothing outside `randomiser-campaigns/`,
such as one with the standard pools, writes no record.

The scans read a file that still holds exactly the bytes Apply wrote last as that
earlier file: CIR's copy of it, the archive's resource where override had none before,
or the game text without the strings Apply appended. Anything else is read as it is: a
file another mod or the player changed since Apply, a copy that is missing or does
not match the record, and every file when the record cannot be read. So a real change
of the game's mods still changes the scan and sets the earlier configurations aside;
putting back Apply's file, or the file the game had before, makes the game what the
scan knew again. Route recognition, the configuration check and the export keep
reading the game's files as they are and recognise CIR's own patches there, so
applying the extended pools again on a game whose `SAFANJ.DLG` already is Safana's
route copy offers and exports her route as before. When Apply replaces a file that
still holds its last bytes, the record keeps the earlier file; any other file it
replaces becomes the earlier file, since the scan read that one. After a record that
could not be read, the scans read Apply's own earlier files as the game's; the first
Apply that writes outside `randomiser-campaigns/` then starts a new record, which
takes each file it replaces as it is, Apply's earlier ones included, so the scan the
configurations were made with stays (an Apply that writes nothing new there leaves
the unreadable record unused). The export finds the installer's version of the book in the book Apply wrote, with or
without a record, and keeps it in `PPBOOK.installed.bcs` for the runtime. Apply
refuses an
export that would replace the record or a copy, and game text that is no longer
readable, which changed since the export. Since applying leaves the scan as it was,
a later Apply compiles the revisions that saved games keep against the scan they were
made with.

WeiDU does not track these files. Uninstalling CIR restores only the files its
installer changed. Two of Apply's files are also the installer's: `BGEE.LUA`, where
the installer files Unshey's journal entry, and the book of Lavok's sphere
(`PPBOOK.BCS`), which the installer prepares for the ring's quest and Apply extends
with its scrolls' routes. An uninstall puts back the game's own versions. A re-install
writes the installer's `BGEE.LUA`, without Apply's journal entries until the next
Apply writes them again; the scans do not read it. For the book, Apply keeps the
installer's version it extended and its own in `randomiser-campaigns/`
(`PPBOOK.installed.bcs`, `PPBOOK.applied.bcs`), and saved games of revisions with the
scrolls' routes need its own. A re-install that prepares exactly the version Apply
extended puts Apply's back, so the record holds again and those games keep working;
that includes the re-install WeiDU makes behind a re-installed earlier mod. A book
prepared otherwise (by another version of CIR, or after an earlier mod changed it)
stays as the installer prepared it, and the installer says so: those saved games stop
when they are loaded, and the scans read the book as it is. Apply's other files, the
strings it
appended (WeiDU keeps appended strings) and `randomiser-campaigns/` stay as they were
after an uninstall (decision B2 in [the packaging plan](plans/2026-10-14-mode1-packaging.md));
`before-apply/` holds the files to put back by hand. A re-install that leaves the
installer's files as they were keeps the configuration applied last open.

Games where a version of CIR without the record applied the extended pools have no
record. Their scans read CIR's files as the game's, as that version did, so a
configuration that version applied there no longer fits the first start's scan: it is
carried over to it, without what that scan cannot use (see
[Saved configurations after the mods change](#saved-configurations-after-the-mods-change)).
The first Apply of a later version records only the files it changes, and the scan it
was made with stays from then on.

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
An option the game cannot apply is not offered: `/api/recommended` names it in
`unavailable` with the reason, its box is off and disabled, and the dialog shows the
reason. Kangaxx's items need `HLSKULL.DLG`, `CRYPT526.BCS` and `CRYPT202.BCS` as
Baldur's Gate II (EET or BG2:EE) has them or as this option changed them before, the
flail heads Glaicus's conversation as the installer prepared it, the tome variant a
preset with Baldur's Gate's lists, the extra Spell Shield copies a readable
`UDDUER01.STO` and `SCRL8X.ITM`, and cursed disguises one of the cursed items they
disguise and the item each of them is disguised as. A preset that asks for such an
option anyway is refused with that reason, except where the option would change
nothing: the tome variant without Baldur's Gate's lists, and cursed disguises without
a cursed item. The extra Spell Shield copies stay in the shop either way; the
scroll's own shop row is randomised too exactly when it is one of the preset's
rewards, not when the preset fit leaves it out (a scroll the runtime cannot move, or
a shop with two of them).
No option depends on the game's language. Kangaxx's conversation and ward stone and
the cursed disguises' names and descriptions are English, Polish and German, and
English in any other language; an option a translation has only in part stays English
as a whole, so its texts never mix two languages. Apply writes them to the game's
`dialog.tlk` and `dialogF.tlk` like the reward texts, with the female wordings of
Polish.
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

Some creatures that Item Randomiser gives an item to can leave alive, turn into another
creature or never appear. A conversation sends them away (Arkushule, Tarnor's gang), a
quest or cutscene removes them (Neb, Nevaziah's crypt), they flee (Brennan Risling below
half his hit points), the easiest difficulty takes them out (`LOWBYE.BCS` for Necre and
Taibela), or Watcher's Keep removes four of its statues for a party under five members,
under level 14 or with `XPGiven` above 500000. A reward there could be lost.
`lists/carriers/<campaign>.2da` names these creatures as Item Randomiser's tables do, by
area and creature file, each with its reason (`difficulty`, `party`, `transformed`,
`story`, `conversation`, `reward`, `flees`). The table comes from a review of the
scripts, conversations and spells of EET 2.7.3.0 on 2026-10-26. A place on such a
creature is left out (`creature-can-go-missing`), and the preset dialog says how many
("… are on creatures that may leave, vanish or never appear, so they are left out"). A
group place gives its rewards to its other members only, and is left out when none of the
members the game has is left (the red wizards' group of AR3000 also names Lasal, whom no
EE game has). Not listed are creatures that leave only when they die, whose reward drops as loot
(at the party's feet when no body is left). Also not listed, as reviewed and kept, are
the Iron Throne's leaders (on the chapter 5 path; chapter 7 removes them), Nyalee (she
comes back) and Gorion (he dies in the prologue). Four creatures reward the party before
they leave: Degrodel, Bentha, Tamah and Fll'Yissetat. Their reply could hand over the
place's reward one day; until then their places are left out too. The table is a review
of the games without other mods. Another mod's change can make a listed creature stay
(its places are then left out anyway) or make another creature leave, which the table
does not see.

The runtime finds a place's creature by its script name in the area. A creature
without a name of its own (the engine's blank, `None`), or with one that another actor
of the area has too, cannot be told apart, and its reward would never arrive
(`OWNER_AMBIGUOUS`). The export leaves such a creature out of its group, and a place on
it alone is left out ("Native target needs a unique actor script name in its area", as
for an actor place). On EET 2.7.3.0 these are 13 places of `eet-original` and 17 of
`eet-boss-loot`, among them Lendarn's at Firewine Bridge, and the slaver wizard of the
slavers' group in AR0405. Of them, 11 and 14 remain besides the places the carrier table
leaves out. With the extended pools, the two rules together leave out 96 places of
`eet-original` (861 of 957 stay) and 129 of `eet-boss-loot` (1025 of 1154 stay), and 25
groups of each give their rewards to fewer members.

Quest rewards and places that Item Randomiser handled with its own conversation and
script patches (the `.fl` places of its `mode2` tables, and rewards that a conversation
or script gives) need a route of CIR's. A row for which CIR has none would be left out
in every game, and the preset dialog would name it apart ("… belong to quests that
Chriz Item Randomiser cannot change yet"), with the reason `route-not-supported`. Every
such row of the original tables has a route; `tests/test_original_catalog.py` checks
that none waits for one.

Two such places give the item to each version of a troll: `pptroll.fl` (the painting
troll in Spellhold, AR1514, row 37) and `torgal.fl` (TorGal in AR1301, row 89, with the
flail heads). The EE games bring in only the first version, which stands in the area
(PPTROLL1, TORGAL): a fallen troll sleeps and stays the same creature (TROLLREG.SPL,
CDTORGAL.SPL), and no script, conversation, spell, item or area creates the classic
game's fallen versions (PPTROLL2, TORGAL2) that turned into the risen ones. These two
places are the first version's, found and checked like a place Item Randomiser names
by its creature file (`creature-not-static-in-area`, `ambiguous-target-records`, ...).

BG1's quest routes (Varci's payment, `VARCI.DLG`; Aldeth's sword, `ALDETH.DLG`;
Nemphre's scroll, `NEMPHR.DLG`) work as BG2's do, with flags of
their own (`FLPR_QB1_<row>` beside BG2's `FLPR_QM2_<row>`). They are registered with
BG:EE's names, and on EET they follow EET's rename lists: there Varci gives
`SHLD07A`, which EET's lists name for BG:EE's `SHLD07`. Without those lists (CIR's
older copy) EET's shield is not found, and the route is left out with its reason.
Nemphre's place is a row of its own (136, her scroll is 159); Item Randomiser's lists
give it scrolls only, so her words ("take this scroll") stay true and need no text of
Item Randomiser's.

A preset's row of such a reward or place is that route: the row of the same
conversation or script and item, or of the same `.fl` place and area, as one row of
the original table names it. Boss Loot's rows of Aldeth's sword, Varci's shield and
Nemphre's scroll (items 62, 71 and 339; places 46, 71 and 291) use the routes of the
original rows 16, 20 and 159 (places 16, 20 and 136), with those rows' flags. The
native fixture names the route's row beside the preset's (`route_source`,
`route_target`), and the runtime refuses any other route (`NATIVE_ROUTE_INVALID`). A
preset with two rows of one reward or place has the route for neither, since the
runtime finds a route's row by it; both are left out with `route-not-supported`.

The Sahuagin treasury's rod (`RODS02` in AR2300) moves like any container reward,
but the king's reward reply (SAHKNG01.DLG) creates another one. Where the game's
dialogue gives the rod, that reply follows the rod's row, as Lesa's conversation
does for her sword: where the plan moves the rod, the king no longer creates one. A
game without that dialogue, or whose king gives no rod, needs no link. Where the
reply gives the rod in a way CIR cannot gate, or AR2300 holds a second rod that the
reply could stand for, the rod's row is left out with its reason.

The treasury itself is a place (row 6 of the extended tables, Item Randomiser's
`sahtreas.fl`): the one AR2300 container whose script is `SAHTREAS`, found in the scan
wherever the game keeps it. Its reward lies there until the king pays. The scene after
his reward reply takes the party away for good, so the reply hands the treasury's
reward over when the game shows it (a condition of that reply, `FLPR_Handover`, which
is always true, so the king can always pay): to the party member he talks to, else
the first living one, or to the next member with room in the backpack; with no room
anywhere it lands at the payee's feet. A reward the party took from the treasury
already stays taken. A reward that has not reached the treasury yet goes to that
party member instead. On the prince's side the king never pays, and the reward stays
in the treasury to be looted. Once the king's job is done (`sahkngjob` 2, which he
sets just before his reward reply), `SAHTREAS.BCS` destroys the items that reply
gives. It runs with every AI update of the treasury, trapped or not (2.7.3.0: the
container's constructor sets its script, and its AI update ends in the engine's script
processing). Each of those destroys runs only while that item's row leaves the original
where it is (`FLPR_QM2_7` for the rod, `FLPR_QM2_57` for the bracer), so the rod or the
bracer that moved into the treasury stays. A game without that script needs no gate.
The treasury's place is left out with its reason when the king gives no rod
("SAHKNG01.DLG has no reply that gives RODS02") or AR2300 has no such container or more
than one. Where the script uses one of the king's items in another way than
`DestroyItem` ("SAHTREAS.BCS block 0 uses RODS02 in an action other than DestroyItem(),
which the route cannot gate"), that item's rows are left out (for the rod, with the
treasury's place); a script that cannot be read leaves out both. The place is left out
too where a reward of its tier (the only ones a campaign can put there) could be lost
there: one with the name of an item the script destroys, other than the rod's and the
bracer's own rows (another mod's rod, or an item another mod's block destroys:
"SAHTREAS.BCS destroys every RODS02 in the treasury, and the reward ... can be one"), or
one that the king's handover could not tell apart from another item of its name with
other charges, flags or expiration: one lying in the treasury ("The treasury holds
another ..."), or another reward that can lie there too, through another place in the
same container (as the base row 316 of the scroll tier) or, with overflow, through the
treasury's own place ("The reward ... can lie in the treasury as another ..."). No such
reward is in the unmodded games.

The Sahuagin prince is a place too (row 59, Item Randomiser's `tadpoles2.fl`): Item
Randomiser gives the item to SAHPR2.CRE and to the one he talks to in the reply that
ends his job (`villynatyjob` 2), which gives nothing in the games. Here the prince
(SAHPR2 in AR2300) carries the place's reward, as Jermien and the other carried
rewards do, so killing or robbing him gets it too, and each reply of his conversation
(SAHPR2.DLG) that ends his job hands it over first (`FLPR_Transfer`, before the reply's
other actions). Those replies are found by meaning: each that sets `villynatyjob` to 2,
also through `ActionOverride`, in any letter case and spacing. The place is left out
with its reason where no reply ends his job ("SAHPR2.DLG has no reply that sets
villynatyjob to 2"), where a reply names the job in actions that cannot be read, where
a reply changes the job another way that could end it unseen ("... changes
villynatyjob with IncrementGlobal(), which the route cannot follow"), or where a reply
of another conversation reached from his, such as a party member's interjection, ends
the job too: he would not hand the reward over there ("... also sets villynatyjob to 2,
which ends the job of SAHPR2.DLG's speaker, but there it would not hand over the reward
it carries"). On the king's side the prince never pays, and the reward stays with him
to be looted.

Row 60 (Item Randomiser's `tadpoles3.fl`, "Sahuagin king or prince") is the prince's
place too. Item Randomiser flips a coin at install: the item goes to SAHPR2.CRE or to
both kings, SAHKNG01.CRE and SAHKNG02.CRE. The king leaves with what he carries on
either side: on his own side he pays and leaves the area (SAHKNG01.DLG state 81), and
on the prince's side he destroys himself once the prince's job begins (SAHKNG01.BCS),
while SAHKNG02, who then takes the throne, has the items of his own file. So here the
prince carries this place's reward as well, and each reply that ends his job hands
over row 59's reward first, then row 60's. Both places are left out together, for the
reasons above.

Row 58 (Item Randomiser's `tadpoles1.fl`) is the king's own place: his reward reply
gives it to the one he pays, like any quest reward. Item Randomiser also gives the
item to both kings, SAHKNG01.CRE and SAHKNG02.CRE, because on the prince's side that
reply never comes: there the party kills the king who took the throne for the prince,
and takes the item from him. Here the place's reward waits until the king pays or
dies: it goes to the one he pays, or it lies on the ground at the corpse of whichever
king died, as for any creature that died before its reward arrived. Both kings are
named `sahkng01`, so whichever one the area has is the place. While he lives the
reward waits, since he may still pay, or destroy himself when the prince's job begins
(SAHKNG01.BCS), and then the other king comes. The place is left out with its reason
where a king's file is missing ("Required reward resource is absent: cre:sahkng02") or
has another script name ("Original reward recipient changed"): on the prince's side
the reward could then never arrive.

Tolgerias's ring (row 40, `RING33`, which TOLGER2.CRE carries) moves like any
creature's item, but Tolgerias pays for Valygar's body with the ring he carries
(TOLGER.DLG, four replies). Where the game's dialogue gives the ring, those replies
follow the ring's row, as Lesa's conversation does for her sword: where the plan moves
the ring, he no longer gives one. His place (row 50, Item Randomiser's `tolger.fl`) is
those replies, which hand its reward to the one he pays. Item Randomiser also gives the
place's item to both of him, TOLGER.CRE in his house and TOLGER2.CRE, who stands in the
planar sphere (AR0412) on every path and never pays. So until he pays, the place's
reward waits for TOLGER2's death and then lies on the ground at his corpse, as the
Sahuagin king's does. Where the party kills Tolgerias in his house, his area's script
(AR1002.BCS) removes his body at once, and the reward still waits for TOLGER2. Where he
pays grudgingly ("You win, fool."), his words name the ring; where the ring moved, CIR
shows Item Randomiser's line instead ("Here is a trinket in exchange for the body"). His
class script (TOLGER.BCS, which both run) destroys the ring of the one once the other
has lost his (`CDRing33Gone`). That destroy runs only while the ring's row leaves it
where it is (`FLPR_QM2_40`), so a ring that moved to one of TOLGER2's own places stays;
since the block tests the ring, it stays one block, which never runs while the ring is
moved. The place is left out with its reason where he pays no ring ("TOLGER.DLG has no
reply that gives RING33") or TOLGER2.CRE is missing or has another script name
("Original reward recipient changed"). The ring's row and the place are left out where
a reply gives it in a way CIR cannot gate, or where a block of the script that tests
the ring does more than destroy it ("TOLGER.BCS block 0 tests RING33 and does more than
destroy it, which the route cannot gate"): with a moved ring, the rest of that block
would never run. His second ring (TOLGER.CRE) moves with the first, as Item
Randomiser's table of second copies says (below).

Rows 25, 49, 215 and 252 (Item Randomiser's `chapter3.fl`, "a named vampire or shadow
thief during chapter 3"; 215 and 252 come with the scrolls) are places of the guild
war. Item Randomiser gives each of their items to two creatures, since the party
fights one side only: to one of Bodhi's vampires, whom Aran's side fights in her lair
(AR0801.BCS creates them for Aran's last job), and to one of Aran's shadow thieves,
whom Bodhi's side fights in his guild house (AR0329; with Rogue Rebalancing in its own
guild, RR#010) or at the docks (Arkanis Gath, whom AR0300.BCS creates for Bodhi's first
job). It chooses each among those who can use the item, else among all. Here each place
has one reward, and it goes to the first of these who can take it: the vampire drawn
for it (LASSAL02, BODVAM01 or BODVAM02) alive, into its backpack; the thief drawn for
it (BOOTER02, ARNWAR06 or ARKANISG; with Rogue Rebalancing, which Item Randomiser knows
by its RR#ARAN2.CRE, RR#BOOTR, RR#HAZ or ARKANISG) at his corpse; or Aran (ARAN02 or
RR#ARAN2) at his corpse. Each is drawn among those who can use the reward, else among
all, as Item Randomiser draws them. So on Aran's side the vampire carries the reward
and drops it when killed, and on Bodhi's side it lies at the corpse of whichever of the
thief and Aran dies first. A vampire killed before the reward reached it leaves no
body, since a mist replaces it. The reward then falls at a party member's feet, and a
message names it ([campaign runtime](campaign-runtime-prototype.md)). Alive, the
thieves and Aran never take it: on Aran's side they are the party's allies, and Rogue
Rebalancing removes the living thieves once Aran is dead. Bodhi's side must kill Aran
(BODHI.DLG asks for `Dead("aran")`). Nothing is given twice, so no second copy needs
destroying, as Item Randomiser's script for the Alternatives mod does.

Two of Item Randomiser's creatures are not among them: Tanova (BODTAN), whose vampire
script (VAMPIR01.BCS of the unmodded game) replaces her with a bat once her hit points
fall below 10, which would take a reward she carries; and Dedral (ARNWAR07, RR#DEDRL),
of whom AR0329's traps create more (AMBUSH2.BCS, ARNSPW1.BCS), and who leaves in his
conversation, so that his corpse could not be told from another's.

The runtime finds each creature by its script name in its area. So each must be once in
the four areas the handler names (AR0801, AR0329, RR#010, AR0300), as an actor that
appears there or as a creature the area's own script creates, whatever the conditions,
and the only creature there of its script name; one that is not (another mod's second
copy, another creature of its script name, one placed for no hour of the day, one
without a script name) is left out of its group. The places are left out where no
vampire is left ("Item Randomiser's chapter 3 place needs one of Bodhi's vampires
(LASSAL02, BODVAM01, BODVAM02) in AR0801, once and with a script name of its own
there"), or no thief and no Aran ("... needs one of Aran's thieves or Aran himself where
its handler looks for them ..."), or where the script of one of those areas cannot be
read; without AR0801 they are left out with `area-resource-absent`. Apply changes none of
these files.

Adalon's place (row 72 of BG2's base table) is the silver dragon herself (UDSILVER in
AR2102). Item Randomiser gives its item to UDSILVER.CRE, who is not meant to die: once
the party brings back her eggs, her reply takes them (`TakePartyItem("misc9t")`) and
pays with her crossbow (the egg deal's route, row 44 of the extended tables); she then
takes the party to AR2402 (CUT44C.BCS) and leaves there with what she carries
(UDSILVER.DLG state 57 removes her). Here she carries the place's reward, as the
prince does, so killing her gets it too, and each reply of her conversation that takes
the eggs hands it to the one she talks to first (`FLPR_Transfer`, before the reply's
other actions; both variants of the egg deal's gated reply). Without the eggs she
keeps it. Those replies are found by meaning: each that takes MISC9T from the party
(`TakePartyItem`, `TakePartyItemAll`, `TakePartyItemNum`, `TakePartyItemRange` or
`TakeItemReplace`), also through `ActionOverride`, in any letter case and spacing. The
place is left out with its reason where no reply takes the eggs ("UDSILVER.DLG has no
reply that takes MISC9T"), where a reply names them in actions that cannot be read,
where UDSILVER.CRE is missing or has another script name ("Original reward recipient
changed"), or where a reply of another conversation reached from hers, such as a party
member's interjection, takes them too ("... also takes MISC9T, which ends the job of
UDSILVER.DLG's speaker, but there it would not hand over the reward it carries"). The
egg deal's own route stays as it is either way.

The egg deal's place (row 66 of the extended tables) gets its reward like any quest
reward: her reply marks the party member it gives the crossbow to, and the reward
goes to the one with that mark (since 1.0.3 the protagonist where that one left the
party, see [the runtime](campaign-runtime-prototype.md)). Her conversation ends by clearing every queued action
of the party (UDSILVER.DLG state 46), and with them the mark her reply queued, so in
CIR 1.0.2 that reward never arrived. Her reply now also marks the one she talks to
when the game shows it, as the only one with that mark (a condition of both variants
of that reply, `FLPR_Mark`, which is always true, like the treasury's
`FLPR_Handover`). Her reply gives the crossbow to that one (`LastTalkedToBy`, in any
letter case and spacing); where it gives it to another, the egg deal's route is left
out with its reason ("UDSILVER.DLG state 41 gives XBOW10 to another than the one the
speaker talks to, whom the route cannot mark at once").

CIR 1.0.2 patched UDSILVER.DLG for the egg deal only, without that mark. A patch of an
earlier version with some of a dialogue's routes of today, or without the mark, is
still CIR's own: the scans and the check read the dialogue from it as it was, and
Apply patches it again with every route. A changed copy of it is left out as any
other. A revision exported with CIR 1.0.2 keeps her place as a plain creature, and
her reply hands that reward over too: its saves read the dialogue Apply patches now,
which supersedes the one they were exported with (`superseded.lua`, above), so the
egg deal's reward reaches them as well where the deal is still to come. Where it
was done under 1.0.2, the reward's mark is gone; it arrives at the protagonist
after the runtime's wait for a missing mark (approved by Chris, 2026-10-28).

The book of Lavok's sphere (`PPBOOK.BCS`, AR1513) gives two scrolls with the ring on
its last page (rows 260 and 262 of the extended tables, beside the ring's row 109).
Each scroll has a route of its own, which only drops its grant where its row moves the
scroll; the book's places are the ring's quest, so the scrolls have no place of their
own. The installer prepares the book for the ring's quest; the scrolls' routes patch
the game's own book, so they need no prepared book (a game whose book the installer
left as it was can still move the scrolls; the ring stays as the game has it). Apply
writes the book only for a revision that moves a scroll; otherwise it stays as the
installer prepared it. A revision that moves only the ring records the installer's
book, and Apply keeps that version and the one it wrote side by side
(`randomiser-campaigns/PPBOOK.installed.bcs` and `PPBOOK.applied.bcs`): the runtime
accepts the book Apply wrote for that revision's saves when both still match. A
re-install of CIR that prepares exactly the book Apply extended puts Apply's back; one
that prepares another book leaves it, with a note, and the saves of a configuration
that moves a scroll then stop when they are loaded (see [Apply's own
files](#applys-own-files)).

Rewards an NPC carries: the genie in Trademeet (`TRGENI01`), Captain Dennis
(`LOUTCAPT`), the Amkethran priest (`AMCLER01`), the sage Jermien (`JUGJER01`, whose
sword keeps its daily charge), the sculptor Yarryl (`SCYARRYL`, his copy of the
temple's reward), and in BG1 Aldeth (`ALDETH`) and Nemphre (`NEMPHR`) carry the item
their conversation hands over. The runtime knows each of these copies by its
creature's and item's names, which EET keeps; a game that renames one leaves its row
out with its reason. When
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

Second copies of moved rewards: Item Randomiser's `lists/duplicates/bg1.2da` and
`bg2.2da` (shipped in `lists/duplicates/`) name a shop, creature or area container
that holds a second copy of a reward, such as Bernard's crossbow (`XBOW06`) or the
ring in Rogue Rebalancing's `RR#010`. When that reward's row moves, the copy goes with
it, as Item Randomiser's installer removes it: the configuration finds every record
of the item in the holder (EET's names for BG1 rows, any slot, charges or flags) and
names it in the plan (`duplicates`); a creature keeps the table's replacement
(`SPER02` for the Sahuagin prince, `SPER01` for BG1's `KNIGHTSK`). Tolgerias' second
ring (`TOLGER.CRE`) thus moves with the ring of `TOLGER2.CRE`. The copies a carried
reward's route hands over (the creatures above, also for a preset's row of their
route, such as Boss Loot's Nemphre) are that route's and are not named again.
Binding and the campaign accept only the shipped rows under their reviewed names, and
the export writes each creature and shop as the game has it. The source gate removes the copy wherever its holder loads, unless the
start keeps the reward in place. A holder the game lacks, or one without the item,
has no copy. The reward is left out with its reason where the holder cannot be read,
the copies differ ("… holds Item Randomiser's second copy of RING07 in records that
differ"), another row moves the same record, or EET's lists rename the holder or
item to a name the runtime does not know (`second-copy-name-unreviewed`).

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
