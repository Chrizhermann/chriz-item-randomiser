-- Bounded native bridge for the disposable Mode 1 fixture, not arbitrary loot.
-- Unique creature sources; empty named type-4 ground piles; one unexpired,
-- nonstackable, droppable item per operation. No listeners or autoloading here.
local Native = {}
-- The ordinary transport consumes this message on an engine update. Allow a
-- conservative 20 game seconds (15 ticks/second), never unlimited settling.
-- Paused time does not consume this allowance; this is not a wall-clock timer.
Native.MAX_SETTLE_TICKS = 300
local Adapter = {}
Adapter.__index = Adapter
local lists = { "m_lVertSort", "m_lVertSortBack", "m_lVertSortFlight", "m_lVertSortUnder" }
local required = { "EEex_Area_GetVisible", "EEex_GameObject_Get", "EEex_GameObject_CastUserType",
    "EEex_GameObject_IsSprite", "EEex_Utility_IterateCPtrList", "EEex_UDToPtr",
    "EEex_Resource_Demand", "EEex_Action_ExecuteResponseStringOnAIBaseInstantly", "EEex_CastUD" }

local function integer(value, lo, hi)
    return type(value) == "number" and value == math.floor(value) and value >= lo and value <= hi
end
local function resref(value)
    return type(value) == "string" and #value >= 1 and #value <= 8 and
        not value:find("[^A-Za-z0-9#_-]") and value:lower() or nil
end
local function text(field)
    if not field or type(field.get) ~= "function" then return nil end
    local value = field:get()
    return type(value) == "string" and value:lower() or nil
end
local function hash(value)
    return type(value) == "string" and #value == 64 and not value:find("[^0-9a-f]")
end
local function payload(value)
    if type(value) ~= "table" or not resref(value.resref) or value.expiration ~= 0 or
        (value.flags ~= 0 and value.flags ~= 1) or type(value.charges) ~= "table" then return nil end
    local charges = {}
    for index = 1, 3 do
        if not integer(value.charges[index], 0, 65535) then return nil end
        charges[index] = value.charges[index]
    end
    for key in pairs(value.charges) do
        if not integer(key, 1, 3) then return nil end
    end
    return { resref = resref(value.resref), charges = charges, expiration = 0, flags = value.flags }
end
local function same(a, b)
    return a and b and a.resref == b.resref and a.expiration == b.expiration and a.flags == b.flags and
        a.charges[1] == b.charges[1] and a.charges[2] == b.charges[2] and a.charges[3] == b.charges[3]
end
local function pointer(api, object)
    local address = api.EEex_UDToPtr(object)
    assert(address ~= nil and address ~= 0, "NATIVE_POINTER_UNAVAILABLE")
    return tostring(address)
end

function Native.new(api, fixture)
    assert(type(api) == "table" and type(fixture) == "table", "NATIVE_CONFIG_INVALID")
    for _, name in ipairs(required) do assert(type(api[name]) == "function", name .. "_MISSING") end
    local self = setmetatable({ api = api, sources = {}, targets = {}, reservations = {},
        attempted = {}, tick = nil, pending = nil, last_error = nil }, Adapter)
    local identities, source_resrefs = {}, {}
    for _, kind in ipairs({ "sources", "targets" }) do
        assert(type(fixture[kind]) == "table", "NATIVE_CONFIG_INVALID")
        for id, endpoint in pairs(fixture[kind]) do
            assert(type(id) == "string" and type(endpoint) == "table" and resref(endpoint.area) and
                type(endpoint.script_name) == "string" and #endpoint.script_name > 0 and
                hash(endpoint.identity) and not identities[endpoint.identity], "NATIVE_ENDPOINT_INVALID")
            identities[endpoint.identity] = true
            local copy = { area = resref(endpoint.area), script_name = endpoint.script_name:lower(),
                identity = endpoint.identity }
            if kind == "sources" then
                copy.payload = assert(payload(endpoint.payload), "NATIVE_PAYLOAD_UNSUPPORTED")
                assert(copy.payload.resref:match("^flpri[123]$") and not source_resrefs[copy.payload.resref],
                    "NATIVE_FIXTURE_RESOURCE_NOT_UNIQUE")
                source_resrefs[copy.payload.resref] = true
            else
                assert(integer(endpoint.x, 0, 32767) and integer(endpoint.y, 0, 32767), "NATIVE_POSITION_INVALID")
                copy.x, copy.y = endpoint.x, endpoint.y
            end
            self[kind][id] = copy
        end
    end
    return self
end

function Adapter:_resolve(endpoint, kind)
    local api, area = self.api, self.api.EEex_Area_GetVisible()
    if not area or text(area.m_resref) ~= endpoint.area then return nil, "AREA_UNAVAILABLE" end
    local found, matches, seen = nil, 0, {}
    for _, field in ipairs(lists) do
        assert(area[field], "NATIVE_AREA_LIST_MISSING")
        api.EEex_Utility_IterateCPtrList(area[field], function(id)
            assert(integer(id, 0, 2147483647), "NATIVE_OBJECT_ID_INVALID")
            if seen[id] then return end
            seen[id] = true
            local object = api.EEex_GameObject_Get(id)
            if not object then return end
            object = api.EEex_GameObject_CastUserType(object)
            if not object or text(object.m_scriptName) ~= endpoint.script_name then return end
            local is_sprite = api.EEex_GameObject_IsSprite(object, true)
            if (kind == "source" and is_sprite == true) or
                (kind == "target" and is_sprite == false and object.m_lstItems ~= nil) then
                matches, found = matches + 1, { object = object, id = id, pointer = pointer(api, object) }
            end
        end)
    end
    if matches ~= 1 then return nil, matches == 0 and "OWNER_MISSING" or "OWNER_AMBIGUOUS" end
    if kind == "source" then
        if api.EEex_GameObject_IsSprite(found.object, false) ~= true then return nil, "SOURCE_NOT_ALIVE" end
    elseif found.object.m_containerType ~= 4 or not found.object.m_pos or
        found.object.m_pos.x ~= endpoint.x or found.object.m_pos.y ~= endpoint.y then
        return nil, "TARGET_NOT_FIXTURE_GROUND_PILE"
    end
    return found
end

function Adapter:_item(item, slot)
    local signature = { resref = text(item.cResRef), charges = {
        item.m_useCount1, item.m_useCount2, item.m_useCount3 },
        expiration = item.m_wear, flags = item.m_flags }
    assert(resref(signature.resref) and integer(signature.expiration, 0, 65535) and
        integer(signature.flags, 0, 4294967295), "NATIVE_ITEM_PAYLOAD_INVALID")
    for index = 1, 3 do
        assert(integer(signature.charges[index], 0, 65535), "NATIVE_ITEM_CHARGE_INVALID")
    end
    return { pointer = pointer(self.api, item), slot = slot, payload = signature }
end

function Adapter:_items(owner, kind)
    local result = {}
    local function append(item, slot)
        if item then result[#result + 1] = self:_item(item, slot) end
    end
    if kind == "source" then
        local equipment = owner.object.m_equipment
        assert(equipment and equipment.m_items, "NATIVE_INVENTORY_MISSING")
        for slot = 0, 38 do append(equipment.m_items:get(slot), slot) end
    else
        self.api.EEex_Utility_IterateCPtrList(owner.object.m_lstItems, function(item)
            append(item, #result + 1)
        end)
    end
    return result
end

function Adapter:_queue()
    local globals = self.api.EngineGlobals
    local chitin = globals and globals.g_pBaldurChitin
    local handler = chitin and chitin.m_cMessageHandler
    assert(handler and handler.m_messageList, "NATIVE_MESSAGE_QUEUE_MISSING")
    local result = { addresses = {}, additions = {} }
    self.api.EEex_Utility_IterateCPtrList(handler.m_messageList, function(message)
        local address = pointer(self.api, message)
        assert(not result.addresses[address], "NATIVE_DUPLICATE_MESSAGE_POINTER")
        result.addresses[address] = true
        -- This type/subtype pair is verified from this fixture's 2.6.6 binary.
        -- Never cast an unrelated base message to the larger item-message type.
        if message:virtual_GetMsgType() == 67 and message:virtual_GetMsgSubType() == 12 then
            assert(message:virtual_GetCommType() == 1, "NATIVE_MESSAGE_COMM_UNSUPPORTED")
            local typed = self.api.EEex_CastUD(message, "CMessageContainerAddItem")
            assert(typed and integer(message.m_targetId, 0, 2147483647) and
                integer(message.m_sourceId, 0, 2147483647) and
                integer(typed.m_slotNum, -32768, 32767) and
                integer(typed.m_bCompressContainer, 0, 1), "NATIVE_MESSAGE_FIELDS_INVALID")
            local item = self:_item(typed.m_item)
            result.additions[#result.additions + 1] = { pointer = address, target = message.m_targetId,
                source = message.m_sourceId, slot = typed.m_slotNum, compress = typed.m_bCompressContainer,
                payload = item.payload, embedded_item_pointer = item.pointer }
        end
    end)
    -- Only plain copied values escape the iteration, never queued userdata that
    -- the engine can free when it consumes the message on its next update.
    return result
end

local function conflicts(message, owner_id, item)
    return message.target == owner_id or message.payload.resref == item.resref
end

local function same_message(first, second)
    return first.pointer == second.pointer and first.target == second.target and first.source == second.source and
        first.slot == second.slot and first.compress == second.compress and same(first.payload, second.payload)
end

function Adapter:_supported(item)
    local header = self.api.EEex_Resource_Demand(item.resref, "ITM")
    if not header or not integer(header.maxStackable, 0, 1) or
        not integer(header.itemFlags, 0, 4294967295) then return nil, "ITEM_NOT_NONSTACKABLE" end
    -- Resource bit 2 enables movement/drop, bit 3 makes the ground icon visible.
    if math.floor(header.itemFlags / 4) % 2 ~= 1 or math.floor(header.itemFlags / 8) % 2 ~= 1 then
        return nil, "ITEM_NOT_VISIBLE_DROPPABLE"
    end
    return true
end

function Adapter:inspect_source(id)
    local endpoint = self.sources[id]
    if not endpoint then return nil, "SOURCE_UNKNOWN" end
    self.reservations[endpoint.identity] = nil
    local supported, reason = self:_supported(endpoint.payload)
    if not supported then return nil, reason end
    local owner; owner, reason = self:_resolve(endpoint, "source")
    if not owner then return nil, reason end
    local items, selected, matches = self:_items(owner, "source"), nil, 0
    for _, item in ipairs(items) do
        if item.payload.resref == endpoint.payload.resref then matches, selected = matches + 1, item end
    end
    if matches ~= 1 then return nil, matches == 0 and "SOURCE_ITEM_MISSING" or "SOURCE_ITEM_AMBIGUOUS" end
    if not same(selected.payload, endpoint.payload) then return nil, "SOURCE_PAYLOAD_MISMATCH" end
    self.reservations[endpoint.identity] = { owner = owner, items = items, selected = selected, kind = "suppress", id = id }
    return { identity = endpoint.identity, source_id = id, exact_matches = 1,
        quantity = 1, payload = payload(endpoint.payload) }
end

function Adapter:inspect_target(id)
    local endpoint = self.targets[id]
    if not endpoint then return nil, "TARGET_UNKNOWN" end
    self.reservations[endpoint.identity] = nil
    local owner, reason = self:_resolve(endpoint, "target")
    if not owner then return nil, reason end
    local items = self:_items(owner, "target")
    if #items ~= 0 then return nil, "TARGET_NOT_EMPTY" end
    self.reservations[endpoint.identity] = { owner = owner, items = items, kind = "deliver", id = id }
    return { identity = endpoint.identity, target_id = id, accepts = 1 }
end

local function unchanged(before, after, removed)
    local expected = 0
    for _, old in ipairs(before) do
        if old ~= removed then
            expected = expected + 1
            local matches = 0
            for _, current in ipairs(after) do
                if old.pointer == current.pointer and old.slot == current.slot and same(old.payload, current.payload) then
                    matches = matches + 1
                end
            end
            if matches ~= 1 then return false end
        end
    end
    return #after == expected
end

function Adapter:execute(command)
    if self.pending then return nil, "MUTATION_PENDING" end
    if type(command) ~= "table" or command.mode ~= "execute-once" or type(command.session) ~= "table" or
        not integer(command.nonce, 1, 2147483647) or not integer(command.index, 1, 4096) or
        command.quantity ~= 1 or not hash(command.revision) then return nil, "COMMAND_INVALID" end
    if self.tick == nil then return nil, "OBSERVATION_CLOCK_MISSING" end
    local tried = self.attempted[command.session]
    if tried and tried[command.nonce] then return nil, "ALREADY_ATTEMPTED" end
    local reserved = self.reservations[command.identity]
    local source = self.sources[command.source_id]
    if not reserved or not source or reserved.kind ~= command.kind or
        not same(payload(command.payload), source.payload) then return nil, "RESERVATION_MISMATCH" end
    local kind = command.kind == "suppress" and "source" or "target"
    local endpoint = kind == "source" and source or self.targets[command.target_id]
    if not endpoint or endpoint.identity ~= command.identity or
        reserved.id ~= (kind == "source" and command.source_id or command.target_id) then return nil, "RESERVATION_MISMATCH" end
    local owner, reason = self:_resolve(endpoint, kind)
    if not owner then return nil, reason end
    if owner.id ~= reserved.owner.id or owner.pointer ~= reserved.owner.pointer then return nil, "OWNER_CHANGED" end
    local before = self:_items(owner, kind)
    if not unchanged(reserved.items, before) then return nil, "INVENTORY_CHANGED" end
    local supported; supported, reason = self:_supported(source.payload)
    if not supported then return nil, reason end
    local before_queue
    if kind == "target" then
        before_queue = self:_queue()
        for _, message in ipairs(before_queue.additions) do
            if conflicts(message, owner.id, source.payload) then return nil, "DELIVERY_QUEUE_NOT_EXCLUSIVE" end
        end
    end
    tried = tried or {}; self.attempted[command.session] = tried; tried[command.nonce] = true
    -- Retain the exact core session token, not the caller's mutable command table.
    local receipt = { session = command.session, kind = command.kind, index = command.index,
        nonce = command.nonce, revision = command.revision, identity = command.identity,
        payload = payload(source.payload) }
    self.pending = { receipt = receipt, endpoint = endpoint, kind = kind, owner = owner,
        before = before, removed = reserved.selected, tick = self.tick, complete = false }
    self.reservations[command.identity] = nil
    local p = source.payload
    local action = kind == "source" and string.format('DestroyItem("%s")', p.resref) or
        string.format('CreateItem("%s",%d,%d,%d)', p.resref, p.charges[1], p.charges[2], p.charges[3])
    -- No item iterator is active here. Pending/attempted are published first;
    -- an exception must disable this adapter, never cause a blind action retry.
    self.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly(action, owner.object)
    local after = self:_items(owner, kind)
    if kind == "source" then
        if not unchanged(reserved.items, after, reserved.selected) then
            self.last_error = "REMOVAL_NOT_EXACT"; return nil, self.last_error
        end
    else
        local after_queue, added, conflicting = self:_queue(), {}, 0
        for _, message in ipairs(after_queue.additions) do
            if not before_queue.addresses[message.pointer] then added[#added + 1] = message end
            if conflicts(message, owner.id, p) then conflicting = conflicting + 1 end
        end
        if #after == 1 and same(after[1].payload, p) and #added == 0 and conflicting == 0 then
            self.pending.created_pointer = after[1].pointer
        elseif #after == 0 and #added == 1 and conflicting == 1 and added[1].target == owner.id and added[1].source == owner.id and
            added[1].slot == 32767 and same(added[1].payload, p) then
            -- The engine queues an embedded CItem value, then Run() heap-copies
            -- it and appends to this empty container. Message and inventory
            -- pointers are intentionally different. Bind the exact new message
            -- to this nonce before allowing later observations to establish the
            -- resulting pointer; never resubmit CreateItem.
            self.pending.queued = added[1]
        else
            self.last_error = "CREATED_INSTANCE_UNPROVEN"; return nil, self.last_error
        end
    end
    self.pending.complete = true
    return true
end

function Adapter:on_observation_boundary(tick)
    if not integer(tick, 0, 9007199254740991) then return nil, "OBSERVATION_CLOCK_INVALID" end
    if self.tick and tick <= self.tick then return nil, "LATER_GAME_TICK_REQUIRED" end
    self.tick = tick
    return true
end

function Adapter:observe(command)
    if type(command) ~= "table" or command.mode == "observe-only" then return nil, "PENDING_RELOAD_UNPROVEN" end
    local pending = self.pending
    if not pending then return nil, "NO_PENDING_MUTATION" end
    local receipt = pending.receipt
    for _, key in ipairs({ "session", "kind", "index", "nonce", "revision", "identity" }) do
        if receipt[key] ~= command[key] then return nil, "RECEIPT_MISMATCH" end
    end
    if not pending.complete then return nil, self.last_error or "MUTATION_OUTCOME_UNPROVEN" end
    if not self.tick or self.tick <= pending.tick then return nil, "LATER_GAME_TICK_REQUIRED" end
    if pending.queued and self.tick - pending.tick >= Native.MAX_SETTLE_TICKS then
        pending.complete = false
        self.last_error = "DELIVERY_SETTLING_TIMEOUT"
        return nil, self.last_error
    end
    local owner, reason = self:_resolve(pending.endpoint, pending.kind)
    if not owner then return nil, reason end
    if owner.id ~= pending.owner.id or owner.pointer ~= pending.owner.pointer then return nil, "OWNER_CHANGED" end
    local items = self:_items(owner, pending.kind)
    if pending.kind == "source" then
        -- Compare every other slot/pointer/payload, not merely the selected count.
        local removed
        for _, old in ipairs(pending.before) do
            if old.pointer == pending.removed.pointer and old.slot == pending.removed.slot then removed = old end
        end
        if not removed or not unchanged(pending.before, items, removed) then return nil, "REMOVAL_NOT_EXACT" end
        receipt.removed, receipt.remaining = 1, 0
    else
        local queue = self:_queue()
        if pending.queued then
            local matching
            for _, message in ipairs(queue.additions) do
                if conflicts(message, owner.id, receipt.payload) then
                    if not pending.created_pointer and same_message(message, pending.queued) then
                        matching = message
                    else return nil, "DELIVERY_QUEUE_CONFLICT" end
                end
            end
            if not pending.created_pointer then
                if matching then
                    if #items ~= 0 then return nil, "DELIVERY_EARLY_ITEM" end
                    return nil, "DELIVERY_SETTLING"
                end
                if queue.addresses[pending.queued.pointer] then return nil, "DELIVERY_MESSAGE_REUSED" end
                if #items ~= 1 or items[1].slot ~= 1 or not same(items[1].payload, receipt.payload) then
                    return nil, "DELIVERY_MESSAGE_LOST"
                end
                -- The exact message has been consumed. Its append operation
                -- produced the sole full-payload item in this exclusively
                -- owned fixture target. Pin that actual heap copy, then demand
                -- another game tick before publishing the receipt.
                pending.created_pointer, pending.pinned_tick = items[1].pointer, self.tick
                return nil, "DELIVERY_SETTLING"
            end
            if self.tick <= pending.pinned_tick then return nil, "DELIVERY_SETTLING" end
        else
            -- Even the synchronous fallback must remain exclusive until its
            -- later receipt; an intervening queued producer invalidates proof.
            for _, message in ipairs(queue.additions) do
                if conflicts(message, owner.id, receipt.payload) then return nil, "DELIVERY_QUEUE_CONFLICT" end
            end
        end
        if #items ~= 1 or items[1].pointer ~= pending.created_pointer or
            not same(items[1].payload, receipt.payload) then return nil, "CREATED_INSTANCE_CHANGED" end
        local supported; supported, reason = self:_supported(receipt.payload)
        if not supported then return nil, reason end
        -- This is restricted to a known reachable fixture ground pile; it is
        -- not a claim that arbitrary creature inventory implies lootability.
        receipt.created, receipt.nonce_bound_instance, receipt.lootable = 1, 1, 1
    end
    self.pending = nil
    return receipt
end

function Adapter:diagnostics()
    local result = { tick = self.tick, last_error = self.last_error, sources = {}, targets = {} }
    local area = self.api.EEex_Area_GetVisible()
    result.area = area and text(area.m_resref) or nil
    if self.pending then
        result.pending = { kind = self.pending.receipt.kind, nonce = self.pending.receipt.nonce,
            complete = self.pending.complete, created_pointer = self.pending.created_pointer,
            queued = self.pending.queued, pinned_tick = self.pending.pinned_tick }
    end
    for _, kind in ipairs({ "sources", "targets" }) do
        for id, endpoint in pairs(self[kind]) do
            local label = kind == "sources" and "source" or "target"
            local owner, reason = self:_resolve(endpoint, label)
            result[kind][id] = owner and { object_id = owner.id, pointer = owner.pointer,
                items = self:_items(owner, label) } or { unavailable = reason }
        end
    end
    return result
end

function Adapter:destroy()
    self.pending, self.tick, self.last_error = nil, nil, nil
    self.reservations, self.attempted = {}, {}
end

return Native
