-- Single-player shop transport. Uses AddStoreItem and the engine's normal
-- save/cache path. Only the newly appended stock row is corrected: it keeps
-- the selected source payload, identified like a shop's own stock. Hook
-- locations: FLPRSite.lua.
local Site = ...
assert(type(Site)=='table' and type(Site.locate)=='function', 'SHOP_SITES_MISSING')
local Shop = {}
Shop.__index = Shop
local HEADER, STRIDE, LIMIT = 128, 64, 4096
local save = [[
    pushfq
    push rax
    push rcx
    push rdx
    push r8
    push r9
    push r10
    push r11
]]
local restore = [[
    pop r11
    pop r10
    pop r9
    pop r8
    pop rdx
    pop rcx
    pop rax
    popfq
]]

local function write_ref(api, address, ref)
    ref = ref:upper()
    assert(#ref >= 1 and #ref <= 8 and not ref:find('[^A-Z0-9#_%-]'), 'SHOP_RESREF_INVALID')
    for i=1,8 do api.EEex_Write8(address+i-1, ref:byte(i) or 0) end
end

function Shop:reset()
    self.api.EEex_Write64(self.buffer, 0)
    self.api.EEex_Write64(self.buffer+72, 0)
    self.entries, self.by_unit = {}, {}
end

function Shop:bind(fixture, controller)
    self:reset()
    for _, assignment in ipairs(assert(controller:assignments())) do
        local endpoint = fixture.targets[assignment.target_id]
        if endpoint and endpoint.kind == 'store' then
            local item = (assignment.payload or fixture.sources[assignment.source_id].payload).resref
            local entry = {unit=assignment.unit, store=endpoint.store, item=item,
                key=string.format('FLPR_P%04d', assignment.unit),
                stock_key=string.format('FLPR_SH%04d', assignment.unit)}
            local index = #self.entries
            assert(index < LIMIT, 'SHOP_CAPACITY_EXCEEDED')
            entry.address = self.buffer+HEADER+index*STRIDE
            for offset=0,STRIDE-1,8 do self.api.EEex_Write64(entry.address+offset,0) end
            write_ref(self.api,entry.address,entry.store)
            write_ref(self.api,entry.address+8,item)
            self.api.EEex_Write32(entry.address+16,assert(fixture.shop_prices[item]))
            local purchased = self.api.EEex_GameState_GetGlobalInt(entry.key)
            assert(purchased==0 or purchased==1, 'SHOP_PURCHASE_STATE_INVALID')
            self.api.EEex_Write64(entry.address+24,
                purchased==1 and 2 or (assignment.delivery_phase==2 and 1 or 0))
            -- One-based stock positions survive the STO save/cache path. Zero
            -- retains the first-matching-row behavior of older test saves.
            local stock = self.api.EEex_GameState_GetGlobalInt(entry.stock_key)
            assert(stock >= 0 and stock == math.floor(stock), 'SHOP_STOCK_POSITION_INVALID')
            self.api.EEex_Write64(entry.address+40, stock)
            self.entries[index+1], self.by_unit[entry.unit] = entry, entry
        end
    end
    self.api.EEex_Write64(self.buffer+72,#self.entries)
end

function Shop:sync_purchases()
    for _,entry in ipairs(self.entries) do
        if self.api.EEex_ReadU64(entry.address+24)==2 then
            self.api.EEex_GameState_SetGlobalInt(entry.key,1)
        end
        self.api.EEex_GameState_SetGlobalInt(entry.stock_key,
            self.api.EEex_ReadU64(entry.address+40))
    end
end

function Shop:inspect_target(endpoint)
    local api=self.api
    -- SetItemToStore has a separate multiplayer/server route, taken while a
    -- network session is established; only its local route is instrumented.
    local chitin=api.EngineGlobals and api.EngineGlobals.g_pBaldurChitin
    local network=chitin and chitin.cNetwork
    local session=network and network.m_bConnectionEstablished
    if session==nil or (session~=0 and session~=false) then return nil,'SHOP_MULTIPLAYER_UNSUPPORTED' end
    if not api.EEex_Sprite_GetInPortrait(0) then return nil,'OWNER_MISSING' end
    return true
end

function Shop:deliver(endpoint,payload,unit)
    local api,b=self.api,self.buffer
    local entry=assert(self.by_unit[unit], 'SHOP_ASSIGNMENT_MISSING')
    assert(entry.store==endpoint.store and entry.item==payload.resref,'SHOP_ASSIGNMENT_MISMATCH')
    local ready,reason=self:inspect_target(endpoint)
    if not ready then return nil,reason end
    assert(api.EEex_ReadU64(b)==0, 'SHOP_OPERATION_PENDING')
    -- Shops sell identified stock. Most sources are a creature's or a
    -- container's unidentified item; the source's other flags stay.
    local flags=payload.flags%2==0 and payload.flags+1 or payload.flags
    write_ref(api,b+8,endpoint.store);write_ref(api,b+16,payload.resref)
    api.EEex_Write16(b+24,payload.expiration)
    for i=1,3 do api.EEex_Write16(b+24+i*2,payload.charges[i]) end
    api.EEex_Write32(b+32,flags)
    api.EEex_Write64(b+48,1);api.EEex_Write64(b,1)
    local ok,failure=pcall(api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly,
        string.format('AddStoreItem("%s","%s",1,%d)',endpoint.store,payload.resref,flags),
        api.EEex_Sprite_GetInPortrait(0))
    api.EEex_Write64(b,0)
    if not ok then return nil,tostring(failure) end
    local status=api.EEex_ReadU64(b+48)
    if status~=3 then return nil,'SHOP_INSERTION_UNPROVEN' end
    api.EEex_Write64(entry.address+40,api.EEex_ReadU64(b+64))
    api.EEex_Write64(entry.address+24,1)
    self:sync_purchases()
    self.last_delivery={unit=unit,store=endpoint.store,item=payload.resref,
        before_count=api.EEex_ReadU64(b+40),after_count=api.EEex_ReadU64(b+64)}
    return true
end

function Shop.install(api)
    local sites={
        {key='shop_add_item_call',call=true},
        {key='shop_after_call',size=8},
        {key='shop_price',size=6},
        {key='shop_compress',size=10},
        {key='shop_merge',branch=true},
        -- The MOV after this CMP/CMOVE pair is also a branch destination. Leave
        -- it intact; place the hook between CMP and CMOVE, before that boundary.
        {key='shop_select',between=true},
    }
    for _,site in ipairs(sites) do site.address=Site.locate(api,site.key,'SHOP') end
    -- Only this call's price is replaced: the store screen's row prices.
    local price_return=Site.locate(api,'shop_price_call','SHOP')+5
    local chitin=Site.global(api,'g_pBaldurChitin','SHOP')
    Site.check_layout(api,'shop','SHOP')
    local b=api.EEex_Malloc(HEADER+STRIDE*LIMIT)
    assert(b and b~=0,'SHOP_ALLOCATION_FAILED')
    for offset=0,HEADER-1,8 do api.EEex_Write64(b+offset,0) end
    local self=setmetatable({api=api,buffer=b,entries={},by_unit={}},Shop)
    local function code(body) return save..'mov r10, '..b..'\n'..body..restore end
    local before=code([[
        cmp qword ptr ds:[r10],1
        jne shop_before_done
        mov rax,qword ptr ds:[rcx]
        cmp rax,qword ptr ds:[r10+8]
        jne shop_before_done
        mov rax,qword ptr ds:[rdx+0x10]
        cmp rax,qword ptr ds:[r10+16]
        jne shop_before_done
        mov qword ptr ds:[r10+56],rcx
        mov eax,dword ptr ds:[rcx+0xB8]
        mov qword ptr ds:[r10+40],rax
        mov qword ptr ds:[r10+48],2
        shop_before_done:
    ]])
    local after=code([[
        cmp qword ptr ds:[r10],1
        jne shop_after_done
        cmp qword ptr ds:[r10+48],2
        jne shop_after_done
        mov rcx,qword ptr ds:[r10+56]
        mov eax,dword ptr ds:[rcx+0xB8]
        mov qword ptr ds:[r10+64],rax
        sub rax,qword ptr ds:[r10+40]
        cmp rax,1
        jne shop_after_done
        mov r11,qword ptr ds:[rcx+0xB0]
        mov r11,qword ptr ds:[r11+16]
        mov rax,qword ptr ds:[r11]
        cmp rax,qword ptr ds:[r10+16]
        jne shop_after_done
        mov rax,qword ptr ds:[r10+24]
        mov qword ptr ds:[r11+8],rax
        mov eax,dword ptr ds:[r10+32]
        mov dword ptr ds:[r11+16],eax
        mov dword ptr ds:[r11+20],1
        mov dword ptr ds:[r11+24],0
        mov qword ptr ds:[r10+48],3
        shop_after_done:
    ]])
    -- At this callsite EAX is the base price, before the game's normal charge,
    -- reputation and merchant calculations. Other callers and resale are intact.
    local price=code(string.format([[
        mov r11,%d
        cmp qword ptr ss:[rsp+0xA8],r11
        jne shop_price_done
        mov r9,qword ptr ds:[r10+72]
        add r10,128
        shop_price_scan:
        test r9,r9
        je shop_price_done
        cmp qword ptr ds:[r10+24],1
        jne shop_price_next
        mov rax,qword ptr ds:[r15]
        cmp rax,qword ptr ds:[r10]
        jne shop_price_next
        mov rax,qword ptr ds:[rdi+0x10]
        cmp rax,qword ptr ds:[r10+8]
        jne shop_price_next
        mov r8d,dword ptr ss:[rsp+0x80]
        mov rcx,qword ptr ds:[r10+40]
        test rcx,rcx
        je shop_price_lookup
        dec rcx
        cmp ecx,r8d
        jne shop_price_next
        shop_price_lookup:
        mov r11,qword ptr ds:[r15+0xA8]
        shop_price_row:
        test r11,r11
        je shop_price_done
        test r8d,r8d
        je shop_price_found
        mov r11,qword ptr ds:[r11]
        dec r8d
        jmp shop_price_row
        shop_price_found:
        mov r11,qword ptr ds:[r11+16]
        mov rax,qword ptr ds:[r11]
        cmp rax,qword ptr ds:[r10+8]
        jne shop_price_done
        cmp qword ptr ds:[r10+40],0
        jne shop_price_owned
        mov r8,qword ptr ds:[r15+0xA8]
        shop_price_first:
        mov rcx,qword ptr ds:[r8+16]
        mov rax,qword ptr ds:[rcx]
        cmp rax,qword ptr ds:[r10+8]
        je shop_price_distinct
        mov r8,qword ptr ds:[r8]
        test r8,r8
        jne shop_price_first
        jmp shop_price_done
        shop_price_distinct:
        cmp rcx,r11
        jne shop_price_done
        shop_price_owned:
        mov qword ptr ds:[r10+32],r11
        mov eax,dword ptr ds:[r10+16]
        mov qword ptr ss:[rsp+48],rax
        jmp shop_price_done
        shop_price_next:
        add r10,64
        dec r9
        jmp shop_price_scan
        shop_price_done:
    ]],price_return))
    -- CompressItems deletes exhausted rows after buy/steal rollback is settled.
    -- Persist positions here, when the list really changes, rather than when a
    -- purchase first decrements stock (which may subsequently be restored).
    api.FLPR_ShopPurchases=function() self:sync_purchases() end
    local consumed=api.EEex_FlattenTable({save..'mov r10, '..b..'\n'..[[
        xor r8d,r8d
        mov r11,qword ptr ds:[rsi+0xA8]
        shop_removed_index:
        test r11,r11
        je shop_removed_done
        cmp r11,rbx
        je shop_removed_found
        inc r8
        mov r11,qword ptr ds:[r11]
        jmp shop_removed_index
        shop_removed_found:
        inc r8
        xor edx,edx
        mov r9,qword ptr ds:[r10+72]
        add r10,128
        shop_removed_scan:
        test r9,r9
        je shop_removed_publish
        mov rax,qword ptr ds:[rsi]
        cmp rax,qword ptr ds:[r10]
        jne shop_removed_next
        or edx,2
        cmp qword ptr ds:[r10+24],1
        jne shop_removed_next
        mov rcx,qword ptr ds:[r10+40]
        test rcx,rcx
        je shop_removed_legacy
        cmp rcx,r8
        je shop_removed_owned
        jb shop_removed_next
        dec qword ptr ds:[r10+40]
        or edx,1
        jmp shop_removed_next
        shop_removed_legacy:
        mov rax,qword ptr ds:[rdi]
        cmp rax,qword ptr ds:[r10+8]
        jne shop_removed_next
        cmp qword ptr ds:[r10+32],rdi
        jne shop_removed_next
        shop_removed_owned:
        mov qword ptr ds:[r10+24],2
        mov qword ptr ds:[r10+40],0
        or edx,1
        shop_removed_next:
        add r10,64
        dec r9
        jmp shop_removed_scan
        shop_removed_publish:
        test edx,2
        je shop_removed_done
        mov rax,]]..string.format('%.0f',chitin)..[[

        mov rax,qword ptr ds:[rax]
        test rax,rax
        je shop_removed_sync
        mov rax,qword ptr ds:[rax+0x1118]
        test rax,rax
        je shop_removed_sync
        cmp qword ptr ds:[rax+0x7A0],rsi
        jne shop_removed_sync
        mov r11,qword ptr ds:[rax+0x6C8]
        dec r8
        shop_removed_ui:
        test r11,r11
        je shop_removed_sync
        mov rcx,qword ptr ds:[r11+16]
        mov eax,dword ptr ds:[rcx+0x18]
        test eax,eax
        js shop_removed_ui_next
        cmp eax,r8d
        jb shop_removed_ui_next
        je shop_removed_ui_owned
        dec dword ptr ds:[rcx+0x18]
        jmp shop_removed_ui_next
        shop_removed_ui_owned:
        mov dword ptr ds:[rcx+0x18],-1
        mov dword ptr ds:[rcx+0x10],0
        shop_removed_ui_next:
        mov r11,qword ptr ds:[r11]
        jmp shop_removed_ui
        shop_removed_sync:
        test edx,1
        je shop_removed_done
        #MAKE_SHADOW_SPACE(32)
    ]],api.EEex_GenLuaCall('FLPR_ShopPurchases'),[[
        call_error:
        #DESTROY_SHADOW_SPACE
        shop_removed_done:
    ]]..restore})
    -- A player can sell another copy before buying the randomized one. Keep
    -- that ordinary copy in a separate row so it retains its ordinary price.
    -- A failed buy/steal can restore a zero-quantity row before compression;
    -- permit that engine rollback to merge into the original stock row.
    local separate=code([[
        jne shop_merge_done
        cmp qword ptr ds:[r10],1
        jne shop_merge_normal
        mov rax,qword ptr ds:[rbp]
        cmp rax,qword ptr ds:[r10+8]
        jne shop_merge_normal
        mov rax,qword ptr ds:[r13]
        cmp rax,qword ptr ds:[r10+16]
        je shop_merge_separate
        shop_merge_normal:
        mov r9,qword ptr ds:[r10+72]
        add r10,128
        shop_merge_scan:
        test r9,r9
        je shop_merge_done
        cmp qword ptr ds:[r10+24],1
        jne shop_merge_next
        mov rax,qword ptr ds:[rbp]
        cmp rax,qword ptr ds:[r10]
        jne shop_merge_next
        mov rax,qword ptr ds:[r13]
        cmp rax,qword ptr ds:[r10+8]
        jne shop_merge_next
        cmp dword ptr ds:[r13+0x14],0
        je shop_merge_done
        mov r11,qword ptr ds:[rbp+0xA8]
        mov rcx,qword ptr ds:[r10+40]
        test rcx,rcx
        je shop_merge_first
        dec rcx
        shop_merge_position:
        test r11,r11
        je shop_merge_next
        test rcx,rcx
        je shop_merge_position_found
        mov r11,qword ptr ds:[r11]
        dec rcx
        jmp shop_merge_position
        shop_merge_position_found:
        mov r8,qword ptr ds:[r11+16]
        jmp shop_merge_distinct
        shop_merge_first:
        mov r8,qword ptr ds:[r11+16]
        mov rax,qword ptr ds:[r8]
        cmp rax,qword ptr ds:[r10+8]
        je shop_merge_distinct
        mov r11,qword ptr ds:[r11]
        test r11,r11
        jne shop_merge_first
        jmp shop_merge_done
        shop_merge_distinct:
        cmp r8,r13
        jne shop_merge_next
        shop_merge_separate:
    ]]..restore..[[
        jmp #L(jmp_success)
        shop_merge_next:
        add r10,64
        dec r9
        jmp shop_merge_scan
        shop_merge_done:
    ]])
    -- UpdateStoreItems normally copies one selected entry to EVERY equivalent
    -- item row. Match the actual stock position and consume the selection once.
    -- Header +80 is a permanent empty list node, used to end that match search.
    local selection=code([[
        jne shop_selection_done
        mov rcx,qword ptr ds:[r10+72]
        add r10,128
        shop_selection_scan:
        test rcx,rcx
        je shop_selection_done
        mov r11,qword ptr ds:[rbp+0x7A0]
        mov rax,qword ptr ds:[r11]
        cmp rax,qword ptr ds:[r10]
        jne shop_selection_next
        mov rax,qword ptr ds:[r8+0x10]
        cmp rax,qword ptr ds:[r10+8]
        jne shop_selection_next
        cmp dword ptr ds:[r9+0x10],1
        jne shop_selection_reject
        mov eax,dword ptr ds:[r9+0x18]
        cmp eax,dword ptr ds:[rbx+0x18]
        jne shop_selection_reject
        mov dword ptr ds:[r9+0x10],0
        mov rax,]]..(b+80)..'\n'..[[
        mov qword ptr ss:[rsp+40],rax
        jmp shop_selection_done
        shop_selection_reject:
        and qword ptr ss:[rsp+56],-65
        jmp shop_selection_done
        shop_selection_next:
        add r10,64
        dec rcx
        jmp shop_selection_scan
        shop_selection_done:
    ]])
    local assemblies={before,after,price,consumed,separate,selection}
    api.EEex_DisableCodeProtection()
    local ok,failure=pcall(function()
        for i,site in ipairs(sites) do
            local address=site.address
            local assembly=type(assemblies[i])=='string' and {assemblies[i]} or assemblies[i]
            if site.between then api.EEex_HookBetweenRestore(address,0,4,4,4,8,assembly)
            elseif site.branch then api.EEex_HookBeforeConditionalJump(address,0,assembly)
            elseif site.call then api.EEex_HookBeforeCall(address,assembly)
            else api.EEex_HookBeforeRestore(address,0,site.size,site.size,assembly) end
            if i==3 and api.EEex_HookIntegrityWatchdog_Load then
                api.EEex_HookIntegrityWatchdog_IgnoreRegisters(address,{api.EEex_HookIntegrityWatchdogRegister.RAX})
            end
            if i==6 and api.EEex_HookIntegrityWatchdog_Load then
                api.EEex_HookIntegrityWatchdog_IgnoreRegisters(address,{api.EEex_HookIntegrityWatchdogRegister.RCX})
            end
        end
    end)
    api.EEex_EnableCodeProtection()
    assert(ok,failure)
    api.EEex_GameState_AddDestroyedListener(function() self:reset() end)
    return self
end
return Shop
