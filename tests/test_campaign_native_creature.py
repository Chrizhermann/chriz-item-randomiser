"""Hermetic creature-target checks for the bounded native adapter.

The fake engine models the static evidence in
docs/native-creature-transport-evidence.md: GiveItemCreate queues one
CMessageAddItem (type 67, subtype 2), and a later handler update places a heap
copy in the first empty backpack slot. No game process or game file is used.
"""
from pathlib import Path
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT21

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = (ROOT / "prototype" / "FLPRNat.lua").read_text(encoding="utf-8")
CASES = r'''
local cases = {}
local function setup(N)
    local f = {calls={}, casts={}, queue={}, next_address=900}
    local function str(value) return {get=function() return value end} end
    local function item(ref, address, flags)
        return {cResRef=str(ref), m_useCount1=0, m_useCount2=0, m_useCount3=0,
            m_wear=0, m_flags=flags == nil and 1 or flags, address=address}
    end
    f.item = item
    local function sprite(address, name, slots)
        return {address=address, sprite=true, alive=true, portrait=-1, m_scriptName=str(name),
            m_typeAI={m_EnemyAlly=128}, slots=slots,
            m_equipment={m_items={get=function(_, slot) return slots[slot] end}}}
    end
    f.sprite = sprite
    f.source1 = sprite(101, "FLPRS1", {[21]=item("FLPRI1", 301)})
    f.source2 = sprite(102, "FLPRS2", {[21]=item("FLPRI2", 302)})
    -- Equipped armour and one unrelated backpack item must never change.
    f.creature = sprite(201, "FLPRC1", {[1]=item("PLAT01", 311), [21]=item("OTHER", 312)})
    f.objects = {[1]=f.source1, [2]=f.source2, [3]=f.creature}
    f.ids = {[f.source1]=1, [f.source2]=2, [f.creature]=3}
    f.area = {m_resref=str("BG2600"), m_lVertSort={1, 2, 3}, m_lVertSortBack={},
        m_lVertSortFlight={}, m_lVertSortUnder={}}
    f.header = {maxStackable=1, itemFlags=12, genericName=7100, identifiedName=7101}
    local api = {}
    api.EngineGlobals = {g_pBaldurChitin={m_cMessageHandler={m_messageList=f.queue}}}
    api.EEex_CastUD = function(object, kind) f.casts[#f.casts + 1] = kind; return object end
    api.EEex_Area_GetVisible = function() return f.area end
    api.EEex_GameObject_Get = function(id) return f.objects[id] end
    api.EEex_GameObject_CastUserType = function(o) return o end
    api.EEex_GameObject_IsSprite = function(o, dead) return o.sprite and (dead or o.alive) or false end
    api.EEex_Sprite_GetPortraitIndex = function(o) return o.portrait end
    -- GLOBAL variables by name in any letter case, as the engine finds them
    -- (the death variables SPRITE_IS_DEAD<script name>), and the party.
    f.globals = {}
    api.EEex_GameState_GetGlobalInt = function(name) return f.globals[name:upper()] or 0 end
    api.EEex_Sprite_GetInPortrait = function() return nil end
    api.EEex_Sprite_GetName = function(o) return o.name end
    api.EEex_Utility_IterateCPtrList = function(list, fn) for _, entry in ipairs(list) do fn(entry) end end
    api.EEex_UDToPtr = function(o) return o.address end
    api.EEex_Resource_Demand = function(ref, ext) assert(ext == "ITM"); return f.header end
    function f.message(target, ref, subtype, source)
        f.next_address = f.next_address + 1
        return {address=f.next_address, m_targetId=target, m_sourceId=source or target,
            m_item=item(ref, f.next_address + 5000), m_slotNum=32767, m_bCompressContainer=1,
            virtual_GetMsgType=function() return 67 end,
            virtual_GetMsgSubType=function() return subtype or 2 end,
            virtual_GetCommType=function() return 1 end}
    end
    api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly = function(action, o)
        f.calls[#f.calls + 1] = action
        local ref = action:match('^DestroyItem%("([^"]+)"%)$')
        if ref then
            for slot, value in pairs(o.slots) do
                if value.cResRef:get():lower() == ref then o.slots[slot] = nil end
            end
            return
        end
        ref = action:match('^GiveItemCreate%("([^"]+)",Myself,0,0,0%)$')
        assert(ref, "unexpected action " .. action)
        f.queue[#f.queue + 1] = f.message(f.ids[o], ref:upper())
    end
    -- One CMessageHandler::AsynchronousUpdate: Run each queued creature item
    -- message by heap-copying it into the first empty backpack slot.
    function f.update()
        while #f.queue > 0 do
            local message = table.remove(f.queue, 1)
            local target = f.objects[message.m_targetId]
            if message.virtual_GetMsgSubType() == 2 and target and target.sprite then
                for slot = 18, 33 do
                    if not target.slots[slot] then
                        f.next_address = f.next_address + 1
                        target.slots[slot] = item(message.m_item.cResRef:get(), f.next_address, message.m_item.m_flags)
                        break
                    end
                end
            end
        end
    end
    local function source(index)
        return {area="BG2600", script_name="FLPRS" .. index, identity=string.rep(tostring(index), 64),
            payload={resref="flpri" .. index, charges={0, 0, 0}, expiration=0, flags=1}}
    end
    f.source_ids = {"cre:flprs1/item/0", "cre:flprs2/item/0"}
    f.target_id = "are:bg2600/actor/40"
    f.fixture = {sources={[f.source_ids[1]]=source(1), [f.source_ids[2]]=source(2)},
        targets={[f.target_id]={kind="creature", area="BG2600", script_name="FLPRC1", identity=string.rep("c", 64)}}}
    f.api = api
    f.adapter = N.new(api, f.fixture)
    f.session = {}
    function f.command(index, nonce)
        local id = f.source_ids[index]
        return {mode="execute-once", kind="deliver", session=f.session, nonce=nonce or (10 + index), index=index,
            revision=string.rep("e", 64), identity=string.rep("c", 64), source_id=id, target_id=f.target_id,
            quantity=1, payload=f.fixture.sources[id].payload}
    end
    f.tick = 0
    function f.boundary() f.tick = f.tick + 1; assert(f.adapter:on_observation_boundary(f.tick)) end
    function f.start(index)
        assert(f.adapter:inspect_target(f.target_id))
        f.boundary()
        local command = f.command(index)
        assert(f.adapter:execute(command))
        return command
    end
    return f
end
local function fail(value, reason, want)
    assert(value == nil and reason == want, tostring(reason) .. " ~= " .. want)
end
local function unrelated_intact(f)
    assert(f.creature.slots[1].address == 311 and f.creature.slots[21].address == 312)
end
local function clone(value)
    if type(value) ~= "table" then return value end
    local result = {}
    for key, entry in pairs(value) do result[key] = clone(entry) end
    return result
end
local function equal_tree(actual, expected)
    if type(actual) ~= "table" or type(expected) ~= "table" then
        assert(actual == expected, "read-only observation changed world state")
        return
    end
    for key, value in pairs(expected) do equal_tree(actual[key], value) end
    for key in pairs(actual) do assert(expected[key] ~= nil, "observation added world state") end
end
local function read_only(f, operation)
    local before = clone({objects=f.objects, queue=f.queue, party=f.party, calls=f.calls})
    local result, reason = operation()
    equal_tree({objects=f.objects, queue=f.queue, party=f.party, calls=f.calls}, before)
    return result, reason
end
local function complete_held(f, index)
    -- Suppress the source through the real adapter so later uniqueness scans
    -- see the single delivered copy, as in the controlled campaign fixture.
    local removal = f.command(index, index)
    removal.kind, removal.target_id = "suppress", nil
    removal.identity = f.fixture.sources[removal.source_id].identity
    assert(f.adapter:inspect_source(removal.source_id)); f.boundary()
    assert(f.adapter:execute(removal)); f.boundary(); assert(f.adapter:observe(removal))
    local command = f.start(index)
    f.update(); f.boundary()
    local result, reason = f.adapter:observe(command); fail(result, reason, "DELIVERY_SETTLING")
    f.boundary(); local receipt = assert(f.adapter:observe(command))
    local request = {kind="verify-held-drop", mode="observe-only", index=command.index,
        session=command.session, revision=command.revision, nonce=command.nonce,
        source_id=command.source_id, target_id=command.target_id, payload=clone(command.payload), token={}}
    return request, receipt, command
end
local function dropped(N)
    local f = setup(N)
    local request = complete_held(f, 1)
    local binding = assert(f.adapter:held_binding(request))
    f.creature.alive = false
    f.creature.slots[binding.slot] = nil
    f.ground_item = f.item("FLPRI1", 1801)
    f.ground = {address=701, sprite=false, m_containerType=4, m_lstItems={f.ground_item},
        m_scriptName={get=function() return "GROUND" end}}
    f.objects[4] = f.ground; f.area.m_lVertSortBack = {4}
    f.party = {}
    f.api.EEex_Sprite_GetInPortrait = function(index) return f.party[index] end
    local candidate = {owner_pointer=binding.owner_pointer, original_item_pointer=binding.original_item_pointer,
        slot=binding.slot, new_item_pointer=tostring(f.ground_item.address),
        container_pointer=tostring(f.ground.address), payload=clone(binding.payload)}
    return f, request, candidate
end
local function two_same_resref_held(N)
    local f = setup(N)
    f.source2.slots[21] = f.item("FLPRI1", 302)
    f.fixture.sources[f.source_ids[2]].payload.resref = "flpri1"
    f.adapter = N.new(f.api, f.fixture)
    local first, receipt1 = complete_held(f, 1)
    local first_item = f.creature.slots[18]
    local second, receipt2 = complete_held(f, 2)
    assert(f.creature.slots[18] == first_item and first_item.address ~= f.creature.slots[19].address)
    return f, first, second, receipt1, receipt2
end

function cases.one_reward_is_bound_to_its_message_and_held_after_two_later_ticks(N)
    local f = setup(N); local a = f.adapter
    local command = f.start(1)
    assert(#f.calls == 1 and f.calls[1] == 'GiveItemCreate("flpri1",Myself,0,0,0)')
    assert(#f.queue == 1 and f.creature.slots[18] == nil)
    local pending = a:diagnostics().pending
    assert(pending.queued.form == "creature" and pending.queued.target == 3 and pending.queued.source == 3)
    f.boundary(); local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    f.update(); f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    assert(a:diagnostics().pending.created_slot == 18)
    x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    f.boundary(); local receipt = assert(a:observe(command))
    assert(receipt.created == 1 and receipt.nonce_bound_instance == 1 and receipt.held == 1)
    assert(receipt.lootable == 0 and receipt.holder == "creature" and receipt.slot == 18)
    assert(receipt.session == command.session and receipt.payload.flags == 1)
    unrelated_intact(f); assert(#f.calls == 1)
    x, e = a:execute(command); fail(x, e, "ALREADY_ATTEMPTED")
end
function cases.creature_message_preserves_the_selected_flags_only(N)
    -- Identified and stolen (Baeloth's items), and bits a mod set (0x3E7).
    for _, values in ipairs({{0, 1}, {1, 0}, {0, 5}, {1, 4}, {1, 0x3E7}, {0, 0x80000004}}) do
        local engine_flag, selected_flag = values[1], values[2]
        local f = setup(N)
        f.fixture.sources[f.source_ids[1]].payload.flags = selected_flag
        f.adapter = N.new(f.api, f.fixture)
        local unrelated = f.message(77, "OTHER", 2)
        unrelated.m_item.m_flags = 8
        f.queue[1] = unrelated
        f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly = function(action, owner)
            f.calls[#f.calls + 1] = action
            local message = f.message(f.ids[owner], "FLPRI1", 2)
            message.m_item.m_flags = engine_flag
            f.queue[#f.queue + 1] = message
        end
        local command = f.start(1)
        assert(#f.queue == 2 and f.queue[1] == unrelated)
        assert(unrelated.m_item.m_flags == 8 and f.queue[2].m_item.m_flags == selected_flag)
        f.update(); f.boundary()
        local result, reason = f.adapter:observe(command); fail(result, reason, "DELIVERY_SETTLING")
        f.boundary(); local receipt = assert(f.adapter:observe(command))
        assert(receipt.payload.flags == selected_flag and f.creature.slots[18].m_flags == selected_flag)
        assert(unrelated.m_item.m_flags == 8)
        unrelated_intact(f)
    end
    -- Reproduced with RING43: the engine identifies the later heap copy too.
    -- Preserve both an unidentified source and its other flags.
    for _,selected in ipairs({0,2,4,0x3E6,0x80000004}) do
        local f=setup(N)
        f.fixture.sources[f.source_ids[1]].payload.flags=selected
        f.adapter=N.new(f.api,f.fixture)
        local command=f.start(1);f.update()
        f.creature.slots[18].m_flags=selected+1
        f.boundary()
        local result,reason=f.adapter:observe(command);fail(result,reason,'DELIVERY_SETTLING')
        assert(f.creature.slots[18].m_flags==selected)
        f.boundary();local receipt=assert(f.adapter:observe(command))
        assert(receipt.payload.flags==selected and #f.calls==1)
        unrelated_intact(f)
    end
end
function cases.two_rewards_to_one_creature_each_bind_their_own_instance(N)
    local f = setup(N); local a = f.adapter
    local first = f.start(1); f.update(); f.boundary(); a:observe(first); f.boundary()
    local receipt1 = assert(a:observe(first))
    local second = f.start(2)
    assert(f.calls[2] == 'GiveItemCreate("flpri2",Myself,0,0,0)')
    f.update(); f.boundary(); local x, e = a:observe(second); fail(x, e, "DELIVERY_SETTLING")
    f.boundary(); local receipt2 = assert(a:observe(second))
    assert(receipt1.slot == 18 and receipt2.slot == 19 and receipt2.payload.resref == "flpri2")
    assert(f.creature.slots[18].cResRef:get() == "FLPRI1" and f.creature.slots[19].cResRef:get() == "FLPRI2")
    unrelated_intact(f)
end
function cases.same_resref_originals_deliver_sequentially_as_distinct_held_instances(N)
    local f, first, second, receipt1, receipt2 = two_same_resref_held(N)
    assert(receipt1.slot == 18 and receipt2.slot == 19)
    assert(receipt1.source_id ~= receipt2.source_id and receipt1.payload.resref == "flpri1" and
        receipt2.payload.resref == "flpri1")
    local binding1 = assert(read_only(f, function() return f.adapter:held_binding(first) end))
    local binding2 = assert(read_only(f, function() return f.adapter:held_binding(second) end))
    assert(binding1.slot == 18 and binding2.slot == 19 and
        binding1.original_item_pointer == tostring(f.creature.slots[18].address) and
        binding2.original_item_pointer == tostring(f.creature.slots[19].address) and
        binding1.original_item_pointer ~= binding2.original_item_pointer)
    assert(f.creature.slots[18].cResRef:get() == "FLPRI1" and
        f.creature.slots[19].cResRef:get() == "FLPRI1")
    unrelated_intact(f)
end
function cases.absent_or_elsewhere_target_is_deferred_without_reservation(N)
    local f = setup(N); local a = f.adapter
    f.area.m_lVertSort = {1, 2}
    local x, e = a:inspect_target(f.target_id); fail(x, e, "OWNER_MISSING")
    f.area.m_resref = {get=function() return "BG0100" end}
    x, e = a:inspect_target(f.target_id); fail(x, e, "AREA_UNAVAILABLE")
    f.boundary(); x, e = a:execute(f.command(1)); fail(x, e, "RESERVATION_MISMATCH")
    assert(#f.calls == 0 and #f.queue == 0)
end
-- Without a reward to place; with one, a dead creature's reward goes to the
-- ground at its corpse (the ground cases below).
function cases.dead_party_or_familiar_creatures_never_receive(N)
    for _, variant in ipairs({"dead", "party", "familiar"}) do
        local f = setup(N)
        if variant == "dead" then f.creature.alive = false
        elseif variant == "party" then f.creature.portrait = 0
        else f.creature.m_typeAI.m_EnemyAlly = 3 end
        local want = ({dead="TARGET_NOT_ALIVE", party="TARGET_PARTY_MEMBER", familiar="TARGET_FAMILIAR"})[variant]
        local x, e = f.adapter:inspect_target(f.target_id); fail(x, e, want)
        assert(#f.calls == 0)
    end
end
function cases.same_named_corpse_or_duplicate_makes_identity_ambiguous(N)
    for _, alive in ipairs({false, true}) do
        local f = setup(N)
        local twin = f.sprite(202, "FLPRC1", {}); twin.alive = alive
        f.objects[4] = twin; f.area.m_lVertSortBack = {4}
        local x, e = f.adapter:inspect_target(f.target_id); fail(x, e, "OWNER_AMBIGUOUS")
    end
end
function cases.full_backpack_is_refused_before_any_journal(N)
    local f = setup(N)
    for slot = 18, 33 do
        if not f.creature.slots[slot] then f.creature.slots[slot] = f.item("FILL", 400 + slot) end
    end
    local x, e = f.adapter:inspect_target(f.target_id); fail(x, e, "TARGET_INVENTORY_FULL")
    -- Quick and weapon slots are not backpack capacity for CMessageAddItem.
    f.creature.slots[33] = nil; f.creature.slots[15] = nil; f.creature.slots[35] = nil
    assert(f.adapter:inspect_target(f.target_id))
    assert(#f.calls == 0)
end
function cases.queued_message_for_target_or_fixture_item_blocks_inspection(N)
    for _, variant in ipairs({"any-to-target", "container-fixture", "creature-fixture"}) do
        local f = setup(N)
        if variant == "any-to-target" then f.queue[1] = f.message(3, "OTHER", 34)
        elseif variant == "container-fixture" then f.queue[1] = f.message(77, "FLPRI2", 12)
        else f.queue[1] = f.message(78, "FLPRI1", 2) end
        local x, e = f.adapter:inspect_target(f.target_id); fail(x, e, "TARGET_QUEUE_BUSY")
        assert(#f.calls == 0)
    end
end
-- CMessageSetDialogWait (67/40) only sets two dialog-wait fields of its target.
function cases.dialog_wait_for_creature_target_never_blocks_its_delivery(N)
    local f = setup(N); f.queue[1] = f.message(3, "OTHER", 40)
    local command = f.start(1)
    f.update(); f.queue[1] = f.message(3, "OTHER", 40); f.boundary()
    local x, e = f.adapter:observe(command); fail(x, e, "DELIVERY_SETTLING")
    f.boundary(); local receipt = assert(f.adapter:observe(command))
    assert(receipt.holder == "creature" and receipt.slot == 18 and #f.calls == 1)
    for _, kind in ipairs(f.casts) do assert(kind == "CMessageAddItem") end
    unrelated_intact(f)
end
function cases.unrelated_queue_traffic_is_neither_cast_nor_blocking(N)
    local f = setup(N); f.queue[1] = f.message(77, "OTHER", 34); f.queue[2] = f.message(78, "OTHER", 2)
    local command = f.start(1)
    for _, kind in ipairs(f.casts) do assert(kind == "CMessageAddItem") end
    -- Inspect and before-execute cast the unrelated subtype-2 message; the
    -- after-execute read also casts the operation's own new message.
    assert(#f.casts == 4)
    f.update(); f.boundary()
    local x, e = f.adapter:observe(command); fail(x, e, "DELIVERY_SETTLING")
    f.boundary(); assert(f.adapter:observe(command))
end
function cases.execute_requires_exactly_one_matching_bound_message(N)
    for _, variant in ipairs({"none", "target", "source", "payload-2", "payload-8", "form", "extra", "synchronous"}) do
        local f = setup(N); local a = f.adapter
        f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly = function(action)
            f.calls[#f.calls + 1] = action
            if variant == "none" then return end
            local m = f.message(3, "FLPRI1")
            if variant == "target" then m.m_targetId = 99
            elseif variant == "source" then m.m_sourceId = 99
            elseif variant == "payload-2" then m.m_item.m_flags = 2
            elseif variant == "payload-8" then m.m_item.m_flags = 8
            elseif variant == "form" then m.virtual_GetMsgSubType = function() return 12 end
            elseif variant == "extra" then f.queue[#f.queue + 1] = f.message(99, "OTHER", 2)
            elseif variant == "synchronous" then f.creature.slots[18] = f.item("FLPRI1", 777) end
            f.queue[#f.queue + 1] = m
        end
        assert(a:inspect_target(f.target_id)); f.boundary()
        local command = f.command(1)
        local x, e = a:execute(command); fail(x, e, "CREATED_INSTANCE_UNPROVEN")
        f.boundary(); x, e = a:observe(command); fail(x, e, "CREATED_INSTANCE_UNPROVEN")
        x, e = a:execute(command); fail(x, e, "MUTATION_PENDING")
        assert(#f.calls == 1)
    end
end
function cases.unexpected_placement_is_never_accepted(N)
    local wants = {merged="DELIVERY_PAYLOAD_CHANGED", quick="DELIVERY_PLACEMENT_UNEXPECTED",
        two="DELIVERY_PLACEMENT_UNEXPECTED", lost="DELIVERY_MESSAGE_LOST", payload="DELIVERY_PAYLOAD_CHANGED",
        unrelated="DELIVERY_MESSAGE_LOST"}
    for _, variant in ipairs({"merged", "quick", "two", "lost", "payload", "unrelated"}) do
        local f = setup(N); local a = f.adapter
        -- An item of the reward's name it holds already, merged into instead of a new one.
        if variant == "merged" then f.creature.slots[22] = f.item("FLPRI1", 313) end
        local command = f.start(1); table.remove(f.queue, 1)
        if variant == "merged" then f.creature.slots[22].m_useCount1 = 2
        elseif variant == "quick" then f.creature.slots[15] = f.item("FLPRI1", 778)
        elseif variant == "two" then f.creature.slots[18] = f.item("FLPRI1", 778); f.creature.slots[19] = f.item("FLPRI1", 779)
        elseif variant == "payload" then f.creature.slots[18] = f.item("FLPRI1", 778, 0)
        elseif variant == "unrelated" then f.creature.slots[21].m_useCount1 = 2 end
        f.boundary(); local x, e = a:observe(command); fail(x, e, wants[variant])
    end
end
-- The creature's other items may change while its reward is on its way: a
-- potion it drinks in a fight, an item it is given first, or the one that the
-- Run of a full backpack puts on the ground to make room for the reward.
function cases.other_items_may_change_while_a_reward_is_on_its_way(N)
    for _, variant in ipairs({"drunk", "given", "displaced"}) do
        local f = setup(N); local a = f.adapter
        if variant == "drunk" then f.creature.slots[15] = f.item("POTN08", 314) end
        local command = f.start(1)
        local slot = 18
        if variant == "drunk" then f.creature.slots[15] = nil
        elseif variant == "given" then table.insert(f.queue, 1, f.message(3, "OTHER2", 2)); slot = 19
        else table.remove(f.queue, 1); f.creature.slots[21] = f.item("FLPRI1", 778); slot = 21 end
        f.update(); f.boundary()
        local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
        f.boundary(); local receipt = assert(a:observe(command))
        assert(receipt.holder == "creature" and receipt.held == 1 and receipt.slot == slot, variant)
        assert(f.creature.slots[slot].cResRef:get() == "FLPRI1" and #f.calls == 1)
    end
end
-- An item of the reward's name the creature had is not the reward where it
-- moved, or where it changed in its own slot outside the backpack (a charge
-- used in a fight): the reward is the new one in the backpack.
function cases.items_of_the_rewards_name_it_had_may_move_or_be_used(N)
    for _, variant in ipairs({"used", "moved", "packed", "equipped"}) do
        local f = setup(N); local a = f.adapter
        local slots = f.creature.slots
        slots[15] = f.item("FLPRI1", 313); slots[15].m_useCount1 = 3
        slots[22] = f.item("FLPRI1", 314)
        local command = f.start(1)
        -- While the reward is on its way.
        if variant == "used" then slots[15].m_useCount1 = 2
        elseif variant == "moved" then slots[16], slots[15] = slots[15], nil
        elseif variant == "packed" then slots[25], slots[15] = slots[15], nil; slots[25].m_useCount1 = 2
        else slots[10], slots[22] = slots[22], nil end
        f.boundary(); local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
        -- And as it arrives.
        f.update()
        if variant == "used" then slots[15].m_useCount1 = 1
        elseif variant == "equipped" then slots[11], slots[10] = slots[10], nil end
        f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
        assert(a:diagnostics().pending.created_slot == 18, variant)
        f.boundary(); local receipt = assert(a:observe(command))
        assert(receipt.holder == "creature" and receipt.slot == 18 and slots[18].cResRef:get() == "FLPRI1", variant)
        assert(#f.calls == 1)
    end
end
-- A fight or a scene sends the creature messages; items go elsewhere.
function cases.messages_for_the_creature_let_its_delivery_go_on(N)
    for _, variant in ipairs({"to-target", "fixture-item", "other-item"}) do
        local f = setup(N); local a = f.adapter
        local command = f.start(1)
        if variant == "to-target" then f.queue[2] = f.message(3, "OTHER", 34)
        elseif variant == "fixture-item" then f.queue[2] = f.message(77, "FLPRI2", 12)
        else f.queue[2] = f.message(3, "OTHER", 2) end
        f.boundary(); local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
        -- A hit while the reward is pinned.
        f.update(); f.queue[1] = f.message(3, "OTHER", 34)
        f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
        f.boundary(); local receipt = assert(a:observe(command))
        assert(receipt.holder == "creature" and receipt.slot == 18 and f.creature.slots[18].cResRef:get() == "FLPRI1")
        unrelated_intact(f); assert(#f.calls == 1)
    end
end
-- Another item of the reward's name for the creature would be a second one
-- like it; a changed message is not the one CIR bound.
function cases.competing_or_changed_messages_stop_observation(N)
    for _, variant in ipairs({"same-name", "changed"}) do
        local f = setup(N); local a = f.adapter
        local command = f.start(1)
        if variant == "same-name" then f.queue[2] = f.message(3, "FLPRI1", 2, 78)
        else f.queue[1].m_item.m_flags = 0 end
        f.boundary(); local x, e = a:observe(command); fail(x, e, "DELIVERY_QUEUE_CONFLICT")
    end
end
function cases.inventory_change_while_message_pending_is_rejected(N)
    local f = setup(N); local a = f.adapter
    local command = f.start(1); f.creature.slots[19] = f.item("FLPRI1", 790)
    f.boundary(); local x, e = a:observe(command); fail(x, e, "DELIVERY_EARLY_ITEM")
end
function cases.pinned_instance_must_survive_the_second_observation(N)
    for _, variant in ipairs({"pointer", "slot", "removed"}) do
        local f = setup(N); local a = f.adapter
        local command = f.start(1); f.update(); f.boundary()
        local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
        local placed = f.creature.slots[18]
        if variant == "pointer" then f.creature.slots[18] = f.item("FLPRI1", 791)
        elseif variant == "slot" then f.creature.slots[18] = nil; f.creature.slots[20] = placed
        else f.creature.slots[18] = nil end
        f.boundary(); x, e = a:observe(command); fail(x, e, "CREATED_INSTANCE_CHANGED")
    end
end
-- Once the reward's message has run, the engine may give its address to
-- another message at once: the reward's instance tells. Another item message
-- of the reward's name for the creature still stops CIR, at that address too.
function cases.reused_message_address_never_stops_a_delivery(N)
    for _, variant in ipairs({"before", "after", "same-name", "unrun"}) do
        local f = setup(N); local a = f.adapter
        local command = f.start(1); local address = f.queue[1].address
        local function reuse(ref, subtype)
            local reused = f.message(3, ref, subtype); reused.address = address; f.queue[1] = reused
        end
        if variant == "unrun" then
            -- Its message gone without running, another one at its address.
            reuse("OTHER", 34); f.boundary()
            local x, e = a:observe(command); fail(x, e, "DELIVERY_QUEUE_CONFLICT")
        else
            f.update()
            if variant == "before" then reuse("OTHER", 34) end
            f.boundary(); local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
            assert(a:diagnostics().pending.created_slot == 18, variant)
            if variant == "after" then reuse("OTHER", 34) elseif variant == "same-name" then reuse("FLPRI1", 2) end
            f.boundary()
            if variant == "same-name" then
                x, e = a:observe(command); fail(x, e, "DELIVERY_QUEUE_CONFLICT")
            else
                local receipt = assert(a:observe(command))
                assert(receipt.held == 1 and receipt.holder == "creature" and receipt.slot == 18, variant)
                unrelated_intact(f)
            end
        end
    end
end
function cases.stuck_creature_message_times_out_and_never_recovers(N)
    local f = setup(N); local a = f.adapter
    local command = f.start(1)
    assert(a:on_observation_boundary(f.tick + N.MAX_SETTLE_TICKS))
    local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING_TIMEOUT")
    f.update(); assert(a:on_observation_boundary(f.tick + N.MAX_SETTLE_TICKS + 1))
    x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING_TIMEOUT")
    assert(#f.calls == 1)
end
function cases.owner_replacement_or_inventory_change_before_execute_is_rejected(N)
    local f = setup(N); local a = f.adapter
    assert(a:inspect_target(f.target_id)); f.boundary()
    f.creature.address = 999
    local x, e = a:execute(f.command(1)); fail(x, e, "OWNER_CHANGED")
    f = setup(N); a = f.adapter; assert(a:inspect_target(f.target_id)); f.boundary()
    f.creature.slots[22] = f.item("OTHER", 555)
    x, e = a:execute(f.command(1)); fail(x, e, "INVENTORY_CHANGED")
    assert(#f.calls == 0)
end
function cases.reload_observation_is_never_replayed(N)
    local f = setup(N); local command = f.command(1); command.mode = "observe-only"
    local x, e = f.adapter:observe(command); fail(x, e, "PENDING_RELOAD_UNPROVEN")
    assert(#f.calls == 0)
end
function cases.diagnostics_keep_dead_creature_inventory_as_read_only_evidence(N)
    local f = setup(N); f.creature.alive = false
    local state = f.adapter:diagnostics().targets[f.target_id]
    assert(state.unavailable == "TARGET_NOT_ALIVE" and state.object_id == 3 and #state.items == 2)
    assert(#f.calls == 0)
end
function cases.creature_target_configuration_is_strict(N)
    local f = setup(N)
    local target = f.fixture.targets[f.target_id]
    target.x = 10
    local ok, e = pcall(function() N.new(f.api, f.fixture) end)
    assert(not ok and tostring(e):find("NATIVE_POSITION_INVALID", 1, true))
    target.x = nil; target.kind = "shop"
    ok, e = pcall(function() N.new(f.api, f.fixture) end)
    assert(not ok and tostring(e):find("NATIVE_TARGET_KIND_UNSUPPORTED", 1, true))
    target.kind = "creature"; f.api.EEex_Sprite_GetPortraitIndex = nil
    ok, e = pcall(function() N.new(f.api, f.fixture) end)
    assert(not ok and tostring(e):find("EEex_Sprite_GetPortraitIndex_MISSING", 1, true))
end
function cases.held_binding_retains_each_completed_delivery_and_returns_payload_copies(N)
    local f = setup(N)
    local first, receipt, command = complete_held(f, 1)
    local initial = assert(read_only(f, function() return f.adapter:held_binding(first) end))
    assert(initial.owner_pointer == "201" and initial.slot == 18)
    assert(initial.original_item_pointer == tostring(f.creature.slots[18].address))
    assert(initial.payload.resref == "flpri1" and initial.payload.flags == 1)
    -- None of these public tables may alias the adapter's retained payload.
    initial.payload.charges[1] = 123; initial.payload.flags = 0
    receipt.payload.charges[2] = 456; receipt.payload.resref = "other"
    receipt.source_id = "changed"; receipt.nonce = 999; receipt.session = {}
    command.payload.charges[3] = 789; command.revision = string.rep("f", 64)
    local second = complete_held(f, 2)
    local first_again = assert(read_only(f, function() return f.adapter:held_binding(first) end))
    local second_binding = assert(read_only(f, function() return f.adapter:held_binding(second) end))
    assert(first_again.slot == 18 and first_again.payload.charges[1] == 0 and
        first_again.payload.charges[2] == 0 and first_again.payload.charges[3] == 0)
    assert(first_again.payload.resref == "flpri1" and first_again.payload.flags == 1)
    assert(second_binding.slot == 19 and second_binding.payload.resref == "flpri2")
    assert(first_again.original_item_pointer ~= second_binding.original_item_pointer)
    assert(f.adapter:diagnostics().pending == nil); unrelated_intact(f)
end
function cases.held_binding_is_absent_before_receipt_after_destroy_or_in_a_new_adapter(N)
    local f = setup(N)
    local command = f.start(1)
    local request = clone(command); request.session = command.session
    request.kind, request.mode = "verify-held-drop", "observe-only"
    local x, e = read_only(f, function() return f.adapter:held_binding(request) end)
    fail(x, e, "HELD_RELOAD_UNPROVEN")
    f = setup(N); request = complete_held(f, 1)
    local replacement = N.new(f.api, f.fixture)
    x, e = read_only(f, function() return replacement:held_binding(request) end)
    fail(x, e, "HELD_RELOAD_UNPROVEN")
    f.adapter:destroy()
    x, e = read_only(f, function() return f.adapter:held_binding(request) end)
    fail(x, e, "HELD_RELOAD_UNPROVEN")
    x, e = read_only(f, function() return f.adapter:verify_held_drop(request, {}) end)
    fail(x, e, "HELD_RELOAD_UNPROVEN")
end
function cases.held_methods_reject_missing_or_changed_request_identity(N)
    for _, key in ipairs({"index", "session", "revision", "nonce", "source_id", "target_id", "kind", "mode", "payload"}) do
        for _, variant in ipairs({"missing", "changed"}) do
            local f, request, candidate = dropped(N)
            if variant == "missing" then request[key] = nil
            elseif key == "session" then request[key] = {}
            elseif key == "index" or key == "nonce" then request[key] = request[key] + 1
            elseif key == "payload" then request.payload.flags = 0
            else request[key] = "wrong" end
            local want = key == "index" and "HELD_RELOAD_UNPROVEN" or "HELD_BINDING_MISMATCH"
            local x, e = read_only(f, function() return f.adapter:held_binding(request) end); fail(x, e, want)
            x, e = read_only(f, function() return f.adapter:verify_held_drop(request, candidate) end); fail(x, e, want)
        end
    end
end
function cases.held_methods_reject_malformed_request_payload_without_throwing(N)
    for _, shape in ipairs({"number", "missing-charges", "scalar-charges", "extra-charge", "missing-charge"}) do
        local f, request, candidate = dropped(N)
        if shape == "number" then request.payload = 7
        elseif shape == "missing-charges" then request.payload.charges = nil
        elseif shape == "scalar-charges" then request.payload.charges = 7
        elseif shape == "extra-charge" then request.payload.charges[4] = 0
        else request.payload.charges[3] = nil end
        local x, e = read_only(f, function() return f.adapter:held_binding(request) end)
        fail(x, e, "HELD_BINDING_MISMATCH")
        x, e = read_only(f, function() return f.adapter:verify_held_drop(request, candidate) end)
        fail(x, e, "HELD_BINDING_MISMATCH")
    end
end
function cases.held_binding_rejects_changed_owner_slot_item_payload_or_duplicate_name(N)
    local wants = {pointer="OWNER_CHANGED", id="OWNER_CHANGED", missing="OWNER_MISSING",
        renamed="OWNER_MISSING", duplicate="OWNER_AMBIGUOUS", corpse_twin="OWNER_AMBIGUOUS",
        dead="TARGET_NOT_ALIVE", party="TARGET_PARTY_MEMBER", familiar="TARGET_FAMILIAR"}
    for _, variant in ipairs({"pointer", "id", "missing", "renamed", "duplicate", "corpse_twin",
        "dead", "party", "familiar", "item-pointer", "slot", "removed", "flags", "charge", "wear", "resref", "item-duplicate"}) do
        local f = setup(N); local request = complete_held(f, 1)
        local placed = f.creature.slots[18]
        if variant == "pointer" then f.creature.address = 999
        elseif variant == "id" then f.objects[3] = nil; f.objects[7] = f.creature; f.area.m_lVertSort = {1, 2, 7}
        elseif variant == "missing" then f.objects[3] = nil
        elseif variant == "renamed" then f.creature.m_scriptName = {get=function() return "OTHER" end}
        elseif variant == "duplicate" or variant == "corpse_twin" then
            local twin = f.sprite(202, "FLPRC1", {}); twin.alive = variant == "duplicate"
            f.objects[4] = twin; f.area.m_lVertSortBack = {4}
        elseif variant == "dead" then f.creature.alive = false
        elseif variant == "party" then f.creature.portrait = 0
        elseif variant == "familiar" then f.creature.m_typeAI.m_EnemyAlly = 3
        elseif variant == "item-pointer" then f.creature.slots[18] = f.item("FLPRI1", 991)
        elseif variant == "slot" then f.creature.slots[18] = nil; f.creature.slots[19] = placed
        elseif variant == "removed" then f.creature.slots[18] = nil
        elseif variant == "flags" then placed.m_flags = 0
        elseif variant == "charge" then placed.m_useCount2 = 1
        elseif variant == "wear" then placed.m_wear = 1
        elseif variant == "resref" then placed.cResRef = {get=function() return "OTHER" end}
        else f.creature.slots[19] = f.item("FLPRI1", 992) end
        local x, e = read_only(f, function() return f.adapter:held_binding(request) end)
        fail(x, e, wants[variant] or "HELD_INSTANCE_CHANGED")
    end
end
function cases.held_binding_rejects_conflicting_queue_and_allows_unrelated_messages(N)
    for _, variant in ipairs({"owner", "fixture-creature", "fixture-container", "unrelated", "dialog-wait"}) do
        local f = setup(N); local request = complete_held(f, 1)
        if variant == "owner" then f.queue[1] = f.message(3, "OTHER", 34)
        elseif variant == "fixture-creature" then f.queue[1] = f.message(90, "FLPRI1", 2)
        elseif variant == "fixture-container" then f.queue[1] = f.message(90, "FLPRI2", 12)
        elseif variant == "dialog-wait" then f.queue[1] = f.message(3, "OTHER", 40)
        else f.queue[1] = f.message(90, "OTHER", 40) end
        local x, e = read_only(f, function() return f.adapter:held_binding(request) end)
        if variant == "unrelated" or variant == "dialog-wait" then assert(x) else fail(x, e, "HELD_QUEUE_CONFLICT") end
    end
end
function cases.verify_held_drop_accepts_exact_dead_owner_and_unique_ground_heap_copy_without_writes(N)
    local f, request, candidate = dropped(N)
    -- One live object can appear in several area lists; it counts once.
    f.area.m_lVertSortUnder = {3, 4}; f.area.m_lVertSortFlight = {4}
    f.queue[1] = f.message(90, "OTHER", 40)
    assert(read_only(f, function() return f.adapter:verify_held_drop(request, candidate) end))
    assert(read_only(f, function() return f.adapter:verify_held_drop(request, candidate) end))
    assert(#f.calls == 2 and #f.queue == 1 and #f.ground.m_lstItems == 1)
    unrelated_intact(f)
end
function cases.same_resref_trace_backed_ground_copies_verify_independently(N)
    local f, first, second = two_same_resref_held(N)
    local binding1 = assert(f.adapter:held_binding(first))
    local binding2 = assert(f.adapter:held_binding(second))
    f.creature.alive = false
    f.creature.slots[18], f.creature.slots[19] = nil, nil
    local ground1, ground2 = f.item("FLPRI1", 1801), f.item("FLPRI1", 1802)
    local ground = {address=701, sprite=false, m_containerType=4, m_lstItems={ground1,ground2},
        m_scriptName={get=function() return "GROUND" end}}
    f.objects[4] = ground; f.area.m_lVertSortBack = {4}
    f.api.EEex_Sprite_GetInPortrait = function() return nil end
    local function candidate(binding, item)
        return {owner_pointer=binding.owner_pointer, original_item_pointer=binding.original_item_pointer,
            slot=binding.slot, new_item_pointer=tostring(item.address),
            container_pointer=tostring(ground.address), payload=clone(binding.payload)}
    end
    assert(read_only(f, function() return f.adapter:verify_held_drop(first,candidate(binding1,ground1)) end))
    assert(read_only(f, function() return f.adapter:verify_held_drop(second,candidate(binding2,ground2)) end))
end
function cases.verify_held_drop_rejects_missing_or_changed_candidate_binding(N)
    for _, field in ipairs({"owner_pointer", "original_item_pointer", "slot", "new_item_pointer", "container_pointer"}) do
        for _, variant in ipairs({"missing", "changed"}) do
            local f, request, candidate = dropped(N)
            if variant == "missing" then candidate[field] = nil
            else candidate[field] = field == "slot" and 19 or "9999" end
            local want = (field == "new_item_pointer" or field == "container_pointer") and
                "DROP_GROUND_INSTANCE_UNPROVEN" or "DROP_BINDING_MISMATCH"
            local x, e = read_only(f, function() return f.adapter:verify_held_drop(request, candidate) end)
            fail(x, e, want)
        end
    end
    local f, request = dropped(N)
    local x, e = read_only(f, function() return f.adapter:verify_held_drop(request, nil) end)
    fail(x, e, "DROP_BINDING_MISMATCH")
end
function cases.verify_held_drop_rejects_living_replaced_missing_or_ambiguous_owner(N)
    for _, variant in ipairs({"alive", "pointer", "id", "missing", "renamed", "duplicate", "corpse-twin", "area"}) do
        local f, request, candidate = dropped(N)
        if variant == "alive" then f.creature.alive = true
        elseif variant == "pointer" then f.creature.address = 999
        elseif variant == "id" then f.objects[3] = nil; f.objects[7] = f.creature; f.area.m_lVertSort = {1, 2, 7}
        elseif variant == "missing" then f.objects[3] = nil
        elseif variant == "renamed" then f.creature.m_scriptName = {get=function() return "OTHER" end}
        elseif variant == "area" then f.area.m_resref = {get=function() return "BG0100" end}
        else
            local twin = f.sprite(202, "FLPRC1", {}); twin.alive = variant == "duplicate"
            f.objects[7] = twin; f.area.m_lVertSortFlight = {7}
        end
        local x, e = read_only(f, function() return f.adapter:verify_held_drop(request, candidate) end)
        fail(x, e, "DROP_OWNER_UNPROVEN")
    end
end
function cases.verify_held_drop_rejects_wrong_ground_instance_payload_or_pointer_alias(N)
    for _, variant in ipairs({"pointer", "container-pointer", "container-type", "missing", "flags", "charge", "wear", "resref",
        "same-pointer-twice", "original-still-on-corpse", "original-on-other-sprite"}) do
        local f, request, candidate = dropped(N)
        if variant == "pointer" then f.ground_item.address = 999
        elseif variant == "container-pointer" then f.ground.address = 999
        elseif variant == "container-type" then f.ground.m_containerType = 0
        elseif variant == "missing" then f.ground.m_lstItems = {}
        elseif variant == "flags" then f.ground_item.m_flags = 0
        elseif variant == "charge" then f.ground_item.m_useCount3 = 1
        elseif variant == "wear" then f.ground_item.m_wear = 1
        elseif variant == "resref" then f.ground_item.cResRef = {get=function() return "OTHER" end}
        elseif variant == "original-still-on-corpse" then
            f.creature.slots[18] = f.item("FLPRI1", tonumber(candidate.original_item_pointer))
        elseif variant == "original-on-other-sprite" then
            f.source2.slots[18] = f.item("FLPRI1", tonumber(candidate.original_item_pointer))
        else
            f.objects[5] = {address=702, sprite=false, m_containerType=4, m_lstItems={f.ground_item},
                m_scriptName={get=function() return "OTHERPILE" end}}
            f.area.m_lVertSortUnder = {5}
        end
        local x, e = read_only(f, function() return f.adapter:verify_held_drop(request, candidate) end)
        fail(x, e, "DROP_GROUND_INSTANCE_UNPROVEN")
    end
end
function cases.verify_held_drop_rejects_party_owned_candidate(N)
    for _, variant in ipairs({"picked-up", "party-only"}) do
        local f, request, candidate = dropped(N)
        local party = f.sprite(801, "PARTY", {}); party.portrait = 0
        party.slots[18] = f.ground_item
        f.ground.m_lstItems = {}
        f.party[0] = party
        if variant ~= "party-only" then f.objects[5] = party; f.area.m_lVertSortUnder = {5} end
        local x, e = read_only(f, function() return f.adapter:verify_held_drop(request, candidate) end)
        fail(x, e, "DROP_GROUND_INSTANCE_UNPROVEN")
    end
    local f, request, candidate = dropped(N); f.api.EEex_Sprite_GetInPortrait = nil
    local x, e = read_only(f, function() return f.adapter:verify_held_drop(request, candidate) end)
    fail(x, e, "PARTY_SCAN_UNAVAILABLE")
end
function cases.verify_held_drop_allows_unrelated_same_resref_copies_with_distinct_pointers(N)
    local f, request, candidate = dropped(N)
    f.creature.slots[18] = f.item("FLPRI1", 888)
    f.source2.slots[18] = f.item("FLPRI1", 889)
    f.objects[5] = {address=702, sprite=false, m_containerType=4,
        m_lstItems={f.item("FLPRI1", 890)}, m_scriptName={get=function() return "OTHERPILE" end}}
    f.area.m_lVertSortUnder = {5}
    local party = f.sprite(801, "PARTY", {[18]=f.item("FLPRI1", 891)})
    party.portrait = 0; f.party[0] = party
    assert(read_only(f, function() return f.adapter:verify_held_drop(request,candidate) end))
end
function cases.verify_held_drop_rejects_container_or_fixture_queue_conflicts(N)
    for _, variant in ipairs({"container", "fixture-creature", "fixture-container"}) do
        local f, request, candidate = dropped(N)
        if variant == "container" then f.queue[1] = f.message(4, "OTHER", 34)
        elseif variant == "fixture-creature" then f.queue[1] = f.message(90, "FLPRI1", 2)
        else f.queue[1] = f.message(90, "FLPRI2", 12) end
        local x, e = read_only(f, function() return f.adapter:verify_held_drop(request, candidate) end)
        fail(x, e, "DROP_QUEUE_CONFLICT")
    end
end
function cases.guarded_creature_observation_uses_inventory_without_destroying_items(N)
    local f=setup(N)
    local id=f.source_ids[1]
    f.fixture.sources[id].kind='guarded-creature'
    f.fixture.source_gate={schema='flpr-unvisited-fixture-gate-v1',source_id=id}
    f.area.address=99
    f.adapter=N.new(f.api,f.fixture)
    local result,reason=f.adapter:inspect_source(id)
    fail(result,reason,'SOURCE_GUARD_REQUIRED')
    assert(f.adapter:guarded_source_remaining(id,'99','101').remaining==1)
    f.source1.slots[21]=nil
    -- The read-only guard observer also works after this original owner died.
    f.source1.alive=false
    assert(f.adapter:guarded_source_remaining(id,'99','101').remaining==0)
    assert(#f.calls==0 and f.source2.slots[21].cResRef:get()=='FLPRI2')
end
function cases.own_reward_in_a_guarded_source_creature_is_not_its_original(N)
    local f=setup(N)
    -- FLPRC1 carried a guarded original of the reward's name; the gate removed
    -- it as the creature's pristine area loaded (the kuo-toa prince's BRAC16).
    local guarded_id='cre:flprc1/item/3'
    f.fixture.sources[guarded_id]={kind='guarded-creature',area='BG2600',script_name='FLPRC1',
        identity=string.rep('9',64),payload={resref='flpri1',charges={0,0,0},expiration=0,flags=1}}
    f.fixture.source_gate={schema='flpr-unvisited-fixture-gate-v1',source_id=guarded_id}
    f.area.address=99
    f.adapter=N.new(f.api,f.fixture)
    assert(f.adapter:guarded_source_remaining(guarded_id,'99','201').remaining==0)
    complete_held(f,1)
    assert(f.creature.slots[18].cResRef:get()=='FLPRI1')
    -- The reward CIR gave it is not the original.
    assert(f.adapter:guarded_source_remaining(guarded_id,'99','201').remaining==0)
    -- Any other item of that name counts, as the original would.
    f.creature.slots[25]=f.item('FLPRI1',1999)
    assert(f.adapter:guarded_source_remaining(guarded_id,'99','201').remaining==1)
    assert(N.new(f.api,f.fixture):guarded_source_remaining(guarded_id,'99','201').remaining==2)
    f.adapter:destroy()
    assert(f.adapter:guarded_source_remaining(guarded_id,'99','201').remaining==2)
    unrelated_intact(f)
end
local function group_world(N)
    local f=setup(N)
    f.member2=f.sprite(202,'FLPRC2',{})
    f.objects[4],f.ids[f.member2]=f.member2,4
    f.area.m_lVertSort[#f.area.m_lVertSort+1]=4
    f.fixture.targets[f.target_id]={kind='group',area='BG2600',identity=string.rep('c',64),
        members={{resref='flprc1',script_name='FLPRC1'},
                 {resref='flprc2',script_name='FLPRC2'}},
        eligible={flpri1={1,2},flpri2={2}}}
    f.adapter=N.new(f.api,f.fixture,17)
    return f
end
function cases.group_target_replays_seeded_member_and_delivers_to_that_member(N)
    local f=group_world(N)
    local ready=assert(f.adapter:inspect_target(f.target_id,f.source_ids[1],1))
    assert(ready.accepts==1)
    local selected=f.adapter.reservations[string.rep('c',64)].owner
    assert(selected.id==3 or selected.id==4)
    local restored=N.new(f.api,f.fixture,17)
    assert(restored:inspect_target(f.target_id,f.source_ids[1],1))
    assert(restored.reservations[string.rep('c',64)].owner.id==selected.id)
    f.boundary()
    local command=f.command(1)
    assert(f.adapter:execute(command))
    assert(#f.queue==1 and f.queue[1].m_targetId==selected.id)
    f.update(); f.boundary()
    local receipt,reason=f.adapter:observe(command); fail(receipt,reason,'DELIVERY_SETTLING')
    f.boundary(); receipt=assert(f.adapter:observe(command))
    assert(receipt.held==1 and receipt.lootable==0 and receipt.holder=='creature')
    assert(f.objects[selected.id].slots[18].cResRef:get()=='FLPRI1')
    assert(#f.calls==1)
end
function cases.group_target_honors_eligible_member_and_queue_exclusion(N)
    local f=group_world(N)
    assert(f.adapter:inspect_target(f.target_id,f.source_ids[2],2))
    assert(f.adapter.reservations[string.rep('c',64)].owner.id==4)
    f.queue[1]=f.message(4,'FLPRI2',2)
    local ready,reason=f.adapter:inspect_target(f.target_id,f.source_ids[2],2)
    fail(ready,reason,'TARGET_QUEUE_BUSY')
    assert(#f.calls==0)
    f.fixture.targets[f.target_id].eligible.flpri2={3}
    local ok,error=pcall(function() N.new(f.api,f.fixture,17) end)
    assert(not ok and tostring(error):find('NATIVE_GROUP_CANDIDATES_INVALID',1,true))
end
function cases.quest_target_requires_event_then_delivers_to_party_portrait_zero(N)
    local f=setup(N)
    f.creature.portrait=0; f.creature.m_id=3
    local create=f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly
    f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly=function(action,owner)
        create(action,owner)
        -- Party delivery also emits ordinary gains-item feedback. It is not
        -- an additional item instance and has no CMessageAddItem payload.
        local feedback=f.message(0,'OTHER',16,0)
        feedback.m_item=nil
        feedback.virtual_GetCommType=function() return 4 end
        f.queue[#f.queue+1]=feedback
    end
    local claimed=0
    f.api.EEex_GameState_GetGlobalInt=function(name)
        assert(name=='FLPR_EPOOL'); return claimed
    end
    f.api.EEex_Sprite_GetInPortrait=function(index)
        assert(index==0); return f.creature
    end
    f.fixture.targets[f.target_id]={kind='quest',area='BG2600',
        identity=string.rep('c',64),event_global='FLPR_EPOOL'}
    f.adapter=N.new(f.api,f.fixture)
    local ready,reason=f.adapter:inspect_target(f.target_id,f.source_ids[1],1)
    fail(ready,reason,'QUEST_NOT_CLAIMED')
    assert(#f.calls==0 and #f.queue==0)
    claimed=1
    assert(f.adapter:inspect_target(f.target_id,f.source_ids[1],1))
    f.boundary()
    local command=f.command(1)
    assert(f.adapter:execute(command))
    assert(#f.queue==2 and f.queue[1].m_targetId==3 and f.queue[1].m_sourceId==3)
    assert(f.queue[1].virtual_GetMsgSubType()==2 and f.queue[1].virtual_GetCommType()==1)
    assert(f.queue[2].m_targetId==0 and f.queue[2].m_sourceId==0)
    assert(f.queue[2].virtual_GetMsgSubType()==16 and f.queue[2].virtual_GetCommType()==4)
    assert(f.creature.slots[18]==nil and f.creature.slots[19]==nil)
    f.update(); f.boundary()
    local receipt; receipt,reason=f.adapter:observe(command)
    fail(receipt,reason,'DELIVERY_SETTLING')
    f.boundary(); receipt=assert(f.adapter:observe(command))
    assert(receipt.created==1 and receipt.lootable==1 and receipt.held==nil)
    assert(receipt.holder=='party' and receipt.slot==18)
    equal_tree(receipt.payload,command.payload)
    assert(f.creature.slots[18].cResRef:get()=='FLPRI1' and #f.calls==1)
    local copies=0
    for _,item in pairs(f.creature.slots) do
        if item.cResRef:get()=='FLPRI1' then copies=copies+1 end
    end
    assert(copies==1 and #f.queue==0)
    unrelated_intact(f)
    local result; result,reason=f.adapter:execute(command)
    fail(result,reason,'ALREADY_ATTEMPTED')
    assert(#f.calls==1 and #f.queue==0)
end
-- CGameSprite::ProcessAI queues a CMessageSetDialogWait (67/40) from every party
-- member to itself on each AI update, so one is nearly always queued for the
-- recipient (2.7.3.0 lab). Counting it as a conflict held every party reward back.
function cases.party_reward_arrives_while_members_queue_dialog_waits(N)
    local f = setup(N)
    f.creature.portrait = 0; f.creature.m_id = 3
    local companion = f.sprite(204, "IMOEN2", {}); companion.portrait = 1
    f.objects[4] = companion; f.ids[companion] = 4; f.area.m_lVertSort = {1, 2, 3, 4}
    local function waits()
        f.queue[#f.queue + 1] = f.message(3, "OTHER", 40)
        f.queue[#f.queue + 1] = f.message(4, "OTHER", 40)
    end
    local update = f.update
    f.update = function() update(); waits() end
    f.api.EEex_GameState_GetGlobalInt = function(name) assert(name == "FLPR_EPOOL"); return 1 end
    f.api.EEex_Sprite_GetInPortrait = function(index) assert(index == 0); return f.creature end
    f.fixture.targets[f.target_id] = {kind="quest", area="BG2600", identity=string.rep("c", 64),
        event_global="FLPR_EPOOL"}
    f.adapter = N.new(f.api, f.fixture)
    waits()
    assert(f.adapter:inspect_target(f.target_id, f.source_ids[1], 1))
    f.boundary()
    local command = f.command(1)
    assert(f.adapter:execute(command))
    assert(#f.queue == 3 and f.queue[3].virtual_GetMsgSubType() == 2 and f.queue[3].m_targetId == 3)
    f.update(); f.boundary()
    local receipt, reason = f.adapter:observe(command); fail(receipt, reason, "DELIVERY_SETTLING")
    f.boundary(); receipt = assert(f.adapter:observe(command))
    assert(receipt.holder == "party" and receipt.slot == 18 and #f.calls == 1)
    assert(f.creature.slots[18].cResRef:get() == "FLPRI1" and next(companion.slots) == nil)
    unrelated_intact(f)
end

-- The ground at a dead target's corpse, as the 2.7.3.0 lab showed it
-- (docs/native-corpse-ground-evidence.md): CreateItem on a sprite outside the
-- party fills the highest empty slot from 34 down to 15 at once, on a party
-- member the first empty backpack slot (docs/native-creature-transport-evidence.md).
-- DropItem takes the first item of that name out and queues one container add
-- (subtype 12, slot 32767, target and source the pile) into a type-4 pile at
-- the sprite: a listed pile there, or a new one that joins the area as its
-- message runs.
local function ground_world(N)
    local f = setup(N)
    f.creature.alive = false
    f.creature.m_pos = {x=500, y=600}
    f.next_object = 50
    local function copy_item(value, address)
        local result = f.item(value.cResRef:get(), address, value.m_flags)
        result.m_useCount1, result.m_useCount2, result.m_useCount3 =
            value.m_useCount1, value.m_useCount2, value.m_useCount3
        return result
    end
    f.copy_item = copy_item
    local function listed(id)
        for _, field in ipairs({"m_lVertSort", "m_lVertSortBack", "m_lVertSortFlight", "m_lVertSortUnder"}) do
            for _, entry in ipairs(f.area[field]) do if entry == id then return true end end
        end
    end
    function f.pile_at(x, y, items)
        f.next_object = f.next_object + 1
        local pile = {address=7000 + f.next_object, sprite=false, m_containerType=4, m_pos={x=x, y=y},
            m_lstItems=items or {}, m_scriptName={get=function() return "" end}}
        f.objects[f.next_object] = pile
        return f.next_object, pile
    end
    f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly = function(action, o)
        f.calls[#f.calls + 1] = action
        local ref, c1, c2, c3 = action:match('^CreateItem%("([^"]+)",(%d+),(%d+),(%d+)%)$')
        if ref then
            if f.create_variant == "none" then return end
            local first, last, step = 34, 15, -1
            if o.portrait ~= -1 then first, last, step = 18, 33, 1 end
            for slot = first, last, step do
                if not o.slots[slot] then
                    f.next_address = f.next_address + 1
                    local made = f.item(ref:upper(), f.next_address)
                    made.m_useCount1, made.m_useCount2, made.m_useCount3 = tonumber(c1), tonumber(c2), tonumber(c3)
                    o.slots[slot] = made
                    if f.create_variant == "queues" then f.queue[#f.queue + 1] = f.message(77, "OTHER", 12, 77) end
                    return
                end
            end
            error("fake corpse has no free slot")
        end
        ref = action:match('^DropItem%("([^"]+)",%[%-1%.%-1%]%)$')
        assert(ref, "unexpected action " .. action)
        if f.drop_variant == "kept" then return end
        local taken
        for slot = 0, 38 do
            local value = o.slots[slot]
            if value and value.cResRef:get():lower() == (f.drop_variant == "other" and "other" or ref) then
                taken = value
                if f.drop_variant ~= "stays" then o.slots[slot] = nil end
                break
            end
        end
        if f.drop_variant == "both" then o.slots[21] = nil end
        assert(taken, "fake DropItem found nothing")
        local id
        for candidate, object in pairs(f.objects) do
            if not object.sprite and object.m_containerType == 4 and listed(candidate) and
                math.abs(object.m_pos.x - o.m_pos.x) <= 16 and math.abs(object.m_pos.y - o.m_pos.y) <= 16 then
                id = candidate
            end
        end
        id = id or f.pile_at(o.m_pos.x, o.m_pos.y - 5)
        local message = f.message(id, ref:upper(), 12, id)
        message.m_item = copy_item(taken, f.next_address + 5000)
        if f.drop_variant == "self" then message.m_targetId, message.m_sourceId = f.ids[o], f.ids[o]
        elseif f.drop_variant == "slot" then message.m_slotNum = 0
        elseif f.drop_variant == "payload" then message.m_item.m_useCount1 = 7 end
        f.queue[#f.queue + 1] = message
        if f.drop_variant == "extra" then f.queue[#f.queue + 1] = f.message(id, "OTHER", 12, id) end
    end
    -- One handler update: a container add appends a heap copy to its pile,
    -- which joins the area's lists if it is new.
    function f.update()
        while #f.queue > 0 do
            local message = table.remove(f.queue, 1)
            local target = f.objects[message.m_targetId]
            if message.virtual_GetMsgSubType() == 12 and target and not target.sprite then
                f.next_address = f.next_address + 1
                target.m_lstItems[#target.m_lstItems + 1] = copy_item(message.m_item, f.next_address)
                if not listed(message.m_targetId) then
                    f.area.m_lVertSortBack[#f.area.m_lVertSortBack + 1] = message.m_targetId
                end
            end
        end
    end
    return f
end
local function ground_start(f, index)
    local witness = assert(f.adapter:inspect_target(f.target_id, f.source_ids[index], index))
    assert(witness.ground == true and witness.accepts == 1 and witness.target_id == f.target_id)
    f.boundary()
    local command = f.command(index)
    assert(f.adapter:execute(command))
    return command
end
local function corpse_as_before(f)
    unrelated_intact(f)
    local count = 0
    for _ in pairs(f.creature.slots) do count = count + 1 end
    assert(count == 2, "the corpse kept a reward")
end
function cases.dead_target_reward_goes_to_a_new_pile_at_its_corpse(N)
    local f = ground_world(N); local a = f.adapter
    local command = ground_start(f, 1)
    assert(#f.calls == 2 and f.calls[1] == 'CreateItem("flpri1",0,0,0)' and f.calls[2] == 'DropItem("flpri1",[-1.-1])')
    corpse_as_before(f)
    assert(#f.queue == 1 and f.queue[1].virtual_GetMsgSubType() == 12)
    local pile_id = f.queue[1].m_targetId
    local pile = f.objects[pile_id]
    assert(pile and #f.area.m_lVertSortBack == 0 and a:diagnostics().pending.ground == true)
    f.boundary(); local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    f.update(); f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    assert(a:diagnostics().pending.pile_pointer == tostring(pile.address))
    f.boundary(); local receipt = assert(a:observe(command))
    assert(receipt.created == 1 and receipt.nonce_bound_instance == 1 and receipt.lootable == 1 and
        receipt.held == nil and receipt.holder == "ground" and receipt.payload.flags == 1 and receipt.notice == nil)
    assert(#pile.m_lstItems == 1 and pile.m_lstItems[1].cResRef:get() == "FLPRI1" and
        pile.m_lstItems[1].m_flags == 1 and f.area.m_lVertSortBack[1] == pile_id)
    corpse_as_before(f); assert(#f.calls == 2 and a.pending == nil)
    x, e = a:execute(command); fail(x, e, "ALREADY_ATTEMPTED")
end
function cases.ground_reuses_a_listed_pile_and_keeps_its_items(N)
    local f = ground_world(N); local a = f.adapter
    local loot = f.item("SW1H01", 880)
    local pile_id, pile = f.pile_at(503, 597, {loot})
    f.area.m_lVertSortBack = {pile_id}
    local command = ground_start(f, 1)
    assert(f.queue[1].m_targetId == pile_id)
    f.boundary(); local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    f.update(); f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    f.boundary(); local receipt = assert(a:observe(command))
    assert(receipt.holder == "ground" and #pile.m_lstItems == 2 and pile.m_lstItems[1] == loot and
        loot.address == 880 and pile.m_lstItems[2].cResRef:get() == "FLPRI1")
    corpse_as_before(f)
end
function cases.two_rewards_of_one_dead_target_each_bind_their_own_instance(N)
    local f = ground_world(N); local a = f.adapter
    f.source2.slots[21] = f.item("FLPRI1", 302)
    f.fixture.sources[f.source_ids[2]].payload.resref = "flpri1"
    f.adapter = N.new(f.api, f.fixture); a = f.adapter
    local first = ground_start(f, 1); f.update(); f.boundary(); a:observe(first); f.boundary()
    assert(a:observe(first))
    local second = ground_start(f, 2); f.update(); f.boundary()
    local x, e = a:observe(second); fail(x, e, "DELIVERY_SETTLING")
    f.boundary(); assert(a:observe(second))
    local pile = f.objects[f.area.m_lVertSortBack[1]]
    assert(#f.area.m_lVertSortBack == 1 and #pile.m_lstItems == 2 and
        pile.m_lstItems[1].address ~= pile.m_lstItems[2].address)
    for _, value in ipairs(pile.m_lstItems) do assert(value.cResRef:get() == "FLPRI1") end
    corpse_as_before(f); assert(#f.calls == 4)
end
function cases.ground_keeps_the_sources_identified_and_unstealable_flags(N)
    for _, selected in ipairs({0, 2}) do
        local f = ground_world(N)
        f.fixture.sources[f.source_ids[1]].payload.flags = selected
        f.adapter = N.new(f.api, f.fixture)
        local command = ground_start(f, 1)
        assert(f.queue[1].m_item.m_flags == selected)
        -- The add copies the item and identifies it again, like a container add.
        f.update(); local pile = f.objects[f.area.m_lVertSortBack[1]]
        pile.m_lstItems[1].m_flags = selected + 1
        f.boundary(); local x, e = f.adapter:observe(command); fail(x, e, "DELIVERY_SETTLING")
        assert(pile.m_lstItems[1].m_flags == selected)
        f.boundary(); local receipt = assert(f.adapter:observe(command))
        assert(receipt.payload.flags == selected and receipt.holder == "ground")
    end
end
function cases.ground_waits_for_a_party_member_familiar_copy_full_corpse_or_busy_queue(N)
    local wants = {party="TARGET_NOT_ALIVE", familiar="TARGET_NOT_ALIVE", copy="CORPSE_ITEM_AMBIGUOUS",
        full="TARGET_INVENTORY_FULL", busy="TARGET_QUEUE_BUSY", reward="TARGET_QUEUE_BUSY",
        pile="TARGET_QUEUE_BUSY", far="TARGET_QUEUE_BUSY",
        unnamed="TARGET_NOT_ALIVE", area="AREA_UNAVAILABLE", gone="OWNER_MISSING"}
    for _, variant in ipairs({"party", "familiar", "copy", "full", "busy", "reward", "pile", "far", "unnamed",
        "area", "gone"}) do
        local f = ground_world(N)
        if variant == "party" then f.creature.portrait = 2
        elseif variant == "familiar" then f.creature.m_typeAI.m_EnemyAlly = 3
        elseif variant == "copy" then f.creature.slots[22] = f.item("FLPRI1", 820)
        elseif variant == "full" then
            for slot = 15, 34 do if not f.creature.slots[slot] then f.creature.slots[slot] = f.item("FILL", 400 + slot) end end
        elseif variant == "busy" then f.queue[1] = f.message(3, "OTHER", 34)
        elseif variant == "reward" then f.queue[1] = f.message(77, "FLPRI2", 12)
        elseif variant == "pile" or variant == "far" then
            -- A drop or death drop on its way into the pile the drop would reuse, or anywhere.
            local pile_id = variant == "pile" and f.pile_at(500, 595, {}) or f.pile_at(2000, 2000, {})
            f.area.m_lVertSortBack = {pile_id}; f.queue[1] = f.message(pile_id, "OTHER", 12, pile_id)
        elseif variant == "area" then f.area.m_resref = {get=function() return "BG0100" end}
        elseif variant == "gone" then f.area.m_lVertSort = {1, 2} end
        local x, e
        if variant == "unnamed" then x, e = f.adapter:inspect_target(f.target_id)
        else x, e = f.adapter:inspect_target(f.target_id, f.source_ids[1], 1) end
        fail(x, e, wants[variant])
        f.boundary(); x, e = f.adapter:execute(f.command(1)); fail(x, e, "RESERVATION_MISMATCH")
        assert(#f.calls == 0)
    end
    -- Slots outside 15..34 are not where CreateItem puts it.
    local f = ground_world(N)
    for slot = 15, 34 do if not f.creature.slots[slot] then f.creature.slots[slot] = f.item("FILL", 400 + slot) end end
    f.creature.slots[34] = nil; f.creature.slots[35] = f.item("FILL", 435)
    assert(f.adapter:inspect_target(f.target_id, f.source_ids[1], 1))
end
function cases.ground_execute_requires_one_created_instance_and_one_drop_message(N)
    for _, variant in ipairs({"none", "queues", "kept", "stays", "both", "other", "extra", "self", "slot",
        "payload"}) do
        local f = ground_world(N); local a = f.adapter
        if variant == "none" or variant == "queues" then f.create_variant = variant
        else f.drop_variant = variant end
        assert(a:inspect_target(f.target_id, f.source_ids[1], 1)); f.boundary()
        local command = f.command(1)
        local x, e = a:execute(command); fail(x, e, "CREATED_INSTANCE_UNPROVEN")
        local calls = (variant == "none" or variant == "queues") and 1 or 2
        assert(#f.calls == calls)
        f.boundary(); x, e = a:observe(command); fail(x, e, "CREATED_INSTANCE_UNPROVEN")
        x, e = a:execute(command); fail(x, e, "MUTATION_PENDING")
        assert(#f.calls == calls)
    end
end
function cases.ground_execute_rejects_a_changed_corpse_or_queue(N)
    local wants = {replaced="OWNER_CHANGED", alive="OWNER_CHANGED", items="INVENTORY_CHANGED",
        queued="DELIVERY_QUEUE_NOT_EXCLUSIVE", pile="DELIVERY_QUEUE_NOT_EXCLUSIVE"}
    for _, variant in ipairs({"replaced", "alive", "items", "queued", "pile"}) do
        local f = ground_world(N); local a = f.adapter
        assert(a:inspect_target(f.target_id, f.source_ids[1], 1)); f.boundary()
        if variant == "replaced" then f.creature.address = 999
        elseif variant == "alive" then f.creature.alive = true
        elseif variant == "items" then f.creature.slots[23] = f.item("OTHER", 556)
        elseif variant == "pile" then
            local pile_id = f.pile_at(500, 595, {})
            f.area.m_lVertSortBack = {pile_id}; f.queue[1] = f.message(pile_id, "OTHER", 12, pile_id)
        else f.queue[1] = f.message(3, "OTHER", 34) end
        local x, e = a:execute(f.command(1)); fail(x, e, wants[variant])
        assert(#f.calls == 0)
    end
end
function cases.ground_observation_rejects_conflicts_lost_adds_and_changed_piles(N)
    local wants = {changed="DELIVERY_QUEUE_CONFLICT", lost="GROUND_PILE_MISSING",
        early="DELIVERY_EARLY_ITEM", removed="CREATED_INSTANCE_CHANGED",
        replaced="OWNER_CHANGED", unrun="DELIVERY_QUEUE_CONFLICT", payload="DELIVERY_PAYLOAD_CHANGED"}
    for _, variant in ipairs({"changed", "lost", "early", "removed", "replaced", "unrun", "payload"}) do
        local f = ground_world(N); local a = f.adapter
        local pile_id, pile = f.pile_at(500, 600, {})
        if variant == "early" or variant == "replaced" then f.area.m_lVertSortBack = {pile_id} end
        local command = ground_start(f, 1)
        local id = f.queue[1].m_targetId
        if variant == "changed" then f.queue[1].m_item.m_useCount1 = 4
        elseif variant == "lost" then table.remove(f.queue, 1)
        elseif variant == "early" then pile.m_lstItems[1] = f.item("FLPRI1", 1991)
        elseif variant == "removed" then
            f.update(); f.boundary(); local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
            f.objects[id].m_lstItems = {}
        elseif variant == "replaced" then pile.address = 9999
        elseif variant == "unrun" then
            -- Its add gone without running, another message at its address.
            local reused = f.message(55, "OTHER", 40); reused.address = f.queue[1].address; f.queue[1] = reused
        elseif variant == "payload" then f.update(); f.objects[id].m_lstItems[1].m_useCount2 = 3 end
        f.boundary(); local x, e = a:observe(command); fail(x, e, wants[variant])
    end
end
-- The handler runs queued adds in order: an add queued after the reward's
-- lands after it, and items taken out of the pile meanwhile are not it. Once
-- the add has run, another message may have its address.
function cases.ground_reward_is_the_first_new_item_of_its_pile(N)
    for _, variant in ipairs({"later-add", "later-item", "taken", "reused"}) do
        local f = ground_world(N); local a = f.adapter
        local pile_id, pile = f.pile_at(503, 597, {f.item("SW1H01", 880)})
        f.area.m_lVertSortBack = {pile_id}
        local command = ground_start(f, 1)
        local address = f.queue[1].address
        if variant == "later-add" then f.queue[2] = f.message(pile_id, "FLPRI1", 12, pile_id)
        elseif variant == "taken" then table.remove(pile.m_lstItems, 1) end
        f.boundary(); local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
        f.update()
        if variant == "later-item" then pile.m_lstItems[#pile.m_lstItems + 1] = f.item("FLPRI1", 1990)
        elseif variant == "reused" then
            local reused = f.message(pile_id, "OTHER", 12, pile_id); reused.address = address; f.queue[1] = reused
        end
        f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
        local reward = pile.m_lstItems[variant == "taken" and 1 or 2]
        assert(reward.cResRef:get() == "FLPRI1" and a:diagnostics().pending.created_pointer == tostring(reward.address))
        assert(#pile.m_lstItems == (({taken=1, reused=2})[variant] or 3), variant)
        -- The add at the reward's former address lands after it.
        if variant == "reused" then f.update(); assert(#pile.m_lstItems == 3 and pile.m_lstItems[2] == reward) end
        f.boundary(); local receipt = assert(a:observe(command))
        assert(receipt.holder == "ground" and receipt.lootable == 1 and #f.calls == 2, variant)
    end
end
function cases.stuck_ground_message_times_out_and_never_recovers(N)
    local f = ground_world(N); local a = f.adapter
    local command = ground_start(f, 1)
    assert(a:on_observation_boundary(f.tick + N.MAX_SETTLE_TICKS))
    local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING_TIMEOUT")
    f.update(); assert(a:on_observation_boundary(f.tick + N.MAX_SETTLE_TICKS + 1))
    x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING_TIMEOUT")
    assert(#f.calls == 2)
end

-- A creature target that died and left no body (Adapter:_inspect_party_ground):
-- no sprite of its script name in its area any more, its death counted in
-- SPRITE_IS_DEAD<script name>, and the party there. Kara (portrait 0) and
-- Minsc (portrait 1) stand in the area with empty backpacks.
local function vanish_world(N)
    local f = ground_world(N)
    f.area.m_lVertSort = {1, 2}
    f.globals.SPRITE_IS_DEADFLPRC1 = 1
    f.party = {}
    f.api.EEex_Sprite_GetInPortrait = function(index) return f.party[index] end
    function f.member(portrait, id, name)
        local o = f.sprite(800 + id, name:upper(), {})
        o.portrait, o.m_id, o.name, o.m_pos = portrait, id, name, {x=300 + 40 * portrait, y=400}
        f.objects[id], f.ids[o], f.party[portrait] = o, id, o
        f.area.m_lVertSort[#f.area.m_lVertSort + 1] = id
        return o
    end
    f.kara, f.minsc = f.member(0, 8, "Kara"), f.member(1, 9, "Minsc")
    return f
end
local function vanish_start(f, index)
    local witness = assert(f.adapter:inspect_target(f.target_id, f.source_ids[index], index))
    assert(witness.ground == true and witness.accepts == 1 and witness.target_id == f.target_id)
    f.boundary()
    local command = f.command(index)
    assert(f.adapter:execute(command))
    return command
end
local function vanish_receipt(f, command)
    f.update(); f.boundary()
    local x, e = f.adapter:observe(command); fail(x, e, "DELIVERY_SETTLING")
    f.boundary(); return assert(f.adapter:observe(command))
end
function cases.vanished_target_reward_falls_at_the_first_party_members_feet(N)
    local f = vanish_world(N); local a = f.adapter
    local command = vanish_start(f, 1)
    assert(#f.calls == 2 and f.calls[1] == 'CreateItem("flpri1",0,0,0)' and f.calls[2] == 'DropItem("flpri1",[-1.-1])')
    -- Kara has dropped it at once; Minsc is untouched.
    assert(next(f.kara.slots) == nil and next(f.minsc.slots) == nil)
    assert(#f.queue == 1 and f.queue[1].virtual_GetMsgSubType() == 12)
    local pile_id = f.queue[1].m_targetId
    local pile = f.objects[pile_id]
    assert(pile.m_pos.x == f.kara.m_pos.x and a:diagnostics().pending.notice.member == "Kara")
    f.boundary(); local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    local receipt = vanish_receipt(f, command)
    assert(receipt.created == 1 and receipt.nonce_bound_instance == 1 and receipt.lootable == 1 and
        receipt.held == nil and receipt.holder == "ground")
    assert(#pile.m_lstItems == 1 and pile.m_lstItems[1].cResRef:get() == "FLPRI1" and
        pile.m_lstItems[1].m_flags == 1 and f.area.m_lVertSortBack[1] == pile_id)
    -- The player is told the item's name (identified, flags 1) and Kara's.
    assert(receipt.notice.item == 7101 and receipt.notice.resref == "flpri1" and receipt.notice.member == "Kara")
    unrelated_intact(f); assert(#f.calls == 2 and a.pending == nil)
    x, e = a:execute(command); fail(x, e, "ALREADY_ATTEMPTED")
end
function cases.vanish_notice_names_the_reward_as_the_player_sees_it(N)
    for _, variant in ipairs({"unidentified", "unnamed", "nameless"}) do
        local f = vanish_world(N)
        if variant == "unidentified" then f.fixture.sources[f.source_ids[1]].payload.flags = 0
        elseif variant == "unnamed" then f.header.identifiedName, f.header.genericName = 4294967295, 0
        else f.kara.name = nil end
        f.adapter = N.new(f.api, f.fixture)
        local notice = vanish_receipt(f, vanish_start(f, 1)).notice
        if variant == "unidentified" then assert(notice.item == 7100 and notice.member == "Kara")
        elseif variant == "unnamed" then assert(notice.item == nil and notice.resref == "flpri1")
        else assert(notice.member == nil and notice.item == 7101) end
    end
end
function cases.vanish_drop_takes_the_first_member_who_can_drop_it(N)
    for _, variant in ipairs({"dead", "elsewhere", "copy", "equipped", "full", "busy"}) do
        local f = vanish_world(N)
        if variant == "dead" then f.kara.alive = false
        elseif variant == "elsewhere" then f.area.m_lVertSort = {1, 2, 9}
        elseif variant == "copy" then f.kara.slots[25] = f.item("FLPRI1", 830)
        elseif variant == "equipped" then f.kara.slots[1] = f.item("FLPRI1", 831)
        elseif variant == "full" then for slot = 18, 33 do f.kara.slots[slot] = f.item("FILL", 600 + slot) end
        else f.queue[1] = f.message(8, "OTHER", 34) end
        local kept = clone(f.kara.slots)
        local command = vanish_start(f, 1)
        assert(vanish_receipt(f, command).notice.member == "Minsc" and next(f.minsc.slots) == nil, variant)
        equal_tree(f.kara.slots, kept)
    end
end
function cases.vanish_drop_waits_while_no_member_can_or_the_target_is_not_gone(N)
    local wants = {full="TARGET_INVENTORY_FULL", copy="PARTY_ITEM_AMBIGUOUS", busy="TARGET_QUEUE_BUSY",
        pile="TARGET_QUEUE_BUSY", nobody="OWNER_MISSING", alive="OWNER_MISSING", sourceless="OWNER_MISSING",
        area="AREA_UNAVAILABLE", unsupported="ITEM_NOT_VISIBLE_DROPPABLE"}
    for _, variant in ipairs({"full", "copy", "busy", "pile", "nobody", "alive", "sourceless", "area", "unsupported"}) do
        local f = vanish_world(N)
        for _, member in ipairs({f.kara, f.minsc}) do
            if variant == "full" then
                for slot = 18, 33 do member.slots[slot] = f.item("FILL", 100 * member.m_id + slot) end
            elseif variant == "copy" then member.slots[30] = f.item("FLPRI1", 830 + member.m_id)
            elseif variant == "busy" then f.queue[#f.queue + 1] = f.message(member.m_id, "OTHER", 34) end
        end
        if variant == "pile" then
            -- A drop or death drop on its way into a pile, anywhere.
            local pile_id = f.pile_at(2000, 2000, {})
            f.area.m_lVertSortBack = {pile_id}; f.queue[1] = f.message(pile_id, "OTHER", 12, pile_id)
        elseif variant == "nobody" then f.area.m_lVertSort = {1, 2}
        -- Not counted dead: not there yet, or gone alive (DestroySelf, EscapeArea).
        elseif variant == "alive" then f.globals.SPRITE_IS_DEADFLPRC1 = nil
        elseif variant == "area" then f.area.m_resref = {get=function() return "BG0100" end}
        elseif variant == "unsupported" then f.header.itemFlags = 4 end
        local x, e
        if variant == "sourceless" then x, e = f.adapter:inspect_target(f.target_id)
        else x, e = f.adapter:inspect_target(f.target_id, f.source_ids[1], 1) end
        fail(x, e, wants[variant])
        f.boundary(); x, e = f.adapter:execute(f.command(1)); fail(x, e, "RESERVATION_MISMATCH")
        assert(#f.calls == 0, variant)
    end
    -- It waits with the reason of the first member in portrait order.
    local f = vanish_world(N)
    for slot = 18, 33 do f.kara.slots[slot] = f.item("FILL", 600 + slot) end
    f.minsc.slots[30] = f.item("FLPRI1", 839)
    local x, e = f.adapter:inspect_target(f.target_id, f.source_ids[1], 1); fail(x, e, "TARGET_INVENTORY_FULL")
end
function cases.vanish_reads_the_death_variable_of_the_targets_script_name(N)
    for _, name in ipairs({"FLPRC1", "LONGVAMPIRENAME12345"}) do
        local f = vanish_world(N)
        f.fixture.targets[f.target_id].script_name = name
        f.adapter = N.new(f.api, f.fixture)
        local read, seen = f.api.EEex_GameState_GetGlobalInt, {}
        f.api.EEex_GameState_GetGlobalInt = function(variable) seen[#seen + 1] = variable; return read(variable) end
        -- The variable holds the first 32 characters, as the engine stores it.
        local variable = ("SPRITE_IS_DEAD" .. name:lower()):sub(1, 32)
        f.globals.SPRITE_IS_DEADFLPRC1 = nil
        local x, e = f.adapter:inspect_target(f.target_id, f.source_ids[1], 1); fail(x, e, "OWNER_MISSING")
        assert(seen[#seen] == variable and #variable == math.min(32, 14 + #name), seen[#seen])
        f.globals[variable:upper()] = 2
        assert(f.adapter:inspect_target(f.target_id, f.source_ids[1], 1).ground == true)
    end
end
function cases.vanish_execute_rejects_a_changed_target_member_or_queue(N)
    local wants = {back="OWNER_CHANGED", corpse="OWNER_CHANGED", uncounted="OWNER_CHANGED", left="OWNER_CHANGED",
        died="OWNER_CHANGED", replaced="OWNER_CHANGED", swapped="OWNER_CHANGED", readdressed="OWNER_CHANGED",
        items="INVENTORY_CHANGED", queued="DELIVERY_QUEUE_NOT_EXCLUSIVE", pile="DELIVERY_QUEUE_NOT_EXCLUSIVE"}
    for _, variant in ipairs({"back", "corpse", "uncounted", "left", "died", "replaced", "swapped", "readdressed",
        "items", "queued", "pile"}) do
        local f = vanish_world(N); local a = f.adapter
        assert(a:inspect_target(f.target_id, f.source_ids[1], 1)); f.boundary()
        if variant == "back" then f.creature.alive = true; f.area.m_lVertSort[#f.area.m_lVertSort + 1] = 3
        elseif variant == "corpse" then f.area.m_lVertSort[#f.area.m_lVertSort + 1] = 3
        elseif variant == "uncounted" then f.globals.SPRITE_IS_DEADFLPRC1 = 0
        elseif variant == "left" then f.area.m_lVertSort = {1, 2, 9}
        elseif variant == "died" then f.kara.alive = false
        elseif variant == "replaced" then f.kara.address = 999
        elseif variant == "swapped" then f.party[0] = f.minsc
        elseif variant == "readdressed" then
            -- Another sprite where Kara's was, under a new object id.
            local other = f.member(0, 10, "Imoen"); other.address = f.kara.address
        elseif variant == "items" then f.kara.slots[20] = f.item("OTHER", 557)
        elseif variant == "pile" then
            local pile_id = f.pile_at(300, 395, {})
            f.area.m_lVertSortBack = {pile_id}; f.queue[1] = f.message(pile_id, "OTHER", 12, pile_id)
        else f.queue[1] = f.message(8, "OTHER", 34) end
        local x, e = a:execute(f.command(1)); fail(x, e, wants[variant])
        assert(#f.calls == 0, variant)
    end
end
function cases.vanish_execute_requires_one_created_instance_and_one_drop_message(N)
    for _, variant in ipairs({"none", "queues", "kept", "stays", "other", "extra", "slot", "payload"}) do
        local f = vanish_world(N); local a = f.adapter
        if variant == "none" or variant == "queues" then f.create_variant = variant
        else f.drop_variant = variant end
        if variant == "other" then f.kara.slots[1] = f.item("OTHER", 832) end
        assert(a:inspect_target(f.target_id, f.source_ids[1], 1)); f.boundary()
        local command = f.command(1)
        local x, e = a:execute(command); fail(x, e, "CREATED_INSTANCE_UNPROVEN")
        local calls = (variant == "none" or variant == "queues") and 1 or 2
        assert(#f.calls == calls, variant)
        f.boundary(); x, e = a:observe(command); fail(x, e, "CREATED_INSTANCE_UNPROVEN")
        x, e = a:execute(command); fail(x, e, "MUTATION_PENDING")
    end
end
function cases.vanish_serves_creature_targets_only(N)
    -- A quest target's recipient is a party member: none there is no death.
    local f = vanish_world(N)
    f.fixture.targets[f.target_id] = {kind="quest", area="BG2600", identity=string.rep("c", 64),
        event_global="FLPR_EPOOL"}
    f.globals.FLPR_EPOOL, f.party[0] = 1, nil
    f.adapter = N.new(f.api, f.fixture)
    local x, e = f.adapter:inspect_target(f.target_id, f.source_ids[1], 1); fail(x, e, "OWNER_MISSING")
    assert(#f.calls == 0 and next(f.adapter.reservations) == nil)
    -- A container that is gone never died, whatever its name.
    f = vanish_world(N)
    f.fixture.targets[f.target_id] = {kind="container", area="BG2600", script_name="FLPRC1", x=500, y=600,
        container_type=4, identity=string.rep("c", 64)}
    f.adapter = N.new(f.api, f.fixture)
    x, e = f.adapter:inspect_target(f.target_id, f.source_ids[1], 1); fail(x, e, "OWNER_MISSING")
    assert(#f.calls == 0 and next(f.adapter.reservations) == nil)
end

-- A creature target that dies while its reward is on its way (BG2:EE 2.7.3.0
-- lab, 2026-10-26: a scene killed uddoor05 one tick after its ring04 had
-- arrived, and CIR stopped). Its death drop (CGameSprite::DropInventory) takes
-- the body's items out at once, in slot order, and queues one container add
-- for each into a ground pile at the corpse
-- (docs/native-death-drop-provenance-20260926.md). Run has no death check: a
-- creature add that runs later puts its item into the corpse.
local function death_world(N)
    local f = setup(N)
    f.creature.m_pos = {x=500, y=600}
    f.next_object = 50
    local function listed(id)
        for _, field in ipairs({"m_lVertSort", "m_lVertSortBack", "m_lVertSortFlight", "m_lVertSortUnder"}) do
            for _, entry in ipairs(f.area[field]) do if entry == id then return true end end
        end
    end
    local function copy_item(value, address)
        local result = f.item(value.cResRef:get(), address, value.m_flags)
        result.m_useCount1, result.m_useCount2, result.m_useCount3 =
            value.m_useCount1, value.m_useCount2, value.m_useCount3
        return result
    end
    function f.pile_at(x, y, items)
        f.next_object = f.next_object + 1
        local pile = {address=7000 + f.next_object, sprite=false, m_containerType=4, m_pos={x=x, y=y},
            m_lstItems=items or {}, m_scriptName={get=function() return "" end}}
        f.objects[f.next_object] = pile
        return f.next_object, pile
    end
    -- The pile a drop at a sprite uses: a listed one there, or a new one.
    local function pile_for(o)
        for candidate, object in pairs(f.objects) do
            if not object.sprite and object.m_containerType == 4 and listed(candidate) and
                math.abs(object.m_pos.x - o.m_pos.x) <= 16 and math.abs(object.m_pos.y - o.m_pos.y) <= 16 then
                return candidate
            end
        end
        return (f.pile_at(o.m_pos.x, o.m_pos.y - 5))
    end
    local function drop(o, slot, id)
        local value = o.slots[slot]
        o.slots[slot] = nil
        local message = f.message(id, value.cResRef:get(), 12, id)
        message.m_item = copy_item(value, f.next_address + 5000)
        f.queue[#f.queue + 1] = message
    end
    local give = f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly
    f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly = function(action, o)
        local ref = action:match('^DropItem%("([^"]+)",%[%-1%.%-1%]%)$')
        if not ref then return give(action, o) end
        f.calls[#f.calls + 1] = action
        for slot = 0, 38 do
            local value = o.slots[slot]
            if value and value.cResRef:get():lower() == ref then return drop(o, slot, pile_for(o)) end
        end
        error("fake DropItem found nothing")
    end
    -- Its death (or that of sprite <o>), and with <dropping> its death drop;
    -- an undroppable item (flag 8) stays in the body.
    function f.die(dropping, o)
        o = o or f.creature
        o.alive = false
        if not dropping then return end
        local id = pile_for(o)
        for slot = 0, 38 do
            local value = o.slots[slot]
            if value and math.floor(value.m_flags / 8) % 2 == 0 then drop(o, slot, id) end
        end
    end
    -- One handler update, in queue order: a creature add copies its item into
    -- the first empty backpack slot, of a corpse too; a container add appends
    -- a copy to its pile, which joins the area's lists if it is new.
    function f.update()
        while #f.queue > 0 do
            local message = table.remove(f.queue, 1)
            local target = f.objects[message.m_targetId]
            local subtype = message.virtual_GetMsgSubType()
            if subtype == 2 and target and target.sprite then
                for slot = 18, 33 do
                    if not target.slots[slot] then
                        f.next_address = f.next_address + 1
                        target.slots[slot] = copy_item(message.m_item, f.next_address)
                        break
                    end
                end
            elseif subtype == 12 and target and not target.sprite then
                f.next_address = f.next_address + 1
                target.m_lstItems[#target.m_lstItems + 1] = copy_item(message.m_item, f.next_address)
                if not listed(message.m_targetId) then
                    f.area.m_lVertSortBack[#f.area.m_lVertSortBack + 1] = message.m_targetId
                end
            end
        end
    end
    -- The names in the pile at the corpse, in order, and the pile.
    function f.pile()
        local pile = f.objects[f.area.m_lVertSortBack[1]]
        local names = {}
        for _, value in ipairs(pile.m_lstItems) do names[#names + 1] = value.cResRef:get() end
        return table.concat(names, " "), pile
    end
    return f
end
-- As in the lab: the reward had arrived, then its creature died. The death
-- drop took it along with the rest; the reward had reached its creature.
function cases.reward_its_creature_dropped_as_it_died_was_held(N)
    for _, variant in ipairs({"dropped", "dropping", "hit", "address"}) do
        local f = death_world(N); local a = f.adapter
        local command = f.start(1); local address = f.queue[1].address
        f.update(); f.boundary()
        local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
        f.die(true)
        -- The death drop's first add got the address of the reward's message.
        if variant == "address" then f.queue[1].address = address end
        if variant ~= "dropping" and variant ~= "address" then f.update() end
        -- A message of the killing blow, still queued for it.
        if variant == "hit" then f.queue[#f.queue + 1] = f.message(3, "OTHER", 34) end
        f.boundary(); local receipt = assert(a:observe(command))
        assert(receipt.created == 1 and receipt.nonce_bound_instance == 1 and receipt.held == 1 and
            receipt.lootable == 0 and receipt.holder == "creature" and receipt.slot == 18, variant)
        assert(#f.calls == 1 and a.pending == nil and a.held[command.index] == nil)
        f.update()
        assert(f.pile() == "PLAT01 FLPRI1 OTHER" and next(f.creature.slots) == nil)
        x, e = a:execute(command); fail(x, e, "ALREADY_ATTEMPTED")
    end
end
-- It died before the reward's message ran, which then put the reward into
-- the corpse; its death drop takes the reward along later.
function cases.reward_that_reached_a_dying_creature_waits_for_its_death_drop(N)
    local f = death_world(N); local a = f.adapter
    local command = f.start(1)
    f.die(false); f.update(); f.boundary()
    local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    assert(a:diagnostics().pending.created_slot == 18)
    for _ = 1, 3 do f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING") end
    assert(a:diagnostics().pending.dead_tick and #f.calls == 1)
    f.die(true); f.update(); f.boundary()
    local receipt = assert(a:observe(command))
    assert(receipt.held == 1 and receipt.holder == "creature" and receipt.slot == 18 and #f.calls == 1)
    assert(f.pile() == "PLAT01 FLPRI1 OTHER")
end
-- Its death drop ran before the reward's message, which then put the reward
-- into the corpse, where nobody could take it: CIR drops it at the corpse.
function cases.reward_that_came_after_the_death_drop_goes_to_the_ground_at_the_corpse(N)
    local f = death_world(N); local a = f.adapter
    local command = f.start(1)
    f.die(true); f.update(); f.boundary()
    local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    assert(f.creature.slots[18].cResRef:get() == "FLPRI1" and f.pile() == "PLAT01 OTHER")
    local waited = 0
    repeat
        waited = waited + 1
        f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    until #f.calls == 2 or waited > N.DEATH_DROP_TICKS + 1
    assert(waited == N.DEATH_DROP_TICKS + 1 and f.calls[2] == 'DropItem("flpri1",[-1.-1])')
    assert(next(f.creature.slots) == nil and a:diagnostics().pending.ground == true)
    f.update(); f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    f.boundary(); local receipt = assert(a:observe(command))
    assert(receipt.holder == "ground" and receipt.lootable == 1 and receipt.held == nil and receipt.notice == nil)
    local names, pile = f.pile()
    assert(names == "PLAT01 OTHER FLPRI1" and #f.area.m_lVertSortBack == 1 and pile.m_lstItems[3].m_flags == 1)
    assert(#f.calls == 2 and a.pending == nil)
    x, e = a:execute(command); fail(x, e, "ALREADY_ATTEMPTED")
end
-- The drop waits while the corpse holds another item of the reward's name
-- (DropItem takes the first) or an add is on its way into some pile.
function cases.corpse_drop_waits_for_a_single_copy_and_a_quiet_queue(N)
    for _, variant in ipairs({"copy", "add"}) do
        local f = death_world(N); local a = f.adapter
        if variant == "copy" then f.creature.slots[30] = f.item("FLPRI1", 315, 9) end
        local command = f.start(1)
        f.die(true); f.update()
        f.boundary(); local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
        for _ = 1, N.DEATH_DROP_TICKS + 3 do
            if variant == "add" then f.queue[1] = f.message(77, "OTHER", 12, 77) end
            f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
        end
        assert(#f.calls == 1, variant)
        if variant == "copy" then f.creature.slots[30] = nil else f.queue[1] = nil end
        f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
        assert(#f.calls == 2 and f.creature.slots[18] == nil, variant)
    end
end
-- Its message ran and it died with its death drop before CIR looked again:
-- the reward is the one new item like it on the ground.
function cases.reward_never_seen_in_its_dead_creature_is_found_on_the_ground(N)
    for _, variant in ipairs({"new", "reused", "old-copy", "in-flight", "address", "identified"}) do
        local f = death_world(N); local a = f.adapter
        if variant == "reused" or variant == "old-copy" then
            local id = f.pile_at(500, 598, {f.item(variant == "reused" and "SW1H01" or "FLPRI1", 880)})
            f.area.m_lVertSortBack = {id}
        elseif variant == "identified" then
            f.fixture.sources[f.source_ids[1]].payload.flags = 0
            f.adapter = N.new(f.api, f.fixture); a = f.adapter
        end
        local command = f.start(1); local address = f.queue[1].address
        f.update(); f.die(true)
        -- The reward's own add in the death drop got its message's address.
        local flight = variant == "in-flight" or variant == "address"
        if variant == "address" then f.queue[2].address = address end
        if not flight then f.update() end
        local names, pile = nil, nil
        if variant == "identified" then
            names, pile = f.pile()
            -- The add identified the zero-lore reward again.
            assert(pile.m_lstItems[2].m_flags == 0); pile.m_lstItems[2].m_flags = 1
        end
        f.boundary(); local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
        if flight then
            assert(a:diagnostics().pending.found == nil)
            f.update(); f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
        end
        local found = assert(a:diagnostics().pending.found, variant)
        names, pile = f.pile()
        local reward = pile.m_lstItems[(variant == "reused" or variant == "old-copy") and 3 or 2]
        assert(reward.cResRef:get() == "FLPRI1" and found.pointer == tostring(reward.address), variant)
        assert(reward.m_flags == (variant == "identified" and 0 or 1))
        f.boundary(); local receipt = assert(a:observe(command))
        assert(receipt.holder == "ground" and receipt.lootable == 1 and receipt.held == nil and #f.calls == 1, variant)
        assert(a.pending == nil)
    end
end
function cases.dropped_reward_is_never_guessed(N)
    -- An item just like it that the creature carried fell with it.
    local f = death_world(N); local a = f.adapter
    f.creature.slots[22] = f.item("FLPRI1", 313)
    local command = f.start(1)
    f.update(); f.die(true); f.update(); f.boundary()
    local x, e = a:observe(command); fail(x, e, "DROPPED_REWARD_AMBIGUOUS")
    -- Another one like it came to the ground elsewhere at the same time.
    f = death_world(N); a = f.adapter
    command = f.start(1)
    f.update(); f.die(true); f.update()
    local id = f.pile_at(900, 900, {f.item("FLPRI1", 881)})
    f.area.m_lVertSortBack[#f.area.m_lVertSortBack + 1] = id
    f.boundary(); x, e = a:observe(command); fail(x, e, "DROPPED_REWARD_AMBIGUOUS")
    -- Gone without a trace: it waits until the settling time is over.
    f = death_world(N); a = f.adapter
    command = f.start(1)
    f.update(); f.die(true)
    for index = #f.queue, 1, -1 do
        if f.queue[index].m_item.cResRef:get() == "FLPRI1" then table.remove(f.queue, index) end
    end
    f.update(); f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    assert(a:on_observation_boundary(f.tick + N.MAX_SETTLE_TICKS))
    x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING_TIMEOUT")
    assert(#f.calls == 1)
end
-- A creature that died and left no body (its death counted, Adapter:_vanished).
function cases.reward_of_a_creature_that_died_without_a_body(N)
    local function vanish(f) f.area.m_lVertSort = {1, 2}; f.globals.SPRITE_IS_DEADFLPRC1 = 1 end
    -- The reward had arrived: it reached its creature.
    local f = death_world(N); local a = f.adapter
    local command = f.start(1)
    f.update(); f.boundary(); local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    f.die(true); vanish(f); f.update(); f.boundary()
    local receipt = assert(a:observe(command))
    assert(receipt.held == 1 and receipt.holder == "creature" and #f.calls == 1)
    -- Never seen: found where it fell.
    f = death_world(N); a = f.adapter
    command = f.start(1)
    f.update(); f.die(true); vanish(f); f.update(); f.boundary()
    x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    f.boundary(); receipt = assert(a:observe(command))
    assert(receipt.holder == "ground" and receipt.lootable == 1 and #f.calls == 1)
    -- Gone from its area alive, its object still there: its items are read
    -- where it is now. The reward reached it.
    f = death_world(N); a = f.adapter
    command = f.start(1)
    f.update(); f.area.m_lVertSort = {1, 2}; f.boundary()
    x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    f.boundary(); receipt = assert(a:observe(command))
    assert(receipt.held == 1 and receipt.holder == "creature" and receipt.slot == 18 and #f.calls == 1)
    assert(a.held[command.index] == nil and f.creature.slots[18].cResRef:get() == "FLPRI1")
end
-- A party member keeps its items when it dies (a quest target's recipient).
function cases.party_recipient_that_dies_keeps_its_reward(N)
    local f = death_world(N)
    f.creature.portrait = 0; f.creature.m_id = 3
    f.api.EEex_GameState_GetGlobalInt = function(name) assert(name == "FLPR_EPOOL"); return 1 end
    f.api.EEex_Sprite_GetInPortrait = function(index) assert(index == 0); return f.creature end
    f.fixture.targets[f.target_id] = {kind="quest", area="BG2600", identity=string.rep("c", 64),
        event_global="FLPR_EPOOL"}
    f.adapter = N.new(f.api, f.fixture); local a = f.adapter
    assert(a:inspect_target(f.target_id, f.source_ids[1], 1)); f.boundary()
    local command = f.command(1); assert(a:execute(command))
    -- Where it would leave no body, its items would fall.
    assert(a.pending.piles and next(a.pending.piles) == nil and a.pending.area == "bg2600")
    f.die(false); f.update(); f.boundary()
    local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    f.boundary(); local receipt = assert(a:observe(command))
    assert(receipt.holder == "party" and receipt.lootable == 1 and receipt.held == nil and receipt.slot == 18)
    assert(#f.calls == 1 and f.creature.slots[18].cResRef:get() == "FLPRI1")
end
-- A recipient a reward reply marked, outside the party. Only a delivery on its
-- way follows it into death: one that died before still waits at inspection.
function cases.marked_recipient_outside_the_party_that_dies(N)
    local function marked()
        local f = death_world(N)
        f.creature.m_id = 3
        f.api.EEex_GameState_GetGlobalInt = function(name) assert(name == "FLPR_EPOOL"); return 1 end
        f.api.EEex_Sprite_GetLocalInt = function(o, name)
            assert(name == "FLPR_RPOOL"); return o == f.creature and 1 or 0
        end
        f.fixture.targets[f.target_id] = {kind="quest", area="*", identity=string.rep("c", 64),
            adapter_id="mode2-reward-v1", event_global="FLPR_EPOOL", recipient_local="FLPR_RPOOL"}
        f.adapter = N.new(f.api, f.fixture)
        return f, f.adapter
    end
    local f, a = marked()
    f.creature.alive = false
    local x, e = a:inspect_target(f.target_id, f.source_ids[1], 1); fail(x, e, "TARGET_NOT_ALIVE")
    assert(#f.calls == 0 and a.reservations[string.rep("c", 64)] == nil)
    -- Its message ran and it died with its death drop before CIR looked again.
    f, a = marked()
    assert(a:inspect_target(f.target_id, f.source_ids[1], 1)); f.boundary()
    local command = f.command(1); assert(a:execute(command))
    assert(a.pending.piles)
    f.update(); f.die(true); f.update(); f.boundary()
    x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    assert(a:diagnostics().pending.found)
    f.boundary(); local receipt = assert(a:observe(command))
    assert(receipt.holder == "ground" and receipt.lootable == 1 and #f.calls == 1)
    assert(f.pile() == "PLAT01 FLPRI1 OTHER")
    -- Its death drop came before its message, which put the reward into the
    -- body: CIR drops it at the corpse.
    f, a = marked()
    assert(a:inspect_target(f.target_id, f.source_ids[1], 1)); f.boundary()
    command = f.command(1); assert(a:execute(command))
    f.die(true); f.update()
    for _ = 1, N.DEATH_DROP_TICKS + 2 do f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING") end
    assert(#f.calls == 2 and a:diagnostics().pending.ground == true)
    f.update(); f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    f.boundary(); receipt = assert(a:observe(command))
    assert(receipt.holder == "ground" and receipt.lootable == 1 and f.pile() == "PLAT01 OTHER FLPRI1")
end
-- CIR reads only the items of the reward's name in the piles of the area:
-- anything of any mod may lie on the ground, even under a name CIR would
-- never write (Adapter:_pile_items).
function cases.piles_are_read_for_the_rewards_name_only(N)
    for _, variant in ipairs({"found", "corpse"}) do
        local f = death_world(N); local a = f.adapter
        local id, odd = f.pile_at(500, 598, {f.item("BAD NAME", 880), f.item("ODD!$", 881)})
        f.area.m_lVertSortBack = {id}
        local command = f.start(1)
        local x, e
        if variant == "found" then
            f.update(); f.die(true); f.update()
            f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
            assert(a:diagnostics().pending.found)
        else
            f.die(true); f.update()
            local waited = 0
            repeat
                waited = waited + 1
                f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
            until #f.calls == 2 or waited > N.DEATH_DROP_TICKS + 2
            assert(#f.calls == 2 and a:diagnostics().pending.ground == true)
            f.update(); f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
        end
        f.boundary(); local receipt = assert(a:observe(command))
        assert(receipt.holder == "ground" and receipt.lootable == 1, variant)
        assert(odd.m_lstItems[1].cResRef:get() == "BAD NAME" and odd.m_lstItems[2].cResRef:get() == "ODD!$")
    end
end
-- CIR looks for a reward its creature dropped at most once in
-- DROP_SCAN_TICKS game ticks: each look reads the whole area.
function cases.dropped_reward_is_looked_for_every_few_ticks(N)
    local f = death_world(N); local a = f.adapter
    local command = f.start(1)
    f.update(); f.die(true)
    for index = #f.queue, 1, -1 do
        if f.queue[index].m_item.cResRef:get() == "FLPRI1" then table.remove(f.queue, index) end
    end
    f.update()
    local looks = {}
    for _ = 1, 2 * N.DROP_SCAN_TICKS + 1 do
        f.boundary(); local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
        local tick = a:diagnostics().pending.scan_tick
        if looks[#looks] ~= tick then looks[#looks + 1] = tick end
    end
    assert(#looks == 3 and looks[2] - looks[1] == N.DROP_SCAN_TICKS and looks[3] - looks[2] == N.DROP_SCAN_TICKS)
    assert(#f.calls == 1)
end
-- death_world with the party there: Kara (portrait 0) and Minsc (portrait 1),
-- with empty backpacks. CreateItem on a party member stores the item in its
-- first empty backpack slot at once.
local function lost_world(N)
    local f = death_world(N)
    f.party = {}
    f.api.EEex_Sprite_GetInPortrait = function(index) return f.party[index] end
    function f.member(portrait, id, name)
        local o = f.sprite(800 + id, name:upper(), {})
        o.portrait, o.m_id, o.name, o.m_pos = portrait, id, name, {x=300 + 40 * portrait, y=400}
        f.objects[id], f.ids[o], f.party[portrait] = o, id, o
        f.area.m_lVertSort[#f.area.m_lVertSort + 1] = id
        return o
    end
    f.kara, f.minsc = f.member(0, 8, "Kara"), f.member(1, 9, "Minsc")
    local act = f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly
    f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly = function(action, o)
        local ref, c1, c2, c3 = action:match('^CreateItem%("([^"]+)",(%d+),(%d+),(%d+)%)$')
        if not ref then return act(action, o) end
        f.calls[#f.calls + 1] = action
        assert(o.portrait ~= -1, "fake CreateItem on party members only")
        for slot = 18, 33 do
            if not o.slots[slot] then
                f.next_address = f.next_address + 1
                local made = f.item(ref:upper(), f.next_address)
                made.m_useCount1, made.m_useCount2, made.m_useCount3 = tonumber(c1), tonumber(c2), tonumber(c3)
                o.slots[slot] = made
                return
            end
        end
        error("fake party member has no free slot")
    end
    -- Sprite <o> (the creature) leaves the game: off the area's lists and
    -- the party, and its object is gone.
    function f.gone(o)
        o = o or f.creature
        local id = f.ids[o]
        for index = #f.area.m_lVertSort, 1, -1 do
            if f.area.m_lVertSort[index] == id then table.remove(f.area.m_lVertSort, index) end
        end
        if o.portrait ~= -1 then f.party[o.portrait] = nil end
        f.objects[id] = nil
    end
    -- Observes until the reward is dropped at a member's feet.
    function f.until_dropped(command)
        for _ = 1, N.DEATH_DROP_TICKS + N.DROP_SCAN_TICKS + 1 do
            f.boundary(); local x, e = f.adapter:observe(command); fail(x, e, "DELIVERY_SETTLING")
            if f.adapter.pending.ground then return end
        end
        error("the lost reward never fell")
    end
    return f
end
-- A creature that left the game while its reward was on its way, and the
-- reward nowhere: its message ran when the creature was gone, or the reward
-- went with it (removed without a death drop). Chris, 2026-10-26: such a
-- reward falls at a party member's feet, with a message. Whether its death
-- counted does not matter: its object is gone.
function cases.reward_of_a_creature_gone_from_the_game_falls_at_the_partys_feet(N)
    for _, variant in ipairs({"before-run", "with-it", "uncounted"}) do
        local f = lost_world(N); local a = f.adapter
        if variant ~= "uncounted" then f.globals.SPRITE_IS_DEADFLPRC1 = 1 end
        local command = f.start(1)
        if variant == "with-it" then f.update(); f.gone()
        else
            f.gone(); f.boundary(); local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
            f.update()
        end
        f.until_dropped(command)
        assert(f.tick - a.pending.gone_tick >= N.DEATH_DROP_TICKS, variant)
        assert(f.calls[2] == 'CreateItem("flpri1",0,0,0)' and f.calls[3] == 'DropItem("flpri1",[-1.-1])', variant)
        assert(next(f.kara.slots) == nil)
        f.update(); f.boundary(); local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
        f.boundary(); local receipt = assert(a:observe(command))
        assert(receipt.holder == "ground" and receipt.lootable == 1 and receipt.held == nil, variant)
        assert(receipt.notice.member == "Kara" and receipt.notice.resref == "flpri1" and receipt.notice.item == 7101)
        local names, pile = f.pile()
        assert(names == "FLPRI1" and pile.m_pos.x == 300 and #f.calls == 3 and a.pending == nil, variant)
        x, e = a:execute(command); fail(x, e, "ALREADY_ATTEMPTED")
    end
end
-- It waits while no member can drop it, and while the reward may still come
-- to the ground: a death drop's add on its way. A creature that left the
-- game with its death drop has its reward on the ground.
function cases.lost_reward_waits_for_a_member_or_its_drop(N)
    -- Kara holds an item of its name, Minsc's backpack is full.
    local f = lost_world(N); local a = f.adapter
    f.kara.slots[3] = f.item("FLPRI1", 830)
    for slot = 18, 33 do f.minsc.slots[slot] = f.item("MISC01", 840 + slot) end
    local command = f.start(1)
    f.gone(); f.update()
    local x, e
    for _ = 1, N.DEATH_DROP_TICKS + 2 * N.DROP_SCAN_TICKS do
        f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    end
    assert(#f.calls == 1 and a:diagnostics().pending.lost_tick)
    -- Placing it has a settling time of its own.
    f.tick = a.pending.tick + N.MAX_SETTLE_TICKS; assert(a:on_observation_boundary(f.tick))
    x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    -- Minsc makes room: it falls at his feet.
    f.minsc.slots[33] = nil
    f.until_dropped(command)
    assert(f.calls[2] == 'CreateItem("flpri1",0,0,0)' and f.minsc.slots[33] == nil)
    f.update(); f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    f.boundary(); local receipt = assert(a:observe(command))
    assert(receipt.holder == "ground" and receipt.notice.member == "Minsc")
    -- No member ever can: CIR stops once that time is over too.
    f = lost_world(N); a = f.adapter
    for _, member in ipairs({f.kara, f.minsc}) do
        for slot = 18, 33 do member.slots[slot] = f.item("MISC01", 860 + slot + 20 * member.portrait) end
    end
    command = f.start(1)
    f.gone(); f.update()
    for _ = 1, N.DEATH_DROP_TICKS + N.DROP_SCAN_TICKS + 1 do
        f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
        if a.pending.lost_tick then break end
    end
    f.tick = assert(a.pending.lost_tick) + N.MAX_SETTLE_TICKS; assert(a:on_observation_boundary(f.tick))
    x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING_TIMEOUT")
    assert(#f.calls == 1)
    -- Its death drop's add carries the reward, then the reward lies on the ground.
    f = lost_world(N); a = f.adapter
    command = f.start(1)
    f.update(); f.die(true); f.gone()
    for _ = 1, N.DEATH_DROP_TICKS + N.DROP_SCAN_TICKS + 1 do
        f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    end
    assert(#f.calls == 1 and a:diagnostics().pending.found == nil)
    f.update(); f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    assert(a:diagnostics().pending.found)
    f.boundary(); receipt = assert(a:observe(command))
    assert(receipt.holder == "ground" and receipt.notice == nil and #f.calls == 1)
    assert(f.pile() == "PLAT01 FLPRI1 OTHER")
end
-- A party member a reward reply marked, gone without a body while its reward
-- was on its way (chunked, say): its items fell, the reward with them. Or its
-- message ran after: the reward is nowhere and falls at another member's feet.
function cases.marked_party_member_that_leaves_no_body(N)
    local function marked()
        local f = lost_world(N)
        f.globals.FLPR_EPOOL = 1
        f.api.EEex_Sprite_GetLocalInt = function(o, name)
            assert(name == "FLPR_RPOOL"); return o == f.kara and 1 or 0
        end
        f.fixture.targets[f.target_id] = {kind="quest", area="*", identity=string.rep("c", 64),
            adapter_id="mode2-reward-v1", event_global="FLPR_EPOOL", recipient_local="FLPR_RPOOL"}
        f.adapter = N.new(f.api, f.fixture)
        assert(f.adapter:inspect_target(f.target_id, f.source_ids[1], 1)); f.boundary()
        local command = f.command(1); assert(f.adapter:execute(command))
        assert(f.queue[1].m_targetId == 8)
        return f, f.adapter, command
    end
    local f, a, command = marked()
    f.update(); f.die(true, f.kara); f.gone(f.kara)
    f.boundary(); local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    f.update(); f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    assert(a:diagnostics().pending.found)
    f.boundary(); local receipt = assert(a:observe(command))
    assert(receipt.holder == "ground" and receipt.notice == nil and #f.calls == 1 and f.pile() == "FLPRI1")
    f, a, command = marked()
    f.die(false, f.kara); f.gone(f.kara); f.update()
    f.until_dropped(command)
    f.update(); f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    f.boundary(); receipt = assert(a:observe(command))
    assert(receipt.holder == "ground" and receipt.notice.member == "Minsc" and #f.calls == 3)
end
-- Where the party left the area the delivery began in, its piles there can no
-- longer tell: an area's objects leave memory with it. Nothing is put at the
-- party's feet then; the reward parks (Adapter:_left), and a later settle
-- counts its recipient's items.
function cases.lost_reward_parks_after_the_party_left(N)
    local f = lost_world(N)
    f.globals.FLPR_EPOOL = 1; f.creature.m_id = 3
    f.api.EEex_Sprite_GetLocalInt = function(o) return o == f.creature and 1 or 0 end
    f.fixture.targets[f.target_id] = {kind="quest", area="*", identity=string.rep("c", 64),
        adapter_id="mode2-reward-v1", event_global="FLPR_EPOOL", recipient_local="FLPR_RPOOL"}
    f.adapter = N.new(f.api, f.fixture); local a = f.adapter
    assert(a:inspect_target(f.target_id, f.source_ids[1], 1)); f.boundary()
    local command = f.command(1); assert(a:execute(command))
    -- The party went on, and the area it left was unloaded.
    f.area = {m_resref={get=function() return "BG0100" end}, m_lVertSort={8, 9}, m_lVertSortBack={},
        m_lVertSortFlight={}, m_lVertSortUnder={}}
    f.objects[3] = nil; f.update()
    f.boundary(); local receipt = assert(a:observe(command))
    assert(receipt.parked == 1 and receipt.created == nil and receipt.holder == nil)
    assert(#f.calls == 1 and a.pending == nil)
end
-- Chris, 2026-10-27 (finding 5): the party may leave the place while a reward
-- is on its way. What was proven stands; anything else parks with the counts
-- before it (the witness's settle), and the next visit settles it.
local function leave(f) f.area.m_resref = {get=function() return "BG0100" end} end
local function come_back(f) f.area.m_resref = {get=function() return "BG2600" end} end
local function witnessed_start(f, index)
    index = index or 1
    local witness = assert(f.adapter:inspect_target(f.target_id, f.source_ids[index], index))
    f.boundary()
    local command = f.command(index); assert(f.adapter:execute(command))
    return command, witness
end
local function settle_request(f, witness, index)
    local id = f.source_ids[index or 1]
    return {kind="settle-parked", mode="observe-only", index=index or 1, session=f.session, token={},
        revision=string.rep("e", 64), source_id=id, target_id=f.target_id, settle=witness.settle,
        payload=clone(f.fixture.sources[id].payload)}
end
-- Parked with its message still queued, the area left behind.
local function parked_start(f)
    local command, witness = witnessed_start(f)
    leave(f); f.boundary()
    local receipt = assert(f.adapter:observe(command))
    assert(receipt.parked == 1 and f.adapter.pending == nil)
    return command, witness
end
function cases.leaving_parks_a_reward_on_its_way_and_keeps_what_was_proven(N)
    local f = death_world(N); local a = f.adapter
    local command, witness = witnessed_start(f)
    -- No item of its name in the creature or on the ground, first-of member none.
    assert(witness.settle == 1)
    leave(f); f.boundary()
    local receipt = assert(read_only(f, function() return a:observe(command) end))
    assert(receipt.parked == 1 and receipt.created == nil and receipt.lootable == nil and a.pending == nil)
    assert(#f.calls == 1)
    -- Pinned in its backpack before the party left: held there.
    f = death_world(N); a = f.adapter
    command = witnessed_start(f)
    f.update(); f.boundary(); local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    leave(f); f.boundary(); receipt = assert(a:observe(command))
    assert(receipt.held == 1 and receipt.lootable == 0 and receipt.holder == "creature" and receipt.slot == 18 and
        receipt.parked == nil and a.held[command.index] == nil)
    -- Found in the pile its death drop made: on the ground.
    f = death_world(N); a = f.adapter
    command = witnessed_start(f)
    f.update(); f.die(true); f.update(); f.boundary()
    x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    assert(a:diagnostics().pending.found)
    leave(f); f.boundary(); receipt = assert(a:observe(command))
    assert(receipt.holder == "ground" and receipt.lootable == 1 and receipt.parked == nil)
    -- No area shown while one loads: it waits.
    f = death_world(N); a = f.adapter
    command = witnessed_start(f)
    f.api.EEex_Area_GetVisible = function() return nil end; f.boundary()
    x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
end
function cases.witness_counts_the_items_of_the_rewards_name_before_it(N)
    local f = death_world(N)
    f.creature.slots[19] = f.item("FLPRI1", 551)
    local id = f.pile_at(500, 598, {f.item("FLPRI1", 552), f.item("OTHER", 553), f.item("FLPRI1", 554)})
    f.area.m_lVertSortBack = {id}
    local witness = assert(f.adapter:inspect_target(f.target_id, f.source_ids[1], 1))
    -- held 1, piles 2
    assert(witness.settle == 1 + 1 + 1024 * 2)
    -- Without a reward to place there are no counts.
    assert(f.adapter:inspect_target(f.target_id).settle == nil)
end
function cases.settle_counts_the_creatures_items_on_the_next_visit(N)
    -- Its message ran after the party left, before its area went: arrived.
    local f = death_world(N); local a = f.adapter
    local command, witness = parked_start(f)
    f.update()
    local request = settle_request(f, witness)
    local x, e = a:settle_parked(request); fail(x, e, "AREA_UNAVAILABLE")
    come_back(f); f.boundary()
    local receipt = assert(read_only(f, function() return a:settle_parked(request) end))
    assert(receipt.arrived == 1 and receipt.holder == "creature" and receipt.token == request.token and
        receipt.session == f.session and receipt.settle == witness.settle and receipt.index == 1 and #f.calls == 1)
    -- Its message went with its area: not arrived, for a new delivery.
    f = death_world(N); a = f.adapter
    command, witness = parked_start(f)
    table.remove(f.queue, 1); come_back(f); f.boundary()
    receipt = assert(a:settle_parked(settle_request(f, witness)))
    assert(receipt.arrived == 0 and receipt.holder == nil)
    -- An item of its name on its way anywhere could change the counts: it waits.
    f.queue[1] = f.message(77, "FLPRI1", 12, 77)
    x, e = a:settle_parked(settle_request(f, witness)); fail(x, e, "TARGET_QUEUE_BUSY")
    -- One of its name it had before is no arrival.
    f = death_world(N); a = f.adapter
    f.creature.slots[19] = f.item("FLPRI1", 551)
    command, witness = parked_start(f)
    table.remove(f.queue, 1); come_back(f); f.boundary()
    assert(a:settle_parked(settle_request(f, witness)).arrived == 0)
    -- Joined the party since: the party has it.
    f = death_world(N); a = f.adapter
    command, witness = parked_start(f)
    f.update(); f.creature.portrait = 2; come_back(f); f.boundary()
    receipt = assert(a:settle_parked(settle_request(f, witness)))
    assert(receipt.arrived == 1 and receipt.holder == "party")
end
function cases.settle_finds_a_dead_creatures_reward_in_its_body_or_on_the_ground(N)
    for _, variant in ipairs({"dropped", "body", "none"}) do
        local f = death_world(N); local a = f.adapter
        local _, witness = parked_start(f)
        if variant == "dropped" then f.update(); f.die(true); f.update()
        elseif variant == "body" then f.die(true); f.update()
        else table.remove(f.queue, 1); f.die(true); f.update() end
        come_back(f); f.boundary()
        local receipt = assert(read_only(f, function() return a:settle_parked(settle_request(f, witness)) end))
        if variant == "none" then assert(receipt.arrived == 0 and receipt.holder == nil, variant)
        else
            assert(receipt.arrived == 1 and receipt.holder == (variant == "body" and "creature" or "ground"), variant)
        end
        assert(#f.calls == 1)
    end
end
function cases.settle_counts_the_piles_where_its_creature_left_no_body(N)
    for _, arrived in ipairs({true, false}) do
        local f = death_world(N); local a = f.adapter
        local _, witness = parked_start(f)
        if arrived then f.update() else table.remove(f.queue, 1) end
        f.die(true); f.update()
        f.area.m_lVertSort = {1, 2}; f.globals.SPRITE_IS_DEADFLPRC1 = 1
        come_back(f); f.boundary()
        local receipt = assert(a:settle_parked(settle_request(f, witness)))
        assert(receipt.arrived == (arrived and 1 or 0) and receipt.holder == (arrived and "ground" or nil))
    end
    -- Gone without its death counted: it waits, as at inspection.
    local f = death_world(N); local a = f.adapter
    local _, witness = parked_start(f)
    table.remove(f.queue, 1); f.area.m_lVertSort = {1, 2}; come_back(f); f.boundary()
    local x, e = a:settle_parked(settle_request(f, witness)); fail(x, e, "OWNER_MISSING")
end
-- A quest reward's recipient is in the party: it is followed into any area
-- while its reward is on its way, and a parked one is counted wherever it is.
function cases.quest_recipient_is_followed_into_another_area(N)
    local f = death_world(N)
    f.creature.portrait = 0; f.creature.m_id = 3
    f.api.EEex_GameState_GetGlobalInt = function(name) assert(name == "FLPR_EPOOL"); return 1 end
    f.api.EEex_Sprite_GetInPortrait = function(index) assert(index == 0); return f.creature end
    f.fixture.targets[f.target_id] = {kind="quest", area="BG2600", identity=string.rep("c", 64),
        event_global="FLPR_EPOOL"}
    f.adapter = N.new(f.api, f.fixture); local a = f.adapter
    local command, witness = witnessed_start(f)
    -- A party recipient keeps its items wherever it dies: no ground counts.
    assert(witness.settle == 1)
    leave(f); f.update(); f.boundary()
    local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    f.boundary(); local receipt = assert(a:observe(command))
    assert(receipt.holder == "party" and receipt.lootable == 1 and receipt.slot == 18 and receipt.parked == nil)
    -- A save held it on its way: counted in another area.
    receipt = assert(a:settle_parked(settle_request(f, witness)))
    assert(receipt.arrived == 1 and receipt.holder == "party")
    f.creature.alive = false
    receipt = assert(a:settle_parked(settle_request(f, witness)))
    assert(receipt.arrived == 1 and receipt.holder == "party")
    f.creature.slots[18] = nil
    assert(a:settle_parked(settle_request(f, witness)).arrived == 0)
end
return cases
'''

CASE_NAMES = sorted(Lua51().execute(CASES).keys())


def make_case(name):
    def run(self):
        lua = self.runtime(unpack_returned_tuples=True)
        lua.execute(CASES)[name](lua.execute(ADAPTER))
    return run


for label, runtime in (("Lua51", Lua51), ("Lua53", Lua53), ("LuaJIT21", LuaJIT21)):
    globals()[label + "NativeCreatureTests"] = type(label + "NativeCreatureTests", (unittest.TestCase,),
        {"runtime": runtime, **{"test_" + name: make_case(name) for name in CASE_NAMES}})


if __name__ == "__main__":
    unittest.main()
