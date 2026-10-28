"""Adalon marks the one she talks to as the recipient of her egg reward at once.

Item Randomiser gives her crossbow to the one she talks to when she takes the
eggs (UDSILVER.DLG state 41). CIR's reward reply marks that recipient through
an action of his own, and the end of her talk clears every queued action of
the party first. So the reply's condition marks him when the game shows it
(FLPRNat mark_recipient, M_FLPR FLPR_Mark), and the reward of the egg deal's
place follows the mark. Fake engine of test_campaign_native_creature.
"""
from pathlib import Path
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT

from test_campaign_native_creature import CASES as NATIVE_FIXTURE
from test_campaign_native_host import CORE, HOST

ROOT = Path(__file__).resolve().parents[1]
NATIVE = (ROOT / "prototype/FLPRNat.lua").read_text(encoding="utf-8")
CASES = r'''
local function str(value) return {get=function() return value end} end
-- AR2102 as Adalon's egg reply finds it: the one she talks to and another
-- party member before her, and the egg deal's place (a quest target).
local function egg_world(N)
    local f=setup(N)
    f.globals,f.mark={},'FLPR_RA_EGGDEALIO'
    f.target_id='original-target:bg2:mode2:target:66'
    f.payee=f.creature;f.payee.m_id=3;f.payee.portrait=0;f.payee.locals={}
    f.member=f.sprite(206,'IMOEN2',{});f.member.m_id=6;f.member.portrait=1;f.member.locals={}
    f.adalon=f.sprite(205,'UDSILVER',{});f.adalon.m_id=5;f.adalon.last_talked=f.payee;f.adalon.locals={}
    f.objects[5],f.objects[6]=f.adalon,f.member
    f.ids[f.adalon],f.ids[f.member]=5,6
    f.area.m_resref=str('AR2102')
    f.area.m_lVertSort={3,5,6}
    f.party={[0]=f.payee,[1]=f.member}
    f.api.EEex_Sprite_GetInPortrait=function(index) return f.party[index] end
    f.api.EEex_Sprite_GetLocalInt=function(sprite,key) return sprite.locals and sprite.locals[key] or 0 end
    f.api.EEex_GameState_GetGlobalInt=function(key) return f.globals[key] or 0 end
    f.api.EEex_Object_EvalStringAsAIBase=function(text,speaker)
        assert(text=='LastTalkedToBy' and speaker==f.adalon);return speaker.last_talked
    end
    local act=f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly
    f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly=function(action,o)
        local name,value=action:match('^SetGlobal%("(FLPR_R[%w_]+)","LOCALS",([01])%)$')
        if name then
            f.calls[#f.calls+1]=action
            if not (f.mark_fails and value=='1') and not (f.clear_fails and value=='0') then
                o.locals=o.locals or {};o.locals[name]=tonumber(value)
            end
            return
        end
        return act(action,o)
    end
    f.fixture.targets={[f.target_id]={kind='quest',area='*',identity=string.rep('c',64),
        adapter_id='mode2-reward-v1',event_global='FLPR_EA_EGGDEALIO',recipient_local=f.mark}}
    function f:reload() self.adapter=N.new(self.api,self.fixture) end
    function f:mark_now(mark) return self.adapter:mark_recipient(self.adalon,mark or self.mark) end
    f:reload()
    return f
end

function cases.the_one_she_talks_to_is_marked_at_once(N)
    local f=egg_world(N)
    assert(f:mark_now()==nil)
    assert(f.payee.locals.FLPR_RA_EGGDEALIO==1 and f.member.locals.FLPR_RA_EGGDEALIO==nil)
    assert(#f.calls==1 and f.calls[1]=='SetGlobal("FLPR_RA_EGGDEALIO","LOCALS",1)')
    -- The condition may be checked again: he stays the only one.
    assert(f:mark_now()==nil and #f.calls==2 and f.member.locals.FLPR_RA_EGGDEALIO==nil)
    assert(f.adalon.locals.FLPR_RA_EGGDEALIO==nil)
end

-- The reply was shown to another before, who did not take it: only the one
-- she gives the reward to now keeps the mark, dead or alive, in the party or
-- in the area.
function cases.another_ones_earlier_mark_is_taken_away(N)
    local f=egg_world(N)
    f.member.locals.FLPR_RA_EGGDEALIO=1
    local fallen=f.sprite(207,'XNPC',{});fallen.m_id=7;fallen.alive=false;fallen.locals={FLPR_RA_EGGDEALIO=1}
    f.objects[7],f.ids[fallen]=fallen,7
    f.area.m_lVertSort={3,5,6,7}
    assert(f:mark_now()==nil)
    assert(f.payee.locals.FLPR_RA_EGGDEALIO==1 and f.member.locals.FLPR_RA_EGGDEALIO==0 and
        fallen.locals.FLPR_RA_EGGDEALIO==0)
    assert(#f.calls==3 and f.calls[1]=='SetGlobal("FLPR_RA_EGGDEALIO","LOCALS",1)' and
        f.calls[2]=='SetGlobal("FLPR_RA_EGGDEALIO","LOCALS",0)' and f.calls[3]==f.calls[2])
    -- Another mark stays as it is.
    f.member.locals.FLPR_RA_SAHROD=1
    assert(f:mark_now()==nil and f.member.locals.FLPR_RA_SAHROD==1)
end

-- As the Sahuagin king's payee: the one she talks to where it is in the
-- party, else the first living party member; no one, and nothing is marked.
function cases.the_recipient_is_the_one_she_talks_to_else_the_party(N)
    local f=egg_world(N)
    f.adalon.last_talked=nil
    assert(f:mark_now()==nil and f.payee.locals.FLPR_RA_EGGDEALIO==1)
    f=egg_world(N)
    local former=f.sprite(207,'XNPC',{});former.m_id=7;former.locals={};f.adalon.last_talked=former
    f.payee.alive=false
    assert(f:mark_now()==nil and f.member.locals.FLPR_RA_EGGDEALIO==1 and former.locals.FLPR_RA_EGGDEALIO==nil)
    f=egg_world(N)
    f.payee.alive=false
    assert(f:mark_now()==nil and f.member.locals.FLPR_RA_EGGDEALIO==1 and f.payee.locals.FLPR_RA_EGGDEALIO==nil)
    f=egg_world(N)
    f.payee.alive,f.member.alive=false,false
    assert(f:mark_now()=='MARK_PAYEE_MISSING' and #f.calls==0)
end

-- A mark of no quest place of this campaign (the place is left out, or the
-- spell's) changes nothing; one that is no recipient mark is refused.
function cases.only_a_quest_place_s_mark_is_set(N)
    local f=egg_world(N)
    assert(f:mark_now('FLPR_RA_SCULPTOR')==nil and #f.calls==0)
    f.fixture.targets={}
    f:reload()
    assert(f:mark_now()==nil and #f.calls==0 and f.payee.locals.FLPR_RA_EGGDEALIO==nil)
    f=egg_world(N)
    for _,mark in ipairs({'FLPR_EA_EGGDEALIO','flpr_ra_eggdealio','FLPR_RA_EGG DEAL',false,66}) do
        local ok,err=pcall(f.adapter.mark_recipient,f.adapter,f.adalon,mark)
        assert(not ok and tostring(err):find('NATIVE_MARK_INVALID',1,true),tostring(mark))
    end
    local ok,err=pcall(f.adapter.mark_recipient,f.adapter,f.adalon,nil)
    assert(not ok and tostring(err):find('NATIVE_MARK_INVALID',1,true) and #f.calls==0)
end

function cases.an_unproven_mark_stops_the_campaign(N)
    for _,case in ipairs({{'mark_fails',1},{'clear_fails',2}}) do
        local f=egg_world(N)
        f.member.locals.FLPR_RA_EGGDEALIO=1
        f[case[1]]=true
        local ok,err=pcall(f.mark_now,f)
        assert(not ok and tostring(err):find('NATIVE_RECIPIENT_MARK_FAILED',1,true) and #f.calls==case[2],case[1])
    end
end

-- Her reply sets the deal's event too: the reward of its place goes to the
-- one marked, after the talk.
function cases.the_marked_one_receives_the_egg_deal_s_reward(N)
    local f=egg_world(N)
    f.member.locals.FLPR_RA_EGGDEALIO=1
    local x,e=f.adapter:inspect_target(f.target_id,f.source_ids[1],1);fail(x,e,'QUEST_NOT_CLAIMED')
    assert(f:mark_now()==nil);f.globals.FLPR_EA_EGGDEALIO=1
    assert(f.adapter:inspect_target(f.target_id,f.source_ids[1],1));f.boundary()
    local command=f.command(1);assert(f.adapter:execute(command))
    f.update();f.boundary()
    x,e=f.adapter:observe(command);fail(x,e,'DELIVERY_SETTLING')
    f.boundary();local receipt=assert(f.adapter:observe(command))
    assert(receipt.holder=='party' and receipt.lootable==1 and receipt.slot==18)
    assert(f.payee.slots[18].cResRef:get()=='FLPRI1' and next(f.member.slots)==nil)
end
'''

CASE_NAMES = (
    "the_one_she_talks_to_is_marked_at_once",
    "another_ones_earlier_mark_is_taken_away",
    "the_recipient_is_the_one_she_talks_to_else_the_party",
    "only_a_quest_place_s_mark_is_set",
    "an_unproven_mark_stops_the_campaign",
    "the_marked_one_receives_the_egg_deal_s_reward",
)

CALLBACK = r'''
local state=0
local Core={G={state='flpr_state'}}
local storage={get=function(_,key) assert(key=='flpr_state');return state end}
local root={}
local recorded={}
local function record(context,reason) recorded[#recorded+1]=context..': '..tostring(reason) end
%s
assert(FLPR_Mark({},'FLPR_RA_EGGDEALIO')==true) -- Not randomised.
state=2
assert(FLPR_Mark({},'FLPR_RA_EGGDEALIO')==true) -- No host yet.
local speaker,result,calls,disabled={},{},0,nil
root.Host={status='running',
    native={mark_recipient=function(_,actor,mark)
        assert(actor==speaker and mark=='FLPR_RA_EGGDEALIO')
        calls=calls+1
        if result.fail then error('BROKEN') end
        return result[1]
    end},
    _disable=function(self,reason) disabled=reason;self.status='disabled' end}
assert(FLPR_Mark(speaker,'FLPR_RA_EGGDEALIO')==true and calls==1 and #recorded==0)
-- No one to mark: recorded, the reply still shows.
result={'MARK_PAYEE_MISSING'}
assert(FLPR_Mark(speaker,'FLPR_RA_EGGDEALIO')==true and recorded[1]=='mark: MARK_PAYEE_MISSING')
-- A failure stops the campaign; she still takes the eggs.
result={fail=true}
assert(FLPR_Mark(speaker,'FLPR_RA_EGGDEALIO')==true and root.Disabled:find('CAMPAIGN_MARK_FAILED',1,true))
assert(disabled==root.Disabled and root.Host.status=='disabled')
-- Stopped: nothing more is marked.
result={}
assert(FLPR_Mark(speaker,'FLPR_RA_EGGDEALIO')==true and calls==3)
-- Even a failing stop leaves the reply.
root.Disabled=nil;root.Host.status='running';result={fail=true}
root.Host._disable=function() error('ALSO_BROKEN') end
assert(FLPR_Mark(speaker,'FLPR_RA_EGGDEALIO')==true and root.Disabled)
storage.get=function() error('NO_STORAGE') end
root.Disabled=nil
assert(FLPR_Mark(speaker,'FLPR_RA_EGGDEALIO')==true and root.Disabled:find('NO_STORAGE',1,true))
'''


class RecipientMarkRuntimeTests(unittest.TestCase):
    def test_mark_runtime(self):
        code = NATIVE_FIXTURE.rsplit("return cases", 1)[0] + CASES + "\nreturn cases"
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime(unpack_returned_tuples=True)
            cases = lua.execute(code)
            for name in CASE_NAMES:
                with self.subTest(runtime=runtime.__module__, case=name):
                    cases[name](lua.execute(NATIVE), lua.execute(CORE), lua.execute(HOST))

    def test_every_case_runs(self):
        code = NATIVE_FIXTURE.rsplit("return cases", 1)[0] + CASES + "\nreturn cases"
        defined = set(Lua51(unpack_returned_tuples=True).execute(code).keys())
        native = set(Lua51(unpack_returned_tuples=True).execute(NATIVE_FIXTURE).keys())
        self.assertEqual(defined - native, set(CASE_NAMES))

    def test_the_reply_condition_is_always_true(self):
        entry = (ROOT / "prototype/M_FLPR.lua").read_text(encoding="utf-8")
        callback = "function FLPR_Mark" + entry.split("function FLPR_Mark", 1)[1].split(
            "    root.Disabled = nil\n", 1)[0]
        for runtime in (Lua51, Lua53, LuaJIT):
            with self.subTest(runtime=runtime.__module__):
                runtime(unpack_returned_tuples=True).execute(CALLBACK % callback)


if __name__ == "__main__":
    unittest.main()
