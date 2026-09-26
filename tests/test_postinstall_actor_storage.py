"""Synthetic ARE actor storage ambiguities; no installed game is accessed."""

from copy import deepcopy
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from flir_config import cli
from flir_config.campaigns import _validate_campaign, compile_campaign
from flir_config.profiles import ProfileError, _digest, edit_profile, new_profile, write_json
from flir_config.resources import ScanError, scan_game
from flir_config.sources import (bind_profile, new_binding_plan, scan_world,
                                 validate_world, world_fingerprint)
from flir_config.world_formats import parse_are
from test_postinstall_resources import make_game, snapshot
from test_postinstall_world_formats import item_record, make_are, make_cre, make_sto


CONCERN = "unresolved-creature-storage"
DIAGNOSTIC = {"reason": "incomplete-embedded-cre"}


def actor_base(area, index):
    return struct.unpack_from("<I", area, 0x54)[0] + index * 0x110


def incomplete_area():
    """Two flags=0 HUNTER actors with offset-only and size-only storage."""
    area = make_are(
        [item_record(flags=1)], [("Chest", 0, 1)],
        [("Offset Only", "HUNTER"), ("Size Only", "HUNTER"),
         ("Clean Embedded", make_cre([item_record(flags=1)]))])
    for index, offset, size in ((0, 0x300, 0), (1, 0, 3)):
        base = actor_base(area, index)
        struct.pack_into("<I", area, base + 0x28, 0)
        struct.pack_into("<II", area, base + 0x88, offset, size)
    return area


def source(world, identity):
    return next(row for row in world["sources"] if row["id"] == identity)


def target(world, identity):
    return next(row for row in world["targets"] if row["id"] == identity)


class IncompleteActorFormatTests(unittest.TestCase):
    def test_default_rejects_both_shapes_but_opt_in_preserves_raw_actor_fields(self):
        for index, expected_offset, expected_size in ((0, 0x200, 0), (1, 0, 3)):
            area = make_are(actors=[("Hunter", "HUNTER")])
            base = actor_base(area, 0)
            struct.pack_into("<I", area, base + 0x28, 0)
            struct.pack_into("<II", area, base + 0x88, expected_offset, expected_size)
            with self.subTest(index=index), self.assertRaises(ScanError):
                parse_are(area)
            parsed = parse_are(area, allow_incomplete_actors=True)["actors"][0]
            self.assertEqual(parsed["record_index"], 0)
            self.assertEqual(parsed["byte_offset"], base)
            self.assertEqual(parsed["name"], "Hunter")
            self.assertEqual(parsed["cre_resref"], "hunter")
            self.assertEqual(parsed["flags"], 0)
            self.assertEqual((parsed["embedded_cre_offset"], parsed["embedded_cre_size"]),
                             (expected_offset, expected_size))
            self.assertIsNone(parsed["embedded_cre"])
            self.assertEqual(parsed["storage_diagnostics"], DIAGNOSTIC)

    def test_mixed_area_keeps_complete_embedded_and_container_inventory(self):
        area = parse_are(incomplete_area(), allow_incomplete_actors=True)
        self.assertEqual([actor["record_index"] for actor in area["actors"]], [0, 1, 2])
        self.assertEqual([actor["storage_diagnostics"] for actor in area["actors"][:2]],
                         [DIAGNOSTIC, DIAGNOSTIC])
        self.assertEqual([actor["embedded_cre"] for actor in area["actors"][:2]], [None, None])
        self.assertEqual(area["actors"][2]["embedded_cre"]["items"][0]["resref"], "synth001")
        self.assertEqual(area["containers"][0]["items"][0]["resref"], "synth001")


class IncompleteActorWorldTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="flir-incomplete-actor-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.game = self.root / "synthetic-game"
        make_game(self.game)
        override = self.game / "override"
        (override / "area.are").write_bytes(incomplete_area())
        (override / "hunter.cre").write_bytes(make_cre([item_record(flags=1)]))
        (override / "shop.sto").write_bytes(make_sto([(item_record(flags=1), 1, 0)]))
        self.catalog = scan_game(self.game)
        self.profile = edit_profile(new_profile(self.catalog, "test", "Test adventure"),
                                    self.catalog, include=["synth001"])

    def scan(self):
        before = snapshot(self.game)
        world = scan_world(self.catalog)
        self.assertEqual(snapshot(self.game), before)
        validate_world(world, self.catalog)
        return world

    def rehash(self, world):
        world["fingerprint"] = world_fingerprint(world)
        return world

    def plan(self, world, source_id="sto:shop/item/0", target_id="are:area/container/0"):
        plan = new_binding_plan(self.profile, self.catalog, world)
        plan["sources"] = [{"item": "synth001", "policy": "relocate",
                            "source_ids": [source_id], "registration_ids": []}]
        plan["targets"] = [{"id": target_id, "capacity": 1, "optional": False}]
        return plan

    def test_scan_flags_unresolved_targets_without_sources_or_template_fallback(self):
        world = self.scan()
        self.assertEqual(world["schema"], "flir-world-catalog-v2")
        for index, offset, size in ((0, 0x300, 0), (1, 0, 3)):
            row = target(world, f"are:area/actor/{index}")
            selector = row["selector"]
            self.assertEqual(selector["storage_diagnostics"], DIAGNOSTIC)
            self.assertEqual((selector["record_index"], selector["cre_resref"], selector["flags"]),
                             (index, "hunter", 0))
            self.assertEqual((selector["embedded_cre_offset"], selector["embedded_cre_size"]),
                             (offset, size))
            self.assertIn(CONCERN, row["concerns"])
            for forbidden in ("template_resource", "inventory_diagnostics", "template_inventory_diagnostics"):
                self.assertNotIn(forbidden, selector)
            self.assertFalse(any(source_row["owner"] == row["id"] for source_row in world["sources"]))
        clean = target(world, "are:area/actor/2")
        self.assertNotIn("storage_diagnostics", clean["selector"])
        self.assertEqual(source(world, "are:area/actor/2/item/0")["item"], "synth001")

    def test_cli_counts_only_unresolved_actors(self):
        catalog_path = self.root / "catalog.json"
        world_path = self.root / "world.json"
        write_json(catalog_path, self.catalog)
        before = snapshot(self.game)
        args = cli.parser().parse_args(["world", "scan", "--catalog", str(catalog_path),
                                        "--out", str(world_path)])
        summary = cli.run(args)
        self.assertEqual(snapshot(self.game), before)
        self.assertEqual(summary["unresolved_actor_storage"], 2)
        self.assertEqual(summary["source_records"], 4)

    def test_rehashed_world_rejects_removed_or_inconsistent_storage_evidence(self):
        original = self.scan()
        changes = {
            "schema_v1": lambda w: w.update(schema="flir-world-catalog-v1"),
            "remove_concern": lambda w: target(w, "are:area/actor/0")["concerns"].remove(CONCERN),
            "remove_diagnostic": lambda w: target(w, "are:area/actor/0")["selector"].pop("storage_diagnostics"),
            "wrong_reason": lambda w: target(w, "are:area/actor/0")["selector"]["storage_diagnostics"].__setitem__("reason", "other"),
            "add_template": lambda w: target(w, "are:area/actor/0")["selector"].__setitem__("template_resource", "cre:hunter"),
            "add_inventory_diagnostic": lambda w: target(w, "are:area/actor/0")["selector"].__setitem__("inventory_diagnostics", {}),
            "clear_offset": lambda w: target(w, "are:area/actor/0")["selector"].__setitem__("embedded_cre_offset", 0),
            "set_external_flag": lambda w: target(w, "are:area/actor/0")["selector"].__setitem__("flags", 1),
        }
        for name, change in changes.items():
            forged = deepcopy(original)
            change(forged)
            with self.subTest(name=name), self.assertRaises(ProfileError):
                validate_world(self.rehash(forged), self.catalog)
        forged = deepcopy(original)
        moved = deepcopy(source(forged, "are:area/actor/2/item/0"))
        forged["sources"].remove(source(forged, "are:area/actor/2/item/0"))
        moved["owner"] = "are:area/actor/0"
        moved["id"] = moved["owner"] + "/item/0"
        forged["sources"].append(moved)
        forged["sources"].sort(key=lambda row: row["id"])
        with self.assertRaises(ProfileError):
            validate_world(self.rehash(forged), self.catalog)

    def test_unresolved_target_cannot_bind_but_unrelated_path_compiles(self):
        world = self.scan()
        for index in (0, 1):
            with self.subTest(index=index), self.assertRaises(ProfileError):
                bind_profile(self.profile, self.catalog, world,
                             self.plan(world, target_id=f"are:area/actor/{index}"))
        clean = self.plan(world)
        self.assertEqual(bind_profile(self.profile, self.catalog, world, clean)["reward_units"], 1)
        self.assertEqual(len(compile_campaign(self.profile, self.catalog, world, clean)["targets"]), 1)

    def test_campaign_rejects_raw_storage_key_without_concern(self):
        world = self.scan()
        forged = deepcopy(compile_campaign(self.profile, self.catalog, world, self.plan(world)))
        forged["targets"][0]["record"]["selector"]["storage_diagnostics"] = deepcopy(DIAGNOSTIC)
        forged["revision"] = _digest({key: value for key, value in forged.items() if key != "revision"})
        with self.assertRaises(ProfileError):
            _validate_campaign(forged)

    def test_campaign_rejects_incomplete_actor_after_diagnostic_and_marker_stripped(self):
        world = self.scan()
        clean = compile_campaign(self.profile, self.catalog, world, self.plan(world))
        area_hash = next(row["sha256"] for row in world["resources"] if row["id"] == "are:area")
        for index in (0, 1):
            forged = deepcopy(clean)
            actor = deepcopy(target(world, f"are:area/actor/{index}"))
            actor["selector"].pop("storage_diagnostics")
            actor["concerns"].remove(CONCERN)
            forged["targets"][0].update(id=actor["id"], record=actor,
                                        resource_sha256=area_hash)
            forged["revision"] = _digest({key: value for key, value in forged.items() if key != "revision"})
            with self.subTest(index=index), self.assertRaises(ProfileError):
                _validate_campaign(forged)


if __name__ == "__main__":
    unittest.main()
