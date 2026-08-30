local FakeEEex = {}
FakeEEex.__index = FakeEEex

local LIVE_LISTS = {
    "m_lVertSort",
    "m_lVertSortBack",
    "m_lVertSortFlight",
    "m_lVertSortUnder",
}

local function copy_array(source)
    local result = {}
    for index, value in ipairs(source or {}) do
        result[index] = value
    end
    return result
end

local function item_wrapper(item)
    if not item then
        return nil
    end
    return {
        cResRef = {
            get = function()
                return item.resref
            end,
        },
        m_useCount1 = item.charges[1],
        m_useCount2 = item.charges[2],
        m_useCount3 = item.charges[3],
    }
end

local function named_list(name)
    return {
        _name = name,
        values = {},
    }
end

local function make_menu(on_open, on_close)
    return {
        reference_onOpen = { fn = on_open },
        reference_onClose = { fn = on_close },
    }
end

function FakeEEex.item(resref, charges)
    return {
        resref = resref,
        charges = copy_array(charges),
    }
end

function FakeEEex.new(repository_root, manifest, options)
    options = options or {}
    local self = setmetatable({}, FakeEEex)
    self.repository_root = repository_root
    self.manifest = manifest
    self.clock = options.clock or 0
    self.area = nil
    self.objects = {}
    self.next_object_id = 100
    self.globals = options.globals or {}
    self.resources = options.resources or {}
    self.listeners = {
        sprite_loaded = {},
        game_state_destroyed = {},
        menu_loaded = {},
    }
    self.do_files = {}
    self.loaded_menus = {}
    self.menu_pushes = {}
    self.menu_pops = {}
    self.menu = make_menu(options.on_open, options.on_close)
    self.list_visits = {}
    self.fetch_count = 0
    self.fetches = {}
    self.slot_reads = {}
    self.parsed = {}
    self.parse_calls = 0
    self.free_count = 0
    self.queue_attempts = 0
    self.queued = {}
    self.queue_return = options.queue_return
    self.queue_error = options.queue_error
    self.next_action_count = options.action_count or 2

    local environment = {
        FLDLV = options.root_namespace or {},
    }
    setmetatable(environment, { __index = _G })
    environment._G = environment
    self.env = environment

    self:_install_globals(options.missing_capability)
    return self
end

function FakeEEex:_install_globals(missing_capability)
    local env = self.env
    local fake = self

    env.EEex_Active = true

    env.Infinity_DoFile = function(resref)
        fake.do_files[#fake.do_files + 1] = resref
        if resref == "FLDLVMan" then
            env.FLDLV.Manifest = fake.manifest
            return
        end
        if resref == "FLDLVCor" then
            local path = fake.repository_root .. "/copy/FLDLVCor.lua"
            local chunk, load_error = loadfile(path, "t", env)
            assert(chunk, load_error)
            return chunk()
        end
        error("unexpected Infinity_DoFile resref: " .. tostring(resref))
    end

    env.Infinity_GetClockTicks = function()
        return fake.clock
    end

    env.Infinity_PushMenu = function(name)
        fake.menu_pushes[#fake.menu_pushes + 1] = name
    end

    env.Infinity_PopMenu = function(name)
        fake.menu_pops[#fake.menu_pops + 1] = name
    end

    env.EEex_Action_ParseResponseString = function(source)
        fake.parse_calls = fake.parse_calls + 1
        local action_values = {}
        for index = 1, fake.next_action_count do
            action_values[index] = { index = index }
        end
        local parsed = {
            source = source,
            freed = false,
            m_curResponse = {
                m_actionList = {
                    _name = "parsed_actions",
                    values = action_values,
                },
            },
        }
        function parsed:free()
            assert(not self.freed, "parsed response freed twice")
            self.freed = true
            fake.free_count = fake.free_count + 1
        end
        fake.parsed[#fake.parsed + 1] = parsed
        return parsed
    end

    env.EEex_Action_QueueScriptFileResponseOnAIBase = function(parsed, object)
        fake.queue_attempts = fake.queue_attempts + 1
        fake.queued[#fake.queued + 1] = {
            parsed = parsed,
            object_id = object and object._source and object._source.id or nil,
            fetch_serial = object and object._fetch_serial or nil,
        }
        if fake.queue_error then
            error(fake.queue_error)
        end
        return fake.queue_return
    end

    env.EEex_Area_GetVisible = function()
        return fake.area
    end

    env.EEex_GameObject_Get = function(object_id)
        local source = fake.objects[object_id]
        if not source then
            return nil
        end
        fake.fetch_count = fake.fetch_count + 1
        fake.fetches[#fake.fetches + 1] = object_id
        local wrapper = {
            _source = source,
            _fetch_serial = fake.fetch_count,
            m_scriptName = {
                get = function()
                    return source.script_name
                end,
            },
            m_pos = {
                x = source.x or 0,
                y = source.y or 0,
            },
            m_containerType = source.container_type,
        }
        if source.kind == "creature" then
            wrapper.m_equipment = {
                m_items = {
                    get = function(_, slot)
                        fake.slot_reads[#fake.slot_reads + 1] = slot
                        return item_wrapper(source.slots[slot])
                    end,
                },
            }
        elseif source.has_items ~= false then
            wrapper.m_lstItems = {
                _name = "object_items",
                _items = true,
                values = source.items,
            }
        end
        return wrapper
    end

    env.EEex_GameObject_CastUserType = function(object)
        return object
    end

    env.EEex_GameObject_IsSprite = function(object, allow_dead)
        if not object or not object._source or object._source.kind ~= "creature" then
            return false
        end
        return allow_dead == true or object._source.alive ~= false
    end

    env.EEex_GameState_GetGlobalInt = function(name)
        return fake.globals[name] or 0
    end

    env.EEex_GameState_SetGlobalInt = function(name, value)
        fake.globals[name] = value
    end

    env.EEex_GameState_AddDestroyedListener = function(listener)
        fake.listeners.game_state_destroyed[#fake.listeners.game_state_destroyed + 1] = listener
    end

    env.EEex_Menu_AddAfterMainFileLoadedListener = function(listener)
        fake.listeners.menu_loaded[#fake.listeners.menu_loaded + 1] = listener
    end

    env.EEex_Menu_AddMainFileLoadedListener = function(listener)
        fake.listeners.menu_loaded[#fake.listeners.menu_loaded + 1] = listener
    end

    env.EEex_Menu_LoadFile = function(resref)
        fake.loaded_menus[#fake.loaded_menus + 1] = resref
    end

    env.EEex_Menu_Find = function(name)
        if name == "WORLD_ACTIONBAR" then
            return fake.menu
        end
        return nil
    end

    env.EEex_Menu_GetItemFunction = function(reference)
        return reference and reference.fn or nil
    end

    env.EEex_Menu_SetItemFunction = function(reference, callback)
        reference.fn = callback
    end

    env.EEex_Resource_Fetch = function(resref, extension)
        assert(extension == "ITM", "unexpected fake resource extension")
        if fake.resources[string.lower(resref)] == false then
            return nil
        end
        return { resref = resref, extension = extension }
    end

    env.EEex_Sprite_AddLoadedListener = function(listener)
        fake.listeners.sprite_loaded[#fake.listeners.sprite_loaded + 1] = listener
    end

    env.EEex_Trigger_EvalConditionalStringAsAIBase = function(source, object)
        assert(source == "InventoryFull(Myself)", "unexpected fake trigger")
        local full = object and object._source and object._source.full
        if full == nil then
            return false
        end
        return full
    end

    env.EEex_Utility_IterateCPtrList = function(list, callback)
        assert(type(list) == "table" and type(list.values) == "table",
            "fake pointer list missing values")
        if list._name then
            fake.list_visits[list._name] = (fake.list_visits[list._name] or 0) + 1
        end
        for _, value in ipairs(list.values) do
            local presented = list._items and item_wrapper(value) or value
            if callback(presented) then
                break
            end
        end
    end

    if missing_capability then
        env[missing_capability] = nil
    end
end

function FakeEEex:load_bootstrap()
    local path = self.repository_root .. "/copy/M_FLDLV.lua"
    local chunk, load_error = loadfile(path, "t", self.env)
    assert(chunk, load_error)
    return chunk()
end

function FakeEEex:set_clock(value)
    self.clock = value
end

function FakeEEex:set_area(resref)
    if not resref then
        self.area = nil
        return
    end
    local area = {
        m_resref = {
            get = function()
                return resref
            end,
        },
    }
    for _, name in ipairs(LIVE_LISTS) do
        area[name] = named_list(name)
    end
    self.area = area
end

function FakeEEex:add_object(spec, list_name)
    assert(self.area, "fake area missing")
    list_name = list_name or "m_lVertSort"
    local list = assert(self.area[list_name], "unknown fake live list")
    local id = spec.id or self.next_object_id
    if not spec.id then
        self.next_object_id = self.next_object_id + 1
    end
    local source = {
        id = id,
        script_name = spec.script_name or "",
        kind = spec.kind or "container",
        alive = spec.alive ~= false,
        full = spec.full,
        x = spec.x or 0,
        y = spec.y or 0,
        container_type = spec.container_type,
        has_items = spec.has_items,
        items = spec.items or {},
        slots = spec.slots or {},
    }
    self.objects[id] = source
    list.values[#list.values + 1] = id
    return source
end

function FakeEEex:add_existing_id(object_id, list_name)
    local list = assert(self.area and self.area[list_name], "unknown fake live list")
    list.values[#list.values + 1] = object_id
end

function FakeEEex:remove_id(object_id)
    self.objects[object_id] = nil
    if not self.area then
        return
    end
    for _, name in ipairs(LIVE_LISTS) do
        local values = self.area[name].values
        for index = #values, 1, -1 do
            if values[index] == object_id then
                table.remove(values, index)
            end
        end
    end
end

function FakeEEex:rebuild_world_menu(on_open, on_close)
    self.menu = make_menu(on_open, on_close)
    return self.menu
end

function FakeEEex:trigger_menu_loaded(...)
    for _, listener in ipairs(self.listeners.menu_loaded) do
        listener(...)
    end
end

function FakeEEex:trigger_sprite_loaded(...)
    for _, listener in ipairs(self.listeners.sprite_loaded) do
        listener(...)
    end
end

function FakeEEex:trigger_destroyed(...)
    for _, listener in ipairs(self.listeners.game_state_destroyed) do
        listener(...)
    end
end

return FakeEEex
