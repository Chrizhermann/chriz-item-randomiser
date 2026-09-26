-- Opt-in synthetic source suppression diagnostic. Never autoloaded or connected
-- to Core receipts. Invoke loadfile(this)(loadfile('FLPRGateAsm.lua')()).
-- Caller pins Baldur.exe SHA256 before installation:
-- fc821a4806a0305b84fd85f1aad2bd472c8db642ed34b4494ae62351cae1c580
-- Save GLOBAL FLPR_U1_MODE: 1 selects the authored original; 2 excludes it.
-- Mode is captured only from an engine-loaded GAM, never set through this API.
local build = ...
assert(type(build) == 'function', 'SOURCE_GATE_ASSEMBLY_BUILDER_REQUIRED')
assert(not FLPR_SOURCE_GATE and not FLPR_AREA_TRACE, 'SOURCE_GATE_ALREADY_INSTALLED')
assert(EEex_Active and FLPR and FLPR.Host and FLPR.Host.status == 'complete'
    and FLPR.Host.controller.manifest.revision ==
    'c0acc859837a30315637e16d73c427393ca2694d0cd54eeb7d45ed82eee78e91'
    and not FLPR.Held, 'SOURCE_GATE_REQUIRES_QUIET_FIXTURE')
assert(EEex_Area_GetVisible().m_resref:get():lower() == 'bg2600', 'SOURCE_GATE_WRONG_AREA')
assert(type(EEex_GameState_AddDestroyedListener) == 'function', 'SOURCE_GATE_LIFECYCLE_MISSING')
local base = EEex_GetImageBase()
local sites = {
    {rva=0x292A84, size=8, code='enter',
        bytes='48894df8488d4dc8488945a8410fb6f944884c2460'},
    {rva=0x292F45, size=7, code='capture',
        bytes='8bd6413974242c0f86c40400000f1f400066660f1f84'},
    {rva=0x190270, size=7, code='item',
        bytes='418bc7488d0c80488b45884c8d2c88498bd5488d4df8'},
    -- The alternate exit bypasses construction AND its CString cleanup. This
    -- target resumes at the ordinary loop increment with original registers.
    {rva=0x1906FC,
        bytes='418b464441ffc74103464041bc0000000044897c2450443bf80f8255fbffff4c8b6d98410f'},
}
for _,site in ipairs(sites) do
    local offset=0
    for byte in site.bytes:gmatch('%x%x') do
        assert(EEex_ReadU8(base+site.rva+offset)==tonumber(byte,16), 'SOURCE_GATE_CODE_MISMATCH')
        offset=offset+1
    end
end
local header,capacity,stride=128,2048,128
local buffer=EEex_Malloc(header+capacity*stride)
assert(buffer and buffer~=0, 'SOURCE_GATE_ALLOC_FAILED')
for offset=0,header+capacity*stride-1,8 do EEex_Write64(buffer+offset,0) end
local gate={installed=false}
FLPR_SOURCE_GATE=gate
local assembly=build(buffer,base)
EEex_DisableCodeProtection()
local ok,failure=pcall(function()
    for _,site in ipairs(sites) do
        if site.code then
            EEex_HookBeforeRestore(base+site.rva,0,site.size,site.size,{assembly[site.code]})
            local address=base+site.rva
            assert(EEex_ReadU8(address)==0xE9, 'SOURCE_GATE_DETOUR_NOT_WRITTEN')
            local target=address+5+EEex_Read32(address+1)
            assert(target>0 and (target<address or target>=address+#site.bytes/2),
                'SOURCE_GATE_DETOUR_INVALID')
        end
    end
end)
EEex_EnableCodeProtection()
assert(ok,failure)
-- Existing EEex lifecycle callback runs after game destruction. An aborted
-- load or New Game cannot inherit this probe's previously captured policy.
EEex_GameState_AddDestroyedListener(function()
    EEex_Write64(buffer+32,0)
    EEex_Write64(buffer+48,0)
    EEex_Write64(buffer+56,0)
    EEex_Write64(buffer+104,EEex_ReadU64(buffer+104)+1)
end)
gate.installed=true
function gate.arm()
    assert(gate.installed and not gate.started, 'SOURCE_GATE_ONE_SHOT')
    gate.started=true
    EEex_Write64(buffer,1)
end
function gate.stop()
    EEex_Write64(buffer,0)
    EEex_Write64(buffer+32,0)
    EEex_Write64(buffer+48,0)
    EEex_Write64(buffer+56,0)
end
local function raw(address,length)
    local bytes={}
    for offset=0,length-1 do bytes[#bytes+1]=string.format('%02x',EEex_ReadU8(address+offset)) end
    return table.concat(bytes)
end
function gate.inspect()
    local active,count=EEex_ReadU64(buffer+24),EEex_ReadU64(buffer+8)
    local published=true
    local result={schema='flpr-source-gate-trace-v1',installed=gate.installed,
        armed=EEex_ReadU64(buffer)==1,overflow=EEex_ReadU64(buffer+16)~=0 or count>capacity,
        mode=EEex_ReadU64(buffer+32),generation=EEex_ReadU64(buffer+40),
        skips=EEex_ReadU64(buffer+96),destroys=EEex_ReadU64(buffer+104),events={}}
    for index=0,math.min(count,capacity)-1 do
        local row=buffer+header+index*stride
        local kind=EEex_ReadU64(row)
        if kind~=1 and kind~=2 and kind~=3 then published=false end
        result.events[#result.events+1]={sequence=index+1,kind=kind,
            generation=EEex_ReadU64(row+8),mode=EEex_ReadU64(row+16),decision=EEex_ReadU64(row+24),
            object_pointer=tostring(EEex_ReadU64(row+32)),other_value=tostring(EEex_ReadU64(row+40)),
            index=EEex_ReadU64(row+48),name_raw=raw(row+56,32),payload_raw=raw(row+88,20),
            detail1=EEex_ReadU64(row+112),detail2=EEex_ReadU64(row+120)}
    end
    result.stable=published and not result.armed and active==0 and EEex_ReadU64(buffer+24)==0
        and EEex_ReadU64(buffer+8)==count
    return result
end
return {installed=true,sites=3,capacity=capacity,armed=false,area='FLPRU1',
    selected_original='FLPRU1A:FLPRI1',mode_global='FLPR_U1_MODE'}
