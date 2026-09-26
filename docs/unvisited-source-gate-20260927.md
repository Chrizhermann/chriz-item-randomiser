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

All **854 offline tests passed** after adding the probe: ten new instruction
tests and eight new wrapper tests. Install the test dependencies from
`tests/requirements.txt` in the chosen test environment. This run kept the new
emulator libraries under the ignored evidence directory and supplied that path
through `PYTHONPATH`; it did not modify the game's Python or Lua environment.

Native acceptance is pending. The planned sequence creates selected and excluded
save branches before visiting FLPRU1, reloads each before first entry, checks the
exact live contents, saves the results, and reloads both in one process. These
are deliberately branches of the already accepted quiet fixture, not evidence
of fresh randomization or a new seed. Missing-mode loading must also retain the
original. A separate fresh-process check and eventual early reward protocol
remain necessary.

Fresh evidence belongs in `work/source-gate-20260927/`. All game access requires
the shared reservation and an idle machine slot; staging is temporary and all
owned processes must close before restoration and release.
