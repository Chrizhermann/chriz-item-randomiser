# Unvisited-original interception: established findings

The completed load-only and standalone saved-choice diagnostics have been
retired from the current tree. Their code, tests and full reports remain in
commits `9959517` and `8421330`. The integrated implementation and its current
acceptance status are in [campaign source guards](campaign-source-guards-20260927.md).
Retain the findings below when changing that implementation.

## Native loading path

The tested Windows BG2EE executable has SHA256
`fc821a4806a0305b84fd85f1aad2bd472c8db642ed34b4494ae62351cae1c580`.
Addresses below are RVAs relative to its image base, not portable API addresses.

- `27EFD2`: `CInfGame::LoadArea` calls `CGameArea::Unmarshal` before activation.
- `18D9C9`: Unmarshal constructs a container from the raw ARE record and item table.
- `190270`: the container loop sees a 20-byte item record before constructing CItem.
  Skipping to the ordinary loop increment at `1906FC` prevents that construction.
- `292A84`: capture the serialized GAM pointer and length after frame setup.
- `292F45`: after GLOBAL import, the saved campaign choice is available before areas load.

Saved areas reorder containers and repack item indexes. The authored A/B/C order
became C/B/A, and unreferenced item zero disappeared. An original physical index
cannot identify a returned item in a later save. The integrated guard therefore
also checks full authored ARE content and the specific load invocation.

## What actually passed

The raw-loading run matched all three authored containers and their payloads on
first entry and reload. The standalone saved-choice gate then removed only
FLPRI1 from FLPRU1A in the selected branch, retained it in the excluded branch,
and preserved unrelated items. Selected saves contained two referenced items;
excluded saves contained three. A second process confirmed the same saved-choice
behavior. These were branches of an existing campaign, not fresh New Games.
No early reward or integrated campaign receipt was involved.

Local evidence remains under `work/source-interception-20260927/`,
`work/source-gate-20260927/`, and `work/source-gate-reload-20260927/`.
Those runs closed normally. Their one-shot mutation requests must not be replayed.
For travel the working console call was `C:MoveToArea("FLPRU1")`;
`MoveBetweenAreas` did not produce a successful entry in this fixture.

The current `FLPRGateAsm.lua` retains instruction emulation used by the integrated
campaign tests. The old install wrappers and trace-validator-only tests are no
longer needed. These container results establish no general CRE, shop,
script-grant or repeated-spawn support.
