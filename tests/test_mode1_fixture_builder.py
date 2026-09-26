"""Binary fixture construction only; these checks do not prove engine behavior."""

import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT21

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_mode1_fixture import (VARIANTS, add_area_fixture, build, item_payload,
                                make_source_cre, make_target_cre, mark_initial_gam)
from flir_config.campaigns import read_campaign
from flir_config.world_formats import parse_are, parse_cre
from test_postinstall_world_formats import item_record, make_are, make_cre

ADAPTER = (ROOT / "prototype" / "FLPRNat.lua").read_text(encoding="utf-8")
CORE = (ROOT / "prototype" / "FLPRCore.lua").read_text(encoding="utf-8")
HOST = (ROOT / "prototype" / "FLPRHost.lua").read_text(encoding="utf-8")
# Fake engine per docs/native-creature-transport-evidence.md: DestroyItem removes
# synchronously; GiveItemCreate queues CMessageAddItem (67/2), and a later handler
# update heap-copies it into the first empty backpack slot (internal 18-33).
CREATURE_RUN = r'''
return function(Core, Host, Native, manifest, fixture)
    local f = {calls={}, queue={}, address=900, values={FLPR_FRESH=1}, tick=0, reports={}}
    local function str(value) return {get=function() return value end} end
    local function item(ref)
        f.address = f.address + 1
        return {cResRef=str(ref), m_useCount1=0, m_useCount2=0, m_useCount3=0, m_wear=0, m_flags=1, address=f.address}
    end
    local function sprite(name, slots)
        f.address = f.address + 1
        slots[10] = item("FIST")
        return {address=f.address, sprite=true, alive=true, portrait=-1, m_scriptName=str(name),
            m_typeAI={m_EnemyAlly=128}, slots=slots,
            m_equipment={m_items={get=function(_, slot) return slots[slot] end}}}
    end
    local unrelated = item("FLPRX1")
    f.objects = {sprite("FLPRS1", {[18]=item("FLPRI1")}), sprite("FLPRS2", {[18]=item("FLPRI2")}),
        sprite("FLPRS3", {[18]=item("FLPRI3")}), sprite("FLPRC1", {}), sprite("FLPRC2", {[18]=unrelated})}
    local ids = {}
    for id, object in ipairs(f.objects) do ids[object] = id end
    local area = {m_resref=str("BG2600"), m_lVertSort={1, 2, 3, 4, 5}, m_lVertSortBack={},
        m_lVertSortFlight={}, m_lVertSortUnder={}}
    local api = {EngineGlobals={g_pBaldurChitin={m_cMessageHandler={m_messageList=f.queue}}}}
    api.EEex_CastUD = function(object) return object end
    api.EEex_Area_GetVisible = function() return area end
    api.EEex_GameObject_Get = function(id) return f.objects[id] end
    api.EEex_GameObject_CastUserType = function(object) return object end
    api.EEex_GameObject_IsSprite = function(o, dead) return o.sprite and (dead or o.alive) or false end
    api.EEex_Sprite_GetPortraitIndex = function(o) return o.portrait end
    api.EEex_Utility_IterateCPtrList = function(list, fn) for _, entry in ipairs(list) do fn(entry) end end
    api.EEex_UDToPtr = function(o) return o.address end
    api.EEex_Resource_Demand = function(_, ext) assert(ext == "ITM"); return {maxStackable=1, itemFlags=12} end
    api.EEex_Action_ExecuteResponseStringOnAIBaseInstantly = function(action, o)
        f.calls[#f.calls + 1] = action
        local ref = action:match('^DestroyItem%("([^"]+)"%)$')
        if ref then
            for slot, value in pairs(o.slots) do
                if value.cResRef:get():lower() == ref then o.slots[slot] = nil end
            end
            return
        end
        ref = assert(action:match('^GiveItemCreate%("([^"]+)",Myself,0,0,0%)$'), action)
        f.address = f.address + 1
        f.queue[#f.queue + 1] = {address=f.address, m_targetId=ids[o], m_sourceId=ids[o], m_item=item(ref:upper()),
            virtual_GetMsgType=function() return 67 end, virtual_GetMsgSubType=function() return 2 end,
            virtual_GetCommType=function() return 1 end}
    end
    local function update()
        while #f.queue > 0 do
            local message = table.remove(f.queue, 1)
            local target = f.objects[message.m_targetId]
            for slot = 18, 33 do
                if not target.slots[slot] then target.slots[slot] = item(message.m_item.cResRef:get()); break end
            end
        end
    end
    local bundle = {manifest=manifest, fixture=fixture}
    local host = Host.new({core=Core,
        storage={get=function(_, name) return f.values[name] or 0 end, set=function(_, name, value) f.values[name] = value end},
        resolve=function(revision) if revision == manifest.revision then return bundle end end,
        next=function() return bundle end, seed=function() return 424242 end,
        native=function(value) return Native.new(api, value) end,
        world=function() return true end, tick=function() return f.tick end,
        report=function(status, reason) f.reports[#f.reports + 1] = tostring(status) .. ":" .. tostring(reason) end})
    assert(host:capture_new("confirmed-bg1-character-accept"))
    for _ = 1, 200 do
        f.tick = f.tick + 1; host:poll(); update()
        if host.status == "complete" or host.status == "disabled" then break end
    end
    assert(host.status == "complete", tostring(host.status) .. ":" .. tostring(host.error))
    assert(#f.reports == 1 and f.reports[1] == "complete:nil", f.reports[1])
    for _, assignment in ipairs(host:inspect().assignments) do assert(assignment.delivery_phase == Core.PHASE.held) end
    local actions = {}
    for _, action in ipairs(f.calls) do local verb = action:match("^(%a+)"); actions[verb] = (actions[verb] or 0) + 1 end
    assert(actions.DestroyItem == 3 and actions.GiveItemCreate == 3 and #f.calls == 6)
    local held = {}
    for index = 1, 5 do
        for slot, value in pairs(f.objects[index].slots) do
            local ref = value.cResRef:get():lower()
            if ref:match("^flpri") then
                assert(index >= 4, "source kept its original")
                held[#held + 1] = f.objects[index].m_scriptName:get() .. "@" .. slot
            end
        end
    end
    table.sort(held)
    -- Two rewards fill FLPRC1's first backpack slots; FLPRC2 keeps FLPRX1 in 18.
    assert(table.concat(held, ",") == "FLPRC1@18,FLPRC1@19,FLPRC2@19", table.concat(held, ","))
    assert(f.objects[5].slots[18] == unrelated and unrelated.cResRef:get() == "FLPRX1")
    return true
end
'''


def make_gam():
    data = bytearray(0xB4)
    data[:8] = b"GAMEV2.0"
    struct.pack_into("<II", data, 0x38, len(data), 1)
    record = bytearray(84)
    struct.pack_into("<32sH", record, 0, b"EXISTING", 1)
    struct.pack_into("<i", record, 0x28, 47)
    return bytes(data + record + b"unrelated trailing section")


def make_itm():
    data = bytearray(0x72 + 0x38)
    data[:8] = b"ITM V1  "
    struct.pack_into("<I", data, 0x18, 0x2C)
    struct.pack_into("<H", data, 0x38, 1)
    struct.pack_into("<IH", data, 0x64, 0x72, 1)
    return bytes(data)


def synthetic_game(root):
    override = root / "game" / "override"
    override.mkdir(parents=True)
    originals = {"BALDUR.GAM": make_gam(), "COW.CRE": bytes(make_cre()),
        "BG2600.ARE": bytes(make_are()), "BGSTARTA.2DA": b"2DA V1.0\n*\n VALUE\nSTART_AREA BG2600\n"}
    originals.update({name + ".ITM": make_itm() for name in ("SW1H01", "AX1H01", "BLUN04")})
    for name, data in originals.items():
        (override / name).write_bytes(data)
    return root / "game", originals


def adapter_targets(native_lua):
    """Construct the real bounded adapter from generated data-only Lua."""
    lua = Lua51(unpack_returned_tuples=True)
    native = lua.execute(ADAPTER)
    api = lua.eval("setmetatable({}, {__index=function() return function() end end})")
    adapter = native.new(api, lua.execute(native_lua.read_text(encoding="utf-8")))
    return {key: (row.kind, row.script_name, row.sprite, row.x, row.y)
            for key, row in adapter.targets.items()}


class Mode1FixtureTests(unittest.TestCase):
    def test_gam_marker_preserves_original_bytes_and_existing_variables(self):
        before = make_gam()
        after = mark_initial_gam(before)
        self.assertEqual(after[:0x38], before[:0x38])
        self.assertEqual(after[0x40:len(before)], before[0x40:])
        offset, count = struct.unpack_from("<II", after, 0x38)
        self.assertEqual((offset, count), (len(before), 2))
        self.assertEqual(after[offset:offset + 84], before[0xB4:0xB4 + 84])
        self.assertEqual(after[offset + 84:offset + 95], b"FLPR_FRESH\0")
        self.assertEqual(struct.unpack_from("<i", after, offset + 84 + 0x28)[0], 1)
        with self.assertRaisesRegex(ValueError, "already contains"):
            mark_initial_gam(after)

    def test_cre_source_has_one_unequipped_copy_no_scripts_or_effects(self):
        before = make_cre([item_record()], slots={2: 0})
        payload = item_payload("flpri1", make_itm())
        after = make_source_cre(before, "FLPRS1", payload)
        parsed = parse_cre(after)
        self.assertEqual(parsed["script_name"], "FLPRS1")
        self.assertEqual(len(parsed["items"]), 1)
        self.assertEqual(parsed["items"][0]["slots"], [21])
        self.assertEqual(parsed["items"][0]["flags"], 1)
        self.assertEqual(parsed["items"][0]["charges"], [0, 0, 0])
        self.assertEqual(after[0x248:0x270], bytes(40))
        self.assertEqual(after[0x2CC:0x2D4], bytes(8))

    def test_area_keeps_existing_tables_and_embedded_cre_offsets(self):
        before = bytes(make_are([item_record()], [("OldChest", 0, 1)],
            [("OldActor", make_cre([item_record()]))]))
        after = add_area_fixture(before)
        old, new = parse_are(before), parse_are(after)
        self.assertEqual(new["actors"][0]["embedded_cre_offset"], old["actors"][0]["embedded_cre_offset"])
        self.assertEqual(new["actors"][0]["embedded_cre"], old["actors"][0]["embedded_cre"])
        self.assertEqual(new["containers"][0]["items"], old["containers"][0]["items"])
        allowed = set(range(0x54, 0x5A)) | set(range(0x70, 0x76))
        self.assertTrue(all(a == b for index, (a, b) in enumerate(zip(before, after)) if index not in allowed))
        self.assertEqual([row["name"] for row in new["containers"][-3:]], ["FLPRT1", "FLPRT2", "FLPRT3"])
        self.assertTrue(all(row["type"] == 4 and row["item_count"] == 0 and row["flags"] == 0
                            for row in new["containers"][-3:]))
        with self.assertRaisesRegex(ValueError, "already contains"):
            add_area_fixture(after)

    def test_rejects_stacked_nondroppable_or_enchanted_item(self):
        for offset, fmt, value in ((0x38, "H", 20), (0x18, "I", 8), (0x60, "I", 1)):
            item = bytearray(make_itm())
            struct.pack_into("<" + fmt, item, offset, value)
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, "mundane"):
                item_payload("flpri1", item)

    def test_full_builder_writes_valid_store_and_does_not_modify_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game, originals = synthetic_game(root)
            override, output = game / "override", root / "payload"
            report = build(game, output)
            manifest = read_campaign(output / "campaign-store")
            self.assertEqual(len(manifest["sources"]), 3)
            self.assertEqual(len(manifest["targets"]), 3)
            self.assertEqual(manifest["revision"], report["revision"])
            native = json.loads((output / "fixture-native.json").read_text())
            self.assertTrue(all(row["record"]["record"]["flags"] == 1 for row in manifest["sources"]))
            self.assertTrue(all(row["payload"]["flags"] == 1 for row in native["sources"].values()))
            identities = [row["identity"] for key in ("sources", "targets") for row in native[key].values()]
            self.assertEqual(len(set(identities)), 6)
            self.assertEqual({path.name: path.read_bytes() for path in override.iterdir()}, originals)
            # The accepted variant gains no creature fields, files or kinds.
            self.assertEqual(report["variant"], "ground-piles")
            self.assertTrue({"variant", "ready_detail", "unrelated_items"}.isdisjoint(native))
            self.assertEqual(sorted(path.name for path in (output / "override").iterdir()),
                ["BALDUR.GAM", "BG2600.ARE", "FLPRI1.ITM", "FLPRI2.ITM", "FLPRI3.ITM",
                 "FLPRS1.CRE", "FLPRS2.CRE", "FLPRS3.CRE"])
            self.assertEqual(sorted(row[0] for row in adapter_targets(output / "fixture-native.lua").values()),
                ["ground-pile"] * 3)
            with self.assertRaisesRegex(ValueError, "never overwrite"):
                build(game, output)
            with self.assertRaisesRegex(ValueError, "outside"):
                build(game, game / "generated")

    def test_recipient_cre_is_empty_or_holds_only_its_unrelated_item(self):
        before = make_cre([item_record()], slots={2: 0})
        empty = parse_cre(make_target_cre(before, "FLPRC1"))
        self.assertEqual((empty["script_name"], empty["items"]), ("FLPRC1", []))
        holder = make_target_cre(before, "FLPRC2", item_payload("flprx1", make_itm()))
        parsed = parse_cre(holder)
        self.assertEqual(parsed["script_name"], "FLPRC2")
        self.assertEqual([(row["resref"], row["slots"], row["flags"]) for row in parsed["items"]],
                         [("flprx1", [21], 1)])
        self.assertEqual((holder[0x248:0x270], holder[0x2CC:0x2D4], holder[0x270]), (bytes(40), bytes(8), 128))

    def test_creature_area_adds_killable_recipients_and_keeps_containers(self):
        before = bytes(make_are([item_record()], [("OldChest", 0, 1)],
            [("OldActor", make_cre([item_record()]))]))
        after = add_area_fixture(before, "creatures")
        old, new = parse_are(before), parse_are(after)
        allowed = set(range(0x54, 0x5A))
        self.assertTrue(all(a == b for index, (a, b) in enumerate(zip(before, after)) if index not in allowed))
        self.assertEqual(new["containers"], old["containers"])
        self.assertEqual(new["actors"][0]["embedded_cre"], old["actors"][0]["embedded_cre"])
        # Sources stay invulnerable (flags 5); recipients are killable (flags 1).
        self.assertEqual([(row["name"], row["flags"], row["cre_resref"], row["embedded_cre_size"])
                          for row in new["actors"][1:]],
            [("FLPRS1", 5, "flprs1", 0), ("FLPRS2", 5, "flprs2", 0), ("FLPRS3", 5, "flprs3", 0),
             ("FLPRC1", 1, "flprc1", 0), ("FLPRC2", 1, "flprc2", 0)])
        self.assertEqual([(row["x"], row["y"]) for row in new["actors"][4:]], [(1140, 650), (1260, 650)])
        with self.assertRaisesRegex(ValueError, "already contains"):
            add_area_fixture(after, "creatures")
        with self.assertRaisesRegex(ValueError, "Unknown fixture variant"):
            add_area_fixture(before, "shops")

    def test_full_creature_builder_binds_recipients_and_loads_in_the_adapter(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game, originals = synthetic_game(root)
            output = root / "payload"
            report = build(game, output, "creatures")
            manifest = read_campaign(output / "campaign-store")
            self.assertEqual((report["variant"], manifest["revision"]), ("creatures", report["revision"]))
            self.assertEqual(manifest["profile_id"], "native-mode1-creature-fixture")
            self.assertEqual(len(manifest["sources"]), 3)
            area = parse_are((output / "override" / "BG2600.ARE").read_bytes())
            self.assertFalse(any(row["name"].startswith("FLPR") for row in area["containers"]))
            actors = {row["name"]: row for row in area["actors"]}
            targets = {row["record"]["selector"]["name"]: row for row in manifest["targets"]}
            self.assertEqual({name: row["capacity"] for name, row in targets.items()}, {"FLPRC1": 2, "FLPRC2": 1})
            for name, row in targets.items():
                record = row["record"]
                self.assertEqual((row["id"], record["kind"]), (f"are:bg2600/actor/{actors[name]['record_index']}", "actor"))
                self.assertEqual(record["selector"]["template_resource"], "cre:" + name.lower())
                self.assertEqual(record["concerns"], ["actor-availability", "external-cre-template", "lootability-unverified"])
            recipients = {name: parse_cre((output / "override" / f"{name}.CRE").read_bytes()) for name in targets}
            self.assertEqual(recipients["FLPRC1"]["items"], [])
            self.assertEqual([row["resref"] for row in recipients["FLPRC2"]["items"]], ["flprx1"])
            self.assertEqual((output / "override" / "FLPRX1.ITM").read_bytes(), originals["SW1H01.ITM"])
            native = json.loads((output / "fixture-native.json").read_text())
            self.assertEqual(native["variant"], "creatures")
            self.assertTrue(native["ready_detail"].startswith("have been given to"))
            self.assertEqual(native["unrelated_items"],
                {"FLPRC2": [{"resref": "flprx1", "expiration": 0, "charges": [0, 0, 0], "flags": 1}]})
            identities = [row["identity"] for key in ("sources", "targets") for row in native[key].values()]
            self.assertEqual(len(set(identities)), 5)
            self.assertEqual(adapter_targets(output / "fixture-native.lua"),
                {row["id"]: ("creature", row["record"]["selector"]["name"].lower(), True, None, None)
                 for row in manifest["targets"]})
            self.assertEqual({path.name: path.read_bytes() for path in (game / "override").iterdir()}, originals)
            # Deterministic, and never the accepted ground-pile revision.
            self.assertEqual(build(game, root / "again", "creatures")["revision"], report["revision"])
            self.assertNotEqual(build(game, root / "piles")["revision"], report["revision"])

    def test_generated_creature_fixture_completes_as_held_through_the_real_stack(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game, _ = synthetic_game(root)
            output = root / "payload"
            revision = build(game, output, "creatures")["revision"]
            manifest_lua = (output / "campaign-store" / "revisions" / (revision + ".lua")).read_text(encoding="utf-8")
            native_lua = (output / "fixture-native.lua").read_text(encoding="utf-8")
            for runtime in (Lua51, Lua53, LuaJIT21):
                with self.subTest(runtime=runtime.__module__):
                    lua = runtime(unpack_returned_tuples=True)
                    run = lua.execute(CREATURE_RUN)
                    self.assertTrue(run(lua.execute(CORE), lua.execute(HOST), lua.execute(ADAPTER),
                                        lua.execute(manifest_lua), lua.execute(native_lua)))

    def test_builder_refuses_unknown_variant_or_any_existing_fixture_resource(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game, _ = synthetic_game(root)
            with self.assertRaisesRegex(ValueError, "Unknown fixture variant"):
                build(game, root / "unknown", "shops")
            for name in ("FLPRS2.CRE", "FLPRI3.ITM", "FLPRC1.CRE", "FLPRC2.CRE", "FLPRX1.ITM"):
                (game / "override" / name).write_bytes(b"earlier fixture output")
                for variant in VARIANTS:
                    with self.subTest(name=name, variant=variant), \
                            self.assertRaisesRegex(ValueError, "already contains fixture resources"):
                        build(game, root / f"{name}-{variant}", variant)
                (game / "override" / name).unlink()
            self.assertFalse(any(path.name != "game" for path in root.iterdir()))


if __name__ == "__main__":
    unittest.main()
