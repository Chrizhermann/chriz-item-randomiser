# Mode 1 Manifest-Driven EEex Delivery Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:executing-plans to implement this plan task-by-task.

**Goal:** Replace ordinary Mode 1 delivery actors with a validated, extensible manifest and an EEex controller that proves item creation before marking a token delivered, while preserving existing saves, classic-game support, and Mode 2 behavior.

**Architecture:** Keep SSL and the existing `fl*` assignment-global contract. Normalize the install-time catalog into stable logical-item, randomizable-unit, source, slot, endpoint, and sparse-override relations; validate and plan removals before mutation; generate deterministic Lua data; and deliver through a guarded render-driven EEex controller with GLOBAL-backed transactions. Select the backend only for components 1100/1200, retain the legacy backend for unsupported games, and leave components 1300/1400 on `weidu_action.tpa`.

**Tech Stack:** WeiDU 24900, SSL/BCS, PowerShell 7, Python 3.11 standard library, Lua 5.3.3 for pure tests, EEex v0.11.0-alpha/LuaJIT 5.1 for isolated runtime tests, EEex Remote Console v0.2.0.

**Design:** `docs/plans/2026-08-27-mode1-eeex-delivery-design.md`

**Required skills during execution:** @test-driven-development, @systematic-debugging, @bg-modding, @eeex-api, @weidu-modding, @verification-before-completion, @requesting-code-review

---

## Fixed test boundaries

- Repository: `C:\Users\chris\.codex\worktrees\d9c1\bgee-itemrandomiser-fix`
- Disposable EET game: `C:\Users\chris\Games\EET-IR-Test-b600e94\bg2`
- Baseline decompiles: `C:\Users\chris\Games\EET-IR-Test-b600e94\decompile-all-b600e94`
- WeiDU 24900: `C:\Users\chris\Games\EET-IR-Test-b600e94\bg2\EET\bin\win32\x86_64\weidu.exe`
- Lua 5.3.3: `C:\Users\chris\Games\EET-IR-Test-b600e94\bg2\EET\bin\win32\x86_64\lua.exe`
- Remote Console tools: `C:\src\private\eeex-remote-console\tools`
- Isolated profile: `C:\Users\chris\OneDrive\Documents\Baldur's Gate - Enhanced Edition Trilogy - IR Test b600e94`

Never run any command in `C:\Games\Baldur's Gate II Enhanced Edition modded`, never write the
standard EET profile, never edit any save directly, and never run `handover_v4.lua`.

### Task 1: Reproduce and fix campaign-local SSL completion

**Files:**

- Create: `tests/Test-SentinelDependencies.ps1`
- Modify: `lib/ssl.tpa:22-26`

**Step 1: Write the dependency-set test**

Implement a PowerShell test that reads decompiled `fltier*.baf` files, groups scripts by the
`flSqueaked*` global they reference, derives the expected `fl*tDone` set from the tier scripts in
that group, extracts the dependency set from the block that writes the sentinel to `1`, and
prints only campaign/sentinel aggregate counts.

The result object must contain `sentinel`, `expectedCount`, `actualCount`, `extraCount`,
`missingCount`, and `passed`; it must not print item or location rows.

**Step 2: Run the test against the b600e94 baseline and record RED**

Run:

```powershell
pwsh -NoProfile -File .\tests\Test-SentinelDependencies.ps1 `
  -BafDirectory 'C:\Users\chris\Games\EET-IR-Test-b600e94\decompile-all-b600e94'
```

Expected: nonzero exit with the aggregate line showing one BG2/SoA sentinel has 18 expected
campaign tiers, 31 actual dependencies, and 13 extras. The ToB sentinel remains green.

**Step 3: Apply the minimal production fix**

At the start of every `OUTER_FOR (sslpass...)` iteration, before scanning
`removed_item_array`, add:

```weidu
ACTION_CLEAR_ARRAY removed_tier_array
```

Do not change `sslvar`, tier ordering, RNG, or Mode 2 files.

**Step 4: Parse-check the changed TPA**

Run:

```powershell
& 'C:\Users\chris\Games\EET-IR-Test-b600e94\bg2\EET\bin\win32\x86_64\weidu.exe' `
  --nogame --parse-check TPA .\lib\ssl.tpa
```

Expected: exit 0.

**Step 5: Reinstall only in the disposable EET tree**

First prove `Baldur.exe` and `InfinityLoader.exe` are not running from the disposable path.
Copy only the changed `lib\ssl.tpa` into the disposable game's `randomiser\lib`, then run from
the disposable game root:

```powershell
& '.\setup-randomiser.exe' `
  --force-uninstall-list 1100 --force-install-list 1100 `
  --language 0 --use-lang en_US --no-exit-pause
```

Expected: component 1100 and every temporarily popped later component reinstall successfully;
no TLK permission error appears at the end.

**Step 6: Decompile fresh tier scripts and verify GREEN**

Decompile `override\fltier*.bcs` into a new temporary directory with WeiDU 24900, then run the
same dependency test.

Expected: every campaign has `extraCount=0`, `missingCount=0`, and the command exits 0.

**Step 7: Commit**

```powershell
git add tests/Test-SentinelDependencies.ps1 lib/ssl.tpa
git commit -m 'fix: isolate SSL tier completion by campaign'
```

### Task 2: Establish the hermetic unit runner and Mode boundary guard

**Files:**

- Create: `tests/run-unit.ps1`
- Create: `tests/Test-ModeBoundary.ps1`
- Create: `tests/README.md`

**Step 1: Write the failing Mode boundary assertions**

Assert that:

- components 1100/1200 set `weidu_action = 0` and components 1300/1400 set it to `1`;
- only 1300/1400 include `lib/weidu_action.tpa`;
- `lib/weidu_action.tpa`, `lists/items/mode2`, and `lists/locations/mode2` have no diff from
  `1efc64a`;
- no planned EEex runtime filename appears in either Mode 2 component body.

Run:

```powershell
pwsh -NoProfile -File .\tests\Test-ModeBoundary.ps1
```

Expected before the backend branch exists: fail on the missing explicit Mode 1 branch checks,
while all Mode 2 immutability checks pass.

**Step 2: Add the common runner**

`tests/run-unit.ps1` must locate the configured WeiDU/Lua executables, run PowerShell tests,
WeiDU parse-checks, WeiDU harnesses, and `tests\lua\run.lua` when present, and stop on the first
failure. It must use disposable temporary directories and must never infer a live game path.

**Step 3: Document safe invocation**

Document the disposable paths, the no-live-install rule, the lack of a standalone LuaJIT
executable, and the fact that LuaJIT coverage runs only inside the isolated game.

**Step 4: Run the runner**

Run:

```powershell
pwsh -NoProfile -File .\tests\run-unit.ps1
```

Expected: sentinel test and existing static checks pass; the explicitly future backend assertion
remains RED and is named as such rather than suppressed.

**Step 5: Commit**

```powershell
git add tests/run-unit.ps1 tests/Test-ModeBoundary.ps1 tests/README.md
git commit -m 'test: add hermetic Mode boundary runner'
```

### Task 3: Add the normalized public catalog and conflict semantics

**Files:**

- Create: `lib/catalog.tpa`
- Create: `tests/weidu/catalog-harness.tp2`
- Create: `tests/Test-Catalog.ps1`
- Create: `tests/fixtures/catalog/base.2da`
- Create: `tests/fixtures/catalog/extensions.2da`

**Step 1: Write failing catalog tests**

Cover provider-qualified stable-ID grammar, complete-row `ADD`, `REPLACE`, and `DISABLE`,
expected-fingerprint matching, duplicate/conflicting ownership, disabled-row tombstones, and
canonical output ordering for these row kinds:

- logical item;
- randomizable unit;
- source locator;
- assignment slot;
- endpoint;
- group membership and sparse override.

Expected RED: `lib/catalog.tpa` and its public functions do not exist.

**Step 2: Implement stable-ID validation**

Accept only lowercase provider/ID segments made from `[a-z0-9][a-z0-9._-]*`, require exactly
one provider separator, reject empty segments, and bound serialized IDs so generated globals and
diagnostics stay within their explicit limits.

**Step 3: Implement one generic complete-row operation**

Create `flir_catalog_apply` with explicit `kind`, `operation`, `provider`, `id`,
`expected_fingerprint`, and kind-specific fields. Store each field in parallel associative arrays
keyed by `(kind, provider:id)`. Add thin public wrappers for each row kind so external mods do not
construct internal array names.

`ADD` fails if present, `REPLACE` fails unless the expected row fingerprint matches, and
`DISABLE` preserves the row identity while setting numeric `enabled=0`.

**Step 4: Implement deterministic row fingerprints**

Canonicalize fields in schema order, escape delimiters, and compute a deterministic numeric
fingerprint using WeiDU integer operations. Tests must prove identical input is stable and a
one-field change differs.

**Step 5: Run the catalog harness**

Run:

```powershell
pwsh -NoProfile -File .\tests\Test-Catalog.ps1
```

Expected: all positive and conflict cases pass; normal output contains provider/opaque IDs but
no joined item/location data.

**Step 6: Commit**

```powershell
git add lib/catalog.tpa tests/weidu/catalog-harness.tp2 tests/Test-Catalog.ps1 tests/fixtures/catalog
git commit -m 'feat: add extensible normalized catalog'
```

### Task 4: Move extension source overrides before filtering and split removal plan/apply

**Files:**

- Create: `lib/removal_plan.tpa`
- Create: `tests/weidu/removal-harness.tp2`
- Create: `tests/Test-RemovalPlan.ps1`
- Create: `tests/fixtures/sources/`
- Modify: `lib/arrays.tpa`
- Modify: `lib/delete.tpa`
- Modify: `randomiser.tp2:124-177`

**Step 1: Write failing source-plan fixtures**

Build minimal CRE, ARE/container, STO, and declared virtual-source fixtures. Cover exact
multiplicity, moved-source `REPLACE`, ambiguous duplicates, absent ITM/source, captured charge
triples, extra `x*`/`y*` units, and a validation failure that leaves fixture bytes unchanged.

Expected RED: current filtering happens before an extension can replace a source, and current
deletion mutates while discovering.

**Step 2: Add the pre-filter extension hook**

Inside the Mode 1 `arrays.tpa` path, load base stage-1 declarations, apply catalog extensions,
then run the current `create_item_array#stage1#filter` and finalizers. Guard the hook with
`weidu_action = 0`; Mode 2 follows its original path byte-for-byte.

**Step 3: Implement a read-only removal plan**

Refactor current source-kind discovery into functions that emit exact planned operations and
randomizable-unit rows without writing a game resource. Capture resource preconditions,
instance/slot identity, occurrence, flags, and all three charges.

Absent/unmatched registered sources safely disable the unit. Ambiguous multiplicity and a
changed precondition are fatal.

**Step 4: Validate before mutation**

Run catalog, registry, endpoint, and capacity validation against the planned units. Ensure a
failure occurs before the apply phase and that WeiDU rollback is not being used to mask an
avoidable preflight failure.

**Step 5: Apply exactly the plan**

Move the current mutation code behind the validated plan. Before each write, re-check its
captured precondition; abort on drift. Emit `removed_item_array`, `charge_array`,
`extra_tokens`, and `bcs_source_array` from applied operations so downstream legacy behavior is
preserved.

**Step 6: Run removal tests twice**

Run the same plan/apply fixtures twice and assert deterministic output plus byte-exact
idempotency/rollback where applicable.

```powershell
pwsh -NoProfile -File .\tests\Test-RemovalPlan.ps1
```

Expected: all tests pass and each compact token corresponds to one actually removed unit.

**Step 7: Commit**

```powershell
git add lib/removal_plan.tpa lib/arrays.tpa lib/delete.tpa randomiser.tp2 tests/weidu/removal-harness.tp2 tests/Test-RemovalPlan.ps1 tests/fixtures/sources
git commit -m 'refactor: validate item removals before mutation'
```

### Task 5: Add persistent compact registries and legacy migration snapshots

**Files:**

- Create: `lib/registry.tpa`
- Create: `tests/weidu/registry-harness.tp2`
- Create: `tests/Test-Registry.ps1`
- Modify: `lib/random_seed.tpa`

**Step 1: Write RED tests for persistence semantics**

In a synthetic game, install a registry fixture, reinstall the owning component in one WeiDU
run, and uninstall it. Prove what `COPY +` and `COPY_EXISTING +` do to a state file before using
the existing seed-file pattern. The test must fail if the registry disappears during the
uninstall half or is restored to stale contents.

**Step 2: Define registry schema**

Use one preserved `fl#irreg.2da` state resource with schema, backend, catalog fingerprint, and
rows for `(campaign, tier, kind, stable ID, compact value, enabled)`. Keep tombstones with
`enabled=0`. The file contains no item/slot join.

**Step 3: Bootstrap legacy and fresh mappings**

For a fresh install, allocate unit tokens and slot values monotonically from the canonical
effective catalog. For an old unmarked preserve install, snapshot the exact filtered per-tier
counter ordering before extensions can add/reorder rows. Never treat the raw Location column as
the saved slot value.

Preserve declared extra `x*`/`y*` compact tokens as separate units.

**Step 4: Enforce non-reuse and compatibility**

Reject compact collisions, legacy ordinal drift, tombstone reuse, backend switching during a
preserve reinstall, and incompatible registry schema/fingerprint changes. Newly registered unit
globals remain `0` in started saves and enter only new games.

**Step 5: Run registry tests**

```powershell
pwsh -NoProfile -File .\tests\Test-Registry.ps1
```

Expected: monotonic allocation, tombstones, exact legacy preservation, and reinstall survival
all pass.

**Step 6: Commit**

```powershell
git add lib/registry.tpa lib/random_seed.tpa tests/weidu/registry-harness.tp2 tests/Test-Registry.ps1
git commit -m 'feat: persist stable Mode 1 registries'
```

### Task 6: Model endpoints, capacity, stable slots, and authored fallbacks

**Files:**

- Create: `lib/endpoints.tpa`
- Create: `lists/endpoints/base/bg1.2da`
- Create: `lists/endpoints/base/bg2.2da`
- Create: `lists/endpoints/fallbacks.2da`
- Create: `tests/weidu/endpoints-harness.tp2`
- Create: `tests/Test-Endpoints.ps1`
- Modify: `lib/fixes.tpa:15-44`

**Step 1: Write failing endpoint/capacity tests**

Cover explicit target kinds, same-area fallback, progression bands, fallback cycles,
cross-area rejection, capacity, several slots per endpoint, fair round allocation, tombstone
gaps, and insufficient capacity in both loss modes.

Expected RED: current implicit overflow duplicates random location rows and has no coordinate or
capacity model.

**Step 2: Normalize current location declarations**

Classify ordinary targets as creature, container, or group; route the three special BCS rows to
their legacy adapter. Derive static coordinates from the effective ARE only when the target can
be resolved unambiguously; otherwise require an authored fallback row. Reject `[1,1]` as a
fallback.

**Step 3: Add capacity-round allocation**

Allocate one stable slot per eligible endpoint before any second slot, then repeat up to
capacity. Keep migrated legacy values fixed and append new values. For sparse/tombstoned values,
set SSL `MaxRandom` to the maximum active stable value and keep the active `LocationList`
explicit.

**Step 4: Replace implicit overflow only for EEex**

Guard `fixes.tpa:15-44`: legacy Mode 1 and Mode 2 retain current behavior; the EEex backend uses
the explicit endpoint model. No-loss capacity shortage is fatal before removal; some-loss mode
may lose only through its intentional probability.

**Step 5: Run endpoint tests including sparse-stall bound**

```powershell
pwsh -NoProfile -File .\tests\Test-Endpoints.ps1
```

Expected: every ordinary built-in endpoint has an authorized same-area fallback, capacity is
sufficient, and a long tombstone gap still completes assignment within the test bound.

**Step 6: Commit**

```powershell
git add lib/endpoints.tpa lib/fixes.tpa lists/endpoints tests/weidu/endpoints-harness.tp2 tests/Test-Endpoints.ps1
git commit -m 'feat: add explicit delivery endpoints and capacity'
```

### Task 7: Generate a deterministic, spoiler-safe Lua manifest

**Files:**

- Create: `lib/manifest.tpa`
- Create: `tests/weidu/manifest-harness.tp2`
- Create: `tests/Test-Manifest.ps1`
- Create: `tests/fixtures/manifest/`

**Step 1: Write failing manifest tests**

Require schema/backend/fingerprint fields and separate `tokens_by_global`,
`slots_by_tier_value`, `endpoints_by_id`, and `sparse_overrides` tables. Assert deterministic
ordering, Lua string escaping, eight-character engine basenames, numeric persisted flags, and
absence of a prejoined assignment map.

**Step 2: Implement a real Lua encoder**

Encode strings, numbers, arrays, and maps explicitly. Do not apply broad `EVALUATE_BUFFER` to
generated Lua. Escape backslash, quotes, control characters, and container names safely for Lua
5.1/5.3.

**Step 3: Generate canonical fingerprints**

Hash schema plus canonical registry/catalog relations into numeric words suitable for GLOBAL
storage. Do not put an arbitrary full fingerprint in one 32-character global string.

**Step 4: Generate and load the fixture manifest**

Run the WeiDU harness, then load the result with Lua 5.3:

```powershell
pwsh -NoProfile -File .\tests\Test-Manifest.ps1
```

Expected: two identical runs are byte-identical; the Lua interpreter loads the table; one-field
fixture changes alter the fingerprint.

**Step 5: Commit**

```powershell
git add lib/manifest.tpa tests/weidu/manifest-harness.tp2 tests/Test-Manifest.ps1 tests/fixtures/manifest
git commit -m 'feat: generate deterministic delivery manifest'
```

### Task 8: Add the exact Mode 1 backend seam and retain special BCS transforms

**Files:**

- Create: `lib/delivery_special.tpa`
- Create: `lib/delivery_manifest.tpa`
- Create: `lib/delivery_neutralize.tpa`
- Modify: `lib/delivery.tpa:183-205`
- Modify: `lib/fixes.tpa`
- Modify: `randomiser.tp2:124-177`
- Modify: `tests/Test-ModeBoundary.ps1`

**Step 1: Extend the Mode boundary test and verify RED**

Require backend selection before `fixes.tpa`, the exact post-fixes branch, special-transform
execution for both backends, `ssl.tpa` after delivery, and no EEex path in Mode 2.

Expected: fail until the new includes and component branches exist.

**Step 2: Implement capability/backend selection**

For 1100/1200 only, detect `override/M___EEex.lua`, the required v0.11 API source layout, and an
anchored active `LuaPatchMode=REPLACE_INTERNAL_WITH_EXTERNAL` setting. Record
`eeex-manifest-v1` or `legacy-bcs-v1` in the preserved registry. Do not detect by component
number or an unanchored INI substring.

**Step 3: Extract the three special transformations exactly**

Move the special BCS cases from `delivery.tpa` into `delivery_special.tpa`, preserving their
outer tier/location context and `process_script_for_m1` inputs. Call the extracted include for
both backends. Add decompile/output fixture assertions so no special path disappears.

**Step 4: Branch ordinary delivery**

Legacy backend calls ordinary `delivery.tpa`. EEex backend generates the manifest and does not
insert ordinary actors into ARE resources.

**Step 5: Neutralize historical ordinary actors**

Generate stubs for the union of historical ordinary `fl<tier>t<slot>.bcs` resrefs. Each stub
only destroys its actor and performs no token write. Explicitly exclude `fltier*` assignment
actors and every special target script.

**Step 6: Run unit/Mode boundary tests**

```powershell
pwsh -NoProfile -File .\tests\run-unit.ps1
```

Expected: all tests pass; `git diff --exit-code 1efc64a -- lib/weidu_action.tpa lists/items/mode2 lists/locations/mode2`
is clean.

**Step 7: Commit**

```powershell
git add randomiser.tp2 lib/delivery.tpa lib/delivery_special.tpa lib/delivery_manifest.tpa lib/delivery_neutralize.tpa lib/fixes.tpa tests/Test-ModeBoundary.ps1
git commit -m 'feat: select manifest delivery for Mode 1'
```

### Task 9: Build the pure Lua delivery transaction state machine

**Files:**

- Create: `copy/FLDLVCor.lua`
- Create: `tests/lua/run.lua`
- Create: `tests/lua/fake_engine.lua`
- Create: `tests/lua/test_manifest.lua`
- Create: `tests/lua/test_delivery.lua`
- Create: `tests/lua/test_persistence.lua`

**Step 1: Write RED tests before production Lua**

Use a dependency-injected fake engine. Cover manifest rejection, positive/zero/`-1` globals,
living target, dead/missing fallback, full endpoint, several units sharing one endpoint, sparse
override, exact charge signature, baseline-relative verification, queue acknowledgement without
creation, endpoint locking, quarantine, GameState teardown recovery, and callback-error
deduplication.

Run:

```powershell
& 'C:\Users\chris\Games\EET-IR-Test-b600e94\bg2\EET\bin\win32\x86_64\lua.exe' .\tests\lua\run.lua
```

Expected RED: `FLDLVCor.lua` is missing.

**Step 2: Implement manifest validation and token selection**

Reject schema/backend/fingerprint mismatch. Read each unit global independently; process only a
positive value represented by an active stable slot. Leave `0` and `-1` untouched.

**Step 3: Implement numeric transaction phases**

Use explicit integers such as `NONE=0`, `PREPARED=1`, `QUEUED=2`, `ACKED=3`,
`QUARANTINED=4`. Persist unit, locked endpoint selector, baseline, quantity/signature,
fingerprint words, nonce, and phase through injected GLOBAL accessors.

**Step 4: Implement delivery/verification policy**

Select fallback before queueing and lock it. Queue transport plus an acknowledgement callback.
Submission and ACK must leave the token positive. Only a later exact count delta commits `-1`.
If the locked endpoint becomes unobservable, quarantine; never switch endpoints after queueing.

**Step 5: Implement GameState recovery**

Clear ephemeral userdata/IDs/ACK state on destruction. In a new GameState, observe the locked
endpoint first; only if absent after the old queue is gone may policy prepare a retry. Never
retry a queued transaction in the same GameState.

**Step 6: Run Lua tests GREEN under 5.3**

Expected: all cases pass with no engine globals and no file I/O required.

**Step 7: Commit**

```powershell
git add copy/FLDLVCor.lua tests/lua
git commit -m 'feat: add verified delivery transaction core'
```

### Task 10: Add the EEex v0.11 bootstrap, render poll, and isolated API probes

**Files:**

- Create: `copy/M_FLDLV.lua`
- Create: `copy/FLDLV.menu`
- Create: `tests/runtime/probe-eeex-surface.lua`
- Create: `tests/runtime/probe-transport.lua`
- Create: `tests/runtime/probe-aggregate.lua`
- Modify: `lib/delivery_manifest.tpa`

**Step 1: Write runtime probes before the adapter**

Through the serialized Remote Console in the disposable game only, prove:

- `area.m_lVertSort` yields numeric object IDs;
- sprite/container script names can be read and duplicate matches are rejected;
- container and creature item iteration exposes resref plus three use counts;
- container `CreateItem`, creature `GiveItemCreate`, and a safe ground-pile transport behave as
  expected;
- queued action and ACK behavior across area transition and save/load;
- creature-full behavior.

Use synthetic eight-character test resources and aggregate output. Do not call an unverified
native constructor or `*OfTypeInRange` with a `CGameObjectType` integer.

**Step 2: Run the surface probe and require GREEN evidence**

Start only the disposable game via its `InfinityLoader.exe`; verify process executable paths.
Run Remote Console ping/smoke, close any forced dialogue, keep the simulation unpaused for queued
actions, and execute `probe-eeex-surface.lua`.

Expected: structured aggregate result with every safe-surface assertion true. Diagnose a timeout
by checking process liveness before any retry.

**Step 3: Implement the guarded bootstrap**

`M_FLDLV.lua` must:

- return if EEex/capabilities are absent;
- keep `FLDLV = FLDLV or {}` at root;
- load `FLDLVMan` and `FLDLVCor`;
- register append-only listeners once through dynamic trampolines;
- use `EEex_Sprite_AddLoadedListener` only to dirty the next sweep;
- use `EEex_GameState_AddDestroyedListener` to clear ephemeral state;
- hook/re-hook the world UI after UI reload without stacking callbacks;
- never display text at M_ load time.

**Step 4: Implement the invisible world poll**

`FLDLV.menu` calls one contained poll predicate per render. The poll returns immediately when no
visible area exists, debounces full scans, compares the current area identity, and re-resolves
all objects fresh from `m_lVertSort` during a sweep. Object IDs never survive the sweep.

**Step 5: Implement verified queued transports**

Build only the action strings proven by the probes, including exact charge args and a safe Lua
long-bracket ACK payload. The post-count remains the only commit evidence.

**Step 6: Re-run pure tests and syntax gates**

Run `tests\run-unit.ps1` and load all source Lua under Lua 5.3. Expected: pass.

**Step 7: Commit**

```powershell
git add copy/M_FLDLV.lua copy/FLDLV.menu copy/FLDLVCor.lua lib/delivery_manifest.tpa tests/runtime
git commit -m 'feat: drive delivery through guarded EEex polling'
```

### Task 11: Add hermetic install, reinstall, uninstall, and Mode 2 matrices

**Files:**

- Create: `tests/ie_fake_game.py`
- Create: `tests/Test-InstallerMatrix.ps1`
- Create: `tests/Test-Mode2Parity.ps1`
- Create: `tests/fixtures/game/`

**Step 1: Write RED installer-publication tests**

Create a minimal synthetic EE game with KEY/BIFF/TLK markers and explicit publication
allowlists. Assert:

- EEex Mode 1 publishes bootstrap/core/menu/manifest/registry and no ordinary actors;
- legacy Mode 1 publishes no EEex assets and retains ordinary delivery behavior;
- registry survives compatible reinstall;
- uninstall restores override bytes exactly except the explicitly preserved state file;
- failed validation publishes nothing;
- special transformation fixtures remain;
- Mode 2 publishes no EEex delivery asset/registry.

**Step 2: Run the installer matrix and fix every discrepancy**

```powershell
pwsh -NoProfile -File .\tests\Test-InstallerMatrix.ps1
```

Expected: pass with KEY/BIF/TLK hashes unchanged and no unexpected override files.

**Step 3: Build separate fixed-seed Mode 2 baseline/candidate clones**

Do not reuse the Mode 1 disposable tree because cross-mode preservation is intentionally
rejected. Create two fresh isolated EET fixtures, install `b600e94` in one and the candidate in
the other with the same captured seed/options/removed-items inputs, and run components 1300 and
1400 separately.

**Step 4: Compare Mode 2 behavior**

Compare active WeiDU log entries, effective override resource hashes/semantic dumps, and TLK
entry deltas. Ignore timestamps and backup-internal paths only. Assert no EEex asset/backend
state is present.

```powershell
pwsh -NoProfile -File .\tests\Test-Mode2Parity.ps1
```

Expected: baseline and candidate are behaviorally identical for both Mode 2 variants.

**Step 5: Commit**

```powershell
git add tests/ie_fake_game.py tests/Test-InstallerMatrix.ps1 tests/Test-Mode2Parity.ps1 tests/fixtures/game
git commit -m 'test: cover installer and Mode 2 parity matrices'
```

### Task 12: Reinstall and runtime-verify in the disposable EET

**Files:**

- Create: `tests/runtime/setup-runtime-fixture.tp2`
- Create: `tests/runtime/probe-synthetic-delivery.lua`
- Create: `tests/runtime/probe-persistence.lua`
- Create: `tests/inspect-isolated-save.py`
- Create: `docs/test-evidence/2026-08-27-isolated-eet.md`

**Step 1: Capture pre-test boundary evidence**

Verify no game/loader process is running. Re-check the previously recorded live install/profile
hashes read-only and record only pass/fail in the evidence document. Do not enumerate live save
content or expose item/location joins.

**Step 2: Deploy candidate source to the disposable mod folder**

Copy only tracked candidate files needed by the installer into
`C:\Users\chris\Games\EET-IR-Test-b600e94\bg2\randomiser`. Do not mirror-delete the directory
and do not overwrite its backup/state directories.

**Step 3: Reinstall component 1100**

From the disposable game root, run the one-process force-uninstall/force-install command. Verify
the bottom of the DEBUG log, TLK mtime/entry count, generated manifest parse, backend marker,
neutralized ordinary scripts, and retained special transformations.

**Step 4: Launch and prove EEex/Remote Console health**

Launch the disposable `InfinityLoader.exe`, verify both process paths, wait for ready, then run
Remote Console ping plus its full watchdog smoke test. Do not run concurrent clients.

**Step 5: Verify new-game aggregate assignment**

Start a fresh SoA game in the isolated profile. After assignment settles, report only aggregate
counts/hashes. Acceptance:

- every expected token is positive or already verified `-1`;
- zero unassigned tokens remain;
- every campaign-local `tDone` set and sentinel is complete;
- no unexpected global/token exists.

**Step 6: Run synthetic delivery cases**

Use the test-only extension fixture to exercise a living target, dead/missing fallback, two units
sharing one endpoint, exact charged item, full endpoint, and quarantined invalid endpoint.
Assert token stays positive through queue/ACK and becomes `-1` only after later count evidence.

**Step 7: Prove save/load persistence**

Through the game UI, write a distinct isolated test save. Reload it and run the aggregate
persistence probe. Then inspect the save read-only with `tests/inspect-isolated-save.py` for the
synthetic resref/signature, endpoint count, transaction globals, and final token state.

Never edit the save and never use a standard/live save slot.

**Step 8: Close the disposable game and re-check boundaries**

Close both exact disposable processes. Re-run the live install/profile fingerprint comparison;
it must remain unchanged.

**Step 9: Record evidence and commit**

The evidence document records versions, commits, commands, aggregate results, save name/hash,
unverified boundaries, and no concrete base-game assignment pairings.

```powershell
git add tests/runtime/setup-runtime-fixture.tp2 tests/runtime/probe-synthetic-delivery.lua tests/runtime/probe-persistence.lua tests/inspect-isolated-save.py docs/test-evidence/2026-08-27-isolated-eet.md
git commit -m 'test: verify manifest delivery on isolated EET'
```

### Task 13: Document extensions, migration, and spoiler-safe operations

**Files:**

- Create: `docs/extensions.md`
- Modify: `readme-randomiser.html`
- Modify: `docs/handover.md`
- Modify: `docs/plans/2026-08-27-mode1-eeex-delivery-design.md`

**Step 1: Write extension documentation**

Document provider IDs, row schemas, `ADD/REPLACE/DISABLE`, pre-filter source replacement,
logical items versus randomizable units, capacity/fallback requirements, new-game-only item
policy, and complete non-spoiler examples using synthetic resources.

**Step 2: Document backend/migration behavior**

Explain EEex capability detection, legacy fallback, registry/tombstones, frozen existing-save
assignments, ordinary actor neutralization, quarantine, and why Mode 2 is separate.

**Step 3: Update handover status**

Mark the original b600e94 install test complete, record the diagnosed sentinel discrepancy and
fix, point to isolated test evidence, and preserve every Task A hard rule. Do not add concrete
unreached item/location data.

**Step 4: Run docs/static checks**

Run `git diff --check`, the complete unit suite, and search normal log strings/tests for accidental
joined item/location output.

**Step 5: Commit**

```powershell
git add docs/extensions.md readme-randomiser.html docs/handover.md docs/plans/2026-08-27-mode1-eeex-delivery-design.md
git commit -m 'docs: explain extensible Mode 1 delivery'
```

### Task 14: Final verification and independent review

**Files:**

- Review: all changes from `1efc64a`

**Step 1: Run the complete verification suite fresh**

Run unit, WeiDU harness, installer matrix, Mode 2 parity, isolated EET aggregate/runtime, and
live-boundary checks without relying on earlier output.

**Step 2: Inspect repository state**

Run:

```powershell
git status --short --branch
git diff --check 1efc64a..HEAD
git log --oneline --decorate 1efc64a..HEAD
```

Expected: clean worktree, no whitespace errors, coherent small commits.

**Step 3: Request independent code review**

Dispatch one reviewer for requirements/mode/safety compliance and one reviewer for Lua/WeiDU
correctness. Fix every substantiated finding with RED-first regression coverage.

**Step 4: Re-run affected and complete checks**

Do not claim completion from cached or pre-fix output. Record exact final pass counts and any
explicitly untested compatibility matrix.

**Step 5: Push the branch and report**

After verifying the authenticated GitHub identity is `Chrizhermann`, push
`codex/eeex-delivery-redesign` to the public fork. Do not open an upstream PR or publish a release
without separate authorization.
