"""Exported STO -> campaign gate -> emulated store constructor, without a game."""

from pathlib import Path
import re
import struct
import unittest

from keystone import Ks, KS_ARCH_X86, KS_MODE_64
from lupa.luajit21 import LuaRuntime
from unicorn import UcError
from unicorn.x86_const import (UC_X86_REG_R14, UC_X86_REG_RAX, UC_X86_REG_RBP,
                               UC_X86_REG_RBX, UC_X86_REG_RDI, UC_X86_REG_RIP, UC_X86_REG_RSP)

from tests.test_campaign_gate_bridge import CASES, CORE, GATE, POLICY, BUILD
from tests.test_campaign_gate_policy import expected_fields
from tests import test_postinstall_native_export as export_tests
from tests.test_postinstall_world_formats import item_record, make_sto
from tests.test_source_gate_assembly import BUFFER, IMAGE, STACK, GateMachine
from tests.engine_sites import gate_targets, load


STORE_CODE, SAVED_CODE, DONE_CODE = 0x150000, 0x160000, 0x170000
STORE_OBJECT, STORE_HEADER, RAW_STO = 0x601000, 0x602000, 0x606000
LIST_NODE, LIST_ITEM = 0x608000, 0x609000
MEMORY_SIZE = 128 + 2049 * 128 + 32 + 64 * 64


class StoreGateTests(unittest.TestCase):
    def setUp(self):
        fixture = export_tests.NativeExportTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.original = bytes(make_sto([
            (item_record('SYNTH002', expiration=0, charges=(1, 2, 3), flags=1), 2, 0),
            (item_record('SYNTH001', expiration=0, charges=(4, 5, 6), flags=1), 3, 0),
            (item_record('SYNTH002', expiration=0, charges=(7, 8, 9), flags=1), 1, 0)], gap=5))
        (fixture.override / 'shop.sto').write_bytes(self.original)
        fixture.refresh()
        revision, source = fixture.write_store_source()
        fixture.export(revision)
        base = fixture.output / 'campaign-store/revisions' / revision
        self.removed = Path(str(base) + '.source-1.removed.sto').read_bytes()

        lua = LuaRuntime(unpack_returned_tuples=True)
        core, build, compile_policy = (lua.execute(value) for value in (CORE, BUILD, POLICY))
        gate = load(lua, GATE)
        factory = lua.execute(CASES.replace('0x140000000,1000,', '0x10000000,3145728,')
                              .replace('return cases', 'return fixture'))
        f = factory(core)
        f.manifest = lua.execute(Path(str(base) + '.lua').read_bytes())
        native = lua.execute(Path(str(base) + '.native.lua').read_bytes())
        f.bundle = lua.table_from({'manifest': f.manifest, 'fixture': native})
        f.revision = revision
        f.pristine = lua.table_from({'stores': lua.table_from({revision: lua.table_from({
            source['id']: lua.table_from({'original': self.original, 'removed': self.removed})})})})
        lua.globals().f = f
        f.install(f, gate, build, compile_policy)
        f.capture(f)
        f.prepare(f)
        f.adapter(f)
        f.bind(f)
        assembly = dict(build(BUFFER, gate_targets(lua, IMAGE), f.gate.policy).items())
        # The saved-store record the gate keeps (FLPRGat.lua), and its hook code.
        self.record = int(lua.execute('return f.api.FLPR_CAMPAIGN_GATE.recorder'))
        assembly['saved_store'] = gate.recorder(self.record)

        machine = GateMachine()
        machine.uc.mem_map(0x130000, 0x50000)
        machine.uc.mem_map(self.record & ~0xFFFF, 0x20000)
        assembler = Ks(KS_ARCH_X86, KS_MODE_64)
        for name, address in (('enter', 0x100000), ('capture', 0x110000),
                              ('store', STORE_CODE), ('saved_store', SAVED_CODE),
                              ('store_done', DONE_CODE)):
            code = re.sub(r'\b(?:ds|ss):', '', assembly[name]).replace('#MANUAL_HOOK_EXIT(1)', 'nop')
            binary, _ = assembler.asm(code, address)
            machine.write(address, bytes(binary) + b'\x90')
            machine.sentinels[name] = address + len(binary)
        size = int(f.allocated_bytes)
        machine.write(BUFFER, bytes(f.bytes[i] or 0 for i in range(BUFFER, BUFFER + size)))
        machine.write(RAW_STO, self.original)
        machine.write(STORE_OBJECT, b'SHOP\0\0\0\0')
        machine.write_d(STORE_OBJECT + 0x10, 0x3F6)
        machine.write_d(STORE_HEADER + 0x48, len(self.original))
        machine.write_q(STACK + 0x8000 - 0x20, int.from_bytes(b'SHOP\0\0\0\0', 'little'))
        # The hook saves eight registers before reading [rsp+0x60].
        machine.write_q(STACK + 0xF000 + 0x20, STORE_HEADER)
        self.lua, self.f, self.machine, self.source = lua, f, machine, source

    def hook(self, name, *, raw=RAW_STO):
        machine = self.machine
        machine.uc.reg_write(UC_X86_REG_RBX, STORE_OBJECT)
        machine.uc.reg_write(UC_X86_REG_RDI, raw)
        machine.uc.reg_write(UC_X86_REG_RSP, STACK + 0xF000)
        machine.uc.reg_write(UC_X86_REG_RBP, STACK + 0x8000)
        machine.uc.reg_write(UC_X86_REG_R14, 0)
        machine.uc.reg_write(UC_X86_REG_RAX, 0x123456)
        machine.stop, machine.steps = None, 0
        try:
            machine.uc.emu_start({'store': STORE_CODE, 'saved_store': SAVED_CODE,
                                  'store_done': DONE_CODE}[name], 0, count=200000)
        except UcError as exc:
            self.fail(f'{name} at {machine.uc.reg_read(UC_X86_REG_RIP):#x}: {exc}')
        self.assertEqual(machine.stop, 'continue')
        self.assertEqual(machine.read_q(BUFFER + 24), 0)
        return machine.uc.reg_read(UC_X86_REG_RDI)

    def install_postparse_list(self):
        machine = self.machine
        sale = struct.unpack_from('<I', self.removed, 0x34)[0]
        machine.write_d(STORE_OBJECT + 0xB8, 2)
        machine.write_q(STORE_OBJECT + 0xA8, LIST_NODE)
        for index in range(2):
            node, item = LIST_NODE + index * 0x40, LIST_ITEM + index * 0x40
            machine.write_q(node, LIST_NODE + (index + 1) * 0x40 if index == 0 else 0)
            machine.write_q(node + 0x10, item)
            machine.write(item, self.removed[sale + index * 28:sale + (index + 1) * 28])

    def test_raw_substitution_requires_postparse_list_before_quantity_receipt(self):
        machine, f = self.machine, self.f
        pointer = self.hook('store')
        self.assertEqual(pointer, f.gate.policy.store.removed)
        self.assertEqual(bytes(machine.uc.mem_read(pointer, len(self.removed))), self.removed)
        self.assertEqual(bytes(machine.uc.mem_read(RAW_STO, len(self.original))), self.original)
        context = int(f.gate.policy.store.context)
        self.assertEqual(machine.read_q(BUFFER + context + 16), 0)
        self.install_postparse_list()
        self.hook('store_done')
        self.assertEqual(machine.read_q(BUFFER + context + 16), machine.read_q(BUFFER + 40))
        data = bytes(machine.uc.mem_read(BUFFER, int(f.allocated_bytes)))
        f.pokestring(f, BUFFER, data)
        self.lua.execute('local r=assert(f.controller:guarded_source_removal_request(1)); '
                         'f.controller:on_observation_boundary(); '
                         'local receipt=assert(f.adapter_instance:observe_removal(r)); '
                         'assert(receipt.removed==3 and receipt.remaining==0); '
                         'assert(f.controller:confirm_guarded_source_removal(receipt))')
        self.assertEqual(f['values'].flpr_s0001, 3)

    def restore(self, *names, r14=0, kind=0x3F6, count=None):
        """dimmUncompressResources restores each named resource from a saved
        game; then CInfGame::Unmarshal's entry applies the record (Lua)."""
        machine, f = self.machine, self.f
        if count is not None:
            machine.write_q(self.record, count)
        for name in names:
            machine.write_q(STACK + 0x8000 - 0x20, int.from_bytes(name.ljust(8, b'\0'), 'little'))
            machine.write_d(STORE_OBJECT + 0x10, kind)
            machine.uc.reg_write(UC_X86_REG_RBX, STORE_OBJECT)
            machine.uc.reg_write(UC_X86_REG_RSP, STACK + 0xF000)
            machine.uc.reg_write(UC_X86_REG_RBP, STACK + 0x8000)
            machine.uc.reg_write(UC_X86_REG_R14, r14)
            machine.stop, machine.steps = None, 0
            machine.uc.emu_start(SAVED_CODE, 0, count=10000)
            self.assertEqual(machine.stop, 'continue')
        f.pokestring(f, self.record, bytes(machine.uc.mem_read(self.record, 16 + 8 * 4096)))
        self.lua.execute('f.api.FLPR_CAMPAIGN_GATE:_restore_saved_stores()')
        context = BUFFER + int(f.gate.policy.store.context)
        machine.write_q(context, int(f.peek(f, context, 8)))
        machine.write_d(STORE_OBJECT + 0x10, 0x3F6)
        return machine.read_q(context)

    def test_saved_store_marker_blocks_reapplication_even_if_row_is_present(self):
        self.assertEqual(self.restore(b'SHOP'), 1)
        self.assertEqual(self.hook('store'), RAW_STO)
        self.assertEqual(self.machine.read_q(BUFFER + 120), 0)

    def test_saved_store_record_needs_this_restored_sto(self):
        # Another STO, a resource the engine does not restore, or another
        # resource type leaves the source guarded; case does not matter.
        for names, options in (((b'OTHER', b'SHOP2'), {}), ((b'SHOP',), {'r14': 1}),
                               ((b'SHOP',), {'kind': 0x3ED})):
            with self.subTest(names=names, **options):
                self.assertEqual(self.restore(*names, **options), 0)
        self.assertEqual(self.machine.read_q(self.record), 2)
        self.assertEqual(self.restore(b'shop'), 1)
        self.assertEqual(self.hook('store'), RAW_STO)

    def test_full_saved_store_record_marks_every_store_source(self):
        self.assertEqual(self.restore(b'OTHER', count=4096), 1)
        self.assertEqual(self.machine.read_q(self.record + 8), 1)
        self.assertEqual(self.hook('store'), RAW_STO)

    def test_destroyed_game_clears_the_saved_store_record(self):
        self.restore(b'SHOP')
        self.lua.execute('for _,callback in ipairs(f.callbacks) do callback() end')
        self.assertEqual(int(self.f.peek(self.f, self.record, 8)), 0)
        context = BUFFER + int(self.f.gate.policy.store.context)
        self.assertEqual(int(self.f.peek(self.f, context, 8)), 0)

    def test_saved_campaign_revision_and_phase_control_store_guard(self):
        machine, f = self.machine, self.f
        for revision, phase, expected in ((f.revision, 6, True), ('f' * 64, 6, False),
                                          (f.revision, 3, False)):
            with self.subTest(revision=revision[:8], phase=phase):
                fields = expected_fields(revision=revision, phase=phase)
                length = machine.gam(fields, declared=0xB4 + 84 * len(fields))
                machine.run('enter', length=length)
                machine.run('capture')
                self.assertEqual(self.hook('store') == f.gate.policy.store.removed, expected)


if __name__ == '__main__':
    unittest.main()
