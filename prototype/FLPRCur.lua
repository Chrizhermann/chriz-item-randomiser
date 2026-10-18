-- Original component 570 only. Keep CItem resrefs and engine-owned CRes bytes;
-- hand out a campaign's immutable ITM view before any equipped effects are read.
-- Hook locations: FLPRSite.lua.
local Site=...
assert(type(Site)=='table' and type(Site.locate)=='function','CURSED_SITES_MISSING')
local Cur={}; local Runtime={}; Runtime.__index=Runtime
local NAMES={'brac08','brac11','leat06','plat17','ring04','ring23','sper03','sw2h03'}
local RANGE=2147483646
-- Both returns of CRes::Demand that hand out loaded resource data.
local SITES={'cur_demand1','cur_demand2'}
local function integer(n,lo,hi) return type(n)=='number' and n==math.floor(n) and n>=lo and n<=hi end
local function number(n) return string.format('%.0f',n) end
local function u32(s,o) local a,b,c,d=s:byte(o+1,o+4);return a+b*256+c*65536+d*16777216 end
local function save() return 'pushfq\npush rax\npush rcx\npush rdx\npush rsi\npush rdi\npush r8\npush r9\npush r10\npush r11\n' end
local function restore() return 'pop r11\npop r10\npop r9\npop r8\npop rdi\npop rsi\npop rdx\npop rcx\npop rax\npopfq\n' end
local function rebase()
    return 'mov qword ptr ds:[rcx+0x68], rax\nmov edx, dword ptr ds:[rax+0x6A]\n'..
        'add rdx, rax\nmov qword ptr ds:[rcx+0x60], rdx\nmov edx, dword ptr ds:[rax+0x64]\n'..
        'add rdx, rax\nmov qword ptr ds:[rcx+0x58], rdx\n'
end
local function registered(self,name,label)
    return 'mov r11, '..number(self.registry)..'\nmov r11, qword ptr ds:[r11]\n'..
        'mov rdx, '..number(self.registry_count)..'\nmov edx, dword ptr ds:[rdx]\n'..
        'test r11, r11\nje '..label..'_gone\ncmp edx, 2000000\nja '..label..'_gone\n'..
        label..'_registry:\ntest edx, edx\nje '..label..'_gone\ndec edx\n'..
        'cmp qword ptr ds:[r11+rdx*8], rcx\njne '..label..'_registry\n'..
        'cmp dword ptr ds:[rcx+0x10], 0x3ED\njne '..label..'_gone\n'..
        'mov rdx, qword ptr ds:[rcx+8]\ntest rdx, rdx\nje '..label..'_gone\n'..
        'cmp byte ptr ds:[rdx+6], 0\njne '..label..'_gone\n'..
        'mov eax, dword ptr ds:[rdx]\nor eax, 0x20202020\ncmp eax, '..number(u32(name,0))..'\njne '..label..'_gone\n'..
        'movzx eax, word ptr ds:[rdx+4]\nor eax, 0x2020\ncmp eax, '..(name:byte(5)+name:byte(6)*256)..'\n'..
        'je '..label..'_present\n'..label..'_gone:\nmov qword ptr ds:[r10], 0\njmp '..label..'_done\n'..
        label..'_present:\n'
end
-- RCX=CRes, R10=one of eight slots. Engine buffers are compared, never edited.
local function view(state,label)
    return 'mov rax, qword ptr ds:[rcx+0x40]\n'..
        'cmp qword ptr ds:[r10+8], 0\nje '..label..'_raw\n'..
        'mov edx, dword ptr ds:[rcx+0x48]\ncmp rdx, qword ptr ds:[r10+24]\njne '..label..'_bad\n'..
        'push rcx\nmov rsi, rax\nmov rdi, qword ptr ds:[r10+16]\nmov rcx, rdx\ncld\nrepe cmpsb\npop rcx\n'..
        'jne '..label..'_bad\nmov rax, qword ptr ds:[r10+8]\njmp '..label..'_raw\n'..
        label..'_bad:\nmov rdx, '..number(state)..'\nmov qword ptr ds:[rdx+16], 1\n'..
        label..'_raw:\n'..rebase()
end
local function refresh(self,prefix,clear)
    local out={}
    for index,name in ipairs(NAMES) do
        local slot=self.slots[name]; local label=prefix..index
        out[#out+1]='mov r10, '..number(slot)..'\n'
        if clear then out[#out+1]='mov qword ptr ds:[r10+8], 0\n' end
        out[#out+1]='mov rcx, qword ptr ds:[r10]\ntest rcx, rcx\nje '..label..'_done\n'..
            registered(self,name,label)..
            'cmp byte ptr ds:[rcx+0x51], 0\nje '..label..'_done\n'..
            'cmp qword ptr ds:[rcx+0x40], 0\nje '..label..'_done\n'..
            view(self.state,label)..label..'_done:\n'
    end
    return table.concat(out)
end
local function selection(self,revision,index)
    local out={'mov r8, qword ptr ds:[r10+96]\nmov qword ptr ds:[r10+8], r8\n'..
        'add r8, 570\ncmp r8, 2147483646\njbe gate_curse_salt_'..index..'\nsub r8, 2147483646\n'..
        'gate_curse_salt_'..index..':\nmov qword ptr ds:[r10], '..index..'\n'}
    for ordinal,item in ipairs(revision.items) do
        local label='gate_curse_draw_'..index..'_'..ordinal
        local count=#item.pointers
        out[#out+1]=label..':\nimul rax, r8, 16807\nxor edx, edx\nmov ecx, 2147483647\ndiv rcx\n'..
            'mov r8, rdx\nlea rax, ds:[rdx-1]\ncmp rax, '..number(RANGE-RANGE%count)..'\njae '..label..'\n'..
            'xor edx, edx\nmov ecx, '..count..'\ndiv rcx\nmov r9, '..number(item.table)..'\n'..
            'mov rax, qword ptr ds:[r9+rdx*8]\nmov r9, '..number(self.slots[item.resref])..'\n'..
            'mov qword ptr ds:[r9+8], rax\nmov rax, '..number(item.original)..'\n'..
            'mov qword ptr ds:[r9+16], rax\nmov qword ptr ds:[r9+24], '..item.size..'\n'
    end
    return table.concat(out)
end

function Cur.new(api,bundles,pristine,compile)
    local self=setmetatable({api=api,revisions={},ordered={},slots={}},Runtime)
    local state=assert(api.EEex_Malloc(128+#NAMES*32),'CURSED_ALLOC_FAILED');self.state=state
    for at=0,128+#NAMES*32-1,8 do api.EEex_Write64(state+at,0) end
    for index,name in ipairs(NAMES) do self.slots[name]=state+128+(index-1)*32 end
    local assets={}
    local function bytes(value)
        if assets[value] then return assets[value] end
        local address=assert(api.EEex_Malloc(#value),'CURSED_ALLOC_FAILED')
        for at=1,#value do local n=value:byte(at);api.EEex_Write8(address+at-1,n>=128 and n-256 or n) end
        assets[value]=address;return address
    end
    for _,bundle in ipairs(bundles) do
        local spec,fixture=bundle.manifest.cursed_disguises,bundle.fixture.cursed_disguises
        if spec then
            assert(bundle.manifest.original_options.cursed_disguise==true and fixture and
                fixture.algorithm=='park-miller-cursed-v1' and integer(#fixture.items,1,8) and
                #fixture.items==#spec.choices,
                'CURSED_CONFIG_INVALID')
            local revision=bundle.manifest.revision
            local entry=assert(pristine and pristine[revision],'CURSED_BYTES_MISSING')
            local current={revision=revision,items={},policy=compile(revision,'cursed_disguise')}
            assert(not self.revisions[revision],'CURSED_REVISION_DUPLICATED')
            self.revisions[revision]=current;self.ordered[#self.ordered+1]=current
            local seen={}
            for ordinal,item in ipairs(fixture.items) do
                local name=item.resref;local authored=spec.choices[ordinal]
                assert(self.slots[name] and not seen[name] and authored.source==name and
                    item.sha256==spec.resource_hashes[name] and integer(#item.variants,1,7) and
                    #item.variants==#authored.variants,
                    'CURSED_ITEM_INVALID');seen[name]=true
                local data=assert(entry[name],'CURSED_ITEM_BYTES_MISSING')
                local original=data.original
                assert(type(original)=='string' and #original==item.size and #original>=0x72 and
                    original:sub(1,8)=='ITM V1  ' and #data.variants==#item.variants,'CURSED_ITEM_BYTES_INVALID')
                local row={resref=name,size=#original,original=bytes(original),pointers={}}
                row.table=assert(api.EEex_Malloc(#item.variants*8),'CURSED_ALLOC_FAILED')
                for face,value in ipairs(data.variants) do
                    assert(type(value)=='string' and #value==#original and value:sub(1,8)=='ITM V1  ' and
                        value:sub(0x64+1,0x72)==original:sub(0x64+1,0x72) and
                        item.variants[face].donor==authored.variants[face].donor,'CURSED_VARIANT_INVALID')
                    row.pointers[face]=bytes(value);api.EEex_Write64(row.table+(face-1)*8,row.pointers[face])
                end
                current.items[#current.items+1]=row
            end
        else assert(not fixture,'CURSED_CONFIG_INVALID') end
    end
    assert(#self.ordered>0,'CURSED_CONFIG_MISSING')
    self.sites={}
    for index,key in ipairs(SITES) do self.sites[index]=Site.locate(api,key,'CURSED') end
    -- dimmDumpAllInTempOverride reads the current resource registry (array and
    -- count) on each iteration. Membership is checked before dereferencing
    -- remembered CRes pointers, since a later override/language rebuild may
    -- replace the process resource objects.
    self.registry_count=Site.rip_target(api,Site.locate(api,'cur_registry_count','CURSED'),7)
    self.registry=Site.rip_target(api,Site.locate(api,'cur_registry_array','CURSED'),7)
    Site.check_layout(api,'cursed','CURSED')
    local enter=save()..'mov r10, '..number(state)..'\nmov qword ptr ds:[r10], 0\n'..
        'mov qword ptr ds:[r10+8], 0\nmov qword ptr ds:[r10+16], 0\n'..
        'mov qword ptr ds:[r10+48], rdx\nmov eax, r8d\nmov qword ptr ds:[r10+56], rax\n'..
        refresh(self,'gate_curse_enter_',true)..restore()
    -- The GAM being read: GAME V2.0 (BG2:EE, EET), GAME V2.1 (their saves in
    -- Throne of Bhaal) or GAME V1.1 (BG:EE), the same 0xB4-byte header and
    -- 84-byte variable records.
    local capture_header=save()..'mov r10, '..number(state)..'\n'..[[
        cmp r14, qword ptr ds:[r10+48]
        jne gate_curse_done
        cmp qword ptr ds:[r10+56], 0xB4
        jb gate_curse_done
        cmp qword ptr ds:[r10+56], 0x1000000
        ja gate_curse_done
        test r14, r14
        je gate_curse_done
        mov rax, 0x302e3256454d4147
        cmp qword ptr ds:[r14], rax
        je gate_curse_gam_version
        mov rax, 0x312e3256454d4147
        cmp qword ptr ds:[r14], rax
        je gate_curse_gam_version
        mov rax, 0x312e3156454d4147
        cmp qword ptr ds:[r14], rax
        jne gate_curse_done
        gate_curse_gam_version:
        mov eax, dword ptr ds:[r14+0x38]
        cmp eax, 0xB4
        jb gate_curse_done
        mov ecx, dword ptr ds:[r14+0x3C]
        cmp ecx, 16384
        ja gate_curse_done
        imul rdx, rcx, 84
        add rdx, rax
        cmp rdx, qword ptr ds:[r10+56]
        ja gate_curse_done
    ]]
    -- Each block fits EEex's single-page near allocator. Keep independent
    -- revision checks and final refresh as separate existing Gate chain parts.
    self.assemblies={{enter=enter}}
    local combined={}
    for index,current in ipairs(self.ordered) do
        local prefix='gate_curse_policy_'..index..'_'
        local capture=capture_header..'mov r10, '..number(state)..'\nmov qword ptr ds:[r10+72], 1\n'..
            'mov qword ptr ds:[r10+80], 0\nmov qword ptr ds:[r10+88], 0\nmov qword ptr ds:[r10+96], 0\n'..
            'mov eax, dword ptr ds:[r14+0x38]\nadd rax, r14\nmov ecx, dword ptr ds:[r14+0x3C]\n'..
            current.policy.scan:gsub('gate_',prefix)..'\n'..prefix..'capture_log:\n'..
            'cmp qword ptr ds:[r10+72], 0\njne '..prefix..'next\n'..
            selection(self,current,index)..prefix..'next:\n'..'gate_curse_done:\n'..restore()
        capture=capture:gsub('gate_curse_done','gate_curse_done_'..index)
            :gsub('gate_curse_gam_version','gate_curse_gam_version_'..index)
        self.assemblies[#self.assemblies+1]={capture=capture};combined[#combined+1]=capture
    end
    local final=save()..refresh(self,'gate_curse_refresh_',false)..restore()
    self.assemblies[#self.assemblies+1]={capture=final};combined[#combined+1]=final
    self.assembly={enter=enter,capture=table.concat(combined)} -- offline inspection only
    local demand={'mov rax, qword ptr ds:[rbx+0x40]\n',save(),
        'cmp dword ptr ds:[rbx+0x10], 0x3ED\njne gate_curse_demand_done\n'..
        'mov rcx, qword ptr ds:[rbx+8]\ntest rcx, rcx\nje gate_curse_demand_done\n'..
        'cmp byte ptr ds:[rcx+6], 0\njne gate_curse_demand_done\n'..
        'mov eax, dword ptr ds:[rcx]\nor eax, 0x20202020\nmovzx edx, word ptr ds:[rcx+4]\nor edx, 0x2020\n'}
    for index,name in ipairs(NAMES) do
        demand[#demand+1]='cmp eax, '..number(u32(name,0))..'\njne gate_curse_name_'..index..'\n'..
            'cmp edx, '..(name:byte(5)+name:byte(6)*256)..'\njne gate_curse_name_'..index..'\n'..
            'mov r10, '..number(self.slots[name])..'\njmp gate_curse_matched\ngate_curse_name_'..index..':\n'
    end
    demand[#demand+1]='jmp gate_curse_demand_done\ngate_curse_matched:\n'..
        'mov qword ptr ds:[r10], rbx\nmov rcx, rbx\n'..view(state,'gate_curse_view')..
        'mov qword ptr ss:[rsp+64], rax\ngate_curse_demand_done:\n'..restore()
    self.demand=table.concat(demand)
    return self
end

function Runtime:install(detours)
    self.detours={}
    for _,address in ipairs(self.sites) do
        -- Replay only ADD RSP,0x20. Replaying MOV RAX,[CRes+0x40] would undo
        -- the chosen return view; the demand block performs that MOV itself.
        self.api.EEex_HookBeforeRestore(address,4,4,8,{self.demand})
        if self.api.EEex_HookIntegrityWatchdog_Load then
            self.api.EEex_HookIntegrityWatchdog_IgnoreRegisters(address,{self.api.EEex_HookIntegrityWatchdogRegister.RAX})
        end
        assert(self.api.EEex_ReadU8(address)==0xE9,'CURSED_DETOUR_NOT_WRITTEN')
        local target=address+5+self.api.EEex_Read32(address+1)
        assert(target>0 and (target<address or target>=address+8),'CURSED_DETOUR_INVALID')
        local entry={address=address,target=target}
        detours[#detours+1]=entry;self.detours[#self.detours+1]=entry
    end
end
function Runtime:_refresh(clear)
    local api=self.api
    for _,name in ipairs(NAMES) do
        local slot=self.slots[name]
        if clear then api.EEex_Write64(slot+8,0) end
        local res=api.EEex_ReadU64(slot)
        if res~=0 then
            local array=api.EEex_ReadU64(self.registry)
            local count=api.EEex_Read32(self.registry_count)
            local found=false
            if array~=0 and integer(count,1,2000000) then
                for index=0,count-1 do if api.EEex_ReadU64(array+index*8)==res then found=true;break end end
            end
            if found then
                found=api.EEex_Read32(res+0x10)==0x3ED
                local ref=api.EEex_ReadU64(res+8)
                if ref==0 then found=false end
                if found then
                    for index=1,6 do
                        local byte=api.EEex_ReadU8(ref+index-1)
                        if byte>=65 and byte<=90 then byte=byte+32 end
                        if byte~=name:byte(index) then found=false;break end
                    end
                    if api.EEex_ReadU8(ref+6)~=0 then found=false end
                end
            end
            if not found then api.EEex_Write64(slot,0);res=0 end
        end
        if res~=0 and api.EEex_ReadU8(res+0x51)~=0 then
            local raw=api.EEex_ReadU64(res+0x40)
            if raw~=0 then
                local selected=api.EEex_ReadU64(slot+8)
                local view=raw
                if selected~=0 then
                    local expected,size=api.EEex_ReadU64(slot+16),api.EEex_ReadU64(slot+24)
                    local exact=api.EEex_Read32(res+0x48)==size
                    if exact then for offset=0,size-1 do
                        if api.EEex_ReadU8(raw+offset)~=api.EEex_ReadU8(expected+offset) then exact=false;break end
                    end end
                    if exact then view=selected else api.EEex_Write64(self.state+16,1) end
                end
                api.EEex_Write64(res+0x68,view)
                api.EEex_Write64(res+0x60,view+api.EEex_Read32(view+0x6A))
                api.EEex_Write64(res+0x58,view+api.EEex_Read32(view+0x64))
            end
        end
    end
end
function Runtime:reset()
    self.api.EEex_Write64(self.state,0);self.api.EEex_Write64(self.state+8,0)
    self:_refresh(true)
end
function Runtime:check(bundle)
    for _,site in ipairs(self.detours or {}) do
        assert(self.api.EEex_ReadU8(site.address)==0xE9 and
            site.address+5+self.api.EEex_Read32(site.address+1)==site.target,'CURSED_DETOUR_CHANGED')
    end
    assert(self.api.EEex_ReadU64(self.state+16)==0,'CURSED_RESOURCE_CHANGED')
    if bundle then
        local current=self.revisions[bundle.manifest.revision]
        local active=self.api.EEex_ReadU64(self.state)
        assert((current and self.ordered[active]==current and
            self.api.EEex_ReadU64(self.state+8)==self.api.EEex_GameState_GetGlobalInt('FLPR_SEED')) or
            (not current and active==0),'CURSED_CAMPAIGN_POLICY_MISMATCH')
    end
    return true
end
function Runtime:prepare_new(bundle,controller)
    self:reset()
    local current=self.revisions[bundle.manifest.revision]
    if not current then return true end
    assert(controller and controller.manifest and controller.manifest.revision==current.revision and
        self.api.EEex_GameState_GetGlobalInt('FLPR_FRESH')==1,'CURSED_CAPTURE_REQUIRED')
    local seed=controller.storage:get('flpr_seed')
    assert(integer(seed,1,RANGE),'CURSED_SEED_INVALID')
    for _,field in ipairs(current.policy.fields) do
        assert(self.api.EEex_GameState_GetGlobalInt(field.name)==(field.seed and seed or field.value),
            'CURSED_CAPTURE_MISMATCH')
    end
    local state=(seed-1+570)%RANGE+1
    for _,item in ipairs(current.items) do
        local count=#item.pointers;local cutoff=RANGE-RANGE%count
        repeat state=(state*16807)%2147483647 until state-1<cutoff
        local slot=self.slots[item.resref]
        self.api.EEex_Write64(slot+16,item.original);self.api.EEex_Write64(slot+24,item.size)
        self.api.EEex_Write64(slot+8,item.pointers[(state-1)%count+1])
    end
    self.api.EEex_Write64(self.state+8,seed)
    for index,row in ipairs(self.ordered) do if row==current then self.api.EEex_Write64(self.state,index) end end
    self:_refresh(false)
    self:check(bundle)
    return true
end
return Cur
