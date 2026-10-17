"""Campaign starts on synthetic BG:EE, BG2:EE and EET installs; no real games.

Each start gets a revision with only the rewards and places it reaches. A
reward a start cannot reach stays as the game has it, so these checks look
for two things: nothing lands where the player cannot go, and every start
that can be randomised still is.
"""

from collections import defaultdict
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from flir_config.campaigns import _render_lua, read_campaign
from flir_config.native_export import NEXT_SCHEMA, export_native
from flir_config.original_configuration import build_original_configuration
from flir_config.platform import (PRESET_CAMPAIGNS, STARTS, area_part, game_platform,
                                  preset_game)
from flir_config.profiles import ProfileError, edit_profile, new_profile
from flir_config.resources import scan_game
from flir_config.sources import new_binding_plan, scan_world
from flir_config.starts import describe_starts, write_starts
from flir_config.templates import DEFAULT_OPTIONS, list_templates
from tests.test_original_configuration import source_row, target_row
from tests.test_postinstall_resources import make_game, make_itm, mark_eet, snapshot
from tests.test_postinstall_world_formats import item_record, make_are, make_cre, make_sto

# The areas that tell each game apart (EET has both and its flag).
MARKERS = {"eet": ("oh1000", "oh6000"), "bgee": ("oh1000",), "bg2ee": ("oh6000",), "other": ()}


def reward(name: str) -> bytes:
    return item_record(name, expiration=0, charges=(0, 0, 0), flags=1)


def chest(area: str) -> str:
    return f"are:{area}/container/0/item/0"


def place(area: str) -> str:
    return f"are:{area}/container/1"


class StartsFixture(unittest.TestCase):
    def build(self, platform: str, areas: tuple[str, ...], *, guard: str | None = None,
              shop: bool = False) -> None:
        """A game whose areas each have a chest with a reward and an empty chest.

        <guard> places a creature carrying SYNTH002 in that area; <shop> adds a
        shop selling it.
        """
        temp = tempfile.TemporaryDirectory(prefix="flir-starts-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.game, self.store, self.output = (self.root / name for name in
                                              ("game", "campaigns", "randomiser-campaigns"))
        make_game(self.game, [("SYNTH001", make_itm()), ("SYNTH002", make_itm())])
        override = self.game / "override"
        for name in ("synth001", "synth002"):
            item = bytearray(make_itm())
            struct.pack_into("<I", item, 0x18, 0x4c)
            (override / f"{name}.itm").write_bytes(item)
        if platform == "eet":
            mark_eet(self.game)
        for area in MARKERS[platform]:
            (override / f"{area}.are").write_bytes(make_are())
        for area in areas:
            actors = [("Guard", "TOBGUARD")] if area == guard else []
            (override / f"{area}.are").write_bytes(make_are(
                [reward("SYNTH001")], [("Source", 0, 1), ("Target", 1, 0)], actors))
        if guard:
            (override / "tobguard.cre").write_bytes(make_cre([reward("SYNTH002")]))
        if shop:
            (override / "shop.sto").write_bytes(make_sto([(reward("SYNTH002"), 1, 0)]))
        self.catalog = scan_game(self.game)
        self.world = scan_world(self.catalog)

    def configuration(self, capacities: dict[str, int]) -> tuple[dict, dict]:
        """Every reward of the game, relocated to the empty chests of these areas."""
        by_item = defaultdict(list)
        for source in self.world["sources"]:
            if source["kind"] in ("container", "cre-template", "store"):
                by_item[source["item"]].append(source["id"])
        profile = edit_profile(new_profile(self.catalog, "starts", "Starts"), self.catalog,
                               include=sorted(by_item))
        plan = new_binding_plan(profile, self.catalog, self.world)
        plan["sources"] = [{"item": item, "policy": "relocate", "source_ids": sorted(ids),
                            "registration_ids": []} for item, ids in sorted(by_item.items())]
        plan["targets"] = [{"id": place(area), "capacity": capacity, "optional": False}
                           for area, capacity in capacities.items()]
        return profile, plan

    def contents(self, written: dict) -> dict[str, tuple[set, set]]:
        """The source and target identities of each start's revision."""
        result = {}
        for start, revision in written["starts"].items():
            campaign = read_campaign(self.store, revision)
            result[start] = ({row["id"] for row in campaign["sources"]},
                             {row["id"] for row in campaign["targets"]})
        return result

    def creature(self) -> str:
        return next(row["id"] for row in self.world["sources"] if row["kind"] == "cre-template")

    def shop(self) -> str:
        return next(row["id"] for row in self.world["sources"] if row["kind"] == "store")


class PlatformTests(StartsFixture):
    def test_game_is_told_apart_as_weidu_does(self):
        for platform in ("eet", "bgee", "bg2ee"):
            with self.subTest(platform=platform):
                self.build(platform, ())
                self.assertEqual(game_platform(self.world), platform)
        # The flag's spelling does not matter on a case-insensitive file system.
        self.build("bgee", ())
        (self.game / "override" / "eet.FLAG").write_bytes(b"")
        self.assertEqual(game_platform(self.world), "eet")
        self.build("other", ())
        with self.assertRaisesRegex(ProfileError, "this game is none of them"):
            game_platform(self.world)

    def test_presets_follow_the_tables_the_game_has(self):
        self.build("bgee", ())
        self.assertEqual(preset_game(self.world, "bg1-original"), "bgee")
        for template, name in (("bg2-original", "BG2"), ("eet-original", "EET")):
            with self.assertRaisesRegex(ProfileError, f"This preset is for {name}; this game is BG:EE"):
                preset_game(self.world, template)
        self.assertEqual({row["id"] for row in list_templates(PRESET_CAMPAIGNS["bgee"])},
                         {"bg1-original", "bg1-boss-loot"})
        self.assertEqual({row["id"] for row in list_templates(PRESET_CAMPAIGNS["bg2ee"])},
                         {"bg2-original"})
        self.assertEqual(len(list_templates(PRESET_CAMPAIGNS["eet"])), len(list_templates()))
        self.build("eet", ())
        for template in ("bg1-original", "bg2-original", "eet-original"):
            self.assertEqual(preset_game(self.world, template), "eet")

    def test_area_names_tell_their_part_of_the_game(self):
        cases = {
            "eet": {"bg0100": "bg1", "BG2600N": "bg1", "oh1000": "bg1", "oh3010": "bg1",
                    "bd0010": "sod", "ar0100": "soa", "oh6000": "soa", "ar3000": "wk",
                    "ar3027": "wk", "ar4000": "tob", "ar6200": "tob", "ar2600": "soa",
                    "zz0001": None, "ar01": None, "bp0001": None},
            "bgee": {"ar0100": "bg1", "oh1000": "bg1", "bd0010": "sod", "bg0100": None},
            "bg2ee": {"ar0100": "soa", "oh6000": "soa", "oh1000": None, "ar3000": "wk",
                      "ar5000": "tob", "bd0010": None, "bg0100": None},
        }
        for platform, areas in cases.items():
            for area, part in areas.items():
                with self.subTest(platform=platform, area=area):
                    self.assertEqual(area_part(platform, area), part)


class StartRevisionTests(StartsFixture):
    EET_AREAS = ("bg0100", "bd0100", "ar0100", "ar3000", "ar4000", "zz0001")

    def test_eet_starts_get_only_what_they_reach(self):
        self.build("eet", self.EET_AREAS, guard="ar4000", shop=True)
        profile, plan = self.configuration({area: 2 for area in self.EET_AREAS})
        before = snapshot(self.game)
        written = write_starts(profile, plan, self.catalog, self.world, self.store)
        self.assertEqual(snapshot(self.game), before)
        self.assertEqual((written["platform"], written["full"], written["main"], written["skipped"]),
                         ("eet", "BG1", None, {}))
        creature, shop = self.creature(), self.shop()
        everything = {chest(area) for area in self.EET_AREAS} | {creature, shop}
        self.assertEqual(self.contents(written), {
            "BG1": (everything, {place(area) for area in self.EET_AREAS}),
            # The creature stands in a Throne of Bhaal area; a shop or an area
            # whose part is unknown stays as the game has it.
            "SOD": ({chest("bd0100"), chest("ar0100"), chest("ar3000"), chest("ar4000"), creature},
                    {place("bd0100"), place("ar0100"), place("ar3000"), place("ar4000")}),
            "SOA": ({chest("ar0100"), chest("ar3000"), chest("ar4000"), creature},
                    {place("ar0100"), place("ar3000"), place("ar4000")}),
            "TOB": ({chest("ar3000"), chest("ar4000"), creature},
                    {place("ar3000"), place("ar4000")})})
        # The full configuration stays the store's own current revision.
        self.assertEqual(read_campaign(self.store)["revision"], written["starts"]["BG1"])
        self.assertEqual(describe_starts(written), "New games randomised: Baldur's Gate, "
                         "Siege of Dragonspear, Shadows of Amn, Throne of Bhaal.")

        result = export_native(self.store, self.catalog, self.world, self.output,
                               starts=written["starts"], main=written["main"])
        self.assertEqual(result["starts"], written["starts"])
        self.assertEqual((self.output / "next.lua").read_bytes(),
                         _render_lua({"schema": NEXT_SCHEMA, "starts": written["starts"]}))
        for revision in written["starts"].values():
            self.assertTrue((self.output / "campaign-store" / "revisions" /
                             f"{revision}.native.json").is_file())
        self.assertEqual(snapshot(self.game), before)

    def test_a_start_with_too_few_places_leaves_rewards_where_they_are(self):
        self.build("eet", self.EET_AREAS, guard="ar4000")
        # Throne of Bhaal has three rewards but two places.
        capacities = {area: 2 for area in self.EET_AREAS} | {"ar3000": 1, "ar4000": 1}
        profile, plan = self.configuration(capacities)
        written = write_starts(profile, plan, self.catalog, self.world, self.store)
        contents = self.contents(written)
        self.assertIn(self.creature(), contents["SOA"][0])
        self.assertEqual(contents["TOB"], ({chest("ar3000"), chest("ar4000")},
                                           {place("ar3000"), place("ar4000")}))
        # Without its reward, the start no longer moves the creature's item.
        self.assertEqual({row["record"]["item"] for row in
                          read_campaign(self.store, written["starts"]["TOB"])["sources"]},
                         {"synth001"})

    def test_starts_that_reach_nothing_are_named_and_not_randomised(self):
        self.build("eet", ("bg0100", "bd0100"))
        profile, plan = self.configuration({"bg0100": 2})
        written = write_starts(profile, plan, self.catalog, self.world, self.store)
        self.assertEqual(set(written["starts"]), {"BG1"})
        reason = "nothing in this configuration is reached from this start"
        self.assertEqual(written["skipped"], {"SOD": reason, "SOA": reason, "TOB": reason})
        self.assertEqual(describe_starts(written),
                         f"New games randomised: Baldur's Gate. Not randomised: "
                         f"Siege of Dragonspear ({reason}); Shadows of Amn ({reason}); "
                         f"Throne of Bhaal ({reason}).")

    def test_bgee_starts_baldurs_gate_and_siege_of_dragonspear(self):
        self.build("bgee", ("ar0100", "bd0100", "zz0001"), shop=True)
        profile, plan = self.configuration({"ar0100": 2, "bd0100": 1, "zz0001": 1})
        written = write_starts(profile, plan, self.catalog, self.world, self.store)
        self.assertEqual((written["platform"], written["full"], written["main"]),
                         ("bgee", "BG1", "BG1"))
        everything = {chest("ar0100"), chest("bd0100"), chest("zz0001"), self.shop()}
        self.assertEqual(self.contents(written), {
            "BG1": (everything, {place("ar0100"), place("bd0100"), place("zz0001")}),
            "SOD": ({chest("bd0100")}, {place("bd0100")})})
        export_native(self.store, self.catalog, self.world, self.output,
                      starts=written["starts"], main=written["main"])
        self.assertEqual((self.output / "next.lua").read_bytes(), _render_lua(
            {"schema": NEXT_SCHEMA, "starts": written["starts"], "main": "BG1"}))

    def test_bg2ee_starts_shadows_of_amn_and_throne_of_bhaal(self):
        # BG2:EE has no Siege of Dragonspear: a BD area is unknown there.
        self.build("bg2ee", ("ar0100", "ar3000", "ar4000", "bd0100"), guard="ar4000")
        profile, plan = self.configuration({"ar0100": 2, "ar3000": 1, "ar4000": 2, "bd0100": 1})
        written = write_starts(profile, plan, self.catalog, self.world, self.store)
        self.assertEqual((written["platform"], written["full"], written["main"]),
                         ("bg2ee", "SOA", "SOA"))
        self.assertEqual(set(written["starts"]), set(STARTS["bg2ee"]))
        self.assertEqual(self.contents(written)["TOB"],
                         ({chest("ar3000"), chest("ar4000"), self.creature()},
                          {place("ar3000"), place("ar4000")}))
        self.assertEqual(len(self.contents(written)["SOA"][0]), 5)
        export_native(self.store, self.catalog, self.world, self.output,
                      starts=written["starts"], main=written["main"])
        self.assertEqual((self.output / "next.lua").read_bytes(), _render_lua(
            {"schema": NEXT_SCHEMA, "starts": written["starts"], "main": "SOA"}))

    def test_other_games_are_refused_before_anything_is_written(self):
        self.build("other", ("ar0100",))
        profile, plan = self.configuration({"ar0100": 1})
        with self.assertRaisesRegex(ProfileError, "this game is none of them"):
            write_starts(profile, plan, self.catalog, self.world, self.store)
        self.assertFalse(self.store.exists())


class PresetStartTests(StartsFixture):
    """Rows of Item Randomiser's tables: a BG1 row stays out of later EET starts."""

    def draft(self) -> dict:
        def item(identity, campaign, area=None):
            row = source_row(identity, ident="x", adapter="physical" if area else "virtual",
                             source_ids=[chest(area)] if area else None,
                             resource=None if area else "itm:synth001")
            if area:
                row["source_resource"] = f"are:{area}"
            row.update(campaign=campaign, source_ref=f"lists/items/base/{campaign}.2da:1")
            return row

        def target(identity, campaign, area):
            row = target_row(identity, "x", "24")
            row.update(campaign=campaign, area=area, location=f"{area}.are",
                       area_resource=f"are:{area}", target_ids=[place(area)],
                       source_ref=f"lists/locations/base/{campaign}.2da:1",
                       endpoint_source_ref=f"lists/locations/base/{campaign}.2da:1")
            return row

        return {"name": "Original", "choices": {"items": "standard", "targets": "standard"},
                "options": DEFAULT_OPTIONS | {"scrolls": False, "loss_mode": "none",
                                              "loss_percent": 0, "unrandomized_percent": 30},
                "items": [item("bg1:base:item:1", "bg1", "bg0100"),
                          item("bg2:base:item:1", "bg2", "ar0100"),
                          item("bg2:base:item:2", "bg2", "ar4000"),
                          item("bg1:base:item:3", "bg1"), item("bg2:base:item:3", "bg2")],
                "targets": [target("bg1:base:target:1", "bg1", "bg0100"),
                            target("bg2:base:target:1", "bg2", "ar0100"),
                            target("bg2:base:target:2", "bg2", "ar4000")]}

    def test_table_rows_follow_their_campaign_and_area(self):
        self.build("eet", ("bg0100", "ar0100", "ar4000"))
        draft = self.draft()
        with patch("flir_config.original_configuration.resolve_template", return_value=draft), \
                patch("flir_config.original_registrations.resolve_template", return_value=draft):
            built = build_original_configuration(self.catalog, self.world, "eet-original")
            self.assertEqual(built["left_out"], [])
            written = write_starts(built["profile"], built["plan"], self.catalog, self.world,
                                   self.store)
        bg2_virtual = "virtual:bg2:base:item:3"
        later = ({chest("ar0100"), chest("ar4000"), bg2_virtual},
                 {"original-target:bg2:base:target:1", "original-target:bg2:base:target:2"})
        contents = self.contents(written)
        self.assertEqual(len(contents["BG1"][0]), 5)
        self.assertEqual(contents["SOD"], later)
        self.assertEqual(contents["SOA"], later)
        # A BG2 table row may be anywhere in Shadows of Amn: not in a ToB start.
        self.assertEqual(contents["TOB"], ({chest("ar4000")}, {"original-target:bg2:base:target:2"}))
        tob = read_campaign(self.store, written["starts"]["TOB"])
        self.assertEqual(set(tob["randomization"]["sources"]), {chest("ar4000")})
        self.assertEqual(set(tob["randomization"]["targets"]), {"original-target:bg2:base:target:2"})


if __name__ == "__main__":
    unittest.main()
