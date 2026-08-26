# Mode 1 manifest-driven EEex delivery redesign

Date: 2026-08-27

Status: approved design; implementation pending

Scope: Item Randomiser components 1100 and 1200 only

## Context and evidence

This design follows the completed live repair recorded in `docs/handover.md` and the fresh,
isolated EET install test of commit `b600e94`.

The install test proved that the original `flSqueaked` collision fix lets all 293 BG2 token
globals receive positive assignments. It also exposed a separate completion-sentinel defect:
`lib/ssl.tpa` retains `removed_tier_array` between the EET BG1 and BG2 passes, so the BG2
sentinel waits on BG1 tier-completion globals that can never be set in the BG2 campaign.
That discrepancy was diagnosed before any redesign code was changed.

The existing delivery actors have seven independently verified failure classes, including
missing slot values, single-use slots, cross-area targets, post-mortem delivery, and marking a
token delivered without proving that the item became lootable. The redesign must eliminate
those structural failure modes without changing how Mode 1 chooses assignments.

## Immutable safety boundaries

- The live game directory is read-only. No install, uninstall, override write, or WeiDU run is
  permitted there.
- Live saves are never edited directly.
- `research/2026-08-live-audit/handover_v4.lua` is historical evidence and must never be run
  again.
- The persisted repair save and its source save remain untouched.
- Tests and diagnostics must not reveal concrete item-to-location pairings.
- A successful EEex queue call is not delivery evidence. Runtime tests must observe the item in
  the world and, where persistence is claimed, in a separately written test save.
- Only one remote-console client may use an isolated game's IPC channel at a time.

## Goals

1. Preserve the current Mode 1 contract: SSL assigns each compact token global a numeric slot
   value at new-game start, and `-1` means delivered.
2. Replace ordinary one-shot BCS delivery actors with one guarded EEex controller on supported
   EE games.
3. Generate a normalized install-time manifest from the final filtered Mode 1 catalog.
4. Verify an actual destination item-count increase, with the expected charges, before setting
   a token to `-1`.
5. Make items, source locators, locations, target groups, capacities, and fallbacks explicitly
   extensible by other mods.
6. Preserve stable token and location identities across compatible reinstalls and existing
   saves.
7. Support several assignment slots at one physical endpoint and deterministic overflow
   handling without silently losing items.
8. Keep normal installation and runtime diagnostics aggregate and spoiler-free.

## Non-goals

- Do not redesign Mode 2. Components 1300 and 1400 remain install-time randomisation and must
  retain their current behavior.
- Do not replace SSL assignment in this phase. Reproducible player-entered seeds, pools larger
  than the compact-global contract allows, and hot-added runtime catalogs belong to a later
  assignment redesign.
- Do not automatically discover and randomise arbitrary mod items.
- Do not automatically delete an item found at an ambiguous, unregistered location.
- Do not convert the three special script transformations in `lib/delivery.tpa` yet. They stay
  on their proven legacy paths until each has a dedicated semantic model and test fixture.
- Do not silently drop classic BG2, Tutu, or BGT support.

## Mode boundary and backend selection

The redesign is strictly behind `weidu_action = 0`:

- Components 1100/1200 load the Mode 1 catalog and choose a delivery backend.
- Components 1300/1400 continue to load `lib/weidu_action.tpa` and the Mode 2 supplemental
  item/location lists exactly as before.
- `lib/weidu_action.tpa` is not refactored through the new manifest.
- Mode 2 does not install the EEex bootstrap, runtime controller, manifest, backend marker, or
  registry.

The Mode 1 backend is selected once and recorded:

- `eeex-manifest-v1`: EE game with the required EEex capabilities. The first implementation
  conservatively requires the active LuaJIT loader setting used by the isolated test and Remote
  Console, detected from an anchored active INI line rather than an EEex component number.
- `legacy-bcs-v1`: classic game, or an EE game without the required capabilities.

A preserve-compatible reinstall keeps the recorded backend. An explicit migration path may
move an old unmarked Mode 1 installation to `eeex-manifest-v1`; it must preserve the existing
`fl*` globals and neutralize legacy delivery scripts rather than editing saves.

The exact installer seam is:

1. apply Mode 1 source/catalog extensions before the stage-1 item/source filter;
2. select/record the backend in components 1100/1200 before `fixes.tpa`;
3. after `fixes.tpa`, run current `delivery.tpa` for the legacy backend, or run manifest
   generation plus ordinary-actor neutralization for the EEex backend;
4. run an extracted special-BCS include for the three retained transformations;
5. run `ssl.tpa` for both backends.

Changes inside shared `arrays.tpa`, `delete.tpa`, and `fixes.tpa` are guarded by Mode 1/backend
state because Mode 2 includes those files too. The implicit random location duplication in
`fixes.tpa` is disabled only for the EEex backend; explicit endpoint capacity replaces it.

## Three-layer model

### 1. Catalog

The catalog describes what may participate, where it may be removed from, and which delivery
endpoints are legal. It contains no random assignment outcome.

The catalog is built in two phases:

1. Resolve and validate all registrations without mutating game resources.
2. Apply the exact validated removal plan and retain only entries whose registered source was
   actually removed.

If anything changes between the two phases, installation fails and WeiDU rolls the component
back. This preserves the current useful rule that an item only gets a token after a real source
instance was removed.

Source `REPLACE` operations must run before the current stage-1 item/source filter. The present
`delete.tpa` interleaves discovery, mutation, compact-unit creation, charge capture, and script
bookkeeping, so the two-phase contract requires a real plan/apply refactor; a manifest include
after deletion is not sufficient.

### 2. Assignment

SSL remains responsible for new-game randomisation. It receives the validated compact token
set and stable numeric slot set for each tier. Its public save-game state remains:

- `0`: not assigned in this playthrough;
- positive numeric value: pending at that slot;
- `-1`: delivered and verified.

The BG1 and BG2 SSL passes rebuild all pass-local tier state. A campaign sentinel may depend
only on tier-completion globals emitted for that same campaign.

### 3. Delivery

The EEex controller joins a pending token row to a slot row only at runtime. It resolves the
current area object, chooses the primary or permitted fallback endpoint, performs a guarded
delivery transaction, verifies the resulting item instance, and only then writes `-1`.

Catalog files and normal logs never contain a precomputed item-to-location assignment map.

## Stable identity and compact registries

Every public row has a provider-qualified stable identifier:

- item ID: `provider:item-id`;
- source ID: `provider:source-id`;
- location ID: `provider:location-id`;
- endpoint ID: `provider:endpoint-id`;
- group ID: `provider:group-id`.

Stable IDs are distinct from engine-facing compact values:

- a randomizable-unit registry maps `(campaign, tier, stable unit ID)` to the compact token used by
  `fl<tier>t<token>`;
- a location registry maps `(campaign, tier, stable location ID)` to the positive numeric slot
  value.

Built-in declaration IDs are derived from campaign plus the legacy tier/declaration identity,
not from filtered array order. A canonical fresh-install registry and an old-save migration
snapshot are separate artifacts: an unmarked legacy install must snapshot its exact effective
per-tier ordering before extensions add or sort rows. The existing preserve file records removed
units but cannot prove historical slot meanings by itself.

New compact values are allocated monotonically and persisted in the registry. Disabled or
removed entries become tombstones; their stable IDs, tokens, and slot values are never reused.
Legacy `x*`/`y*` extra tokens are separate randomizable units even when they reference the same
logical item resref.

The registry is deliberately separate from the generated runtime manifest. It is versioned and
contains identities plus compact values but no joined assignment outcome. Its survival across a
compatible WeiDU uninstall/reinstall must be proven in a synthetic test; a component-owned
override file must not be assumed to survive the uninstall half. A fingerprint covers the
canonical registry and catalog content.

Conflicting compact values, an attempted tombstone reuse, a legacy mapping change, or two
providers claiming the same stable ID is a fatal install-time error.

## Public extension operations

The public catalog API supports complete-row operations for logical items/randomizable units,
sources, assignment slots/endpoints, and group membership:

- `ADD`: introduce a new provider-owned stable ID; fail if it already exists.
- `REPLACE`: replace one complete row owned or explicitly targeted by the provider; fail when
  the expected previous row/fingerprint does not match.
- `DISABLE`: tombstone a row while preserving its stable and compact identities.

There is no implicit last-writer-wins behavior. Conflicting replacements fail with opaque IDs
and provider names so install order cannot silently decide game content.

Catalog changes require a Randomiser reinstall. Ordinary Mode 1 rerandomisation still happens
at new-game start and does not require a reinstall.

## Item and source schema

A normalized logical item row contains at least:

```text
provider, item_id, tier, resref, variant_policy,
source_set_id, enabled
```

A normalized randomizable-unit row contains at least:

```text
provider, unit_id, item_id, tier, compact_token,
occurrence, charge1, charge2, charge3, enabled
```

A normalized source row contains at least:

```text
provider, source_id, item_id, source_kind, resource,
object_or_slot, expected_quantity, multiplicity_policy,
charge_policy, priority, enabled
```

Supported source kinds are explicit rather than inferred from punctuation:

- area container/item table;
- creature inventory;
- store inventory;
- script/dialogue transformation handled by a named legacy adapter;
- deliberate virtual/synthetic source.

The default charge policy captures the charges of the removed instance. Synthetic items must
declare charges or a validated ITM-derived policy explicitly.

Each proven removed instance emits or preserves one unit row and one compact token/global. The
model does not collapse several independently assigned legacy units into `quantity > 1`.

If the expected item resource or declared environmental source is absent, the item is safely
excluded and reported only in aggregate. If the item was moved by another mod, that mod or a
compatibility fragment must `REPLACE` the source locator. An optional scanner may report an
opaque candidate count, but it must never auto-remove ambiguous duplicates or quest items.

## Location, endpoint, capacity, and fallback schema

A stable assignment slot is separate from its physical endpoint.

A normalized slot row contains at least:

```text
provider, location_id, tier, slot_value, endpoint_id,
weight, progression_band, enabled
```

A normalized endpoint row contains at least:

```text
provider, endpoint_id, area, target_kind, target_identity,
x, y, capacity, static_policy, fallback_chain, enabled
```

Target kinds include living creature, named container, ground coordinate, and registered group
adapter. Every non-ground endpoint has a same-area ground fallback with authored or validated
coordinates. Dynamic targets require an authored fallback; installation never guesses a
progression-breaking area.

The legacy location tables contain no coordinates, and the old invisible actor position
`[1,1]` is not a valid fallback anchor. Phase 1 must author fallback coordinates or derive and
validate them from the effective ARE target before any destructive removal.

Several stable slot rows may reference one endpoint. This is the formal mechanism for allowing
several randomised items at one physical location. Capacity is the maximum number of Randomiser
deliveries the endpoint promises to accept, not a guess based on a currently empty inventory.

Slot expansion is fair and deterministic: allocate one slot to each eligible endpoint in
stable order before allocating any endpoint's second slot, then continue in rounds up to each
capacity. Legacy slot values remain fixed; additional slots are appended without renumbering
them.

Tombstones may leave gaps in positive slot values. SSL therefore uses the maximum active stable
value as `MaxRandom`, not the number of active rows. Gaps are never assigned; tests must bound
the extra no-response ticks so a sparse legacy registry cannot stall new-game assignment.

When demand exceeds primary capacity:

1. use explicitly registered capacity in the same tier/progression band;
2. use an explicitly registered same-progression overflow endpoint;
3. use party-foot delivery only when a catalog policy explicitly permits it as the last resort.

In no-items-lost mode, insufficient safe capacity is a fatal validation error before removal.
In some-items-lost mode, structural capacity failure is still an error; loss may occur only
through that mode's intentional assignment probability.

## Group and variant overrides

Current `.flg` groups may select a different creature for different items. The normalized
manifest represents this as a sparse override relation:

```text
(stable item ID, stable location ID) -> endpoint override
```

The base slot remains stable, while only the target endpoint varies. The override is validated
for tier/progression compatibility, target existence policy, capacity, and same-area fallback.
It never changes the compact slot value stored in an existing save.

Random book/variant behavior is represented by an explicit `variant_policy`, not by duplicating
nearly identical delivery blocks.

## Generated runtime manifest

The EEex backend installs three engine-loadable Lua resources, each with an eight-character
resref-safe basename, plus one static poll menu:

- `M_FLDLV.lua`: minimal guarded bootstrap;
- `FLDLVCor.lua`: controller and dependency-injected runtime logic;
- `FLDLVMan.lua`: generated data only.
- `FLDLV.menu`: recurring world-screen poll driver.

Generated data is Lua rather than runtime JSON so the controller does not depend on `io` for
manifest loading. LuaJIT is therefore a conservative first-backend policy and a requirement of
the current in-game test/diagnostic path, not a requirement of the generated table format.

`FLDLVMan.lua` exposes separate relations:

```text
schema, fingerprint, backend,
tokens_by_global, slots_by_tier_value,
endpoints_by_id, sparse_overrides
```

Token rows contain the global, stable unit, tier, compact token, item resref, expected charges,
and variant policy. Slot rows contain only stable slot/endpoint metadata. No static joined
item/location outcome is emitted because Mode 1 chooses it in game.

The generator produces a canonical ordering and deterministic fingerprint. Manifest schema or
fingerprint incompatibility disables delivery and reports a single aggregate error; it never
marks tokens delivered.

## EEex controller lifecycle

The bootstrap uses a process-lifetime root namespace and append-only listener guard:

```lua
FLDLV = FLDLV or {}
```

It registers each EEex listener once and uses a thin trampoline that resolves the current
handler at event time. Hot reload may replace controller functions without stacking callbacks
or retaining stale code.

The controller:

- capability-checks every required EEex symbol;
- contains errors once at the callback boundary while leaving internal functions unwrapped for
  tests;
- never displays text during `M_*.lua` loading;
- never caches sprite/container userdata across ticks, area transitions, or save loads;
- stores stable IDs, resrefs, and area codes across polls; numeric object IDs are ephemeral and
  may be retained only within the current safe poll/transaction step, then re-resolved by stable
  identity before another native call;
- compares engine objects by stable object ID, never Lua userdata equality;
- enumerates area objects through verified object-ID lists and never passes a
  `CGameObjectType` integer to a `CAIObjectType*` API;
- uses a guarded `WORLD_ACTIONBAR` render/menu poll as the recurring driver;
- may use `EEex_Sprite_AddLoadedListener` only to dirty/debounce a future poll, because it is a
  per-sprite post-unmarshal/spawn signal rather than an area-loaded or recurring-tick hook;
- clears all ephemeral object/queue state from the GameState-destroyed listener before a load or
  session teardown.

These lifecycle seams are present in the installed EEex v0.11.0-alpha and the installed Remote
Console poll pattern. EEex v0.11 has no dedicated area-loaded Lua listener and no direct
item-construction helper. Delivery transports and item-iteration fields still require isolated
runtime probes before production use; unproven native calls are not introduced directly in a
running session.

## Delivery transaction

Each pending token is processed independently, even when several tokens share a slot or
endpoint.

1. Read the current global. Stop unless it is a positive value represented by the manifest.
2. Resolve the slot and any sparse override for the current token.
3. Resolve a fresh primary target. A living creature is eligible; a corpse is not.
4. If the target is missing, dead, full, or remains unresolved past its settling policy, select
   the next permitted same-area fallback.
5. Count matching item instances with expected charges at the chosen endpoint.
6. Persist a compact transaction record in game GLOBAL integers containing the unit/token,
   endpoint selector, baseline count, expected quantity/charges, manifest fingerprint pieces,
   and phase. This avoids making recovery depend on one sprite's UDAux; boolean-like fields use
   `0`/`1` only.
7. Perform the delivery through a verified queued BCS transport. A guarded Lua action may record
   that queue processing advanced, but neither submission nor that acknowledgement proves that
   an item was created.
8. On a later safe tick, re-resolve the endpoint and require its matching count to be at least
   `baseline + expected quantity` with matching charges.
9. Only after that observation, set the assignment global to `-1` and clear the transaction.

An observed pre-existing copy cannot satisfy the transaction because verification is relative
to the persisted baseline. Post-count observation is the sole commit evidence. A
queued-but-unobserved transaction is never blindly replayed in the same GameState. After the
verified GameState-destroyed boundary has discarded the prior world and queues, recovery first
re-observes the endpoint; if the expected increase is still absent, policy may return the
transaction to a retryable state. Without that boundary or another proven queue-introspection
mechanism, the controller remains conservatively pending rather than risking a duplicate.

The selected endpoint is locked when the transaction is prepared. If that endpoint becomes
unobservable after queueing, the controller must not switch to a fallback: the original action
may already have created the item. It quarantines until the locked endpoint can be checked or a
GameState boundary proves the old queue is gone.

If the effective ITM disappears after installation, or the endpoint cannot be resolved safely,
the token is quarantined: leave its positive global unchanged, retain diagnostics, and try only
a catalog-authorized fallback. Structural failure never becomes `-1`.

## Legacy actor neutralization and special transformations

For `eeex-manifest-v1`, ordinary delivery actors are no longer placed in new ARE resources.
Existing saves may already contain them, so their shared generated scripts are replaced with a
stub that destroys only the inert actor and performs no token write under the EEex backend. This
prevents BCS and Lua from racing without removing or editing saved actors. Assignment actors
named `fltier*` are a separate family and must never be neutralized.

The three special script transformations currently selected by special BCS rows continue to be
compiled and patched through the legacy path. They are excluded from the ordinary EEex manifest
until each has a dedicated adapter and end-to-end fixture.

For `legacy-bcs-v1`, current delivery actor generation remains available. Campaign-local SSL
state and validation improvements may be shared, but no EEex files are installed.

## Existing-save policy

- Existing `-1` globals stay delivered.
- Existing positive globals keep their exact legacy slot meaning.
- Existing `0` globals stay unassigned unless the original SSL flow for that save has not yet
  run.
- Newly registered items affect new games only. They are not injected into an already-started
  Mode 1 save after reinstall.
- Compatible new endpoints may serve as fallback for an old pending assignment, but old slot
  values are never remapped.
- A pending old token whose item row or ITM disappeared is quarantined, not synthesized and not
  marked complete.
- Backend, schema, registry, and manifest fingerprints are recorded with the save-facing
  transaction state so an incompatible reinstall fails closed.

This policy permits delivery repairs and safer fallback locations in existing saves while
keeping their original randomised outcome frozen.

## Diagnostics and spoiler policy

Normal installation/runtime diagnostics may report:

- schema/backend/fingerprint status;
- aggregate counts of enabled, disabled, tombstoned, assigned, delivered, pending, quarantined,
  fallback, and failed rows;
- opaque provider-qualified IDs for validation conflicts;
- a hash of assignment state for regression comparisons.

They must not print or write a joined item-to-location table. A detailed mapping dump is an
explicit opt-in developer action, disabled by default and never used while assisting the blind
playthrough.

## Validation policy

Fatal before applying the removal plan:

- malformed or duplicate stable IDs;
- compact token/global or slot collisions;
- tombstone reuse or legacy ordinal drift;
- conflicting `REPLACE` operations;
- invalid tier/progression relationships;
- missing or cross-area fallback definitions;
- ambiguous capacity or insufficient no-loss capacity;
- generated resref/global length violations;
- manifest schema/fingerprint inconsistency.

Safely exclude before assignment:

- absent ITM;
- absent declared environmental resource;
- registered source instance not found at the expected multiplicity;
- a provider-disabled row.

Quarantine at runtime without writing `-1`:

- positive global with no token row;
- positive slot value with no compatible slot row;
- effective ITM missing after install;
- no authorized endpoint can be observed safely;
- delivery action acknowledged but the expected count increase is absent.

## Test strategy

Implementation follows strict RED-GREEN-REFACTOR sequencing.

1. **Campaign-local SSL test**: install/compile a two-pass EET fixture, decompile the BG2
   sentinel script, and first demonstrate that it incorrectly depends on BG1 tier globals.
   The fix must make each campaign's dependency set exact and disjoint.
2. **Manifest unit tests**: stable registry bootstrap, monotonic allocation, tombstones,
   `ADD/REPLACE/DISABLE` conflicts, source planning, capacity rounds, sparse group overrides,
   fingerprints, and spoiler-free diagnostics.
3. **Removal-plan tests**: missing/moved/multiple sources, exact charges, rollback on validation
   failure, and token creation only after a proven removal.
4. **Lua unit tests**: use dependency-injected fake globals/areas/targets/actions to cover living
   target, corpse fallback, missing target, full endpoint, multiple tokens at one endpoint,
   queue acknowledgement, count-relative verification, quarantine, and save/load recovery.
5. **Static gates**: WeiDU parse-checks; Lua 5.3 syntax/unit tests; generated engine basenames at
   most eight characters. No standalone LuaJIT executable is present, so LuaJIT 5.1 coverage
   runs inside the isolated game through the serialized Remote Console.
6. **Mode matrix**: components 1100 and 1200 generate the manifest backend only when capability
   checks pass; components 1300 and 1400 produce no EEex assets and retain byte-equivalent Mode 2
   behavior on separate fresh fixed-seed fixtures; classic Mode 1 retains the legacy backend.
7. **Synthetic/fresh-install integration**: verify install, reinstall, uninstall restoration,
   registry preservation, no legacy actor insertion for the EEex backend, no-op legacy scripts,
   and the three special transformations.
8. **Isolated EET runtime**: with the game and loader closed during WeiDU operations, reinstall
   into the existing disposable EET tree, start a fresh game, and verify aggregate assignment and
   sentinel state. Acceptance allows positive plus already-delivered `-1` globals to equal the
   expected set, with zero unassigned. Exercise living, dead/missing, multi-item, fallback, and
   save/reload delivery scenarios through spoiler-free probes. Forced dialogue must be closed for
   Remote Console polling, and the simulation must be unpaused for queued actions to execute.
9. **Persistence evidence**: a runtime queue/ack is insufficient. For every claimed delivery,
   inspect the destination/world state; for save survival, write a distinct isolated test save
   through the game and inspect its structures read-only.
10. **Live-boundary audit**: compare the live install/profile fingerprints taken before testing;
    they must remain unchanged.

## Implementation phases

### Phase 1: repaired Mode 1 core

- campaign-local SSL state and sentinel test;
- normalized built-in catalog and frozen legacy registries;
- explicit randomizable-unit rows for legacy extra-token multiplicity;
- a per-install migration snapshot captured before extensions change ordering;
- pre-removal validation and actual-removal manifest rows;
- EEex backend, transaction verification, same-area fallback, and legacy actor neutralization;
- explicit endpoint capacity and several slots per endpoint;
- existing-save migration rules;
- classic legacy backend seam;
- full isolated EET install/runtime verification.

### Phase 2: documented third-party extension surface

- public extension functions/fragments for all four operations and row families;
- provider conflict/fingerprint fixtures;
- compatibility examples that do not expose concrete base-game assignments;
- optional read-only candidate scanner with aggregate output.

The internal Phase 1 catalog uses the same public row model so Phase 2 does not require another
runtime redesign.

### Later assignment redesign

Replace SSL only if the project adopts reproducible user seed codes, pools beyond compact-global
limits, or runtime catalog changes. That work must include an explicit migration design and must
not be folded into this delivery repair.

## Approved decisions

- Mode 2 remains separate and unchanged.
- Stable provider-qualified identities are mandatory.
- Compact tokens/slot values are persistent and never reused.
- Other mods integrate through explicit registrations, not discovery-based deletion.
- Several slots may share one physical endpoint.
- Capacity is filled evenly before second/third assignments.
- Structural overflow is a validation problem, not intentional item loss.
- Existing saves freeze assignments; new items are new-game-only.
- Existing pending tokens may benefit from compatible fallback locations.
- Delivery is complete only after the item instance is observed with the expected charges.
- Normal diagnostics remain spoiler-free.
