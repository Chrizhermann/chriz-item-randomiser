"""Places on creatures the runtime cannot tell apart are left out; synthetic games only.

The runtime finds a place's creature by its script name in the area. A creature
without a name of its own (the engine's blank, None), or with one that another
actor of the area has too, would never get its reward (OWNER_AMBIGUOUS): a group
place leaves it out of its members, and a place on it alone is left out and named.
"""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from flir_config.campaigns import write_campaign  # noqa: E402
from flir_config.native_export import export_native  # noqa: E402
from flir_config.original_configuration import fit_original_configuration  # noqa: E402
from flir_config.resources import scan_game  # noqa: E402
from flir_config.sources import scan_world  # noqa: E402
from flir_config.templates import DEFAULT_OPTIONS  # noqa: E402
from test_original_configuration import source_row, target_row  # noqa: E402
from test_postinstall_resources import make_game  # noqa: E402
from test_postinstall_world_formats import make_are, make_cre  # noqa: E402
from test_unreadable_resources import droppable, reward  # noqa: E402

ALIKE = "Native target needs a unique actor script name in its area: original-target:"


def named(script_name: bytes) -> bytes:
    creature = bytearray(make_cre())
    creature[0x280:0x2A0] = script_name.ljust(32, b"\0")
    return bytes(creature)


class AlikeRecipientTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="cir-alike-recipients-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.game = self.root / "game"
        make_game(self.game, [("SYNTH001", droppable())])
        override = self.game / "override"
        (override / "oh1000.are").write_bytes(make_are())
        # A guard with a name of his own, a mage with the engine's blank, two
        # creatures of one name, a creature placed twice and one no area places.
        for name, script_name in (("guard", b"GuardDV"), ("mage", b"None"), ("twina", b"TwinDV"),
                                  ("twinb", b"TwinDV"), ("pair", b"PairDV"), ("lone", b"LoneDV")):
            (override / f"{name}.cre").write_bytes(named(script_name))
        (override / "area.are").write_bytes(make_are(
            [reward("SYNTH001")], [("Source", 0, 1)],
            [("Guard", "GUARD"), ("Mage", "MAGE"), ("Twin", "TWINA"), ("Twin", "TWINB"),
             ("Pair", "PAIR"), ("Pair", "PAIR")]))
        self.catalog = scan_game(self.game)
        self.world = scan_world(self.catalog)

    def group(self, identity, members):
        place = target_row(identity, "x", "24")
        place.update(endpoint_kind="group", target="guards.flg", container_name=None,
                     members=[f"cre:{name}" for name in members],
                     binding_problem="group-requires-runtime-selection")
        return place

    def creature(self, identity, name):
        place = target_row(identity, "x", "24")
        place.update(endpoint_kind=None, target=f"{name}.cre", container_name=None,
                     creature_resource=f"cre:{name}")
        return place

    def apply(self, *places):
        """The fitted configuration of one reward and <places>, and its native fixture."""
        source = source_row("source-1", ident="x", tier="24",
                            source_ids=[next(row["id"] for row in self.world["sources"]
                                             if row["resource"] == "are:area")])
        draft = {"name": "Original", "choices": {"items": "standard", "targets": "standard"},
                 "options": DEFAULT_OPTIONS | {"scrolls": False}, "items": [source], "targets": list(places)}
        with patch("flir_config.original_configuration.resolve_template", return_value=draft), \
                patch("flir_config.original_registrations.resolve_template", return_value=draft):
            fitted = fit_original_configuration(self.catalog, self.world, "bg1-original")
            store, output = self.root / "campaigns", self.root / "export"
            revision = write_campaign(fitted["profile"], self.catalog, self.world, fitted["plan"],
                                      store)["revision"]
            export_native(store, self.catalog, self.world, output, revision)
        native = json.loads((output / "campaign-store" / "revisions" / f"{revision}.native.json")
                            .read_text(encoding="utf-8"))
        return fitted, native

    def left_out(self, *identities):
        return [{"id": identity, "row": "lists/locations/base/bg1.2da:1", "kind": "place",
                 "reason": ALIKE + identity} for identity in identities]

    def test_a_group_gives_rewards_only_to_members_it_can_tell_apart(self):
        fitted, native = self.apply(self.group("group-1", ["guard", "mage", "twina", "pair", "lone"]))
        self.assertEqual(fitted["not_used"], [])
        self.assertEqual(native["targets"]["original-target:group-1"]["members"],
                         [{"resref": "guard", "script_name": "GuardDV"},
                          {"resref": "lone", "script_name": "LoneDV"}])
        self.assertNotIn("MAGE.CRE", native["resource_hashes"])

    def test_a_place_on_a_creature_it_cannot_tell_apart_is_left_out_and_named(self):
        fitted, native = self.apply(self.creature("guard-1", "guard"), self.creature("mage-1", "mage"),
                                    self.creature("twin-1", "twina"), self.creature("pair-1", "pair"),
                                    self.creature("lone-1", "lone"))
        self.assertEqual(fitted["not_used"], self.left_out("mage-1", "twin-1", "pair-1"))
        self.assertEqual({identity: target["script_name"] for identity, target in native["targets"].items()},
                         {"original-target:guard-1": "GuardDV", "original-target:lone-1": "LoneDV"})

    def test_a_group_without_a_member_it_can_tell_apart_is_left_out_and_named(self):
        fitted, native = self.apply(self.group("group-1", ["guard"]), self.group("group-2", ["mage", "twinb"]))
        self.assertEqual(fitted["not_used"], self.left_out("group-2"))
        self.assertEqual(list(native["targets"]), ["original-target:group-1"])


if __name__ == "__main__":
    unittest.main()
