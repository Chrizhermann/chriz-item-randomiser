"""Export -> Lua guard -> emulated CRE constructor handoff. No live engine.

Reuses the existing synthetic installation, hook memory and instruction harnesses.
EEex's native call wrapper and actual game loading still need a live check.
"""
from pathlib import Path
import re
import unittest

from keystone import Ks, KS_ARCH_X86, KS_MODE_64
from lupa.luajit21 import LuaRuntime
from unicorn.x86_const import UC_X86_REG_R13, UC_X86_REG_R14, UC_X86_REG_RDX, UC_X86_REG_RCX, UC_X86_REG_R8

from tests.test_campaign_gate_bridge import CASES, CORE, GATE, POLICY, BUILD
from tests.engine_sites import gate_targets, load
from tests.test_campaign_area_provenance import (
    AREA, BUFFER, HOOKS, RAW, ProvenanceMachine,
)
from tests.test_campaign_gate_policy import expected_fields
from tests.test_source_gate_assembly import REGISTERS
from tests import test_postinstall_native_export as export_tests
from tests.test_postinstall_world_formats import item_record, make_cre, make_are

CRE_INPUT, SPRITE, ACTOR_CODE = AREA+0xA000, AREA+0xB000, 0x150000


class CreatureGateTests(unittest.TestCase):
    def setUp(self):
        self.build_fixture()

    def build_fixture(self, embedded=False, equipped=False, replacement=None):
        fixture = export_tests.NativeExportTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.original = bytes(make_cre(
            [item_record('SYNTH001', expiration=0,
                         charges=(2, 3, 4) if equipped else (0, 0, 0), flags=1)]
            if equipped else [
                item_record('SYNTH001', expiration=0, charges=(0, 0, 0), flags=1),
                item_record('SYNTH002', expiration=0, charges=(2, 3, 4), flags=1)],
            slots={9: 0} if equipped else None))
        if embedded:
            raw = bytearray(self.original)
            raw[0x280:0x2A0] = b'EmbeddedDV'.ljust(32, b'\0')
            self.original = bytes(raw)
            fixture.area = bytes(make_are([], [], [('Source', self.original), ('Target', 'ACTOR')]))
            (fixture.override/'area.are').write_bytes(fixture.area)
        else:
            (fixture.override/'actor.cre').write_bytes(self.original)
        fixture.refresh()
        revision, source = fixture.write_creature_source(
            'embedded-cre' if embedded else 'cre-template', 'are:area/actor/1',
            replacement=replacement)
        fixture.export(revision)
        base = fixture.output/'campaign-store/revisions'/revision
        self.removed = fixture.creature_artifacts(revision)[1].read_bytes()
        self.area = fixture.area
        lua = LuaRuntime(unpack_returned_tuples=True)
        core, build, compile_policy = (lua.execute(value) for value in (CORE, BUILD, POLICY))
        gate = load(lua, GATE)
        # Obtain the shared fake EEex API without adding a second hook harness.
        factory = lua.execute(CASES.replace('0x140000000,1000,', '0x10000000,3145728,')
                              .replace('return cases', 'return fixture'))
        f = factory(core)
        f.manifest = lua.execute(Path(str(base)+'.lua').read_bytes())
        native = lua.execute(Path(str(base)+'.native.lua').read_bytes())
        f.bundle = lua.table_from({'manifest': f.manifest, 'fixture': native})
        f.revision = revision
        f.pristine = lua.table_from({
            'AREA': lua.table_from({'bytes': self.area, 'resource_sha256': native.resource_hashes['AREA.ARE']}),
            'creatures': lua.table_from({revision: lua.table_from({source['id']:
                lua.table_from({'original': self.original, 'removed': self.removed})})}),
        })
        lua.globals().f = f
        f.install(f, gate, build, compile_policy)
        f.capture(f)
        f.prepare(f)
        f.adapter(f)
        f.bind(f)
        # Launch hooks every location; this source's code runs at the four
        # shared load hooks and both CRE calls (HOOKED order, bridge test).
        self.assertEqual(len(f.hooks), 11)
        self.assertEqual([index for index in range(1, 12) if not f.idle(f, index)], [1, 2, 4, 5, 6, 7])
        assembly = build(BUFFER, gate_targets(lua, 0x10000000), f.gate.policy)
        self.assertEqual(assembly.item, '')
        machine = ProvenanceMachine(blob=self.area)
        assembler = Ks(KS_ARCH_X86, KS_MODE_64)
        self.addresses = {**HOOKS, 'actor': ACTOR_CODE}
        for name, address in self.addresses.items():
            code = assembly[name]
            if not code:
                continue
            binary, _ = assembler.asm(re.sub(r'\b(?:ds|ss):', '', code), address)
            machine.write(address, bytes(binary)+b'\x90')
            machine.sentinels[name] = address+len(binary)
        size = int(f.allocated_bytes)
        machine.write(BUFFER, bytes(f.bytes[i] or 0 for i in range(BUFFER, BUFFER+size)))
        machine.write(CRE_INPUT, self.original)
        self.lua, self.f, self.machine, self.source = lua, f, machine, source

    def actor(self, actor_delta=0):
        machine, f = self.machine, self.f
        # AREA entry sets the unchanged register sentinels in the shared harness.
        machine.hook('area_enter', raw=RAW, length=len(self.area))
        values = {reg: machine.uc.reg_read(reg) for reg in REGISTERS}
        values.update({UC_X86_REG_RCX: SPRITE, UC_X86_REG_RDX: CRE_INPUT,
                       UC_X86_REG_R14: AREA})
        values[UC_X86_REG_R8] = len(self.original)
        values[UC_X86_REG_R13] = RAW+int(f.gate.policy.provenance.container_offset)+actor_delta
        for reg, value in values.items():
            machine.uc.reg_write(reg, value)
        machine.stop, machine.steps = None, 0
        machine.uc.emu_start(ACTOR_CODE, 0, count=200000)
        self.assertEqual(machine.stop, 'continue')
        for reg, value in values.items():
            if reg != UC_X86_REG_RDX:
                self.assertEqual(machine.uc.reg_read(reg), value, f'register {reg}')
        self.assertEqual(machine.read_q(BUFFER+24), 0)
        self.assertEqual(bytes(machine.uc.mem_read(CRE_INPUT, len(self.original))), self.original)
        return machine.uc.reg_read(UC_X86_REG_RDX)

    def test_exact_actor_gets_private_inventory_and_removal_receipt(self):
        pointer = self.actor()
        self.assertEqual(pointer, self.f.gate.policy.creature.removed)
        self.assertEqual(bytes(self.machine.uc.mem_read(pointer, len(self.removed))), self.removed)
        self.assertEqual(self.machine.read_q(BUFFER+120), 1)
        # Feed the actual assembly-produced witness back through the Lua receipt.
        data = bytes(self.machine.uc.mem_read(BUFFER, int(self.f.allocated_bytes)))
        self.f.pokestring(self.f, BUFFER, data)
        self.f.remaining = 0
        self.lua.execute('local r=assert(f.controller:guarded_source_removal_request(1)); '
                         'f.controller:on_observation_boundary(); '
                         'local receipt=assert(f.adapter_instance:observe_removal(r)); '
                         'assert(f.controller:confirm_guarded_source_removal(receipt))')
        self.assertEqual(self.f['values'].flpr_s0001, 3)

    def test_other_actor_and_saved_area_do_not_lose_returned_items(self):
        self.assertEqual(self.actor(actor_delta=272), CRE_INPUT)
        self.machine.hook('area_exit')
        # Saved AREs are not the pristine authored load, even if an actor happens
        # to carry exactly the original payload again.
        self.machine.write(RAW+len(self.area)-1, bytes([self.area[-1] ^ 1]))
        self.assertEqual(self.actor(), CRE_INPUT)
        self.assertEqual(self.machine.read_q(BUFFER+120), 0)

    def test_embedded_creature_uses_same_private_inventory_handoff(self):
        self.build_fixture(embedded=True)
        pointer = self.actor()
        self.assertEqual(pointer, self.f.gate.policy.creature.removed)
        self.assertEqual(bytes(self.machine.uc.mem_read(pointer, len(self.removed))), self.removed)

    def test_equipped_replacement_reaches_emulated_constructor(self):
        self.build_fixture(equipped=True, replacement='synth002')
        source = self.source
        self.assertEqual(self.f.manifest.sources[1].replacement.resref, 'synth002')
        self.assertEqual(self.f.bundle.fixture.sources[source['id']].replacement, 'synth002')
        item = export_tests.parse_cre(self.removed)['items'][0]
        self.assertEqual((item['resref'], item['slots'], item['charges'], item['flags']),
                         ('synth002', [9], [2, 3, 4], 1))

        pointer = self.actor()
        self.assertEqual(pointer, self.f.gate.policy.creature.removed)
        self.assertEqual(bytes(self.machine.uc.mem_read(pointer, len(self.removed))), self.removed)
        self.assertEqual(bytes(self.machine.uc.mem_read(CRE_INPUT, len(self.original))),
                         self.original)
        self.assertEqual(self.machine.read_q(BUFFER+120), 1)

    def test_saved_campaign_choice_controls_protection(self):
        f, machine = self.f, self.machine
        for revision, phase, expected in ((f.revision, 6, True), ('f'*64, 6, False),
                                           (f.revision, 3, False)):
            with self.subTest(revision=revision[:8], phase=phase):
                fields = expected_fields(revision=revision, phase=phase)
                length = machine.gam(fields, declared=0xB4+84*len(fields))
                machine.run('enter', length=length)
                machine.run('capture')
                pointer = self.actor()
                self.assertEqual(pointer, f.gate.policy.creature.removed if expected else CRE_INPUT)


if __name__ == '__main__':
    unittest.main()
