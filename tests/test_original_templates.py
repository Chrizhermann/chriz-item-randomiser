"""Offline checks for lossless original Randomiser template drafts."""

from collections import defaultdict
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from flir_config.templates import (DEFAULT_OPTIONS, ROOT, _table_rows, build_template,  # noqa: E402
                                   campaign_tables, list_templates, recommended_preset,
                                   template_tables)


class OriginalTemplateTests(unittest.TestCase):
    def test_descriptors_and_complete_table_totals(self):
        self.assertEqual(
            [row["id"] for row in list_templates()],
            ["bg1-original", "bg2-original", "eet-original", "sod-original", "bg1-sod-original",
             "eet-sod-original", "bg1-boss-loot", "eet-boss-loot", "bg1-sod-boss-loot",
             "eet-sod-boss-loot"],
        )
        # Chriz Item Randomiser has no modes; its presets are not named after them.
        for row in list_templates():
            self.assertNotRegex(row["name"] + " " + row["description"], r"(?i)\bmode\b")
        self.assertEqual(build_template("bg1-original")["choices"],
                         {"items": "standard", "targets": "standard"})
        for identity, pools, items, targets in (
            ("bg1-original", "standard", 212, 218),
            ("bg1-original", "extended", 380, 397),
            ("bg2-original", "standard", 293, 309),
            ("bg2-original", "extended", 546, 574),
            ("eet-original", "standard", 505, 527),
            ("eet-original", "extended", 926, 971),
            ("bg1-boss-loot", "standard", 214, 216),
            ("bg1-boss-loot", "extended", 609, 594),
            ("eet-boss-loot", "standard", 507, 525),
            ("eet-boss-loot", "extended", 1155, 1168),
            ("sod-original", "standard", 74, 74),
            ("sod-original", "extended", 74, 74),
            ("bg1-sod-original", "standard", 286, 292),
            ("eet-sod-original", "standard", 579, 601),
            ("eet-sod-original", "extended", 1000, 1045),
            ("bg1-sod-boss-loot", "standard", 288, 290),
            ("eet-sod-boss-loot", "extended", 1229, 1242),
        ):
            with self.subTest(identity=identity, pools=pools):
                draft = build_template(identity, items=pools, targets=pools)
                self.assertEqual(draft["runtime_mode"], "new-game")
                self.assertEqual(draft["counts"]["items"], items)
                self.assertEqual(draft["counts"]["targets"], targets)
                self.assertEqual(len({row["id"] for row in draft["items"]}), items)
                self.assertEqual(len({row["id"] for row in draft["targets"]}), targets)
                self.assertIn("draft", draft["state"])

    def test_repeated_rewards_and_authored_endpoints_survive(self):
        draft = build_template("bg1-original")
        bracers = [row for row in draft["items"] if row["resref"] == "brac01"
                   and row["tier"] == "24"]
        self.assertEqual([row["occurrence"] for row in bracers], [1, 2])
        self.assertEqual([row["token"] for row in bracers], ["01", "02"])
        self.assertTrue(all(row["source_ref"].startswith("lists/items/base/bg1.2da:")
                            for row in bracers))
        self.assertTrue(all(row["endpoint_id"] and row["endpoint_source_ref"]
                            for row in draft["targets"]))
        self.assertEqual({row["endpoint_kind"] for row in draft["targets"]},
                         {"container", "creature", "group"})
        self.assertTrue(all(row["runtime_support"] == "needs-group-target-adapter"
                            for row in draft["targets"] if row["endpoint_kind"] == "group"))

    def test_option_gates_preserve_rows_and_behavior_options(self):
        off = build_template("bg2-original")
        on = build_template("bg2-original", options={
            "scrolls": True, "kangaxx": True, "flail_heads": True,
            "loss_mode": "some", "loss_percent": 35,
            "unrandomized_percent": 20, "totsc_tome": True,
        })
        self.assertEqual(on["counts"]["items"], off["counts"]["items"])
        self.assertEqual(on["counts"]["targets"], off["counts"]["targets"])
        self.assertEqual(on["counts"]["enabled_items"], on["counts"]["items"])
        self.assertEqual(on["counts"]["enabled_targets"], on["counts"]["targets"])
        self.assertEqual(on["options"]["loss_mode"], "some")
        self.assertEqual(on["options"]["unrandomized_percent"], 20)
        for tier, gate in (("s4", "scrolls"), ("fa", "flail_heads"),
                           ("ka", "kangaxx")):
            self.assertTrue(any(row["tier"] == tier and row["option_gate"] == gate
                                and not row["enabled"] for row in off["items"]))

    def test_item_and_target_pools_are_independent(self):
        draft = build_template("eet-original", items="extended")
        self.assertEqual(draft["counts"]["items"], 926)
        self.assertEqual(draft["counts"]["targets"], 527)
        self.assertTrue(any(row["family"] == "mode2" for row in draft["items"]))
        self.assertTrue(all(row["family"] == "base" for row in draft["targets"]))
        reverse = build_template("bg2-original", items="standard", targets="extended")
        self.assertEqual(reverse["counts"]["items"], 293)
        self.assertEqual(reverse["counts"]["targets"], 574)
        self.assertTrue(all(row["endpoint_id"] is None for row in reverse["targets"]
                            if row["family"] == "mode2"))
        self.assertTrue(all(row["runtime_support"] == "needs-endpoint-adapter"
                            for row in reverse["targets"] if row["family"] == "mode2"))

    def test_recommended_preset_is_standard_without_loss_plus_flail_heads_and_tome(self):
        preset = recommended_preset("eet")
        self.assertEqual((preset["id"], preset["items"], preset["targets"]),
                         ("eet-original", "standard", "standard"))
        self.assertEqual(preset["options"], DEFAULT_OPTIONS | {"flail_heads": True, "totsc_tome": True})
        self.assertEqual(preset["options"]["loss_mode"], "none")
        draft = build_template(preset["id"], items=preset["items"], targets=preset["targets"],
                               options=preset["options"])
        self.assertTrue(any(row["option_gate"] == "flail_heads" and row["enabled"] for row in draft["items"]))
        self.assertFalse(any(row["option_gate"] in ("scrolls", "kangaxx") and row["enabled"]
                             for row in draft["items"]))
        self.assertEqual(recommended_preset("bg1")["id"], "bg1-original")
        preset["options"]["kangaxx"] = True
        self.assertFalse(recommended_preset("eet")["options"]["kangaxx"])
        with self.assertRaises(ValueError):
            recommended_preset("iwd")

    def test_sod_tables_join_the_presets_that_name_them(self):
        self.assertEqual(campaign_tables("eet-sod"), ("bg1", "sod", "bg2"))
        self.assertEqual(template_tables("bg1-sod-boss-loot"), ("bg1", "sod"))
        self.assertEqual(template_tables("eet-original"), ("bg1", "bg2"))
        sod = build_template("sod-original")
        self.assertEqual(sod["campaigns"], ["sod"])
        self.assertEqual(sod["name"], "SoD: Chriz Item Randomiser's lists")
        for kind in ("items", "targets"):
            self.assertTrue(all(row["campaign"] == "sod" and row["family"] == "base" and
                                row["id"].startswith("sod:base:") and
                                row["source_ref"].startswith(f"lists/{'items' if kind == 'items' else 'locations'}/base/sod.2da:")
                                and row["requires_game_conversion"] for row in sod[kind]))
        # Chapter sections, apart from the BG1 and BG2 tiers; no option gates them.
        self.assertEqual({row["tier"] for row in sod["items"]}, {"32", "33", "34", "35", "36"})
        self.assertTrue(all(row["enabled"] and row["option_gate"] is None
                            for row in sod["items"] + sod["targets"]))
        # The SoD tables have no authored endpoints: a place's target names its kind.
        self.assertTrue(all(row["endpoint_id"] is None for row in sod["targets"]))
        # Each item row has its home place, paired by its Ident.
        items = {row["ident"]: row for row in sod["items"]}
        places = {row["ident"]: row for row in sod["targets"]}
        self.assertEqual(set(items), set(places))
        self.assertEqual(len(items), len(sod["items"]))
        for ident, row in items.items():
            self.assertEqual((places[ident]["area"], places[ident]["tier"]),
                             (row["source"].rsplit(".", 1)[0], row["tier"]))
        combined = build_template("eet-sod-original")
        self.assertEqual(combined["name"], "EET + SoD: Item Randomiser's lists and the SoD lists")
        self.assertEqual(combined["campaigns"], ["bg1", "sod", "bg2"])
        for kind in ("items", "targets"):
            self.assertEqual([row for row in combined[kind] if row["campaign"] == "sod"], sod[kind])
            self.assertEqual([row for row in combined[kind] if row["campaign"] != "sod"],
                             build_template("eet-original")[kind])
        boss = {row["id"]: row for row in list_templates()}["eet-sod-boss-loot"]
        self.assertIn("For BG1; SoD keeps Chriz Item Randomiser's lists and BG2 keeps Item "
                      "Randomiser's lists.", boss["description"])
        self.assertEqual([row for row in build_template("bg1-sod-boss-loot")["items"]
                          if row["campaign"] == "sod"], sod["items"])
        self.assertEqual(recommended_preset("bg1-sod")["id"], "bg1-sod-original")

    def test_boss_loot_replaces_only_the_bg1_tables(self):
        draft = build_template("eet-boss-loot", items="extended", targets="extended")
        self.assertEqual(draft["name"], "EET: Boss Loot by aShuN")
        original = build_template("bg2-original", items="extended", targets="extended")
        for kind in ("items", "targets"):
            bg1 = [row for row in draft[kind] if row["campaign"] == "bg1"]
            self.assertTrue(bg1)
            self.assertTrue(all(row["id"].startswith("bg1:boss-loot:") and
                                row["source_ref"].startswith("lists/presets/boss-loot/")
                                for row in bg1))
            self.assertEqual([row for row in draft[kind] if row["campaign"] == "bg2"], original[kind])

    def test_boss_loot_places_take_the_endpoint_of_the_same_place(self):
        endpoints = {(row["target"], row["area"]): row["endpoint_id"]
                     for row in build_template("bg1-original")["targets"]}
        without = set()
        for row in build_template("bg1-boss-loot")["targets"]:
            place = (row["target"], row["area"])
            self.assertEqual(row["endpoint_id"], endpoints.get(place))
            if row["endpoint_id"] is None:
                without.add(place)
                self.assertEqual(row["runtime_support"], "needs-endpoint-adapter")
        # Places the original standard tables do not use; they are bound by name.
        self.assertEqual(without, {
            ("nemphr.cre", "ar0813"), ("yago.cre", "ar0135"), ("container1", "ar3802"),
            ("baerin.cre", "ar3500"), ("raemon.cre", "ar1901"), ("lindin.cre", "ar3500"),
            ("spelunkers.flg", "ar5506"), ("tent.flg", "ar1901"), ("assassins1.flg", "ar5000"),
            ("assassins2.flg", "ar4000")})

    def test_boss_loot_keeps_one_item_and_one_place_per_pairing(self):
        def rows(folder, kind, columns):
            for family in ("base", "mode2"):
                for line, fields in _table_rows(ROOT / folder / kind / family / "bg1.2da", columns):
                    yield f"{family}:{line}", [field.lower() for field in fields]

        original_items = {fields[5]: (fields[0], fields[2])
                          for _, fields in rows("lists", "items", 7) if fields[5] != "x"}
        preset = "lists/presets/boss-loot"
        for kind, columns in (("items", 7), ("locations", 6)):
            seen = defaultdict(list)
            for line, fields in rows(preset, kind, columns):
                if fields[columns - 2] != "x":
                    seen[fields[columns - 2]].append(line)
            self.assertEqual({ident: lines for ident, lines in seen.items() if len(lines) > 1}, {})
        for line, fields in rows(preset, "items", 7):
            if fields[5] in original_items:
                self.assertEqual((fields[0], fields[2]), original_items[fields[5]], line)
        # The Tales of the Sword Coast tome option finds Durlag's tome by its ident.
        tome = [row for row in build_template("bg1-boss-loot")["items"] if row["ident"] == "b2c"]
        self.assertEqual([(row["resref"], row["source"]) for row in tome], [("book08", "ar0504.are")])

    def test_boss_loot_marks_items_that_only_other_mods_add(self):
        optional = {row["resref"]: row["optional_mod"]
                    for row in build_template("bg1-boss-loot", items="extended")["items"]
                    if row["optional_mod"]}
        self.assertEqual({mod for mod in optional.values()}, {"Rogue Rebalancing", "IWDification"})
        self.assertTrue(all((mod == "Rogue Rebalancing") == name.startswith("rr#")
                            for name, mod in optional.items()))
        for identity, pools in (("bg1-boss-loot", "standard"), ("eet-original", "extended")):
            self.assertFalse(any(row["optional_mod"]
                                 for row in build_template(identity, items=pools)["items"]))

    def test_invalid_choices_fail_closed(self):
        with self.assertRaises(ValueError):
            build_template("unknown")
        with self.assertRaises(ValueError):
            build_template("eet-mode1")
        with self.assertRaises(ValueError):
            build_template("bg1-original", items="mode1+mode2")
        with self.assertRaises(ValueError):
            build_template("bg1-original", options={"scrolls": 1})
        with self.assertRaises(ValueError):
            build_template("bg1-original", options={"loss_percent": 101})


if __name__ == "__main__":
    unittest.main()
