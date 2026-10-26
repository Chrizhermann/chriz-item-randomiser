"""The chapter 3 places reward the first of their creatures who can take it.

Item Randomiser's chapter3.fl gives each place's item to one of Bodhi's
vampires and to one of Aran's shadow thieves, since the party fights one side
only. CIR's place (FLPRNat "first-of", see chapter3.py) has one reward: the
vampire drawn for it takes it alive, the thief drawn for it and Aran only at
their corpses, whoever comes first in the visible area. Fake engine of
test_campaign_native_creature.
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
-- Place 25: the vampires VAMP1 and VAMP2 in AR0801, the thief THIEF1 and
-- Aran (ARAN) in AR0329. Seed 17 draws the first vampire for unit 1 and the
-- second for unit 2; FLPRI2 suits only the second.
local PLACE='original-target:bg2:mode2:target:25'
local IDENTITY=string.rep('c',64)
local function chapter3_world(N)
    local f=ground_world(N)
    f.target_id=PLACE
    local function put(id,address,name,area)
        local o=f.sprite(address,name,{});o.m_id=id;o.m_pos={x=100*id,y=100*id};o.area=area
        f.objects[id],f.ids[o]=o,id
        return o
    end
    f.vamp1,f.vamp2=put(4,401,'VAMP1','AR0801'),put(5,402,'VAMP2','AR0801')
    f.thief,f.aran=put(6,403,'THIEF1','AR0329'),put(7,404,'ARAN','AR0329')
    -- The party enters <area>: its creatures and the two sources are there.
    function f.enter(area)
        f.area.m_resref={get=function() return area end}
        local listed={1,2}
        for id=4,7 do if f.objects[id].area==area then listed[#listed+1]=id end end
        f.area.m_lVertSort=listed
    end
    -- GiveItemCreate queues a creature message and DestroyItem takes a
    -- source's item, as setup's engine does; the corpse actions are ground_world's.
    local ground=f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly
    f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly=function(action,o)
        local ref=action:match('^GiveItemCreate%("([^"]+)",Myself,0,0,0%)$')
        if ref then
            f.calls[#f.calls+1]=action
            f.queue[#f.queue+1]=f.message(f.ids[o],ref:upper())
            return
        end
        ref=action:match('^DestroyItem%("([^"]+)"%)$')
        if ref then
            f.calls[#f.calls+1]=action
            for slot,value in pairs(o.slots) do
                if value.cResRef:get():lower()==ref then o.slots[slot]=nil end
            end
            return
        end
        return ground(action,o)
    end
    -- A creature item message lands in the first empty backpack slot.
    local update=f.update
    function f.update()
        for _,message in ipairs(f.queue) do
            local target=f.objects[message.m_targetId]
            if message.virtual_GetMsgSubType()==2 and target and target.sprite then
                for slot=18,33 do
                    if not target.slots[slot] then
                        f.next_address=f.next_address+1
                        target.slots[slot]=f.item(message.m_item.cResRef:get(),f.next_address,message.m_item.m_flags)
                        break
                    end
                end
            end
        end
        update()
    end
    f.fixture.targets={[PLACE]={kind='first-of',identity=IDENTITY,groups={
        {members={{resref='vamp1',script_name='VAMP1',area='AR0801'},
                  {resref='vamp2',script_name='VAMP2',area='AR0801'}},
         eligible={flpri1={1,2},flpri2={2}},corpse_only=false},
        {members={{resref='thief1',script_name='THIEF1',area='AR0329'}},
         eligible={flpri1={1},flpri2={1}},corpse_only=true},
        {members={{resref='aran02',script_name='ARAN',area='AR0329'}},corpse_only=true}}}}
    f.adapter=N.new(f.api,f.fixture,17)
    f.enter('AR0801')
    return f
end
local function reserved(f) return f.adapter.reservations[IDENTITY] end
local function empty(sprite) return next(sprite.slots)==nil end

function cases.the_drawn_vampire_takes_the_reward_alive(N)
    local f=chapter3_world(N);local a=f.adapter
    local witness=assert(a:inspect_target(PLACE,f.source_ids[1],1))
    assert(witness.ground==nil and witness.target_id==PLACE and witness.identity==IDENTITY)
    assert(reserved(f).owner.id==4 and reserved(f).endpoint.script_name=='vamp1' and
        reserved(f).endpoint.area=='ar0801')
    f.boundary()
    local command=f.command(1)
    assert(a:execute(command))
    assert(#f.calls==1 and f.calls[1]=='GiveItemCreate("flpri1",Myself,0,0,0)' and f.queue[1].m_targetId==4)
    f.update();f.boundary()
    local receipt,reason=a:observe(command);fail(receipt,reason,'DELIVERY_SETTLING')
    f.boundary();receipt=assert(a:observe(command))
    assert(receipt.held==1 and receipt.lootable==0 and receipt.holder=='creature')
    assert(f.vamp1.slots[18].cResRef:get()=='FLPRI1' and empty(f.vamp2) and empty(f.thief) and empty(f.aran))
    local x,e=a:execute(command);fail(x,e,'ALREADY_ATTEMPTED')
end

function cases.each_reward_draws_one_creature_of_each_group(N)
    local f=chapter3_world(N);local a=f.adapter
    assert(a:inspect_target(PLACE,f.source_ids[1],2) and reserved(f).owner.id==5)
    assert(a:inspect_target(PLACE,f.source_ids[2],1) and reserved(f).owner.id==5)
    -- The same draw after a reload.
    local restored=N.new(f.api,f.fixture,17)
    assert(restored:inspect_target(PLACE,f.source_ids[1],2) and restored.reservations[IDENTITY].owner.id==5)
    -- The drawn vampire gone: the other one does not stand in for it.
    f.area.m_lVertSort={1,2,5}
    local x,e=a:inspect_target(PLACE,f.source_ids[1],1);fail(x,e,'OWNER_MISSING')
    assert(#f.calls==0 and next(a.reservations)==nil)
    -- A reward with no candidates in a group, or no reward: none of this place's.
    f.fixture.targets[PLACE].groups[1].eligible={flpri2={2}}
    local narrow=N.new(f.api,f.fixture,17)
    x,e=narrow:inspect_target(PLACE,f.source_ids[1],1);fail(x,e,'TARGET_UNKNOWN')
    x,e=a:inspect_target(PLACE);fail(x,e,'TARGET_UNKNOWN')
    x,e=a:inspect_target(PLACE,f.source_ids[1],0);fail(x,e,'TARGET_UNKNOWN')
    assert(#f.calls==0)
end

function cases.a_thief_takes_it_only_at_his_corpse(N)
    local f=chapter3_world(N);local a=f.adapter
    f.enter('AR0329')
    local x,e=a:inspect_target(PLACE,f.source_ids[1],1);fail(x,e,'TARGET_ALIVE')
    assert(#f.calls==0 and next(a.reservations)==nil)
    f.thief.alive=false
    local witness=assert(a:inspect_target(PLACE,f.source_ids[1],1))
    assert(witness.ground==true and reserved(f).owner.id==6 and reserved(f).endpoint.corpse_only==true)
    f.boundary();local command=f.command(1);assert(a:execute(command))
    assert(#f.calls==2 and f.calls[1]=='CreateItem("flpri1",0,0,0)' and f.calls[2]=='DropItem("flpri1",[-1.-1])')
    f.boundary();x,e=a:observe(command);fail(x,e,'DELIVERY_SETTLING')
    f.update();f.boundary();x,e=a:observe(command);fail(x,e,'DELIVERY_SETTLING')
    x,e=a:observe(command);fail(x,e,'DELIVERY_SETTLING')
    f.boundary();local receipt=assert(a:observe(command))
    assert(receipt.holder=='ground' and empty(f.thief) and empty(f.aran))
    local pile=f.objects[f.area.m_lVertSortBack[1]]
    assert(#pile.m_lstItems==1 and pile.m_lstItems[1].cResRef:get()=='FLPRI1')
end

function cases.aran_takes_it_if_he_dies_before_the_thief(N)
    local f=chapter3_world(N);local a=f.adapter
    f.enter('AR0329');f.aran.alive=false
    assert(a:inspect_target(PLACE,f.source_ids[1],1).ground==true and reserved(f).owner.id==7)
    -- Both dead: the thief, who comes first.
    f.thief.alive=false
    assert(a:inspect_target(PLACE,f.source_ids[1],1).ground==true and reserved(f).owner.id==6)
    -- A dead vampire's corpse too, where one stays.
    f=chapter3_world(N);f.vamp1.alive=false
    assert(f.adapter:inspect_target(PLACE,f.source_ids[1],1).ground==true and reserved(f).owner.id==4)
end

-- Aran's side: the drawn vampire dies before the reward reaches it, and the
-- mist it turns into leaves no body. The thief and Aran live on, so the reward
-- falls at the feet of the party there (FLPRNat's _inspect_party_ground).
function cases.a_vampire_gone_into_mist_drops_it_at_the_partys_feet(N)
    local f=chapter3_world(N);local a=f.adapter
    local hero=f.sprite(801,'KARA',{})
    hero.portrait,hero.m_id,hero.name,hero.m_pos=0,8,'Kara',{x=300,y=400}
    f.objects[8],f.ids[hero]=hero,8
    f.api.EEex_Sprite_GetInPortrait=function(index) if index==0 then return hero end end
    f.area.m_lVertSort={1,2,5,8}
    -- Gone, but not counted dead: it waits, as before the vampires come.
    local x,e=a:inspect_target(PLACE,f.source_ids[1],1);fail(x,e,'OWNER_MISSING')
    f.globals.SPRITE_IS_DEADVAMP1=1
    local witness=assert(a:inspect_target(PLACE,f.source_ids[1],1))
    assert(witness.ground==true and reserved(f).owner.id==8 and reserved(f).portrait==0 and
        reserved(f).endpoint.script_name=='vamp1')
    f.boundary();local command=f.command(1);assert(a:execute(command))
    assert(#f.calls==2 and f.calls[1]=='CreateItem("flpri1",0,0,0)' and f.calls[2]=='DropItem("flpri1",[-1.-1])')
    f.boundary();x,e=a:observe(command);fail(x,e,'DELIVERY_SETTLING')
    f.update();f.boundary();x,e=a:observe(command);fail(x,e,'DELIVERY_SETTLING')
    f.boundary();local receipt=assert(a:observe(command))
    assert(receipt.holder=='ground' and receipt.notice.member=='Kara' and receipt.notice.resref=='flpri1')
    assert(empty(hero) and empty(f.vamp2) and empty(f.thief) and empty(f.aran))
    local pile=f.objects[f.area.m_lVertSortBack[1]]
    assert(#pile.m_lstItems==1 and pile.m_lstItems[1].cResRef:get()=='FLPRI1')
end

function cases.it_waits_with_the_first_reason_in_the_visible_area(N)
    local f=chapter3_world(N);local a=f.adapter
    -- Before the vampires come: the first vampire's absence.
    f.area.m_lVertSort={1,2}
    local x,e=a:inspect_target(PLACE,f.source_ids[1],1);fail(x,e,'OWNER_MISSING')
    -- A full backpack is the vampire's own wait; nobody else stands in.
    f.enter('AR0801')
    for slot=18,33 do f.vamp1.slots[slot]=f.item('FILL',600+slot) end
    x,e=a:inspect_target(PLACE,f.source_ids[1],1);fail(x,e,'TARGET_INVENTORY_FULL')
    f.enter('BG0100')
    x,e=a:inspect_target(PLACE,f.source_ids[1],1);fail(x,e,'AREA_UNAVAILABLE')
    assert(#f.calls==0 and next(a.reservations)==nil)
end

function cases.execute_delivers_to_the_reserved_creature_only(N)
    local f=chapter3_world(N);local a=f.adapter
    assert(a:inspect_target(PLACE,f.source_ids[1],1));f.boundary()
    f.vamp1.address=999
    local x,e=a:execute(f.command(1));fail(x,e,'OWNER_CHANGED')
    assert(#f.calls==0)
    for _,change in ipairs({function(r) r.endpoint=nil end,
        function(r) r.endpoint={kind='group',identity=IDENTITY} end,
        function(r) r.endpoint=f.adapter.targets[PLACE].groups[2].members[1];r.endpoint.identity=string.rep('d',64) end}) do
        f=chapter3_world(N);a=f.adapter
        assert(a:inspect_target(PLACE,f.source_ids[1],1));f.boundary()
        change(reserved(f))
        x,e=a:execute(f.command(1));fail(x,e,'RESERVATION_MISMATCH')
        assert(#f.calls==0)
    end
end

function cases.diagnostics_show_each_creature(N)
    local f=chapter3_world(N)
    f.vamp2.alive=false
    local members=f.adapter:diagnostics().targets[PLACE].members
    assert(#members==4)
    assert(members[1].area=='ar0801' and members[1].script_name=='vamp1' and members[1].object_id==4 and
        members[1].unavailable==nil)
    assert(members[2].object_id==5 and members[2].unavailable=='TARGET_NOT_ALIVE')
    assert(members[3].area=='ar0329' and members[3].object_id==nil and members[3].unavailable=='AREA_UNAVAILABLE')
    assert(members[4].script_name=='aran' and members[4].unavailable=='AREA_UNAVAILABLE')
end

function cases.the_place_configuration_is_strict(N)
    local f=chapter3_world(N)
    local function refused(change,want,seed)
        local fixture=clone(f.fixture);change(fixture.targets[PLACE])
        local ok,err=pcall(N.new,f.api,fixture,seed==nil and 17 or seed)
        assert(not ok and tostring(err):find(want,1,true),tostring(err))
    end
    refused(function() end,'NATIVE_FIRST_OF_INVALID',false)
    refused(function(t) t.groups={} end,'NATIVE_FIRST_OF_INVALID')
    refused(function(t) t.groups[1].members={} end,'NATIVE_FIRST_OF_INVALID')
    refused(function(t) t.groups[2].corpse_only=nil end,'NATIVE_FIRST_OF_INVALID')
    refused(function(t) t.groups[2].corpse_only='yes' end,'NATIVE_FIRST_OF_INVALID')
    refused(function(t) t.groups[1].eligible='all' end,'NATIVE_FIRST_OF_INVALID')
    refused(function(t) t.groups[3].members[1].area=nil end,'NATIVE_FIRST_OF_MEMBER_INVALID')
    refused(function(t) t.groups[3].members[1].area='AR/0329' end,'NATIVE_FIRST_OF_MEMBER_INVALID')
    refused(function(t) t.groups[3].members[1].script_name='' end,'NATIVE_FIRST_OF_MEMBER_INVALID')
    refused(function(t) t.groups[3].members[1].resref=nil end,'NATIVE_FIRST_OF_MEMBER_INVALID')
    refused(function(t) t.groups[1].eligible.flpri2={3} end,'NATIVE_FIRST_OF_CANDIDATES_INVALID')
    refused(function(t) t.groups[1].eligible.flpri2={} end,'NATIVE_FIRST_OF_CANDIDATES_INVALID')
    -- Its creatures are its members: the place has no name, position,
    -- handover or corpse of its own.
    refused(function(t) t.x,t.y=1,2 end,'NATIVE_POSITION_INVALID')
    refused(function(t) t.corpse_only=true end,'NATIVE_CORPSE_ONLY_INVALID')
    refused(function(t) t.inventory_reward=true end,'NATIVE_INVENTORY_REWARD_INVALID')
    refused(function(t) t.handover={event_global='FLPR_EM2_25',recipient_local='FLPR_RM2_25'} end,
        'NATIVE_HANDOVER_INVALID')
end

-- The real host and core: the place waits while the thief lives, and the
-- reward lies at his corpse once he is dead.
function cases.the_host_waits_until_one_of_them_can_take_it(N,Core,Host)
    local f=chapter3_world(N)
    f.enter('AR0329')
    f.globals={}
    f.api.EEex_GameState_GetGlobalInt=function(key) return f.globals[key] or 0 end
    local source_id=f.source_ids[1]
    f.fixture.sources[f.source_ids[2]]=nil
    f.fixture.sources[source_id].area='AR0329'
    local manifest={schema=Core.SCHEMA,state='prototype-not-playable',revision=string.rep('a',64),
        bound_revision=string.rep('b',64),world_fingerprint=string.rep('c',64),profile_id='chapter3-test',
        sources={{id=source_id,quantity=1,resource_sha256=string.rep('1',64),record={
            id=source_id,record=f.fixture.sources[source_id].payload}}},
        targets={{id=PLACE,capacity=1,resource_sha256=string.rep('2',64),record={id=PLACE,kind='group'}}}}
    local bundle={manifest=manifest,fixture=f.fixture}
    local storage={get=function(_,key) return f.globals[key] or 0 end}
    function storage:set(key,value)
        if self.undo and self.undo.previous[key]==nil then
            self.undo.previous[key]=self:get(key); self.undo.order[#self.undo.order+1]=key
        end
        f.globals[key]=value
    end
    function storage:begin() assert(not self.undo,'STORAGE_TRANSACTION_ACTIVE'); self.undo={previous={},order={}} end
    function storage:commit() assert(self.undo,'STORAGE_TRANSACTION_MISSING'); self.undo=nil end
    function storage:rollback()
        local undo=self.undo; self.undo=nil
        for i=#undo.order,1,-1 do f.globals[undo.order[i]]=undo.previous[undo.order[i]] end
    end
    local tick=0
    local deps={core=Core,storage=storage,resolve=function() return bundle end,
        next=function() return bundle end,seed=function() return 17 end,
        world=function() return true end,tick=function() return tick end,
        native=function(fixture) f.adapter=N.new(f.api,fixture,17);return f.adapter end}
    local host=Host.new(deps);f.globals.FLPR_FRESH=1
    assert(host:capture_new('confirmed-character-accept'))
    local function poll() tick=tick+1;host:poll();f.update();assert(host.status~='disabled',host.error) end
    for _=1,12 do poll() end
    assert(host.status~='complete' and host.deferred[1].reason=='TARGET_ALIVE' and f.source1.slots[21]==nil)
    f.thief.alive=false
    for _=1,Host.DEFER_TICKS+12 do poll() end
    assert(host.status=='complete' and host.controller:assignments()[1].delivery_phase==Core.PHASE.confirmed)
    local pile=f.objects[f.area.m_lVertSortBack[1]]
    assert(#pile.m_lstItems==1 and pile.m_lstItems[1].cResRef:get()=='FLPRI1' and empty(f.thief) and empty(f.aran))
end
'''

CASE_NAMES = (
    "the_drawn_vampire_takes_the_reward_alive",
    "each_reward_draws_one_creature_of_each_group",
    "a_thief_takes_it_only_at_his_corpse",
    "aran_takes_it_if_he_dies_before_the_thief",
    "a_vampire_gone_into_mist_drops_it_at_the_partys_feet",
    "it_waits_with_the_first_reason_in_the_visible_area",
    "execute_delivers_to_the_reserved_creature_only",
    "diagnostics_show_each_creature",
    "the_place_configuration_is_strict",
    "the_host_waits_until_one_of_them_can_take_it",
)


class Chapter3RuntimeTests(unittest.TestCase):
    def test_chapter3_runtime(self):
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


if __name__ == "__main__":
    unittest.main()
