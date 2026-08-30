-- Stateful instant-transport probe for the disposable synthetic fixture.
--
-- The first accepted call executes three synthetic INSTANT.IDS actions and
-- proves that recipient action state is unchanged.  The engine publishes the
-- new item-list entries only after that Lua call returns, so the second call
-- verifies all three exact deltas and proves that no transport was repeated.
-- The ground target is resolved only by authenticated area/type/coordinates,
-- never by script name.  Returned data contains check codes and counts only.

FLDLVProbe = FLDLVProbe or {}
FLDLVProbe.results = FLDLVProbe.results or {}

local CODES = {
    "T01", "T02", "T03", "T04", "T05", "T06", "T07", "T08",
}
local ITEM_KEYS = { "creature", "container", "pile", "filler" }
local LIVE_OBJECT_LISTS = {
    "m_lVertSort",
    "m_lVertSortBack",
    "m_lVertSortFlight",
    "m_lVertSortUnder",
}

local function safe(callable, ...)
    local values = { pcall(callable, ...) }
    if not values[1] then
        return nil
    end
    table.remove(values, 1)
    return values
end

local function valid_identity(value)
    return type(value) == "string"
        and #value >= 1 and #value <= 8
        and string.match(value, "^[A-Za-z0-9#_-]+$") ~= nil
end

local function normalized_identity(value)
    if type(value) ~= "string" then
        return nil
    end
    return string.lower((string.gsub(value, "%s+", "")))
end

local function valid_triplet(value)
    if type(value) ~= "table" then
        return false
    end
    for index = 1, 3 do
        local charge = value[index]
        if type(charge) ~= "number" or charge ~= math.floor(charge)
            or charge < 0 or charge > 65535 then
            return false
        end
    end
    return true
end

local function valid_item(item)
    return type(item) == "table" and valid_identity(item.resref)
        and valid_triplet(item.charges)
end

local function validate_config(config)
    if type(config) ~= "table" or type(config.items) ~= "table"
        or type(config.item_resrefs) ~= "table"
        or type(config.charge_triples) ~= "table"
        or type(config.ability_maxima) ~= "table" then
        return false
    end

    local targets = {
        config.area_resref,
        config.creature_script,
        config.duplicate_script,
        config.last_slot_script,
        config.full_script,
        config.container_script,
        config.pile_script,
    }
    local seen_targets = {}
    for _, identity in ipairs(targets) do
        if not valid_identity(identity) then
            return false
        end
        local normalized = normalized_identity(identity)
        if seen_targets[normalized] then
            return false
        end
        seen_targets[normalized] = true
    end

    local seen_items = {}
    for _, key in ipairs(ITEM_KEYS) do
        local resref = config.item_resrefs[key]
        local charges = config.charge_triples[key]
        local maxima = config.ability_maxima[key]
        if not valid_identity(resref) or not valid_triplet(charges)
            or not valid_triplet(maxima) then
            return false
        end
        local normalized = normalized_identity(resref)
        if seen_items[normalized] then
            return false
        end
        seen_items[normalized] = true
        for index = 1, 3 do
            if charges[index] > maxima[index] then
                return false
            end
        end
        if key == "filler" then
            if charges[1] ~= 0 or charges[2] ~= 0 or charges[3] ~= 0
                or maxima[1] ~= 0 or maxima[2] ~= 0
                or maxima[3] ~= 0 then
                return false
            end
        else
            local item = config.items[key]
            if not valid_item(item)
                or normalized_identity(item.resref) ~= normalized then
                return false
            end
            for index = 1, 3 do
                if item.charges[index] ~= charges[index] then
                    return false
                end
            end
        end
    end
    return true
end

local function valid_ground_endpoint(endpoint, area_identity)
    return type(endpoint) == "table"
        and valid_identity(area_identity)
        and endpoint.area_resref == normalized_identity(area_identity)
        and endpoint.container_type == 4
        and type(endpoint.x) == "number" and endpoint.x == math.floor(endpoint.x)
        and endpoint.x >= 0 and endpoint.x <= 65535
        and type(endpoint.y) == "number" and endpoint.y == math.floor(endpoint.y)
        and endpoint.y >= 0 and endpoint.y <= 65535
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
        probe = "transport",
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
    FLDLVProbe.results.transport = result
    return result
end

local function terminal_failure(state, code)
    state.checks[code] = "F"
    state.phase = "failed"
    return sanitized_result(state)
end

local function visible_objects()
    local values = safe(EEex_Area_GetVisible)
    local area = values and values[1] or nil
    if not area or not area.m_lVertSort or not area.m_resref then
        return nil, nil, nil
    end
    local area_values = safe(function()
        return area.m_resref:get()
    end)
    local area_identity = area_values and area_values[1] or nil
    if not valid_identity(area_identity) then
        return nil, nil, nil
    end
    local objects = {}
    local iterated = safe(function()
        local seen = {}
        for _, field in ipairs(LIVE_OBJECT_LISTS) do
            local list = area[field]
            if not list then
                error("missing live object list")
            end
            EEex_Utility_IterateCPtrList(list, function(object_id)
                if type(object_id) ~= "number"
                    or object_id ~= math.floor(object_id) then
                    error("bad object id")
                end
                if not seen[object_id] then
                    local raw = EEex_GameObject_Get(object_id)
                    local object = raw and EEex_GameObject_CastUserType(raw) or nil
                    if not object then
                        error("object disappeared during iteration")
                    end
                    seen[object_id] = true
                    objects[#objects + 1] = { id = object_id, object = object }
                end
            end)
        end
    end)
    if not iterated then
        return nil, nil, nil
    end
    return area, area_identity, objects
end

local function script_name(entry)
    if not entry or not entry.object or not entry.object.m_scriptName then
        return nil
    end
    local values = safe(function()
        return entry.object.m_scriptName:get()
    end)
    return values and values[1] or nil
end

local function resolve_unique(objects, identity)
    if not valid_identity(identity) then
        return nil
    end
    local wanted = normalized_identity(identity)
    local found
    local count = 0
    for _, entry in ipairs(objects or {}) do
        if normalized_identity(script_name(entry)) == wanted then
            count = count + 1
            found = entry
        end
    end
    return count == 1 and found or nil
end

local function sprite_status(entry)
    if not entry or not entry.object then
        return nil
    end
    local values = safe(EEex_GameObject_IsSprite, entry.object, true)
    if not values or type(values[1]) ~= "boolean" then
        return nil
    end
    return values[1]
end

local function read_container_type(entry)
    if not entry or not entry.object then
        return nil
    end
    local values = safe(function()
        return entry.object.m_containerType
    end)
    local value = values and values[1] or nil
    if type(value) ~= "number" or value ~= math.floor(value) then
        return nil
    end
    return value
end

local function read_position(entry)
    if not entry or not entry.object or not entry.object.m_pos then
        return nil, nil
    end
    local values = safe(function()
        return entry.object.m_pos.x, entry.object.m_pos.y
    end)
    local x = values and values[1] or nil
    local y = values and values[2] or nil
    if type(x) ~= "number" or x ~= math.floor(x)
        or type(y) ~= "number" or y ~= math.floor(y) then
        return nil, nil
    end
    return x, y
end

local function resolve_ground(objects, endpoint, area_identity)
    if not valid_ground_endpoint(endpoint, area_identity) then
        return nil
    end
    local found
    local count = 0
    for _, entry in ipairs(objects or {}) do
        local x, y = read_position(entry)
        if sprite_status(entry) == false and entry.object.m_lstItems
            and read_container_type(entry) == endpoint.container_type
            and x == endpoint.x and y == endpoint.y then
            count = count + 1
            found = entry
        end
    end
    return count == 1 and found or nil
end

local function refresh_target(entry, target_kind, expected_identity,
    endpoint, area_identity)
    if not entry or type(entry.id) ~= "number" then
        return nil
    end
    local values = safe(function()
        local raw = EEex_GameObject_Get(entry.id)
        return raw and EEex_GameObject_CastUserType(raw) or nil
    end)
    local object = values and values[1] or nil
    if not object then
        return nil
    end
    local live = { id = entry.id, object = object }
    if target_kind == "creature" then
        if not valid_identity(expected_identity)
            or sprite_status(live) ~= true
            or normalized_identity(script_name(live))
                ~= normalized_identity(expected_identity)
            or not object.m_equipment or not object.m_equipment.m_items then
            return nil
        end
    elseif target_kind == "container" then
        if not valid_identity(expected_identity)
            or sprite_status(live) ~= false
            or normalized_identity(script_name(live))
                ~= normalized_identity(expected_identity)
            or not object.m_lstItems or read_container_type(live) ~= 8 then
            return nil
        end
    elseif target_kind == "pile" then
        local x, y = read_position(live)
        if not valid_ground_endpoint(endpoint, area_identity)
            or sprite_status(live) ~= false or not object.m_lstItems
            or read_container_type(live) ~= 4
            or x ~= endpoint.x or y ~= endpoint.y then
            return nil
        end
    else
        return nil
    end
    return live
end

local function item_signature(item)
    if not item or not item.cResRef then
        return nil
    end
    local values = safe(function()
        return item.cResRef:get(), item.m_useCount1, item.m_useCount2,
            item.m_useCount3
    end)
    if not values or not valid_identity(values[1])
        or type(values[2]) ~= "number" or type(values[3]) ~= "number"
        or type(values[4]) ~= "number" then
        return nil
    end
    return values[1], values[2], values[3], values[4]
end

local function matching_count(entry, target_kind, expected)
    if not entry or not entry.object or not valid_item(expected) then
        return nil
    end
    local count = 0
    local function inspect(item)
        local resref, charge1, charge2, charge3 = item_signature(item)
        if not resref then
            error("unreadable item")
        end
        if normalized_identity(resref) == normalized_identity(expected.resref)
            and charge1 == expected.charges[1]
            and charge2 == expected.charges[2]
            and charge3 == expected.charges[3] then
            count = count + 1
        end
    end
    local iterated = safe(function()
        if target_kind == "creature" then
            for slot = 0, 38 do
                local item = entry.object.m_equipment.m_items:get(slot)
                if item then
                    inspect(item)
                end
            end
        else
            EEex_Utility_IterateCPtrList(entry.object.m_lstItems, inspect)
        end
    end)
    return iterated and count or nil
end

local function inventory_full(entry)
    if not entry or sprite_status(entry) ~= true then
        return nil
    end
    local values = safe(EEex_Trigger_EvalConditionalStringAsAIBase,
        "InventoryFull(Myself)", entry.object)
    if not values or type(values[1]) ~= "boolean" then
        return nil
    end
    return values[1]
end

local function action_snapshot(entry)
    local snapshot
    local inspected = safe(function()
        local object = assert(entry and entry.object, "target object missing")
        local current = assert(object.m_curAction, "current action missing")
        local current_id = current.m_actionID
        if type(current_id) ~= "number" or current_id ~= math.floor(current_id) then
            error("current action id invalid")
        end
        local queued = assert(object.m_queuedActions, "queued actions missing")
        local queued_ids = {}
        EEex_Utility_IterateCPtrList(queued, function(action)
            local action_id = action and action.m_actionID
            if type(action_id) ~= "number" or action_id ~= math.floor(action_id) then
                error("queued action id invalid")
            end
            queued_ids[#queued_ids + 1] = action_id
        end)
        snapshot = { current = current_id, queued = queued_ids }
    end)
    return inspected and snapshot or nil
end

local function actions_unchanged(before, after)
    if not before or not after or before.current ~= after.current
        or #before.queued ~= #after.queued then
        return false
    end
    for index, action_id in ipairs(before.queued) do
        if after.queued[index] ~= action_id then
            return false
        end
    end
    return true
end

local function execute_transport(state, entry, target_kind, expected_identity,
    endpoint, area_identity, item, code)
    if not valid_item(item) then
        return false, "item"
    end
    local target = refresh_target(entry, target_kind, expected_identity,
        endpoint, area_identity)
    if not target then
        return false, "target"
    end
    if target_kind == "creature" then
        local full = inventory_full(target)
        if full ~= false then
            return false, full == true and "capacity" or "target"
        end
    end

    local baseline = matching_count(target, target_kind, item)
    local before_actions = action_snapshot(target)
    if baseline == nil or not before_actions then
        return false, "target"
    end

    local transport
    if target_kind == "creature" then
        transport = string.format(
            'GiveItemCreate("%s",Myself,%d,%d,%d)', item.resref,
            item.charges[1], item.charges[2], item.charges[3])
    else
        transport = string.format(
            'CreateItem("%s",%d,%d,%d)', item.resref,
            item.charges[1], item.charges[2], item.charges[3])
    end
    local parsed_values = safe(EEex_Action_ParseResponseString,
        transport)
    local parsed = parsed_values and parsed_values[1] or nil
    if not parsed or not parsed.m_curResponse
        or not parsed.m_curResponse.m_actionList then
        return false, "parse"
    end
    local action_count = 0
    local counted = safe(function()
        EEex_Utility_IterateCPtrList(parsed.m_curResponse.m_actionList,
            function()
                action_count = action_count + 1
            end)
    end)
    if not counted or action_count ~= 1 then
        safe(function()
            parsed:free()
        end)
        return false, "parse"
    end

    state.instant_attempts = (state.instant_attempts or 0) + 1
    state.parsed_actions = (state.parsed_actions or 0) + action_count
    local executed = safe(EEex_Action_ExecuteScriptFileResponseAsAIBaseInstantly,
        parsed, target.object)
    local freed = safe(function()
        parsed:free()
    end)

    local after_target = refresh_target(target, target_kind, expected_identity,
        endpoint, area_identity)
    local after_actions = after_target and action_snapshot(after_target) or nil
    local stable_actions = actions_unchanged(before_actions, after_actions)
    if stable_actions then
        state.unchanged_actions = (state.unchanged_actions or 0) + 1
    end
    if not executed or not freed or not stable_actions then
        return false, "execution"
    end
    return true, "executed"
end

local function full_guard_ok(state, full_entry, last_entry, config,
    endpoint, area_identity)
    local full_live = refresh_target(full_entry, "creature",
        config.full_script, endpoint, area_identity)
    local last_live = refresh_target(last_entry, "creature",
        config.last_slot_script, endpoint, area_identity)
    if not full_live or not last_live
        or inventory_full(full_live) ~= true
        or inventory_full(last_live) ~= false then
        return false
    end
    local count = matching_count(full_live, "creature", config.items.creature)
    return count ~= nil and count == state.full_guard_baseline
end

local config = FLDLVProbe.config
local state = FLDLVProbe.transport_state
if type(state) ~= "table" or state.version ~= 2 then
    state = {
        version = 2,
        phase = "start",
        checks = {},
        exact_transports = 0,
        exact_deltas = 0,
        instant_attempts = 0,
        parsed_actions = 0,
        unchanged_actions = 0,
    }
    FLDLVProbe.transport_state = state
else
    state.checks = copy_checks(state.checks)
end

if state.phase == "failed" or state.phase == "done" then
    return sanitized_result(state)
end

if not validate_config(config) then
    return terminal_failure(state, "T01")
end

local area, area_identity, objects = visible_objects()
if not area or normalized_identity(area_identity)
    ~= normalized_identity(config.area_resref) then
    return sanitized_result(state)
end
local endpoint = FLDLVProbe.ground_endpoint
if not valid_ground_endpoint(endpoint, area_identity) then
    return terminal_failure(state, "T01")
end

local creature = resolve_unique(objects, config.creature_script)
local container = resolve_unique(objects, config.container_script)
local pile = resolve_ground(objects, endpoint, area_identity)
local last_slot = resolve_unique(objects, config.last_slot_script)
local full_creature = resolve_unique(objects, config.full_script)
if not creature or not container or not pile or not last_slot
    or not full_creature then
    return sanitized_result(state)
end

if state.phase == "start" then
    local creature_live = refresh_target(creature, "creature",
        config.creature_script, endpoint, area_identity)
    local container_live = refresh_target(container, "container",
        config.container_script, endpoint, area_identity)
    local pile_live = refresh_target(pile, "pile", nil,
        endpoint, area_identity)
    local full_live = refresh_target(full_creature, "creature",
        config.full_script, endpoint, area_identity)
    local last_live = refresh_target(last_slot, "creature",
        config.last_slot_script, endpoint, area_identity)
    if not creature_live or not container_live or not pile_live
        or not full_live or not last_live then
        return terminal_failure(state, "T01")
    end

    local initial_creature = matching_count(creature_live, "creature",
        config.items.creature)
    local initial_container = matching_count(container_live, "container",
        config.items.container)
    local initial_pile = matching_count(pile_live, "pile", config.items.pile)
    local initial_full = matching_count(full_live, "creature",
        config.items.creature)
    if initial_creature ~= 0 or initial_container ~= 0 or initial_pile ~= 0
        or initial_full ~= 0 then
        return terminal_failure(state, "T01")
    end
    state.initial_counts = {
        creature = initial_creature,
        container = initial_container,
        pile = initial_pile,
    }
    state.full_guard_baseline = initial_full

    local full_before_actions = action_snapshot(full_live)
    local before_attempts = state.instant_attempts
    local before_parsed = state.parsed_actions
    local accepted, reason = execute_transport(state, full_live, "creature",
        config.full_script, endpoint, area_identity,
        config.items.creature, "T05")
    local full_after = refresh_target(full_live, "creature",
        config.full_script, endpoint, area_identity)
    state.full_rejection_observed = accepted == false and reason == "capacity"
        and state.instant_attempts == before_attempts
        and state.parsed_actions == before_parsed
        and inventory_full(last_live) == false
        and inventory_full(full_live) == true
        and full_after ~= nil
        and actions_unchanged(full_before_actions, action_snapshot(full_after))
        and matching_count(full_after, "creature", config.items.creature)
            == state.full_guard_baseline
    if not state.full_rejection_observed then
        return terminal_failure(state, "T05")
    end

    state.checks.T01 = "P"
    local container_ok = execute_transport(state, container_live, "container",
        config.container_script, endpoint, area_identity,
        config.items.container, "T02")
    if not container_ok then
        return terminal_failure(state, "T02")
    end
    local creature_ok = execute_transport(state, creature_live, "creature",
        config.creature_script, endpoint, area_identity,
        config.items.creature, "T03")
    if not creature_ok then
        return terminal_failure(state, "T03")
    end
    local pile_ok = execute_transport(state, pile_live, "pile", nil,
        endpoint, area_identity, config.items.pile, "T04")
    if not pile_ok then
        return terminal_failure(state, "T04")
    end

    state.checks.T05 = state.full_rejection_observed
        and full_guard_ok(state, full_creature, last_slot, config,
            endpoint, area_identity) and "P" or "F"
    state.checks.T06 = state.instant_attempts == 3
        and state.parsed_actions == 3
        and "P" or "F"
    state.checks.T07 = state.unchanged_actions == 3 and "P" or "F"
    if state.checks.T05 ~= "P" or state.checks.T06 ~= "P"
        or state.checks.T07 ~= "P" then
        state.phase = "failed"
        return sanitized_result(state)
    end

    state.phase = "settle"
    return sanitized_result(state)
end

if not full_guard_ok(state, full_creature, last_slot, config,
    endpoint, area_identity) then
    return terminal_failure(state, "T05")
end

if state.phase == "settle" then
    local creature_live = refresh_target(creature, "creature",
        config.creature_script, endpoint, area_identity)
    local container_live = refresh_target(container, "container",
        config.container_script, endpoint, area_identity)
    local pile_live = refresh_target(pile, "pile", nil,
        endpoint, area_identity)
    local creature_count = creature_live and matching_count(creature_live,
        "creature", config.items.creature) or nil
    local container_count = container_live and matching_count(container_live,
        "container", config.items.container) or nil
    local pile_count = pile_live and matching_count(pile_live, "pile",
        config.items.pile) or nil
    state.checks.T02 = container_count == state.initial_counts.container + 1
        and "P" or "F"
    state.checks.T03 = creature_count == state.initial_counts.creature + 1
        and "P" or "F"
    state.checks.T04 = pile_count == state.initial_counts.pile + 1
        and "P" or "F"
    state.exact_deltas = (state.checks.T02 == "P" and 1 or 0)
        + (state.checks.T03 == "P" and 1 or 0)
        + (state.checks.T04 == "P" and 1 or 0)
    state.exact_transports = state.exact_deltas
    local stable = state.exact_deltas == 3
        and state.instant_attempts == 3
        and state.exact_transports == 3
        and state.unchanged_actions == 3
    state.checks.T08 = stable and "P" or "F"
    state.phase = stable and "done" or "failed"
    return sanitized_result(state)
end

return sanitized_result(state)
