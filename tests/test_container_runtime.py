"""Offline container quest observation uses saved delivery and scoped latch state."""
from pathlib import Path
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT

from test_campaign_native_creature import CASES as NATIVE_FIXTURE
from test_campaign_native_host import CORE, HOST
from test_campaign_guard_host import CASES as GUARD_FIXTURE

ROOT = Path(__file__).resolve().parents[1]
NATIVE = (ROOT / "prototype/FLPRNat.lua").read_text(encoding="utf-8")
CASES = r'''
local function container_world(N,scope)
    local f=setup(N)
    f.globals={FLPR_CSAMIA=1};f.area_values={};f.writes={}
    f.area.m_resref={get=function() return 'AR1202' end}
    f.container={address=201,sprite=false,m_lstItems={},m_containerType=2,
        m_pos={x=598,y=1228},m_scriptName={get=function() return 'Container 1' end}}
    f.objects[3]=f.container
    f.target_id='original-target:bg2:mode2:target:53'
    f.fixture.targets={[f.target_id]={kind='container',area='AR1202',script_name='Container 1',
        x=598,y=1228,container_type=2,identity=string.rep('c',64),loot_latch={
            activation_global='FLPR_CSAMIA',latch_global='flSamiaTreasure',latch_scope=scope or 'AR1202'}}}
    f.api.EEex_GameState_GetGlobalInt=function(key) return f.globals[key] or 0 end
    f.api.EEex_GameState_SetGlobalInt=function(key,value)
        f.globals[key]=value;f.writes[#f.writes+1]={'GLOBAL',key,value}
    end
    f.api.EEex_Area_GetVariableInt=function(area,key)
        assert(area==f.area);return f.area_values[key] or 0
    end
    f.api.EEex_Area_SetVariableInt=function(area,key,value)
        assert(area==f.area);f.area_values[key]=value;f.writes[#f.writes+1]={'AR1202',key,value}
    end
    f.assignment={unit=1,source_id=f.source_ids[1],target_id=f.target_id,delivery_phase=2}
    function f:reload() self.adapter=N.new(self.api,self.fixture,17,self.reward_payload) end
    function f:observe(assignments)
        local ok,monitor=self.adapter:observe_container_latches(assignments or {self.assignment},2)
        assert(ok);return monitor
    end
    function f:latched() return (scope=='GLOBAL' and self.globals or self.area_values).flSamiaTreasure end
    f:reload()
    return f
end

function cases.only_confirmed_active_assignments_can_latch(N,Core)
    local f=container_world(N)
    for _,phase in pairs(Core.PHASE) do
        if phase~=Core.PHASE.confirmed then
            f.assignment.delivery_phase=phase;assert(not f:observe())
        end
    end
    assert(not f:observe({}) and #f.writes==0)
    f.assignment.delivery_phase=Core.PHASE.confirmed
    f.assignment.target_id=nil;assert(not f:observe())
    f.assignment.target_id=f.target_id
    f.globals.FLPR_CSAMIA=0;assert(not f:observe() and #f.writes==0)
    f.globals.FLPR_CSAMIA=1
    f.adapter.pending={};assert(f:observe() and #f.writes==0)
    f.adapter.pending=nil
    -- Resref presence alone matters: changed charges/flags do not mean looted.
    local reward=f.item('FLPRI1',400,3);reward.m_useCount1=7
    f.container.m_lstItems={reward};assert(f:observe() and #f.writes==0)
    f.container.m_lstItems={f.item('OTHER',401)}
    assert(not f:observe() and f:latched()==1 and #f.writes==1)
    assert(f.container.m_lstItems[1].address==401)
end

function cases.unloaded_missing_ambiguous_or_wrong_container_is_not_empty(N)
    local f=container_world(N);local area=f.area
    f.area=nil;assert(f:observe() and #f.writes==0)
    f.area={m_resref={get=function() return 'AR1303' end}}
    assert(f:observe() and #f.writes==0)
    f.area=area;f.objects[3]=nil;assert(f:observe() and #f.writes==0)
    f.objects[3]=f.container;f.container.m_pos.x=600
    assert(f:observe() and #f.writes==0)
    f.container.m_pos.x=598
    local duplicate=clone(f.container);duplicate.address=202
    f.objects[4]=duplicate;area.m_lVertSortBack={4}
    assert(f:observe() and #f.writes==0)
    area.m_lVertSortBack={}
    assert(not f:observe() and f:latched()==1)
end

function cases.scoped_latches_persist_across_reload_reentry_and_item_return(N)
    for _,scope in ipairs({'GLOBAL','AR1202'}) do
        local f=container_world(N,scope)
        f.container.m_lstItems={f.item('FLPRI1',400)}
        f:reload();assert(f:observe())
        f.container.m_lstItems={};assert(not f:observe() and f:latched()==1)
        assert(f.writes[1][1]==scope and #f.writes==1)
        f.container.m_lstItems={f.item('FLPRI1',401)}
        f.adapter:destroy();f:reload()
        assert(not f:observe() and f:latched()==1 and #f.writes==1)
        local area=f.area;f.area=nil
        assert(not f:observe()) -- Session proof carries over after leaving.
        f.area=area;assert(not f:observe() and #f.writes==1)
    end
end

function cases.samia_either_slot_and_selected_reward_variant_suffice(N)
    local f=container_world(N)
    local second='original-target:bg2:mode2:target:28'
    f.fixture.targets[second]=clone(f.fixture.targets[f.target_id])
    f.fixture.targets[second].identity=string.rep('d',64)
    f:reload()
    f.container.m_lstItems={f.item('FLPRI1',400)}
    local assignments={f.assignment,{unit=2,source_id=f.source_ids[2],target_id=second,delivery_phase=2}}
    assert(not f:observe(assignments) and #f.writes==1)
    -- The placement's chosen variant, not the source's old item, is watched.
    f=container_world(N)
    f.reward_payload=function(unit)
        assert(unit==1);return {resref='BOOK04',expiration=0,charges={0,0,0},flags=0}
    end
    f:reload();f.container.m_lstItems={f.item('BOOK04',401)}
    assert(f:observe() and #f.writes==0)
    f.container.m_lstItems={f.item('FLPRI1',402)}
    assert(not f:observe() and #f.writes==1)
end

function cases.host_keeps_observing_after_completion_and_restores_saved_latch(N,Core,Host)
    local f=container_world(N)
    f.globals={};f.fixture.sources[f.source_ids[2]]=nil
    f.fixture.sources[f.source_ids[1]].area='AR1202'
    local source_id=f.source_ids[1]
    local manifest={schema=Core.SCHEMA,state='prototype-not-playable',revision=string.rep('a',64),
        bound_revision=string.rep('b',64),world_fingerprint=string.rep('c',64),profile_id='container-test',
        sources={{id=source_id,quantity=1,resource_sha256=string.rep('1',64),record={
            id=source_id,record=f.fixture.sources[source_id].payload}}},
        targets={{id=f.target_id,capacity=1,resource_sha256=string.rep('2',64),record={id=f.target_id}}}}
    local bundle={manifest=manifest,fixture=f.fixture}
    local original_action=f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly
    f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly=function(action,owner)
        local ref=action:match('^CreateItem%("([^"]+)",0,0,0%)$')
        if not ref then return original_action(action,owner) end
        assert(owner==f.container);owner.m_lstItems[#owner.m_lstItems+1]=f.item(ref,500)
    end
    local storage={get=function(_,key) return f.globals[key] or 0 end,
        set=function(_,key,value) f.globals[key]=value end}
    local tick,prepared,observations=0,0,0
    local deps={core=Core,storage=storage,resolve=function() return bundle end,
        next=function() return bundle end,seed=function() return 17 end,
        world=function() return true end,tick=function() return tick end,
        prepare_campaign=function(selected,controller)
            assert(selected==bundle and controller:assignments()[1].target_id==f.target_id)
            assert(storage:get(Core.G.state)==2)
            prepared=prepared+1;f.globals.FLPR_CSAMIA=1;return true
        end,
        native=function(fixture)
            f.adapter=N.new(f.api,fixture)
            local observe=f.adapter.observe_container_latches
            f.adapter.observe_container_latches=function(self,assignments,confirmed)
                assert(not f.host.pending and confirmed==Core.PHASE.confirmed)
                observations=observations+1;return observe(self,assignments,confirmed)
            end
            return f.adapter
        end}
    f.host=Host.new(deps);f.globals.FLPR_FRESH=1
    assert(f.host:capture_new('confirmed-bg1-character-accept') and prepared==1)
    assert(f.host:capture_new('confirmed-bg1-character-accept') and prepared==1)
    local function poll() tick=tick+1;f.host:poll();assert(f.host.status~='disabled',f.host.error) end
    for _=1,12 do poll() end
    assert(f.host.status=='complete' and f.host:inspect().container_latch_monitor)
    assert(f.host.controller:assignments()[1].delivery_phase==Core.PHASE.confirmed)
    assert(#f.container.m_lstItems==1 and #f.writes==0)
    local area=f.area;f.area=nil
    for _=1,3 do poll() end
    assert(f.host.status=='complete' and f.host.container_latch_monitor and #f.writes==0)
    f.area=area;f.container.m_lstItems={};poll()
    assert(f:latched()==1 and not f.host.container_latch_monitor and #f.writes==1)
    local count=observations;poll();assert(observations==count)
    f.host:destroy();f.host=Host.new(deps)
    poll();poll()
    assert(f.host.status=='complete' and not f.host.container_latch_monitor)
    assert(prepared==1 and #f.writes==1 and #f.container.m_lstItems==0)
end
'''


class ContainerRuntimeTests(unittest.TestCase):
    def test_container_observation_and_host_lifecycle(self):
        names = (
            "only_confirmed_active_assignments_can_latch",
            "unloaded_missing_ambiguous_or_wrong_container_is_not_empty",
            "scoped_latches_persist_across_reload_reentry_and_item_return",
            "samia_either_slot_and_selected_reward_variant_suffice",
            "host_keeps_observing_after_completion_and_restores_saved_latch",
        )
        code = NATIVE_FIXTURE.rsplit("return cases", 1)[0] + CASES + "\nreturn cases"
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime(unpack_returned_tuples=True)
            cases = lua.execute(code)
            for name in names:
                with self.subTest(runtime=runtime.__module__, case=name):
                    cases[name](lua.execute(NATIVE), lua.execute(CORE), lua.execute(HOST))

    def test_capture_prepares_activation_before_source_guard_and_checks_failure(self):
        code = GUARD_FIXTURE.rsplit("return cases", 1)[0] + r'''
return function(Core,Host,fail_prepare)
    local f=setup(Core,Host)
    local order={}
    local guard=f.deps.prepare_source_guard
    f.deps.prepare_campaign=function(bundle,controller)
        assert(bundle==f.bundle and #controller:assignments()==2 and f.values.flpr_state==2)
        order[#order+1]='campaign'
        if fail_prepare then return nil,'ACTIVATION_FAILED' end
        return true
    end
    f.deps.prepare_source_guard=function(bundle,controller)
        assert(order[1]=='campaign');order[#order+1]='guard'
        return guard(bundle,controller)
    end
    local ok,reason=f:capture()
    if fail_prepare then
        assert(not ok and reason=='ACTIVATION_FAILED' and #order==1 and f.host.status=='disabled')
    else
        assert(ok and #order==2 and order[2]=='guard')
        assert(f.host:capture_new('confirmed-bg1-character-accept') and #order==2)
    end
end
'''
        for runtime in (Lua51, Lua53, LuaJIT):
            for fail_prepare in (False, True):
                lua = runtime(unpack_returned_tuples=True)
                with self.subTest(runtime=runtime.__module__, failure=fail_prepare):
                    lua.execute(code)(lua.execute(CORE), lua.execute(HOST), fail_prepare)


if __name__ == "__main__":
    unittest.main()
