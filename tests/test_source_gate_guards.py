"""Offline wrapper guards for the opt-in source gate.

The builder returns harmless strings here. Native instruction behavior belongs
to the separate assembly tests; these mocks establish Lua lifecycle only.
"""

from pathlib import Path
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT


ROOT = Path(__file__).resolve().parents[1]
SETUP = r'''
memory, events, restore_calls = {}, {}, {}
reads, allocations, writes, builder_calls, callbacks = 0, 0, 0, 0, {}
count_reads, mutate_count_on_second_read = 0, false
fail_signature_at, fail_hook, omit_detour_at, invalid_detour_at = nil, nil, nil, nil
area_name = "BG2600"
EEex_Active = true
FLPR = {Host={status="complete",controller={manifest={revision=
    "c0acc859837a30315637e16d73c427393ca2694d0cd54eeb7d45ed82eee78e91"}}}}
function EEex_Area_GetVisible() return {m_resref={get=function() return area_name end}} end
function EEex_GetImageBase() return 0x140000000 end
function EEex_GameState_AddDestroyedListener(callback) callbacks[#callbacks+1]=callback end
function builder(buffer,base)
    builder_calls=builder_calls+1
    return {enter="nop",capture="nop",item="nop"}
end
function pokehex(address, hex)
    local offset=0
    for pair in hex:gmatch("%x%x") do
        memory[address+offset]=tonumber(pair,16)
        offset=offset+1
    end
end
function poke(address,value,size)
    for offset=0,size-1 do
        memory[address+offset]=value%256
        value=math.floor(value/256)
    end
end
local function read(address,size)
    local value=0
    for offset=size-1,0,-1 do value=value*256+(memory[address+offset] or 0) end
    return value
end
local signatures={
    {0x292A84,"48894df8488d4dc8488945a8410fb6f944884c2460"},
    {0x292F45,"8bd6413974242c0f86c40400000f1f400066660f1f84"},
    {0x190270,"418bc7488d0c80488b45884c8d2c88498bd5488d4df8"},
    {0x1906FC,"418b464441ffc74103464041bc0000000044897c2450443bf80f8255fbffff4c8b6d98410f"},
}
signature_addresses={}
for i,site in ipairs(signatures) do
    local first=0x140000000+site[1]
    pokehex(first,site[2])
    signature_addresses[i]={first,first+#site[2]/2-1}
end
function EEex_ReadU8(address)
    reads=reads+1
    if address==fail_signature_at then return 255 end
    return memory[address] or 0
end
function EEex_ReadU64(address)
    if address==1008 then
        count_reads=count_reads+1
        if mutate_count_on_second_read and count_reads==2 then poke(address,read(address,8)+1,8) end
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
    if value==0 then
        for offset=0,7 do memory[address+offset]=nil end
    else poke(address,value,8) end
end
function EEex_DisableCodeProtection() events[#events+1]="unprotect" end
function EEex_EnableCodeProtection() events[#events+1]="protect" end
local hook_count=0
function EEex_HookBeforeRestore(address,delay,size,return_delay,code)
    hook_count=hook_count+1
    restore_calls[#restore_calls+1]={address,delay,size,return_delay,code}
    events[#events+1]="hook"..hook_count
    if hook_count==fail_hook then error("ASSEMBLER_FAILED") end
    if hook_count~=omit_detour_at then
        poke(address,0xE9,1)
        poke(address+1,hook_count==invalid_detour_at and 0 or 5000,4)
    end
end
'''


class SourceGateGuards(unittest.TestCase):
    def run_case(self, case):
        for cls in (Lua51, Lua53, LuaJIT):
            with self.subTest(runtime=cls.__module__):
                lua = cls(unpack_returned_tuples=True)
                lua.execute(SETUP)
                lua.globals().gate_path = (ROOT / "tools/probes/FLPRGate.lua").as_posix()
                lua.execute(case)

    def test_all_four_signatures_are_checked_before_allocation_or_mutation(self):
        for site in range(1, 5):
            for boundary in (1, 2):
                with self.subTest(site=site, boundary=boundary):
                    self.run_case(f'''
                        fail_signature_at=signature_addresses[{site}][{boundary}]
                        local ok,err=pcall(function() return assert(loadfile(gate_path))(builder) end)
                        assert(not ok and tostring(err):find("CODE_MISMATCH"))
                        assert(reads>0 and allocations==0 and writes==0 and builder_calls==0)
                        assert(#events==0 and #restore_calls==0 and #callbacks==0)
                        assert(FLPR_SOURCE_GATE==nil)
                    ''')

    def test_all_three_partial_hook_failures_reprotect_and_block_retry(self):
        for failing_hook in (1, 2, 3):
            with self.subTest(hook=failing_hook):
                self.run_case(f'''
                    fail_hook={failing_hook}
                    local ok,err=pcall(function() return assert(loadfile(gate_path))(builder) end)
                    assert(not ok and tostring(err):find("ASSEMBLER_FAILED"))
                    assert(events[#events]=="protect" and EEex_ReadU64(1000)==0)
                    assert(allocations==1 and builder_calls==1)
                    assert(#restore_calls=={failing_hook} and #callbacks==0)
                    assert(FLPR_SOURCE_GATE and not FLPR_SOURCE_GATE.installed)
                    local n,w=#events,writes
                    ok,err=pcall(function() return assert(loadfile(gate_path))(builder) end)
                    assert(not ok and tostring(err):find("ALREADY_INSTALLED"))
                    assert(#events==n and writes==w and EEex_ReadU64(1000)==0)
                ''')

    def test_builder_context_lifecycle_and_area_preconditions_precede_reads(self):
        self.run_case('''
            local ok,err=pcall(function() return assert(loadfile(gate_path))() end)
            assert(not ok and tostring(err):find("ASSEMBLY_BUILDER_REQUIRED"))
            assert(reads==0 and allocations==0 and writes==0 and FLPR_SOURCE_GATE==nil)
        ''')
        for change, expected in (
            ('FLPR.Host.status="disabled"', "REQUIRES_QUIET_FIXTURE"),
            ('FLPR.Host.controller.manifest.revision="other"', "REQUIRES_QUIET_FIXTURE"),
            ('FLPR.Held={item="watched"}', "REQUIRES_QUIET_FIXTURE"),
            ('FLPR_AREA_TRACE={}', "ALREADY_INSTALLED"),
            ('area_name="AR9999"', "WRONG_AREA"),
            ('EEex_GameState_AddDestroyedListener=nil', "LIFECYCLE_MISSING"),
        ):
            with self.subTest(change=change):
                self.run_case(f'''
                    {change}
                    local ok,err=pcall(function() return assert(loadfile(gate_path))(builder) end)
                    assert(not ok and tostring(err):find("{expected}"))
                    assert(reads==0 and allocations==0 and writes==0 and builder_calls==0)
                    assert(#events==0 and #callbacks==0 and FLPR_SOURCE_GATE==nil)
                ''')

    def test_detour_missing_or_invalid_reprotects_and_disarms(self):
        for site in (1, 2, 3):
            for field, expected in (("omit_detour_at", "DETOUR_NOT_WRITTEN"),
                                    ("invalid_detour_at", "DETOUR_INVALID")):
                with self.subTest(site=site, field=field):
                    self.run_case(f'''
                        {field}={site}
                        local ok,err=pcall(function() return assert(loadfile(gate_path))(builder) end)
                        assert(not ok and tostring(err):find("{expected}"))
                        assert(events[#events]=="protect" and EEex_ReadU64(1000)==0)
                        assert(not FLPR_SOURCE_GATE.installed and #callbacks==0)
                        assert(#restore_calls=={site})
                        local n=#events
                        ok,err=pcall(function() return assert(loadfile(gate_path))(builder) end)
                        assert(not ok and tostring(err):find("ALREADY_INSTALLED") and #events==n)
                    ''')

    def test_successful_install_arm_once_stop_clears_policy(self):
        self.run_case('''
            local report=assert(loadfile(gate_path))(builder)
            assert(report.installed and report.sites==3 and report.capacity==2048)
            assert(report.area=="FLPRU1" and report.selected_original=="FLPRU1A:FLPRI1")
            assert(report.mode_global=="FLPR_U1_MODE" and not report.armed)
            assert(FLPR_SOURCE_GATE.installed and allocations==1 and builder_calls==1)
            assert(malloc_size==128+2048*128 and EEex_ReadU64(1000)==0)
            assert(#restore_calls==3 and #callbacks==1 and events[#events]=="protect")
            for index,site in ipairs({{0x292A84,8},{0x292F45,7},{0x190270,7}}) do
                local call=restore_calls[index]
                assert(call[1]==0x140000000+site[1] and call[2]==0)
                assert(call[3]==site[2] and call[4]==site[2])
                assert(type(call[5])=="table" and #call[5]==1 and call[5][1]=="nop")
            end
            FLPR_SOURCE_GATE.arm(); assert(EEex_ReadU64(1000)==1)
            poke(1000+32,2,8);poke(1000+48,1234,8);poke(1000+56,5678,8)
            FLPR_SOURCE_GATE.stop()
            assert(EEex_ReadU64(1000)==0 and EEex_ReadU64(1032)==0)
            assert(EEex_ReadU64(1048)==0 and EEex_ReadU64(1056)==0)
            assert(not pcall(FLPR_SOURCE_GATE.arm) and EEex_ReadU64(1000)==0)
        ''')

    def test_destroy_callback_clears_prior_policy_and_counts_generation(self):
        self.run_case('''
            assert(loadfile(gate_path))(builder)
            local gate=FLPR_SOURCE_GATE
            poke(1000+32,2,8);poke(1000+40,3,8)
            poke(1000+48,1234,8);poke(1000+56,5678,8)
            poke(1000+96,1,8);poke(1000+104,7,8)
            callbacks[1]()
            assert(EEex_ReadU64(1000+32)==0 and EEex_ReadU64(1000+48)==0)
            assert(EEex_ReadU64(1000+56)==0 and EEex_ReadU64(1000+104)==8)
            assert(EEex_ReadU64(1000+40)==3 and EEex_ReadU64(1000+96)==1)
            local snapshot=gate.inspect()
            assert(snapshot.mode==0 and snapshot.generation==3)
            assert(snapshot.skips==1 and snapshot.destroys==8)
        ''')

    def test_stopped_snapshot_exact_rows_bounded_count_and_writer_stability(self):
        self.run_case('''
            assert(loadfile(gate_path))(builder)
            local gate=FLPR_SOURCE_GATE
            gate.arm();gate.stop()
            assert(gate.inspect().stable)
            local first=1000+128
            poke(1000+8,2,8)
            poke(first,3,8)
            poke(first+8,12,8);poke(first+16,2,8);poke(first+24,1,8)
            poke(first+32,141,8);poke(first+40,142,8);poke(first+48,7,8)
            pokehex(first+56,string.rep("ab",32))
            pokehex(first+88,"000102030405060708090a0b0c0d0e0f10111213")
            poke(first+112,143,8);poke(first+120,144,8)
            local second=first+128
            poke(second,1,8);poke(second+32,241,8);poke(second+48,42,8)
            pokehex(second+56,string.rep("cd",32))
            pokehex(second+88,string.rep("ef",20))
            local snapshot=gate.inspect()
            assert(snapshot.stable and #snapshot.events==2)
            local row=snapshot.events[1]
            assert(row.sequence==1 and row.kind==3 and row.generation==12)
            assert(row.mode==2 and row.decision==1 and row.index==7)
            assert(row.object_pointer=="141" and row.other_value=="142")
            assert(row.name_raw==string.rep("ab",32) and #row.name_raw==64)
            assert(row.payload_raw=="000102030405060708090a0b0c0d0e0f10111213")
            assert(#row.payload_raw==40 and row.detail1==143 and row.detail2==144)
            local row2=snapshot.events[2]
            assert(row2.sequence==2 and row2.kind==1 and row2.object_pointer=="241")
            assert(row2.index==42 and row2.name_raw==string.rep("cd",32))
            assert(row2.payload_raw==string.rep("ef",20))
            poke(1000+24,1,8);assert(not gate.inspect().stable)
            poke(1000+24,0,8)
            count_reads=0;mutate_count_on_second_read=true
            assert(not gate.inspect().stable)
            mutate_count_on_second_read=false
            poke(1000+8,2049,8)
            snapshot=gate.inspect()
            assert(snapshot.schema=="flpr-source-gate-trace-v1")
            assert(snapshot.overflow and not snapshot.stable and #snapshot.events==2048)
            poke(1000+16,1,8);poke(1000+8,0,8)
            snapshot=gate.inspect()
            assert(snapshot.overflow and #snapshot.events==0)
        ''')

    def test_unpublished_row_is_not_a_stable_event(self):
        self.run_case('''
            assert(loadfile(gate_path))(builder)
            poke(1000+8,1,8)
            local snapshot=FLPR_SOURCE_GATE.inspect()
            assert(not snapshot.stable and #snapshot.events==1)
            assert(snapshot.events[1].kind==0)
        ''')


if __name__ == "__main__":
    unittest.main()
