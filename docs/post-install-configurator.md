# Experimental post-install configurator

This development tool reads installed EE item definitions and creates reusable
**drafts**. It does not activate those drafts in the game yet. Source binding,
campaign persistence, assignment, and collection UI integration remain later work.

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
- Does not scan save files, recursive mod backup folders, world item occurrences,
  scripts, or destination creatures. Mod ownership and gameplay eligibility are not
  inferred from resource names.
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

## Validation

```powershell
python -m unittest discover -s tests -p 'test_postinstall_*.py' -v
```

These tests construct synthetic game archives in temporary directories. They cover
resource precedence, localized names, binary bounds, bounded search, stale profiles,
concurrent scan changes, immutable revisions, command workflows, and unchanged game bytes. They do not prove
live New Game behavior; that belongs to the isolated runtime milestone in the
[approved design](plans/2026-09-22-mode1-postinstall-profiles-design.md).
