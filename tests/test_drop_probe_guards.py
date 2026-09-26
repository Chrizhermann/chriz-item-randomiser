"""Safety gates around the opt-in native probe; not native-hook acceptance."""
from pathlib import Path
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT

ROOT = Path(__file__).resolve().parents[1]
SETUP = r'''
events, memory, reads, restore_calls = {}, {}, 0, {}
fail_signature_at, fail_hook, omit_detour = nil, nil, false
EEex_Active = true
FLPR = {Host={status="complete",controller={manifest={revision=
    "5c41781045d88881207731401341286d591908699eee1ac03bf7eebcf4da9790"}}}}
function EEex_Area_GetVisible() return {m_resref={get=function() return "BG2600" end}} end
function EEex_GetImageBase() return 0x140000000 end
local fixtures = {
    {0x3925DA,"00488b4d9741b001c744242001000000e8811ce0ff48c786c00f0000000000000fb605dd97"},
    {0x1942B1,"2428440fb7c6894c2420488bd7488bc8e88ac6ffff488b0de30d4d004533c04881c1681700"},
    {0x210B03,"0233ed0fb796b80000004c8bc5488bcbe83847f8ff80beba00000001750d488bcbe81710f8"},
    {0x1FAA30,"48895c240848896c24104889742418574883ec20488d05c5e53900",true},
    {0x1FAB10,"48895c240848896c24104889742418574883ec208beac7411800000000",true},
}
for _,f in ipairs(fixtures) do
    local offset=0
    for h in f[2]:gmatch("%x%x") do
        memory[0x140000000+f[1]+(f[3] and 0 or -16)+offset]=tonumber(h,16)
        offset=offset+1
    end
end
function EEex_ReadU8(address)
    reads=reads+1
    if address==fail_signature_at then return 255 end
    return memory[address] or 0
end
function EEex_Malloc(size) events[#events+1]="allocate"; malloc_size=size; return 1000 end
function EEex_Write64(a,b) memory[a]=b end
function EEex_ReadU64(a) return memory[a] or 0 end
EEex_ReadU16, EEex_ReadU32 = EEex_ReadU64, EEex_ReadU64
EEex_Read32 = EEex_ReadU64
function EEex_DisableCodeProtection() events[#events+1]="unprotect" end
function EEex_EnableCodeProtection() events[#events+1]="protect" end
local count=0
local function hook(address,code)
    count=count+1; events[#events+1]="hook"..count
    if count==fail_hook then error("ASSEMBLER_FAILED") end
    if not omit_detour then memory[address]=0xE9; memory[address+1]=5000 end
end
EEex_HookBeforeCall, EEex_HookAfterCall = hook, hook
function EEex_HookBeforeRestore(address, delay, size, return_delay, code)
    restore_calls[#restore_calls+1]={address,delay,size,return_delay}
    hook(address,code)
end
'''


class ProbeGuards(unittest.TestCase):
    def run_case(self, case):
        for cls in (Lua51, Lua53, LuaJIT):
            with self.subTest(runtime=cls.__module__):
                lua = cls(unpack_returned_tuples=True)
                lua.execute(SETUP)
                lua.globals().probe_path = (ROOT / "tools/probes/FLPRDrop.lua").as_posix()
                lua.execute(case)

    def test_third_site_mismatch_writes_nothing(self):
        self.run_case('''
            fail_signature_at=0x140210B03
            local ok,err=pcall(dofile,probe_path)
            assert(not ok and tostring(err):find("CODE_MISMATCH"))
            assert(#events==0 and FLPR_DROP_TRACE==nil)
        ''')

    def test_failed_partial_install_reprotects_and_prevents_retry(self):
        self.run_case('''
            fail_hook=2
            local ok,err=pcall(dofile,probe_path)
            assert(not ok and tostring(err):find("ASSEMBLER_FAILED"))
            assert(events[#events]=="protect" and memory[1000]==0)
            assert(not FLPR_DROP_TRACE.installed)
            local n=#events
            ok,err=pcall(dofile,probe_path)
            assert(not ok and tostring(err):find("ALREADY_INSTALLED"))
            assert(#events==n)
        ''')

    def test_install_starts_disarmed_and_is_one_shot(self):
        self.run_case('''
            local report=dofile(probe_path)
            assert(report.installed and not report.armed and report.sites==3)
            assert(report.capacity==256 and not report.lifetime)
            assert(FLPR_DROP_TRACE.inspect().schema=="flpr-drop-trace-v1")
            assert(malloc_size==32+256*64 and #restore_calls==0)
            assert(memory[1000]==0 and events[#events]=="protect")
            FLPR_DROP_TRACE.arm(); assert(memory[1000]==1)
            FLPR_DROP_TRACE.stop(); assert(memory[1000]==0)
            assert(not pcall(FLPR_DROP_TRACE.arm))
        ''')

    def test_overflow_report_is_bounded(self):
        self.run_case('''
            dofile(probe_path)
            memory[1008]=300
            local report=FLPR_DROP_TRACE.inspect()
            assert(report.overflow and #report.events==256 and not report.armed)
        ''')

    def test_wrong_campaign_writes_nothing(self):
        self.run_case('''
            FLPR.Host.controller.manifest.revision="another"
            assert(not pcall(dofile,probe_path))
            assert(#events==0 and reads==0)
        ''')

    def test_silent_detour_failure_reprotects_and_stays_disarmed(self):
        self.run_case('''
            omit_detour=true
            local ok,err=pcall(dofile,probe_path)
            assert(not ok and tostring(err):find("DETOUR_NOT_WRITTEN"))
            assert(events[#events]=="protect" and memory[1000]==0)
            assert(not FLPR_DROP_TRACE.installed)
        ''')

    def test_active_writer_makes_snapshot_unstable(self):
        self.run_case('''
            dofile(probe_path)
            memory[1024]=1
            assert(not FLPR_DROP_TRACE.inspect().stable)
            memory[1024]=0
            assert(FLPR_DROP_TRACE.inspect().stable)
        ''')

    def test_lifetime_installs_two_complete_entry_instructions(self):
        self.run_case('''
            FLPR.Host.controller.manifest.revision=
                "c0acc859837a30315637e16d73c427393ca2694d0cd54eeb7d45ed82eee78e91"
            local report=assert(loadfile(probe_path))({lifetime=true})
            assert(report.installed and report.lifetime and not report.armed)
            assert(report.sites==5 and report.capacity==4096)
            assert(malloc_size==32+4096*64 and memory[1000]==0)
            assert(FLPR_DROP_TRACE.inspect().schema=="flpr-drop-lifetime-trace-v1")
            assert(#restore_calls==2)
            assert(restore_calls[1][1]==0x1401FAA30 and restore_calls[2][1]==0x1401FAB10)
            for _,call in ipairs(restore_calls) do
                assert(call[2]==0 and call[3]==5 and call[4]==5)
            end
            assert(events[#events]=="protect")
        ''')

    def test_lifetime_last_site_mismatch_writes_nothing(self):
        self.run_case('''
            FLPR.Host.controller.manifest.revision=
                "c0acc859837a30315637e16d73c427393ca2694d0cd54eeb7d45ed82eee78e91"
            fail_signature_at=0x1401FAB10+28
            local ok,err=pcall(assert(loadfile(probe_path)),{lifetime=true})
            assert(not ok and tostring(err):find("CODE_MISMATCH"))
            assert(reads>0 and #events==0 and #restore_calls==0)
            assert(FLPR_DROP_TRACE==nil)
        ''')

    def test_lifetime_destructor_hook_failures_reprotect_and_prevent_retry(self):
        for failing_hook in (4, 5):
            with self.subTest(failing_hook=failing_hook):
                self.run_case('''
                    FLPR.Host.controller.manifest.revision=
                        "c0acc859837a30315637e16d73c427393ca2694d0cd54eeb7d45ed82eee78e91"
                    fail_hook=FAIL_HOOK
                    local chunk=assert(loadfile(probe_path))
                    local ok,err=pcall(chunk,{lifetime=true})
                    assert(not ok and tostring(err):find("ASSEMBLER_FAILED"))
                    assert(events[#events]=="protect" and memory[1000]==0)
                    assert(not FLPR_DROP_TRACE.installed)
                    assert(#restore_calls==FAIL_HOOK-3)
                    local n=#events
                    ok,err=pcall(chunk,{lifetime=true})
                    assert(not ok and tostring(err):find("ALREADY_INSTALLED"))
                    assert(#events==n and memory[1000]==0)
                '''.replace("FAIL_HOOK", str(failing_hook)))

    def test_lifetime_requires_quiet_revision_and_no_held_watch(self):
        for precondition in (
            "-- non-quiet revision is the default",
            '''FLPR.Host.controller.manifest.revision=
                "c0acc859837a30315637e16d73c427393ca2694d0cd54eeb7d45ed82eee78e91"
                FLPR.Held={item="watched"}''',
        ):
            with self.subTest(precondition=precondition):
                self.run_case(precondition + '''
                    local ok,err=pcall(assert(loadfile(probe_path)),{lifetime=true})
                    assert(not ok and tostring(err):find("REQUIRES_QUIET_DIAGNOSTIC"))
                    assert(#events==0 and reads==0 and FLPR_DROP_TRACE==nil)
                ''')

    def test_invalid_lifetime_options_write_nothing(self):
        for option in ("true", '{lifetime="yes"}', "{unknown=true}"):
            with self.subTest(option=option):
                self.run_case('''
                    local ok,err=pcall(assert(loadfile(probe_path)),__ARG__)
                    assert(not ok and tostring(err):find("BAD_OPTIONS"))
                    assert(#events==0 and reads==0 and FLPR_DROP_TRACE==nil)
                '''.replace("__ARG__", option))


if __name__ == "__main__":
    unittest.main()
