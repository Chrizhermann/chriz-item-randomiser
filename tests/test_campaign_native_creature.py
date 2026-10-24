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
    f.header = {maxStackable=1, itemFlags=12}
    local api = {}
    api.EngineGlobals = {g_pBaldurChitin={m_cMessageHandler={m_messageList=f.queue}}}
    api.EEex_CastUD = function(object, kind) f.casts[#f.casts + 1] = kind; return object end
    api.EEex_Area_GetVisible = function() return f.area end
    api.EEex_GameObject_Get = function(id) return f.objects[id] end
    api.EEex_GameObject_CastUserType = function(o) return o end
    api.EEex_GameObject_IsSprite = function(o, dead) return o.sprite and (dead or o.alive) or false end
    api.EEex_Sprite_GetPortraitIndex = function(o) return o.portrait end
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
function cases.death_before_run_or_before_receipt_never_claims_or_replays(N)
    for _, stage in ipairs({"queued", "pinned"}) do
        local f = setup(N); local a = f.adapter
        local command = f.start(1)
        if stage == "pinned" then
            f.update(); f.boundary(); local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
        end
        f.creature.alive = false
        -- Run has no death check: a queued message would land in the corpse.
        f.update(); f.boundary()
        local x, e = a:observe(command); fail(x, e, "TARGET_DIED_DURING_DELIVERY")
        f.creature.alive = true; f.boundary()
        x, e = a:observe(command); fail(x, e, "TARGET_DIED_DURING_DELIVERY")
        x, e = a:execute(command); fail(x, e, "MUTATION_PENDING")
        assert(#f.calls == 1)
    end
end
function cases.unexpected_placement_is_never_accepted(N)
    local wants = {displaced="INVENTORY_CHANGED", merged="INVENTORY_CHANGED", quick="DELIVERY_PLACEMENT_UNEXPECTED",
        two="DELIVERY_PLACEMENT_UNEXPECTED", lost="DELIVERY_MESSAGE_LOST", payload="DELIVERY_PAYLOAD_CHANGED"}
    for _, variant in ipairs({"displaced", "merged", "quick", "two", "lost", "payload"}) do
        local f = setup(N); local a = f.adapter
        local command = f.start(1); table.remove(f.queue, 1)
        if variant == "displaced" then f.creature.slots[21] = f.item("FLPRI1", 778)
        elseif variant == "merged" then f.creature.slots[21].m_useCount1 = 2
        elseif variant == "quick" then f.creature.slots[15] = f.item("FLPRI1", 778)
        elseif variant == "two" then f.creature.slots[18] = f.item("FLPRI1", 778); f.creature.slots[19] = f.item("FLPRI1", 779)
        elseif variant == "payload" then f.creature.slots[18] = f.item("FLPRI1", 778, 0) end
        f.boundary(); local x, e = a:observe(command); fail(x, e, wants[variant])
    end
end
function cases.competing_or_changed_messages_stop_observation(N)
    for _, variant in ipairs({"to-target", "fixture-item", "changed"}) do
        local f = setup(N); local a = f.adapter
        local command = f.start(1)
        if variant == "to-target" then f.queue[2] = f.message(3, "OTHER", 34)
        elseif variant == "fixture-item" then f.queue[2] = f.message(77, "FLPRI2", 12)
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
function cases.reused_message_pointer_after_consumption_is_rejected(N)
    local f = setup(N); local a = f.adapter
    local command = f.start(1); local address = f.queue[1].address
    f.update(); f.boundary(); local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
    local reused = f.message(55, "OTHER", 40); reused.address = address; f.queue[1] = reused
    f.boundary(); x, e = a:observe(command); fail(x, e, "DELIVERY_MESSAGE_REUSED")
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
-- party fills the highest empty slot from 34 down to 15 at once. DropItem takes
-- the first item of that name out and queues one container add (subtype 12,
-- slot 32767, target and source the pile) into a type-4 pile at the corpse: a
-- listed pile there, or a new one that joins the area as its message runs.
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
            for slot = 34, 15, -1 do
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
        receipt.held == nil and receipt.holder == "ground" and receipt.payload.flags == 1)
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
    local wants = {conflict="DELIVERY_QUEUE_CONFLICT", lost="GROUND_PILE_MISSING", two="DELIVERY_MESSAGE_LOST",
        early="DELIVERY_EARLY_ITEM", removed="CREATED_INSTANCE_CHANGED", area="AREA_UNAVAILABLE",
        replaced="OWNER_CHANGED", reused="DELIVERY_MESSAGE_REUSED", payload="DELIVERY_PAYLOAD_CHANGED"}
    for _, variant in ipairs({"conflict", "lost", "two", "early", "removed", "area", "replaced", "reused", "payload"}) do
        local f = ground_world(N); local a = f.adapter
        local pile_id, pile = f.pile_at(500, 600, {})
        if variant == "early" or variant == "replaced" then f.area.m_lVertSortBack = {pile_id} end
        local command = ground_start(f, 1)
        local id = f.queue[1].m_targetId
        if variant == "conflict" then f.queue[2] = f.message(id, "OTHER", 12, id)
        elseif variant == "lost" then table.remove(f.queue, 1)
        elseif variant == "two" then
            f.update(); local target = f.objects[id]
            target.m_lstItems[2] = f.item("FLPRI1", 1990)
        elseif variant == "early" then pile.m_lstItems[1] = f.item("OTHER", 1991)
        elseif variant == "removed" then
            f.update(); f.boundary(); local x, e = a:observe(command); fail(x, e, "DELIVERY_SETTLING")
            f.objects[id].m_lstItems = {}
        elseif variant == "area" then f.update(); f.area.m_resref = {get=function() return "BG0100" end}
        elseif variant == "replaced" then pile.address = 9999
        elseif variant == "reused" then
            local address = f.queue[1].address
            f.update(); local reused = f.message(55, "OTHER", 40); reused.address = address; f.queue[1] = reused
        elseif variant == "payload" then f.update(); f.objects[id].m_lstItems[1].m_useCount2 = 3 end
        f.boundary(); local x, e = a:observe(command); fail(x, e, wants[variant])
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
