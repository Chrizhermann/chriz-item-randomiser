"""Offline Key source guard, exact recipient routing, and bootstrap integration."""
from pathlib import Path
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT

from test_campaign_native_creature import CASES as NATIVE_FIXTURE
from test_campaign_native_host import CASES as HOST_FIXTURE, CORE, HOST, GLOBALS, BOOTSTRAP

ROOT = Path(__file__).resolve().parents[1]
NATIVE = (ROOT / "prototype/FLPRNat.lua").read_text(encoding="utf-8")
SCRIPT = (ROOT / "prototype/FLPRScript.lua").read_text(encoding="utf-8")

SOURCE_CASE = r'''
return function(Script,kept)
    local id='original-source:bg2:mode2:item:69'
    local p={resref='ring41',expiration=0,charges={1,0,0},flags=0}
    local globals={FLPR_FRESH=1}
    local callback=function() end
    local api={FLPRKEY=callback,FLPR={Spell={callback=callback}},
        EEex_GameState_GetGlobalInt=function(key) return globals[key] or 0 end,
        EEex_GameState_SetGlobalInt=function(key,value) globals[key]=value end}
    local source={id=id,quantity=1,resource_sha256=string.rep('1',64),record={
        id=id,kind='authored-source',adapter_id='original-source-v1',resource='spl:spin621',record=p}}
    local endpoint={kind='guarded-script',adapter_id='spell-key-v1',quest='key',
        resource='spl:spin621',selected_global='FLPR_QKEY',consumed_global='FLPR_XKEY',payload=p}
    local bundle={manifest={revision=string.rep('a',64),sources={source}},fixture={sources={[id]=endpoint}}}
    local old=io.open
    io.open=function(path,mode)
        assert(path=='override/SPIN621.SPL' and mode=='rb')
        return {read=function() return 'PATCHED_SPELL' end,close=function() return true end}
    end
    local ok,member=pcall(Script.new,api,bundle,id,'PATCHED_SPELL')
    io.open=old
    assert(ok,member)
    member.installed=true
    assert(member:prepare_new(bundle,{manifest=bundle.manifest,source_is_kept=function() return kept end}))
    assert((globals.FLPR_QKEY or 0)==(kept and 0 or 1))
    assert(globals.FLPR_XKEY==nil)
    if kept then return true end
    local adapter=member:adapter(bundle)
    local request={kind='source-guard',mode='observe-only',index=1,revision=bundle.manifest.revision,
        source_id=id,resource_sha256=source.resource_sha256,quantity=1,payload=p,session={},token={}}
    local guard=assert(adapter:observe_guard(request))
    assert(guard.active())
    request.kind='guarded-source-removal';request.guard_generation=guard.generation
    local receipt,reason=adapter:observe_removal(request)
    assert(receipt==nil and reason=='SOURCE_NOT_LOADED')
    globals.FLPR_XKEY=1
    assert(adapter:observe_removal(request).exact_source_event==1)
    api.FLPR.Spell.last_error='callback failed'
    receipt,reason=adapter:observe_removal(request)
    assert(receipt==nil and reason=='CAMPAIGN_SPELL_CALLBACK_FAILED:callback failed')
    assert(not guard.active() and member:inspect().last_error==reason)
    member:reset()
    assert(globals.FLPR_QKEY==1 and globals.FLPR_XKEY==1)
    return true
end
'''

NATIVE_CASES = r'''
local function key_world(N,party)
    local f=setup(N)
    f.creature.m_id=3;f.creature.locals={FLPR_RKEY=1}
    f.creature.portrait=party and 2 or -1
    f.source1.m_id=1;f.source1.portrait=0
    f.party={[0]=f.source1};if party then f.party[2]=f.creature end
    f.claimed=0
    f.api.EEex_GameState_GetGlobalInt=function(key) assert(key=='FLPR_EKEY');return f.claimed end
    f.api.EEex_Sprite_GetInPortrait=function(index) return f.party[index] end
    f.api.EEex_Sprite_GetLocalInt=function(sprite,key) return sprite.locals and sprite.locals[key] or 0 end
    f.api.FLPRKEY=function() end
    f.api.FLPR={Spell={callback=f.api.FLPRKEY}}
    f.fixture.targets[f.target_id]={kind='quest',area='*',identity=string.rep('c',64),
        event_global='FLPR_EKEY',recipient_local='FLPR_RKEY'}
    f.adapter=N.new(f.api,f.fixture)
    return f
end
function cases.key_recipient_is_marked_actor_not_portrait_zero(N)
    for _,party in ipairs({true,false}) do
        local f=key_world(N,party)
        local ready,reason=f.adapter:inspect_target(f.target_id,f.source_ids[1],1)
        fail(ready,reason,'QUEST_NOT_CLAIMED')
        f.claimed=1
        -- Repeated list appearances and the party reference are the same actor.
        f.area.m_lVertSortUnder={3};f.area.m_lVertSortFlight={3}
        assert(f.adapter:inspect_target(f.target_id,f.source_ids[1],1))
        f.boundary();local command=f.command(1)
        assert(f.adapter:execute(command))
        assert(#f.queue==1 and f.queue[1].m_targetId==3)
        f.update();f.boundary()
        local receipt;receipt,reason=f.adapter:observe(command)
        fail(receipt,reason,'DELIVERY_SETTLING')
        f.boundary();receipt=assert(f.adapter:observe(command))
        if party then assert(receipt.lootable==1 and receipt.holder=='party' and receipt.held==nil)
        else assert(receipt.lootable==0 and receipt.holder=='creature' and receipt.held==1) end
        assert(f.creature.slots[18].cResRef:get()=='FLPRI1' and not f.source1.slots[18])
    end
end
function cases.key_recipient_marker_missing_duplicate_or_dead_never_falls_back(N)
    for _,variant in ipairs({'missing','duplicate','dead','error'}) do
        local f=key_world(N,true);f.claimed=1
        if variant=='missing' then f.creature.locals={}
        elseif variant=='duplicate' then f.source1.locals={FLPR_RKEY=1}
        elseif variant=='dead' then f.creature.alive=false
        else f.api.FLPR.Spell.last_error='callback failed' end
        local ready,reason=f.adapter:inspect_target(f.target_id,f.source_ids[1],1)
        local wanted={missing='OWNER_MISSING',duplicate='OWNER_AMBIGUOUS',dead='TARGET_NOT_ALIVE',
            error='CAMPAIGN_SPELL_CALLBACK_FAILED:callback failed'}
        fail(ready,reason,wanted[variant]);assert(#f.calls==0 and #f.queue==0)
    end
end
function cases.key_recipient_local_survives_reload_ids_and_party_reordering(N)
    local f=key_world(N,true);f.claimed=1
    f.creature.m_id=74;f.ids[f.creature]=74;f.objects[3]=nil;f.objects[74]=f.creature
    f.area.m_lVertSort={1,2,74};f.party[2]=nil;f.party[4]=f.creature;f.creature.portrait=4
    f.adapter=N.new(f.api,f.fixture)
    assert(f.adapter:inspect_target(f.target_id,f.source_ids[1],1))
    assert(f.adapter.reservations[string.rep('c',64)].owner.id==74)
end
'''

BOOTSTRAP_CASE = r'''
function cases.key_bootstrap_loads_spell_once_reads_spl_and_surfaces_error(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text)
    local previous=loadfile
    local installs,opened=0,0
    local id='original-source:bg2:mode2:item:69'
    f.manifest.sources[1].id=id;f.manifest.sources[1].record.id=id
    f.native_fixture.sources={[id]={kind='guarded-script',adapter_id='spell-key-v1',
        payload={resref='flpri1',charges={0,0,0},expiration=0,flags=1}}}
    f.native_fixture.source_gate={schema='flpr-unvisited-fixture-gate-v1',source_id=id}
    loadfile=function(path)
        if path=='override/FLPRSpell.lua' then return function() return {install=function(api)
            installs=installs+1;local service={callback=function() end}
            api.FLPRKEY=service.callback;return service
        end} end end
        if path=='flpr-fixture/source-gate.lua' then return function() return f.manifest.revision end end
        if path=='flpr-fixture/FLPRGateAsm.lua' or path=='flpr-fixture/FLPRCampaignPolicy.lua' then
            return function() return {} end
        end
        if path=='override/FLPRGat.lua' then return function() return {
            source_ids=function() return {id} end,
            install=function(api,bundles,builder,compiler,pristine)
                assert(pristine.scripts[f.manifest.revision][id]=='REVIEWED_SPL')
                return {prepare_new=function() return true end,reset=function() end}
            end,
        } end end
        return previous(path)
    end
    io.open=function(path,mode)
        assert(path=='flpr-fixture/campaign-store/revisions/'..f.manifest.revision..'.source-1.patched.spl')
        assert(mode=='rb');opened=opened+1
        return {read=function() return 'REVIEWED_SPL' end,close=function() return true end}
    end
    assert((loadstring or load)(text))();assert(FLPR.Disabled==nil,tostring(FLPR.Disabled))
    local service=FLPR.Spell
    assert(installs==1 and opened==1 and service.callback==FLPRKEY)
    assert((loadstring or load)(text))();assert(FLPR.Disabled==nil,tostring(FLPR.Disabled))
    assert(installs==1 and opened==2 and FLPR.Spell==service)
    service.last_error='callback failed'
    FLPR.Poll()
    assert(FLPR.Host.status=='disabled' and FLPR.Disabled=='CAMPAIGN_SPELL_CALLBACK_FAILED:callback failed')
end
function cases.qa_save_ready_allows_protected_running_campaign_without_completing_it(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text)
    local previous,ready=loadfile
    loadfile=function(path)
        if path=='override/FLPRQA.lua' then return function() return {new=function(deps)
            ready=deps.world_ready;return {poll=function() end}
        end} end end
        return previous(path)
    end
    assert((loadstring or load)(text))();assert(FLPR.Disabled==nil,tostring(FLPR.Disabled))
    local phases={Core.PHASE.confirmed,Core.PHASE.protected,Core.PHASE.kept}
    local active=true
    FLPR.Host.status='running'
    FLPR.Host.controller={manifest={revision=f.manifest.revision,sources={{},{},{}}},
        assignments=function()
            return {{source=1,source_phase=phases[1],delivery_phase=0},
                {source=2,source_phase=phases[2],delivery_phase=0},
                {source=3,source_phase=phases[3],delivery_phase=Core.PHASE.kept}}
        end,
        source_guard_active=function(_,index) assert(index==2);return active end}
    f.values[Core.G.journal]=0
    assert(ready(f.manifest.revision) and FLPR.Host.status=='running')
    assert(not ready(string.rep('f',64)))
    for _,field in ipairs({'pending','guard_pending'}) do
        FLPR.Host[field]={};assert(not ready(f.manifest.revision));FLPR.Host[field]=nil
    end
    f.values[Core.G.journal]=2;assert(not ready(f.manifest.revision));f.values[Core.G.journal]=0
    active=false;assert(not ready(f.manifest.revision));active=true
    phases[1]=Core.PHASE.pending;assert(not ready(f.manifest.revision));phases[1]=Core.PHASE.confirmed
    FLPR.Disabled='fixture failure';assert(not ready(f.manifest.revision));FLPR.Disabled=nil
    assert(ready(f.manifest.revision) and FLPR.Host.status=='running')
end
'''


class SpellRuntimeTests(unittest.TestCase):
    def test_key_source_capture_consumption_and_callback_error(self):
        for runtime in (Lua51, Lua53, LuaJIT):
            for kept in (False, True):
                lua = runtime(unpack_returned_tuples=True)
                with self.subTest(runtime=runtime.__module__, kept=kept):
                    self.assertTrue(lua.execute(SOURCE_CASE)(lua.execute(SCRIPT), kept))

    def test_exact_recipient_routing_and_unavailable_recipient_guards(self):
        fixture = NATIVE_FIXTURE.rsplit("return cases", 1)[0] + NATIVE_CASES + "\nreturn cases"
        names = ("key_recipient_is_marked_actor_not_portrait_zero",
                 "key_recipient_marker_missing_duplicate_or_dead_never_falls_back",
                 "key_recipient_local_survives_reload_ids_and_party_reordering")
        for runtime in (Lua51, Lua53, LuaJIT):
            for name in names:
                lua = runtime(unpack_returned_tuples=True)
                with self.subTest(runtime=runtime.__module__, case=name):
                    lua.execute(fixture)[name](lua.execute(NATIVE))

    def test_bootstrap_installs_once_loads_spl_and_disables_host_on_callback_failure(self):
        fixture = HOST_FIXTURE.rsplit("return cases", 1)[0] + BOOTSTRAP_CASE + "\nreturn cases"
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime(unpack_returned_tuples=True)
            with self.subTest(runtime=runtime.__module__):
                lua.execute(fixture).key_bootstrap_loads_spell_once_reads_spl_and_surfaces_error(
                    lua.execute(CORE), lua.execute(HOST), lua.execute(GLOBALS), BOOTSTRAP)

    def test_qa_save_readiness_waits_for_protected_sources_and_no_mutation(self):
        fixture = HOST_FIXTURE.rsplit("return cases", 1)[0] + BOOTSTRAP_CASE + "\nreturn cases"
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime(unpack_returned_tuples=True)
            with self.subTest(runtime=runtime.__module__):
                lua.execute(fixture).qa_save_ready_allows_protected_running_campaign_without_completing_it(
                    lua.execute(CORE), lua.execute(HOST), lua.execute(GLOBALS), BOOTSTRAP)


if __name__ == "__main__":
    unittest.main()
