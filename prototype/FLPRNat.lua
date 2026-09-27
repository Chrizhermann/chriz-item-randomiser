-- Bounded native bridge for the disposable Mode 1 fixture, not arbitrary loot.
-- Unique creature sources; empty named type-4 ground piles or unique living
-- creature targets; one unexpired, nonstackable, droppable item per operation.
-- No listeners or autoloading here.
local Native = {}
-- The ordinary transport consumes this message on an engine update. Allow a
-- conservative 20 game seconds (15 ticks/second), never unlimited settling.
-- Paused time does not consume this allowance; this is not a wall-clock timer.
Native.MAX_SETTLE_TICKS = 300
-- CMessageAddItem::Run scans these internal backpack slots (see
-- docs/native-creature-transport-evidence.md). A full backpack displaces an
-- unrelated item to the ground, so creature delivery requires a free one.
Native.BACKPACK_FIRST, Native.BACKPACK_LAST = 18, 33
local FAMILIAR_ALLEGIANCE = 3
local Adapter = {}
Adapter.__index = Adapter
local lists = { "m_lVertSort", "m_lVertSortBack", "m_lVertSortFlight", "m_lVertSortUnder" }
local required = { "EEex_Area_GetVisible", "EEex_GameObject_Get", "EEex_GameObject_CastUserType",
    "EEex_GameObject_IsSprite", "EEex_Utility_IterateCPtrList", "EEex_UDToPtr",
    "EEex_Resource_Demand", "EEex_Action_ExecuteResponseStringOnAIBaseInstantly", "EEex_CastUD" }
local creature_required = { "EEex_Sprite_GetPortraitIndex" }

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
        attempted = {}, tick = nil, pending = nil, last_error = nil, source_resrefs = {}, held = {} }, Adapter)
    local identities, source_resrefs, creatures, guarded_ids = {}, self.source_resrefs, false, {}
    local guarded_count = 0
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
                copy.kind = endpoint.kind == nil and "creature" or endpoint.kind
                assert(copy.kind == "creature" or copy.kind == "guarded-container", "NATIVE_SOURCE_KIND_UNSUPPORTED")
                copy.sprite = true
                copy.payload = assert(payload(endpoint.payload), "NATIVE_PAYLOAD_UNSUPPORTED")
                assert(not source_resrefs[copy.payload.resref],
                    "NATIVE_FIXTURE_RESOURCE_NOT_UNIQUE")
                if copy.kind == "guarded-container" then
                    assert(integer(endpoint.x,0,32767) and integer(endpoint.y,0,32767) and
                        integer(endpoint.container_type or 4,0,65535), "NATIVE_GUARDED_SOURCE_INVALID")
                    guarded_ids[id] = true
                    guarded_count = guarded_count + 1
                    copy.sprite, copy.x, copy.y = false, endpoint.x, endpoint.y
                    copy.container_type = endpoint.container_type or 4
                end
                source_resrefs[copy.payload.resref] = true
            else
                -- The accepted fixture predates this field; absent means a ground pile.
                copy.kind = endpoint.kind == nil and "ground-pile" or endpoint.kind
                assert(copy.kind == "ground-pile" or copy.kind == "creature", "NATIVE_TARGET_KIND_UNSUPPORTED")
                if copy.kind == "creature" then
                    assert(endpoint.x == nil and endpoint.y == nil, "NATIVE_POSITION_INVALID")
                    copy.sprite, creatures = true, true
                else
                    assert(integer(endpoint.x, 0, 32767) and integer(endpoint.y, 0, 32767), "NATIVE_POSITION_INVALID")
                    copy.x, copy.y = endpoint.x, endpoint.y
                end
            end
            self[kind][id] = copy
        end
    end
    if guarded_count > 0 or fixture.source_gate ~= nil then
        local gate = fixture.source_gate
        assert(guarded_count > 0 and type(gate) == "table" and
            gate.schema == "flpr-unvisited-fixture-gate-v1" and
            (gate.source_id == nil) ~= (gate.source_ids == nil),
            "NATIVE_SOURCE_GATE_INVALID")
        if gate.source_id ~= nil then
            assert(type(gate.source_id) == "string" and guarded_count == 1 and
                guarded_ids[gate.source_id], "NATIVE_SOURCE_GATE_INVALID")
        else
            local ids, seen, count = gate.source_ids, {}, 0
            assert(type(ids) == "table", "NATIVE_SOURCE_GATE_INVALID")
            for index, id in pairs(ids) do
                assert(integer(index, 1, guarded_count) and type(id) == "string" and
                    guarded_ids[id] and not seen[id], "NATIVE_SOURCE_GATE_INVALID")
                seen[id] = true
                count = count + 1
            end
            assert(count == guarded_count, "NATIVE_SOURCE_GATE_INVALID")
            for index = 1, count do
                assert(ids[index] ~= nil, "NATIVE_SOURCE_GATE_INVALID")
            end
        end
    end
    if creatures then
        for _, name in ipairs(creature_required) do assert(type(api[name]) == "function", name .. "_MISSING") end
    end
    return self
end

-- Returns nil for a usable creature target, otherwise the reason it cannot
-- receive a reward now. None of these states permits a substitute recipient.
function Adapter:_creature_state(object)
    local api = self.api
    if api.EEex_GameObject_IsSprite(object, false) ~= true then return "TARGET_NOT_ALIVE" end
    local portrait = api.EEex_Sprite_GetPortraitIndex(object)
    if portrait ~= -1 then
        assert(integer(portrait, 0, 5), "NATIVE_PORTRAIT_INVALID")
        return "TARGET_PARTY_MEMBER"
    end
    -- CMessageAddItem::Run forwards a familiar's item to the protagonist.
    local typeAI = object.m_typeAI
    assert(typeAI and integer(typeAI.m_EnemyAlly, 0, 255), "NATIVE_ALLEGIANCE_INVALID")
    if typeAI.m_EnemyAlly == FAMILIAR_ALLEGIANCE then return "TARGET_FAMILIAR" end
    return nil
end

function Adapter:_resolve(endpoint, kind, visible_area)
    local api, area = self.api, visible_area or self.api.EEex_Area_GetVisible()
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
            -- Dead same-named sprites still count, so a corpse and its
            -- replacement can never be mistaken for one continuous owner.
            local is_sprite = api.EEex_GameObject_IsSprite(object, true)
            if (endpoint.sprite and is_sprite == true) or
                (not endpoint.sprite and is_sprite == false and object.m_lstItems ~= nil) then
                matches, found = matches + 1, { object = object, id = id, pointer = pointer(api, object) }
            end
        end)
    end
    if matches ~= 1 then return nil, matches == 0 and "OWNER_MISSING" or "OWNER_AMBIGUOUS" end
    if kind == "source" and endpoint.sprite then
        if api.EEex_GameObject_IsSprite(found.object, false) ~= true then return nil, "SOURCE_NOT_ALIVE" end
    elseif endpoint.kind == "creature" then
        local reason = self:_creature_state(found.object)
        -- The unusable owner is returned only for read-only diagnostics.
        if reason then return nil, reason, found end
    elseif found.object.m_containerType ~= (endpoint.container_type or 4) or not found.object.m_pos or
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

function Adapter:_items(owner, sprite)
    local result = {}
    local function append(item, slot)
        if item then result[#result + 1] = self:_item(item, slot) end
    end
    if sprite then
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
    -- addresses maps every live message pointer to its base-class target id.
    local result = { addresses = {}, additions = {} }
    self.api.EEex_Utility_IterateCPtrList(handler.m_messageList, function(message)
        local address = pointer(self.api, message)
        assert(not result.addresses[address], "NATIVE_DUPLICATE_MESSAGE_POINTER")
        assert(integer(message.m_targetId, -2147483648, 2147483647), "NATIVE_MESSAGE_FIELDS_INVALID")
        result.addresses[address] = message.m_targetId
        -- These type/subtype pairs are verified from this fixture's 2.6.6 binary.
        -- Never cast an unrelated base message to a larger item-message type.
        local subtype = message:virtual_GetMsgType() == 67 and message:virtual_GetMsgSubType() or nil
        if subtype == 12 then
            assert(message:virtual_GetCommType() == 1, "NATIVE_MESSAGE_COMM_UNSUPPORTED")
            local typed = self.api.EEex_CastUD(message, "CMessageContainerAddItem")
            assert(typed and integer(message.m_targetId, 0, 2147483647) and
                integer(message.m_sourceId, 0, 2147483647) and
                integer(typed.m_slotNum, -32768, 32767) and
                integer(typed.m_bCompressContainer, 0, 1), "NATIVE_MESSAGE_FIELDS_INVALID")
            local item = self:_item(typed.m_item)
            result.additions[#result.additions + 1] = { form = "container", pointer = address,
                target = message.m_targetId, source = message.m_sourceId, slot = typed.m_slotNum,
                compress = typed.m_bCompressContainer, payload = item.payload, embedded_item_pointer = item.pointer }
        elseif subtype == 2 then
            -- CMessageAddItem: GiveItemCreate's creature transport. It has no
            -- slot field; Run chooses the backpack slot when it executes.
            assert(message:virtual_GetCommType() == 1, "NATIVE_MESSAGE_COMM_UNSUPPORTED")
            local typed = self.api.EEex_CastUD(message, "CMessageAddItem")
            assert(typed and integer(message.m_targetId, 0, 2147483647) and
                integer(message.m_sourceId, 0, 2147483647), "NATIVE_MESSAGE_FIELDS_INVALID")
            local item = self:_item(typed.m_item)
            result.additions[#result.additions + 1] = { form = "creature", pointer = address,
                target = message.m_targetId, source = message.m_sourceId,
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
    return first.form == second.form and first.pointer == second.pointer and first.target == second.target and
        first.source == second.source and first.slot == second.slot and first.compress == second.compress and
        same(first.payload, second.payload)
end

-- A creature target is exclusive only if no queued message of any type is
-- addressed to it and no queued item message carries a fixture resource.
-- `ignore` excludes the operation's own bound message pointer.
local function creature_conflict(queue, owner_id, resrefs, ignore)
    for address, target in pairs(queue.addresses) do
        if address ~= ignore and target == owner_id then return true end
    end
    for _, message in ipairs(queue.additions) do
        if message.pointer ~= ignore and resrefs[message.payload.resref] then return true end
    end
    return false
end

local function addition_at(queue, address)
    for _, message in ipairs(queue.additions) do
        if message.pointer == address then return message end
    end
end

local function empty_backpack(items)
    local used, empty = {}, {}
    for _, item in ipairs(items) do used[item.slot] = true end
    for slot = Native.BACKPACK_FIRST, Native.BACKPACK_LAST do
        if not used[slot] then empty[#empty + 1] = slot end
    end
    return empty
end

-- Every earlier slot must keep its pointer and payload, and exactly one
-- previously empty backpack slot may gain an item. Stack merges, displacement
-- to the ground, or any other change fail instead of guessing ownership.
local function placed_item(before, after, empty)
    local old, free, added = {}, {}, {}
    for _, item in ipairs(before) do old[item.slot] = item end
    for _, slot in ipairs(empty) do free[slot] = true end
    for _, current in ipairs(after) do
        local previous = old[current.slot]
        if previous then
            if previous.pointer ~= current.pointer or not same(previous.payload, current.payload) then
                return nil, "INVENTORY_CHANGED"
            end
            old[current.slot] = nil
        else
            added[#added + 1] = current
        end
    end
    if next(old) ~= nil then return nil, "INVENTORY_CHANGED" end
    if #added == 0 then return nil, "DELIVERY_MESSAGE_LOST" end
    if #added ~= 1 or not free[added[1].slot] then return nil, "DELIVERY_PLACEMENT_UNEXPECTED" end
    return added[1]
end

function Native.supported(api, item)
    item = payload(item)
    if not item then return nil, "NATIVE_PAYLOAD_UNSUPPORTED" end
    local header = api.EEex_Resource_Demand(item.resref, "ITM")
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
    if endpoint.kind == "guarded-container" then return nil, "SOURCE_GUARD_REQUIRED" end
    self.reservations[endpoint.identity] = nil
    local supported, reason = self:_supported(endpoint.payload)
    if not supported then return nil, reason end
    local owner; owner, reason = self:_resolve(endpoint, "source")
    if not owner then return nil, reason end
    local items, selected, matches = self:_items(owner, true), nil, 0
    for _, item in ipairs(items) do
        if item.payload.resref == endpoint.payload.resref then matches, selected = matches + 1, item end
    end
    if matches ~= 1 then return nil, matches == 0 and "SOURCE_ITEM_MISSING" or "SOURCE_ITEM_AMBIGUOUS" end
    if not same(selected.payload, endpoint.payload) then return nil, "SOURCE_PAYLOAD_MISMATCH" end
    self.reservations[endpoint.identity] = { owner = owner, items = items, selected = selected, kind = "suppress", id = id }
    return { identity = endpoint.identity, source_id = id, exact_matches = 1,
        quantity = 1, payload = payload(endpoint.payload) }
end

-- Corroborates a guard event against the exact visible area and container.
-- A missing area or owner is never evidence that the guarded item was removed.
function Adapter:guarded_source_remaining(id, area_pointer, owner_pointer)
    local endpoint = self.sources[id]
    if not endpoint then return nil, "SOURCE_UNKNOWN" end
    if endpoint.kind ~= "guarded-container" then return nil, "SOURCE_NOT_GUARDED" end
    if type(area_pointer) ~= "string" or area_pointer == "" or area_pointer == "0" or
        type(owner_pointer) ~= "string" or owner_pointer == "" or owner_pointer == "0" then
        return nil, "SOURCE_POINTER_INVALID"
    end
    local area = self.api.EEex_Area_GetVisible()
    if not area or text(area.m_resref) ~= endpoint.area then return nil, "AREA_UNAVAILABLE" end
    if pointer(self.api, area) ~= area_pointer then return nil, "AREA_CHANGED" end
    local owner, reason = self:_resolve(endpoint, "guard-observe", area)
    if not owner then return nil, reason end
    if owner.pointer ~= owner_pointer then return nil, "OWNER_CHANGED" end
    local remaining = 0
    for _, item in ipairs(self:_items(owner, false)) do
        if item.payload.resref == endpoint.payload.resref then remaining = remaining + 1 end
    end
    return { remaining = remaining, area_pointer = area_pointer, owner_pointer = owner_pointer }
end

function Adapter:inspect_target(id)
    local endpoint = self.targets[id]
    if not endpoint then return nil, "TARGET_UNKNOWN" end
    self.reservations[endpoint.identity] = nil
    local owner, reason = self:_resolve(endpoint, "target")
    if not owner then return nil, reason end
    local items = self:_items(owner, endpoint.sprite)
    if endpoint.kind == "creature" then
        -- Checked before the core journals anything, so these are deferrals.
        local empty = empty_backpack(items)
        if #empty == 0 then return nil, "TARGET_INVENTORY_FULL" end
        if creature_conflict(self:_queue(), owner.id, self.source_resrefs) then return nil, "TARGET_QUEUE_BUSY" end
        self.reservations[endpoint.identity] = { owner = owner, items = items, kind = "deliver", id = id, empty = empty }
        return { identity = endpoint.identity, target_id = id, accepts = 1 }
    end
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
    local source = self.sources[command.source_id]
    if command.kind == "suppress" and source and source.kind == "guarded-container" then
        return nil, "SOURCE_GUARD_REQUIRED"
    end
    if self.tick == nil then return nil, "OBSERVATION_CLOCK_MISSING" end
    local tried = self.attempted[command.session]
    if tried and tried[command.nonce] then return nil, "ALREADY_ATTEMPTED" end
    local reserved = self.reservations[command.identity]
    if not reserved or not source or reserved.kind ~= command.kind or
        not same(payload(command.payload), source.payload) then return nil, "RESERVATION_MISMATCH" end
    local kind = command.kind == "suppress" and "source" or "target"
    local endpoint = kind == "source" and source or self.targets[command.target_id]
    if not endpoint or endpoint.identity ~= command.identity or
        reserved.id ~= (kind == "source" and command.source_id or command.target_id) then return nil, "RESERVATION_MISMATCH" end
    local owner, reason = self:_resolve(endpoint, kind)
    if not owner then return nil, reason end
    if owner.id ~= reserved.owner.id or owner.pointer ~= reserved.owner.pointer then return nil, "OWNER_CHANGED" end
    local before = self:_items(owner, endpoint.sprite)
    if not unchanged(reserved.items, before) then return nil, "INVENTORY_CHANGED" end
    local supported; supported, reason = self:_supported(source.payload)
    if not supported then return nil, reason end
    local creature = kind == "target" and endpoint.kind == "creature"
    local before_queue
    if creature then
        before_queue = self:_queue()
        if creature_conflict(before_queue, owner.id, self.source_resrefs) then
            return nil, "DELIVERY_QUEUE_NOT_EXCLUSIVE"
        end
    elseif kind == "target" then
        before_queue = self:_queue()
        for _, message in ipairs(before_queue.additions) do
            if conflicts(message, owner.id, source.payload) then return nil, "DELIVERY_QUEUE_NOT_EXCLUSIVE" end
        end
    end
    tried = tried or {}; self.attempted[command.session] = tried; tried[command.nonce] = true
    -- Retain the exact core session token, not the caller's mutable command table.
    local receipt = { session = command.session, kind = command.kind, index = command.index,
        nonce = command.nonce, revision = command.revision, identity = command.identity,
        source_id = command.source_id, target_id = command.target_id,
        payload = payload(source.payload) }
    self.pending = { receipt = receipt, endpoint = endpoint, kind = kind, owner = owner,
        before = before, removed = reserved.selected, tick = self.tick, complete = false }
    self.reservations[command.identity] = nil
    local p = source.payload
    local action
    if kind == "source" then
        action = string.format('DestroyItem("%s")', p.resref)
    elseif creature then
        -- Queues one CMessageAddItem from and to this creature. CreateItem on a
        -- non-party creature would instead fill unusual slots without Equip.
        action = string.format('GiveItemCreate("%s",Myself,%d,%d,%d)', p.resref, p.charges[1], p.charges[2], p.charges[3])
    else
        action = string.format('CreateItem("%s",%d,%d,%d)', p.resref, p.charges[1], p.charges[2], p.charges[3])
    end
    -- No item iterator is active here. Pending/attempted are published first;
    -- an exception must disable this adapter, never cause a blind action retry.
    self.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly(action, owner.object)
    local after = self:_items(owner, endpoint.sprite)
    if kind == "source" then
        if not unchanged(reserved.items, after, reserved.selected) then
            self.last_error = "REMOVAL_NOT_EXACT"; return nil, self.last_error
        end
    elseif creature then
        -- The static evidence shows no synchronous creature placement. Require
        -- an untouched inventory plus exactly one new queued message, and bind
        -- that message to this nonce; later observations prove the result.
        local after_queue, added = self:_queue(), {}
        for address in pairs(after_queue.addresses) do
            if not before_queue.addresses[address] then added[#added + 1] = address end
        end
        local message = #added == 1 and addition_at(after_queue, added[1]) or nil
        if not unchanged(reserved.items, after) or not message or message.form ~= "creature" or
            message.target ~= owner.id or message.source ~= owner.id or not same(message.payload, p) or
            creature_conflict(after_queue, owner.id, self.source_resrefs, message.pointer) then
            self.last_error = "CREATED_INSTANCE_UNPROVEN"; return nil, self.last_error
        end
        self.pending.queued, self.pending.empty = message, reserved.empty
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
    local owner, reason, unusable = self:_resolve(pending.endpoint, pending.kind)
    if not owner then
        if reason == "TARGET_NOT_ALIVE" and unusable and unusable.id == pending.owner.id and
            unusable.pointer == pending.owner.pointer then
            -- CMessageAddItem::Run has no death check: the item may now be in
            -- a corpse or in a death drop. Never resume or claim this receipt.
            pending.complete = false
            self.last_error = "TARGET_DIED_DURING_DELIVERY"
            return nil, self.last_error
        end
        return nil, reason
    end
    if owner.id ~= pending.owner.id or owner.pointer ~= pending.owner.pointer then return nil, "OWNER_CHANGED" end
    local items = self:_items(owner, pending.endpoint.sprite)
    if pending.kind == "target" and pending.endpoint.kind == "creature" then
        return self:_observe_creature(pending, receipt, owner, items)
    end
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

function Adapter:_observe_creature(pending, receipt, owner, items)
    local queue, queued = self:_queue(), pending.queued
    if creature_conflict(queue, owner.id, self.source_resrefs, queued.pointer) then
        return nil, "DELIVERY_QUEUE_CONFLICT"
    end
    local present = queue.addresses[queued.pointer] ~= nil
    if not pending.created_pointer then
        if present then
            local current = addition_at(queue, queued.pointer)
            if not current or not same_message(current, queued) then return nil, "DELIVERY_QUEUE_CONFLICT" end
            if not unchanged(pending.before, items) then return nil, "DELIVERY_EARLY_ITEM" end
            return nil, "DELIVERY_SETTLING"
        end
        -- The exact message has been consumed. Its Run heap-copies the item into
        -- one previously empty backpack slot; pin that actual pointer and slot,
        -- then demand another later tick before publishing anything.
        local placed, failure = placed_item(pending.before, items, pending.empty)
        if not placed then return nil, failure end
        if not same(placed.payload, receipt.payload) then return nil, "DELIVERY_PAYLOAD_CHANGED" end
        pending.created_pointer, pending.created_slot, pending.pinned_tick = placed.pointer, placed.slot, self.tick
        return nil, "DELIVERY_SETTLING"
    end
    if present then return nil, "DELIVERY_MESSAGE_REUSED" end
    if self.tick <= pending.pinned_tick then return nil, "DELIVERY_SETTLING" end
    local placed = placed_item(pending.before, items, pending.empty)
    if not placed or placed.pointer ~= pending.created_pointer or placed.slot ~= pending.created_slot or
        not same(placed.payload, receipt.payload) then return nil, "CREATED_INSTANCE_CHANGED" end
    local supported, reason = self:_supported(receipt.payload)
    if not supported then return nil, reason end
    -- Held by a living, non-party creature in its backpack. That is not proof
    -- of lootability: the death drop must be verified separately in the engine.
    receipt.created, receipt.nonce_bound_instance, receipt.lootable, receipt.held = 1, 1, 0, 1
    receipt.holder, receipt.slot = "creature", placed.slot
    -- Session-only provenance. A save reload cannot reconstruct this record
    -- by finding another item with the same name, address, or payload.
    self.held[receipt.index] = { session=receipt.session, revision=receipt.revision,
        nonce=receipt.nonce, source_id=receipt.source_id, target_id=receipt.target_id,
        owner_id=owner.id, owner_pointer=owner.pointer, item_pointer=placed.pointer,
        slot=placed.slot, payload=payload(placed.payload), endpoint=pending.endpoint }
    self.pending = nil
    return receipt
end

function Adapter:_held(request)
    local held = type(request) == "table" and self.held[request.index]
    if not held then return nil, "HELD_RELOAD_UNPROVEN" end
    for _, key in ipairs({ "session", "revision", "nonce", "source_id", "target_id" }) do
        if request[key] ~= held[key] then return nil, "HELD_BINDING_MISMATCH" end
    end
    if request.kind ~= "verify-held-drop" or request.mode ~= "observe-only" or
        not same(payload(request.payload), held.payload) then return nil, "HELD_BINDING_MISMATCH" end
    return held
end

-- Only the controlled fixture may use this snapshot to begin a death trace.
-- It is not proof against unobserved remove/recreate at a recycled address.
function Adapter:held_binding(request)
    local held, reason = self:_held(request)
    if not held then return nil, reason end
    local owner; owner, reason = self:_resolve(held.endpoint, "target")
    if not owner then return nil, reason end
    if owner.id ~= held.owner_id or owner.pointer ~= held.owner_pointer then return nil, "OWNER_CHANGED" end
    if creature_conflict(self:_queue(), owner.id, self.source_resrefs) then return nil, "HELD_QUEUE_CONFLICT" end
    local matches, found = 0, nil
    for _, item in ipairs(self:_items(owner, true)) do
        if item.payload.resref == held.payload.resref then matches = matches + 1 end
        if item.pointer == held.item_pointer and item.slot == held.slot and same(item.payload, held.payload) then found = item end
    end
    if not found or matches ~= 1 then return nil, "HELD_INSTANCE_CHANGED" end
    return { owner_pointer=held.owner_pointer, original_item_pointer=held.item_pointer,
        slot=held.slot, payload=payload(held.payload) }
end

-- Revalidate a stopped trace's exact heap copy in this known walkable fixture.
-- Pointers from the trace are compared with live objects; never dereferenced.
function Adapter:verify_held_drop(request, candidate)
    local held, reason = self:_held(request)
    if not held then return nil, reason end
    if type(candidate) ~= "table" or candidate.owner_pointer ~= held.owner_pointer or
        candidate.original_item_pointer ~= held.item_pointer or candidate.slot ~= held.slot then
        return nil, "DROP_BINDING_MISMATCH"
    end
    local owner, unavailable, corpse = self:_resolve(held.endpoint, "target")
    if owner or unavailable ~= "TARGET_NOT_ALIVE" or not corpse or
        corpse.id ~= held.owner_id or corpse.pointer ~= held.owner_pointer then return nil, "DROP_OWNER_UNPROVEN" end
    local api, area = self.api, self.api.EEex_Area_GetVisible()
    if not area or text(area.m_resref) ~= "bg2600" then return nil, "AREA_UNAVAILABLE" end
    if type(api.EEex_Sprite_GetInPortrait) ~= "function" then return nil, "PARTY_SCAN_UNAVAILABLE" end
    local seen, matches, occurrences, found = {}, 0, 0, nil
    local function scan(object, id, party)
        if not object then return end
        local ptr = pointer(api, object)
        if seen[ptr] then return end
        seen[ptr] = true
        local sprite = api.EEex_GameObject_IsSprite(object, true)
        if not sprite and not object.m_lstItems then return end
        for _, item in ipairs(self:_items({object=object}, sprite)) do
            if item.payload.resref == held.payload.resref then occurrences = occurrences + 1 end
            if item.pointer == candidate.new_item_pointer then
                matches = matches + 1
                if not party and not sprite and object.m_containerType == 4 and
                    ptr == candidate.container_pointer and same(item.payload, held.payload) then found = id end
            end
        end
    end
    for index = 0, 5 do scan(api.EEex_Sprite_GetInPortrait(index), nil, true) end
    for _, field in ipairs(lists) do
        assert(area[field], "NATIVE_AREA_LIST_MISSING")
        api.EEex_Utility_IterateCPtrList(area[field], function(id)
            local object = api.EEex_GameObject_Get(id)
            if object then scan(api.EEex_GameObject_CastUserType(object), id, false) end
        end)
    end
    if matches ~= 1 or occurrences ~= 1 or not found then return nil, "DROP_GROUND_INSTANCE_UNPROVEN" end
    if creature_conflict(self:_queue(), found, self.source_resrefs) then return nil, "DROP_QUEUE_CONFLICT" end
    return true
end
function Adapter:_supported(item) return Native.supported(self.api, item) end

function Adapter:diagnostics()
    local result = { tick = self.tick, last_error = self.last_error, sources = {}, targets = {} }
    local area = self.api.EEex_Area_GetVisible()
    result.area = area and text(area.m_resref) or nil
    if self.pending then
        result.pending = { kind = self.pending.receipt.kind, nonce = self.pending.receipt.nonce,
            complete = self.pending.complete, created_pointer = self.pending.created_pointer,
            created_slot = self.pending.created_slot, queued = self.pending.queued,
            pinned_tick = self.pending.pinned_tick, empty = self.pending.empty }
    end
    for _, kind in ipairs({ "sources", "targets" }) do
        for id, endpoint in pairs(self[kind]) do
            local label = kind == "sources" and "source" or "target"
            local owner, reason, unusable = self:_resolve(endpoint, label)
            -- A dead or party-owned creature's inventory is evidence, not a target.
            local seen = owner or unusable
            result[kind][id] = seen and { object_id = seen.id, pointer = seen.pointer, unavailable = reason,
                items = self:_items(seen, endpoint.sprite) } or { unavailable = reason }
        end
    end
    return result
end

function Adapter:destroy()
    self.pending, self.tick, self.last_error = nil, nil, nil
    self.reservations, self.attempted, self.held = {}, {}, {}
end

return Native
