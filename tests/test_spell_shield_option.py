"""Component 560 campaign quantities and source evidence, on temporary data."""

from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from flir_config.campaigns import compile_campaign, _validate_campaign
from flir_config.original_catalog import resolve_template
from flir_config.original_configuration import _original_source, build_original_configuration
from flir_config.original_options import (SPELL_SHIELD_SOURCE, is_spell_shield_row,
                                          spell_shield_spec, patch_spell_shield_stock)
from flir_config.profiles import ProfileError, _digest, edit_profile, new_profile
from flir_config.resources import scan_game
from flir_config.sources import bind_profile, new_binding_plan, scan_world
from flir_config.templates import DEFAULT_OPTIONS
from flir_config.world_formats import parse_sto
from test_postinstall_resources import make_game, make_itm, mark_eet, snapshot
from test_postinstall_world_formats import item_record, make_are, make_sto
from test_original_configuration import source_row
from sod_table_fixtures import sod_tables


class SpellShieldOptionTests(unittest.TestCase):
    def fixture(self, quantity=1, infinite=0, absent=False, *, randomized=True, anchor_first=False):
        temp = tempfile.TemporaryDirectory(prefix="flir-spell-shield-")
        self.addCleanup(temp.cleanup)
        game = Path(temp.name) / "game"
        make_game(game, [("SCRL8X", make_itm()), ("SYNTH001", make_itm()), ("SCRLAL", make_itm())])
        mark_eet(game)
        rows = [(item_record("synth001", expiration=0, flags=1), 1, 0)]
        if anchor_first:
            rows.insert(0, (item_record("scrlal", expiration=0, flags=1), 1, 0))
        if not absent:
            rows.append((item_record("scrl8x", expiration=0, charges=(1, 1, 0), flags=0), quantity, infinite))
        raw = bytes(make_sto(rows))
        (game / "override" / "udduer01.sto").write_bytes(raw)
        (game / "override" / "ar1202.are").write_bytes(make_are([], [("Container 5", 0, 0)]))
        catalog = scan_game(game)
        world = scan_world(catalog)
        profile = edit_profile(new_profile(catalog, "spell-shield", "Spell Shield"), catalog,
                               include=["scrl8x"] if randomized else ["synth001"])
        options = DEFAULT_OPTIONS | {"more_spell_shield": True, "scrolls": randomized}
        draft = resolve_template(catalog, world, "bg2-original", items="extended", targets="extended", options=options)
        row = next(row for row in draft["items"] if is_spell_shield_row(row))
        spec = spell_shield_spec(world, randomized=randomized)
        plan = new_binding_plan(profile, catalog, world)
        plan.update(original_template_id="bg2-original", original_choices=draft["choices"],
                    original_options=options, target_registrations=[], spell_shield=spec)
        if randomized:
            registration = _original_source(row, spec["resource_sha256"], spec["payload"],
                                            [spec["original_source_id"]] if spec["original_source_id"] else [])
            registration["copies"] = spec["quantity"]
            plan["registrations"] = [registration]
            plan["sources"] = [{"item": "scrl8x", "policy": "relocate", "source_ids": [],
                                "registration_ids": [SPELL_SHIELD_SOURCE]}]
        else:
            plan["sources"] = [{"item": "synth001", "policy": "relocate",
                                "source_ids": [f"sto:udduer01/item/{int(anchor_first)}"], "registration_ids": []}]
        plan["targets"] = [{"id": "are:ar1202/container/0", "capacity": max(3, quantity), "optional": False}]
        return game, catalog, world, profile, plan, raw, draft

    def test_existing_zero_larger_and_infinite_stock_feed_exact_reward_quantity(self):
        for quantity, infinite in ((0, 0), (1, 0), (3, 0), (7, 0), (0, 1)):
            with self.subTest(quantity=quantity, infinite=infinite):
                game, catalog, world, profile, plan, raw, _ = self.fixture(quantity, infinite)
                before, files = deepcopy(world), snapshot(game)
                bound = bind_profile(profile, catalog, world, plan)
                manifest = compile_campaign(profile, catalog, world, plan)
                source = manifest["sources"][0]
                self.assertEqual(bound["reward_units"], max(3, quantity))
                self.assertEqual(source["quantity"], max(3, quantity))
                self.assertEqual(source["record"]["kind"], "store")
                self.assertEqual(source["record"]["record"], plan["spell_shield"]["payload"])
                self.assertEqual(source["record"]["physical_source_ids"], ["sto:udduer01/item/1"])
                self.assertEqual(manifest["world_fingerprint"], world["fingerprint"])
                self.assertEqual(plan["spell_shield"]["original_record"]["quantity"], quantity)
                self.assertEqual(world, before)
                self.assertEqual(snapshot(game), files)
                self.assertEqual(parse_sto(patch_spell_shield_stock(raw))["items"][1]["quantity"], source["quantity"])

    def test_absent_stock_has_three_authored_payloads_and_no_fabricated_physical_id(self):
        _, catalog, world, profile, plan, raw, draft = self.fixture(absent=True)
        manifest = compile_campaign(profile, catalog, world, plan)
        source = manifest["sources"][0]
        self.assertEqual(source["record"]["physical_source_ids"], [])
        self.assertEqual(source["quantity"], 3)
        self.assertEqual(source["record"]["record"],
                         {"resref": "scrl8x", "expiration": 0, "charges": [0, 0, 0], "flags": 1})
        self.assertEqual(plan["spell_shield"]["record_index"], 1)
        self.assertEqual(len(parse_sto(patch_spell_shield_stock(raw))["items"]), 2)
        draft["items"] = [row for row in draft["items"] if is_spell_shield_row(row)]
        draft["targets"] = [row for row in draft["targets"] if row["id"] == "bg2:base:target:8"]
        with patch("flir_config.original_configuration.resolve_template", return_value=draft):
            built = build_original_configuration(catalog, world, "bg2-original", items="extended", targets="extended")
        self.assertEqual(built["plan"]["registrations"][0]["copies"], 3)
        self.assertEqual(built["plan"]["spell_shield"], plan["spell_shield"])

    def test_option_remains_independent_when_scroll_randomization_is_disabled(self):
        _, catalog, world, profile, plan, _, draft = self.fixture(randomized=False)
        manifest = compile_campaign(profile, catalog, world, plan)
        self.assertEqual(manifest["spell_shield"]["source_id"], "")
        self.assertEqual(manifest["spell_shield"]["quantity"], 3)
        self.assertEqual(sum(row["quantity"] for row in manifest["sources"]), 1)
        draft["items"] = [source_row("other", item="synth001", resource="sto:udduer01",
                                      source_ids=["sto:udduer01/item/0"])]
        draft["targets"] = [row for row in draft["targets"] if row["id"] == "bg2:base:target:8"]
        with patch("flir_config.original_configuration.resolve_template", return_value=draft):
            built = build_original_configuration(catalog, world, "bg2-original", items="extended", targets="extended")
        self.assertEqual(built["plan"]["spell_shield"]["source_id"], "")

    def test_forged_quantity_original_payload_or_disabled_option_is_rejected(self):
        _, catalog, world, profile, plan, _, _ = self.fixture()
        for field, value in (("quantity", 4), ("original_source_id", "sto:udduer01/item/0")):
            altered = deepcopy(plan)
            altered["spell_shield"][field] = value
            with self.assertRaisesRegex(ProfileError, "Spell Shield"):
                bind_profile(profile, catalog, world, altered)
        altered = deepcopy(plan)
        altered["original_options"]["more_spell_shield"] = False
        with self.assertRaisesRegex(ProfileError, "Spell Shield"):
            bind_profile(profile, catalog, world, altered)
        manifest = compile_campaign(profile, catalog, world, plan)
        manifest["sources"][0]["quantity"] = 4
        manifest["revision"] = _digest({key: value for key, value in manifest.items() if key != "revision"})
        with self.assertRaisesRegex(ProfileError, "Spell Shield"):
            _validate_campaign(manifest)

    def test_randomised_stock_follows_whether_the_preset_reads_bg2s_tables(self):
        _, catalog, world, profile, plan, _, _ = self.fixture(randomized=False)
        manifest = compile_campaign(profile, catalog, world, plan)
        manifest["original_options"]["scrolls"] = True
        # The scroll's row is in BG2's extended table: only a preset reading it randomises it,
        # also one that reads Siege of Dragonspear's tables (synthetic ones here).
        with sod_tables():
            for template, randomised in (("bg1-original", False), ("bg1-boss-loot", False),
                                         ("bg2-original", True), ("eet-original", True),
                                         ("sod-original", False), ("bg1-sod-original", False),
                                         ("eet-sod-original", True), ("eet-sod-boss-loot", True)):
                with self.subTest(template=template):
                    altered = deepcopy(manifest)
                    altered["original_template_id"] = template
                    altered["revision"] = _digest({key: value for key, value in altered.items()
                                                   if key != "revision"})
                    if randomised:
                        with self.assertRaisesRegex(ProfileError, "Spell Shield source does not match"):
                            _validate_campaign(altered)
                    else:
                        _validate_campaign(altered)

    def test_duplicate_stock_keeps_first_row_option_but_cannot_silently_drop_rewards(self):
        _, _, world, _, plan, _, _ = self.fixture()
        duplicate = deepcopy(next(row for row in world["sources"] if row["item"] == "scrl8x"))
        duplicate["id"] = "sto:udduer01/item/2"
        duplicate["record"].update(record_index=2, quantity=9)
        world["sources"].append(duplicate)
        independent = spell_shield_spec(world, randomized=False)
        self.assertEqual(independent["quantity"], 3)
        self.assertEqual(independent["original_record"], plan["spell_shield"]["original_record"])
        with self.assertRaisesRegex(ProfileError, "duplicate shop-source"):
            spell_shield_spec(world, randomized=True)


if __name__ == "__main__":
    unittest.main()
