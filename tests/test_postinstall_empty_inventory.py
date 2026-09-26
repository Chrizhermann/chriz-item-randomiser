"""Synthetic empty item references; no installed game or live runtime is used."""

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
from flir_config.profiles import ProfileError, _digest, edit_profile, new_profile, read_json, write_json
from flir_config.resources import ScanError, scan_game
from flir_config.sources import (bind_profile, new_binding_plan, scan_world,
                                 validate_world, world_fingerprint)
from flir_config.world_formats import parse_are, parse_cre, parse_sto
from test_postinstall_resources import make_game, snapshot
from test_postinstall_world_formats import item_record, make_are, make_cre, make_sto


def empty_record(*, expiration=29, charges=(0, 5, 65535), flags=0x80000008):
    return item_record("", expiration=expiration, charges=charges, flags=flags)


class EmptyInventoryFormatTests(unittest.TestCase):
    def test_cre_empty_row_keeps_payload_slots_and_next_index(self):
        parsed = parse_cre(make_cre([empty_record(), item_record("S1-4", flags=1)],
                                    slots={0: 0, 9: 1}, gap=7))
        empty, valid = parsed["items"]
        self.assertEqual((empty["resref"], empty["record_index"], empty["byte_offset"]),
                         ("", 0, 0x2D4 + 7))
        self.assertEqual((empty["expiration"], empty["charges"], empty["flags"], empty["slots"]),
                         (29, [0, 5, 65535], 0x80000008, [0]))
        self.assertEqual((valid["resref"], valid["record_index"], valid["slots"]),
                         ("s1-4", 1, [9]))
        self.assertEqual(valid["byte_offset"], empty["byte_offset"] + 20)

    def test_embedded_cre_empty_row_is_rebased_without_shifting_following_row(self):
        data = make_are(actors=[("Embedded", make_cre([empty_record(), item_record()], gap=3))], gap=5)
        actor = parse_are(data)["actors"][0]
        empty, valid = actor["embedded_cre"]["items"]
        self.assertEqual((empty["resref"], empty["record_index"], empty["slots"]), ("", 0, [21]))
        self.assertEqual(empty["byte_offset"], actor["embedded_cre_offset"] + 0x2D4 + 3)
        self.assertEqual((valid["resref"], valid["record_index"], valid["byte_offset"]),
                         ("synth001", 1, empty["byte_offset"] + 20))

    def test_area_container_empty_row_keeps_ownership_and_next_index(self):
        parsed = parse_are(make_are([empty_record(), item_record()], [("Chest", 0, 2)], gap=11))
        empty, valid = parsed["items"]
        self.assertEqual((empty["resref"], empty["record_index"], empty["byte_offset"]),
                         ("", 0, 0x11C + 11))
        self.assertEqual((empty["container_index"], empty["container_indexes"]), (0, [0]))
        self.assertEqual([row["record_index"] for row in parsed["containers"][0]["items"]], [0, 1])
        self.assertEqual((valid["resref"], valid["record_index"], valid["byte_offset"]),
                         ("synth001", 1, empty["byte_offset"] + 20))

    def test_store_empty_row_keeps_stock_and_next_index(self):
        parsed = parse_sto(make_sto([(empty_record(), 6, 0), (item_record(), 2, 1)], gap=9))
        empty, valid = parsed["items"]
        self.assertEqual((empty["resref"], empty["record_index"], empty["byte_offset"]),
                         ("", 0, 0x9C + 9))
        self.assertEqual((empty["quantity"], empty["infinite_supply_flag"], empty["infinite_supply"]),
                         (6, 0, False))
        self.assertEqual((empty["expiration"], empty["charges"], empty["flags"]),
                         (29, [0, 5, 65535], 0x80000008))
        self.assertEqual((valid["resref"], valid["record_index"], valid["byte_offset"]),
                         ("synth001", 1, empty["byte_offset"] + 28))

    def test_malformed_item_resrefs_and_required_actor_resref_still_fail(self):
        for raw in (b"\0BAD\0\0\0\0", b"A\0B\0\0\0\0\0", b" BAD\0\0\0\0", b"A B\0\0\0\0\0", b"\xff\0\0\0\0\0\0\0"):
            record = raw + empty_record()[8:]
            fixtures = ((parse_cre, make_cre([record])),
                        (parse_are, make_are([record], [("Chest", 0, 1)])),
                        (parse_sto, make_sto([(record, 1, 0)])),
                        (parse_are, make_are(actors=[("Embedded", make_cre([record]))])))
            for parser, fixture in fixtures:
                with self.subTest(raw=raw, parser=parser.__name__, signature=bytes(fixture[:8])):
                    with self.assertRaises(ScanError):
                        parser(fixture)
        actor = make_are(actors=[("External", "ACTOR")])
        actor_base = struct.unpack_from("<I", actor, 0x54)[0]
        actor[actor_base + 0x80:actor_base + 0x88] = b"\0" * 8
        with self.assertRaisesRegex(ScanError, "reference"):
            parse_are(actor)

    def test_empty_records_still_obey_overlap_and_slot_alias_checks(self):
        cre = make_cre([empty_record()])
        struct.pack_into("<I", cre, 0x2BC, struct.unpack_from("<I", cre, 0x2B8)[0])
        with self.assertRaisesRegex(ScanError, "Overlapping"):
            parse_cre(cre)
        aliased = parse_cre(make_cre([empty_record()], slots={0: 0, 21: 0}))
        self.assertEqual(aliased["aliased_item_indexes"], [0])
        self.assertEqual(aliased["items"][0]["slots"], [0, 21])


class EmptyInventoryWorldTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="flir-empty-inventory-")
        self.addCleanup(self.temp.cleanup)
        self.game = Path(self.temp.name) / "synthetic-game"
        make_game(self.game)
        override = self.game / "override"
        (override / "actor.cre").write_bytes(make_cre([empty_record(), item_record(flags=1)]))
        (override / "area.are").write_bytes(make_are(
            [empty_record(), item_record(flags=1)], [("Chest", 0, 2)],
            [("Embedded", make_cre([empty_record(), item_record(flags=1)]))]))
        (override / "shop.sto").write_bytes(make_sto(
            [(empty_record(), 6, 0), (item_record(flags=1), 2, 0)]))
        self.catalog = scan_game(self.game)
        self.profile = edit_profile(new_profile(self.catalog, "test", "Test adventure"),
                                    self.catalog, include=["synth001"])

    def scan(self):
        before = snapshot(self.game)
        world = scan_world(self.catalog)
        self.assertEqual(snapshot(self.game), before)
        self.assertEqual(validate_world(deepcopy(world), self.catalog),
                         validate_world(world, self.catalog))
        return world

    def test_scan_retains_all_empty_rows_and_exact_concern_with_v1_shape(self):
        world = self.scan()
        self.assertEqual(world["schema"], "flir-world-catalog-v1")
        self.assertEqual(set(world), {"schema", "game_root", "catalog_fingerprint", "coverage",
                                      "resources", "sources", "targets", "fingerprint"})
        empty_ids = {"cre:actor/item/0", "are:area/container/0/item/0",
                     "are:area/actor/0/item/0", "sto:shop/item/0"}
        by_id = {row["id"]: row for row in world["sources"]}
        self.assertTrue(empty_ids <= set(by_id))
        for identity in empty_ids:
            with self.subTest(identity=identity):
                row = by_id[identity]
                self.assertEqual((row["item"], row["record"]["resref"]), ("", ""))
                self.assertIn("empty-item-reference", row["concerns"])
                self.assertNotIn("missing-item-definition", row["concerns"])
                self.assertEqual(row["record"]["record_index"], 0)
        self.assertEqual(by_id["are:area/container/0/item/0"]["owner"], "are:area/container/0")
        self.assertEqual(by_id["sto:shop/item/0"]["record"]["quantity"], 6)
        self.assertEqual(by_id["cre:actor/item/0"]["record"]["slots"], [21])
        for identity in ("cre:actor/item/1", "are:area/container/0/item/1",
                         "are:area/actor/0/item/1", "sto:shop/item/1"):
            self.assertEqual(by_id[identity]["item"], "synth001")
            self.assertNotIn("empty-item-reference", by_id[identity]["concerns"])

    def test_cli_world_scan_summary_counts_empty_rows_without_source_locations(self):
        root = Path(self.temp.name)
        catalog_path, world_path = root / "catalog.json", root / "world.json"
        write_json(catalog_path, self.catalog)
        before = snapshot(self.game)
        args = cli.parser().parse_args(["world", "scan", "--catalog", str(catalog_path),
                                        "--out", str(world_path)])
        summary = cli.run(args)
        self.assertEqual(snapshot(self.game), before)
        self.assertEqual(summary["empty_item_records"], 4)
        self.assertEqual(summary["source_records"], 8)
        self.assertEqual(set(summary), {"world_path", "fingerprint", "source_records",
                                        "target_candidates", "empty_item_records",
                                        "dangling_inventory_owners", "dangling_inventory_slots",
                                        "invalid_inventory_owners", "invalid_item_records",
                                        "unresolved_actor_storage"})
        self.assertEqual((summary["dangling_inventory_owners"], summary["dangling_inventory_slots"]), (0, 0))
        self.assertEqual(read_json(world_path)["fingerprint"], summary["fingerprint"])

    def test_empty_row_changes_fingerprint_but_existing_nonempty_row_binds(self):
        world = self.scan()
        altered = deepcopy(world)
        empty = next(row for row in altered["sources"] if row["id"] == "sto:shop/item/0")
        empty["record"]["charges"][1] += 1
        self.assertNotEqual(world_fingerprint(altered), world["fingerprint"])
        with self.assertRaisesRegex(ProfileError, "content changed"):
            validate_world(altered, self.catalog)
        plan = new_binding_plan(self.profile, self.catalog, world)
        plan["sources"] = [{"item": "synth001", "policy": "relocate",
                            "source_ids": ["are:area/container/0/item/1"], "registration_ids": []}]
        plan["targets"] = [{"id": "are:area/actor/0", "capacity": 1, "optional": False}]
        bound = bind_profile(self.profile, self.catalog, world, plan)
        source = bound["sources"][0]["source"]
        self.assertEqual(source["id"], "are:area/container/0/item/1")
        self.assertEqual((source["record"]["record_index"], source["record"]["charges"]),
                         (1, [7, 3, 2]))
        self.assertEqual(compile_campaign(self.profile, self.catalog, world, plan)["sources"][0]["id"],
                         source["id"])

    def test_forged_empty_concern_and_item_remapping_are_rejected(self):
        world = self.scan()
        for mutation in ("remove", "missing", "remap", "catalog"):
            forged = deepcopy(world)
            row = next(row for row in forged["sources"] if row["id"] == "sto:shop/item/0")
            if mutation == "remove":
                row["concerns"].remove("empty-item-reference")
            elif mutation == "missing":
                row["concerns"].remove("empty-item-reference")
                row["concerns"].append("missing-item-definition")
                row["concerns"].sort()
            elif mutation == "remap":
                row["item"] = "synth001"
            else:
                forged["resources"][0]["resref"] = ""
            forged["fingerprint"] = world_fingerprint(forged)
            with self.subTest(mutation=mutation), self.assertRaises(ProfileError):
                validate_world(forged, self.catalog)

    def test_empty_row_cannot_enter_binding_or_campaign_compiler(self):
        world = self.scan()
        plan = new_binding_plan(self.profile, self.catalog, world)
        plan["sources"] = [{"item": "synth001", "policy": "relocate",
                            "source_ids": ["sto:shop/item/0"], "registration_ids": []}]
        plan["targets"] = [{"id": "are:area/actor/0", "capacity": 1, "optional": False}]
        with self.assertRaises(ProfileError):
            bind_profile(self.profile, self.catalog, world, plan)
        with self.assertRaises(ProfileError):
            compile_campaign(self.profile, self.catalog, world, plan)
        plan["sources"][0]["item"] = ""
        with self.assertRaises(ProfileError):
            bind_profile(self.profile, self.catalog, world, plan)

    def test_rehashed_campaign_rejects_empty_item_even_without_concern(self):
        world = self.scan()
        plan = new_binding_plan(self.profile, self.catalog, world)
        plan["sources"] = [{"item": "synth001", "policy": "relocate",
                            "source_ids": ["cre:actor/item/1"], "registration_ids": []}]
        plan["targets"] = [{"id": "are:area/actor/0", "capacity": 1, "optional": False}]
        body = compile_campaign(self.profile, self.catalog, world, plan)
        for add_concern in (False, True):
            forged = deepcopy(body)
            source = forged["sources"][0]["record"]
            source["item"] = source["record"]["resref"] = ""
            if add_concern:
                source["concerns"] = sorted(source["concerns"] + ["empty-item-reference"])
            forged["revision"] = _digest({k: v for k, v in forged.items() if k != "revision"})
            with self.subTest(add_concern=add_concern), self.assertRaises(ProfileError):
                _validate_campaign(forged)

    def test_empty_row_still_participates_in_world_span_and_slot_checks(self):
        world = self.scan()
        for mutation in ("overlap", "slot"):
            forged = deepcopy(world)
            by_id = {row["id"]: row for row in forged["sources"]}
            if mutation == "overlap":
                by_id["sto:shop/item/1"]["record"]["byte_offset"] = by_id["sto:shop/item/0"]["record"]["byte_offset"]
            else:
                by_id["cre:actor/item/1"]["record"]["slots"] = [21]
            forged["fingerprint"] = world_fingerprint(forged)
            with self.subTest(mutation=mutation), self.assertRaises(ProfileError):
                validate_world(forged, self.catalog)


if __name__ == "__main__":
    unittest.main()
