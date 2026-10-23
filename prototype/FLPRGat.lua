-- Campaign source gate for configured ARE container, creature, store and
-- template sources. Hook locations and their expected bytes: FLPRSite.lua.
-- build = tools/probes/FLPRGateAsm.lua; compile = FLPRCampaignPolicy.lua.
-- The hooks are placed once, at game launch, each with an empty chain. A
-- campaign revision's source checks are built only when a game needs them:
-- New Game builds the revision it starts, and loading a saved game builds the
-- revision its GLOBALs name (CInfGame::Unmarshal calls back into Lua before
-- any source check reads the GAM). Every built revision's checks stay chained.
local Site = ...
assert(type(Site)=='table' and type(Site.locate)=='function', 'CAMPAIGN_GATE_SITES_MISSING')
local Gate = {}
local Runtime = {}; Runtime.__index = Runtime
local Adapter = {}; Adapter.__index = Adapter
local Group = {}; Group.__index = Group
local CAPACITY, HEADER, STRIDE = 2048, 128, 128
local CONTEXT=HEADER+(CAPACITY+1)*STRIDE
local MEMORY_SIZE=CONTEXT+32+64*64
-- A member's policy table (FLPRCampaignPolicy) follows its native state; the
-- pristine copies a member owns follow the table.
local POLICY_TABLE, TABLE_SIZE = MEMORY_SIZE, 1024
local BODY=MEMORY_SIZE+TABLE_SIZE
-- STO resrefs restored from a saved game since the last game was destroyed.
local RECORDS=4096
local function number(value) return string.format('%.0f',value) end
-- EEex_Memset and FLPR.Memory (M_FLPR.lua, LuaJIT ffi) move whole blocks;
-- without them every byte is written and checked through EEex.
local function zero(api,address,size)
    if type(api.EEex_Memset)=='function' then api.EEex_Memset(address,0,size); return end
    for offset=0,size-1,8 do api.EEex_Write64(address+offset,0) end
end
local function bulk(api)
    local root=api.FLPR
    local memory=type(root)=='table' and root.Memory
    if type(memory)=='table' and type(memory.write)=='function' and type(memory.read)=='function' then return memory end
end
local function write_bytes(api,address,value)
    local memory=bulk(api)
    if memory then memory.write(address,value); return address end
    for offset=1,#value do
        local byte=value:byte(offset)
        api.EEex_Write8(address+offset-1,byte>=128 and byte-256 or byte) -- Write8 takes signed bytes.
    end
    return address
end
local function same_bytes(api,address,value)
    local memory=bulk(api)
    if memory then return memory.read(address,#value)==value end
    for offset=1,#value do
        if api.EEex_ReadU8(address+offset-1)~=value:byte(offset) then return false end
    end
    return true
end
-- Copies the policy's field table behind the member's state and names it in
-- the policy's scan code.
local function bind_policy(api,buffer,policy)
    assert(type(policy)=='table' and type(policy.table)=='string' and #policy.table<=TABLE_SIZE and
        type(policy.scan)=='string','CAMPAIGN_GATE_POLICY_INVALID')
    local address=write_bytes(api,buffer+POLICY_TABLE,policy.table)
    assert(same_bytes(api,address,policy.table),'CAMPAIGN_GATE_POLICY_COPY_FAILED')
    policy.table_address=address
    policy.scan=policy.scan:gsub('@POLICY_TABLE@',number(address))
    return policy
end
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
-- The saved-store site only records which STO files a saved game restores
-- (dimmUncompressResources runs before CInfGame::Unmarshal, so before a
-- loaded game's revision is known); Group:_on_game applies the record.
local store_sites = {
    {key='gate_store',size=8,code='store'},
    {key='gate_saved_store',size=7,recorder=true},
    {key='gate_store_done',size=5,code='store_done'},
}
local template_site = {key='gate_template',size=5,code='template'}
-- Every location the gate can use, in hook order.
local all_sites = {}
for _,list in ipairs({sites,actor_sites,store_sites,{template_site}}) do
    for _,site in ipairs(list) do all_sites[#all_sites+1]=site end
end
-- The item hook resumes at the instruction after the item address computation
-- (the verified bytes of gate_item cover it).
local ITEM_RESUME = 15
local function hash(value)
    return type(value)=='string' and #value==64 and not value:find('[^0-9a-f]')
end
local function integer(value,lo,hi)
    return type(value)=='number' and value==math.floor(value) and value>=lo and value<=hi
end
-- A source keeps its flags on the reward, except undroppable (8). With copy,
-- a creature's copy of a reward, which is only removed or renamed: whatever
-- flags or expiry another mod gave it stay as they are.
local function payload(value,copy)
    if type(value)~='table' or type(value.resref)~='string' or #value.resref<1 or #value.resref>8 or
        value.resref:find('[^a-z0-9#_%-]') or not integer(value.expiration,0,copy and 65535 or 0) or
        not integer(value.flags,0,4294967295) or not copy and value.flags%16>=8 or
        type(value.charges)~='table' then return false end
    for i=1,3 do if not integer(value.charges[i],0,65535) then return false end end
    return true
end
local function same_payload(a,b,copy)
    return payload(a,copy) and payload(b,copy) and a.resref==b.resref and a.expiration==b.expiration and
        a.flags==b.flags and a.charges[1]==b.charges[1] and a.charges[2]==b.charges[2] and
        a.charges[3]==b.charges[3]
end
function Gate.source_ids(fixture)
    local config=type(fixture)=='table' and fixture.source_gate
    assert(type(config)=='table' and config.schema=='flpr-unvisited-fixture-gate-v1' and
        (config.source_id==nil)~=(config.source_ids==nil), 'CAMPAIGN_GATE_CONFIG_INVALID')
    local ids=config.source_ids or {config.source_id}
    assert(type(ids)=='table' and (#ids>0 or type(fixture.spell_shield)=='table' or
        type(fixture.cursed_disguises)=='table' or type(fixture.duplicates)=='table'), 'CAMPAIGN_GATE_CONFIG_INVALID')
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
    local buffer=api.EEex_Malloc(BODY+(cached and 0 or #bytes)+2*#original)
    assert(buffer and buffer~=0,'CAMPAIGN_GATE_ALLOC_FAILED')
    zero(api,buffer,MEMORY_SIZE)
    local cursor=buffer+BODY
    local function copy(value)
        local address=write_bytes(api,cursor,value)
        cursor=cursor+#value
        return address
    end
    local expected=cached or copy(bytes); cache[binding.area_hash]=expected
    local policy=bind_policy(api,buffer,compile(revision,index))
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
        local buffer=api.EEex_Malloc(BODY)
        assert(buffer and buffer~=0,'CAMPAIGN_GATE_ALLOC_FAILED')
        zero(api,buffer,MEMORY_SIZE)
        local policy=bind_policy(api,buffer,compile(revision,index))
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
        local buffer=api.EEex_Malloc(BODY+2*#original)
        assert(buffer and buffer~=0,'CAMPAIGN_GATE_ALLOC_FAILED')
        zero(api,buffer,MEMORY_SIZE)
        local policy=bind_policy(api,buffer,compile(revision,index))
        policy.store={expected=write_bytes(api,buffer+BODY,original),removed=write_bytes(api,buffer+BODY+#original,removed),
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
    local cached=cache[resource_hash]
    local buffer=api.EEex_Malloc(BODY+(cached and 0 or #pristine.bytes)+(binding.replacement and 20 or 0))
    assert(buffer and buffer~=0,'CAMPAIGN_GATE_ALLOC_FAILED')
    zero(api,buffer,MEMORY_SIZE)
    local policy=bind_policy(api,buffer,compile(revision,index))
    local expected_pointer=cached or buffer+BODY
    if not cached then
        -- WriteLString truncates binary data at NUL.
        write_bytes(api,expected_pointer,pristine.bytes)
        assert(same_bytes(api,expected_pointer,pristine.bytes),'CAMPAIGN_GATE_PRISTINE_COPY_FAILED')
        cache[resource_hash]=expected_pointer
    end
    policy.provenance={expected=expected_pointer,length=#pristine.bytes,
        container_offset=owner,item_offset=items}
    policy.source={area=binding.area,name=name,payload=raw,index=binding.record,
        inventory=bytes:sub(owner+0x41,owner+0x48),position=bytes:sub(owner+0x21,owner+0x28)}
    if binding.replacement then
        local address=buffer+BODY+(cached and 0 or #pristine.bytes)
        write_bytes(api,address,binding.replacement..string.rep('\0',8-#binding.replacement)..raw:sub(9))
        policy.source.replacement_pointer=address
    end
    local self=setmetatable({api=api,buffer=buffer,revision=revision,index=index,
        resource_hash=resource_hash,binding=binding,expected=expected,policy=policy,detours={},installed=false},Runtime)
    return self,build(buffer,targets,policy)
end

-- Each block preserves registers and flags. A matched item exits the shared
-- hook immediately; other blocks see the original constructor inputs.
function Gate.compose(assemblies,with_parts)
    local parts={}
    for index,assembly in ipairs(assemblies) do
        for name,code in pairs(assembly) do
            if code~='' then
                local list=parts[name]
                if not list then list={}; parts[name]=list end
                list[#list+1]=code:gsub('gate_', 'source'..index..'_')..'\n'
            end
        end
    end
    local joined={}
    for name,list in pairs(parts) do joined[name]=table.concat(list) end
    if with_parts then return joined,parts end
    return joined
end

-- Each hooked location jumps to a fixed entry that reads its next code from a
-- slot: an empty chain at first, later the newest revision's source checks.
-- The jump saves RAX on the stack and every slot target begins by restoring
-- it, so the checks see the engine's registers, flags and stack unchanged.
local function jump_via(slot)
    return 'push rax\nmov rax, '..number(slot)..'\nmov rax, qword ptr ds:[rax]\njmp rax\n'
end

-- EEex's near-code allocator accepts one code page per JIT request. A full
-- preset exceeds that page when all source checks are emitted as one hook.
-- Keep whole source blocks together and jump between allocations: CALL would
-- shift the engine stack used by the constructor checks. The entry/exit below
-- follows EEex_HookBeforeRestore / EEex_HookBeforeCall, using EEex's allocator.
function Gate.hook(api,site,address,prologue)
    api.EEex_HookIntegrityWatchdog_IgnoreRegisters(address,{})
    api.EEex_HookIntegrityWatchdog_DefaultIgnoreStack(address)
    local slot=api.EEex_Malloc(8)
    assert(slot and slot~=0,'CAMPAIGN_GATE_ALLOC_FAILED')
    local hooked={site=site,address=address,slot=slot}
    local entry=api.EEex_RunWithAssemblyLabels({{'hook_address',address}},function()
        local tail={'#MANUAL_HOOK_EXIT(0)\n'}
        if site.call then
            tail[#tail+1]='call '..number(address+5+api.EEex_Read32(address+1))..'\n'
        else
            tail[#tail+1]=api.EEex_StoreBytesAssembly(address,site.size)
        end
        tail[#tail+1]='\njmp '..number(address+(site.call and 5 or site.size))..'\n'
        hooked.tail=api.EEex_JITNear(api.EEex_FlattenTable(tail))
        hooked.empty=api.EEex_JITNear({'pop rax\njmp '..number(hooked.tail)..'\n'})
        api.EEex_Write64(slot,hooked.empty)
        return api.EEex_JITNear(api.EEex_FlattenTable({api.EEex_HookIntegrityWatchdog_HookEnter,
            prologue or '','\n',jump_via(slot)}))
    end)
    hooked.entry,hooked.head=entry,hooked.tail
    api.EEex_JITAt(address,{'jmp short '..number(entry)..'\n'..
        string.rep('nop\n',(site.call and 5 or site.size)-5)})
    assert(api.EEex_ReadU8(address)==0xE9,'CAMPAIGN_GATE_DETOUR_NOT_WRITTEN')
    local target=address+5+api.EEex_Read32(address+1)
    assert(target>0 and (target<address or target>=address+#Site.SPECS[site.key].bytes/2),
        'CAMPAIGN_GATE_DETOUR_INVALID')
    hooked.detour={address=address,target=target}
    return hooked
end

-- JIT code for parts in front of the location's current chain. Nothing runs
-- it until Gate.publish points the slot at it.
function Gate.link(api,hooked,parts)
    local chunks,size={},0
    for _,part in ipairs(parts) do
        if #chunks==0 or size+#part>16384 then chunks[#chunks+1]={}; size=0 end
        local chunk=chunks[#chunks]
        chunk[#chunk+1]=part; size=size+#part
    end
    return api.EEex_RunWithAssemblyLabels({{'hook_address',hooked.address}},function()
        local next_address=hooked.head
        for index=#chunks,1,-1 do
            next_address=api.EEex_JITNear({table.concat(chunks[index]),'\njmp '..number(next_address)..'\n'})
        end
        return {first=next_address,entry=api.EEex_JITNear({'pop rax\njmp '..number(next_address)..'\n'})}
    end)
end

function Gate.publish(api,hooked,linked)
    api.EEex_Write64(hooked.slot,linked.entry)
    hooked.head=linked.first
end

-- One location with one chain (focused checks of the chain mechanics).
function Gate.install_chain(api,site,address,parts)
    local hooked=Gate.hook(api,site,address)
    Gate.publish(api,hooked,Gate.link(api,hooked,parts))
    return hooked
end

-- Calls the Lua dispatcher Group:_on_game with the GAM bytes and size at
-- CInfGame::Unmarshal entry, preserving every register and the flags.
local function dispatcher(api,name)
    return api.EEex_FlattenTable({
        {[[
            pushfq
            #STACK_MOD(8)
            #MAKE_SHADOW_SPACE(120)
            mov qword ptr ss:[rsp+#SHADOW_SPACE_BOTTOM(-8)], rax
            mov qword ptr ss:[rsp+#SHADOW_SPACE_BOTTOM(-16)], rcx
            mov qword ptr ss:[rsp+#SHADOW_SPACE_BOTTOM(-24)], rdx
            mov qword ptr ss:[rsp+#SHADOW_SPACE_BOTTOM(-32)], r8
            mov qword ptr ss:[rsp+#SHADOW_SPACE_BOTTOM(-40)], r9
            mov qword ptr ss:[rsp+#SHADOW_SPACE_BOTTOM(-48)], r10
            mov qword ptr ss:[rsp+#SHADOW_SPACE_BOTTOM(-56)], r11
        ]]},
        api.EEex_GenLuaCall(name,{args={
            function(offset) return {'mov qword ptr ss:[rsp+#$(1)], rdx #ENDL',{offset}} end,
            function(offset) return {'mov eax, r8d #ENDL mov qword ptr ss:[rsp+#$(1)], rax #ENDL',{offset}} end,
        }}),
        {[[
            call_error:
            mov r11, qword ptr ss:[rsp+#SHADOW_SPACE_BOTTOM(-56)]
            mov r10, qword ptr ss:[rsp+#SHADOW_SPACE_BOTTOM(-48)]
            mov r9, qword ptr ss:[rsp+#SHADOW_SPACE_BOTTOM(-40)]
            mov r8, qword ptr ss:[rsp+#SHADOW_SPACE_BOTTOM(-32)]
            mov rdx, qword ptr ss:[rsp+#SHADOW_SPACE_BOTTOM(-24)]
            mov rcx, qword ptr ss:[rsp+#SHADOW_SPACE_BOTTOM(-16)]
            mov rax, qword ptr ss:[rsp+#SHADOW_SPACE_BOTTOM(-8)]
            #DESTROY_SHADOW_SPACE
            popfq
            #STACK_MOD(-8)
        ]]},
    })
end

-- dimmUncompressResources: records each STO resref (raw) it restores. RBX is
-- the resource, R14D zero for a restore, [RBP-0x20] the resref.
local function recorder(address)
    return 'pushfq\npush rax\npush r10\npush r11\n'..
        'test r14d, r14d\njne gate_recorder_done\n'..
        'cmp word ptr ds:[rbx+0x10], 0x3F6\njne gate_recorder_done\n'..
        'mov r10, '..number(address)..'\nmov r11, qword ptr ds:[r10]\n'..
        'cmp r11, '..RECORDS..'\njae gate_recorder_full\n'..
        'mov rax, qword ptr ss:[rbp-0x20]\nmov qword ptr ds:[r10+r11*8+16], rax\n'..
        'inc qword ptr ds:[r10]\njmp gate_recorder_done\n'..
        'gate_recorder_full:\nmov qword ptr ds:[r10+8], 1\n'..
        'gate_recorder_done:\npop r11\npop r10\npop rax\npopfq\n'
end
Gate.recorder=recorder
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
    local buffer=api.EEex_Malloc(BODY)
    assert(buffer and buffer~=0,'CAMPAIGN_GATE_ALLOC_FAILED')
    zero(api,buffer,MEMORY_SIZE)
    local policy=bind_policy(api,buffer,compile(revision,'more_spell_shield'))
    policy.store={expected=0,removed=0,length=#original,items=offset,count=count,
        index=index,ref='udduer01',context=CONTEXT,payload=raw}
    local assembly=build(buffer,targets,policy)
    local member=setmetatable({api=api,buffer=buffer,revision=revision,index='more_spell_shield',
        resource_hash=spec.resource_sha256,binding={id='@spell-shield',kind='store',store='udduer01',
        record=index,quantity=spec.quantity,payload=spec.payload},policy=policy,detours={},installed=false,
        stock_option=true,permanent=true},Runtime)
    return member,{enter=assembly.enter,capture=assembly.capture},
        {kind='sto',ref='udduer01',original=original,effective=effective,members={},option_member=member,
         insertion=first==nil and index or nil}
end

-- Item Randomiser's Mode 2 item rows 12/31/32/39/84 and duplicates/bg2.2da rows 26/19/20/21;
-- BG1's rows 16 and 159 (duplicates/bg1.2da's Nemphre copy). EET keeps these BG1 names.
-- AMCLER01's inventory route is also removed in weidu_action.tpa's amcler.fl
-- handler. These physical copies belong to the selected dialogue source;
-- selecting the matching reward destination alone must not remove them.
-- Which records of the creature hold the item, and with which charges and
-- flags, differs between games: the export names them, and prepare_auxiliary
-- checks each against the original CRE.
local auxiliary_sources={
    ['original-source:bg2:mode2:item:12']={cre='scyarryl',item='blun20',selected='FLPR_QM2_12'},
    ['original-source:bg2:mode2:item:31']={cre='jugjer01',item='sw1h26',selected='FLPR_QM2_31'},
    ['original-source:bg2:mode2:item:32']={cre='trgeni01',item='sw1h23',selected='FLPR_QM2_32',replacement='blun05'},
    ['original-source:bg2:mode2:item:39']={cre='loutcapt',item='dagg11',selected='FLPR_QM2_39'},
    ['original-source:bg2:mode2:item:84']={cre='amcler01',item='ring43',selected='FLPR_QM2_84'},
    ['original-source:bg1:mode2:item:16']={cre='aldeth',item='sw1h03',selected='FLPR_QB1_16'},
    ['original-source:bg1:mode2:item:159']={cre='nemphr',item='scrl1q',selected='FLPR_QB1_159'},
}
local function auxiliary_binding(bundle,id,ordinal)
    local m,f=bundle.manifest,bundle.fixture
    local endpoint=f.sources and f.sources[id]
    local auxiliary=endpoint and endpoint.auxiliary_creatures and endpoint.auxiliary_creatures[ordinal]
    local index,source
    for i,row in ipairs(m.sources) do if row.id==id then index,source=i,row end end
    -- A preset's row of one of these routes names the route's row, of the same campaign.
    local route=id
    if endpoint and endpoint.route_source~=nil then
        local preset=id:match('^original%-source:(bg[12]):[a-z0-9%-]+:mode2:item:[0-9]+$')
        route=preset and type(endpoint.route_source)=='string' and
            endpoint.route_source:match('^original%-source:'..preset..':mode2:item:[0-9]+$') or ''
    end
    local approved=auxiliary_sources[route]
    assert(approved and index and source.quantity==1 and ordinal==1 and
        endpoint.kind=='guarded-script' and auxiliary and auxiliary.resource=='cre:'..approved.cre and
        auxiliary.selected_global==approved.selected and endpoint.selected_global==auxiliary.selected_global and
        hash(auxiliary.resource_sha256) and f.resource_hashes[approved.cre:upper()..'.CRE']==auxiliary.resource_sha256 and
        payload(auxiliary.payload,true) and auxiliary.payload.resref==approved.item and
        type(auxiliary.record_indices)=='table' and #auxiliary.record_indices>0,
        'CAMPAIGN_GATE_AUXILIARY_INVALID')
    -- Item Randomiser's duplicates/bg2.2da:20 leaves BLUN05 in TRGENI01's original weapon slot.
    -- The other auxiliary records are removed without substitutes.
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
    local buffer=api.EEex_Malloc(BODY)
    assert(buffer and buffer~=0,'CAMPAIGN_GATE_ALLOC_FAILED')
    zero(api,buffer,MEMORY_SIZE)
    local policy=bind_policy(api,buffer,compile(revision,index,true,binding.selected_global))
    local assembly=build(buffer,targets,policy)
    local member=setmetatable({api=api,buffer=buffer,revision=revision,index=index,
        resource_hash=resource_hash,binding=binding,policy=policy,detours={},installed=false,
        auxiliary=true,permanent=true,inventory_original=original},Runtime)
    return member,{enter=assembly.enter,capture=assembly.capture}
end

-- Item Randomiser's lists/duplicates/bg1.2da and bg2.2da by line: the second
-- copies of a reward that its installer removes when the reward's row moves
-- (tools/flir_config/duplicates.py). Only these rows are removed, each under
-- the names reviewed for it: as authored and, for a BG1 row, as EET names
-- them. Which records of the holder are the copies, and with which charges and
-- flags, differs between games: the export names them, and prepare_copy checks
-- each against the holder as the game has it.
local DUPLICATE_ROWS={
    ['bg1:duplicate:3']={item={'helm06'},resource={'sto:inn3351'}},
    ['bg1:duplicate:4']={item={'sw1h03'},resource={'are:ar1006','are:bg1006'}},
    ['bg1:duplicate:5']={item={'shld07'},resource={'cre:varci'}},
    ['bg1:duplicate:6']={item={'sper03'},resource={'cre:knightsk'},replacement={'sper01'}},
    ['bg1:duplicate:7']={item={'leat06'},resource={'sto:stocheap'}},
    ['bg1:duplicate:8']={item={'scrl5l'},resource={'sto:ulgoth'}},
    ['bg1:duplicate:9']={item={'scrl2d'},resource={'sto:ulgoth'}},
    ['bg1:duplicate:10']={item={'scrl2f'},resource={'sto:ulgoth'}},
    ['bg1:duplicate:11']={item={'scrl2h'},resource={'sto:ulgoth'}},
    ['bg1:duplicate:12']={item={'scrl1q'},resource={'cre:nemphr'}},
    ['bg2:duplicate:3']={item={'xbow06'},resource={'sto:bernard'}},
    ['bg2:duplicate:4']={item={'ax1h16'},resource={'cre:amcarras'}},
    ['bg2:duplicate:5']={item={'boot12'},resource={'cre:amcarras'}},
    ['bg2:duplicate:6']={item={'plat21'},resource={'cre:amcarras'}},
    ['bg2:duplicate:7']={item={'rods06'},resource={'sto:amsmug01'}},
    ['bg2:duplicate:8']={item={'helm07'},resource={'cre:fwjassy'}},
    ['bg2:duplicate:9']={item={'bow10'},resource={'sto:ribald3'}},
    ['bg2:duplicate:10']={item={'amul16'},resource={'cre:fwjassy'}},
    ['bg2:duplicate:11']={item={'sw1h54c'},resource={'cre:fwjassy'}},
    ['bg2:duplicate:12']={item={'sw1h53'},resource={'sto:bernard'}},
    ['bg2:duplicate:13']={item={'sw1h53'},resource={'sto:bernard2'}},
    ['bg2:duplicate:14']={item={'ring07'},resource={'cre:aran02'}},
    ['bg2:duplicate:15']={item={'ring07'},resource={'cre:rr#aran2'}},
    ['bg2:duplicate:16']={item={'amul21'},resource={'cre:aran02'}},
    ['bg2:duplicate:17']={item={'amul21'},resource={'cre:rr#aran2'}},
    ['bg2:duplicate:18']={item={'helm03'},resource={'cre:sctelwyn'}},
    ['bg2:duplicate:19']={item={'sw1h26'},resource={'cre:jugjer01'}},
    ['bg2:duplicate:20']={item={'sw1h23'},resource={'cre:trgeni01'},replacement={'blun05'}},
    ['bg2:duplicate:21']={item={'dagg11'},resource={'cre:loutcapt'}},
    ['bg2:duplicate:22']={item={'ring33'},resource={'cre:tolger'}},
    ['bg2:duplicate:23']={item={'sw2h17'},resource={'cre:abazigal'}},
    ['bg2:duplicate:24']={item={'ring35'},resource={'cre:arnman09'}},
    ['bg2:duplicate:25']={item={'sper08'},resource={'cre:sahpr2'},replacement={'sper02'}},
    ['bg2:duplicate:26']={item={'blun20'},resource={'cre:scyarryl'}},
    ['bg2:duplicate:27']={item={'ring26'},resource={'are:rr#010'}},
    ['bg2:duplicate:28']={item={'ring35'},resource={'cre:rr#bootr'}},
    ['bg2:duplicate:29']={item={'sw1h45'},resource={'cre:pptroll2'}},
}
-- A copy of the reviewed rows, for the checks that compare them with the tables.
function Gate.duplicate_rows()
    local result={}
    for id,row in pairs(DUPLICATE_ROWS) do
        local entry={}
        for key,names in pairs(row) do
            entry[key]={}
            for i,name in ipairs(names) do entry[key][i]=name end
        end
        result[id]=entry
    end
    return result
end
local function listed(names,value)
    for _,name in ipairs(names or {}) do if name==value then return true end end
    return false
end
-- Second copy <number> of a revision: its reviewed row, the source it leaves
-- with, and the records of its holder that the export names.
local function copy_binding(bundle,number)
    local m,f=bundle.manifest,bundle.fixture
    local entry=type(f.duplicates)=='table' and f.duplicates[number]
    local row=type(entry)=='table' and DUPLICATE_ROWS[entry.id]
    assert(row,'CAMPAIGN_GATE_DUPLICATE_INVALID')
    local bound
    for _,candidate in ipairs(type(m.duplicates)=='table' and m.duplicates or {}) do
        if candidate.id==entry.id and candidate.source_id==entry.source_id then
            assert(not bound,'CAMPAIGN_GATE_DUPLICATE_INVALID'); bound=candidate
        end
    end
    local index,source
    for i,candidate in ipairs(m.sources) do if candidate.id==entry.source_id then index,source=i,candidate end end
    local kind,name
    if type(entry.resource)=='string' then kind,name=entry.resource:match('^(%a+):([a-z0-9#_%-]+)$') end
    local stock=entry.stock
    assert(bound and index and kind and #name<=8 and listed(row.resource,entry.resource) and
        bound.resource==entry.resource and bound.resource_sha256==entry.resource_sha256 and
        hash(entry.resource_sha256) and f.resource_hashes[name:upper()..'.'..kind:upper()]==entry.resource_sha256 and
        listed(row.item,bound.item) and source.record.item==bound.item and
        payload(entry.payload,true) and entry.payload.resref==bound.item and
        type(entry.record_indices)=='table' and #entry.record_indices>0 and
        ((kind=='sto')==(type(stock)=='table')) and
        (kind~='sto' or (integer(stock.quantity,0,4294967295) and integer(stock.infinite_supply_flag,0,4294967295))),
        'CAMPAIGN_GATE_DUPLICATE_INVALID')
    local replacement=bound.replacement
    assert((replacement==nil and entry.replacement==nil and row.replacement==nil) or
        (kind=='cre' and type(replacement)=='table' and entry.replacement==replacement.resref and
         listed(row.replacement,replacement.resref) and hash(replacement.resource_sha256) and
         f.resource_hashes[replacement.resref:upper()..'.ITM']==replacement.resource_sha256),
        'CAMPAIGN_GATE_DUPLICATE_REPLACEMENT_INVALID')
    local seen,records,count={},{},0
    for key,record in pairs(entry.record_indices) do
        assert(integer(key,1,#entry.record_indices) and integer(record,0,65534) and not seen[record],
            'CAMPAIGN_GATE_DUPLICATE_RECORD_INVALID')
        seen[record],records[key],count=true,record,count+1
    end
    assert(count==#entry.record_indices,'CAMPAIGN_GATE_DUPLICATE_RECORD_INVALID')
    return m.revision,index,entry.resource_sha256,{id=entry.id,source_id=entry.source_id,number=number,
        kind=kind=='cre' and 'template' or (kind=='sto' and 'store' or 'container'),holder=kind,
        resource=entry.resource,name=name,store=kind=='sto' and name or nil,
        creature_resource=kind=='cre' and entry.resource or nil,records=records,record=records[1],
        payload=entry.payload,stock=stock and {quantity=stock.quantity,infinite_supply_flag=stock.infinite_supply_flag},
        replacement=replacement and replacement.resref,replacement_hash=replacement and replacement.resource_sha256}
end

-- The members of second copy <number>: one for a creature's or shop's
-- inventory group, or one for each record in an area's containers. Each is
-- active while its source moves (FLPRCampaignPolicy: any state but kept).
local function prepare_copy(api,bundle,number,build,compile,pristine,targets,cache)
    local revision,index,resource_hash,binding=copy_binding(bundle,number)
    local p=binding.payload
    local function member(buffer,policy,fields)
        local result={api=api,buffer=buffer,revision=revision,index=index,resource_hash=resource_hash,
            policy=policy,detours={},installed=false,binding={},
            copy={number=number,phase=string.format('FLPR_S%04d',index)}}
        for key,value in pairs(binding) do result.binding[key]=value end
        for key,value in pairs(fields) do result[key]=value end
        return setmetatable(result,Runtime)
    end
    if binding.holder~='are' then
        local cre=binding.holder=='cre'
        local original=pristine.duplicates and pristine.duplicates[revision] and pristine.duplicates[revision][number]
        assert(type(original)=='string' and original:sub(1,8)==(cre and 'CRE V1.0' or 'STORV1.0') and
            #original>=(cre and 0x2D4 or 0x9C),'CAMPAIGN_GATE_DUPLICATE_BYTES_MISSING')
        local function u16(offset) local a,b=original:byte(offset+1,offset+2);return a+b*256 end
        local function u32(offset) return u16(offset)+u16(offset+2)*65536 end
        local stride=cre and 20 or 28
        local offset,count=u32(cre and 0x2BC or 0x34),u32(cre and 0x2C0 or 0x38)
        assert(offset>=(cre and 0x2D4 or 0x9C) and offset+count*stride<=#original,
            'CAMPAIGN_GATE_DUPLICATE_LAYOUT_INVALID')
        for _,record in ipairs(binding.records) do
            local at=offset+record*stride
            assert(record<count and original:sub(at+1,at+8):match('^[^%z]*'):lower()==p.resref and
                u16(at+8)==p.expiration and u16(at+10)==p.charges[1] and u16(at+12)==p.charges[2] and
                u16(at+14)==p.charges[3] and u32(at+16)==p.flags and
                (cre or (u32(at+20)==binding.stock.quantity and u32(at+24)==binding.stock.infinite_supply_flag)),
                'CAMPAIGN_GATE_DUPLICATE_BINDING_MISMATCH')
        end
        local buffer=api.EEex_Malloc(BODY)
        assert(buffer and buffer~=0,'CAMPAIGN_GATE_ALLOC_FAILED')
        zero(api,buffer,MEMORY_SIZE)
        local policy=bind_policy(api,buffer,compile(revision,index,true,nil,true))
        local assembly=build(buffer,targets,policy)
        return {member(buffer,policy,{inventory_original=original})},{{enter=assembly.enter,capture=assembly.capture}}
    end
    local area=pristine[binding.name:upper()]
    assert(type(area)=='table' and area.resource_sha256==resource_hash and type(area.bytes)=='string' and
        #area.bytes>=0x11C and #area.bytes<=2147483647 and area.bytes:sub(1,8)=='AREAV1.0',
        'CAMPAIGN_GATE_PRISTINE_INVALID')
    local bytes=area.bytes
    local function u16(offset) local a,b=bytes:byte(offset+1,offset+2);return a+b*256 end
    local function u32(offset) return u16(offset)+u16(offset+2)*65536 end
    local containers,container_count,items,item_count=u32(0x70),u16(0x74),u32(0x78),u16(0x76)
    assert(containers>=0x11C and containers+container_count*192<=#bytes and items>=0x11C and
        items+item_count*20<=#bytes,'CAMPAIGN_GATE_PRISTINE_LAYOUT_INVALID')
    local members,assemblies={},{}
    for _,record in ipairs(binding.records) do
        -- The one container whose items hold the record; the item hook names it.
        local container
        for candidate=0,container_count-1 do
            local first=u32(containers+candidate*192+0x40)
            if first<=record and record<first+u32(containers+candidate*192+0x44) then
                assert(container==nil,'CAMPAIGN_GATE_DUPLICATE_CONTAINER_AMBIGUOUS'); container=candidate
            end
        end
        assert(container and record<item_count,'CAMPAIGN_GATE_DUPLICATE_CONTAINER_MISSING')
        local owner,item=containers+container*192,items+record*20
        local raw=bytes:sub(item+1,item+20)
        assert(raw:sub(1,8):match('^[^%z]*'):lower()==p.resref and u16(item+8)==p.expiration and
            u16(item+10)==p.charges[1] and u16(item+12)==p.charges[2] and u16(item+14)==p.charges[3] and
            u32(item+16)==p.flags and u32(owner+0x40)+u32(owner+0x44)<=item_count,
            'CAMPAIGN_GATE_DUPLICATE_BINDING_MISMATCH')
        local cached=cache[resource_hash]
        local buffer=api.EEex_Malloc(BODY+(cached and 0 or #bytes))
        assert(buffer and buffer~=0,'CAMPAIGN_GATE_ALLOC_FAILED')
        zero(api,buffer,MEMORY_SIZE)
        local policy=bind_policy(api,buffer,compile(revision,index,true,nil,true))
        local expected=cached or buffer+BODY
        if not cached then
            write_bytes(api,expected,bytes)
            assert(same_bytes(api,expected,bytes),'CAMPAIGN_GATE_PRISTINE_COPY_FAILED')
            cache[resource_hash]=expected
        end
        policy.provenance={expected=expected,length=#bytes,container_offset=owner,item_offset=items}
        policy.source={area=binding.name,name=bytes:sub(owner+1,owner+32),payload=raw,index=record,
            inventory=bytes:sub(owner+0x41,owner+0x48),position=bytes:sub(owner+0x21,owner+0x28)}
        local copy=member(buffer,policy,{})
        copy.binding.record,copy.binding.container=record,container
        members[#members+1]=copy
        assemblies[#assemblies+1]=build(buffer,targets,policy)
    end
    return members,assemblies
end

-- Hooks every gate location this engine has, each with an empty chain, and
-- the saved-store record. deps: build, compile, Script, Inventory (builder)
-- or inventory (loader), Cur (module) or cursed (loader), pristine (shared
-- table), load (revision -> bundle, filling pristine), revisions (the applied
-- ones), report (context, reason). A location this engine lacks only fails
-- the revisions that need it.
function Gate.new(api,deps)
    assert(type(deps)=='table' and type(deps.build)=='function' and type(deps.compile)=='function',
        'CAMPAIGN_GATE_CONFIG_INVALID')
    assert(api.EEex_Active and not api.FLPR_SOURCE_GATE and not api.FLPR_AREA_TRACE and
        not api.FLPR_CAMPAIGN_GATE, 'CAMPAIGN_GATE_INSTALL_CONTEXT_INVALID')
    assert(type(api.EEex_GameState_AddDestroyedListener)=='function' and
        type(api.EEex_GameState_GetGlobalInt)=='function' and type(api.EEex_Write8)=='function' and
        type(api.EEex_GenLuaCall)=='function','CAMPAIGN_GATE_LIFECYCLE_MISSING')
    local self=setmetatable({api=api,deps=deps,
        pristine=deps.pristine or {creatures={},stores={},scripts={},templates={},spell_shield={},cursed={},
            auxiliary={},duplicates={}},
        members={},by_revision={},option_members={},auxiliary_members={},copy_members={},copies={},
        copy_cursor=0,cache={},found={},missing={},hooked={},detours={},built={},failed={},installed=false},Group)
    if deps.revisions then
        self.allowed={}
        for _,revision in ipairs(deps.revisions) do self.allowed[revision]=true end
    end
    -- Every location is found and checked before the first write.
    local layout_ok,layout=pcall(Site.check_layout,api,'gate','CAMPAIGN_GATE')
    for _,site in ipairs(all_sites) do
        local address,reason=Site.find(api,site.key)
        if not layout_ok then self.missing[site.key]=tostring(layout)
        elseif address then self.found[site.key]=address
        else self.missing[site.key]='CAMPAIGN_GATE_CODE_MISMATCH:'..reason end
    end
    -- Both actor hooks hand the CRE bytes to the creature constructor, and the
    -- item hook identifies the area loader's container call by its return.
    local function calls(key,target_key)
        if not self.found[key] then return end
        local target=self.found[target_key]
        if not target then
            self.missing[key]=self.missing[target_key]; self.found[key]=nil
        elseif Site.call_target(api,self.found[key])~=target then
            self.missing[key]='CAMPAIGN_GATE_CODE_MISMATCH:'..key..':call-target'; self.found[key]=nil
        end
    end
    for _,site in ipairs(actor_sites) do calls(site.key,'gate_template') end
    calls('gate_container_call','gate_container_entry')
    local found=self.found
    if found.gate_item and found.gate_container_call and found.gate_container_skip then
        self.targets={item_resume=found.gate_item+ITEM_RESUME,container_return=found.gate_container_call+5,
            container_skip=found.gate_container_skip}
    end
    self.recorder=api.EEex_Malloc(16+RECORDS*8)
    assert(self.recorder and self.recorder~=0,'CAMPAIGN_GATE_ALLOC_FAILED')
    zero(api,self.recorder,16+RECORDS*8)
    api.FLPR_CAMPAIGN_GATE=self -- partial installation is never retried in-process
    api.FLPR_CampaignGateGame=function(data,size)
        local ok,reason=pcall(self._on_game,self,data,size)
        if not ok then
            self.last_error=tostring(reason)
            if deps.report then pcall(deps.report,'gate',reason) end
        end
    end
    api.EEex_DisableCodeProtection()
    local ok,failure=pcall(function()
        for _,site in ipairs(all_sites) do
            local address=found[site.key]
            if address and (site.code or site.recorder) then
                local hooked=Gate.hook(api,site,address,
                    site.key=='gate_enter' and dispatcher(api,'FLPR_CampaignGateGame') or nil)
                if api.EEex_HookIntegrityWatchdog_Load then
                    local register=api.EEex_HookIntegrityWatchdogRegister
                    if site.call then
                        api.EEex_HookIntegrityWatchdog_IgnoreRegisters(address,{register.RAX,register.RDX})
                    elseif site.code=='store' or site.code=='template' or site.code=='item' then
                        api.EEex_HookIntegrityWatchdog_IgnoreRegisters(address,{
                            site.code=='store' and register.RDI or
                                (site.code=='item' and register.R13 or register.RDX)})
                    end
                end
                self.hooked[site.key]=hooked
                self.detours[#self.detours+1]=hooked.detour
                if site.recorder then Gate.publish(api,hooked,Gate.link(api,hooked,{recorder(self.recorder)})) end
            end
        end
    end)
    api.EEex_EnableCodeProtection()
    assert(ok,failure)
    api.EEex_GameState_AddDestroyedListener(function()
        self:reset()
        api.EEex_Write64(self.recorder,0); api.EEex_Write64(self.recorder+8,0)
    end)
    self.installed=true
    return self
end

-- Builds the source checks of one campaign revision and puts them in front
-- of every chain they use. Nothing changes the running chains until all of
-- the revision's code is built; then its members are armed and published.
function Group:_add(selected)
    local api,deps,pristine=self.api,self.deps,self.pristine
    local build,compile=deps.build,deps.compile
    local revision=selected.manifest.revision
    assert(not self.by_revision[revision], 'CAMPAIGN_GATE_REVISION_DUPLICATED')
    local physical,creatures,stores,templates=false,false,false,false
    local cursed=selected.manifest.cursed_disguises~=nil or selected.fixture.cursed_disguises~=nil
    if selected.manifest.spell_shield then physical=true;stores=true end
    for _,endpoint in pairs(selected.fixture.sources) do
        if endpoint.kind~='guarded-script' then physical=true end
        if endpoint.kind=='guarded-creature' then creatures=true end
        if endpoint.kind=='guarded-store' then stores=true end
        if endpoint.kind=='guarded-template' then templates=true end
        if endpoint.auxiliary_creatures then physical=true;templates=true end
    end
    -- Second copies: areas need the item hook, creatures the template site
    -- and shops the store sites.
    local duplicates=type(selected.fixture.duplicates)=='table' and selected.fixture.duplicates or {}
    for _,copy in ipairs(duplicates) do
        local kind=type(copy)=='table' and type(copy.resource)=='string' and copy.resource:match('^(%a+):')
        physical=true
        if kind=='cre' then templates=true elseif kind=='sto' then stores=true end
    end
    local needed={}
    if physical then for _,site in ipairs(sites) do needed[#needed+1]=site end end
    if cursed and not physical then needed[#needed+1]=sites[1];needed[#needed+1]=sites[2] end
    if creatures then for _,site in ipairs(actor_sites) do needed[#needed+1]=site end end
    if stores then for _,site in ipairs(store_sites) do needed[#needed+1]=site end end
    if templates then needed[#needed+1]=template_site end
    for _,site in ipairs(needed) do
        if self.missing[site.key] then error(self.missing[site.key],0) end
    end
    local targets=physical and self.targets or nil
    local Inventory=deps.Inventory
    local function inventory()
        Inventory=Inventory or (deps.inventory and deps.inventory())
        assert(Inventory,'CAMPAIGN_GATE_INVENTORY_BUILDER_MISSING')
        return Inventory
    end
    local members,auxiliary,copies,assemblies,inventory_groups={},{},{},{},{}
    local option,cur,cur_entry,first_cursed
    if cursed then
        local Cur=deps.Cur or (deps.cursed and deps.cursed())
        assert(Cur,'CAMPAIGN_GATE_CURSED_ADAPTER_MISSING')
        first_cursed=not self.cursed
        cur=self.cursed or Cur.create(api,compile)
        local assembly
        cur_entry,assembly=cur:prepare(selected,pristine.cursed)
        -- The disguise state is reset at GAM entry and its views refreshed after
        -- every revision's selection: the first cursed revision's chain, which
        -- later revisions' chains precede, carries both.
        if first_cursed then assemblies[#assemblies+1]={enter=cur.enter} end
        assemblies[#assemblies+1]=assembly
        if first_cursed then assemblies[#assemblies+1]={capture=cur.final} end
    end
    if selected.manifest.spell_shield then
        inventory()
        local member,assembly,group=prepare_stock_option(api,selected,build,compile,pristine,targets)
        option=member
        assemblies[#assemblies+1]=assembly
        inventory_groups[revision..'/sto:udduer01']=group
    end
    for _,id in ipairs(Gate.source_ids(selected.fixture)) do
        local member,assembly
        if selected.fixture.sources[id].kind=='guarded-script' then
            assert(deps.Script,'CAMPAIGN_SCRIPT_ADAPTER_MISSING')
            member=deps.Script.new(api,selected,id,pristine.scripts[revision][id],pristine.extended)
        else
            member,assembly=prepare(api,selected,build,compile,pristine,id,targets,self.cache)
            local endpoint=selected.fixture.sources[id]
            if endpoint.kind=='guarded-template' or endpoint.multiple_stock or
                (selected.manifest.spell_shield and endpoint.kind=='guarded-store' and endpoint.store=='udduer01') then
                inventory()
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
                assembly={enter=assembly.enter,capture=assembly.capture}
            end
            assemblies[#assemblies+1]=assembly
        end
        members[#members+1]=member
        for ordinal in ipairs(selected.fixture.sources[id].auxiliary_creatures or {}) do
            inventory()
            local member_aux,auxiliary_assembly=prepare_auxiliary(api,selected,id,ordinal,build,compile,pristine,targets)
            auxiliary[#auxiliary+1]=member_aux
            assemblies[#assemblies+1]=auxiliary_assembly
            local key=revision..'/'..member_aux.binding.creature_resource
            local group=inventory_groups[key]
            if not group then
                group={kind='cre',original=member_aux.inventory_original,members={}}
                inventory_groups[key]=group
            else assert(group.original==member_aux.inventory_original,'CAMPAIGN_GATE_AUXILIARY_RESOURCE_MISMATCH') end
            group.members[#group.members+1]=member_aux
        end
    end
    for number in ipairs(duplicates) do
        local copy_members,copy_assemblies=prepare_copy(api,selected,number,build,compile,pristine,targets,self.cache)
        for i,member in ipairs(copy_members) do
            copies[#copies+1]=member
            assemblies[#assemblies+1]=copy_assemblies[i]
            local binding=member.binding
            if binding.holder~='are' then
                -- The creature or shop is rewritten as a whole, with any source in it.
                inventory()
                local key=revision..'/'..binding.resource
                local group=inventory_groups[key]
                if not group then
                    group={kind=binding.holder,ref=binding.store,original=member.inventory_original,members={}}
                    inventory_groups[key]=group
                else assert(group.original==member.inventory_original,'CAMPAIGN_GATE_DUPLICATE_RESOURCE_MISMATCH') end
                group.members[#group.members+1]=member
            end
        end
    end
    for _,group in pairs(inventory_groups) do
        local original=group.original
        local effective=group.effective or original
        local address=api.EEex_Malloc(#original+(group.option_member and 2 or 1)*#effective)
        assert(address and address~=0,'CAMPAIGN_GATE_ALLOC_FAILED')
        write_bytes(api,address,original)
        if group.option_member then
            write_bytes(api,address+#original,effective)
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
        -- Each record has one owner: a second edit would remove another record.
        local edited={}
        for _,member in ipairs(group.members) do
            for _,record in ipairs(member.binding.records or {member.binding.record}) do
                if group.insertion and not member.binding.stock_option and record>=group.insertion then record=record+1 end
                assert(record<count and not edited[record],'CAMPAIGN_GATE_INVENTORY_INDEX_INVALID')
                edited[record]=true
                group.edits[#group.edits+1]={index=record,buffer=member.buffer,
                    replacement=member.binding.replacement,context=CONTEXT}
            end
        end
        table.sort(group.edits,function(a,b) return a.index>b.index end)
        assemblies[#assemblies+1]={[group.kind=='cre' and 'template' or 'store']=inventory()(group)}
    end
    local _,parts=Gate.compose(assemblies,true)
    local linked={}
    for _,site in ipairs(all_sites) do
        local list=site.code and parts[site.code]
        if list and #list>0 then
            local hooked=self.hooked[site.key]
            if not hooked then error(self.missing[site.key] or 'CAMPAIGN_GATE_CODE_MISMATCH:'..site.key..':not-hooked',0) end
            linked[#linked+1]={hooked=hooked,chain=Gate.link(api,hooked,list)}
        end
    end
    if first_cursed then
        api.EEex_DisableCodeProtection()
        local ok,failure=pcall(cur.install,cur,self.detours)
        api.EEex_EnableCodeProtection()
        assert(ok,failure)
        self.cursed=cur
    end
    -- Nothing below can fail: arm (before New Game or any GAM load reaches
    -- them), then chain, then record the revision.
    if cur then cur:commit(cur_entry) end
    local armed={}
    for _,member in ipairs(members) do armed[#armed+1]=member end
    if option then armed[#armed+1]=option end
    for _,member in ipairs(auxiliary) do armed[#armed+1]=member end
    for _,member in ipairs(copies) do armed[#armed+1]=member end
    for _,member in ipairs(armed) do
        member.detours=self.detours
        member.installed=true
        if member.buffer then api.EEex_Write64(member.buffer,1) end
    end
    for _,entry in ipairs(linked) do Gate.publish(api,entry.hooked,entry.chain) end
    for _,member in ipairs(members) do self.members[#self.members+1]=member end
    for _,member in ipairs(copies) do self.copies[#self.copies+1]=member end
    self.by_revision[revision]=members
    self.option_members[revision]=option
    self.auxiliary_members[revision]=auxiliary
    self.copy_members[revision]=copies
    self.built[revision]=true
    return members
end

-- Whether the applied configuration lists this revision for the gate.
function Group:guards(revision)
    return hash(revision) and (not self.allowed or self.allowed[revision]==true)
end

-- Builds a campaign revision's source checks unless they exist; refuses a
-- revision the applied configuration does not list.
function Group:ensure(revision)
    if self.built[revision] then return true end
    if self.failed[revision] then error(self.failed[revision],0) end
    local ok,reason=pcall(function()
        assert(self:guards(revision),'CAMPAIGN_GATE_REVISION_NOT_APPLIED')
        assert(type(self.deps.load)=='function','CAMPAIGN_GATE_REVISION_UNAVAILABLE')
        local bundle=self.deps.load(revision)
        assert(type(bundle)=='table' and type(bundle.manifest)=='table' and type(bundle.fixture)=='table' and
            bundle.manifest.revision==revision,'CAMPAIGN_GATE_REVISION_MISMATCH')
        self:_add(bundle)
    end)
    if not ok then self.failed[revision]=tostring(reason); error(reason,0) end
    return true
end

local GAM_VERSIONS={['GAMEV2.0']=true,['GAMEV2.1']=true,['GAMEV1.1']=true}
-- The revision a GAM's FLPR_R01..FLPR_R16 name (each once, zero-padded,
-- 0..0xFFFF), as the source checks' policy reads them; nil otherwise.
function Group:_gam_revision(data,size)
    local api=self.api
    if type(data)~='number' or data<=0 or type(size)~='number' or size<0xB4 or size>0x1000000 then return nil end
    local version={}
    for offset=0,7 do version[#version+1]=string.char(api.EEex_ReadU8(data+offset)) end
    if not GAM_VERSIONS[table.concat(version)] then return nil end
    local offset,count=api.EEex_Read32(data+0x38),api.EEex_Read32(data+0x3C)
    if offset<0xB4 or count<0 or count>16384 or offset+count*84>size then return nil end
    local values,seen={},0
    for index=0,count-1 do
        local record=data+offset+index*84
        if api.EEex_Read32(record)==0x52504C46 and api.EEex_ReadU8(record+4)==0x5F and
            api.EEex_ReadU8(record+5)==0x52 then
            local tens,ones=api.EEex_ReadU8(record+6)-48,api.EEex_ReadU8(record+7)-48
            local field=tens*10+ones
            local padded=true
            for at=8,28,4 do if api.EEex_Read32(record+at)~=0 then padded=false end end
            if padded and tens>=0 and tens<=9 and ones>=0 and ones<=9 and field>=1 and field<=16 then
                if values[field] then return nil end
                local value=api.EEex_Read32(record+0x28)
                if value<0 or value>0xFFFF then return nil end
                values[field]=value; seen=seen+1
            end
        end
    end
    if seen~=16 then return nil end
    local out={}
    for field=1,16 do out[field]=string.format('%04x',values[field]) end
    return table.concat(out)
end

-- Resrefs compared as the store hook compares them: every byte OR 0x20.
local function folded(value)
    return (value:gsub('.',function(c)
        local byte=c:byte()
        return string.char(math.floor(byte/32)%2==1 and byte or byte+32)
    end))
end
-- Marks each built store source whose STO the loaded game restored, as a
-- saved store: its original stock is the saved game's, not suppressed again.
function Group:_restore_saved_stores()
    local api,recorder=self.api,self.recorder
    local count,overflow=api.EEex_ReadU64(recorder),api.EEex_ReadU64(recorder+8)~=0
    if count==0 and not overflow then return end
    local restored={}
    for index=0,math.min(count,RECORDS)-1 do
        local raw={}
        for offset=0,7 do raw[#raw+1]=string.char(api.EEex_ReadU8(recorder+16+index*8+offset)) end
        restored[folded(table.concat(raw))]=true
    end
    local stores={}
    for _,member in ipairs(self.members) do stores[#stores+1]=member end
    for _,member in pairs(self.option_members) do stores[#stores+1]=member end
    for _,member in ipairs(self.copies) do stores[#stores+1]=member end
    for _,member in ipairs(stores) do
        -- A shop's second copy left with the shop's saved stock.
        local ref=member.policy and member.policy.store and member.policy.store.ref or
            (member.copy and member.binding.store)
        if ref and (overflow or restored[folded(ref:upper()..string.rep('\0',8-#ref))]) then
            api.EEex_Write64(member.buffer+CONTEXT,1)
        end
    end
end

-- CInfGame::Unmarshal entry (New Game template or saved game), before any
-- source check reads the GAM: builds the revision a saved game names.
function Group:_on_game(data,size)
    local revision=self:_gam_revision(data,size)
    if revision and not self.built[revision] and not self.failed[revision] and self.deps.load and
        self:guards(revision) then
        local ok,reason=pcall(self.ensure,self,revision)
        if not ok and self.deps.report then pcall(self.deps.report,'gate',reason) end
    end
    self:_restore_saved_stores()
    return revision
end

-- All applied revisions at once (offline checks and focused tests).
function Gate.install(api,bundle,build,compile,pristine,Script,Inventory,Cur)
    local bundles=bundle.manifest and {bundle} or bundle
    assert(type(bundles)=='table' and #bundles>0, 'CAMPAIGN_GATE_CONFIG_INVALID')
    local count=0
    for key in pairs(bundles) do assert(integer(key,1,#bundles), 'CAMPAIGN_GATE_CONFIG_INVALID'); count=count+1 end
    assert(count==#bundles, 'CAMPAIGN_GATE_CONFIG_INVALID')
    local group=Gate.new(api,{build=build,compile=compile,pristine=pristine,Script=Script,Inventory=Inventory,Cur=Cur})
    for _,selected in ipairs(bundles) do group:_add(selected) end
    local has_auxiliary=false
    for _,entries in pairs(group.auxiliary_members) do if #entries>0 then has_auxiliary=true end end
    if #group.members==1 and next(group.option_members)==nil and not group.cursed and not has_auxiliary and
        #group.copies==0 then
        return group.members[1]
    end
    return group
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
    if self.copy then
        local revision,index,resource_hash,binding=copy_binding(bundle,self.copy.number)
        local b=self.binding
        assert(revision==self.revision and index==self.index and resource_hash==self.resource_hash and
            binding.id==b.id and binding.source_id==b.source_id and binding.resource==b.resource and
            binding.replacement==b.replacement and binding.replacement_hash==b.replacement_hash and
            same_payload(binding.payload,b.payload,true) and #binding.records==#b.records and
            (binding.stock==nil)==(b.stock==nil) and (binding.stock==nil or
             (binding.stock.quantity==b.stock.quantity and
              binding.stock.infinite_supply_flag==b.stock.infinite_supply_flag)),
            'CAMPAIGN_GATE_DUPLICATE_REVISION_MISMATCH')
        for i,record in ipairs(binding.records) do assert(record==b.records[i],'CAMPAIGN_GATE_DUPLICATE_REVISION_MISMATCH') end
        return
    end
    if self.auxiliary then
        local revision,index,resource_hash,binding=auxiliary_binding(bundle,self.binding.id,self.binding.ordinal)
        local b=self.binding
        assert(revision==self.revision and index==self.index and resource_hash==self.resource_hash and
            binding.creature_resource==b.creature_resource and binding.selected_global==b.selected_global and
            binding.replacement==b.replacement and binding.replacement_hash==b.replacement_hash and
            same_payload(binding.payload,b.payload,true) and #binding.records==#b.records,
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
        local value=self.api.EEex_GameState_GetGlobalInt(field.name)
        -- A second copy's source may be one the campaign adds (virtual: confirmed).
        assert(value==(field.phase and 1 or field.value) or
            (self.copy~=nil and field.phase and integer(value,1,6)),'CAMPAIGN_GATE_CAPTURE_MISMATCH')
    end
    if self.copy then return self:_enrol_copy(controller) end
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
-- New Game publishes a second copy's protection without an event row: its log
-- is no evidence (_copy_proof), and an area's item hook fills it as the area
-- loads again in one session.
function Runtime:_enrol_copy(controller)
    local generation=self:_q(40)
    assert(self:_intact() and self:_q(0)==1 and self:_q(24)==0 and self:_q(32)==0 and self:_q(72)==1 and
        not self.enrolled and integer(generation,1,2147483646),'CAMPAIGN_GATE_NEW_CONTEXT_INVALID')
    self:_write(40,generation+1); self:_write(112,0); self:_write(120,0)
    self.enrolled={generation=generation+1,destroys=self:_q(104),session=controller.session}
    self:_write(32,1)
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
-- A second copy is removed wherever its holder loads, and nothing waits on a
-- removal: its member needs its hooks, its arming and its proven policy. Its
-- event log is no evidence (an area's fills up as the area loads again).
function Runtime:_copy_proof()
    if not self:_intact() or self:_q(0)~=1 or self:_q(24)~=0 or self:_q(32)~=1 then
        return nil,'CAMPAIGN_GATE_INACTIVE'
    end
    local enrolled=self.enrolled and self.enrolled.generation==self:_q(40) and self.enrolled.destroys==self:_q(104)
    if not enrolled and not (self:_q(72)==0 and self:_q(80)==self.policy.mask and self:_q(88)==1) then
        return nil,'CAMPAIGN_GATE_POLICY_UNPROVEN'
    end
    return true
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

-- A revision's checks exist once a game needed them (Group:ensure): New Game
-- builds them first, loading a saved game at CInfGame::Unmarshal entry.
function Group:_members(bundle)
    local revision=bundle.manifest.revision
    local members=self.by_revision[revision]
    if not members and self.failed[revision] then error(self.failed[revision],0) end
    assert(members or not self:guards(revision),'CAMPAIGN_GATE_REVISION_NOT_BUILT')
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
    for _,member in ipairs(self.copy_members[bundle.manifest.revision] or {}) do
        member:prepare_new(bundle,controller)
    end
    if self.cursed then self.cursed:prepare_new(bundle,controller) end
    self.active_revision=bundle.manifest.revision
    self:check(bundle,true)
    return true
end
function Group:reset()
    self.active_revision=nil
    for _,member in ipairs(self.members) do member:reset() end
    for _,member in pairs(self.option_members or {}) do member:reset() end
    for _,entries in pairs(self.auxiliary_members or {}) do for _,member in ipairs(entries) do member:reset() end end
    for _,member in ipairs(self.copies) do member:reset() end
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
    result.duplicates={}
    for _,member in ipairs(self.copies) do if not revision or member.revision==revision then
        result.duplicates[member.revision..'/'..member.binding.id..'/'..member.binding.record]=member:inspect()
    end end
    return result
end
-- Second copies proved by one check, in turn; New Game and loading prove all.
Gate.COPY_CHECKS=4
function Group:check(bundle,full)
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
    -- A second copy's member is active exactly while its source moves: in
    -- any state but kept (7), which never changes once the campaign began.
    local copies=self.copies
    for _=1,full and #copies or math.min(Gate.COPY_CHECKS,#copies) do
        self.copy_cursor=self.copy_cursor%#copies+1
        local member=copies[self.copy_cursor]
        if member.revision==revision and member.api.EEex_GameState_GetGlobalInt(member.copy.phase)~=7 then
            if bundle then member:_bundle(bundle) end
            local proof,reason=member:_copy_proof()
            assert(proof,reason or 'CAMPAIGN_GATE_DUPLICATE_INACTIVE')
        else assert(member:_q(32)==0,'CAMPAIGN_GATE_DUPLICATE_KEPT_ACTIVE') end
    end
    return true
end
function Group:adapter(bundle,native)
    self:check(bundle,true)
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
