"""Lab-only automation (tools/lab/M_FLPRQA.lua) against the real runtime bootstrap.

Neither tools/lab file is installed for players or packaged; a lab stages both
into override next to component 1500 and the EEex Remote Console.
"""
from pathlib import Path
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT

from test_campaign_native_host import CASES as HOST_FIXTURE, CORE, HOST, GLOBALS, BOOTSTRAP, UI

ROOT = Path(__file__).resolve().parents[1]
LAB = (ROOT / "tools/lab/M_FLPRQA.lua").read_text(encoding="utf-8")
LAB_CASES = r'''
-- Loads the lab module after the runtime, as a lab's override does, and
-- returns the callbacks it hands to the QA runner.
local function lab(f)
    local previous,api=loadfile
    loadfile=function(path)
        if path=='override/FLPRQA.lua' then return function() return {new=function(callbacks)
            api=callbacks;return {poll=function() f.qa_polls=(f.qa_polls or 0)+1 end}
        end} end end
        return previous(path)
    end
    EEexRemote={Poll=function() f.remote_polls=(f.remote_polls or 0)+1; return "" end}
    EEex_Menu_AddBeforeUIItemRenderListener=function(name,fn) f.render_listener={name=name,fn=fn} end
    assert((loadstring or load)(LAB_SOURCE))()
    assert(FLPRLab.Runner()==FLPRLab.Runner() and api)
    return api
end
function cases.lab_save_ready_allows_protected_running_campaign_without_completing_it(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text)
    local ready=lab(f).world_ready
    local phases={Core.PHASE.confirmed,Core.PHASE.protected,Core.PHASE.kept}
    local active=true
    FLPR.Host.status='running'
    FLPR.Host.controller={manifest={revision=f.manifest.revision,sources={{},{},{}}},
        assignments=function()
            return {{source=1,source_phase=phases[1],delivery_phase=0},
                {source=2,source_phase=phases[2],delivery_phase=0},
                {source=3,source_phase=phases[3],delivery_phase=Core.PHASE.kept}}
        end,
        source_guard_active=function(_,index) assert(index==2);return active end}
    f.values[Core.G.journal]=0
    assert(ready(f.manifest.revision) and FLPR.Host.status=='running')
    assert(not ready(string.rep('f',64)))
    for _,field in ipairs({'pending','guard_pending'}) do
        FLPR.Host[field]={};assert(not ready(f.manifest.revision));FLPR.Host[field]=nil
    end
    f.values[Core.G.journal]=2;assert(not ready(f.manifest.revision));f.values[Core.G.journal]=0
    active=false;assert(not ready(f.manifest.revision));active=true
    phases[1]=Core.PHASE.pending;assert(not ready(f.manifest.revision));phases[1]=Core.PHASE.confirmed
    FLPR.Disabled='fixture failure';assert(not ready(f.manifest.revision));FLPR.Disabled=nil
    assert(ready(f.manifest.revision) and FLPR.Host.status=='running')
end
function cases.lab_remote_poll_runs_qa_and_is_blocked_during_save_and_load(Core,Host,Globals,text)
    local f=bootstrap(Core,Host,Globals,text)
    lab(f)
    for _,blocked in ipairs({"SAVE","SAVE_NEWSAVE","LOAD"}) do
        Infinity_IsMenuOnStack=function(name) return name==blocked and 1 or nil end
        assert(EEexRemote.Poll()=="")
    end
    assert(f.remote_polls==nil and f.qa_polls==3)
    Infinity_IsMenuOnStack=function(name) return name=="WORLD_ACTIONBAR" and 1 or nil end
    EEexRemote.Poll(); assert(f.remote_polls==1 and f.qa_polls==4)
    -- The chapter narration hides the world UI; its text item keeps polling.
    assert(f.render_listener.name=="text_CHAPTERSCROLL_item")
    f.render_listener.fn(); assert(f.remote_polls==2 and f.qa_polls==5)
end
'''
CASE_NAMES = ("lab_save_ready_allows_protected_running_campaign_without_completing_it",
              "lab_remote_poll_runs_qa_and_is_blocked_during_save_and_load")


class LabQaModuleTests(unittest.TestCase):
    def test_lab_module(self):
        fixture = HOST_FIXTURE.rsplit("return cases", 1)[0] + LAB_CASES + "\nreturn cases"
        for runtime in (Lua51, Lua53, LuaJIT):
            for name in CASE_NAMES:
                lua = runtime(unpack_returned_tuples=True)
                with self.subTest(runtime=runtime.__module__, case=name):
                    lua.execute(UI)
                    lua.globals().LAB_SOURCE = LAB
                    lua.execute(fixture)[name](lua.execute(CORE), lua.execute(HOST), lua.execute(GLOBALS), BOOTSTRAP)


if __name__ == "__main__":
    unittest.main()
