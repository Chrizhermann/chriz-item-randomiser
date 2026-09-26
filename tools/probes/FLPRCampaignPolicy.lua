-- Pure, pinned-fixture GAM policy compiler. No engine access or hook install.
-- The caller supplies the immutable campaign revision and protected source's
-- sorted manifest index. Never accepts player-supplied assembly or identifiers.
return function(revision, source_index)
    assert(type(revision) == 'string' and #revision == 64 and not revision:find('[^0-9a-f]'),
        'CAMPAIGN_GATE_REVISION_INVALID')
    assert(type(source_index) == 'number' and source_index == math.floor(source_index) and
        source_index >= 1 and source_index <= 4096, 'CAMPAIGN_GATE_SOURCE_INDEX_INVALID')
    local fields = {{name='FLPR_STATE', value=2}, {name='FLPR_GUARD', value=1}}
    for index=1,16 do
        fields[#fields+1] = {name=string.format('FLPR_R%02d',index),
            value=tonumber(revision:sub((index-1)*4+1,index*4),16)}
    end
    fields[#fields+1] = {name=string.format('FLPR_S%04d',source_index), phase=true}
    local out = {'test rcx, rcx', 'je gate_capture_log', 'gate_scan:'}
    local function emit(line) out[#out+1]=line end
    local function qword(bytes)
        local pieces={}
        for index=8,1,-1 do pieces[#pieces+1]=string.format('%02x',bytes:byte(index)) end
        return '0x'..table.concat(pieces)
    end
    for index,field in ipairs(fields) do
        local next_label='gate_field_'..index..'_next'
        local name=field.name..string.rep('\0',32-#field.name)
        for offset=0,24,8 do
            emit('mov rdx, '..qword(name:sub(offset+1,offset+8)))
            emit('cmp qword ptr ds:[rax+'..offset..'], rdx')
            emit('jne '..next_label)
        end
        local bit=2^(index-1)
        emit('test qword ptr ds:[r10+80], '..string.format('%.0f',bit))
        emit('jne gate_policy_duplicate')
        emit('or qword ptr ds:[r10+80], '..string.format('%.0f',bit))
        if field.phase then
            -- Saved physical removal means no further suppression. In
            -- particular, a player's returned copy must not be deleted later.
            emit('cmp dword ptr ds:[rax+0x28], 3')
            emit('je gate_policy_removed')
            emit('cmp dword ptr ds:[rax+0x28], 1')
            emit('je gate_policy_protect')
            emit('cmp dword ptr ds:[rax+0x28], 6')
            emit('jne gate_policy_mismatch')
            emit('gate_policy_protect:')
            emit('mov qword ptr ds:[r10+88], 1')
            emit('jmp gate_scan_next')
            emit('gate_policy_removed:')
            emit('mov qword ptr ds:[r10+88], 2')
        else
            emit('cmp dword ptr ds:[rax+0x28], '..field.value)
            emit('jne gate_policy_mismatch')
        end
        emit('jmp gate_scan_next')
        emit(next_label..':')
    end
    emit('gate_scan_next:')
    emit('add rax, 84')
    emit('dec rcx')
    emit('jne gate_scan')
    emit('cmp qword ptr ds:[r10+80], '..string.format('%.0f',2^#fields-1))
    emit('jne gate_capture_log') -- status 1: required fields missing
    emit('mov qword ptr ds:[r10+72], 0')
    emit('jmp gate_capture_log')
    emit('gate_policy_duplicate:')
    emit('mov qword ptr ds:[r10+72], 3')
    emit('jmp gate_capture_log')
    emit('gate_policy_mismatch:')
    emit('mov qword ptr ds:[r10+72], 4')
    emit('jmp gate_capture_log')
    return {schema='flpr-campaign-gate-policy-v1', scan=table.concat(out,'\n'),
        revision=revision, source_index=source_index, fields=fields, mask=2^#fields-1}
end
