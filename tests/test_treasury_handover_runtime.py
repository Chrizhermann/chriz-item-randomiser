"""The Sahuagin king hands his treasury's rewards to the one he pays.

Item Randomiser's sahtreas.fl: the new item of place 6 lies in the treasury,
and the king's reward reply gives it. CIR leaves the reward in the treasury
until the reply's condition hands it to the payee (FLPRNat
hand_over_container_reward, M_FLPR FLPR_Handover); a reward not placed yet
follows the payment to the payee. The king's own place (tadpoles1.fl, row 58)
receives only at his corpse until that payment. Fake engine of
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
local function str(value) return {get=function() return value end} end
-- AR2300 as the king's last scene leaves it: the payee and another party
-- member before the king, the treasury (container "Treasury") with one of
-- its own items and the reward of place 6.
local function treasury_world(N,Core)
    local f=setup(N)
    f.phases,f.globals=Core.PHASE,{}
    f.target_id='original-target:bg2:mode2:target:6'
    f.payee=f.creature;f.payee.m_id=3;f.payee.portrait=0;f.payee.locals={}
    f.member=f.sprite(206,'IMOEN2',{});f.member.m_id=6;f.member.portrait=1;f.member.locals={}
    f.king=f.sprite(205,'SAHKNG01',{});f.king.m_id=5;f.king.last_talked=f.payee
    f.treasury={address=204,sprite=false,m_containerType=8,m_pos={x=437,y=717},
        m_scriptName=str('Treasury'),m_lstItems={f.item('MISCBI',401,0),f.item('FLPRI1',402)}}
    f.objects[4],f.objects[5],f.objects[6]=f.treasury,f.king,f.member
    f.ids[f.treasury],f.ids[f.king],f.ids[f.member]=4,5,6
    f.area.m_resref=str('AR2300')
    f.area.m_lVertSort,f.area.m_lVertSortBack={3,5,6},{4}
    f.party={[0]=f.payee,[1]=f.member}
    f.api.EEex_Sprite_GetInPortrait=function(index) return f.party[index] end
    f.api.EEex_Sprite_GetLocalInt=function(sprite,key) return sprite.locals and sprite.locals[key] or 0 end
    f.api.EEex_GameState_GetGlobalInt=function(key) return f.globals[key] or 0 end
    f.api.EEex_Object_EvalStringAsAIBase=function(text,speaker)
        assert(text=='LastTalkedToBy' and speaker==f.king);return speaker.last_talked
    end
    local act=f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly
    f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly=function(action,o)
        local name=action:match('^SetGlobal%("(FLPR_R[%w_]+)","LOCALS",1%)$')
        if name then
            f.calls[#f.calls+1]=action
            if not f.mark_fails then o.locals[name]=1 end
            return
        end
        local ref=action:match('^DestroyItem%("([^"]+)"%)$')
        if ref and o.m_lstItems then
            f.calls[#f.calls+1]=action
            if f.destroy_other then ref='miscbi' end
            local kept,removed={},false
            for _,item in ipairs(o.m_lstItems) do
                if not f.destroy_none and item.cResRef:get():lower()==ref and (f.destroy_all or not removed) then
                    removed=true
                else kept[#kept+1]=item end
            end
            o.m_lstItems=kept
            return
        end
        ref=action:match('^CreateItem%("([^"]+)",0,0,0%)$')
        if ref and o.m_lstItems then
            f.calls[#f.calls+1]=action
            o.m_lstItems[#o.m_lstItems+1]=f.item(ref:upper(),500)
            return
        end
        if action:match('^GiveItemCreate') then
            if f.give_fails then f.calls[#f.calls+1]=action;return end
            act(action,o)
            if f.give_twice then f.queue[#f.queue+1]=f.message(f.ids[o],'FLPRI1') end
            -- A party member's gain also queues ordinary feedback, which is
            -- no item message.
            local feedback=f.message(0,'OTHER',16,0)
            feedback.m_item=nil
            feedback.virtual_GetCommType=function() return 4 end
            f.queue[#f.queue+1]=feedback
            return
        end
        return act(action,o)
    end
    f.fixture.targets={[f.target_id]={kind='container',area='AR2300',script_name='Treasury',x=437,y=717,
        container_type=8,identity=string.rep('c',64),
        handover={event_global='FLPR_EA_SAHROD',recipient_local='FLPR_RA_SAHROD'}}}
    f.assignments={{unit=1,source_id=f.source_ids[1],target_id=f.target_id,delivery_phase=Core.PHASE.confirmed}}
    function f:reload() self.adapter=N.new(self.api,self.fixture) end
    function f:hand_over(id)
        return self.adapter:hand_over_container_reward(self.king,id or self.target_id,self.assignments,self.phases)
    end
    function f:retry() assert(self.adapter:retry_handovers(self.assignments,self.phases)) end
    -- Item messages only; the party feedback carries no item.
    function f:given()
        local result={}
        for _,message in ipairs(self.queue) do
            if message.virtual_GetMsgSubType()==2 then result[#result+1]=message end
        end
        return result
    end
    function f:full(sprite,first)
        for slot=18,33 do sprite.slots[slot]=sprite.slots[slot] or self.item('MISC01',first+slot) end
    end
    f:reload()
    return f
end

function cases.the_confirmed_reward_moves_from_the_treasury_to_the_payee_once(N,Core)
    local f=treasury_world(N,Core)
    -- The source's flags, which GiveItemCreate does not make.
    f.fixture.sources[f.source_ids[1]].payload.flags=3
    f.treasury.m_lstItems[2].m_flags=3
    f:reload()
    local count,reason,owed=f:hand_over()
    assert(count==1 and reason==nil and owed==false,tostring(reason))
    assert(f.payee.locals.FLPR_RA_SAHROD==1 and f.member.locals.FLPR_RA_SAHROD==nil)
    assert(#f.calls==3 and f.calls[1]=='SetGlobal("FLPR_RA_SAHROD","LOCALS",1)')
    assert(f.calls[2]=='GiveItemCreate("flpri1",Myself,0,0,0)' and f.calls[3]=='DestroyItem("flpri1")')
    local given=f:given()
    assert(#given==1 and given[1].m_targetId==3 and given[1].m_sourceId==3 and given[1].m_item.m_flags==3)
    assert(#f.treasury.m_lstItems==1 and f.treasury.m_lstItems[1].address==401)
    f.update()
    assert(f.payee.slots[18].cResRef:get()=='FLPRI1' and f.payee.slots[18].m_flags==3)
    unrelated_intact(f)
    -- The condition may be checked again: the reward has left the treasury.
    count,reason,owed=f:hand_over()
    assert(count==0 and reason==nil and owed==false and #f.calls==4 and #f.queue==0)
    assert(not f.adapter:owes_handover() and f.adapter:diagnostics().owed_handovers==nil)
end

function cases.a_taken_or_unequal_reward_stays_where_it_is(N,Core)
    local f=treasury_world(N,Core)
    -- The party took it already: never a second copy.
    f.treasury.m_lstItems={f.item('MISCBI',401,0)}
    local count,reason,owed=f:hand_over()
    assert(count==0 and reason==nil and owed==false and #f.calls==1 and #f.queue==0)
    -- Another item of its name: DestroyItem could take that one instead.
    for _,items in ipairs({{f.item('FLPRI1',402,0)},{f.item('FLPRI1',402),f.item('FLPRI1',403,5)}}) do
        f.treasury.m_lstItems=items;f.calls={}
        count,reason,owed=f:hand_over()
        assert(count==0 and reason=='HANDOVER_ITEM_AMBIGUOUS' and owed==false and #f.queue==0)
        assert(#f.calls==1 and f.treasury.m_lstItems==items and #items==#items)
    end
    -- Two equal copies: one goes to the payee, one stays.
    f.treasury.m_lstItems={f.item('FLPRI1',402),f.item('FLPRI1',403)}
    count,reason=f:hand_over()
    assert(count==1 and reason==nil and #f.treasury.m_lstItems==1 and #f:given()==1)
    -- Each of two rewards, the one taken already apart.
    f=treasury_world(N,Core)
    f.assignments[2]={unit=2,source_id=f.source_ids[2],target_id=f.target_id,delivery_phase=Core.PHASE.confirmed}
    f.assignments[3]={unit=3,source_id=f.source_ids[2],target_id=f.target_id,delivery_phase=Core.PHASE.pending}
    f.treasury.m_lstItems[3]=f.item('FLPRI2',403)
    assert(f:hand_over()==2 and #f.treasury.m_lstItems==1 and #f:given()==2)
    assert(f:given()[1].m_item.cResRef:get()=='FLPRI1' and f:given()[2].m_item.cResRef:get()=='FLPRI2')
end

function cases.the_payee_is_the_one_he_talks_to_else_the_party(N,Core)
    -- No one he talks to, the protagonist dead: the next living member.
    local f=treasury_world(N,Core)
    f.king.last_talked=nil;f.payee.alive=false
    assert(f:hand_over()==1 and f:given()[1].m_targetId==6)
    assert(f.member.locals.FLPR_RA_SAHROD==1 and f.payee.locals.FLPR_RA_SAHROD==nil)
    -- A familiar would pass it on to the protagonist; pay the party itself.
    f=treasury_world(N,Core)
    local familiar=f.sprite(207,'FAMILIAR',{});familiar.m_id=7;familiar.locals={}
    familiar.m_typeAI.m_EnemyAlly=3;f.king.last_talked=familiar
    assert(f:hand_over()==1 and f:given()[1].m_targetId==3)
    assert(f.payee.locals.FLPR_RA_SAHROD==1 and familiar.locals.FLPR_RA_SAHROD==nil)
    -- No one at all: nothing moves and nothing is marked.
    f=treasury_world(N,Core)
    f.king.last_talked=nil;f.party={}
    local count,reason,owed=f:hand_over()
    assert(count==0 and reason=='HANDOVER_PAYEE_MISSING' and owed==nil and #f.calls==0)
    assert(#f.treasury.m_lstItems==2 and not f.adapter:owes_handover())
end

function cases.a_full_backpack_passes_the_reward_to_a_member_with_room(N,Core)
    -- GiveItemCreate would drop it at the full payee's feet, in an area the
    -- party leaves for good. The payee keeps the mark.
    local f=treasury_world(N,Core)
    f:full(f.payee,700)
    assert(f:hand_over()==1 and f:given()[1].m_targetId==6 and f.payee.locals.FLPR_RA_SAHROD==1)
    unrelated_intact(f)
    -- A slot is taken by an item message already queued for the member.
    f=treasury_world(N,Core)
    f:full(f.payee,700);f.payee.slots[18]=nil
    f.queue[1]=f.message(3,'OTHER')
    assert(f:hand_over()==1 and f:given()[2].m_targetId==6)
    -- Nobody has room: the payee, as the original reply would.
    f=treasury_world(N,Core)
    f:full(f.payee,700);f:full(f.member,800)
    assert(f:hand_over()==1 and f:given()[1].m_targetId==3)
end

function cases.a_pending_mutation_or_missing_treasury_owes_the_handover(N,Core)
    local f=treasury_world(N,Core)
    f.adapter.pending={}
    local count,reason,owed=f:hand_over()
    assert(count==0 and reason=='HANDOVER_MUTATION_PENDING' and owed==true)
    assert(f.payee.locals.FLPR_RA_SAHROD==1 and #f.calls==1 and #f.queue==0 and #f.treasury.m_lstItems==2)
    -- Retried once nothing is pending, from the next observed tick on, to
    -- the payee the reply marked.
    f:retry();assert(#f.calls==1 and f.adapter:owes_handover())
    f.boundary();f:retry();assert(#f.calls==1)
    f.adapter.pending=nil
    assert(f.adapter:diagnostics().owed_handovers[1]==f.target_id)
    -- A retry that waited for the mutation does not put this one off.
    f.king.last_talked=nil;f:retry()
    assert(#f.calls==3 and #f:given()==1 and f:given()[1].m_targetId==3 and #f.treasury.m_lstItems==1)
    assert(not f.adapter:owes_handover())
    f.boundary();f:retry();assert(#f.calls==3)
    -- Away from the treasury: owed, and tried again HANDOVER_RETRY_TICKS later.
    f=treasury_world(N,Core)
    f.area.m_resref=str('AR2400')
    count,reason,owed=f:hand_over()
    assert(count==0 and reason=='AREA_UNAVAILABLE' and owed==true and f.payee.locals.FLPR_RA_SAHROD==1)
    f.boundary();f:retry();assert(f.adapter:owes_handover() and #f.calls==1)
    f.area.m_resref=str('AR2300')
    for _=1,N.HANDOVER_RETRY_TICKS-1 do f.boundary();f:retry() end
    assert(f.adapter:owes_handover() and #f.calls==1)
    f.boundary();f:retry()
    assert(not f.adapter:owes_handover() and #f.calls==3 and #f:given()==1)
    -- The retry needs its marked payee, alive.
    f=treasury_world(N,Core)
    f.adapter.pending={};f:hand_over();f.adapter.pending=nil
    f.payee.locals.FLPR_RA_SAHROD=nil;f.boundary();f:retry()
    assert(f.adapter:owes_handover() and #f.calls==1)
    f.payee.locals.FLPR_RA_SAHROD=1;f.payee.alive=false
    for _=1,N.HANDOVER_RETRY_TICKS do f.boundary();f:retry() end
    assert(f.adapter:owes_handover() and #f.calls==1)
    f.payee.alive=true
    for _=1,N.HANDOVER_RETRY_TICKS do f.boundary();f:retry() end
    assert(not f.adapter:owes_handover() and #f.calls==3)
    -- A reward on its way into the treasury when the king pays follows him.
    f=treasury_world(N,Core)
    f.treasury.m_lstItems={f.item('MISCBI',401,0)}
    f.assignments[1].delivery_phase=f.phases.in_flight
    f.adapter.pending={}
    assert(select(3,f:hand_over())==true)
    f.treasury.m_lstItems[2]=f.item('FLPRI1',402)
    f.assignments[1].delivery_phase=f.phases.confirmed
    f.adapter.pending=nil;f.boundary();f:retry()
    assert(not f.adapter:owes_handover() and #f:given()==1 and #f.treasury.m_lstItems==1)
    -- Nothing placed there: nothing owed, wherever the party is.
    f=treasury_world(N,Core)
    f.area.m_resref=str('AR2400')
    f.assignments[1].delivery_phase=f.phases.pending
    count,reason,owed=f:hand_over()
    assert(count==0 and reason==nil and owed==false and not f.adapter:owes_handover())
    -- A new adapter (a loaded game) owes nothing.
    f=treasury_world(N,Core)
    f.adapter.pending={};f:hand_over();f.adapter:destroy()
    assert(not f.adapter:owes_handover())
    f.adapter.pending={};f:hand_over();f:reload()
    assert(not f.adapter:owes_handover())
end

function cases.after_payment_a_reward_not_placed_yet_goes_to_the_payee(N,Core)
    local f=treasury_world(N,Core)
    f.treasury.m_lstItems={f.item('MISCBI',401,0)}
    -- Before the payment the place is the treasury.
    assert(f.adapter:inspect_target(f.target_id,f.source_ids[1],1))
    assert(f.adapter.reservations[string.rep('c',64)].owner.id==4)
    -- The reply marks the payee and sets its event.
    f.payee.locals.FLPR_RA_SAHROD=1;f.globals.FLPR_EA_SAHROD=1
    -- Wherever the party is then.
    f.area.m_resref=str('AR2400');f.area.m_lVertSortBack={}
    assert(f.adapter:inspect_target(f.target_id,f.source_ids[1],1))
    assert(f.adapter.reservations[string.rep('c',64)].owner.id==3)
    f.boundary()
    local command=f.command(1)
    assert(f.adapter:execute(command))
    assert(#f:given()==1 and f:given()[1].m_targetId==3 and #f.treasury.m_lstItems==1)
    f.update();f.boundary()
    local receipt,reason=f.adapter:observe(command);fail(receipt,reason,'DELIVERY_SETTLING')
    f.boundary();receipt=assert(f.adapter:observe(command))
    assert(receipt.holder=='party' and receipt.slot==18 and f.payee.slots[18].cResRef:get()=='FLPRI1')
    unrelated_intact(f)
    -- Two marked: no guess.
    f.member.locals.FLPR_RA_SAHROD=1
    local ready;ready,reason=f.adapter:inspect_target(f.target_id,f.source_ids[2],2)
    fail(ready,reason,'OWNER_AMBIGUOUS')
    -- Not marked yet: the reward waits.
    f.member.locals.FLPR_RA_SAHROD=nil;f.payee.locals.FLPR_RA_SAHROD=nil
    ready,reason=f.adapter:inspect_target(f.target_id,f.source_ids[2],2)
    fail(ready,reason,'OWNER_MISSING')
end

function cases.a_presets_row_stands_for_the_treasurys_place(N,Core)
    local f=treasury_world(N,Core)
    local preset='original-target:bg2:boss-loot:mode2:target:71'
    f.fixture.targets[preset]=f.fixture.targets[f.target_id];f.fixture.targets[f.target_id]=nil
    f.fixture.targets[preset].route_target=f.target_id
    f.assignments[1].target_id=preset;f:reload()
    assert(f:hand_over()==1 and #f:given()==1)
    -- Without its route the reply's row is no place of this campaign.
    f=treasury_world(N,Core)
    f.fixture.targets[preset]=f.fixture.targets[f.target_id];f.fixture.targets[f.target_id]=nil
    f.assignments[1].target_id=preset;f:reload()
    assert(f:hand_over()==0 and #f.calls==0)
end

function cases.handover_configuration_is_strict(N,Core)
    local f=treasury_world(N,Core)
    local function refused(change,want,api)
        local fixture=clone(f.fixture);change(fixture.targets[f.target_id])
        local ok,err=pcall(N.new,api or f.api,fixture)
        assert(not ok and tostring(err):find(want,1,true),tostring(err))
    end
    refused(function(t) t.kind=nil;t.container_type=nil end,'NATIVE_HANDOVER_INVALID')
    refused(function(t) t.handover=true end,'NATIVE_HANDOVER_INVALID')
    refused(function(t) t.handover.recipient_local='FLPR_RA_OTHER' end,'NATIVE_HANDOVER_INVALID')
    refused(function(t) t.handover.event_global='SAHROD' end,'NATIVE_HANDOVER_INVALID')
    refused(function(t) t.handover.recipient_local='flpr_ra_sahrod' end,'NATIVE_HANDOVER_INVALID')
    refused(function(t)
        t.handover.event_global='FLPR_EA_'..string.rep('X',30)
        t.handover.recipient_local='FLPR_RA_'..string.rep('X',30)
    end,'NATIVE_HANDOVER_INVALID')
    refused(function(t) t.claim_global='FLPR_EM2_6' end,'NATIVE_HANDOVER_INVALID')
    local api={};for name,value in pairs(f.api) do api[name]=value end
    api.EEex_GameState_SetGlobalInt=function() end
    refused(function(t)
        t.loot_latch={activation_global='FLPR_CSAMIA',latch_global='flSamiaTreasure',latch_scope='GLOBAL'}
    end,'NATIVE_HANDOVER_INVALID',api)
    for _,name in ipairs({'EEex_Object_EvalStringAsAIBase','EEex_Sprite_GetLocalInt',
        'EEex_Sprite_GetInPortrait','EEex_GameState_GetGlobalInt'}) do
        api={};for key,value in pairs(f.api) do api[key]=value end
        api[name]=nil
        refused(function() end,'NATIVE_HANDOVER_API_MISSING',api)
    end
    -- Only the treasury's place hands over; a place left out has nothing to.
    assert(f:hand_over('original-target:bg2:mode2:target:316')==0 and #f.calls==0)
    f.fixture.targets[f.target_id].handover=nil;f:reload()
    local ok,err=pcall(f.hand_over,f)
    assert(not ok and tostring(err):find('NATIVE_HANDOVER_INVALID',1,true) and #f.calls==0)
end

function cases.an_unproven_mark_creation_or_removal_stops_the_campaign(N,Core)
    for _,case in ipairs({{'mark_fails','NATIVE_HANDOVER_MARK_FAILED'},
        {'give_fails','NATIVE_HANDOVER_CREATION_UNPROVEN'},{'give_twice','NATIVE_HANDOVER_CREATION_UNPROVEN'},
        {'destroy_none','NATIVE_HANDOVER_REMOVAL_NOT_EXACT'},{'destroy_other','NATIVE_HANDOVER_REMOVAL_NOT_EXACT'},
        {'destroy_all','NATIVE_HANDOVER_REMOVAL_NOT_EXACT'}}) do
        local f=treasury_world(N,Core)
        f[case[1]]=true
        f.treasury.m_lstItems[3]=f.item('FLPRI1',403)
        local ok,err=pcall(f.hand_over,f)
        assert(not ok and tostring(err):find(case[2],1,true),case[1]..': '..tostring(err))
    end
end

-- The king's own place (Item Randomiser's tadpoles1.fl, row 58): the king
-- is FLPRC1 of ground_world, alive, before a party member he may pay.
local KING={kind='creature',area='BG2600',script_name='FLPRC1',corpse_only=true,
    handover={event_global='FLPR_EM2_57',recipient_local='FLPR_RM2_57'},identity=string.rep('c',64)}
local function king_world(N)
    local f=ground_world(N)
    f.creature.alive=true
    f.globals={}
    f.payee=f.sprite(206,'IMOEN2',{});f.payee.m_id=6;f.payee.portrait=0;f.payee.locals={}
    f.objects[6],f.ids[f.payee]=f.payee,6
    f.party={[0]=f.payee}
    f.api.EEex_Sprite_GetInPortrait=function(index) return f.party[index] end
    f.api.EEex_Sprite_GetLocalInt=function(sprite,key) return sprite.locals and sprite.locals[key] or 0 end
    f.api.EEex_GameState_GetGlobalInt=function(key) return f.globals[key] or 0 end
    f.api.EEex_Object_EvalStringAsAIBase=function() error('NO_REPLY_HERE') end
    local drop,update=f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly,f.update
    f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly=function(action,o)
        local ref=action:match('^GiveItemCreate%("([^"]+)",Myself,0,0,0%)$')
        if not ref then return drop(action,o) end
        f.calls[#f.calls+1]=action
        f.queue[#f.queue+1]=f.message(f.ids[o],ref:upper())
    end
    -- A creature item message lands in the first empty backpack slot.
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
    f.fixture.targets[f.target_id]=clone(KING)
    f.adapter=N.new(f.api,f.fixture)
    return f
end

function cases.the_kings_place_waits_while_he_lives(N,Core,Host)
    local f=king_world(N)
    for _,source_id in ipairs({f.source_ids[1],false}) do
        local x,e=f.adapter:inspect_target(f.target_id,source_id or nil,1);fail(x,e,'TARGET_ALIVE')
    end
    assert(#f.calls==0 and #f.queue==0 and next(f.adapter.reservations)==nil)
    -- Gone (SAHKNG01.BCS destroys him when the prince's job begins), or the
    -- party elsewhere: it waits for the king who comes, as the host does.
    f.area.m_lVertSort={1,2}
    local x,e=f.adapter:inspect_target(f.target_id,f.source_ids[1],1);fail(x,e,'OWNER_MISSING')
    f.area.m_lVertSort={1,2,3};f.area.m_resref={get=function() return 'BG0100' end}
    x,e=f.adapter:inspect_target(f.target_id,f.source_ids[1],1);fail(x,e,'AREA_UNAVAILABLE')
    assert(#f.calls==0 and Host.DEFERRABLE.TARGET_ALIVE==true)
end

function cases.the_kings_place_lies_at_his_corpse_once_he_is_dead(N)
    local f=king_world(N);local a=f.adapter
    f.creature.alive=false
    local command=ground_start(f,1)
    assert(#f.calls==2 and f.calls[1]=='CreateItem("flpri1",0,0,0)' and f.calls[2]=='DropItem("flpri1",[-1.-1])')
    f.boundary();local x,e=a:observe(command);fail(x,e,'DELIVERY_SETTLING')
    f.update();f.boundary();x,e=a:observe(command);fail(x,e,'DELIVERY_SETTLING')
    x,e=a:observe(command);fail(x,e,'DELIVERY_SETTLING')
    f.boundary();local receipt=assert(a:observe(command))
    assert(receipt.holder=='ground' and #f.payee.slots==0)
    corpse_as_before(f)
    -- His corpse beside a living king of the same name: no guess.
    f=king_world(N);f.creature.alive=false
    f.objects[7]=f.sprite(207,'FLPRC1',{});f.area.m_lVertSort={1,2,3,7}
    x,e=f.adapter:inspect_target(f.target_id,f.source_ids[1],1);fail(x,e,'OWNER_AMBIGUOUS')
    assert(#f.calls==0)
end

function cases.the_kings_place_goes_to_the_one_he_pays(N)
    -- His reward reply marks the payee and sets its event: wherever the
    -- party is then, and though the king may die afterwards.
    for _,dead in ipairs({false,true}) do
        local f=king_world(N);local a=f.adapter
        f.payee.locals.FLPR_RM2_57=1;f.globals.FLPR_EM2_57=1
        f.creature.alive=not dead;f.area.m_resref={get=function() return 'AR2400' end}
        local witness=assert(a:inspect_target(f.target_id,f.source_ids[1],1))
        assert(witness.ground==nil and a.reservations[string.rep('c',64)].owner.id==6)
        f.boundary()
        local command=f.command(1)
        assert(a:execute(command))
        assert(f.calls[#f.calls]=='GiveItemCreate("flpri1",Myself,0,0,0)' and f.queue[#f.queue].m_targetId==6)
        f.update();f.boundary()
        local receipt,reason=a:observe(command);fail(receipt,reason,'DELIVERY_SETTLING')
        f.boundary();receipt=assert(a:observe(command))
        assert(receipt.holder=='party' and receipt.slot==18 and f.payee.slots[18].cResRef:get()=='FLPRI1')
        corpse_as_before(f)
    end
    -- Not marked yet: the reward waits; two marked: no guess.
    local f=king_world(N);f.globals.FLPR_EM2_57=1
    local x,e=f.adapter:inspect_target(f.target_id,f.source_ids[1],1);fail(x,e,'OWNER_MISSING')
    f.payee.locals.FLPR_RM2_57=1;f.creature.locals={FLPR_RM2_57=1}
    x,e=f.adapter:inspect_target(f.target_id,f.source_ids[1],1);fail(x,e,'OWNER_AMBIGUOUS')
    assert(#f.calls==0)
end

function cases.the_kings_place_configuration_is_strict(N,Core)
    local f=king_world(N)
    local function refused(change,want)
        local fixture=clone(f.fixture);change(fixture.targets[f.target_id],fixture)
        local ok,err=pcall(N.new,f.api,fixture,17)
        assert(not ok and tostring(err):find(want,1,true),tostring(err))
    end
    refused(function(t) t.corpse_only=false end,'NATIVE_CORPSE_ONLY_INVALID')
    refused(function(t) t.corpse_only='yes' end,'NATIVE_CORPSE_ONLY_INVALID')
    refused(function(t) t.handover=nil end,'NATIVE_CORPSE_ONLY_INVALID')
    refused(function(t) t.inventory_reward=true end,'NATIVE_CORPSE_ONLY_INVALID')
    -- Only one creature by its name: no quest's payee, no group member.
    refused(function(t)
        t.kind,t.area,t.script_name='quest','*',nil
        t.adapter_id,t.event_global,t.recipient_local='mode2-reward-v1','FLPR_EM2_58','FLPR_RM2_58'
    end,'NATIVE_CORPSE_ONLY_INVALID')
    refused(function(t)
        t.kind,t.script_name,t.eligible='group',nil,{}
        t.members={{resref='FLPRC1',script_name='FLPRC1'}}
    end,'NATIVE_CORPSE_ONLY_INVALID')
    refused(function(t) t.handover.recipient_local='FLPR_RM2_58' end,'NATIVE_HANDOVER_INVALID')
    refused(function(_,fixture) fixture.sources[f.source_ids[1]].corpse_only=true end,'NATIVE_CORPSE_ONLY_INVALID')
    -- Only the treasury's place hands rewards over at the reply.
    local ok,err=pcall(f.adapter.hand_over_container_reward,f.adapter,f.creature,f.target_id,{},Core.PHASE)
    assert(not ok and tostring(err):find('NATIVE_HANDOVER_INVALID',1,true) and #f.calls==0)
end

-- The real host and core: the reward goes into the treasury, then the king
-- pays while it is still being observed, or with the treasury out of reach.
local function treasury_host(N,Core,Host,endpoint)
    local f=treasury_world(N,Core)
    if endpoint then f.fixture.targets[f.target_id]=endpoint end
    f.treasury.m_lstItems={f.item('MISCBI',401,0)}
    f.fixture.sources[f.source_ids[2]]=nil
    f.fixture.sources[f.source_ids[1]].area='AR2300'
    f.area.m_lVertSort={1,3,5,6}
    local source_id=f.source_ids[1]
    local manifest={schema=Core.SCHEMA,state='prototype-not-playable',revision=string.rep('a',64),
        bound_revision=string.rep('b',64),world_fingerprint=string.rep('c',64),profile_id='treasury-test',
        sources={{id=source_id,quantity=1,resource_sha256=string.rep('1',64),record={
            id=source_id,record=f.fixture.sources[source_id].payload}}},
        targets={{id=f.target_id,capacity=1,resource_sha256=string.rep('2',64),record={id=f.target_id}}}}
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
    f.retries=0
    local deps={core=Core,storage=storage,resolve=function() return bundle end,
        next=function() return bundle end,seed=function() return 17 end,
        world=function() return true end,tick=function() return tick end,
        native=function(fixture)
            f.adapter=N.new(f.api,fixture)
            local retry=f.adapter.retry_handovers
            f.adapter.retry_handovers=function(self,assignments,phases)
                assert(not f.host.pending and phases==Core.PHASE)
                f.retries=f.retries+1;return retry(self,assignments,phases)
            end
            return f.adapter
        end}
    f.host=Host.new(deps);f.globals.FLPR_FRESH=1
    assert(f.host:capture_new('confirmed-character-accept'))
    function f.poll() tick=tick+1;f.host:poll();assert(f.host.status~='disabled',f.host.error) end
    function f.pay()
        return f.adapter:hand_over_container_reward(f.king,f.target_id,f.host.controller:assignments(),Core.PHASE)
    end
    return f
end

function cases.the_host_hands_over_what_the_reply_owed(N,Core,Host)
    local f=treasury_host(N,Core,Host)
    local paid
    for _=1,12 do
        f.poll()
        if not paid and f.host.pending and f.host.pending.kind=='deliver' then
            local count,reason,owed=f.pay()
            assert(count==0 and reason=='HANDOVER_MUTATION_PENDING' and owed==true)
            paid=true
        end
    end
    assert(paid and f.host.status=='complete' and not f.adapter:owes_handover())
    assert(f.host.controller:assignments()[1].delivery_phase==Core.PHASE.confirmed)
    assert(#f.treasury.m_lstItems==1 and #f:given()==1 and f:given()[1].m_targetId==3)
    f.update();assert(f.payee.slots[18].cResRef:get()=='FLPRI1')
    -- Complete and owing nothing: the host rests.
    local retries=f.retries;f.poll();assert(f.retries==retries)
end

function cases.a_complete_host_steps_on_while_a_handover_is_owed(N,Core,Host)
    local f=treasury_host(N,Core,Host)
    for _=1,12 do f.poll() end
    assert(f.host.status=='complete' and #f.treasury.m_lstItems==2)
    local retries=f.retries;f.poll();assert(f.retries==retries)
    -- The treasury out of reach when the king pays.
    f.area.m_lVertSortBack={}
    local count,reason,owed=f.pay()
    assert(count==0 and reason=='OWNER_MISSING' and owed==true)
    f.poll();f.poll()
    assert(f.retries==retries+2 and f.adapter:owes_handover() and #f:given()==0)
    f.area.m_lVertSortBack={4}
    for _=1,N.HANDOVER_RETRY_TICKS do f.poll() end
    assert(not f.adapter:owes_handover() and #f:given()==1 and #f.treasury.m_lstItems==1)
    assert(f.host.status=='complete' and f.host:inspect().native.owed_handovers==nil)
    retries=f.retries;f.poll();assert(f.retries==retries)
end

-- The king's own place on the real host: the campaign runs on while he
-- lives, and his payment brings the reward to the payee.
function cases.the_host_waits_for_the_king_until_he_pays(N,Core,Host)
    local king=clone(KING);king.area,king.script_name='AR2300','SAHKNG01'
    local f=treasury_host(N,Core,Host,king)
    for _=1,12 do f.poll() end
    assert(f.host.status~='complete' and f.host.deferred[1].reason=='TARGET_ALIVE' and #f:given()==0)
    f.payee.locals.FLPR_RM2_57=1;f.globals.FLPR_EM2_57=1
    for _=1,Host.DEFER_TICKS+12 do f.poll();f.update() end
    assert(f.host.status=='complete' and f.host.controller:assignments()[1].delivery_phase==Core.PHASE.confirmed)
    assert(f.payee.slots[18].cResRef:get()=='FLPRI1' and f.member.slots[18]==nil and #f.treasury.m_lstItems==1)
end
'''

CASE_NAMES = (
    "the_confirmed_reward_moves_from_the_treasury_to_the_payee_once",
    "a_taken_or_unequal_reward_stays_where_it_is",
    "the_payee_is_the_one_he_talks_to_else_the_party",
    "a_full_backpack_passes_the_reward_to_a_member_with_room",
    "a_pending_mutation_or_missing_treasury_owes_the_handover",
    "after_payment_a_reward_not_placed_yet_goes_to_the_payee",
    "a_presets_row_stands_for_the_treasurys_place",
    "handover_configuration_is_strict",
    "an_unproven_mark_creation_or_removal_stops_the_campaign",
    "the_kings_place_waits_while_he_lives",
    "the_kings_place_lies_at_his_corpse_once_he_is_dead",
    "the_kings_place_goes_to_the_one_he_pays",
    "the_kings_place_configuration_is_strict",
    "the_host_hands_over_what_the_reply_owed",
    "a_complete_host_steps_on_while_a_handover_is_owed",
    "the_host_waits_for_the_king_until_he_pays",
)

CALLBACK = r'''
local state=0
local Core={G={state='flpr_state'},PHASE={confirmed=2}}
local storage={get=function(_,key) assert(key=='flpr_state');return state end}
local root={}
local recorded={}
local function record(context,reason) recorded[#recorded+1]=context..': '..tostring(reason) end
%s
assert(FLPR_Handover({},6)==true) -- Not randomised.
state=2
assert(FLPR_Handover({},6)==true) -- No host yet.
local speaker,assignments,expected={},{},'original-target:bg2:mode2:target:6'
local result,disabled={1},nil
root.Host={status='running',controller={assignments=function() return assignments end},
    native={hand_over_container_reward=function(_,actor,id,rows,phases)
        assert(actor==speaker and id==expected and rows==assignments and phases.confirmed==2)
        if result.fail then error('BROKEN') end
        return result[1],result[2],result[3]
    end},
    _disable=function(self,reason) disabled=reason;self.status='disabled' end}
assert(FLPR_Handover(speaker,6)==true and root.LastHandover.count==1 and #recorded==0)
-- Owed: the host retries it, no error.
result={0,'HANDOVER_MUTATION_PENDING',true}
assert(FLPR_Handover(speaker,6)==true and root.LastHandover.owed==true and #recorded==0)
-- Left in the treasury for good: recorded.
result={0,'HANDOVER_ITEM_AMBIGUOUS',false}
assert(FLPR_Handover(speaker,6)==true and recorded[1]=='handover: HANDOVER_ITEM_AMBIGUOUS')
-- A BG1 row passes its campaign (1).
expected='original-target:bg1:mode2:target:16';result={1}
assert(FLPR_Handover(speaker,16,1)==true)
-- A failure stops the campaign; the king still pays.
expected='original-target:bg2:mode2:target:6';result={fail=true}
assert(FLPR_Handover(speaker,6)==true and root.Disabled:find('CAMPAIGN_HANDOVER_FAILED',1,true))
assert(disabled==root.Disabled and root.Host.status=='disabled')
result={1};root.LastHandover=nil
assert(FLPR_Handover(speaker,6)==true and root.LastHandover==nil)
-- Even a failing stop leaves the reply.
root.Disabled=nil;root.Host.status='running';result={fail=true}
root.Host._disable=function() error('ALSO_BROKEN') end
assert(FLPR_Handover(speaker,6)==true and root.Disabled)
state=function() error('NO_STORAGE') end
storage.get=function() error('NO_STORAGE') end
root.Disabled=nil
assert(FLPR_Handover(speaker,6)==true and root.Disabled:find('NO_STORAGE',1,true))
'''


class TreasuryHandoverRuntimeTests(unittest.TestCase):
    def test_handover_runtime(self):
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
        callback = "function FLPR_Handover" + entry.split("function FLPR_Handover", 1)[1].split(
            "    root.Disabled = nil\n", 1)[0]
        for runtime in (Lua51, Lua53, LuaJIT):
            with self.subTest(runtime=runtime.__module__):
                runtime(unpack_returned_tuples=True).execute(CALLBACK % callback)


if __name__ == "__main__":
    unittest.main()
