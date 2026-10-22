-- Reviewed compiled quest branches read saved GLOBALs. An unselected source
-- grants its original item; selected sources witness the omitted grant.
local Script = {}
local Member = {}; Member.__index = Member
local Adapter = {}; Adapter.__index = Adapter
local ID = 'bcs:blpool/grant/pool-reward'
local SELECTED, CONSUMED = 'FLPR_QPOOL', 'FLPR_XPOOL'
local QUESTS = {
    [ID] = {quest='pool',resource='bcs:blpool',selected=SELECTED,consumed=CONSUMED},
    ['original-source:bg2:base:item:9'] = {quest='altar',resource='bcs:troalt',
        selected='FLPR_QALTAR',consumed='FLPR_XALTAR'},
    ['original-source:bg2:base:item:109'] = {quest='book',resource='bcs:ppbook',
        selected='FLPR_QBOOK',consumed='FLPR_XBOOK'},
    ['original-source:bg2:mode2:item:69'] = {quest='key',resource='spl:spin621',
        selected='FLPR_QKEY',consumed='FLPR_XKEY',adapter='spell-key-v1',extension='SPL'},
}

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
    local spec=QUESTS[id]
    if endpoint and endpoint.adapter_id=='mode2-reward-v1' then
        -- The exporter binds this endpoint to the original table row and its
        -- reviewed grant. Derive the saved flags from that row, not table order;
        -- a BG1 row's flags are its own (B1_<row>, BG2's are M2_<row>).
        local campaign,row=id:match('^original%-source:(bg[12]):mode2:item:([0-9]+)$')
        local resource=source and source.record and source.record.resource
        local extension,name
        if type(resource)=='string' then extension,name=resource:match('^(%a+):([a-z0-9_#%-]+)$') end
        if row and (extension=='bcs' or extension=='dlg') and name then
            local suffix=(campaign=='bg1' and 'B1_' or 'M2_')..row
            spec={quest=name,resource=resource,extension=extension:upper(),adapter='mode2-reward-v1',
                selected='FLPR_Q'..suffix,consumed='FLPR_X'..suffix}
        end
    end
    local legacy=source and source.record and source.record.adapter_id=='blpool-v1'
    local valid=spec and source and endpoint and type(source.record)=='table' and
        source.quantity==1 and endpoint.kind=='guarded-script' and
        same(endpoint.payload,source.record.record)
    if legacy then
        valid=valid and source.record.kind=='scripted' and id==ID and source.record.resource=='bcs:blpool' and
            source.record.selector=='pool-reward' and endpoint.adapter_id=='blpool-v1'
    else
        valid=valid and source.record.kind=='authored-source' and
            source.record.adapter_id=='original-source-v1' and
            source.record.resource==spec.resource and endpoint.adapter_id==(spec.adapter or 'original-quest-v1') and
            endpoint.quest==spec.quest and endpoint.resource==spec.resource and
            endpoint.selected_global==spec.selected and endpoint.consumed_global==spec.consumed
    end
    assert(valid,'CAMPAIGN_SCRIPT_BINDING_INVALID')
    local resources=endpoint.resources or {spec.resource}
    assert(type(resources)=='table' and #resources>0,'CAMPAIGN_SCRIPT_BINDING_INVALID')
    local primary=false
    for _,resource in ipairs(resources) do
        local extension,name=resource:match('^(%a+):([a-z0-9_#%-]+)$')
        assert(name and #name<=8 and (extension=='dlg' or extension=='bcs' or extension=='spl'),
            'CAMPAIGN_SCRIPT_BINDING_INVALID')
        primary=primary or resource==spec.resource
        local bytes=endpoint.resources and type(expected)=='table' and expected[resource] or expected
        assert(type(bytes)=='string' and #bytes>0,'CAMPAIGN_SCRIPT_PATCH_CHANGED')
        -- The game must still hold the bytes this revision was exported with.
        local actual=read('override/'..name:upper()..'.'..extension:upper())
        assert(actual==bytes,'CAMPAIGN_SCRIPT_PATCH_CHANGED')
    end
    assert(primary,'CAMPAIGN_SCRIPT_BINDING_INVALID')
    assert(type(api.EEex_GameState_SetGlobalInt)=='function','CAMPAIGN_SCRIPT_GLOBALS_MISSING')
    if spec.adapter=='spell-key-v1' then
        assert(api.FLPR and api.FLPR.Spell and api.FLPR.Spell.callback==api.FLPRKEY and
            type(api.FLPRKEY)=='function','CAMPAIGN_SPELL_SERVICE_MISSING')
    end
    return setmetatable({api=api,revision=bundle.manifest.revision,index=index,
        resource_hash=source.resource_sha256,binding={id=id,payload=endpoint.payload},
        selected=spec.selected,consumed=spec.consumed,spell=spec.adapter=='spell-key-v1',
        generation=1,installed=false},Member)
end

function Member:_bundle(bundle)
    local source=bundle.manifest.sources[self.index]
    assert(bundle.manifest.revision==self.revision and source and source.id==self.binding.id and
        source.resource_sha256==self.resource_hash and same(source.record.record,self.binding.payload),
        'CAMPAIGN_SCRIPT_REVISION_MISMATCH')
end
function Member:reset()
    -- Save loading supplies its own GLOBALs; never clear them here.
    self.generation=self.generation+1
end
function Member:prepare_new(bundle,controller)
    self:_bundle(bundle)
    assert(not self:_error(),self:_error())
    local api=self.api
    assert(controller and controller.manifest and controller.manifest.revision==self.revision and
        api.EEex_GameState_GetGlobalInt('FLPR_FRESH')==1,
        'CAMPAIGN_SCRIPT_CAPTURE_REQUIRED')
    assert(api.EEex_GameState_GetGlobalInt(self.selected)==0 and api.EEex_GameState_GetGlobalInt(self.consumed)==0,
        'CAMPAIGN_SCRIPT_ALREADY_STARTED')
    if controller.source_is_kept and controller:source_is_kept(self.index) then return true end
    api.EEex_GameState_SetGlobalInt(self.selected,1)
    assert(api.EEex_GameState_GetGlobalInt(self.selected)==1,'CAMPAIGN_SCRIPT_SELECTION_FAILED')
    return true
end
function Member:inspect()
    return {installed=self.installed,generation=self.generation,
        selected=self.api.EEex_GameState_GetGlobalInt(self.selected),
        consumed=self.api.EEex_GameState_GetGlobalInt(self.consumed),last_error=self:_error()}
end
function Member:_error()
    if not self.spell then return nil end
    local service=self.api.FLPR and self.api.FLPR.Spell
    if not service or service.callback~=self.api.FLPRKEY then return 'CAMPAIGN_SPELL_SERVICE_MISSING' end
    return service.last_error and ('CAMPAIGN_SPELL_CALLBACK_FAILED:'..service.last_error) or nil
end
function Member:adapter(bundle)
    self:_bundle(bundle)
    return setmetatable({gate=self,generation=self.generation},Adapter)
end
function Adapter:destroy() self.destroyed=true end
function Adapter:_active()
    return not self.destroyed and not self.gate:_error() and self.gate.installed and self.generation==self.gate.generation and
        self.gate.api.EEex_GameState_GetGlobalInt(self.gate.selected)==1
end
function Adapter:_request(request,kind)
    local gate=self.gate
    return self:_active() and type(request)=='table' and request.kind==kind and request.mode=='observe-only' and
        request.index==gate.index and request.revision==gate.revision and request.source_id==gate.binding.id and
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
    if self.gate:_error() then return nil,self.gate:_error() end
    if not self:_request(request,'source-guard') then return nil,'CAMPAIGN_SCRIPT_REQUEST_MISMATCH' end
    self.session=request.session
    local result=receipt(request)
    result.pre_access,result.exact_source_bound,result.generation=1,1,self.generation
    result.active=function() return self:_active() end
    return result
end
function Adapter:observe_removal(request)
    if self.gate:_error() then return nil,self.gate:_error() end
    if not self:_request(request,'guarded-source-removal') or request.session~=self.session or
        request.guard_generation~=self.generation then return nil,'CAMPAIGN_SCRIPT_REQUEST_MISMATCH' end
    if self.gate.api.EEex_GameState_GetGlobalInt(self.gate.consumed)~=1 then return nil,'SOURCE_NOT_LOADED' end
    local result=receipt(request)
    result.exact_source_event,result.removed,result.remaining=1,1,0
    return result
end
return Script
