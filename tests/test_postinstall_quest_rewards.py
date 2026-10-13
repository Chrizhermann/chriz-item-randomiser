"""Portable checks for the three reviewed compiled quest-reward blocks."""

from pathlib import Path
import hashlib
import sys
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from flir_config import quest_rewards as quests
from flir_config.profiles import ProfileError
from flir_config.scripted_sources import patch_blpool
from test_postinstall_blpool_script import compiled_blpool

SCRIPT = (ROOT / "prototype" / "FLPRScript.lua").read_text(encoding="utf-8")
SCRIPT_CASE = r'''
return function(Script,id,quest,resource,selected,consumed,kept,expected,actual,legacy)
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
    local ok,member=pcall(Script.new,api,bundle,id,expected)
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


def obj(value=0):
    return f'0 0 0 0 0 0 0 {value} 0 0 0 0 ""OB\n'


def trigger(op, value=0, string="", target=0):
    return f'TR\n{op} {value} 0 0 0 "{string}" "" OB\n{obj(target)}TR\n'


def action(op, value=0, string="", target=0):
    return (f'AC\n{op}OB\n{obj()}' + f'OB\n{obj(target)}' + f'OB\n{obj()}' +
            f'{value} 0 0 0 0"{string}" "" AC\n')


def block(conditions, actions):
    return 'CR\nCO\n' + conditions + 'CO\nRS\nRE\n100' + actions + 'RE\nRS\nCR\n'


def compiled_altar():
    actions = (action(169, string="MISC9D") + action(30, 1, "GLOBALSecretThree") +
               action(26, string="EFF_P92") + action(254) +
               action(82, string="BLUN23") + action(63) + action(151, 47724))
    return ('SC\n' + block(trigger(16501, string="MISC9D", target=1), actions) + 'SC\n').encode()


def compiled_book():
    ordinary = [block(trigger(112), action(151, 50657)) for _ in range(6)]
    reward = (action(151, 50658) + action(109, 1, "AR1513page") +
              action(140, string="ring09", target=17) +
              action(140, string="scrl8z", target=17) +
              action(140, string="scrl9b", target=17))
    ordinary.append(block(trigger(112) + trigger(16399, 5, "AR1513page"), reward))
    ordinary.append(block(trigger(112), action(151, 14702)))
    return ('SC\n' + ''.join(ordinary) + 'SC\n').encode()


class QuestRewardTests(unittest.TestCase):
    def patch_synthetic(self, name, original):
        old_original, old_patched = quests.ORIGINAL_HASHES[name], quests.PATCHED_HASHES[name]
        self.addCleanup(quests.ORIGINAL_HASHES.__setitem__, name, old_original)
        self.addCleanup(quests.PATCHED_HASHES.__setitem__, name, old_patched)
        quests.ORIGINAL_HASHES[name] = hashlib.sha256(original).hexdigest()
        quests.PATCHED_HASHES[name] = ""
        patched = quests.patch_quest(name, original)
        quests.PATCHED_HASHES[name] = hashlib.sha256(patched).hexdigest()
        self.assertEqual(quests.patch_quest(name, patched), patched)
        return patched

    def test_altar_only_splits_grant_and_keeps_quest_progression(self):
        original = compiled_altar()
        patched = self.patch_synthetic("troalt", original)
        self.assertEqual(patched.count(b'0 0 0 0 0"BLUN23" "" AC'), 1)
        self.assertEqual(patched.count(b'GLOBALFLPR_XALTAR'), 1)
        self.assertEqual(patched.count(b'GLOBALFLPR_EALTAR'), 2)
        for value in (b'"MISC9D"', b'"GLOBALSecretThree"', b'"EFF_P92"', b'47724'):
            self.assertEqual(patched.count(value), original.count(value) * 2)
        with self.assertRaisesRegex(ProfileError, "changed"):
            quests.patch_quest("troalt", original.replace(b"BLUN23", b"BLUN24"))

    def test_book_only_splits_page_five_ring_and_keeps_both_scrolls(self):
        original = compiled_book()
        patched = self.patch_synthetic("ppbook", original)
        self.assertEqual(patched.count(b'0 0 0 0 0"ring09" "" AC'), 1)
        self.assertEqual(patched.count(b'GLOBALFLPR_XBOOK'), 1)
        self.assertEqual(patched.count(b'GLOBALFLPR_EBOOK'), 2)
        for item in (b'"scrl8z"', b'"scrl9b"'):
            self.assertEqual(patched.count(item), 2)
        prefix = original.split(b'CR\nCO\n')[0]
        self.assertTrue(patched.startswith(prefix))
        self.assertEqual(patched.count(b'CR\nCO\n'), original.count(b'CR\nCO\n') + 1)

    def test_pool_v2_extends_legacy_bytes_and_only_known_upgrade_is_compatible(self):
        original = compiled_blpool()
        legacy = patch_blpool(original)
        v2 = quests.patch_quest("blpool", original)
        self.assertEqual(v2, quests.patch_quest("blpool", legacy))
        self.assertEqual(quests.patch_quest("blpool", v2), v2)
        self.assertTrue(quests.is_compatible_patch(legacy, v2))
        self.assertFalse(quests.is_compatible_patch(legacy, v2.replace(b"2020", b"2021", 1)))
        self.assertEqual(v2.count(b'GLOBALFLPR_EPOOL'), 2)
        self.assertEqual(v2.count(b'GLOBALFLPR_XPOOL'), 1)
        self.assertEqual(v2.count(b'0 0 0 0 0"BLUN12" "" AC'), 1)

    def test_isolated_lab_evidence_if_present(self):
        root = ROOT / "work" / "quest-rewards-20260928"
        if not (root / "troalt.bcs").exists():
            self.skipTest("Isolated-lab evidence is not part of the source tree")
        for name in quests.QUESTS:
            original = (root / f"{name}.bcs").read_bytes()
            self.assertEqual(hashlib.sha256(original).hexdigest(), quests.ORIGINAL_HASHES[name])
            patched = quests.patch_quest(name, original)
            self.assertEqual(hashlib.sha256(patched).hexdigest(), quests.PATCHED_HASHES[name])

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

    def test_old_lua_blpool_revision_accepts_exact_v2_extension_only(self):
        legacy = patch_blpool(compiled_blpool()).decode("ascii")
        v2 = quests.patch_quest("blpool", compiled_blpool()).decode("ascii")
        spec = quests.QUESTS["blpool"]
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime(unpack_returned_tuples=True)
            script, case = lua.execute(SCRIPT), lua.execute(SCRIPT_CASE)
            with self.subTest(runtime=runtime.__module__):
                self.assertTrue(case(script, spec["source_ids"][0], spec["quest"], spec["resource"],
                                     spec["selected_global"], spec["consumed_global"],
                                     False, legacy, v2, True))
                with self.assertRaisesRegex(Exception, "CAMPAIGN_SCRIPT_PATCH_CHANGED"):
                    case(script, spec["source_ids"][0], spec["quest"], spec["resource"],
                         spec["selected_global"], spec["consumed_global"],
                         False, legacy, v2.replace("2020", "2021", 1), True)


if __name__ == "__main__":
    unittest.main()
