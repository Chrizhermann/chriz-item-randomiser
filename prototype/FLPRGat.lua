-- Opt-in campaign bridge for the authored FLPRU1 fixture only. Never installed
-- by the normal TP2. Caller pins executable/helper/resource hashes before use.
-- build = tools/probes/FLPRGateAsm.lua; compile = FLPRCampaignPolicy.lua.
local Gate = {}
local Runtime = {}; Runtime.__index = Runtime
local Adapter = {}; Adapter.__index = Adapter
local CAPACITY, HEADER, STRIDE = 2048, 128, 128
local CONTEXT=HEADER+(CAPACITY+1)*STRIDE
local MEMORY_SIZE=CONTEXT+32+64*64
local ID = 'are:flpru1/container/0/item/1'
local sites = {
    {rva=0x292A84,size=8,code='enter',bytes='48894df8488d4dc8488945a8410fb6f944884c2460'},
    {rva=0x292F45,size=7,code='capture',bytes='8bd6413974242c0f86c40400000f1f400066660f1f84'},
    {rva=0x190270,size=7,code='item',bytes='418bc7488d0c80488b45884c8d2c88498bd5488d4df8'},
    {rva=0x18B8AC,size=8,code='area_enter',bytes='4533e444884c24604489642468450fb6e94c8bfa4c8bf1'},
    {rva=0x18DF94,size=8,code='area_exit',bytes='488b9c2470020000418bc4488b8d000100004833cce8'},
    {rva=0x18D9C9,bytes='e8322400004885c00f84e90000004c8b00418bd4488bc8'},
    {rva=0x18FE05,bytes='5556574154415541564157488d6c24804881ec80010000'},
    {rva=0x1906FC,bytes='418b464441ffc74103464041bc0000000044897c2450443bf80f8255fbffff4c8b6d98410f'},
}
local function hash(value)
    return type(value)=='string' and #value==64 and not value:find('[^0-9a-f]')
end
local function integer(value,lo,hi)
    return type(value)=='number' and value==math.floor(value) and value>=lo and value<=hi
end
local function payload(value)
    return type(value)=='table' and value.resref=='flpri1' and value.expiration==0 and value.flags==1 and
        type(value.charges)=='table' and value.charges[1]==0 and value.charges[2]==0 and value.charges[3]==0
end
local function validate(bundle)
    assert(type(bundle)=='table' and type(bundle.manifest)=='table' and type(bundle.fixture)=='table',
        'CAMPAIGN_GATE_CONFIG_INVALID')
    local m,f=bundle.manifest,bundle.fixture
    assert(hash(m.revision) and f.revision==m.revision and type(f.source_gate)=='table' and
        f.source_gate.schema=='flpr-unvisited-fixture-gate-v1' and f.source_gate.source_id==ID,
        'CAMPAIGN_GATE_CONFIG_INVALID')
    local index,selected
    for i,source in ipairs(m.sources) do
        if source.id==ID then assert(not selected,'CAMPAIGN_GATE_SOURCE_DUPLICATED'); index,selected=i,source end
    end
    assert(index and selected.quantity==1 and hash(selected.resource_sha256) and
        selected.record.kind=='container' and selected.record.id==ID and
        selected.record.resource=='are:flpru1' and selected.record.owner=='are:flpru1/container/0' and
        selected.record.record.record_index==1 and payload(selected.record.record),
        'CAMPAIGN_GATE_SOURCE_INVALID')
    local endpoint=f.sources and f.sources[ID]
    assert(endpoint and endpoint.kind=='guarded-container' and endpoint.area=='FLPRU1' and
        endpoint.script_name=='FLPRU1A' and endpoint.x==1140 and endpoint.y==650 and
        payload(endpoint.payload) and f.resource_hashes and
        f.resource_hashes['FLPRU1.ARE']==selected.resource_sha256, 'CAMPAIGN_GATE_BINDING_INVALID')
    return m.revision,index,selected.resource_sha256
end
local function hex(value)
    return (value:gsub('.',function(c) return string.format('%02x',c:byte()) end))
end
local NAME_RAW=hex('FLPRU1A'..string.rep('\0',25))
local PAYLOAD_RAW=hex('FLPRI1'..string.rep('\0',10)..'\1\0\0\0')

function Gate.install(api,bundle,build,compile,pristine)
    local revision,index,resource_hash=validate(bundle)
    -- The isolated harness hashes this immutable copy, the ARE and both frozen
    -- manifests before launch. Runtime copies it to separate owned memory; it
    -- must never compare a resource buffer to itself or demand an unloaded ARE.
    assert(type(pristine)=='table' and pristine.resource_sha256==resource_hash and
        type(pristine.bytes)=='string' and #pristine.bytes==27492 and
        pristine.bytes:sub(1,8)=='AREAV1.0', 'CAMPAIGN_GATE_PRISTINE_INVALID')
    local function u32(offset)
        local a,b,c,d=pristine.bytes:byte(offset+1,offset+4)
        return a+b*256+c*65536+d*16777216
    end
    assert(u32(0x70)==0x68D4 and u32(0x78)==0x6B14,'CAMPAIGN_GATE_PRISTINE_LAYOUT_INVALID')
    assert(api.EEex_Active and not api.FLPR_SOURCE_GATE and not api.FLPR_AREA_TRACE and
        not api.FLPR_CAMPAIGN_GATE, 'CAMPAIGN_GATE_INSTALL_CONTEXT_INVALID')
    assert(type(api.EEex_GameState_AddDestroyedListener)=='function' and
        type(api.EEex_GameState_GetGlobalInt)=='function' and type(api.EEex_Write8)=='function',
        'CAMPAIGN_GATE_LIFECYCLE_MISSING')
    local policy=compile(revision,index)
    local base=api.EEex_GetImageBase()
    for _,site in ipairs(sites) do
        local offset=0
        for byte in site.bytes:gmatch('%x%x') do
            assert(api.EEex_ReadU8(base+site.rva+offset)==tonumber(byte,16),'CAMPAIGN_GATE_CODE_MISMATCH')
            offset=offset+1
        end
    end
    local buffer=api.EEex_Malloc(MEMORY_SIZE+#pristine.bytes)
    assert(buffer and buffer~=0,'CAMPAIGN_GATE_ALLOC_FAILED')
    for offset=0,MEMORY_SIZE-1,8 do api.EEex_Write64(buffer+offset,0) end
    -- WriteLString uses C-string semantics and truncates binary data at NUL.
    for offset=1,#pristine.bytes do
        local byte=pristine.bytes:byte(offset)
        api.EEex_Write8(buffer+MEMORY_SIZE+offset-1,byte>=128 and byte-256 or byte)
    end
    for offset=1,#pristine.bytes do
        assert(api.EEex_ReadU8(buffer+MEMORY_SIZE+offset-1)==pristine.bytes:byte(offset),
            'CAMPAIGN_GATE_PRISTINE_COPY_FAILED')
    end
    policy.provenance={expected=buffer+MEMORY_SIZE,length=#pristine.bytes,
        container_offset=u32(0x70),item_offset=u32(0x78)}
    local self=setmetatable({api=api,base=base,buffer=buffer,revision=revision,index=index,
        resource_hash=resource_hash,policy=policy,detours={},installed=false},Runtime)
    api.FLPR_CAMPAIGN_GATE=self -- partial installation is never retried in-process
    local assembly=build(buffer,base,policy)
    api.EEex_DisableCodeProtection()
    local ok,failure=pcall(function()
        for _,site in ipairs(sites) do
            if site.code then
                local address=base+site.rva
                api.EEex_HookBeforeRestore(address,0,site.size,site.size,{assembly[site.code]})
                assert(api.EEex_ReadU8(address)==0xE9,'CAMPAIGN_GATE_DETOUR_NOT_WRITTEN')
                local target=address+5+api.EEex_Read32(address+1)
                assert(target>0 and (target<address or target>=address+#site.bytes/2),
                    'CAMPAIGN_GATE_DETOUR_INVALID')
                self.detours[#self.detours+1]={address=address,target=target}
            end
        end
    end)
    api.EEex_EnableCodeProtection()
    assert(ok,failure)
    api.EEex_GameState_AddDestroyedListener(function()
        self:reset()
    end)
    self.installed=true
    api.EEex_Write64(buffer,1) -- installed before New Game / any saved GAM load
    return self
end

function Runtime:reset()
    self.enrolled=nil
    local api,buffer=self.api,self.buffer
    api.EEex_Write64(buffer+32,0)
    api.EEex_Write64(buffer+48,0); api.EEex_Write64(buffer+56,0)
    api.EEex_Write64(buffer+112,0)
    api.EEex_Write64(buffer+104,api.EEex_ReadU64(buffer+104)+1)
    for offset=CONTEXT+32,MEMORY_SIZE-1,64 do
        api.EEex_Write64(buffer+offset+24,0); api.EEex_Write64(buffer+offset,0)
    end
end

function Runtime:_q(offset) return self.api.EEex_ReadU64(self.buffer+offset) end
function Runtime:_write(offset,value) self.api.EEex_Write64(self.buffer+offset,value) end
function Runtime:_raw(address,length)
    local out={}
    for offset=0,length-1 do out[#out+1]=string.format('%02x',self.api.EEex_ReadU8(address+offset)) end
    return table.concat(out)
end
function Runtime:_row(index)
    local row=self.buffer+HEADER+index*STRIDE
    local function q(offset) return self.api.EEex_ReadU64(row+offset) end
    return {kind=q(0),generation=q(8),mode=q(16),decision=q(24),
        object_pointer=tostring(q(32)),other_value=tostring(q(40)),index=q(48),
        name_raw=self:_raw(row+56,32),payload_raw=self:_raw(row+88,20),
        detail1=q(112),detail2=q(120)}
end
function Runtime:inspect()
    local count,generation,destroys,active=self:_q(8),self:_q(40),self:_q(104),self:_q(24)
    local result={schema='flpr-campaign-gate-trace-v1',revision=self.revision,
        installed=self.installed,armed=self:_q(0)==1,overflow=self:_q(16)~=0 or count>CAPACITY,
        count=count,generation=generation,destroys=destroys,mode=self:_q(32),status=self:_q(72),
        mask=self:_q(80),candidate=self:_q(88),skips=self:_q(96),load_skips=self:_q(120),events={}}
    local published=true
    for index=0,math.min(count,CAPACITY)-1 do
        local row=self:_row(index)
        if row.kind~=1 and row.kind~=2 and row.kind~=3 and row.kind~=4 then published=false end
        result.events[#result.events+1]=row
    end
    if self:_q(112)==1 then result.witness=self:_row(CAPACITY) end
    result.stable=published and active==0 and self:_q(24)==0 and self:_q(8)==count and
        self:_q(40)==generation and self:_q(104)==destroys and self:_q(32)==result.mode
    return result
end
function Runtime:_intact()
    if not self.installed then return false end
    for _,detour in ipairs(self.detours) do
        if self.api.EEex_ReadU8(detour.address)~=0xE9 or
            detour.address+5+self.api.EEex_Read32(detour.address+1)~=detour.target then return false end
    end
    return true
end
function Runtime:_bundle(bundle)
    local revision,index,resource_hash=validate(bundle)
    assert(revision==self.revision and index==self.index and resource_hash==self.resource_hash,
        'CAMPAIGN_GATE_REVISION_MISMATCH')
end

function Runtime:prepare_new(bundle,controller)
    self:_bundle(bundle)
    assert(controller and controller.manifest and controller.manifest.revision==self.revision,
        'CAMPAIGN_GATE_CAPTURE_REQUIRED')
    assert(self.api.EEex_GameState_GetGlobalInt('FLPR_FRESH')==1,'CAMPAIGN_GATE_FRESH_REQUIRED')
    for _,field in ipairs(self.policy.fields) do
        assert(self.api.EEex_GameState_GetGlobalInt(field.name)==(field.phase and 1 or field.value),
            'CAMPAIGN_GATE_CAPTURE_MISMATCH')
    end
    local snapshot=self:inspect()
    assert(self:_intact() and snapshot.armed and snapshot.stable and not snapshot.overflow and
        snapshot.mode==0 and snapshot.status==1 and snapshot.load_skips==0 and not self.enrolled and
        integer(snapshot.generation,1,2147483646) and snapshot.count<CAPACITY,
        'CAMPAIGN_GATE_NEW_CONTEXT_INVALID')
    for _,row in ipairs(snapshot.events) do
        assert(row.generation~=snapshot.generation or row.kind~=3,'CAMPAIGN_GATE_ALREADY_VISITED')
    end
    -- This trusted call is synchronous inside the confirmed character-accept
    -- handoff, before the engine accepts/enters the world. It never loads an area.
    local generation=snapshot.generation+1
    local row=self.buffer+HEADER+snapshot.count*STRIDE
    for offset=0,120,8 do self.api.EEex_Write64(row+offset,0) end
    self.api.EEex_Write64(row+8,generation); self.api.EEex_Write64(row+16,1)
    self.api.EEex_Write64(row+48,self.index); self.api.EEex_Write64(row+112,self.policy.mask)
    self.api.EEex_Write64(row,4)
    self:_write(8,snapshot.count+1)
    self:_write(40,generation); self:_write(112,0); self:_write(120,0)
    self.enrolled={generation=generation,destroys=snapshot.destroys,session=controller.session}
    self:_write(32,1) -- publish protection last
    return true
end

function Runtime:_proof()
    local snapshot=self:inspect()
    if not self:_intact() or not snapshot.stable or not snapshot.armed or snapshot.overflow or
        snapshot.mode~=1 or not integer(snapshot.generation,1,2147483647) then
        return nil,'CAMPAIGN_GATE_INACTIVE'
    end
    local enrolled=self.enrolled and self.enrolled.generation==snapshot.generation and
        self.enrolled.destroys==snapshot.destroys
    if not enrolled and not (snapshot.status==0 and snapshot.mask==self.policy.mask and snapshot.candidate==1) then
        return nil,'CAMPAIGN_GATE_POLICY_UNPROVEN'
    end
    for _,row in ipairs(snapshot.events) do
        if row.generation==snapshot.generation and row.kind==3 and row.decision~=0 and row.decision~=1 then
            return nil,'CAMPAIGN_GATE_SOURCE_CHANGED'
        end
    end
    return snapshot
end

function Runtime:adapter(bundle,native)
    self:_bundle(bundle)
    assert(type(native)=='table' and type(native.guarded_source_remaining)=='function',
        'CAMPAIGN_GATE_WORLD_OBSERVER_MISSING')
    return setmetatable({gate=self,native=native,destroyed=false},Adapter)
end
function Adapter:destroy() self.destroyed=true; self.bound=nil end
function Adapter:_request(request,kind)
    local gate=self.gate
    return not self.destroyed and type(request)=='table' and request.kind==kind and request.mode=='observe-only'
        and request.index==gate.index and request.revision==gate.revision and request.source_id==ID
        and request.resource_sha256==gate.resource_hash and request.quantity==1 and payload(request.payload)
        and type(request.session)=='table' and type(request.token)=='table'
end
local function receipt(request)
    local result={}
    for _,key in ipairs({'kind','mode','index','revision','source_id','resource_sha256','quantity',
        'session','token','guard_generation'}) do result[key]=request[key] end
    result.payload={resref='flpri1',expiration=0,flags=1,charges={0,0,0}}
    return result
end
function Adapter:observe_guard(request)
    if not self:_request(request,'source-guard') then return nil,'CAMPAIGN_GATE_REQUEST_MISMATCH' end
    local snapshot,reason=self.gate:_proof()
    if not snapshot then return nil,reason end
    local bound={generation=snapshot.generation,destroys=snapshot.destroys,session=request.session,revoked=false}
    self.bound=bound
    local result=receipt(request)
    result.pre_access,result.exact_source_bound,result.generation=1,1,bound.generation
    result.active=function()
        if self.destroyed or self.bound~=bound or bound.revoked then return false end
        local ok,current=pcall(function() return self.gate:_proof() end)
        if not ok or not current or current.generation~=bound.generation or current.destroys~=bound.destroys then
            bound.revoked=true; return false
        end
        return true
    end
    return result
end
function Adapter:observe_removal(request)
    if not self:_request(request,'guarded-source-removal') or not self.bound or
        request.session~=self.bound.session or request.guard_generation~=self.bound.generation or self.bound.revoked then
        return nil,'CAMPAIGN_GATE_REQUEST_MISMATCH'
    end
    local snapshot,reason=self.gate:_proof()
    if not snapshot then return nil,reason end
    if snapshot.generation~=self.bound.generation or snapshot.destroys~=self.bound.destroys then
        return nil,'CAMPAIGN_GATE_GENERATION_CHANGED'
    end
    local witness=snapshot.witness
    if not witness then return nil,'SOURCE_NOT_LOADED' end
    if snapshot.load_skips~=1 or witness.kind~=3 or witness.generation~=snapshot.generation or
        witness.mode~=1 or witness.decision~=1 or witness.index~=1 or witness.name_raw~=NAME_RAW or
        witness.payload_raw~=PAYLOAD_RAW or witness.detail1~=0x200000001 or witness.detail2~=0x4028a0474 or
        witness.object_pointer=='0' or witness.other_value=='0' then return nil,'CAMPAIGN_GATE_WITNESS_INVALID' end
    local world; world,reason=self.native:guarded_source_remaining(ID,witness.object_pointer,witness.other_value)
    if not world then
        if reason=='AREA_UNAVAILABLE' then return nil,'SOURCE_NOT_LOADED' end
        return nil,reason
    end
    if world.remaining~=0 or world.area_pointer~=witness.object_pointer or world.owner_pointer~=witness.other_value then
        return nil,'CAMPAIGN_GATE_WORLD_MISMATCH'
    end
    local result=receipt(request)
    result.exact_source_event,result.removed,result.remaining=1,1,0
    return result
end

return Gate
