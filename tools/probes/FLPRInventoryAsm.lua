-- Rewrite one pristine CRE/STO into a private buffer before the engine reads it.
-- Each selected record uses its campaign policy; descending edits keep original
-- record indexes valid. Shared source files and saved inventories stay untouched.
return function(group)
    local out={}
    local function emit(s) out[#out+1]=s end
    local function number(n) return string.format('%.0f',n) end
    local cre=group.kind=='cre'
    assert(cre or group.kind=='sto','INVENTORY_KIND_INVALID')
    local stride=cre and 20 or 28
    local count_offset=cre and 0x2C0 or 0x38
    local input=cre and 'rdx' or 'rdi'
    -- Preserve all registers, except the input pointer deliberately handed to
    -- the engine. No call into Lua is made during resource construction.
    emit('pushfq\npush rax\npush rcx\npush rdx\npush rsi\npush rdi\npush r8\npush r9\npush r10\npush r11')
    emit('mov r9, '..input)
    emit('test r9, r9\nje gate_inventory_done')
    if group.spell_shield then
        assert(not cre,'INVENTORY_STOCK_OPTION_INVALID')
        emit('mov r10, '..number(group.spell_shield.buffer))
        emit('cmp qword ptr ds:[r10+32], 1\njne gate_inventory_done')
        emit('cmp qword ptr ds:[r10+'..group.spell_shield.context..'], 0\njne gate_inventory_done')
    end
    if cre then
        emit('cmp r8d, '..group.length..'\njne gate_inventory_done')
    else
        -- SetResRef's demanded CRes is at original RSP+32. Validate length
        -- before comparing bytes, including when another shop is smaller.
        emit('mov rax, qword ptr ss:[rsp+112]\ncmp dword ptr ds:[rax+72], '..group.length..'\njne gate_inventory_done')
        local ref=group.ref:upper()..string.rep('\0',8-#group.ref)
        local hex={};for n=8,1,-1 do hex[#hex+1]=string.format('%02x',ref:byte(n)) end
        emit('mov rax, qword ptr ds:[rbx]\nmov r10, 0x2020202020202020\nor rax, r10\nmov r11, 0x'..table.concat(hex)..'\nor r11, r10\ncmp rax, r11\njne gate_inventory_done')
    end
    emit('mov rsi, r9\nmov rdi, '..number(group.expected)..'\nmov ecx, '..group.length..'\ncld\nrepe cmpsb\njne gate_inventory_done')
    emit('mov rsi, '..(group.spell_shield and number(group.spell_shield.effective) or 'r9')..
        '\nmov rdi, '..number(group.output)..'\nmov ecx, '..
        (group.spell_shield and group.spell_shield.length or group.length)..'\nrep movsb')
    emit('mov r8, '..number(group.output))
    local previous=math.huge
    for i,row in ipairs(group.edits) do
        assert(row.index<previous,'INVENTORY_EDITS_NOT_DESCENDING');previous=row.index
        local next_label='gate_inventory_next_'..i
        local offset=group.item_offset+row.index*stride
        emit('mov r10, '..number(row.buffer))
        emit('cmp qword ptr ds:[r10+32], 1\njne '..next_label)
        if not cre then emit('cmp qword ptr ds:[r10+'..row.context..'], 0\njne '..next_label) end
        if row.replacement and row.replacement~='blank' then
            local bytes=row.replacement..string.rep('\0',8-#row.replacement)
            local hex={}
            for n=8,1,-1 do hex[#hex+1]=string.format('%02x',bytes:byte(n)) end
            emit('mov rax, 0x'..table.concat(hex)..'\nmov qword ptr ds:[r8+'..offset..'], rax')
        else
            emit('mov ecx, dword ptr ds:[r8+'..count_offset..']\nsub ecx, '..(row.index+1))
            emit('imul ecx, ecx, '..stride..'\nlea rdi, ds:[r8+'..offset..']\nlea rsi, ds:[rdi+'..stride..']\nrep movsb')
            emit('xor eax, eax\nmov ecx, '..stride..'\nrep stosb\ndec dword ptr ds:[r8+'..count_offset..']')
            if cre then
                emit('lea rdi, ds:[r8+'..group.slot_offset..']\nmov ecx, 38')
                emit('gate_inventory_slot_'..i..':\nmovzx eax, word ptr ds:[rdi]\ncmp eax, 65535\nje gate_inventory_slot_next_'..i)
                emit('cmp eax, '..row.index..'\njb gate_inventory_slot_next_'..i..'\nja gate_inventory_shift_'..i)
                emit('mov eax, 65535\njmp gate_inventory_write_'..i)
                emit('gate_inventory_shift_'..i..':\ndec eax\ngate_inventory_write_'..i..':\nmov word ptr ds:[rdi], ax')
                emit('gate_inventory_slot_next_'..i..':\nadd rdi, 2\ndec ecx\njne gate_inventory_slot_'..i)
            end
        end
        emit(next_label..':')
    end
    if cre then
        -- If the equipped weapon was removed, select another weapon or fists.
        emit('movzx eax, word ptr ds:[r8+'..(group.slot_offset+76)..']\ncmp eax, 4\njae gate_inventory_handoff')
        emit('lea rdi, ds:[r8+'..(group.slot_offset+18)..']\ncmp word ptr ds:[rdi+rax*2], 65535\njne gate_inventory_handoff')
        emit('xor ecx, ecx\ngate_inventory_weapon:\ncmp word ptr ds:[rdi+rcx*2], 65535\njne gate_inventory_weapon_found\ninc ecx\ncmp ecx, 4\njb gate_inventory_weapon')
        emit('mov ecx, 1000\ngate_inventory_weapon_found:\nmov word ptr ds:[r8+'..(group.slot_offset+76)..'], cx\nmov word ptr ds:[r8+'..(group.slot_offset+78)..'], 0')
    end
    emit('gate_inventory_handoff:')
    -- Saved register slots: RDX +48; RDI +32 (after ten pushes).
    emit('mov qword ptr ss:[rsp+'..(cre and 48 or 32)..'], r8')
    emit('gate_inventory_done:\npop r11\npop r10\npop r9\npop r8\npop rdi\npop rsi\npop rdx\npop rcx\npop rax\npopfq')
    return table.concat(out,'\n')
end
