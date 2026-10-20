"""A campaign step costs little, however many sources the campaign guards.

Real Core and Host with many guarded sources, numeric save GLOBALs that count
engine reads, fake world and adapters; no engine. Before 1.0.2 every engine tick
read each unit's GLOBALs and proved every guarded source's lease: about 125 ms a
tick in a 565-unit campaign on aShuN's modded BG:EE, which slowed the game to a
crawl from its start.
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
local function copy(value)
    if type(value)~="table" then return value end
    local result={}
    for key,item in pairs(value) do result[key]=copy(item) end
    return result
end
local function payload(ref)
    return {resref=ref,charges={0,0,0},expiration=0,flags=1}
end
local function source_id(index) return string.format("are:flpru1/container/0/item/%03d",index) end

-- count guarded container sources, all delivered to one container elsewhere.
local function setup(Core,Host,count)
    local f={values={},reads=0,tick=0,lease={},loaded={},proofs={},removals={},reports={}}
    local revision=string.rep("a",64)
    local manifest={schema=Core.SCHEMA,state="prototype-not-playable",revision=revision,
        bound_revision=string.rep("b",64),world_fingerprint=string.rep("c",64),
        profile_id="step-cost-synthetic",sources={},targets={}}
    local fixture={revision=revision,sources={},
        source_gate={schema="flpr-unvisited-fixture-gate-v1",source_ids={}}}
    for index=1,count do
        local id=source_id(index)
        manifest.sources[index]={id=id,quantity=1,resource_sha256=string.rep("1",64),
            record={id=id,kind="container",record=payload(string.format("flpr%03d",index))}}
        fixture.sources[id]={kind="guarded-container"}
        fixture.source_gate.source_ids[index]=id
        f.lease[id]=true
    end
    manifest.targets[1]={id="are:flpru2/container/1",capacity=count,
        resource_sha256=string.rep("3",64),record={id="are:flpru2/container/1",kind="container"}}
    f.manifest,f.bundle=manifest,{manifest=manifest,fixture=fixture}
    f.storage={}
    function f.storage:get(key) f.reads=f.reads+1; return f.values[key] or 0 end
    function f.storage:set(key,value)
        if self.undo and self.undo.previous[key]==nil then
            self.undo.previous[key]=f.values[key] or 0; self.undo.order[#self.undo.order+1]=key
        end
        f.values[key]=value
    end
    function f.storage:begin() assert(not self.undo); self.undo={previous={},order={}} end
    function f.storage:commit() assert(self.undo); self.undo=nil end
    function f.storage:rollback()
        local undo=self.undo; self.undo=nil
        for i=#undo.order,1,-1 do f.values[undo.order[i]]=undo.previous[undo.order[i]] end
    end
    local function native_factory()
        local native={}
        function native:on_observation_boundary() return true end
        -- The recipient's area is never loaded: every delivery waits.
        function native:inspect_target() return nil,"AREA_UNAVAILABLE" end
        function native:destroy() end
        return native
    end
    local function guard_factory()
        local adapter={}
        function adapter:observe_guard(request)
            local receipt=copy(request)
            receipt.session=request.session; receipt.token=request.token
            receipt.pre_access=1; receipt.exact_source_bound=1; receipt.generation=1
            receipt.active=function()
                f.proofs[request.index]=(f.proofs[request.index] or 0)+1
                return not adapter.destroyed and f.lease[request.source_id]
            end
            return receipt
        end
        function adapter:observe_removal(request)
            f.removals[request.index]=(f.removals[request.index] or 0)+1
            if not f.loaded[request.source_id] then return nil,"SOURCE_NOT_LOADED" end
            local receipt=copy(request)
            receipt.session=request.session; receipt.token=request.token
            receipt.exact_source_event=1; receipt.removed=1; receipt.remaining=0
            return receipt
        end
        function adapter:destroy() self.destroyed=true end
        return adapter
    end
    f.deps={core=Core,storage=f.storage,
        resolve=function(id) return id==revision and f.bundle or nil end,
        next=function() return f.bundle end,seed=function() return 1234 end,
        native=native_factory,source_guard=guard_factory,
        prepare_source_guard=function() return true end,
        world=function() return true end,tick=function() return f.tick end,
        report=function(status,reason) f.reports[#f.reports+1]={status,reason} end}
    f.host=Host.new(f.deps)
    f.values.FLPR_FRESH=1
    assert(f.host:capture_new("confirmed-character-accept"))
    function f:poll() self.tick=self.tick+1; self.host:poll() end
    function f:counted(action)
        self.proofs,self.removals,self.reads={},{},0
        action()
        local proofs,removals=0,0
        for _,n in pairs(self.proofs) do proofs=proofs+n end
        for _,n in pairs(self.removals) do removals=removals+n end
        return proofs,removals,self.reads
    end
    -- Initialise, register and confirm every guard, then ask for every removal.
    for _=1,4 do f:poll() end
    assert(f.host.status=="running",tostring(f.host.error))
    for index=1,count do
        assert(f.values[string.format("flpr_s%04d",index)]==Core.PHASE.protected+1)
        assert(f.host.guarded[index].removal_pending)
    end
    return f
end

function cases.idle_step_reads_few_globals_and_proves_a_bounded_share(Core,Host)
    local f=setup(Core,Host,200)
    local built,assignments=0,f.host.controller.assignments
    f.host.controller.assignments=function(...) built=built+1; return assignments(...) end
    for _=1,10 do
        local proofs,removals,reads=f:counted(function() f:poll() end)
        assert(proofs<=Host.LEASE_CHECKS and removals<=Host.REMOVAL_CHECKS,proofs..'/'..removals)
        -- The state, the journal and a rotating share of the kept values.
        assert(reads<=2+Core.CACHE_CHECKS,tostring(reads))
    end
    assert(built==0 and f.host.status=="running")
end

function cases.every_lease_and_removal_is_observed_within_a_cycle(Core,Host)
    local count=40
    local f=setup(Core,Host,count)
    local steps=math.ceil(count/math.min(Host.LEASE_CHECKS,Host.REMOVAL_CHECKS))
    local proofs,removals={},{}
    for _=1,steps do
        f:counted(function() f:poll() end)
        for index,n in pairs(f.proofs) do proofs[index]=(proofs[index] or 0)+n end
        for index,n in pairs(f.removals) do removals[index]=(removals[index] or 0)+n end
    end
    for index=1,count do assert(proofs[index] and removals[index],tostring(index)) end
end

function cases.lapsed_lease_stops_the_host_within_a_cycle(Core,Host)
    local count=40
    for _,lapsed in ipairs({1,17,count}) do
        local f=setup(Core,Host,count)
        f.lease[source_id(lapsed)]=false
        for _=1,math.ceil(count/Host.LEASE_CHECKS) do
            f:poll()
            if f.host.status=="disabled" then break end
        end
        assert(f.host.status=="disabled" and f.host.error:find("SOURCE_GUARD_INACTIVE",1,true),
            tostring(f.host.error))
    end
end

function cases.loaded_source_is_confirmed_within_a_cycle(Core,Host)
    local count=40
    local f=setup(Core,Host,count)
    f.loaded[source_id(29)]=true
    for _=1,math.ceil(count/Host.REMOVAL_CHECKS) do f:poll() end
    assert(f.values.flpr_s0029==Core.PHASE.confirmed+1 and f.host.status=="running")
    assert(f.values.flpr_s0028==Core.PHASE.protected+1)
end

function cases.kept_values_need_no_engine_reads(Core,Host)
    local f=setup(Core,Host,20)
    local controller=f.host.controller
    local _,_,reads=f:counted(function()
        for _=1,3 do
            assert(#controller:assignments()==20)
            assert(controller:source_guard_active(7))
            assert(controller:reward_payload(3).resref=="flpr003")
        end
    end)
    assert(reads==0,tostring(reads))
end

function cases.writes_reach_the_save_and_change_the_state_version(Core,Host)
    local f=setup(Core,Host,20)
    local controller=f.host.controller
    local version=controller:state_version()
    f.loaded[source_id(1)]=true
    for _=1,2 do f:poll() end
    assert(f.values.flpr_s0001==Core.PHASE.confirmed+1)
    assert(controller:state_version()>version)
    assert(controller:assignments()[1].source_phase==Core.PHASE.confirmed)
end

function cases.value_changed_behind_the_controller_stops_the_campaign(Core,Host)
    for _,key in ipairs({"flpr_state","flpr_jkind","flpr_d0011","flpr_s0005","flpr_a0020","flpr_r16"}) do
        local f=setup(Core,Host,20)
        local kept=#f.host.controller.cache_order
        f.values[key]=(f.values[key] or 0)+1
        for _=1,math.ceil(kept/Core.CACHE_CHECKS)+1 do
            f:poll()
            if f.host.status=="disabled" then break end
        end
        assert(f.host.status=="disabled" and f.host.error:find("GLOBAL_CHANGED",1,true),
            key..': '..tostring(f.host.error))
    end
end

function cases.restore_reads_the_save_again(Core,Host)
    local f=setup(Core,Host,20)
    f.host:destroy()
    f.values.flpr_s0004=Core.PHASE.confirmed+1 -- a save from after that source's removal
    for _=1,2 do f:poll() end
    assert(f.host.status=="running" and f.host.controller:assignments()[4].source_phase==Core.PHASE.confirmed)
end

return cases
'''


class StepCostTests(unittest.TestCase):
    def test_lua_cases(self):
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime(unpack_returned_tuples=True)
            core, host, cases = lua.execute(CORE), lua.execute(HOST), lua.execute(CASES)
            for name in sorted(cases.keys()):
                with self.subTest(runtime=runtime.__module__, case=name):
                    cases[name](core, host)


if __name__ == "__main__":
    unittest.main()
