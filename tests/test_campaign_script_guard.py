"""Offline BLPOOL script guard protocol on Lua 5.1, 5.3, and LuaJIT."""

from pathlib import Path
import sys
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from engine_sites import load  # noqa: E402

CORE = (ROOT / "prototype/FLPRCore.lua").read_text(encoding="utf-8")
GATE = (ROOT / "prototype/FLPRGat.lua").read_text(encoding="utf-8")
SCRIPT = (ROOT / "prototype/FLPRScript.lua").read_text(encoding="utf-8")
CASES = r'''
local cases={}
local ID='bcs:blpool/grant/pool-reward'
local REV=string.rep('a',64)
local HASH=string.rep('1',64)
local BYTES='EXPECTED_BLPOOL_BCS_BYTES'

local function fixture(Core,Gate,Script)
    local f={globals={},values={},callbacks={}}
    local function payload()
        return {resref='flpri1',charges={2,0,0},expiration=0,flags=1}
    end
    f.bundle={manifest={schema=Core.SCHEMA,state='prototype-not-playable',
        revision=REV,bound_revision=string.rep('b',64),world_fingerprint=string.rep('c',64),
        profile_id='script-guard-test',
        sources={{id=ID,quantity=1,resource_sha256=HASH,
            record={id=ID,kind='scripted',adapter_id='blpool-v1',resource='bcs:blpool',
                selector='pool-reward',record=payload()}}},
        targets={{id='are:target/container/0',capacity=1,resource_sha256=string.rep('2',64),
            record={id='are:target/container/0',kind='container'}}}},
        fixture={revision=REV,source_gate={schema='flpr-unvisited-fixture-gate-v1',source_id=ID},
            sources={[ID]={kind='guarded-script',adapter_id='blpool-v1',payload=payload()}}}}
    f.storage={}
    function f.storage:get(key) return f.values[key] or 0 end
    function f.storage:set(key,value) f.values[key]=value end
    -- No engine image: the gate finds no location, which a script source
    -- does not need. Launch still keeps the saved-store record.
    f.api={EEex_Active=true,
        EEex_Write8=function() end,EEex_Write64=function() end,
        EEex_Malloc=function() return 0x1000 end,
        EEex_GenLuaCall=function() error('a script source needs no game hook') end,
        EEex_DisableCodeProtection=function() end,
        EEex_EnableCodeProtection=function() end,
        EEex_GameState_GetGlobalInt=function(key) return f.globals[key] or 0 end,
        EEex_GameState_SetGlobalInt=function(key,value) f.globals[key]=value end,
        EEex_GameState_AddDestroyedListener=function(callback)
            f.callbacks[#f.callbacks+1]=callback
        end}
    -- A script source needs no native checks.
    local function native() error('a script source builds no native code') end
    f.pristine={scripts={[REV]={[ID]=BYTES}}}
    function f:controller_new()
        return assert(Core.new(self.storage,function(revision)
            if revision==REV then return self.bundle.manifest end
        end))
    end
    f.controller=f:controller_new()
    function f:install()
        local old=io.open
        io.open=function(path,mode)
            assert(path=='override/BLPOOL.BCS' and mode=='rb')
            return {read=function(_,request) assert(request=='*a'); return BYTES end,
                    close=function() return true end}
        end
        local ok,value=pcall(Gate.install,self.api,self.bundle,native,native,self.pristine,Script)
        io.open=old
        assert(ok,value)
        self.gate=value
        return value
    end
    function f:capture()
        assert(self.controller:capture(self.bundle.manifest,17,'confirmed-new-game'))
        self.globals.FLPR_FRESH=1
    end
    function f:select()
        self:capture()
        assert(self.gate:prepare_new(self.bundle,self.controller))
        return self.gate:adapter(self.bundle)
    end
    return f
end

function cases.selected_campaign_requires_fresh_capture_and_exact_patch(Core,Gate,Script)
    local f=fixture(Core,Gate,Script)
    local old=io.open
    io.open=function() return {read=function() return 'CHANGED' end,close=function() return true end} end
    local ok,reason=pcall(Script.new,f.api,f.bundle,ID,BYTES)
    io.open=old
    assert(not ok and tostring(reason):find('CAMPAIGN_SCRIPT_PATCH_CHANGED',1,true))
    local gate=f:install()
    assert(f.globals.FLPR_QPOOL==nil and f.globals.FLPR_XPOOL==nil)
    ok,reason=pcall(gate.prepare_new,gate,f.bundle,f.controller)
    assert(not ok and tostring(reason):find('CAMPAIGN_SCRIPT_CAPTURE_REQUIRED',1,true))
    f:capture()
    assert(gate:prepare_new(f.bundle,f.controller))
    assert(f.globals.FLPR_QPOOL==1 and f.globals.FLPR_XPOOL==nil)
    ok,reason=pcall(gate.prepare_new,gate,f.bundle,f.controller)
    assert(not ok and tostring(reason):find('CAMPAIGN_SCRIPT_ALREADY_STARTED',1,true))
end

function cases.waits_for_one_authored_event_then_commits_exact_receipt(Core,Gate,Script)
    local f=fixture(Core,Gate,Script); f:install()
    local adapter=f:select()
    local request=assert(f.controller:source_guard_request(1))
    local receipt=assert(adapter:observe_guard(request))
    assert(receipt.active() and receipt.pre_access==1 and receipt.exact_source_bound==1)
    assert(not f.controller:confirm_source_guard(receipt)) -- later observation boundary
    f.controller:on_observation_boundary()
    assert(f.controller:confirm_source_guard(receipt))
    assert(f.values.flpr_s0001==Core.PHASE.protected+1) -- stored phase is one-based
    local removal=assert(f.controller:guarded_source_removal_request(1))
    local result,reason=adapter:observe_removal(removal)
    assert(result==nil and reason=='SOURCE_NOT_LOADED')
    assert(f.values.flpr_s0001==Core.PHASE.protected+1)
    f.globals.FLPR_XPOOL=1 -- authored branch has consumed its single reward
    result=assert(adapter:observe_removal(removal))
    assert(result.exact_source_event==1 and result.removed==1 and result.remaining==0)
    assert(not f.controller:confirm_guarded_source_removal(result)) -- later boundary
    f.controller:on_observation_boundary()
    assert(f.controller:confirm_guarded_source_removal(result))
    assert(f.values.flpr_s0001==Core.PHASE.confirmed+1)
    assert(not f.controller:guarded_source_removal_request(1))
end

function cases.reload_preserves_saved_globals_and_revokes_old_lease(Core,Gate,Script)
    local f=fixture(Core,Gate,Script); f:install()
    local adapter=f:select()
    local request=assert(f.controller:source_guard_request(1))
    local receipt=assert(adapter:observe_guard(request))
    f.controller:on_observation_boundary()
    assert(f.controller:confirm_source_guard(receipt) and receipt.active())
    f.globals.FLPR_XPOOL=1
    f.callbacks[1]() -- game state destroyed during save load
    assert(f.globals.FLPR_QPOOL==1 and f.globals.FLPR_XPOOL==1)
    assert(not receipt.active())
    local stale,reason=adapter:observe_removal(request)
    assert(stale==nil and reason=='CAMPAIGN_SCRIPT_REQUEST_MISMATCH')
    f.controller=f:controller_new()
    assert(f.controller:restore())
    local fresh=f.gate:adapter(f.bundle)
    local new_request=assert(f.controller:source_guard_request(1))
    local new_receipt=assert(fresh:observe_guard(new_request))
    f.controller:on_observation_boundary()
    assert(f.controller:confirm_source_guard(new_receipt))
    assert(new_receipt.active() and not receipt.active())
end

return cases
'''


class CampaignScriptGuardTests(unittest.TestCase):
    def test_lua_cases(self):
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime(unpack_returned_tuples=True)
            core, gate, script, cases = (
                lua.execute(CORE), load(lua, GATE), lua.execute(SCRIPT), lua.execute(CASES))
            for name in sorted(cases.keys()):
                with self.subTest(runtime=runtime.__module__, case=name):
                    cases[name](core, gate, script)


if __name__ == "__main__":
    unittest.main()
