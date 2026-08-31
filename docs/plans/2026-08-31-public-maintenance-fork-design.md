# Item Randomiser Public Maintenance Fork Design

**Date:** 2026-08-31  
**Status:** Approved

## Context

Item Randomiser was created by Fredrik Lindgren (Wisp). The current public fork is
maintained by Christopher Hermann for Chriz's BG Collection and is also intended to be
useful as a standalone public mod. Version 8 contains the first maintained-fork runtime
release, but the repository, README, release, and support metadata still present the
project almost entirely as the original upstream mod.

The public framing should distinguish original authorship from current maintenance
without changing the technical identity that WeiDU installations and saved games use.

## Public identity

- Player-facing name: **Item Randomiser**.
- Repository: **`Chrizhermann/chriz-item-randomiser`**.
- Attribution shown prominently:

  > Original mod by Fredrik Lindgren (Wisp)  
  > Maintained by Christopher Hermann for Chriz's BG Collection

- Describe the project as a **public maintained fork**, not as the official upstream
  continuation and not as a new mod authored entirely by the maintainer.
- Preserve links to the original GitHub repository and Gibberlings Three pages under an
  explicit "Original project" label.

## Compatibility boundary

The reframe must not change identifiers used by WeiDU, installed resources, or saves:

- package directory `randomiser/`;
- `randomiser.tp2` and `setup-randomiser` basename;
- WeiDU backup path and `DESIGNATED` component numbers;
- existing `fl*` resources, scripts, GLOBALs, markers, and transaction state;
- runtime EEex filenames and manifest schema identifiers.

The GitHub repository, default branch, prose, metadata, release presentation, and archive
label may change. The release archive must still unpack to the compatible `randomiser/`
layout.

## Documentation and metadata

Add a concise root `README.md` for GitHub. It should contain:

- the title and attribution block;
- a short description of the original mod;
- a spoiler-free summary of maintained-fork changes;
- supported games and the Mode 1 / Mode 2 distinction;
- current installation and release links;
- links to the full HTML manual, original project, and issue tracker;
- an accurate license and provenance note.

Update `readme-randomiser.html` to:

- identify original author and current maintainer separately;
- add an "About this maintained fork" section;
- summarize the v8+ reliability, EEex delivery, extensibility, and compatibility work;
- make installation instructions match the ZIP distribution;
- separate maintained-fork history from the original version history;
- preserve the original acknowledgements and clearly attribute their first-person voice;
- distinguish original support links from current support links;
- replace obsolete maintained-fork behavior notes where the new backend changed them.

Add `randomiser.ini` with mod-manager metadata while retaining the technical mod name.
Do not add or imply a repository-wide software license: upstream declares none. Preserve
all file-specific license notices and state the repository-wide status plainly.

## GitHub presentation

- Rename the repository from `randomiser` to `chriz-item-randomiser`.
- Rename the default branch from `repair` to `main`.
- Update the local shared `origin` URL after the repository rename; retain `upstream`.
- Use a description that credits the original mod and identifies the reliable EET/EEex
  maintenance work.
- Replace the stale homepage with the maintained fork's current release page.
- Add focused topics for Baldur's Gate, WeiDU, EEex, EET, and item randomisation.
- Enable GitHub Issues for public maintenance. Do not add elaborate issue templates yet.

## Release policy

Published tag `v8` remains immutable. Its release description may gain attribution and a
pointer to the current release, but its tag, source archives, and asset remain unchanged.

The completed reframe ships as version **8.1**:

- synchronize TP2 and README versions to `8.1`;
- create annotated tag `v8.1` on the verified branding commit;
- build `randomiser-v8.1.zip` with the compatible internal layout;
- publish it as GitHub Latest and non-prerelease;
- keep `v8` available as historical evidence.

## Verification

The change is documentation, metadata, and repository presentation only. Verification is:

1. assert that every compatibility-sensitive name and component number is unchanged;
2. run the complete hermetic PowerShell, WeiDU, and Lua suite;
3. confirm the production TP2 parses and reports version 8.1;
4. build the release archive from the immutable release commit;
5. compare every packaged path with the intended runtime file set and exclude tests,
   research, and internal plans;
6. re-download the published asset and compare its SHA-256 and embedded TP2 version;
7. verify new and redirected repository URLs, default branch, annotated tag target,
   release flags, metadata, topics, issue setting, and clean local state.

No game installation or in-game test is required because runtime behavior and installed
identifiers do not change. The live game directory and saves remain out of scope.

