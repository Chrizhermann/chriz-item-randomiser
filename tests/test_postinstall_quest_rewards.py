"""Runtime guards for the three original quest rewards that CIR prepares."""

from pathlib import Path
import sys
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from flir_config import quest_rewards as quests

SCRIPT = (ROOT / "prototype" / "FLPRScript.lua").read_text(encoding="utf-8")
SCRIPT_CASE = r'''
return function(Script,id,quest,resource,selected,consumed,kept,expected,actual,legacy,extended)
    local p={resref=quest=='pool' and 'blun12' or (quest=='altar' and 'blun23' or 'ring09'),
        charges={0,0,0},expiration=0,flags=0}
    local globals={FLPR_FRESH=1}
    local source={id=id,quantity=1,resource_sha256=string.rep('1',64),
        record={id=id,kind=legacy and 'scripted' or 'authored-source',
            adapter_id=legacy and 'blpool-v1' or 'original-source-v1',
            resource=resource,selector=legacy and 'pool-reward' or id:sub(17),record=p}}
    local endpoint={kind='guarded-script',adapter_id=legacy and 'blpool-v1' or 'original-quest-v1',payload=p,
        quest=quest,resource=resource,selected_global=selected,consumed_global=consumed}
    local bundle={manifest={revision=string.rep('a',64),sources={source}},
        fixture={sources={[id]=endpoint}}}
    local api={EEex_GameState_GetGlobalInt=function(key) return globals[key] or 0 end,
        EEex_GameState_SetGlobalInt=function(key,value) globals[key]=value end}
    local old=io.open
    io.open=function(path,mode)
        assert(path=='override/'..resource:sub(5):upper()..'.BCS' and mode=='rb')
        return {read=function(_,request) assert(request=='*a'); return actual end,
                close=function() return true end}
    end
    local ok,member=pcall(Script.new,api,bundle,id,expected,extended)
    io.open=old
    assert(ok,member)
    member.installed=true
    local controller={manifest=bundle.manifest,source_is_kept=function() return kept end}
    assert(member:prepare_new(bundle,controller))
    if kept then assert(globals[selected]==nil)
    else assert(globals[selected]==1) end
    assert(globals[consumed]==nil)
    local adapter=member:adapter(bundle)
    if not kept then
        local session,token={},{}
        local request={kind='source-guard',mode='observe-only',index=1,
            revision=bundle.manifest.revision,source_id=id,
            resource_sha256=source.resource_sha256,quantity=1,payload=p,
            session=session,token=token}
        local receipt=assert(adapter:observe_guard(request))
        assert(receipt.active() and receipt.exact_source_bound==1)
        request.kind='guarded-source-removal'; request.guard_generation=member.generation
        local missing,reason=adapter:observe_removal(request)
        assert(missing==nil and reason=='SOURCE_NOT_LOADED')
        globals[consumed]=1
        assert(adapter:observe_removal(request).exact_source_event==1)
        member:reset()
        assert(globals[selected]==1 and globals[consumed]==1 and not receipt.active())
    end
    return true
end
'''


class QuestRewardTests(unittest.TestCase):
    def test_new_lua_guards_use_independent_selected_and_consumed_globals(self):
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime(unpack_returned_tuples=True)
            script, case = lua.execute(SCRIPT), lua.execute(SCRIPT_CASE)
            for name in ("troalt", "ppbook", "blpool"):
                spec = quests.QUESTS[name]
                source_id = spec["source_ids"][0]
                for kept in (False, True):
                    with self.subTest(runtime=runtime.__module__, quest=name, kept=kept):
                        self.assertTrue(case(script, source_id, spec["quest"], spec["resource"],
                                             spec["selected_global"], spec["consumed_global"],
                                             kept, "EXPECTED_SCRIPT_BYTES", "EXPECTED_SCRIPT_BYTES", False))

    def test_script_changed_after_export_is_refused(self):
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime(unpack_returned_tuples=True)
            script, case = lua.execute(SCRIPT), lua.execute(SCRIPT_CASE)
            for name, legacy in (("blpool", True), ("troalt", False)):
                spec = quests.QUESTS[name]
                with self.subTest(runtime=runtime.__module__, quest=name), \
                        self.assertRaisesRegex(Exception, "CAMPAIGN_SCRIPT_PATCH_CHANGED"):
                    case(script, spec["source_ids"][0], spec["quest"], spec["resource"],
                         spec["selected_global"], spec["consumed_global"],
                         False, "EXPORTED_SCRIPT_BYTES", "SCRIPT_REPLACED_LATER", legacy)

    def test_an_installed_quest_script_accepts_the_version_apply_extended_it_to(self):
        # Apply adds the book's scroll routes to the installer's PPBOOK.BCS and
        # keeps both versions; a revision exported with the installer's
        # accepts Apply's, and nothing else.
        spec = quests.QUESTS["ppbook"]
        args = (spec["source_ids"][0], spec["quest"], spec["resource"], spec["selected_global"],
                spec["consumed_global"], False)
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime(unpack_returned_tuples=True)
            script, case = lua.execute(SCRIPT), lua.execute(SCRIPT_CASE)

            def versions(resource="bcs:ppbook", installed="INSTALLED", applied="APPLIED"):
                return lua.table_from({resource: lua.table_from({"installed": installed, "applied": applied})})
            with self.subTest(runtime=runtime.__module__):
                self.assertTrue(case(script, *args, "INSTALLED", "APPLIED", False, versions()))
                self.assertTrue(case(script, *args, "INSTALLED", "INSTALLED", False, versions()))
                self.assertTrue(case(script, *args, "APPLIED", "APPLIED", False, versions()))
            for label, expected, actual, extended in (
                    ("no versions", "INSTALLED", "APPLIED", None),
                    ("another installed", "OLDER", "APPLIED", versions()),
                    ("another applied", "INSTALLED", "CHANGED", versions()),
                    ("another script", "INSTALLED", "APPLIED", versions("bcs:troalt")),
                    ("only one", "INSTALLED", "APPLIED", lua.table_from({"bcs:ppbook": False}))):
                with self.subTest(runtime=runtime.__module__, case=label), \
                        self.assertRaisesRegex(Exception, "CAMPAIGN_SCRIPT_PATCH_CHANGED"):
                    case(script, *args, expected, actual, False, extended)
            # The pool's legacy adapter has no installer-prepared script.
            pool = quests.QUESTS["blpool"]
            with self.subTest(runtime=runtime.__module__, case="legacy"), \
                    self.assertRaisesRegex(Exception, "CAMPAIGN_SCRIPT_PATCH_CHANGED"):
                case(script, pool["source_ids"][0], pool["quest"], pool["resource"], pool["selected_global"],
                     pool["consumed_global"], False, "INSTALLED", "APPLIED", True, versions("bcs:blpool"))


if __name__ == "__main__":
    unittest.main()
