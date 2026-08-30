-- Manifest-driven Item Randomiser delivery bootstrap for EEex v0.11.
--
-- Listener registrations are process-lifetime append-only.  Their callbacks
-- are deliberately thin trampolines into this root namespace so Infinity_DoFile
-- hot reloads replace behavior without stacking listeners or retaining stale
-- controller code.

FLDLV = FLDLV or {}

local root = FLDLV
local unpack_values = unpack or table.unpack

root.Diagnostics = root.Diagnostics or { counts = {} }
root.Diagnostics.counts = root.Diagnostics.counts or {}

-- Publish a disabled surface before probing or loading anything.  If a hot
-- reload fails partway through, append-only listener trampolines must not keep
-- dispatching into the previous controller.
root.Disabled = "INITIALIZING"
root.Controller = nil
root.Engine = nil
root.Manifest = nil
root.Core = nil
root["_ack" .. "Callbacks"] = nil
root["_generation"] = nil
root._OnSpriteLoaded = nil
root._OnGameStateDestroyed = nil
root._OnMenusLoaded = nil
root._OnWorldOpen = nil
root["_Delivery" .. "Ack"] = nil
root._MenuPoll = function()
    return true
end

local REQUIRED_CAPABILITIES = {
    "Infinity_DoFile",
    "Infinity_GetClockTicks",
    "Infinity_IsMenuOnStack",
    "Infinity_PushMenu",
    "EEex_Action_ExecuteScriptFileResponseAsAIBaseInstantly",
    "EEex_Action_ParseResponseString",
    "EEex_Area_GetVisible",
    "EEex_GameObject_CastUserType",
    "EEex_GameObject_Get",
    "EEex_GameObject_IsSprite",
    "EEex_GameState_AddDestroyedListener",
    "EEex_GameState_GetGlobalInt",
    "EEex_GameState_SetGlobalInt",
    "EEex_Menu_AddAfterMainFileLoadedListener",
    "EEex_Menu_Find",
    "EEex_Menu_GetItemFunction",
    "EEex_Menu_LoadFile",
    "EEex_Menu_SetItemFunction",
    "EEex_Resource_Fetch",
    "EEex_Sprite_AddLoadedListener",
    "EEex_Trigger_EvalConditionalStringAsAIBase",
    "EEex_Utility_IterateCPtrList",
}

root.RequiredCapabilities = REQUIRED_CAPABILITIES

local function capabilities_available()
    if EEex_Active ~= true then
        return false
    end
    for _, name in ipairs(REQUIRED_CAPABILITIES) do
        if type(rawget(_G, name)) ~= "function" then
            return false
        end
    end
    return true
end

if not capabilities_available() then
    root.Disabled = "CAPABILITY"
    return
end

local dependencies_ok = pcall(function()
    Infinity_DoFile("FLDLVMan")
    Infinity_DoFile("FLDLVCor")
end)
if not dependencies_ok or type(root.Manifest) ~= "table" or
    type(root.Core) ~= "table" or type(root.Core.new) ~= "function" then
    root.Disabled = "LOAD"
    return
end

root.Constants = {
    DEBOUNCE_TICKS = 250,
    SETTLE_TICKS = 100,
    POLL_TICKS = 500,
}

local LIVE_OBJECT_LISTS = {
    "m_lVertSort",
    "m_lVertSortBack",
    "m_lVertSortFlight",
    "m_lVertSortUnder",
}

local function pack_values(...)
    return { n = select("#", ...), ... }
end

local function is_integer(value)
    return type(value) == "number" and value == math.floor(value)
end

local function normalized_text(value)
    if type(value) ~= "string" then
        return nil
    end
    return string.lower((string.gsub(value, "%s+", "")))
end

local function same_text(first, second)
    local normalized_first = normalized_text(first)
    return normalized_first ~= nil and normalized_first == normalized_text(second)
end

local function read_resref(field)
    if not field or type(field.get) ~= "function" then
        return nil
    end
    local value = field:get()
    if type(value) ~= "string" or #value < 1 then
        return nil
    end
    return value
end

local function opaque_code(code)
    code = tostring(code or "ENGINE_ERROR")
    if not string.match(code, "^[A-Z0-9_]+$") then
        return "ENGINE_ERROR"
    end
    return code
end

local Engine = {}
Engine.__index = Engine

function Engine.new(manifest)
    return setmetatable({ manifest = manifest }, Engine)
end

function Engine:get_global(name)
    return EEex_GameState_GetGlobalInt(name)
end

function Engine:set_global(name, value)
    EEex_GameState_SetGlobalInt(name, value)
end

function Engine:get_current_area()
    local area = EEex_Area_GetVisible()
    if not area then
        return nil
    end
    return read_resref(area.m_resref)
end

function Engine:is_item_available(resref)
    if type(resref) ~= "string" or #resref < 1 or #resref > 8 then
        return false
    end
    return EEex_Resource_Fetch(resref, "ITM") ~= nil
end

function Engine:choose_variant(_, candidate_count)
    assert(is_integer(candidate_count) and candidate_count > 0,
        "variant candidate count must be a positive integer")
    return math.random(candidate_count)
end

function Engine:report_error(code, _)
    local safe_code = opaque_code(code)
    local counts = root.Diagnostics.counts
    counts[safe_code] = (counts[safe_code] or 0) + 1
end

function Engine:_walk_visible_objects(visitor)
    local area = EEex_Area_GetVisible()
    if not area then
        return nil
    end
    local area_resref = read_resref(area.m_resref)
    assert(area_resref, "visible area has no valid resref")
    local seen = {}
    for _, field in ipairs(LIVE_OBJECT_LISTS) do
        local list = area[field]
        assert(list, "visible area is missing a live object list")
        EEex_Utility_IterateCPtrList(list, function(object_id)
            assert(is_integer(object_id), "live object list yielded a non-integer id")
            if not seen[object_id] then
                seen[object_id] = true
                local object = EEex_GameObject_Get(object_id)
                if object then
                    object = EEex_GameObject_CastUserType(object)
                    if object then
                        visitor(object)
                    end
                end
            end
        end)
    end
    return area_resref
end

function Engine:validate_visible_surface()
    return self:_walk_visible_objects(function() end)
end

local function object_script_name(object)
    return object and read_resref(object.m_scriptName) or nil
end

local function object_is_nonsprite(object)
    return EEex_GameObject_IsSprite(object, true) == false
end

local function object_has_item_list(object)
    return object and object.m_lstItems ~= nil
end

local function object_matches_endpoint(object, endpoint)
    if endpoint.target_kind == "creature" then
        return EEex_GameObject_IsSprite(object, true) == true and
            same_text(object_script_name(object), endpoint.target_identity)
    end
    if endpoint.target_kind == "container" then
        return object_is_nonsprite(object) and object_has_item_list(object) and
            same_text(object_script_name(object), endpoint.target_identity)
    end
    if endpoint.target_kind == "ground" then
        return object_is_nonsprite(object) and object_has_item_list(object) and
            object.m_containerType == 4 and object.m_pos ~= nil and
            object.m_pos.x == endpoint.x and object.m_pos.y == endpoint.y
    end
    return false
end

function Engine:_resolve_endpoint(endpoint)
    local found
    local matches = 0
    local area_resref = self:_walk_visible_objects(function(object)
        if object_matches_endpoint(object, endpoint) then
            matches = matches + 1
            found = object
        end
    end)
    if not area_resref or not same_text(area_resref, endpoint.area) then
        return nil, 0
    end
    if matches ~= 1 then
        return nil, matches
    end
    return found, 1
end

local function unavailable_observation(endpoint_id, endpoint, reason)
    return {
        endpoint_id = endpoint_id,
        target_kind = endpoint.target_kind,
        observable = false,
        eligible = false,
        settled = (root._settlingSweeps or 0) >= 1,
        verifiable = false,
        reason = reason,
    }
end

function Engine:observe_endpoint(endpoint_id, endpoint)
    if type(endpoint_id) ~= "string" or type(endpoint) ~= "table" then
        return unavailable_observation(tostring(endpoint_id), {}, "INVALID")
    end
    local object, matches = self:_resolve_endpoint(endpoint)
    if not object then
        return unavailable_observation(endpoint_id, endpoint,
            matches > 1 and "AMBIGUOUS" or "MISSING")
    end

    if endpoint.target_kind == "creature" then
        local alive = EEex_GameObject_IsSprite(object, false)
        assert(type(alive) == "boolean", "sprite liveness did not return a boolean")
        local full = false
        if alive then
            full = EEex_Trigger_EvalConditionalStringAsAIBase(
                "InventoryFull(Myself)", object)
            assert(type(full) == "boolean", "InventoryFull did not return a boolean")
        end
        return {
            endpoint_id = endpoint_id,
            target_kind = endpoint.target_kind,
            observable = true,
            eligible = alive and full == false,
            settled = (root._settlingSweeps or 0) >= 1,
            verifiable = alive,
            reason = not alive and "DEAD" or (full and "FULL" or "READY"),
        }
    end

    return {
        endpoint_id = endpoint_id,
        target_kind = endpoint.target_kind,
        observable = true,
        eligible = true,
        settled = (root._settlingSweeps or 0) >= 1,
        verifiable = true,
        reason = "READY",
    }
end

local function item_signature(item)
    if not item then
        return nil
    end
    local resref = read_resref(item.cResRef)
    local charge1 = item.m_useCount1
    local charge2 = item.m_useCount2
    local charge3 = item.m_useCount3
    if not resref or not is_integer(charge1) or not is_integer(charge2) or
        not is_integer(charge3) then
        error("live item has an invalid signature")
    end
    return {
        resref = resref,
        charges = { charge1, charge2, charge3 },
    }
end

function Engine:list_items(observation)
    assert(type(observation) == "table" and observation.observable == true,
        "cannot list an unobservable endpoint")
    local endpoint = self.manifest.endpoints_by_id[observation.endpoint_id]
    assert(endpoint and endpoint.target_kind == observation.target_kind,
        "observation no longer matches the manifest")
    local object, matches = self:_resolve_endpoint(endpoint)
    assert(object and matches == 1, "endpoint changed before item observation")

    local items = {}
    local function append(item)
        local signature = item_signature(item)
        if signature then
            items[#items + 1] = signature
        end
    end
    if endpoint.target_kind == "creature" then
        assert(object.m_equipment and object.m_equipment.m_items,
            "creature inventory is unavailable")
        for slot = 0, 38 do
            append(object.m_equipment.m_items:get(slot))
        end
    else
        assert(object.m_lstItems, "container item list is unavailable")
        EEex_Utility_IterateCPtrList(object.m_lstItems, append)
    end
    return items
end

local function valid_delivery_item(item)
    if type(item) ~= "table" or type(item.resref) ~= "string" or
        #item.resref < 1 or #item.resref > 8 or
        not string.match(item.resref, "^[A-Za-z0-9#_-]+$") or
        item.quantity ~= 1 or type(item.charges) ~= "table" then
        return false
    end
    for index = 1, 3 do
        local charge = item.charges[index]
        if not is_integer(charge) or charge < 0 or charge > 65535 then
            return false
        end
    end
    return true
end

local function parsed_action_count(parsed)
    if not parsed or not parsed.m_curResponse or
        not parsed.m_curResponse.m_actionList then
        return nil
    end
    local count = 0
    EEex_Utility_IterateCPtrList(parsed.m_curResponse.m_actionList, function()
        count = count + 1
    end)
    return count
end

function Engine:execute_delivery(endpoint_id, _, item)
    local endpoint = self.manifest.endpoints_by_id[endpoint_id]
    if not endpoint or endpoint.external_delivery ~= 0 or
        not valid_delivery_item(item) then
        return false
    end

    local object, matches = self:_resolve_endpoint(endpoint)
    if not object or matches ~= 1 then
        return false
    end
    if endpoint.target_kind == "creature" then
        if EEex_GameObject_IsSprite(object, false) ~= true then
            return false
        end
        if EEex_Trigger_EvalConditionalStringAsAIBase(
                "InventoryFull(Myself)", object) ~= false then
            return false
        end
    elseif endpoint.target_kind ~= "container" and endpoint.target_kind ~= "ground" then
        return false
    end

    local charges = item.charges
    local transport
    if endpoint.target_kind == "creature" then
        transport = string.format('GiveItemCreate("%s",Myself,%d,%d,%d)',
            item.resref, charges[1], charges[2], charges[3])
    else
        transport = string.format('CreateItem("%s",%d,%d,%d)',
            item.resref, charges[1], charges[2], charges[3])
    end
    local parsed = EEex_Action_ParseResponseString(transport)
    if not parsed then
        return false
    end

    local freed = false
    local function free_parsed()
        if not freed then
            freed = true
            parsed:free()
        end
    end

    local count_ok, action_count = pcall(parsed_action_count, parsed)
    if not count_ok then
        local free_ok, free_error = pcall(free_parsed)
        if not free_ok then
            error(free_error, 0)
        end
        error(action_count, 0)
    end
    if action_count ~= 1 then
        free_parsed()
        return false
    end

    local execute_ok, execute_error = pcall(
        EEex_Action_ExecuteScriptFileResponseAsAIBaseInstantly, parsed, object)
    local free_ok, free_error = pcall(free_parsed)
    if not execute_ok then
        error(execute_error, 0)
    end
    if not free_ok then
        error(free_error, 0)
    end

    -- The v0.11 instant executor returns nil.  Reaching this point without an
    -- exception means the one INSTANT.IDS action finished synchronously; the
    -- core still verifies the exact inventory delta before committing.
    return true
end

root.Engine = Engine.new(root.Manifest)
root.Disabled = nil
root._boundaryReported = root._boundaryReported or {}
root._dirty = true
root._dirtyTick = Infinity_GetClockTicks()
root._lastPollTick = nil
root._currentArea = nil
root._settlingSweeps = 0
root._menuPushed = root._menuPushed == true

local controller, controller_error = root.Core.new(root.Engine, root.Manifest)
if not controller then
    root.Disabled = opaque_code(controller_error)
    return
end
root.Controller = controller

function root._Boundary(label, callback, ...)
    local arguments = pack_values(...)
    local results
    local ok, failure = xpcall(function()
        results = pack_values(callback(unpack_values(arguments, 1, arguments.n)))
    end, debug.traceback)
    if not ok then
        label = tostring(label or "callback")
        if not root._boundaryReported[label] then
            root._boundaryReported[label] = true
            root.Engine:report_error("CALLBACK_ERROR", failure)
        end
        return nil
    end
    return unpack_values(results, 1, results.n)
end

local function mark_dirty()
    root._dirty = true
    root._dirtyTick = Infinity_GetClockTicks()
    root._settlingSweeps = 0
end

function root._OnSpriteLoaded(_)
    -- Sprite-loaded is per-object and can fire before conjured fields settle.
    -- Never classify or retain its userdata here; merely debounce a later scan.
    mark_dirty()
end

function root._OnGameStateDestroyed()
    root._currentArea = nil
    root._settlingSweeps = 0
    root._dirty = true
    root._dirtyTick = Infinity_GetClockTicks()
    root._lastPollTick = nil
    root._menuPushed = false
    root.Controller:on_game_state_destroyed()
end

function root._OnWorldOpen(previous, ...)
    local results = { n = 0 }
    if previous then
        results = pack_values(previous(...))
    end
    if not Infinity_IsMenuOnStack("FLDLV_POLL") then
        Infinity_PushMenu("FLDLV_POLL")
        root._menuPushed = true
    end
    return unpack_values(results, 1, results.n)
end

local function make_world_open_wrapper(previous)
    return function(...)
        -- The wrapper belongs to the live UI.MENU object and must remain stable
        -- across Lua reloads.  Resolve all mod behavior through the root each
        -- time so it cannot retain a stale implementation closure.
        local namespace = FLDLV
        local handler = namespace and namespace._OnWorldOpen
        local boundary = namespace and namespace._Boundary
        if type(handler) == "function" and type(boundary) == "function" then
            return boundary("world_open", handler, previous, ...)
        end
        if previous then
            return previous(...)
        end
    end
end

function root._OnMenusLoaded()
    local menu = EEex_Menu_Find("WORLD_ACTIONBAR")
    if not menu or not menu.reference_onOpen then
        return
    end
    local current_open = EEex_Menu_GetItemFunction(menu.reference_onOpen)
    if current_open == root._activeOpenWrapper then
        return
    end
    EEex_Menu_LoadFile("FLDLV")
    -- A different open callback means UI.MENU was rebuilt (for example F5),
    -- so the old pushed-state belongs to the discarded menu stack.  Repeated
    -- notifications for the same live menu must preserve it.
    root._menuPushed = false
    local open_wrapper = make_world_open_wrapper(current_open)
    EEex_Menu_SetItemFunction(menu.reference_onOpen, open_wrapper)
    root._activeOpenWrapper = open_wrapper
end

local function elapsed(now, before)
    if before == nil then
        return math.huge
    end
    local difference = now - before
    if difference < 0 then
        return math.huge
    end
    return difference
end

function root._Poll()
    local area_identity = root.Engine:get_current_area()
    if not area_identity then
        root._currentArea = nil
        root._settlingSweeps = 0
        root._lastPollTick = nil
        return
    end

    local now = Infinity_GetClockTicks()
    if not same_text(area_identity, root._currentArea) then
        root._currentArea = area_identity
        root._settlingSweeps = 0
        root._dirty = true
        root._dirtyTick = now
    end

    if root._dirty then
        if elapsed(now, root._dirtyTick) < root.Constants.DEBOUNCE_TICKS then
            return
        end
    else
        local interval = root._settlingSweeps < 2 and
            root.Constants.SETTLE_TICKS or root.Constants.POLL_TICKS
        if elapsed(now, root._lastPollTick) < interval then
            return
        end
    end

    local swept_area = root.Engine:validate_visible_surface()
    if not swept_area or not same_text(swept_area, root._currentArea) then
        mark_dirty()
        return
    end
    root.Controller:poll()
    root._dirty = false
    root._lastPollTick = now
    if root._settlingSweeps < 2 then
        root._settlingSweeps = root._settlingSweeps + 1
    end
end

function root._MenuPoll()
    root._Boundary("poll", root._Poll)
    return true
end

if not root._listenersRegistered then
    root._listenersRegistered = true
    EEex_Sprite_AddLoadedListener(function(...)
        local handler = FLDLV and FLDLV._OnSpriteLoaded
        if handler then
            return FLDLV._Boundary("sprite_loaded", handler, ...)
        end
    end)
    EEex_GameState_AddDestroyedListener(function(...)
        local handler = FLDLV and FLDLV._OnGameStateDestroyed
        if handler then
            return FLDLV._Boundary("game_state_destroyed", handler, ...)
        end
    end)
    EEex_Menu_AddAfterMainFileLoadedListener(function(...)
        local handler = FLDLV and FLDLV._OnMenusLoaded
        if handler then
            return FLDLV._Boundary("menus_loaded", handler, ...)
        end
    end)
end
