"""Reviewed dialogue-source CRE copies share selection without extra reward units."""
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
from flir_config.creature_inventory import rewrite_inventory
from test_campaign_gate_bridge import CASES, CORE, GATE, POLICY, BUILD
from engine_sites import gate_targets, load
from test_campaign_gate_policy import BIND, POLICY_TABLE, expected_fields
from test_campaign_inventory_assembly import run, BUFFER, EXPECTED, OUTPUT
from test_postinstall_world_formats import make_cre, item_record
from test_source_gate_assembly import GateMachine, BUFFER as POLICY_BUFFER, IMAGE, CODES

REVISION = "a" * 64
SOURCE = "original-source:bg2:mode2:item:12"
INVENTORY = (ROOT / "tools/probes/FLPRInventoryAsm.lua").read_text()
MEMORY_SIZE = 128 + 2049 * 128 + 32 + 64 * 64
FAMILIES = {12: ("scyarryl", "blun20", 0), 32: ("trgeni01", "sw1h23", 0),
            39: ("loutcapt", "dagg11", 6), 84: ("amcler01", "ring43", 0)}
SETUP = """
return function(f,Gate,build,compile,Inventory,original,family,creature,item,record)
    local id='original-source:bg2:mode2:item:'..family
    local selected='FLPR_QM2_'..family
    local p={resref=item,expiration=0,charges={0,0,0},flags=0}
    local aux={resource='cre:'..creature,resource_sha256=string.rep('2',64),
        record_indices={record},payload=p,selected_global=selected}
    local manifest={revision=string.rep('a',64),sources={{id=id,quantity=1,
        resource_sha256=string.rep('1',64),record={kind='authored-source',record=p}}}}
    local fixture={revision=manifest.revision,sources={[id]={kind='guarded-script',
        selected_global=selected,auxiliary_creatures={aux}}},
        resource_hashes={[creature:upper()..'.CRE']=aux.resource_sha256},
        source_gate={schema='flpr-unvisited-fixture-gate-v1',source_ids={id}}}
    if family==32 then
        aux.replacement='blun05';fixture.resource_hashes['BLUN05.ITM']=string.rep('3',64)
    end
    local bundle={manifest=manifest,fixture=fixture}
    local pristine={auxiliary={[manifest.revision]={[id]={['cre:'..creature]=original}}},
        scripts={[manifest.revision]={[id]='reviewed-script'}}}
    -- Only the scripted transport is mocked. The auxiliary uses the actual
    -- policy, Gate member lifecycle, exact CRE match and inventory assembler.
    local Script={}
    function Script.new(api,selected_bundle,source_id,bytes)
        assert(source_id==id and bytes=='reviewed-script')
        local member={binding={id=id},revision=manifest.revision,index=1}
        function member:_bundle(b) assert(b==bundle) end
        function member:prepare_new(b,controller)
            self:_bundle(b)
            if not controller:source_is_kept(1) then f.values[selected]=1 end
        end
        function member:reset() end
        function member:inspect() return {selected=f.values[selected]} end
        function member:adapter(b)
            self:_bundle(b);f.adapter_count=(f.adapter_count or 0)+1
            return {observe_guard=function() return 'primary-only' end,destroy=function() end}
        end
        return member
    end
    f.groups={}
    f.gate=Gate.install(f.api,bundle,build,compile,pristine,Script,function(group)
        f.groups[#f.groups+1]=group;return Inventory(group)
    end)
    return bundle
end
"""


def fixture(runtime=LuaJIT, *, family=12, original=None):
    lua = runtime(unpack_returned_tuples=True)
    core, build, policy, inventory = [lua.execute(text) for text in (CORE, BUILD, POLICY, INVENTORY)]
    gate = load(lua, GATE)
    f = lua.execute(CASES.replace("return cases", "return fixture"))(core)
    creature, item, record = FAMILIES[family]
    if original is None:
        records = [item_record("OTHER00", charges=(1, 2, 3)) for _ in range(record)]
        records += [item_record(item.upper(), expiration=0, charges=(0, 0, 0), flags=0),
                    item_record("OTHER01", charges=(2, 3, 4), flags=3),
                    item_record("OTHER02", charges=(1, 0, 7), flags=1)]
        original = bytes(make_cre(records, slots={9: record, 21: record + 1, 22: record + 2}, gap=13))
    bundle = lua.execute(SETUP)(f, gate, build, policy, inventory, original,
                               family, creature, item, record)
    return lua, f, bundle, original


def loaded_policy(phase=1, selected=1, *, revision=REVISION, permanent=True, selected_global="FLPR_QM2_12"):
    lua = LuaJIT(unpack_returned_tuples=True)
    policy = lua.execute(POLICY)(REVISION, 1, True if permanent else None,
                                 selected_global if permanent else None)
    table = bytes.fromhex(lua.eval(BIND)(policy, POLICY_TABLE))
    assembly = lua.execute(BUILD)(POLICY_BUFFER, gate_targets(lua, IMAGE), policy)
    machine = GateMachine()
    machine.uc.mem_write(POLICY_TABLE, table)
    for name in ("enter", "capture"):
        binary, _ = Ks(KS_ARCH_X86, KS_MODE_64).asm(re.sub(r"\b(?:ds|ss):", "", assembly[name]), CODES[name])
        machine.uc.mem_write(CODES[name], bytes(binary) + b"\x90")
        machine.sentinels[name] = CODES[name] + len(binary)
    fields = expected_fields(revision, phase=phase)
    if selected is not None:
        fields.append((selected_global, selected))
    size = 0xB4 + len(fields) * 84
    machine.gam(fields, declared=size)
    machine.run("enter", length=size)
    machine.run("capture")
    return machine


class AuxiliaryCreatureTests(unittest.TestCase):
    def test_trgeni_preserves_weapon_slot_with_exact_original_blun05_replacement(self):
        _, f, bundle, original = fixture(family=32)
        auxiliary, group = f.gate.auxiliary_members[REVISION][1], f.groups[1]
        self.assertEqual(auxiliary.binding.replacement, "blun05")
        normalized = {"kind": "cre", "length": group.length, "expected": EXPECTED, "output": OUTPUT,
                      "item_offset": group.item_offset, "slot_offset": group.slot_offset,
                      "edits": [{"index": row.index, "buffer": BUFFER, "context": 40,
                                 "replacement": row.replacement} for row in group.edits.values()]}
        pointer, changed = run(normalized, original, active=(0,))
        self.assertEqual(pointer, OUTPUT)
        at = group.item_offset
        self.assertEqual(changed, original[:at] + b"blun05\0\0" + original[at + 8:])
        self.assertEqual(changed, rewrite_inventory(original, {0: "blun05"}))
        self.assertEqual(struct.unpack_from("<H", changed, group.slot_offset + 9 * 2)[0], 0)
        self.assertEqual(run(normalized, original)[1], original)
        endpoint = bundle.fixture.sources["original-source:bg2:mode2:item:32"]
        endpoint.auxiliary_creatures[1].replacement = None
        with self.assertRaisesRegex(Exception, "CAMPAIGN_GATE_AUXILIARY_REPLACEMENT_INVALID"):
            auxiliary._bundle(auxiliary, bundle)
        endpoint.auxiliary_creatures[1].replacement = "blun05"
        bundle.fixture.resource_hashes["BLUN05.ITM"] = None
        with self.assertRaisesRegex(Exception, "CAMPAIGN_GATE_AUXILIARY_REPLACEMENT_INVALID"):
            auxiliary._bundle(auxiliary, bundle)

    def test_reviewed_dialogue_families_bind_only_their_exact_duplicate(self):
        for family in (32, 39, 84):
            with self.subTest(family=family):
                _, f, bundle, _ = fixture(family=family)
                auxiliary = f.gate.auxiliary_members[REVISION][1]
                creature, item, record = FAMILIES[family]
                self.assertEqual(len(bundle.manifest.sources), 1)
                self.assertEqual(len(f.gate.members), 1)
                self.assertEqual(auxiliary.index, 1)
                self.assertEqual(auxiliary.binding.creature_resource, "cre:" + creature)
                self.assertEqual(auxiliary.binding.payload.resref, item)
                self.assertEqual(auxiliary.binding.records[1], record)
                self.assertEqual(auxiliary.policy.fields[20].name, f"FLPR_QM2_{family}")
                self.assertEqual(sum(not f.idle(f, index) for index in f.hooks.keys()), 3)
                # A different approved creature still cannot be bound to this source.
                endpoint = bundle.fixture.sources[f"original-source:bg2:mode2:item:{family}"]
                endpoint.auxiliary_creatures[1].resource = "cre:scyarryl"
                with self.assertRaisesRegex(Exception, "CAMPAIGN_GATE_AUXILIARY_INVALID"):
                    auxiliary._bundle(auxiliary, bundle)

    def test_loutcapt_consumed_source_removes_record_six_and_preserves_aliased_ammunition(self):
        # Inventory and slot aliases from cached scan-with-spells/world.json.
        # The CRE header is synthetic; the real inventory assembler is emulated.
        rows = (("RNDTRE04", 0, 0), ("HELM01", 0, 2), ("BOLT01", 20, 0),
                ("XBOW01", 0, 2), ("HALB01", 0, 2), ("PLAT04", 0, 2),
                ("DAGG11", 0, 0), ("POTN04", 0, 0), ("POTN52", 2, 0))
        original = bytes(make_cre([item_record(name, expiration=0, charges=(charges, 0, 0), flags=flags)
                                   for name, charges, flags in rows],
                                  slots={27: 0, 0: 1, 13: 2, 14: 2, 9: 3, 10: 4, 1: 5, 11: 6, 20: 7, 19: 8},
                                  gap=11))
        _, f, bundle, _ = fixture(family=39, original=original)
        auxiliary, group = f.gate.auxiliary_members[REVISION][1], f.groups[1]
        normalized = {"kind": "cre", "length": group.length, "expected": EXPECTED, "output": OUTPUT,
                      "item_offset": group.item_offset, "slot_offset": group.slot_offset,
                      "edits": [{"index": row.index, "buffer": BUFFER, "context": 40}
                                for row in group.edits.values()]}
        self.assertEqual([row["index"] for row in normalized["edits"]], [6])
        for phase, selected in ((3, 1), (7, 0)):
            machine = loaded_policy(phase, selected, selected_global="FLPR_QM2_39")
            mode = machine.read_q(POLICY_BUFFER + 32)
            f.pokestring(f, auxiliary.buffer, bytes(machine.uc.mem_read(POLICY_BUFFER, MEMORY_SIZE)))
            f["values"].FLPR_QM2_39 = selected
            self.assertTrue(f.gate.check(f.gate, bundle))
            pointer, changed = run(normalized, original, active=(0,) if mode == 1 else ())
            self.assertEqual(pointer, OUTPUT)
            self.assertEqual(changed, rewrite_inventory(original, {6: None}) if selected else original)

    def test_policy_requires_selection_and_keeps_consumed_primary_auxiliary_guarded(self):
        for phase, flag, mode in ((1, 1, 1), (6, 1, 1), (3, 1, 1), (7, 0, 0),
                                  (7, 1, 0), (1, 0, 0), (3, 0, 0), (1, None, 0)):
            with self.subTest(phase=phase, flag=flag):
                machine = loaded_policy(phase, flag)
                self.assertEqual(machine.read_q(POLICY_BUFFER + 32), mode)
        self.assertEqual(loaded_policy(3, 1, revision="b" * 64).read_q(POLICY_BUFFER + 32), 0)
        # Original physical source behavior still stops suppression at phase3.
        self.assertEqual(loaded_policy(3, 1, permanent=False).read_q(POLICY_BUFFER + 32), 2)

    def test_auxiliary_is_not_a_second_source_adapter_and_new_kept_selection_stays_off(self):
        for runtime in (Lua51, Lua53, LuaJIT):
            for kept in (False, True):
                with self.subTest(runtime=runtime.__module__, kept=kept):
                    lua, f, bundle, _ = fixture(runtime)
                    self.assertEqual(len(bundle.manifest.sources), 1)
                    self.assertEqual(len(f.gate.members), 1)
                    auxiliary = f.gate.auxiliary_members[REVISION][1]
                    self.assertEqual(auxiliary.index, 1)
                    self.assertEqual(len(auxiliary.policy.fields), 20)
                    # Launch hooks every location; the source's code runs at GAM
                    # entry, the capture and the CRE template load.
                    self.assertEqual(len(f.hooks), 11)
                    self.assertEqual({hook[1] - 0x140000000 for index, hook in f.hooks.items()
                                      if not f.idle(f, index)}, {0x292A84, 0x292F45, 0x341220})
                    lua.execute("""return function(f,bundle,kept)
                        local aux=f.gate.auxiliary_members[bundle.manifest.revision][1]
                        f.values.FLPR_FRESH=1;f.values.FLPR_QM2_12=0
                        for _,field in ipairs(aux.policy.fields) do
                            if field.name~='FLPR_QM2_12' then
                                f.values[field.name]=field.phase and (kept and 7 or 1) or field.value
                            end
                        end
                        aux:_write(40,1);aux:_write(72,1)
                        local controller={manifest=bundle.manifest,session={}}
                        function controller:source_is_kept(index) assert(index==1);return kept end
                        assert(f.gate:prepare_new(bundle,controller))
                        assert(aux:_q(32)==(kept and 0 or 1))
                        local adapter=f.gate:adapter(bundle,{})
                        assert(adapter:check())
                        assert(adapter:observe_guard({index=1})=='primary-only')
                    end""")(f, bundle, kept)
                    self.assertEqual(f.adapter_count, 1)
                    f.gate.reset(f.gate)
                    self.assertEqual(auxiliary._q(auxiliary, 32), 0)

    def test_saved_consumed_source_removes_only_duplicate_and_checks_auxiliary_proof(self):
        lua, f, bundle, original = fixture()
        auxiliary = f.gate.auxiliary_members[REVISION][1]
        group = f.groups[1]
        normalized = {"kind": "cre", "length": group.length, "expected": EXPECTED, "output": OUTPUT,
                      "item_offset": group.item_offset, "slot_offset": group.slot_offset,
                      "edits": [{"index": row.index, "buffer": BUFFER, "context": 40}
                                for row in group.edits.values()]}
        self.assertEqual([row["index"] for row in normalized["edits"]], [0])
        for phase, flag in ((1, 1), (3, 1), (7, 0), (3, 0)):
            machine = loaded_policy(phase, flag)
            mode = machine.read_q(POLICY_BUFFER + 32)
            f.pokestring(f, auxiliary.buffer, bytes(machine.uc.mem_read(POLICY_BUFFER, MEMORY_SIZE)))
            f["values"].FLPR_QM2_12 = flag
            self.assertTrue(f.gate.check(f.gate, bundle))
            pointer, changed = run(normalized, original, active=(0,) if mode == 1 else ())
            self.assertEqual(pointer, OUTPUT)
            self.assertEqual(changed, rewrite_inventory(original, {0: None}) if mode == 1 else original)
        f["values"].FLPR_QM2_12 = 1
        with self.assertRaisesRegex(Exception, "CAMPAIGN_GATE_INACTIVE"):
            f.gate.check(f.gate, bundle)


if __name__ == "__main__":
    unittest.main()
