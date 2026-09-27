"""Native export checks on a temporary synthetic installation only."""

import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from flir_config.campaigns import _render_lua, write_campaign
from flir_config.native_export import export_native
from flir_config.profiles import ProfileError, edit_profile, new_profile
from flir_config.resources import scan_game
from flir_config.sources import new_binding_plan, scan_world
from tests.test_postinstall_resources import make_game, make_itm, snapshot
from tests.test_postinstall_world_formats import item_record, make_are, make_cre


class NativeExportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="flir-native-export-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.game, self.store, self.output = (self.root / name for name in ("game", "campaigns", "flpr-fixture"))
        make_game(self.game, [("SYNTH001", make_itm()), ("SYNTH002", make_itm())])
        self.override = self.game / "override"
        for name in ("synth001", "synth002"):
            item = bytearray(make_itm())
            struct.pack_into("<I", item, 0x18, 0x4c)
            (self.override / f"{name}.itm").write_bytes(item)
        self.first = "are:area/container/0/item/0"
        self.second = "are:area/container/1/item/1"
        embedded = bytearray(make_cre([]))
        embedded[0x280:0x288] = b"EmbedDV\0"
        (self.override / "actor.cre").write_bytes(make_cre([]))
        self.area = bytes(make_are([
            item_record("SYNTH001", expiration=0, charges=(0, 0, 0), flags=1),
            item_record("SYNTH002", expiration=0, charges=(0, 0, 0), flags=1)],
            [("ChestOne", 0, 1), ("ChestTwo", 1, 1)],
            [("External", "ACTOR"), ("Embedded", bytes(embedded))]))
        (self.override / "area.are").write_bytes(self.area)
        self.refresh()

    def refresh(self):
        self.catalog = scan_game(self.game)
        self.world = scan_world(self.catalog)

    def write(self, items=("synth001",), target="are:area/actor/0"):
        profile = edit_profile(new_profile(self.catalog, "native-test", "Native test"),
                               self.catalog, include=list(items))
        plan = new_binding_plan(profile, self.catalog, self.world)
        ids = {"synth001": self.first, "synth002": self.second}
        plan["sources"] = [{"item": item, "policy": "relocate", "source_ids": [ids[item]],
                            "registration_ids": []} for item in items]
        plan["targets"] = [{"id": target, "capacity": len(items), "optional": False}]
        return write_campaign(profile, self.catalog, self.world, plan, self.store)["revision"]

    def export(self, revision=None):
        return export_native(self.store, self.catalog, self.world, self.output, revision)

    def test_new_profile_preserves_old_native_revision_and_pristine_area(self):
        game_before = snapshot(self.game)
        a = self.write(("synth001", "synth002"))
        first = self.export(a)
        self.assertEqual((first["revision"], first["revisions"], first["state"]),
                         (a, [a], "experimental-native-config"))
        a_paths = [self.output / "campaign-store" / "revisions" / f"{a}{suffix}"
                   for suffix in (".json", ".lua", ".native.json", ".native.lua")]
        old_bytes = [path.read_bytes() for path in a_paths]
        a_native = json.loads(old_bytes[2])
        self.assertEqual(set(a_native["sources"]), {self.first, self.second})
        self.assertEqual(a_native["source_gate"]["source_ids"], [self.first, self.second])
        self.assertEqual(a_native["targets"]["are:area/actor/0"]["script_name"], "TestDV")
        pristine = self.output / "AREA.pristine.are"
        self.assertEqual(pristine.read_bytes(), self.area)

        b = self.write()
        second = self.export()
        self.assertEqual((second["revision"], second["revisions"]), (b, sorted([a, b])))
        self.assertEqual(Path(second["config_path"]), self.output)
        self.assertEqual([path.read_bytes() for path in a_paths], old_bytes)
        self.assertEqual(pristine.read_bytes(), self.area)
        b_native = json.loads((self.output / "campaign-store" / "revisions" /
                               f"{b}.native.json").read_bytes())
        self.assertEqual(list(b_native["sources"]), [self.first])
        self.assertEqual(b_native["source_gate"]["source_ids"], [self.first])
        self.assertEqual((self.output / "source-gate.lua").read_bytes(), _render_lua(sorted([a, b])))
        self.assertEqual((self.output / "next.lua").read_bytes(), _render_lua(b))
        self.assertEqual(snapshot(self.game), game_before)

    def test_embedded_and_external_actor_names_become_native_creature_targets(self):
        for index, name in ((0, "TestDV"), (1, "EmbedDV")):
            with self.subTest(actor=index):
                revision = self.write(target=f"are:area/actor/{index}")
                self.export(revision)
                native = json.loads((self.output / "campaign-store" / "revisions" /
                                     f"{revision}.native.json").read_bytes())
                self.assertEqual(native["targets"][f"are:area/actor/{index}"]["script_name"], name)
                self.assertEqual(native["targets"][f"are:area/actor/{index}"]["kind"], "creature")

    def test_stackable_item_fails_before_publishing_next_pointer(self):
        stackable = bytearray((self.override / "synth001.itm").read_bytes())
        struct.pack_into("<H", stackable, 0x38, 20)
        (self.override / "synth001.itm").write_bytes(stackable)
        self.refresh()
        self.write()
        game_before = snapshot(self.game)
        with self.assertRaises(ProfileError):
            self.export()
        self.assertFalse((self.output / "next.lua").exists())
        self.assertEqual(snapshot(self.game), game_before)

    def test_expired_source_fails_before_publishing_next_pointer(self):
        area = bytearray(self.area)
        struct.pack_into("<H", area, area.index(b"SYNTH001") + 8, 3)
        (self.override / "area.are").write_bytes(area)
        self.refresh()
        self.write()
        with self.assertRaisesRegex(ProfileError, "unexpired"):
            self.export()
        self.assertFalse((self.output / "next.lua").exists())

    def test_same_container_duplicate_resref_cannot_export_one_original(self):
        original = item_record("SYNTH001", expiration=0, charges=(0, 0, 0), flags=1)
        area = make_are([original, original], [("ChestOne", 0, 2)], [("External", "ACTOR")])
        (self.override / "area.are").write_bytes(area)
        self.refresh()
        self.write()
        with self.assertRaisesRegex(ProfileError, "another copy"):
            self.export()
        self.assertFalse((self.output / "next.lua").exists())


if __name__ == "__main__":
    unittest.main()
