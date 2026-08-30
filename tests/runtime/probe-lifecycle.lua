-- Controlled save/load persistence probe for the disposable synthetic fixture.
--
-- Invocation contract (all in the same disposable game process):
--   1. Invoke once; wait for phase "await_save", then create a disposable save.
--   2. Invoke again; wait for phase "await_load", then load that save.
--   3. Invoke again.  The probe proves the saved value was restored and clears
--      its synthetic GLOBAL before reporting phase "done".
--
-- Calling step 2 before saving cannot produce a false pass: the subsequent
-- load must still restore the first sentinel value.  Calling step 3 before
-- loading is harmless; it remains pending while the mutation value is active.

FLDLVProbe = FLDLVProbe or {}
FLDLVProbe.results = FLDLVProbe.results or {}

local GLOBAL_NAME = "FLIRPRBLIFE"
local SAVED_VALUE = 17317
local MUTATED_VALUE = 28909
local CODES = {
    "L01", "L02", "L03", "L04", "L05", "L06", "L07", "L08", "L09",
}

local function safe(callable, ...)
    local values = { pcall(callable, ...) }
    if not values[1] then
        return nil
    end
    table.remove(values, 1)
    return values
end

local function copy_checks(checks)
    local out = {}
    for code, status in pairs(checks or {}) do
        out[code] = status
    end
    return out
end

local function sanitized_result(state)
    local result = {
        schema = "flir-eeex-probe-v1",
        probe = "lifecycle",
        phase = state.phase,
        passed = 0,
        failed = 0,
        skipped = 0,
        pending = 0,
        checks = {},
    }
    for _, code in ipairs(CODES) do
        local status = state.checks[code] or "S"
        result.checks[#result.checks + 1] = code .. ":" .. status
        if status == "P" then
            result.passed = result.passed + 1
        elseif status == "F" then
            result.failed = result.failed + 1
        elseif status == "K" then
            result.skipped = result.skipped + 1
        else
            result.pending = result.pending + 1
        end
    end
    FLDLVProbe.results.lifecycle = result
    return result
end

local function get_global()
    local values = safe(EEex_GameState_GetGlobalInt, GLOBAL_NAME)
    local value = values and values[1] or nil
    if type(value) ~= "number" or value ~= math.floor(value) then
        return nil
    end
    return value
end

local function set_and_observe(value)
    local written = safe(EEex_GameState_SetGlobalInt, GLOBAL_NAME, value)
    if not written then
        return false
    end
    return get_global() == value
end

local function cleanup_owned_global(state)
    local current = get_global()
    if state.owns_global or current == SAVED_VALUE or current == MUTATED_VALUE then
        local cleaned = set_and_observe(0)
        state.checks.L09 = cleaned and "P" or "F"
        state.owns_global = not cleaned
        return cleaned
    end
    state.checks.L09 = "K"
    return true
end

local function fail(state, code)
    state.checks[code] = "F"
    cleanup_owned_global(state)
    state.phase = "failed"
    return sanitized_result(state)
end

local state = FLDLVProbe.lifecycle_state
if type(state) ~= "table" then
    state = {
        phase = "start",
        checks = {},
        owns_global = false,
    }
    FLDLVProbe.lifecycle_state = state
else
    state.checks = copy_checks(state.checks)
end

if state.phase == "done" or state.phase == "failed" then
    return sanitized_result(state)
end

if state.phase == "start" then
    local api_ok = type(EEex_GameState_GetGlobalInt) == "function"
        and type(EEex_GameState_SetGlobalInt) == "function"
    state.checks.L01 = api_ok and "P" or "F"
    if not api_ok then
        state.checks.L09 = "K"
        state.phase = "failed"
        return sanitized_result(state)
    end

    local baseline = get_global()
    if baseline ~= 0 then
        -- A known value means a previous interrupted disposable probe owns it;
        -- remove it, but still fail this run instead of accepting stale state.
        if baseline == SAVED_VALUE or baseline == MUTATED_VALUE then
            state.owns_global = true
        end
        return fail(state, "L02")
    end
    state.checks.L02 = "P"

    local write_values = safe(EEex_GameState_SetGlobalInt,
        GLOBAL_NAME, SAVED_VALUE)
    if not write_values then
        return fail(state, "L03")
    end
    state.owns_global = true
    state.checks.L03 = "P"
    if get_global() ~= SAVED_VALUE then
        return fail(state, "L04")
    end
    state.checks.L04 = "P"
    state.phase = "await_save"
    return sanitized_result(state)
end

if state.phase == "await_save" then
    if get_global() ~= SAVED_VALUE then
        return fail(state, "L05")
    end
    state.checks.L05 = "P"

    local write_values = safe(EEex_GameState_SetGlobalInt,
        GLOBAL_NAME, MUTATED_VALUE)
    if not write_values then
        return fail(state, "L06")
    end
    state.checks.L06 = "P"
    if get_global() ~= MUTATED_VALUE then
        return fail(state, "L07")
    end
    state.checks.L07 = "P"
    state.phase = "await_load"
    return sanitized_result(state)
end

if state.phase == "await_load" then
    local restored = get_global()
    if restored == MUTATED_VALUE then
        -- The controlled save has not been loaded yet.
        return sanitized_result(state)
    end
    if restored ~= SAVED_VALUE then
        return fail(state, "L08")
    end
    state.checks.L08 = "P"
    if not cleanup_owned_global(state) then
        state.phase = "failed"
        return sanitized_result(state)
    end
    state.phase = "done"
    return sanitized_result(state)
end

return fail(state, "L01")
