-- Read-only container-load diagnostic for the disposable FLPRU1 area only.
-- Not autoloaded, not a source-removal mechanism, and never calls Lua/engine
-- code in a native hook. Caller verifies Baldur.exe SHA256 before installation:
-- fc821a4806a0305b84fd85f1aad2bd472c8db642ed34b4494ae62351cae1c580
-- Captured raw bytes retain physical indexes, including empty containers.
assert(not FLPR_AREA_TRACE, "AREA_TRACE_ALREADY_INSTALLED")
assert(EEex_Active and FLPR and FLPR.Host and FLPR.Host.status == "complete"
    and FLPR.Host.controller.manifest.revision ==
    "c0acc859837a30315637e16d73c427393ca2694d0cd54eeb7d45ed82eee78e91"
    and not FLPR.Held, "AREA_TRACE_REQUIRES_QUIET_FIXTURE")
assert(EEex_Area_GetVisible().m_resref:get():lower() == "bg2600", "AREA_TRACE_WRONG_AREA")

local base = EEex_GetImageBase()
local sites = {
    {rva=0x18D9C9, offset=-16,
        bytes="4889542428498bd666894c2420488bc8e8322400004885c00f84e90000004c8b00418bd4488bc8ffc341ff5058"},
    {rva=0x190270, offset=0,
        bytes="418bc7488d0c80488b45884c8d2c88498bd5488d4df8"},
}
for _, site in ipairs(sites) do
    local offset = site.offset
    for byte in site.bytes:gmatch("%x%x") do
        assert(EEex_ReadU8(base+site.rva+offset) == tonumber(byte,16), "AREA_TRACE_CODE_MISMATCH")
        offset = offset + 1
    end
end

local capacity, stride, header = 2048, 128, 32
local buffer = EEex_Malloc(header + capacity*stride)
assert(buffer and buffer ~= 0, "AREA_TRACE_ALLOC_FAILED")
for offset=0, header+capacity*stride-1, 8 do EEex_Write64(buffer+offset,0) end
-- Header: enabled, atomic ticket count, overflow, active writers.
-- Row: kind; area, owner, file-container pointers; index; raw area name (8);
-- raw container name (32); first/count (8); position/type/lock (8);
-- raw item record (20, padded to 24); raw item-record pointer.
local function assembly(kind, area, owner, record, index, item)
    local item_copy = ""
    if item then
        item_copy = [[
            mov eax, r15d
            lea rax, ds:[rax+rax*4]
            shl rax, 2
            add rax, qword ptr ss:[rbp-0x78]
            mov qword ptr ds:[r11+120], rax
            mov r10, qword ptr ds:[rax]
            mov qword ptr ds:[r11+96], r10
            mov r10, qword ptr ds:[rax+8]
            mov qword ptr ds:[r11+104], r10
            mov r10d, dword ptr ds:[rax+16]
            mov dword ptr ds:[r11+112], r10d
        ]]
    end
    local code = string.format([[
        pushfq
        push rax
        push r10
        push r11
        mov r10, %d
        lock inc qword ptr ds:[r10+24]
        cmp qword ptr ds:[r10], 1
        jne trace_done
        mov rax, %s
        test rax, rax
        je trace_done
        mov r11, 54242522975302
        cmp qword ptr ds:[rax+0x204], r11
        jne trace_done
        mov r11, 1
        lock xadd qword ptr ds:[r10+8], r11
        cmp r11, %d
        jae trace_overflow
        shl r11, 7
        add r11, r10
        add r11, 32
        mov qword ptr ds:[r11+8], rax
        mov rax, qword ptr ds:[rax+0x204]
        mov qword ptr ds:[r11+40], rax
        mov rax, %s
        mov qword ptr ds:[r11+16], rax
        mov rax, %s
        mov qword ptr ds:[r11+24], rax
        %s
        mov qword ptr ds:[r11+32], rax
        mov rax, qword ptr ds:[r11+24]
        mov r10, qword ptr ds:[rax]
        mov qword ptr ds:[r11+48], r10
        mov r10, qword ptr ds:[rax+8]
        mov qword ptr ds:[r11+56], r10
        mov r10, qword ptr ds:[rax+16]
        mov qword ptr ds:[r11+64], r10
        mov r10, qword ptr ds:[rax+24]
        mov qword ptr ds:[r11+72], r10
        mov r10, qword ptr ds:[rax+0x40]
        mov qword ptr ds:[r11+80], r10
        mov r10, qword ptr ds:[rax+0x20]
        mov qword ptr ds:[r11+88], r10
        %s
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
    ]], buffer,area,capacity,owner,record,index,item_copy,kind,buffer)
    -- Entry and exit code share one assembled detour, so their labels differ.
    return {(code:gsub("trace_", "area_" .. tostring(kind) .. "_"))}
end

local begin_code = assembly(1,"rdx","rcx","r8","mov eax, ebx")
-- Hook pushes 32 bytes; the original return RAX is at the saved-stack +16.
local end_code = assembly(3,"r14","qword ptr ss:[rsp+16]","rdi","mov eax, ebx")
local item_code = assembly(2,"qword ptr ss:[rbp-0x60]","qword ptr ss:[rbp-0x68]",
    "r14","mov eax, r15d",true)
local trace = {installed=false}
FLPR_AREA_TRACE = trace
EEex_DisableCodeProtection()
local ok, failure = pcall(function()
    EEex_HookBeforeAndAfterCall(base+sites[1].rva,begin_code,end_code)
    -- Two whole instructions: mov eax,r15d (3), lea rcx,[rax+rax*4] (4).
    -- Neither is relative to RIP or branches; restore all seven bytes.
    EEex_HookBeforeRestore(base+sites[2].rva,0,7,7,item_code)
    for _, site in ipairs(sites) do
        local address = base+site.rva
        assert(EEex_ReadU8(address) == 0xE9, "AREA_TRACE_DETOUR_NOT_WRITTEN")
        local target = address+5+EEex_Read32(address+1)
        assert(target > 0 and (target < address-16 or target >= address+45), "AREA_TRACE_DETOUR_INVALID")
    end
end)
EEex_EnableCodeProtection()
assert(ok,failure)
trace.installed = true

function trace.arm()
    assert(trace.installed and not trace.started, "AREA_TRACE_ONE_SHOT")
    trace.started = true
    EEex_Write64(buffer,1)
end
function trace.stop() EEex_Write64(buffer,0) end

local function raw(address,length)
    local bytes = {}
    for offset=0,length-1 do bytes[#bytes+1]=string.format("%02x",EEex_ReadU8(address+offset)) end
    return table.concat(bytes)
end
function trace.inspect()
    local active, count = EEex_ReadU64(buffer+24), EEex_ReadU64(buffer+8)
    local published = true
    local result = {schema="flpr-area-load-trace-v1",installed=trace.installed,
        armed=EEex_ReadU64(buffer)==1,overflow=EEex_ReadU64(buffer+16)~=0 or count>capacity,events={}}
    for index=0,math.min(count,capacity)-1 do
        local row = buffer+header+index*stride
        local kind = EEex_ReadU64(row) -- publication marker precedes payload reads
        if kind ~= 1 and kind ~= 2 and kind ~= 3 then published = false end
        result.events[#result.events+1] = {sequence=index+1,kind=kind,
            area_pointer=tostring(EEex_ReadU64(row+8)),owner_pointer=tostring(EEex_ReadU64(row+16)),
            record_pointer=tostring(EEex_ReadU64(row+24)),index=EEex_ReadU64(row+32),
            area_raw=raw(row+40,8),name_raw=raw(row+48,32),
            first=EEex_ReadU32(row+80),count=EEex_ReadU32(row+84),
            x=EEex_ReadU16(row+88),y=EEex_ReadU16(row+90),container_type=EEex_ReadU16(row+92),
            lock=EEex_ReadU16(row+94),payload_raw=raw(row+96,20),
            item_record_pointer=tostring(EEex_ReadU64(row+120))}
    end
    result.stable = published and not result.armed and active==0 and EEex_ReadU64(buffer+24)==0
        and EEex_ReadU64(buffer+8)==count
    return result
end
return {installed=true,sites=#sites,capacity=capacity,armed=false,area="FLPRU1"}
