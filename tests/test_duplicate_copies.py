"""Item Randomiser's second copies of moved rewards; synthetic games only.

lists/duplicates/bg1.2da and bg2.2da name a shop, creature or area container
that holds a second copy of a reward a row moves (flir_config/duplicates.py).
The configuration names each copy this game has, binding and the campaign
accept only those, and the export prepares each holder for the source gate. A
holder the game lacks, or one without the item, has no copy; a copy that
cannot be found exactly leaves its reward out with the reason.
"""

from copy import deepcopy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from flir_config import campaigns  # noqa: E402
from flir_config.campaigns import compile_campaign, write_campaign  # noqa: E402
from flir_config.duplicates import expected_copies, reviewed, table  # noqa: E402
from flir_config.game_names import _eet_tables  # noqa: E402
from flir_config.native_export import export_native  # noqa: E402
from flir_config.original_configuration import (build_original_configuration,  # noqa: E402
                                                fit_original_configuration)
from flir_config.profiles import ProfileError  # noqa: E402
from flir_config.resources import scan_game  # noqa: E402
from flir_config.sources import bind_profile, scan_world  # noqa: E402
from flir_config.starts import _without  # noqa: E402
from flir_config.templates import DEFAULT_OPTIONS  # noqa: E402
from test_original_configuration import source_row, target_row  # noqa: E402
from test_postinstall_resources import make_game, make_itm, mark_eet, write_eet_names  # noqa: E402
from test_postinstall_world_formats import item_record, make_are, make_cre, make_sto  # noqa: E402

ITEMS = ("XBOW06", "SW1H53", "SPER08", "SPER02", "RING26", "AMUL16", "SW1H03", "SYNTH001")
# The rewards in the area container they move from: (campaign, ident, item).
ROWS = (("bg2", "i3c", "xbow06"), ("bg2", "wa", "sw1h53"), ("bg2", "w23", "sper08"),
        ("bg2", "ib", "amul16"), ("bg1", "v8", "sw1h03"))


def droppable() -> bytes:
    item = bytearray(make_itm())
    struct.pack_into("<I", item, 0x18, 0x4c)
    return bytes(item)


def reward(name: str, charges=(0, 0, 0)) -> bytes:
    return item_record(name, expiration=0, charges=charges, flags=1)


class DuplicateGame:
    """An EET game whose holders keep the copies of five real table rows."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="cir-duplicates-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.game = self.root / "game"
        make_game(self.game, [(name, droppable()) for name in ITEMS])
        mark_eet(self.game)
        self.override = self.game / "override"
        files = {
            # The rewards' own places, and the places they may go.
            "area.are": make_are([reward(item.upper()) for *_, item in ROWS],
                                 [("Source", 0, len(ROWS)), ("Target A", len(ROWS), 0),
                                  ("Target B", len(ROWS), 0)]),
            # Second copies: a shop, a creature with a replacement, an area
            # container, a BG1 area under its EET name, and a creature whose
            # two copies differ.
            "bernard.sto": make_sto([(reward("XBOW06"), 1, 0), (reward("SW1H53"), 2, 0)]),
            "sahpr2.cre": make_cre([reward("SPER08"), reward("SYNTH001")]),
            "rr#010.are": make_are([reward("RING26")], [("Chest", 0, 1)]),
            "bg1006.are": make_are([reward("SW1H03")], [("Chest", 0, 1)]),
            "fwjassy.cre": make_cre([reward("AMUL16"), reward("AMUL16", charges=(1, 0, 0))]),
        }
        for name, data in files.items():
            (self.override / name).write_bytes(data)
        self.scan()

    def scan(self):
        self.catalog = scan_game(self.game)
        self.world = scan_world(self.catalog)

    def record(self, resource: str, item: str) -> str:
        return next(row["id"] for row in self.world["sources"]
                    if row["resource"] == resource and row["item"] == item)

    def place(self, identity: str, campaign: str, name: str) -> dict:
        row = target_row(identity, "x", "24")
        row.update(campaign=campaign, target=name, container_name=name, target_ids=[next(
            row["id"] for row in self.world["targets"]
            if row["kind"] == "container" and row["resource"] == "are:area" and row["selector"]["name"] == name)])
        return row

    def draft(self, *extra: dict, rows=ROWS) -> dict:
        items = []
        for campaign, ident, item in rows:
            row = source_row(f"{campaign}-{item}", item=item, ident=ident,
                             source_ids=[self.record("are:area", item)])
            items.append({**row, "campaign": campaign})
        ring = source_row("bg2-ring26", adapter="virtual", item="ring26", ident="i89", resource="itm:ring26")
        items.append({**ring, "campaign": "bg2"})
        return {"name": "Original", "choices": {"items": "standard", "targets": "standard"},
                "options": DEFAULT_OPTIONS | {"scrolls": False}, "items": items + list(extra),
                "targets": [self.place("bg2-place", "bg2", "Target A"),
                            self.place("bg1-place", "bg1", "Target B")]}

    def patched(self, draft: dict):
        stack = patch("flir_config.original_configuration.resolve_template", return_value=draft)
        registrations = patch("flir_config.original_registrations.resolve_template", return_value=draft)
        stack.start(), registrations.start()
        self.addCleanup(stack.stop)
        self.addCleanup(registrations.stop)

    def exported(self, generated: dict) -> tuple[dict, Path, str]:
        store, output = self.root / "campaigns", self.root / "export"
        revision = write_campaign(generated["profile"], self.catalog, self.world, generated["plan"],
                                  store)["revision"]
        export_native(store, self.catalog, self.world, output, revision)
        native = json.loads((output / "campaign-store" / "revisions" / f"{revision}.native.json")
                            .read_text(encoding="utf-8"))
        return native, output, revision


class ExpectedCopyTests(unittest.TestCase):
    def test_tolgerias_second_ring_moves_with_his_reward(self):
        # The extended row 40 moves TOLGER2.CRE's ring; his other creature file
        # keeps a second one (lists/duplicates/bg2.2da:22, ident w1c).
        held = {("cre:tolger", "ring33"): [{"id": "cre:tolger/item/3"}]}
        identity = "cre:tolger2/item/3"
        self.assertEqual(expected_copies(identity, "bg2", "w1c", "ring33", {"unreadable": {}}, held, "eet", None,
                                         route=None),
                         [{"id": "bg2:duplicate:22", "source_id": identity, "resource": "cre:tolger",
                           "item": "ring33"}])

    def test_a_carried_routes_copy_is_its_routes_also_for_a_presets_row(self):
        # Nemphre carries her scroll, and her route hands that copy over: for
        # her own row and for Boss Loot's row of it (the route the export finds,
        # mode2_route_source), lists/duplicates/bg1.2da:12 names no second copy.
        route = "original-source:bg1:mode2:item:159"
        game = ("bg1", "vs1", "scrl1q", {"unreadable": {}},
                {("cre:nemphr", "scrl1q"): [{"id": "cre:nemphr/item/0"}]}, "bgee", None)
        self.assertEqual(expected_copies(route, *game, route=route), [])
        self.assertEqual(expected_copies("original-source:bg1:boss-loot:mode2:item:339", *game, route=route), [])
        # A source that is no route moves the creature's copy as the table's.
        self.assertEqual(expected_copies("cre:nemphr/item/0", *game, route=None),
                         [{"id": "bg1:duplicate:12", "source_id": "cre:nemphr/item/0", "resource": "cre:nemphr",
                           "item": "scrl1q"}])


class DuplicateCopyTests(DuplicateGame, unittest.TestCase):
    def test_shipped_tables_are_item_randomisers(self):
        self.assertEqual(len(table("bg1")), 10)
        self.assertEqual(len(table("bg2")), 27)
        self.assertEqual(table("bg2")[3], {"item": "xbow06", "source": "bernard.sto",
                                           "replacement": "blank", "ident": "i3c"})
        self.assertEqual(table("bg2")[25]["replacement"], "sper02")
        self.assertEqual(table("bg1")[4]["source"], "ar1006.are")

    def test_runtime_knows_each_row_under_its_authored_and_eet_names(self):
        for copy, known in (
                ({"id": "bg1:duplicate:4", "resource": "are:bg1006", "item": "sw1h03"}, True),
                ({"id": "bg1:duplicate:4", "resource": "are:ar1006", "item": "sw1h03"}, True),
                ({"id": "bg2:duplicate:25", "resource": "cre:sahpr2", "item": "sper08",
                  "replacement": {"resref": "sper02", "resource_sha256": "0" * 64}}, True),
                # EET 14's lists name Varci's shield SHLD07A; CIR's copy of them does not.
                ({"id": "bg1:duplicate:5", "resource": "cre:varci", "item": "shld07a"}, False),
                ({"id": "bg2:duplicate:25", "resource": "cre:sahpr2", "item": "sper08"}, False),
                ({"id": "bg2:duplicate:3", "resource": "sto:bernard", "item": "xbow06", "replacement": "sper02"},
                 False),
                ({"id": "bg2:duplicate:3", "resource": "sto:bernard2", "item": "xbow06"}, False),
                ({"id": "bg2:duplicate:1", "resource": "sto:bernard", "item": "xbow06"}, False)):
            with self.subTest(copy=copy):
                self.assertIs(reviewed(copy), known)

    def test_copy_under_unreviewed_eet_names_leaves_its_reward_out(self):
        # This EET's own lists rename BG1's SW1H03, so its copy in BG1006.ARE
        # has a name the gate does not know: the reward stays where it is.
        tables = deepcopy(_eet_tables())
        tables["itm"]["sw1h03"] = "sw1h03x"
        write_eet_names(self.game, tables)
        (self.override / "sw1h03x.itm").write_bytes(droppable())
        (self.override / "area.are").write_bytes(make_are(
            [reward(item.upper()) for *_, item in ROWS[:-1]] + [reward("SW1H03X")],
            [("Source", 0, len(ROWS)), ("Target A", len(ROWS), 0), ("Target B", len(ROWS), 0)]))
        (self.override / "bg1006.are").write_bytes(make_are([reward("SW1H03X")], [("Chest", 0, 1)]))
        self.scan()
        self.patched(self.draft({**source_row("bg1-sw1h03", item="sw1h03x", ident="v8",
                                              source_ids=[self.record("are:area", "sw1h03x")]),
                                 "campaign": "bg1"}, rows=ROWS[:-1]))
        generated = fit_original_configuration(self.catalog, self.world, "eet-original")
        self.assertIn(("bg1-sw1h03", "second-copy-name-unreviewed"),
                      {(row["id"], row["reason"]) for row in generated["not_used"]})
        self.assertNotIn("bg1:duplicate:4", json.dumps(generated["plan"]))

    def test_plan_names_the_copies_this_game_has(self):
        self.patched(self.draft())
        plan = build_original_configuration(self.catalog, self.world, "eet-original")["plan"]
        area = {item: self.record("are:area", item) for *_, item in ROWS}
        expected = [
            {"id": "bg2:duplicate:3", "source_id": area["xbow06"], "resource": "sto:bernard", "item": "xbow06"},
            # Its second row names BERNARD2.STO, which this game does not have.
            {"id": "bg2:duplicate:12", "source_id": area["sw1h53"], "resource": "sto:bernard", "item": "sw1h53"},
            {"id": "bg2:duplicate:25", "source_id": area["sper08"], "resource": "cre:sahpr2", "item": "sper08",
             "replacement": "sper02"},
            {"id": "bg2:duplicate:10", "source_id": area["amul16"], "resource": "cre:fwjassy", "item": "amul16"},
            # AR1006.ARE is BG1006.ARE on EET.
            {"id": "bg1:duplicate:4", "source_id": area["sw1h03"], "resource": "are:bg1006", "item": "sw1h03"},
            {"id": "bg2:duplicate:27", "source_id": "virtual:bg2-ring26", "resource": "are:rr#010",
             "item": "ring26"}]
        self.assertEqual(plan["duplicates"], sorted(expected, key=lambda row: (row["source_id"], row["id"])))

    def test_reward_whose_copies_differ_is_left_out(self):
        self.patched(self.draft())
        generated = fit_original_configuration(self.catalog, self.world, "eet-original")
        source = self.record("are:area", "amul16")
        self.assertEqual(generated["not_used"], [{
            "id": "bg2-amul16", "row": "lists/items/base/bg1.2da:1", "kind": "reward",
            "reason": f"FWJASSY.CRE holds Item Randomiser's second copy of AMUL16 in records that differ: {source}"}])
        self.assertNotIn("amul16", json.dumps(generated["plan"]))
        self.assertEqual(len(generated["plan"]["duplicates"]), 5)

    def test_export_prepares_each_copy_and_its_holder(self):
        draft = self.draft(source_row("bg2-synth001", item="synth001", ident="x",
                                      source_ids=[self.record("cre:sahpr2", "synth001")]))
        self.patched(draft)
        generated = fit_original_configuration(self.catalog, self.world, "eet-original")
        native, output, revision = self.exported(generated)
        copies = {row["id"]: row for row in native["duplicates"]}
        self.assertEqual(sorted(copies), ["bg1:duplicate:4", "bg2:duplicate:12", "bg2:duplicate:25",
                                          "bg2:duplicate:27", "bg2:duplicate:3"])
        payload = {"resref": "xbow06", "expiration": 0, "charges": [0, 0, 0], "flags": 1}
        store = copies["bg2:duplicate:3"]
        self.assertEqual({key: store[key] for key in ("resource", "record_indices", "payload", "stock")},
                         {"resource": "sto:bernard", "record_indices": [0], "payload": payload,
                          "stock": {"quantity": 1, "infinite_supply_flag": 0}})
        self.assertEqual(copies["bg2:duplicate:12"]["stock"], {"quantity": 2, "infinite_supply_flag": 0})
        creature = copies["bg2:duplicate:25"]
        self.assertEqual((creature["record_indices"], creature["replacement"]), ([0], "sper02"))
        self.assertNotIn("stock", creature)
        self.assertEqual(copies["bg2:duplicate:27"]["source_id"], "virtual:bg2-ring26")
        self.assertNotIn("replacement", copies["bg1:duplicate:4"])
        # A source in a copy's creature is rewritten with the creature.
        source = self.record("cre:sahpr2", "synth001")
        self.assertEqual(native["sources"][source]["kind"], "guarded-template")
        self.assertIn("source_gate", native)
        for resource, row in (("SAHPR2.CRE", creature), ("BERNARD.STO", store)):
            self.assertEqual(native["resource_hashes"][resource], row["resource_sha256"])
        # The creature and shop as the game has them, an area as its shared pristine copy.
        revisions = output / "campaign-store" / "revisions"
        numbered = {row["id"]: number for number, row in enumerate(native["duplicates"], 1)}
        self.assertEqual((revisions / f"{revision}.duplicate-{numbered['bg2:duplicate:3']}.original.sto")
                         .read_bytes(), (self.override / "bernard.sto").read_bytes())
        self.assertEqual((revisions / f"{revision}.duplicate-{numbered['bg2:duplicate:25']}.original.cre")
                         .read_bytes(), (self.override / "sahpr2.cre").read_bytes())
        for area in ("rr#010", "bg1006"):
            self.assertEqual((output / f"{area.upper()}.pristine.are").read_bytes(),
                             (self.override / f"{area}.are").read_bytes())
            self.assertFalse(any(path.name.startswith(f"{revision}.duplicate-") and path.suffix == ".are"
                                 for path in revisions.iterdir()))

    def test_holder_without_the_item_or_absent_has_no_copy(self):
        (self.override / "bernard.sto").write_bytes(make_sto([(reward("SW1H53"), 2, 0)]))
        (self.override / "sahpr2.cre").unlink()
        self.scan()
        self.patched(self.draft())
        plan = build_original_configuration(self.catalog, self.world, "eet-original")["plan"]
        self.assertEqual(sorted(row["id"] for row in plan["duplicates"]),
                         ["bg1:duplicate:4", "bg2:duplicate:10", "bg2:duplicate:12", "bg2:duplicate:27"])

    def test_unreadable_holder_leaves_its_rewards_out(self):
        (self.override / "bernard.sto").write_bytes(
            b"STORV1.1" + bytes(make_sto([(reward("XBOW06"), 1, 0)]))[8:])
        self.scan()
        self.patched(self.draft())
        generated = fit_original_configuration(self.catalog, self.world, "eet-original")
        reasons = {row["id"]: row["reason"] for row in generated["not_used"]}
        for item in ("xbow06", "sw1h53"):
            self.assertEqual(reasons[f"bg2-{item}"],
                             f"BERNARD.STO cannot be read, so Item Randomiser's second copy of {item.upper()} "
                             f"there cannot be found: {self.record('are:area', item)}")
        self.assertNotIn("sto:bernard", json.dumps(generated["plan"]))

    def test_record_a_reward_moves_is_not_also_a_copy(self):
        # Another row moves the shop's own crossbow.
        mover = source_row("bg2-shop-xbow06", item="xbow06", ident="x",
                           source_ids=[self.record("sto:bernard", "xbow06")])
        self.patched(self.draft({**mover, "campaign": "bg2"}))
        generated = fit_original_configuration(self.catalog, self.world, "eet-original")
        reasons = {row["id"]: row["reason"] for row in generated["not_used"]}
        self.assertEqual(reasons["bg2-xbow06"],
                         "BERNARD.STO's XBOW06 moves as a reward of its own, so it cannot also be the second "
                         f"copy of another: {self.record('are:area', 'xbow06')}")
        native, *_ = self.exported(generated)
        # The shop keeps its other copy and its moved crossbow in one rewrite.
        self.assertEqual(native["sources"][self.record("sto:bernard", "xbow06")]["multiple_stock"], True)
        self.assertIn("bg2:duplicate:12", {row["id"] for row in native["duplicates"]})

    def test_binding_accepts_only_the_copies_of_the_tables_and_the_scan(self):
        self.patched(self.draft())
        generated = build_original_configuration(self.catalog, self.world, "eet-original")
        profile, plan = generated["profile"], generated["plan"]
        bound = bind_profile(profile, self.catalog, self.world, plan)
        self.assertEqual(bound["duplicates"][0]["resource_sha256"], next(
            row["sha256"] for row in self.world["resources"] if row["id"] == bound["duplicates"][0]["resource"]))
        replacement = next(row for row in bound["duplicates"] if "replacement" in row)["replacement"]
        self.assertEqual(replacement["resref"], "sper02")
        changes = {
            "a copy left out": lambda copies: copies.pop(),
            "another holder": lambda copies: copies[0].update(resource="sto:bernard2"),
            "another row": lambda copies: copies[0].update(id="bg2:duplicate:13"),
            "no replacement": lambda copies: next(row for row in copies if "replacement" in row).pop("replacement"),
            "a copy twice": lambda copies: copies.append(dict(copies[0])),
            "no copies": lambda copies: copies.clear(),
        }
        for label, change in changes.items():
            with self.subTest(label):
                edited = deepcopy(plan)
                change(edited["duplicates"])
                with self.assertRaisesRegex(ProfileError, "Second copies of the moved rewards disagree"):
                    bind_profile(profile, self.catalog, self.world, edited)
        # Without the preset's settings no copy is moved with a reward.
        manual = {key: value for key, value in deepcopy(plan).items()
                  if key not in ("original_template_id", "original_choices", "original_options",
                                 "target_registrations")}
        with self.assertRaisesRegex(ProfileError, "need original plan metadata"):
            bind_profile(profile, self.catalog, self.world, manual)
        # A source the editor no longer moves takes its copies along.
        source = plan["duplicates"][0]["source_id"]
        edited = deepcopy(plan)
        edited["randomization"]["row_filters"]["sources"][source]["group"] = ""
        with self.assertRaisesRegex(ProfileError, "Second copies of the moved rewards disagree"):
            bind_profile(profile, self.catalog, self.world, edited)

    def test_campaign_accepts_only_rows_of_the_shipped_tables(self):
        self.patched(self.draft())
        generated = build_original_configuration(self.catalog, self.world, "eet-original")
        body = compile_campaign(generated["profile"], self.catalog, self.world, generated["plan"])
        self.assertEqual(len(body["duplicates"]), 6)
        bg2 = next(index for index, row in enumerate(body["duplicates"]) if row["id"] == "bg2:duplicate:3")
        replaced = next(index for index, row in enumerate(body["duplicates"]) if "replacement" in row)
        changes = {
            "unknown row": (bg2, lambda row: row.update(id="bg2:duplicate:99"), "names no reward row"),
            "header line": (bg2, lambda row: row.update(id="bg2:duplicate:1"), "names no reward row"),
            "other item": (bg2, lambda row: row.update(item="sw1h53"), "names no reward row"),
            "other holder": (bg2, lambda row: row.update(resource="sto:bernard2"), "disagrees with its row"),
            "other kind": (bg2, lambda row: row.update(resource="cre:bernard"), "kind disagrees"),
            "added replacement": (bg2, lambda row: row.update(replacement={
                "resref": "sper02", "resource_sha256": row["resource_sha256"]}), "disagrees with its row"),
            "other replacement": (replaced, lambda row: row["replacement"].update(resref="sper01"),
                                  "disagrees with its row"),
            "unknown source": (bg2, lambda row: row.update(source_id="are:area/item/99"), "names no reward row"),
        }
        for label, (index, change, message) in changes.items():
            with self.subTest(label):
                edited = deepcopy(body)
                change(edited["duplicates"][index])
                edited["revision"] = campaigns._digest({key: value for key, value in edited.items()
                                                        if key != "revision"})
                with self.assertRaisesRegex(ProfileError, message):
                    campaigns._validate_campaign(edited)
        edited = deepcopy(body)
        edited["duplicates"].reverse()
        edited["revision"] = campaigns._digest({key: value for key, value in edited.items() if key != "revision"})
        with self.assertRaisesRegex(ProfileError, "unique and sorted"):
            campaigns._validate_campaign(edited)

    def test_a_start_without_a_reward_keeps_its_copies(self):
        self.patched(self.draft())
        generated = build_original_configuration(self.catalog, self.world, "eet-original")
        plan = generated["plan"]
        source = self.record("are:area", "xbow06")
        _, without = _without(generated["profile"], plan, self.catalog, {source}, set())
        self.assertEqual([row for row in without["duplicates"] if row["source_id"] == source], [])
        self.assertEqual(len(without["duplicates"]), len(plan["duplicates"]) - 1)
        everything = {row["source_id"] for row in plan["duplicates"]}
        _, without = _without(generated["profile"], plan, self.catalog, everything, set())
        self.assertNotIn("duplicates", without)


if __name__ == "__main__":
    unittest.main()
