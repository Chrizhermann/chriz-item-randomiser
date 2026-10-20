-- Campaign source gate for configured ARE container, creature, store and
-- template sources. Hook locations and their expected bytes: FLPRSite.lua.
-- build = tools/probes/FLPRGateAsm.lua; compile = FLPRCampaignPolicy.lua.
local Site = ...
assert(type(Site)=='table' and type(Site.locate)=='function', 'CAMPAIGN_GATE_SITES_MISSING')
local Gate = {}
local Runtime = {}; Runtime.__index = Runtime
local Adapter = {}; Adapter.__index = Adapter
local Group = {}; Group.__index = Group
local CAPACITY, HEADER, STRIDE = 2048, 128, 128
local CONTEXT=HEADER+(CAPACITY+1)*STRIDE
local MEMORY_SIZE=CONTEXT+32+64*64
-- Entries without code are only checked: the container call's return address,
-- the constructor frame and the item loop are what the item hook relies on.
local sites = {
    {key='gate_enter',size=8,code='enter'},
    {key='gate_capture',size=7,code='capture'},
    {key='gate_item',size=7,code='item'},
    {key='gate_area_enter',size=8,code='area_enter'},
    {key='gate_area_exit',size=8,code='area_exit'},
    {key='gate_container_call'},
    {key='gate_container_entry'},
    {key='gate_container_skip'},
}
local actor_sites = {
    {key='gate_actor1',code='actor',call=true},
    {key='gate_actor2',code='actor',call=true},
}
local store_sites = {
    {key='gate_store',size=8,code='store'},
    {key='gate_saved_store',size=7,code='saved_store'},
    {key='gate_store_done',size=5,code='store_done'},
}
local template_site = {key='gate_template',size=5,code='template'}
-- The item hook resumes at the instruction after the item address computation
-- (the verified bytes of gate_item cover it).
local ITEM_RESUME = 15
local function hash(value)
    return type(value)=='string' and #value==64 and not value:find('[^0-9a-f]')
end
local function integer(value,lo,hi)
    return type(value)=='number' and value==math.floor(value) and value>=lo and value<=hi
end
local function payload(value)
    if type(value)~='table' or type(value.resref)~='string' or #value.resref<1 or #value.resref>8 or
        value.resref:find('[^a-z0-9#_%-]') or value.expiration~=0 or
        not integer(value.flags,0,3) or type(value.charges)~='table' then return false end
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
    assert(type(ids)=='table' and (#ids>0 or type(fixture.spell_shield)=='table' or
        type(fixture.cursed_disguises)=='table'), 'CAMPAIGN_GATE_CONFIG_INVALID')
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
    if endpoint and endpoint.kind=='guarded-template' then
        local index,selected
        for i,source in ipairs(m.sources) do if source.id==id then index,selected=i,source end end
        local authored=selected and selected.record.kind=='authored-source'
        assert(index and selected.quantity==1 and (selected.record.kind=='cre-template' or authored) and
            selected.record.resource==endpoint.creature_resource and
            (authored or id==selected.record.resource..'/item/'..selected.record.record.record_index) and
            same_payload(endpoint.payload,selected.record.record) and
            f.resource_hashes[selected.record.resource:sub(5):upper()..'.CRE']==selected.resource_sha256,
            'CAMPAIGN_GATE_TEMPLATE_INVALID')
        local records=authored and endpoint.record_indices or {selected.record.record.record_index}
        assert(type(records)=='table' and #records>0,'CAMPAIGN_GATE_TEMPLATE_INVALID')
        local replacement=selected.replacement and selected.replacement.resref
        if authored and selected.record.authored.source=='unshey.fl' then replacement='flrbelt1' end
        assert(endpoint.replacement==replacement,'CAMPAIGN_GATE_REPLACEMENT_INVALID')
        return m.revision,index,selected.resource_sha256,{id=id,kind='template',
            creature_resource=selected.record.resource,record=records[1],records=records,
            payload=endpoint.payload,replacement=replacement}
    end
    if endpoint and endpoint.kind=='guarded-store' then
        local index,selected
        for i,source in ipairs(m.sources) do if source.id==id then index,selected=i,source end end
        if endpoint.stock_option=='more_spell_shield' then
            local spec=m.spell_shield
            assert(index and spec and spec.source_id==id and id=='original-source:bg2:mode2:item:228' and
                selected.record.kind=='store' and selected.record.adapter_id=='original-source-v1' and
                selected.record.resource=='sto:udduer01' and selected.resource_sha256==spec.resource_sha256 and
                selected.quantity==spec.quantity and endpoint.quantity==spec.quantity and endpoint.store=='udduer01' and
                endpoint.record_index==spec.record_index and endpoint.multiple_stock==true and
                same_payload(selected.record.record,spec.payload) and same_payload(endpoint.payload,spec.payload) and
                f.resource_hashes['UDDUER01.STO']==spec.resource_sha256,'CAMPAIGN_GATE_STOCK_BINDING_INVALID')
            return m.revision,index,spec.resource_sha256,{id=id,kind='store',store='udduer01',
                record=spec.record_index,quantity=spec.quantity,payload=spec.payload,stock_option=true}
        end
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
    local replacement=selected.replacement and selected.replacement.resref
    if replacement=='blank' then replacement=nil end
    assert(endpoint.replacement==replacement,'CAMPAIGN_GATE_REPLACEMENT_INVALID')
    return m.revision,index,selected.resource_sha256,{id=id,area=area,container=container,record=record,
        name=endpoint.script_name,x=endpoint.x,y=endpoint.y,container_type=endpoint.container_type or 4,
        replacement=replacement,
        payload={resref=p.resref,expiration=0,flags=p.flags,charges={p.charges[1],p.charges[2],p.charges[3]}}}
end
local function hex(value)
    return (value:gsub('.',function(c) return string.format('%02x',c:byte()) end))
end
local function prepare_creature(api,bundle,build,compile,pristine,id,targets,cache)
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
    local self=setmetatable({api=api,buffer=buffer,revision=revision,index=index,
        resource_hash=resource_hash,binding=binding,policy=policy,detours={},installed=false,
        expected={name_raw=hex(name),payload_raw=hex(raw),index=binding.record,
            detail1_raw=string.rep('0',16),detail2_raw=string.rep('0',16)}},Runtime)
    return self,build(buffer,targets,policy)
end
local function prepare(api,bundle,build,compile,pristine,id,targets,cache)
    if bundle.fixture.sources[id].kind=='guarded-template' then
        local revision,index,resource_hash,binding=validate(bundle,id)
        local original=assert(pristine.templates[revision][id],'CAMPAIGN_GATE_TEMPLATE_MISSING')
        assert(original:sub(1,8)=='CRE V1.0' and #original>=0x2D4,'CAMPAIGN_GATE_TEMPLATE_INVALID')
        local buffer=api.EEex_Malloc(MEMORY_SIZE)
        for offset=0,MEMORY_SIZE-1,8 do api.EEex_Write64(buffer+offset,0) end
        local policy=compile(revision,index)
        local assembly=build(buffer,targets,policy)
        local self=setmetatable({api=api,buffer=buffer,revision=revision,index=index,
            resource_hash=resource_hash,binding=binding,policy=policy,detours={},installed=false,
            permanent=true,inventory_original=original},Runtime)
        return self,{enter=assembly.enter,capture=assembly.capture}
    end
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
            u32(original,item+20)==binding.quantity and (binding.stock_option or u32(original,item+24)==0) and
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
        local self=setmetatable({api=api,buffer=buffer,revision=revision,index=index,
            resource_hash=resource_hash,binding=binding,policy=policy,detours={},installed=false,
            expected={name_raw=hex(binding.store:upper()..string.rep('\0',32-#binding.store)),
                payload_raw=hex(raw:sub(1,20)),index=binding.record,
                detail1_raw=hex(raw:sub(21,28)),detail2_raw=string.rep('0',16)}},Runtime)
        return self,build(buffer,targets,policy)
    end
    if bundle.fixture.sources[id].kind=='guarded-creature' then
        return prepare_creature(api,bundle,build,compile,pristine,id,targets,cache)
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
    local buffer=api.EEex_Malloc(MEMORY_SIZE+(cached and 0 or #pristine.bytes)+(binding.replacement and 20 or 0))
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
    if binding.replacement then
        local address=buffer+MEMORY_SIZE+(cached and 0 or #pristine.bytes)
        local value=binding.replacement..string.rep('\0',8-#binding.replacement)..raw:sub(9)
        for n=1,#value do
            local byte=value:byte(n);api.EEex_Write8(address+n-1,byte>=128 and byte-256 or byte)
        end
        policy.source.replacement_pointer=address
    end
    local self=setmetatable({api=api,buffer=buffer,revision=revision,index=index,
        resource_hash=resource_hash,binding=binding,expected=expected,policy=policy,detours={},installed=false},Runtime)
    return self,build(buffer,targets,policy)
end

-- Each block preserves registers and flags. A matched item exits the shared
-- hook immediately; other blocks see the original constructor inputs.
function Gate.compose(assemblies,with_parts)
    local joined,parts={},{}
    for index,assembly in ipairs(assemblies) do
        for name,code in pairs(assembly) do
            if code~='' then
                local part=code:gsub('gate_', 'source'..index..'_')..'\n'
                joined[name]=(joined[name] or '')..part
                parts[name]=parts[name] or {}
                parts[name][#parts[name]+1]=part
            end
        end
    end
    if with_parts then return joined,parts end
    return joined
end

-- EEex's near-code allocator accepts one code page per JIT request. A full
-- preset exceeds that page when all source checks are emitted as one hook.
-- Keep whole source blocks together and jump between allocations: CALL would
-- shift the engine stack used by the constructor checks. The entry/exit below
-- follows EEex_HookBeforeRestore / EEex_HookBeforeCall, using EEex's allocator.
function Gate.install_chain(api,site,address,parts)
    local chunks={}
    for _,part in ipairs(parts) do
        if #chunks==0 or #chunks[#chunks]+#part>16384 then chunks[#chunks+1]='' end
        chunks[#chunks]=chunks[#chunks]..part
    end
    api.EEex_HookIntegrityWatchdog_IgnoreRegisters(address,{})
    api.EEex_HookIntegrityWatchdog_DefaultIgnoreStack(address)
    local entry=api.EEex_RunWithAssemblyLabels({{'hook_address',address}},function()
        local tail={'#MANUAL_HOOK_EXIT(0)\n'}
        if site.call then
            tail[#tail+1]='call '..string.format('%.0f',address+5+api.EEex_Read32(address+1))..'\n'
        else
            tail[#tail+1]=api.EEex_StoreBytesAssembly(address,site.size)
        end
        tail[#tail+1]='\njmp '..string.format('%.0f',address+(site.call and 5 or site.size))..'\n'
        local next_address=api.EEex_JITNear(api.EEex_FlattenTable(tail))
        for index=#chunks,1,-1 do
            next_address=api.EEex_JITNear({chunks[index], '\njmp '..string.format('%.0f',next_address)..'\n'})
        end
        return api.EEex_JITNear(api.EEex_FlattenTable({api.EEex_HookIntegrityWatchdog_HookEnter,
            '\njmp '..string.format('%.0f',next_address)..'\n'}))
    end)
    api.EEex_JITAt(address,{'jmp short '..string.format('%.0f',entry)..'\n'..
        string.rep('nop\n',(site.call and 5 or site.size)-5)})
end
local function prepare_stock_option(api,bundle,build,compile,pristine,targets)
    local spec=bundle.manifest.spell_shield
    local native=bundle.fixture.spell_shield
    assert(spec and native and bundle.manifest.original_options.more_spell_shield==true and
        spec.resource=='sto:udduer01' and hash(spec.resource_sha256) and
        native.resource_sha256==spec.resource_sha256 and native.source_id==spec.source_id and
        native.record_index==spec.record_index and native.quantity==spec.quantity and
        same_payload(native.payload,spec.payload),'CAMPAIGN_GATE_STOCK_OPTION_INVALID')
    local revision=bundle.manifest.revision
    local entry=assert(pristine.spell_shield and pristine.spell_shield[revision],
        'CAMPAIGN_GATE_STOCK_BYTES_MISSING')
    local original,effective=entry.original,entry.effective
    assert(type(original)=='string' and #original>=0x9C and original:sub(1,8)=='STORV1.0' and
        type(effective)=='string','CAMPAIGN_GATE_STOCK_BYTES_INVALID')
    local function u16(bytes,offset) local a,b=bytes:byte(offset+1,offset+2);return a+b*256 end
    local function u32(bytes,offset) return u16(bytes,offset)+u16(bytes,offset+2)*65536 end
    local function word(value) return string.char(value%256,math.floor(value/256)%256,
        math.floor(value/65536)%256,math.floor(value/16777216)%256) end
    local function put(bytes,offset,value) return bytes:sub(1,offset)..value..bytes:sub(offset+#value+1) end
    local offset,count=u32(original,0x34),u32(original,0x38)
    assert((count==0 or offset>=0x9C) and offset+count*28<=#original,'CAMPAIGN_GATE_STOCK_LAYOUT_INVALID')
    local first,anchor,records=nil,nil,{}
    for i=0,count-1 do
        local record=original:sub(offset+i*28+1,offset+(i+1)*28)
        local name=record:sub(1,8):match('^[^%z]*'):lower()
        records[#records+1]=record
        if not first and name=='scrl8x' then first=i end
        if not anchor and (name=='scrlal' or name=='scrl7q' or name=='scrl7p') then anchor=i end
    end
    local index=first or anchor or count
    local expected,raw
    if first then
        raw=records[first+1]
        local source=spec.original_record
        assert(spec.original_source_id=='sto:udduer01/item/'..first and source.record_index==first and
            source.byte_offset==offset+first*28 and source.quantity==u32(raw,20) and
            source.infinite_supply_flag==u32(raw,24) and same_payload(source,spec.payload),
            'CAMPAIGN_GATE_STOCK_ORIGINAL_MISMATCH')
        raw=put(raw,20,word(math.max(3,u32(raw,20))))
        expected=put(original,offset+first*28,raw)
    else
        assert(spec.original_source_id=='' and next(spec.original_record)==nil,
            'CAMPAIGN_GATE_STOCK_ORIGINAL_MISMATCH')
        raw='scrl8x\0\0'..string.rep('\0',8)..word(1)..word(3)..word(0)
        table.insert(records,index+1,raw)
        expected=put(original,0x34,word(#original)..word(count+1))..table.concat(records)
    end
    assert(effective==expected and spec.record_index==index and spec.quantity==u32(raw,20) and
        spec.payload.resref=='scrl8x' and spec.payload.expiration==u16(raw,8) and
        spec.payload.charges[1]==u16(raw,10) and spec.payload.charges[2]==u16(raw,12) and
        spec.payload.charges[3]==u16(raw,14) and spec.payload.flags==u32(raw,16),
        'CAMPAIGN_GATE_STOCK_EFFECTIVE_MISMATCH')
    local buffer=api.EEex_Malloc(MEMORY_SIZE)
    assert(buffer and buffer~=0,'CAMPAIGN_GATE_ALLOC_FAILED')
    for at=0,MEMORY_SIZE-1,8 do api.EEex_Write64(buffer+at,0) end
    local policy=compile(revision,'more_spell_shield')
    policy.store={expected=0,removed=0,length=#original,items=offset,count=count,
        index=index,ref='udduer01',context=CONTEXT,payload=raw}
    local assembly=build(buffer,targets,policy)
    local member=setmetatable({api=api,buffer=buffer,revision=revision,index='more_spell_shield',
        resource_hash=spec.resource_sha256,binding={id='@spell-shield',kind='store',store='udduer01',
        record=index,quantity=spec.quantity,payload=spec.payload},policy=policy,detours={},installed=false,
        stock_option=true,permanent=true},Runtime)
    return member,{enter=assembly.enter,capture=assembly.capture,saved_store=assembly.saved_store},
        {kind='sto',ref='udduer01',original=original,effective=effective,members={},option_member=member,
         insertion=first==nil and index or nil}
end

-- Item Randomiser's Mode 2 item rows 12/32/39/84 and duplicates/bg2.2da rows 26/20/21.
-- AMCLER01's inventory route is also removed in weidu_action.tpa's amcler.fl
-- handler. These physical copies belong to the selected dialogue source;
-- selecting the matching reward destination alone must not remove them.
local auxiliary_sources={
    ['original-source:bg2:mode2:item:12']={cre='scyarryl',item='blun20',record=0,selected='FLPR_QM2_12'},
    ['original-source:bg2:mode2:item:32']={cre='trgeni01',item='sw1h23',record=0,selected='FLPR_QM2_32',replacement='blun05'},
    ['original-source:bg2:mode2:item:39']={cre='loutcapt',item='dagg11',record=6,selected='FLPR_QM2_39'},
    ['original-source:bg2:mode2:item:84']={cre='amcler01',item='ring43',record=0,selected='FLPR_QM2_84'},
}
local function auxiliary_binding(bundle,id,ordinal)
    local m,f=bundle.manifest,bundle.fixture
    local endpoint=f.sources and f.sources[id]
    local auxiliary=endpoint and endpoint.auxiliary_creatures and endpoint.auxiliary_creatures[ordinal]
    local index,source
    for i,row in ipairs(m.sources) do if row.id==id then index,source=i,row end end
    local approved=auxiliary_sources[id]
    assert(approved and index and source.quantity==1 and ordinal==1 and
        endpoint.kind=='guarded-script' and auxiliary and auxiliary.resource=='cre:'..approved.cre and
        auxiliary.selected_global==approved.selected and endpoint.selected_global==auxiliary.selected_global and
        hash(auxiliary.resource_sha256) and f.resource_hashes[approved.cre:upper()..'.CRE']==auxiliary.resource_sha256 and
        payload(auxiliary.payload) and auxiliary.payload.resref==approved.item and auxiliary.payload.flags==0 and
        auxiliary.payload.charges[1]==0 and auxiliary.payload.charges[2]==0 and auxiliary.payload.charges[3]==0 and
        type(auxiliary.record_indices)=='table' and #auxiliary.record_indices==1 and
        auxiliary.record_indices[1]==approved.record,
        'CAMPAIGN_GATE_AUXILIARY_INVALID')
    -- Item Randomiser's duplicates/bg2.2da:20 leaves BLUN05 in TRGENI01's original weapon slot.
    -- The other reviewed auxiliary records are removed without substitutes.
    assert(auxiliary.replacement==approved.replacement and (not approved.replacement or
        hash(f.resource_hashes[approved.replacement:upper()..'.ITM'])),
        'CAMPAIGN_GATE_AUXILIARY_REPLACEMENT_INVALID')
    local seen,records={},{}
    for i,record in ipairs(auxiliary.record_indices) do
        assert(integer(record,0,65534) and not seen[record],'CAMPAIGN_GATE_AUXILIARY_RECORD_INVALID')
        seen[record],records[i]=true,record
    end
    return m.revision,index,auxiliary.resource_sha256,{id=id,kind='template',
        creature_resource=auxiliary.resource,record=records[1],records=records,
        payload=auxiliary.payload,selected_global=auxiliary.selected_global,ordinal=ordinal,
        replacement=approved.replacement,replacement_hash=approved.replacement and
            f.resource_hashes[approved.replacement:upper()..'.ITM']}
end

local function prepare_auxiliary(api,bundle,id,ordinal,build,compile,pristine,targets)
    local revision,index,resource_hash,binding=auxiliary_binding(bundle,id,ordinal)
    local entries=pristine.auxiliary and pristine.auxiliary[revision] and pristine.auxiliary[revision][id]
    local original=entries and entries[binding.creature_resource]
    assert(type(original)=='string' and #original>=0x2D4 and original:sub(1,8)=='CRE V1.0',
        'CAMPAIGN_GATE_AUXILIARY_BYTES_MISSING')
    local function u16(offset) local a,b=original:byte(offset+1,offset+2);return a+b*256 end
    local function u32(offset) return u16(offset)+u16(offset+2)*65536 end
    local offset,count=u32(0x2BC),u32(0x2C0)
    assert(offset>=0x2D4 and offset+count*20<=#original,'CAMPAIGN_GATE_AUXILIARY_LAYOUT_INVALID')
    for _,record in ipairs(binding.records) do
        local at=offset+record*20
        assert(record<count and original:sub(at+1,at+8):match('^[^%z]*'):lower()==binding.payload.resref and
            u16(at+8)==binding.payload.expiration and u16(at+10)==binding.payload.charges[1] and
            u16(at+12)==binding.payload.charges[2] and u16(at+14)==binding.payload.charges[3] and
            u32(at+16)==binding.payload.flags,'CAMPAIGN_GATE_AUXILIARY_BINDING_MISMATCH')
    end
    local buffer=api.EEex_Malloc(MEMORY_SIZE)
    assert(buffer and buffer~=0,'CAMPAIGN_GATE_ALLOC_FAILED')
    for at=0,MEMORY_SIZE-1,8 do api.EEex_Write64(buffer+at,0) end
    local policy=compile(revision,index,true,binding.selected_global)
    local assembly=build(buffer,targets,policy)
    local member=setmetatable({api=api,buffer=buffer,revision=revision,index=index,
        resource_hash=resource_hash,binding=binding,policy=policy,detours={},installed=false,
        auxiliary=true,permanent=true,inventory_original=original},Runtime)
    return member,{enter=assembly.enter,capture=assembly.capture}
end

function Gate.install(api,bundle,build,compile,pristine,Script,Inventory,Cur)
    local bundles=bundle.manifest and {bundle} or bundle
    assert(type(bundles)=='table' and #bundles>0, 'CAMPAIGN_GATE_CONFIG_INVALID')
    assert(api.EEex_Active and not api.FLPR_SOURCE_GATE and not api.FLPR_AREA_TRACE and
        not api.FLPR_CAMPAIGN_GATE, 'CAMPAIGN_GATE_INSTALL_CONTEXT_INVALID')
    assert(type(api.EEex_GameState_AddDestroyedListener)=='function' and
        type(api.EEex_GameState_GetGlobalInt)=='function' and type(api.EEex_Write8)=='function',
        'CAMPAIGN_GATE_LIFECYCLE_MISSING')
    local selected_sites={}
    local physical,creatures,stores,templates,cursed=false,false,false,false,false
    for _,selected in ipairs(bundles) do
        if selected.manifest.spell_shield then physical=true;stores=true end
        if selected.manifest.cursed_disguises then cursed=true end
        for _,endpoint in pairs(selected.fixture.sources) do
            if endpoint.kind~='guarded-script' then physical=true end
            if endpoint.kind=='guarded-creature' then creatures=true end
            if endpoint.kind=='guarded-store' then stores=true end
            if endpoint.kind=='guarded-template' then templates=true end
            if endpoint.auxiliary_creatures then physical=true;templates=true end
        end
    end
    if physical then for _,site in ipairs(sites) do selected_sites[#selected_sites+1]=site end end
    if cursed and not physical then
        selected_sites[#selected_sites+1]=sites[1];selected_sites[#selected_sites+1]=sites[2]
    end
    if creatures then
        assert(type(api.EEex_HookBeforeCall)=='function','CAMPAIGN_GATE_CREATURE_HOOK_MISSING')
        for _,site in ipairs(actor_sites) do selected_sites[#selected_sites+1]=site end
    end
    if stores then for _,site in ipairs(store_sites) do selected_sites[#selected_sites+1]=site end end
    if templates then selected_sites[#selected_sites+1]=template_site end
    -- Every location is found and checked before the first write.
    local found={}
    for _,site in ipairs(selected_sites) do found[site.key]=Site.locate(api,site.key,'CAMPAIGN_GATE') end
    Site.check_layout(api,'gate','CAMPAIGN_GATE')
    local function calls(key,target_key)
        local target=found[target_key] or Site.locate(api,target_key,'CAMPAIGN_GATE')
        if Site.call_target(api,found[key])~=target then
            error('CAMPAIGN_GATE_CODE_MISMATCH:'..key..':call-target',0)
        end
    end
    -- Both actor hooks hand the CRE bytes to the creature constructor, and the
    -- item hook identifies the area loader's container call by its return.
    if creatures then for _,site in ipairs(actor_sites) do calls(site.key,'gate_template') end end
    local targets
    if physical then
        calls('gate_container_call','gate_container_entry')
        targets={item_resume=found.gate_item+ITEM_RESUME,container_return=found.gate_container_call+5,
            container_skip=found.gate_container_skip}
    end
    local members,assemblies,cache,by_revision,inventory_groups,option_members,auxiliary_members={},{},{},{},{},{},{}
    local cursed_runtime
    if cursed then
        assert(Cur,'CAMPAIGN_GATE_CURSED_ADAPTER_MISSING')
        cursed_runtime=Cur.new(api,bundles,pristine.cursed,compile)
        for _,assembly in ipairs(cursed_runtime.assemblies) do assemblies[#assemblies+1]=assembly end
    end
    local count=0
    for key in pairs(bundles) do assert(integer(key,1,#bundles), 'CAMPAIGN_GATE_CONFIG_INVALID'); count=count+1 end
    assert(count==#bundles, 'CAMPAIGN_GATE_CONFIG_INVALID')
    for _,selected in ipairs(bundles) do
        local revision=selected.manifest.revision
        assert(not by_revision[revision], 'CAMPAIGN_GATE_REVISION_DUPLICATED')
        local current={}; by_revision[revision]=current
        auxiliary_members[revision]={}
        if selected.manifest.spell_shield then
            assert(Inventory,'CAMPAIGN_GATE_INVENTORY_BUILDER_MISSING')
            local member,assembly,group=prepare_stock_option(api,selected,build,compile,pristine,targets)
            option_members[revision]=member
            assemblies[#assemblies+1]=assembly
            inventory_groups[revision..'/sto:udduer01']=group
        end
        for _,id in ipairs(Gate.source_ids(selected.fixture)) do
            local member,assembly
            if selected.fixture.sources[id].kind=='guarded-script' then
                assert(Script,'CAMPAIGN_SCRIPT_ADAPTER_MISSING')
                member=Script.new(api,selected,id,pristine.scripts[revision][id])
            else
                member,assembly=prepare(api,selected,build,compile,pristine,id,targets,cache)
                local endpoint=selected.fixture.sources[id]
                if endpoint.kind=='guarded-template' or endpoint.multiple_stock or
                    (selected.manifest.spell_shield and endpoint.kind=='guarded-store' and endpoint.store=='udduer01') then
                    assert(Inventory,'CAMPAIGN_GATE_INVENTORY_BUILDER_MISSING')
                    local kind=endpoint.kind=='guarded-template' and 'cre' or 'sto'
                    local resource=kind=='cre' and endpoint.creature_resource or 'sto:'..endpoint.store
                    local key=revision..'/'..resource
                    local group=inventory_groups[key]
                    if not group then
                        local original=kind=='cre' and member.inventory_original or pristine.stores[revision][id].original
                        group={kind=kind,ref=endpoint.store,original=original,members={}}
                        inventory_groups[key]=group
                    end
                    group.members[#group.members+1]=member
                    member.permanent=true
                    assembly={enter=assembly.enter,capture=assembly.capture,saved_store=assembly.saved_store}
                end
                assemblies[#assemblies+1]=assembly
            end
            members[#members+1]=member
            current[#current+1]=member
            for ordinal in ipairs(selected.fixture.sources[id].auxiliary_creatures or {}) do
                assert(Inventory,'CAMPAIGN_GATE_INVENTORY_BUILDER_MISSING')
                local auxiliary,auxiliary_assembly=prepare_auxiliary(api,selected,id,ordinal,build,compile,pristine,targets)
                auxiliary_members[revision][#auxiliary_members[revision]+1]=auxiliary
                assemblies[#assemblies+1]=auxiliary_assembly
                local key=revision..'/'..auxiliary.binding.creature_resource
                local group=inventory_groups[key]
                if not group then
                    group={kind='cre',original=auxiliary.inventory_original,members={}}
                    inventory_groups[key]=group
                else assert(group.original==auxiliary.inventory_original,'CAMPAIGN_GATE_AUXILIARY_RESOURCE_MISMATCH') end
                group.members[#group.members+1]=auxiliary
            end
        end
    end
    for _,group in pairs(inventory_groups) do
        local original=group.original
        local effective=group.effective or original
        local address=api.EEex_Malloc(#original+(group.option_member and 2 or 1)*#effective)
        assert(address and address~=0,'CAMPAIGN_GATE_ALLOC_FAILED')
        for n=1,#original do
            local byte=original:byte(n)
            api.EEex_Write8(address+n-1,byte>=128 and byte-256 or byte)
        end
        if group.option_member then
            for n=1,#effective do
                local byte=effective:byte(n)
                api.EEex_Write8(address+#original+n-1,byte>=128 and byte-256 or byte)
            end
            group.spell_shield={buffer=group.option_member.buffer,context=CONTEXT,
                effective=address+#original,length=#effective}
        end
        local function u32(offset)
            local a,b,c,d=effective:byte(offset+1,offset+4)
            return a+b*256+c*65536+d*16777216
        end
        group.expected,group.output,group.length=address,address+#original+
            (group.option_member and #effective or 0),#original
        group.item_offset=u32(group.kind=='cre' and 0x2BC or 0x34)
        group.slot_offset=group.kind=='cre' and u32(0x2B8) or nil
        local count=u32(group.kind=='cre' and 0x2C0 or 0x38)
        assert(group.item_offset+count*(group.kind=='cre' and 20 or 28)<=#effective,
            'CAMPAIGN_GATE_INVENTORY_LAYOUT_INVALID')
        if group.slot_offset then assert(group.slot_offset+80<=#original,'CAMPAIGN_GATE_INVENTORY_LAYOUT_INVALID') end
        group.edits={}
        for _,member in ipairs(group.members) do
            for _,record in ipairs(member.binding.records or {member.binding.record}) do
                if group.insertion and not member.binding.stock_option and record>=group.insertion then record=record+1 end
                assert(record<count,'CAMPAIGN_GATE_INVENTORY_INDEX_INVALID')
                group.edits[#group.edits+1]={index=record,buffer=member.buffer,
                    replacement=member.binding.replacement,context=CONTEXT}
            end
        end
        table.sort(group.edits,function(a,b) return a.index>b.index end)
        assemblies[#assemblies+1]={[group.kind=='cre' and 'template' or 'store']=Inventory(group)}
    end
    local has_auxiliary=false
    for _,entries in pairs(auxiliary_members) do if #entries>0 then has_auxiliary=true end end
    local self=#members==1 and next(option_members)==nil and not cursed_runtime and not has_auxiliary and members[1] or
        setmetatable({members=members,by_revision=by_revision,option_members=option_members,
            auxiliary_members=auxiliary_members,cursed=cursed_runtime,installed=false},Group)
    local assembly,parts=Gate.compose(assemblies,true)
    local detours={}
    for _,member in ipairs(members) do member.detours=detours end
    for _,member in pairs(option_members) do member.detours=detours end
    for _,entries in pairs(auxiliary_members) do for _,member in ipairs(entries) do member.detours=detours end end
    api.FLPR_CAMPAIGN_GATE=self -- partial installation is never retried in-process
    api.EEex_DisableCodeProtection()
    local ok,failure=pcall(function()
        for _,site in ipairs(selected_sites) do
            if site.code and assembly[site.code] and assembly[site.code]~='' then
                local address=found[site.key]
                local chained=#assembly[site.code]>16384
                if chained then Gate.install_chain(api,site,address,parts[site.code]) end
                if site.call then
                    if not chained then api.EEex_HookBeforeCall(address,{assembly[site.code]}) end
                    if api.EEex_HookIntegrityWatchdog_Load then
                        api.EEex_HookIntegrityWatchdog_IgnoreRegisters(address,{
                            api.EEex_HookIntegrityWatchdogRegister.RAX,api.EEex_HookIntegrityWatchdogRegister.RDX})
                    end
                else
                    if not chained then api.EEex_HookBeforeRestore(address,0,site.size,site.size,{assembly[site.code]}) end
                    if (site.code=='store' or site.code=='template' or site.code=='item') and api.EEex_HookIntegrityWatchdog_Load then
                        api.EEex_HookIntegrityWatchdog_IgnoreRegisters(address,{
                            site.code=='store' and api.EEex_HookIntegrityWatchdogRegister.RDI or
                                (site.code=='item' and api.EEex_HookIntegrityWatchdogRegister.R13 or
                                 api.EEex_HookIntegrityWatchdogRegister.RDX)})
                    end
                end
                assert(api.EEex_ReadU8(address)==0xE9,'CAMPAIGN_GATE_DETOUR_NOT_WRITTEN')
                local target=address+5+api.EEex_Read32(address+1)
                assert(target>0 and (target<address or target>=address+#Site.SPECS[site.key].bytes/2),
                    'CAMPAIGN_GATE_DETOUR_INVALID')
                detours[#detours+1]={address=address,target=target}
            end
        end
        if cursed_runtime then cursed_runtime:install(detours) end
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
    for _,member in pairs(option_members) do member.installed=true;api.EEex_Write64(member.buffer,1) end
    for _,entries in pairs(auxiliary_members) do for _,member in ipairs(entries) do
        member.installed=true;api.EEex_Write64(member.buffer,1)
    end end
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
-- What a proof reads of an event row. The host proves leases on every tick in
-- turn; reading each row's names and payload bytes one by one cost the most.
function Runtime:_proof_row(index)
    local row=self.buffer+HEADER+index*STRIDE
    return {kind=self.api.EEex_ReadU64(row),generation=self.api.EEex_ReadU64(row+8),
        decision=self.api.EEex_ReadU64(row+24)}
end
function Runtime:inspect(proof)
    local count,generation,destroys,active=self:_q(8),self:_q(40),self:_q(104),self:_q(24)
    local result={schema='flpr-campaign-gate-trace-v1',revision=self.revision,
        installed=self.installed,armed=self:_q(0)==1,overflow=self:_q(16)~=0 or count>CAPACITY,
        count=count,generation=generation,destroys=destroys,mode=self:_q(32),status=self:_q(72),
        mask=self:_q(80),candidate=self:_q(88),skips=self:_q(96),load_skips=self:_q(120),events={}}
    local published=true
    for index=0,math.min(count,CAPACITY)-1 do
        local row=proof and self:_proof_row(index) or self:_row(index)
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
    if self.auxiliary then
        local revision,index,resource_hash,binding=auxiliary_binding(bundle,self.binding.id,self.binding.ordinal)
        local b=self.binding
        assert(revision==self.revision and index==self.index and resource_hash==self.resource_hash and
            binding.creature_resource==b.creature_resource and binding.selected_global==b.selected_global and
            binding.replacement==b.replacement and binding.replacement_hash==b.replacement_hash and
            same_payload(binding.payload,b.payload) and #binding.records==#b.records,
            'CAMPAIGN_GATE_AUXILIARY_REVISION_MISMATCH')
        for i,record in ipairs(binding.records) do assert(record==b.records[i],'CAMPAIGN_GATE_AUXILIARY_REVISION_MISMATCH') end
        return
    end
    if self.stock_option then
        local spec=bundle.manifest.spell_shield
        assert(bundle.manifest.revision==self.revision and spec and
            spec.resource_sha256==self.resource_hash and spec.record_index==self.binding.record and
            spec.quantity==self.binding.quantity and same_payload(spec.payload,self.binding.payload),
            'CAMPAIGN_GATE_STOCK_REVISION_MISMATCH')
        return
    end
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
    if not self.stock_option and controller.source_is_kept and controller:source_is_kept(self.index) then return true end
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
    self.api.EEex_Write64(row+48,self.stock_option and 0 or self.index); self.api.EEex_Write64(row+112,self.policy.mask)
    self.api.EEex_Write64(row,4)
    self:_write(8,snapshot.count+1)
    self:_write(40,generation); self:_write(112,0); self:_write(120,0)
    self.enrolled={generation=generation,destroys=snapshot.destroys,session=controller.session}
    self:_write(32,1) -- publish protection last
    return true
end

function Runtime:_proof()
    local snapshot=self:inspect(true)
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
    -- A CRE template can be spawned again; its guard remains campaign-wide.
    -- Do not claim a single permanent world removal from a constructor handoff.
    if self.gate.permanent then return nil,'SOURCE_NOT_LOADED' end
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
    local option=self.option_members and self.option_members[bundle.manifest.revision]
    if option then option:prepare_new(bundle,controller) end
    for _,member in ipairs((self.auxiliary_members or {})[bundle.manifest.revision] or {}) do
        member:prepare_new(bundle,controller)
    end
    if self.cursed then self.cursed:prepare_new(bundle,controller) end
    self.active_revision=bundle.manifest.revision
    self:check(bundle)
    return true
end
function Group:reset()
    self.active_revision=nil
    for _,member in ipairs(self.members) do member:reset() end
    for _,member in pairs(self.option_members or {}) do member:reset() end
    for _,entries in pairs(self.auxiliary_members or {}) do for _,member in ipairs(entries) do member:reset() end end
    if self.cursed then self.cursed:reset() end
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
    if self.option_members and next(self.option_members) then
        result.stock_options={}
        for key,member in pairs(self.option_members) do
            if not revision or key==revision then result.stock_options[key]=member:inspect() end
        end
    end
    if self.cursed then result.cursed={revision_index=self.cursed.api.EEex_ReadU64(self.cursed.state),
        seed=self.cursed.api.EEex_ReadU64(self.cursed.state+8),error=self.cursed.api.EEex_ReadU64(self.cursed.state+16)} end
    result.auxiliary={}
    for key,entries in pairs(self.auxiliary_members or {}) do if not revision or key==revision then
        for _,member in ipairs(entries) do
            result.auxiliary[key..'/'..member.binding.id..'/'..member.binding.creature_resource]=member:inspect()
        end
    end end
    return result
end
function Group:check(bundle)
    if self.cursed then self.cursed:check(bundle) end
    local revision=bundle and bundle.manifest.revision or self.active_revision
    for key,entries in pairs(self.auxiliary_members or {}) do
        for _,member in ipairs(entries) do
            if key==revision and member.api.EEex_GameState_GetGlobalInt(member.binding.selected_global)==1 then
                if bundle then member:_bundle(bundle) end
                local proof,reason=member:_proof()
                assert(proof,reason or 'CAMPAIGN_GATE_AUXILIARY_INACTIVE')
            else assert(member:_q(32)==0,'CAMPAIGN_GATE_AUXILIARY_UNSELECTED_ACTIVE') end
        end
    end
    return true
end
function Group:adapter(bundle,native)
    self:check(bundle)
    local adapters={}
    for _,member in ipairs(self:_members(bundle)) do adapters[member.index]=member:adapter(bundle,native) end
    self.active_revision=bundle.manifest.revision
    local group,self_gate={},self
    function group:check() return self_gate:check(bundle) end
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
