-- Bounded native bridge for the disposable Mode 1 fixture, not arbitrary loot.
-- Unique creature sources; named containers or unique living creature targets;
-- one unexpired, droppable item record per operation, with exact stack charges.
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
local function guarded(endpoint)
    return endpoint.kind=='guarded-container' or endpoint.kind=='guarded-creature' or
        endpoint.kind=='guarded-store' or endpoint.kind=='guarded-script' or
        endpoint.kind=='guarded-template'
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
        not integer(value.flags, 0, 3) or type(value.charges) ~= "table" then return nil end
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
local function constructor_payload(actual, expected)
    -- CreateItem/GiveItemCreate choose only identification from the ITM.
    -- The source's stolen bit must be set on the one proven new instance.
    local created = payload(actual)
    if not created or (created.flags ~= 0 and created.flags ~= 1) then return false end
    created.flags = expected.flags
    return same(created, expected)
end
local function pointer(api, object)
    local address = api.EEex_UDToPtr(object)
    assert(address ~= nil and address ~= 0, "NATIVE_POINTER_UNAVAILABLE")
    return tostring(address)
end

function Native.new(api, fixture, seed, reward_payload)
    assert(type(api) == "table" and type(fixture) == "table", "NATIVE_CONFIG_INVALID")
    for _, name in ipairs(required) do assert(type(api[name]) == "function", name .. "_MISSING") end
    local self = setmetatable({ api = api, sources = {}, targets = {}, reservations = {},
        attempted = {}, tick = nil, pending = nil, last_error = nil, source_resrefs = {}, held = {},
        seed=seed, reward_payload=reward_payload, container_latched={} }, Adapter)
    local identities, source_resrefs, creatures, guarded_ids = {}, self.source_resrefs, false, {}
    local guarded_count = 0
    for _, kind in ipairs({ "sources", "targets" }) do
        assert(type(fixture[kind]) == "table", "NATIVE_CONFIG_INVALID")
        for id, endpoint in pairs(fixture[kind]) do
            assert(type(id) == "string" and type(endpoint) == "table" and
                hash(endpoint.identity) and not identities[endpoint.identity], "NATIVE_ENDPOINT_INVALID")
            local store_source = kind == "sources" and endpoint.kind == "guarded-store"
            local script_source = kind == "sources" and endpoint.kind == "guarded-script"
            local template_source = kind == "sources" and endpoint.kind == "guarded-template"
            local virtual_source = kind == "sources" and endpoint.kind == "virtual"
            local store_target = kind == "targets" and endpoint.kind == "store"
            local group_target = kind == "targets" and endpoint.kind == "group"
            local quest_target = kind == "targets" and endpoint.kind == "quest"
            if quest_target then
                local spell=endpoint.recipient_local=='FLPR_RKEY' and endpoint.event_global=='FLPR_EKEY'
                local scripted=endpoint.adapter_id=='mode2-reward-v1' and
                    type(endpoint.recipient_local)=='string' and type(endpoint.event_global)=='string' and
                    endpoint.recipient_local:match('^FLPR_R[A-Z0-9_]+$') and
                    endpoint.recipient_local:sub(7)==endpoint.event_global:sub(7)
                local marked=spell or scripted
                assert(((marked and endpoint.area=='*') or
                    (endpoint.recipient_local==nil and resref(endpoint.area))) and type(endpoint.event_global)=='string' and
                    endpoint.event_global:match('^FLPR_E[A-Z0-9_]+$') and
                    type(api.EEex_GameState_GetGlobalInt)=='function' and
                    type(api.EEex_Sprite_GetInPortrait)=='function','NATIVE_QUEST_INVALID')
                if marked then
                    assert(type(api.EEex_Sprite_GetLocalInt)=='function','NATIVE_QUEST_RECIPIENT_API_MISSING')
                end
                if spell then
                    assert(api.FLPR and api.FLPR.Spell and api.FLPR.Spell.callback==api.FLPRKEY and
                        type(api.FLPRKEY)=='function','CAMPAIGN_SPELL_SERVICE_MISSING')
                end
            elseif group_target then
                assert(resref(endpoint.area) and integer(seed,1,2147483646) and
                    type(endpoint.members)=='table' and #endpoint.members>0 and type(endpoint.eligible)=='table',
                    'NATIVE_GROUP_INVALID')
                for _,member in ipairs(endpoint.members) do
                    assert(resref(member.resref) and type(member.script_name)=='string' and #member.script_name>0,
                        'NATIVE_GROUP_MEMBER_INVALID')
                end
            elseif virtual_source then
                assert(endpoint.area == nil and endpoint.script_name == nil, "NATIVE_VIRTUAL_SOURCE_INVALID")
            elseif template_source then
                local reference = type(endpoint.creature_resource) == "string" and
                    endpoint.creature_resource:match("^cre:(.+)$")
                assert(resref(reference) and endpoint.area == nil and endpoint.script_name == nil,
                    "NATIVE_GUARDED_TEMPLATE_INVALID")
            elseif script_source then
                assert((endpoint.adapter_id == "blpool-v1" or endpoint.adapter_id == "original-quest-v1" or
                    endpoint.adapter_id == 'spell-key-v1' or endpoint.adapter_id == 'mode2-reward-v1') and endpoint.area == nil and
                    endpoint.script_name == nil, "NATIVE_GUARDED_SCRIPT_INVALID")
            elseif store_source then
                assert(resref(endpoint.store) and endpoint.area == nil and endpoint.script_name == nil and
                    integer(endpoint.record_index, 0, 4294967295) and
                    integer(endpoint.quantity, 1, 4294967295), "NATIVE_GUARDED_STORE_INVALID")
            elseif store_target then
                assert(resref(endpoint.store) and endpoint.area == nil and endpoint.script_name == nil and
                    endpoint.x == nil and endpoint.y == nil, "NATIVE_STORE_TARGET_INVALID")
            else
                assert(resref(endpoint.area) and type(endpoint.script_name) == "string" and
                    #endpoint.script_name > 0, "NATIVE_ENDPOINT_INVALID")
            end
            identities[endpoint.identity] = true
            local copy = { identity = endpoint.identity }
            if endpoint.inventory_reward ~= nil then
                assert(kind=='targets' and endpoint.kind=='creature' and endpoint.inventory_reward==true,
                    'NATIVE_INVENTORY_REWARD_INVALID')
                copy.inventory_reward=true
            end
            if endpoint.claim_global then
                assert(kind=='targets' and endpoint.kind=='container' and
                    type(endpoint.claim_global)=='string' and endpoint.claim_global:match('^FLPR_EM2_[0-9]+$') and
                    type(api.EEex_GameState_GetGlobalInt)=='function','NATIVE_QUEST_INVALID')
                copy.claim_global=endpoint.claim_global
            end
            if quest_target then
                copy.area,copy.quest_event=endpoint.area=='*' and '*' or resref(endpoint.area),endpoint.event_global
                copy.recipient_local=endpoint.recipient_local
                copy.spell_event=endpoint.recipient_local=='FLPR_RKEY'
            elseif group_target then
                copy.area,copy.members,copy.eligible=resref(endpoint.area),{},{}
                for n,member in ipairs(endpoint.members) do
                    copy.members[n]={resref=member.resref,script_name=member.script_name:lower()}
                end
                for item,candidates in pairs(endpoint.eligible) do
                    assert(resref(item) and type(candidates)=='table' and #candidates>0,'NATIVE_GROUP_CANDIDATES_INVALID')
                    copy.eligible[item]={}
                    for n,index in ipairs(candidates) do
                        assert(integer(index,1,#copy.members),'NATIVE_GROUP_CANDIDATES_INVALID')
                        copy.eligible[item][n]=index
                    end
                end
            elseif virtual_source then
                -- Explicit additional stock has only a payload, never an owner.
            elseif template_source then
                -- The constructor input is protected by Gate, not a live actor.
                copy.creature_resource = "cre:" .. resref(endpoint.creature_resource:sub(5))
            elseif script_source then
                copy.adapter_id = endpoint.adapter_id
            elseif store_source then
                copy.store, copy.record_index, copy.quantity =
                    resref(endpoint.store), endpoint.record_index, endpoint.quantity
            elseif store_target then
                copy.store = resref(endpoint.store)
            else
                copy.area, copy.script_name = resref(endpoint.area), endpoint.script_name:lower()
            end
            if kind == "sources" then
                copy.kind = endpoint.kind == nil and "creature" or endpoint.kind
                assert(copy.kind == "creature" or copy.kind == "virtual" or guarded(copy), "NATIVE_SOURCE_KIND_UNSUPPORTED")
                if not store_source and not script_source and not template_source and not virtual_source then copy.sprite = true end
                copy.payload = assert(payload(endpoint.payload), "NATIVE_PAYLOAD_UNSUPPORTED")
                if copy.kind == "guarded-container" then
                    assert(integer(endpoint.x,0,32767) and integer(endpoint.y,0,32767) and
                        integer(endpoint.container_type or 4,0,65535), "NATIVE_GUARDED_SOURCE_INVALID")
                    copy.sprite, copy.x, copy.y = false, endpoint.x, endpoint.y
                    copy.container_type = endpoint.container_type or 4
                end
                if guarded(copy) then
                    guarded_ids[id] = true
                    guarded_count = guarded_count + 1
                end
                source_resrefs[copy.payload.resref] = true
            else
                -- The accepted fixture predates this field; absent means a ground pile.
                copy.kind = quest_target and 'creature' or (endpoint.kind == nil and "ground-pile" or endpoint.kind)
                assert(copy.kind == "ground-pile" or copy.kind == "container" or copy.kind == "creature" or copy.kind == "group" or copy.kind == "store",
                    "NATIVE_TARGET_KIND_UNSUPPORTED")
                if copy.kind == "creature" or copy.kind == "group" then
                    assert(endpoint.x == nil and endpoint.y == nil, "NATIVE_POSITION_INVALID")
                    copy.sprite, creatures = true, true
                elseif copy.kind == "ground-pile" or copy.kind == "container" then
                    assert(integer(endpoint.x, 0, 32767) and integer(endpoint.y, 0, 32767), "NATIVE_POSITION_INVALID")
                    copy.x, copy.y = endpoint.x, endpoint.y
                    if copy.kind == "container" then
                        assert(integer(endpoint.container_type, 0, 65535), "NATIVE_CONTAINER_TYPE_INVALID")
                        copy.container_type = endpoint.container_type
                    end
                end
            end
            if endpoint.loot_latch ~= nil then
                local latch=endpoint.loot_latch
                assert(kind=='targets' and copy.kind=='container' and type(latch)=='table' and
                    type(latch.activation_global)=='string' and #latch.activation_global<=32 and
                    latch.activation_global:match('^FLPR_C[A-Z0-9_]+$') and
                    type(latch.latch_global)=='string' and #latch.latch_global<=32 and
                    latch.latch_global:match('^[A-Za-z][A-Za-z0-9_]*$') and
                    (latch.latch_scope=='GLOBAL' or latch.latch_scope==copy.area:upper()),
                    'NATIVE_CONTAINER_LATCH_INVALID')
                assert(type(api.EEex_GameState_GetGlobalInt)=='function',
                    'NATIVE_CONTAINER_LATCH_API_MISSING')
                if latch.latch_scope=='GLOBAL' then
                    assert(type(api.EEex_GameState_SetGlobalInt)=='function',
                        'NATIVE_CONTAINER_LATCH_API_MISSING')
                else
                    assert(type(api.EEex_Area_GetVariableInt)=='function' and
                        type(api.EEex_Area_SetVariableInt)=='function','NATIVE_CONTAINER_LATCH_API_MISSING')
                end
                copy.loot_latch={activation_global=latch.activation_global,
                    latch_global=latch.latch_global,latch_scope=latch.latch_scope}
            end
            self[kind][id] = copy
        end
    end
    if guarded_count > 0 or fixture.source_gate ~= nil then
        local gate = fixture.source_gate
        assert((guarded_count > 0 or type(fixture.spell_shield)=='table' or
            type(fixture.cursed_disguises)=='table') and type(gate) == "table" and
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
    for _, endpoint in pairs(self.targets) do
        if endpoint.kind == "store" then
            local shop = api.FLPR and api.FLPR.Shop
            assert(type(shop) == "table" and type(shop.inspect_target) == "function" and
                type(shop.deliver) == "function", "NATIVE_SHOP_SERVICE_MISSING")
            break
        end
    end
    return self
end

-- Returns nil for a usable creature target, otherwise the reason it cannot
-- receive a reward now. None of these states permits a substitute recipient.
function Adapter:_creature_state(object, party_reward)
    local api = self.api
    if api.EEex_GameObject_IsSprite(object, false) ~= true then return "TARGET_NOT_ALIVE" end
    local portrait = api.EEex_Sprite_GetPortraitIndex(object)
    if portrait ~= -1 then
        assert(integer(portrait, 0, 5), "NATIVE_PORTRAIT_INVALID")
        if not party_reward then return "TARGET_PARTY_MEMBER" end
    end
    -- CMessageAddItem::Run forwards a familiar's item to the protagonist.
    local typeAI = object.m_typeAI
    assert(typeAI and integer(typeAI.m_EnemyAlly, 0, 255), "NATIVE_ALLEGIANCE_INVALID")
    if typeAI.m_EnemyAlly == FAMILIAR_ALLEGIANCE then return "TARGET_FAMILIAR" end
    return nil
end

function Adapter:_resolve(endpoint, kind, visible_area)
    local api, area = self.api, visible_area or self.api.EEex_Area_GetVisible()
    if not area or (endpoint.area~='*' and text(area.m_resref) ~= endpoint.area) then return nil, "AREA_UNAVAILABLE" end
    if endpoint.claim_global and api.EEex_GameState_GetGlobalInt(endpoint.claim_global)~=1 then
        return nil,'QUEST_NOT_CLAIMED'
    end
    if endpoint.quest_event then
        if endpoint.spell_event then
            local service=api.FLPR and api.FLPR.Spell
            if not service or service.callback~=api.FLPRKEY then return nil,'CAMPAIGN_SPELL_SERVICE_MISSING' end
            if service.last_error then return nil,'CAMPAIGN_SPELL_CALLBACK_FAILED:'..service.last_error end
        end
        if api.EEex_GameState_GetGlobalInt(endpoint.quest_event)~=1 then return nil,'QUEST_NOT_CLAIMED' end
        local object
        if endpoint.recipient_local then
            local matches,seen=0,{}
            local function scan(candidate)
                if not candidate then return end
                candidate=api.EEex_GameObject_CastUserType(candidate)
                if not candidate or api.EEex_GameObject_IsSprite(candidate,true)~=true then return end
                local address=pointer(api,candidate)
                if seen[address] then return end
                seen[address]=true
                if api.EEex_Sprite_GetLocalInt(candidate,endpoint.recipient_local)==1 then
                    matches,object=matches+1,candidate
                end
            end
            for _,field in ipairs(lists) do
                assert(area[field],'NATIVE_AREA_LIST_MISSING')
                api.EEex_Utility_IterateCPtrList(area[field],function(id)
                    assert(integer(id,0,2147483647),'NATIVE_OBJECT_ID_INVALID')
                    scan(api.EEex_GameObject_Get(id))
                end)
            end
            for index=0,5 do scan(api.EEex_Sprite_GetInPortrait(index)) end
            if matches~=1 then return nil,matches==0 and 'OWNER_MISSING' or 'OWNER_AMBIGUOUS' end
        else object=api.EEex_Sprite_GetInPortrait(0) end
        if not object then return nil,'OWNER_MISSING' end
        local reason=self:_creature_state(object,true)
        if reason then return nil,reason end
        return {object=object,id=object.m_id,pointer=pointer(api,object)}
    end
    local found, matches, seen, wrong_container = nil, 0, {}, false
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
                if not endpoint.sprite and (not object.m_pos or object.m_pos.x~=endpoint.x or
                    object.m_pos.y~=endpoint.y or object.m_containerType~=(endpoint.container_type or 4)) then
                    wrong_container=true;return
                end
                matches, found = matches + 1, { object = object, id = id, pointer = pointer(api, object) }
            end
        end)
    end
    if matches ~= 1 then
        if matches==0 and wrong_container then return nil,'TARGET_NOT_FIXTURE_GROUND_PILE' end
        return nil, matches == 0 and "OWNER_MISSING" or "OWNER_AMBIGUOUS"
    end
    if kind == "source" and endpoint.sprite then
        if api.EEex_GameObject_IsSprite(found.object, false) ~= true then return nil, "SOURCE_NOT_ALIVE" end
    elseif endpoint.kind == "creature" then
        local reason = self:_creature_state(found.object)
        -- The unusable owner is returned only for read-only diagnostics.
        if reason then return nil, reason, found end
    elseif not endpoint.sprite and (found.object.m_containerType ~= (endpoint.container_type or 4) or not found.object.m_pos or
        found.object.m_pos.x ~= endpoint.x or found.object.m_pos.y ~= endpoint.y) then
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
        -- These type/subtype pairs are the engine's own constants, the same in
        -- BG:EE and BG2:EE 2.6.6.0 and 2.7.3.0 (each class's GetMsgType/GetMsgSubType).
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

function Adapter:_preserve_message_flags(message, expected)
    if message.payload.flags == expected.flags then return true end
    local updated = false
    self.api.EEex_Utility_IterateCPtrList(self.api.EngineGlobals.g_pBaldurChitin.m_cMessageHandler.m_messageList,
        function(value)
            if pointer(self.api, value) ~= message.pointer then return end
            local type_name = message.form == "creature" and "CMessageAddItem" or "CMessageContainerAddItem"
            local item = self.api.EEex_CastUD(value, type_name).m_item
            assert(pointer(self.api, item) == message.embedded_item_pointer, "CREATED_INSTANCE_CHANGED")
            item.m_flags = expected.flags
            assert(same(self:_item(item).payload, expected), "CREATED_FLAGS_NOT_PRESERVED")
            updated = true
        end)
    assert(updated, "CREATED_INSTANCE_CHANGED")
    message.payload.flags = expected.flags
    return true
end

function Adapter:_preserve_container_flags(owner, created, expected)
    if created.payload.flags == expected.flags then return true end
    local updated = false
    self.api.EEex_Utility_IterateCPtrList(owner.object.m_lstItems, function(item)
        if pointer(self.api, item) ~= created.pointer then return end
        assert(same(self:_item(item).payload, created.payload), "CREATED_INSTANCE_CHANGED")
        item.m_flags = expected.flags
        assert(same(self:_item(item).payload, expected), "CREATED_FLAGS_NOT_PRESERVED")
        updated = true
    end)
    assert(updated, "CREATED_INSTANCE_CHANGED")
    created.payload.flags = expected.flags
    return true
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
    if not header or not integer(header.maxStackable, 0, 65535) or
        not integer(header.itemFlags, 0, 4294967295) then return nil, "ITEM_STACK_LIMIT_UNAVAILABLE" end
    -- Resource bit 2 enables movement/drop, bit 3 makes the ground icon visible.
    if math.floor(header.itemFlags / 4) % 2 ~= 1 or math.floor(header.itemFlags / 8) % 2 ~= 1 then
        return nil, "ITEM_NOT_VISIBLE_DROPPABLE"
    end
    return true, nil, header.maxStackable
end

function Adapter:inspect_source(id)
    local endpoint = self.sources[id]
    if not endpoint then return nil, "SOURCE_UNKNOWN" end
    if guarded(endpoint) then return nil, "SOURCE_GUARD_REQUIRED" end
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

-- Corroborates a guard event against the exact visible area and owner.
-- A missing area or owner is never evidence that the guarded item was removed.
function Adapter:guarded_source_remaining(id, area_pointer, owner_pointer)
    local endpoint = self.sources[id]
    if not endpoint then return nil, "SOURCE_UNKNOWN" end
    if not guarded(endpoint) then return nil, "SOURCE_NOT_GUARDED" end
    if endpoint.kind == "guarded-store" or endpoint.kind == "guarded-script" or
        endpoint.kind == "guarded-template" then return nil, "SOURCE_GUARD_REQUIRED" end
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
    for _, item in ipairs(self:_items(owner, endpoint.sprite)) do
        if item.payload.resref == endpoint.payload.resref then remaining = remaining + 1 end
    end
    return { remaining = remaining, area_pointer = area_pointer, owner_pointer = owner_pointer }
end

function Adapter:_reward(source_id, unit)
    local source = self.sources[source_id]
    if not source then return nil end
    if not self.reward_payload then return source.payload end
    local result = assert(payload(self.reward_payload(unit)), 'REWARD_PAYLOAD_INVALID')
    self.source_resrefs[result.resref] = true
    return result
end

-- These dialogue rewards already live in the speaker's inventory, so combat
-- and pickpocketing remain valid alternatives. Wait for delivery to settle,
-- then use the original transfer action; never recreate a missing item.
function Adapter:transfer_inventory_reward(speaker, target_id, assignments, phases)
    local endpoint=self.targets[target_id]
    if not endpoint then return -1 end -- Target excluded from this campaign.
    assert(endpoint.inventory_reward, 'NATIVE_INVENTORY_REWARD_INVALID')
    local rewards={}
    for _,assignment in ipairs(assignments) do
        if assignment.target_id==target_id then
            if assignment.delivery_phase~=phases.held and assignment.delivery_phase~=phases.confirmed then
                return 0
            end
            rewards[#rewards+1]=assert(self:_reward(assignment.source_id,assignment.unit),
                'NATIVE_INVENTORY_REWARD_MISSING')
        end
    end
    if #rewards==0 then return -1 end
    if self.pending then return 0 end
    local owner,reason=self:_resolve(endpoint,'target')
    assert(owner,reason)
    assert(pointer(self.api,speaker)==owner.pointer,'NATIVE_INVENTORY_REWARD_SPEAKER_CHANGED')
    for _,reward in ipairs(rewards) do
        self.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly(
            'GiveItem("'..reward.resref..'",LastTalkedToBy)',speaker)
    end
    return -1
end

function Adapter:_target(id, source_id, unit)
    local endpoint=self.targets[id]
    if not endpoint or endpoint.kind~='group' then return endpoint end
    local source=self.sources[source_id]
    if not source or not integer(unit,1,4096) then return nil end
    local choices=endpoint.eligible[self:_reward(source_id, unit).resref]
    if not choices then return nil end
    local draw=((self.seed+unit*48271)%2147483647*16807)%2147483647
    local member=endpoint.members[choices[draw%#choices+1]]
    return {kind='creature',sprite=true,area=endpoint.area,script_name=member.script_name,
        identity=endpoint.identity}
end

-- The original flrkeep/flrsamia/flrsafan scripts latch !Contains(reward),
-- not party ownership. A frozen confirmed delivery is the prerequisite for
-- observing that absence; an unloaded or unresolved container proves nothing.
function Adapter:observe_container_latches(assignments, confirmed_phase)
    local api,waiting=self.api,{}
    for _,assignment in ipairs(assignments) do
        local endpoint=self.targets[assignment.target_id]
        local latch=endpoint and endpoint.loot_latch
        if latch and assignment.delivery_phase==confirmed_phase and
            api.EEex_GameState_GetGlobalInt(latch.activation_global)==1 then
            local key=latch.latch_scope..'/'..latch.latch_global
            if not self.container_latched[key] then
                local area=api.EEex_Area_GetVisible()
                local loaded=area and text(area.m_resref)==endpoint.area
                local global=latch.latch_scope=='GLOBAL'
                -- EEex_Area_{Get,Set}VariableInt access the loaded area's
                -- saved m_variables; GameState functions only cover GLOBAL.
                local function read()
                    if global then return api.EEex_GameState_GetGlobalInt(latch.latch_global) end
                    if loaded then return api.EEex_Area_GetVariableInt(area,latch.latch_global) end
                end
                if read()==1 then
                    self.container_latched[key]=true
                elseif loaded and not self.pending then
                    local owner=self:_resolve(endpoint,'target',area)
                    if owner then
                        local reward=assert(self:_reward(assignment.source_id,assignment.unit),
                            'NATIVE_CONTAINER_LATCH_REWARD_MISSING')
                        local present=false
                        for _,item in ipairs(self:_items(owner,false)) do
                            if item.payload.resref==reward.resref then present=true;break end
                        end
                        if not present then
                            if global then api.EEex_GameState_SetGlobalInt(latch.latch_global,1)
                            else api.EEex_Area_SetVariableInt(area,latch.latch_global,1) end
                            assert(read()==1,'NATIVE_CONTAINER_LATCH_WRITE_FAILED')
                            self.container_latched[key]=true
                        end
                    end
                end
                waiting[key]=true
            end
        end
    end
    -- Samia's two slots share one latch: either absent reward satisfies both.
    for key in pairs(waiting) do if not self.container_latched[key] then return true,true end end
    return true,false
end

function Adapter:inspect_target(id, source_id, unit)
    local endpoint = self:_target(id, source_id, unit)
    if not endpoint then return nil, "TARGET_UNKNOWN" end
    local source = source_id and self.sources[source_id]
    if source_id and not source then return nil, "SOURCE_UNKNOWN" end
    self.reservations[endpoint.identity] = nil
    if endpoint.kind == "store" then
        local ready, reason = self.api.FLPR.Shop:inspect_target(endpoint)
        if not ready then return nil, reason or "SHOP_TARGET_UNAVAILABLE" end
        self.reservations[endpoint.identity] = { kind = "deliver", id = id }
        return { identity = endpoint.identity, target_id = id, accepts = 1 }
    end
    local owner, reason = self:_resolve(endpoint, "target")
    if not owner then return nil, reason end
    local items = self:_items(owner, endpoint.sprite)
    if source then
        local supported, stack_limit
        local reward = self:_reward(source_id, unit)
        supported, reason, stack_limit = self:_supported(reward)
        if not supported then return nil, reason end
        if stack_limit > 1 then
            for _, item in ipairs(items) do
                if item.payload.resref == reward.resref then return nil, "TARGET_STACK_COLLISION" end
            end
        end
    end
    if endpoint.kind == "creature" then
        -- Checked before the core journals anything, so these are deferrals.
        local empty = empty_backpack(items)
        if #empty == 0 then return nil, "TARGET_INVENTORY_FULL" end
        if creature_conflict(self:_queue(), owner.id, self.source_resrefs) then return nil, "TARGET_QUEUE_BUSY" end
        self.reservations[endpoint.identity] = { owner = owner, items = items, kind = "deliver", id = id, empty = empty }
        return { identity = endpoint.identity, target_id = id, accepts = 1 }
    end
    if endpoint.kind == "ground-pile" and #items ~= 0 then return nil, "TARGET_NOT_EMPTY" end
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
    if command.kind == "suppress" and source and guarded(source) then
        return nil, "SOURCE_GUARD_REQUIRED"
    end
    -- Suppression always uses the original item; an authored reward variant
    -- (such as the TotSC tome) is resolved independently for this reward unit.
    if command.kind == "deliver" and source then
        source = {payload = self:_reward(command.source_id, command.index)}
    end
    if self.tick == nil then return nil, "OBSERVATION_CLOCK_MISSING" end
    local tried = self.attempted[command.session]
    if tried and tried[command.nonce] then return nil, "ALREADY_ATTEMPTED" end
    local reserved = self.reservations[command.identity]
    if not reserved or not source or reserved.kind ~= command.kind or
        not same(payload(command.payload), source.payload) then return nil, "RESERVATION_MISMATCH" end
    local kind = command.kind == "suppress" and "source" or "target"
    local endpoint = kind == "source" and source or self:_target(command.target_id, command.source_id, command.index)
    if not endpoint or endpoint.identity ~= command.identity or
        reserved.id ~= (kind == "source" and command.source_id or command.target_id) then return nil, "RESERVATION_MISMATCH" end
    if kind == "target" and endpoint.kind == "store" then
        tried = tried or {}; self.attempted[command.session] = tried; tried[command.nonce] = true
        local receipt = { session = command.session, kind = command.kind, index = command.index,
            nonce = command.nonce, revision = command.revision, identity = command.identity,
            source_id = command.source_id, target_id = command.target_id,
            payload = payload(source.payload) }
        self.pending = { receipt = receipt, endpoint = endpoint, kind = kind,
            tick = self.tick, complete = false }
        self.reservations[command.identity] = nil
        local delivered, reason = self.api.FLPR.Shop:deliver(endpoint, source.payload, command.index)
        if not delivered then
            self.last_error = reason or "SHOP_DELIVERY_UNPROVEN"
            return nil, self.last_error
        end
        self.pending.complete = true
        return true
    end
    local owner, reason = self:_resolve(endpoint, kind)
    if not owner then return nil, reason end
    if owner.id ~= reserved.owner.id or owner.pointer ~= reserved.owner.pointer then return nil, "OWNER_CHANGED" end
    local before = self:_items(owner, endpoint.sprite)
    if not unchanged(reserved.items, before) then return nil, "INVENTORY_CHANGED" end
    local supported, stack_limit; supported, reason, stack_limit = self:_supported(source.payload)
    if not supported then return nil, reason end
    if kind == "target" and stack_limit > 1 then
        -- A stack merge can consume the reward without creating a distinct
        -- pointer. Reject it before the native action, preserving exact units.
        for _, item in ipairs(before) do
            if item.payload.resref == source.payload.resref then return nil, "TARGET_STACK_COLLISION" end
        end
    end
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
        -- Party rewards also enqueue a "party gains item" notification. Bind
        -- the new item message; unrelated feedback is not another delivery.
        for _, entry in ipairs(after_queue.additions) do
            if not before_queue.addresses[entry.pointer] then added[#added + 1] = entry.pointer end
        end
        local message = #added == 1 and addition_at(after_queue, added[1]) or nil
        -- GiveItemCreate chooses identification from the ITM's lore value;
        -- it does not accept the original inventory's identified flag. Bind
        -- the newly created message first, then preserve that selected flag.
        if not unchanged(reserved.items, after) or not message or message.form ~= "creature" or
            message.target ~= owner.id or message.source ~= owner.id or not constructor_payload(message.payload, p) or
            creature_conflict(after_queue, owner.id, self.source_resrefs, message.pointer) then
            self.last_error = "CREATED_INSTANCE_UNPROVEN"; return nil, self.last_error
        end
        self:_preserve_message_flags(message, p)
        self.pending.queued, self.pending.empty = message, reserved.empty
    else
        local after_queue, added, conflicting = self:_queue(), {}, 0
        for _, message in ipairs(after_queue.additions) do
            if not before_queue.addresses[message.pointer] then added[#added + 1] = message end
            if conflicts(message, owner.id, p) then conflicting = conflicting + 1 end
        end
        local placed = placed_item(before, after, {#before + 1})
        if placed and constructor_payload(placed.payload, p) and #added == 0 and conflicting == 0 then
            self:_preserve_container_flags(owner, placed, p)
            self.pending.created_pointer = placed.pointer
        elseif unchanged(before, after) and #added == 1 and conflicting == 1 and added[1].target == owner.id and added[1].source == owner.id and
            added[1].form == "container" and added[1].slot == 32767 and constructor_payload(added[1].payload, p) then
            -- The engine queues an embedded CItem value, then Run() heap-copies
            -- it and appends to the container, preserving its earlier contents. Message and inventory
            -- pointers are intentionally different. Bind the exact new message
            -- to this nonce before allowing later observations to establish the
            -- resulting pointer; never resubmit CreateItem.
            self:_preserve_message_flags(added[1], p)
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
    if pending.kind == "target" and pending.endpoint.kind == "store" then
        receipt.created, receipt.nonce_bound_instance, receipt.lootable = 1, 1, 1
        self.pending = nil
        return receipt
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
                    if not unchanged(pending.before, items) then return nil, "DELIVERY_EARLY_ITEM" end
                    return nil, "DELIVERY_SETTLING"
                end
                if queue.addresses[pending.queued.pointer] then return nil, "DELIVERY_MESSAGE_REUSED" end
                local placed = placed_item(pending.before, items, {#pending.before + 1})
                if not placed then return nil, "DELIVERY_MESSAGE_LOST" end
                if not same(placed.payload, receipt.payload) then
                    -- CMessageContainerAddItem::Run copies the message's item with
                    -- the same CItem copy constructor as CMessageAddItem::Run, which
                    -- can identify a zero-lore item again (AMUL12 on EET 2.7.3).
                    -- Preserve the source's flags on the one proven new container
                    -- instance, just as on its message; nothing else may differ.
                    local found = placed.payload
                    local expected = { resref = found.resref, charges = found.charges,
                        expiration = found.expiration, flags = receipt.payload.flags }
                    if receipt.payload.flags % 2 ~= 0 or found.flags ~= receipt.payload.flags + 1 or
                        not same(expected, receipt.payload) then return nil, "DELIVERY_PAYLOAD_CHANGED" end
                    self:_preserve_container_flags(owner, placed, receipt.payload)
                end
                -- The exact message has been consumed. Its append operation
                -- produced one new full-payload item and preserved existing
                -- contents. Pin that actual heap copy, then demand
                -- another game tick before publishing the receipt.
                pending.created_pointer, pending.pinned_tick = placed.pointer, self.tick
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
        local placed = placed_item(pending.before, items, {#pending.before + 1})
        if not placed or placed.pointer ~= pending.created_pointer or
            not same(placed.payload, receipt.payload) then return nil, "CREATED_INSTANCE_CHANGED" end
        local supported; supported, reason = self:_supported(receipt.payload)
        if not supported then return nil, reason end
        -- Container access (locks, traps, and normal opening) remains the
        -- engine's responsibility. Only its inventory has gained a reward.
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
        if not same(placed.payload, receipt.payload) then
            -- CMessageAddItem::Run can identify a zero-lore item again while
            -- copying it from the message. Preserve the source's flags on the
            -- one proven new inventory instance, just as on its message.
            local expected=payload(placed.payload)
            expected.flags=receipt.payload.flags
            if receipt.payload.flags%2~=0 or placed.payload.flags~=receipt.payload.flags+1 or
                not same(expected,receipt.payload) then return nil,'DELIVERY_PAYLOAD_CHANGED' end
            local item=owner.object.m_equipment.m_items:get(placed.slot)
            assert(pointer(self.api,item)==placed.pointer and same(self:_item(item).payload,placed.payload),
                'CREATED_INSTANCE_CHANGED')
            item.m_flags=receipt.payload.flags
            assert(same(self:_item(item).payload,receipt.payload),'CREATED_FLAGS_NOT_PRESERVED')
            placed.payload.flags=receipt.payload.flags
        end
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
    if pending.endpoint.quest_event and self.api.EEex_Sprite_GetPortraitIndex(owner.object)~=-1 then
        receipt.lootable,receipt.held=1,nil
        receipt.holder,receipt.slot='party',placed.slot
        self.pending=nil
        return receipt
    end
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
    local tracked, tracked_count = {}, 0
    for _, binding in pairs(self.held) do
        if binding.owner_id == held.owner_id and binding.owner_pointer == held.owner_pointer and
            binding.payload.resref == held.payload.resref then
            if tracked[binding.item_pointer] then return nil, "HELD_INSTANCE_CHANGED" end
            tracked[binding.item_pointer] = binding
            tracked_count = tracked_count + 1
        end
    end
    local matches, found = 0, nil
    for _, item in ipairs(self:_items(owner, true)) do
        if item.payload.resref == held.payload.resref then
            matches = matches + 1
            local binding = tracked[item.pointer]
            if not binding or item.slot ~= binding.slot or not same(item.payload, binding.payload) then
                return nil, "HELD_INSTANCE_CHANGED"
            end
            tracked[item.pointer] = nil
            if item.pointer == held.item_pointer then found = item end
        end
    end
    if not found or matches ~= tracked_count or next(tracked) then return nil, "HELD_INSTANCE_CHANGED" end
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
    local seen, matches, original_occurrences, found = {}, 0, 0, nil
    local function scan(object, id, party)
        if not object then return end
        local ptr = pointer(api, object)
        if seen[ptr] then return end
        seen[ptr] = true
        local sprite = api.EEex_GameObject_IsSprite(object, true)
        if not sprite and not object.m_lstItems then return end
        for _, item in ipairs(self:_items({object=object}, sprite)) do
            if item.pointer == held.item_pointer then original_occurrences = original_occurrences + 1 end
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
    if matches ~= 1 or original_occurrences ~= 0 or not found then
        return nil, "DROP_GROUND_INSTANCE_UNPROVEN"
    end
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
            if endpoint.kind == "guarded-template" then
                result[kind][id] = { unavailable = "SOURCE_GUARD_REQUIRED",
                    creature_resource = endpoint.creature_resource }
            elseif endpoint.kind == "virtual" then
                result[kind][id] = { payload = payload(endpoint.payload) }
            elseif endpoint.kind == "guarded-script" then
                result[kind][id] = { unavailable = "SOURCE_GUARD_REQUIRED", adapter_id = endpoint.adapter_id }
            elseif endpoint.kind == "guarded-store" then
                result[kind][id] = { unavailable = "SOURCE_GUARD_REQUIRED", store = endpoint.store,
                    record_index = endpoint.record_index, quantity = endpoint.quantity }
            elseif endpoint.kind == "store" then
                result[kind][id] = { store = endpoint.store, identity = endpoint.identity }
            else
                local label = kind == "sources" and "source" or "target"
                local owner, reason, unusable = self:_resolve(endpoint, label)
                -- A dead or party-owned creature's inventory is evidence, not a target.
                local seen = owner or unusable
                result[kind][id] = seen and { object_id = seen.id, pointer = seen.pointer, unavailable = reason,
                    items = self:_items(seen, endpoint.sprite) } or { unavailable = reason }
            end
        end
    end
    return result
end

function Adapter:destroy()
    self.pending, self.tick, self.last_error = nil, nil, nil
    self.reservations, self.attempted, self.held, self.container_latched = {}, {}, {}, {}
end

return Native
