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
-- The marked party member, not portrait 0. One marked outside the party (it
-- left) is not the one: someone else is, as Chris asked on 2026-10-28, and CIR
-- picks the protagonist.
function cases.key_recipient_is_the_marked_party_member_else_the_protagonist(N)
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
        assert(#f.queue==1 and f.queue[1].m_targetId==(party and 3 or 1))
        f.update();f.boundary()
        local receipt;receipt,reason=f.adapter:observe(command)
        fail(receipt,reason,'DELIVERY_SETTLING')
        f.boundary();receipt=assert(f.adapter:observe(command))
        assert(receipt.lootable==1 and receipt.holder=='party' and receipt.held==nil)
        local holder,other=f.creature,f.source1
        if not party then holder,other=other,holder end
        assert(holder.slots[18].cResRef:get()=='FLPRI1' and not other.slots[18])
    end
end
-- Not marked yet: it waits RECIPIENT_GRACE_STEPS host steps, then goes to the
-- protagonist. Two marked: the first in portrait order. A dead marked member
-- may be raised: it waits.
function cases.key_recipient_waits_for_its_mark_takes_the_first_and_waits_while_dead(N)
    for _,variant in ipairs({'missing','duplicate','dead','error'}) do
        local f=key_world(N,true);f.claimed=1
        if variant=='missing' then f.creature.locals={}
        elseif variant=='duplicate' then f.source1.locals={FLPR_RKEY=1}
        elseif variant=='dead' then f.creature.alive=false
        else f.api.FLPR.Spell.last_error='callback failed' end
        local ready,reason=f.adapter:inspect_target(f.target_id,f.source_ids[1],1)
        if variant=='duplicate' then
            assert(ready and f.adapter.reservations[string.rep('c',64)].owner.id==1)
        else
            local wanted={missing='OWNER_MISSING',dead='TARGET_NOT_ALIVE',
                error='CAMPAIGN_SPELL_CALLBACK_FAILED:callback failed'}
            fail(ready,reason,wanted[variant])
        end
        assert(#f.calls==0 and #f.queue==0)
        for _=1,N.RECIPIENT_GRACE_STEPS-1 do f.boundary() end
        ready,reason=f.adapter:inspect_target(f.target_id,f.source_ids[1],1)
        if variant=='missing' then
            fail(ready,reason,'OWNER_MISSING');f.boundary()
            assert(f.adapter:inspect_target(f.target_id,f.source_ids[1],1))
            assert(f.adapter.reservations[string.rep('c',64)].owner.id==1)
        elseif variant=='dead' then
            -- Past the wait too: a marked member is no missing mark.
            fail(ready,reason,'TARGET_NOT_ALIVE');f.boundary()
            ready,reason=f.adapter:inspect_target(f.target_id,f.source_ids[1],1)
            fail(ready,reason,'TARGET_NOT_ALIVE')
        end
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
        if path=='randomiser-campaigns/source-gate.lua' then return function() return f.manifest.revision end end
        if path=='override/FLPRGateAsm.lua' or path=='override/FLPRCampaignPolicy.lua' then
            return function() return {} end
        end
        if path=='override/FLPRGat.lua' then return function() return {
            source_ids=function() return {id} end,
            new=function(api,deps)
                return {guards=function() return true end,
                    ensure=function(_,revision)
                        deps.load(revision)
                        assert(deps.pristine.scripts[f.manifest.revision][id]=='REVIEWED_SPL')
                        return true
                    end,
                    prepare_new=function() return true end,reset=function() end}
            end,
        } end end
        return previous(path)
    end
    local record=io.open
    io.open=function(path,mode)
        if path=='randomiser-campaigns/last-error.txt' or path:match('^override/flrandomiser[12]%.mrk$') then
            return record(path,mode)
        end
        assert(path=='randomiser-campaigns/campaign-store/revisions/'..f.manifest.revision..'.source-1.patched.spl')
        assert(mode=='rb');opened=opened+1
        return {read=function() return 'REVIEWED_SPL' end,close=function() return true end}
    end
    assert((loadstring or load)(text))();assert(FLPR.Disabled==nil,tostring(FLPR.Disabled))
    local service=FLPR.Spell
    -- The reviewed SPL is read when a game needs the revision's checks.
    assert(installs==1 and opened==0 and service.callback==FLPRKEY)
    assert(FLPR.SourceGate:ensure(f.manifest.revision) and opened==1)
    assert((loadstring or load)(text))();assert(FLPR.Disabled==nil,tostring(FLPR.Disabled))
    assert(FLPR.SourceGate:ensure(f.manifest.revision))
    assert(installs==1 and opened==2 and FLPR.Spell==service)
    service.last_error='callback failed'
    FLPR.Poll()
    assert(FLPR.Host.status=='disabled' and FLPR.Disabled=='CAMPAIGN_SPELL_CALLBACK_FAILED:callback failed')
    assert(f.error_file:find('campaign: CAMPAIGN_SPELL_CALLBACK_FAILED:callback failed',1,true))
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
        names = ("key_recipient_is_the_marked_party_member_else_the_protagonist",
                 "key_recipient_waits_for_its_mark_takes_the_first_and_waits_while_dead",
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


if __name__ == "__main__":
    unittest.main()
