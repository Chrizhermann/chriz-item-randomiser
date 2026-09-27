"""Offline checks for lossless original Randomiser template drafts."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from flir_config.templates import build_template, list_templates  # noqa: E402


class OriginalTemplateTests(unittest.TestCase):
    def test_descriptors_and_complete_table_totals(self):
        self.assertEqual(
            [row["id"] for row in list_templates()],
            ["bg1-mode1", "bg1-mode1-plus-mode2", "bg2-mode1",
             "bg2-mode1-plus-mode2", "eet-mode1", "eet-mode1-plus-mode2"],
        )
        for identity, items, targets in (
            ("bg1-mode1", 212, 218),
            ("bg1-mode1-plus-mode2", 380, 397),
            ("bg2-mode1", 293, 309),
            ("bg2-mode1-plus-mode2", 546, 574),
            ("eet-mode1", 505, 527),
            ("eet-mode1-plus-mode2", 926, 971),
        ):
            with self.subTest(identity=identity):
                draft = build_template(identity)
                self.assertEqual(draft["runtime_mode"], "mode1")
                self.assertEqual(draft["counts"]["items"], items)
                self.assertEqual(draft["counts"]["targets"], targets)
                self.assertEqual(len({row["id"] for row in draft["items"]}), items)
                self.assertEqual(len({row["id"] for row in draft["targets"]}), targets)
                self.assertIn("draft", draft["state"])

    def test_repeated_rewards_and_authored_endpoints_survive(self):
        draft = build_template("bg1-mode1")
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
        off = build_template("bg2-mode1")
        on = build_template("bg2-mode1", options={
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
        draft = build_template("eet-mode1", items="mode1+mode2")
        self.assertEqual(draft["counts"]["items"], 926)
        self.assertEqual(draft["counts"]["targets"], 527)
        self.assertTrue(any(row["family"] == "mode2" for row in draft["items"]))
        self.assertTrue(all(row["family"] == "base" for row in draft["targets"]))
        reverse = build_template("bg2-mode1-plus-mode2", items="mode1")
        self.assertEqual(reverse["counts"]["items"], 293)
        self.assertEqual(reverse["counts"]["targets"], 574)
        self.assertTrue(all(row["endpoint_id"] is None for row in reverse["targets"]
                            if row["family"] == "mode2"))
        self.assertTrue(all(row["runtime_support"] == "needs-endpoint-adapter"
                            for row in reverse["targets"] if row["family"] == "mode2"))

    def test_invalid_choices_fail_closed(self):
        with self.assertRaises(ValueError):
            build_template("unknown")
        with self.assertRaises(ValueError):
            build_template("bg1-mode1", items="mode2")
        with self.assertRaises(ValueError):
            build_template("bg1-mode1", options={"scrolls": 1})
        with self.assertRaises(ValueError):
            build_template("bg1-mode1", options={"loss_percent": 101})


if __name__ == "__main__":
    unittest.main()
