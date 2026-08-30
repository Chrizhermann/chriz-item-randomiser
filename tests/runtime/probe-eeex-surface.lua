-- Read-only EEex surface probe for the disposable EET fixture.
--
-- The fixture publishes only synthetic identities in FLDLVProbe.config.  This
-- probe returns check codes and counts only.  The authenticated pile position
-- is retained solely in-process for the transport probe; it is never returned.

FLDLVProbe = FLDLVProbe or {}
FLDLVProbe.results = FLDLVProbe.results or {}

local LIVE_OBJECT_LISTS = {
    "m_lVertSort",
    "m_lVertSortBack",
    "m_lVertSortFlight",
    "m_lVertSortUnder",
}

local result = {
    schema = "flir-eeex-probe-v1",
    probe = "surface",
    phase = "complete",
    passed = 0,
    failed = 0,
    skipped = 0,
    pending = 0,
    checks = {},
}

local function record(code, status)
    result.checks[#result.checks + 1] = code .. ":" .. status
    if status == "P" then
        result.passed = result.passed + 1
    else
        result.failed = result.failed + 1
        result.phase = "failed"
    end
end

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
        local number = value[index]
        if type(number) ~= "number" or number ~= math.floor(number)
            or number < 0 or number > 65535 then
            return false
        end
    end
    return true
end

local ITEM_KEYS = { "creature", "container", "pile", "filler" }

local function validate_config(config)
    if type(config) ~= "table" or type(config.items) ~= "table"
        or type(config.item_resrefs) ~= "table"
        or type(config.charge_triples) ~= "table"
        or type(config.ability_maxima) ~= "table" then
        return false
    end

    local identity_values = {
        config.area_resref,
        config.creature_script,
        config.duplicate_script,
        config.last_slot_script,
        config.full_script,
        config.container_script,
        config.pile_script,
    }
    local seen_targets = {}
    for _, identity in ipairs(identity_values) do
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

        if key == "filler" then
            if maxima[1] ~= 0 or maxima[2] ~= 0 or maxima[3] ~= 0
                or charges[1] ~= 0 or charges[2] ~= 0
                or charges[3] ~= 0 then
                return false
            end
        else
            local item = config.items[key]
            if type(item) ~= "table"
                or normalized_identity(item.resref) ~= normalized
                or not valid_triplet(item.charges) then
                return false
            end
            for index = 1, 3 do
                if item.charges[index] ~= charges[index]
                    or charges[index] > maxima[index] then
                    return false
                end
            end
        end
    end
    return true
end

local required_functions = {
    "EEex_Action_ExecuteScriptFileResponseAsAIBaseInstantly",
    "EEex_Action_ParseResponseString",
    "EEex_Area_GetVisible",
    "EEex_GameObject_CastUserType",
    "EEex_GameObject_Get",
    "EEex_GameObject_IsSprite",
    "EEex_GameState_GetGlobalInt",
    "EEex_GameState_SetGlobalInt",
    "EEex_Resource_Demand",
    "EEex_Resource_Fetch",
    "EEex_Trigger_EvalConditionalStringAsAIBase",
    "EEex_Utility_IterateCPtrList",
}

local functions_ok = type(EEex_Active) == "boolean" and EEex_Active
for _, name in ipairs(required_functions) do
    if type(rawget(_G, name)) ~= "function" then
        functions_ok = false
    end
end
record("S01", functions_ok and "P" or "F")

local config = FLDLVProbe.config
local config_ok = validate_config(config)

local area
local area_identity
local area_ok = false
if functions_ok and config_ok then
    local values = safe(EEex_Area_GetVisible)
    area = values and values[1] or nil
    if area and area.m_lVertSort and area.m_resref then
        local resref_values = safe(function()
            return area.m_resref:get()
        end)
        area_identity = resref_values and resref_values[1] or nil
        area_ok = valid_identity(area_identity)
            and normalized_identity(area_identity)
                == normalized_identity(config.area_resref)
    end
end
record("S02", area_ok and "P" or "F")

local objects = {}
local object_surface_ok = area_ok
if area_ok then
    local iteration = safe(function()
        local seen = {}
        for _, field in ipairs(LIVE_OBJECT_LISTS) do
            local list = area[field]
            if not list then
                error("missing live object list")
            end
            EEex_Utility_IterateCPtrList(list, function(object_id)
                if type(object_id) ~= "number"
                    or object_id ~= math.floor(object_id) then
                    error("non-numeric object id")
                end
                if not seen[object_id] then
                    local raw = EEex_GameObject_Get(object_id)
                    local cast = raw and EEex_GameObject_CastUserType(raw) or nil
                    if not cast then
                        error("object retrieval or cast failed")
                    end
                    seen[object_id] = true
                    objects[#objects + 1] = { id = object_id, object = cast }
                end
            end)
        end
    end)
    object_surface_ok = iteration ~= nil and #objects > 0
    if object_surface_ok then
        for _, entry in ipairs(objects) do
            local live_values = safe(function()
                local raw = EEex_GameObject_Get(entry.id)
                return raw and EEex_GameObject_CastUserType(raw) or nil
            end)
            if not live_values or not live_values[1] then
                object_surface_ok = false
                break
            end
        end
    end
end
record("S03", object_surface_ok and "P" or "F")

local function read_script_name(object)
    if not object or not object.m_scriptName then
        return nil
    end
    local values = safe(function()
        return object.m_scriptName:get()
    end)
    return values and values[1] or nil
end

local function resolve_unique(script_name)
    if not valid_identity(script_name) then
        return nil, 0
    end
    local wanted = normalized_identity(script_name)
    local found
    local count = 0
    for _, entry in ipairs(objects) do
        if normalized_identity(read_script_name(entry.object)) == wanted then
            count = count + 1
            found = entry
        end
    end
    if count ~= 1 then
        return nil, count
    end
    return found, count
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

local creature
local last_slot
local full_creature
local container
local pile
local identity_ok = config_ok and object_surface_ok
if identity_ok then
    creature = resolve_unique(config.creature_script)
    last_slot = resolve_unique(config.last_slot_script)
    full_creature = resolve_unique(config.full_script)
    container = resolve_unique(config.container_script)
    -- Fixture-only authentication: transport never resolves this script name.
    pile = resolve_unique(config.pile_script)
    local duplicate, duplicate_count = resolve_unique(config.duplicate_script)
    local pile_x, pile_y = read_position(pile)
    identity_ok = creature ~= nil and last_slot ~= nil
        and full_creature ~= nil and container ~= nil and pile ~= nil
        and duplicate == nil and duplicate_count == 2
        and sprite_status(creature) == true
        and sprite_status(last_slot) == true
        and sprite_status(full_creature) == true
        and sprite_status(container) == false
        and sprite_status(pile) == false
        and container.object.m_lstItems ~= nil
        and pile.object.m_lstItems ~= nil
        and read_container_type(container) == 8
        and read_container_type(pile) == 4
        and pile_x ~= nil and pile_y ~= nil
    if identity_ok then
        FLDLVProbe.ground_endpoint = {
            area_resref = normalized_identity(area_identity),
            x = pile_x,
            y = pile_y,
            container_type = 4,
        }
    else
        FLDLVProbe.ground_endpoint = nil
    end
else
    FLDLVProbe.ground_endpoint = nil
end
record("S04", identity_ok and "P" or "F")

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
    return {
        resref = values[1],
        charges = { values[2], values[3], values[4] },
    }
end

local function list_container_items(entry)
    local items = {}
    if not entry or not entry.object or not entry.object.m_lstItems then
        return nil
    end
    local values = safe(function()
        EEex_Utility_IterateCPtrList(entry.object.m_lstItems, function(item)
            local signature = item_signature(item)
            if not signature then
                error("bad container item")
            end
            items[#items + 1] = signature
        end)
    end)
    return values and items or nil
end

local function contains_filler(items)
    if type(items) ~= "table" then
        return false
    end
    for _, item in ipairs(items) do
        if normalized_identity(item.resref)
            == normalized_identity(config.item_resrefs.filler) then
            return item.charges[1] == config.charge_triples.filler[1]
                and item.charges[2] == config.charge_triples.filler[2]
                and item.charges[3] == config.charge_triples.filler[3]
        end
    end
    return false
end

local container_items = identity_ok and list_container_items(container) or nil
local pile_items = identity_ok and list_container_items(pile) or nil
local containers_ok = contains_filler(container_items)
    and contains_filler(pile_items)
record("S05", containers_ok and "P" or "F")

local function list_creature_items(entry)
    local items = {}
    if not entry or not entry.object or not entry.object.m_equipment
        or not entry.object.m_equipment.m_items then
        return nil
    end
    local values = safe(function()
        for slot = 0, 38 do
            local item = entry.object.m_equipment.m_items:get(slot)
            if item then
                local signature = item_signature(item)
                if not signature then
                    error("bad creature item")
                end
                items[#items + 1] = signature
            end
        end
    end)
    return values and items or nil
end

local function resource_available(key)
    local resref = config.item_resrefs[key]
    local fetched = safe(EEex_Resource_Fetch, resref, "ITM")
    if not fetched or not fetched[1] then
        return false
    end
    local demanded = safe(EEex_Resource_Demand, resref, "ITM")
    local header = demanded and demanded[1] or nil
    if not header or type(header.abilityCount) ~= "number" then
        return false
    end
    if key == "filler" then
        return header.abilityCount == 0
    end
    return header.abilityCount == 3
end

local creature_items = identity_ok and list_creature_items(creature) or nil
local last_slot_items = identity_ok and list_creature_items(last_slot) or nil
local full_items = identity_ok and list_creature_items(full_creature) or nil
local last_values = identity_ok and safe(
    EEex_Trigger_EvalConditionalStringAsAIBase,
    "InventoryFull(Myself)", last_slot.object) or nil
local full_values = identity_ok and safe(
    EEex_Trigger_EvalConditionalStringAsAIBase,
    "InventoryFull(Myself)", full_creature.object) or nil

local resources_ok = config_ok
if resources_ok then
    for _, key in ipairs(ITEM_KEYS) do
        if not resource_available(key) then
            resources_ok = false
            break
        end
    end
end

local parse_ok = false
if functions_ok and config_ok then
    local probe_item = config.items.container
    local parsed_values = safe(EEex_Action_ParseResponseString,
        string.format('CreateItem("%s",%d,%d,%d)', probe_item.resref,
            probe_item.charges[1], probe_item.charges[2], probe_item.charges[3]))
    local parsed = parsed_values and parsed_values[1] or nil
    if parsed and parsed.m_curResponse and parsed.m_curResponse.m_actionList then
        local action_count = 0
        local iterated = safe(function()
            EEex_Utility_IterateCPtrList(
                parsed.m_curResponse.m_actionList, function()
                    action_count = action_count + 1
                end)
        end)
        parse_ok = iterated ~= nil and action_count == 1
        safe(function()
            parsed:free()
        end)
    end
end

local inventory_ok = contains_filler(creature_items)
    and contains_filler(last_slot_items)
    and contains_filler(full_items)
    and last_values ~= nil and last_values[1] == false
    and full_values ~= nil and full_values[1] == true
    and resources_ok and parse_ok
record("S06", inventory_ok and "P" or "F")

table.sort(result.checks)
FLDLVProbe.results.surface = result
return result
