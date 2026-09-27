"""Pure Lua source-guard protocol tests on Lua 5.1, 5.3, and LuaJIT.

The synthetic store and observations do not invoke a game adapter or create items.
"""
from pathlib import Path
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT


ROOT = Path(__file__).resolve().parents[1]
CASES = r'''
local cases={}
local SOURCE_ID="test/container/0/item/0"
local SOURCE_HASH=string.rep("1",64)
local function clone(value)
    if type(value)~="table" then return value end
    local result={}
    for key,item in pairs(value) do result[key]=clone(item) end
    return result
end
local function failed(expected, result, reason)
    assert(result==nil and reason==expected, tostring(result).." / "..tostring(reason).." ~= "..expected)
end
local function payload()
    return {resref="flpri1",charges={0,0,0},expiration=0,flags=1}
end
local function fixture(Core, revision)
    local f={values={},writes=0,write_keys={},lease_active=true,lookup={}}
    f.manifest={schema=Core.SCHEMA,state="prototype-not-playable",
        revision=string.rep(revision or "a",64),bound_revision=string.rep("b",64),
        world_fingerprint=string.rep("c",64),profile_id="source-guard-synthetic",
        sources={{id=SOURCE_ID,quantity=1,resource_sha256=SOURCE_HASH,
            record={id=SOURCE_ID,kind="container",record=payload()}}},
        targets={{id="test/target/0",capacity=1,resource_sha256=string.rep("2",64),
            record={id="test/target/0",kind="container"}}}}
    f.lookup[f.manifest.revision]=f.manifest
    f.storage={}
    function f.storage:get(key) return f.values[key] or 0 end
    function f.storage:set(key,value)
        f.writes=f.writes+1
        f.write_keys[#f.write_keys+1]=key
        if f.fail_before_key==key then error("BEFORE_PHASE_WRITE") end
        f.values[key]=value
        if f.fail_after_key==key then error("AFTER_PHASE_WRITE") end
    end
    function f:new_controller()
        return assert(Core.new(self.storage,function(id) return f.lookup[id] end))
    end
    f.controller=f:new_controller()
    function f:capture() assert(self.controller:capture(self.manifest,17,"confirmed-new-game")) end
    function f:guard_request()
        return assert(self.controller:source_guard_request(1))
    end
    function f:guard_receipt(request)
        local receipt=clone(request)
        receipt.session=request.session; receipt.token=request.token
        receipt.pre_access=1; receipt.exact_source_bound=1; receipt.generation=7
        receipt.active=function()
            if f.lease_error then error("LEASE_THROW") end
            return f.lease_active
        end
        return receipt
    end
    function f:protect()
        local request=self:guard_request()
        self.controller:on_observation_boundary()
        assert(self.controller:confirm_source_guard(self:guard_receipt(request)))
        return request
    end
    function f:delivery_witness()
        local assignment=assert(self.controller:assignments())[1]
        return {identity=string.rep("d",64),target_id=assignment.target_id,accepts=1}
    end
    function f:removal_request()
        return assert(self.controller:guarded_source_removal_request(1))
    end
    function f:removal_receipt(request)
        local receipt=clone(request)
        receipt.session=request.session; receipt.token=request.token
        receipt.exact_source_event=1; receipt.removed=1; receipt.remaining=0
        return receipt
    end
    function f:source_witness()
        return {identity=string.rep("e",64),source_id=SOURCE_ID,
            exact_matches=1,quantity=1,payload=payload()}
    end
    return f
end

function cases.guard_request_is_observe_only_and_commits_one_source_phase(Core)
    local f=fixture(Core); f:capture()
    assert(Core.PHASE.protected==5 and Core.G.guard=="flpr_guard")
    assert(f.values.flpr_guard==1)
    local request=f:guard_request()
    assert(request.kind=="source-guard" and request.mode=="observe-only" and request.index==1)
    assert(type(request.session)=="table" and type(request.token)=="table")
    assert(request.revision==f.manifest.revision and request.source_id==SOURCE_ID)
    assert(request.resource_sha256==SOURCE_HASH and request.quantity==1)
    assert(request.payload.resref=="flpri1" and request.payload.flags==1)
    assert(f.values.flpr_jkind==0 and f.values.flpr_s0001==1)
    failed("LATER_OBSERVATION_REQUIRED",f.controller:confirm_source_guard(f:guard_receipt(request)))
    f.controller:on_observation_boundary()
    local before=f.writes
    assert(f.controller:confirm_source_guard(f:guard_receipt(request)))
    assert(f.values.flpr_s0001==6 and f.values.flpr_jkind==0)
    assert(f.writes==before+1 and f.write_keys[#f.write_keys]=="flpr_s0001")
    assert(f.controller:assignments()[1].source_phase==5)
    failed("ALREADY_ATTEMPTED",f.controller:begin_source(1,f:source_witness()))
end

function cases.guard_receipt_scope_and_session_are_exact(Core)
    local f=fixture(Core); f:capture()
    local request=f:guard_request(); f.controller:on_observation_boundary()
    local edits={kind="wrong",mode="execute-once",index=2,revision=string.rep("f",64),
        source_id="other",resource_sha256=string.rep("0",64),quantity=2}
    for field,value in pairs(edits) do
        local receipt=f:guard_receipt(request); receipt[field]=value
        assert(not f.controller:confirm_source_guard(receipt),field)
        assert(f.values.flpr_s0001==1 and f.values.flpr_jkind==0)
    end
    local wrong=f:guard_receipt(request); wrong.session={}
    assert(not f.controller:confirm_source_guard(wrong))
    wrong=f:guard_receipt(request); wrong.token={}
    assert(not f.controller:confirm_source_guard(wrong))
    wrong=f:guard_receipt(request); wrong.payload.flags=2
    assert(not f.controller:confirm_source_guard(wrong))
    wrong=f:guard_receipt(request); wrong.payload.charges[2]=1
    assert(not f.controller:confirm_source_guard(wrong))
    assert(f.controller:confirm_source_guard(f:guard_receipt(request)))
end

function cases.request_payload_is_a_copy_of_the_frozen_source(Core)
    local f=fixture(Core); f:capture()
    local request=f:guard_request()
    request.payload.flags=99; request.payload.charges[1]=9
    assert(f.controller.manifest.sources[1].record.record.flags==1)
    assert(f.controller.manifest.sources[1].record.record.charges[1]==0)
    local again=f:guard_request()
    assert(again.token==request.token and again.payload.flags==1 and again.payload.charges[1]==0)
    f.controller:on_observation_boundary()
    assert(not f.controller:confirm_source_guard(f:guard_receipt(request)))
    assert(f.values.flpr_s0001==1)
    assert(f.controller:confirm_source_guard(f:guard_receipt(again)))
end

function cases.guard_receipt_requires_exact_positive_active_lease(Core)
    local cases_bad={
        function(r) r.pre_access=0 end,
        function(r) r.exact_source_bound=0 end,
        function(r) r.generation=0 end,
        function(r) r.generation=1.5 end,
        function(r) r.generation=2147483648 end,
        function(r) r.active=function() return 1 end end,
        function(r) r.active=function() return false end end,
        function(r) r.active=function() error("LEASE_THROW") end end,
    }
    for _,alter in ipairs(cases_bad) do
        local f=fixture(Core); f:capture()
        local request=f:guard_request(); f.controller:on_observation_boundary()
        local receipt=f:guard_receipt(request); alter(receipt)
        local ok,result,reason=pcall(function() return f.controller:confirm_source_guard(receipt) end)
        assert(ok and result==nil,tostring(reason))
        assert(f.values.flpr_s0001==1 and f.values.flpr_jkind==0)
    end
end

function cases.guard_is_for_single_physical_container_or_creature_and_quiet_journal(Core)
    for _,kind in ipairs({"container","cre-template","embedded-cre"}) do
        local f=fixture(Core); f.manifest.sources[1].record.kind=kind; f:capture()
        assert(f.controller:source_guard_request(1))
        assert(f.values.flpr_s0001==1 and f.values.flpr_jkind==0)
    end
    for _,change in ipairs({
        function(f) f.manifest.sources[1].quantity=2; f.manifest.targets[1].capacity=2 end,
        function(f) f.manifest.sources[1].record.kind="actor" end,
    }) do
        local f=fixture(Core); change(f); f:capture()
        assert(not f.controller:source_guard_request(1))
        assert(f.values.flpr_s0001==1 and f.values.flpr_jkind==0)
    end
    local f=fixture(Core); f:capture(); f:protect()
    local operation=assert(f.controller:begin_delivery(1,f:delivery_witness()))
    assert(operation.kind=="deliver" and f.values.flpr_jkind==2)
    assert(not f.controller:source_guard_request(1))
    assert(not f.controller:guarded_source_removal_request(1))
end

function cases.delivery_needs_live_lease_and_observe_keeps_journal_when_revoked(Core)
    local f=fixture(Core); f:capture(); f:protect()
    f.lease_active=false
    failed("SOURCE_GUARD_INACTIVE",f.controller:begin_delivery(1,f:delivery_witness()))
    assert(f.values.flpr_jkind==0 and f.values.flpr_d0001==1)
    f.lease_active=true
    failed("SOURCE_GUARD_INACTIVE",f.controller:begin_delivery(1,f:delivery_witness()))
    assert(f.values.flpr_jkind==0 and f.values.flpr_d0001==1)
    local rebound=f:guard_request(); f.controller:on_observation_boundary()
    assert(f.controller:confirm_source_guard(f:guard_receipt(rebound)))
    local operation=assert(f.controller:begin_delivery(1,f:delivery_witness()))
    f.controller:on_observation_boundary()
    f.lease_active=false
    local receipt=clone(operation); receipt.session=operation.session
    receipt.created=1; receipt.nonce_bound_instance=1; receipt.lootable=1
    failed("SOURCE_GUARD_INACTIVE",f.controller:observe(receipt))
    assert(f.values.flpr_jkind==2 and f.values.flpr_d0001==2)
    f.lease_active=true
    failed("SOURCE_GUARD_INACTIVE",f.controller:observe(receipt))
    assert(f.values.flpr_jkind==2 and f.values.flpr_d0001==2)
    failed("MUTATION_PENDING",f.controller:source_guard_request(1))
end

function cases.throwing_lease_is_permanently_revoked(Core)
    local f=fixture(Core); f:capture(); f:protect()
    f.lease_error=true
    failed("SOURCE_GUARD_INACTIVE",f.controller:begin_delivery(1,f:delivery_witness()))
    f.lease_error=false
    failed("SOURCE_GUARD_INACTIVE",f.controller:begin_delivery(1,f:delivery_witness()))
    assert(f.values.flpr_jkind==0 and f.values.flpr_d0001==1)
    local rebound=f:guard_request(); f.controller:on_observation_boundary()
    assert(f.controller:confirm_source_guard(f:guard_receipt(rebound)))
    assert(f.controller:begin_delivery(1,f:delivery_witness()))
end

function cases_guarded_removal(Core)
    local f=fixture(Core); f:capture(); f:protect()
    local request=f:removal_request()
    assert(request.kind=="guarded-source-removal" and request.mode=="observe-only")
    assert(request.index==1 and request.revision==f.manifest.revision)
    assert(request.source_id==SOURCE_ID and request.resource_sha256==SOURCE_HASH)
    assert(request.quantity==1 and request.guard_generation==7)
    assert(request.payload.resref=="flpri1")
    failed("LATER_OBSERVATION_REQUIRED",f.controller:confirm_guarded_source_removal(f:removal_receipt(request)))
    f.controller:on_observation_boundary()
    for _,alter in ipairs({
        function(r) r.exact_source_event=0 end,
        function(r) r.removed=0 end,
        function(r) r.remaining=1 end,
        function(r) r.guard_generation=8 end,
        function(r) r.payload.flags=2 end,
        function(r) r.token={} end,
        function(r) r.session={} end,
    }) do
        local receipt=f:removal_receipt(request); alter(receipt)
        assert(not f.controller:confirm_guarded_source_removal(receipt))
        assert(f.values.flpr_s0001==6 and f.values.flpr_jkind==0)
    end
    f.lease_active=false
    failed("SOURCE_GUARD_INACTIVE",f.controller:confirm_guarded_source_removal(f:removal_receipt(request)))
    f.lease_active=true
    failed("SOURCE_GUARD_INACTIVE",f.controller:confirm_guarded_source_removal(f:removal_receipt(request)))
    assert(f.values.flpr_s0001==6 and f.values.flpr_jkind==0)
    local rebound=f:guard_request(); f.controller:on_observation_boundary()
    assert(f.controller:confirm_source_guard(f:guard_receipt(rebound)))
    assert(not f.controller:confirm_guarded_source_removal(f:removal_receipt(request)))
    local current=f:removal_request(); f.controller:on_observation_boundary()
    assert(current.token~=request.token)
    local before=f.writes
    assert(f.controller:confirm_guarded_source_removal(f:removal_receipt(current)))
    assert(f.values.flpr_s0001==3 and f.values.flpr_jkind==0)
    assert(f.writes==before+1 and f.write_keys[#f.write_keys]=="flpr_s0001")
    assert(not f.controller:guarded_source_removal_request(1))
    return true
end
cases.guarded_removal_requires_exact_later_evidence=cases_guarded_removal

function cases.restore_rebinds_guard_and_preserves_delivery_phase(Core)
    local f=fixture(Core); f:capture(); f:protect()
    local before=assert(f.controller:assignments())[1]
    local old_request=f:guard_request()
    local restored=f:new_controller(); assert(restored:restore()); f.controller=restored
    local after=assert(restored:assignments())[1]
    assert(before.target_id==after.target_id and before.ordinal==after.ordinal)
    assert(after.source_phase==5 and after.delivery_phase==0)
    failed("SOURCE_GUARD_INACTIVE",restored:begin_delivery(1,f:delivery_witness()))
    assert(not restored:confirm_source_guard(f:guard_receipt(old_request)))
    local rebound=f:guard_request(); restored:on_observation_boundary()
    assert(restored:confirm_source_guard(f:guard_receipt(rebound)))
    local operation=assert(restored:begin_delivery(1,f:delivery_witness()))
    restored:on_observation_boundary()
    local receipt=clone(operation); receipt.session=operation.session
    receipt.created=1; receipt.nonce_bound_instance=1; receipt.lootable=1
    assert(restored:observe(receipt))
    assert(restored:assignments()[1].delivery_phase==2)
    local third=f:new_controller(); assert(third:restore()); f.controller=third
    assert(third:assignments()[1].source_phase==5 and third:assignments()[1].delivery_phase==2)
    assert(not third:guarded_source_removal_request(1))
    local renewed=f:guard_request(); third:on_observation_boundary()
    assert(third:confirm_source_guard(f:guard_receipt(renewed)))
    assert(third:assignments()[1].delivery_phase==2)
end

function cases.rebind_invalidates_old_guarded_removal_receipt(Core)
    local f=fixture(Core); f:capture(); f:protect()
    local removal=f:removal_request()
    local renewal=f:guard_request(); f.controller:on_observation_boundary()
    assert(f.controller:confirm_source_guard(f:guard_receipt(renewal)))
    assert(not f.controller:confirm_guarded_source_removal(f:removal_receipt(removal)))
    assert(f.values.flpr_s0001==6 and f.values.flpr_jkind==0)
    local current=f:removal_request(); f.controller:on_observation_boundary()
    assert(current.token~=removal.token and current.guard_generation==7)
    assert(f.controller:confirm_guarded_source_removal(f:removal_receipt(current)))
end

function cases.old_save_and_legacy_physical_phase_remain_valid(Core)
    local f=fixture(Core); f:capture()
    local operation=assert(f.controller:begin_source(1,f:source_witness()))
    f.controller:on_observation_boundary()
    local receipt=clone(operation); receipt.session=operation.session
    receipt.removed=1; receipt.remaining=0
    assert(f.controller:observe(receipt))
    assert(f.values.flpr_s0001==3)
    f.values.flpr_guard=nil -- pre-guard save's absent GLOBAL reads as zero
    local restored=f:new_controller(); assert(restored:restore())
    assert(restored:assignments()[1].source_phase==2)
    assert(restored:begin_delivery(1,f:delivery_witness()))
end

function cases.invalid_persisted_source_and_guard_versions_reject_restore(Core)
    for _,values in ipairs({
        {flpr_s0001=5}, -- held is never a source phase
        {flpr_s0001=6,flpr_guard=0},
        {flpr_s0001=6,flpr_guard=2},
    }) do
        local f=fixture(Core); f:capture()
        for key,value in pairs(values) do f.values[key]=value end
        local before=f.writes
        local ok,result=pcall(function() return f:new_controller():restore() end)
        assert(not ok or result==nil)
        assert(f.writes==before)
    end
end

function cases.ineligible_persisted_protected_source_rejects_restore(Core)
    for _,change in ipairs({
        function(f) f.manifest.sources[1].record.kind="actor" end,
        function(f) f.manifest.sources[1].quantity=2; f.manifest.targets[1].capacity=2 end,
    }) do
        local f=fixture(Core); change(f); f:capture()
        f.values.flpr_s0001=6
        local before=f.writes
        local ok,result=pcall(function() return f:new_controller():restore() end)
        assert(not ok or result==nil)
        assert(f.writes==before)
    end
end

function cases.phase_commit_write_cuts_restore_without_creation(Core)
    for _,failure in ipairs({"fail_before_key","fail_after_key"}) do
        local f=fixture(Core); f:capture()
        local request=f:guard_request(); f.controller:on_observation_boundary()
        f[failure]="flpr_s0001"
        assert(not pcall(function() f.controller:confirm_source_guard(f:guard_receipt(request)) end))
        f[failure]=nil
        local expected=f.values.flpr_s0001==6 and 5 or 0
        local restored=f:new_controller(); assert(restored:restore())
        assert(restored:assignments()[1].source_phase==expected)
        assert(f.values.flpr_jkind==0 and f.values.flpr_d0001==1)
        f.controller=restored
        assert(not restored:begin_delivery(1,f:delivery_witness()))
    end
end

function cases.guarded_removal_commit_cuts_restore_without_unearned_delivery(Core)
    for _,failure in ipairs({"fail_before_key","fail_after_key"}) do
        local f=fixture(Core); f:capture(); f:protect()
        local request=f:removal_request(); f.controller:on_observation_boundary()
        f[failure]="flpr_s0001"
        assert(not pcall(function()
            f.controller:confirm_guarded_source_removal(f:removal_receipt(request))
        end))
        f[failure]=nil
        local expected=f.values.flpr_s0001==3 and 2 or 5
        local restored=f:new_controller(); assert(restored:restore()); f.controller=restored
        assert(restored:assignments()[1].source_phase==expected)
        assert(f.values.flpr_jkind==0 and f.values.flpr_d0001==1)
        if expected==5 then
            failed("SOURCE_GUARD_INACTIVE",restored:begin_delivery(1,f:delivery_witness()))
            assert(f.values.flpr_jkind==0)
        else
            local operation=assert(restored:begin_delivery(1,f:delivery_witness()))
            assert(operation.kind=="deliver" and f.values.flpr_jkind==2)
        end
    end
end

function cases.destroy_and_new_campaign_invalidate_tokens(Core)
    local f=fixture(Core); f:capture()
    local request=f:guard_request(); f.controller:on_observation_boundary()
    f.controller:destroy()
    assert(not f.controller:confirm_source_guard(f:guard_receipt(request)))
    local second=fixture(Core,"d"); second:capture()
    second.controller:on_observation_boundary()
    assert(not second.controller:confirm_source_guard(f:guard_receipt(request)))
    assert(second.values.flpr_s0001==1 and second.values.flpr_jkind==0)
end

return cases
'''


class SourceGuardTests(unittest.TestCase):
    def test_lua_cases(self):
        module = (ROOT / "prototype/FLPRCore.lua").read_text(encoding="utf-8")
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime(unpack_returned_tuples=True)
            core, cases = lua.execute(module), lua.execute(CASES)
            for name in sorted(cases.keys()):
                with self.subTest(runtime=runtime.__module__, case=name):
                    cases[name](core)


if __name__ == "__main__":
    unittest.main()
