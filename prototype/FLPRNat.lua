-- Bounded native bridge for the disposable Mode 1 fixture, not arbitrary loot.
-- Unique creature sources; named containers or unique living creature targets;
-- one unexpired, droppable item record per operation, with exact stack charges.
-- No listeners or autoloading here.
local Native = {}
-- The ordinary transport consumes this message on an engine update. Allow a
-- conservative 20 game seconds (15 ticks/second), never unlimited settling.
-- Paused time does not consume this allowance; this is not a wall-clock timer.
-- A reward found nowhere gets it once more from then, to fall at a party
-- member's feet (Adapter:_lost_reward).
Native.MAX_SETTLE_TICKS = 300
-- A reward that reached a creature as it died lies in the body until the
-- death drop takes it along. One that came after that drop would stay there
-- for good; this many game ticks after CIR saw the death, CIR drops it at the
-- corpse itself (Adapter:_corpse_reward). A reward whose creature is gone from
-- the game and that is nowhere falls at a party member's feet this many ticks
-- after CIR saw the creature gone (Adapter:_lost_reward).
Native.DEATH_DROP_TICKS = 15
-- CIR looks for a reward its dying creature dropped (Adapter:_find_dropped)
-- at most once in this many game ticks: each look reads every object of the
-- area.
Native.DROP_SCAN_TICKS = 5
-- An owed treasury handover (Adapter:retry_handovers) is tried again
-- after this many game ticks.
Native.HANDOVER_RETRY_TICKS = 30
-- A reward whose marked recipient no one in the visible area or the party
-- carries waits this many host steps (one a game tick, 60 game seconds), long
-- enough for a reply's queued mark to run once its talk is over, then goes to
-- the protagonist (Adapter:_recipient). Steps count, not game time: resting or
-- travelling moves the clock by hours in one step.
Native.RECIPIENT_GRACE_STEPS = 900
-- CMessageAddItem::Run scans these internal backpack slots (see
-- docs/native-creature-transport-evidence.md). A full backpack displaces an
-- unrelated item to the ground, so creature delivery requires a free one.
Native.BACKPACK_FIRST, Native.BACKPACK_LAST = 18, 33
-- CGameAIBase::CreateItem on a sprite that is not in the party stores the item
-- in the highest empty internal slot from 34 down to 15, synchronously; with
-- none free it queues a ground pile instead (see the transport evidence).
Native.CREATE_FIRST, Native.CREATE_LAST = 15, 34
local FAMILIAR_ALLEGIANCE = 3
-- The engine counts each death of a creature in GLOBAL SPRITE_IS_DEAD<script
-- name>, the variable Dead() reads; the games' own scripts read it too (BG2:EE
-- AR0607.BCS: SPRITE_IS_DEADQuayle). A variable name holds 32 characters.
local DEATH_VARIABLE, VARIABLE_LENGTH = "SPRITE_IS_DEAD", 32
local Adapter = {}
Adapter.__index = Adapter
local lists = { "m_lVertSort", "m_lVertSortBack", "m_lVertSortFlight", "m_lVertSortUnder" }
local required = { "EEex_Area_GetVisible", "EEex_GameObject_Get", "EEex_GameObject_CastUserType",
    "EEex_GameObject_IsSprite", "EEex_Utility_IterateCPtrList", "EEex_UDToPtr",
    "EEex_Resource_Demand", "EEex_Action_ExecuteResponseStringOnAIBaseInstantly", "EEex_CastUD" }
-- The last three serve a reward whose creature left no body (Adapter:_inspect_party_ground).
local creature_required = { "EEex_Sprite_GetPortraitIndex", "EEex_GameState_GetGlobalInt",
    "EEex_Sprite_GetInPortrait", "EEex_Sprite_GetName" }

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
-- A reward keeps its source's flags, except undroppable (8): the export
-- refuses that source, since such a reward would never leave a creature.
local function payload(value)
    if type(value) ~= "table" or not resref(value.resref) or value.expiration ~= 0 or
        not integer(value.flags, 0, 4294967295) or value.flags % 16 >= 8 or
        type(value.charges) ~= "table" then return nil end
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
-- The reward as CreateItem/GiveItemCreate made it, or nil. They choose
-- identification from the ITM, and they make every usage count 0 of a
-- stackable item 1 (2.7.3.0: SCRL75 0/0/0 is created 1/1/1, POTN08 4/0/0
-- 4/1/1; WAND10 and PLAT01 keep 0/0/0). Every other field is the reward's.
-- The source's flags are then set on the one proven new instance.
local function created_payload(actual, expected, stack_limit)
    local created = payload(actual)
    if not created or created.resref ~= expected.resref or created.expiration ~= expected.expiration or
        (created.flags ~= 0 and created.flags ~= 1) then return nil end
    for index = 1, 3 do
        local count = expected.charges[index]
        if created.charges[index] ~= count and not (count == 0 and stack_limit > 1 and
            created.charges[index] == 1) then return nil end
    end
    created.flags = expected.flags
    return created
end
local function pointer(api, object)
    local address = api.EEex_UDToPtr(object)
    assert(address ~= nil and address ~= 0, "NATIVE_POINTER_UNAVAILABLE")
    return tostring(address)
end
-- The object ids in an area's lists.
local function area_ids(api, area)
    local ids = {}
    for _, field in ipairs(lists) do
        assert(area[field], "NATIVE_AREA_LIST_MISSING")
        api.EEex_Utility_IterateCPtrList(area[field], function(id) ids[id] = true end)
    end
    return ids
end
-- The game text of the name the player sees on an item, by its flags
-- (identified or not), or nil.
local function shown_name(api, item)
    local header = api.EEex_Resource_Demand(item.resref, "ITM")
    for _, field in ipairs(item.flags % 2 == 1 and { "identifiedName", "genericName" } or { "genericName" }) do
        local ref = header and header[field]
        if integer(ref, 1, 2147483646) then return ref end
    end
end
-- What the player is told of the reward <p> at a party member's feet
-- (FLPRHost's notice).
local function party_notice(api, member, p)
    local name = api.EEex_Sprite_GetName(member)
    return { item = shown_name(api, p), resref = p.resref, member = type(name) == "string" and name or nil }
end

function Native.new(api, fixture, seed, reward_payload)
    assert(type(api) == "table" and type(fixture) == "table", "NATIVE_CONFIG_INVALID")
    for _, name in ipairs(required) do assert(type(api[name]) == "function", name .. "_MISSING") end
    local self = setmetatable({ api = api, sources = {}, targets = {}, reservations = {},
        attempted = {}, tick = nil, pending = nil, last_error = nil, source_resrefs = {}, held = {},
        placed = {}, seed=seed, reward_payload=reward_payload, container_latched={}, route_targets={},
        owed_handovers={}, parked={}, steps=0, unmarked={} }, Adapter)
    local identities, source_resrefs, creatures, guarded_ids, routes = {}, self.source_resrefs, false, {}, {}
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
            local first_target = kind == "targets" and endpoint.kind == "first-of"
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
            elseif first_target then
                assert(integer(seed,1,2147483646) and type(endpoint.groups)=='table' and #endpoint.groups>0,
                    'NATIVE_FIRST_OF_INVALID')
                for _,group in ipairs(endpoint.groups) do
                    assert(type(group)=='table' and type(group.members)=='table' and #group.members>0 and
                        type(group.corpse_only)=='boolean' and (group.eligible==nil or type(group.eligible)=='table'),
                        'NATIVE_FIRST_OF_INVALID')
                    for _,member in ipairs(group.members) do
                        assert(type(member)=='table' and resref(member.resref) and resref(member.area) and
                            type(member.script_name)=='string' and #member.script_name>0,
                            'NATIVE_FIRST_OF_MEMBER_INVALID')
                    end
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
            elseif first_target then
                -- Each member is a creature endpoint of its own area, under
                -- the place's identity; a group with candidates gives one of
                -- them per reward (Adapter:_first_of).
                copy.groups={}
                for g,group in ipairs(endpoint.groups) do
                    local members,eligible={},nil
                    for n,member in ipairs(group.members) do
                        members[n]={kind='creature',sprite=true,area=resref(member.area),
                            script_name=member.script_name:lower(),identity=endpoint.identity,
                            corpse_only=group.corpse_only or nil}
                    end
                    if group.eligible then
                        eligible={}
                        for item,candidates in pairs(group.eligible) do
                            assert(resref(item) and type(candidates)=='table' and #candidates>0,
                                'NATIVE_FIRST_OF_CANDIDATES_INVALID')
                            eligible[item]={}
                            for n,index in ipairs(candidates) do
                                assert(integer(index,1,#members),'NATIVE_FIRST_OF_CANDIDATES_INVALID')
                                eligible[item][n]=index
                            end
                        end
                    end
                    copy.groups[g]={members=members,eligible=eligible}
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
                assert(copy.kind == "ground-pile" or copy.kind == "container" or copy.kind == "creature" or
                    copy.kind == "group" or copy.kind == "first-of" or copy.kind == "store",
                    "NATIVE_TARGET_KIND_UNSUPPORTED")
                if copy.kind == "creature" or copy.kind == "group" or copy.kind == "first-of" then
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
            -- The Sahuagin treasury's place (Item Randomiser's sahtreas.fl): the
            -- king's reward reply hands its rewards to the payee
            -- (hand_over_container_reward); after that reply a reward not
            -- placed yet goes to the payee instead (Adapter:_target). The
            -- king's own place (tadpoles1.fl) and Tolgerias's (tolger.fl) wait
            -- for their reply the same way, but at a creature that receives only
            -- at his corpse (corpse_only): a living king may still pay, or destroy
            -- himself on the prince's side; Tolgerias pays in his house, and
            -- TOLGER2 fights in the planar sphere.
            if endpoint.corpse_only ~= nil then
                assert(kind=='targets' and endpoint.kind=='creature' and copy.quest_event==nil and
                    endpoint.corpse_only==true and endpoint.handover~=nil and copy.inventory_reward==nil,
                    'NATIVE_CORPSE_ONLY_INVALID')
                copy.corpse_only=true
            end
            if endpoint.handover ~= nil then
                local handover=endpoint.handover
                assert(kind=='targets' and (copy.kind=='container' or copy.corpse_only) and type(handover)=='table' and
                    type(handover.event_global)=='string' and #handover.event_global<=32 and
                    handover.event_global:match('^FLPR_E[A-Z0-9_]+$') and
                    type(handover.recipient_local)=='string' and
                    handover.recipient_local:match('^FLPR_R[A-Z0-9_]+$') and
                    handover.recipient_local:sub(7)==handover.event_global:sub(7) and
                    copy.claim_global==nil and copy.loot_latch==nil,'NATIVE_HANDOVER_INVALID')
                for _,name in ipairs({'EEex_GameState_GetGlobalInt','EEex_Sprite_GetLocalInt',
                    'EEex_Sprite_GetInPortrait','EEex_Object_EvalStringAsAIBase'}) do
                    assert(type(api[name])=='function','NATIVE_HANDOVER_API_MISSING')
                end
                copy.handover={event_global=handover.event_global,recipient_local=handover.recipient_local}
                copy.paid={kind='creature',sprite=true,area='*',quest_event=handover.event_global,
                    recipient_local=handover.recipient_local,spell_event=false,identity=copy.identity}
                creatures=true
            end
            -- A preset's row of an extended reward route names the route's own
            -- row of the same campaign, which this fixture has no endpoint of;
            -- each route has one row. FLPRScript and FLPRGat take the source's
            -- flags and copies by it, the reward reply names the target by it.
            local route=endpoint.route_target
            if kind=='sources' then route=endpoint.route_source end
            if route~=nil then
                local unit=kind=='sources' and 'item' or 'target'
                local prefix=kind=='sources' and '^original%-source:' or '^original%-target:'
                local campaign=id:match(prefix..'(bg[12]):[a-z0-9%-]+:mode2:'..unit..':[0-9]+$')
                assert(campaign and type(route)=='string' and
                    route:match(prefix..campaign..':mode2:'..unit..':[0-9]+$') and
                    not routes[route] and fixture[kind][route]==nil and
                    (kind=='targets' and (copy.inventory_reward or copy.handover) or
                     kind=='sources' and script_source and endpoint.adapter_id=='mode2-reward-v1'),
                    'NATIVE_ROUTE_INVALID')
                routes[route]=id
                if kind=='targets' then self.route_targets[route]=id end
            end
            self[kind][id] = copy
        end
    end
    if guarded_count > 0 or fixture.source_gate ~= nil then
        local gate = fixture.source_gate
        assert((guarded_count > 0 or type(fixture.spell_shield)=='table' or
            type(fixture.cursed_disguises)=='table' or type(fixture.duplicates)=='table') and type(gate) == "table" and
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

-- Every sprite, dead or alive, in the visible area or the party whose
-- LOCALS <name> is 1.
function Adapter:_all_marked(area, name)
    local api,seen,found=self.api,{},{}
    local function scan(candidate)
        if not candidate then return end
        candidate=api.EEex_GameObject_CastUserType(candidate)
        if not candidate or api.EEex_GameObject_IsSprite(candidate,true)~=true then return end
        local address=pointer(api,candidate)
        if seen[address] then return end
        seen[address]=true
        if api.EEex_Sprite_GetLocalInt(candidate,name)==1 then found[#found+1]=candidate end
    end
    for _,field in ipairs(lists) do
        assert(area[field],'NATIVE_AREA_LIST_MISSING')
        api.EEex_Utility_IterateCPtrList(area[field],function(id)
            assert(integer(id,0,2147483647),'NATIVE_OBJECT_ID_INVALID')
            scan(api.EEex_GameObject_Get(id))
        end)
    end
    for index=0,5 do scan(api.EEex_Sprite_GetInPortrait(index)) end
    return found
end

-- The recipient a reward reply (or the Key's spell) marked: the party member,
-- dead or alive, whose LOCALS <name> is 1, the first in portrait order. One
-- that is not in the party (it left, or never was) is not the one: someone
-- else gets the reward, as Chris asked on 2026-10-28 ("Gerne jemand anderem
-- geben"), and CIR picks the protagonist, as for an unmarked quest's reward.
-- Where no one in the visible area or the party has the mark, it may still be
-- on its way: a reply's ActionOverride marks its recipient only once its talk
-- is over. The reward waits then, and goes to the protagonist only after
-- RECIPIENT_GRACE_STEPS host steps without a mark, as for one marked who
-- stays elsewhere, or a mark the game dropped (session only).
function Adapter:_recipient(area, name)
    local api=self.api
    for index=0,5 do
        local member=api.EEex_Sprite_GetInPortrait(index)
        if member and api.EEex_Sprite_GetLocalInt(member,name)==1 then
            self.unmarked[name]=nil
            return member
        end
    end
    if #self:_all_marked(area,name)==0 then
        self.unmarked[name]=self.unmarked[name] or self.steps
        if self.steps-self.unmarked[name]<Native.RECIPIENT_GRACE_STEPS then return nil,'OWNER_MISSING' end
    else self.unmarked[name]=nil end
    local protagonist=api.EEex_Sprite_GetInPortrait(0)
    if not protagonist then return nil,'OWNER_MISSING' end
    return protagonist
end

-- <observing>: a parked reward (Adapter:settle_parked). Its quest recipient is
-- followed into any area, and returned where it died.
function Adapter:_resolve(endpoint, kind, visible_area, observing)
    local api, area = self.api, visible_area or self.api.EEex_Area_GetVisible()
    local anywhere = endpoint.area == '*' or (observing and endpoint.quest_event ~= nil)
    if not area or (not anywhere and text(area.m_resref) ~= endpoint.area) then return nil, "AREA_UNAVAILABLE" end
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
            local reason
            object,reason=self:_recipient(area,endpoint.recipient_local)
            if not object then return nil,reason end
        else object=api.EEex_Sprite_GetInPortrait(0) end
        if not object then return nil,'OWNER_MISSING' end
        local found={object=object,id=object.m_id,pointer=pointer(api,object)}
        local reason=self:_creature_state(object,true)
        -- A recipient that died with its reward on its way (Adapter:_delivery_owner).
        if reason then return nil,reason,observing and found or nil end
        return found
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
    -- addresses maps every live message pointer to its base-class target id;
    -- inert marks the messages that cannot change their target (see below).
    local result = { addresses = {}, additions = {}, inert = {} }
    self.api.EEex_Utility_IterateCPtrList(handler.m_messageList, function(message)
        local address = pointer(self.api, message)
        assert(not result.addresses[address], "NATIVE_DUPLICATE_MESSAGE_POINTER")
        assert(integer(message.m_targetId, -2147483648, 2147483647), "NATIVE_MESSAGE_FIELDS_INVALID")
        result.addresses[address] = message.m_targetId
        -- These type/subtype pairs are the engine's own constants, the same in
        -- BG:EE and BG2:EE 2.6.6.0 and 2.7.3.0 (each class's GetMsgType/GetMsgSubType).
        -- Never cast an unrelated base message to a larger item-message type.
        local subtype = message:virtual_GetMsgType() == 67 and message:virtual_GetMsgSubType() or nil
        if subtype == 40 then
            -- CMessageSetDialogWait. Its Run only sets two dialog-wait fields of
            -- the target sprite. Every party member queues one to itself on each
            -- of its AI updates, so one is nearly always queued for some member
            -- (docs/native-creature-transport-evidence.md).
            result.inert[address] = true
        elseif subtype == 12 then
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

-- A creature target is exclusive only if no queued message of any type but an
-- inert one is addressed to it and no queued item message carries a fixture
-- resource. `ignore` excludes the operation's own bound message pointer.
local function creature_conflict(queue, owner_id, resrefs, ignore)
    for address, target in pairs(queue.addresses) do
        if address ~= ignore and target == owner_id and not queue.inert[address] then return true end
    end
    for _, message in ipairs(queue.additions) do
        if message.pointer ~= ignore and resrefs[message.payload.resref] then return true end
    end
    return false
end

-- DropItem may reuse any pile near a corpse. Another queued add (a drop, a
-- death drop) could reach that pile too and stop the campaign after the
-- drop, so a ground delivery waits until no container add is queued.
local function container_add(queue)
    for _, message in ipairs(queue.additions) do
        if message.form == "container" then return true end
    end
    return false
end

local function addition_at(queue, address)
    for _, message in ipairs(queue.additions) do
        if message.pointer == address then return message end
    end
end

local function empty_slots(items, first, last)
    local used, empty = {}, {}
    for _, item in ipairs(items) do used[item.slot] = true end
    for slot = first, last do
        if not used[slot] then empty[#empty + 1] = slot end
    end
    return empty
end
local function empty_backpack(items)
    return empty_slots(items, Native.BACKPACK_FIRST, Native.BACKPACK_LAST)
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

-- The one instance of <resref> a creature gained since <before>. Run puts
-- its item into the first empty backpack slot and nowhere else
-- (docs/native-creature-transport-evidence.md). The creature goes on
-- meanwhile: its items may change in a fight, by its own actions or with its
-- death drop. An item of that name it had (its pointer) is not new where it
-- moved, nor where it changed in its own slot outside the backpack, by a
-- charge used, say. One that changed in its own backpack slot is what a
-- stack merge leaves, where Run makes no new instance: it is no copy of the
-- reward (reward_copy), so CIR stops rather than guess.
local function arrived(before, after, resref)
    local had, found = {}, nil
    for _, item in ipairs(before) do
        if item.payload.resref == resref then had[item.pointer] = item end
    end
    for _, current in ipairs(after) do
        if current.payload.resref == resref then
            local previous = had[current.pointer]
            local backpack = current.slot >= Native.BACKPACK_FIRST and current.slot <= Native.BACKPACK_LAST
            if not previous or (backpack and previous.slot == current.slot and
                not same(previous.payload, current.payload)) then
                if found or not backpack then return nil, "DELIVERY_PLACEMENT_UNEXPECTED" end
                found = current
            end
        end
    end
    if not found then return nil, "DELIVERY_MESSAGE_LOST" end
    return found
end

-- The first item of a pile that was not in it before (pointer and payload).
-- An add appends its item after the pile's items, and the handler runs the
-- queued messages in order (docs/native-creature-transport-evidence.md): an
-- add queued after another lands after it. Items taken out are not new.
local function first_new(before, after)
    for _, current in ipairs(after) do
        local old = false
        for _, previous in ipairs(before) do
            old = old or (previous.pointer == current.pointer and same(previous.payload, current.payload))
        end
        if not old then return current end
    end
end

local function item_at(items, slot)
    for _, item in ipairs(items) do
        if item.slot == slot then return item end
    end
end

-- The item with this pointer, as long as it still has this payload.
local function holding(items, address, expected)
    for _, item in ipairs(items) do
        if item.pointer == address then return same(item.payload, expected) and item or nil end
    end
end

-- Whether <found> is the reward <created> as an add left it: true for its
-- payload, "identified" for that payload identified again. The adds copy
-- their item with the CItem copy constructor, which can identify a zero-lore
-- item (AMUL12 on EET 2.7.3); CIR then sets the source's flags back.
local function reward_copy(found, created)
    if same(found, created) then return true end
    local expected = { resref = found.resref, charges = found.charges, expiration = found.expiration,
        flags = created.flags }
    return created.flags % 2 == 0 and found.flags == created.flags + 1 and same(expected, created) and
        "identified" or false
end

-- The counts before a reward at its place, in one GLOBAL value (FLPRCore's
-- settle): <held> items of its name in its holder, <piles> in the ground piles
-- of its area, the first-of <member> that takes it, whether a reply had <paid>
-- it to the payee. By them a later visit tells whether it arrived
-- (Adapter:settle_parked). Nil where they do not fit: that delivery cannot park.
local function settle_value(held, piles, member, paid)
    if not integer(held, 0, 1023) or not integer(piles, 0, 4095) or not integer(member, 0, 127) then return nil end
    return 1 + held + 1024 * (piles + 4096 * (member + 128 * (paid and 1 or 0)))
end
local function settle_counts(value)
    if not integer(value, 1, 1073741824) then return nil end
    value = value - 1
    local held = value % 1024; value = (value - held) / 1024
    local piles = value % 4096; value = (value - piles) / 4096
    local member = value % 128
    return { held = held, piles = piles, member = member, paid = (value - member) / 128 == 1 }
end
-- The holder a reward at this endpoint goes to, as one key: its creature, its
-- container (places can share one), or the party for a quest recipient, who
-- may stand anywhere and trades items with the others. A store has none: its
-- stock row tells (FLPRShop). A group or first-of place names its member first.
local function holder_key(endpoint)
    if endpoint.kind == "store" then return nil end
    if endpoint.area == "*" or endpoint.quest_event then return "party" end
    assert(endpoint.kind ~= "group" and endpoint.kind ~= "first-of" and type(endpoint.area) == "string" and
        type(endpoint.script_name) == "string", "NATIVE_HOLDER_UNKNOWN")
    if endpoint.sprite then return "creature:" .. endpoint.area .. ":" .. endpoint.script_name end
    return string.format("container:%s:%s:%d:%d:%d", endpoint.area, endpoint.script_name, endpoint.x, endpoint.y,
        endpoint.container_type or 4)
end
-- How many items of <name> are among <items>.
local function count_named(items, name)
    local count = 0
    for _, item in ipairs(items) do
        if item.payload.resref == name then count = count + 1 end
    end
    return count
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

-- A reward instance CIR put into a creature, container or ground pile in
-- this session, by its holder's object (Adapter.placed). A guarded source's
-- holder may get a reward of the source's own name: the draw (FLPRCore) can
-- give a place its own item back, and many scrolls share a name.
function Adapter:_note_placed(holder, item_pointer)
    local placed = self.placed[holder.pointer] or {}
    self.placed[holder.pointer] = placed
    placed[item_pointer] = holder.id
end

-- Corroborates a guard event against the exact visible area and owner.
-- A missing area or owner is never evidence that the guarded item was removed.
-- The gate's witness exists only in the session whose load of the pristine
-- area removed the original, so every reward CIR gave that holder since is
-- in Adapter.placed: those instances are not the original.
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
    local own, remaining = self.placed[owner.pointer] or {}, 0
    for _, item in ipairs(self:_items(owner, endpoint.sprite)) do
        if item.payload.resref == endpoint.payload.resref and own[item.pointer] ~= owner.id then
            remaining = remaining + 1
        end
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
    -- The reply names the route's own row; a preset's row may stand for it.
    target_id=self.route_targets[target_id] or target_id
    local endpoint=self.targets[target_id]
    if not endpoint then return -1 end -- Target excluded from this campaign.
    -- A revision exported before its place had this route (Adalon's place in
    -- CIR 1.0.2) has a plain creature there, whose rewards are in the same
    -- backpack: the reply hands them over too.
    assert(endpoint.inventory_reward or endpoint.kind=='creature' and endpoint.quest_event==nil and
        not endpoint.corpse_only and endpoint.handover==nil, 'NATIVE_INVENTORY_REWARD_INVALID')
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

-- Every item but <removed> is still there, once and as it was; nothing new.
local function without(before, after, removed)
    if #after ~= #before - 1 then return false end
    for _, old in ipairs(before) do
        local found = 0
        for _, current in ipairs(after) do
            if current.pointer == old.pointer then
                if not same(current.payload, old.payload) then return false end
                found = found + 1
            end
        end
        if found ~= (old == removed and 0 or 1) then return false end
    end
    return true
end

-- The Sahuagin king pays "a few surfacer trinkets from my treasury": the
-- rewards of the treasury's place. The condition of his reward reply calls
-- this when the game shows that reply, so it runs at once: the scene after
-- the reply clears every queued action and takes the party away for good.
-- The payee is the one he talks to (LastTalkedToBy) where it is a living party
-- member, else the first living party member (Adapter:_mark_payee), marked so
-- that a reward not placed yet follows the payment to the payee
-- (Adapter:_target). Each reward still in the treasury goes to
-- the payee, or to the first party member after it with room: a new copy is
-- queued with the source's flags (GiveItemCreate, which drops it at a full
-- backpack's feet), then DestroyItem takes one equal copy out of the
-- treasury. A reward the party took already stays taken, never a second
-- copy. What the reply cannot hand over yet is owed (retry_handovers).
-- Returns how many rewards went to the party, why not all could, and
-- whether the rest is owed.
function Adapter:hand_over_container_reward(speaker, target_id, assignments, phases)
    target_id=self.route_targets[target_id] or target_id
    local endpoint=self.targets[target_id]
    if not endpoint then return 0 end -- Place excluded from this campaign.
    assert(endpoint.handover and endpoint.kind=='container','NATIVE_HANDOVER_INVALID')
    local payee=self:_mark_payee(speaker,endpoint.handover.recipient_local,'NATIVE_HANDOVER_MARK_FAILED')
    if not payee then return 0,'HANDOVER_PAYEE_MISSING' end
    return self:_hand_over(target_id,endpoint,payee,assignments,phases)
end

-- The one a reward reply's speaker talks to (LastTalkedToBy) where it is a
-- living party member (a mark outside the party counts for nothing,
-- Adapter:_recipient), else the first living party member, marked with the
-- LOCALS <mark> at once: the reply marks it too, but through an action of
-- another actor that the scene after it may clear. Nil if there is no one;
-- <failure> if the mark does not hold.
function Adapter:_mark_payee(speaker, mark, failure)
    local api=self.api
    local talked=api.EEex_Object_EvalStringAsAIBase('LastTalkedToBy',speaker)
    local payee=talked and api.EEex_GameObject_CastUserType(talked)
    if payee and (self:_creature_state(payee,true) or api.EEex_Sprite_GetPortraitIndex(payee)==-1) then
        payee=nil
    end
    for index=0,5 do
        local member=not payee and api.EEex_Sprite_GetInPortrait(index)
        if member and not self:_creature_state(member,true) then payee=member end
    end
    if not payee then return nil end
    api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly('SetGlobal("'..mark..'","LOCALS",1)',payee)
    assert(api.EEex_Sprite_GetLocalInt(payee,mark)==1,failure)
    return payee
end

-- Adalon takes the eggs: her reward reply marks the one she talks to through
-- an action of his own, and the end of her talk clears every queued action
-- of the party before he can take it. The condition of that reply calls this
-- when the game shows it (M_FLPR's FLPR_Mark) with the mark of the place's
-- recipient, the same in every preset's row there. The one she talks to is
-- marked at once (Adapter:_mark_payee), as the only one: another one the
-- reply was shown to before loses the mark. Nothing happens where no quest
-- place of this campaign has that mark. Returns why no one could be marked.
function Adapter:mark_recipient(speaker, mark)
    assert(type(mark)=='string' and mark:match('^FLPR_R[A-Z0-9_]+$'),'NATIVE_MARK_INVALID')
    local used=false
    for _,endpoint in pairs(self.targets) do
        used=used or (endpoint.quest_event~=nil and not endpoint.spell_event and endpoint.recipient_local==mark)
    end
    if not used then return end
    local payee=self:_mark_payee(speaker,mark,'NATIVE_RECIPIENT_MARK_FAILED')
    if not payee then return 'MARK_PAYEE_MISSING' end
    local api,area=self.api,self.api.EEex_Area_GetVisible()
    local own=pointer(api,payee)
    for _,other in ipairs(area and self:_all_marked(area,mark) or {}) do
        if pointer(api,other)~=own then
            api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly('SetGlobal("'..mark..'","LOCALS",0)',other)
            assert(api.EEex_Sprite_GetLocalInt(other,mark)==0,'NATIVE_RECIPIENT_MARK_FAILED')
        end
    end
end

-- A handover the reply could not finish, while another mutation was still to
-- be observed (perhaps a reward on its way into the treasury), a reward of the
-- same name was parked there, or the treasury could not be found: the rest is
-- retried on the host's steps, at once when nothing is
-- pending and then every HANDOVER_RETRY_TICKS, to the payee the reply marked
-- (Adapter:_recipient), while the treasury's area is loaded. Session only, as
-- the reply's moment is.
function Adapter:retry_handovers(assignments, phases)
    if next(self.owed_handovers)==nil or self.pending or not self.tick or
        self.tick<(self.handover_retry or 0) then return true end
    self.handover_retry=self.tick+Native.HANDOVER_RETRY_TICKS
    local area=self.api.EEex_Area_GetVisible()
    for target_id in pairs(self.owed_handovers) do
        local endpoint=self.targets[target_id]
        if area and text(area.m_resref)==endpoint.area then
            local payee=self:_recipient(area,endpoint.handover.recipient_local)
            if payee and not self:_creature_state(payee,true) then
                self:_hand_over(target_id,endpoint,payee,assignments,phases)
            end
        end
    end
    return true
end

function Adapter:owes_handover() return next(self.owed_handovers)~=nil end

function Adapter:_hand_over(target_id, endpoint, payee, assignments, phases)
    local api=self.api
    -- A mutation still to be observed could take these changes for its own.
    if self.pending then
        self.owed_handovers[target_id]=true
        return 0,'HANDOVER_MUTATION_PENDING',true
    end
    local due,waiting={},false
    for _,assignment in ipairs(assignments) do
        if assignment.target_id==target_id then
            -- A parked reward of this place follows once it has settled.
            if assignment.delivery_phase==phases.parked then waiting=true
            elseif assignment.delivery_phase==phases.confirmed then
                local reward=assert(self:_reward(assignment.source_id,assignment.unit),'NATIVE_HANDOVER_REWARD_MISSING')
                -- A reward parked in the treasury, of any place there, counts the
                -- items of its name (Adapter:set_parked): one of that name stays.
                if self:_parked_at(endpoint,reward) then waiting=true else due[#due+1]=reward end
            end
        end
    end
    local owner,reason
    if #due>0 then
        owner,reason=self:_resolve(endpoint,'target')
        if not owner then
            self.owed_handovers[target_id]=true
            return 0,reason,true
        end
    end
    self.owed_handovers[target_id]=waiting or nil
    -- The payee first, then the rest of the party, each with its free
    -- backpack slots less the item messages already queued for it.
    local party,seen={payee},{[pointer(api,payee)]=true}
    for index=0,5 do
        local member=api.EEex_Sprite_GetInPortrait(index)
        if member and not seen[pointer(api,member)] and not self:_creature_state(member,true) then
            party[#party+1],seen[pointer(api,member)]=member,true
        end
    end
    local count,ambiguous=0,false
    for _,reward in ipairs(due) do
        local items,present,equal=self:_items(owner,false),0,true
        for _,item in ipairs(items) do
            if item.payload.resref==reward.resref then
                present,equal=present+1,equal and same(item.payload,reward)
            end
        end
        -- DestroyItem takes an item by its name: every treasury item of that
        -- name must be an equal copy of the reward, or it could take another.
        if present>0 and not equal then ambiguous=true end
        if present>0 and equal then
            local supported,why,stack_limit=self:_supported(reward)
            assert(supported,why)
            local queue,recipient=self:_queue(),nil
            for _,member in ipairs(party) do
                local room=#empty_backpack(self:_items({object=member},true))
                for _,message in ipairs(queue.additions) do
                    if message.target==member.m_id then room=room-1 end
                end
                if room>0 and not recipient then recipient=member end
            end
            recipient=recipient or payee
            api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly(string.format('GiveItemCreate("%s",Myself,%d,%d,%d)',
                reward.resref,reward.charges[1],reward.charges[2],reward.charges[3]),recipient)
            local added={}
            for _,message in ipairs(self:_queue().additions) do
                if not queue.addresses[message.pointer] then added[#added+1]=message end
            end
            local message=#added==1 and added[1] or nil
            local created=message and created_payload(message.payload,reward,stack_limit)
            assert(created and message.form=='creature' and message.target==recipient.m_id and
                message.source==recipient.m_id,'NATIVE_HANDOVER_CREATION_UNPROVEN')
            self:_preserve_message_flags(message,created)
            api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly(
                string.format('DestroyItem("%s")',reward.resref),owner.object)
            local after,removed=self:_items(owner,false),nil
            for _,old in ipairs(items) do
                local kept=false
                for _,current in ipairs(after) do kept=kept or current.pointer==old.pointer end
                if not kept then removed=removed==nil and old or false end
            end
            assert(removed and removed.payload.resref==reward.resref and without(items,after,removed),
                'NATIVE_HANDOVER_REMOVAL_NOT_EXACT')
            count=count+1
        end
    end
    if ambiguous then return count,'HANDOVER_ITEM_AMBIGUOUS',false end
    -- The rest follows once its parked one has settled (Adapter:retry_handovers).
    return count,waiting and 'HANDOVER_REWARD_PARKED' or nil,waiting
end

function Adapter:_target(id, source_id, unit)
    local endpoint=self.targets[id]
    -- Once a reward reply has paid (the Sahuagin king's, Tolgerias's), the
    -- rewards it hands over go to the payee: the party leaves the treasury and
    -- the holder behind.
    if endpoint and endpoint.handover and
        self.api.EEex_GameState_GetGlobalInt(endpoint.handover.event_global)==1 then return endpoint.paid end
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

-- Item Randomiser's chapter 3 places (chapter3.py) give their item to one of
-- Bodhi's vampires and one of Aran's thieves; the party meets one side only.
-- The creatures that may take this reward, in order: from each group with
-- candidates the one drawn for the reward, as a group target draws it, from
-- the others every member. The first of them who can take it does.
function Adapter:_first_of(endpoint, source_id, unit)
    if not self.sources[source_id] or not integer(unit,1,4096) then return nil end
    local resref=self:_reward(source_id, unit).resref
    local draw=((self.seed+unit*48271)%2147483647*16807)%2147483647
    local members={}
    for _,group in ipairs(endpoint.groups) do
        if group.eligible then
            local choices=group.eligible[resref]
            if not choices then return nil end
            members[#members+1]=group.members[choices[draw%#choices+1]]
        else
            for _,member in ipairs(group.members) do members[#members+1]=member end
        end
    end
    return members
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
        -- No counts: a store's stock row tells (Adapter:settle_parked).
        return { identity = endpoint.identity, target_id = id, accepts = 1, settle = settle_value(0, 0, 0, false) }
    end
    if endpoint.kind == "first-of" then
        -- The first creature that can take the reward now takes it; its
        -- reservation names it for execute. While none can, the reason is
        -- the first one's in the visible area: each waits there for its own.
        local members = self:_first_of(endpoint, source_id, unit)
        if not members then return nil, "TARGET_UNKNOWN" end
        local area, waiting = self.api.EEex_Area_GetVisible(), nil
        for index, member in ipairs(members) do
            local witness, reason = self:_inspect(member, id, source, source_id, unit, index)
            if witness then return witness end
            if not waiting and area and text(area.m_resref) == member.area then waiting = reason end
        end
        return nil, waiting or "AREA_UNAVAILABLE"
    end
    -- Whether a reply had paid the place's rewards to its payee (Adapter:_target).
    return self:_inspect(endpoint, id, source, source_id, unit, 0, endpoint == self.targets[id].paid)
end

-- The witness of a reward's target. Its counts before the reward (settle_value)
-- name the first-of <member> it took and whether it was <paid> to a payee.
function Adapter:_inspect(endpoint, id, source, source_id, unit, member, paid)
    if source and self:_parked_at(endpoint, self:_reward(source_id, unit)) then return nil, "TARGET_PARKED" end
    local owner, reason, corpse = self:_resolve(endpoint, "target")
    if not owner then
        if reason == "TARGET_NOT_ALIVE" and corpse and source then
            return self:_inspect_ground(endpoint, id, self:_reward(source_id, unit), corpse, member, paid)
        end
        if reason == "OWNER_MISSING" and source and self:_vanished(endpoint) then
            return self:_inspect_party_ground(endpoint, id, self:_reward(source_id, unit), member, paid)
        end
        return nil, reason
    end
    -- A place that receives only at its creature's corpse waits while it lives.
    if endpoint.corpse_only then return nil, "TARGET_ALIVE" end
    local items = self:_items(owner, endpoint.sprite)
    local reward = source and self:_reward(source_id, unit)
    if reward then
        local supported, stack_limit
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
        -- Where its death drop could put the reward (Adapter:execute), and its
        -- count there. A party member keeps its items wherever it dies, and a
        -- quest's recipient is one (Adapter:_recipient).
        local piles, count = {}, 0
        for pile_id, pile in pairs(reward and self:_piles(reward.resref) or {}) do
            piles[pile_id], count = { pointer = pile.pointer, items = pile.items }, count + #pile.items
        end
        if self.api.EEex_Sprite_GetPortraitIndex(owner.object) ~= -1 then count = 0 end
        self.reservations[endpoint.identity] = { owner = owner, items = items, kind = "deliver", id = id, empty = empty,
            endpoint = endpoint, piles = reward and piles }
        return { identity = endpoint.identity, target_id = id, accepts = 1,
            settle = reward and settle_value(count_named(items, reward.resref), count, member, paid) }
    end
    if endpoint.kind == "ground-pile" and #items ~= 0 then return nil, "TARGET_NOT_EMPTY" end
    self.reservations[endpoint.identity] = { owner = owner, items = items, kind = "deliver", id = id,
        endpoint = endpoint }
    return { identity = endpoint.identity, target_id = id, accepts = 1,
        settle = reward and settle_value(count_named(items, reward.resref), 0, member, paid) }
end

-- A creature that died before its reward arrived can never hold it (Chris,
-- 2026-10-22: it needs a fallback that always works). It goes to the ground
-- where the corpse lies, while the corpse is in the visible area: CreateItem on
-- the corpse, then DropItem, which moves it into a pile there (2.7.3.0 lab, see
-- docs/native-corpse-ground-evidence.md). A dead party member keeps its
-- inventory and may be raised, so its reward waits, as for a familiar.
-- DropItem takes an item by name, so the corpse must hold no other copy.
function Adapter:_inspect_ground(endpoint, id, reward, corpse, member, paid)
    local api, object = self.api, corpse.object
    local portrait = api.EEex_Sprite_GetPortraitIndex(object)
    local typeAI = object.m_typeAI
    assert(typeAI and integer(typeAI.m_EnemyAlly, 0, 255), "NATIVE_ALLEGIANCE_INVALID")
    if portrait ~= -1 or typeAI.m_EnemyAlly == FAMILIAR_ALLEGIANCE then return nil, "TARGET_NOT_ALIVE" end
    local supported, reason = self:_supported(reward)
    if not supported then return nil, reason end
    local items = self:_items(corpse, true)
    for _, item in ipairs(items) do
        if item.payload.resref == reward.resref then return nil, "CORPSE_ITEM_AMBIGUOUS" end
    end
    local empty = empty_slots(items, Native.CREATE_FIRST, Native.CREATE_LAST)
    if #empty == 0 then return nil, "TARGET_INVENTORY_FULL" end
    local queue = self:_queue()
    if creature_conflict(queue, corpse.id, self.source_resrefs) or container_add(queue) then
        return nil, "TARGET_QUEUE_BUSY"
    end
    self.reservations[endpoint.identity] = { owner = corpse, items = items, kind = "deliver", id = id,
        ground = true, empty = empty, reward = reward, endpoint = endpoint }
    return { identity = endpoint.identity, target_id = id, accepts = 1, ground = true,
        settle = settle_value(0, self:_count_piles(reward.resref), member, paid) }
end

-- How many items of <name> lie in the ground piles of the visible area.
function Adapter:_count_piles(name)
    local count = 0
    for _, pile in pairs(self:_piles(name)) do count = count + #pile.items end
    return count
end

-- A creature target that died and left no body: no sprite of its script name
-- in the visible area of its own (Adapter:_resolve), and the engine counted
-- its death. Bodhi's vampires turn into mist as they die (Die(), then op151
-- replaces the body); a chunked death leaves no body either.
function Adapter:_vanished(endpoint)
    -- A quest target's recipient has no script name of its own.
    if endpoint.kind ~= "creature" or not endpoint.script_name then return false end
    local deaths = self.api.EEex_GameState_GetGlobalInt((DEATH_VARIABLE .. endpoint.script_name):sub(1, VARIABLE_LENGTH))
    return integer(deaths, 1, 2147483647)
end

-- Such a creature can never hold its reward either. Chris, 2026-10-26:
-- "Einfach das Item auf dem Boden droppen mit einer Nachricht", so clear that
-- nobody overlooks it. It falls to the ground at the feet of a party member in
-- the visible area, the first in portrait order who can drop it, as at a
-- corpse: CreateItem on a party member stores it in the first empty backpack
-- slot at once (docs/native-creature-transport-evidence.md), DropItem moves it
-- into a pile at the member. The receipt names the item and the member for
-- the player (FLPRHost's notice). While no member can drop it, it waits.
function Adapter:_inspect_party_ground(endpoint, id, reward, place_member, paid)
    local supported, reason = self:_supported(reward)
    if not supported then return nil, reason end
    local queue = self:_queue()
    -- DropItem may reuse a pile near the member, as near a corpse.
    if container_add(queue) then return nil, "TARGET_QUEUE_BUSY" end
    local member, waiting = self:_dropper(reward, queue)
    if not member then return nil, waiting end
    self.reservations[endpoint.identity] = { owner = member.owner, items = member.items, kind = "deliver", id = id,
        ground = true, portrait = member.portrait, empty = member.empty, reward = reward, endpoint = endpoint }
    return { identity = endpoint.identity, target_id = id, accepts = 1, ground = true,
        settle = settle_value(0, self:_count_piles(reward.resref), place_member, paid) }
end

-- The first party member in portrait order who can drop <reward> at its
-- feet: alive in the visible area, with an empty backpack slot, no item of
-- the reward's name (DropItem takes an item by name) and no message queued.
-- Else nil and why none can, the first member's reason.
function Adapter:_dropper(reward, queue)
    local api = self.api
    local present, waiting = area_ids(api, api.EEex_Area_GetVisible()), nil
    for portrait = 0, 5 do
        local member = api.EEex_Sprite_GetInPortrait(portrait)
        if member and present[member.m_id] and api.EEex_GameObject_IsSprite(member, false) == true then
            local owner = { object = member, id = member.m_id, pointer = pointer(api, member) }
            local items, named = self:_items(owner, true), false
            for _, item in ipairs(items) do
                if item.payload.resref == reward.resref then named = true end
            end
            local empty = empty_backpack(items)
            if named then waiting = waiting or "PARTY_ITEM_AMBIGUOUS"
            elseif #empty == 0 then waiting = waiting or "TARGET_INVENTORY_FULL"
            elseif creature_conflict(queue, owner.id, self.source_resrefs) then waiting = waiting or "TARGET_QUEUE_BUSY"
            else return { owner = owner, items = items, empty = empty, portrait = portrait } end
        end
    end
    return nil, waiting or "OWNER_MISSING"
end

-- The corpse or party member a ground delivery's reservation names, as long
-- as it still is: the same corpse, or the same living member in the visible
-- area while the target's creature is still gone without a body.
function Adapter:_drop_owner(endpoint, reserved)
    local api = self.api
    local owner, reason, corpse = self:_resolve(endpoint, "target")
    if not reserved.portrait then
        if owner or reason ~= "TARGET_NOT_ALIVE" or not corpse or corpse.id ~= reserved.owner.id or
            corpse.pointer ~= reserved.owner.pointer then return nil end
        return corpse
    end
    if owner or reason ~= "OWNER_MISSING" or not self:_vanished(endpoint) then return nil end
    local member = api.EEex_Sprite_GetInPortrait(reserved.portrait)
    if not member or member.m_id ~= reserved.owner.id or pointer(api, member) ~= reserved.owner.pointer or
        api.EEex_GameObject_IsSprite(member, false) ~= true or
        not area_ids(api, api.EEex_Area_GetVisible())[member.m_id] then return nil end
    return { object = member, id = member.m_id, pointer = reserved.owner.pointer }
end

-- The ground pile with this object id in the visible area of the endpoint, or
-- why there is none. A pile the engine creates for a drop joins the area's
-- lists only when its message runs.
function Adapter:_ground_pile(id, endpoint)
    local api, area = self.api, self.api.EEex_Area_GetVisible()
    -- A marked recipient may stand in any area (Adapter:_resolve).
    if not area or (endpoint.area ~= "*" and text(area.m_resref) ~= endpoint.area) then
        return nil, "AREA_UNAVAILABLE"
    end
    local listed = false
    for _, field in ipairs(lists) do
        assert(area[field], "NATIVE_AREA_LIST_MISSING")
        api.EEex_Utility_IterateCPtrList(area[field], function(entry)
            if entry == id then listed = true end
        end)
    end
    if not listed then return nil, "GROUND_PILE_MISSING" end
    local object = api.EEex_GameObject_Get(id)
    object = object and api.EEex_GameObject_CastUserType(object)
    if not object or api.EEex_GameObject_IsSprite(object, true) ~= false or object.m_lstItems == nil or
        object.m_containerType ~= 4 then return nil, "GROUND_PILE_INVALID" end
    return { object = object, id = id, pointer = pointer(api, object) }
end

-- The items of <name> in a ground pile, at their places in it. CIR reads no
-- other item there: anything of any mod may lie on the ground.
function Adapter:_pile_items(pile, name)
    local result, place = {}, 0
    self.api.EEex_Utility_IterateCPtrList(pile.object.m_lstItems, function(item)
        place = place + 1
        if text(item.cResRef) == name then result[#result + 1] = self:_item(item, place) end
    end)
    return result
end

-- Every ground pile of the visible area by its object id, with its items of
-- <name>.
function Adapter:_piles(name)
    local api, piles = self.api, {}
    for id in pairs(area_ids(api, api.EEex_Area_GetVisible())) do
        local object = api.EEex_GameObject_Get(id)
        object = object and api.EEex_GameObject_CastUserType(object)
        if object and api.EEex_GameObject_IsSprite(object, true) == false and object.m_lstItems ~= nil and
            object.m_containerType == 4 then
            local pile = { object = object, id = id, pointer = pointer(api, object) }
            pile.items = self:_pile_items(pile, name)
            piles[id] = pile
        end
    end
    return piles
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
    -- A place of several creatures: the one that inspect_target reserved.
    if kind == "target" and endpoint.kind == "first-of" then
        endpoint = reserved.endpoint
        if not endpoint or endpoint.identity ~= command.identity or endpoint.kind ~= "creature" then
            return nil, "RESERVATION_MISMATCH"
        end
    end
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
    if kind == "target" and reserved.ground then return self:_execute_ground(command, reserved, endpoint, source.payload) end
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
        local created = message and created_payload(message.payload, p, stack_limit)
        -- GiveItemCreate chooses identification from the ITM's lore value;
        -- it does not accept the original inventory's identified flag. Bind
        -- the newly created message first, then preserve that selected flag.
        if not unchanged(reserved.items, after) or not message or message.form ~= "creature" or
            message.target ~= owner.id or message.source ~= owner.id or not created or
            creature_conflict(after_queue, owner.id, self.source_resrefs, message.pointer) then
            self.last_error = "CREATED_INSTANCE_UNPROVEN"; return nil, self.last_error
        end
        self:_preserve_message_flags(message, created)
        self.pending.queued, self.pending.empty, self.pending.created = message, reserved.empty, created
        -- Where the creature's death drop could put the reward, and the area
        -- it is in (Adapter:_find_dropped). A party member keeps its items
        -- when it dies, but not where it leaves no body. Plain values only,
        -- never the piles; inspect_target read them this tick.
        local piles = reserved.piles
        if not piles then
            piles = {}
            for id, pile in pairs(self:_piles(created.resref)) do piles[id] = { pointer = pile.pointer, items = pile.items } end
        end
        self.pending.piles, self.pending.area = piles, text(self.api.EEex_Area_GetVisible().m_resref)
    else
        local after_queue, added, conflicting = self:_queue(), {}, 0
        for _, message in ipairs(after_queue.additions) do
            if not before_queue.addresses[message.pointer] then added[#added + 1] = message end
            if conflicts(message, owner.id, p) then conflicting = conflicting + 1 end
        end
        local placed = placed_item(before, after, {#before + 1})
        local created = placed and created_payload(placed.payload, p, stack_limit)
        local queued = #added == 1 and created_payload(added[1].payload, p, stack_limit)
        if created and #added == 0 and conflicting == 0 then
            self:_preserve_container_flags(owner, placed, created)
            self.pending.created_pointer, self.pending.created = placed.pointer, created
        elseif queued and unchanged(before, after) and conflicting == 1 and added[1].target == owner.id and
            added[1].source == owner.id and added[1].form == "container" and added[1].slot == 32767 then
            -- The engine queues an embedded CItem value, then Run() heap-copies
            -- it and appends to the container, preserving its earlier contents. Message and inventory
            -- pointers are intentionally different. Bind the exact new message
            -- to this nonce before allowing later observations to establish the
            -- resulting pointer; never resubmit CreateItem.
            self:_preserve_message_flags(added[1], queued)
            self.pending.queued, self.pending.created = added[1], queued
        else
            self.last_error = "CREATED_INSTANCE_UNPROVEN"; return nil, self.last_error
        end
    end
    self.pending.complete = true
    return true
end

-- The reward of a dead creature target, at its corpse (Adapter:_inspect_ground)
-- or at a party member's feet (Adapter:_inspect_party_ground). CreateItem
-- stores it in one empty slot of that sprite at once; DropItem then takes that
-- instance out again and queues one container add into a ground pile at the
-- sprite, a pile it reuses or creates. Bind that message to this nonce, as
-- for a container target; later observations find the pile by its id.
function Adapter:_execute_ground(command, reserved, endpoint, p)
    local api = self.api
    local owner = self:_drop_owner(endpoint, reserved)
    if not owner then return nil, "OWNER_CHANGED" end
    if not same(p, reserved.reward) then return nil, "RESERVATION_MISMATCH" end
    local before = self:_items(owner, true)
    if not unchanged(reserved.items, before) then return nil, "INVENTORY_CHANGED" end
    local supported, reason, stack_limit = self:_supported(p)
    if not supported then return nil, reason end
    local before_queue = self:_queue()
    if creature_conflict(before_queue, owner.id, self.source_resrefs) or container_add(before_queue) then
        return nil, "DELIVERY_QUEUE_NOT_EXCLUSIVE"
    end
    -- What the player is told of a reward at a party member's feet.
    local notice = reserved.portrait and party_notice(api, owner.object, p) or nil
    local tried = self.attempted[command.session] or {}
    self.attempted[command.session] = tried; tried[command.nonce] = true
    local receipt = { session = command.session, kind = command.kind, index = command.index,
        nonce = command.nonce, revision = command.revision, identity = command.identity,
        source_id = command.source_id, target_id = command.target_id, payload = payload(p) }
    -- Its pile is in the visible area (Adapter:_ground_left).
    self.pending = { receipt = receipt, endpoint = endpoint, kind = "target", owner = owner,
        before = before, tick = self.tick, complete = false, ground = true, notice = notice,
        area = text(api.EEex_Area_GetVisible().m_resref) }
    self.reservations[command.identity] = nil
    -- Pending/attempted are published first; an exception must disable this
    -- adapter, never cause a blind action retry.
    local created, message, pile = self:_create_drop(owner, endpoint, before, reserved.empty, p, stack_limit,
        before_queue)
    if not created then
        self.last_error = "CREATED_INSTANCE_UNPROVEN"; return nil, self.last_error
    end
    self.pending.queued, self.pending.created = message, created
    self.pending.pile_before = pile and self:_pile_items(pile, created.resref) or {}
    self.pending.pile_pointer = pile and pile.pointer or nil
    self.pending.complete = true
    return true
end

-- CreateItem of the reward <p> on a sprite that stores it in one of its
-- <empty> slots at once (a corpse, a living party member), then DropItem
-- (Adapter:_drop). No item iterator is active here, and the caller saw no
-- container add queued (<before_queue>). Returns the reward as created, the
-- drop's message and its pile, or nothing if not exactly so.
function Adapter:_create_drop(owner, endpoint, before, empty, p, stack_limit, before_queue)
    self.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly(
        string.format('CreateItem("%s",%d,%d,%d)', p.resref, p.charges[1], p.charges[2], p.charges[3]), owner.object)
    local holding_items = self:_items(owner, true)
    local placed = placed_item(before, holding_items, empty)
    local created = placed and created_payload(placed.payload, p, stack_limit)
    for _, message in ipairs(self:_queue().additions) do
        if not before_queue.addresses[message.pointer] then created = nil end
    end
    if not created then return nil end
    local message, pile = self:_drop(owner, endpoint, holding_items, placed, before_queue)
    if not message then return nil end
    self:_preserve_message_flags(message, created)
    return created, message, pile
end

-- DropItem on a sprite whose <items> hold the reward's instance <placed>. It
-- takes that instance out at once and queues one container add into a ground
-- pile at the sprite, a pile it reuses or creates. The message carries the
-- instance as it was, and nothing else in the sprite may change. No other
-- queued add can reach that pile first: the caller saw none queued
-- (container_add), and this is the only new one. Returns the message and the
-- pile (nil while a new pile is not listed yet), or nothing if not so.
function Adapter:_drop(owner, endpoint, items, placed, before_queue)
    self.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly(
        string.format('DropItem("%s",[-1.-1])', placed.payload.resref), owner.object)
    local added = {}
    for _, message in ipairs(self:_queue().additions) do
        if not before_queue.addresses[message.pointer] then added[#added + 1] = message end
    end
    local message = #added == 1 and added[1] or nil
    if not message or not unchanged(items, self:_items(owner, true), placed) or message.form ~= "container" or
        message.slot ~= 32767 or message.target ~= message.source or
        not same(message.payload, placed.payload) then return nil end
    local pile, missing = self:_ground_pile(message.target, endpoint)
    if not pile and missing ~= "GROUND_PILE_MISSING" then return nil end
    return message, pile
end

function Adapter:on_observation_boundary(tick)
    if not integer(tick, 0, 9007199254740991) then return nil, "OBSERVATION_CLOCK_INVALID" end
    if self.tick and tick <= self.tick then return nil, "LATER_GAME_TICK_REQUIRED" end
    self.tick, self.steps = tick, self.steps + 1
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
    if pending.kind == "target" and pending.endpoint.kind == "store" then
        receipt.created, receipt.nonce_bound_instance, receipt.lootable = 1, 1, 1
        self.pending = nil
        return receipt
    end
    -- A reward waits while no area is shown (loading one): nothing can be read.
    if pending.kind == "target" and not self.api.EEex_Area_GetVisible() then return nil, "DELIVERY_SETTLING" end
    if self:_away(pending.endpoint) or self:_ground_left(pending) then return self:_left(pending, receipt) end
    if pending.queued and self.tick - (pending.lost_tick or pending.tick) >= Native.MAX_SETTLE_TICKS then
        pending.complete = false
        self.last_error = "DELIVERY_SETTLING_TIMEOUT"
        return nil, self.last_error
    end
    if pending.ground then return self:_observe_ground(pending, receipt) end
    if pending.kind == "target" and pending.endpoint.kind == "creature" then
        return self:_observe_creature(pending, receipt)
    end
    local owner, reason = self:_resolve(pending.endpoint, pending.kind)
    if not owner then return nil, reason end
    if owner.id ~= pending.owner.id or owner.pointer ~= pending.owner.pointer then return nil, "OWNER_CHANGED" end
    local items = self:_items(owner, pending.endpoint.sprite)
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
                local created = pending.created
                if not same(placed.payload, created) then
                    -- CMessageContainerAddItem::Run copies the message's item with
                    -- the same CItem copy constructor as CMessageAddItem::Run, which
                    -- can identify a zero-lore item again (AMUL12 on EET 2.7.3).
                    -- Preserve the source's flags on the one proven new container
                    -- instance, just as on its message; nothing else may differ.
                    local found = placed.payload
                    local expected = { resref = found.resref, charges = found.charges,
                        expiration = found.expiration, flags = created.flags }
                    if created.flags % 2 ~= 0 or found.flags ~= created.flags + 1 or
                        not same(expected, created) then return nil, "DELIVERY_PAYLOAD_CHANGED" end
                    self:_preserve_container_flags(owner, placed, created)
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
            not same(placed.payload, pending.created) then return nil, "CREATED_INSTANCE_CHANGED" end
        local supported; supported, reason = self:_supported(receipt.payload)
        if not supported then return nil, reason end
        -- Container access (locks, traps, and normal opening) remains the
        -- engine's responsibility. Only its inventory has gained a reward.
        receipt.created, receipt.nonce_bound_instance, receipt.lootable = 1, 1, 1
        self:_note_placed(owner, placed.pointer)
    end
    self.pending = nil
    return receipt
end

-- Whether the party is no longer in the area of a mutation's place: the objects
-- there left memory with it, and come back as other objects. A quest recipient
-- is followed into any area (Adapter:_resolve).
function Adapter:_away(endpoint)
    if endpoint.area == "*" or endpoint.quest_event then return false end
    local area = self.api.EEex_Area_GetVisible()
    return not area or text(area.m_resref) ~= endpoint.area
end

-- Whether the party left the area of a reward on its way to the ground, or
-- found there: its pile is in the area its delivery began in, also where its
-- quest recipient is followed anywhere.
function Adapter:_ground_left(pending)
    if not pending.ground and not pending.found then return false end
    local area = self.api.EEex_Area_GetVisible()
    return not area or text(area.m_resref) ~= pending.area
end

-- The party left the place before the receipt (Adapter:_away). What execute or
-- an earlier observation proved stands: DestroyItem took exactly the original,
-- a reward was pinned in its holder or found on the ground. Nothing is read
-- there any more. Anything else parks with its counts before it (Core:park),
-- and the next visit settles it (Adapter:settle_parked).
function Adapter:_left(pending, receipt)
    self.pending = nil
    if pending.kind == "source" then
        receipt.removed, receipt.remaining = 1, 0
        return receipt
    end
    if not pending.found and not pending.created_pointer then
        receipt.parked = 1
        return receipt
    end
    receipt.created, receipt.nonce_bound_instance = 1, 1
    if pending.found or pending.ground then
        receipt.lootable, receipt.holder, receipt.notice = 1, "ground", pending.notice
    elseif pending.endpoint.kind == "creature" then
        -- Held by its creature, as Adapter:_observe_creature tells it.
        receipt.lootable, receipt.held, receipt.holder, receipt.slot = 0, 1, "creature", pending.created_slot
    else
        receipt.lootable = 1
    end
    return receipt
end

-- As a container target's queued add: wait for the bound message, then pin the
-- one new instance in the pile it named, then publish one later tick on. No
-- add was queued before this one (Adapter:_drop), so its item is the first
-- new one of its name in the pile; adds queued after it land after it, and
-- items taken out meanwhile are not the reward. CIR reads only the items of
-- that name there (Adapter:_pile_items). Once the message has run, the engine
-- may give its address to another message (in a fight often at once): only
-- the reward's instance tells then.
function Adapter:_observe_ground(pending, receipt)
    local queued = pending.queued
    local queue = not pending.created_pointer and self:_queue()
    local present = queue and queue.addresses[queued.pointer] ~= nil
    local current = present and addition_at(queue, queued.pointer)
    local matching = current and same_message(current, queued)
    -- A new pile joins the area only as the message runs.
    if matching and not pending.pile_pointer then return nil, "DELIVERY_SETTLING" end
    local pile, reason = self:_ground_pile(queued.target, pending.endpoint)
    -- Another message at its address while its pile is not there: it changed.
    if not pile then return nil, present and not matching and "DELIVERY_QUEUE_CONFLICT" or reason end
    if pending.pile_pointer and pile.pointer ~= pending.pile_pointer then return nil, "OWNER_CHANGED" end
    local items = self:_pile_items(pile, pending.created.resref)
    if matching then
        if first_new(pending.pile_before, items) then return nil, "DELIVERY_EARLY_ITEM" end
        return nil, "DELIVERY_SETTLING"
    end
    if not pending.created_pointer then
        local placed = first_new(pending.pile_before, items)
        if not placed then return nil, present and "DELIVERY_QUEUE_CONFLICT" or "DELIVERY_MESSAGE_LOST" end
        local copy = reward_copy(placed.payload, pending.created)
        if not copy then return nil, "DELIVERY_PAYLOAD_CHANGED" end
        -- The add copies the item as for a container target and can identify
        -- a zero-lore item again; restore the source's flags.
        if copy == "identified" then self:_preserve_container_flags(pile, placed, pending.created) end
        pending.created_pointer, pending.pinned_tick, pending.pile_pointer = placed.pointer, self.tick, pile.pointer
        return nil, "DELIVERY_SETTLING"
    end
    if self.tick <= pending.pinned_tick then return nil, "DELIVERY_SETTLING" end
    if not holding(items, pending.created_pointer, pending.created) then return nil, "CREATED_INSTANCE_CHANGED" end
    return self:_on_ground(pending, receipt, pile, pending.created_pointer)
end

-- On the ground where the dead target lies, or at a party member's feet;
-- anyone may pick it up.
function Adapter:_on_ground(pending, receipt, pile, item_pointer)
    local supported, reason = self:_supported(receipt.payload)
    if not supported then return nil, reason end
    self:_note_placed(pile, item_pointer)
    receipt.created, receipt.nonce_bound_instance, receipt.lootable, receipt.holder = 1, 1, 1, "ground"
    receipt.notice = pending.notice
    self.pending = nil
    return receipt
end

-- The creature a reward is on its way to, as at its execution: the creature
-- and "alive", its corpse and "dead". One that is no longer found (no sprite
-- of its script name in its area) is "vanished": it left alive, or it died
-- and left no body, or a script removed it. CIR then follows its object while
-- that is still the same one (Adapter:_tracked), else nil. A quest's recipient
-- is followed by its object from the start: neither its mark nor the party
-- changing meanwhile makes another one the recipient (Adapter:_recipient).
-- Anything else: nil and why it cannot be told.
function Adapter:_delivery_owner(pending)
    if pending.endpoint.quest_event then
        local owner = self:_tracked(pending.owner)
        if not owner then return nil, "vanished" end
        return owner, self.api.EEex_GameObject_IsSprite(owner.object, false) == true and "alive" or "dead"
    end
    local owner, reason, unusable = self:_resolve(pending.endpoint, "target")
    local state = owner and "alive" or "dead"
    owner = owner or (reason == "TARGET_NOT_ALIVE" and unusable) or nil
    if owner then
        if owner.id ~= pending.owner.id or owner.pointer ~= pending.owner.pointer then return nil, "OWNER_CHANGED" end
        return owner, state
    end
    if reason == "OWNER_MISSING" then return self:_tracked(pending.owner), "vanished" end
    return nil, reason
end

-- The sprite a delivery began with, by its object id, while that id still
-- gives that same object, wherever it stands now. Its object leaves memory
-- when it leaves the game, and with its area (Adapter:_find_dropped).
function Adapter:_tracked(owner)
    local api = self.api
    local object = api.EEex_GameObject_Get(owner.id)
    object = object and api.EEex_GameObject_CastUserType(object)
    if object and api.EEex_GameObject_IsSprite(object, true) == true and pointer(api, object) == owner.pointer then
        return { object = object, id = owner.id, pointer = owner.pointer }
    end
end

-- A creature target's reward on its way: GiveItemCreate queued one
-- CMessageAddItem, whose Run copies the item into the first empty backpack
-- slot, of a corpse too (it has no death check). Until the receipt the
-- creature goes on: a fight, a scene, its own actions or its death may change
-- it, and none of that stops CIR. Only the reward's own instance is followed
-- (arrived). Chris, 2026-10-22: a reward that cannot arrive needs a fallback
-- that always works; that of a dead recipient goes to the ground.
-- - Alive: the instance is pinned, then held one later tick on.
-- - Dead with the reward in its body: the death drop takes it along with the
--   creature's items; one that came after the drop goes to the ground at the
--   corpse (Adapter:_corpse_reward).
-- - Dead or gone with the reward out of its body once pinned: the death drop
--   took it, or it went with its creature. The reward reached its creature and
--   is held, as the reward of any creature that dies or leaves later.
-- - Gone, while its object is still there (it left the area alive, say): its
--   items are read where it is now. CIR runs no action on it.
-- - Dead or gone and never seen in its body: on the ground (Adapter:_find_dropped).
--   Where its creature is gone from the game and the reward is nowhere, it
--   falls at a party member's feet (Adapter:_lost_reward).
-- A party member keeps its items when it dies (a quest target's recipient).
function Adapter:_observe_creature(pending, receipt)
    if pending.found then return self:_observe_found(pending, receipt) end
    local owner, state = self:_delivery_owner(pending)
    if not owner and state ~= "vanished" then return nil, state end
    local api, queue, queued, created = self.api, self:_queue(), pending.queued, pending.created
    -- Since when its object is gone, and whether the party left the area the
    -- delivery began in (Adapter:_find_dropped).
    if not owner then pending.gone_tick = pending.gone_tick or self.tick end
    local area = api.EEex_Area_GetVisible()
    if not area or text(area.m_resref) ~= pending.area then pending.left = true end
    local current = addition_at(queue, queued.pointer)
    local waiting = not pending.created_pointer and current and same_message(current, queued)
    -- Another item of the reward's name on its way to the creature would be a
    -- second instance like it: the reward could not be told apart.
    for _, message in ipairs(queue.additions) do
        if (message ~= current or not waiting) and message.target == pending.owner.id and
            message.payload.resref == created.resref then return nil, "DELIVERY_QUEUE_CONFLICT" end
    end
    local items = owner and self:_items(owner, true)
    -- Once the message has run, the engine may give its address to another
    -- message (a death drop's adds, say): only the reward's instance tells then.
    local present = queue.addresses[queued.pointer] ~= nil
    if not pending.created_pointer then
        if waiting then
            -- Nothing of the reward's name may arrive before its message runs.
            if items then
                local early, why = arrived(pending.before, items, created.resref)
                if early or why ~= "DELIVERY_MESSAGE_LOST" then return nil, "DELIVERY_EARLY_ITEM" end
            end
            return nil, "DELIVERY_SETTLING"
        end
        -- The exact message has been consumed. Pin the instance its Run made,
        -- then demand another later tick before publishing anything.
        local placed, failure = nil, "DELIVERY_MESSAGE_LOST"
        if items then placed, failure = arrived(pending.before, items, created.resref) end
        if not placed then
            -- Not in a living creature: lost, or changed where another message
            -- has its address. Not in a dead one: dropped.
            if state == "alive" and present and failure == "DELIVERY_MESSAGE_LOST" then
                return nil, "DELIVERY_QUEUE_CONFLICT"
            end
            if state == "alive" or failure ~= "DELIVERY_MESSAGE_LOST" then return nil, failure end
            return self:_find_dropped(pending, queue, owner)
        end
        local copy = reward_copy(placed.payload, created)
        if not copy then return nil, "DELIVERY_PAYLOAD_CHANGED" end
        if copy == "identified" then
            -- CMessageAddItem::Run can identify a zero-lore item again while
            -- copying it from the message. Preserve the source's flags on the
            -- one proven new inventory instance, just as on its message.
            local item = owner.object.m_equipment.m_items:get(placed.slot)
            assert(pointer(api, item) == placed.pointer and same(self:_item(item).payload, placed.payload),
                "CREATED_INSTANCE_CHANGED")
            item.m_flags = created.flags
            assert(same(self:_item(item).payload, created), "CREATED_FLAGS_NOT_PRESERVED")
        end
        pending.created_pointer, pending.created_slot, pending.pinned_tick = placed.pointer, placed.slot, self.tick
        return nil, "DELIVERY_SETTLING"
    end
    if self.tick <= pending.pinned_tick then return nil, "DELIVERY_SETTLING" end
    local kept = items and item_at(items, pending.created_slot)
    if kept and kept.pointer ~= pending.created_pointer then kept = nil end
    if kept and not same(kept.payload, created) then return nil, "CREATED_INSTANCE_CHANGED" end
    local party = owner and api.EEex_Sprite_GetPortraitIndex(owner.object) ~= -1
    if (state == "alive" or party) and not kept then return nil, "CREATED_INSTANCE_CHANGED" end
    if state == "dead" and not party then
        if kept then return self:_corpse_reward(pending, owner, items, kept, queue) end
    end
    local supported, reason = self:_supported(receipt.payload)
    if not supported then return nil, reason end
    receipt.created, receipt.nonce_bound_instance, receipt.slot = 1, 1, pending.created_slot
    if kept then self:_note_placed(owner, kept.pointer) end
    if party then
        receipt.lootable, receipt.holder = 1, "party"
        self.pending = nil
        return receipt
    end
    -- Held by its creature in its backpack, or dropped with it as it died.
    -- That is not proof of lootability: the death drop is the engine's. A quest
    -- reward's recipient that left the party on its way holds it too.
    receipt.lootable, receipt.held, receipt.holder = 0, 1, "creature"
    if state == "alive" then
        -- Session-only provenance. A save reload cannot reconstruct this record
        -- by finding another item with the same name, address, or payload.
        self.held[receipt.index] = { session=receipt.session, revision=receipt.revision,
            nonce=receipt.nonce, source_id=receipt.source_id, target_id=receipt.target_id,
            owner_id=owner.id, owner_pointer=owner.pointer, item_pointer=kept.pointer,
            slot=kept.slot, payload=payload(kept.payload), reward=payload(receipt.payload),
            endpoint=pending.endpoint }
    end
    self.pending = nil
    return receipt
end

-- The reward in the body of its dead creature. The death drop takes it along
-- with the creature's items, unless the reward came after that drop: then it
-- would stay in the body for good, where nobody can take it. Once the drop
-- had time to happen, CIR drops it at the corpse itself, as the reward of a
-- dead target (Adapter:_execute_ground), and follows it into the pile.
function Adapter:_corpse_reward(pending, owner, items, kept, queue)
    pending.dead_tick = pending.dead_tick or self.tick
    if self.tick - pending.dead_tick < Native.DEATH_DROP_TICKS then return nil, "DELIVERY_SETTLING" end
    -- DropItem takes the first item of the name, and its pile must get no
    -- other add first: wait while either could happen.
    for _, item in ipairs(items) do
        if item ~= kept and item.payload.resref == kept.payload.resref then return nil, "DELIVERY_SETTLING" end
    end
    if container_add(queue) then return nil, "DELIVERY_SETTLING" end
    local message, pile = self:_drop(owner, pending.endpoint, items, kept, queue)
    if not message then
        pending.complete = false
        self.last_error = "CREATED_INSTANCE_UNPROVEN"
        return nil, self.last_error
    end
    self:_preserve_message_flags(message, pending.created)
    pending.ground, pending.queued = true, message
    pending.pile_before = pile and self:_pile_items(pile, pending.created.resref) or {}
    pending.pile_pointer = pile and pile.pointer or nil
    pending.created_pointer, pending.created_slot, pending.pinned_tick = nil, nil, nil
    return nil, "DELIVERY_SETTLING"
end

-- A reward whose creature died or went before CIR saw it in its body: the
-- death drop took it, as every item the creature carried, into a ground pile
-- of the area. It is the one item like it in a pile that was not there when
-- the delivery began, while none like it is on its way still. An item like it
-- that the creature carried would fall with it, and the two could not be told
-- apart. Found, it is pinned and confirmed one later tick on (Adapter:_observe_found).
-- Only the piles of the area the delivery began in can tell, and only while
-- the party has not left it: an area's objects leave memory with it, and
-- come back as other objects. Where it left, the reward parks (Adapter:_left).
-- Where the creature is gone from the game (<owner> nil: its object no longer
-- exists) and nothing like the reward has come to the ground or is on its way
-- there for DEATH_DROP_TICKS, the reward is nowhere (Adapter:_lost_reward),
-- with a settling time of its own. Otherwise it waits, at most until the
-- settling time is over.
function Adapter:_find_dropped(pending, queue, owner)
    local created = pending.created
    for _, item in ipairs(pending.before) do
        if reward_copy(item.payload, created) then return nil, "DROPPED_REWARD_AMBIGUOUS" end
    end
    for _, message in ipairs(queue.additions) do
        if message.form == "container" and reward_copy(message.payload, created) then
            return nil, "DELIVERY_SETTLING"
        end
    end
    if pending.left then return self:_left(pending, pending.receipt) end
    if pending.scan_tick and self.tick - pending.scan_tick < Native.DROP_SCAN_TICKS then
        return nil, "DELIVERY_SETTLING"
    end
    pending.scan_tick = self.tick
    local found, count = nil, 0
    for id, pile in pairs(self:_piles(created.resref)) do
        local old = pending.piles[id]
        old = old and old.pointer == pile.pointer and old.items or {}
        for _, item in ipairs(pile.items) do
            if reward_copy(item.payload, created) and not holding(old, item.pointer, item.payload) then
                found, count = { pile = pile, item = item }, count + 1
            end
        end
    end
    if count > 1 then return nil, "DROPPED_REWARD_AMBIGUOUS" end
    if count == 0 then
        if owner or self.tick - pending.gone_tick < Native.DEATH_DROP_TICKS then return nil, "DELIVERY_SETTLING" end
        pending.lost_tick = pending.lost_tick or self.tick
        return self:_lost_reward(pending, queue)
    end
    if reward_copy(found.item.payload, created) == "identified" then
        self:_preserve_container_flags(found.pile, found.item, created)
    end
    pending.found = { id = found.pile.id, pile_pointer = found.pile.pointer, pointer = found.item.pointer }
    pending.found_tick = self.tick
    return nil, "DELIVERY_SETTLING"
end

-- A reward whose creature is gone from the game, and that is nowhere: its
-- message ran when the creature was gone already, or it went with the
-- creature. Chris, 2026-10-26: a reward whose carrier left no body falls to
-- the ground at a party member's feet, with a message. So does this one, as
-- where the carrier was gone before its delivery began
-- (Adapter:_inspect_party_ground): CreateItem on the first member who can
-- drop it, DropItem, then the drop is followed under the delivery's nonce
-- (Adapter:_observe_ground). It waits while no member can.
function Adapter:_lost_reward(pending, queue)
    local p = pending.receipt.payload
    local supported, reason, stack_limit = self:_supported(p)
    if not supported then return nil, reason end
    if container_add(queue) then return nil, "DELIVERY_SETTLING" end
    local member = self:_dropper(p, queue)
    if not member then return nil, "DELIVERY_SETTLING" end
    -- The receipt waits for this one drop; an exception disables the adapter
    -- and never repeats the action.
    local created, message, pile = self:_create_drop(member.owner, pending.endpoint, member.items, member.empty,
        p, stack_limit, queue)
    if not created then
        pending.complete = false
        self.last_error = "CREATED_INSTANCE_UNPROVEN"
        return nil, self.last_error
    end
    pending.ground, pending.queued, pending.created = true, message, created
    pending.notice = party_notice(self.api, member.owner.object, p)
    pending.pile_before = pile and self:_pile_items(pile, created.resref) or {}
    pending.pile_pointer = pile and pile.pointer or nil
    return nil, "DELIVERY_SETTLING"
end

function Adapter:_observe_found(pending, receipt)
    if self.tick <= pending.found_tick then return nil, "DELIVERY_SETTLING" end
    local pile, reason = self:_ground_pile(pending.found.id, pending.endpoint)
    if not pile then return nil, reason end
    if pile.pointer ~= pending.found.pile_pointer or
        not holding(self:_pile_items(pile, pending.created.resref), pending.found.pointer, pending.created) then
        return nil, "CREATED_INSTANCE_CHANGED"
    end
    return self:_on_ground(pending, receipt, pile, pending.found.pointer)
end

-- A parked reward (FLPRCore): did it arrive? As approved for 1.0.3 (Chris,
-- 2026-10-27), CIR remembers the half handover and checks on the next visit of
-- the place; with one more item of the reward's name in the creature or
-- container than before, it arrived, else CIR gives it then. Exactly one more
-- is the reward; none, or several (something else came too), is not.
-- The counts were read as its delivery began (Adapter:_inspect) and are read
-- where it began (Adapter:_settle_endpoint): a living creature its items, a
-- dead one its body and the ground piles of its area (its death drop puts its
-- items there), one gone without a body the piles, where its death drop put
-- what it held. A party member keeps its items, alive or dead, in any area.
-- A store took its reward at once, and its stock row tells (FLPRShop). Its
-- holder tells the core where it is. Nothing is created, moved or read in
-- another area here; while an item of the reward's name is on its way
-- anywhere, the counts could change and it waits.
function Adapter:settle_parked(request)
    if type(request) ~= "table" or request.kind ~= "settle-parked" or request.mode ~= "observe-only" or
        type(request.session) ~= "table" or not integer(request.index, 1, 4096) or
        not hash(request.revision) then return nil, "REQUEST_INVALID" end
    if self.pending then return nil, "MUTATION_PENDING" end
    local counts, reward = settle_counts(request.settle), payload(request.payload)
    if not counts then return nil, "PARKED_EVIDENCE_INVALID" end
    if not reward then return nil, "NATIVE_PAYLOAD_UNSUPPORTED" end
    local endpoint = self:_settle_endpoint(request, counts)
    if not endpoint then return nil, "TARGET_UNKNOWN" end
    local receipt = { kind = request.kind, mode = request.mode, index = request.index,
        session = request.session, token = request.token, revision = request.revision,
        source_id = request.source_id, target_id = request.target_id, settle = request.settle,
        payload = reward, arrived = 0 }
    if endpoint.kind == "store" then
        if self.api.FLPR.Shop:delivered(request.index) then receipt.arrived, receipt.holder = 1, "store" end
        return receipt
    end
    if self:_away(endpoint) then return nil, "AREA_UNAVAILABLE" end
    for _, message in ipairs(self:_queue().additions) do
        if message.payload.resref == reward.resref then return nil, "TARGET_QUEUE_BUSY" end
    end
    local owner, reason, unusable = self:_resolve(endpoint, "target", nil, true)
    if endpoint.kind ~= "creature" then
        if not owner then return nil, reason end
        if count_named(self:_items(owner, false), reward.resref) == counts.held + 1 then
            receipt.arrived, receipt.holder = 1, "container"
        end
        return receipt
    end
    -- An unusable recipient is still the one it was: dead, or in the party.
    owner = owner or ((reason == "TARGET_NOT_ALIVE" or reason == "TARGET_PARTY_MEMBER" or
        reason == "TARGET_FAMILIAR") and unusable) or nil
    if owner then
        local api = self.api
        local party = api.EEex_Sprite_GetPortraitIndex(owner.object) ~= -1
        local body = count_named(self:_items(owner, true), reward.resref)
        if party or api.EEex_GameObject_IsSprite(owner.object, false) == true then
            if body == counts.held + 1 then receipt.arrived, receipt.holder = 1, party and "party" or "creature" end
        elseif body + self:_count_piles(reward.resref) == counts.held + counts.piles + 1 then
            -- In its body where its death drop took nothing after it came.
            receipt.arrived, receipt.holder = 1, body > counts.held and "creature" or "ground"
        end
        return receipt
    end
    if reason == "OWNER_MISSING" and self:_vanished(endpoint) then
        if self:_count_piles(reward.resref) == counts.held + counts.piles + 1 then
            receipt.arrived, receipt.holder = 1, "ground"
        end
        return receipt
    end
    return nil, reason
end

-- The endpoint a parked reward's delivery began with (Adapter:inspect_target):
-- the payee only where a reply had paid it then, the first-of member it took,
-- a group's member as drawn for it.
function Adapter:_settle_endpoint(request, counts)
    local endpoint = self.targets[request.target_id]
    if not endpoint then return nil end
    if counts.paid then return endpoint.paid end
    if endpoint.kind == "group" then return self:_target(request.target_id, request.source_id, request.index) end
    if endpoint.kind == "first-of" then
        local members = self:_first_of(endpoint, request.source_id, request.index)
        return members and members[counts.member]
    end
    return endpoint
end

-- The holders of the parked rewards (FLPRHost gives their settle requests),
-- each with its reward's name. Until a reward settles, no other item of its
-- name goes to its holder, from any place there (Adapter:_inspect), and none
-- leaves it by a handover (Adapter:_hand_over): its counts would take that one
-- for the reward. Other names, and other members of its place, go on.
function Adapter:set_parked(requests)
    local parked = {}
    for _, request in ipairs(requests) do
        local counts, reward = settle_counts(request.settle), payload(request.payload)
        local endpoint = counts and reward and self:_settle_endpoint(request, counts)
        local key = endpoint and holder_key(endpoint)
        if key then parked[key .. "|" .. reward.resref] = true end
    end
    self.parked = parked
    return true
end

function Adapter:_parked_at(endpoint, reward)
    local key = reward and holder_key(endpoint)
    return key ~= nil and self.parked[key .. "|" .. reward.resref] == true
end

function Adapter:_held(request)
    local held = type(request) == "table" and self.held[request.index]
    if not held then return nil, "HELD_RELOAD_UNPROVEN" end
    for _, key in ipairs({ "session", "revision", "nonce", "source_id", "target_id" }) do
        if request[key] ~= held[key] then return nil, "HELD_BINDING_MISMATCH" end
    end
    if request.kind ~= "verify-held-drop" or request.mode ~= "observe-only" or
        not same(payload(request.payload), held.reward) then return nil, "HELD_BINDING_MISMATCH" end
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
    for id in pairs(self.owed_handovers) do
        result.owed_handovers = result.owed_handovers or {}
        result.owed_handovers[#result.owed_handovers + 1] = id
    end
    if self.pending then
        result.pending = { kind = self.pending.receipt.kind, nonce = self.pending.receipt.nonce,
            complete = self.pending.complete, created_pointer = self.pending.created_pointer,
            created_slot = self.pending.created_slot, queued = self.pending.queued,
            pinned_tick = self.pending.pinned_tick, empty = self.pending.empty,
            ground = self.pending.ground, pile_pointer = self.pending.pile_pointer,
            notice = self.pending.notice, dead_tick = self.pending.dead_tick, found = self.pending.found,
            gone_tick = self.pending.gone_tick, left = self.pending.left, scan_tick = self.pending.scan_tick,
            lost_tick = self.pending.lost_tick }
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
            elseif endpoint.kind == "first-of" then
                local members = {}
                for _, group in ipairs(endpoint.groups) do
                    for _, member in ipairs(group.members) do
                        local owner, reason, unusable = self:_resolve(member, "target")
                        local seen = owner or unusable
                        members[#members + 1] = { area = member.area, script_name = member.script_name,
                            object_id = seen and seen.id, pointer = seen and seen.pointer, unavailable = reason }
                    end
                end
                result[kind][id] = { members = members }
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
    self.placed, self.owed_handovers, self.handover_retry, self.unmarked = {}, {}, nil, {}
end

return Native
