-- Pure, pinned-fixture GAM policy compiler. No engine access or hook install.
-- The caller supplies the immutable campaign revision and protected source's
-- sorted manifest index. Never accepts player-supplied assembly or identifiers.
-- The required GLOBALs are a table of 48-byte rows (zero-padded 32-byte name,
-- kind, value) that the caller copies to memory it owns and names in the scan
-- by replacing @POLICY_TABLE@. One loop reads them, so a source's scan code
-- stays small however many fields its policy has.
local TABLE_ROW = 48
local KIND = { value = 0, phase = 1, seed = 2, permanent_phase = 3 }
local function u64(value)
    local out = {}
    for _ = 1, 8 do out[#out+1] = string.char(value % 256); value = math.floor(value / 256) end
    return table.concat(out)
end
return function(revision, source_index, permanent, selected_global)
    assert(type(revision) == 'string' and #revision == 64 and not revision:find('[^0-9a-f]'),
        'CAMPAIGN_GATE_REVISION_INVALID')
    local stock_option=source_index=='more_spell_shield'
    local cursed_option=source_index=='cursed_disguise'
    assert(stock_option or cursed_option or (type(source_index) == 'number' and source_index == math.floor(source_index) and
        source_index >= 1 and source_index <= 4096), 'CAMPAIGN_GATE_SOURCE_INDEX_INVALID')
    assert((permanent==nil or permanent==true) and
        (not permanent or (not stock_option and not cursed_option)) and
        (selected_global==nil or (permanent and (selected_global=='FLPR_QM2_12' or
            selected_global=='FLPR_QM2_32' or selected_global=='FLPR_QM2_39' or selected_global=='FLPR_QM2_84'))),
        'CAMPAIGN_GATE_AUXILIARY_POLICY_INVALID')
    local fields = {{name='FLPR_STATE', value=2}, {name='FLPR_GUARD', value=1}}
    for index=1,16 do
        fields[#fields+1] = {name=string.format('FLPR_R%02d',index),
            value=tonumber(revision:sub((index-1)*4+1,index*4),16)}
    end
    if cursed_option then fields[#fields+1] = {name='FLPR_SEED', seed=true}
    elseif not stock_option then fields[#fields+1] = {name=string.format('FLPR_S%04d',source_index), phase=true} end
    if selected_global then fields[#fields+1]={name=selected_global,value=1} end
    local rows = {}
    for _, field in ipairs(fields) do
        -- Every field name starts with FLPR_; the scan skips other GLOBALs on that prefix.
        assert(field.name:sub(1, 5) == 'FLPR_' and #field.name <= 32, 'CAMPAIGN_GATE_POLICY_FIELD_INVALID')
        local kind = field.seed and KIND.seed or
            (field.phase and (permanent and KIND.permanent_phase or KIND.phase) or KIND.value)
        rows[#rows+1] = field.name..string.rep('\0', 32-#field.name)..u64(kind)..u64(field.value or 0)
    end
    -- RAX walks the GAM's 84-byte variable records (RCX of them), R10 is the
    -- member's buffer; R8/R9 hold the table row and its bit (hooks save both).
    -- A record matching a row sets that row's bit once; a second match is a
    -- duplicate. Phase: 1 or 6 protects, 3 means the original was removed
    -- (a permanent template keeps protecting); any other value refuses.
    local out = {[[
        test rcx, rcx
        je gate_capture_log
        gate_scan:
        cmp dword ptr ds:[rax], 0x52504C46
        jne gate_scan_next
        mov r8, @POLICY_TABLE@
        xor r9d, r9d
        gate_field:
        mov rdx, qword ptr ds:[r8]
        cmp qword ptr ds:[rax], rdx
        jne gate_field_next
        mov rdx, qword ptr ds:[r8+8]
        cmp qword ptr ds:[rax+8], rdx
        jne gate_field_next
        mov rdx, qword ptr ds:[r8+16]
        cmp qword ptr ds:[rax+16], rdx
        jne gate_field_next
        mov rdx, qword ptr ds:[r8+24]
        cmp qword ptr ds:[rax+24], rdx
        jne gate_field_next
        bt qword ptr ds:[r10+80], r9
        jc gate_policy_duplicate
        bts qword ptr ds:[r10+80], r9
        mov rdx, qword ptr ds:[r8+32]
        test rdx, rdx
        je gate_field_value
        cmp rdx, 2
        je gate_field_seed
        cmp dword ptr ds:[rax+0x28], 3
        jne gate_field_phase
        cmp rdx, 3
        je gate_policy_protect
        mov qword ptr ds:[r10+88], 2
        jmp gate_scan_next
        gate_field_phase:
        cmp dword ptr ds:[rax+0x28], 1
        je gate_policy_protect
        cmp dword ptr ds:[rax+0x28], 6
        jne gate_policy_mismatch
        gate_policy_protect:
        mov qword ptr ds:[r10+88], 1
        jmp gate_scan_next
        gate_field_seed:
        mov edx, dword ptr ds:[rax+0x28]
        cmp edx, 1
        jb gate_policy_mismatch
        cmp edx, 2147483646
        ja gate_policy_mismatch
        mov qword ptr ds:[r10+96], rdx
        jmp gate_scan_next
        gate_field_value:
        mov edx, dword ptr ds:[rax+0x28]
        cmp edx, dword ptr ds:[r8+40]
        jne gate_policy_mismatch
        jmp gate_scan_next
        gate_field_next:
        add r8, ]]..TABLE_ROW..[[

        inc r9
        cmp r9, ]]..#fields..[[

        jb gate_field
        gate_scan_next:
        add rax, 84
        dec rcx
        jne gate_scan
        cmp qword ptr ds:[r10+80], ]]..string.format('%.0f',2^#fields-1)..[[

        jne gate_capture_log]]}
    if stock_option or cursed_option then out[#out+1] = 'mov qword ptr ds:[r10+88], 1' end
    out[#out+1] = [[
        mov qword ptr ds:[r10+72], 0
        jmp gate_capture_log
        gate_policy_duplicate:
        mov qword ptr ds:[r10+72], 3
        jmp gate_capture_log
        gate_policy_mismatch:
        mov qword ptr ds:[r10+72], 4
        jmp gate_capture_log]]
    return {schema='flpr-campaign-gate-policy-v1', scan=table.concat(out,'\n'),
        table=table.concat(rows), table_row=TABLE_ROW,
        revision=revision, source_index=source_index, fields=fields, mask=2^#fields-1}
end
