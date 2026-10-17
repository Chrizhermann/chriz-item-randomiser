-- Where the native hooks go. Each location is a byte pattern in the engine's
-- code ('??' = any byte, such as a call target that moves between builds),
-- found once per session in the executable the game runs from, so the hooks
-- follow the engine code from build to build: BG:EE and BG2:EE (also used by
-- EET), 2.7.3.0 and 2.6.6.0. A location is used only when its pattern occurs
-- exactly once in the code section and the bytes at the hook are still the
-- expected ones in memory (no other mod hooked the same instructions).
-- Otherwise the caller stops with <PREFIX>_CODE_MISMATCH and the reason, and
-- New Game offers to start without randomisation; the game itself carries on.
-- EEex's extra_pattern_dbs are not used on purpose: a pattern the loader does
-- not find there stops EEex from starting at all.
-- tools/engine_patterns.py checks these patterns against any Baldur.exe.
local Site = {}

-- at: offset of the hook from the pattern's first byte; bytes: what the hook
-- expects there.
Site.SPECS = {
    -- CInfGame::Unmarshal: a saved game or New Game template starts loading
    gate_enter={at=16,pattern='488B05????????4C8BF94805F815000048894DF8488D4DC8488945A8410FB6F9',
        bytes='48894df8488d4dc8488945a8410fb6f944884c2460'},
    -- CInfGame::Unmarshal: before the GAM variable list is read
    gate_capture={at=14,pattern='0F82????????498BCFE8????????8BD6413974242C0F86????????0F1F4000',
        bytes='8bd6413974242c0f86????????0f1f400066660f1f84'},
    -- CGameContainer::CGameContainer: one ARE container item record
    gate_item={at=13,pattern='443BF90F83????????0F1F4000418BC7488D0C80488B45884C8D2C88498BD5',
        bytes='418bc7488d0c80488b45884c8d2c88498bd5488d4df8'},
    -- CGameArea::Unmarshal: area file bytes arrive
    gate_area_enter={at=17,pattern='488B05????????4833C4488985000100004533E444884C24604489642468450FB6E9',
        bytes='4533e444884c24604489642468450fb6e94c8bfa4c8bf1'},
    -- CGameArea::Unmarshal: return
    gate_area_exit={at=14,pattern='E8????????488D4D90E8????????488B9C2470020000418BC4488B8D00010000',
        bytes='488b9c2470020000418bc4488b8d000100004833cc'},
    -- CGameArea::Unmarshal: the call that constructs one container
    gate_container_call={at=16,pattern='4889542428498BD666894C2420488BC8E8????????4885C00F84????????4C8B00',
        bytes='e8????????4885c00f84????????4c8b00418bd4488bc8'},
    -- CGameContainer::CGameContainer: entry and frame setup. Only the call's
    -- target is checked here; another mod may hook the function's first bytes.
    gate_container_entry={at=0,pattern='48895C24205556574154415541564157488D6C24804881EC80010000488B05????????4833C448894570',
        bytes='??????????5556574154415541564157488d6c24804881ec80010000'},
    -- CGameContainer::CGameContainer: after one item record
    gate_container_skip={at=15,pattern='E8????????488D4C2460E8????????418B464441FFC74103464041BC00000000',
        bytes='418b464441ffc74103464041bc0000000044897c2450443bf80f82????????4c8b6d98410f'},
    -- CGameArea::Unmarshal: first creature construction call
    gate_actor1={at=13,pattern='66894C2428488B4DA889742420E8????????498DB580000000E9????????',
        bytes='e8????????498db580000000e9????????'},
    -- CGameArea::Unmarshal: second creature construction call
    gate_actor2={at=15,pattern='458B858C0000004903D7450FB74D2CE8????????488BF84885C00F84????????',
        bytes='e8????????488bf84885c00f84????????'},
    -- CStore::SetResRef: STO header copy
    gate_store={at=13,pattern='837838FF0F95C1898B0C0100000F1047080F1143080F104F180F114B18',
        bytes='0f1047080f114308'},
    -- dimmUncompressResources: a saved STO is restored
    gate_saved_store={at=17,pattern='8B05????????8943384889734044896348C6435001498BCFE8????????4585F6753E',
        bytes='c6435001498bcf'},
    -- CStore::SetResRef: stock list built
    gate_store_done={at=20,pattern='4889B3F80000004C8D044049C1E002E8????????4C8B742448488B6C2460',
        bytes='4c8b742448'},
    -- CGameSprite::CGameSprite: entry
    gate_template={at=0,pattern='48895C24185556574154415541564157488D6C24F94881EC00010000',
        bytes='48895c24185556574154415541564157'},
    -- CRes::Demand: return of an already loaded resource
    cur_demand1={at=20,pattern='40534883EC20488BD94885C974??80795100740A488B41404883C4205BC3E8????????',
        bytes='488b41404883c420'},
    -- CRes::Demand: return after loading
    cur_demand2={at=14,pattern='488BCBFF10884351807B5100740A488B43404883C4205BC333C04883C420',
        bytes='488b43404883c420'},
    -- dimmDumpAllInTempOverride: reads the resource count
    cur_registry_count={at=6,pattern='40564883EC20486335????????4885F60F8E????????48895C243048896C243833ED48897C24408BFD0F1F8000000000488B1D????????8B05????????',
        bytes='486335????????'},
    -- dimmDumpAllInTempOverride: reads the resource array
    cur_registry_array={at=14,pattern='48897C24408BFD0F1F8000000000488B1D????????8B05????????488B1CFB',
        bytes='488b1d????????'},
    -- CInfGame::SetItemToStore: local AddItemExt call
    shop_add_item_call={at=14,pattern='458BC6488D8C2470010000498BD4E8????????488D8C2470010000E8????????',
        bytes='e8????????'},
    -- CInfGame::SetItemToStore: after the local AddItemExt call
    shop_after_call={at=16,pattern='488D8C2470010000498BD4E8????????488D8C2470010000E8????????488D8C2470010000',
        bytes='488d8c2470010000'},
    -- CStore::GetItemBuyValue: base price known
    shop_price={at=12,pattern='450FB6E8488BFAE8????????488D4F108BF0488D15????????E8????????',
        bytes='488d4f108bf0'},
    -- CStore::CompressItems: an exhausted row is removed
    shop_compress={at=12,pattern='837F14007727F64718017521488BD3488D8EA0000000488B1BE8????????',
        bytes='488bd3488d8ea0000000'},
    -- CStore::AddItem: merge with an equal row
    shop_merge={at=14,pattern='498BD5E8????????488B00483B030F85????????837D08050F85????????',
        bytes='0f85????????'},
    -- CScreenStore::UpdateStoreItems: selection copied to equal rows
    shop_select={at=14,pattern='418B4024394224750D410FB7401C6639421C490F44F9488B094885C975D0',
        bytes='6639421c490f44f9'},
    -- CScreenStore::UpdateStoreItems: the GetItemBuyValue call for store rows
    shop_price_call={at=14,pattern='488B8DA0070000498BD6440FB7C8E8????????89431C418BD7488B8DA0070000',
        bytes='e8????????89431c'},
}

-- Engine data offsets the hook code uses directly, the same in 2.6.6.0 and
-- 2.7.3.0. Where EEex's bindings know a field at another offset, that engine
-- build changed its data layout and the hooks must not run. A field EEex does
-- not bind cannot be checked this way; the matched code then has to do.
Site.LAYOUT = {
    gate={{'CGameArea.m_resref',0x204},{'CStore.m_lInventory',0xA0},{'CRes.type',0x10},{'CRes.nSize',0x48}},
    shop={{'CStore.m_lInventory',0xA0},{'CItem.cResRef',0x10},{'CBaldurChitin.m_pEngineStore',0x1118},
        {'CScreenStore.m_pStore',0x7A0},{'CScreenStore.m_lStoreItems',0x6C0},
        {'CScreenStoreItem.m_bSelected',0x10},{'CScreenStoreItem.m_nSlot',0x18}},
    cursed={{'CRes.resref',0x8},{'CRes.type',0x10},{'CRes.pData',0x40},{'CRes.nSize',0x48},
        {'CRes.bLoaded',0x51},{'CResItem.pAbilities',0x58},{'CResItem.pEffects',0x60},
        {'CResItem.pHeader',0x68}},
}

-- The executables InfinityLoader starts, in its order (InfinityLoader.ini
-- ExeNames); the one whose headers are mapped at the image base is running.
Site.EXECUTABLES = {'Baldur.exe','BaldurII.exe','SiegeOfDragonspear.exe'}

function Site.read_file(name)
    local file=io.open(name,'rb')
    if not file then return nil end
    local data=file:read('*a')
    file:close()
    return data
end

local function u16(data,at) local a,b=data:byte(at+1,at+2); return a+b*256 end
local function u32(data,at)
    local a,b,c,d=data:byte(at+1,at+4)
    return a+b*256+c*65536+d*16777216
end

-- The PE headers of a 64-bit executable and its code section, or nil.
local function headers(data)
    if #data<0x40 or data:sub(1,2)~='MZ' then return nil end
    local pe=u32(data,0x3C)
    if pe+26>#data or data:sub(pe+1,pe+4)~='PE\0\0' or u16(data,pe+24)~=0x20B then return nil end
    local sections=pe+24+u16(data,pe+20)
    local count=u16(data,pe+6)
    if sections+count*40>#data then return nil end
    local image={pe=pe,sections=sections,finish=sections+count*40}
    for at=sections,image.finish-1,40 do
        if data:sub(at+1,at+8)=='.text\0\0\0' then
            local size,raw=math.min(u32(data,at+8),u32(data,at+16)),u32(data,at+20)
            if raw+size<=#data then image.code={va=u32(data,at+12),raw=raw,size=size} end
        end
    end
    return image.code and image
end

-- The running program's headers (COFF header and section table) are mapped
-- at the image base unchanged.
local function running(api,base,data,image)
    for _,range in ipairs({{image.pe,image.pe+24},{image.sections,image.finish}}) do
        for at=range[1],range[2]-1 do
            if api.EEex_ReadU8(base+at)~=data:byte(at+1) then return false end
        end
    end
    return true
end

-- Every match of each location's pattern in the code, as offsets from the
-- image base. The longest run of fixed bytes is searched as plain text and
-- the other fixed bytes are compared around each hit.
function Site.search(data,code)
    local found={}
    for key,spec in pairs(Site.SPECS) do
        local fixed,anchor,anchor_at,run,run_at,size={},'',0,{},0,0
        local function close()
            if #run>#anchor then anchor,anchor_at=table.concat(run),run_at end
            run={}
        end
        for pair in spec.pattern:gmatch('..') do
            if pair=='??' then close() else
                local byte=tonumber(pair,16)
                if #run==0 then run_at=size end
                run[#run+1]=string.char(byte); fixed[#fixed+1]={size,byte}
            end
            size=size+1
        end
        close()
        local first,last=code.raw+1,code.raw+code.size-size+1
        local hits,init={},first+anchor_at
        while true do
            local position=data:find(anchor,init,true)
            if not position or position-anchor_at>last then break end
            local start,same=position-anchor_at,true
            for _,check in ipairs(fixed) do
                if data:byte(start+check[1])~=check[2] then same=false; break end
            end
            if same then hits[#hits+1]=code.va+start-first+spec.at end
            init=position+1
        end
        found[key]=hits
    end
    return found
end

-- Searches the running executable once per session; the result (or why there
-- is none) stays in api.FLPR_ENGINE_SITES for every caller.
function Site.scan(api)
    if api.FLPR_ENGINE_SITES then return api.FLPR_ENGINE_SITES end
    local engine={failure='engine:image-base-unknown'}
    local base=type(api.EEex_GetImageBase)=='function' and api.EEex_GetImageBase()
    if type(base)=='number' and base>0 then
        engine.failure='engine:executable-not-found'
        for _,name in ipairs(Site.EXECUTABLES) do
            local data=Site.read_file(name)
            if data then
                local image=headers(data)
                if image and running(api,base,data,image) then
                    engine={base=base,file=name,offsets=Site.search(data,image.code)}
                    break
                end
                engine.failure='engine:not-the-running-executable:'..name
            end
        end
    end
    api.FLPR_ENGINE_SITES=engine
    return engine
end

-- The checked address of a location, or nil and the reason.
function Site.find(api,key)
    local spec=Site.SPECS[key]
    if not spec then return nil,'unknown-site:'..tostring(key) end
    local engine=Site.scan(api)
    if engine.failure then return nil,engine.failure end
    local offsets=engine.offsets[key] or {}
    if #offsets==0 then return nil,key..':not-found' end
    if #offsets>1 then return nil,key..':ambiguous:'..#offsets end
    local address=engine.base+offsets[1]
    local offset=0
    for pair in spec.bytes:gmatch('..') do
        if pair~='??' and api.EEex_ReadU8(address+offset)~=tonumber(pair,16) then
            return nil,key..':differs-at:'..offset
        end
        offset=offset+1
    end
    return address
end

function Site.locate(api,key,prefix)
    local address,reason=Site.find(api,key)
    if not address then error(prefix..'_CODE_MISMATCH:'..reason,0) end
    return address
end

-- An EEex label for an engine global (address of the variable); EEex itself
-- needs these, so its loader always defines them.
function Site.global(api,label,prefix)
    local value=type(api.EEex_TryLabel)=='function' and api.EEex_TryLabel(label)
    if type(value)~='number' or value<=0 then error(prefix..'_CODE_MISMATCH:'..label..':not-found',0) end
    return value
end

-- Target of the 5-byte relative call at address.
function Site.call_target(api,address)
    return address+5+api.EEex_Read32(address+1)
end

-- The address a RIP-relative instruction of this length refers to; its
-- displacement is the instruction's last four bytes.
function Site.rip_target(api,address,length)
    return address+length+api.EEex_Read32(address+length-4)
end

function Site.check_layout(api,group,prefix)
    if type(api.EEex_OffsetOf)~='function' then return end
    for _,field in ipairs(Site.LAYOUT[group]) do
        local ok,offset=pcall(api.EEex_OffsetOf,field[1])
        if ok and type(offset)=='number' and offset~=field[2] then
            error(prefix..'_CODE_MISMATCH:layout:'..field[1]..':'..offset,0)
        end
    end
end

return Site
