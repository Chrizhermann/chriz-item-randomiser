[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $PSScriptRoot 'runtime'
$luaPath = [System.Environment]::GetEnvironmentVariable('FL_IR_TEST_LUA', 'Process')
$tempRoot = [System.Environment]::GetEnvironmentVariable('FL_IR_TEST_TEMP_ROOT', 'Process')

if ([string]::IsNullOrWhiteSpace($luaPath) -or
    -not (Test-Path -LiteralPath $luaPath -PathType Leaf)) {
    throw 'FL_IR_TEST_LUA does not name the validated Lua 5.3 interpreter.'
}
if ([string]::IsNullOrWhiteSpace($tempRoot) -or
    -not (Test-Path -LiteralPath $tempRoot -PathType Container)) {
    throw 'FL_IR_TEST_TEMP_ROOT does not name the validated disposable test directory.'
}

$probeNames = @(
    'probe-eeex-surface.lua',
    'probe-transport.lua',
    'probe-lifecycle.lua',
    'probe-aggregate.lua'
)
foreach ($probeName in $probeNames) {
    $probePath = Join-Path $runtimeRoot $probeName
    if (-not (Test-Path -LiteralPath $probePath -PathType Leaf)) {
        throw "Missing runtime probe '$probeName'."
    }
    & $luaPath -e 'assert(loadfile(arg[1]))' $probePath
    if ($LASTEXITCODE -ne 0) {
        throw "Lua syntax check failed for '$probeName'."
    }
}

$surface = [System.IO.File]::ReadAllText((Join-Path $runtimeRoot 'probe-eeex-surface.lua'))
$transport = [System.IO.File]::ReadAllText((Join-Path $runtimeRoot 'probe-transport.lua'))
$lifecycle = [System.IO.File]::ReadAllText((Join-Path $runtimeRoot 'probe-lifecycle.lua'))
$aggregate = [System.IO.File]::ReadAllText((Join-Path $runtimeRoot 'probe-aggregate.lua'))
$adapter = [System.IO.File]::ReadAllText((Join-Path $repositoryRoot 'copy/M_FLDLV.lua'))

if ($surface -notmatch 'EEex_Action_ExecuteScriptFileResponseAsAIBaseInstantly' -or
    $transport -notmatch 'EEex_Action_ExecuteScriptFileResponseAsAIBaseInstantly' -or
    $adapter -notmatch 'EEex_Action_ExecuteScriptFileResponseAsAIBaseInstantly') {
    throw 'The delivery surface does not require and use the official instant executor.'
}
foreach ($forbidden in @('EEex_Action_Queue', 'EEex_LuaAction', '_DeliveryAck')) {
    if ($transport -match [regex]::Escape($forbidden) -or
        $adapter -match [regex]::Escape($forbidden)) {
        throw "The delivery source still contains forbidden queued transport '$forbidden'."
    }
}
if ($transport -notmatch 'm_curAction' -or
    $transport -notmatch 'm_queuedActions' -or
    $transport -notmatch 'action_snapshot' -or
    $transport -notmatch 'actions_unchanged') {
    throw 'The transport probe does not prove that instant delivery preserves action state.'
}

if ($surface -notmatch 'ability_maxima' -or
    $surface -notmatch 'cResRef' -or
    $surface -notmatch 'm_useCount1' -or
    $surface -notmatch 'm_useCount2' -or
    $surface -notmatch 'm_useCount3' -or
    $surface -notmatch 'ground_endpoint') {
    throw 'The surface probe does not authenticate runtime item charge triplets and the private ground endpoint.'
}
if ($surface -match 'header:getAbility|maxUsageCount') {
    throw 'The surface probe must not use EEex v0.11 item-header ability stepping for charge evidence.'
}
foreach ($liveObjectList in @('m_lVertSort', 'm_lVertSortBack', 'm_lVertSortFlight', 'm_lVertSortUnder')) {
    if ($surface -notmatch [regex]::Escape($liveObjectList) -or
        $transport -notmatch [regex]::Escape($liveObjectList)) {
        throw "The runtime probes do not sweep verified live object list '$liveObjectList'."
    }
}
if ($transport -notmatch 'm_containerType' -or
    $transport -notmatch 'container_type\s*==\s*4' -or
    $transport -match 'resolve_unique\s*\([^\r\n]*config\.pile_script' -or
    $transport -notmatch 'last_slot_script' -or
    $transport -notmatch 'full_script' -or
    $transport -notmatch 'InventoryFull\(Myself\)' -or
    $transport -notmatch 'settle' -or
    $transport -notmatch 'instant_attempts') {
    throw 'The transport probe lacks coordinate/type ground resolution or observed capacity/action/stability checks.'
}
if ($lifecycle -notmatch 'EEex_GameState_GetGlobalInt' -or
    $lifecycle -notmatch 'EEex_GameState_SetGlobalInt' -or
    $lifecycle -notmatch 'await_save' -or
    $lifecycle -notmatch 'await_load' -or
    $lifecycle -notmatch 'cleanup') {
    throw 'The lifecycle probe does not implement the controlled save/load/cleanup round trip.'
}
foreach ($codeRange in @('S01', 'S06', 'T01', 'T08', 'L01', 'L09')) {
    if ($aggregate -notmatch [regex]::Escape($codeRange)) {
        throw "The aggregate contract omits '$codeRange'."
    }
}

$harnessPath = Join-Path $tempRoot ('runtime-probe-aggregate-' + [guid]::NewGuid().ToString('N') + '.lua')
$harness = @'
local aggregate_path = assert(arg[1])
local lifecycle_path = assert(arg[2])
local transport_path = assert(arg[3])

local expected = {
    surface = { prefix = "S", count = 6, phase = "complete" },
    transport = { prefix = "T", count = 8, phase = "done" },
    lifecycle = { prefix = "L", count = 9, phase = "done" },
}

local function fresh_results()
    local out = {}
    for name, spec in pairs(expected) do
        local checks = {}
        for index = 1, spec.count do
            checks[#checks + 1] = string.format("%s%02d:P", spec.prefix, index)
        end
        out[name] = {
            schema = "flir-eeex-probe-v1",
            probe = name,
            phase = spec.phase,
            passed = spec.count,
            failed = 0,
            skipped = 0,
            pending = 0,
            checks = checks,
        }
    end
    return out
end

local function execute(results)
    FLDLVProbe = { results = results }
    local value = assert(dofile(aggregate_path))
    assert(#value.checks == 23, "aggregate must always return the exact 23-code set")
    local seen = {}
    for _, check in ipairs(value.checks) do
        local code = assert(string.match(check, "^([STL]%d%d):[PFKS]$"))
        assert(not seen[code], "aggregate returned a duplicate code")
        seen[code] = true
    end
    return value
end

local valid = execute(fresh_results())
assert(valid.valid == true and valid.failed == 0 and valid.passed == 23)

local missing = fresh_results()
table.remove(missing.transport.checks, 8)
missing.transport.passed = 7
local missing_result = execute(missing)
assert(missing_result.valid == false and missing_result.failed > 0)

local duplicate = fresh_results()
duplicate.surface.checks[6] = duplicate.surface.checks[1]
local duplicate_result = execute(duplicate)
assert(duplicate_result.valid == false and duplicate_result.failed > 0)

local unexpected = fresh_results()
unexpected.lifecycle.checks[#unexpected.lifecycle.checks + 1] = "L10:P"
unexpected.lifecycle.passed = 10
local unexpected_result = execute(unexpected)
assert(unexpected_result.valid == false and unexpected_result.failed > 0)

local bad_count = fresh_results()
bad_count.surface.passed = 5
local bad_count_result = execute(bad_count)
assert(bad_count_result.valid == false and bad_count_result.failed > 0)

print("PASS RuntimeProbeAggregate_ExactContract")

local globals = {}
function EEex_GameState_GetGlobalInt(name)
    return globals[name] or 0
end
function EEex_GameState_SetGlobalInt(name, value)
    globals[name] = value
end

FLDLVProbe = {}
local prepared = assert(dofile(lifecycle_path))
assert(prepared.phase == "await_save" and prepared.passed == 4
    and prepared.pending == 5)
local saved_value = assert(globals.FLIRPRBLIFE)

local mutated = assert(dofile(lifecycle_path))
assert(mutated.phase == "await_load" and mutated.passed == 7
    and globals.FLIRPRBLIFE ~= saved_value)
local still_waiting = assert(dofile(lifecycle_path))
assert(still_waiting.phase == "await_load" and still_waiting.pending == 2)

globals.FLIRPRBLIFE = saved_value
local restored = assert(dofile(lifecycle_path))
assert(restored.phase == "done" and restored.passed == 9
    and restored.failed == 0 and restored.pending == 0
    and globals.FLIRPRBLIFE == 0)

FLDLVProbe = {}
globals.FLIRPRBLIFE = saved_value
local stale = assert(dofile(lifecycle_path))
assert(stale.phase == "failed" and stale.failed == 1
    and globals.FLIRPRBLIFE == 0)

print("PASS RuntimeProbeLifecycle_ControlledRoundTripAndCleanup")

local function identity(value)
    return { get = function() return value end }
end

local function instance_item(resref, charges)
    return {
        cResRef = identity(resref),
        m_useCount1 = charges[1],
        m_useCount2 = charges[2],
        m_useCount3 = charges[3],
    }
end

local function item_slots(seed_count)
    local values = {}
    for index = 0, seed_count - 1 do
        values[index] = instance_item("FLRTPFL", { 0, 0, 0 })
    end
    return {
        values = values,
        get = function(self, slot) return self.values[slot] end,
    }
end

local objects
local instant_calls
local mutate_action_state
local function add_action_state(object, current_id)
    object.m_curAction = { m_actionID = current_id }
    object.m_queuedActions = {
        { m_actionID = current_id + 10 },
        { m_actionID = current_id + 20 },
    }
    return object
end
local function creature(script, seed_count, full)
    return add_action_state({
        _sprite = true,
        _full = full,
        m_scriptName = identity(script),
        m_equipment = { m_items = item_slots(seed_count) },
    }, 7)
end

local function container(script, container_type, x, y)
    return add_action_state({
        _sprite = false,
        m_scriptName = identity(script),
        m_containerType = container_type,
        m_pos = { x = x, y = y },
        m_lstItems = { instance_item("FLRTPFL", { 0, 0, 0 }) },
    }, 11)
end

local function reset_transport_world(mutate)
    objects = {
        [1] = creature("FLRTPU", 1, false),
        [2] = creature("FLRTPL", 15, false),
        [3] = creature("FLRTPF", 16, true),
        [4] = container("FLRTPC", 8, 100, 148),
        -- Deliberately not config.pile_script: transport must use endpoint only.
        [5] = container("ACTPILE", 4, 164, 148),
    }
    instant_calls = 0
    mutate_action_state = mutate == true
    FLDLVProbe = {
        config = {
            area_resref = "FLRTPRA",
            creature_script = "FLRTPU",
            duplicate_script = "FLRTPD",
            last_slot_script = "FLRTPL",
            full_script = "FLRTPF",
            container_script = "FLRTPC",
            pile_script = "FLRTPP",
            item_resrefs = {
                creature = "FLRTPIT", container = "FLRTPJ",
                pile = "FLRTPK", filler = "FLRTPFL",
            },
            charge_triples = {
                creature = { 2, 3, 5 }, container = { 7, 11, 13 },
                pile = { 17, 19, 23 }, filler = { 0, 0, 0 },
            },
            ability_maxima = {
                creature = { 7, 11, 13 }, container = { 17, 19, 23 },
                pile = { 29, 31, 37 }, filler = { 0, 0, 0 },
            },
            items = {
                creature = { resref = "FLRTPIT", charges = { 2, 3, 5 } },
                container = { resref = "FLRTPJ", charges = { 7, 11, 13 } },
                pile = { resref = "FLRTPK", charges = { 17, 19, 23 } },
            },
        },
        ground_endpoint = {
            area_resref = "flrtpra", x = 164, y = 148, container_type = 4,
        },
    }
end

local area = {
    m_lVertSort = { 1, 2, 3, 4, 5 },
    m_lVertSortBack = {},
    m_lVertSortFlight = {},
    m_lVertSortUnder = {},
    m_resref = identity("FLRTPRA"),
}
function EEex_Area_GetVisible() return area end
function EEex_Utility_IterateCPtrList(list, callback)
    for _, value in ipairs(list) do callback(value) end
end
function EEex_GameObject_Get(id) return objects[id] end
function EEex_GameObject_CastUserType(object) return object end
function EEex_GameObject_IsSprite(object) return object._sprite end
function EEex_Trigger_EvalConditionalStringAsAIBase(_, object)
    return object._full
end
function EEex_Action_ParseResponseString(text)
    return {
        text = text,
        m_curResponse = { m_actionList = { { m_actionID = 140 } } },
        free = function() end,
    }
end
function EEex_Action_ExecuteScriptFileResponseAsAIBaseInstantly(parsed, target)
    instant_calls = instant_calls + 1
    local resref, charge1, charge2, charge3 = string.match(parsed.text,
        'GiveItemCreate%("([^"]+)",Myself,(%d+),(%d+),(%d+)%)')
    if not resref then
        resref, charge1, charge2, charge3 = string.match(parsed.text,
            'CreateItem%("([^"]+)",(%d+),(%d+),(%d+)%)')
    end
    assert(resref)
    local item = instance_item(resref, {
        tonumber(charge1), tonumber(charge2), tonumber(charge3),
    })
    if target._sprite then
        for slot = 0, 38 do
            if target.m_equipment.m_items.values[slot] == nil then
                target.m_equipment.m_items.values[slot] = item
                break
            end
        end
    else
        target.m_lstItems[#target.m_lstItems + 1] = item
    end
    if mutate_action_state then
        target.m_curAction.m_actionID = target.m_curAction.m_actionID + 1
        target.m_queuedActions[#target.m_queuedActions + 1] = { m_actionID = 99 }
    end
end

reset_transport_world()
local transport = assert(dofile(transport_path))
assert(transport.phase == "settle" and transport.passed == 7
    and transport.pending == 1 and instant_calls == 3)
transport = assert(dofile(transport_path))
assert(transport.phase == "done" and transport.passed == 8
    and transport.failed == 0 and transport.pending == 0
    and instant_calls == 3)
local stable = assert(dofile(transport_path))
assert(stable.phase == "done" and instant_calls == 3)

reset_transport_world()
FLDLVProbe.config.creature_script = ""
local rejected = assert(dofile(transport_path))
assert(rejected.phase == "failed" and rejected.failed == 1
    and instant_calls == 0)

reset_transport_world(true)
local action_mutation = assert(dofile(transport_path))
assert(action_mutation.phase == "failed" and action_mutation.failed == 1
    and instant_calls == 1)

print("PASS RuntimeProbeTransport_InstantDeltaAndActionGuards")
'@

try {
    [System.IO.File]::WriteAllText($harnessPath, $harness, (New-Object System.Text.UTF8Encoding($false)))
    & $luaPath $harnessPath `
        (Join-Path $runtimeRoot 'probe-aggregate.lua') `
        (Join-Path $runtimeRoot 'probe-lifecycle.lua') `
        (Join-Path $runtimeRoot 'probe-transport.lua')
    if ($LASTEXITCODE -ne 0) {
        throw 'The aggregate adversarial Lua harness failed.'
    }
}
finally {
    if (Test-Path -LiteralPath $harnessPath -PathType Leaf) {
        Remove-Item -LiteralPath $harnessPath -Force
    }
}

Write-Output 'PASS RuntimeProbes_SyntaxAndStaticContracts'
