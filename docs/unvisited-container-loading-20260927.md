# Unvisited container loading: bounded diagnostic

The player must be able to receive a relocated reward before visiting its
original area. The current native fixture cannot do that: it resolves only the
visible area's sources and waits for their removal. This investigation finds a
place to intercept a selected original before access, without changing shared
resource bytes. It does not yet remove anything or authorize an early receipt.

## Evidence and scope

The copied BG2:EE executable has SHA256
`fc821a4806a0305b84fd85f1aad2bd472c8db642ed34b4494ae62351cae1c580`.
Its `CInfGame::LoadArea` calls `CGameArea::Unmarshal` at preferred address
`0x14027EFD2`, before the successful path's `EnterSpawn` (`0x14027F052`) and
`OnActivation` (`0x14027FAA9`). Within unmarshal, the area container-table loop
calls `CGameContainer::CGameContainer` at `0x14018D9C9`.

The container's raw item loop at `0x140190270` sees the physical item-table
index and 20-byte record before resref processing or CItem construction. The
ordinary serialized-item path constructs its item at `0x140190695`, appends it
at `0x1401906D9`, then later registers the container at `0x1401907A5` and adds it
to the area at `0x1401907E6`. A separate generated-item path also exists;
ordinary physical records do not establish support for it.

The [pinned EEex structure documentation](https://github.com/Bubb13/EEex-Docs/blob/35445db362f56095156e3b43aa8f6f0f50f728a0/source/EE%20Game%20Structures%20%28x64%29/CG/index.rst)
places `CGameArea.m_resref` at `+0x204`. The inspected EEex hook implementation
supports a before/after call hook and restoration of complete instructions.
These are low-level implementation leads, not a public area-loaded callback or
a general pre-access guarantee. The binary addresses apply only to the pinned
executable, not to another build.

## Implemented diagnostic

`tools/probes/FLPRArea.lua` is explicitly loaded from the accepted quiet fixture.
It observes only the authored `FLPRU1` area. Both signatures are validated before
allocating or patching. It installs a combined constructor entry/exit hook and
a raw-item-loop hook, preserving registers, flags and displaced instructions.
The latter displaces exactly seven bytes: two complete, non-branch instructions.
Entry and exit assembly labels differ because they share one detour.

The native code copies primitives only and calls neither Lua nor engine code.
The one-shot 2,048-row buffer has atomic tickets, an active-writer count, an
overflow marker and a last-written publication marker. Invalid/unpublished rows
remain visible and make the snapshot unstable; they are not silently skipped.
The captured raw names, payloads and physical indexes can be compared with the
exact input bytes after the trace has stopped. Partial installation restores
code protection, remains disarmed, and requires process shutdown before retry.

`tools/build_unvisited_fixture.py` builds the second area offline from the
previously copied BG2600.ARE. It preserves art references and unrelated raw
bytes, disables active scripts/actors/spawns/regions/rest encounters, and adds
three authored containers: two with three total test items and one empty.
An unreferenced item at table index zero makes the referenced physical indexes
start at one. No real item or destination choices are made.

`tools/validate_area_trace.py` compares each complete load with that load's
actual ARE input. It checks every row and exact payload, owner, table pointer,
index and boundary; losses and extra events fail. A saved area may repack its
tables, so a second load is checked against its serialized ARE, never blindly
against the original's indexes. `extract_area` in the existing save inspector
extracts just that explicitly named area with the same bounded decompression
and duplicate checks as its retained BG2600 wrapper.

## Validation and next acceptance sequence

All **836 offline tests passed**. This includes the builder, raw trace validator,
probe failure guards on three Lua runtimes, and named saved-area extraction.
Static hook review matched both signatures and the register/stack mappings.
Neither tests nor static review establish native execution.

The pending native sequence, after the other BG tasks are idle:

1. Acquire the shared reservation, protect existing resources/saves and stage
   only the isolated fixture. Load the already accepted quiet held save.
2. Confirm its save lacks FLPRU1. Arm once and queue one ordinary area-travel
   action to FLPRU1; capture entry, records and exit before the world observation.
3. Observe the exact live container contents without pickup or creation. Save
   under a new diagnostic name, copy the save, and extract its FLPRU1.ARE.
4. Reload that save in the same process while the diagnostic remains armed.
   Stop the trace and validate both loads against their respective raw inputs.
5. Quit normally, restore staging, verify protected hashes and release the
   reservation. Record failures rather than replaying consumed actions.

Further work still needs an actual suppression mechanism, campaign decision
availability before loading (including restore), early-delivery representation,
excluded-original preservation and A/B campaign isolation. Container results
cannot establish CRE, shop, script-grant or repeated-spawn coverage.

Evidence is kept outside the installation in
`work/source-interception-20260927/`. Old mutation requests must not be rerun.
