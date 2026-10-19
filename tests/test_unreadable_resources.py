"""Game files the world scan cannot read are left out with a note; synthetic games only.

Another mod's damaged or unusual area, creature or store must not stop CIR for
the whole game. The scan keeps the file's resource row, takes no records from
it and names it with its reason; nothing later reads it or puts a reward in it.
"""

from copy import deepcopy
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from flir_config.campaigns import write_campaign  # noqa: E402
from flir_config.editor import Editor  # noqa: E402
from flir_config.native_export import export_native  # noqa: E402
from flir_config.original_catalog import resolve_template  # noqa: E402
from flir_config.original_configuration import (build_original_configuration,  # noqa: E402
                                                fit_original_configuration)
from flir_config.original_options import spell_shield_spec  # noqa: E402
from flir_config.platform import game_platform, has_sod  # noqa: E402
from flir_config.profiles import ProfileError, edit_profile, new_profile, write_json  # noqa: E402
from flir_config.resources import scan_game  # noqa: E402
from flir_config.sources import (bind_profile, new_binding_plan, scan_world,  # noqa: E402
                                 unreadable_notes, world_fingerprint)
from flir_config.templates import DEFAULT_OPTIONS  # noqa: E402
from test_original_configuration import source_row, target_row  # noqa: E402
from test_postinstall_resources import make_game, make_itm, snapshot  # noqa: E402
from test_postinstall_world_formats import item_record, make_are, make_cre, make_sto  # noqa: E402
from sod_table_fixtures import ITEM_HEADER, PLACE_HEADER, sod_tables  # noqa: E402

DAMAGED = {
    # A truncated area, a creature with another file's header, a store of another version.
    "broken.are": make_are([item_record()], [("Chest", 0, 1)])[:0x100],
    "badhead.cre": b"CRE V9.9" + bytes(make_cre([item_record()]))[8:],
    "oldshop.sto": b"STORV1.1" + bytes(make_sto([(item_record(), 2, 0)]))[8:],
}
REASONS = [{"id": "are:broken", "reason": "Truncated AREA header."},
           {"id": "cre:badhead", "reason": "Unsupported world resource format; expected CRE V1.0."},
           {"id": "sto:oldshop", "reason": "Unsupported world resource format; expected STORV1.0."}]
NOTE = ("BROKEN.ARE (Truncated AREA header), BADHEAD.CRE (Unsupported world resource format; "
        "expected CRE V1.0) and OLDSHOP.STO (Unsupported world resource format; expected STORV1.0) "
        "cannot be read, so Chriz Item Randomiser leaves them out: their items stay as the game "
        "has them and no reward goes there")


def droppable() -> bytes:
    item = bytearray(make_itm())
    struct.pack_into("<I", item, 0x18, 0x4c)
    return bytes(item)


def reward(name: str) -> bytes:
    return item_record(name, expiration=0, charges=(0, 0, 0), flags=1)


class UnreadableWorldTests(unittest.TestCase):
    """tests/test_postinstall_sources.py's game, with three damaged files added."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="cir-unreadable-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.game = self.root / "game"
        make_game(self.game)
        self.override = self.game / "override"
        item = item_record(flags=1)
        for name, data in {"actor.cre": make_cre([item, item_record(charges=(2, 9, 0), flags=4)]),
                           "area.are": make_are([item], [("Chest", 0, 1)],
                                                [("External", "ACTOR"), ("Embedded", make_cre([item]))]),
                           "shop.sto": make_sto([(item, 3, 0)]),
                           "grant.bcs": b"SC\nsynthetic grant\nSC\n",
                           "grant.dlg": b"DLG V1.0synthetic grant"}.items():
            (self.override / name).write_bytes(data)
        self.catalog = scan_game(self.game)
        self.profile = edit_profile(new_profile(self.catalog, "test", "Test adventure"), self.catalog,
                                    include=["synth001"])

    def damage(self):
        for name, data in DAMAGED.items():
            (self.override / name).write_bytes(data)

    def cli(self, *args):
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "randomiser_config.py"), *map(str, args)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["result"]

    def test_damaged_area_creature_and_store_are_left_out_with_a_note(self):
        before = scan_world(self.catalog)
        self.damage()
        files = snapshot(self.game)
        world = scan_world(self.catalog)
        self.assertEqual(snapshot(self.game), files)
        self.assertEqual(world["unreadable"], REASONS)
        # Everything else is read as without the damaged files.
        self.assertEqual(world["sources"], before["sources"])
        self.assertEqual(world["targets"], before["targets"])
        self.assertEqual(world["schema"], before["schema"])
        left_out = {row["id"] for row in REASONS}
        self.assertEqual({row["id"] for row in world["resources"]} - left_out,
                         {row["id"] for row in before["resources"]})
        self.assertEqual([row for row in world["resources"] if row["id"] not in left_out],
                         before["resources"])
        self.assertEqual(unreadable_notes(world), [NOTE])
        self.assertEqual(world["fingerprint"], world_fingerprint(world))
        # The scan checks its own result; a binding plan can be made over it.
        self.assertEqual(new_binding_plan(self.profile, self.catalog, world)["world_fingerprint"],
                         world["fingerprint"])
        one = {**world, "unreadable": world["unreadable"][:1]}
        one["fingerprint"] = world_fingerprint(one)
        self.assertEqual(unreadable_notes(one), [
            "BROKEN.ARE (Truncated AREA header) cannot be read, so Chriz Item Randomiser leaves it "
            "out: its items stay as the game has them and no reward goes there"])

    def test_a_game_without_damaged_files_scans_as_before(self):
        world = scan_world(self.catalog)
        self.assertNotIn("unreadable", world)
        self.assertEqual(unreadable_notes(world), [])
        # The reference gave this game this world before scans could leave files out (5494ab5).
        self.assertEqual(world["fingerprint"], "8a942c9fdd6ad6abfba256cab86624cf165e83750db2a477ee559ec8c99e5a93")

    def test_world_scan_command_names_the_left_out_files(self):
        catalog_path, world_path = self.root / "catalog.json", self.root / "world.json"
        write_json(catalog_path, self.catalog)
        normal = self.cli("world", "scan", "--catalog", catalog_path, "--out", world_path)
        self.assertNotIn("warnings", normal)
        self.damage()
        result = self.cli("world", "scan", "--catalog", catalog_path, "--out", world_path)
        self.assertEqual(result["warnings"], [NOTE])
        self.assertEqual((result["source_records"], result["target_candidates"]),
                         (normal["source_records"], normal["target_candidates"]))
        self.assertEqual(json.loads(world_path.read_text(encoding="utf-8"))["unreadable"], REASONS)

    def test_actor_whose_creature_file_cannot_be_read_cannot_receive_rewards(self):
        self.damage()
        (self.override / "area.are").write_bytes(make_are([item_record(flags=1)], [("Chest", 0, 1)],
                                                          [("Broken", "BADHEAD"), ("Absent", "NOSUCH")]))
        world = scan_world(self.catalog)
        actors = {row["id"]: row["concerns"] for row in world["targets"] if row["kind"] == "actor"}
        self.assertIn("unreadable-creature-template", actors["are:area/actor/0"])
        self.assertNotIn("missing-creature-template", actors["are:area/actor/0"])
        self.assertIn("missing-creature-template", actors["are:area/actor/1"])
        plan = new_binding_plan(self.profile, self.catalog, world)
        plan["sources"] = [{"item": "synth001", "policy": "relocate",
                            "source_ids": ["are:area/container/0/item/0"], "registration_ids": []}]
        for target, message in (("are:area/actor/0", "creature template that cannot be read"),
                                ("are:area/actor/1", "missing creature template"),
                                ("are:broken/container/0", "resource cannot be read: are:broken"),
                                ("cre:badhead", "resource cannot be read: cre:badhead")):
            with self.subTest(target=target):
                plan["targets"] = [{"id": target, "capacity": 1, "optional": False}]
                with self.assertRaisesRegex(ProfileError, message):
                    bind_profile(self.profile, self.catalog, world, plan)

    def test_world_checks_what_the_scan_left_out(self):
        self.damage()
        world = scan_world(self.catalog)

        def changed(edit, message):
            forged = deepcopy(world)
            edit(forged)
            forged["fingerprint"] = world_fingerprint(forged)
            with self.assertRaisesRegex(ProfileError, message):
                new_binding_plan(self.profile, self.catalog, forged)

        for edit, message in (
                (lambda w: w["unreadable"].append({"id": "sto:shop", "reason": "Forged."}),
                 "refers to a resource the scan left out"),
                (lambda w: w["unreadable"].insert(0, {"id": "are:area", "reason": "Forged."}),
                 "refers to a resource the scan left out"),
                (lambda w: w["unreadable"].insert(1, {"id": "bcs:grant", "reason": "Forged."}),
                 "must be a scanned creature, area or store"),
                (lambda w: w["unreadable"].insert(0, {"id": "are:absent", "reason": "Forged."}),
                 "must be a scanned creature, area or store"),
                (lambda w: w["unreadable"].reverse(), "must be sorted"),
                (lambda w: w["unreadable"].append(dict(w["unreadable"][-1])), "Duplicate unreadable"),
                (lambda w: w.update(unreadable=[]), "Invalid unreadable resource list"),
                (lambda w: w.update(unreadable={}), "Invalid unreadable resource list"),
                (lambda w: w["unreadable"][0].update(line=1), "Unsupported unreadable resource fields"),
                (lambda w: w["unreadable"][0].update(reason=""), "Invalid unreadable resource reason"),
                (lambda w: w["unreadable"][0].update(reason=None), "Invalid unreadable resource reason"),
                (lambda w: w["unreadable"][0].update(id=None), "Invalid unreadable resource identity")):
            with self.subTest(message=message):
                changed(edit, message)
        # The fingerprint covers the list.
        forged = deepcopy(world)
        forged["unreadable"][0]["reason"] = "Another reason."
        with self.assertRaisesRegex(ProfileError, "content changed"):
            new_binding_plan(self.profile, self.catalog, forged)
        forged = deepcopy(world)
        del forged["unreadable"]
        with self.assertRaisesRegex(ProfileError, "content changed"):
            new_binding_plan(self.profile, self.catalog, forged)


class UnreadableTableRowTests(unittest.TestCase):
    """Rows of Item Randomiser's tables whose files the scan left out."""

    @staticmethod
    def rows(template, world, catalog=None, **choices):
        result = resolve_template(catalog or {"items": []}, world, template, game=choices.pop("game"),
                                  **choices)
        return ({row["source_ref"]: row for row in result["items"]},
                {row["source_ref"]: row for row in result["targets"]})

    def world(self, present, unreadable):
        return {"resources": [{"id": name} for name in present], "sources": [], "targets": [],
                "unreadable": [{"id": name, "reason": "Truncated header."} for name in unreadable]}

    def test_rewards_from_left_out_files_are_not_available(self):
        catalog = {"items": [{"resref": name} for name in ("brac01", "ring20", "belt04")]}
        present = ("are:ar4701", "cre:denak", "cre:ogreunsh")
        for unreadable in ((), present):
            with self.subTest(unreadable=unreadable):
                items, _ = self.rows("bg1-original", self.world(present, unreadable), catalog, game="bgee")
                for line in ("4", "11", "49"):
                    row = items["lists/items/base/bg1.2da:" + line]
                    if unreadable:
                        self.assertEqual((row["available"], row["availability_reason"], row["binding_problem"]),
                                         (False, "source-resource-unreadable", "source-resource-unreadable"))
                    else:
                        self.assertNotEqual(row["availability_reason"], "source-resource-unreadable")

    def test_places_in_left_out_files_name_the_file_kind(self):
        present = ("are:ar4801", "cre:neira", "are:ar3301", "sto:taerum", "are:ar1303",
                   "bcs:kpgol01", "bcs:kpgol03")
        for unreadable, expected in (
                (("cre:neira",), {"base/bg1.2da:6": "creature-resource-unreadable"}),
                (("are:ar4801",), {"base/bg1.2da:6": "area-resource-unreadable"}),
                (("sto:taerum",), {"mode2/bg1.2da:6": "store-resource-unreadable"}),
                (("are:ar3301", "sto:taerum"), {"mode2/bg1.2da:6": "area-resource-unreadable"})):
            with self.subTest(unreadable=unreadable):
                _, places = self.rows("bg1-original", self.world(present, unreadable), game="bgee",
                                      items="extended", targets="extended")
                for line, problem in expected.items():
                    self.assertEqual(places["lists/locations/" + line]["binding_problem"], problem)
        # The golems' treasure quest: its container is in AR1303.
        _, places = self.rows("bg2-original", self.world(present, ("are:ar1303",)), game="bg2ee",
                              items="extended", targets="extended")
        quest = next(row for row in places.values() if row["id"] == "bg2:mode2:target:13")
        self.assertEqual((quest["area_resource"], quest["binding_problem"]),
                         ("are:ar1303", "area-resource-unreadable"))

    def test_spell_shield_option_names_its_unreadable_store(self):
        world = self.world(("sto:udduer01",), ("sto:udduer01",))
        with self.assertRaisesRegex(ProfileError, "UDDUER01.STO, which cannot be read"):
            spell_shield_spec(world, randomized=False)


# Two rewards and three places in Siege of Dragonspear's synthetic tables;
# BD1000.ARE holds one of each.
ITEMS = ITEM_HEADER + """
amul12   blank       bd0062.are   32   01    d1    100    first area
bow02    blank       bd1000.are   32   02    d2    100    second area
"""
PLACES = PLACE_HEADER + """
1        32   corpse           bd0062 d1    100    home of d1
2        32   pile03           bd1000 d2    100    home of d2
3        32   chest            bd0062 x     100    spare
"""


class UnreadablePresetTests(unittest.TestCase):
    """A preset on a BG:EE game whose BD1000.ARE cannot be read."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="cir-unreadable-preset-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.game = self.root / "game"
        make_game(self.game, [("AMUL12", droppable()), ("BOW02", droppable())])
        self.override = self.game / "override"
        (self.override / "oh1000.are").write_bytes(make_are())
        (self.override / "bd0062.are").write_bytes(make_are([reward("AMUL12")], [("Corpse", 0, 1), ("Chest", 1, 0)]))
        self.area = bytes(make_are([reward("BOW02")], [("Pile03", 0, 1)]))
        (self.override / "bd1000.are").write_bytes(self.area[:0x100])
        self.enterContext(sod_tables(ITEMS, PLACES))
        self.catalog = scan_game(self.game)
        self.world = scan_world(self.catalog)

    def test_the_game_and_its_parts_are_still_recognised(self):
        self.assertEqual([row["id"] for row in self.world["unreadable"]], ["are:bd1000"])
        self.assertEqual(game_platform(self.world), "bgee")
        self.assertTrue(has_sod(self.world))
        # A damaged area that tells the game apart does not hide the game.
        (self.override / "oh1000.are").write_bytes(b"AREAV1.0")
        world = scan_world(self.catalog)
        self.assertEqual([row["id"] for row in world["unreadable"]], ["are:bd1000", "are:oh1000"])
        self.assertEqual(game_platform(world), "bgee")

    def test_preset_leaves_out_the_rows_of_the_file_and_says_why(self):
        generated = fit_original_configuration(self.catalog, self.world, "sod-original")
        self.assertEqual(generated["not_used_notes"], [
            "BD1000.ARE (Truncated AREA header) cannot be read, so Chriz Item Randomiser leaves it out: "
            "its items stay as the game has them and no reward goes there",
            "1 reward and 1 place from the lists cannot be used in this game, so they are left out; "
            "a left-out reward stays as the game has it"])
        self.assertEqual([(row["id"], row["kind"], row["reason"]) for row in generated["not_used"]],
                         [("sod:base:item:4", "reward", "source-resource-unreadable"),
                          ("sod:base:target:4", "place", "area-resource-unreadable")])
        plan = generated["plan"]
        self.assertEqual([row["id"] for row in plan["targets"]],
                         ["original-target:sod:base:target:3", "original-target:sod:base:target:5"])
        self.assertNotIn("bd1000", json.dumps(plan))
        # The configuration is prepared and exported without the left-out file.
        store, output = self.root / "campaigns", self.root / "export"
        revision = write_campaign(generated["profile"], self.catalog, self.world, plan, store)["revision"]
        export_native(store, self.catalog, self.world, output, revision)
        native = json.loads((output / "campaign-store" / "revisions" / f"{revision}.native.json")
                            .read_text(encoding="utf-8"))
        self.assertIn("BD0062.ARE", native["resource_hashes"])
        self.assertNotIn("BD1000.ARE", native["resource_hashes"])
        self.assertFalse(any("bd1000" in path.name.lower() for path in output.rglob("*")))

    def test_editor_shows_the_note_with_the_preset(self):
        editor = Editor(self.catalog, self.world, self.root / "editor")
        state = editor.post("/api/template", {"id": "sod-original"})
        self.assertTrue(state["not_used"][0].startswith("BD1000.ARE (Truncated AREA header) cannot be read"))
        self.assertEqual(state["not_used_rows"][1]["reason"], "area-resource-unreadable")
        self.assertTrue(editor.post("/api/validate", {"profile": state["profile"],
                                                      "plan": state["plan"]})["runtime_supported"])

    def test_readable_again_the_file_is_used_again(self):
        (self.override / "bd1000.are").write_bytes(self.area)
        world = scan_world(self.catalog)
        self.assertNotIn("unreadable", world)
        generated = fit_original_configuration(self.catalog, world, "sod-original")
        self.assertEqual(generated["not_used"], [])
        self.assertEqual(len(generated["plan"]["targets"]), 3)


class UnreadableGroupMemberTests(unittest.TestCase):
    """A group recipient whose members include a creature the scan could not read."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="cir-unreadable-group-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.game = self.root / "game"
        make_game(self.game, [("SYNTH001", droppable())])
        override = self.game / "override"
        (override / "oh1000.are").write_bytes(make_are())
        guard = bytearray(make_cre())
        guard[0x280:0x288] = b"GuardDV\0"
        (override / "guard.cre").write_bytes(guard)
        (override / "broken.cre").write_bytes(b"CRE V1.0")
        (override / "area.are").write_bytes(make_are(
            [reward("SYNTH001")], [("Source", 0, 1)], [("Guard", "GUARD"), ("Broken", "BROKEN")]))
        self.catalog = scan_game(self.game)
        self.world = scan_world(self.catalog)

    def draft(self):
        place = target_row("group-1", "x", "24")
        place.update(endpoint_kind="group", target="guards.flg", container_name=None,
                     members=["cre:guard", "cre:broken"],
                     target_ids=["are:area/actor/0", "are:area/actor/1"],
                     binding_problem="group-requires-runtime-selection")
        source = source_row("source-1", ident="x", tier="24",
                            source_ids=[next(row["id"] for row in self.world["sources"]
                                             if row["resource"] == "are:area")])
        return {"name": "Original", "choices": {"items": "standard", "targets": "standard"},
                "options": DEFAULT_OPTIONS | {"scrolls": False}, "items": [source], "targets": [place]}

    def test_group_reward_goes_only_to_members_that_could_be_read(self):
        self.assertEqual([row["id"] for row in self.world["unreadable"]], ["cre:broken"])
        draft = self.draft()
        with patch("flir_config.original_configuration.resolve_template", return_value=draft), \
                patch("flir_config.original_registrations.resolve_template", return_value=draft):
            built = build_original_configuration(self.catalog, self.world, "bg1-original")
            store, output = self.root / "campaigns", self.root / "export"
            revision = write_campaign(built["profile"], self.catalog, self.world, built["plan"],
                                      store)["revision"]
            export_native(store, self.catalog, self.world, output, revision)
        native = json.loads((output / "campaign-store" / "revisions" / f"{revision}.native.json")
                            .read_text(encoding="utf-8"))
        group = native["targets"]["original-target:group-1"]
        self.assertEqual(group["members"], [{"resref": "guard", "script_name": "GuardDV"}])
        self.assertIn("GUARD.CRE", native["resource_hashes"])
        self.assertNotIn("BROKEN.CRE", native["resource_hashes"])


if __name__ == "__main__":
    unittest.main()
