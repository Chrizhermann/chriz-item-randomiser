local FakeEngine = {}
FakeEngine.__index = FakeEngine

local function copy_array(source)
    local result = {}
    for index, value in ipairs(source or {}) do
        result[index] = value
    end
    return result
end

local function copy_item(item)
    return {
        resref = item.resref,
        charges = copy_array(item.charges),
    }
end

local function endpoint_state(overrides)
    local result = {
        present = true,
        observable = true,
        alive = true,
        full = false,
        settled = true,
        items = {},
    }
    for key, value in pairs(overrides or {}) do
        result[key] = value
    end
    return result
end

function FakeEngine.new(options)
    options = options or {}
    local self = setmetatable({}, FakeEngine)
    self.globals = options.globals or {}
    self.endpoints = options.endpoints or {
        primary = endpoint_state(),
        fallback = endpoint_state(),
        container = endpoint_state(),
        override = endpoint_state(),
    }
    self.executions = {}
    self.errors = {}
    self.events = {}
    self.current_area = options.current_area or "artest"
    self.missing_items = options.missing_items or {}
    self.execute_accept = options.execute_accept ~= false
    self.execute_create = options.execute_create ~= false
    self.execute_error = options.execute_error
    self.execute_error_after_create = options.execute_error_after_create == true
    self.execute_hook = options.execute_hook
    self.variant_choice = options.variant_choice or 1
    return self
end

function FakeEngine:choose_variant(token, candidate_count)
    assert(type(token) == "table", "variant token missing")
    assert(self.variant_choice >= 1 and self.variant_choice <= candidate_count,
        "fake variant choice outside candidate range")
    return self.variant_choice
end

function FakeEngine:get_global(name)
    self.events[#self.events + 1] = { kind = "get_global", name = name }
    return self.globals[name] or 0
end

function FakeEngine:set_global(name, value)
    assert(type(value) == "number" and value % 1 == 0, "GLOBAL value must be an integer")
    self.globals[name] = value
    self.events[#self.events + 1] = {
        kind = "set_global",
        name = name,
        value = value,
    }
end

function FakeEngine:get_current_area()
    return self.current_area
end

function FakeEngine:is_item_available(resref)
    return self.missing_items[string.lower(resref)] ~= true
end

function FakeEngine:observe_endpoint(endpoint_id, endpoint)
    local state = self.endpoints[endpoint_id]
    if state == nil or state.present == false or state.observable == false then
        return {
            endpoint_id = endpoint_id,
            observable = false,
            eligible = false,
            settled = state == nil or state.settled ~= false,
            verifiable = false,
            reason = (state == nil or state.present == false) and "missing" or "unobservable",
        }
    end

    local alive = state.alive ~= false
    local full = state.full == true
    local verifiable = state.verifiable
    if verifiable == nil then
        verifiable = endpoint.target_kind ~= "creature" or alive
    end
    return {
        endpoint_id = endpoint_id,
        observable = true,
        eligible = alive and not full,
        settled = state.settled ~= false,
        verifiable = verifiable,
        reason = not alive and "dead" or (full and "full" or "ready"),
        items = state.items,
        target_kind = endpoint.target_kind,
    }
end

function FakeEngine:list_items(observation)
    assert(observation.observable, "cannot list an unobservable endpoint")
    return observation.items
end

function FakeEngine:execute_delivery(endpoint_id, endpoint, item)
    local copied_item = copy_item(item)
    self.events[#self.events + 1] = {
        kind = "execute_delivery",
        endpoint_id = endpoint_id,
    }
    self.executions[#self.executions + 1] = {
        endpoint_id = endpoint_id,
        endpoint = endpoint,
        item = copied_item,
    }
    if self.execute_error and not self.execute_error_after_create then
        error(self.execute_error)
    end
    if self.execute_create then
        self:add_item(endpoint_id, copied_item.resref, copied_item.charges)
    end
    if self.execute_hook then
        self.execute_hook(self, endpoint_id, endpoint, copied_item)
    end
    if self.execute_error then
        error(self.execute_error)
    end
    return self.execute_accept
end

function FakeEngine:add_item(endpoint_id, resref, charges)
    local state = assert(self.endpoints[endpoint_id], "unknown fake endpoint: " .. endpoint_id)
    state.items[#state.items + 1] = {
        resref = resref,
        charges = copy_array(charges),
    }
end

function FakeEngine:count_exact(endpoint_id, resref, charges)
    local state = assert(self.endpoints[endpoint_id], "unknown fake endpoint: " .. endpoint_id)
    local count = 0
    for _, item in ipairs(state.items) do
        if string.lower(item.resref) == string.lower(resref) and
            item.charges[1] == charges[1] and item.charges[2] == charges[2] and
            item.charges[3] == charges[3] then
            count = count + 1
        end
    end
    return count
end

function FakeEngine:report_error(code, detail)
    self.errors[#self.errors + 1] = {
        code = code,
        detail = detail,
    }
    self.events[#self.events + 1] = {
        kind = "report_error",
        code = code,
        detail = detail,
    }
end

function FakeEngine.endpoint(overrides)
    return endpoint_state(overrides)
end

function FakeEngine.manifest()
    return {
        schema = "flir-delivery-manifest-v1",
        backend = "eeex-manifest-v1",
        fingerprint = { 101, 202, 303, 404 },
        tokens_by_global = {
            fl1t1 = {
                global = "fl1t1",
                unit_id = "test:unit-a",
                item_id = "test:item-a",
                tier = "1",
                compact_token = "1",
                item_resref = "tstitema",
                charges = { 3, 2, 1 },
                variant_policy = "exact",
                enabled = 1,
            },
            fl1t2 = {
                global = "fl1t2",
                unit_id = "test:unit-b",
                item_id = "test:item-b",
                tier = "1",
                compact_token = "2",
                item_resref = "tstitemb",
                charges = { 0, 4, 0 },
                variant_policy = "exact",
                enabled = 1,
            },
        },
        slots_by_tier_value = {
            ["1"] = {
                [1] = {
                    slot_id = "test:slot-a",
                    tier = "1",
                    slot_value = 1,
                    endpoint_id = "primary",
                    weight = 1,
                    progression_band = "test",
                    enabled = 1,
                },
                [2] = {
                    slot_id = "test:slot-b",
                    tier = "1",
                    slot_value = 2,
                    endpoint_id = "container",
                    weight = 1,
                    progression_band = "test",
                    enabled = 1,
                },
                [3] = {
                    slot_id = "test:slot-shared",
                    tier = "1",
                    slot_value = 3,
                    endpoint_id = "primary",
                    weight = 1,
                    progression_band = "test",
                    enabled = 1,
                },
            },
        },
        endpoints_by_id = {
            primary = {
                area = "artest",
                target_kind = "creature",
                target_identity = "test-primary",
                x = 0,
                y = 0,
                capacity = 4,
                static_policy = "runtime-resolve",
                fallback_id = "fallback",
                adapter = "-",
                external_delivery = 0,
                enabled = 1,
            },
            fallback = {
                area = "artest",
                target_kind = "ground",
                target_identity = "-",
                x = 10,
                y = 20,
                capacity = 8,
                static_policy = "authored-static",
                fallback_id = "-",
                adapter = "-",
                external_delivery = 0,
                enabled = 1,
            },
            container = {
                area = "artest",
                target_kind = "container",
                target_identity = "test-container",
                x = 0,
                y = 0,
                capacity = 2,
                static_policy = "runtime-resolve",
                fallback_id = "fallback",
                adapter = "-",
                external_delivery = 0,
                enabled = 1,
            },
            override = {
                area = "artest",
                target_kind = "ground",
                target_identity = "-",
                x = 30,
                y = 40,
                capacity = 2,
                static_policy = "authored-static",
                fallback_id = "-",
                adapter = "-",
                external_delivery = 0,
                enabled = 1,
            },
        },
        unit_overrides = {},
        sparse_overrides = {},
    }
end

return FakeEngine
