-- Pure assembly builder for configured container suppression.
-- No installation or engine access. Exposed separately for instruction emulation.
-- targets holds engine addresses found by FLPRSite.lua: item_resume (after the
-- container item address computation), container_return (return address of the
-- area loader's container call) and container_skip (the next item record).
return function(buffer, targets, policy)
    if policy ~= nil then
        assert(type(policy) == 'table' and policy.schema == 'flpr-campaign-gate-policy-v1'
            and type(policy.scan) == 'string', 'SOURCE_GATE_POLICY_INVALID')
    end
    local function target(name)
        local value = type(targets) == 'table' and targets[name]
        assert(type(value) == 'number' and value > 0, 'SOURCE_GATE_TARGET_MISSING:' .. name)
        return string.format('%.0f', value)
    end
    local save = [[
        pushfq
        push rax
        push rcx
        push rdx
        push r8
        push r9
        push r10
        push r11
        mov r10, @BUFFER@
        lock inc qword ptr ds:[r10+24]
    ]]
    local restore = [[
        mov r10, @BUFFER@
        lock dec qword ptr ds:[r10+24]
        pop r11
        pop r10
        pop r9
        pop r8
        pop rdx
        pop rcx
        pop rax
        popfq
    ]]
    -- Allocating a log row precedes any destructive decision. Overflow prevents
    -- suppression, invalidates the snapshot and leaves the record to the engine.
    local allocate = [[
        mov r11, 1
        lock xadd qword ptr ds:[r10+8], r11
        cmp r11, 2048
        jae gate_overflow
        shl r11, 7
        add r11, r10
        add r11, 128
        mov rax, qword ptr ds:[r10+40]
        mov qword ptr ds:[r11+8], rax
    ]]
    if policy then
        -- Runtime protection must outlive diagnostic capacity. Overflow is a
        -- permanent observation failure, but the exact selected original still
        -- takes the guarded path. Reserve a separate, never-ringed witness row.
        -- Campaign callers allocate header + 2049 rows, not the probe's 2048.
        allocate = [[
            mov r11, 1
            lock xadd qword ptr ds:[r10+8], r11
            cmp r11, 2048
            jb gate_row_ready
            mov qword ptr ds:[r10+16], 1
            mov r11, 2047
            gate_row_ready:
            shl r11, 7
            add r11, r10
            add r11, 128
        ]]
        for offset=0,120,8 do
            allocate = allocate .. 'mov qword ptr ds:[r11+'..offset..'], 0\n'
        end
        allocate = allocate .. [[
            mov rax, qword ptr ds:[r10+40]
            mov qword ptr ds:[r11+8], rax
        ]]
    end
    local finish = [[
        jmp gate_done
        gate_overflow:
        mov qword ptr ds:[r10+16], 1
        mov qword ptr ds:[r10+32], 0
        gate_done:
    ]] .. restore
    local enter = save .. [[
        mov qword ptr ds:[r10+32], 0
        inc qword ptr ds:[r10+40]
        mov qword ptr ds:[r10+48], rdx
        mov eax, r8d
        mov qword ptr ds:[r10+56], rax
        mov qword ptr ds:[r10+64], 0
        mov qword ptr ds:[r10+72], 0
        mov qword ptr ds:[r10+80], 0
        mov qword ptr ds:[r10+88], 0
        mov qword ptr ds:[r10+120], 0
        cmp qword ptr ds:[r10], 1
        jne gate_done
    ]] .. allocate .. [[
        mov qword ptr ds:[r11+32], rdx
        mov eax, r8d
        mov qword ptr ds:[r11+40], rax
        mov qword ptr ds:[r11], 1
    ]] .. finish
    local scan = policy and policy.scan or [[
        gate_scan:
        mov r11, 0x5f31555f52504c46
        cmp qword ptr ds:[rax], r11
        jne gate_scan_next
        cmp qword ptr ds:[rax+8], 0x45444f4d
        jne gate_scan_next
        cmp qword ptr ds:[rax+16], 0
        jne gate_scan_next
        cmp qword ptr ds:[rax+24], 0
        jne gate_scan_next
        inc qword ptr ds:[r10+80]
        mov edx, dword ptr ds:[rax+0x28]
        mov qword ptr ds:[r10+88], rdx
        gate_scan_next:
        add rax, 84
        dec rcx
        jne gate_scan
        cmp qword ptr ds:[r10+80], 0
        je gate_capture_log
        mov qword ptr ds:[r10+72], 3
        cmp qword ptr ds:[r10+80], 1
        jne gate_capture_log
        mov qword ptr ds:[r10+72], 4
        cmp qword ptr ds:[r10+88], 1
        je gate_capture_valid
        cmp qword ptr ds:[r10+88], 2
        jne gate_capture_log
        gate_capture_valid:
        mov qword ptr ds:[r10+72], 0
]]
    -- The GAM being read: GAME V2.0 (BG2:EE, EET) or GAME V1.1 (BG:EE), the
    -- same 0xB4-byte header and 84-byte variable records.
    local capture = save .. [[
        mov qword ptr ds:[r10+32], 0
        mov qword ptr ds:[r10+72], 5
        cmp qword ptr ds:[r10], 1
        jne gate_done
        cmp r14, qword ptr ds:[r10+48]
        jne gate_capture_log
        mov qword ptr ds:[r10+72], 2
        cmp qword ptr ds:[r10+56], 0xB4
        jb gate_capture_log
        cmp qword ptr ds:[r10+56], 0x1000000
        ja gate_capture_log
        test r14, r14
        je gate_capture_log
        mov rax, 0x302e3256454d4147
        cmp qword ptr ds:[r14], rax
        je gate_capture_version
        mov rax, 0x312e3156454d4147
        cmp qword ptr ds:[r14], rax
        jne gate_capture_log
        gate_capture_version:
        mov eax, dword ptr ds:[r14+0x38]
        cmp eax, 0xB4
        jb gate_capture_log
        mov ecx, dword ptr ds:[r14+0x3C]
        cmp ecx, 16384
        ja gate_capture_log
        imul r11, rcx, 84
        add r11, rax
        cmp r11, qword ptr ds:[r10+56]
        ja gate_capture_log
        mov qword ptr ds:[r10+64], rcx
        mov qword ptr ds:[r10+72], 1
        add rax, r14
        test rcx, rcx
        je gate_capture_log
]] .. scan .. '\n' .. [[
        gate_capture_log:
    ]] .. allocate .. [[
        mov rax, qword ptr ds:[r10+48]
        mov qword ptr ds:[r11+32], rax
        mov rax, qword ptr ds:[r10+56]
        mov qword ptr ds:[r11+40], rax
        mov rax, qword ptr ds:[r10+64]
        mov qword ptr ds:[r11+48], rax
        mov rax, qword ptr ds:[r10+72]
        mov qword ptr ds:[r11+24], rax
        mov rax, qword ptr ds:[r10+80]
        mov qword ptr ds:[r11+112], rax
        mov rax, qword ptr ds:[r10+88]
        mov qword ptr ds:[r11+120], rax
        cmp qword ptr ds:[r10+16], 0
        jne gate_capture_publish
        cmp qword ptr ds:[r10+72], 0
        jne gate_capture_publish
        mov qword ptr ds:[r10+32], rax
        mov qword ptr ds:[r11+16], rax
        gate_capture_publish:
        mov qword ptr ds:[r11], 2
        mov qword ptr ds:[r10+48], 0
        mov qword ptr ds:[r10+56], 0
    ]] .. finish
    local item = save .. [[
        cmp qword ptr ds:[r10], 1
        jne gate_done
        mov rax, qword ptr ss:[rbp-0x60]
        test rax, rax
        je gate_done
        mov r11, 0x315552504c46
        cmp qword ptr ds:[rax+0x204], r11
        jne gate_done
    ]] .. allocate .. [[
        mov rax, qword ptr ds:[r10+32]
        mov qword ptr ds:[r11+16], rax
        mov rax, qword ptr ss:[rbp-0x60]
        mov qword ptr ds:[r11+32], rax
        mov rax, qword ptr ss:[rbp-0x68]
        mov qword ptr ds:[r11+40], rax
        mov eax, r15d
        mov qword ptr ds:[r11+48], rax
        mov rax, qword ptr ds:[r14]
        mov qword ptr ds:[r11+56], rax
        mov rax, qword ptr ds:[r14+8]
        mov qword ptr ds:[r11+64], rax
        mov rax, qword ptr ds:[r14+16]
        mov qword ptr ds:[r11+72], rax
        mov rax, qword ptr ds:[r14+24]
        mov qword ptr ds:[r11+80], rax
        mov rax, qword ptr ds:[r14+0x40]
        mov qword ptr ds:[r11+112], rax
        mov rax, qword ptr ds:[r14+0x20]
        mov qword ptr ds:[r11+120], rax
        mov eax, r15d
        imul rax, rax, 20
        add rax, qword ptr ss:[rbp-0x78]
        mov rdx, qword ptr ds:[rax]
        mov qword ptr ds:[r11+88], rdx
        mov rcx, qword ptr ds:[rax+8]
        mov qword ptr ds:[r11+96], rcx
        mov edx, dword ptr ds:[rax+16]
        mov dword ptr ds:[r11+104], edx
        cmp qword ptr ds:[r10+32], 1
        jne gate_item_publish
        cmp qword ptr ds:[r10+16], 0
        jne gate_item_publish
        cmp qword ptr ss:[rbp-0x68], 0
        je gate_item_publish
        mov rdx, 0x41315552504c46
        cmp qword ptr ds:[r14], rdx
        jne gate_item_publish
        mov rdx, 0x314952504c46
        cmp qword ptr ds:[rax], rdx
        jne gate_item_publish
        mov qword ptr ds:[r11+24], 2
        cmp qword ptr ds:[r14+8], 0
        jne gate_item_publish
        cmp qword ptr ds:[r14+16], 0
        jne gate_item_publish
        cmp qword ptr ds:[r14+24], 0
        jne gate_item_publish
        cmp r15d, 1
        jne gate_item_publish
        mov rdx, 0x200000001
        cmp qword ptr ds:[r14+0x40], rdx
        jne gate_item_publish
        mov rdx, 0x4028a0474
        cmp qword ptr ds:[r14+0x20], rdx
        jne gate_item_publish
        test rcx, rcx
        jne gate_item_publish
        cmp dword ptr ds:[rax+16], 1
        jne gate_item_publish
        mov qword ptr ds:[r11+24], 3
        cmp qword ptr ds:[r10+120], 0
        jne gate_item_publish
        inc qword ptr ds:[r10+120]
        inc qword ptr ds:[r10+96]
        mov qword ptr ds:[r11+24], 1
        mov qword ptr ds:[r11], 3
    ]] .. restore .. [[
        #MANUAL_HOOK_EXIT(1)
        jmp @SKIP@
        gate_item_publish:
        mov qword ptr ds:[r11], 3
    ]] .. finish
    if policy then
        -- These are exact, internal template fragments, not user replacements.
        -- Refuse a refactor that would silently lose one of the policy changes.
        local function replace_once(code, before, after)
            local first,last=code:find(before,1,true)
            assert(first and not code:find(before,last+1,true), 'CAMPAIGN_GATE_TEMPLATE_CHANGED')
            return code:sub(1,first-1)..after..code:sub(last+1)
        end
        enter=replace_once(enter,'mov qword ptr ds:[r10+120], 0',
            'mov qword ptr ds:[r10+120], 0\nmov qword ptr ds:[r10+112], 0')
        capture=replace_once(capture,[[        cmp qword ptr ds:[r10+16], 0
        jne gate_capture_publish
]],'')
        item=replace_once(item,[[        cmp qword ptr ds:[r10+16], 0
        jne gate_item_publish
]],'')
        local witness=''
        for offset=0,120,8 do
            witness=witness..'mov rax, qword ptr ds:[r11+'..offset..']\n'..
                'mov qword ptr ds:[r10+'..(128+2048*128+offset)..'], rax\n'
        end
        witness=witness..'mov qword ptr ds:[r10+112], 1\n'
        item=replace_once(item,[[        mov qword ptr ds:[r11+24], 1
        mov qword ptr ds:[r11], 3
]],[[        mov qword ptr ds:[r11+24], 1
        mov qword ptr ds:[r11], 3
]]..witness)
        if policy.source then
            local s=policy.source
            assert(type(s.area)=='string' and #s.area>=1 and #s.area<=8 and
                not s.area:find('[^a-zA-Z0-9#_%-]') and type(s.name)=='string' and #s.name==32 and
                type(s.payload)=='string' and #s.payload==20 and type(s.inventory)=='string' and
                #s.inventory==8 and type(s.position)=='string' and #s.position==8 and
                type(s.index)=='number' and s.index==math.floor(s.index) and s.index>=0 and
                s.index<=2147483646,'CAMPAIGN_GATE_SOURCE_BINDING_INVALID')
            local function literal(bytes)
                local out={}
                for i=#bytes,1,-1 do out[#out+1]=string.format('%02x',bytes:byte(i)) end
                return '0x'..table.concat(out)
            end
            item=replace_once(item,'mov r11, 0x315552504c46',
                'mov r11, '..literal(s.area:upper()..string.rep('\0',8-#s.area)))
            item=replace_once(item,'mov rdx, 0x41315552504c46','mov rdx, '..literal(s.name:sub(1,8)))
            for offset=8,24,8 do
                item=replace_once(item,'cmp qword ptr ds:[r14+'..offset..'], 0',
                    'mov rdx, '..literal(s.name:sub(offset+1,offset+8))..'\n'..
                    'cmp qword ptr ds:[r14+'..offset..'], rdx')
            end
            item=replace_once(item,'mov rdx, 0x314952504c46','mov rdx, '..literal(s.payload:sub(1,8)))
            item=replace_once(item,'cmp r15d, 1','cmp r15d, '..string.format('%.0f',s.index))
            item=replace_once(item,'mov rdx, 0x200000001','mov rdx, '..literal(s.inventory))
            item=replace_once(item,'mov rdx, 0x4028a0474','mov rdx, '..literal(s.position))
            item=replace_once(item,'test rcx, rcx',
                'mov rdx, '..literal(s.payload:sub(9,16))..'\ncmp rcx, rdx')
            item=replace_once(item,'cmp dword ptr ds:[rax+16], 1',
                'cmp dword ptr ds:[rax+16], '..literal(s.payload:sub(17,20)))
            if s.replacement_pointer then
                assert(type(s.replacement_pointer)=='number' and s.replacement_pointer>0,
                    'CAMPAIGN_GATE_REPLACEMENT_INVALID')
                item=replace_once(item,'#MANUAL_HOOK_EXIT(1)\n        jmp @SKIP@',
                    'mov r13, '..string.format('%.0f',s.replacement_pointer)..'\n'..
                    '#MANUAL_HOOK_EXIT(1)\n        jmp '..target('item_resume'))
            end
        end
    end
    local area_enter,area_exit
    if policy and policy.provenance then
        local p=policy.provenance
        local function natural(v) return type(v)=='number' and v==math.floor(v) and v>0 end
        assert(natural(p.expected) and natural(p.length) and p.length>=256 and p.length<=2147483647
            and natural(p.container_offset) and p.container_offset+192<=p.length
            and natural(p.item_offset) and p.item_offset+((policy.source and policy.source.index or 3)+1)*20<=p.length,
            'CAMPAIGN_GATE_PROVENANCE_INVALID')
        -- 64 independent invocation slots, after the dedicated removal witness.
        -- Match the constructor's saved caller frame AND its exact call site.
        -- Raw pointers alone can be recycled after Unmarshal has returned.
        local context=128+2049*128
        local first=context+32
        local last=first+64*64
        local clear=[[
            lea r11, [r10+@FIRST@]
            gate_clear_loop:
            cmp qword ptr [r11], rbp
            jne gate_clear_next
            mov qword ptr [r11+24], 0
            mov qword ptr [r11], 0
            gate_clear_next:
            add r11, 64
            lea rax, [r10+@LAST@]
            cmp r11, rax
            jb gate_clear_loop
        ]]
        area_enter=save..clear..[[
            inc qword ptr [r10+@CONTEXT@]
            cmp qword ptr [r10], 1
            jne gate_area_done
            cmp r8d, @LENGTH@
            jne gate_area_done
            test rdx, rdx
            je gate_area_done
            test rcx, rcx
            je gate_area_done
            mov r8, @EXPECTED@
            xor r11d, r11d
            gate_compare_loop:
            mov rax, qword ptr [rdx+r11]
            cmp rax, qword ptr [r8+r11]
            jne gate_area_done
            add r11, 8
            cmp r11, @QBYTES@
            jb gate_compare_loop
        ]]
        for offset=math.floor(p.length/8)*8,p.length-1 do
            area_enter=area_enter..'mov al, byte ptr [rdx+'..offset..']\n'..
                'cmp al, byte ptr [r8+'..offset..']\njne gate_area_done\n'
        end
        area_enter=area_enter..[[
            lea r11, [r10+@FIRST@]
            gate_claim_loop:
            xor eax, eax
            lock cmpxchg qword ptr [r11], rbp
            je gate_claimed
            add r11, 64
            lea rax, [r10+@LAST@]
            cmp r11, rax
            jb gate_claim_loop
            inc qword ptr [r10+@CONTEXT_OVERFLOW@]
            mov qword ptr [r10+16], 1
            jmp gate_area_done
            gate_claimed:
            mov qword ptr [r11+24], 0
            mov qword ptr [r11+8], rcx
            mov qword ptr [r11+16], rdx
            mov rax, qword ptr [r10+40]
            mov qword ptr [r11+32], rax
            lea rax, [rdx+@ITEMS@]
            mov qword ptr [r11+40], rax
            lea rax, [rdx+@CONTAINERS@]
            mov qword ptr [r11+48], rax
            mov qword ptr [r11+24], 1
            inc qword ptr [r10+@CONTEXT_ACCEPTED@]
            gate_area_done:
        ]]..restore
        area_exit=save..clear..restore
        local check=[[
            mov rdx, @CALL_RETURN@
            cmp qword ptr [rbp+0xB8], rdx
            jne gate_item_publish
            mov r8, qword ptr [rbp+0xB0]
            lea r9, [r10+@FIRST@]
            gate_context_loop:
            cmp qword ptr [r9], r8
            je gate_context_found
            add r9, 64
            lea rdx, [r10+@LAST@]
            cmp r9, rdx
            jb gate_context_loop
            jmp gate_item_publish
            gate_context_found:
            cmp qword ptr [r9+24], 1
            jne gate_item_publish
            mov rdx, qword ptr [r10+40]
            cmp qword ptr [r9+32], rdx
            jne gate_item_publish
            mov rdx, qword ptr [rbp-0x60]
            cmp qword ptr [r9+8], rdx
            jne gate_item_publish
            mov rdx, qword ptr [rbp-0x78]
            cmp qword ptr [r9+40], rdx
            jne gate_item_publish
            cmp qword ptr [r9+48], r14
            jne gate_item_publish
        ]]
        local marker='        cmp qword ptr ss:[rbp-0x68], 0'
        local pos=assert(item:find(marker,1,true),'CAMPAIGN_GATE_TEMPLATE_CHANGED')
        item=item:sub(1,pos-1)..check..item:sub(pos)
        local substitutions={FIRST=first,LAST=last,CONTEXT=context,
            CONTEXT_OVERFLOW=context+8,CONTEXT_ACCEPTED=context+16,
            LENGTH=p.length,QBYTES=math.floor(p.length/8)*8,EXPECTED=p.expected,
            ITEMS=p.item_offset,CONTAINERS=p.container_offset,CALL_RETURN=tonumber(target('container_return'))}
        local function substitute(code)
            for key,value in pairs(substitutions) do code=code:gsub('@'..key..'@',string.format('%.0f',value)) end
            return code
        end
        area_enter,area_exit,item=substitute(area_enter),substitute(area_exit),substitute(item)
    end
    local actor
    if policy and policy.creature then
        local c=policy.creature
        assert(policy.provenance and type(c.expected)=='number' and type(c.removed)=='number' and
            type(c.length)=='number' and c.length>=0x2D4 and type(c.item_offset)=='number' and
            c.item_offset>=0x2D4 and c.item_offset+20<=c.length,
            'CAMPAIGN_GATE_CREATURE_INVALID')
        -- At both ARE actor constructor calls: rbp is the ARE load frame,
        -- r14 its CGameArea, r13 the authored actor, rdx/r8 the CRE bytes/size,
        -- and rcx the allocated sprite. Supply private bytes before any item is
        -- constructed; never write the engine's shared resource buffer.
        local first=128+2049*128+32
        actor=save..[[
            cmp qword ptr [r10], 1
            jne gate_actor_done
            cmp qword ptr [r10+32], 1
            jne gate_actor_done
            lea r9, [r10+@FIRST@]
            gate_actor_context:
            cmp qword ptr [r9], rbp
            je gate_actor_found
            add r9, 64
            lea rax, [r10+@LAST@]
            cmp r9, rax
            jb gate_actor_context
            jmp gate_actor_done
            gate_actor_found:
            cmp qword ptr [r9+24], 1
            jne gate_actor_done
            cmp qword ptr [r9+8], r14
            jne gate_actor_done
            cmp qword ptr [r9+48], r13
            jne gate_actor_done
            mov rax, qword ptr [r10+40]
            cmp qword ptr [r9+32], rax
            jne gate_actor_done
        ]]..allocate..[[
            mov qword ptr [r11+32], r14
            mov qword ptr [r11+40], rcx
            mov qword ptr [r11+16], 1
            mov qword ptr [r11+24], 2
            cmp r8d, @LENGTH@
            jne gate_actor_publish
            test rdx, rdx
            je gate_actor_publish
            mov r8, @EXPECTED@
            xor r9d, r9d
            gate_actor_compare:
            mov rax, qword ptr [rdx+r9]
            cmp rax, qword ptr [r8+r9]
            jne gate_actor_publish
            add r9, 8
            cmp r9, @QBYTES@
            jb gate_actor_compare
        ]]
        for offset=math.floor(c.length/8)*8,c.length-1 do
            actor=actor..'mov al, byte ptr [rdx+'..offset..']\ncmp al, byte ptr [r8+'..offset..
                ']\njne gate_actor_publish\n'
        end
        actor=actor..'mov qword ptr [r11+48], '..c.index..'\n'
        for offset=0,24,8 do
            actor=actor..'mov rax, qword ptr [rdx+'..(0x280+offset)..']\n'..
                'mov qword ptr [r11+'..(56+offset)..'], rax\n'
        end
        for offset=0,8,8 do
            actor=actor..'mov rax, qword ptr [rdx+'..(c.item_offset+offset)..']\n'..
                'mov qword ptr [r11+'..(88+offset)..'], rax\n'
        end
        actor=actor..'mov eax, dword ptr [rdx+'..(c.item_offset+16)..']\n'..
            'mov dword ptr [r11+104], eax\n'..[[
            mov qword ptr [r11+24], 3
            cmp qword ptr [r10+120], 0
            jne gate_actor_publish
            inc qword ptr [r10+120]
            inc qword ptr [r10+96]
            mov qword ptr [r11+24], 1
            mov qword ptr [r11], 3
        ]]
        for offset=0,120,8 do
            actor=actor..'mov rax, qword ptr [r11+'..offset..']\n'..
                'mov qword ptr [r10+'..(128+2048*128+offset)..'], rax\n'
        end
        actor=actor..[[
            mov qword ptr [r10+112], 1
            mov rax, @REMOVED@
            mov qword ptr [rsp+32], rax
            gate_actor_publish:
            mov qword ptr [r11], 3
            gate_actor_done:
        ]]..restore
        for key,value in pairs({FIRST=first,LAST=first+64*64,LENGTH=c.length,
            EXPECTED=c.expected,REMOVED=c.removed,QBYTES=math.floor(c.length/8)*8}) do
            actor=actor:gsub('@'..key..'@',string.format('%.0f',value))
        end
        item='' -- A creature member never intercepts container construction.
    end
    local store,saved_store,store_done
    if policy and policy.store then
        local s=policy.store
        local function literal(bytes)
            local out={}
            for i=#bytes,1,-1 do out[#out+1]=string.format('%02x',bytes:byte(i)) end
            return '0x'..table.concat(out)
        end
        local ref=s.ref:upper()..string.rep('\0',8-#s.ref)
        -- CResRef copied by SetResRef can retain case; saved member names are
        -- uppercase. Fold both forms, including their zero padding, equally.
        local match_ref=[[
            mov r9, 0x2020202020202020
            or rax, r9
            mov r8, @REF@
            or r8, r9
            cmp rax, r8
        ]]
        saved_store=save..[[
            test r14d, r14d
            jne gate_done
            cmp word ptr [rbx+0x10], 0x3F6
            jne gate_done
            mov rax, qword ptr [rbp-0x20]
        ]]..match_ref..[[
            jne gate_done
            mov qword ptr [r10+@CONTEXT@], 1
            gate_done:
        ]]..restore
        store=save..[[
            cmp qword ptr [r10], 1
            jne gate_done
            cmp qword ptr [r10+32], 1
            jne gate_done
            cmp qword ptr [r10+@CONTEXT@], 0
            jne gate_done
            mov rax, qword ptr [rbx]
        ]]..match_ref..[[
            jne gate_done
            cmp qword ptr [r10+120], 0
            jne gate_done
        ]]..allocate..[[
            mov qword ptr [r11+16], 1
            mov qword ptr [r11+24], 2
            mov qword ptr [r11+32], rbx
            mov rdx, qword ptr [rsp+0x60]
            mov qword ptr [r11+40], rdx
            mov qword ptr [r11+48], @INDEX@
            cmp dword ptr [rdx+0x48], @LENGTH@
            jne gate_publish
            mov r8, @EXPECTED@
            xor r9d, r9d
            gate_compare:
            mov rax, qword ptr [rdi+r9]
            cmp rax, qword ptr [r8+r9]
            jne gate_publish
            add r9, 8
            cmp r9, @QBYTES@
            jb gate_compare
        ]]
        for offset=math.floor(s.length/8)*8,s.length-1 do
            store=store..'mov al, byte ptr [rdi+'..offset..']\ncmp al, byte ptr [r8+'..offset..
                ']\njne gate_publish\n'
        end
        store=store..'mov rax, '..literal(ref)..'\nmov qword ptr [r11+56], rax\n'
        for offset=0,8,8 do
            store=store..'mov rax, '..literal(s.payload:sub(offset+1,offset+8))..
                '\nmov qword ptr [r11+'..(88+offset)..'], rax\n'
        end
        store=store..'mov eax, '..literal(s.payload:sub(17,20))..'\nmov dword ptr [r11+104], eax\n'..
            'mov rax, '..literal(s.payload:sub(21,28))..'\nmov qword ptr [r11+112], rax\n'..[[
            mov qword ptr [r10+@PENDING@], rbx
            mov qword ptr [r10+@ROW@], r11
            mov qword ptr [r11+24], 1
            mov rdi, @REMOVED@
            gate_publish:
            mov qword ptr [r11], 3
            gate_done:
        ]]..restore
        -- The engine owns the newly constructed list. Check the actual result
        -- before publishing a removal receipt; never retain its pointers for Lua.
        store_done=save..[[
            cmp qword ptr [r10+@PENDING@], rbx
            jne gate_done
            mov qword ptr [r10+@PENDING@], 0
            mov r11, qword ptr [r10+@ROW@]
            mov qword ptr [r11+24], 4
            cmp dword ptr [rbx+0xB8], @COUNT@
            jne gate_done
            mov r8, qword ptr [rbx+0xA8]
            mov r9, @REMOVED_ITEMS@
            mov ecx, @COUNT@
            test ecx, ecx
            je gate_tail
            gate_rows:
            test r8, r8
            je gate_done
            mov rdx, qword ptr [r8+0x10]
        ]]
        for offset=0,16,8 do
            store_done=store_done..'mov rax, qword ptr [rdx+'..offset..']\n'..
                'cmp rax, qword ptr [r9+'..offset..']\njne gate_done\n'
        end
        store_done=store_done..[[
            mov eax, dword ptr [rdx+24]
            cmp eax, dword ptr [r9+24]
            jne gate_done
            mov r8, qword ptr [r8]
            add r9, 28
            dec ecx
            jne gate_rows
            gate_tail:
            test r8, r8
            jne gate_done
            mov qword ptr [r11+24], 1
            mov rax, qword ptr [r10+40]
            mov qword ptr [r10+@COMPLETE@], rax
            inc qword ptr [r10+120]
            inc qword ptr [r10+96]
        ]]
        for offset=0,120,8 do
            store_done=store_done..'mov rax, qword ptr [r11+'..offset..']\n'..
                'mov qword ptr [r10+'..(128+2048*128+offset)..'], rax\n'
        end
        store_done=store_done..'mov qword ptr [r10+112], 1\ngate_done:\n'..restore
        for key,value in pairs({REF=literal(ref),CONTEXT=s.context,PENDING=s.context+8,
            ROW=s.context+24,COMPLETE=s.context+16,INDEX=s.index,LENGTH=s.length,EXPECTED=s.expected,
            REMOVED=s.removed,REMOVED_ITEMS=s.removed+s.items,COUNT=s.count,QBYTES=math.floor(s.length/8)*8}) do
            local replacement=type(value)=='number' and string.format('%.0f',value) or value
            store=store:gsub('@'..key..'@',replacement)
            saved_store=saved_store:gsub('@'..key..'@',replacement)
            store_done=store_done:gsub('@'..key..'@',replacement)
        end
        item=''
    end
    local function expand(code, prefix)
        return (code:gsub('@BUFFER@', string.format('%.0f', buffer))
            :gsub('@SKIP@', function() return target('container_skip') end)
            :gsub('gate_', prefix .. '_'))
    end
    return {enter=expand(enter,'gate_enter'), capture=expand(capture,'gate_capture'),
        item=expand(item,'gate_item'), area_enter=area_enter and expand(area_enter,'gate_area_enter'),
        area_exit=area_exit and expand(area_exit,'gate_area_exit'),
        actor=actor and expand(actor,'gate_actor'), store=store and expand(store,'gate_store'),
        saved_store=saved_store and expand(saved_store,'gate_saved_store'),
        store_done=store_done and expand(store_done,'gate_store_done')}
end
