-- Opt-in campaign bridge for configured ARE container and creature sources. Never installed
-- by the normal TP2. Caller pins executable/helper/resource hashes before use.
-- build = tools/probes/FLPRGateAsm.lua; compile = FLPRCampaignPolicy.lua.
local Gate = {}
local Runtime = {}; Runtime.__index = Runtime
local Adapter = {}; Adapter.__index = Adapter
local Group = {}; Group.__index = Group
local CAPACITY, HEADER, STRIDE = 2048, 128, 128
local CONTEXT=HEADER+(CAPACITY+1)*STRIDE
local MEMORY_SIZE=CONTEXT+32+64*64
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
local actor_sites = {
    {rva=0x18C448,code='actor',call=true,bytes='e8d34d1b00498db580000000e93d010000'},
    {rva=0x18C591,code='actor',call=true,bytes='e88a4c1b00488bf84885c00f8444010000'},
}
local store_sites = {
    {rva=0x250765,size=8,code='store',bytes='0f1047080f114308'},
    {rva=0x405295,size=7,code='saved_store',bytes='c6435001498bcf'},
    {rva=0x250967,size=5,code='store_done',bytes='4c8b742448'},
}
local function hash(value)
    return type(value)=='string' and #value==64 and not value:find('[^0-9a-f]')
end
local function integer(value,lo,hi)
    return type(value)=='number' and value==math.floor(value) and value>=lo and value<=hi
end
local function payload(value)
    if type(value)~='table' or type(value.resref)~='string' or #value.resref<1 or #value.resref>8 or
        value.resref:find('[^a-z0-9#_%-]') or value.expiration~=0 or
        (value.flags~=0 and value.flags~=1) or type(value.charges)~='table' then return false end
    for i=1,3 do if not integer(value.charges[i],0,65535) then return false end end
    return true
end
local function same_payload(a,b)
    return payload(a) and payload(b) and a.resref==b.resref and a.flags==b.flags and
        a.charges[1]==b.charges[1] and a.charges[2]==b.charges[2] and a.charges[3]==b.charges[3]
end
function Gate.source_ids(fixture)
    local config=type(fixture)=='table' and fixture.source_gate
    assert(type(config)=='table' and config.schema=='flpr-unvisited-fixture-gate-v1' and
        (config.source_id==nil)~=(config.source_ids==nil), 'CAMPAIGN_GATE_CONFIG_INVALID')
    local ids=config.source_ids or {config.source_id}
    assert(type(ids)=='table' and #ids>0, 'CAMPAIGN_GATE_CONFIG_INVALID')
    local seen,result,count={},{},0
    for key,id in pairs(ids) do
        assert(integer(key,1,#ids) and type(id)=='string' and not seen[id], 'CAMPAIGN_GATE_CONFIG_INVALID')
        seen[id],result[key]=true,id
        count=count+1
    end
    assert(count==#ids, 'CAMPAIGN_GATE_CONFIG_INVALID')
    return result
end
local function validate(bundle,id)
    assert(type(bundle)=='table' and type(bundle.manifest)=='table' and type(bundle.fixture)=='table',
        'CAMPAIGN_GATE_CONFIG_INVALID')
    local m,f=bundle.manifest,bundle.fixture
    assert(hash(m.revision) and f.revision==m.revision, 'CAMPAIGN_GATE_CONFIG_INVALID')
    local found=false
    for _,selected_id in ipairs(Gate.source_ids(f)) do if selected_id==id then found=true end end
    assert(found, 'CAMPAIGN_GATE_SOURCE_INVALID')
    local endpoint=f.sources and f.sources[id]
    if endpoint and endpoint.kind=='guarded-store' then
        local index,selected
        for i,source in ipairs(m.sources) do if source.id==id then index,selected=i,source end end
        local store,record=id:match('^sto:([a-z0-9#_%-]+)/item/(%d+)$')
        record=tonumber(record)
        assert(index and store and #store<=8 and integer(record,0,2147483646) and
            hash(selected.resource_sha256) and integer(selected.quantity,1,2147483647),
            'CAMPAIGN_GATE_SOURCE_INVALID')
        local source=selected.record
        assert(source.kind=='store' and source.id==id and source.resource=='sto:'..store and
            source.owner==source.resource and source.record.record_index==record and
            not source.record.infinite_supply and source.record.quantity==selected.quantity and
            endpoint.store==store and endpoint.quantity==selected.quantity and endpoint.record_index==record and
            same_payload(endpoint.payload,source.record) and
            f.resource_hashes[store:upper()..'.STO']==selected.resource_sha256,
            'CAMPAIGN_GATE_BINDING_INVALID')
        return m.revision,index,selected.resource_sha256,{id=id,kind='store',store=store,
            record=record,quantity=selected.quantity,payload=endpoint.payload}
    end
    if endpoint and endpoint.kind=='guarded-creature' then
        local index,selected
        for i,source in ipairs(m.sources) do if source.id==id then index,selected=i,source end end
        local area,actor=endpoint.actor_owner:match('^are:([a-z0-9#_%-]+)/actor/(%d+)$')
        actor=tonumber(actor)
        assert(index and selected.quantity==1 and hash(selected.resource_sha256) and area and
            #area<=8 and integer(actor,0,65534) and endpoint.actor_index==actor and
            endpoint.area:lower()==area and type(endpoint.script_name)=='string' and
            #endpoint.script_name>0 and #endpoint.script_name<=32 and not endpoint.script_name:find('%z'),
            'CAMPAIGN_GATE_SOURCE_INVALID')
        local source=selected.record
        assert((source.kind=='cre-template' or source.kind=='embedded-cre') and source.id==id and
            source.resource==endpoint.creature_resource and
            id==source.owner..'/item/'..source.record.record_index and
            ((source.kind=='cre-template' and source.owner==source.resource) or
             (source.kind=='embedded-cre' and source.owner==endpoint.actor_owner and source.resource=='are:'..area)) and
            same_payload(endpoint.payload,source.record) and source.record.slots and
            #source.record.slots==1 and integer(source.record.slots[1],0,37),
            'CAMPAIGN_GATE_BINDING_INVALID')
        local replacement=selected.replacement and selected.replacement.resref
        local slot=source.record.slots[1]
        assert(endpoint.replacement==replacement and
            ((slot>=21 and slot<=36) or replacement) and
            (not replacement or replacement=='blank' or
             (type(replacement)=='string' and #replacement>=1 and #replacement<=8 and
              not replacement:find('[^a-z0-9#_%-]') and replacement~=endpoint.payload.resref and
              hash(selected.replacement.resource_sha256) and
              f.resource_hashes[replacement:upper()..'.ITM']==selected.replacement.resource_sha256)),
            'CAMPAIGN_GATE_REPLACEMENT_INVALID')
        local kind,name=source.resource:match('^(%a+):([a-z0-9#_%-]+)$')
        assert(kind and f.resource_hashes[name:upper()..'.'..kind:upper()]==selected.resource_sha256 and
            hash(f.resource_hashes[area:upper()..'.ARE']), 'CAMPAIGN_GATE_BINDING_INVALID')
        return m.revision,index,selected.resource_sha256,{id=id,kind='creature',area=area,actor=actor,
            record=source.record.record_index,slot=source.record.slots[1],name=endpoint.script_name,
            creature_resource=source.resource,payload=endpoint.payload,replacement=replacement,
            area_hash=f.resource_hashes[area:upper()..'.ARE']}
    end
    local area,container,record=id:match('^are:([a-z0-9#_%-]+)/container/(%d+)/item/(%d+)$')
    container,record=tonumber(container),tonumber(record)
    assert(area and #area<=8 and integer(container,0,65534) and integer(record,0,2147483646),
        'CAMPAIGN_GATE_SOURCE_INVALID')
    local index,selected
    for i,source in ipairs(m.sources) do
        if source.id==id then assert(not selected,'CAMPAIGN_GATE_SOURCE_DUPLICATED'); index,selected=i,source end
    end
    assert(index and selected.quantity==1 and hash(selected.resource_sha256) and
        selected.record.kind=='container' and selected.record.id==id and
        selected.record.resource=='are:'..area and selected.record.owner=='are:'..area..'/container/'..container and
        selected.record.record.record_index==record and payload(selected.record.record),
        'CAMPAIGN_GATE_SOURCE_INVALID')
    assert(endpoint and endpoint.kind=='guarded-container' and type(endpoint.area)=='string' and
        endpoint.area:lower()==area and type(endpoint.script_name)=='string' and
        #endpoint.script_name>0 and #endpoint.script_name<=32 and not endpoint.script_name:find('%z') and
        integer(endpoint.x,0,32767) and integer(endpoint.y,0,32767) and
        integer(endpoint.container_type or 4,0,65535) and
        same_payload(endpoint.payload,selected.record.record) and f.resource_hashes and
        f.resource_hashes[area:upper()..'.ARE']==selected.resource_sha256, 'CAMPAIGN_GATE_BINDING_INVALID')
    local p=endpoint.payload
    return m.revision,index,selected.resource_sha256,{id=id,area=area,container=container,record=record,
        name=endpoint.script_name,x=endpoint.x,y=endpoint.y,container_type=endpoint.container_type or 4,
        payload={resref=p.resref,expiration=0,flags=p.flags,charges={p.charges[1],p.charges[2],p.charges[3]}}}
end
local function hex(value)
    return (value:gsub('.',function(c) return string.format('%02x',c:byte()) end))
end
local function prepare_creature(api,bundle,build,compile,pristine,id,base,cache)
    local revision,index,resource_hash,binding=validate(bundle,id)
    local area=assert(pristine[binding.area:upper()], 'CAMPAIGN_GATE_PRISTINE_MISSING')
    local cre=assert(pristine.creatures and pristine.creatures[revision] and pristine.creatures[revision][id],
        'CAMPAIGN_GATE_CREATURE_MISSING')
    local bytes,original,removed=area.bytes,cre.original,cre.removed
    assert(area.resource_sha256==binding.area_hash and bytes:sub(1,8)=='AREAV1.0' and
        original:sub(1,8)=='CRE V1.0' and #original>=0x2D4 and #original==#removed and
        removed:sub(1,8)=='CRE V1.0', 'CAMPAIGN_GATE_PRISTINE_INVALID')
    local function u16(data,offset) local a,b=data:byte(offset+1,offset+2); return a+b*256 end
    local function u32(data,offset) return u16(data,offset)+u16(data,offset+2)*65536 end
    local owner=u32(bytes,0x54)+binding.actor*272
    assert(binding.actor<u16(bytes,0x58) and owner>=0x11C and owner+272<=#bytes,
        'CAMPAIGN_GATE_PRISTINE_LAYOUT_INVALID')
    local ref=bytes:sub(owner+0x81,owner+0x88):match('^[^%z]*'):lower()
    if binding.creature_resource:sub(1,4)=='cre:' then
        assert(ref==binding.creature_resource:sub(5),'CAMPAIGN_GATE_CREATURE_BINDING_MISMATCH')
    else
        local start,length=u32(bytes,owner+0x88),u32(bytes,owner+0x8C)
        -- The engine tests only the first marker byte (e.g. "*EMBED").
        assert((ref=='' or ref:sub(1,1)=='*') and length==#original and bytes:sub(start+1,start+length)==original,
            'CAMPAIGN_GATE_CREATURE_BINDING_MISMATCH')
    end
    local items,slots,count=u32(original,0x2BC),u32(original,0x2B8),u32(original,0x2C0)
    assert(items>=0x2D4 and items+count*20<=#original and binding.record<count and
        slots>=0x2D4 and slots+80<=#original and u16(original,slots+2*binding.slot)==binding.record and
        u32(removed,0x2BC)==items and u32(removed,0x2B8)==slots,
        'CAMPAIGN_GATE_CREATURE_LAYOUT_INVALID')
    local item=items+binding.record*20
    if binding.replacement and binding.replacement~='blank' then
        -- As in del_cre_itm: replace only the resref. Keep slot, selected weapon,
        -- charges and flags, so the original owner can still use the substitute.
        local replacement=binding.replacement..string.rep('\0',8-#binding.replacement)
        assert(removed==original:sub(1,item)..replacement..original:sub(item+9),
            'CAMPAIGN_GATE_CREATURE_REPLACEMENT_MISMATCH')
    else
        assert(u32(removed,0x2C0)==count-1 and u16(removed,slots+2*binding.slot)==65535,
            'CAMPAIGN_GATE_CREATURE_LAYOUT_INVALID')
    end
    local raw,name=original:sub(item+1,item+20),original:sub(0x281,0x2A0)
    assert(name:match('^[^%z]*'):lower()==binding.name:lower() and
        raw:sub(1,8):match('^[^%z]*'):lower()==binding.payload.resref and u16(original,item+8)==0 and
        u16(original,item+10)==binding.payload.charges[1] and u16(original,item+12)==binding.payload.charges[2] and
        u16(original,item+14)==binding.payload.charges[3] and u32(original,item+16)==binding.payload.flags,
        'CAMPAIGN_GATE_PRISTINE_BINDING_MISMATCH')
    local cached=cache[binding.area_hash]
    local buffer=api.EEex_Malloc(MEMORY_SIZE+(cached and 0 or #bytes)+2*#original)
    assert(buffer and buffer~=0,'CAMPAIGN_GATE_ALLOC_FAILED')
    for offset=0,MEMORY_SIZE-1,8 do api.EEex_Write64(buffer+offset,0) end
    local cursor=buffer+MEMORY_SIZE
    local function copy(value)
        local address=cursor
        for offset=1,#value do
            local byte=value:byte(offset)
            api.EEex_Write8(address+offset-1,byte>=128 and byte-256 or byte)
        end
        cursor=cursor+#value
        return address
    end
    local expected=cached or copy(bytes); cache[binding.area_hash]=expected
    local policy=compile(revision,index)
    -- Reuse the ARE invocation binding. Its owner pointer is this actor record.
    policy.provenance={expected=expected,length=#bytes,container_offset=owner,item_offset=owner}
    policy.creature={expected=copy(original),removed=copy(removed),length=#original,
        item_offset=item,index=binding.record}
    local self=setmetatable({api=api,base=base,buffer=buffer,revision=revision,index=index,
        resource_hash=resource_hash,binding=binding,policy=policy,detours={},installed=false,
        expected={name_raw=hex(name),payload_raw=hex(raw),index=binding.record,
            detail1_raw=string.rep('0',16),detail2_raw=string.rep('0',16)}},Runtime)
    return self,build(buffer,base,policy)
end
local function prepare(api,bundle,build,compile,pristine,id,base,cache)
    if bundle.fixture.sources[id].kind=='guarded-store' then
        local revision,index,resource_hash,binding=validate(bundle,id)
        local sto=assert(pristine.stores and pristine.stores[revision] and pristine.stores[revision][id],
            'CAMPAIGN_GATE_STORE_MISSING')
        local original,removed=sto.original,sto.removed
        assert(type(original)=='string' and #original>=0x9C and #original==#removed and
            original:sub(1,8)=='STORV1.0' and removed:sub(1,8)=='STORV1.0',
            'CAMPAIGN_GATE_PRISTINE_INVALID')
        local function u16(data,offset) local a,b=data:byte(offset+1,offset+2); return a+b*256 end
        local function u32(data,offset) return u16(data,offset)+u16(data,offset+2)*65536 end
        local items,count=u32(original,0x34),u32(original,0x38)
        local item=items+binding.record*28
        assert(items>=0x9C and items+count*28<=#original and binding.record<count and
            u32(removed,0x34)==items and u32(removed,0x38)==count-1,
            'CAMPAIGN_GATE_STORE_LAYOUT_INVALID')
        local raw=original:sub(item+1,item+28)
        assert(raw:sub(1,8):match('^[^%z]*'):lower()==binding.payload.resref and u16(original,item+8)==0 and
            u16(original,item+10)==binding.payload.charges[1] and u16(original,item+12)==binding.payload.charges[2] and
            u16(original,item+14)==binding.payload.charges[3] and u32(original,item+16)==binding.payload.flags and
            u32(original,item+20)==binding.quantity and u32(original,item+24)==0 and
            removed:sub(items+1,items+(count-1)*28)==
                original:sub(items+1,item)..original:sub(item+29,items+count*28),
            'CAMPAIGN_GATE_PRISTINE_BINDING_MISMATCH')
        local buffer=api.EEex_Malloc(MEMORY_SIZE+2*#original)
        assert(buffer and buffer~=0,'CAMPAIGN_GATE_ALLOC_FAILED')
        for offset=0,MEMORY_SIZE-1,8 do api.EEex_Write64(buffer+offset,0) end
        local function copy(value,address)
            for offset=1,#value do
                local byte=value:byte(offset)
                api.EEex_Write8(address+offset-1,byte>=128 and byte-256 or byte)
            end
            return address
        end
        local policy=compile(revision,index)
        policy.store={expected=copy(original,buffer+MEMORY_SIZE),removed=copy(removed,buffer+MEMORY_SIZE+#original),
            length=#original,items=items,count=count-1,index=binding.record,ref=binding.store,
            context=CONTEXT,payload=raw}
        local self=setmetatable({api=api,base=base,buffer=buffer,revision=revision,index=index,
            resource_hash=resource_hash,binding=binding,policy=policy,detours={},installed=false,
            expected={name_raw=hex(binding.store:upper()..string.rep('\0',32-#binding.store)),
                payload_raw=hex(raw:sub(1,20)),index=binding.record,
                detail1_raw=hex(raw:sub(21,28)),detail2_raw=string.rep('0',16)}},Runtime)
        return self,build(buffer,base,policy)
    end
    if bundle.fixture.sources[id].kind=='guarded-creature' then
        return prepare_creature(api,bundle,build,compile,pristine,id,base,cache)
    end
    local revision,index,resource_hash,binding=validate(bundle,id)
    pristine=pristine.bytes and pristine or pristine[binding.area:upper()]
    -- The isolated harness hashes this immutable copy, the ARE and both frozen
    -- manifests before launch. Runtime copies it to separate owned memory; it
    -- must never compare a resource buffer to itself or demand an unloaded ARE.
    assert(type(pristine)=='table' and pristine.resource_sha256==resource_hash and
        type(pristine.bytes)=='string' and #pristine.bytes>=0x11C and #pristine.bytes<=2147483647 and
        pristine.bytes:sub(1,8)=='AREAV1.0', 'CAMPAIGN_GATE_PRISTINE_INVALID')
    local bytes=pristine.bytes
    local function u16(offset)
        local a,b=bytes:byte(offset+1,offset+2); return a+b*256
    end
    local function u32(offset)
        local a,b,c,d=pristine.bytes:byte(offset+1,offset+4)
        return a+b*256+c*65536+d*16777216
    end
    local containers,items,item_count=u32(0x70),u32(0x78),u16(0x76)
    assert(containers>=0x11C and containers+u16(0x74)*192<=#bytes and binding.container<u16(0x74) and
        items>=0x11C and items+item_count*20<=#bytes and binding.record<item_count,
        'CAMPAIGN_GATE_PRISTINE_LAYOUT_INVALID')
    local owner=containers+binding.container*192
    local item=items+binding.record*20
    local name=bytes:sub(owner+1,owner+32)
    local raw=bytes:sub(item+1,item+20)
    assert(name:match('^[^%z]*'):lower()==binding.name:lower() and u16(owner+0x20)==binding.x and
        u16(owner+0x22)==binding.y and u16(owner+0x24)==binding.container_type and
        u32(owner+0x40)<=binding.record and binding.record<u32(owner+0x40)+u32(owner+0x44) and
        u32(owner+0x40)+u32(owner+0x44)<=item_count and
        raw:sub(1,8):match('^[^%z]*'):lower()==binding.payload.resref and u16(item+8)==0 and
        u16(item+10)==binding.payload.charges[1] and u16(item+12)==binding.payload.charges[2] and
        u16(item+14)==binding.payload.charges[3] and u32(item+16)==binding.payload.flags,
        'CAMPAIGN_GATE_PRISTINE_BINDING_MISMATCH')
    local expected={name_raw=hex(name),payload_raw=hex(raw),index=binding.record,
        detail1_raw=hex(bytes:sub(owner+0x41,owner+0x48)),
        detail2_raw=hex(bytes:sub(owner+0x21,owner+0x28))}
    local policy=compile(revision,index)
    local cached=cache[resource_hash]
    local buffer=api.EEex_Malloc(MEMORY_SIZE+(cached and 0 or #pristine.bytes))
    assert(buffer and buffer~=0,'CAMPAIGN_GATE_ALLOC_FAILED')
    for offset=0,MEMORY_SIZE-1,8 do api.EEex_Write64(buffer+offset,0) end
    local expected_pointer=cached or buffer+MEMORY_SIZE
    if not cached then
        -- WriteLString truncates binary data at NUL; Write8 takes signed bytes.
        for offset=1,#pristine.bytes do
            local byte=pristine.bytes:byte(offset)
            api.EEex_Write8(expected_pointer+offset-1,byte>=128 and byte-256 or byte)
        end
        for offset=1,#pristine.bytes do
            assert(api.EEex_ReadU8(expected_pointer+offset-1)==pristine.bytes:byte(offset),
                'CAMPAIGN_GATE_PRISTINE_COPY_FAILED')
        end
        cache[resource_hash]=expected_pointer
    end
    policy.provenance={expected=expected_pointer,length=#pristine.bytes,
        container_offset=owner,item_offset=items}
    policy.source={area=binding.area,name=name,payload=raw,index=binding.record,
        inventory=bytes:sub(owner+0x41,owner+0x48),position=bytes:sub(owner+0x21,owner+0x28)}
    local self=setmetatable({api=api,base=base,buffer=buffer,revision=revision,index=index,
        resource_hash=resource_hash,binding=binding,expected=expected,policy=policy,detours={},installed=false},Runtime)
    return self,build(buffer,base,policy)
end

-- Each block preserves registers and flags. A matched item exits the shared
-- hook immediately; other blocks see the original constructor inputs.
function Gate.compose(assemblies)
    local joined={}
    for index,assembly in ipairs(assemblies) do
        for name,code in pairs(assembly) do
            joined[name]=(joined[name] or '')..code:gsub('gate_', 'source'..index..'_')..'\n'
        end
    end
    return joined
end
function Gate.install(api,bundle,build,compile,pristine,Script)
    local bundles=bundle.manifest and {bundle} or bundle
    assert(type(bundles)=='table' and #bundles>0, 'CAMPAIGN_GATE_CONFIG_INVALID')
    assert(api.EEex_Active and not api.FLPR_SOURCE_GATE and not api.FLPR_AREA_TRACE and
        not api.FLPR_CAMPAIGN_GATE, 'CAMPAIGN_GATE_INSTALL_CONTEXT_INVALID')
    assert(type(api.EEex_GameState_AddDestroyedListener)=='function' and
        type(api.EEex_GameState_GetGlobalInt)=='function' and type(api.EEex_Write8)=='function',
        'CAMPAIGN_GATE_LIFECYCLE_MISSING')
    local base=api.EEex_GetImageBase()
    local selected_sites={}
    local physical,creatures,stores=false,false,false
    for _,selected in ipairs(bundles) do
        for _,endpoint in pairs(selected.fixture.sources) do
            if endpoint.kind~='guarded-script' then physical=true end
            if endpoint.kind=='guarded-creature' then creatures=true end
            if endpoint.kind=='guarded-store' then stores=true end
        end
    end
    if physical then for _,site in ipairs(sites) do selected_sites[#selected_sites+1]=site end end
    if creatures then
        assert(type(api.EEex_HookBeforeCall)=='function','CAMPAIGN_GATE_CREATURE_HOOK_MISSING')
        for _,site in ipairs(actor_sites) do selected_sites[#selected_sites+1]=site end
    end
    if stores then for _,site in ipairs(store_sites) do selected_sites[#selected_sites+1]=site end end
    for _,site in ipairs(selected_sites) do
        local offset=0
        for byte in site.bytes:gmatch('%x%x') do
            assert(api.EEex_ReadU8(base+site.rva+offset)==tonumber(byte,16),'CAMPAIGN_GATE_CODE_MISMATCH')
            offset=offset+1
        end
    end
    local members,assemblies,cache,by_revision={},{},{},{}
    local count=0
    for key in pairs(bundles) do assert(integer(key,1,#bundles), 'CAMPAIGN_GATE_CONFIG_INVALID'); count=count+1 end
    assert(count==#bundles, 'CAMPAIGN_GATE_CONFIG_INVALID')
    for _,selected in ipairs(bundles) do
        local revision=selected.manifest.revision
        assert(not by_revision[revision], 'CAMPAIGN_GATE_REVISION_DUPLICATED')
        local current={}; by_revision[revision]=current
        for _,id in ipairs(Gate.source_ids(selected.fixture)) do
            local member,assembly
            if selected.fixture.sources[id].kind=='guarded-script' then
                assert(Script,'CAMPAIGN_SCRIPT_ADAPTER_MISSING')
                member=Script.new(api,selected,id,pristine.scripts[revision][id])
            else
                member,assembly=prepare(api,selected,build,compile,pristine,id,base,cache)
                assemblies[#assemblies+1]=assembly
            end
            members[#members+1]=member
            current[#current+1]=member
        end
    end
    local self=#members==1 and members[1] or setmetatable({members=members,by_revision=by_revision,installed=false},Group)
    local assembly=Gate.compose(assemblies)
    local detours={}
    for _,member in ipairs(members) do member.detours=detours end
    api.FLPR_CAMPAIGN_GATE=self -- partial installation is never retried in-process
    api.EEex_DisableCodeProtection()
    local ok,failure=pcall(function()
        for _,site in ipairs(selected_sites) do
            if site.code and assembly[site.code] and assembly[site.code]~='' then
                local address=base+site.rva
                if site.call then
                    api.EEex_HookBeforeCall(address,{assembly[site.code]})
                    if api.EEex_HookIntegrityWatchdog_Load then
                        api.EEex_HookIntegrityWatchdog_IgnoreRegisters(address,{
                            api.EEex_HookIntegrityWatchdogRegister.RAX,api.EEex_HookIntegrityWatchdogRegister.RDX})
                    end
                else
                    api.EEex_HookBeforeRestore(address,0,site.size,site.size,{assembly[site.code]})
                    if site.code=='store' and api.EEex_HookIntegrityWatchdog_Load then
                        api.EEex_HookIntegrityWatchdog_IgnoreRegisters(address,{
                            api.EEex_HookIntegrityWatchdogRegister.RDI})
                    end
                end
                assert(api.EEex_ReadU8(address)==0xE9,'CAMPAIGN_GATE_DETOUR_NOT_WRITTEN')
                local target=address+5+api.EEex_Read32(address+1)
                assert(target>0 and (target<address or target>=address+#site.bytes/2),
                    'CAMPAIGN_GATE_DETOUR_INVALID')
                detours[#detours+1]={address=address,target=target}
            end
        end
    end)
    api.EEex_EnableCodeProtection()
    assert(ok,failure)
    api.EEex_GameState_AddDestroyedListener(function()
        self:reset()
    end)
    for _,member in ipairs(members) do
        member.installed=true
        if member.buffer then api.EEex_Write64(member.buffer,1) end -- before New Game / any saved GAM load
    end
    self.installed=true
    return self
end

function Runtime:reset()
    self.enrolled=nil
    local api,buffer=self.api,self.buffer
    api.EEex_Write64(buffer+32,0)
    api.EEex_Write64(buffer+48,0); api.EEex_Write64(buffer+56,0)
    api.EEex_Write64(buffer+112,0)
    if self.binding.kind=='store' then
        for offset=0,24,8 do api.EEex_Write64(buffer+CONTEXT+offset,0) end
    end
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
        detail1=q(112),detail2=q(120),detail1_raw=self:_raw(row+112,8),detail2_raw=self:_raw(row+120,8)}
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
    if self.binding.kind=='store' then result.store_completed=generation>0 and self:_q(CONTEXT+16)==generation end
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
    local revision,index,resource_hash,binding=validate(bundle,self.binding.id)
    local b=self.binding
    assert(revision==self.revision and index==self.index and resource_hash==self.resource_hash and
        binding.id==b.id and binding.name==b.name and binding.x==b.x and binding.y==b.y and
        binding.kind==b.kind and binding.actor==b.actor and binding.area==b.area and
        binding.record==b.record and binding.slot==b.slot and
        binding.creature_resource==b.creature_resource and binding.area_hash==b.area_hash and
        binding.store==b.store and binding.quantity==b.quantity and
        binding.container_type==b.container_type and same_payload(binding.payload,b.payload),
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
        and request.index==gate.index and request.revision==gate.revision and request.source_id==gate.binding.id
        and request.resource_sha256==gate.resource_hash and request.quantity==(gate.binding.quantity or 1) and same_payload(request.payload,gate.binding.payload)
        and type(request.session)=='table' and type(request.token)=='table'
end
local function receipt(request)
    local result={}
    for _,key in ipairs({'kind','mode','index','revision','source_id','resource_sha256','quantity',
        'session','token','guard_generation'}) do result[key]=request[key] end
    local p=request.payload
    result.payload={resref=p.resref,expiration=0,flags=p.flags,charges={p.charges[1],p.charges[2],p.charges[3]}}
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
    local expected=self.gate.expected
    if snapshot.load_skips~=1 or witness.kind~=3 or witness.generation~=snapshot.generation or
        witness.mode~=1 or witness.decision~=1 or witness.index~=expected.index or witness.name_raw~=expected.name_raw or
        witness.payload_raw~=expected.payload_raw or witness.detail1_raw~=expected.detail1_raw or
        witness.detail2_raw~=expected.detail2_raw or
        witness.object_pointer=='0' or witness.other_value=='0' then return nil,'CAMPAIGN_GATE_WITNESS_INVALID' end
    if self.gate.binding.kind=='store' then
        if not snapshot.store_completed then return nil,'SOURCE_NOT_LOADED' end
        local result=receipt(request)
        result.exact_source_event,result.removed,result.remaining=1,request.quantity,0
        return result
    end
    local world; world,reason=self.native:guarded_source_remaining(self.gate.binding.id,witness.object_pointer,witness.other_value)
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

function Group:_members(bundle)
    local members=self.by_revision[bundle.manifest.revision]
    assert(members and #Gate.source_ids(bundle.fixture)==#members, 'CAMPAIGN_GATE_REVISION_MISMATCH')
    for _,member in ipairs(members) do member:_bundle(bundle) end
    return members
end
function Group:prepare_new(bundle,controller)
    local members=self:_members(bundle)
    for _,member in ipairs(members) do member:prepare_new(bundle,controller) end
    self.active_revision=bundle.manifest.revision
    return true
end
function Group:reset()
    self.active_revision=nil
    for _,member in ipairs(self.members) do member:reset() end
end
function Group:inspect(revision)
    revision=revision or self.active_revision
    local result={installed=self.installed,sources={}}
    for _,member in ipairs(revision and assert(self.by_revision[revision]) or self.members) do
        -- Before campaign capture, include revision in each key to avoid merging
        -- two profiles which relocate the same physical record.
        local id=revision and member.binding.id or member.revision..'/'..member.binding.id
        result.sources[id]=member:inspect()
    end
    return result
end
function Group:adapter(bundle,native)
    local adapters={}
    for _,member in ipairs(self:_members(bundle)) do adapters[member.index]=member:adapter(bundle,native) end
    self.active_revision=bundle.manifest.revision
    local group={}
    function group:destroy() for _,adapter in pairs(adapters) do adapter:destroy() end end
    for _,method in ipairs({'observe_guard','observe_removal'}) do
        group[method]=function(_,request)
            local adapter=type(request)=='table' and adapters[request.index]
            if not adapter then return nil,'CAMPAIGN_GATE_REQUEST_MISMATCH' end
            return adapter[method](adapter,request)
        end
    end
    return group
end

return Gate
