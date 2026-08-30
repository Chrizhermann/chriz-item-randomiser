# Synchronous EEex Item Delivery Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:executing-plans to implement this plan task-by-task.

**Goal:** Replace recipient-owned queued delivery actions with synchronous, immediately verified EEex item insertion while deferring unspawned targets.

**Architecture:** Keep the generated manifest, stable endpoint resolver, exact item signatures, and durable GLOBAL journal. Execute one `CreateItem` or `GiveItemCreate` action through EEex's INSTANT.IDS executor, re-resolve the locked endpoint immediately, and commit only after an exact count increase. Missing primaries defer; dead/full primaries may use their authorized ground fallback.

**Tech Stack:** Lua 5.1/5.3, EEex v0.11 action and object APIs, WeiDU 249, PowerShell test harnesses, disposable EET 2.6.6 installation.

---

### Task 1: Convert the transaction core to synchronous execution

**Files:**
- Modify: `tests/lua/fake_engine.lua`
- Modify: `tests/lua/test_delivery.lua`
- Modify: `tests/lua/test_persistence.lua`
- Modify: `copy/FLDLVCor.lua`

**Step 1: Write the failing core tests**

Replace queue/ACK-specific assertions with tests for:

```lua
test("LivingPrimaryExecutesAndCommitsSynchronously", function()
    local manifest, engine, controller = setup({ fl1t1 = 1 })
    controller:poll()
    equal(engine:get_global("fl1t1"), -1)
    equal(engine:get_global(Core.GLOBALS.phase), Core.PHASE.NONE)
    equal(engine:count_exact("primary", "tstitema", { 3, 2, 1 }), 1)
end)

test("MissingSpawnGatedPrimaryDefersWithoutFallback", function()
    local manifest, engine, controller = setup({ fl1t1 = 1 })
    engine.endpoints.primary.present = false
    engine.endpoints.primary.settled = true
    controller:poll()
    equal(engine:get_global("fl1t1"), 1)
    equal(#engine.executions, 0)
    equal(#engine.endpoints.fallback.items, 0)
end)
```

Add focused cases for dead/full fallback, an execution that creates no exact
item, an execution error followed by successful observation, two assignments
sharing one endpoint, and no retry loop in the same GameState.

Update persistence cases to cover `PREPARED`, `EXECUTING`, `VERIFIED`, and an
unobservable locked endpoint. Remove nonce and stale-ACK cases.

**Step 2: Run the Lua suite and verify RED**

Run:

```powershell
& 'C:\Users\chris\Games\EET-IR-Test-b600e94\bg2\EET\bin\win32\x86_64\lua.exe' .\tests\lua\run.lua
```

Expected: FAIL because `execute_delivery`, `EXECUTING`, and `VERIFIED` do not
exist and the current core still enters `QUEUED`.

**Step 3: Make the fake engine synchronous**

Replace `queue_delivery()` / `process_queue()` with:

```lua
function FakeEngine:execute_delivery(endpoint_id, _, item)
    self.events[#self.events + 1] = {
        kind = "execute_delivery",
        endpoint_id = endpoint_id,
    }
    if self.execute_error then error(self.execute_error) end
    if self.execute_create ~= false then
        self:add_item(endpoint_id, item.resref, item.charges)
    end
    return self.execute_accept ~= false
end
```

Track `executions` and an ephemeral `retry_blocked` behavior rather than fake
engine queues.

**Step 4: Implement the synchronous core**

- Rename numeric phases `QUEUED -> EXECUTING` and `ACKED -> VERIFIED` while
  preserving values 2 and 3.
- Rename reason `QUEUE_FAILURE -> EXECUTION_FAILURE` while preserving value 7.
- Replace the required engine method with `execute_delivery`.
- Persist `PREPARED`, set `EXECUTING`, call the engine once, then immediately
  re-observe the locked endpoint and exact signature.
- On an exact `baseline + quantity` result, set `VERIFIED`, write token `-1`,
  and clear the journal.
- On stable unchanged state, keep the token positive, record the per-token
  reason, clear the journal, and block only that token until GameState teardown.
- On an unobservable `EXECUTING` endpoint, retain the locked journal and set
  `QUARANTINED`; never switch fallback.
- Clear ephemeral retry blocks at GameState teardown.
- Treat `MISSING` as deferred regardless of settling count; follow fallback
  only for an observable primary with a definitive `DEAD` or `FULL` reason.

**Step 5: Run the Lua suite and verify GREEN**

Run the command from Step 2. Expected: every Lua test passes with no
queue/ACK terminology in active test names.

**Step 6: Commit**

```powershell
git add copy/FLDLVCor.lua tests/lua/fake_engine.lua tests/lua/test_delivery.lua tests/lua/test_persistence.lua
git commit -m "refactor: make delivery transactions synchronous"
```

### Task 2: Replace the EEex queue adapter with instant execution

**Files:**
- Modify: `tests/lua/fake_eeex.lua`
- Modify: `tests/lua/test_adapter.lua`
- Modify: `copy/M_FLDLV.lua`

**Step 1: Write the failing adapter tests**

Require `EEex_Action_ExecuteScriptFileResponseAsAIBaseInstantly`, explicitly
remove `EEex_Action_QueueScriptFileResponseOnAIBase`, and assert one parsed
action:

```lua
local executed = root.Engine:execute_delivery("primary", endpoint,
    valid_item("tstitema", { 3, 2, 1 }))
equal(executed, true)
equal(fake.instant_attempts, 1)
equal(fake.queue_attempts, nil)
equal(fake.parsed[1].source,
    'GiveItemCreate("tstitema",Myself,3,2,1)')
equal(fake.free_count, 1)
```

Add equivalent container/ground `CreateItem` coverage, exact one-action
preflight rejection, instant-executor exception propagation, stable endpoint
re-resolution, and GameState teardown with no ACK/generation state.

**Step 2: Run the Lua suite and verify RED**

Run the Task 1 Lua command. Expected: FAIL because the bootstrap still requires
the queue API and publishes `_DeliveryAck` state.

**Step 3: Extend the fake EEex surface**

Implement a fake
`EEex_Action_ExecuteScriptFileResponseAsAIBaseInstantly(parsed, object)` that
records the parsed source, fresh object wrapper serial, and any configured
exception. Remove fake ACK and queue state.

**Step 4: Implement the instant adapter**

Implement:

```lua
function Engine:execute_delivery(endpoint_id, _, item)
    -- Re-resolve and preflight the locked endpoint.
    -- Build exactly one GiveItemCreate or CreateItem action.
    -- Parse, require action_count == 1, execute instantly, and free once.
    -- Return true only after the executor returns without exception.
end
```

Remove `_DeliveryAck`, `_ackCallbacks`, generation handling, the queued
two-action payload, and the queue capability requirement. Preserve opaque
diagnostics and all existing object-identity checks.

**Step 5: Run the Lua suite and verify GREEN**

Run the Task 1 Lua command. Expected: all Lua tests pass.

**Step 6: Commit**

```powershell
git add copy/M_FLDLV.lua tests/lua/fake_eeex.lua tests/lua/test_adapter.lua
git commit -m "fix: execute EEex item delivery instantly"
```

### Task 3: Make the runtime probe enforce the no-queue contract

**Files:**
- Modify: `tests/runtime/probe-eeex-surface.lua`
- Modify: `tests/runtime/probe-transport.lua`
- Modify: `tests/runtime/probe-aggregate.lua` if its result keys change
- Modify: `tests/Test-RuntimeProbes.ps1`

**Step 1: Write failing static/runtime-probe assertions**

Require the instant executor and reject delivery source containing any of:

```text
EEex_Action_Queue
EEex_LuaAction
_DeliveryAck
```

The transport probe must snapshot the recipient's current action and queued
action IDs, execute one synthetic delivery, then assert:

```lua
exact_after == exact_before + 1
current_action_after == current_action_before
queued_actions_after == queued_actions_before
```

**Step 2: Run the focused PowerShell test and verify RED**

```powershell
pwsh -NoProfile -File .\tests\Test-RuntimeProbes.ps1
```

Expected: FAIL because the checked source/probe still uses queue and ACK APIs.

**Step 3: Update the probes minimally**

Use only the official instant executor for action 82/140. Keep existing
synthetic resrefs, spoiler-free aggregate output, uniqueness guards, and parsed
action cleanup.

**Step 4: Run the focused PowerShell test and verify GREEN**

Run the Step 2 command. Expected: PASS.

**Step 5: Commit**

```powershell
git add tests/runtime tests/Test-RuntimeProbes.ps1
git commit -m "test: require queue-free runtime delivery"
```

### Task 4: Run the complete local verification matrix

**Files:**
- Modify only if a failing related assertion proves an update is required.

**Step 1: Run whitespace and forbidden-source checks**

```powershell
git diff --check
rg -n 'EEex_Action_Queue|EEex_LuaAction|_DeliveryAck' copy/M_FLDLV.lua copy/FLDLVCor.lua
```

Expected: no whitespace errors and no forbidden delivery symbols.

**Step 2: Run the full hermetic suite**

```powershell
pwsh -NoProfile -File .\tests\run-unit.ps1
```

Expected: exit 0, all PowerShell/WeiDU/Lua checks pass, and the live-game safety
guards remain green.

**Step 3: Review the change footprint**

```powershell
git status --short
git diff --stat 71d5ede..HEAD
git diff --check 71d5ede..HEAD
```

Expected: only the planned core, adapter, focused tests, probes, and plan files.
Diagnose every related failure before changing code; report unrelated failures
without bypassing them.

### Task 5: Reinstall and runtime-test only the disposable EET copy

**Files:**
- Copy tracked candidate files into: `C:\Users\chris\Games\EET-IR-Test-b600e94\bg2\randomiser`
- Do not modify: `C:\Games\Baldur's Gate II Enhanced Edition modded`

**Step 1: Verify both game processes are absent**

```powershell
Get-Process -Name Baldur,InfinityLoader -ErrorAction SilentlyContinue
```

Expected: no output.

**Step 2: Deploy only changed tracked mod files**

Copy the reviewed candidate source files without mirror-delete and without
touching the disposable mod's backup/state directories. Compare SHA-256 hashes
between repository source and staged files.

**Step 3: Reinstall component 1100 in one WeiDU process**

From `C:\Users\chris\Games\EET-IR-Test-b600e94\bg2` run:

```powershell
'y' | & .\Setup-Randomiser.exe --game . --use-lang en_US --language 0 `
  --force-uninstall-list 1100 --force-install-list 1100 --no-exit-pause
```

The explicit `y` answers the mod-owned saved-game compatibility prompt when
the preserved seed/options/removal files are present.  Omitting it makes the
noninteractive reinstall stop at `ACTION_READLN` after uninstalling 1100.

Expected: exit 0, component 1100 appears exactly once, generated manifest and
Lua parse checks pass, installed `M_FLDLV.lua` matches staged source, and Remote
Console remains installed.

**Step 4: Run the contained in-game probe**

Ask the owner to start only the disposable `InfinityLoader.exe`. Use the single
Remote Console IPC channel to run ping, the watchdog smoke test, and the updated
transport aggregate. Acceptance is queue-free execution with unchanged
current/queued actions, followed by exact insertion evidence on the next engine
boundary, clean transaction globals, and no item-location output.

**Step 5: Close the game and perform final review**

Confirm both disposable processes exit. Re-run the Lua suite and focused runtime
source test, inspect `git diff --stat`, and obtain an independent final code
review before claiming completion. Do not repeat the Azamantes encounter unless
the contained probe disagrees with the automated contract.
