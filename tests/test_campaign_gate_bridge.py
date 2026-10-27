"""Synthetic campaign source-gate wrapper and jump-chain checks.

World observations and EEex services are mocked; the focused chain check runs
x86 in an emulator. Neither establishes native engine timing or source removal.
"""
from pathlib import Path
import re
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'work/source-gate-20260927/analysis-deps'))
sys.path.insert(0, str(ROOT / 'tests'))

from keystone import Ks, KS_ARCH_X86, KS_MODE_64
from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT
from unicorn import Uc, UC_ARCH_X86, UC_MODE_64, UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_RAX, UC_X86_REG_RSP, UC_X86_REG_EFLAGS

from engine_sites import ENGINE_LUA, load, site as load_site


CORE = (ROOT / "prototype/FLPRCore.lua").read_text(encoding="utf-8")
GATE = (ROOT / "prototype/FLPRGat.lua").read_text(encoding="utf-8")
POLICY = (ROOT / "tools/probes/FLPRCampaignPolicy.lua").read_text(encoding="utf-8")
BUILD = (ROOT / "tools/probes/FLPRGateAsm.lua").read_text(encoding="utf-8")
CASES = ENGINE_LUA + r'''
local cases={}
local ID='are:flpru1/container/0/item/1'
local BASE, BUFFER, CAPACITY, STRIDE=0x140000000,1000,2048,128
local MEMORY_SIZE=128+2049*128+32+64*64
-- A member's policy table (19 rows of 48 bytes) and then its pristine copies
-- follow its native state (FLPRGat.lua).
local POLICY_TABLE,TABLE=MEMORY_SIZE,19*48
local BODY=MEMORY_SIZE+1024
-- Hook slots, the saved-store record and other small blocks come from their
-- own region, so a member's buffer is the first allocation at BUFFER.
local SMALL=BASE+0x4000000
-- Locations with code or the saved-store record, in hook order (FLPRGat.lua).
local HOOKED={'gate_enter','gate_capture','gate_item','gate_area_enter','gate_area_exit',
    'gate_actor1','gate_actor2','gate_store','gate_saved_store','gate_store_done','gate_template'}
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
-- The locations a container source needs (FLPRSite.lua keys).
local GATE_KEYS={'gate_enter','gate_capture','gate_item','gate_area_enter','gate_area_exit',
    'gate_container_call','gate_container_entry','gate_container_skip'}
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
    f.api={EEex_Active=true}
    engine_image(f.api,BASE,function(address,byte) f.bytes[address]=byte end)
    function f.api.EEex_GetImageBase() return BASE end
    function f.api.EEex_ReadU8(address)
        f.reads=(f.reads or 0)+1
        return f.bytes[address] or 0
    end
    function f.api.EEex_ReadU64(address) return f:peek(address,8) end
    function f.api.EEex_Read32(address)
        local value=f:peek(address,4)
        return value>=2147483648 and value-4294967296 or value
    end
    function f.api.EEex_Malloc(size)
        if size<MEMORY_SIZE then
            local address=SMALL+(f.small_bytes or 0)
            f.small_bytes=(f.small_bytes or 0)+size+(8-size%8)%8
            f.small_allocations=(f.small_allocations or 0)+1
            return address
        end
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
    -- Model EEex's table flattening and separate near allocations. The real
    -- preprocessor rejects nested assembly tables, so this mock does too.
    function f.api.EEex_FlattenTable(values)
        local result={}
        local function append(value)
            if type(value)=='table' then
                for _,nested in ipairs(value) do append(nested) end
            else result[#result+1]=value end
        end
        append(values); return result
    end
    f.api.EEex_HookIntegrityWatchdog_HookEnter={'; watchdog entry\n'}
    function f.api.EEex_HookIntegrityWatchdog_IgnoreRegisters() end
    function f.api.EEex_HookIntegrityWatchdog_DefaultIgnoreStack() end
    function f.api.EEex_RunWithAssemblyLabels(labels,action)
        assert(labels[1][1]=='hook_address')
        f.hook_address=labels[1][2]
        local result=action(); f.hook_address=nil; return result
    end
    function f.api.EEex_StoreBytesAssembly(address,size)
        local bytes={'.DB '}
        for offset=0,size-1 do
            if offset>0 then bytes[#bytes+1]=', ' end
            bytes[#bytes+1]=f:peek(address+offset,1)
        end
        bytes[#bytes+1]='\n'; return bytes
    end
    f.jit={}
    function f.api.EEex_JITNear(assembly)
        assert(f.hook_address,'every chunk must retain native unwind context')
        for _,value in ipairs(assembly) do
            assert(type(value)=='string' or type(value)=='number','JIT assembly must be flat')
        end
        local address=BASE+0x800000+(f.jit_count or 0)*0x10000
        f.jit_count=(f.jit_count or 0)+1
        f.jit[address]=table.concat(assembly)
        return address
    end
    function f.api.EEex_JITAt(address,assembly)
        local code=table.concat(assembly)
        local target=assert(tonumber(code:match('^jmp short (%d+)')))
        local _,padding=code:gsub('nop\n','')
        local size=5+padding; local index=#f.hooks+1
        f.hooks[index]={address,0,size,size,entry=target}
        f.events[#f.events+1]='hook'..index
        if f.fail_hook==index then error('HOOK_FAILED') end
        f:poke(address,0xE9,1); f:poke(address+1,target-address-5,4)
    end
    -- The code a hooked location runs now: its entry jumps through a slot to
    -- the newest chain, whose chunks end in jumps to the next and the tail.
    function f:chain(index)
        local entry=self.jit[self.hooks[index].entry]
        local slot=assert(tonumber(entry:match('mov rax, (%d+)\nmov rax, qword ptr ds:%[rax%]\njmp rax')))
        local chunks,next_address={entry},self:peek(slot,8)
        while self.jit[next_address] do
            local chunk=self.jit[next_address]; chunks[#chunks+1]=chunk
            assert(#chunks<=self.jit_count+1,'chain cycle')
            next_address=tonumber(chunk:match('jmp (%d+)%s*$'))
        end
        return table.concat(chunks,'\n')
    end
    -- Whether a hooked location still runs only its empty chain.
    function f:empty(index)
        local entry=self.jit[self.hooks[index].entry]
        local slot=assert(tonumber(entry:match('mov rax, (%d+)\nmov rax, qword ptr ds:%[rax%]\njmp rax')))
        local code=self.jit[self:peek(slot,8)]
        return code:match('^pop rax\njmp (%d+)\n$')~=nil and
            self.jit[tonumber(code:match('jmp (%d+)'))]:find('#MANUAL_HOOK_EXIT(0)',1,true)==1
    end
    -- Whether a hooked location runs only what launch placed there: its empty
    -- chain, or the saved-store record, and then the original instructions.
    function f:idle(index)
        local entry=self.jit[self.hooks[index].entry]
        local slot=assert(tonumber(entry:match('mov rax, (%d+)\nmov rax, qword ptr ds:%[rax%]\njmp rax')))
        local address=self:peek(slot,8)
        while self.jit[address] do
            local chunk=self.jit[address]
            if chunk:find('#MANUAL_HOOK_EXIT(0)',1,true)==1 then return true end
            if not chunk:match('^pop rax\njmp %d+\n$') and not chunk:find('gate_recorder_done',1,true) then
                return false
            end
            address=tonumber(chunk:match('jmp (%d+)%s*$'))
        end
        return false
    end
    -- EEex's generated Lua call; the GAM hook passes the data and its size.
    function f.api.EEex_GenLuaCall(name,meta)
        assert(type(meta)=='table' and #meta.args==2)
        f.lua_calls=(f.lua_calls or 0)+1
        return {'; lua call '..name..'\n'}
    end
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
    assert(f.allocations==1 and f.malloc_size==BODY+27492)
    assert(f.byte_writes==TABLE+27492 and #f.hooks==#HOOKED and #f.callbacks==1 and f:q(0)==1)
    assert(f:peek(BUFFER+BODY+0x70,4)==0x68D4 and f:peek(BUFFER+BODY+0x78,4)==0x6B14)
    assert(gate.policy.provenance.expected==BUFFER+BODY and
        gate.policy.provenance.length==27492 and gate.policy.provenance.container_offset==0x68D4 and
        gate.policy.provenance.item_offset==0x6B14)
    -- The policy's field table follows the native state; the scan names it.
    assert(gate.policy.table_address==BUFFER+POLICY_TABLE and f:peek(BUFFER+POLICY_TABLE,4)==0x52504C46 and
        f:chain(2):find('mov r8, '..string.format('%.0f',BUFFER+POLICY_TABLE)..'\n',1,true))
    assert(f.events[1]=='unprotect' and f.events[#f.events]=='protect')
    -- Every location is hooked; a container source's code runs at the first five.
    for index,key in ipairs(HOOKED) do
        local engine=f.api.FLPR_ENGINE_SITES
        local call=f.hooks[index]
        assert(call[1]==engine.base+engine.offsets[key][1] and call[2]==0)
        assert(f:idle(index)==(index>5) and f:empty(index)==(index>5 and key~='gate_saved_store'),key)
    end
    for index,size in ipairs({8,7,7,8,8}) do
        assert(f.hooks[index][3]==size and f.hooks[index][4]==size)
        assert(f:chain(index):find('source1_',1,true))
    end
    -- Only the GAM hook calls back into Lua, before its chain.
    assert(f.lua_calls==1 and f:chain(1):find('; lua call FLPR_CampaignGateGame\n',1,true))
    for index=2,#HOOKED do assert(not f:chain(index):find('; lua call',1,true)) end
end

-- A location this engine lacks is never written, and fails only the revisions
-- that need it, before any of their code is built or reachable.
function cases.every_location_is_checked_before_any_write(Core,Gate,build,compile,Site)
    local function address(f,key)
        local engine=f.api.FLPR_ENGINE_SITES
        return engine.base+engine.offsets[key][1]
    end
    local function untouched(f,key)
        assert(f.allocations==0 and f.api.FLPR_CAMPAIGN_GATE and next(f.api.FLPR_CAMPAIGN_GATE.built)==nil)
        for index=1,#f.hooks do assert(f:idle(index)) end
        if key then
            local engine=f.api.FLPR_ENGINE_SITES
            for _,offset in ipairs(engine.offsets[key]) do
                for _,call in ipairs(f.hooks) do assert(call[1]~=engine.base+offset) end
            end
            assert(not f.api.FLPR_CAMPAIGN_GATE.hooked[key])
        end
    end
    for _,key in ipairs(GATE_KEYS) do
        local spec=Site.SPECS[key]
        local checked={}
        for at=1,#spec.bytes,2 do
            if spec.bytes:sub(at,at+1)~='??' then checked[#checked+1]=math.floor((at-1)/2) end
        end
        for _,offset in ipairs({checked[1],checked[#checked]}) do
            local f=fixture(Core)
            local changed=address(f,key)+offset
            f.bytes[changed]=(f.bytes[changed]+1)%256
            fails('CAMPAIGN_GATE_CODE_MISMATCH:'..key..':differs-at:'..offset,
                function() f:install(Gate,build,compile) end)
            untouched(f,key)
        end
        for _,matches in ipairs({0,2}) do
            local f=fixture(Core); local offsets=f.api.FLPR_ENGINE_SITES.offsets
            offsets[key]=matches==2 and {offsets[key][1],offsets[key][1]+64} or {}
            fails('CAMPAIGN_GATE_CODE_MISMATCH:'..key..(matches==2 and ':ambiguous:2' or ':not-found'),
                function() f:install(Gate,build,compile) end)
            untouched(f,key)
        end
    end
    -- Bytes a hook relies on outside its own location: the item hook takes the
    -- area loader's container call as the constructor's caller.
    local f=fixture(Core)
    local call=address(f,'gate_container_call')
    f:poke(call+1,f:peek(call+1,4)+16,4)
    fails('CAMPAIGN_GATE_CODE_MISMATCH:gate_container_call:call-target',
        function() f:install(Gate,build,compile) end)
    untouched(f)
    -- Another mod's jump at the constructor's entry is not a mismatch.
    f=fixture(Core)
    local entry=address(f,'gate_container_entry')
    f:poke(entry,0xE9,1); f:poke(entry+1,0x12345678,4)
    assert(f:install(Gate,build,compile).installed)
    -- No readable executable: the session scan reports it to every caller.
    local read=Site.read_file
    Site.read_file=function() return nil end
    f=fixture(Core); f.api.FLPR_ENGINE_SITES=nil
    fails('CAMPAIGN_GATE_CODE_MISMATCH:engine:executable-not-found',function() f:install(Gate,build,compile) end)
    Site.read_file=read
    untouched(f); assert(#f.hooks==0)
end

function cases.partial_hook_failure_cannot_arm_or_retry(Core,Gate,build,compile)
    for failing=1,#HOOKED do
        local f=fixture(Core); f.fail_hook=failing
        fails('HOOK_FAILED',function() f:install(Gate,build,compile) end)
        assert(f.events[#f.events]=='protect' and f:q(0)==0 and #f.hooks==failing)
        assert(f.api.FLPR_CAMPAIGN_GATE and not f.api.FLPR_CAMPAIGN_GATE.installed)
        assert(#f.callbacks==0)
        fails('CAMPAIGN_GATE_INSTALL_CONTEXT_INVALID',function() f:install(Gate,build,compile) end)
        assert(f:q(0)==0 and #f.hooks==failing)
    end
end

-- A revision whose pristine copy is rejected, or whose copies do not read
-- back, is never armed and none of its code becomes reachable.
function cases.pristine_rejection_and_copy_failure_publish_nothing(Core,Gate,build,compile)
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
        assert(not ok and f.allocations==0 and #f.hooks==#HOOKED)
        for index=1,#HOOKED do assert(f:idle(index)) end
        assert(next(f.api.FLPR_CAMPAIGN_GATE.built)==nil)
    end
    -- The policy table is copied first, then the pristine ARE.
    for _,case in ipairs({{0,'CAMPAIGN_GATE_POLICY_COPY_FAILED',TABLE},
        {TABLE,'CAMPAIGN_GATE_PRISTINE_COPY_FAILED',TABLE+27492},
        {TABLE+0x70,'CAMPAIGN_GATE_PRISTINE_COPY_FAILED',TABLE+27492},
        {TABLE+27491,'CAMPAIGN_GATE_PRISTINE_COPY_FAILED',TABLE+27492}}) do
        local f=fixture(Core); f.copy_corrupt=case[1]
        fails(case[2],function() f:install(Gate,build,compile) end)
        assert(f.allocations==1 and f.byte_writes==case[3] and f:q(0)==0)
        for index=1,#HOOKED do assert(f:idle(index)) end
        assert(next(f.api.FLPR_CAMPAIGN_GATE.built)==nil)
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
        assert(not ok and f.allocations==0 and #f.hooks==#HOOKED)
        for index=1,#HOOKED do assert(f:idle(index)) end
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

-- The host proves leases on every tick in turn. A proof reads each event row's
-- kind, generation and decision, never its names and payload bytes one by one.
function cases.lease_proof_reads_no_event_row_bytes(Core,Gate,build,compile)
    local f=fixture(Core); f:install(Gate,build,compile); f:capture(); f:prepare(); f:adapter()
    local receipt=f:bind()
    assert(f.gate:inspect().count>=1)
    f.reads=0
    assert(receipt.active())
    assert(f.reads==#f.gate.detours,tostring(f.reads)) -- each detour's jump opcode
    local snapshot=f.gate:inspect()
    assert(snapshot.events[1].name_raw and snapshot.events[1].payload_raw) -- diagnostics keep whole rows
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

-- A proof that cannot be made now waits, as after a reload: the witnessed area
-- load is not the visible one, or its holder is gone, replaced or doubled. Any
-- other refusal still stops CIR, and the proof still confirms once it can run.
function cases.unprovable_removal_waits_for_the_witnessed_holder(Core,Gate,build,compile)
    local f=fixture(Core); f:install(Gate,build,compile); f:capture(); f:prepare(); f:adapter()
    f:bind()
    local request=assert(f.controller:guarded_source_removal_request(1))
    f.controller:on_observation_boundary()
    f:publish_witness()
    for _,reason in ipairs({'AREA_UNAVAILABLE','AREA_CHANGED','OWNER_MISSING','OWNER_CHANGED','OWNER_AMBIGUOUS'}) do
        f.world_error=reason
        local result,why=f.adapter_instance:observe_removal(request)
        assert(result==nil and why=='SOURCE_NOT_LOADED',reason)
    end
    for _,reason in ipairs({'SOURCE_POINTER_INVALID','SOURCE_UNKNOWN','TARGET_NOT_FIXTURE_GROUND_PILE'}) do
        f.world_error=reason
        local result,why=f.adapter_instance:observe_removal(request)
        assert(result==nil and why==reason,reason)
    end
    assert(f.values.flpr_s0001==6)
    f.remaining=0
    assert(f.controller:confirm_guarded_source_removal(assert(f.adapter_instance:observe_removal(request))))
    assert(f.values.flpr_s0001==3)
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
    local assembly=f:chain(3)
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

function cases.source_flags_other_than_undroppable_are_bound_exactly(Core,Gate,build,compile)
    -- Identified and stolen (Baeloth's items), and bits a mod set (0x3E7).
    for _,flags in ipairs({5,0x3E7,13}) do
        local f=fixture(Core)
        f.manifest.sources[1].record.record.flags=flags
        f.bundle.fixture.sources[ID].payload.flags=flags
        f.pristine.bytes=pristine_bytes({flags=flags})
        if flags==13 then
            -- Undroppable: such a reward would never leave a creature target.
            fails('CAMPAIGN_GATE_SOURCE_INVALID',function() f:install(Gate,build,compile) end)
            assert(f.allocations==0)
        else
            local gate=f:install(Gate,build,compile)
            assert(gate.binding.payload.flags==flags and gate.policy.source.payload:sub(17,20)==u32(flags))
        end
    end
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
    assert(#members==2 and #f.hooks==#HOOKED and #f.callbacks==1 and f.allocations==2)
    assert(members[1].buffer~=members[2].buffer and f.byte_writes==2*TABLE+#f.pristine.bytes)
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
    assert(#f.gate.members==2 and #f.hooks==#HOOKED and #f.callbacks==1)
    assert(f.byte_writes==2*TABLE+#f.pristine.bytes)
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

-- The gate as M_FLPR creates it at launch: hooks only; revisions come from
-- the loader when a game needs them.
local function lazy(f,Gate,build,compile,revisions)
    f.loads,f.reports={},{}
    f.gate=Gate.new(f.api,{build=build,compile=compile,pristine=f.pristine,
        revisions=revisions or {f.revision},
        load=function(revision)
            f.loads[#f.loads+1]=revision
            if f.load_error then error(f.load_error,0) end
            return (f.bundles or {})[revision] or f.bundle
        end,
        report=function(context,reason) f.reports[#f.reports+1]=context..':'..tostring(reason) end})
    return f.gate
end
-- A GAM in fake memory: header, then 84-byte variable records (name, value at 0x28).
local function gam(f,address,fields,options)
    options=options or {}
    local offset=options.offset or 0xB4
    for at=0,offset-1 do f.bytes[address+at]=0 end
    f:pokestring(address,options.version or 'GAMEV2.0')
    f:poke(address+0x38,offset,4); f:poke(address+0x3C,options.count or #fields,4)
    for index,field in ipairs(fields) do
        local record=address+offset+(index-1)*84
        for at=0,83 do f.bytes[record+at]=0 end
        f:pokestring(record,field[1])
        f:poke(record+0x28,field[2]%4294967296,4)
    end
    return offset+#fields*84
end
local REVISION=string.rep('0123456789abcdef',4)
local function campaign_fields(revision,extra)
    local fields={{'FLPR_STATE',2},{'OTHER_GLOBAL',7}}
    for index=1,16 do
        fields[#fields+1]={string.format('FLPR_R%02d',index),tonumber(revision:sub(index*4-3,index*4),16)}
    end
    for _,field in ipairs(extra or {}) do fields[#fields+1]=field end
    return fields
end

function cases.launch_hooks_only_and_builds_a_revision_when_a_game_needs_it(Core,Gate,build,compile)
    local f=fixture(Core)
    local second=clone(f.bundle); second.manifest.revision=string.rep('e',64)
    second.fixture.revision=second.manifest.revision
    f.bundles={[f.revision]=f.bundle,[second.manifest.revision]=second}
    local gate=lazy(f,Gate,build,compile,{f.revision,second.manifest.revision})
    -- Launch: every location hooked with nothing reachable; no revision read.
    assert(gate.installed and #f.hooks==#HOOKED and #f.callbacks==1 and #f.loads==0 and f.allocations==0)
    for index=1,#HOOKED do assert(f:idle(index)) end
    assert(gate:guards(f.revision) and gate:guards(second.manifest.revision) and
        not gate:guards(string.rep('f',64)) and not gate:guards('wrong'))
    -- The Host's guard is told when a game never needed the revision.
    fails('CAMPAIGN_GATE_REVISION_NOT_BUILT',function() gate:adapter(f.bundle,{}) end)
    -- A game's revision is built once; its members are armed before any of
    -- its code is reachable, and only the locations it uses run code.
    assert(gate:ensure(f.revision) and gate:ensure(f.revision))
    assert(#f.loads==1 and f.allocations==1 and f:q(0)==1 and gate.built[f.revision])
    for index=1,#HOOKED do assert(f:idle(index)==(index>5)) end
    assert(#gate.members==1 and gate.by_revision[f.revision][1]==gate.members[1])
    -- Another revision's checks run before those already built, which stay.
    assert(gate:ensure(second.manifest.revision) and #f.loads==2 and #gate.members==2)
    local newer=gate.by_revision[second.manifest.revision][1]
    assert(newer.buffer~=BUFFER and newer.policy.provenance.expected==BUFFER+BODY) -- shared ARE copy
    for index=1,5 do
        local code=f:chain(index)
        local older_at=code:find('mov r10, '..string.format('%.0f',BUFFER)..'\n',1,true)
        local newer_at=code:find('mov r10, '..string.format('%.0f',newer.buffer)..'\n',1,true)
        assert(older_at and newer_at and newer_at<older_at,index)
    end
    f.callbacks[1]()
    for _,member in ipairs(gate.members) do assert(member:_q(104)==1) end
end

function cases.failed_revision_is_cached_reported_and_named(Core,Gate,build,compile)
    local f=fixture(Core); local gate=lazy(f,Gate,build,compile)
    f.load_error='SOURCE_GATE_PRISTINE_MISSING'
    fails('SOURCE_GATE_PRISTINE_MISSING',function() gate:ensure(f.revision) end)
    f.load_error=nil
    fails('SOURCE_GATE_PRISTINE_MISSING',function() gate:ensure(f.revision) end)
    assert(#f.loads==1 and not gate.built[f.revision] and f.allocations==0)
    fails('SOURCE_GATE_PRISTINE_MISSING',function() gate:adapter(f.bundle,{}) end)
    -- A revision the configuration does not list is never loaded.
    fails('CAMPAIGN_GATE_REVISION_NOT_APPLIED',function() gate:ensure(string.rep('f',64)) end)
    assert(#f.loads==1)
    -- The loader must return the revision asked for.
    f=fixture(Core); gate=lazy(f,Gate,build,compile,{string.rep('e',64)})
    fails('CAMPAIGN_GATE_REVISION_MISMATCH',function() gate:ensure(string.rep('e',64)) end)
    assert(f.allocations==0)
    for index=1,#HOOKED do assert(f:idle(index)) end
end

function cases.gam_revision_reads_the_campaign_fields_exactly(Core,Gate,build,compile)
    local f=fixture(Core); local gate=lazy(f,Gate,build,compile)
    local G=SMALL+0x100000
    local function read(fields,options)
        local size=gam(f,G,fields,options)
        return gate:_gam_revision(G,(options or {}).size or size)
    end
    -- Other GLOBALs, FLPR_ names that are not R01..R16 and any order are fine.
    local fields=campaign_fields(REVISION,{{'FLPR_RKEY',1},{'FLPR_R17',5},{'FLPR_R00',5},{'FLPR_S0001',6}})
    assert(read(fields)==REVISION)
    local reversed={}
    for index=#fields,1,-1 do reversed[#reversed+1]=fields[index] end
    assert(read(reversed)==REVISION)
    for _,version in ipairs({'GAMEV2.1','GAMEV1.1'}) do assert(read(fields,{version=version})==REVISION) end
    assert(read(fields,{version='GAMEV9.9'})==nil)
    -- Each field is needed once, zero-padded, with a value of 0..0xFFFF.
    for index=3,18 do
        local missing,duplicate,large,padded={},{},{},{}
        for at,field in ipairs(fields) do
            if at~=index then missing[#missing+1]=field end
            duplicate[#duplicate+1]=field
            large[#large+1]=at==index and {field[1],0x10000} or field
            padded[#padded+1]=at==index and {field[1]..'X',field[2]} or field
        end
        duplicate[#duplicate+1]=fields[index]
        assert(read(missing)==nil and read(duplicate)==nil and read(large)==nil and read(padded)==nil,index)
    end
    local negative=clone(fields); negative[3][2]=-1
    assert(read(negative)==nil)
    -- The variable table must lie inside the GAM it came with.
    local size=gam(f,G,fields)
    assert(gate:_gam_revision(G,size-1)==nil and gate:_gam_revision(G,0xB3)==nil)
    assert(read(fields,{offset=0xB3})==nil and read(fields,{count=16385})==nil)
    assert(gate:_gam_revision(0,size)==nil and gate:_gam_revision(G,0x1000001)==nil)
end

function cases.game_entry_builds_the_saved_revision_and_reports_failures(Core,Gate,build,compile)
    local f=fixture(Core); f.revision=REVISION; f.bundle.manifest.revision=REVISION
    f.bundle.fixture.revision=REVISION
    local gate=lazy(f,Gate,build,compile)
    local G=SMALL+0x100000
    local size=gam(f,G,campaign_fields(REVISION))
    f.api.FLPR_CampaignGateGame(G,size)
    assert(gate.built[REVISION] and #f.loads==1 and #f.reports==0)
    f.api.FLPR_CampaignGateGame(G,size)
    assert(#f.loads==1)
    -- New Game templates and saves without a listed revision build nothing.
    f=fixture(Core); gate=lazy(f,Gate,build,compile,{REVISION})
    for _,fields in ipairs({{{'FLPR_FRESH',1}},campaign_fields(string.rep('f',64))}) do
        f.api.FLPR_CampaignGateGame(G,gam(f,G,fields))
    end
    assert(#f.loads==0 and next(gate.built)==nil and #f.reports==0)
    -- A failure is reported and kept; the engine call returns normally.
    f.load_error='SOURCE_GATE_REVISION_UNAVAILABLE'
    f.api.FLPR_CampaignGateGame(G,gam(f,G,campaign_fields(REVISION)))
    f.api.FLPR_CampaignGateGame(G,gam(f,G,campaign_fields(REVISION)))
    assert(#f.loads==1 and #f.reports==1 and f.reports[1]=='gate:SOURCE_GATE_REVISION_UNAVAILABLE')
    assert(gate.failed[REVISION]=='SOURCE_GATE_REVISION_UNAVAILABLE')
end

function cases.owned_copies_move_in_blocks_when_available(Core,Gate,build,compile)
    local function bulk(f)
        f.blocks,f.memsets={},{}
        f.api.FLPR={Memory={
            write=function(address,value)
                f.blocks[#f.blocks+1]={address,#value}; f:pokestring(address,value)
                if f.bulk_corrupt==address then f.bytes[address+0x70]=0xFF end
            end,
            read=function(address,length)
                local out={}
                for at=0,length-1 do out[#out+1]=string.char(f.bytes[address+at] or 0) end
                return table.concat(out)
            end}}
        function f.api.EEex_Memset(address,value,size)
            assert(value==0); f.memsets[#f.memsets+1]={address,size}
        end
    end
    local f=fixture(Core); bulk(f)
    local gate=f:install(Gate,build,compile)
    assert(gate.installed and f.byte_writes==nil and #f.blocks==2)
    assert(f.blocks[1][1]==BUFFER+POLICY_TABLE and f.blocks[1][2]==TABLE)
    assert(f.blocks[2][1]==BUFFER+BODY and f.blocks[2][2]==27492 and f:peek(BUFFER+BODY+0x70,4)==0x68D4)
    local zeroed=false
    for _,call in ipairs(f.memsets) do if call[1]==BUFFER and call[2]==MEMORY_SIZE then zeroed=true end end
    assert(zeroed)
    -- A block that does not read back is refused.
    f=fixture(Core); bulk(f); f.bulk_corrupt=BUFFER+BODY
    fails('CAMPAIGN_GATE_PRISTINE_COPY_FAILED',function() f:install(Gate,build,compile) end)
    for index=1,#HOOKED do assert(f:idle(index)) end
end

return cases
'''


class CampaignGateBridgeTests(unittest.TestCase):
    def test_jump_chain_keeps_stack_and_exits_once(self):
        # The real watchdog is represented by flag-preserving counters. This
        # checks the authored entry/exit placement, not the watchdog internals.
        site, allocations, data, stack = 0x100000, 0x200000, 0x400000, 0x500000
        normal, skip, bad, callee = site+5, site+0x100, site+0x200, site+0x300
        for runtime in (Lua51, Lua53, LuaJIT):
            for call in (False, True):
                with self.subTest(runtime=runtime.__module__, call=call):
                    lua = runtime(unpack_returned_tuples=True)
                    gate = load(lua, GATE)
                    uc = Uc(UC_ARCH_X86, UC_MODE_64)
                    for address, size in ((site, 0x1000), (allocations, 0x10000),
                                          (data, 0x1000), (stack, 0x10000)):
                        uc.mem_map(address, size)
                    assembler = Ks(KS_ARCH_X86, KS_MODE_64)
                    compiled, labels = [], {}

                    def counter(offset):
                        return f'pushfq\ninc qword ptr [{data+offset}]\npopfq\n'

                    def emit(address, values):
                        self.assertTrue(all(isinstance(value, (str, int))
                                            for value in values.values()))
                        code = ''.join(str(value) for value in values.values())
                        for instance in (0, 1):
                            code = code.replace(f'#MANUAL_HOOK_EXIT({instance})',
                                                counter(8+8*instance))
                        code = re.sub(r';[^\n]*', '', code).replace('jmp short ', 'jmp ')
                        binary, _ = assembler.asm(code, address)
                        uc.mem_write(address, bytes(binary))

                    def jit(values):
                        self.assertEqual(labels.get('hook_address'), site)
                        address = allocations + 0x1000*len(compiled)
                        compiled.append(address); emit(address, values); return address

                    def scoped(pairs, action):
                        labels.update((pair[1], pair[2]) for pair in pairs.values())
                        result = action(); labels.clear(); return result

                    # The location's original call, which the chain's tail replays.
                    uc.mem_write(site, b'\xe8' + struct.pack('<i', callee - site - 5))
                    slot = data + 0x800
                    api = lua.table_from({
                        'EEex_HookIntegrityWatchdog_IgnoreRegisters': lambda *_: None,
                        'EEex_HookIntegrityWatchdog_DefaultIgnoreStack': lambda *_: None,
                        'EEex_HookIntegrityWatchdog_HookEnter': lua.table_from([counter(0)]),
                        'EEex_RunWithAssemblyLabels': scoped,
                        'EEex_JITNear': jit, 'EEex_JITAt': emit,
                        'EEex_Malloc': lambda size: slot,
                        'EEex_Write64': lambda address, value: uc.mem_write(
                            int(address), struct.pack('<Q', int(value))),
                        'EEex_ReadU8': lambda address: uc.mem_read(int(address), 1)[0],
                        'EEex_Read32': lambda address: struct.unpack(
                            '<i', uc.mem_read(int(address), 4))[0],
                        'EEex_StoreBytesAssembly': lambda *_: lua.table_from(
                            ['mov rax, qword ptr [rsp+32]\n']),
                    })
                    api.EEex_FlattenTable = lua.eval('''function(values)
                        local out={}
                        local function add(value)
                            if type(value)=='table' then
                                for _,entry in ipairs(value) do add(entry) end
                            else out[#out+1]=value end
                        end
                        add(values); return out
                    end''')
                    # Two indivisible source blocks exceed the partition limit.
                    # Each checks a native stack argument after its own pushes.
                    check = (f'pushfq\npush rax\nmov rax, [rsp+48]\ncmp rax, 12345\n'
                             f'jne {bad}\npop rax\npopfq\n')
                    first = counter(32)+check+';'+('padding '*1200)+'\n'
                    second = (counter(40)+check+f'pushfq\ncmp qword ptr [{data+24}], 1\n'
                              'je selected\npopfq\njmp done\nselected:\npopfq\n'
                              f'#MANUAL_HOOK_EXIT(1)\njmp {skip}\ndone:\n;'+
                              ('padding '*1200)+'\n')
                    key = 'gate_actor1' if call else 'gate_enter'
                    gate.install_chain(api, lua.table_from({'key': key, 'call': call, 'size': 5}),
                                       site, lua.table_from([first, second]))
                    # Hook: tail, empty chain, entry. Chain: two chunks, its entry.
                    self.assertEqual(len(compiled), 6)
                    self.assertEqual(struct.unpack('<Q', uc.mem_read(slot, 8))[0], compiled[5])
                    emit(callee, lua.table_from(['mov rax, [rsp+40]\nret\n']))
                    stopped = []

                    def stop_at_route(machine, address, *_):
                        if address in (normal, skip, bad):
                            stopped.append(address); machine.emu_stop()

                    uc.hook_add(UC_HOOK_CODE, stop_at_route)
                    for selected in (False, True):
                        uc.mem_write(data, bytes(48))
                        uc.mem_write(data+24, struct.pack('<Q', selected))
                        rsp = stack+0x8000
                        uc.mem_write(rsp+32, struct.pack('<Q', 12345))
                        uc.reg_write(UC_X86_REG_RSP, rsp)
                        uc.reg_write(UC_X86_REG_RAX, 6789)
                        uc.reg_write(UC_X86_REG_EFLAGS, 0x246)
                        stopped.clear(); uc.emu_start(site, 0, count=10000)
                        self.assertEqual(stopped, [skip if selected else normal])
                        self.assertEqual(uc.reg_read(UC_X86_REG_RSP), rsp)
                        self.assertEqual(uc.reg_read(UC_X86_REG_EFLAGS), 0x246)
                        self.assertEqual(uc.reg_read(UC_X86_REG_RAX), 6789 if selected else 12345)
                        self.assertEqual(struct.unpack('<6Q', uc.mem_read(data, 48)),
                                         (1, int(not selected), int(selected), int(selected), 1, 1))

    def test_lua_cases(self):
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime(unpack_returned_tuples=True)
            core, gate, compile_policy, build = (
                lua.execute(CORE), load(lua, GATE), lua.execute(POLICY), lua.execute(BUILD))
            site = load_site(lua)
            cases = lua.execute(CASES)
            for name in sorted(cases.keys()):
                with self.subTest(runtime=runtime.__module__, case=name):
                    cases[name](core, gate, build, compile_policy, site)


if __name__ == '__main__':
    unittest.main()
