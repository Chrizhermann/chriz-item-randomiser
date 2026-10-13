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


class InventoryRewardRuntimeTests(unittest.TestCase):
    def test_wait_transfer_and_reload(self):
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime(unpack_returned_tuples=True)
            with self.subTest(runtime=runtime.__module__):
                lua.execute(FIXTURE.rsplit("return cases", 1)[0] + SCENARIO)(
                    lua.execute(NATIVE), lua.execute(CORE))

    def test_callback_leaves_unmanaged_campaigns_and_waits_for_managed_loads(self):
        entry = (ROOT / "prototype/M_FLPR.lua").read_text(encoding="utf-8")
        callback = "function FLPR_Transfer" + entry.split("function FLPR_Transfer", 1)[1].split("    root.QA =", 1)[0]
        lua = Lua51(unpack_returned_tuples=True)
        lua.execute(r'''
            local state=0
            local Core={G={state='flpr_state'},PHASE={held=4,confirmed=2}}
            local storage={get=function(_,key) assert(key=='flpr_state');return state end}
            local root={}
        ''' + callback + r'''
            assert(FLPR_Transfer({},'excluded')==-1)
            state=2;assert(FLPR_Transfer({},'selected')==0)
            local speaker,assignments={},{}
            root.Host={controller={assignments=function() return assignments end},native={
                transfer_inventory_reward=function(_,actor,id,rows,phases)
                    assert(actor==speaker and id=='original-target:bg2:mode2:target:32' and rows==assignments and phases.held==4)
                    return -1
                end}}
            assert(FLPR_Transfer(speaker,32)==-1)
        ''')


if __name__ == "__main__":
    unittest.main()
