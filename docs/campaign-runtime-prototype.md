# Experimental campaign protocol

This implements the logical part of milestone 3 in the
[Mode 1 design](plans/2026-09-22-mode1-postinstall-profiles-design.md). It is
**not playable or installed by the TP2**. The existing delivery backend is unchanged.
The compiler consumes explicitly reviewed physical source/target bindings; the
Lua controller models campaign capture and the source/delivery transaction rules.
Neither performs native item mutation or detects a real New Game.

## Prepare experimental input

Create the catalogue, world scan, profile, and binding plan using the
[configurator](post-install-configurator.md) and [binding interface](source-binding-drafts.md).
Then, from this repository:

```powershell
python tools/randomiser_config.py prototype prepare --catalog 'D:/RandomiserDrafts/catalog.json' --world 'D:/RandomiserDrafts/world.json' --profile 'D:/RandomiserDrafts/profile.json' --plan 'D:/RandomiserDrafts/bindings.json' --store 'D:/RandomiserDrafts/prototype'
python tools/randomiser_config.py prototype inspect --store 'D:/RandomiserDrafts/prototype'
```

Inspection also accepts `--revision <sha256>` for an earlier revision. Every
artifact has schema `flir-campaign-prototype-v1` and state `prototype-not-playable`.
Preparation produces immutable `revisions/<sha256>.json` and `.lua` companions,
then updates the separate `prototype-next.json` pointer. Reading verifies the
content hash, schema, and exact generated Lua bytes. It never falls back from a
missing requested revision to the next-run pointer. Keep revisions needed by saves.

Only finite physical relocation is accepted: creature inventory records, embedded
creatures, containers, and finite shop stock. This is format coverage, not proof
of runtime support for those sources. Script registrations and deliberate additions
are rejected in this prototype. It preserves the full selected record, including
all charges, flags, expiration, owner, and stock quantity. Source IDs and target
IDs are sorted before hashing. Limits are 4,096 reward units and 4,096 target slots;
oversized input fails without truncation. Tier selection and overlap resolution
are still future work. Christopher continues to own all creative selections.

The generated Lua contains data only. Strings use fixed-width escaped UTF-8 bytes;
configuration text cannot become Lua expressions. Integer values must be exactly
representable in Lua 5.1. JSON null becomes Lua nil, so null object fields are absent
in Lua. The prototype's required source/target arrays do not permit null entries.
Outputs remain outside the scanned game tree. Preparation neither invokes WeiDU
nor changes resources or saves.

## Controller and host responsibilities

`prototype/FLPRCore.lua` exports `Core.new(storage, resolve_revision)`. Storage
provides numeric `get`/`set` methods. The resolver must return the **validated exact
immutable revision**, not an arbitrary Lua file or the next-run configuration.
The Python reader validates generated companions; there is no native runtime
artifact loader yet. The core checks its logical shape but does not calculate SHA-256
or replace the compiler's full schema validation.

`prototype/FLPRGlob.lua` is an explicit adapter for the supported EEex numeric
GLOBAL read/write functions. It registers no listeners. Tests substitute ordinary
Lua tables for both the adapter's game API and the save store.

| Host call | Contract |
| --- | --- |
| `capture(manifest, seed, "confirmed-new-game")` | Requires a trusted New Game handoff and an unmarked campaign. Copies the revision, draws assignments with a private seeded generator, writes all fields, then publishes the committed marker. The caller supplies a seed in 1..2,147,483,646. |
| `restore()` | Resolves the saved revision and validates the saved assignment and journal state. An unmarked save remains inactive; a missing revision or incomplete capture never triggers a fresh draw. |
| `begin_source(index, witness)` | Requires exactly one resolved occurrence with the selected source ID, quantity, and full payload. Persists an in-flight journal before returning a logical removal command. |
| `begin_delivery(unit, witness)` | Requires confirmed suppression of that unit's original source and an available assigned target. Persists a journal before returning a logical creation command. |
| `on_observation_boundary()` / `observe(receipt)` | Confirmation must come from a later host observation, identify the same transaction and session, and prove the requested outcome. The boundary call itself is not evidence of an engine tick. |
| `pending_observation()` | After restore, requests fresh inspection of an in-flight transaction. Its `observe-only` result never permits replaying removal or creation. |
| `destroy()` | Invalidates cached state and pending callback identity when the host proves session destruction. Saved GLOBALs are unchanged. |

Capture stores the complete 256-bit revision as 16 bounded numeric chunks, the
seed, explicit per-unit target slots, and per-source/per-unit phases. A revision
and seed are not a unique campaign ID: progress belongs to each save's own GLOBALs.
Assignments use a private Park-Miller generator and Fisher-Yates shuffle, leaving
the game's random generator untouched. The same seed and revision reproduce the
same assignment across the tested Lua runtimes. Automatic fresh seed selection is
a future host responsibility.

All writes are read back. The campaign marker is committed last; a partial capture
cannot be resumed as a fresh run. Missing phase variables are invalid, not fresh
attempts. Only one mutation can be in flight. A partial journal disables progress
rather than guessing whether a command ran. If confirmation committed just before
journal clearing was interrupted, restore clears the journal without replaying it.
Host callback boundaries must catch storage/resolver errors and disable this path;
the core deliberately raises on invalid GLOBAL values or failed writes.

Witness identities must describe an exact runtime occurrence, not just an item
resref or owner template. The core validates the receipt's shape and identity;
the future host must obtain the actual evidence. Source confirmation proves the
selected quantity was removed and none of that selected occurrence remains.
Delivery confirmation must prove this transaction's new instance, exact payload,
and actual lootability. A matching pre-existing item or an increased count is not
sufficient. Bad outcome evidence quarantines the transaction without automatic
replay. Missing targets defer delivery and do not create a fallback pile.

Each command also carries an opaque Lua session table. A receipt must preserve
that exact reference; it cannot serialize or deep-copy it. Restore/destroy/new
capture renew the reference, rejecting callbacks left over from another session
even when its revision, index, and nonce happen to match. Reload recovery must
reacquire world evidence and use the new observation request's session reference.

## Remaining engine gates

[Pinned EEex source evidence](runtime-feasibility-evidence.md) establishes numeric
GLOBAL access but does not establish a reliable New Game completion callback or
safe exact-copy removal. Lua initialization is process startup, and sprite-loaded
callbacks run inside unmarshalling. Neither is sufficient to activate this path.

Before a playable slice, prove the lifecycle handoff; persist/reload real campaign
state; resolve exact, nonoverlapping owner instances; suppress originals before
player access; inspect mutable shop stock; and verify lootable delivery. Also prove
that source suppression does not require the player to visit the source before a
distant assigned reward can become available. Preserve excluded originals and
independence when switching between two campaigns in one process.

The legacy installer removes originals at install time. The new opt-in path must
preserve those originals and avoid reinterpreting existing legacy tokens. Swapping
a shared manifest or mutating shared demanded resource bytes cannot establish
campaign isolation. None of these migration or native adapters is enabled here.

## Validation boundary

```powershell
python -m unittest discover -s tests -p 'test_postinstall_*.py' -v
python -m unittest discover -s tests -p 'test_campaign_runtime.py' -v
```

The first suite needs Python 3.10+ only. The second uses the separately installed
test dependency `lupa` (validated with 2.8), including Lua 5.1, Lua 5.3, and LuaJIT
2.1. It fails if a requested runtime is missing. It exercises synthetic save stores,
exact-copy ambiguity, changed next-run settings, interrupted writes, reloads,
quarantine, delayed observations, stale callbacks, and compiler-to-Lua integration.
These checks establish the logical protocol, not game-engine acceptance. No live
game, installed mod, or player save was modified or used to claim persistence.

### Separate native fixture

The opt-in disposable BG1 fixture now has bounded native delivery and serialized
save evidence. Its current results, remaining checks, and QA save-render crash
are tracked in [native fixture status](native-fixture-status.md). Those results
do not activate the general adapter through the normal installer or establish
full-game acceptance.

The [26 September follow-up](native-acceptance-20260926.md) separately verified
B reload in a fresh process and two new campaigns under unchanged B settings.
It also records the first fresh campaign's manual-save refusal, the QA diagnostic
fix, the accepted initial autosave evidence, and complete test-process cleanup.

### Scanner status and installation check

A read-only scan of the isolated EET installation on 2026-09-22 indexed 3,544
effective items. Resource access also read all 8,209 CREs and 1,016 AREs, with its
installation snapshot checks passing. These are item-index and resource-access
results, **not a completed world/source catalogue**.

The resource reader and world resource-identity validator now retain legitimate
internal spaces such as `MONKTU 8.DLG` in KEY entries and override filenames.
Leading/trailing spaces, control characters, path separators, and unsafe filename
metacharacters remain rejected.

The earlier world scan stopped at `BDABIS1D.CRE`: its physical inventory record 0
contains an all-NUL item resref, although slot 0 references that record. Record 1
contains `S1-4` and is referenced by slot 9. The parser raised
`Invalid world resource reference.` On 2026-09-23 the offline scanner was changed
to preserve empty physical records explicitly, including their payload and
ownership, and reject them for binding/compilation. Valid subsequent records keep
their original indexes. See [the representation contract](source-binding-drafts.md).

The authorized read-only rescan ran on 2026-09-24 after the other local BG tasks
were idle and no game or installer process was running. The item scan succeeded
with 3,544 items and explicit `en_US` localization. Direct raw-byte comparison
also confirmed the real `BDABIS1D.CRE` preserves both physical records, their
payloads, indexes, and slots under the new parser.

That first world scan **failed**: `BDCORPSE.CRE` has zero item records, while slots
21 and 22 reference indexes 0 and 1. Unlike an existing record with an empty
resref, those records do not exist. The scanner retained its strict rejection;
no resource was skipped and no world catalogue was published. The subsequent
offline change now preserves these references as owner diagnostics and excludes
affected sources/targets from binding and compilation. The retry passed that
resource but stopped at a malformed item name in `OHDFaer.CRE`. CRE discovery now
preserves those raw name bytes and likewise quarantines the owner. The last newly
observed shape, incomplete embedded actor storage, is also retained as unresolved
and excluded from target selection. The final scan completed with 41,577 source
records and 24,305 target candidates; the installation-snapshot check passed.
This completes physical discovery, including explicit diagnostics, not runtime
acceptance or interpretation of all scripted grants.
See [the rescan evidence](installation-scan-20260924.md) for byte-level details.

All results were saved outside the installation. This check launched no game or
installer and changed no installed resource or save. A successful parse remains
discovery evidence, not proof of safe general runtime suppression or lootability.
