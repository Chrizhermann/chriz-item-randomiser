"""Emulate private CRE/STO inventory edits; no engine or installed game access."""

from pathlib import Path
import re
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "work" / "source-gate-20260927" / "analysis-deps"))
sys.path.insert(0, str(ROOT / "tools"))

from keystone import Ks, KS_ARCH_X86, KS_MODE_64
from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT
from unicorn import Uc, UC_ARCH_X86, UC_MODE_64
from unicorn.x86_const import (
    UC_X86_REG_EFLAGS, UC_X86_REG_R8, UC_X86_REG_R9, UC_X86_REG_R10,
    UC_X86_REG_R11, UC_X86_REG_R12, UC_X86_REG_R13, UC_X86_REG_R14,
    UC_X86_REG_R15, UC_X86_REG_RAX, UC_X86_REG_RBP, UC_X86_REG_RBX,
    UC_X86_REG_RCX, UC_X86_REG_RDI, UC_X86_REG_RDX, UC_X86_REG_RSI,
    UC_X86_REG_RSP,
)

from flir_config.creature_inventory import rewrite_inventory as rewrite_cre
from flir_config.store_inventory import rewrite_inventory as rewrite_sto
from flir_config.world_formats import parse_cre
from tests.test_postinstall_world_formats import item_record, make_cre, make_sto


CODE, INPUT, EXPECTED, OUTPUT = 0x100000, 0x300000, 0x400000, 0x500000
BUFFER, CRES, REF, STACK = 0x600000, 0x700000, 0x710000, 0x800000
REGISTERS = (UC_X86_REG_RAX, UC_X86_REG_RBX, UC_X86_REG_RCX, UC_X86_REG_RDX,
             UC_X86_REG_RSI, UC_X86_REG_RDI, UC_X86_REG_R8, UC_X86_REG_R9,
             UC_X86_REG_R10, UC_X86_REG_R11, UC_X86_REG_R12, UC_X86_REG_R13,
             UC_X86_REG_R14, UC_X86_REG_R15, UC_X86_REG_RBP, UC_X86_REG_RSP,
             UC_X86_REG_EFLAGS)


def assemble(group):
    path = (ROOT / "tools" / "probes" / "FLPRInventoryAsm.lua").as_posix()
    sources = []
    for runtime in (Lua51, Lua53, LuaJIT):
        lua = runtime(unpack_returned_tuples=True)
        lua.globals().factory_path = path
        factory = lua.execute("return assert(loadfile(factory_path))()")
        sources.append(factory(lua.table_from(group, recursive=True)))
    assert sources[0] == sources[1] == sources[2]
    binary, _ = Ks(KS_ARCH_X86, KS_MODE_64).asm(
        re.sub(r"\b(?:ds|ss):", "", sources[0]), CODE)
    return bytes(binary)


def run(group, original, *, active=(), contexts=None, length=None, ref="SHOP01", actual=None,
        stock_effective=None, stock_active=False, stock_saved=False):
    actual = original if actual is None else actual
    uc = Uc(UC_ARCH_X86, UC_MODE_64)
    for address in (CODE, INPUT, EXPECTED, OUTPUT, BUFFER, CRES, REF, STACK):
        uc.mem_map(address, 0x10000)
    code = assemble(group)
    uc.mem_write(CODE, code)
    uc.mem_write(INPUT, actual)
    uc.mem_write(EXPECTED, original)
    output_length = len(stock_effective) if stock_effective is not None else len(original)
    uc.mem_write(OUTPUT, b"\xCC" * output_length)
    if stock_effective is not None:
        option = group["spell_shield"]
        uc.mem_write(option["effective"], stock_effective)
        uc.mem_write(option["buffer"] + 32, struct.pack("<Q", int(stock_active)))
        uc.mem_write(option["buffer"] + option["context"], struct.pack("<Q", int(stock_saved)))
    uc.mem_write(REF, ref.encode("ascii").ljust(8, b"\0"))
    uc.mem_write(CRES + 72, struct.pack("<I", len(original) if length is None else length))
    for index, row in enumerate(group["edits"]):
        address = row["buffer"]
        uc.mem_write(address + 32, struct.pack("<Q", int(index in active)))
        if group["kind"] == "sto":
            uc.mem_write(address + row["context"], struct.pack("<Q", (contexts or {}).get(index, 0)))
    sp = STACK + 0xF000
    uc.mem_write(sp + 32, struct.pack("<Q", CRES))
    starting = {
        UC_X86_REG_RAX: 0x123456, UC_X86_REG_RBX: REF,
        UC_X86_REG_RCX: 0x234567, UC_X86_REG_RDX: INPUT,
        UC_X86_REG_RSI: 0x345678, UC_X86_REG_RDI: INPUT,
        UC_X86_REG_R8: len(original) if length is None else length,
        UC_X86_REG_R9: 0x456789, UC_X86_REG_R10: 0x56789A,
        UC_X86_REG_R11: 0x6789AB, UC_X86_REG_R12: 0x789ABC,
        UC_X86_REG_R13: 0x89ABCD, UC_X86_REG_R14: 0x9ABCDE,
        UC_X86_REG_R15: 0xABCDEF, UC_X86_REG_RBP: STACK + 0x8000,
        UC_X86_REG_RSP: sp,
        UC_X86_REG_EFLAGS: 0x246,
    }
    for register, value in starting.items():
        uc.reg_write(register, value)
    uc.emu_start(CODE, CODE + len(code), count=200000)
    handed = UC_X86_REG_RDX if group["kind"] == "cre" else UC_X86_REG_RDI
    for register in REGISTERS:
        if register != handed:
            assert uc.reg_read(register) == starting[register], f"register {register} changed"
    assert bytes(uc.mem_read(INPUT, len(original))) == actual
    return uc.reg_read(handed), bytes(uc.mem_read(OUTPUT, output_length))


def cre_group(data):
    parsed = parse_cre(data)
    return {"kind": "cre", "length": len(data), "expected": EXPECTED, "output": OUTPUT,
            "item_offset": parsed["items_offset"], "slot_offset": parsed["item_slots_offset"],
            "edits": [
                {"index": 3, "buffer": BUFFER + 0x300},
                {"index": 2, "buffer": BUFFER + 0x200},
                {"index": 1, "buffer": BUFFER + 0x100, "replacement": "sw1h01"},
                {"index": 0, "buffer": BUFFER},
            ]}


def sto_group(data):
    return {"kind": "sto", "length": len(data), "expected": EXPECTED, "output": OUTPUT,
            "item_offset": struct.unpack_from("<I", data, 0x34)[0], "ref": "SHOP01",
            "edits": [
                {"index": 3, "buffer": BUFFER + 0x300, "context": 40},
                {"index": 2, "buffer": BUFFER + 0x200, "context": 40},
                {"index": 1, "buffer": BUFFER + 0x100, "context": 40},
            ]}


class InventoryAssemblyTests(unittest.TestCase):
    def test_cre_mixed_keep_removal_and_equipped_replacement_match_python(self):
        data = make_cre([item_record(f"ITEM000{i}") for i in range(4)],
                        slots={9: 0, 10: 1, 11: 2, 12: 3, 21: 3}, gap=7)
        slot_offset = parse_cre(data)["item_slots_offset"]
        struct.pack_into("<HH", data, slot_offset + 76, 0, 2)
        original = bytes(data)
        group = cre_group(original)
        pointer, result = run(group, original, active=(1, 2, 3))
        self.assertEqual(pointer, OUTPUT)
        self.assertEqual(result, rewrite_cre(original, {0: None, 1: "sw1h01", 2: None}))

    def test_sto_multiple_removals_skip_saved_store_context_and_match_python(self):
        data = bytes(make_sto([(item_record(f"ITEM000{i}"), 2, 0)
                               for i in range(4)], gap=9))
        group = sto_group(data)
        pointer, result = run(group, data, active=(0, 1, 2), contexts={1: 1})
        self.assertEqual(pointer, OUTPUT)
        self.assertEqual(result, rewrite_sto(data, {1, 3}))

    def test_inactive_or_wrong_length_never_edits_original(self):
        cre = bytes(make_cre([item_record(f"ITEM000{i}") for i in range(4)]))
        sto = bytes(make_sto([(item_record(f"ITEM000{i}"), 2, 0)
                              for i in range(4)]))
        for data, group in ((cre, cre_group(cre)), (sto, sto_group(sto))):
            with self.subTest(kind=group["kind"], condition="inactive"):
                pointer, result = run(group, data)
                self.assertEqual(pointer, OUTPUT)
                self.assertEqual(result, data)
            with self.subTest(kind=group["kind"], condition="wrong-length"):
                pointer, result = run(group, data, active=range(len(group["edits"])),
                                      length=len(data) - 1)
                self.assertEqual(pointer, INPUT)
                self.assertEqual(result, b"\xCC" * len(data))
            with self.subTest(kind=group["kind"], condition="source-absent"):
                changed = bytearray(data)
                changed[group["item_offset"]] = ord("X")
                pointer, result = run(group, data, active=range(len(group["edits"])),
                                      actual=bytes(changed))
                self.assertEqual(pointer, INPUT)
                self.assertEqual(result, b"\xCC" * len(data))
        pointer, result = run(sto_group(sto), sto, active=(0, 1, 2), ref="OTHER")
        self.assertEqual(pointer, INPUT)
        self.assertEqual(result, b"\xCC" * len(sto))


if __name__ == "__main__":
    unittest.main()
