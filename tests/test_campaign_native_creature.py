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
    for _, variant in ipairs({"owner", "fixture-creature", "fixture-container", "unrelated"}) do
        local f = setup(N); local request = complete_held(f, 1)
        if variant == "owner" then f.queue[1] = f.message(3, "OTHER", 40)
        elseif variant == "fixture-creature" then f.queue[1] = f.message(90, "FLPRI1", 2)
        elseif variant == "fixture-container" then f.queue[1] = f.message(90, "FLPRI2", 12)
        else f.queue[1] = f.message(90, "OTHER", 40) end
        local x, e = read_only(f, function() return f.adapter:held_binding(request) end)
        if variant == "unrelated" then assert(x) else fail(x, e, "HELD_QUEUE_CONFLICT") end
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
function cases.verify_held_drop_rejects_wrong_ground_instance_or_payload_and_duplicate_occurrences(N)
    for _, variant in ipairs({"pointer", "container-pointer", "container-type", "missing", "flags", "charge", "wear", "resref",
        "second-ground-copy", "same-pointer-twice", "copy-on-corpse", "copy-on-other-sprite"}) do
        local f, request, candidate = dropped(N)
        if variant == "pointer" then f.ground_item.address = 999
        elseif variant == "container-pointer" then f.ground.address = 999
        elseif variant == "container-type" then f.ground.m_containerType = 0
        elseif variant == "missing" then f.ground.m_lstItems = {}
        elseif variant == "flags" then f.ground_item.m_flags = 0
        elseif variant == "charge" then f.ground_item.m_useCount3 = 1
        elseif variant == "wear" then f.ground_item.m_wear = 1
        elseif variant == "resref" then f.ground_item.cResRef = {get=function() return "OTHER" end}
        elseif variant == "copy-on-corpse" then f.creature.slots[18] = f.item("FLPRI1", 888)
        elseif variant == "copy-on-other-sprite" then f.source2.slots[18] = f.item("FLPRI1", 888)
        else
            local other_item = variant == "same-pointer-twice" and f.ground_item or f.item("FLPRI1", 888)
            f.objects[5] = {address=702, sprite=false, m_containerType=4, m_lstItems={other_item},
                m_scriptName={get=function() return "OTHERPILE" end}}
            f.area.m_lVertSortUnder = {5}
        end
        local x, e = read_only(f, function() return f.adapter:verify_held_drop(request, candidate) end)
        fail(x, e, "DROP_GROUND_INSTANCE_UNPROVEN")
    end
end
function cases.verify_held_drop_rejects_party_owned_item_or_another_party_copy(N)
    for _, variant in ipairs({"picked-up", "additional-copy", "party-only"}) do
        local f, request, candidate = dropped(N)
        local party = f.sprite(801, "PARTY", {}); party.portrait = 0
        party.slots[18] = variant == "additional-copy" and f.item("FLPRI1", 888) or f.ground_item
        if variant ~= "additional-copy" then f.ground.m_lstItems = {} end
        f.party[0] = party
        if variant ~= "party-only" then f.objects[5] = party; f.area.m_lVertSortUnder = {5} end
        local x, e = read_only(f, function() return f.adapter:verify_held_drop(request, candidate) end)
        fail(x, e, "DROP_GROUND_INSTANCE_UNPROVEN")
    end
    local f, request, candidate = dropped(N); f.api.EEex_Sprite_GetInPortrait = nil
    local x, e = read_only(f, function() return f.adapter:verify_held_drop(request, candidate) end)
    fail(x, e, "PARTY_SCAN_UNAVAILABLE")
end
function cases.verify_held_drop_rejects_container_or_fixture_queue_conflicts(N)
    for _, variant in ipairs({"container", "fixture-creature", "fixture-container"}) do
        local f, request, candidate = dropped(N)
        if variant == "container" then f.queue[1] = f.message(4, "OTHER", 40)
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
