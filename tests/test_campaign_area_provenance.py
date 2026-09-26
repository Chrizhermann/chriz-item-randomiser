"""Emulate campaign area provenance hooks using mapped synthetic memory.

This covers generated instructions only. The EEex hook wrapper, native ABI, and
real area lifetime remain outside this emulator's evidence.
"""
from functools import lru_cache
from pathlib import Path
import re
import struct
import unittest

from keystone import Ks, KS_ARCH_X86, KS_MODE_64
from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT
from unicorn.x86_const import (
    UC_X86_REG_EFLAGS, UC_X86_REG_R8, UC_X86_REG_R9, UC_X86_REG_R10,
    UC_X86_REG_R11, UC_X86_REG_R14, UC_X86_REG_R15, UC_X86_REG_RAX,
    UC_X86_REG_RBP, UC_X86_REG_RCX, UC_X86_REG_RDX, UC_X86_REG_RSP,
)

from tests.test_campaign_gate_policy import REVISION, expected_fields
from tests.test_source_gate_assembly import (
    AREA, BUFFER, CODES, IMAGE, INPUT, STACK, GateMachine,
)


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = INPUT + 0x4000
RAW = AREA + 0x8000
LENGTH = 421  # Includes five tail bytes after the final aligned qword.
CONTAINER_OFFSET = 32
ITEM_OFFSET = 320
CONTAINER = RAW + CONTAINER_OFFSET
ITEMS = RAW + ITEM_OFFSET
CTX = BUFFER + 128 + 2049*128
FIRST = CTX + 32
CTOR_FRAME = STACK + 0x8000
AREA_FRAME = STACK + 0x5000
RETURN_SITE = IMAGE + 0x18D9CE
HOOKS = {**CODES, 'area_enter': 0x130000, 'area_exit': 0x140000}
VOLATILE = (UC_X86_REG_RAX, UC_X86_REG_RCX, UC_X86_REG_RDX, UC_X86_REG_R8,
            UC_X86_REG_R9, UC_X86_REG_R10, UC_X86_REG_R11, UC_X86_REG_EFLAGS)


def pristine_blob():
    blob = bytearray(LENGTH)
    blob[:8] = b'AREAV1.0'
    blob[8:16] = b'FLPRU1\0\0'
    blob[CONTAINER_OFFSET:CONTAINER_OFFSET+8] = b'FLPRU1A\0'
    struct.pack_into('<Q', blob, CONTAINER_OFFSET+0x20,
                     1140 | (650 << 16) | (4 << 32))
    struct.pack_into('<Q', blob, CONTAINER_OFFSET+0x40, 1 | (2 << 32))
    blob[ITEM_OFFSET+20:ITEM_OFFSET+40] = b'FLPRI1\0\0' + b'\0'*8 + b'\x01\0\0\0'
    for position in range(400, LENGTH):
        blob[position] = (position * 17) % 251
    return bytes(blob)


def configured_blob():
    blob = bytearray(1021)
    blob[:8] = b'AREAV1.0'
    blob[224:256] = b'Longer Chest Name'.ljust(32, b'\0')
    struct.pack_into('<4H', blob, 224+0x20, 303, 507, 1, 99)
    struct.pack_into('<2I', blob, 224+0x40, 2, 2)
    blob[840:860] = b'SW1H01\0\0' + struct.pack('<4HI', 0, 7, 8, 9, 0)
    return bytes(blob)


@lru_cache(maxsize=2)
def generated_hooks(configured=False):
    outputs = []
    for runtime in (Lua51, Lua53, LuaJIT):
        lua = runtime(unpack_returned_tuples=True)
        lua.globals().policy_path = (ROOT / 'tools/probes/FLPRCampaignPolicy.lua').as_posix()
        lua.globals().asm_path = (ROOT / 'tools/probes/FLPRGateAsm.lua').as_posix()
        policy_factory = lua.execute('return assert(loadfile(policy_path))()')
        assembly_factory = lua.execute('return assert(loadfile(asm_path))()')
        policy = policy_factory(REVISION, 1)
        blob = configured_blob() if configured else pristine_blob()
        policy['provenance'] = lua.table_from({
            'expected': EXPECTED, 'length': len(blob),
            'container_offset': 224 if configured else CONTAINER_OFFSET,
            'item_offset': 800 if configured else ITEM_OFFSET,
        })
        if configured:
            policy['source'] = lua.table_from({
                'area': 'ARTEST', 'name': blob[224:256], 'payload': blob[840:860], 'index': 2,
                'inventory': blob[224+0x40:224+0x48], 'position': blob[224+0x20:224+0x28],
            })
        output = assembly_factory(BUFFER, IMAGE, policy)
        outputs.append({name: output[name] for name in HOOKS})
    assert outputs[0] == outputs[1] == outputs[2]
    assert outputs[0]['area_enter'] and outputs[0]['area_exit']
    return outputs[0]


@lru_cache(maxsize=2)
def compiled_hooks(configured=False):
    assembler = Ks(KS_ARCH_X86, KS_MODE_64)
    binaries = {}
    for name, source in generated_hooks(configured).items():
        source = re.sub(r'\b(?:ds|ss):', '', source)
        source = source.replace('#MANUAL_HOOK_EXIT(1)', 'nop')
        binary, _ = assembler.asm(source, HOOKS[name])
        binaries[name] = bytes(binary)
    return binaries


class ProvenanceMachine(GateMachine):
    def __init__(self, blob=None, configured=False):
        super().__init__()
        self.uc.mem_map(0x130000, 0x30000)
        self.expected = (configured_blob() if configured else pristine_blob()) if blob is None else blob
        self.write(EXPECTED, self.expected)
        self.write(RAW, self.expected)
        for name, binary in compiled_hooks(configured).items():
            self.uc.mem_write(HOOKS[name], binary + b'\x90')
        self.sentinels = {name: HOOKS[name] + len(binary)
                          for name, binary in compiled_hooks(configured).items()}
        if configured:
            self.write(AREA+0x204, b'ARTEST\0\0')
        self.write_q(CTOR_FRAME-0x60, AREA)
        self.write_q(CTOR_FRAME-0x68, AREA+0x1000)
        self.write_q(CTOR_FRAME-0x78, ITEMS)
        self.write_q(CTOR_FRAME+0xB0, AREA_FRAME)
        self.write_q(CTOR_FRAME+0xB8, RETURN_SITE)

    def capture(self, phase=1):
        fields = expected_fields(phase=phase)
        length = self.gam(fields, declared=0xB4+84*len(fields))
        self.run('enter', length=length)
        self.run('capture')
        return self.read_q(BUFFER+32)

    def hook(self, name, *, frame=AREA_FRAME, area=AREA, raw=RAW, length=LENGTH):
        starting = {
            UC_X86_REG_RAX: 0x123456, UC_X86_REG_RCX: area,
            UC_X86_REG_RDX: raw, UC_X86_REG_R8: length,
            UC_X86_REG_R9: 0x345678, UC_X86_REG_R10: 0x456789,
            UC_X86_REG_R11: 0x56789A, UC_X86_REG_R14: 0x6789AB,
            UC_X86_REG_R15: 0x789ABC, UC_X86_REG_RBP: frame,
            UC_X86_REG_RSP: STACK+0xF000, UC_X86_REG_EFLAGS: 0x246,
        }
        for reg, value in starting.items():
            self.uc.reg_write(reg, value)
        self.stop = None
        self.steps = 0
        self.uc.emu_start(HOOKS[name], 0, count=200000)
        assert self.stop == 'continue'
        for reg in VOLATILE + (UC_X86_REG_RBP, UC_X86_REG_RSP):
            assert self.uc.reg_read(reg) == starting[reg], f'register {reg} changed'
        assert self.read_q(BUFFER+24) == 0

    def item_hook(self, *, caller=AREA_FRAME, return_site=RETURN_SITE,
                  table=ITEMS, container=CONTAINER, area_ref=AREA, index=1):
        self.write_q(CTOR_FRAME+0xB0, caller)
        self.write_q(CTOR_FRAME+0xB8, return_site)
        self.write_q(CTOR_FRAME-0x78, table)
        self.write_q(CTOR_FRAME-0x60, area_ref)
        return self.run('item', index=index, container=container,
                        regs={UC_X86_REG_RBP: CTOR_FRAME,
                              UC_X86_REG_R14: container})

    def valid_slots(self):
        return [(self.read_q(FIRST+64*i), self.read_q(FIRST+64*i+24))
                for i in range(64) if self.read_q(FIRST+64*i)]


class CampaignAreaProvenanceTests(unittest.TestCase):
    def test_configured_area_chest_item_index_and_charges_select_one_record(self):
        machine = ProvenanceMachine(configured=True)
        self.assertEqual(machine.capture(), 1)
        machine.hook('area_enter', length=1021)
        self.assertEqual(machine.read_q(FIRST+48), RAW+224)
        self.assertEqual(machine.item_hook(container=RAW+224, table=RAW+800, index=1), 'continue')
        self.assertEqual(machine.item_hook(container=RAW+224, table=RAW+800, index=2), 'skip')
        self.assertEqual(machine.read_q(BUFFER+96), 1)
        saved = ProvenanceMachine(configured=True)
        saved.capture(phase=6)
        saved.write(RAW+1000, b'\x01')  # Changed saved area with an identical returned item.
        saved.hook('area_enter', length=1021)
        self.assertEqual(saved.item_hook(container=RAW+224, table=RAW+800, index=2), 'continue')
        self.assertEqual(saved.read_q(BUFFER+96), 0)

    def test_exact_full_blob_publishes_context_and_allows_one_skip(self):
        machine = ProvenanceMachine()
        self.assertEqual(machine.capture(), 1)
        self.assertEqual(machine.item_hook(), 'continue')
        self.assertEqual(machine.read_q(BUFFER+96), 0)
        machine.hook('area_enter')
        self.assertEqual(machine.read_q(CTX), 1)
        self.assertEqual(machine.read_q(CTX+16), 1)
        self.assertEqual(machine.valid_slots(), [(AREA_FRAME, 1)])
        self.assertEqual(machine.read_q(FIRST+8), AREA)
        self.assertEqual(machine.read_q(FIRST+16), RAW)
        self.assertEqual(machine.read_q(FIRST+32), machine.read_q(BUFFER+40))
        self.assertEqual(machine.read_q(FIRST+40), ITEMS)
        self.assertEqual(machine.read_q(FIRST+48), CONTAINER)
        self.assertEqual(machine.item_hook(), 'skip')
        self.assertEqual(machine.read_q(BUFFER+96), 1)

    def test_wrong_header_or_unrelated_byte_preserves_exact_item(self):
        for offset in (0, 205, LENGTH-1):
            with self.subTest(changed_byte=offset):
                machine = ProvenanceMachine(); machine.capture()
                original = bytes(machine.uc.mem_read(RAW+offset, 1))[0]
                machine.write(RAW+offset, bytes((original ^ 0xFF,)))
                machine.hook('area_enter')
                self.assertEqual(machine.read_q(CTX+16), 0)
                self.assertEqual(machine.valid_slots(), [])
                self.assertEqual(machine.item_hook(), 'continue')
                self.assertEqual(machine.read_q(BUFFER+96), 0)

    def test_declared_length_bounds_comparison_without_adjacent_read(self):
        machine = ProvenanceMachine(); machine.capture()
        machine.write(RAW+LENGTH, b'\xFF')
        machine.reads.clear()
        machine.hook('area_enter')
        self.assertEqual(machine.read_q(CTX+16), 1)
        self.assertFalse(any(address <= RAW+LENGTH < address+size
                             for address, size in machine.reads))
        different = ProvenanceMachine(); different.capture()
        different.hook('area_enter', length=LENGTH+1)
        self.assertEqual(different.read_q(CTX+16), 0)
        for area, raw in ((0, RAW), (AREA, 0)):
            with self.subTest(area=area, raw=raw):
                null_input = ProvenanceMachine(); null_input.capture()
                null_input.hook('area_enter', area=area, raw=raw)
                self.assertEqual(null_input.read_q(CTX+16), 0)

    def test_external_callsite_frame_and_table_drift_cannot_skip(self):
        for change in (
            {'return_site': RETURN_SITE+5},
            {'caller': AREA_FRAME+0x100},
            {'table': ITEMS+20},
            {'container': CONTAINER+0x100},
            {'area_ref': AREA+0x1000},
        ):
            with self.subTest(change=change):
                machine = ProvenanceMachine(); machine.capture()
                machine.hook('area_enter')
                if 'area_ref' in change:
                    machine.write_q(change['area_ref']+0x204,
                                    int.from_bytes(b'FLPRU1\0\0', 'little'))
                self.assertEqual(machine.item_hook(**change), 'continue')
                self.assertEqual(machine.read_q(BUFFER+96), 0)

    def test_same_frame_nonpristine_reentry_revokes_old_context(self):
        machine = ProvenanceMachine(); machine.capture()
        machine.hook('area_enter')
        self.assertEqual(machine.valid_slots(), [(AREA_FRAME, 1)])
        machine.write(RAW+205, b'\xA5')
        machine.hook('area_enter')
        self.assertEqual(machine.valid_slots(), [])
        self.assertEqual(machine.item_hook(), 'continue')
        self.assertEqual(machine.read_q(BUFFER+96), 0)

    def test_exit_and_reentry_manage_invocation_lifetime(self):
        machine = ProvenanceMachine(); machine.capture()
        other = AREA_FRAME + 0x100
        machine.hook('area_enter', frame=AREA_FRAME)
        machine.hook('area_enter', frame=other)
        self.assertEqual(set(machine.valid_slots()), {(AREA_FRAME, 1), (other, 1)})
        machine.hook('area_exit', frame=other)
        self.assertEqual(machine.valid_slots(), [(AREA_FRAME, 1)])
        machine.hook('area_exit', frame=AREA_FRAME)
        self.assertEqual(machine.valid_slots(), [])
        self.assertEqual(machine.item_hook(), 'continue')
        machine.hook('area_enter', frame=AREA_FRAME)
        self.assertEqual(machine.valid_slots(), [(AREA_FRAME, 1)])
        self.assertEqual(machine.item_hook(), 'skip')

    def test_saved_protected_source_returned_copy_is_not_removed(self):
        machine = ProvenanceMachine(); self.assertEqual(machine.capture(phase=6), 1)
        machine.write(RAW+205, b'\xA5')  # Additional saved content; original row remains byte-exact.
        self.assertEqual(bytes(machine.uc.mem_read(ITEMS+20,20)),
                         pristine_blob()[ITEM_OFFSET+20:ITEM_OFFSET+40])
        machine.hook('area_enter')
        self.assertEqual(machine.valid_slots(), [])
        self.assertEqual(machine.item_hook(), 'continue')
        self.assertEqual(machine.read_q(BUFFER+96), 0)

    def test_generation_change_rejects_stale_context_until_reentry(self):
        machine = ProvenanceMachine(); machine.capture()
        machine.hook('area_enter')
        self.assertEqual(machine.valid_slots(), [(AREA_FRAME, 1)])
        self.assertEqual(machine.capture(), 1)
        self.assertEqual(machine.item_hook(), 'continue')
        machine.hook('area_enter')
        self.assertEqual(machine.item_hook(), 'skip')

    def test_context_slots_overflow_without_false_removal(self):
        machine = ProvenanceMachine(); machine.capture()
        frames = [STACK+0x3000+index*0x100 for index in range(65)]
        for frame in frames:
            machine.hook('area_enter', frame=frame)
        self.assertEqual(machine.read_q(CTX), 65)
        self.assertEqual(machine.read_q(CTX+8), 1)
        self.assertEqual(machine.read_q(CTX+16), 64)
        self.assertEqual(machine.read_q(BUFFER+16), 1)
        self.assertEqual(len(machine.valid_slots()), 64)
        self.assertEqual(machine.item_hook(caller=frames[-1]), 'continue')
        self.assertEqual(machine.read_q(BUFFER+96), 0)


if __name__ == '__main__':
    unittest.main()
