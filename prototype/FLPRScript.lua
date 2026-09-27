-- The authored BLPOOL branch reads saved GLOBALs. No native action hook is
-- needed: an unselected campaign runs the original GiveItemCreate branch.
local Script = {}
local Member = {}; Member.__index = Member
local Adapter = {}; Adapter.__index = Adapter
local ID = 'bcs:blpool/grant/pool-reward'
local SELECTED, CONSUMED = 'FLPR_QPOOL', 'FLPR_XPOOL'

local function same(a,b)
    return a and b and a.resref==b.resref and a.expiration==b.expiration and a.flags==b.flags and
        a.charges and b.charges and a.charges[1]==b.charges[1] and
        a.charges[2]==b.charges[2] and a.charges[3]==b.charges[3]
end
local function read(path)
    local file=assert(io.open(path,'rb'),'CAMPAIGN_SCRIPT_PATCH_MISSING')
    local bytes=file:read('*a'); assert(file:close()); return bytes
end

function Script.new(api,bundle,id,expected)
    local index,source
    for i,row in ipairs(bundle.manifest.sources) do if row.id==id then index,source=i,row end end
    local endpoint=bundle.fixture.sources[id]
    assert(id==ID and source and source.quantity==1 and source.record.kind=='scripted' and
        source.record.adapter_id=='blpool-v1' and source.record.resource=='bcs:blpool' and
        source.record.selector=='pool-reward' and endpoint.kind=='guarded-script' and
        endpoint.adapter_id=='blpool-v1' and same(endpoint.payload,source.record.record),
        'CAMPAIGN_SCRIPT_BINDING_INVALID')
    assert(type(expected)=='string' and #expected>0 and
        read('override/BLPOOL.BCS')==expected,'CAMPAIGN_SCRIPT_PATCH_CHANGED')
    assert(type(api.EEex_GameState_SetGlobalInt)=='function','CAMPAIGN_SCRIPT_GLOBALS_MISSING')
    return setmetatable({api=api,revision=bundle.manifest.revision,index=index,
        resource_hash=source.resource_sha256,binding={id=id,payload=endpoint.payload},
        generation=1,installed=false},Member)
end

function Member:_bundle(bundle)
    local source=bundle.manifest.sources[self.index]
    assert(bundle.manifest.revision==self.revision and source and source.id==ID and
        source.resource_sha256==self.resource_hash and same(source.record.record,self.binding.payload),
        'CAMPAIGN_SCRIPT_REVISION_MISMATCH')
end
function Member:reset()
    -- Save loading supplies its own GLOBALs; never clear them here.
    self.generation=self.generation+1
end
function Member:prepare_new(bundle,controller)
    self:_bundle(bundle)
    local api=self.api
    assert(controller and controller.manifest and controller.manifest.revision==self.revision and
        api.EEex_GameState_GetGlobalInt('FLPR_FRESH')==1,
        'CAMPAIGN_SCRIPT_CAPTURE_REQUIRED')
    assert(api.EEex_GameState_GetGlobalInt(SELECTED)==0 and api.EEex_GameState_GetGlobalInt(CONSUMED)==0,
        'CAMPAIGN_SCRIPT_ALREADY_STARTED')
    api.EEex_GameState_SetGlobalInt(SELECTED,1)
    assert(api.EEex_GameState_GetGlobalInt(SELECTED)==1,'CAMPAIGN_SCRIPT_SELECTION_FAILED')
    return true
end
function Member:inspect()
    return {installed=self.installed,generation=self.generation,
        selected=self.api.EEex_GameState_GetGlobalInt(SELECTED),
        consumed=self.api.EEex_GameState_GetGlobalInt(CONSUMED)}
end
function Member:adapter(bundle)
    self:_bundle(bundle)
    return setmetatable({gate=self,generation=self.generation},Adapter)
end
function Adapter:destroy() self.destroyed=true end
function Adapter:_active()
    return not self.destroyed and self.gate.installed and self.generation==self.gate.generation and
        self.gate.api.EEex_GameState_GetGlobalInt(SELECTED)==1
end
function Adapter:_request(request,kind)
    local gate=self.gate
    return self:_active() and type(request)=='table' and request.kind==kind and request.mode=='observe-only' and
        request.index==gate.index and request.revision==gate.revision and request.source_id==ID and
        request.resource_sha256==gate.resource_hash and request.quantity==1 and
        same(request.payload,gate.binding.payload) and type(request.session)=='table' and type(request.token)=='table'
end
local function receipt(request)
    local result={}
    for _,key in ipairs({'kind','mode','index','revision','source_id','resource_sha256','quantity',
        'session','token','guard_generation'}) do result[key]=request[key] end
    local p=request.payload
    result.payload={resref=p.resref,expiration=p.expiration,flags=p.flags,charges={p.charges[1],p.charges[2],p.charges[3]}}
    return result
end
function Adapter:observe_guard(request)
    if not self:_request(request,'source-guard') then return nil,'CAMPAIGN_SCRIPT_REQUEST_MISMATCH' end
    self.session=request.session
    local result=receipt(request)
    result.pre_access,result.exact_source_bound,result.generation=1,1,self.generation
    result.active=function() return self:_active() end
    return result
end
function Adapter:observe_removal(request)
    if not self:_request(request,'guarded-source-removal') or request.session~=self.session or
        request.guard_generation~=self.generation then return nil,'CAMPAIGN_SCRIPT_REQUEST_MISMATCH' end
    if self.gate.api.EEex_GameState_GetGlobalInt(CONSUMED)~=1 then return nil,'SOURCE_NOT_LOADED' end
    local result=receipt(request)
    result.exact_source_event,result.removed,result.remaining=1,1,0
    return result
end
return Script
