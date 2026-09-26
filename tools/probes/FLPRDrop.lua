-- Opt-in diagnostic for the isolated BG2:EE 2.6.6 fixture. NOT an autoload module.
-- Observe the ordinary death-drop path without changing its arguments/results.
-- Caller must first verify Baldur.exe SHA256:
-- fc821a4806a0305b84fd85f1aad2bd472c8db642ed34b4494ae62351cae1c580
-- No Lua callbacks, engine calls, or retained userdata inside the native hooks.
-- Buffer lifetime is the process lifetime; installation is deliberately one-shot.
assert(not FLPR_DROP_TRACE, "DROP_TRACE_ALREADY_INSTALLED")
assert(EEex_Active and FLPR and FLPR.Host and FLPR.Host.status == "complete",
    "DROP_TRACE_REQUIRES_ISOLATED_FIXTURE")
local revisions = {
    ["5c41781045d88881207731401341286d591908699eee1ac03bf7eebcf4da9790"] = true,
    ["0d3ede4e2caa0665b9753645169fbc5ce0f681870709da66d90bc08ba5e83877"] = true,
}
assert(revisions[FLPR.Host.controller.manifest.revision],
    "DROP_TRACE_WRONG_REVISION")
assert(EEex_Area_GetVisible().m_resref:get():lower() == "bg2600", "DROP_TRACE_WRONG_AREA")

local sites = {
    {rva=0x3925DA, before=true,
        bytes="00488b4d9741b001c744242001000000e8811ce0ff48c786c00f0000000000000fb605dd97"},
    {rva=0x1942B1,
        bytes="2428440fb7c6894c2420488bd7488bc8e88ac6ffff488b0de30d4d004533c04881c1681700"},
    {rva=0x210B03,
        bytes="0233ed0fb796b80000004c8bc5488bcbe83847f8ff80beba00000001750d488bcbe81710f8"},
}
local base = EEex_GetImageBase()
-- Validate ALL sites before modifying any, including the surrounding register
-- setup and the instruction after each call. Refuse other hooks/builds here.
for _, site in ipairs(sites) do
    local offset = 0
    for byte in site.bytes:gmatch("%x%x") do
        assert(EEex_ReadU8(base + site.rva - 16 + offset) == tonumber(byte, 16),
            "DROP_TRACE_CODE_MISMATCH:" .. tostring(site.rva))
        offset = offset + 1
    end
end

local capacity, stride, header = 256, 64, 32
local buffer = EEex_Malloc(header + capacity * stride)
assert(buffer and buffer ~= 0, "DROP_TRACE_ALLOC_FAILED")
for offset = 0, header + capacity * stride - 1, 8 do EEex_Write64(buffer + offset, 0) end
-- Header: enabled, claimed event count, overflow, active writers. Event kind at
-- +0 is published LAST. Atomic tickets prevent row collisions; the writer count
-- makes a stopped snapshot reject writers still finishing an earlier ticket.
local function assembly(kind, fields, item)
    return {string.format([[
        pushfq
        push rax
        push r10
        push r11
        mov r10, %d
        lock inc qword ptr ds:[r10+24]
        cmp qword ptr ds:[r10], 1
        jne trace_done
        mov r11, 1
        lock xadd qword ptr ds:[r10+8], r11
        cmp r11, %d
        jae trace_overflow
        shl r11, 6
        add r11, r10
        add r11, 32
        %s
        mov rax, %s
        test rax, rax
        je trace_publish
        mov r10, qword ptr ds:[rax+0x10]
        mov qword ptr ds:[r11+40], r10
        mov r10, qword ptr ds:[rax+0x18]
        mov qword ptr ds:[r11+48], r10
        mov r10, qword ptr ds:[rax+0x20]
        mov qword ptr ds:[r11+56], r10
        trace_publish:
        mov qword ptr ds:[r11], %d
        jmp trace_done
        trace_overflow:
        mov qword ptr ds:[r10+16], 1
        trace_done:
        mov r10, %d
        lock dec qword ptr ds:[r10+24]
        pop r11
        pop r10
        pop rax
        popfq
    ]], buffer, capacity, fields, item, kind, buffer)}
end
local code = {
    assembly(1, [[
        mov qword ptr ds:[r11+8], rdi
        mov qword ptr ds:[r11+16], rdx
        mov qword ptr ds:[r11+24], rcx
        movzx eax, bx
        mov qword ptr ds:[r11+32], rax
    ]], "rdx"),
    assembly(2, [[
        mov qword ptr ds:[r11+8], rdi
        mov rax, qword ptr ss:[rsp+16]
        mov qword ptr ds:[r11+16], rax
        mov qword ptr ds:[r11+24], rbx
    ]], "rdi"),
    assembly(3, [[
        mov qword ptr ds:[r11+8], rsi
        mov qword ptr ds:[r11+16], rbp
        mov qword ptr ds:[r11+24], rbx
    ]], "rbp"),
}

-- A partial installation must never be retried in this process. Restore code
-- protection even if the assembler reports an error, and leave capture off.
local trace = {installed=false}
FLPR_DROP_TRACE = trace
EEex_DisableCodeProtection()
local ok, failure = pcall(function()
    for index, site in ipairs(sites) do
        local address = base + site.rva
        local original_target = address + 5 + EEex_Read32(address + 1)
        local hook = site.before and EEex_HookBeforeCall or EEex_HookAfterCall
        hook(address, code[index])
        assert(EEex_ReadU8(address) == 0xE9, "DROP_TRACE_DETOUR_NOT_WRITTEN")
        local destination = address + 5 + EEex_Read32(address + 1)
        assert(destination > 0 and (destination < address - 16 or destination >= address + 21)
            and destination ~= original_target, "DROP_TRACE_DETOUR_INVALID")
    end
end)
EEex_EnableCodeProtection()
assert(ok, failure)
trace.installed = true

function trace.arm()
    assert(trace.installed and not trace.started, "DROP_TRACE_ONE_SHOT")
    trace.started = true
    EEex_Write64(buffer, 1)
end

function trace.stop()
    EEex_Write64(buffer, 0)
end

function trace.inspect()
    local active = EEex_ReadU64(buffer + 24)
    local count = EEex_ReadU64(buffer + 8)
    local result = {schema="flpr-drop-trace-v1", installed=trace.installed,
        armed=EEex_ReadU64(buffer) == 1,
        overflow=EEex_ReadU64(buffer + 16) ~= 0 or count > capacity, events={}}
    for index = 0, math.min(count, capacity) - 1 do
        local row = buffer + header + index * stride
        -- Read the publication marker before its payload, never afterwards.
        local kind = EEex_ReadU64(row)
        local chars = {}
        for n = 0, 7 do
            local char = EEex_ReadU8(row + 40 + n)
            if char == 0 then break end
            chars[#chars+1] = string.char(char)
        end
        result.events[#result.events+1] = {
            sequence=index+1, kind=kind,
            a=tostring(EEex_ReadU64(row+8)), b=tostring(EEex_ReadU64(row+16)),
            c=tostring(EEex_ReadU64(row+24)), d=EEex_ReadU64(row+32),
            resref=table.concat(chars):lower(), charge1=EEex_ReadU16(row+52),
            charge2=EEex_ReadU16(row+54), charge3=EEex_ReadU16(row+56),
            expiration=EEex_ReadU16(row+58), flags=EEex_ReadU32(row+60),
        }
    end
    result.stable = not result.armed and active == 0 and EEex_ReadU64(buffer + 24) == 0
        and EEex_ReadU64(buffer + 8) == count
    return result
end

return {installed=true, sites=#sites, capacity=capacity, armed=false}
