"""Dialogue transfers reuse delivered NPC inventory, including after loading."""
from pathlib import Path
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT
from test_campaign_native_creature import CASES as FIXTURE

ROOT = Path(__file__).resolve().parents[1]
NATIVE = (ROOT / "prototype/FLPRNat.lua").read_text(encoding="utf-8")
CORE = (ROOT / "prototype/FLPRCore.lua").read_text(encoding="utf-8")
SCENARIO = r'''
return function(N,Core)
    local f=setup(N)
    f.fixture.targets[f.target_id].inventory_reward=true
    f.adapter=N.new(f.api,f.fixture)
    local assignments={}
    for n,id in ipairs(f.source_ids) do
        assignments[n]={unit=n,source_id=id,target_id=f.target_id,delivery_phase=Core.PHASE.pending}
    end
    local calls,party={},{}
    f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly=function(action,speaker)
        assert(speaker==f.creature)
        local ref=assert(action:match('^GiveItem%("([^"]+)",LastTalkedToBy%)$'),action)
        calls[#calls+1]=ref
        for slot,item in pairs(speaker.slots) do
            if item.cResRef:get():lower()==ref then
                party[#party+1]=item;speaker.slots[slot]=nil;break
            end
        end
    end
    local function transfer()
        return f.adapter:transfer_inventory_reward(f.creature,f.target_id,assignments,Core.PHASE)
    end
    assert(f.adapter:transfer_inventory_reward(f.creature,'excluded',assignments,Core.PHASE)==-1)
    assert(f.adapter:transfer_inventory_reward(f.creature,f.target_id,{},Core.PHASE)==-1)
    -- A full target capacity waits for every delivery before transferring any.
    assignments[1].delivery_phase=Core.PHASE.held
    f.creature.slots[18]=f.item('FLPRI1',500)
    assert(transfer()==0 and #calls==0)
    assignments[2].delivery_phase=Core.PHASE.confirmed
    f.adapter.pending={};assert(transfer()==0 and #calls==0);f.adapter.pending=nil
    -- The second reward was stolen already: GiveItem cannot recreate it.
    assert(transfer()==-1 and #calls==2 and #party==1)
    assert(party[1].address==500 and calls[1]=='flpri1' and calls[2]=='flpri2')
    unrelated_intact(f)
    -- Persisted held phases suffice after a load; missing rewards stay missing.
    f.adapter:destroy();f.adapter=N.new(f.api,f.fixture)
    assert(transfer()==-1 and #party==1)
    unrelated_intact(f)
    -- Transfer the selected appearance/variant, not the source's old resref.
    f.creature.slots[18]=f.item('BOOK04',501)
    f.adapter=N.new(f.api,f.fixture,17,function()
        return {resref='book04',expiration=0,charges={0,0,0},flags=0}
    end)
    assignments[2]=nil
    assert(transfer()==-1 and #party==2 and party[2].address==501 and calls[#calls]=='book04')
    unrelated_intact(f)
end
'''


ROUTES = r'''
return function(N,Core)
    local f=setup(N)
    -- Boss Loot's rows of Aldeth's place and sword stand for the rows of his
    -- route: his reward reply names the place's row 16.
    local route,place='original-target:bg1:mode2:target:16','original-target:bg1:boss-loot:mode2:target:46'
    local sword='original-source:bg1:boss-loot:mode2:item:62'
    local endpoint=f.fixture.targets[f.target_id]
    endpoint.inventory_reward,endpoint.route_target=true,route
    f.fixture.targets[f.target_id],f.fixture.targets[place]=nil,endpoint
    f.fixture.sources[sword]={kind='guarded-script',adapter_id='mode2-reward-v1',quest='aldeth',
        resource='dlg:aldeth',selected_global='FLPR_QB1_16',consumed_global='FLPR_XB1_16',
        route_source='original-source:bg1:mode2:item:16',identity=string.rep('d',64),
        payload={resref='sw1h03',charges={0,0,0},expiration=0,flags=0}}
    f.fixture.source_gate={schema='flpr-unvisited-fixture-gate-v1',source_id=sword}
    local adapter=N.new(f.api,f.fixture)
    local assignments={}
    for n,id in ipairs(f.source_ids) do
        assignments[n]={unit=n,source_id=id,target_id=place,delivery_phase=Core.PHASE.held}
    end
    local calls={}
    f.api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly=function(action,speaker)
        assert(speaker==f.creature)
        calls[#calls+1]=assert(action:match('^GiveItem%("([^"]+)",LastTalkedToBy%)$'),action)
    end
    assert(adapter:transfer_inventory_reward(f.creature,route,assignments,Core.PHASE)==-1)
    assert(#calls==2 and calls[1]=='flpri1' and calls[2]=='flpri2')
    -- Another route's row has no endpoint in this campaign.
    assert(adapter:transfer_inventory_reward(f.creature,'original-target:bg1:mode2:target:136',
        assignments,Core.PHASE)==-1 and #calls==2)
    adapter:destroy()
    -- A route is the route's own row of the same campaign, for one row of a
    -- preset that can use it; anything else stops the campaign.
    local function refused(change)
        local fixture=clone(f.fixture)
        change(fixture)
        local ok,err=pcall(N.new,f.api,fixture)
        assert(not ok and tostring(err):find('NATIVE_ROUTE_INVALID',1,true),tostring(err))
    end
    refused(function(x) x.targets[place].route_target='original-target:bg2:mode2:target:16' end)
    refused(function(x) x.targets[place].route_target='original-target:bg1:boss-loot:mode2:target:16' end)
    refused(function(x) x.targets[place].route_target=16 end)
    refused(function(x) x.targets[place].inventory_reward=nil end)
    refused(function(x) x.targets[route],x.targets[place]=x.targets[place],nil end)
    refused(function(x)
        local other=clone(x.targets[place]);other.identity=string.rep('e',64)
        x.targets['original-target:bg1:boss-loot:mode2:target:47']=other
    end)
    refused(function(x)
        local other=clone(x.targets[place]);other.identity,other.route_target=string.rep('e',64),nil
        x.targets[route]=other
    end)
    refused(function(x) x.sources[sword].route_source='original-source:bg2:mode2:item:16' end)
    refused(function(x) x.sources[sword].route_source=route end)
    refused(function(x) x.sources[sword].adapter_id='original-quest-v1' end)
    refused(function(x) x.sources[f.source_ids[1]].route_source='original-source:bg1:mode2:item:20' end)
    -- Without its route names, the preset's place is no route's.
    local fixture=clone(f.fixture)
    fixture.targets[place].route_target=nil
    adapter=N.new(f.api,fixture)
    assert(adapter:transfer_inventory_reward(f.creature,route,assignments,Core.PHASE)==-1 and #calls==2)
end
'''


class InventoryRewardRuntimeTests(unittest.TestCase):
    def test_wait_transfer_and_reload(self):
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime(unpack_returned_tuples=True)
            with self.subTest(runtime=runtime.__module__):
                lua.execute(FIXTURE.rsplit("return cases", 1)[0] + SCENARIO)(
                    lua.execute(NATIVE), lua.execute(CORE))

    def test_a_presets_rows_stand_for_the_rows_of_their_route(self):
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime(unpack_returned_tuples=True)
            with self.subTest(runtime=runtime.__module__):
                lua.execute(FIXTURE.rsplit("return cases", 1)[0] + ROUTES)(
                    lua.execute(NATIVE), lua.execute(CORE))

    def test_callback_leaves_unmanaged_campaigns_and_waits_for_managed_loads(self):
        entry = (ROOT / "prototype/M_FLPR.lua").read_text(encoding="utf-8")
        callback = "function FLPR_Transfer" + entry.split("function FLPR_Transfer", 1)[1].split("    root.Disabled = nil\n", 1)[0]
        lua = Lua51(unpack_returned_tuples=True)
        lua.execute(r'''
            local state=0
            local Core={G={state='flpr_state'},PHASE={held=4,confirmed=2}}
            local storage={get=function(_,key) assert(key=='flpr_state');return state end}
            local root={}
        ''' + callback + r'''
            assert(FLPR_Transfer({},'excluded')==-1)
            state=2;assert(FLPR_Transfer({},'selected')==0)
            local speaker,assignments,expected={},{},'original-target:bg2:mode2:target:32'
            root.Host={controller={assignments=function() return assignments end},native={
                transfer_inventory_reward=function(_,actor,id,rows,phases)
                    assert(actor==speaker and id==expected and rows==assignments and phases.held==4)
                    return -1
                end}}
            assert(FLPR_Transfer(speaker,32)==-1)
            -- A BG1 row passes its campaign (1).
            expected='original-target:bg1:mode2:target:16'
            assert(FLPR_Transfer(speaker,16,1)==-1)
        ''')


if __name__ == "__main__":
    unittest.main()
