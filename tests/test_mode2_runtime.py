"""Mode 2 uses the original speaking character or scripted container."""
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT

from test_postinstall_quest_rewards import SCRIPT, SCRIPT_CASE
from test_spell_runtime import NATIVE, NATIVE_FIXTURE, NATIVE_CASES


CASES = r'''
function cases.dialogue_marks_the_actual_speaker_without_spell_service(N)
    local f=key_world(N,true)
    f.api.FLPR=nil;f.api.FLPRKEY=nil
    f.creature.locals={FLPR_RM2_14=1}
    f.api.EEex_GameState_GetGlobalInt=function(key) assert(key=='FLPR_EM2_14');return f.claimed end
    local target=f.fixture.targets[f.target_id]
    target.adapter_id='mode2-reward-v1'
    target.event_global='FLPR_EM2_14';target.recipient_local='FLPR_RM2_14'
    f.adapter=N.new(f.api,f.fixture)
    local ready,reason=f.adapter:inspect_target(f.target_id,f.source_ids[1],1)
    fail(ready,reason,'QUEST_NOT_CLAIMED')
    f.claimed=1
    assert(f.adapter:inspect_target(f.target_id,f.source_ids[1],1))
    f.boundary();assert(f.adapter:execute(f.command(1)))
    assert(#f.queue==1 and f.queue[1].m_targetId==3)
    assert(not f.source1.slots[18])
end

function cases.scripted_container_waits_for_its_grant_event(N)
    local f=setup(N)
    local target=f.fixture.targets[f.target_id]
    target.kind='container';target.script_name='Container 1'
    target.x=598;target.y=1228;target.container_type=2;target.claim_global='FLPR_EM2_5'
    f.objects[3]={address=201,sprite=false,m_lstItems={},m_containerType=2,
        m_pos={x=598,y=1228},m_scriptName={get=function() return 'Container 1' end}}
    f.claimed=0
    f.api.EEex_GameState_GetGlobalInt=function(key) assert(key=='FLPR_EM2_5');return f.claimed end
    f.adapter=N.new(f.api,f.fixture)
    local ready,reason=f.adapter:inspect_target(f.target_id,f.source_ids[1],1)
    fail(ready,reason,'QUEST_NOT_CLAIMED')
    assert(#f.calls==0 and #f.queue==0)
    f.claimed=1
    assert(f.adapter:inspect_target(f.target_id,f.source_ids[1],1))
    assert(f.adapter.reservations[string.rep('c',64)].owner.id==3)
end
'''


class Mode2RuntimeTests(unittest.TestCase):
    def test_alternate_dialogue_paths_are_checked_together(self):
        case = SCRIPT_CASE.replace("legacy and 'blpool-v1' or 'original-quest-v1'",
                                   "legacy and 'blpool-v1' or 'mode2-reward-v1'")
        case = case.replace("local bundle=", "endpoint.resources={'dlg:hendak','dlg:lehtin'}\n    local bundle=")
        case = case.replace("path=='override/'..resource:sub(5):upper()..'.BCS'",
                            "(path=='override/HENDAK.DLG' or path=='override/LEHTIN.DLG')")
        case = case.replace('return actual end', 'return actual[path] end')
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime(unpack_returned_tuples=True)
            expected = lua.table_from({'dlg:hendak': 'HENDAK', 'dlg:lehtin': 'LEHTIN'})
            actual = lua.table_from({'override/HENDAK.DLG': 'HENDAK', 'override/LEHTIN.DLG': 'LEHTIN'})
            run = lua.execute(case)
            args = (lua.execute(SCRIPT), 'original-source:bg2:mode2:item:30', 'hendak', 'dlg:hendak',
                    'FLPR_QM2_30', 'FLPR_XM2_30', False, expected, actual, False)
            with self.subTest(runtime=runtime.__module__):
                self.assertTrue(run(*args))
                actual['override/LEHTIN.DLG'] = 'UNPATCHED'
                with self.assertRaisesRegex(Exception, 'CAMPAIGN_SCRIPT_PATCH_CHANGED'):
                    run(*args)

    def test_dialogue_source_suppression_and_saved_flags(self):
        case = SCRIPT_CASE.replace("legacy and 'blpool-v1' or 'original-quest-v1'",
                                   "legacy and 'blpool-v1' or 'mode2-reward-v1'")
        case = case.replace("..'.BCS'", "..'.DLG'")
        for runtime in (Lua51, Lua53, LuaJIT):
            for kept in (False, True):
                lua = runtime(unpack_returned_tuples=True)
                with self.subTest(runtime=runtime.__module__, kept=kept):
                    self.assertTrue(lua.execute(case)(lua.execute(SCRIPT),
                        'original-source:bg2:mode2:item:14', 'uhogre01', 'dlg:uhogre01',
                        'FLPR_QM2_14', 'FLPR_XM2_14', kept, 'PATCHED', 'PATCHED', False))

    def test_native_reward_recipient_and_timing(self):
        fixture = NATIVE_FIXTURE.rsplit('return cases', 1)[0] + NATIVE_CASES + CASES + '\nreturn cases'
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime(unpack_returned_tuples=True)
            cases = lua.execute(fixture)
            for name in ('dialogue_marks_the_actual_speaker_without_spell_service',
                         'scripted_container_waits_for_its_grant_event'):
                with self.subTest(runtime=runtime.__module__, case=name):
                    cases[name](lua.execute(NATIVE))
