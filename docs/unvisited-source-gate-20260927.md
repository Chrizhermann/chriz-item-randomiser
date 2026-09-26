# Synthetic unvisited-original suppression

This follows the accepted [raw container-loading diagnostic](unvisited-container-loading-20260927.md).
It is a test-only interception of FLPRI1 in the authored FLPRU1A container. It
does not change campaign receipts or enable production early reward delivery.

## Campaign decision before area loading

The copied executable is the same pinned BG2:EE build, SHA256
`fc821a4806a0305b84fd85f1aad2bd472c8db642ed34b4494ae62351cae1c580`.
Its CInfGame::Unmarshal receives the serialized GAM pointer and size. The probe
captures those primitives at `0x140292A84`, after frame setup and before the size
register is reused. It displaces two whole instructions totaling eight bytes.

At `0x140292F45`, after the GLOBAL import loop and InitDifficulty, it reads the
serialized GLOBAL table with bounds checked against that captured size. This
seven-byte span follows EEex's existing before-call hook rather than replacing
it. Both zero-count and nonzero-count paths pass here before the first direct
LoadArea call at `0x14029344F`. The snapshot accepts exactly one full, padded
`FLPR_U1_MODE` name: value 1 selects the test original, value 2 excludes it.
Missing, malformed, duplicate or unknown values cannot enable suppression.

The probe does not scan the live GLOBAL hash table, whose resize can expose
separate pointer and capacity updates. It consumes the engine-owned serialized
input synchronously within Unmarshal, then clears its input pointer and length.
No resource bytes are changed. A new Unmarshal clears the previous mode before
capturing another; EEex's existing game-destroyed listener clears it as well.
New Game integration and broader native input ownership remain separate gates.

## Exact synthetic interception

The early container-item-loop hook at `0x140190270` observes the raw 20-byte item
record before item or CString construction. It may skip exactly the authored
FLPRU1A / FLPRI1 record: physical index 1, first index 1, count 2, coordinates
1140/650, type 4, lock 0, expiration and all charges 0, flags 1. Names include
their full zero padding. It requires a nonnull owner, the selected campaign
snapshot, no trace overflow and no earlier suppression in that load.

Its alternate exit is `0x1401906FC`, the ordinary loop increment. It bypasses
both construction and the cleanup of temporaries that were never constructed.
Both exits restore registers, flags and stack; the alternate exit uses its own
EEex integrity-watchdog exit. Displaced instructions execute on the normal path.
An item added later with a different payload or table position is not silently
treated as the original. Changed/duplicate candidates are diagnostic failures,
not completed source operations.

The raw item pointer and index are constructor inputs that the engine itself is
about to dereference. The hook has no authoritative whole item-table byte length;
it is not a malformed-ARE memory-safety boundary. Its native acceptance must be
limited to the verified authored area and extracted saved-area inputs. Discovery
and compiler quarantine remain responsible for rejecting malformed resources.

## Evidence and acceptance

`FLPRGateAsm.lua` builds the three assembly bodies without engine access;
`FLPRGate.lua` validates all signatures, allocates a bounded process-lifetime
trace and installs it once, disarmed. Native hooks call neither Lua nor engine
functions. Every suppression first obtains a trace row; overflow leaves the
record to the ordinary engine path. Stopped snapshots preserve incomplete rows
as failures. The diagnostic never creates or confirms a reward.

Offline instruction-emulation tests execute the generated assembly with synthetic
memory and independent payloads. They do not model EEex's watchdog macros,
engine threading, resource lifetime or real save-loading behavior. Separate Lua
tests check installation/lifecycle/snapshot guards on three Lua runtimes.
The extra test dependencies are `keystone-engine==0.9.2` and `unicorn==2.1.4`;
they are required for these tests, without silent skips.

All **863 offline tests passed**, including ten new instruction tests, eight
new wrapper tests and nine trace-validator tests. Install the test dependencies from
`tests/requirements.txt` in the chosen test environment. This run kept the new
emulator libraries under the ignored evidence directory and supplied that path
through `PYTHONPATH`; it did not modify the game's Python or Lua environment.

The stopped trace validator consumes the exact GAM and ARE inputs in load order.
It checks every record, physical index, payload, mode and per-load owner pointer;
the observed world and saved contents are separate acceptance checks. Lead review
caught an inverted area/owner mapping in the initial validator and its mock
oracle. The corrected tests also feed the actual emulated producer's rows into
the validator, and model the engine's saved C/B/A order with only two remaining
items. All 863 tests passed after that correction.

## Native acceptance

The first process passed six ordered loads and 26 complete events. Mode 1 skipped
exactly FLPRI1 in the pristine FLPRU1A container; mode 2 and an absent mode retained
it. All observed FLPRX1/FLPRI2 payloads, three container identities, the empty
container, existing assignments, seed and held phases were preserved. Selected
and excluded saves were created before visiting FLPRU1, then after entry. The
entered selected save contains two items, the excluded save three. Both reload
correctly after the engine reverses container order and repacks item indexes.

Private diagnostic saves created:

- `000000055-IR Gate Selected Ready 0927`
- `000000056-IR Gate Excluded Ready 0927`
- `000000057-IR Gate Selected Entered 0927`
- `000000058-IR Gate Excluded Entered 0927`

The second, fresh process first loaded the two entered saves, then the two ready
saves and first-entered FLPRU1 from each. Its 19 complete events match four exact
GAM/ARE inputs. Already-removed FLPRI1 stayed absent; excluded FLPRI1 stayed
present; the selected ready save caused exactly one new suppression for that
newly constructed world. The trace/world area and owner pointers agree, and all
full item payloads match the first process. No receipt or delivery state changed.
These are branches of the accepted quiet fixture, not fresh campaigns or seeds.

Both processes used separate non-input desktops and normal Quit. The first run
restored 19 staged paths and verified 92 protected files; the second restored 19
and verified 108, including all four new named saves. Both reservations were
released with game and loader absent. A read-only completion check initially
used the wrong main-menu target string; the saved status showed completion, and
the corrected check succeeded without repeating the menu action. The fresh-run
offline evidence helper also needed Windows case-insensitive path matching for
`BALDUR.gam`; duplicate normalized keys are rejected.

Evidence: `work/source-gate-20260927/trace-acceptance.json` and independently
checked `artifact-acceptance.json`/`.md`; the fresh process is in
`work/source-gate-reload-20260927/trace-acceptance.json` and
`world-acceptance.json`. Both directories have `native-released.json`.

This establishes bounded synthetic suppression and saved-policy isolation. The
next gate is actual New Game enrollment and an explicit early-reward protocol:
protected-before-access must not be represented as already physically removed.
CRE/store/script sources, broad template coverage and failure recovery are still
unproven. The temporary FLPRU1 resource must be staged before using these private
diagnostic saves; no production content or runtime component was changed.
