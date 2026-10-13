"""Reviewed Key spell patch and offline callback behavior on supported Lua VMs."""

import hashlib
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from flir_config import spell_rewards as spell
from flir_config.profiles import ProfileError

SCRIPT = (ROOT / "prototype" / "FLPRSpell.lua").read_text(encoding="utf-8")


def spell_fixture():
    data = bytearray(394)
    data[:8] = b"SPL V1  "
    struct.pack_into("<IHIHH", data, 0x64, 114, 1, 154, 0, 0)
    data[114 + 0xC] = 1  # Living target; never assume Player1.
    struct.pack_into("<HH", data, 114 + 0x1E, 5, 0)
    for index in range(5):
        data[154 + index * 48:154 + (index + 1) * 48] = bytes([index + 1]) * 48
    data[spell.EFFECT_OFFSET:spell.EFFECT_OFFSET + 48] = struct.pack(
        "<HBBIIBBI2B8s5I", 122, 2, 0, 1, 0, 9, 0, 0, 100, 0, b"ring41", 0, 0, 0, 0, 0)
    return bytes(data)


LUA_CASE = r'''
return function(Spell,selected,failure)
    local f={globals={},calls={},logs={}}
    local first={m_id=27,locals={}}
    local other={m_id=4,locals={}}
    local function effect()
        return {m_done=false,m_targetType=2,m_effectAmount=1,m_dWFlags=0,
            m_sourceId=88,m_sourceTarget=27,m_source={x=30,y=40},m_target={x=50,y=60},
            m_flags=4,m_sourceType=1,m_sourceRes={get=function() return 'SPIN621' end},
            m_sourceFlags=6,m_projectileType=1,m_slotNum=7,m_casterLevel=12,
            m_secondaryType=0,m_school=0}
    end
    if selected then f.globals.FLPR_QKEY=1 end
    local api={
        EEex_GameState_GetGlobalInt=function(key) return f.globals[key] or 0 end,
        EEex_GameState_SetGlobalInt=function(key,value) f.globals[key]=value end,
        EEex_Sprite_SetLocalInt=function(sprite,key,value) sprite.locals[key]=value end,
        EEex_Sprite_GetLocalInt=function(sprite,key) return sprite.locals[key] or 0 end,
        Infinity_Log=function(message) f.logs[#f.logs+1]=message end,
    }
    local current
    api.EEex_GameObject_ApplyEffect=function(sprite,args)
        assert(current.m_done==true,'callback must be one-shot before resolving another effect')
        assert(f.globals.FLPR_EKEY==nil,'first event must follow successful submission')
        if failure then error('deliberate engine submission failure') end
        f.calls[#f.calls+1]={sprite=sprite,args=args}
        assert(api.FLPRKEY(current,sprite)) -- Immediate effect resolution can be reentrant.
    end
    local service=Spell.install(api)
    assert(service.callback==api.FLPRKEY)
    current=effect()
    local ok,reason=api.FLPRKEY(current,first)
    if failure then
        assert(ok==nil and reason:find('deliberate engine submission failure',1,true))
        assert(service.last_error==reason and #f.logs==1 and current.m_done)
        assert(f.globals.FLPR_EKEY==nil and first.locals.FLPR_RKEY==nil)
        assert(#f.calls==0)
        return true
    end
    assert(ok and service.last_error==nil)
    assert(f.globals.FLPR_EKEY==1 and first.locals.FLPR_RKEY==1)
    if selected then assert(#f.calls==0 and f.globals.FLPR_XKEY==1)
    else
        assert(#f.calls==1 and f.globals.FLPR_XKEY==nil)
        local a=f.calls[1].args
        assert(f.calls[1].sprite==first and a.effectID==122 and a.res=='ring41')
        assert(a.targetType==2 and a.effectAmount==1 and a.dwFlags==0 and a.spellLevel==0)
        assert(a.durationType==9 and a.duration==0 and a.probabilityUpper==100 and a.probabilityLower==0)
        assert(a.numDice==0 and a.diceSize==0 and a.savingThrow==0 and a.saveMod==0 and a.special==0)
        assert(a.sourceID==88 and a.sourceTarget==27 and a.sourceX==30 and a.sourceY==40)
        assert(a.targetX==50 and a.targetY==60 and a.noSave==1 and a.immediateResolve==1)
        assert(a.m_flags==4 and a.m_sourceType==1 and a.m_sourceRes=='SPIN621' and a.m_sourceFlags==6)
        assert(a.m_projectileType==1 and a.m_slotNum==7 and a.m_casterLevel==12)
    end
    local count=#f.calls
    assert(api.FLPRKEY(current,first) and #f.calls==count) -- Permanent effect does not repeat.
    -- Reload preserves saved globals and recipient local, but object IDs may change.
    first.m_id=92
    service=Spell.install(api)
    api.EEex_GameObject_ApplyEffect=function(sprite,args)
        f.calls[#f.calls+1]={sprite=sprite,args=args}
    end
    current=effect()
    assert(api.FLPRKEY(current,other))
    assert(first.locals.FLPR_RKEY==1 and other.locals.FLPR_RKEY==nil)
    assert(f.globals.FLPR_EKEY==1)
    if selected then assert(#f.calls==0)
    else assert(#f.calls==count+1 and f.calls[#f.calls].sprite==other) end
    return true
end
'''


class SpellRewardTests(unittest.TestCase):
    def test_static_patch_changes_only_opcode_and_callback_resource_and_reexports(self):
        original = spell_fixture()
        expected = bytearray(original)
        struct.pack_into("<H", expected, spell.EFFECT_OFFSET, 402)
        expected[spell.EFFECT_OFFSET + 0x14:spell.EFFECT_OFFSET + 0x1C] = b"FLPRKEY\0"
        with patch.object(spell, "ORIGINAL_SHA256", hashlib.sha256(original).hexdigest()), \
                patch.object(spell, "PATCHED_SHA256", hashlib.sha256(expected).hexdigest()):
            result = spell.patch_key_spell(original)
            self.assertEqual(result, expected)
            self.assertEqual(spell.patch_key_spell(result), result)
            self.assertEqual(spell.original_key_spell(result), original)
            self.assertEqual(result[spell.EFFECT_OFFSET + 2:spell.EFFECT_OFFSET + 0x14],
                             original[spell.EFFECT_OFFSET + 2:spell.EFFECT_OFFSET + 0x14])
            self.assertEqual(result[:spell.EFFECT_OFFSET], original[:spell.EFFECT_OFFSET])
            self.assertEqual(result[spell.EFFECT_OFFSET + 0x1C:], original[spell.EFFECT_OFFSET + 0x1C:])
            with self.assertRaisesRegex(ProfileError, "changed"):
                spell.patch_key_spell(result[:-1] + b"changed")

    def test_callback_preserves_unselected_reward_and_gates_selected_reward(self):
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime(unpack_returned_tuples=True)
            module, case = lua.execute(SCRIPT), lua.execute(LUA_CASE)
            for selected in (False, True):
                with self.subTest(runtime=runtime.__module__, selected=selected):
                    self.assertTrue(case(module, selected, False))

    def test_submission_failure_is_visible_to_host_and_never_publishes_success(self):
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime(unpack_returned_tuples=True)
            with self.subTest(runtime=runtime.__module__):
                self.assertTrue(lua.execute(LUA_CASE)(lua.execute(SCRIPT), False, True))


if __name__ == "__main__":
    unittest.main()
