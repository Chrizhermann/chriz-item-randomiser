"""Saved configurations carried over to new scans of the same game; no real games.

After the game's mods change (a new version of Chriz Item Randomiser among
them) the configurator scans the game again, and the configurations saved
with the earlier scans no longer open. rebase_drafts carries them over: one
saved unchanged from a preset becomes the preset as it fits now; of any other
the player's choices stay, the evidence comes from the new scans, and only
what the new scans no longer offer is left out and named.
"""

from copy import deepcopy
from pathlib import Path
import struct
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from flir_config import original_configuration
from flir_config.campaigns import compile_campaign
from flir_config.editor import checked_draft
from flir_config.native_export import _compile
from flir_config.original_catalog import CARRIER_CAN_GO_MISSING
from flir_config.profiles import ProfileError, new_profile, validate_catalog
from flir_config.rebase import (NO_PLACE, NOT_AVAILABLE, NOTHING_LEFT, PRESET_FOLLOWED, PRESET_HAS_MORE,
                                PRESET_UNFIT, SOURCE_ABSENT, SOURCE_AMBIGUOUS, TARGET_ABSENT, TARGET_AMBIGUOUS,
                                TARGET_UNVERIFIED, UNREADABLE, rebase_drafts)
from flir_config.sources import new_binding_plan, validate_world
from test_mode2_rewards import layout_dialogue
from test_postinstall_world_formats import make_are, make_cre
from test_reward_routes import RouteGame, chest_item, place

OGRE_REWARD = 'TakePartyItem("misc5x")\nGiveItemCreate("SHLD26",LastTalkedToBy,0,0,0)'
OGRE, WELL = "original-source:bg2:mode2:item:14", "original-source:bg2:mode2:item:27"
OGRE_PLACE, WELL_PLACE = "original-target:bg2:mode2:target:17", "original-target:bg2:mode2:target:27"
CHEST_2, CHEST_3 = "original-target:bg2:base:target:9002", "original-target:bg2:base:target:9003"
# A chest of the editor's own: the area's first chest holds the item it moves.
CHESTS = make_are([chest_item("SYNTH001")], [("Chest", 0, 1), ("Other", 1, 0)])
# A mod adds a chest before the other one: another chest has its place in the area.
MORE_CHESTS = make_are([chest_item("SYNTH001")], [("Chest", 0, 1), ("New", 1, 0), ("Other", 1, 0)])
CHEST_ITEM, OTHER_CHEST = "are:ar0200/container/0/item/0", "are:ar0200/container/1"
# A new version's lists have a third chest in the area of the preset's chests.
CHEST_4 = "original-target:bg2:base:target:9004"
THREE_CHESTS = make_are([], [("Place2", 0, 0), ("Place3", 0, 0), ("Place4", 0, 0)])


def placed(area: bytes, *points: tuple[int, int]) -> bytes:
    """An area file with its first containers where these points are."""
    area = bytearray(area)
    offset = struct.unpack_from("<I", area, 0x70)[0]
    for index, point in enumerate(points):
        struct.pack_into("<HH", area, offset + index * 0xC0 + 0x20, *point)
    return bytes(area)


def creature(script_name: str) -> bytes:
    """A creature stored in its area with this script name."""
    data = bytearray(make_cre())
    data[0x280:0x2A0] = script_name.encode("ascii").ljust(32, b"\0")
    return bytes(data)


def ogre_dialogue(reward: str = OGRE_REWARD, *, more: bool = False) -> bytes:
    """test_reward_routes' ogre with another reward reply, or with another mod's state at the end."""
    states = [[("", 'SetGlobal("OgreTalk","LOCALS",1)', 9)] for _ in range(9)]
    states.append([('PartyHasItem("misc5x")', reward, None),
                   ('Global("OtherMod","GLOBAL",1)', 'SetGlobal("OtherMod","GLOBAL",2)', None)])
    if more:
        states.append([("", 'SetGlobal("NewMod","GLOBAL",1)', None)])
    return layout_dialogue("uhogre01", states)


class RebaseTests(RouteGame):
    def setUp(self):
        self.build("bg2ee", extra={"ar0200.are": CHESTS})
        self.earlier = self.world

    def tables(self, *, carriers=(), added=()):
        """The preset's rows as the tests resolve them for the current scans;
        the places named go missing in the new lists, which add the rows
        ``added``."""
        draft = self.draft()
        for row in draft["targets"]:
            if "original-target:" + row["id"] in carriers:
                row["binding_problem"] = CARRIER_CAN_GO_MISSING
        draft["targets"] += deepcopy(list(added))
        return draft

    def saved(self, *, record: bool = False) -> dict:
        """The preset fitted to the current scans, as the editor saves it: with
        the record of what the fit made, or as CIR 1.0.3 saved it, without."""
        fitted = self.fit()
        profile = {**fitted["profile"], "id": "saved-preset", "name": "My preset"}
        if not record:
            del fitted["plan"][original_configuration.FIT_RECORD]
        return {"profile": profile, "plan": fitted["plan"]}

    def custom(self, place: str = OTHER_CHEST) -> dict:
        """A configuration made in the editor without a preset: the chest's item
        into the other chest (two slots) and to the ogre's special place."""
        profile = new_profile(self.catalog, "saved-custom", "My own")
        profile["items"] = ["synth001"]
        plan = new_binding_plan(profile, self.catalog, self.world)
        plan["sources"] = [{"item": "synth001", "policy": "relocate", "source_ids": [CHEST_ITEM],
                            "registration_ids": []}]
        with self.resolved(self.tables()):
            special = {row["id"]: row for row in original_configuration.fit_special_places(self.catalog, self.world)}
        plan["target_registrations"] = [special[OGRE_PLACE]]
        plan["targets"] = [{"id": place, "capacity": 2, "optional": False},
                           {"id": OGRE_PLACE, "capacity": 1, "optional": False}]
        return {"profile": profile, "plan": plan}

    def made_with(self, **files: bytes) -> None:
        """The configurations of a test are made with these override files."""
        self.change(**files)
        self.earlier = self.world

    def resolved(self, tables):
        """Both of the configurator's template lookups give these rows."""
        stack = patch("flir_config.original_configuration.resolve_template", return_value=tables)
        registrations = patch("flir_config.original_registrations.resolve_template", return_value=tables)

        class Both:
            def __enter__(self):
                stack.start()
                registrations.start()

            def __exit__(self, *exc):
                registrations.stop()
                stack.stop()

        return Both()

    def change(self, **files: bytes) -> None:
        """Another version of the game's mods: these override files, then a new scan."""
        for name, data in files.items():
            path = self.game / "override" / name
            if data is None:
                path.unlink()
            else:
                path.write_bytes(data)
        self.rescan()

    def rebase(self, drafts, *, carriers=(), added=(), earlier="scan"):
        earlier = self.earlier if earlier == "scan" else earlier
        with self.resolved(self.tables(carriers=carriers, added=added)):
            return rebase_drafts(self.catalog, self.world, drafts, earlier_world=earlier)

    def assert_checks(self, draft: dict, *, carriers=(), added=()) -> None:
        """The carried-over configuration opens and passes the check that Apply runs."""
        self.assertEqual(checked_draft(self.catalog, self.world, draft), draft)
        with self.resolved(self.tables(carriers=carriers, added=added)):
            campaign = compile_campaign(draft["profile"], self.catalog, self.world, draft["plan"])
        _compile(campaign, self.world, validate_world(self.world, self.catalog), validate_catalog(self.catalog),
                 catalog=self.catalog)

    def test_a_changed_route_keeps_every_choice(self):
        # A new version prepares the ogre's conversation otherwise: the scan changes, the route does not.
        saved = self.saved()
        self.change(**{"uhogre01.dlg": ogre_dialogue(more=True)})
        with self.assertRaisesRegex(ProfileError, "belong to a different scan"):
            checked_draft(self.catalog, self.world, saved)
        [result] = self.rebase([saved])
        self.assertEqual((result["left_out"], result["notes"]), ([], []))
        draft = result["draft"]
        self.assertEqual(draft["plan"]["world_fingerprint"], self.world["fingerprint"])
        self.assertEqual(draft["profile"], {**saved["profile"], "catalog_fingerprint": self.catalog["fingerprint"]})
        ogre = next(row for row in draft["plan"]["registrations"] if row["id"] == OGRE)
        self.assertEqual(ogre["resource_sha256"],
                         next(row["sha256"] for row in self.world["resources"] if row["id"] == "dlg:uhogre01"))
        for field in ("sources", "targets", "randomization", "original_choices", "original_options"):
            self.assertEqual(draft["plan"][field], saved["plan"][field], field)
        self.assert_checks(draft)

    def test_a_route_that_no_longer_works_is_left_out_and_named(self):
        saved = self.saved()
        self.change(**{"uhogre01.dlg": ogre_dialogue('TakePartyItem("misc5x")\nGiveGoldForce(500)')})
        [result] = self.rebase([saved])
        reason = "The extended reward route for UHOGRE01 cannot be used: UHOGRE01.DLG has no reply that gives SHLD26"
        self.assertEqual(result["left_out"], [{"id": OGRE, "kind": "reward", "reason": reason},
                                              {"id": OGRE_PLACE, "kind": "place", "reason": reason}])
        self.assertEqual(result["notes"], [
            "1 reward and 1 place from it cannot be used with the game as it is now, so they are left out",
            "A left-out reward stays as the game has it"])
        draft = result["draft"]
        # Nothing moves the shield any more: it is no longer one of the configuration's items.
        self.assertEqual(draft["profile"]["items"], ["helm21"])
        self.assertEqual([row["id"] for row in draft["plan"]["registrations"]], [WELL])
        self.assertEqual(sorted(draft["plan"]["randomization"]["sources"]), [WELL])
        self.assertNotIn(OGRE_PLACE, draft["plan"]["randomization"]["targets"])
        self.assert_checks(draft)

    def test_what_the_preset_leaves_out_now_is_left_out(self):
        # The new lists put the shield's only chest on a creature that may go
        # missing: the preset fitted now has no place for the shield either.
        saved = self.saved()
        self.change(**{"uhogre01.dlg": ogre_dialogue(more=True)})
        [result] = self.rebase([saved], carriers={CHEST_2})
        self.assertEqual(result["left_out"], [{"id": OGRE, "kind": "reward", "reason": NO_PLACE},
                                              {"id": CHEST_2, "kind": "place", "reason": CARRIER_CAN_GO_MISSING}])
        self.assertEqual(result["notes"], [
            "1 reward from it cannot be used with the game as it is now, so it is left out",
            "1 place from it is on a creature that may leave, vanish or never appear, so it is left out",
            "A left-out reward stays as the game has it"])
        draft = result["draft"]
        self.assertEqual(draft["profile"]["items"], ["helm21"])
        self.assertEqual([row["id"] for row in draft["plan"]["registrations"]], [WELL])
        self.assert_checks(draft, carriers={CHEST_2})

    def test_a_reward_whose_places_were_all_left_out_is_left_out(self):
        # The player gave the shield's tier the editor's chest in place of the
        # preset's; a new version takes that chest out of the area.
        saved = self.saved()
        plan, rules = saved["plan"], saved["plan"]["randomization"]
        plan["targets"] = [row for row in plan["targets"] if row["id"] != CHEST_2]
        plan["targets"].append({"id": OTHER_CHEST, "capacity": 1, "optional": False})
        plan["target_registrations"] = [row for row in plan["target_registrations"] if row["id"] != CHEST_2]
        for table in (rules["targets"], rules["row_filters"]["targets"]):
            del table[CHEST_2]
        rules["targets"][OTHER_CHEST] = ["bg2:2"]
        rules["row_filters"]["targets"][OTHER_CHEST] = {"chance": 100, "group": ""}
        self.assert_checks(saved)
        self.change(**{"uhogre01.dlg": ogre_dialogue(more=True),
                       "ar0200.are": make_are([chest_item("SYNTH001")], [("Chest", 0, 1)])})
        [result] = self.rebase([saved])
        # The preset fitted now still has the shield; this configuration has no
        # place for it. The player is told that the preset has both.
        self.assertEqual(result["left_out"], [{"id": OTHER_CHEST, "kind": "place", "reason": TARGET_ABSENT},
                                              {"id": OGRE, "kind": "reward", "reason": NO_PLACE}])
        self.assertEqual(result["notes"], [
            "1 reward and 1 place from it cannot be used with the game as it is now, so they are left out",
            "A left-out reward stays as the game has it", PRESET_HAS_MORE])
        draft = result["draft"]
        self.assertEqual(draft["profile"]["items"], ["helm21"])
        self.assertEqual(sorted(row["id"] for row in draft["plan"]["targets"]), [CHEST_3, OGRE_PLACE, WELL_PLACE])
        self.assertEqual(sorted(draft["plan"]["randomization"]["sources"]), [WELL])
        self.assert_checks(draft)

    def test_items_no_longer_in_the_game_are_left_out(self):
        saved = self.saved()
        saved["profile"]["shop_pricing"] = {"percent": 150, "base_prices": {"helm21": 900, "shld26": 700}}
        self.change(**{"helm21.itm": None})
        [result] = self.rebase([saved])
        self.assertEqual(result["left_out"], [{"id": "helm21", "kind": "item", "reason": "item-absent"}])
        self.assertEqual(result["notes"], ["1 item from it is no longer in the game, so it is left out"])
        profile = result["draft"]["profile"]
        self.assertEqual((profile["items"], profile["shop_pricing"]),
                         (["shld26"], {"percent": 150, "base_prices": {"shld26": 700}}))
        self.assertEqual([row["id"] for row in result["draft"]["plan"]["registrations"]], [OGRE])
        self.assert_checks(result["draft"])
        # Without any of its items there is nothing to carry over.
        self.change(**{"shld26.itm": None})
        self.assertEqual(self.rebase([saved]), [{"draft": None, "reason": NOTHING_LEFT}])

    def test_the_player_keeps_slots_and_tiers(self):
        saved = self.saved()
        plan = saved["plan"]
        next(row for row in plan["targets"] if row["id"] == CHEST_3)["capacity"] = 2
        next(row for row in plan["target_registrations"] if row["id"] == CHEST_3)["capacity"] = 2
        plan["randomization"]["targets"][CHEST_3] = ["bg2:3", "bg2:2"]
        self.change(**{"uhogre01.dlg": ogre_dialogue(more=True)})
        [result] = self.rebase([saved])
        plan = result["draft"]["plan"]
        self.assertEqual(next(row for row in plan["targets"] if row["id"] == CHEST_3)["capacity"], 2)
        self.assertEqual(next(row for row in plan["target_registrations"] if row["id"] == CHEST_3)["capacity"], 2)
        self.assertEqual(plan["randomization"]["targets"][CHEST_3], ["bg2:3", "bg2:2"])
        self.assertEqual((result["draft"]["profile"]["id"], result["draft"]["profile"]["name"]),
                         ("saved-preset", "My preset"))
        self.assert_checks(result["draft"])

    def new_version(self) -> dict:
        """Another version of the game's mods whose lists have a third chest in
        the area of the preset's chests and put the shield's chest on a
        creature that may go missing; the lists, for rebase."""
        self.change(**{"uhogre01.dlg": ogre_dialogue(more=True), "ar0100.are": THREE_CHESTS})
        return {"carriers": {CHEST_2}, "added": [place("bg2:base:target:9004", "2", 2)]}

    def fitted(self, lists: dict) -> dict:
        """The preset fitted to the current scans with these lists."""
        with self.resolved(self.tables(**lists)):
            return original_configuration.fit_original_configuration(
                self.catalog, self.world, self.template, items="extended", targets="extended")

    def test_a_preset_saved_unchanged_becomes_the_preset_as_it_is_now(self):
        saved = self.saved(record=True)
        renamed = deepcopy(saved)
        renamed["profile"].update(id="saved-renamed", name="Renamed")
        self.assertTrue(original_configuration.unchanged(saved["profile"], saved["plan"]))
        self.assertTrue(original_configuration.unchanged(renamed["profile"], renamed["plan"]))
        # Mods that change nothing the preset has: the same preset, nothing to say.
        self.change(**{"uhogre01.dlg": ogre_dialogue(more=True)})
        fresh = self.fitted({})
        [result] = self.rebase([saved])
        self.assertEqual(result, {"draft": {"profile": {**fresh["profile"], "id": "saved-preset", "name": "My preset"},
                                            "plan": fresh["plan"]}, "left_out": [], "notes": []})
        # The new version: the third chest takes the shield's tier, whose chest is gone.
        self.made_with()
        lists = self.new_version()
        fresh = self.fitted(lists)
        self.assertIn(CHEST_4, [row["id"] for row in fresh["plan"]["targets"]])
        self.assertIn(OGRE, [row["id"] for row in fresh["plan"]["registrations"]])
        results = self.rebase([saved, renamed], **lists)
        for result, (identity, name) in zip(results, (("saved-preset", "My preset"), ("saved-renamed", "Renamed"))):
            draft = result["draft"]
            self.assertEqual(draft, {"profile": {**fresh["profile"], "id": identity, "name": name},
                                     "plan": fresh["plan"]})
            # Only the chest is gone; the shield stays, in the third chest's tier.
            self.assertEqual(result["left_out"], [{"id": CHEST_2, "kind": "place", "reason": CARRIER_CAN_GO_MISSING}])
            self.assertEqual(result["notes"], [
                "1 place from it is on a creature that may leave, vanish or never appear, so it is left out",
                PRESET_FOLLOWED])
            # Still the preset unchanged, for the next time the mods change.
            self.assertTrue(original_configuration.unchanged(draft["profile"], draft["plan"]))
            self.assert_checks(draft, **lists)

    def test_a_preset_changed_in_the_editor_keeps_its_choices(self):
        saved = self.saved(record=True)
        next(row for row in saved["plan"]["targets"] if row["id"] == CHEST_3)["capacity"] = 2
        next(row for row in saved["plan"]["target_registrations"] if row["id"] == CHEST_3)["capacity"] = 2
        saved["plan"]["randomization"]["targets"][CHEST_3] = ["bg2:3", "bg2:3"]
        self.assert_checks(saved)
        self.assertFalse(original_configuration.unchanged(saved["profile"], saved["plan"]))
        lists = self.new_version()
        [result] = self.rebase([saved], **lists)
        # Carried over as before; the player is told that the preset has more now.
        self.assertEqual(result["left_out"], [{"id": CHEST_2, "kind": "place", "reason": CARRIER_CAN_GO_MISSING},
                                              {"id": OGRE, "kind": "reward", "reason": NO_PLACE}])
        self.assertEqual(result["notes"], [
            "1 reward from it cannot be used with the game as it is now, so it is left out",
            "1 place from it is on a creature that may leave, vanish or never appear, so it is left out",
            "A left-out reward stays as the game has it", PRESET_HAS_MORE])
        plan = result["draft"]["plan"]
        self.assertEqual(sorted((row["id"], row["capacity"]) for row in plan["targets"]),
                         [(CHEST_3, 2), (OGRE_PLACE, 1), (WELL_PLACE, 1)])
        self.assertNotIn(original_configuration.FIT_RECORD, plan)
        self.assert_checks(result["draft"], **lists)

    def test_a_preset_configuration_saved_by_1_0_3_keeps_its_choices(self):
        # Without the record it cannot be told whether it was changed: it is
        # carried over as one changed in the editor.
        saved = self.saved()
        lists = self.new_version()
        [result] = self.rebase([saved], **lists)
        self.assertEqual(result["notes"][-1], PRESET_HAS_MORE)
        plan = result["draft"]["plan"]
        self.assertEqual(sorted(row["id"] for row in plan["targets"]), [CHEST_3, OGRE_PLACE, WELL_PLACE])
        self.assertNotIn(original_configuration.FIT_RECORD, plan)
        self.assert_checks(result["draft"], **lists)

    def test_choices_made_in_the_editor_follow_the_world_scan(self):
        custom = self.custom()
        self.assert_checks(custom)
        self.change(**{"uhogre01.dlg": ogre_dialogue(more=True)})
        [result] = self.rebase([custom])
        self.assertEqual((result["left_out"], result["notes"]), ([], []))
        draft = result["draft"]
        for field in ("sources", "targets", "target_registrations"):
            self.assertEqual(draft["plan"][field], custom["plan"][field], field)
        self.assert_checks(draft)
        # Without the scan the configuration was made with, the chest in the
        # area cannot be told to be the same one.
        [result] = self.rebase([custom], earlier=None)
        self.assertEqual(result["left_out"], [{"id": OTHER_CHEST, "kind": "place", "reason": TARGET_UNVERIFIED}])
        self.assertEqual(result["notes"],
                         ["1 place from it could not be checked against the scan it was made with, so it is left out"])
        self.assertEqual([row["id"] for row in result["draft"]["plan"]["targets"]], [OGRE_PLACE])
        # A mod adds a chest before it and moves it: the chest of that name is
        # the one chosen, wherever it stands.
        self.change(**{"ar0200.are": placed(MORE_CHESTS, (111, 222), (111, 222), (900, 40))})
        [result] = self.rebase([custom])
        self.assertEqual((result["left_out"], result["notes"]), ([], []))
        draft = result["draft"]
        self.assertEqual(draft["plan"]["targets"], [{"id": "are:ar0200/container/2", "capacity": 2, "optional": False},
                                                    {"id": OGRE_PLACE, "capacity": 1, "optional": False}])
        self.assertEqual(draft["plan"]["sources"], custom["plan"]["sources"])
        self.assert_checks(draft)
        # The item's record is gone: nothing of the configuration can be used.
        self.change(**{"ar0200.are": make_are([], [("Chest", 0, 0), ("Other", 0, 0)])})
        self.assertEqual(self.rebase([custom]), [{"draft": None, "reason": NOTHING_LEFT}])

    def test_a_reward_follows_its_chest_by_name(self):
        # Another chest comes first in the area now, with the same item in it:
        # its record has the identity the reward had. The reward stays the
        # chest's, and the place follows the other chest.
        custom = self.custom()
        custom["plan"]["randomization"] = {
            "loss_percent": 0, "unrandomized_percent": 0,
            "sources": {CHEST_ITEM: {"tier": "bg2:2", "loss_exempt": False,
                                     "exclude_targets": [{"id": OTHER_CHEST, "ordinal": 2}]}},
            "targets": {OTHER_CHEST: ["bg2:2", "bg2:2"], OGRE_PLACE: ["bg2:2"]}}
        self.assert_checks(custom)
        self.change(**{"ar0200.are": make_are([chest_item("SYNTH001"), chest_item("SYNTH001")],
                                              [("Loot", 0, 1), ("Chest", 1, 1), ("Other", 2, 0)])})
        [result] = self.rebase([custom])
        self.assertEqual(result["left_out"], [])
        plan = result["draft"]["plan"]
        reward, place = "are:ar0200/container/1/item/1", "are:ar0200/container/2"
        self.assertEqual(plan["sources"][0]["source_ids"], [reward])
        self.assertEqual(plan["targets"][0]["id"], place)
        self.assertEqual(plan["randomization"]["sources"],
                         {reward: {"tier": "bg2:2", "loss_exempt": False,
                                   "exclude_targets": [{"id": place, "ordinal": 2}]}})
        self.assertEqual(plan["randomization"]["targets"], {place: ["bg2:2", "bg2:2"], OGRE_PLACE: ["bg2:2"]})
        self.assert_checks(result["draft"])
        # A chest with fewer of the item than before keeps them for the first records.
        self.made_with(**{"ar0200.are": make_are([chest_item("SYNTH001"), chest_item("SYNTH001")],
                                                 [("Chest", 0, 2), ("Other", 2, 0)])})
        first, second = self.custom(), self.custom()
        second["plan"]["sources"][0]["source_ids"] = ["are:ar0200/container/0/item/1"]
        self.change(**{"ar0200.are": CHESTS})
        [result] = self.rebase([second])
        self.assertEqual(result, {"draft": None, "reason": NOTHING_LEFT})
        [result] = self.rebase([first])
        self.assertEqual((result["left_out"], result["draft"]["plan"]["sources"][0]["source_ids"]), ([], [CHEST_ITEM]))

    def test_of_several_chests_of_one_name_the_nearest_is_the_one(self):
        # Two piles of one name; the player chose the second.
        self.made_with(**{"ar0200.are": placed(
            make_are([chest_item("SYNTH001")], [("Chest", 0, 1), ("Pile", 1, 0), ("Pile", 1, 0)]),
            (111, 222), (100, 100), (500, 500))})
        custom = self.custom("are:ar0200/container/2")
        self.assert_checks(custom)

        def piles(*points: tuple[int, int]) -> dict:
            names = [("Chest", 0, 1)] + [("Pile", 1, 0)] * len(points)
            self.change(**{"ar0200.are": placed(make_are([chest_item("SYNTH001")], names), (111, 222), *points)})
            [result] = self.rebase([custom])
            return result

        # A mod moves them a little and lists them the other way round.
        result = piles((498, 505), (102, 100))
        self.assertEqual(result["left_out"], [])
        self.assertEqual(result["draft"]["plan"]["targets"][0]["id"], "are:ar0200/container/1")
        self.assert_checks(result["draft"])
        # Two as near to where it stood cannot be told apart.
        result = piles((400, 500), (600, 500))
        self.assertEqual(result["left_out"],
                         [{"id": "are:ar0200/container/2", "kind": "place", "reason": TARGET_AMBIGUOUS}])
        self.assertEqual(result["notes"],
                         ["1 place from it cannot be used with the game as it is now, so it is left out"])
        # The one left stands where the other one stood: the chosen one is gone.
        result = piles((110, 100))
        self.assertEqual(result["left_out"],
                         [{"id": "are:ar0200/container/2", "kind": "place", "reason": TARGET_ABSENT}])
        # A reward in a pile that cannot be told apart is left out too; the
        # other chest's reward stays.
        def area(*points: tuple[int, int]) -> bytes:
            return placed(make_are([chest_item("SYNTH001"), chest_item("HELM21")],
                                   [("Pile", 0, 1), ("Other", 1, 1), ("Pile", 2, 0)]), *points)

        self.made_with(**{"ar0200.are": area((100, 100), (111, 222), (500, 500))})
        custom = self.custom()
        custom["profile"]["items"] = ["helm21", "synth001"]
        custom["plan"]["sources"].append({"item": "helm21", "policy": "relocate",
                                          "source_ids": ["are:ar0200/container/1/item/1"], "registration_ids": []})
        self.assert_checks(custom)
        self.change(**{"ar0200.are": area((300, 50), (111, 222), (300, 150))})
        [result] = self.rebase([custom])
        self.assertEqual(result["left_out"], [{"id": CHEST_ITEM, "kind": "reward", "reason": SOURCE_AMBIGUOUS}])
        self.assertEqual(result["draft"]["profile"]["items"], ["helm21"])
        self.assert_checks(result["draft"])

    def test_a_creature_is_the_one_of_its_script_name(self):
        self.made_with(**{"ar0200.are": make_are([chest_item("SYNTH001")], [("Chest", 0, 1)],
                                                 [("Guard", creature("GUARD1")), ("Boss", creature("BOSSDV"))])})
        custom = self.custom("are:ar0200/actor/1")
        self.assertEqual(checked_draft(self.catalog, self.world, custom), custom)
        # A mod adds a creature before it and calls it otherwise in the area file.
        self.change(**{"ar0200.are": make_are(
            [chest_item("SYNTH001")], [("Chest", 0, 1)],
            [("Guard", creature("GUARD1")), ("Mage", creature("MAGEDV")), ("Big Boss", creature("BOSSDV"))])})
        [result] = self.rebase([custom])
        self.assertEqual(result["left_out"], [])
        self.assertEqual(result["draft"]["plan"]["targets"][0]["id"], "are:ar0200/actor/2")
        self.assertEqual(checked_draft(self.catalog, self.world, result["draft"]), result["draft"])
        # Another script name is another creature.
        self.change(**{"ar0200.are": make_are([chest_item("SYNTH001")], [("Chest", 0, 1)],
                                              [("Guard", creature("GUARD1")), ("Boss", creature("BOSS2"))])})
        [result] = self.rebase([custom])
        self.assertEqual(result["left_out"], [{"id": "are:ar0200/actor/1", "kind": "place", "reason": TARGET_ABSENT}])

    def test_a_configuration_of_these_scans_stays_as_it_is(self):
        saved = self.saved()
        self.assertEqual(self.rebase([saved]), [{"draft": checked_draft(self.catalog, self.world, saved),
                                                 "left_out": [], "notes": []}])

    def test_configurations_that_cannot_be_carried_over(self):
        saved = self.saved()
        self.change(**{"uhogre01.dlg": ogre_dialogue(more=True)})
        damaged = [[], {"profile": {}, "plan": {}}, {"profile": saved["profile"], "plan": []}]
        for field, value in (("sources", [{"item": "helm21", "policy": "relocate", "source_ids": "x"}]),
                             ("targets", [{"id": CHEST_2, "capacity": 0, "optional": False}]),
                             ("targets", [{"id": CHEST_2, "capacity": 1 << 32, "optional": False}]),
                             ("schema", "flir-binding-plan-v0"), ("original_template_id", None),
                             ("notes", [])):
            draft = deepcopy(saved)
            draft["plan"][field] = value
            damaged.append(draft)
        draft = deepcopy(saved)
        draft["plan"]["randomization"]["sources"][OGRE] = {"tier": "bg2:2"}
        damaged.append(draft)
        self.assertEqual(self.rebase(damaged), [{"draft": None, "reason": UNREADABLE}] * len(damaged))
        # A preset of another game, presets and options this version no longer
        # has, and an option this game cannot apply any more.
        other = deepcopy(saved)
        other["plan"]["original_template_id"] = "bg1-original"
        [result] = self.rebase([other])
        self.assertTrue(result["reason"].startswith(PRESET_UNFIT), result)
        other["plan"]["original_template_id"] = "bg2-retired"
        self.assertEqual(self.rebase([other]), [
            {"draft": None, "reason": PRESET_UNFIT + "Unknown original template: bg2-retired"}])
        other = deepcopy(saved)
        other["plan"]["original_options"]["retired_option"] = True
        self.assertEqual(rebase_drafts(self.catalog, self.world, [other]), [
            {"draft": None, "reason": PRESET_UNFIT + "Unknown original-template option"}])
        tables = self.tables()
        tables["options"]["flail_heads"] = True
        with self.resolved(tables):
            [result] = rebase_drafts(self.catalog, self.world, [saved])
        self.assertTrue(result["reason"].startswith(PRESET_UNFIT + "The flail heads option needs"), result)

    def test_one_fit_for_the_configurations_of_one_preset(self):
        # One saved unchanged, its copy as CIR 1.0.3 saved it.
        saved = self.saved(record=True)
        copy = deepcopy(saved)
        copy["profile"]["id"] = "saved-copy"
        del copy["plan"][original_configuration.FIT_RECORD]
        self.change(**{"uhogre01.dlg": ogre_dialogue(more=True)})
        with patch.object(original_configuration, "_fit", wraps=original_configuration._fit) as fit:
            results = self.rebase([saved, copy])
        self.assertEqual(fit.call_count, 1)
        self.assertEqual([result["draft"]["profile"]["id"] for result in results], ["saved-preset", "saved-copy"])

    def beside(self) -> dict:
        """The preset with the chest's item, chosen in the editor, moved too."""
        saved = self.saved()
        saved["profile"]["items"] = ["helm21", "shld26", "synth001"]
        saved["plan"]["sources"].append({"item": "synth001", "policy": "relocate", "source_ids": [CHEST_ITEM],
                                         "registration_ids": []})
        saved["plan"]["randomization"]["sources"][CHEST_ITEM] = {"tier": "bg2:2", "loss_exempt": False,
                                                                 "exclude_targets": []}
        saved["plan"]["randomization"]["row_filters"]["sources"][CHEST_ITEM] = {"chance": 100, "group": ""}
        return saved

    def test_a_record_chosen_in_the_editor_that_an_option_would_move(self):
        # A row of the preset's lists that an option the configuration leaves
        # off would bring moves the chest's item too. The fit does not consider
        # it: the record stays the player's own choice.
        saved = self.beside()
        self.change(**{"uhogre01.dlg": ogre_dialogue(more=True)})
        tables = self.tables()
        ogre = next(row for row in tables["items"] if "original-source:" + row["id"] == OGRE)
        tables["items"].append({**deepcopy(ogre), "id": "bg2:mode2:item:99", "enabled": False,
                                "source_ids": [CHEST_ITEM]})
        with self.resolved(tables):
            [result] = rebase_drafts(self.catalog, self.world, [saved], earlier_world=self.earlier)
        self.assertEqual(result["left_out"], [])
        self.assertIn({"item": "synth001", "policy": "relocate", "source_ids": [CHEST_ITEM], "registration_ids": []},
                      result["draft"]["plan"]["sources"])
        self.assert_checks(result["draft"])
        # Enabled, the row is the preset's: what the fit makes of it decides.
        tables["items"][-1]["enabled"] = True
        with self.resolved(tables):
            [result] = rebase_drafts(self.catalog, self.world, [saved], earlier_world=self.earlier)
        self.assertEqual(result["left_out"], [{
            "id": CHEST_ITEM, "kind": "reward",
            "reason": "Unsupported original quest source: original-source:bg2:mode2:item:99"}])

    def test_a_record_chosen_in_the_editor_beside_a_preset(self):
        # The configuration also moves the chest's item, chosen in the editor.
        saved = self.beside()
        self.change(**{"uhogre01.dlg": ogre_dialogue(more=True)})
        [result] = self.rebase([saved])
        self.assertEqual(result["left_out"], [])
        self.assertIn({"item": "synth001", "policy": "relocate", "source_ids": [CHEST_ITEM], "registration_ids": []},
                      result["draft"]["plan"]["sources"])
        self.assert_checks(result["draft"])
        # The item's record is no longer in the chest: it is left out, the rest stays.
        self.change(**{"ar0200.are": make_are([], [("Chest", 0, 0), ("Other", 0, 0)])})
        [result] = self.rebase([saved])
        self.assertEqual(result["left_out"], [{"id": CHEST_ITEM, "kind": "reward", "reason": SOURCE_ABSENT}])
        self.assertEqual(result["draft"]["profile"]["items"], ["helm21", "shld26"])
        self.assert_checks(result["draft"])
