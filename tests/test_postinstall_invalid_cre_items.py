"""Synthetic malformed CRE item names; no installed game or runtime is touched."""

from copy import deepcopy
from pathlib import Path
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
from flir_config.world_formats import parse_are, parse_cre, parse_sto
from test_postinstall_resources import make_game, snapshot
from test_postinstall_world_formats import item_record, make_are, make_cre, make_sto


INVALID_OWNER = "invalid-inventory-reference"
INVALID_ROW = "invalid-item-reference"
RAW_NAMES = (b"\x01\0\0\0\0\0\0\0", b"\xff\0\0\0\0\0\0\0",
             b"A\0B\0\0\0\0\0")


def malformed_record(raw_name, *, expiration=29, charges=(0, 5, 65535), flags=1):
    return raw_name + item_record("", expiration=expiration, charges=charges, flags=flags)[8:]


def source(world, identity):
    return next(row for row in world["sources"] if row["id"] == identity)


def target(world, identity):
    return next(row for row in world["targets"] if row["id"] == identity)


class InvalidCreatureItemFormatTests(unittest.TestCase):
    def test_opt_in_preserves_raw_names_payload_indexes_and_slots(self):
        records = [malformed_record(raw) for raw in RAW_NAMES]
        records += [item_record("", flags=1), item_record(flags=1)]
        cre = make_cre(records, gap=7)
        with self.assertRaises(ScanError):
            parse_cre(cre)
        parsed = parse_cre(cre, allow_invalid_items=True)
        self.assertEqual(len(parsed["items"]), 5)
        self.assertEqual(parsed["inventory_diagnostics"]["invalid_item_indexes"], [0, 1, 2])
        self.assertEqual(parsed["inventory_diagnostics"]["item_count"], 5)
        self.assertEqual(parsed["inventory_diagnostics"]["slot_words"][21:26], [0, 1, 2, 3, 4])
        for index, raw in enumerate(RAW_NAMES):
            row = parsed["items"][index]
            self.assertEqual(row["resref"], "")
            self.assertEqual(row["raw_resref_hex"], raw.hex())
            self.assertEqual((row["record_index"], row["slots"]), (index, [21 + index]))
            self.assertEqual((row["expiration"], row["charges"], row["flags"]),
                             (29, [0, 5, 65535], 1))
            self.assertEqual(cre[row["byte_offset"]:row["byte_offset"] + 20], records[index])
        empty, valid = parsed["items"][3:]
        self.assertEqual(empty["resref"], "")
        self.assertNotIn("raw_resref_hex", empty)
        self.assertEqual((valid["resref"], valid["record_index"], valid["slots"]),
                         ("synth001", 4, [25]))

    def test_embedded_opt_in_does_not_relax_area_containers_or_stores(self):
        malformed = malformed_record(RAW_NAMES[0])
        area = make_are(actors=[("Embedded", make_cre([malformed]))])
        with self.assertRaises(ScanError):
            parse_are(area)
        actor = parse_are(area, allow_invalid_items=True)["actors"][0]
        cre = actor["embedded_cre"]
        self.assertEqual(cre["inventory_diagnostics"]["invalid_item_indexes"], [0])
        self.assertEqual(cre["inventory_diagnostics"]["cre_offset"], actor["embedded_cre_offset"])
        self.assertEqual(cre["items"][0]["raw_resref_hex"], RAW_NAMES[0].hex())
        self.assertGreater(cre["items"][0]["byte_offset"], actor["embedded_cre_offset"])
        with self.assertRaises(ScanError):
            parse_are(make_are([malformed], [("Chest", 0, 1)]), allow_invalid_items=True)
        with self.assertRaises(ScanError):
            parse_sto(make_sto([(malformed, 1, 0)]))


class InvalidCreatureItemWorldTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="flir-invalid-cre-item-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.game = self.root / "synthetic-game"
        make_game(self.game)
        override = self.game / "override"
        self.direct = make_cre([*(malformed_record(raw) for raw in RAW_NAMES),
                                item_record("", flags=1), item_record(flags=1)])
        self.embedded = make_cre([malformed_record(RAW_NAMES[0])])
        (override / "actor.cre").write_bytes(self.direct)
        (override / "area.are").write_bytes(make_are(
            [], [("Empty Chest", 0, 0)],
            [("External", "ACTOR"), ("Embedded", self.embedded)]))
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

    def test_scan_keeps_every_row_and_flags_all_affected_owners(self):
        world = self.scan()
        self.assertEqual(world["schema"], "flir-world-catalog-v2")
        direct_target = target(world, "cre:actor")
        diag = direct_target["selector"]["inventory_diagnostics"]
        self.assertEqual(diag["invalid_item_indexes"], [0, 1, 2])
        self.assertEqual(diag["slot_words"][21:26], [0, 1, 2, 3, 4])
        self.assertIn(INVALID_OWNER, direct_target["concerns"])
        self.assertNotIn("dangling-inventory-reference", direct_target["concerns"])
        for index in range(5):
            row = source(world, f"cre:actor/item/{index}")
            self.assertEqual(row["record"]["inventory_diagnostics"], diag)
            self.assertIn(INVALID_OWNER, row["concerns"])
            self.assertEqual(row["record"]["slots"], [21 + index])
            if index < 3:
                self.assertEqual(row["record"]["raw_resref_hex"], RAW_NAMES[index].hex())
                self.assertIn(INVALID_ROW, row["concerns"])
                self.assertNotIn("empty-item-reference", row["concerns"])
            elif index == 3:
                self.assertNotIn("raw_resref_hex", row["record"])
                self.assertIn("empty-item-reference", row["concerns"])
            else:
                self.assertEqual(row["item"], "synth001")
                self.assertNotIn(INVALID_ROW, row["concerns"])
        embedded_target = target(world, "are:area/actor/1")
        self.assertEqual(embedded_target["selector"]["inventory_diagnostics"]["invalid_item_indexes"], [0])
        self.assertEqual(source(world, "are:area/actor/1/item/0")["record"]["raw_resref_hex"], RAW_NAMES[0].hex())
        external = target(world, "are:area/actor/0")
        self.assertEqual(external["selector"]["template_inventory_diagnostics"], diag)
        for row in (external, embedded_target):
            self.assertIn(INVALID_OWNER, row["concerns"])

    def test_cli_counts_invalid_records_and_direct_owners_without_external_alias(self):
        catalog_path = self.root / "catalog.json"
        world_path = self.root / "world.json"
        write_json(catalog_path, self.catalog)
        before = snapshot(self.game)
        args = cli.parser().parse_args(["world", "scan", "--catalog", str(catalog_path),
                                        "--out", str(world_path)])
        summary = cli.run(args)
        self.assertEqual(snapshot(self.game), before)
        self.assertEqual(summary["invalid_item_records"], 4)
        self.assertEqual(summary["invalid_inventory_owners"], 2)
        self.assertEqual(summary["dangling_inventory_owners"], 0)
        self.assertEqual(summary["dangling_inventory_slots"], 0)
        self.assertEqual(summary["source_records"], 7)

    def test_rehashed_world_cannot_launder_invalid_row_or_owner_evidence(self):
        original = self.scan()
        changes = {
            "remove_raw": lambda w: source(w, "cre:actor/item/0")["record"].pop("raw_resref_hex"),
            "invalid_raw": lambda w: source(w, "cre:actor/item/0")["record"].__setitem__("raw_resref_hex", "01"),
            "all_null_raw": lambda w: source(w, "cre:actor/item/0")["record"].__setitem__("raw_resref_hex", "0" * 16),
            "remove_row_concern": lambda w: source(w, "cre:actor/item/0")["concerns"].remove(INVALID_ROW),
            "remove_owner_concern": lambda w: source(w, "cre:actor/item/4")["concerns"].remove(INVALID_OWNER),
            "wrong_indexes": lambda w: target(w, "cre:actor")["selector"]["inventory_diagnostics"].__setitem__("invalid_item_indexes", [0, 2]),
            "unsorted_indexes": lambda w: target(w, "cre:actor")["selector"]["inventory_diagnostics"].__setitem__("invalid_item_indexes", [1, 0, 2]),
            "remove_external_marker": lambda w: target(w, "are:area/actor/0")["concerns"].remove(INVALID_OWNER),
            "remove_source_diagnostic": lambda w: source(w, "cre:actor/item/4")["record"].pop("inventory_diagnostics"),
        }
        for name, change in changes.items():
            forged = deepcopy(original)
            change(forged)
            with self.subTest(name=name), self.assertRaises(ProfileError):
                validate_world(self.rehash(forged), self.catalog)

    def test_owner_sources_and_targets_are_blocked_but_unrelated_records_work(self):
        world = self.scan()
        for source_id, target_id in (
            ("cre:actor/item/4", "are:area/container/0"),
            ("sto:shop/item/0", "cre:actor"),
            ("sto:shop/item/0", "are:area/actor/0"),
            ("sto:shop/item/0", "are:area/actor/1"),
        ):
            with self.subTest(source=source_id, target=target_id), self.assertRaises(ProfileError):
                bind_profile(self.profile, self.catalog, world, self.plan(world, source_id, target_id))
        clean = self.plan(world)
        self.assertEqual(bind_profile(self.profile, self.catalog, world, clean)["reward_units"], 1)
        self.assertEqual(len(compile_campaign(self.profile, self.catalog, world, clean)["sources"]), 1)

    def test_campaign_rejects_raw_item_name_without_marker_or_diagnostic(self):
        world = self.scan()
        forged = deepcopy(compile_campaign(self.profile, self.catalog, world, self.plan(world)))
        forged["sources"][0]["record"]["record"]["raw_resref_hex"] = RAW_NAMES[0].hex()
        forged["revision"] = _digest({key: value for key, value in forged.items() if key != "revision"})
        with self.assertRaises(ProfileError):
            _validate_campaign(forged)


if __name__ == "__main__":
    unittest.main()
