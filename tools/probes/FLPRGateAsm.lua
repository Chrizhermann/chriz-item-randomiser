-- Pure assembly builder for the opt-in, synthetic FLPRU1 suppression probe.
-- No installation or engine access. Exposed separately for instruction emulation.
return function(buffer, image_base)
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
        jne gate_capture_log
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
    local function expand(code, prefix)
        return (code:gsub('@BUFFER@', string.format('%.0f', buffer))
            :gsub('@SKIP@', string.format('%.0f', image_base+0x1906FC))
            :gsub('gate_', prefix .. '_'))
    end
    return {enter=expand(enter,'gate_enter'), capture=expand(capture,'gate_capture'),
        item=expand(item,'gate_item')}
end
