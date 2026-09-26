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
            m_wear=0, m_flags=flags or 1, address=address}
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
function cases.absent_or_elsewhere_target_is_deferred_without_reservation(N)
    local f = setup(N); local a = f.adapter
    f.area.m_lVertSort = {1, 2}
    local x, e = a:inspect_target(f.target_id); fail(x, e, "OWNER_MISSING")
    f.area.m_resref = {get=function() return "BG0100" end}
    x, e = a:inspect_target(f.target_id); fail(x, e, "AREA_UNAVAILABLE")
    f.boundary(); x, e = a:execute(f.command(1)); fail(x, e, "RESERVATION_MISMATCH")
    assert(#f.calls == 0 and #f.queue == 0)
end
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
        if variant == "any-to-target" then f.queue[1] = f.message(3, "OTHER", 40)
        elseif variant == "container-fixture" then f.queue[1] = f.message(77, "FLPRI2", 12)
        else f.queue[1] = f.message(78, "FLPRI1", 2) end
        local x, e = f.adapter:inspect_target(f.target_id); fail(x, e, "TARGET_QUEUE_BUSY")
        assert(#f.calls == 0)
    end
end
function cases.unrelated_queue_traffic_is_neither_cast_nor_blocking(N)
    local f = setup(N); f.queue[1] = f.message(77, "OTHER", 40); f.queue[2] = f.message(78, "OTHER", 2)
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
    for _, variant in ipairs({"none", "target", "source", "payload", "form", "extra", "synchronous"}) do
        local f = setup(N); local a = f.adapter
        f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly = function(action)
            f.calls[#f.calls + 1] = action
            if variant == "none" then return end
            local m = f.message(3, "FLPRI1")
            if variant == "target" then m.m_targetId = 99
            elseif variant == "source" then m.m_sourceId = 99
            elseif variant == "payload" then m.m_item.m_flags = 0
            elseif variant == "form" then m.virtual_GetMsgSubType = function() return 12 end
            elseif variant == "extra" then f.queue[#f.queue + 1] = f.message(99, "OTHER", 40)
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
        if variant == "to-target" then f.queue[2] = f.message(3, "OTHER", 40)
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
