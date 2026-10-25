"""Item Randomiser's special places on synthetic BG2:EE and EET games; no real games.

A special place (special_places.py) is a row of Item Randomiser's tables that a
quest, a carrier, a guarded treasure or a group delivers. The configurator
offers those this game can deliver, with the registration a preset's fit
builds for them, and any configuration may give them rewards: one of its own,
or a preset whose options leave the place's row out. A configuration has one
row of a place that the game patches and the runtime name once.
"""

from contextlib import ExitStack, contextmanager
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from flir_config import mode2_rewards, special_places
from flir_config.campaigns import _validate_campaign, compile_campaign
from flir_config.container_rewards import TARGETS as CONTAINER_TARGETS
from flir_config.original_catalog import CREATURE_VERSIONS
from flir_config.native_export import _compile, export_native
from flir_config.original_configuration import fit_original_configuration, fit_special_places
from flir_config.profiles import ProfileError, edit_profile, new_profile, validate_catalog
from flir_config.quest_rewards import QUESTS
from flir_config.sources import bind_profile, new_binding_plan, validate_world
from flir_config.starts import write_starts
from flir_config.templates import build_template
from test_original_configuration import target_row
from test_postinstall_resources import snapshot
from test_postinstall_world_formats import make_are
from test_reward_routes import RouteGame, chest_item

OGRE, WELL, KHOLLOS = (f"original-target:bg2:mode2:target:{line}" for line in (17, 27, 33))
LABELS = {
    OGRE: {"place": "route:bg2:mode2:target:17", "label": "Madulf the ogre (Umar Hills)",
           "note": "He hands it over with his reward."},
    WELL: {"place": "route:bg2:mode2:target:27", "label": "The well (Firkraag's dungeon)",
           "note": "You pull it out of the well with the bucket."}}
SAME_PLACE = f"Original recipient is the same place as {OGRE}; keep one of them: {WELL}"


class SpecialPlaceGame(RouteGame):
    """The ogre's, the well's and Khollos's routes (another mod changed his so
    that nothing gives the belt), Item Randomiser's two chests in AR0100, and a
    test item in the first chest for a configuration of its own."""

    def build(self, platform: str, **game) -> None:
        chests = make_are([chest_item("SYNTH001")], [("Place2", 0, 1), ("Place3", 1, 0)])
        super().build(platform, extra={"ar0100.are": chests}, **game)

    @contextmanager
    def tables(self, draft: dict | None = None):
        """Item Randomiser's lists as this game's draft (RouteGame.draft) has them."""
        draft = self.draft() if draft is None else draft
        with ExitStack() as stack:
            for module in ("original_configuration", "original_registrations"):
                stack.enter_context(patch(f"flir_config.{module}.resolve_template", return_value=draft))
            yield

    def own_configuration(self, *registrations: dict) -> tuple[dict, dict]:
        """A configuration that is no preset: the test item moves to these places."""
        profile = edit_profile(new_profile(self.catalog, "own-places", "Own places"), self.catalog,
                               include=["synth001"])
        plan = new_binding_plan(profile, self.catalog, self.world)
        chest = [row["id"] for row in self.world["sources"] if row["item"] == "synth001"]
        self.assertEqual(len(chest), 1)
        plan["sources"] = [{"item": "synth001", "policy": "relocate", "source_ids": chest, "registration_ids": []}]
        plan["target_registrations"] = list(registrations)
        plan["targets"] = [{"id": row["id"], "capacity": 1, "optional": False} for row in registrations]
        return profile, plan

    def apply(self, profile: dict, plan: dict, draft: dict | None = None) -> dict:
        """Check, write and export a configuration as Apply does; the native endpoints."""
        with self.tables(draft):
            campaign = compile_campaign(profile, self.catalog, self.world, plan)
            _compile(campaign, self.world, validate_world(self.world, self.catalog),
                     validate_catalog(self.catalog), catalog=self.catalog)
            written = write_starts(profile, plan, self.catalog, self.world, self.store)
            export_native(self.store, self.catalog, self.world, self.output,
                          starts=written["starts"], main=written["main"])
        revision = written["starts"][written["full"]]
        return json.loads((self.output / "campaign-store" / "revisions" / f"{revision}.native.json").read_bytes())


class OfferedPlaceTests(SpecialPlaceGame):
    def test_the_game_offers_the_special_places_it_can_deliver(self):
        for platform in ("bg2ee", "eet"):
            with self.subTest(platform=platform):
                self.build(platform)
                with self.tables():
                    offered = fit_special_places(self.catalog, self.world)
                # Khollos's route cannot be used here; Item Randomiser's chests are
                # ordinary places.
                self.assertEqual([row["id"] for row in offered], [OGRE, WELL])
                self.assertEqual({row["id"]: special_places.describe(row["authored"]) for row in offered}, LABELS)
                # Each is the registration a preset's fit builds for its row.
                fitted = {row["id"]: row for row in self.fit()["plan"]["target_registrations"]}
                for row in offered:
                    self.assertEqual(row, fitted[row["id"]])

    def test_a_game_without_the_lists_places_has_none(self):
        self.build("bg2ee")
        draft = self.draft()
        draft["targets"] = [row for row in draft["targets"] if special_places.place(row) is None]
        with self.tables(draft):
            self.assertEqual(fit_special_places(self.catalog, self.world), [])

    def test_a_presets_row_at_a_route_or_container_quest_is_that_place(self):
        # Boss Loot's rows at Aldeth's, Varci's, Nemphre's and Safana's places.
        own = {row["id"]: special_places.place(row)
               for row in build_template("bg1-original", targets="extended")["targets"]}
        boss = {row["id"]: special_places.place(row)
                for row in build_template("bg1-boss-loot", targets="extended")["targets"]}
        for alias, original in (("bg1:boss-loot:mode2:target:46", "bg1:mode2:target:16"),
                                ("bg1:boss-loot:mode2:target:71", "bg1:mode2:target:20"),
                                ("bg1:boss-loot:mode2:target:291", "bg1:mode2:target:136"),
                                ("bg1:boss-loot:mode2:target:47", "bg1:mode2:target:22")):
            self.assertIsNotNone(own[original])
            self.assertEqual(boss[alias], own[original])
            self.assertTrue(special_places.unique(own[original]))
        groups = {key for key in own.values() if key and key.startswith("group:")}
        self.assertTrue(groups)
        self.assertFalse(any(special_places.unique(key) for key in groups))

    def test_every_special_place_has_its_label(self):
        # A new route, container quest, quest script or creature in several
        # versions needs its label before the editor can show it, and no label
        # stays for a place that is gone.
        self.assertEqual(set(special_places.ROUTES), set(mode2_rewards.TARGET_REWARDS.values()))
        self.assertEqual(set(special_places.CONTAINER_QUESTS), set(CONTAINER_TARGETS))
        self.assertEqual(set(special_places.SCRIPTED_QUESTS), set(QUESTS))
        self.assertEqual(set(special_places.CREATURES), set(CREATURE_VERSIONS.values()))

    def test_a_groups_label_names_its_members_as_their_area_does(self):
        row = target_row("bg2:base:target:9004", "x", "4")
        row.update(campaign="bg2", target="guards.flg", endpoint_kind="group", area_resource="are:ar0701",
                   members=["cre:gall", "cre:gaius", "cre:gaius2", "cre:rengaa", "cre:berkref", "cre:hendak",
                            "cre:nameless"])
        names = {"cre:gall": ["Gall"], "cre:gaius": ["Gaius"], "cre:gaius2": ["Gaius"], "cre:rengaa": ["Rengaa"],
                 "cre:berkref": ["Berkref"], "cre:hendak": ["Hendak"]}
        described = special_places.describe(row, lambda area, member: names.get(member, []) if area == "are:ar0701"
                                            else [])
        # A member without a name there goes by its file's.
        self.assertEqual(described, {"place": "group:bg2:guards.flg:are:ar0701",
                                     "label": "One of: Gall, Gaius, Rengaa, Berkref and 2 more (AR0701)",
                                     "note": special_places.GROUP_NOTE})
        self.assertEqual(special_places.describe(row)["label"],
                         "One of: GALL, GAIUS, GAIUS2, RENGAA and 3 more (AR0701)")
        self.assertIsNone(special_places.describe(target_row("bg2:base:target:9005", "x", "4")))


class SpecialPlaceConfigurationTests(SpecialPlaceGame):
    def test_a_configuration_of_its_own_gives_a_special_place_a_reward(self):
        self.build("bg2ee")
        with self.tables():
            ogre = fit_special_places(self.catalog, self.world)[0]
        before = snapshot(self.game)
        native = self.apply(*self.own_configuration(ogre))
        self.assertEqual(snapshot(self.game), before)
        self.assertEqual(native["targets"][OGRE]["kind"], "quest")
        # Apply patches Madulf's conversation for his reward, as for a preset.
        self.assertEqual((self.output / "resource-overrides" / "UHOGRE01.DLG").read_bytes(),
                         mode2_rewards.patch_reward("dlg:uhogre01", self.resources["uhogre01.dlg"]))

    def test_a_preset_takes_a_special_place_its_options_leave_out(self):
        self.build("bg2ee")
        gated = self.draft()
        for row in gated["targets"]:
            if row["id"] == "bg2:mode2:target:27":
                row["enabled"] = False  # As an option the preset does not have would.
        with self.tables(gated):
            fitted = fit_original_configuration(self.catalog, self.world, self.template,
                                                items="extended", targets="extended")
            well = {row["id"]: row for row in fit_special_places(self.catalog, self.world)}[WELL]
        plan = fitted["plan"]
        self.assertNotIn(WELL, {row["id"] for row in plan["target_registrations"]})
        plan["target_registrations"] = sorted(plan["target_registrations"] + [well], key=lambda row: row["id"])
        plan["targets"] = sorted(plan["targets"] + [{"id": WELL, "capacity": 1, "optional": False}],
                                 key=lambda row: row["id"])
        plan["randomization"]["targets"][WELL] = ["bg2:3"]
        plan["randomization"]["row_filters"]["targets"][WELL] = {"chance": 100, "group": ""}
        native = self.apply(fitted["profile"], plan, gated)
        self.assertIn(WELL, native["targets"])
        self.assertTrue((self.output / "resource-overrides" / "DDWELL.BCS").is_file())

    def test_a_configuration_of_its_own_takes_no_other_original_recipient(self):
        self.build("bg2ee")
        fitted = {row["selector"]: row for row in self.fit()["plan"]["target_registrations"]}
        with self.tables():
            profile, plan = self.own_configuration(fitted["bg2:base:target:9002"])
            with self.assertRaisesRegex(ProfileError, "^Original target needs original plan metadata$"):
                bind_profile(profile, self.catalog, self.world, plan)
            changed = json.loads(json.dumps(fitted["bg2:mode2:target:17"]))
            changed["authored"]["tier"] = "9"
            profile, plan = self.own_configuration(changed)
            with self.assertRaisesRegex(ProfileError, "^Original target evidence disagrees with its table row$"):
                bind_profile(profile, self.catalog, self.world, plan)
            # A preset keeps its metadata and its original recipients together.
            preset = self.fit()
            plan = json.loads(json.dumps(preset["plan"]))
            del plan["target_registrations"]
            with self.assertRaisesRegex(ProfileError, "^Original plan metadata is incomplete$"):
                bind_profile(preset["profile"], self.catalog, self.world, plan)
            # A campaign without a preset's settings has no other authored recipient either.
            profile, plan = self.own_configuration(fitted["bg2:mode2:target:17"])
            own = compile_campaign(profile, self.catalog, self.world, plan)
            chests = compile_campaign(preset["profile"], self.catalog, self.world, preset["plan"])["targets"]
        own["targets"] = [row for row in chests if row["id"] == "original-target:bg2:base:target:9002"]
        with self.assertRaisesRegex(ProfileError, "^Authored records need original campaign settings$"):
            _validate_campaign(own)

    def test_two_rows_of_one_place_are_refused(self):
        self.build("bg2ee")
        fitted = self.fit()
        real = special_places.place

        def same(authored):
            # As Boss Loot's row at Aldeth's place is his route's own row.
            key = real(authored)
            return LABELS[OGRE]["place"] if key == LABELS[WELL]["place"] else key

        with self.tables(), patch("flir_config.native_export.special_place", same):
            campaign = compile_campaign(fitted["profile"], self.catalog, self.world, fitted["plan"])
            index, items = validate_world(self.world, self.catalog), validate_catalog(self.catalog)
            with self.assertRaisesRegex(ProfileError, f"^{SAME_PLACE}$"):
                _compile(campaign, self.world, index, items, catalog=self.catalog)
            problems: dict[str, str] = {}
            _compile(campaign, self.world, index, items, problems, catalog=self.catalog)
        self.assertEqual(problems, {WELL: SAME_PLACE})


if __name__ == "__main__":
    unittest.main()
