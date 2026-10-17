"""Synthetic source-guard host integration on three Lua runtimes.

Real Core and Host, numeric save GLOBALs, fake world and adapters; no engine.
"""
from pathlib import Path
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT


ROOT = Path(__file__).resolve().parents[1]
CORE = (ROOT / "prototype/FLPRCore.lua").read_text(encoding="utf-8")
HOST = (ROOT / "prototype/FLPRHost.lua").read_text(encoding="utf-8")
CASES = r'''
local cases={}
local GUARDED="are:flpru1/container/0/item/1"
local PHYSICAL="cre:flprs2/item/0"
local SECOND_GUARDED="are:flpru2/container/0/item/1"
local function copy(value)
    if type(value)~="table" then return value end
    local result={}
    for key,item in pairs(value) do result[key]=copy(item) end
    return result
end
local function payload(ref)
    return {resref=ref,charges={0,0,0},expiration=0,flags=1}
end
local function setup(Core,Host,options)
    options=options or {}
    local f={values={},tick=0,events={},reports={},prepares={},guards={},natives={},
        loaded=false,lease_active=true,next_calls=0,seeds=0,world=true}
    local revision=string.rep("a",64)
    local manifest={schema=Core.SCHEMA,state="prototype-not-playable",revision=revision,
        bound_revision=string.rep("b",64),world_fingerprint=string.rep("c",64),
        profile_id="guard-host-synthetic",sources={},targets={}}
    manifest.sources[1]={id=GUARDED,quantity=1,resource_sha256=string.rep("1",64),
        record={id=GUARDED,kind=options.creature_kind or "container",record=payload("flpri1")}}
    manifest.sources[2]={id=PHYSICAL,quantity=1,resource_sha256=string.rep("2",64),
        record={id=PHYSICAL,kind="cre-template",record=payload("flpri2")}}
    if options.two_guarded then
        manifest.sources[2]={id=SECOND_GUARDED,quantity=1,resource_sha256=string.rep("2",64),
            record={id=SECOND_GUARDED,kind="container",record=payload("flpri2")}}
        f.loaded_by_id={[GUARDED]=false,[SECOND_GUARDED]=false}
        f.lease_active_by_id={[GUARDED]=true,[SECOND_GUARDED]=true}
        f.guard_observations_by_id={}
        f.removal_observations_by_id={}
    end
    manifest.targets[1]={id="are:flpru1/container/1",capacity=2,
        resource_sha256=string.rep("3",64),record={id="are:flpru1/container/1",kind="container"}}
    local descriptor={schema="flpr-unvisited-fixture-gate-v1",source_id=GUARDED}
    local fixture={revision=revision,sources={[GUARDED]={
        kind=options.creature_kind and "guarded-creature" or "guarded-container"}}}
    if options.two_guarded then
        descriptor.source_id=nil; descriptor.source_ids={GUARDED,SECOND_GUARDED}
        fixture.sources[SECOND_GUARDED]={kind="guarded-container"}
    end
    if not options.physical_only then fixture.source_gate=descriptor
    else fixture.sources[GUARDED].kind="physical" end
    if options.bad_schema then fixture.source_gate.schema="wrong" end
    if options.bad_id then fixture.source_gate.source_id="wrong" end
    if options.bad_endpoint then fixture.sources[GUARDED].kind="physical" end
    if options.bad_creature_endpoint then fixture.sources[GUARDED].kind="guarded-creature" end
    f.manifest=manifest
    f.bundle={manifest=manifest,fixture=fixture}
    f.bundles={[revision]=f.bundle}
    f.next_revision=revision
    f.storage={}
    function f.storage:get(key) return f.values[key] or 0 end
    function f.storage:set(key,value)
        if self.undo and self.undo.previous[key]==nil then
            self.undo.previous[key]=self:get(key); self.undo.order[#self.undo.order+1]=key
        end
        f.values[key]=value
    end
    function f.storage:begin() assert(not self.undo,'STORAGE_TRANSACTION_ACTIVE'); self.undo={previous={},order={}} end
    function f.storage:commit() assert(self.undo,'STORAGE_TRANSACTION_MISSING'); self.undo=nil end
    function f.storage:rollback()
        local undo=self.undo; self.undo=nil
        for i=#undo.order,1,-1 do f.values[undo.order[i]]=undo.previous[undo.order[i]] end
    end
    local function native_factory(value)
        local native={fixture=value}
        f.natives[#f.natives+1]=native
        function native:on_observation_boundary(tick) self.tick=tick; return true end
        function native:inspect_source(id)
            f.source_inspections=(f.source_inspections or 0)+1
            if id==GUARDED or id==SECOND_GUARDED then
                assert(options.physical_only,"GUARDED_SOURCE_INSPECTED_PHYSICALLY")
            end
            assert(id==PHYSICAL or (options.physical_only and id==GUARDED))
            return {identity=string.rep("d",64),source_id=id,exact_matches=1,
                quantity=1,payload=payload(id==GUARDED and "flpri1" or "flpri2")}
        end
        function native:inspect_target(id)
            f.target_inspections=(f.target_inspections or 0)+1
            return {identity=string.rep("e",64),target_id=id,accepts=1}
        end
        function native:execute(command)
            assert(options.physical_only or
                (command.source_id~=GUARDED and command.source_id~=SECOND_GUARDED) or command.kind~="suppress",
                "GUARDED_SOURCE_EXECUTED_PHYSICALLY")
            if command.kind=="deliver" then
                assert(f.values.flpr_s0002==(options.two_guarded and 6 or 3),
                    "SECOND_SOURCE_NOT_READY")
                assert(f.values.flpr_s0001==(options.physical_only and 3 or 6),
                    "GUARDED_SOURCE_NOT_PROTECTED")
            end
            f.events[#f.events+1]={kind=command.kind,source_id=command.source_id,
                tick=f.tick,nonce=command.nonce}
            self.pending=command
            return true
        end
        function native:observe(command)
            assert(self.pending==command)
            local receipt=copy(command); receipt.session=command.session
            if command.kind=="suppress" then receipt.removed=1; receipt.remaining=0
            else receipt.created=1; receipt.nonce_bound_instance=1; receipt.lootable=1 end
            self.pending=nil
            return receipt
        end
        function native:destroy() self.destroyed=true; self.pending=nil end
        return native
    end
    local function guard_factory(bundle)
        local adapter={bundle=bundle}
        f.guards[#f.guards+1]=adapter
        function adapter:observe_guard(request)
            f.guard_observations=(f.guard_observations or 0)+1
            assert(request.kind=="source-guard" and request.mode=="observe-only")
            local index=request.source_id==GUARDED and 1 or request.source_id==SECOND_GUARDED and 2
            assert(index and (options.two_guarded or index==1) and request.revision==bundle.manifest.revision)
            assert(request.resource_sha256==bundle.manifest.sources[index].resource_sha256)
            if f.guard_observations_by_id then
                f.guard_observations_by_id[request.source_id]=(f.guard_observations_by_id[request.source_id] or 0)+1
            end
            local receipt=copy(request)
            receipt.session=request.session; receipt.token=request.token
            receipt.pre_access=1; receipt.exact_source_bound=1
            receipt.generation=#f.guards
            receipt.active=function()
                if f.lease_error then error("LEASE_THROW") end
                if f.lease_active_by_id then
                    return not adapter.destroyed and f.lease_active_by_id[request.source_id]
                end
                return not adapter.destroyed and f.lease_active
            end
            return receipt
        end
        function adapter:observe_removal(request)
            f.removal_observations=(f.removal_observations or 0)+1
            local index=request.source_id==GUARDED and 1 or request.source_id==SECOND_GUARDED and 2
            assert(request.kind=="guarded-source-removal" and index and (options.two_guarded or index==1))
            if f.removal_observations_by_id then
                f.removal_observations_by_id[request.source_id]=(f.removal_observations_by_id[request.source_id] or 0)+1
            end
            local loaded=f.loaded
            if f.loaded_by_id then loaded=f.loaded_by_id[request.source_id] end
            if not loaded then
                return nil,"SOURCE_NOT_LOADED"
            end
            local receipt=copy(request)
            receipt.session=request.session; receipt.token=request.token
            receipt.exact_source_event=1; receipt.removed=1; receipt.remaining=0
            return receipt
        end
        function adapter:destroy() self.destroyed=true end
        return adapter
    end
    f.deps={core=Core,storage=f.storage,
        resolve=function(id) return f.bundles[id] end,
        next=function() f.next_calls=f.next_calls+1; return f.bundles[f.next_revision] end,
        seed=function() f.seeds=f.seeds+1; return 1000+f.seeds end,
        native=native_factory,source_guard=guard_factory,
        prepare_source_guard=function(bundle,controller)
            f.prepares[#f.prepares+1]={bundle=bundle,state=f.values.flpr_state,
                fresh=f.values.FLPR_FRESH,controller=controller}
            if options.prepare_failure then return nil,"PREPARE_FAILED" end
            return true
        end,
        world=function() return f.world end,tick=function() return f.tick end,
        report=function(status,reason) f.reports[#f.reports+1]={status,reason} end}
    if options.missing_prepare then f.deps.prepare_source_guard=nil end
    if options.missing_source_guard then f.deps.source_guard=nil end
    f.host=Host.new(f.deps)
    function f:capture()
        self.values.FLPR_FRESH=1
        return self.host:capture_new("confirmed-character-accept")
    end
    function f:poll()
        self.tick=self.tick+1
        self.host:poll()
    end
    function f:poll_until(predicate)
        for _=1,30 do
            self:poll()
            if predicate(self) then return end
            if self.host.status=="disabled" then break end
        end
        error("POLL_CONDITION_UNMET:"..tostring(self.host.error))
    end
    return f
end

function cases.capture_prepares_guard_after_commit_before_marker_consumption(Core,Host)
    local f=setup(Core,Host)
    assert(f:capture())
    assert(#f.prepares==1 and f.prepares[1].bundle==f.bundle)
    assert(f.prepares[1].state==2 and f.prepares[1].fresh==1)
    assert(f.values.FLPR_FRESH==0 and f.seeds==1 and f.next_calls==1)
    assert(f.host:capture_new("confirmed-character-accept"))
    assert(#f.prepares==1 and f.seeds==1 and f.next_calls==1)
end

function cases.missing_preparer_rejects_before_capture(Core,Host)
    local f=setup(Core,Host,{missing_prepare=true})
    assert(not f:capture() and f.host.status=="disabled")
    assert(f.values.flpr_state==nil and f.values.FLPR_FRESH==1)
    assert(f.seeds==0 and f.next_calls==1 and #f.events==0)
end

-- Capture is atomic: a failed guard preparation leaves no committed campaign
-- (the player may start this adventure unrandomised), and a retry draws anew.
function cases.failed_prepare_rolls_back_and_a_retry_captures_again(Core,Host)
    local f=setup(Core,Host,{prepare_failure=true})
    assert(not f:capture() and f.host.status=="disabled" and f.host.error=="PREPARE_FAILED")
    assert(f.values.flpr_state==0 and f.values.FLPR_FRESH==1 and f.storage.undo==nil)
    assert(f.prepares[1].state==2 and f.seeds==1 and f.next_calls==1 and #f.events==0)
    assert(not f.host:capture_new("confirmed-character-accept"))
    assert(f.values.flpr_state==0 and f.seeds==2 and f.next_calls==2 and #f.prepares==2)
end

function cases.malformed_descriptor_or_missing_adapter_fails_closed(Core,Host)
    for _,option in ipairs({"bad_schema","bad_id","bad_endpoint",
        "bad_creature_endpoint","missing_source_guard"}) do
        local f=setup(Core,Host,{[option]=true})
        local captured=f:capture()
        if captured then f:poll_until(function(x) return x.host.status=="disabled" end) end
        assert(f.host.status=="disabled",option)
        assert(#f.events==0 and f.next_calls==1)
        if option=="missing_source_guard" then
            assert(f.values.flpr_state==2 and f.seeds==1)
        else assert(f.values.flpr_state==nil and f.seeds==0) end
    end
end

function cases.guarded_source_is_protected_before_any_delivery_and_original_unloaded(Core,Host)
    local f=setup(Core,Host); assert(f:capture())
    f:poll_until(function(x) return x.host.status=="complete" end)
    assert(f.values.flpr_s0001==6 and f.values.flpr_s0002==3)
    assert(f.values.flpr_d0001==3 and f.values.flpr_d0002==3)
    assert(not f.loaded and #f.guards==1 and (f.guard_observations or 0)>=1)
    assert(f.source_inspections==1)
    assert(#f.events==3 and f.events[1].kind=="suppress" and f.events[1].source_id==PHYSICAL)
    assert(f.events[2].kind=="deliver" and f.events[3].kind=="deliver")
    assert(#f.reports==1 and f.reports[1][1]=="complete")
    assert(f.host.guard_monitor==true)
    local targets=f.target_inspections
    for _=1,4 do f:poll() end
    assert(#f.events==3 and #f.reports==1 and f.values.flpr_s0001==6)
    assert(f.target_inspections==targets)
end

function cases.guarded_creature_delivers_before_visit_and_restores_without_reroll(Core,Host)
    for _,kind in ipairs({"cre-template","embedded-cre"}) do
        local f=setup(Core,Host,{creature_kind=kind}); assert(f:capture())
        f:poll_until(function(x) return x.host.status=="complete" end)
        assert(not f.loaded and f.values.flpr_s0001==6)
        assert(f.values.flpr_d0001==3 and f.values.flpr_d0002==3)
        assert(#f.events==3 and f.events[1].kind=="suppress")
        assert(f.events[2].kind=="deliver" and f.events[3].kind=="deliver")
        assert(f.events[2].tick < 30 and f.host.guard_monitor==true)

        local old=f.guards[1]
        f.host:destroy(); assert(old.destroyed)
        f.host=Host.new(f.deps)
        f:poll_until(function(x) return x.host.status=="complete" and #x.guards==2 end)
        assert(not f.loaded and f.values.flpr_s0001==6)
        assert(f.values.flpr_d0001==3 and f.values.flpr_d0002==3)
        assert(f.seeds==1 and f.next_calls==1 and #f.events==3)
        assert(f.host.guard_monitor==true)
    end
end

function cases.complete_monitor_later_commits_only_exact_source_removal(Core,Host)
    local f=setup(Core,Host); assert(f:capture())
    f:poll_until(function(x) return x.host.status=="complete" end)
    local events=#f.events
    f.loaded=true
    f:poll_until(function(x) return x.values.flpr_s0001==3 end)
    assert(f.host.status=="complete" and #f.events==events and #f.reports==1)
    assert((f.removal_observations or 0)>=1 and f.host.guard_monitor==false)
end

function cases.two_guards_keep_independent_removal_and_lease_state_across_reload(Core,Host)
    local f=setup(Core,Host,{two_guarded=true}); assert(f:capture())
    f:poll_until(function(x) return x.host.status=="complete" end)
    assert(f.values.flpr_s0001==6 and f.values.flpr_s0002==6)
    assert(f.values.flpr_d0001==3 and f.values.flpr_d0002==3)
    assert(f.source_inspections==nil and #f.events==2)
    assert(f.events[1].kind=="deliver" and f.events[2].kind=="deliver")
    assert(f.guard_observations_by_id[GUARDED]==1 and
        f.guard_observations_by_id[SECOND_GUARDED]==1)
    assert(not f.loaded_by_id[GUARDED] and not f.loaded_by_id[SECOND_GUARDED])
    assert(f.host.guard_monitor==true)

    f.loaded_by_id[SECOND_GUARDED]=true
    f:poll_until(function(x) return x.values.flpr_s0002==3 end)
    assert(f.values.flpr_s0001==6 and f.host.status=="complete")
    assert(f.host.guard_monitor==true and #f.events==2)
    assert((f.removal_observations_by_id[SECOND_GUARDED] or 0)>=1)
    f.lease_active_by_id[SECOND_GUARDED]=false
    for _=1,3 do f:poll() end
    assert(f.host.status=="complete" and f.values.flpr_s0001==6)

    local first_observations=f.guard_observations_by_id[GUARDED]
    local second_observations=f.guard_observations_by_id[SECOND_GUARDED]
    local old=f.guards[1]
    f.host:destroy()
    assert(old.destroyed)
    f.host=Host.new(f.deps)
    f:poll_until(function(x) return x.host.status=="complete" and #x.guards==2 end)
    assert(f.values.flpr_s0001==6 and f.values.flpr_s0002==3)
    assert(f.guard_observations_by_id[GUARDED]==first_observations+1,
        tostring(f.guard_observations_by_id[GUARDED]).." vs "..tostring(first_observations))
    assert(f.guard_observations_by_id[SECOND_GUARDED]==second_observations)
    assert(f.host.guard_monitor==true and #f.events==2)

    f.lease_active_by_id[GUARDED]=false
    f:poll_until(function(x) return x.host.status=="disabled" end)
    assert(#f.events==2 and f.values.flpr_s0001==6 and f.values.flpr_s0002==3)
end

function cases.protected_restore_rebinds_frozen_revision_with_fresh_adapter(Core,Host)
    local f=setup(Core,Host); assert(f:capture())
    f:poll_until(function(x) return x.host.status=="complete" end)
    local old=f.guards[1]
    f.host:destroy()
    assert(old.destroyed and f.values.flpr_s0001==6)
    local new_revision=string.rep("f",64)
    local new_manifest=copy(f.manifest); new_manifest.revision=new_revision
    f.bundles[new_revision]={manifest=new_manifest,fixture={revision=new_revision}}
    f.next_revision=new_revision
    f.host=Host.new(f.deps)
    f:poll_until(function(x) return x.host.status=="complete" and #x.guards==2 end)
    assert(f.guards[2]~=old and f.guards[2].bundle==f.bundle)
    assert(f.seeds==1 and f.next_calls==1 and f.values.flpr_s0001==6)
    assert(#f.events==3)
end

function cases.revoked_or_throwing_guard_disables_without_rebind(Core,Host)
    for _,failure in ipairs({"false","throw"}) do
        local f=setup(Core,Host); assert(f:capture())
        f:poll_until(function(x) return x.values.flpr_s0001==6 end)
        if failure=="throw" then f.lease_error=true else f.lease_active=false end
        f:poll_until(function(x) return x.host.status=="disabled" end)
        local count=#f.events
        f.lease_active=true
        for _=1,5 do f:poll() end
        assert(f.host.status=="disabled" and #f.guards==1 and #f.events==count)
    end
end

function cases.completed_host_still_detects_guard_loss(Core,Host)
    local f=setup(Core,Host); assert(f:capture())
    f:poll_until(function(x) return x.host.status=="complete" end)
    f.lease_active=false
    f:poll_until(function(x) return x.host.status=="disabled" end)
    assert(#f.guards==1 and #f.events==3 and f.values.flpr_s0001==6)
end

function cases.physically_confirmed_guarded_source_restores_without_rebind(Core,Host)
    local f=setup(Core,Host); assert(f:capture())
    f:poll_until(function(x) return x.host.status=="complete" end)
    f.loaded=true
    f:poll_until(function(x) return x.values.flpr_s0001==3 end)
    f.host:destroy()
    f.host=Host.new(f.deps)
    f:poll_until(function(x) return x.host.status=="complete" end)
    assert(#f.guards==2 and (f.guard_observations or 0)==1)
    assert(f.values.flpr_s0001==3 and f.host.guard_monitor==false)
    assert(#f.events==3)
end

function cases.pending_delivery_reload_never_replays(Core,Host)
    local f=setup(Core,Host); assert(f:capture())
    f:poll_until(function(x) return x.values.flpr_jkind==2 end)
    local count=#f.events
    f.host:destroy()
    f.host=Host.new(f.deps)
    f:poll_until(function(x) return x.host.status=="disabled" end)
    assert(#f.events==count and f.values.flpr_jkind==2)
end

function cases.ordinary_physical_campaign_unchanged(Core,Host)
    local f=setup(Core,Host,{physical_only=true,missing_prepare=true,missing_source_guard=true})
    assert(f:capture())
    f:poll_until(function(x) return x.host.status=="complete" end)
    assert(f.values.flpr_s0001==3 and f.values.flpr_s0002==3)
    assert(#f.events==4 and f.events[1].kind=="suppress" and f.events[2].kind=="suppress")
    assert(#f.guards==0 and #f.prepares==0)
end

function cases.kept_guarded_source_requests_no_guard_removal_or_delivery(Core,Host)
    local f=setup(Core,Host)
    local target=f.manifest.targets[1].id
    f.manifest.randomization={loss_percent=0,unrandomized_percent=100,
        sources={
            [GUARDED]={tier="low",loss_exempt=false,exclude_targets={{id=target,ordinal=1}}},
            [PHYSICAL]={tier="low",loss_exempt=false,exclude_targets={{id=target,ordinal=2}}},
        },targets={[target]={"low","low"}}}
    assert(f:capture())
    f:poll_until(function(x) return x.host.status=="complete" end)
    for _,assignment in ipairs(f.host.controller:assignments()) do
        assert(assignment.source_phase==Core.PHASE.kept and assignment.delivery_phase==Core.PHASE.kept)
    end
    assert(#f.events==0 and not f.guard_observations and not f.removal_observations)
    assert(not f.source_inspections and not f.target_inspections)
    f.host:destroy(); f.host=Host.new(f.deps)
    f:poll_until(function(x) return x.host.status=="complete" end)
    assert(#f.events==0 and not f.guard_observations and not f.removal_observations)
end

function cases.full_preset_guards_batch_across_one_boundary_and_fail_closed(Core,Host)
    for _,failure_index in ipairs({0,154}) do
        local f=setup(Core,Host,{two_guarded=true})
        local count=302
        f.manifest.sources={}; f.manifest.targets[1].capacity=count
        f.bundle.fixture.sources={}; f.bundle.fixture.source_gate.source_ids={}
        for index=1,count do
            local id=string.format('are:batch/container/%04d/item/0',index)
            f.manifest.sources[index]={id=id,quantity=1,resource_sha256=string.rep('1',64),
                record={id=id,kind='container',record=payload('flpri1')}}
            f.bundle.fixture.sources[id]={kind='guarded-container'}
            f.bundle.fixture.source_gate.source_ids[index]=id
        end
        local function receipt(request,adapter)
            local result=copy(request); result.session=request.session; result.token=request.token
            result.pre_access=1; result.exact_source_bound=1; result.generation=1
            result.active=function() return not adapter.destroyed end
            return result
        end
        f.deps.source_guard=function()
            local adapter={}
            function adapter:observe_guard(request)
                assert(f.host.controller.boundary==f.request_boundary+1,'GUARD_BOUNDARY_CHANGED')
                f.guard_observations=(f.guard_observations or 0)+1
                if request.index==failure_index then return nil,'GUARD_BATCH_FAILURE' end
                return receipt(request,self)
            end
            function adapter:observe_removal()
                f.removal_observations=(f.removal_observations or 0)+1
                return nil,'SOURCE_NOT_LOADED'
            end
            function adapter:destroy() self.destroyed=true end
            return adapter
        end
        local native_factory=f.deps.native
        f.deps.native=function(fixture)
            local native=native_factory(fixture)
            function native:inspect_target()
                for index=1,count do
                    assert(f.host.controller:source_guard_active(index),'REWARD_BEFORE_ALL_GUARDS')
                end
                f.target_inspections=(f.target_inspections or 0)+1
                return nil,'AREA_UNAVAILABLE'
            end
            return native
        end
        assert(f:capture())
        local seed=f.values.flpr_seed
        for reload=1,(failure_index==0 and 2 or 1) do
            f:poll(); f:poll() -- First advancing boundary issues the complete batch.
            assert(f.host.status=='running',tostring(f.host.error))
            assert(#f.host.guard_pending==count and #f.events==0 and not f.host.pending)
            f.request_boundary=f.host.controller.boundary
            for index=1,count do
                assert(f.host.controller.guard_requests[index].boundary==f.request_boundary)
                assert(not f.host.guarded[index].removal_pending)
            end
            local request=f.host.guard_pending[1]
            local ok,reason=f.host.controller:confirm_source_guard(receipt(request,f.host.guard))
            assert(ok==nil and reason=='LATER_OBSERVATION_REQUIRED')
            local observations=f.guard_observations or 0
            local inspections=f.target_inspections or 0
            f.host:poll() -- Same engine tick cannot consume the batch.
            assert((f.guard_observations or 0)==observations and #f.host.guard_pending==count)
            f:poll() -- Every confirmation is on this later boundary, with no delivery.
            assert(#f.events==0 and not f.host.pending and f.values.flpr_jkind==0)
            assert((f.target_inspections or 0)==inspections and not f.source_inspections)
            if failure_index~=0 then
                assert(f.host.status=='disabled' and f.host.error=='GUARD_BATCH_FAILURE')
                assert(f.guard_observations==failure_index)
                for _=1,3 do f:poll() end
                assert(f.guard_observations==failure_index and #f.events==0)
            else
                assert(f.host.status=='running' and not f.host.guard_pending)
                assert(f.guard_observations==observations+count)
                for index=1,count do
                    assert(f.host.guarded[index].bound and f.host.controller:source_guard_active(index))
                    assert(not f.host.guarded[index].removal_pending)
                end
                f:poll() -- Removal observations are requested only after registration.
                assert(f.target_inspections==inspections+count and #f.events==0)
                for index=1,count do assert(f.host.guarded[index].removal_pending) end
                assert(f.values.flpr_seed==seed and f.seeds==1 and f.next_calls==1)
                f.host:destroy(); f.host=Host.new(f.deps)
            end
        end
    end
end

return cases
'''


class GuardHostTests(unittest.TestCase):
    def test_lua_cases(self):
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime(unpack_returned_tuples=True)
            core, host, cases = lua.execute(CORE), lua.execute(HOST), lua.execute(CASES)
            for name in sorted(cases.keys()):
                with self.subTest(runtime=runtime.__module__, case=name):
                    cases[name](core, host)


if __name__ == "__main__":
    unittest.main()
