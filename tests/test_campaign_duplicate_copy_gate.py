"""Item Randomiser's second copies at runtime (FLPRGat.lua, FLPRCampaignPolicy.lua).

The configurator's own export of a synthetic EET game (test_duplicate_copies)
is loaded as M_FLPR.lua loads a revision. The policy, the inventory rewrites and
the area item hook run emulated; EEex, the engine and its timing are mocked.
"""
import json
from pathlib import Path
import re
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "tests")]
from keystone import Ks, KS_ARCH_X86, KS_MODE_64  # noqa: E402
from lupa.lua51 import LuaRuntime as Lua51  # noqa: E402
from lupa.lua53 import LuaRuntime as Lua53  # noqa: E402
from lupa.luajit21 import LuaRuntime as LuaJIT  # noqa: E402

from flir_config.creature_inventory import rewrite_inventory as rewrite_cre  # noqa: E402
from flir_config.duplicates import CAMPAIGNS, accepted, table  # noqa: E402
from flir_config.original_configuration import fit_original_configuration  # noqa: E402
from flir_config.store_inventory import rewrite_inventory as rewrite_sto  # noqa: E402
from tests import test_campaign_area_provenance as provenance  # noqa: E402
from tests.engine_sites import gate_targets, load  # noqa: E402
from tests.test_campaign_gate_bridge import BUILD, CASES, CORE, GATE, POLICY  # noqa: E402
from tests.test_campaign_gate_policy import BIND, POLICY_TABLE, expected_fields  # noqa: E402
from tests.test_campaign_inventory_assembly import BUFFER, EXPECTED, OUTPUT, run  # noqa: E402
from tests.test_source_gate_assembly import AREA, BUFFER as GATE_BUFFER, CODES, IMAGE, GateMachine  # noqa: E402
from test_duplicate_copies import DuplicateGame  # noqa: E402
from test_original_configuration import source_row  # noqa: E402

INVENTORY = (ROOT / "tools/probes/FLPRInventoryAsm.lua").read_text(encoding="utf-8")
MEMORY_SIZE = 128 + 2049 * 128 + 32 + 64 * 64
# A revision as M_FLPR.lua loads it: its manifest, its native fixture, and the
# pristine bytes its sources and second copies are compared against.
LOAD = """
return function(folder,revision)
    local function read(path)
        local file=assert(io.open(path,'rb')); local bytes=file:read('*a'); assert(file:close()); return bytes
    end
    local base=folder..'/campaign-store/revisions/'..revision
    local manifest=assert(loadfile(base..'.lua'))()
    local fixture=assert(loadfile(base..'.native.lua'))()
    local pristine={templates={[revision]={}},duplicates={[revision]={}}}
    local function area(name)
        name=name:upper()
        pristine[name]=pristine[name] or {bytes=read(folder..'/'..name..'.pristine.are'),
            resource_sha256=fixture.resource_hashes[name..'.ARE']}
    end
    for index,source in ipairs(manifest.sources) do
        local endpoint=fixture.sources[source.id]
        if endpoint and endpoint.kind=='guarded-template' then
            pristine.templates[revision][source.id]=read(base..'.source-'..index..'.original.cre')
        elseif endpoint and endpoint.kind=='guarded-container' then area(endpoint.area) end
    end
    for number,copy in ipairs(fixture.duplicates or {}) do
        local kind,name=copy.resource:match('^(%a+):(.+)$')
        if kind=='are' then area(name)
        else pristine.duplicates[revision][number]=read(base..'.duplicate-'..number..'.original.'..kind) end
    end
    return {manifest=manifest,fixture=fixture},pristine
end
"""
INSTALL = """
return function(f,Gate,build,compile,Inventory,bundle,pristine)
    f.bundle,f.manifest,f.groups=bundle,bundle.manifest,{}
    f.gate=Gate.install(f.api,bundle,build,compile,pristine,nil,function(group)
        f.groups[#f.groups+1]=group;return Inventory(group)
    end)
    return f.gate
end
"""
# New Game: the controller's GLOBALs (phases by source id), then the gate.
NEW_GAME = """
return function(f,bundle,phases)
    local gate=f.gate
    f.values.FLPR_FRESH=1
    local members={}
    for _,member in ipairs(gate.members) do members[#members+1]=member end
    for _,member in ipairs(gate.copies) do members[#members+1]=member end
    for _,member in ipairs(members) do
        for _,field in ipairs(member.policy.fields) do
            if not field.phase then f.values[field.name]=field.value end
        end
        member:_write(40,1);member:_write(72,1) -- the New Game template's GAM
    end
    for index,source in ipairs(bundle.manifest.sources) do
        f.values[string.format('FLPR_S%04d',index)]=phases[source.id] or 1
    end
    local controller={manifest=bundle.manifest,session={}}
    function controller:source_is_kept(index) return phases[bundle.manifest.sources[index].id]==7 end
    return gate:prepare_new(bundle,controller)
end
"""
# A second copy's area member as the emulator runs it: its policy for its
# source, with the provenance and record prepare_copy found in the pristine area.
EMULATED_AREA = """
return function(compile,build,targets,member,expected,buffer,table_address)
    local policy=compile(member.revision,member.index,true,nil,true)
    policy.scan=policy.scan:gsub('@POLICY_TABLE@',string.format('%.0f',table_address))
    local p=member.policy
    policy.provenance={expected=expected,length=p.provenance.length,
        container_offset=p.provenance.container_offset,item_offset=p.provenance.item_offset}
    policy.source=p.source
    local assembly=build(buffer,targets,policy)
    return assembly.enter,assembly.capture,assembly.item,assembly.area_enter,assembly.area_exit,
        (policy.table:gsub('.',function(c) return string.format('%02x',c:byte()) end))
end
"""


def assemble(source: str, address: int) -> bytes:
    source = re.sub(r"\b(?:ds|ss):", "", source).replace("#MANUAL_HOOK_EXIT(1)", "nop")
    return bytes(Ks(KS_ARCH_X86, KS_MODE_64).asm(source, address)[0])


def loaded_policy(revision: str, index: int, fields, *, copy=True):
    """The copy policy of source <index> (without copy: the source's own) after
    a GAM with <fields> loaded."""
    lua = LuaJIT(unpack_returned_tuples=True)
    policy = lua.execute(POLICY)(revision, index, True, None, True) if copy else lua.execute(POLICY)(revision, index)
    table_bytes = bytes.fromhex(lua.eval(BIND)(policy, POLICY_TABLE))
    assembly = lua.execute(BUILD)(GATE_BUFFER, gate_targets(lua, IMAGE), policy)
    machine = GateMachine()
    machine.uc.mem_write(POLICY_TABLE, table_bytes)
    for name in ("enter", "capture"):
        binary = assemble(assembly[name], CODES[name])
        machine.uc.mem_write(CODES[name], binary + b"\x90")
        machine.sentinels[name] = CODES[name] + len(binary)
    size = 0xB4 + len(fields) * 84
    machine.gam(fields, declared=size)
    machine.run("enter", length=size)
    machine.run("capture")
    return machine


class CopyRowsTests(unittest.TestCase):
    def test_runtime_rows_are_the_shipped_tables_under_their_reviewed_names(self):
        lua = LuaJIT(unpack_returned_tuples=True)
        rows = load(lua, GATE).duplicate_rows()
        expected = {f"{campaign}:duplicate:{line}": accepted(campaign, entry)
                    for campaign in CAMPAIGNS for line, entry in table(campaign).items()}
        self.assertEqual({identity: {key: frozenset(names.values()) for key, names in row.items()}
                          for identity, row in rows.items()}, expected)

    def test_copy_policy_protects_in_every_state_but_kept(self):
        revision = "".join(f"{0x2000 + index:04x}" for index in range(16))
        for phase, mode in ((1, 1), (2, 1), (3, 1), (4, 1), (5, 1), (6, 1),
                            (7, 0), (0, 0), (8, 0), (-1, 0)):
            with self.subTest(phase=phase):
                machine = loaded_policy(revision, 3, expected_fields(revision, 3, phase))
                self.assertEqual(machine.read_q(GATE_BUFFER + 32), mode)
        # Another revision's GAM, a missing or a repeated phase: nothing is removed.
        other = "f" * 64
        for fields in (expected_fields(other, 3, 3), expected_fields(revision, 3, 3)[:-1],
                       expected_fields(revision, 3, 3) + [("FLPR_S0003", 3)]):
            with self.subTest(fields=fields[-1]):
                self.assertEqual(loaded_policy(revision, 3, fields).read_q(GATE_BUFFER + 32), 0)
        # A source's own policy still stops at confirmed (3): only copies differ.
        self.assertEqual(loaded_policy(revision, 3, expected_fields(revision, 3, 3), copy=False)
                         .read_q(GATE_BUFFER + 32), 2)
        lua = LuaJIT(unpack_returned_tuples=True)
        compile_policy = lua.execute(POLICY)
        for arguments in ((1, None, None, True), (1, True, "FLPR_QM2_12", True), (1, True, None, False)):
            with self.subTest(arguments=arguments), \
                    self.assertRaisesRegex(Exception, "CAMPAIGN_GATE_COPY_POLICY_INVALID"):
                compile_policy(revision, *arguments)


class ExportedCopyGateTests(DuplicateGame, unittest.TestCase):
    """The gate for the export of test_duplicate_copies' game, with a source in
    the creature that holds a copy."""

    def export(self, draft=None):
        if draft is None:
            draft = self.draft(source_row("bg2-synth001", item="synth001", ident="x",
                                          source_ids=[self.record("cre:sahpr2", "synth001")]))
        self.patched(draft)
        generated = fit_original_configuration(self.catalog, self.world, "eet-original")
        self.native, self.output, self.revision = self.exported(generated)
        self.index = {row["id"]: number for number, row in enumerate(self.manifest()["sources"], 1)}
        return self.native

    def manifest(self):
        return json.loads((self.output / "campaign-store" / "revisions" / f"{self.revision}.json")
                          .read_text(encoding="utf-8"))

    def gate(self, runtime=LuaJIT, mutate=None):
        lua = runtime(unpack_returned_tuples=True)
        core, build, policy, inventory = [lua.execute(text) for text in (CORE, BUILD, POLICY, INVENTORY)]
        gate = load(lua, GATE)
        f = lua.execute(CASES.replace("return cases", "return fixture"))(core)
        bundle, pristine = lua.execute(LOAD)(self.output.as_posix(), self.revision)
        if mutate:
            mutate(lua, bundle, pristine)
        lua.execute(INSTALL)(f, gate, build, policy, inventory, bundle, pristine)
        return lua, f, bundle

    @staticmethod
    def copies(f) -> dict:
        return {(member.binding.id, member.binding.record): member for member in f.gate.copies.values()}

    @staticmethod
    def emulated(group, members: dict) -> tuple[dict, dict]:
        """The inventory group with each member's buffer at an emulator address,
        and each member's edit position by its name in <members>."""
        buffers, positions, edits = {}, {}, []
        for position in range(1, len(group.edits) + 1):
            row = group.edits[position]
            buffer = buffers.setdefault(row.buffer, BUFFER + 0x100 * len(buffers))
            edits.append({"index": row.index, "buffer": buffer, "context": 40, "replacement": row.replacement})
            for name, member in members.items():
                if member.buffer == row.buffer:
                    positions[name] = position - 1
        normalized = {"kind": group.kind, "length": group.length, "expected": EXPECTED, "output": OUTPUT,
                      "item_offset": group.item_offset, "edits": edits}
        if group.kind == "cre":
            normalized["slot_offset"] = group.slot_offset
        else:
            normalized["ref"] = group.ref
        return normalized, positions

    def group(self, f, kind):
        found = [group for group in f.groups.values() if group.kind == kind]
        self.assertEqual(len(found), 1)
        return found[0]

    def test_each_holder_loses_its_copies_while_their_source_moves(self):
        self.export()
        sahpr2 = (self.override / "sahpr2.cre").read_bytes()
        bernard = (self.override / "bernard.sto").read_bytes()
        source = self.record("cre:sahpr2", "synth001")
        for runtime in (Lua51, Lua53, LuaJIT):
            with self.subTest(runtime=runtime.__module__):
                _, f, _ = self.gate(runtime)
                copies = self.copies(f)
                self.assertEqual(sorted(copies), [("bg1:duplicate:4", 0), ("bg2:duplicate:12", 1),
                                                  ("bg2:duplicate:25", 0), ("bg2:duplicate:27", 0),
                                                  ("bg2:duplicate:3", 0)])
                for (identity, _), member in copies.items():
                    # Each copy follows its own source's phase, and is no source of its own.
                    index = self.index[member.binding.source_id]
                    self.assertEqual((member.index, member.copy.phase), (index, f"FLPR_S{index:04}"))
                    self.assertEqual(len(member.policy.fields), 19)
                self.assertEqual(len(f.gate.members), 5)
                template = next(member for member in f.gate.members.values() if member.binding.id == source)
                # The creature: the source leaves, the copy becomes the table's replacement.
                group, positions = self.emulated(self.group(f, "cre"), {
                    "copy": copies["bg2:duplicate:25", 0], "source": template})
                self.assertEqual([row["index"] for row in group["edits"]], [1, 0])
                self.assertEqual(group["edits"][1]["replacement"], "sper02")
                for active, changes in ((("copy", "source"), {0: "sper02", 1: None}),
                                        (("copy",), {0: "sper02"}), (("source",), {1: None}), ((), {})):
                    pointer, changed = run(group, sahpr2, active=[positions[name] for name in active])
                    self.assertEqual(pointer, OUTPUT)
                    self.assertEqual(changed, rewrite_cre(sahpr2, changes) if changes else sahpr2)
                # The shop: both copies leave, but not from stock a saved game restored.
                group, positions = self.emulated(self.group(f, "sto"), {
                    "xbow06": copies["bg2:duplicate:3", 0], "sw1h53": copies["bg2:duplicate:12", 1]})
                self.assertEqual(group["ref"], "bernard")
                both = [positions["xbow06"], positions["sw1h53"]]
                self.assertEqual(run(group, bernard, active=both, ref="BERNARD")[1], rewrite_sto(bernard, {0, 1}))
                self.assertEqual(run(group, bernard, active=both, ref="BERNARD",
                                     contexts={positions["xbow06"]: 1})[1], rewrite_sto(bernard, {1}))
                self.assertEqual(run(group, bernard, active=[positions["sw1h53"]], ref="BERNARD")[1],
                                 rewrite_sto(bernard, {1}))
                self.assertEqual(run(group, bernard, ref="BERNARD")[1], bernard)

    def test_area_copy_leaves_once_per_load_of_the_pristine_area(self):
        self.export()
        blob = (self.override / "rr#010.are").read_bytes()
        containers, items = struct.unpack_from("<I", blob, 0x70)[0], struct.unpack_from("<I", blob, 0x78)[0]
        lua, f, _ = self.gate()
        member = self.copies(f)["bg2:duplicate:27", 0]
        self.assertEqual((member.binding.container, member.policy.source.index), (0, 0))
        self.assertEqual((member.policy.provenance.container_offset, member.policy.provenance.item_offset,
                          member.policy.provenance.length), (containers, items, len(blob)))
        self.assertEqual(member.policy.source.area, "rr#010")
        compile_policy, build = lua.execute(POLICY), lua.execute(BUILD)
        code = lua.execute(EMULATED_AREA)(compile_policy, build, gate_targets(lua, IMAGE), member,
                                          provenance.EXPECTED, GATE_BUFFER, POLICY_TABLE)
        index = self.index["virtual:bg2-ring26"]

        def machine(phase):
            emulated = provenance.ProvenanceMachine(blob=blob)
            emulated.write(AREA + 0x204, b"RR#010\0\0")
            for name, source in zip(("enter", "capture", "item", "area_enter", "area_exit"), code[:5]):
                binary = assemble(source, provenance.HOOKS[name])
                emulated.write(provenance.HOOKS[name], binary + b"\x90")
                emulated.sentinels[name] = provenance.HOOKS[name] + len(binary)
            emulated.write(POLICY_TABLE, bytes.fromhex(code[5]))
            fields = expected_fields(self.revision, index, phase)
            size = emulated.gam(fields, declared=0xB4 + 84 * len(fields))
            emulated.run("enter", length=size)
            emulated.run("capture")
            return emulated

        def item(emulated, record=0):
            return emulated.item_hook(container=provenance.RAW + containers, table=provenance.RAW + items,
                                      index=record)

        # The virtual source is confirmed (3) from the start: its copy still leaves.
        emulated = machine(3)
        self.assertEqual(emulated.read_q(GATE_BUFFER + 32), 1)
        emulated.hook("area_enter", length=len(blob))
        self.assertEqual(item(emulated), "skip")
        self.assertEqual(item(emulated), "continue")
        emulated.hook("area_exit")
        # A kept source keeps its copy; an area the saved game changed is left alone.
        kept = machine(7)
        self.assertEqual(kept.read_q(GATE_BUFFER + 32), 0)
        kept.hook("area_enter", length=len(blob))
        self.assertEqual(item(kept), "continue")
        saved = machine(3)
        saved.write(provenance.RAW + len(blob) - 1, b"\x7f")
        saved.hook("area_enter", length=len(blob))
        self.assertEqual(item(saved), "continue")

    def test_new_game_enrols_the_copies_of_moved_sources_only(self):
        self.export()
        xbow06 = self.record("are:area", "xbow06")
        for runtime in (Lua51, Lua53, LuaJIT):
            with self.subTest(runtime=runtime.__module__):
                lua, f, bundle = self.gate(runtime)
                copies = self.copies(f)
                # Nothing is logged for a copy at New Game: an area copy's log may be full.
                area = copies["bg2:duplicate:27", 0]
                area._write(area, 8, 5000)
                area._write(area, 16, 1)
                phases = lua.table_from({xbow06: 7, "virtual:bg2-ring26": 3})
                self.assertTrue(lua.execute(NEW_GAME)(f, bundle, phases))
                modes = {key[0]: member._q(member, 32) for key, member in copies.items()}
                self.assertEqual(modes, {"bg1:duplicate:4": 1, "bg2:duplicate:12": 1, "bg2:duplicate:25": 1,
                                         "bg2:duplicate:27": 1, "bg2:duplicate:3": 0})
                self.assertEqual(area._q(area, 8), 5000)
                # Each step proves a bounded share of the copies, one GLOBAL each.
                reads = lua.execute("""return function(f,bundle,steps)
                    local api,count=f.api,0
                    local read=api.EEex_GameState_GetGlobalInt
                    api.EEex_GameState_GetGlobalInt=function(name) count=count+1; return read(name) end
                    local seen={}
                    for _=1,steps do
                        assert(f.gate:check(bundle))
                        seen[#seen+1]=count; count=0
                    end
                    api.EEex_GameState_GetGlobalInt=read
                    return seen[1],seen[2],f.gate.copy_cursor
                end""")(f, bundle, 2)
                self.assertEqual(reads, (4, 4, 3))
                # A kept source's copy must stay; a moved source's copy must stay removed.
                kept = copies["bg2:duplicate:3", 0]
                kept._write(kept, 32, 1)
                with self.assertRaisesRegex(Exception, "CAMPAIGN_GATE_DUPLICATE_KEPT_ACTIVE"):
                    f.gate.check(f.gate, bundle, True)
                kept._write(kept, 32, 0)
                creature = copies["bg2:duplicate:25", 0]
                creature._write(creature, 32, 0)
                with self.assertRaisesRegex(Exception, "CAMPAIGN_GATE_INACTIVE"):
                    f.gate.check(f.gate, bundle, True)
                f.gate.reset(f.gate)
                self.assertEqual({member._q(member, 32) for member in copies.values()}, {0})
                self.assertIsNone(creature.enrolled)
                self.assertEqual(sorted(f.gate.inspect(f.gate).duplicates.keys()), sorted(
                    f"{self.revision}/{identity}/{record}" for identity, record in copies))

    def test_loaded_game_proves_a_copy_by_its_policy(self):
        self.export()
        lua, f, bundle = self.gate()
        copies = self.copies(f)
        member = copies["bg2:duplicate:25", 0]
        index = member.index
        for key, other in copies.items():
            if other.index != index:
                f["values"][other.copy.phase] = 7
        for phase, mode in ((1, 1), (3, 1), (6, 1), (7, 0)):
            with self.subTest(phase=phase):
                machine = loaded_policy(self.revision, index, expected_fields(self.revision, index, phase))
                self.assertEqual(machine.read_q(GATE_BUFFER + 32), mode)
                f.pokestring(f, member.buffer, bytes(machine.uc.mem_read(GATE_BUFFER, MEMORY_SIZE)))
                f["values"][member.copy.phase] = phase
                self.assertTrue(f.gate.check(f.gate, bundle, True))
        # The GLOBALs say the source moves, but the loaded policy did not protect.
        f["values"][member.copy.phase] = 3
        with self.assertRaisesRegex(Exception, "CAMPAIGN_GATE_INACTIVE"):
            f.gate.check(f.gate, bundle, True)

    def test_gate_refuses_copies_it_cannot_bind_exactly(self):
        self.export()
        numbers = {row["id"]: number for number, row in enumerate(self.native["duplicates"], 1)}
        manifest_numbers = {row["id"]: number for number, row in enumerate(self.manifest()["duplicates"], 1)}

        def entry(identity):
            def change(action):
                def mutate(lua, bundle, pristine):
                    action(lua, bundle.fixture.duplicates[numbers[identity]],
                           bundle.manifest.duplicates[manifest_numbers[identity]], bundle, pristine)
                return mutate
            return change

        def assign(lua, target, key, value):
            target[key] = lua.table_from(value) if isinstance(value, (list, dict)) else value

        cases = {
            # Not a shipped row, or not this row's holder, item or source.
            "unknown row": (entry("bg2:duplicate:3")(lambda lua, e, m, b, p: (
                assign(lua, e, "id", "bg2:duplicate:2"), assign(lua, m, "id", "bg2:duplicate:2"))),
                "CAMPAIGN_GATE_DUPLICATE_INVALID"),
            "other holder": (entry("bg2:duplicate:3")(lambda lua, e, m, b, p: (
                assign(lua, e, "resource", "sto:bernard2"), assign(lua, m, "resource", "sto:bernard2"))),
                "CAMPAIGN_GATE_DUPLICATE_INVALID"),
            "other item": (entry("bg2:duplicate:3")(lambda lua, e, m, b, p: assign(lua, e.payload, "resref", "xbow07")),
                           "CAMPAIGN_GATE_DUPLICATE_INVALID"),
            "no campaign row": (entry("bg2:duplicate:3")(lambda lua, e, m, b, p: assign(lua, m, "source_id", "x")),
                                "CAMPAIGN_GATE_DUPLICATE_INVALID"),
            "changed holder": (entry("bg2:duplicate:3")(lambda lua, e, m, b, p: assign(lua, e, "resource_sha256",
                                                                                         "0" * 64)),
                               "CAMPAIGN_GATE_DUPLICATE_INVALID"),
            "shop without stock": (entry("bg2:duplicate:3")(lambda lua, e, m, b, p: assign(lua, e, "stock", None)),
                                   "CAMPAIGN_GATE_DUPLICATE_INVALID"),
            "no records": (entry("bg2:duplicate:3")(lambda lua, e, m, b, p: assign(lua, e, "record_indices", [])),
                           "CAMPAIGN_GATE_DUPLICATE_INVALID"),
            # The replacement is the row's, and only a creature's.
            "replacement dropped": (entry("bg2:duplicate:25")(lambda lua, e, m, b, p: (
                assign(lua, e, "replacement", None), assign(lua, m, "replacement", None))),
                "CAMPAIGN_GATE_DUPLICATE_REPLACEMENT_INVALID"),
            "other replacement": (entry("bg2:duplicate:25")(lambda lua, e, m, b, p: assign(lua, e, "replacement",
                                                                                            "sper01")),
                                  "CAMPAIGN_GATE_DUPLICATE_REPLACEMENT_INVALID"),
            "replacement of a shop": (entry("bg2:duplicate:3")(lambda lua, e, m, b, p: (
                assign(lua, e, "replacement", "sper02"),
                assign(lua, m, "replacement", {"resref": "sper02", "resource_sha256": "0" * 64}))),
                "CAMPAIGN_GATE_DUPLICATE_REPLACEMENT_INVALID"),
            # Records the holder does not hold as the export says.
            "repeated record": (entry("bg2:duplicate:25")(lambda lua, e, m, b, p: assign(lua, e, "record_indices",
                                                                                          [0, 0])),
                                "CAMPAIGN_GATE_DUPLICATE_RECORD_INVALID"),
            "other charges": (entry("bg2:duplicate:25")(lambda lua, e, m, b, p: assign(lua, e.payload.charges, 1, 1)),
                              "CAMPAIGN_GATE_DUPLICATE_BINDING_MISMATCH"),
            "other quantity": (entry("bg2:duplicate:12")(lambda lua, e, m, b, p: assign(lua, e.stock, "quantity", 1)),
                               "CAMPAIGN_GATE_DUPLICATE_BINDING_MISMATCH"),
            "other record": (entry("bg2:duplicate:12")(lambda lua, e, m, b, p: assign(lua, e, "record_indices", [0])),
                             "CAMPAIGN_GATE_DUPLICATE_BINDING_MISMATCH"),
            "area flags": (entry("bg2:duplicate:27")(lambda lua, e, m, b, p: assign(lua, e.payload, "flags", 0)),
                           "CAMPAIGN_GATE_DUPLICATE_BINDING_MISMATCH"),
            "outside a container": (entry("bg2:duplicate:27")(lambda lua, e, m, b, p: assign(
                lua, e, "record_indices", [1])), "CAMPAIGN_GATE_DUPLICATE_CONTAINER_MISSING"),
            "holder bytes missing": (entry("bg2:duplicate:25")(lambda lua, e, m, b, p: assign(
                lua, p.duplicates[self.revision], numbers["bg2:duplicate:25"], None)),
                "CAMPAIGN_GATE_DUPLICATE_BYTES_MISSING"),
        }
        self.gate()
        for name, (mutate, error) in cases.items():
            with self.subTest(case=name), self.assertRaisesRegex(Exception, error):
                self.gate(mutate=mutate)

    def test_a_campaign_whose_only_guard_is_a_copy(self):
        ring = source_row("bg2-ring26", adapter="virtual", item="ring26", ident="i89", resource="itm:ring26")
        draft = self.draft()
        draft["items"] = [{**ring, "campaign": "bg2"}]
        native = self.export(draft)
        self.assertEqual({row["kind"] for row in native["sources"].values()}, {"virtual"})
        self.assertEqual(native["source_gate"]["source_ids"], [])
        self.assertEqual([row["id"] for row in native["duplicates"]], ["bg2:duplicate:27"])
        lua, f, bundle = self.gate()
        self.assertEqual((len(f.gate.members), len(f.gate.copies)), (0, 1))
        self.assertTrue(lua.execute(NEW_GAME)(f, bundle, lua.table_from({"virtual:bg2-ring26": 3})))
        self.assertEqual(f.gate.copies[1]._q(f.gate.copies[1], 32), 1)


if __name__ == "__main__":
    unittest.main()
