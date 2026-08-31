# Item Randomiser Public Maintenance Fork Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:executing-plans to implement this plan task-by-task.

**Goal:** Reframe Item Randomiser as Christopher Hermann's public maintenance fork while preserving every WeiDU-, resource-, and save-facing identifier, then publish the result as v8.1.

**Architecture:** Add a public Markdown landing page, mod-manager metadata, and an explicitly attributed HTML manual while changing only TP2 author/version metadata. Perform the GitHub repository and branch rename only after local verification, then publish a new immutable v8.1 tag and runtime archive instead of moving v8.

**Tech Stack:** Markdown, XHTML, WeiDU TP2/INI metadata, PowerShell 7, Git, GitHub CLI.

---

Use `@bg-modding` and `@weidu-modding` for the local changes and
`@verification-before-completion` before any release claim. The approved design is
`docs/plans/2026-08-31-public-maintenance-fork-design.md`.

### Task 1: Add the public landing page and mod metadata

**Files:**
- Create: `README.md`
- Create: `randomiser.ini`
- Modify: `randomiser.tp2:2-3`

**Step 1: Run the branding assertion and observe RED**

Run:

```powershell
pwsh -NoProfile -Command @'
$errors = @()
if (-not (Test-Path -LiteralPath 'README.md')) { $errors += 'README.md missing' }
if (-not (Test-Path -LiteralPath 'randomiser.ini')) { $errors += 'randomiser.ini missing' }
$tp2 = Get-Content -Raw -LiteralPath 'randomiser.tp2'
if ($tp2 -notmatch 'AUTHOR "Fredrik Lindgren \(Wisp\); maintained by Christopher Hermann') { $errors += 'TP2 maintainer attribution missing' }
if ($tp2 -notmatch 'VERSION "8\.1"') { $errors += 'TP2 version 8.1 missing' }
if ($errors.Count) { throw ($errors -join '; ') }
'PASS branding metadata'
'@
```

Expected: nonzero with all four missing/old-state assertions represented.

**Step 2: Add `README.md`**

Use this structure and wording:

```markdown
# Item Randomiser

> Original mod by Fredrik Lindgren (Wisp)  
> Maintained by Christopher Hermann for Chriz's BG Collection

Item Randomiser is a large-scale random treasure mod for Baldur's Gate and Baldur's
Gate II. This repository is a public maintained fork of Wisp's original project and
can be used independently of Chriz's BG Collection.

## Maintained fork

Version 8 and later focus on reliable Mode 1 delivery on EET and compatible Enhanced
Edition installations, durable EEex transactions that do not depend on recipient AI
queues, extensible item/source/endpoint registries, and validation before source
removal. The existing WeiDU-based Mode 2 path remains isolated.

## Installation

Download the latest release and extract it into the game directory so the
`randomiser` folder is beside `chitin.key`. Supply a current WeiDU executable for
your platform under the usual `setup-randomiser` name, then run it normally.

## Documentation and support

- [Full manual](readme-randomiser.html)
- [Latest release](https://github.com/Chrizhermann/chriz-item-randomiser/releases/latest)
- [Report a problem](https://github.com/Chrizhermann/chriz-item-randomiser/issues)
- [Original source](https://github.com/FredrikLindgren/randomiser)
- [Original Gibberlings Three page](http://www.gibberlings3.net/item_rand/)

The package directory, TP2 name, component numbers, and `fl*` runtime identifiers
remain unchanged for WeiDU and saved-game compatibility.

## License and provenance

The upstream repository does not declare a repository-wide license. This fork does
not claim to relicense the original work. Individual files that carry their own
license notices retain those terms.
```

**Step 3: Add `randomiser.ini`**

```ini
[Metadata]
Name = Item Randomiser
Author = Fredrik Lindgren (Wisp); maintained by Christopher Hermann
Description = Public maintained fork of the large-scale BG/BGII random treasure mod.
Readme = randomiser/readme-randomiser.html
```

Do not add speculative install-order fields or a license field.

**Step 4: Update TP2 metadata only**

```text
AUTHOR "Fredrik Lindgren (Wisp); maintained by Christopher Hermann (https://github.com/Chrizhermann/chriz-item-randomiser)"
VERSION "8.1"
```

Leave `BACKUP`, `README`, all paths, labels, and component numbers byte-for-byte unchanged.

**Step 5: Re-run the branding assertion**

Expected: `PASS branding metadata` and exit 0.

### Task 2: Reframe the HTML manual without erasing upstream history

**Files:**
- Modify: `readme-randomiser.html:4-24,109-140,299-347`

**Step 1: Run an HTML framing assertion and observe RED**

Check for all of these strings with a PowerShell assertion: `Maintained by Christopher
Hermann`, `About this maintained fork`, `Maintained Fork Version History`, `Original
Version History`, the new repository URL, and `Version 8.1`. Expected: nonzero.

**Step 2: Replace the header attribution**

Keep the `<title>` and `<h1>` as `Item Randomiser`. Replace the header block with:

```html
<p><strong>Original mod:</strong> Fredrik Lindgren, also known as Wisp or Irrbloss.<br />
<strong>Maintained fork:</strong> Christopher Hermann for Chriz's BG Collection.<br />
<strong>Current project:</strong> <a href="https://github.com/Chrizhermann/chriz-item-randomiser">GitHub repository</a> and <a href="https://github.com/Chrizhermann/chriz-item-randomiser/releases/latest">latest release</a>.<br />
<strong>Original project:</strong> <a href="https://github.com/FredrikLindgren/randomiser">source repository</a>, <a href="http://www.gibberlings3.net/item_rand/index.php">Gibberlings Three page</a>, and <a href="http://forums.gibberlings3.net/index.php?showforum=170">forum</a>.</p>
<p><strong>Version 8.1</strong><br />
<strong>Languages:</strong> English, Polish.<br />
<strong>Platforms:</strong> Windows, Mac OS X, Linux.</p>
```

**Step 3: Add “About this maintained fork” before Overview**

State that the fork preserves Wisp's design and installer identity, is maintained publicly,
and that v8+ adds the cross-campaign fix, manifest-driven delivery on capable EEex installs,
durable later-boundary verification, extension registries, and validation before removal.
State that Mode 2 retains its existing install-time path. Keep this spoiler-free.

**Step 4: Replace the obsolete installation section**

Describe the actual ZIP: it contains the `randomiser/` runtime folder and does not bundle a
WeiDU launcher. Tell users to obtain current WeiDU for their platform, place/rename it as
`setup-randomiser`, and run it from the game directory. Preserve the exact technical folder
and setup basename in every platform example.

**Step 5: Qualify the legacy delivery known issue**

Replace the opening Mode 1 delayed-creature paragraph with wording that distinguishes the
new manifest backend from the legacy BCS backend. Explain that the manifest backend verifies
delivery after an engine boundary and uses guarded fallback policy; legacy installs can still
have delayed recipient delivery. Do not name encounter locations or item locations.

**Step 6: Separate acknowledgements and histories**

- Rename the heading to `Original Acknowledgements`.
- Add one sentence explaining that the first-person acknowledgements below are preserved from
  Wisp's original readme.
- Create `Maintained Fork Version History` containing v8.1 and the existing v8 entry.
- Create `Original Version History` beginning with v7 and preserve every older entry unchanged.
- The v8.1 entry should say only that public identity, attribution, documentation, metadata,
  and packaging were updated; no gameplay behavior changed.

**Step 7: Re-run the framing assertion**

Expected: all required strings occur and the command exits 0.

### Task 3: Prove the reframe has no runtime delta

**Files:**
- Verify: `README.md`
- Verify: `randomiser.ini`
- Verify: `randomiser.tp2`
- Verify: `readme-randomiser.html`

**Step 1: Check the allowed diff**

Run:

```powershell
$runtimeDelta = @(git diff --name-only v8..HEAD -- baf copy d languages lib lists ssl style spoilers-randomiser.html)
if ($runtimeDelta.Count) { throw "Unexpected runtime changes: $($runtimeDelta -join ', ')" }
```

Expected: exit 0 with no paths.

**Step 2: Assert compatibility-sensitive TP2 markers**

Require exact matches for `BACKUP "randomiser/backup"`, `README
"randomiser/readme-randomiser.html"`, language paths under `randomiser/`, and components
1100/1200/1300/1400. Assert the TP2 basename and package directory were not renamed.

**Step 3: Run whitespace and parse checks**

Run `git diff --check`, then the existing production TP2 parse through the canonical suite.

**Step 4: Run the full canonical suite**

```powershell
pwsh -NoProfile -File .\tests\run-unit.ps1
```

Expected: exit 0, all PowerShell groups PASS, all WeiDU parse checks PASS, Lua PASS,
Mode boundary `passed=10 failed=0`.

**Step 5: Commit the local reframe**

```powershell
git add README.md randomiser.ini randomiser.tp2 readme-randomiser.html
git commit -m "docs: frame Item Randomiser as maintained fork"
```

### Task 4: Build and preflight the immutable v8.1 package

**Files:**
- Create outside repository: `C:\Users\chris\.codex\artifacts\bgee-itemrandomiser-v8.1-<commit>\randomiser-v8.1.zip`

**Step 1: Verify release commit state**

Require a clean worktree, TP2 `VERSION "8.1"`, and record `git rev-parse HEAD`.

**Step 2: Create annotated tag locally**

```powershell
git tag --annotate v8.1 --message "Item Randomiser v8.1" HEAD
```

Verify `git cat-file -t v8.1` is `tag` and `git rev-list -n 1 v8.1` equals HEAD.

**Step 3: Build the archive from the tag**

Use `git archive --format=zip --prefix=randomiser/` with only these paths:

```text
README.md randomiser.ini randomiser.tp2 readme-randomiser.html spoilers-randomiser.html
baf copy d languages lib lists ssl style
```

Do not package `docs`, `research`, `tests`, `.git*`, or setup executables.

**Step 4: Compare archive paths exactly**

List non-directory ZIP entries, strip the `randomiser/` prefix, and compare them with
`git ls-tree -r --name-only v8.1 -- <same path list>`. Expected: zero differences.

**Step 5: Verify contents and hash**

Read `randomiser/randomiser.tp2` directly from the ZIP and require `VERSION "8.1"`.
Require the new README and INI and absence of internal directories. Record SHA-256 and size.

### Task 5: Rename and configure the public repository

**Files:**
- Modify GitHub repository settings and shared local Git remote only.

**Step 1: Preflight identity and remote state**

Require active `gh auth status` account `Chrizhermann`, clean local state, local `repair`
ahead of or equal to `origin/repair`, no existing `Chrizhermann/chriz-item-randomiser`, and
no remote `v8.1` tag.

**Step 2: Push the verified commit under the old name**

```powershell
git push origin repair
```

Verify remote `repair` equals the release commit before renaming anything.

**Step 3: Rename the repository and update `origin`**

```powershell
gh repo rename -R Chrizhermann/randomiser chriz-item-randomiser --yes
git remote set-url origin https://github.com/Chrizhermann/chriz-item-randomiser.git
```

Leave `upstream` unchanged.

**Step 4: Rename the default branch through GitHub**

```powershell
gh api --method POST repos/Chrizhermann/chriz-item-randomiser/branches/repair/rename -f new_name=main
git branch --move repair main
git fetch --prune origin
git branch --set-upstream-to=origin/main main
```

Verify GitHub default branch is `main` and local HEAD equals `origin/main`.

**Step 5: Set public metadata and enable Issues**

```powershell
gh repo edit Chrizhermann/chriz-item-randomiser `
  --description "Public maintained fork of Wisp's Item Randomiser for BG/BGII, with reliable EET/EEex Mode 1 delivery and extensible manifests." `
  --homepage "https://github.com/Chrizhermann/chriz-item-randomiser/releases/latest" `
  --enable-issues `
  --add-topic baldurs-gate `
  --add-topic baldurs-gate-2 `
  --add-topic infinity-engine `
  --add-topic weidu `
  --add-topic eeex `
  --add-topic eet `
  --add-topic item-randomizer
```

Verify description, homepage, topics, default branch, fork parent, visibility, and issue setting.

### Task 6: Publish and verify v8.1

**Files:**
- Upload: `randomiser-v8.1.zip`
- Modify: GitHub releases `v8.1` and the description of historical `v8`

**Step 1: Push the annotated tag**

```powershell
git push origin v8.1
```

Verify the remote tag object is annotated and peels to the same commit as `origin/main`.

**Step 2: Publish v8.1**

Create a non-draft, non-prerelease, explicitly Latest release titled `Item Randomiser v8.1`.
Release notes must lead with original-author/current-maintainer attribution, summarize only
the public reframe, link the full v8 runtime changes, state that gameplay behavior did not
change from v8, and explain that the ZIP requires a separately supplied WeiDU launcher.

Use `gh release create v8.1 <asset>#Runtime mod files --verify-tag --latest`.

**Step 3: Add an idempotent pointer to v8**

Read the existing v8 body first. If it does not already begin with a maintained-fork notice,
prepend a short attribution block and link to the v8.1/current release. Preserve every
existing v8 note verbatim after the new block. Do not alter the v8 tag or asset.

**Step 4: Re-download and verify the asset**

Download `randomiser-v8.1.zip` to a new exact verification directory. Require the recorded
SHA-256 and size, embedded TP2 version 8.1, exact runtime path set, and no internal material.

**Step 5: Verify all public state**

Require:

- new repository URL resolves and old repository Git URL still redirects;
- default branch and annotated tag peel to the verified commit;
- v8.1 is Latest, non-draft, and non-prerelease;
- v8 remains available and unchanged apart from its release prose;
- Issues are enabled and topics/description/homepage are exact;
- the GitHub TP2 blob equals the local tag blob;
- local `main` is clean and equals `origin/main`;
- no workflow success is claimed when the repository has no workflows.

**Step 6: Report the result**

Provide the new repository and release links, commit/tag, artifact SHA-256, canonical test
result, explicit no-runtime-change boundary, license status, and confirmation that the live
game and saves were untouched.

