"""Execute cursed item selection/view hooks against synthetic GAM/CRes memory."""
from copy import deepcopy
from pathlib import Path
import re
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "tests"),
               str(ROOT / "work/source-gate-20260927/analysis-deps")]
from keystone import Ks, KS_ARCH_X86, KS_MODE_64
from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT
from unicorn import Uc, UC_ARCH_X86, UC_MODE_64
from unicorn.x86_const import *
from flir_config.campaigns import _render_lua
from flir_config.cursed_disguises import cursed_disguise_indices, prepare_cursed_disguises
from test_cursed_disguises import resources, spec_for
from test_postinstall_resources import make_tlk
from test_campaign_gate_bridge import CASES, CORE, GATE, POLICY, BUILD
from test_campaign_gate_policy import expected_fields
from test_campaign_inventory_assembly import REGISTERS
from engine_sites import load

CUR = (ROOT / "prototype/FLPRCur.lua").read_text()
BASE, GAM, CRES, RAW, REF, STACK, ARRAY = 0x140000000, 0x500000, 0x600000, 0x610000, 0x620000, 0x700000, 0x630000
CODES = {"enter": 0x100000, "capture": 0x110000, "demand": 0x120000}


def fixture(runtime=LuaJIT, *, gate=False, second=False):
    data = resources()
    spec = spec_for(data)
    native, files, tlk = prepare_cursed_disguises(spec, data, make_tlk([]))
    revision = "a" * 64
    bundle = {"manifest": {"revision": revision, "original_options": {"cursed_disguise": True},
                           "cursed_disguises": spec},
              "fixture": {"revision": revision, "cursed_disguises": native, "sources": {},
                          "source_gate": {"schema": "flpr-unvisited-fixture-gate-v1", "source_ids": []}}}
    entry = {item["resref"]: {"original": files[item["resref"] + ".original.itm"],
                              "variants": [files[f"{item['resref']}.{index}.itm"]
                                           for index in range(1, len(item["variants"]) + 1)]}
             for item in native["items"]}
    lua = runtime(unpack_returned_tuples=True)
    core, policy = (lua.execute(source) for source in (CORE, POLICY))
    cur = load(lua, CUR)
    f = lua.execute(CASES.replace("return cases", "return fixture"))(core)
    b = lua.execute(_render_lua(bundle))
    bundles, entries = [b], {revision: entry}
    if second:
        other_spec = spec_for(data, item_revisions=True)
        other_native, other_files, _ = prepare_cursed_disguises(other_spec, data, tlk)
        other = deepcopy(bundle)
        other["manifest"].update(revision="b" * 64, cursed_disguises=other_spec)
        other["fixture"].update(revision="b" * 64, cursed_disguises=other_native)
        f.other_bundle = lua.execute(_render_lua(other))
        bundles.append(f.other_bundle)
        entries["b" * 64] = {item["resref"]: {
            "original": other_files[item["resref"] + ".original.itm"],
            "variants": [other_files[f"{item['resref']}.{index}.itm"]
                         for index in range(1, len(item["variants"]) + 1)]}
            for item in other_native["items"]}
    pristine = lua.table_from(entries, recursive=True)
    if gate:
        g = load(lua, GATE).install(f.api, lua.table_from(bundles), lua.execute(BUILD), policy,
                                     lua.table_from({"cursed": pristine}), None, None, cur)
        f.gate = g
        result = g.cursed
    else:
        result = cur.new(f.api, lua.table_from(bundles), pristine, policy)
    return lua, f, result, b, spec, entry


class Machine:
    def __init__(self, f, cur):
        self.uc = Uc(UC_ARCH_X86, UC_MODE_64)
        for address, size in ((0, 0x10000), (0x100000, 0x30000), (GAM, 0x10000),
                              (CRES, 0x40000), (STACK, 0x10000), (BASE + 0x727000, 0x2000)):
            self.uc.mem_map(address, size)
        self.state = int(cur.state)
        self.slots = {name: int(value) for name, value in cur.slots.items()}
        self.uc.mem_write(1000, bytes(int(f.bytes[i] or 0) for i in range(1000, 1000 + int(f.allocated_bytes))))
        self.lengths = {}
        for name, assembly in {"enter": cur.assembly.enter, "capture": cur.assembly.capture,
                               "demand": cur.demand}.items():
            code, _ = Ks(KS_ARCH_X86, KS_MODE_64).asm(re.sub(r"\b(?:ds|ss):", "", assembly), CODES[name])
            self.uc.mem_write(CODES[name], bytes(code))
            self.lengths[name] = len(code)
        self.q(BASE + 0x727C80, ARRAY)
        self.d(BASE + 0x727C88, 1)
        self.q(ARRAY, CRES)

    def q(self, addr, value=None):
        if value is not None:
            self.uc.mem_write(addr, struct.pack("<Q", value))
        return struct.unpack("<Q", self.uc.mem_read(addr, 8))[0]

    def d(self, addr, value):
        self.uc.mem_write(addr, struct.pack("<I", value))

    def item(self, name, data, *, kind=0x3ED):
        self.uc.mem_write(RAW, data)
        self.uc.mem_write(REF, name.upper().encode() + b"\0")
        self.q(CRES + 8, REF)
        self.d(CRES + 0x10, kind)
        self.q(CRES + 0x40, RAW)
        self.d(CRES + 0x48, len(data))
        self.uc.mem_write(CRES + 0x51, b"\1")
        self.q(CRES + 0x68, RAW)
        self.q(CRES + 0x60, RAW + struct.unpack_from("<I", data, 0x6A)[0])
        self.q(CRES + 0x58, RAW + struct.unpack_from("<I", data, 0x64)[0])

    def run(self, name, **overrides):
        start = {register: 0x123456 + index for index, register in enumerate(REGISTERS)}
        start.update({UC_X86_REG_RBX: CRES, UC_X86_REG_RDX: GAM, UC_X86_REG_R14: GAM,
                      UC_X86_REG_R8: 0xB4 + 19 * 84, UC_X86_REG_RSP: STACK + 0xF000,
                      UC_X86_REG_EFLAGS: 0x246})
        start.update({int(key): value for key, value in overrides.items()})
        for register, value in start.items():
            self.uc.reg_write(register, value)
        self.uc.emu_start(CODES[name], CODES[name] + self.lengths[name], count=300000)
        for register, value in start.items():
            if name != "demand" or register != UC_X86_REG_RAX:
                assert self.uc.reg_read(register) == value, (name, register, value, self.uc.reg_read(register))
        return self.uc.reg_read(UC_X86_REG_RAX)

    def load(self, seed, revision="a" * 64, *, fields=None, header=b"GAMEV2.0"):
        fields = expected_fields(revision)[:18] + [("FLPR_SEED", seed)] if fields is None else fields
        self.uc.mem_write(GAM, header)
        self.d(GAM + 0x38, 0xB4)
        self.d(GAM + 0x3C, len(fields))
        for index, (name, value) in enumerate(fields):
            row = bytearray(84)
            row[:len(name)] = name.encode()
            struct.pack_into("<i", row, 0x28, value)
            self.uc.mem_write(GAM + 0xB4 + index * 84, bytes(row))
        self.run("enter", **{str(UC_X86_REG_R8): 0xB4 + len(fields) * 84})
        self.run("capture")


class CursedRuntimeTests(unittest.TestCase):
    def test_runtime_generators_agree_and_both_demand_sites_skip_original_mov(self):
        code = []
        for runtime in (Lua51, Lua53, LuaJIT):
            lua, f, cur, bundle, spec, entry = fixture(runtime, gate=True)
            code.append((cur.assembly.enter, cur.assembly.capture, cur.demand))
            for block in cur.assemblies.values():
                for source in block.values():
                    binary, _ = Ks(KS_ARCH_X86, KS_MODE_64).asm(re.sub(r"\b(?:ds|ss):", "", source), 0x100000)
                    self.assertLess(len(binary), 3072)  # leave page room for EEex's hook wrapper
            self.assertEqual(len(f.gate.members), 0)
            self.assertEqual(len(f.hooks), 4)  # shared GAM entry/capture + exactly 2 item hooks
            for index in (3, 4):
                hook = f.hooks[index]
                self.assertEqual((hook[2], hook[3], hook[4]), (4, 4, 8))
        self.assertEqual(code[0], code[1])
        self.assertEqual(code[0], code[2])

    def test_gam_selection_all_faces_and_cached_seed_switch_restore(self):
        lua, f, cur, bundle, spec, entry = fixture()
        machine = Machine(f, cur)
        # Force the rejection branch of the first d4 draw as well as salt wrap.
        reject_seed = ((2147483646 * pow(16807, -1, 2147483647)) % 2147483647 - 1 - 570) % 2147483646 + 1
        for seed in (1, 1234567, 2147483646, reject_seed, 1):
            machine.load(seed)
            self.assertEqual(machine.q(cur.state + 8), seed)
            choices = cursed_disguise_indices(spec, seed)
            for name, face in choices.items():
                original = entry[name]["original"]
                machine.item(name, original)
                selected = machine.run("demand")
                self.assertEqual(selected, machine.q(cur.slots[name] + 8))
                self.assertEqual(bytes(machine.uc.mem_read(selected, len(original))), entry[name]["variants"][face - 1])
                self.assertEqual(machine.q(CRES + 0x68), selected)
                self.assertEqual(machine.q(CRES + 0x58), selected + 0x72)
                self.assertEqual(machine.q(CRES + 0x60), selected + 0xAA)
                self.assertEqual(machine.q(CRES + 0x40), RAW)
                self.assertEqual(bytes(machine.uc.mem_read(RAW, len(original))), original)
        # Track one stable CRes across save A -> B -> A, before another Demand.
        machine.item("plat17", entry["plat17"]["original"])
        machine.load(1)
        first = machine.run("demand")
        machine.load(1234567)
        self.assertNotEqual(machine.q(CRES + 0x68), first)
        machine.load(1)
        self.assertEqual(machine.q(CRES + 0x68), first)
        machine.load(1, revision="b" * 64)
        self.assertEqual(machine.q(CRES + 0x68), RAW)
        self.assertEqual(machine.run("demand"), RAW)

    def test_bad_saved_policy_and_changed_item_never_get_disguised(self):
        _, f, cur, bundle, spec, entry = fixture()
        machine = Machine(f, cur)
        original = entry["brac08"]["original"]
        machine.item("brac08", original)
        for fields in (expected_fields()[:18], expected_fields("a" * 64)[:18],
                       expected_fields("a" * 64)[:18] + [("FLPR_SEED", 0)],
                       expected_fields("a" * 64)[:18] + [("FLPR_SEED", 1), ("FLPR_SEED", 2)]):
            machine.load(1, fields=fields)
            self.assertEqual(machine.run("demand"), RAW)
        machine.load(1)
        changed = bytearray(original); changed[0x18] ^= 1
        machine.item("brac08", bytes(changed))
        self.assertEqual(machine.run("demand"), RAW)
        self.assertEqual(machine.q(cur.state + 16), 1)
        machine.item("brac08", original, kind=0x3EE)
        self.assertEqual(machine.run("demand"), RAW)

    def test_bgee_v11_and_tob_v21_games_are_read_like_v20_and_other_formats_are_not(self):
        # BG:EE keeps its games as GAME V1.1, BG2:EE and EET as V2.0 and their
        # Throne of Bhaal saves as V2.1.
        _, f, cur, bundle, spec, entry = fixture()
        name = "brac08"
        face = cursed_disguise_indices(spec, 1234567)[name]
        for header in (b"GAMEV1.1", b"GAMEV2.1"):
            with self.subTest(header=header):
                machine = Machine(f, cur)
                machine.item(name, entry[name]["original"])
                machine.load(1234567, header=header)
                self.assertEqual(machine.q(cur.state + 8), 1234567)
                selected = machine.run("demand")
                self.assertEqual(bytes(machine.uc.mem_read(selected, len(entry[name]["original"]))),
                                 entry[name]["variants"][face - 1])
        machine = Machine(f, cur)
        machine.item(name, entry[name]["original"])
        for header in (b"GAMEV1.0", b"GAMEV2.2"):
            with self.subTest(header=header):
                machine.load(1234567, header=header)
                self.assertEqual(machine.run("demand"), RAW)

    def test_removed_registry_object_is_not_dereferenced_on_load_or_reset(self):
        _, f, cur, bundle, spec, entry = fixture()
        machine = Machine(f, cur)
        # A freed object address is deliberately unmapped. Membership must be
        # checked first; no read of stale CRes memory is permissible.
        machine.q(cur.slots.brac08, 0xDEAD0000)
        machine.load(1)
        self.assertEqual(machine.q(cur.slots.brac08), 0)
        f.poke(f, cur.slots.brac08, 0xDEAD0000, 8)
        cur.reset(cur)
        self.assertEqual(f.peek(f, cur.slots.brac08, 8), 0)

    def test_registry_comes_from_the_engine_code_and_a_missing_location_stops_first(self):
        lua, f, cur, bundle, _, entry = fixture()
        # The emulated memory above places the registry where 2.6.6.0's code points.
        self.assertEqual((cur.registry, cur.registry_count), (BASE + 0x727C80, BASE + 0x727C88))
        f.api.FLPR_ENGINE_SITES.offsets.cur_registry_count = lua.table()
        hooks = len(f.hooks)
        with self.assertRaisesRegex(Exception, "CURSED_CODE_MISMATCH:cur_registry_count:not-found"):
            load(lua, CUR).new(f.api, lua.table_from([bundle]),
                               lua.table_from({"a" * 64: entry}, recursive=True), lua.execute(POLICY))
        self.assertEqual(len(f.hooks), hooks)

    def test_new_capture_uses_saved_seed_and_errors_reach_gate_adapter(self):
        for runtime in (Lua51, Lua53, LuaJIT):
            lua, f, cur, bundle, spec, entry = fixture(runtime, gate=True)
            lua.execute("""return function(f,bundle)
                f.values.FLPR_FRESH=1;f.values.flpr_seed=1234567
                local c=f.gate.cursed
                for _,field in ipairs(c.ordered[1].policy.fields) do
                    f.values[field.name]=field.seed and 1234567 or field.value
                end
                f.gate:prepare_new(bundle,{manifest=bundle.manifest,storage=f.storage})
                local adapter=f.gate:adapter(bundle,{})
                assert(adapter:check())
                f:poke(c.state+16,1,8)
                local ok,why=pcall(function() adapter:check() end)
                assert(not ok and tostring(why):find('CURSED_RESOURCE_CHANGED'))
            end""")(f, bundle)
            choices = cursed_disguise_indices(spec, 1234567)
            for row in cur.ordered[1]["items"].values():
                self.assertEqual(f.peek(f, cur.slots[row.resref] + 8, 8), row.pointers[choices[row.resref]])
            f.gate.reset(f.gate)
            self.assertTrue(all(f.peek(f, slot + 8, 8) == 0 for slot in cur.slots.values()))

    def test_switch_to_known_ir_revision_clears_plate_then_restores_saved_choice(self):
        _, f, cur, bundle, spec, entry = fixture(second=True)
        machine = Machine(f, cur)
        machine.item("plat17", entry["plat17"]["original"])
        machine.load(1)
        old = machine.run("demand")
        machine.load(1234567, revision="b" * 64)
        self.assertEqual(machine.q(cur.state), 2)
        self.assertEqual(machine.q(CRES + 0x68), RAW)
        self.assertEqual(machine.q(cur.slots.plat17 + 8), 0)
        machine.load(1)
        self.assertEqual(machine.q(CRES + 0x68), old)

    def test_new_game_refreshes_cached_view_then_option_off_returns_engine_raw(self):
        lua, f, cur, bundle, spec, entry = fixture()
        f.pokestring(f, RAW, entry["plat17"]["original"])
        f.pokestring(f, REF, b"PLAT17\0")
        for address, value, size in ((BASE + 0x727C80, ARRAY, 8), (BASE + 0x727C88, 1, 4),
                (ARRAY, CRES, 8), (CRES + 8, REF, 8), (CRES + 0x10, 0x3ED, 4),
                (CRES + 0x40, RAW, 8), (CRES + 0x48, len(entry["plat17"]["original"]), 4),
                (CRES + 0x51, 1, 1), (cur.slots.plat17, CRES, 8)):
            f.poke(f, address, value, size)
        setup = lua.execute("""return function(f,c,b,seed)
            f.values.FLPR_FRESH=1;f.values.flpr_seed=seed
            for _,field in ipairs(c.ordered[1].policy.fields) do
                f.values[field.name]=field.seed and seed or field.value
            end
            c:prepare_new(b,{manifest=b.manifest,storage=f.storage})
        end""")
        setup(f, cur, bundle, 1)
        old = f.peek(f, CRES + 0x68, 8)
        self.assertNotEqual(old, RAW)
        setup(f, cur, bundle, 1234567)
        self.assertNotEqual(f.peek(f, CRES + 0x68, 8), old)
        setup(f, cur, bundle, 1)
        self.assertEqual(f.peek(f, CRES + 0x68, 8), old)
        off = lua.execute(_render_lua({"manifest": {"revision": "c" * 64}}))
        self.assertTrue(cur.prepare_new(cur, off, None))
        self.assertEqual(f.peek(f, CRES + 0x68, 8), RAW)
        self.assertEqual(f.peek(f, CRES + 0x40, 8), RAW)


if __name__ == "__main__":
    unittest.main()
