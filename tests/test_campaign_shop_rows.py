"""Execute emitted shop-row hooks in synthetic memory; no game access.

EEex's Lua-call/shadow-space macros use a stand-in callback here. These tests
prove hook row selection and register preservation, not the engine callback ABI.
"""
from functools import lru_cache
from pathlib import Path
import re
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'work/source-gate-20260927/analysis-deps'))

from keystone import Ks, KS_ARCH_X86, KS_MODE_64
from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT
from unicorn import Uc, UC_ARCH_X86, UC_MODE_64, UC_HOOK_CODE
from unicorn.x86_const import (
    UC_X86_REG_RAX, UC_X86_REG_RBX, UC_X86_REG_RCX, UC_X86_REG_RDX,
    UC_X86_REG_RSI, UC_X86_REG_RDI, UC_X86_REG_RBP, UC_X86_REG_RSP,
    UC_X86_REG_R8, UC_X86_REG_R9, UC_X86_REG_R10, UC_X86_REG_R11,
    UC_X86_REG_R12, UC_X86_REG_R13, UC_X86_REG_R14, UC_X86_REG_R15,
    UC_X86_REG_EFLAGS,
)

from tests.test_campaign_native_shop import CASES, SHOP

BASE, BUFFER, STORE, NODES, ROWS = 0x100000, 0x200000, 0x400000, 0x410000, 0x411000
UI_NODES, UI_ROWS, SCREEN, CHITIN, ITEM = 0x420000, 0x421000, 0x430000, 0x440000, 0x450000
STACK, CODE, CALLBACK, SEPARATE = 0x800000, 0x900000, 0x980000, 0x980100
SP = STACK + 0x8000
HOOKS = {'price': 0x24FB9A, 'removed': 0x24F860,
         'merge': 0x24F487, 'selection': 0x2E7E00}
REGISTERS = (UC_X86_REG_RAX, UC_X86_REG_RBX, UC_X86_REG_RCX, UC_X86_REG_RDX,
             UC_X86_REG_RSI, UC_X86_REG_RDI, UC_X86_REG_RBP, UC_X86_REG_RSP,
             UC_X86_REG_R8, UC_X86_REG_R9, UC_X86_REG_R10, UC_X86_REG_R11,
             UC_X86_REG_R12, UC_X86_REG_R13, UC_X86_REG_R14, UC_X86_REG_R15,
             UC_X86_REG_EFLAGS)


@lru_cache(maxsize=1)
def emitted_hooks():
    outputs = []
    for runtime in (Lua51, Lua53, LuaJIT):
        lua = runtime(unpack_returned_tuples=True)
        fixture = lua.execute(CASES.replace('return cases', 'return setup'))()
        lua.execute(SHOP).install(fixture.api)

        def flatten(value):
            if isinstance(value, str):
                return value
            return ''.join(flatten(value[index]) for index in range(1, len(value)+1))

        outputs.append({name: flatten(fixture.hooks[BASE+rva]) for name, rva in HOOKS.items()})
    assert outputs[0] == outputs[1] == outputs[2]
    return outputs[0]


class ShopMachine:
    def __init__(self):
        self.uc = Uc(UC_ARCH_X86, UC_MODE_64)
        for address, size in ((BUFFER, 0x10000), (STORE, 0x60000), (STACK, 0x10000),
                              (CODE, 0x90000), ((BASE+0x6650A0) & ~0xFFF, 0x1000)):
            self.uc.mem_map(address, size)
        self.ends, self.starts = {}, {}
        assembler = Ks(KS_ARCH_X86, KS_MODE_64)
        for index, (name, source) in enumerate(emitted_hooks().items()):
            source = re.sub(r'\b(?:ds|ss):', '', source)
            source = source.replace('#L(jmp_success)', str(SEPARATE))
            source = source.replace('#MAKE_SHADOW_SPACE(32)', 'sub rsp, 64')
            source = source.replace('#DESTROY_SHADOW_SPACE', 'add rsp, 64')
            source = source.replace('call fixture', f'call {CALLBACK}\n')
            address = CODE + index*0x10000
            binary, _ = assembler.asm(source, address)
            self.uc.mem_write(address, bytes(binary))
            self.starts[name], self.ends[name] = address, address+len(binary)
        # Deliberately clobber caller-saved registers in the callback stand-in.
        callback, _ = assembler.asm('mov rax, 99; mov rcx, 98; mov rdx, 97; '
                                    'mov r8, 96; mov r9, 95; mov r10, 94; mov r11, 93; ret', CALLBACK)
        self.uc.mem_write(CALLBACK, bytes(callback))
        self.callbacks = 0
        self.uc.hook_add(UC_HOOK_CODE, self._on_code)
        self.ref(STORE, 'SHOP'); self.ref(ITEM+0x10, 'SYNTH001')
        self.q(BUFFER+72, 2)
        for index, position in enumerate((2, 3)):
            entry = self.entry(index)
            self.ref(entry, 'SHOP'); self.ref(entry+8, 'SYNTH001')
            self.d(entry+16, 375+index*175)
            self.q(entry+24, 1); self.q(entry+40, position)
        for index in range(3):
            self.ref(self.row(index), 'SYNTH001')
            self.d(self.row(index)+0x14, 1)
            node, ui = UI_NODES+index*0x20, self.ui(index)
            self.q(node, UI_NODES+(index+1)*0x20 if index<2 else 0)
            self.q(node+16, ui)
            self.d(ui+0x10, 1); self.d(ui+0x18, index); self.d(ui+0x20, 375)
        self.link_stock([0, 1, 2])
        self.q(BASE+0x6650A0, CHITIN); self.q(CHITIN+0x1118, SCREEN)
        self.q(SCREEN+0x7A0, STORE); self.q(SCREEN+0x6C8, UI_NODES)

    @staticmethod
    def entry(index): return BUFFER+128+index*64

    @staticmethod
    def row(index): return ROWS+index*0x40

    @staticmethod
    def ui(index): return UI_ROWS+index*0x40

    def ref(self, address, value):
        self.uc.mem_write(address, value.encode('ascii').ljust(8, b'\0'))

    def q(self, address, value=None):
        if value is not None: self.uc.mem_write(address, struct.pack('<Q', value))
        return struct.unpack('<Q', self.uc.mem_read(address, 8))[0]

    def d(self, address, value=None):
        if value is not None: self.uc.mem_write(address, struct.pack('<I', value & 0xFFFFFFFF))
        return struct.unpack('<I', self.uc.mem_read(address, 4))[0]

    def link_stock(self, indices):
        self.q(STORE+0xA8, NODES+indices[0]*0x20 if indices else 0)
        self.d(STORE+0xB8, len(indices))
        for ordinal, index in enumerate(indices):
            self.q(NODES+index*0x20, NODES+indices[ordinal+1]*0x20 if ordinal+1<len(indices) else 0)
            self.q(NODES+index*0x20+16, self.row(index))

    def _on_code(self, uc, address, _size, _data):
        if address == CALLBACK: self.callbacks += 1
        if address == SEPARATE or address in self.ends.values():
            self.stop = address; uc.emu_stop()

    def run(self, name, registers, *, changed=()):
        initial = {register: 0x1200+index for index, register in enumerate(REGISTERS)}
        initial.update({UC_X86_REG_RSP: SP, UC_X86_REG_EFLAGS: 0x246})
        initial.update(registers)
        for register, value in initial.items(): self.uc.reg_write(register, value)
        self.stop = None
        self.uc.emu_start(self.starts[name], 0, count=10000)
        assert self.stop in (self.ends[name], SEPARATE), 'hook did not reach a valid exit'
        for register, value in initial.items():
            if register not in changed:
                assert self.uc.reg_read(register) == value, f'{name} changed register {register}'
        return self.stop

    def price(self, index):
        self.d(SP+0x40, index); self.q(SP+0x68, BASE+0x2E7C45)
        self.run('price', {UC_X86_REG_RAX: 100, UC_X86_REG_RDI: ITEM, UC_X86_REG_R15: STORE},
                 changed=(UC_X86_REG_RAX,))
        return self.uc.reg_read(UC_X86_REG_RAX)

    def remove(self, original_index):
        self.d(self.row(original_index)+0x14, 0)
        self.run('removed', {UC_X86_REG_RSI: STORE, UC_X86_REG_RBX: NODES+original_index*0x20,
                             UC_X86_REG_RDI: self.row(original_index)})


class ShopRowAssemblyTests(unittest.TestCase):
    def test_duplicate_ordinary_and_owned_rows_keep_their_own_prices(self):
        machine = ShopMachine()
        self.assertEqual([machine.price(index) for index in range(3)], [100, 375, 550])
        self.assertEqual(machine.q(machine.entry(0)+32), machine.row(1))
        self.assertEqual(machine.q(machine.entry(1)+32), machine.row(2))

    def test_actual_compression_shifts_positions_and_retires_only_deleted_unit(self):
        machine = ShopMachine()
        machine.remove(0)  # Engine has chosen to unlink the earlier ordinary row.
        self.assertEqual([machine.q(machine.entry(i)+40) for i in range(2)], [1, 2])
        self.assertEqual([machine.q(machine.entry(i)+24) for i in range(2)], [1, 1])
        self.assertEqual([machine.d(machine.ui(i)+0x18) for i in range(3)], [0xFFFFFFFF, 0, 1])
        self.assertEqual([machine.d(machine.ui(i)+0x10) for i in range(3)], [0, 1, 1])
        machine.link_stock([1, 2])  # Original CObList::RemoveAt completes.
        self.assertEqual([machine.price(index) for index in range(2)], [375, 550])
        machine.remove(1)
        self.assertEqual([machine.q(machine.entry(i)+24) for i in range(2)], [2, 1])
        self.assertEqual([machine.q(machine.entry(i)+40) for i in range(2)], [0, 1])
        self.assertEqual(machine.d(machine.ui(2)+0x18), 0)
        self.assertEqual(machine.d(machine.ui(2)+0x10), 1)
        self.assertEqual(machine.callbacks, 2)

    def test_merge_protects_owned_rows_but_allows_failed_purchase_rollback(self):
        machine = ShopMachine()
        def merge(index):
            return machine.run('merge', {UC_X86_REG_RBP: STORE, UC_X86_REG_R13: machine.row(index)})
        self.assertEqual(merge(0), machine.ends['merge'])
        self.assertEqual(merge(1), SEPARATE)
        self.assertEqual(merge(2), SEPARATE)
        machine.d(machine.row(1)+0x14, 0)
        self.assertEqual(merge(1), machine.ends['merge'])
        self.assertEqual(machine.q(machine.entry(0)+24), 1)
        self.assertEqual(machine.q(machine.entry(0)+40), 2)
        self.assertEqual(machine.callbacks, 0)
        # Our AddStoreItem must append even when ordinary identical stock exists.
        machine.q(BUFFER, 1); machine.ref(BUFFER+8, 'SHOP'); machine.ref(BUFFER+16, 'SYNTH001')
        self.assertEqual(merge(0), SEPARATE)

    def test_equal_price_selection_matches_only_same_stock_index_once(self):
        machine = ShopMachine()
        old, new = machine.ui(1), UI_ROWS+0x200
        machine.d(new+0x20, 375)
        def select(index):
            machine.d(new+0x18, index)
            machine.run('selection', {UC_X86_REG_RBP: SCREEN, UC_X86_REG_RBX: new,
                                      UC_X86_REG_R8: ITEM, UC_X86_REG_R9: old,
                                      UC_X86_REG_RCX: UI_NODES+0x20},
                        changed=(UC_X86_REG_RCX, UC_X86_REG_EFLAGS))
            return bool(machine.uc.reg_read(UC_X86_REG_EFLAGS) & 0x40)
        self.assertFalse(select(0))
        self.assertEqual(machine.d(old+0x10), 1)
        self.assertTrue(select(1))
        self.assertEqual(machine.d(old+0x10), 0)
        self.assertEqual(machine.uc.reg_read(UC_X86_REG_RCX), BUFFER+80)
        self.assertFalse(select(1))
        machine.d(old+0x10, 1)
        machine.remove(0); machine.link_stock([1, 2])
        self.assertTrue(select(0))  # Same row retains selection after compaction.


if __name__ == '__main__':
    unittest.main()
