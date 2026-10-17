"""Emulate the compiled campaign-GLOBAL gate policy against synthetic GAM bytes.

Only pure Lua generation and x86 instructions are covered. The EEex hook ABI,
watchdog macro, game memory lifetime, and native installation are not modeled.
"""
from functools import lru_cache
from pathlib import Path
import re
import unittest

from keystone import Ks, KS_ARCH_X86, KS_MODE_64
from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT

from tests.test_source_gate_assembly import (
    BUFFER, CODES, INPUT, IMAGE, ITEMS, GateMachine, generated as legacy_generated,
)
from tests.engine_sites import gate_targets


ROOT = Path(__file__).resolve().parents[1]
REVISION = "".join(f"{0x1000 + index:04x}" for index in range(16))
RUNTIMES = (Lua51, Lua53, LuaJIT)


def expected_fields(revision=REVISION, source_index=1, phase=1):
    return ([('FLPR_STATE', 2), ('FLPR_GUARD', 1)]
            + [(f'FLPR_R{index:02}', int(revision[(index-1)*4:index*4], 16))
               for index in range(1, 17)]
            + [(f'FLPR_S{source_index:04}', phase)])


def factory(runtime, path):
    lua = runtime(unpack_returned_tuples=True)
    lua.globals().source_path = path.as_posix()
    return lua, lua.execute("return assert(loadfile(source_path))()")


@lru_cache(maxsize=2)
def generated_policy(source_index=1):
    results = []
    for runtime in RUNTIMES:
        lua, compile_policy = factory(runtime, ROOT / "tools/probes/FLPRCampaignPolicy.lua")
        lua.globals().source_path = (ROOT / "tools/probes/FLPRGateAsm.lua").as_posix()
        assemble = lua.execute("return assert(loadfile(source_path))()")
        policy = compile_policy(REVISION, source_index)
        fields = [(policy['fields'][index]['name'], policy['fields'][index]['value'])
                  for index in range(1, 20)]
        output = assemble(BUFFER, gate_targets(lua, IMAGE), policy)
        results.append((policy['schema'], policy['mask'], fields,
                        {name: output[name] for name in CODES}))
    assert results[0] == results[1] == results[2]
    return results[0]


@lru_cache(maxsize=2)
def policy_code(source_index=1):
    assembler = Ks(KS_ARCH_X86, KS_MODE_64)
    output = {}
    for name, source in generated_policy(source_index)[3].items():
        source = re.sub(r"\b(?:ds|ss):", "", source)
        source = source.replace("#MANUAL_HOOK_EXIT(1)", "nop")
        binary, _ = assembler.asm(source, CODES[name])
        output[name] = bytes(binary)
    return output


class PolicyMachine(GateMachine):
    def __init__(self, source_index=1):
        super().__init__()
        for name, binary in policy_code(source_index).items():
            self.uc.mem_write(CODES[name], binary + b"\x90")
        self.sentinels = {name: CODES[name] + len(binary)
                          for name, binary in policy_code(source_index).items()}

    def capture_fields(self, fields, *, declared=None, **kwargs):
        if declared is None:
            declared = 0xB4 + 84*len(fields)
        self.gam(fields, declared=declared, **kwargs)
        self.run('enter', length=declared)
        self.run('capture')
        return self.read_q(BUFFER+32)


class CampaignGatePolicyTests(unittest.TestCase):
    def test_policy_has_exact_19_fields_and_legacy_builder_is_unchanged(self):
        schema, mask, fields, code = generated_policy()
        self.assertEqual(schema, 'flpr-campaign-gate-policy-v1')
        self.assertEqual(mask, (1 << 19) - 1)
        self.assertEqual(len(fields), 19)
        self.assertEqual(fields[:18], expected_fields()[:18])
        self.assertEqual(fields[18][0], 'FLPR_S0001')
        self.assertIsNone(fields[18][1])
        self.assertTrue(all(code[name] for name in CODES))
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime.__module__):
                lua, assemble = factory(runtime, ROOT / 'tools/probes/FLPRGateAsm.lua')
                old = assemble(BUFFER, gate_targets(lua, IMAGE))
                self.assertEqual({name: old[name] for name in CODES}, legacy_generated())

    def test_shuffled_fields_and_unrelated_globals_select_protected_mode(self):
        fields = list(reversed(expected_fields(phase=6)))
        fields.insert(5, ('FLPR_U1_MODE', 2))  # legacy value is irrelevant
        fields.insert(0, ('OTHER_GLOBAL', 99))
        machine = PolicyMachine()
        self.assertEqual(machine.capture_fields(fields), 1)
        self.assertEqual(machine.read_q(BUFFER+72), 0)
        self.assertEqual(machine.read_q(BUFFER+80), (1 << 19) - 1)
        self.assertEqual(machine.read_q(BUFFER+88), 1)
        self.assertEqual(machine.read_q(BUFFER+64), len(fields))

    def test_physical_source_completion_selects_mode_two_and_preserves_item(self):
        machine = PolicyMachine()
        self.assertEqual(machine.capture_fields(expected_fields(phase=3)), 2)
        machine.item()
        self.assertEqual(machine.run('item'), 'continue')
        self.assertEqual(machine.read_q(BUFFER+96), 0)
        self.assertEqual(machine.read_q(BUFFER+120), 0)
        self.assertEqual(machine.read_q(BUFFER+128+2*128+24), 0)

    def test_pending_and_protected_source_skip_only_one_exact_item(self):
        for phase in (1, 6):
            with self.subTest(phase=phase):
                machine = PolicyMachine()
                self.assertEqual(machine.capture_fields(expected_fields(phase=phase)), 1)
                machine.item()
                self.assertEqual(machine.run('item'), 'skip')
                self.assertEqual(machine.run('item'), 'continue')
                self.assertEqual(machine.read_q(BUFFER+96), 1)

    def test_overflow_keeps_exact_suppression_and_dedicated_witness(self):
        machine = PolicyMachine()
        self.assertEqual(machine.capture_fields(expected_fields()), 1)
        machine.write_q(BUFFER+8, 2048)
        machine.item()
        self.assertEqual(machine.run('item'), 'skip')
        self.assertEqual(machine.read_q(BUFFER+16), 1)
        self.assertEqual(machine.read_q(BUFFER+32), 1)
        self.assertEqual(machine.read_q(BUFFER+96), 1)
        self.assertEqual(machine.read_q(BUFFER+112), 1)
        overflow_row = BUFFER+128+2047*128
        witness_row = BUFFER+128+2048*128
        original = bytes(machine.uc.mem_read(witness_row, 128))
        self.assertEqual(machine.read_q(witness_row), 3)
        self.assertEqual(machine.read_q(witness_row+24), 1)
        self.assertEqual(machine.read_q(witness_row+48), 1)
        self.assertEqual(bytes(machine.uc.mem_read(witness_row+88,20)),
                         bytes(machine.uc.mem_read(ITEMS+20,20)))
        self.assertEqual(bytes(machine.uc.mem_read(overflow_row,128)), original)
        machine.item(index=2, item=b'FLPRI2\0\0' + b'\0'*8 + b'\x01\0\0\0')
        self.assertEqual(machine.run('item', index=2), 'continue')
        self.assertEqual(machine.read_q(BUFFER+96), 1)
        self.assertEqual(machine.read_q(BUFFER+112), 1)
        self.assertNotEqual(bytes(machine.uc.mem_read(overflow_row,128)), original)
        self.assertEqual(bytes(machine.uc.mem_read(witness_row,128)), original)

    def test_capture_after_overflow_rebinds_policy_and_enter_resets_witness(self):
        machine = PolicyMachine()
        machine.write_q(BUFFER+8, 2048)
        self.assertEqual(machine.capture_fields(expected_fields(phase=6)), 1)
        self.assertEqual(machine.read_q(BUFFER+16), 1)
        machine.item()
        self.assertEqual(machine.run('item'), 'skip')
        self.assertEqual(machine.read_q(BUFFER+112), 1)
        self.assertEqual(machine.capture_fields(expected_fields(phase=3)), 2)
        self.assertEqual(machine.read_q(BUFFER+112), 0)
        self.assertEqual(machine.read_q(BUFFER+120), 0)
        machine.item()
        self.assertEqual(machine.run('item'), 'continue')
        self.assertEqual(machine.read_q(BUFFER+96), 1)

    def test_missing_each_required_variable_refuses_mode(self):
        fields = expected_fields()
        for missing in range(len(fields)):
            with self.subTest(missing=fields[missing][0]):
                machine = PolicyMachine()
                without = fields[:missing] + fields[missing+1:]
                self.assertEqual(machine.capture_fields(without), 0)
                self.assertEqual(machine.read_q(BUFFER+72), 1)
                self.assertEqual(machine.read_q(BUFFER+80), ((1 << 19) - 1) ^ (1 << missing))

    def test_wrong_each_required_value_refuses_mode(self):
        fields = expected_fields()
        for wrong in range(len(fields)):
            with self.subTest(wrong=fields[wrong][0]):
                changed = list(fields)
                old = changed[wrong][1]
                changed[wrong] = (changed[wrong][0], 2 if wrong == 18 else old + 1)
                machine = PolicyMachine()
                self.assertEqual(machine.capture_fields(changed), 0)
                self.assertEqual(machine.read_q(BUFFER+72), 4)

    def test_duplicate_each_required_variable_refuses_mode(self):
        fields = expected_fields()
        for duplicate in range(len(fields)):
            with self.subTest(duplicate=fields[duplicate][0]):
                changed = fields[:duplicate+1] + [fields[duplicate]] + fields[duplicate+1:]
                machine = PolicyMachine()
                self.assertEqual(machine.capture_fields(changed), 0)
                self.assertEqual(machine.read_q(BUFFER+72), 3)

    def test_exact_full_zero_padded_names_are_required(self):
        for position, replacement in ((0, b'f'), (10, b'X'), (31, b'X')):
            with self.subTest(position=position):
                machine = PolicyMachine()
                fields = expected_fields()
                size = machine.gam(fields, declared=0xB4+84*len(fields))
                machine.write(INPUT+0xB4+position, replacement)
                machine.run('enter', length=size)
                machine.run('capture')
                self.assertEqual(machine.read_q(BUFFER+32), 0)
                self.assertEqual(machine.read_q(BUFFER+72), 1)

    def test_policy_index_and_revision_inputs_are_bounded(self):
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime.__module__):
                _, compile_policy = factory(runtime, ROOT / 'tools/probes/FLPRCampaignPolicy.lua')
                for invalid in (0, 4097, 1.5, -1):
                    with self.subTest(index=invalid):
                        with self.assertRaisesRegex(Exception, 'CAMPAIGN_GATE_SOURCE_INDEX_INVALID'):
                            compile_policy(REVISION, invalid)
                for invalid in ('', 'x'*64, 'A'*64, '1'*63):
                    with self.subTest(revision=invalid[:8]):
                        with self.assertRaisesRegex(Exception, 'CAMPAIGN_GATE_REVISION_INVALID'):
                            compile_policy(invalid, 1)
                accepted = compile_policy(REVISION, 4096)
                self.assertEqual(accepted['fields'][19]['name'], 'FLPR_S4096')
        machine = PolicyMachine()
        self.assertEqual(machine.capture_fields(expected_fields()), 1)
        maximum = PolicyMachine(4096)
        self.assertEqual(maximum.capture_fields(expected_fields(source_index=4096)), 1)

    def test_malformed_gam_header_and_table_bounds_refuse_mode_without_scan(self):
        scenarios = (
            {'header': b'NOTGAME!', 'declared': 0xB4},
            {'declared': 0xB3},
            {'variables_offset': 0xB3, 'declared': 0xB4},
            {'variables_count': 16385, 'declared': 0xB4},
            {'variables_count': 19, 'declared': 0xB4},
        )
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                machine = PolicyMachine()
                declared = scenario['declared']
                options = {key: value for key, value in scenario.items() if key != 'declared'}
                machine.gam((), declared=declared, **options)
                machine.run('enter', length=declared)
                machine.run('capture')
                self.assertEqual(machine.read_q(BUFFER+32), 0)
                self.assertNotEqual(machine.read_q(BUFFER+72), 0)
                self.assertFalse(any(INPUT+0xB4 <= address < INPUT+0x10000
                                     for address, size in machine.reads))


if __name__ == '__main__':
    unittest.main()
