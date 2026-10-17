"""Spell Shield revision policy and virgin-store rewriting, without an engine."""

from copy import deepcopy
import re
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "work/source-gate-20260927/analysis-deps"))
from keystone import Ks, KS_ARCH_X86, KS_MODE_64
from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT
from flir_config.campaigns import compile_campaign, _render_lua
from flir_config.original_options import patch_spell_shield_stock
from flir_config.store_inventory import rewrite_inventory
from tests import test_spell_shield_option as option_tests
from tests.test_campaign_gate_bridge import CASES, CORE, GATE, POLICY, BUILD
from engine_sites import gate_targets, load
from tests.test_campaign_gate_policy import expected_fields
from tests.test_campaign_inventory_assembly import run, EXPECTED, OUTPUT, INPUT, BUFFER
from tests.test_source_gate_assembly import GateMachine, CODES, IMAGE, BUFFER as GATE_BUFFER


INVENTORY = (ROOT / "tools/probes/FLPRInventoryAsm.lua").read_text()


class SpellShieldRuntimeTests(unittest.TestCase):
    def fixture(self, runtime=LuaJIT, *, absent=False, randomized=True, anchor_first=False, no_guard=False):
        fixture = option_tests.SpellShieldOptionTests()
        self.addCleanup(fixture.doCleanups)
        _, catalog, world, profile, plan, original, _ = fixture.fixture(
            absent=absent, randomized=randomized, anchor_first=anchor_first)
        manifest = compile_campaign(profile, catalog, world, plan)
        spec = manifest["spell_shield"]
        effective = patch_spell_shield_stock(original)
        source = manifest["sources"][0]
        record = source["record"]["record"]
        endpoint = {"kind": "guarded-store", "store": "udduer01", "quantity": source["quantity"],
                    "payload": {key: record[key] for key in ("resref", "expiration", "charges", "flags")},
                    "record_index": spec["record_index"] if randomized else record["record_index"],
                    "multiple_stock": True}
        if randomized:
            endpoint["stock_option"] = "more_spell_shield"
        native = {"revision": manifest["revision"], "spell_shield": deepcopy(spec),
                  "source_gate": {"schema": "flpr-unvisited-fixture-gate-v1", "source_ids": [source["id"]]},
                  "sources": {source["id"]: endpoint}, "resource_hashes": {"UDDUER01.STO": spec["resource_sha256"]}}
        baseline = effective if randomized else original
        pristine = {"spell_shield": {manifest["revision"]: {"original": original, "effective": effective}},
                    "stores": {manifest["revision"]: {source["id"]: {
                        "original": baseline, "removed": rewrite_inventory(baseline, {endpoint["record_index"]})}}}}
        if no_guard:
            native["source_gate"]["source_ids"] = []
            native["sources"] = {}
            pristine["stores"] = {}
        lua = runtime(unpack_returned_tuples=True)
        core, build, policy, inventory = [lua.execute(code) for code in (CORE, BUILD, POLICY, INVENTORY)]
        gate = load(lua, GATE)
        f = lua.execute(CASES.replace("return cases", "return fixture"))(core)
        bundle = lua.execute(_render_lua({"manifest": manifest, "fixture": native}))
        f.inventory_groups = lua.table()
        install = lua.execute("""return function(f,Gate,bundle,build,policy,pristine,inventory)
            f.gate=Gate.install(f.api,bundle,build,policy,pristine,nil,function(group)
                f.inventory_groups[#f.inventory_groups+1]=group
                return inventory(group)
            end)
        end""")
        install(f, gate, bundle, build, policy, lua.table_from(pristine, recursive=True), inventory)
        return lua, f, bundle, original, effective

    def test_option_policy_uses_revision_without_any_source_phase_and_rejects_old_campaign(self):
        revision = "abcd" * 16
        sources = []
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime(unpack_returned_tuples=True)
            policy = lua.execute(POLICY)(revision, "more_spell_shield")
            self.assertEqual(len(policy.fields), 18)
            self.assertEqual(policy.mask, (1 << 18) - 1)
            sources.append(lua.execute(BUILD)(GATE_BUFFER, gate_targets(lua, IMAGE), policy))
        for fields, expected in ((expected_fields(revision)[:18], 1),
                                 (expected_fields("0123" * 16)[:18], 0),
                                 (expected_fields(revision)[:17], 0)):
            machine = GateMachine()
            for name in ("enter", "capture"):
                assembly = sources[0][name]
                self.assertEqual(assembly, sources[1][name])
                self.assertEqual(assembly, sources[2][name])
                code, _ = Ks(KS_ARCH_X86, KS_MODE_64).asm(re.sub(r"\b(?:ds|ss):", "", assembly), CODES[name])
                machine.uc.mem_write(CODES[name], bytes(code) + b"\x90")
                machine.sentinels[name] = CODES[name] + len(code)
            length = 0xB4 + 84 * len(fields)
            machine.gam(fields, declared=length)
            machine.run("enter", length=length)
            machine.run("capture")
            self.assertEqual(machine.read_q(GATE_BUFFER + 32), expected)

    def test_capture_kept_source_and_reset_preserve_independent_option_policy(self):
        for runtime in (Lua51, Lua53, LuaJIT):
            with self.subTest(runtime=runtime.__module__):
                lua, f, bundle, _, _ = self.fixture(runtime)
                lua.execute("""return function(f,bundle)
                    f.values.FLPR_FRESH=1
                    local controller={manifest=bundle.manifest,session={}}
                    function controller:source_is_kept() return true end
                    for _,member in ipairs(f.gate.members) do member:_write(40,1);member:_write(72,1) end
                    local option=f.gate.option_members[bundle.manifest.revision]
                    option:_write(40,1);option:_write(72,1)
                    for _,field in ipairs(option.policy.fields) do f.values[field.name]=field.value end
                    assert(f.gate:prepare_new(bundle,controller))
                    assert(f.gate.members[1]:_q(32)==0 and option:_q(32)==1)
                    f.gate:reset()
                    assert(option:_q(32)==0 and option:_q(262400)==0)
                end""")(f, bundle)

    def test_effective_stock_copy_removal_inserted_indexes_and_saved_store_bypass(self):
        for absent, randomized, anchor in ((False, True, False), (True, True, True),
                                           (True, False, True), (False, False, False)):
            with self.subTest(absent=absent, randomized=randomized, anchor=anchor):
                _, f, _, original, effective = self.fixture(absent=absent, randomized=randomized,
                                                            anchor_first=anchor)
                group = f.inventory_groups[1]
                normalized = {"kind": "sto", "ref": "udduer01", "length": len(original),
                              "expected": EXPECTED, "output": OUTPUT, "item_offset": group.item_offset,
                              "edits": [{"index": row.index, "buffer": BUFFER + i * 128, "context": 40}
                                        for i, row in enumerate(group.edits.values())],
                              "spell_shield": {"buffer": BUFFER + 0x8000, "context": 40,
                                               "effective": EXPECTED + 0x8000, "length": len(effective)}}
                for active in ((), (0,)):
                    pointer, result = run(normalized, original, ref="UDDUER01", stock_effective=effective,
                                          stock_active=True, active=active)
                    self.assertEqual(pointer, OUTPUT)
                    self.assertEqual(result, rewrite_inventory(effective, {normalized["edits"][0]["index"]})
                                     if active else effective)
                for enabled, saved in ((False, False), (True, True)):
                    pointer, result = run(normalized, original, ref="UDDUER01", stock_effective=effective,
                                          stock_active=enabled, stock_saved=saved, active=(0,))
                    self.assertEqual(pointer, INPUT)
                    self.assertEqual(result, b"\xCC" * len(effective))

    def test_independent_option_installs_when_no_source_guard_is_selected(self):
        lua, f, bundle, _, _ = self.fixture(randomized=False, no_guard=True)
        self.assertEqual(len(f.gate.members), 0)
        self.assertEqual(len(f.inventory_groups), 1)
        lua.execute("""return function(f,bundle)
            local option=f.gate.option_members[bundle.manifest.revision]
            f.values.FLPR_FRESH=1
            option:_write(40,1);option:_write(72,1)
            for _,field in ipairs(option.policy.fields) do f.values[field.name]=field.value end
            assert(f.gate:prepare_new(bundle,{manifest=bundle.manifest,session={}}))
            assert(option:_q(32)==1)
        end""")(f, bundle)


if __name__ == "__main__":
    unittest.main()
