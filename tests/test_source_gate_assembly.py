"""Emulate generated FLPRU1 gate instructions against synthetic memory only.

The EEex manual-hook exit macro is replaced with a no-op. These checks cannot
establish the real hook wrapper, engine ABI, or live-game behavior.
"""
from pathlib import Path
from functools import lru_cache
import re
import struct
import unittest

from keystone import Ks, KS_ARCH_X86, KS_MODE_64
from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT
from unicorn import Uc, UC_ARCH_X86, UC_MODE_64, UC_HOOK_CODE, UC_HOOK_MEM_READ
from unicorn.x86_const import (
    UC_X86_REG_EFLAGS, UC_X86_REG_R8, UC_X86_REG_R9, UC_X86_REG_R10,
    UC_X86_REG_R11, UC_X86_REG_R14, UC_X86_REG_R15, UC_X86_REG_RAX,
    UC_X86_REG_RBP, UC_X86_REG_RCX, UC_X86_REG_RDX, UC_X86_REG_RSP,
)


ROOT = Path(__file__).resolve().parents[1]
BUFFER = 0x300000
IMAGE = 0x10000000
SKIP = IMAGE + 0x1906FC
INPUT = 0x500000
AREA = 0x600000
OWNER = 0x601000
CONTAINER = 0x602000
ITEMS = 0x603000
STACK = 0x700000
CODES = {"enter": 0x100000, "capture": 0x110000, "item": 0x120000}
REGISTERS = (UC_X86_REG_RAX, UC_X86_REG_RCX, UC_X86_REG_RDX, UC_X86_REG_R8,
             UC_X86_REG_R9, UC_X86_REG_R10, UC_X86_REG_R11, UC_X86_REG_R14,
             UC_X86_REG_R15, UC_X86_REG_RBP, UC_X86_REG_RSP, UC_X86_REG_EFLAGS)


@lru_cache(maxsize=1)
def generated():
    path = (ROOT / "tools/probes/FLPRGateAsm.lua").as_posix()
    outputs = []
    for runtime in (Lua51, Lua53, LuaJIT):
        lua = runtime(unpack_returned_tuples=True)
        lua.globals().factory_path = path
        factory = lua.execute("return assert(loadfile(factory_path))()")
        table = factory(BUFFER, IMAGE)
        outputs.append({name: table[name] for name in CODES})
    assert outputs[0] == outputs[1] == outputs[2]
    return outputs[0]


@lru_cache(maxsize=1)
def compiled():
    assembler = Ks(KS_ARCH_X86, KS_MODE_64)
    binaries = {}
    for name, source in generated().items():
        source = re.sub(r"\b(?:ds|ss):", "", source)
        source = source.replace("#MANUAL_HOOK_EXIT(1)", "nop")
        binary, _ = assembler.asm(source, CODES[name])
        binaries[name] = bytes(binary)
    return binaries


class GateMachine:
    def __init__(self):
        self.uc = Uc(UC_ARCH_X86, UC_MODE_64)
        self.uc.mem_map(0x100000, 0x30000)
        self.uc.mem_map(0x300000, 0x50000)
        self.uc.mem_map(0x500000, 0x10000)
        self.uc.mem_map(0x600000, 0x10000)
        self.uc.mem_map(STACK, 0x10000)
        self.uc.mem_map(SKIP & ~0xFFF, 0x1000)
        self.stop = None
        self.steps = 0
        self.reads = []
        for name, binary in compiled().items():
            self.uc.mem_write(CODES[name], binary + b"\x90")
        self.sentinels = {name: CODES[name] + len(binary)
                          for name, binary in compiled().items()}
        self.uc.mem_write(SKIP, b"\x90")
        self.uc.hook_add(UC_HOOK_CODE, self._on_code)
        self.uc.hook_add(UC_HOOK_MEM_READ, self._on_read)
        self.write_q(BUFFER, 1)
        self.write_q(AREA + 0x204, int.from_bytes(b"FLPRU1\0\0", "little"))
        self.write_q(STACK + 0x8000 - 0x60, AREA)
        self.write_q(STACK + 0x8000 - 0x68, OWNER)
        self.write_q(STACK + 0x8000 - 0x78, ITEMS)

    def _on_code(self, uc, address, size, user_data):
        self.steps += 1
        if address in self.sentinels.values() or address == SKIP:
            self.stop = "skip" if address == SKIP else "continue"
            uc.emu_stop()
        if self.steps > 200000:
            raise AssertionError("instruction limit reached")

    def _on_read(self, uc, access, address, size, value, user_data):
        self.reads.append((address, size))

    def write(self, address, payload):
        self.uc.mem_write(address, bytes(payload))

    def write_q(self, address, value):
        self.write(address, struct.pack("<Q", value))

    def read_q(self, address):
        return struct.unpack("<Q", self.uc.mem_read(address, 8))[0]

    def write_d(self, address, value):
        self.write(address, struct.pack("<I", value))

    def gam(self, modes=(), *, declared=0xB4, header=b"GAMEV2.0",
            variables_offset=0xB4, variables_count=None):
        self.write(INPUT, header)
        self.write_d(INPUT+0x38, variables_offset)
        self.write_d(INPUT+0x3C, len(modes) if variables_count is None else variables_count)
        for index, (name, value) in enumerate(modes):
            row = bytearray(84)
            encoded = name.encode("ascii")
            row[:len(encoded)] = encoded
            struct.pack_into("<i", row, 0x28, value)
            self.write(INPUT+variables_offset+84*index, row)
        return declared

    def item(self, *, name=b"FLPRU1A\0", index=1, item=b"FLPRI1\0\0" + b"\0"*8 + b"\x01\0\0\0",
             first_count=(1, 2), placement=(1140, 650, 4, 0), owner=OWNER):
        self.write(CONTAINER, name.ljust(32, b"\0"))
        self.write_q(CONTAINER+0x40, first_count[0] | (first_count[1] << 32))
        x, y, kind, lock = placement
        self.write_q(CONTAINER+0x20, x | (y << 16) | (kind << 32) | (lock << 48))
        self.write(ITEMS+index*20, item)
        self.write_q(STACK+0x8000-0x68, owner)
        return index

    def select(self, mode=1):
        values = [] if mode is None else [("FLPR_U1_MODE", mode)]
        size = self.gam(values, declared=0xB4+84*len(values))
        self.run("enter", length=size)
        self.run("capture")
        return self.read_q(BUFFER+32)

    def run(self, name, *, input_ptr=INPUT, length=0xB4, index=1,
            container=CONTAINER, regs=None):
        starting = {
            UC_X86_REG_RAX: 0x123456, UC_X86_REG_RCX: 0x234567,
            UC_X86_REG_RDX: input_ptr, UC_X86_REG_R8: length,
            UC_X86_REG_R9: 0x345678, UC_X86_REG_R10: 0x456789,
            UC_X86_REG_R11: 0x56789A, UC_X86_REG_R14: input_ptr if name == "capture" else container,
            UC_X86_REG_R15: index, UC_X86_REG_RBP: STACK+0x8000,
            UC_X86_REG_RSP: STACK+0xF000, UC_X86_REG_EFLAGS: 0x246,
        }
        if regs:
            starting.update(regs)
        for register, value in starting.items():
            self.uc.reg_write(register, value)
        self.stop = None
        self.steps = 0
        self.uc.emu_start(CODES[name], 0, count=200000)
        assert self.stop in ("continue", "skip"), "No continuation reached"
        for register in REGISTERS:
            assert self.uc.reg_read(register) == starting[register], f"register {register} changed"
        assert self.read_q(BUFFER+24) == 0, "active writer count leaked"
        return self.stop


class SourceGateAssemblyTests(unittest.TestCase):
    def test_selected_mode_then_excluded_then_missing_is_recaptured(self):
        machine = GateMachine()
        for mode, expected in ((1, 1), (2, 2), (None, 0)):
            values = [] if mode is None else [("FLPR_U1_MODE", mode)]
            size = machine.gam(values, declared=0xB4+84*len(values))
            self.assertEqual(machine.run("enter", length=size), "continue")
            self.assertEqual(machine.run("capture"), "continue")
            self.assertEqual(machine.read_q(BUFFER+32), expected)
            self.assertEqual(machine.read_q(BUFFER+72), 1 if mode is None else 0)
        self.assertEqual(machine.read_q(BUFFER+40), 3)

    def test_gam_bad_guards_leave_mode_cleared(self):
        cases = (
            {"header": b"NOTGAME!"}, {"declared": 0xB3},
            {"variables_offset": 0xB3}, {"variables_count": 16385},
            {"variables_offset": 0xB4, "variables_count": 1},
        )
        for change in cases:
            with self.subTest(change=change):
                machine = GateMachine()
                size = machine.gam([], **change)
                machine.run("enter", length=size)
                machine.run("capture")
                self.assertEqual(machine.read_q(BUFFER+32), 0)
                self.assertGreater(machine.read_q(BUFFER+72), 0)
                self.assertFalse(any(address >= INPUT+0xB4 and address < INPUT+0x10000
                                     for address, length in machine.reads))

    def test_capture_rejects_pointer_mismatch_and_disabled_gate(self):
        machine = GateMachine()
        size = machine.gam([("FLPR_U1_MODE", 1)], declared=0xB4+84)
        machine.run("enter", length=size)
        machine.run("capture", regs={UC_X86_REG_R14: INPUT+0x1000})
        self.assertEqual(machine.read_q(BUFFER+32), 0)
        self.assertEqual(machine.read_q(BUFFER+72), 5)
        self.assertEqual(machine.read_q(BUFFER+48), 0)
        machine.write_q(BUFFER, 0)
        tickets = machine.read_q(BUFFER+8)
        machine.run("enter", length=size)
        machine.run("capture")
        self.assertEqual(machine.read_q(BUFFER+8), tickets)
        self.assertEqual(machine.read_q(BUFFER+32), 0)
        null_machine = GateMachine()
        null_machine.run("enter", input_ptr=0, length=0xB4)
        null_machine.run("capture", input_ptr=0, regs={UC_X86_REG_R14: 0})
        self.assertEqual(null_machine.read_q(BUFFER+32), 0)

    def test_duplicate_and_invalid_mode_are_not_selected(self):
        for values in (("FLPR_U1_MODE", 3), ("FLPR_U1_MODE", -1)):
            machine = GateMachine()
            size = machine.gam([values], declared=0xB4+84)
            machine.run("enter", length=size)
            machine.run("capture")
            self.assertEqual(machine.read_q(BUFFER+32), 0)
        machine = GateMachine()
        size = machine.gam([("FLPR_U1_MODE", 1), ("FLPR_U1_MODE", 2)], declared=0xB4+168)
        machine.run("enter", length=size)
        machine.run("capture")
        self.assertEqual(machine.read_q(BUFFER+32), 0)
        self.assertEqual(machine.read_q(BUFFER+80), 2)

    def test_selected_exact_item_skips_once_and_logs_raw_bytes(self):
        machine = GateMachine()
        self.assertEqual(machine.select(), 1)
        index = machine.item()
        self.assertEqual(machine.run("item", index=index), "skip")
        self.assertEqual(machine.read_q(BUFFER+96), 1)
        self.assertEqual(machine.read_q(BUFFER+120), 1)
        row = BUFFER+128+2*128
        self.assertEqual(machine.read_q(row), 3)
        self.assertEqual(machine.read_q(row+24), 1)
        self.assertEqual(machine.read_q(row+48), 1)
        self.assertEqual(machine.read_q(row+8), 1)
        self.assertEqual(machine.read_q(row+16), 1)
        self.assertEqual(machine.read_q(row+32), AREA)
        self.assertEqual(machine.read_q(row+40), OWNER)
        self.assertEqual(bytes(machine.uc.mem_read(row+56,32)), bytes(machine.uc.mem_read(CONTAINER,32)))
        self.assertEqual(bytes(machine.uc.mem_read(row+88,20)), bytes(machine.uc.mem_read(ITEMS+20,20)))
        self.assertEqual(machine.run("item", index=index), "continue")
        self.assertEqual(machine.read_q(BUFFER+96), 1)

    def test_mode_and_candidate_field_drifts_preserve_items(self):
        default_item = b"FLPRI1\0\0" + b"\0"*8 + b"\x01\0\0\0"
        drifts = (
            ("mode excluded", {}, 2, 0),
            ("mode missing", {}, None, 0),
            ("owner missing", {"owner": 0}, 1, 0),
            ("container name", {"name": b"FLPRU1B\0"}, 1, 0),
            ("physical index", {"index": 2}, 1, 2),
            ("first index", {"first_count": (0, 2)}, 1, 2),
            ("item count", {"first_count": (1, 1)}, 1, 2),
            ("x", {"placement": (1141, 650, 4, 0)}, 1, 2),
            ("y", {"placement": (1140, 651, 4, 0)}, 1, 2),
            ("type", {"placement": (1140, 650, 5, 0)}, 1, 2),
            ("lock", {"placement": (1140, 650, 4, 1)}, 1, 2),
            ("item resref", {"item": b"FLPRI2\0\0" + default_item[8:]}, 1, 0),
            ("expiration", {"item": default_item[:8] + b"\x01" + default_item[9:]}, 1, 2),
            ("charges", {"item": default_item[:10] + b"\x01" + default_item[11:]}, 1, 2),
            ("flags", {"item": default_item[:16] + b"\x02\0\0\0"}, 1, 2),
        )
        for label, change, mode, decision in drifts:
            with self.subTest(label=label):
                machine = GateMachine()
                self.assertEqual(machine.select(mode), 0 if mode is None else mode)
                index = machine.item(**change)
                self.assertEqual(machine.run("item", index=index), "continue")
                self.assertEqual(machine.read_q(BUFFER+96), 0)
                self.assertEqual(machine.read_q(BUFFER+120), 0)
                self.assertEqual(machine.read_q(BUFFER+128+2*128+24), decision)

    def test_each_nonzero_payload_byte_prevents_suppression(self):
        original = b"FLPRI1\0\0" + b"\0"*8 + b"\x01\0\0\0"
        for offset in range(8, 20):
            with self.subTest(payload_byte=offset):
                machine = GateMachine()
                machine.select()
                changed = bytearray(original)
                changed[offset] = 2 if offset == 16 else 1
                machine.item(item=bytes(changed))
                self.assertEqual(machine.run("item"), "continue")
                self.assertEqual(machine.read_q(BUFFER+96), 0)

    def test_new_load_resets_perload_skip_but_keeps_total(self):
        machine = GateMachine()
        machine.select()
        machine.item()
        self.assertEqual(machine.run("item"), "skip")
        self.assertEqual(machine.read_q(BUFFER+96), 1)
        self.assertEqual(machine.read_q(BUFFER+120), 1)
        machine.select(2)
        self.assertEqual(machine.read_q(BUFFER+96), 1)
        self.assertEqual(machine.read_q(BUFFER+120), 0)
        self.assertEqual(machine.run("item"), "continue")
        machine.select(1)
        self.assertEqual(machine.run("item"), "skip")
        self.assertEqual(machine.read_q(BUFFER+96), 2)
        self.assertEqual(machine.read_q(BUFFER+120), 1)

    def test_wrong_area_and_stopped_gate_never_allocate_item_rows(self):
        machine = GateMachine()
        machine.select()
        machine.item()
        machine.write_q(AREA+0x204, 0)
        tickets = machine.read_q(BUFFER+8)
        self.assertEqual(machine.run("item"), "continue")
        self.assertEqual(machine.read_q(BUFFER+8), tickets)
        machine.write_q(AREA+0x204, int.from_bytes(b"FLPRU1\0\0", "little"))
        machine.write_q(BUFFER, 0)
        self.assertEqual(machine.run("item"), "continue")
        self.assertEqual(machine.read_q(BUFFER+8), tickets)

    def test_overflow_disables_mode_and_prevents_suppression(self):
        machine = GateMachine()
        machine.select()
        machine.item()
        machine.write_q(BUFFER+8, 2048)
        self.assertEqual(machine.run("item"), "continue")
        self.assertEqual(machine.read_q(BUFFER+16), 1)
        self.assertEqual(machine.read_q(BUFFER+32), 0)
        self.assertEqual(machine.read_q(BUFFER+96), 0)


if __name__ == "__main__":
    unittest.main()
