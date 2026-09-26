"""Offline safety and snapshot guards for the opt-in area probe.

These Lua mocks do not execute the native assembly or establish hook correctness.
"""
from pathlib import Path
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT


ROOT = Path(__file__).resolve().parents[1]
SETUP = r'''
memory, events, restore_calls = {}, {}, {}
reads, allocations, writes = 0, 0, 0
count_reads, mutate_count_on_second_read = 0, false
fail_signature_at, fail_hook, omit_detour = nil, nil, false
area_name = "BG2600"
EEex_Active = true
FLPR = {Host={status="complete",controller={manifest={revision=
    "c0acc859837a30315637e16d73c427393ca2694d0cd54eeb7d45ed82eee78e91"}}}}
function EEex_Area_GetVisible() return {m_resref={get=function() return area_name end}} end
function EEex_GetImageBase() return 0x140000000 end

function pokehex(address, hex)
    local offset = 0
    for pair in hex:gmatch("%x%x") do
        memory[address+offset] = tonumber(pair,16)
        offset = offset+1
    end
end
function poke(address, value, size)
    for offset=0,size-1 do
        memory[address+offset] = value % 256
        value = math.floor(value/256)
    end
end
local function read(address, size)
    local value = 0
    for offset=size-1,0,-1 do
        value = value*256 + (memory[address+offset] or 0)
    end
    return value
end
local fixtures = {
    {0x18D9C9,-16,
        "4889542428498bd666894c2420488bc8e8322400004885c00f84e90000004c8b00418bd4488bc8ffc341ff5058"},
    {0x190270,0,"418bc7488d0c80488b45884c8d2c88498bd5488d4df8"},
}
signature_addresses = {}
for i,fixture in ipairs(fixtures) do
    local first=0x140000000+fixture[1]+fixture[2]
    pokehex(first,fixture[3])
    signature_addresses[i]={first,first+#fixture[3]/2-1}
end
function EEex_ReadU8(address)
    reads=reads+1
    if address==fail_signature_at then return 255 end
    return memory[address] or 0
end
function EEex_ReadU16(address) return read(address,2) end
function EEex_ReadU32(address) return read(address,4) end
function EEex_ReadU64(address)
    if address==1008 then
        count_reads=count_reads+1
        if mutate_count_on_second_read and count_reads==2 then poke(address,2,8) end
    end
    return read(address,8)
end
function EEex_Read32(address) return read(address,4) end
function EEex_Malloc(size)
    allocations=allocations+1
    events[#events+1]="allocate"
    malloc_size=size
    return 1000
end
function EEex_Write64(address,value)
    writes=writes+1
    if value ~= 0 then
        poke(address,value,8)
    else
        for offset=0,7 do memory[address+offset]=nil end
    end
end
function EEex_DisableCodeProtection() events[#events+1]="unprotect" end
function EEex_EnableCodeProtection() events[#events+1]="protect" end
local hook_count=0
local function hook(address)
    hook_count=hook_count+1
    events[#events+1]="hook"..hook_count
    if hook_count==fail_hook then error("ASSEMBLER_FAILED") end
    if not omit_detour then poke(address,0xE9,1); poke(address+1,5000,4) end
end
function EEex_HookBeforeAndAfterCall(address,before,after)
    hook(address)
end
function EEex_HookBeforeRestore(address,delay,size,return_delay,code)
    restore_calls[#restore_calls+1]={address,delay,size,return_delay}
    hook(address)
end
'''


class AreaProbeGuards(unittest.TestCase):
    def run_case(self, case):
        for cls in (Lua51, Lua53, LuaJIT):
            with self.subTest(runtime=cls.__module__):
                lua = cls(unpack_returned_tuples=True)
                lua.execute(SETUP)
                lua.globals().probe_path = (ROOT / "tools/probes/FLPRArea.lua").as_posix()
                lua.execute(case)

    def test_each_signature_mismatch_has_zero_allocations_or_writes(self):
        for site in (1, 2):
            for boundary in (1, 2):
                with self.subTest(site=site, boundary=boundary):
                    self.run_case(f'''
                        fail_signature_at=signature_addresses[{site}][{boundary}]
                        local ok,err=pcall(dofile,probe_path)
                        assert(not ok and tostring(err):find("CODE_MISMATCH"))
                        assert(reads>0 and allocations==0 and writes==0)
                        assert(#events==0 and #restore_calls==0 and FLPR_AREA_TRACE==nil)
                    ''')

    def test_partial_combined_or_restore_hook_failure_reprotects_and_prevents_retry(self):
        for failing_hook in (1, 2):
            with self.subTest(failing_hook=failing_hook):
                self.run_case(f'''
                    fail_hook={failing_hook}
                    local ok,err=pcall(dofile,probe_path)
                    assert(not ok and tostring(err):find("ASSEMBLER_FAILED"))
                    assert(events[#events]=="protect" and EEex_ReadU64(1000)==0)
                    assert(allocations==1 and not FLPR_AREA_TRACE.installed)
                    assert(#restore_calls=={failing_hook - 1})
                    local n,w=#events,writes
                    ok,err=pcall(dofile,probe_path)
                    assert(not ok and tostring(err):find("ALREADY_INSTALLED"))
                    assert(#events==n and writes==w and EEex_ReadU64(1000)==0)
                ''')

    def test_quiet_revision_no_held_watch_and_area_are_required_before_reads(self):
        for change, expected in (
            ('FLPR.Host.controller.manifest.revision="other"', "REQUIRES_QUIET_FIXTURE"),
            ('FLPR.Held={item="watched"}', "REQUIRES_QUIET_FIXTURE"),
            ('area_name="AR9999"', "WRONG_AREA"),
        ):
            with self.subTest(change=change):
                self.run_case(f'''
                    {change}
                    local ok,err=pcall(dofile,probe_path)
                    assert(not ok and tostring(err):find("{expected}"))
                    assert(reads==0 and allocations==0 and writes==0)
                    assert(#events==0 and FLPR_AREA_TRACE==nil)
                ''')

    def test_successful_install_has_two_sites_disarmed_arm_once_and_stop(self):
        self.run_case('''
            local report=dofile(probe_path)
            assert(report.installed and report.sites==2 and report.capacity==2048)
            assert(report.area=="FLPRU1" and not report.armed)
            assert(FLPR_AREA_TRACE.installed and allocations==1)
            assert(malloc_size==32+2048*128 and EEex_ReadU64(1000)==0)
            assert(#restore_calls==1)
            assert(restore_calls[1][1]==0x140190270)
            assert(restore_calls[1][2]==0 and restore_calls[1][3]==7 and restore_calls[1][4]==7)
            assert(events[#events]=="protect")
            FLPR_AREA_TRACE.arm(); assert(EEex_ReadU64(1000)==1)
            FLPR_AREA_TRACE.stop(); assert(EEex_ReadU64(1000)==0)
            assert(not pcall(FLPR_AREA_TRACE.arm))
            assert(EEex_ReadU64(1000)==0)
        ''')

    def test_silent_detour_failure_reprotects_and_stays_disarmed(self):
        self.run_case('''
            omit_detour=true
            local ok,err=pcall(dofile,probe_path)
            assert(not ok and tostring(err):find("DETOUR_NOT_WRITTEN"))
            assert(events[#events]=="protect" and EEex_ReadU64(1000)==0)
            assert(not FLPR_AREA_TRACE.installed)
        ''')

    def test_stopped_snapshot_bounded_count_overflow_and_writer_stability(self):
        self.run_case('''
            dofile(probe_path)
            local trace=FLPR_AREA_TRACE
            trace.arm(); trace.stop()
            assert(trace.inspect().stable)
            poke(1000+24,1,8)
            assert(not trace.inspect().stable)
            poke(1000+24,0,8)
            poke(1000+8,1,8)
            poke(1032,1,8)
            count_reads=0
            mutate_count_on_second_read=true
            assert(not trace.inspect().stable)
            mutate_count_on_second_read=false
            poke(1000+8,2049,8)
            local snapshot=trace.inspect()
            assert(snapshot.schema=="flpr-area-load-trace-v1")
            assert(snapshot.overflow and #snapshot.events==2048 and not snapshot.stable)
            poke(1000+16,1,8)
            poke(1000+8,0,8)
            snapshot=trace.inspect()
            assert(snapshot.overflow and #snapshot.events==0)
        ''')

    def test_stopped_snapshot_preserves_raw_records_and_physical_indexes(self):
        self.run_case('''
            dofile(probe_path)
            local trace=FLPR_AREA_TRACE
            local first=1032
            poke(1008,2,8)
            poke(first,2,8)       -- item row publication marker
            poke(first+8,141,8)   -- area pointer
            poke(first+16,142,8)  -- owner pointer
            poke(first+24,143,8)  -- file-container pointer
            poke(first+32,7,8)    -- physical item index, not row ordinal
            pokehex(first+40,"464c505255310000")
            pokehex(first+48,string.rep("ab",32))
            poke(first+80,11,4); poke(first+84,3,4)
            poke(first+88,12,2); poke(first+90,13,2)
            poke(first+92,14,2); poke(first+94,15,2)
            pokehex(first+96,"000102030405060708090a0b0c0d0e0f10111213")
            poke(first+120,144,8)
            local second=first+128
            poke(second,1,8)      -- empty container row
            poke(second+32,42,8) -- physical container index
            pokehex(second+48,string.rep("00",32))
            pokehex(second+96,string.rep("00",20))
            local snapshot=trace.inspect()
            assert(snapshot.stable and not snapshot.overflow and #snapshot.events==2)
            local item,empty=snapshot.events[1],snapshot.events[2]
            assert(item.sequence==1 and item.kind==2 and item.index==7)
            assert(item.area_pointer=="141" and item.owner_pointer=="142")
            assert(item.record_pointer=="143" and item.item_record_pointer=="144")
            assert(item.area_raw=="464c505255310000")
            assert(item.name_raw==string.rep("ab",32) and #item.name_raw==64)
            assert(item.first==11 and item.count==3 and item.x==12 and item.y==13)
            assert(item.container_type==14 and item.lock==15)
            assert(item.payload_raw=="000102030405060708090a0b0c0d0e0f10111213")
            assert(#item.payload_raw==40)
            assert(empty.sequence==2 and empty.kind==1 and empty.index==42)
            assert(empty.name_raw==string.rep("00",32) and #empty.name_raw==64)
            assert(empty.payload_raw==string.rep("00",20) and #empty.payload_raw==40)
        ''')

    def test_unpublished_row_cannot_be_reported_as_stable_event(self):
        self.run_case('''
            dofile(probe_path)
            poke(1008,1,8) -- ticket claimed; row kind is still zero
            local snapshot=FLPR_AREA_TRACE.inspect()
            assert(not snapshot.stable and #snapshot.events==1)
            assert(snapshot.events[1].kind==0)
        ''')


if __name__ == "__main__":
    unittest.main()
