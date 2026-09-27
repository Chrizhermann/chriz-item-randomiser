"""Synthetic Lua wrapper checks for the authored campaign source gate.

Hooks, memory, and world observations are mocked. This does not run x86 code
or establish native engine timing, protection, or source removal.
"""
from pathlib import Path
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT


ROOT = Path(__file__).resolve().parents[1]
CORE = (ROOT / "prototype/FLPRCore.lua").read_text(encoding="utf-8")
GATE = (ROOT / "prototype/FLPRGat.lua").read_text(encoding="utf-8")
POLICY = (ROOT / "tools/probes/FLPRCampaignPolicy.lua").read_text(encoding="utf-8")
BUILD = (ROOT / "tools/probes/FLPRGateAsm.lua").read_text(encoding="utf-8")
CASES = r'''
local cases={}
local ID='are:flpru1/container/0/item/1'
local BASE, BUFFER, CAPACITY, STRIDE=0x140000000,1000,2048,128
local MEMORY_SIZE=128+2049*128+32+64*64
local function u32(value)
    return string.char(value%256,math.floor(value/256)%256,math.floor(value/65536)%256,
        math.floor(value/16777216)%256)
end
local function u16(value) return string.char(value%256,math.floor(value/256)%256) end
local function pristine_bytes(options)
    options=options or {}
    local length,containers,items=options.length or 27492,options.containers or 0x68D4,
        options.items or 0x6B14
    local container,item=options.container or 0,options.item or 1
    local name,resref=options.name or 'FLPRU1A',options.resref or 'FLPRI1'
    local x,y,container_type=options.x or 1140,options.y or 650,options.container_type or 4
    local charges,flags=options.charges or {0,0,0},options.flags or 1
    local bytes=string.rep('\0',length)
    local function put(offset,value)
        bytes=bytes:sub(1,offset)..value..bytes:sub(offset+#value+1)
    end
    put(0,'AREAV1.0')
    put(0x70,u32(containers)); put(0x74,u16(3))
    put(0x76,u16(4)); put(0x78,u32(items))
    put(0x7C,u32(19152)) -- Vertex offset, independent of the item count.
    local owner=containers+container*192
    put(owner,name..string.rep('\0',32-#name))
    put(owner+0x20,u16(x)..u16(y)..u16(container_type))
    put(owner+0x40,u32(container==0 and 1 or item)..u32(container==0 and 2 or 1))
    local record=items+item*20
    put(record,resref..string.rep('\0',8-#resref)..u16(0)..u16(charges[1])..
        u16(charges[2])..u16(charges[3])..u32(flags))
    return bytes
end
local signatures={
    {0x292A84,'48894df8488d4dc8488945a8410fb6f944884c2460'},
    {0x292F45,'8bd6413974242c0f86c40400000f1f400066660f1f84'},
    {0x190270,'418bc7488d0c80488b45884c8d2c88498bd5488d4df8'},
    {0x18B8AC,'4533e444884c24604489642468450fb6e94c8bfa4c8bf1'},
    {0x18DF94,'488b9c2470020000418bc4488b8d000100004833cce8'},
    {0x18D9C9,'e8322400004885c00f84e90000004c8b00418bd4488bc8'},
    {0x18FE05,'5556574154415541564157488d6c24804881ec80010000'},
    {0x1906FC,'418b464441ffc74103464041bc0000000044897c2450443bf80f8255fbffff4c8b6d98410f'},
}
local function clone(value)
    if type(value)~='table' then return value end
    local result={}; for key,item in pairs(value) do result[key]=clone(item) end
    return result
end
local function payload()
    return {resref='flpri1',expiration=0,charges={0,0,0},flags=1}
end
local function fixture(Core)
    local f={bytes={},values={},events={},hooks={},callbacks={},writes=0,allocations=0,
        native_calls=0,remaining=nil,world_error='AREA_UNAVAILABLE'}
    f.revision=string.rep('a',64)
    local source={id=ID,item='flpri1',resource='are:flpru1',
        owner='are:flpru1/container/0',kind='container',
        record={record_index=1,resref='flpri1',expiration=0,charges={0,0,0},flags=1},concerns={}}
    f.manifest={schema=Core.SCHEMA,state='prototype-not-playable',revision=f.revision,
        bound_revision=string.rep('b',64),world_fingerprint=string.rep('c',64),profile_id='gate-bridge',
        sources={{id=ID,quantity=1,resource_sha256=string.rep('1',64),record=source}},
        targets={{id='are:flpru1/container/1',capacity=1,resource_sha256=string.rep('2',64),
            record={id='are:flpru1/container/1',kind='container'}}}}
    f.bundle={manifest=f.manifest,fixture={revision=f.revision,
        source_gate={schema='flpr-unvisited-fixture-gate-v1',source_id=ID},
        resource_hashes={['FLPRU1.ARE']=string.rep('1',64)},
        sources={[ID]={kind='guarded-container',area='FLPRU1',script_name='FLPRU1A',
            x=1140,y=650,payload=payload(),identity=string.rep('3',64)}}}}
    f.pristine={bytes=pristine_bytes(),resource_sha256=string.rep('1',64)}
    function f:poke(address,value,size)
        for offset=0,size-1 do self.bytes[address+offset]=value%256; value=math.floor(value/256) end
    end
    function f:peek(address,size)
        local value=0
        for offset=size-1,0,-1 do value=value*256+(self.bytes[address+offset] or 0) end
        return value
    end
    function f:pokestring(address,value)
        for offset=1,#value do self.bytes[address+offset-1]=value:byte(offset) end
    end
    function f:q(offset,value) if value~=nil then self:poke(BUFFER+offset,value,8) end
        return self:peek(BUFFER+offset,8) end
    for _,site in ipairs(signatures) do
        local address=BASE+site[1]; local offset=0
        for pair in site[2]:gmatch('%x%x') do
            f.bytes[address+offset]=tonumber(pair,16); offset=offset+1
        end
    end
    f.api={EEex_Active=true}
    function f.api.EEex_GetImageBase() return BASE end
    function f.api.EEex_ReadU8(address)
        f.reads=(f.reads or 0)+1
        return f.bytes[address] or 0
    end
    function f.api.EEex_ReadU64(address) return f:peek(address,8) end
    function f.api.EEex_Read32(address) return f:peek(address,4) end
    function f.api.EEex_Malloc(size)
        local address=BUFFER+(f.allocated_bytes or 0)
        f.allocated_bytes=(f.allocated_bytes or 0)+size
        f.allocations=f.allocations+1; f.malloc_size=size; return address
    end
    function f.api.EEex_Write64(address,value)
        f.writes=f.writes+1; f:poke(address,value,8)
    end
    function f.api.EEex_Write8(address,value)
        assert(value>=-128 and value<=127,'native Write8 accepts signed bytes')
        f.byte_writes=(f.byte_writes or 0)+1
        f.bytes[address]=value%256
        if f.copy_corrupt==f.byte_writes-1 then f.bytes[address]=0xFF end
    end
    function f.api.EEex_DisableCodeProtection() f.events[#f.events+1]='unprotect' end
    function f.api.EEex_EnableCodeProtection() f.events[#f.events+1]='protect' end
    function f.api.EEex_HookBeforeRestore(address,delay,size,return_delay,assembly)
        local index=#f.hooks+1
        f.hooks[index]={address,delay,size,return_delay,assembly}
        f.events[#f.events+1]='hook'..index
        if f.fail_hook==index then error('HOOK_FAILED') end
        f:poke(address,0xE9,1); f:poke(address+1,0x1000,4)
    end
    function f.api.EEex_GameState_AddDestroyedListener(callback)
        f.callbacks[#f.callbacks+1]=callback
    end
    function f.api.EEex_GameState_GetGlobalInt(name)
        return f.values[name] or f.values[name:lower()] or 0
    end
    f.storage={}
    function f.storage:get(name) return f.values[name] or 0 end
    function f.storage:set(name,value) f.values[name]=value end
    f.controller=assert(Core.new(f.storage,function() return f.manifest end))
    function f:install(Gate,build,compile)
        self.gate=Gate.install(self.api,self.bundle,build,compile,self.pristine)
        return self.gate
    end
    function f:capture()
        self.values.FLPR_FRESH=1
        assert(self.controller:capture(self.manifest,17,'confirmed-new-game'))
    end
    function f:prepare()
        self:q(40,1); self:q(72,1) -- current missing-policy GAM, before any source access
        self.gate:prepare_new(self.bundle,self.controller)
    end
    function f:adapter()
        local native={}
        function native:guarded_source_remaining(id,area,owner)
            f.native_calls=f.native_calls+1
            f.last_world={id,area,owner}
            if f.remaining==nil then return nil,f.world_error end
            return {remaining=f.remaining,area_pointer=f.world_area_override or area,
                owner_pointer=f.world_owner_override or owner}
        end
        self.native=native
        self.adapter_instance=self.gate:adapter(self.bundle,native)
        return self.adapter_instance
    end
    function f:guard_request()
        return assert(self.controller:source_guard_request(1))
    end
    function f:bind()
        local request=self:guard_request()
        self.controller:on_observation_boundary()
        local receipt=assert(self.adapter_instance:observe_guard(request))
        assert(self.controller:confirm_source_guard(receipt))
        return receipt
    end
    function f:publish_witness(gate)
        gate=gate or self.gate
        local row=gate.buffer+128+CAPACITY*STRIDE
        local owner=gate.policy.provenance.container_offset
        local item=gate.policy.provenance.item_offset+gate.binding.record*20
        local bytes=self.pristine.bytes
        gate:_write(112,1); gate:_write(120,1)
        self:poke(row,3,8); self:poke(row+8,gate:_q(40),8)
        self:poke(row+16,1,8); self:poke(row+24,1,8)
        self:poke(row+32,4000,8); self:poke(row+40,5000,8)
        self:poke(row+48,gate.binding.record,8)
        self:pokestring(row+56,bytes:sub(owner+1,owner+32))
        self:pokestring(row+88,bytes:sub(item+1,item+20))
        self:pokestring(row+112,bytes:sub(owner+0x41,owner+0x48))
        self:pokestring(row+120,bytes:sub(owner+0x21,owner+0x28))
        return row
    end
    return f
end
local function fails(expected,action)
    local ok,error_text=pcall(action)
    assert(not ok and tostring(error_text):find(expected,1,true),tostring(error_text))
end

function cases.full_authored_bundle_and_install_contract(Core,Gate,build,compile)
    local f=fixture(Core); local gate=f:install(Gate,build,compile)
    assert(gate.installed and gate.revision==f.revision and gate.index==1)
    assert(f.allocations==1 and f.malloc_size==MEMORY_SIZE+27492)
    assert(f.byte_writes==27492 and #f.hooks==5 and #f.callbacks==1 and f:q(0)==1)
    assert(f:peek(BUFFER+MEMORY_SIZE+0x70,4)==0x68D4 and
        f:peek(BUFFER+MEMORY_SIZE+0x78,4)==0x6B14)
    assert(gate.policy.provenance.expected==BUFFER+MEMORY_SIZE and
        gate.policy.provenance.length==27492 and gate.policy.provenance.container_offset==0x68D4 and
        gate.policy.provenance.item_offset==0x6B14)
    assert(f.events[1]=='unprotect' and f.events[#f.events]=='protect')
    for index,site in ipairs({{0x292A84,8},{0x292F45,7},{0x190270,7},
        {0x18B8AC,8},{0x18DF94,8}}) do
        local call=f.hooks[index]
        assert(call[1]==BASE+site[1] and call[2]==0 and call[3]==site[2] and call[4]==site[2])
        assert(type(call[5])=='table' and type(call[5][1])=='string')
    end
end

function cases.every_signature_precedes_all_writes(Core,Gate,build,compile)
    for _,site in ipairs(signatures) do
        for _,offset in ipairs({0,#site[2]/2-1}) do
            local f=fixture(Core)
            f.bytes[BASE+site[1]+offset]=0xFF
            fails('CAMPAIGN_GATE_CODE_MISMATCH',function() f:install(Gate,build,compile) end)
            assert(f.allocations==0 and f.writes==0 and #f.hooks==0 and #f.events==0)
            assert(f.api.FLPR_CAMPAIGN_GATE==nil)
        end
    end
end

function cases.partial_hook_failure_cannot_arm_or_retry(Core,Gate,build,compile)
    for failing=1,5 do
        local f=fixture(Core); f.fail_hook=failing
        fails('HOOK_FAILED',function() f:install(Gate,build,compile) end)
        assert(f.events[#f.events]=='protect' and f:q(0)==0 and #f.hooks==failing)
        assert(f.api.FLPR_CAMPAIGN_GATE and not f.api.FLPR_CAMPAIGN_GATE.installed)
        assert(#f.callbacks==0)
        fails('CAMPAIGN_GATE_INSTALL_CONTEXT_INVALID',function() f:install(Gate,build,compile) end)
        assert(f:q(0)==0 and #f.hooks==failing)
    end
end

function cases.pristine_rejection_and_copy_failure_precede_hooks(Core,Gate,build,compile)
    for _,change in ipairs({
        function(f) f.pristine=nil end,
        function(f) f.pristine.bytes=f.pristine.bytes:sub(1,-2) end,
        function(f) f.pristine.resource_sha256=string.rep('0',64) end,
        function(f) f.pristine.bytes='WRONGV1.0'..f.pristine.bytes:sub(10) end,
        function(f) f.pristine.bytes=f.pristine.bytes:sub(1,0x70)..u32(0)..f.pristine.bytes:sub(0x75) end,
        function(f) f.pristine.bytes=f.pristine.bytes:sub(1,0x78)..u32(0)..f.pristine.bytes:sub(0x7D) end,
    }) do
        local f=fixture(Core); change(f)
        local ok=pcall(function() f:install(Gate,build,compile) end)
        assert(not ok and f.allocations==0 and f.writes==0 and #f.hooks==0 and #f.events==0)
        assert(f.api.FLPR_CAMPAIGN_GATE==nil)
    end
    for _,offset in ipairs({0,0x70,27491}) do
        local f=fixture(Core); f.copy_corrupt=offset
        fails('CAMPAIGN_GATE_PRISTINE_COPY_FAILED',function() f:install(Gate,build,compile) end)
        assert(f.allocations==1 and f.byte_writes==27492 and #f.hooks==0 and #f.events==0)
        assert(f.api.FLPR_CAMPAIGN_GATE==nil)
    end
end

function cases.reset_clears_enrollment_inputs_witness_and_invocations(Core,Gate,build,compile)
    local f=fixture(Core); local gate=f:install(Gate,build,compile); f:capture(); f:prepare()
    local context=BUFFER+128+2049*128
    f:q(48,123); f:q(56,456); f:q(112,1)
    f:poke(context+32,123,8); f:poke(context+56,456,8)
    f:poke(context+96,789,8); f:poke(context+120,999,8)
    local destroys=f:q(104)
    gate:reset()
    assert(gate.enrolled==nil and f:q(32)==0 and f:q(48)==0 and f:q(56)==0 and f:q(112)==0)
    assert(f:q(104)==destroys+1 and f:peek(context+32,8)==0 and f:peek(context+56,8)==0 and
        f:peek(context+96,8)==0 and f:peek(context+120,8)==0)
    f:q(32,1); f:q(48,7); f:q(112,1)
    f.callbacks[1]()
    assert(f:q(32)==0 and f:q(48)==0 and f:q(112)==0 and f:q(104)==destroys+2)
end

function cases.bound_config_is_strict_before_mutation(Core,Gate,build,compile)
    for _,change in ipairs({
        function(f) f.bundle.fixture.source_gate.source_id='wrong' end,
        function(f) f.bundle.fixture.sources[ID].x=0 end,
        function(f) f.bundle.manifest.sources[1].record.owner='wrong' end,
        function(f) f.bundle.manifest.sources[1].record.record.record_index=2 end,
        function(f) f.bundle.fixture.resource_hashes['FLPRU1.ARE']=string.rep('0',64) end,
        function(f) f.bundle.manifest.revision='wrong' end,
    }) do
        local f=fixture(Core); change(f)
        local ok=pcall(function() f:install(Gate,build,compile) end)
        assert(not ok and f.allocations==0 and f.writes==0 and #f.hooks==0)
    end
end

function cases.pre_new_context_is_exact_and_enrollment_is_once(Core,Gate,build,compile)
    local f=fixture(Core); f:install(Gate,build,compile)
    f:q(40,1); f:q(72,1)
    fails('CAMPAIGN_GATE_CAPTURE_REQUIRED',function() f.gate:prepare_new(f.bundle,f.controller) end)
    f:capture()
    f.values.flpr_r01=0
    fails('CAMPAIGN_GATE_CAPTURE_MISMATCH',function() f.gate:prepare_new(f.bundle,f.controller) end)
    f.values.flpr_r01=0xAAAA
    f.values.FLPR_FRESH=0
    fails('CAMPAIGN_GATE_FRESH_REQUIRED',function() f.gate:prepare_new(f.bundle,f.controller) end)
    f.values.FLPR_FRESH=1
    f:prepare()
    assert(f:q(32)==1 and f:q(40)==2 and f:q(8)==1)
    local row=BUFFER+128
    assert(f:peek(row,8)==4 and f:peek(row+8,8)==2 and f:peek(row+48,8)==1)
    fails('CAMPAIGN_GATE_NEW_CONTEXT_INVALID',function() f.gate:prepare_new(f.bundle,f.controller) end)
end

function cases.current_visited_or_nonmissing_policy_cannot_enroll(Core,Gate,build,compile)
    for _,state in ipairs({'mode','status','visited','generation','overflow'}) do
        local f=fixture(Core); f:install(Gate,build,compile); f:capture()
        f:q(40,1); f:q(72,1)
        if state=='mode' then f:q(32,1)
        elseif state=='status' then f:q(72,0)
        elseif state=='generation' then f:q(40,0)
        elseif state=='overflow' then f:q(16,1)
        else
            f:q(8,1); local row=BUFFER+128
            f:poke(row,3,8); f:poke(row+8,1,8)
        end
        local before=f:q(8)
        fails(state=='visited' and 'CAMPAIGN_GATE_ALREADY_VISITED' or 'CAMPAIGN_GATE_NEW_CONTEXT_INVALID',
            function() f.gate:prepare_new(f.bundle,f.controller) end)
        assert(f:q(8)==before and f:q(32)~=(state=='mode' and 0 or 1))
    end
end

function cases.guard_receipt_scope_epoch_destroy_and_revocation(Core,Gate,build,compile)
    local f=fixture(Core); f:install(Gate,build,compile); f:capture(); f:prepare(); f:adapter()
    local request=f:guard_request(); f.controller:on_observation_boundary()
    local bad=clone(request); bad.session=request.session; bad.token={}
    local result,reason=f.adapter_instance:observe_guard(bad)
    assert(result~=nil) -- wrapper mirrors scope; Core owns the opaque token check
    assert(not f.controller:confirm_source_guard(result))
    local receipt=assert(f.adapter_instance:observe_guard(request))
    local altered=clone(receipt); altered.session=receipt.session; altered.token=receipt.token
    altered.resource_sha256=string.rep('0',64)
    assert(not f.controller:confirm_source_guard(altered))
    assert(f.controller:confirm_source_guard(receipt) and receipt.active())
    f:q(40,3)
    assert(not receipt.active())
    f:q(40,2)
    assert(not receipt.active()) -- old closure cannot revive
    f.adapter_instance:destroy(); assert(not receipt.active())
    f.callbacks[1](); assert(f:q(32)==0 and f:q(112)==0)
    assert(not receipt.active())
end

function cases.overflow_tampered_detour_and_source_change_reject_guard(Core,Gate,build,compile)
    for _,change in ipairs({'overflow','detour','changed'}) do
        local f=fixture(Core); f:install(Gate,build,compile); f:capture(); f:prepare(); f:adapter()
        if change=='overflow' then f:q(16,1)
        elseif change=='detour' then f:poke(BASE+0x292F45,0x90,1)
        else
            local row=BUFFER+128+STRIDE
            f:q(8,2); f:poke(row,3,8); f:poke(row+8,2,8); f:poke(row+24,2,8)
        end
        local request=f:guard_request(); f.controller:on_observation_boundary()
        local result=f.adapter_instance:observe_guard(request)
        assert(result==nil and f.values.flpr_s0001==1)
    end
end

function cases.mode_two_requires_physical_source_and_no_guard(Core,Gate,build,compile)
    local f=fixture(Core); f:install(Gate,build,compile); f:capture(); f:prepare(); f:adapter()
    local witness={identity=string.rep('d',64),source_id=ID,exact_matches=1,
        quantity=1,payload=payload()}
    local command=assert(f.controller:begin_source(1,witness))
    f.controller:on_observation_boundary()
    local source_receipt=clone(command); source_receipt.session=command.session
    source_receipt.removed=1; source_receipt.remaining=0
    assert(f.controller:observe(source_receipt))
    assert(f.values.flpr_s0001==3 and not f.controller:source_guard_request(1))
    f:q(32,2); f:q(72,0); f:q(80,f.gate.policy.mask); f:q(88,2)
    assert(f.gate:_proof()==nil)
end

function cases.exact_witness_and_world_absence_commit_physical_source(Core,Gate,build,compile)
    local f=fixture(Core); f:install(Gate,build,compile); f:capture(); f:prepare(); f:adapter()
    f:bind()
    local request=assert(f.controller:guarded_source_removal_request(1))
    f.controller:on_observation_boundary()
    local result,reason=f.adapter_instance:observe_removal(request)
    assert(result==nil and reason=='SOURCE_NOT_LOADED' and f.native_calls==0)
    local row=f:publish_witness()
    result,reason=f.adapter_instance:observe_removal(request)
    assert(result==nil and reason=='SOURCE_NOT_LOADED' and f.native_calls==1)
    f.remaining=1
    result,reason=f.adapter_instance:observe_removal(request)
    assert(result==nil and reason=='CAMPAIGN_GATE_WORLD_MISMATCH')
    f.remaining=0
    f.world_owner_override='6000'
    result,reason=f.adapter_instance:observe_removal(request)
    assert(result==nil and reason=='CAMPAIGN_GATE_WORLD_MISMATCH')
    f.world_owner_override=nil
    f.world_area_override='7000'
    result,reason=f.adapter_instance:observe_removal(request)
    assert(result==nil and reason=='CAMPAIGN_GATE_WORLD_MISMATCH')
    f.world_area_override=nil
    f:poke(row+24,2,8)
    result,reason=f.adapter_instance:observe_removal(request)
    assert(result==nil and reason=='CAMPAIGN_GATE_WITNESS_INVALID')
    f:poke(row+24,1,8)
    result=assert(f.adapter_instance:observe_removal(request))
    assert(f.last_world[1]==ID and f.last_world[2]=='4000' and f.last_world[3]=='5000')
    assert(f.controller:confirm_guarded_source_removal(result))
    assert(f.values.flpr_s0001==3 and f.values.flpr_jkind==0)
end

function cases.configured_second_container_uses_its_own_bytes_and_witness(Core,Gate,build,compile)
    local f=fixture(Core)
    local id='are:flprv2/container/1/item/3'
    local new_payload={resref='flprx2',expiration=0,charges={2,3,4},flags=0}
    local source=f.manifest.sources[1].record
    source.id=id; source.item='flprx2'; source.resource='are:flprv2'
    source.owner='are:flprv2/container/1'
    source.record={record_index=3,resref='flprx2',expiration=0,charges={2,3,4},flags=0}
    f.manifest.sources[1].id=id
    f.bundle.fixture.source_gate.source_id=id
    f.bundle.fixture.sources={[id]={kind='guarded-container',area='FLPRV2',
        script_name='FLPRV2B',x=1234,y=789,container_type=5,
        payload=clone(new_payload),identity=string.rep('3',64)}}
    f.bundle.fixture.resource_hashes={['FLPRV2.ARE']=string.rep('1',64)}
    f.pristine.bytes=pristine_bytes({length=0x7200,containers=0x6200,items=0x6800,
        container=1,item=3,name='FLPRV2B',resref='FLPRX2',x=1234,y=789,
        container_type=5,charges={2,3,4},flags=0})
    local gate=f:install(Gate,build,compile)
    local source_bytes=gate.policy.source
    assert(gate.binding.id==id and gate.binding.container==1 and gate.binding.record==3)
    assert(gate.policy.provenance.container_offset==0x6200+192 and
        gate.policy.provenance.item_offset==0x6800 and gate.policy.provenance.length==0x7200)
    assert(source_bytes.area=='flprv2' and source_bytes.index==3 and
        source_bytes.name=='FLPRV2B'..string.rep('\0',25) and
        source_bytes.payload=='FLPRX2'..string.rep('\0',2)..u16(0)..u16(2)..u16(3)..u16(4)..u32(0))
    local assembly=f.hooks[3][5][1]
    local function literal(bytes)
        local result={}
        for i=#bytes,1,-1 do result[#result+1]=string.format('%02x',bytes:byte(i)) end
        return '0x'..table.concat(result)
    end
    assert(assembly:find('cmp r15d, 3',1,true) and
        assembly:find('mov r11, '..literal('FLPRV2'..string.rep('\0',2)),1,true) and
        assembly:find('mov rdx, '..literal(source_bytes.payload:sub(1,8)),1,true) and
        assembly:find('mov rdx, '..literal(source_bytes.inventory),1,true) and
        assembly:find('mov rdx, '..literal(source_bytes.position),1,true))
    f:capture(); f:prepare(); f:adapter(); f:bind()
    local request=assert(f.controller:guarded_source_removal_request(1))
    f.controller:on_observation_boundary()
    local row=f:publish_witness(); f.remaining=0
    f:poke(row+98,9,1)
    local result,reason=f.adapter_instance:observe_removal(request)
    assert(result==nil and reason=='CAMPAIGN_GATE_WITNESS_INVALID')
    f:poke(row+98,2,1)
    result=assert(f.adapter_instance:observe_removal(request))
    assert(f.last_world[1]==id and f.controller:confirm_guarded_source_removal(result))
    assert(f.values.flpr_s0001==3)
end

function cases.multiple_sources_share_hooks_but_keep_separate_receipts(Core,Gate,build,compile)
    local f=fixture(Core)
    local second='are:flpru1/container/0/item/2'
    local source=clone(f.manifest.sources[1])
    source.id=second; source.record.id=second; source.record.record.record_index=2
    source.record.record.resref='flprx1'; f.manifest.sources[2]=source
    f.manifest.targets[1].capacity=2
    local endpoint=clone(f.bundle.fixture.sources[ID]); endpoint.payload.resref='flprx1'
    f.bundle.fixture.sources[second]=endpoint
    f.bundle.fixture.source_gate.source_id=nil
    f.bundle.fixture.source_gate.source_ids={ID,second}
    local raw='FLPRX1\0\0'..string.rep('\0',8)..u32(1)
    local offset=0x6B14+40
    f.pristine.bytes=f.pristine.bytes:sub(1,offset)..raw..f.pristine.bytes:sub(offset+21)
    f:install(Gate,build,compile); local members=f.gate.members
    assert(#members==2 and #f.hooks==5 and #f.callbacks==1 and f.allocations==2)
    assert(members[1].buffer~=members[2].buffer and f.byte_writes==#f.pristine.bytes)
    assert(members[1].policy.provenance.expected==members[2].policy.provenance.expected)
    f:capture()
    for _,member in ipairs(members) do member:_write(40,1); member:_write(72,1) end
    assert(f.gate:prepare_new(f.bundle,f.controller)); f:adapter()
    local receipts={}
    for index=1,2 do
        local request=assert(f.controller:source_guard_request(index))
        f.controller:on_observation_boundary()
        receipts[index]=assert(f.adapter_instance:observe_guard(request))
        assert(f.controller:confirm_source_guard(receipts[index]))
    end
    assert(f.values.flpr_s0001==6 and f.values.flpr_s0002==6)
    local request=assert(f.controller:guarded_source_removal_request(2))
    f:publish_witness(members[2]); f.remaining=0
    f.controller:on_observation_boundary()
    assert(f.controller:confirm_guarded_source_removal(assert(f.adapter_instance:observe_removal(request))))
    assert(f.values.flpr_s0001==6 and f.values.flpr_s0002==3 and f.last_world[1]==second)
    assert(receipts[1].active() and not members[1]:inspect().witness)
    f.adapter_instance:destroy()
    assert(not receipts[1].active() and not receipts[2].active())
    f.gate:reset() -- New Game calls the group directly, before GAM loading.
    for _,member in ipairs(members) do assert(member:_q(32)==0 and member:_q(112)==0) end
    f.callbacks[1]()
    for _,member in ipairs(members) do assert(member:_q(104)==2) end
end

function cases.profiles_share_hooks_and_select_receipts_by_frozen_revision(Core,Gate,build,compile)
    local f=fixture(Core)
    local original=f.bundle; local changed=clone(original)
    changed.manifest.revision=string.rep('e',64)
    changed.fixture.revision=changed.manifest.revision
    f.gate=Gate.install(f.api,{original,changed},build,compile,f.pristine)
    assert(#f.gate.members==2 and #f.hooks==5 and #f.callbacks==1)
    assert(f.byte_writes==#f.pristine.bytes)
    local stale
    for _,bundle in ipairs({changed,original}) do
        f.gate:reset(); f.values={}
        if stale then assert(not stale.active()) end
        f.bundle=bundle; f.manifest=bundle.manifest
        f.controller=assert(Core.new(f.storage,function() return f.manifest end))
        f:capture()
        for _,member in ipairs(f.gate.members) do member:_write(40,1); member:_write(72,1) end
        assert(f.gate:prepare_new(bundle,f.controller)); f:adapter()
        stale=f:bind()
        for _,member in ipairs(f.gate.members) do
            assert(member:_q(32)==(member.revision==bundle.manifest.revision and 1 or 0))
        end
        local inspection=f.gate:inspect()
        assert(inspection.sources[ID].revision==bundle.manifest.revision)
        local wrong=clone(f:guard_request()); wrong.revision=string.rep('f',64)
        assert(not f.adapter_instance:observe_guard(wrong))
    end
    f.callbacks[1]()
    assert(not stale.active() and f.gate.active_revision==nil)
end

return cases
'''


class CampaignGateBridgeTests(unittest.TestCase):
    def test_lua_cases(self):
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime(unpack_returned_tuples=True)
            core, gate, compile_policy, build = (
                lua.execute(CORE), lua.execute(GATE), lua.execute(POLICY), lua.execute(BUILD))
            cases = lua.execute(CASES)
            for name in sorted(cases.keys()):
                with self.subTest(runtime=runtime.__module__, case=name):
                    cases[name](core, gate, build, compile_policy)


if __name__ == '__main__':
    unittest.main()
