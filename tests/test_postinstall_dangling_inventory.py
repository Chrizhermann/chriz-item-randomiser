"""Synthetic dangling CRE slots; no installed game or live runtime is touched."""

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
from flir_config.world_formats import parse_are, parse_cre
from test_postinstall_resources import make_game, snapshot
from test_postinstall_world_formats import item_record, make_are, make_cre, make_sto


CONCERN = "dangling-inventory-reference"


def diagnostic(cre, *, offset=0):
    """Expected raw CRE inventory witness, with local header offsets."""
    slots_offset = int.from_bytes(cre[0x2B8:0x2BC], "little")
    return {
        "cre_offset": offset,
        "item_count": int.from_bytes(cre[0x2C0:0x2C4], "little"),
        "items_offset": int.from_bytes(cre[0x2BC:0x2C0], "little"),
        "item_slots_offset": slots_offset,
        "slot_words": [int.from_bytes(cre[slots_offset + slot * 2:slots_offset + slot * 2 + 2], "little")
                       for slot in range(40)],
    }


def source(world, identity):
    return next(row for row in world["sources"] if row["id"] == identity)


def target(world, identity):
    return next(row for row in world["targets"] if row["id"] == identity)


class DanglingInventoryFormatTests(unittest.TestCase):
    def test_default_parser_rejects_but_opt_in_preserves_zero_table_words(self):
        cre = make_cre([], slots={21: 0, 22: 1})
        with self.assertRaises(ScanError):
            parse_cre(cre)
        parsed = parse_cre(cre, allow_dangling_slots=True)
        self.assertEqual(parsed["items"], [])
        self.assertEqual(parsed["slots"][21:23], [0, 1])
        self.assertIsNone(parsed["slots"][20])
        self.assertEqual(parsed["inventory_diagnostics"], diagnostic(cre))
        self.assertEqual(parsed["inventory_diagnostics"]["slot_words"][38:], [1000, 65535])

    def test_mixed_valid_and_dangling_slots_do_not_invent_item_records(self):
        cre = make_cre([item_record(flags=1)], slots={21: 0, 22: 4})
        parsed = parse_cre(cre, allow_dangling_slots=True)
        self.assertEqual(len(parsed["items"]), 1)
        self.assertEqual(parsed["items"][0]["slots"], [21])
        self.assertEqual(parsed["slots"][22], 4)
        self.assertEqual(parsed["inventory_diagnostics"], diagnostic(cre))
        self.assertEqual(parsed["inventory_diagnostics"]["slot_words"][22], 4)

    def test_embedded_cre_rebases_only_cre_offset_in_diagnostic(self):
        cre = make_cre([], slots={21: 0, 22: 1}, gap=7)
        area = make_are(actors=[("Embedded", cre)], gap=5)
        with self.assertRaises(ScanError):
            parse_are(area)
        actor = parse_are(area, allow_dangling_slots=True)["actors"][0]
        self.assertEqual(actor["embedded_cre"]["inventory_diagnostics"],
                         diagnostic(cre, offset=actor["embedded_cre_offset"]))


class DanglingInventoryWorldTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="flir-dangling-inventory-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.game = Path(temporary.name) / "synthetic-game"
        make_game(self.game)
        override = self.game / "override"
        self.cre = make_cre([item_record(flags=1)], slots={21: 0, 22: 2})
        self.embedded = make_cre([], slots={21: 0, 22: 1}, gap=3)
        (override / "actor.cre").write_bytes(self.cre)
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

    def plan(self, world, source_id="sto:shop/item/0", target_id="are:area/container/0"):
        plan = new_binding_plan(self.profile, self.catalog, world)
        plan["sources"] = [{"item": "synth001", "policy": "relocate",
                            "source_ids": [source_id], "registration_ids": []}]
        plan["targets"] = [{"id": target_id, "capacity": 1, "optional": False}]
        return plan

    def rehash(self, world):
        world["fingerprint"] = world_fingerprint(world)
        return world

    def test_scan_retains_direct_embedded_and_external_diagnostics(self):
        world = self.scan()
        self.assertEqual(world["schema"], "flir-world-catalog-v2")
        direct = diagnostic(self.cre)
        direct_target = target(world, "cre:actor")
        direct_source = source(world, "cre:actor/item/0")
        self.assertEqual(direct_target["selector"]["inventory_diagnostics"], direct)
        self.assertEqual(direct_source["record"]["inventory_diagnostics"], direct)
        self.assertEqual(direct_source["record"]["slots"], [21])
        self.assertIn(CONCERN, direct_source["concerns"])
        self.assertIn(CONCERN, direct_target["concerns"])
        external = target(world, "are:area/actor/0")
        self.assertEqual(external["selector"]["template_inventory_diagnostics"], direct)
        self.assertIn(CONCERN, external["concerns"])
        embedded = target(world, "are:area/actor/1")
        self.assertEqual(embedded["selector"]["inventory_diagnostics"],
                         diagnostic(self.embedded, offset=embedded["selector"]["embedded_cre_offset"]))
        self.assertIn(CONCERN, embedded["concerns"])
        self.assertNotIn("are:area/actor/1/item/0", {row["id"] for row in world["sources"]})
        self.assertEqual(len(world["sources"]), 2)

    def test_world_rejects_rehashed_missing_or_inconsistent_evidence(self):
        original = self.scan()
        changes = {
            "v1_schema": lambda w: w.update(schema="flir-world-catalog-v1"),
            "missing_target_concern": lambda w: target(w, "cre:actor")["concerns"].remove(CONCERN),
            "missing_source_concern": lambda w: source(w, "cre:actor/item/0")["concerns"].remove(CONCERN),
            "missing_external_concern": lambda w: target(w, "are:area/actor/0")["concerns"].remove(CONCERN),
            "missing_direct_diagnostic": lambda w: target(w, "cre:actor")["selector"].pop("inventory_diagnostics"),
            "missing_external_diagnostic": lambda w: target(w, "are:area/actor/0")["selector"].pop("template_inventory_diagnostics"),
            "source_owner_mismatch": lambda w: source(w, "cre:actor/item/0")["record"]["inventory_diagnostics"]["slot_words"].__setitem__(22, 3),
            "short_slot_table": lambda w: target(w, "cre:actor")["selector"]["inventory_diagnostics"]["slot_words"].pop(),
            "count_hides_dangling": lambda w: target(w, "cre:actor")["selector"]["inventory_diagnostics"].__setitem__("item_count", 3),
            "wrong_embedded_offset": lambda w: target(w, "are:area/actor/1")["selector"]["inventory_diagnostics"].__setitem__("cre_offset", 0),
        }
        for name, change in changes.items():
            forged = deepcopy(original)
            change(forged)
            with self.subTest(name=name), self.assertRaises(ProfileError):
                validate_world(self.rehash(forged), self.catalog)

    def test_diagnostic_spans_and_record_table_must_match_owner(self):
        original = self.scan()
        for field, value, message in (
            ("item_slots_offset", 0x2D3, "slots lie outside"),
            ("item_slots_offset", len(self.cre) - 79, "slots lie outside"),
            ("items_offset", 0x2D3, "item and slot sections"),
            ("items_offset", len(self.cre), "item and slot sections"),
        ):
            forged = deepcopy(original)
            changed = deepcopy(target(forged, "cre:actor")["selector"]["inventory_diagnostics"])
            changed[field] = value
            target(forged, "cre:actor")["selector"]["inventory_diagnostics"] = changed
            source(forged, "cre:actor/item/0")["record"]["inventory_diagnostics"] = deepcopy(changed)
            target(forged, "are:area/actor/0")["selector"]["template_inventory_diagnostics"] = deepcopy(changed)
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ProfileError, message):
                validate_world(self.rehash(forged), self.catalog)
        missing = deepcopy(original)
        missing["sources"] = [row for row in missing["sources"] if row["id"] != "cre:actor/item/0"]
        with self.assertRaisesRegex(ProfileError, "physical item records"):
            validate_world(self.rehash(missing), self.catalog)
        stripped = deepcopy(original)
        source(stripped, "cre:actor/item/0")["record"].pop("inventory_diagnostics")
        with self.assertRaisesRegex(ProfileError, "disagree with its owner"):
            validate_world(self.rehash(stripped), self.catalog)

    def test_cli_counts_direct_owners_and_slots_once(self):
        catalog_path = self.root / "catalog.json"
        world_path = self.root / "world.json"
        write_json(catalog_path, self.catalog)
        before = snapshot(self.game)
        args = cli.parser().parse_args(["world", "scan", "--catalog", str(catalog_path),
                                        "--out", str(world_path)])
        summary = cli.run(args)
        self.assertEqual(snapshot(self.game), before)
        # One CRE template and one embedded actor; the external actor is an alias.
        self.assertEqual(summary["dangling_inventory_owners"], 2)
        self.assertEqual(summary["dangling_inventory_slots"], 3)
        self.assertEqual(summary["source_records"], 2)

    def test_affected_source_and_direct_embedded_external_targets_cannot_bind(self):
        world = self.scan()
        for source_id, target_id in (
            ("cre:actor/item/0", "are:area/container/0"),
            ("sto:shop/item/0", "cre:actor"),
            ("sto:shop/item/0", "are:area/actor/0"),
            ("sto:shop/item/0", "are:area/actor/1"),
        ):
            with self.subTest(source=source_id, target=target_id), self.assertRaises(ProfileError):
                bind_profile(self.profile, self.catalog, world, self.plan(world, source_id, target_id))
        clean = self.plan(world)
        self.assertEqual(bind_profile(self.profile, self.catalog, world, clean)["reward_units"], 1)
        self.assertEqual(len(compile_campaign(self.profile, self.catalog, world, clean)["sources"]), 1)

    def test_campaign_validator_rejects_diagnostic_even_with_marker_removed(self):
        world = self.scan()
        clean = compile_campaign(self.profile, self.catalog, world, self.plan(world))
        direct_diag = target(world, "cre:actor")["selector"]["inventory_diagnostics"]
        for location in ("source", "target"):
            forged = deepcopy(clean)
            if location == "source":
                forged["sources"][0]["record"]["record"]["inventory_diagnostics"] = deepcopy(direct_diag)
            else:
                forged["targets"][0]["record"]["selector"]["inventory_diagnostics"] = deepcopy(direct_diag)
            forged["revision"] = _digest({key: value for key, value in forged.items() if key != "revision"})
            with self.subTest(location=location), self.assertRaises(ProfileError):
                _validate_campaign(forged)


if __name__ == "__main__":
    unittest.main()
