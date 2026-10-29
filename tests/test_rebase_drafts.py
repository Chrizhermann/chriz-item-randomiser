"""Saved configurations carried over to new scans of the same game; no real games.

After the game's mods change (a new version of Chriz Item Randomiser among
them) the configurator scans the game again, and the configurations saved
with the earlier scans no longer open. rebase_drafts carries them over: the
player's choices stay, the evidence comes from the new scans, and only what
the new scans no longer offer is left out and named.
"""

from copy import deepcopy
from pathlib import Path
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
from flir_config.rebase import (NO_PLACE, NOTHING_LEFT, PRESET_UNFIT, SOURCE_ABSENT, TARGET_CHANGED,
                                TARGET_UNVERIFIED, UNREADABLE, rebase_drafts)
from flir_config.sources import new_binding_plan, validate_world
from test_mode2_rewards import layout_dialogue
from test_postinstall_world_formats import make_are
from test_reward_routes import RouteGame, chest_item

OGRE_REWARD = 'TakePartyItem("misc5x")\nGiveItemCreate("SHLD26",LastTalkedToBy,0,0,0)'
OGRE, WELL = "original-source:bg2:mode2:item:14", "original-source:bg2:mode2:item:27"
OGRE_PLACE, WELL_PLACE = "original-target:bg2:mode2:target:17", "original-target:bg2:mode2:target:27"
CHEST_2, CHEST_3 = "original-target:bg2:base:target:9002", "original-target:bg2:base:target:9003"
# A chest of the editor's own: the area's first chest holds the item it moves.
CHESTS = make_are([chest_item("SYNTH001")], [("Chest", 0, 1), ("Other", 1, 0)])
# A mod adds a chest before the other one: another chest has its place in the area.
MORE_CHESTS = make_are([chest_item("SYNTH001")], [("Chest", 0, 1), ("New", 1, 0), ("Other", 1, 0)])
CHEST_ITEM, OTHER_CHEST = "are:ar0200/container/0/item/0", "are:ar0200/container/1"


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

    def tables(self, *, carriers=()):
        """The preset's rows as the tests resolve them for the current scans;
        the places named go missing in the new lists."""
        draft = self.draft()
        for row in draft["targets"]:
            if "original-target:" + row["id"] in carriers:
                row["binding_problem"] = CARRIER_CAN_GO_MISSING
        return draft

    def saved(self) -> dict:
        """The preset fitted to the current scans, as the editor saves it."""
        fitted = self.fit()
        profile = {**fitted["profile"], "id": "saved-preset", "name": "My preset"}
        return {"profile": profile, "plan": fitted["plan"]}

    def custom(self) -> dict:
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
        plan["targets"] = [{"id": OTHER_CHEST, "capacity": 2, "optional": False},
                           {"id": OGRE_PLACE, "capacity": 1, "optional": False}]
        return {"profile": profile, "plan": plan}

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

    def rebase(self, drafts, *, carriers=(), earlier="scan"):
        earlier = self.earlier if earlier == "scan" else earlier
        with self.resolved(self.tables(carriers=carriers)):
            return rebase_drafts(self.catalog, self.world, drafts, earlier_world=earlier)

    def assert_checks(self, draft: dict, *, carriers=()) -> None:
        """The carried-over configuration opens and passes the check that Apply runs."""
        self.assertEqual(checked_draft(self.catalog, self.world, draft), draft)
        with self.resolved(self.tables(carriers=carriers)):
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
        # preset's; a new version adds a chest before it in the area.
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
        self.change(**{"uhogre01.dlg": ogre_dialogue(more=True), "ar0200.are": MORE_CHESTS})
        [result] = self.rebase([saved])
        # The preset fitted now still has the shield; this configuration has no place for it.
        self.assertEqual(result["left_out"], [{"id": OTHER_CHEST, "kind": "place", "reason": TARGET_CHANGED},
                                              {"id": OGRE, "kind": "reward", "reason": NO_PLACE}])
        self.assertEqual(result["notes"], [
            "1 reward and 1 place from it cannot be used with the game as it is now, so they are left out",
            "A left-out reward stays as the game has it"])
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
                         ["1 place from it cannot be used with the game as it is now, so it is left out"])
        self.assertEqual([row["id"] for row in result["draft"]["plan"]["targets"]], [OGRE_PLACE])
        # A mod adds a chest before it: another chest now has its place in the area.
        self.change(**{"ar0200.are": MORE_CHESTS})
        [result] = self.rebase([custom])
        self.assertEqual(result["left_out"], [{"id": OTHER_CHEST, "kind": "place", "reason": TARGET_CHANGED}])
        self.assertEqual(result["draft"]["plan"]["sources"], custom["plan"]["sources"])
        # The item's record is gone: nothing of the configuration can be used.
        self.change(**{"ar0200.are": make_are([], [("Chest", 0, 0), ("Other", 0, 0)])})
        self.assertEqual(self.rebase([custom]), [{"draft": None, "reason": NOTHING_LEFT}])

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
        saved = self.saved()
        copy = deepcopy(saved)
        copy["profile"]["id"] = "saved-copy"
        self.change(**{"uhogre01.dlg": ogre_dialogue(more=True)})
        with patch.object(original_configuration, "_fit", wraps=original_configuration._fit) as fit:
            results = self.rebase([saved, copy])
        self.assertEqual(fit.call_count, 1)
        self.assertEqual([result["draft"]["profile"]["id"] for result in results], ["saved-preset", "saved-copy"])

    def test_a_record_chosen_in_the_editor_beside_a_preset(self):
        # The configuration also moves the chest's item, chosen in the editor.
        saved = self.saved()
        saved["profile"]["items"] = ["helm21", "shld26", "synth001"]
        saved["plan"]["sources"].append({"item": "synth001", "policy": "relocate", "source_ids": [CHEST_ITEM],
                                         "registration_ids": []})
        saved["plan"]["randomization"]["sources"][CHEST_ITEM] = {"tier": "bg2:2", "loss_exempt": False,
                                                                 "exclude_targets": []}
        saved["plan"]["randomization"]["row_filters"]["sources"][CHEST_ITEM] = {"chance": 100, "group": ""}
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
