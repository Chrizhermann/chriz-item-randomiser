"""Focused Lua checks for the experimental shop bridge; no game is opened."""
from pathlib import Path
import sys
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT21


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from engine_sites import ENGINE_LUA, load  # noqa: E402

SHOP = (ROOT / "prototype" / "FLPRShop.lua").read_text(encoding="utf-8")
CASES = ENGINE_LUA + r'''
local cases = {}
local function setup()
    local f = {base=0x100000, buffer=0x200000,
        bytes={}, globals={}, hooks={}, calls={}, action_mode='success'}
    local function write(address, value, size)
        for i=0,size-1 do
            f.bytes[address+i]=value%256
            value=math.floor(value/256)
        end
    end
    local function read(address, size)
        local value=0
        for i=size-1,0,-1 do value=value*256+(f.bytes[address+i] or 0) end
        return value
    end
    f.read=read
    local api={}
    engine_image(api,f.base,function(address,byte) write(address,byte,1) end)
    api.EEex_Malloc=function() return f.buffer end
    api.EEex_Write8=function(address,value) write(address,value,1) end
    api.EEex_Write16=function(address,value) write(address,value,2) end
    api.EEex_Write32=function(address,value) write(address,value,4) end
    api.EEex_Write64=function(address,value) write(address,value,8) end
    api.EEex_ReadU8=function(address) return read(address,1) end
    api.EEex_Read32=function(address)
        local value=read(address,4)
        return value>=2147483648 and value-4294967296 or value
    end
    api.EEex_ReadU64=function(address) return read(address,8) end
    api.EEex_FlattenTable=function(parts) return parts end
    api.EEex_GenLuaCall=function() return 'call fixture' end
    api.EEex_DisableCodeProtection=function() end
    api.EEex_EnableCodeProtection=function() end
    api.EEex_HookBeforeCall=function(address,code) f.hooks[address]=code end
    api.EEex_HookBeforeRestore=function(address,_,__,___,code) f.hooks[address]=code end
    api.EEex_HookBeforeConditionalJump=function(address,_,code) f.hooks[address]=code end
    api.EEex_HookBetweenRestore=function(address,a,b,c,d,e,code)
        assert(a==0 and b==4 and c==4 and d==4 and e==8)
        f.hooks[address]=code
    end
    api.EEex_GameState_AddDestroyedListener=function(callback) f.on_destroyed=callback end
    api.EEex_GameState_GetGlobalInt=function(key) return f.globals[key] or 0 end
    api.EEex_GameState_SetGlobalInt=function(key,value) f.globals[key]=value end
    api.EEex_Sprite_GetInPortrait=function() return f.sprite end
    api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly=function(action,sprite)
        f.calls[#f.calls+1]={action=action,sprite=sprite,
            store=read(f.buffer+8,8),item=read(f.buffer+16,8),
            expiration=read(f.buffer+24,2),charges={read(f.buffer+26,2),
                read(f.buffer+28,2),read(f.buffer+30,2)},flags=read(f.buffer+32,4)}
        if f.action_mode=='error' then error('fixture action failed') end
        write(f.buffer+40,4,8);write(f.buffer+64,5,8)
        write(f.buffer+48,f.action_mode=='collision' and 4 or 3,8)
    end
    f.sprite={id=7}
    api.EngineGlobals={g_pBaldurChitin={cNetwork={m_bConnectionEstablished=0}}}
    f.api=api
    f.source_id='are:area/container/0/item/0'
    f.target_id='sto:shop'
    f.payload={resref='synth001',expiration=0,charges={2,3,4},flags=1}
    f.fixture={sources={[f.source_id]={payload=f.payload}},
        targets={[f.target_id]={kind='store',store='shop'}},
        shop_prices={synth001=375}}
    f.assignment={unit=7,source_id=f.source_id,target_id=f.target_id,delivery_phase=0}
    f.controller={assignments=function() return {f.assignment} end}
    return f
end
local function ref(f,address)
    local chars={}
    for i=0,7 do
        local byte=f.read(address+i,1)
        if byte==0 then break end
        chars[#chars+1]=string.char(byte)
    end
    return table.concat(chars)
end

function cases.bind_ignores_kept_and_lost_rewards_and_prices_tome_variant(Shop)
    local f=setup(); local shop=Shop.install(f.api)
    f.assignment.payload={resref='book03',expiration=0,charges={0,0,0},flags=0}
    f.fixture.shop_prices.book03=1200
    f.controller.assignments=function() return {
        {unit=1,source_id=f.source_id,outcome='kept'},
        {unit=2,source_id=f.source_id,outcome='lost'},f.assignment} end
    shop:bind(f.fixture,f.controller)
    assert(#shop.entries==1 and shop.entries[1].item=='book03')
    assert(f.read(shop.entries[1].address+16,4)==1200)
end

function cases.bind_freezes_assignments_and_prices(Shop)
    local f=setup();local shop=Shop.install(f.api)
    local hooks=0
    for _ in pairs(f.hooks) do hooks=hooks+1 end
    assert(hooks==6 and f.hooks[f.base+0x2E7E00])
    local selection=table.concat(f.hooks[f.base+0x2E7E00])
    assert(selection:find('cmp eax,dword ptr ds:[rbx+0x18]',1,true))
    assert(selection:find('mov dword ptr ds:[r9+0x10],0',1,true))
    shop:bind(f.fixture,f.controller)
    local entry=assert(shop.by_unit[7])
    assert(#shop.entries==1 and f.read(f.buffer+72,8)==1)
    assert(entry.store=='shop' and entry.item=='synth001' and entry.key=='FLPR_P0007')
    assert(ref(f,entry.address)=='SHOP' and ref(f,entry.address+8)=='SYNTH001')
    assert(f.read(entry.address+16,4)==375 and f.read(entry.address+24,8)==0)
    f.fixture.shop_prices.synth001=999
    assert(f.read(entry.address+16,4)==375)
end
function cases.same_resref_in_distinct_shops_tracks_each_unit_purchase(Shop)
    local f=setup()
    local second_source,second_target='are:other/container/0/item/0','sto:other'
    f.fixture.sources[second_source]={payload=f.payload}
    f.fixture.targets[second_target]={kind='store',store='other'}
    local assignments={f.assignment,
        {unit=8,source_id=second_source,target_id=second_target,delivery_phase=0}}
    f.controller={assignments=function() return assignments end}
    local shop=Shop.install(f.api);shop:bind(f.fixture,f.controller)
    local first,second=assert(shop.by_unit[7]),assert(shop.by_unit[8])
    assert(#shop.entries==2 and first~=second and first.key=='FLPR_P0007' and second.key=='FLPR_P0008')
    assert(ref(f,first.address)=='SHOP' and ref(f,second.address)=='OTHER')
    assert(ref(f,first.address+8)=='SYNTH001' and ref(f,second.address+8)=='SYNTH001')
    assert(shop:deliver(f.fixture.targets[f.target_id],f.payload,7))
    assert(shop:deliver(f.fixture.targets[second_target],f.payload,8))
    assert(f.calls[1].action=='AddStoreItem("shop","synth001",1,1)' and
        f.calls[2].action=='AddStoreItem("other","synth001",1,1)')
    assert(f.read(first.address+24,8)==1 and f.read(second.address+24,8)==1)
    f.api.EEex_Write64(first.address+24,2);shop:sync_purchases()
    assert(f.globals.FLPR_P0007==1 and f.globals.FLPR_P0008==nil)
    assignments[1].delivery_phase=2;assignments[2].delivery_phase=2
    shop:bind(f.fixture,f.controller)
    assert(f.read(shop.by_unit[7].address+24,8)==2 and f.read(shop.by_unit[8].address+24,8)==1)
end

function cases.purchased_flag_retires_surcharge(Shop)
    local f=setup();f.globals.FLPR_P0007=1
    local shop=Shop.install(f.api);shop:bind(f.fixture,f.controller)
    local entry=shop.by_unit[7]
    assert(f.read(entry.address+24,8)==2)
    local price_hook=table.concat(f.hooks[f.base+0x24FB9A])
    assert(price_hook:find('cmp qword ptr ds:[r10+24],1',1,true))
    f.api.EEex_Write64(entry.address+24,2)
    shop:sync_purchases()
    assert(f.globals.FLPR_P0007==1)
end

function cases.reset_discards_campaign_entries(Shop)
    local f=setup();local shop=Shop.install(f.api)
    shop:bind(f.fixture,f.controller)
    assert(#shop.entries==1)
    f.on_destroyed()
    assert(#shop.entries==0 and next(shop.by_unit)==nil)
    assert(f.read(f.buffer,8)==0 and f.read(f.buffer+72,8)==0)
end

function cases.deliver_publishes_exact_payload_then_activates_unit(Shop)
    local f=setup();local shop=Shop.install(f.api);shop:bind(f.fixture,f.controller)
    assert(shop:deliver(f.fixture.targets[f.target_id],f.payload,7))
    local call=assert(f.calls[1])
    assert(call.action=='AddStoreItem("shop","synth001",1,1)' and call.sprite==f.sprite)
    assert(call.expiration==0 and call.charges[1]==2 and call.charges[2]==3 and
        call.charges[3]==4 and call.flags==1)
    assert(ref(f,f.buffer+8)=='SHOP' and ref(f,f.buffer+16)=='SYNTH001')
    assert(f.read(shop.by_unit[7].address+24,8)==1 and f.read(f.buffer,8)==0)
    assert(f.globals.FLPR_SH0007==5 and f.read(shop.by_unit[7].address+40,8)==5)
    f.assignment.delivery_phase=2
    shop:bind(f.fixture,f.controller)
    assert(f.read(shop.by_unit[7].address+40,8)==5)
    assert(shop.last_delivery.unit==7 and shop.last_delivery.before_count==4 and
        shop.last_delivery.after_count==5)
end

function cases.deliver_identifies_the_shop_row_and_keeps_other_flags(Shop)
    -- Sources are mostly a creature's or a container's unidentified item.
    for source,row in pairs({[0]=1,[1]=1,[2]=3,[3]=3}) do
        local f=setup();f.payload.flags=source
        local shop=Shop.install(f.api);shop:bind(f.fixture,f.controller)
        assert(shop:deliver(f.fixture.targets[f.target_id],f.payload,7))
        local call=assert(f.calls[1])
        assert(call.action==string.format('AddStoreItem("shop","synth001",1,%d)',row) and call.flags==row)
        assert(f.payload.flags==source)
    end
end

function cases.failed_or_unproven_delivery_does_not_activate(Shop)
    for _,mode in ipairs({'error','collision'}) do
        local f=setup();f.action_mode=mode
        local shop=Shop.install(f.api);shop:bind(f.fixture,f.controller)
        local ok,reason=shop:deliver(f.fixture.targets[f.target_id],f.payload,7)
        assert(ok==nil and type(reason)=='string')
        if mode=='collision' then assert(reason=='SHOP_INSERTION_UNPROVEN')
        else assert(reason:find('fixture action failed',1,true)) end
        assert(f.read(shop.by_unit[7].address+24,8)==0 and f.read(f.buffer,8)==0)
        assert(shop.last_delivery==nil and f.globals.FLPR_P0007==nil)
    end
end

function cases.network_session_or_unknown_state_never_delivers(Shop)
    for _,state in ipairs({1,true,'unbound'}) do
        local f=setup()
        local shop=Shop.install(f.api);shop:bind(f.fixture,f.controller)
        if state=='unbound' then f.api.EngineGlobals.g_pBaldurChitin.cNetwork=nil
        else f.api.EngineGlobals.g_pBaldurChitin.cNetwork.m_bConnectionEstablished=state end
        local ok,reason=shop:deliver(f.fixture.targets[f.target_id],f.payload,7)
        assert(ok==nil and reason=='SHOP_MULTIPLAYER_UNSUPPORTED' and #f.calls==0)
        assert(f.read(f.buffer,8)==0 and f.read(shop.by_unit[7].address+24,8)==0)
    end
    local f=setup();f.api.EngineGlobals.g_pBaldurChitin.cNetwork.m_bConnectionEstablished=false
    local shop=Shop.install(f.api);shop:bind(f.fixture,f.controller)
    assert(shop:deliver(f.fixture.targets[f.target_id],f.payload,7) and #f.calls==1)
end

function cases.unmatched_location_stops_before_any_change(Shop)
    local f=setup()
    f.api.FLPR_ENGINE_SITES.offsets.shop_merge={}
    local ok,why=pcall(Shop.install,f.api)
    assert(not ok and why=='SHOP_CODE_MISMATCH:shop_merge:not-found',why)
    assert(next(f.hooks)==nil and f.read(f.buffer,8)==0)
end
return cases
'''


CASE_NAMES = sorted(Lua51().execute(CASES).keys())


def make_case(name):
    def run(self):
        lua = self.runtime(unpack_returned_tuples=True)
        lua.execute(CASES)[name](load(lua, SHOP))
    return run


for label, runtime in (("Lua51", Lua51), ("Lua53", Lua53), ("LuaJIT21", LuaJIT21)):
    globals()[label + "NativeShopTests"] = type(label + "NativeShopTests", (unittest.TestCase,),
        {"runtime": runtime, **{"test_" + name: make_case(name) for name in CASE_NAMES}})


if __name__ == "__main__":
    unittest.main()
