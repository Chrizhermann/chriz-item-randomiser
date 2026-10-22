"""Original-table configuration checks using a synthetic game and saved scan."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from flir_config.original_configuration import build_original_configuration  # noqa: E402
from flir_config.profiles import ProfileError  # noqa: E402
from flir_config.resources import scan_game  # noqa: E402
from flir_config.sources import scan_world  # noqa: E402
from test_postinstall_blpool_script import write_registry  # noqa: E402
from test_postinstall_resources import make_game, make_itm, mark_eet, snapshot  # noqa: E402
from test_postinstall_world_formats import item_record, make_are, make_cre  # noqa: E402


def source_row(identity, *, adapter="physical", item="synth001", source_ids=None,
               ident="pair", tier="24", absent=False, resource=None):
    return {"id": identity, "source_ref": "lists/items/base/bg1.2da:1",
            "campaign": "bg1", "family": "base", "source": "area.are",
            "ident": ident, "token": "synth001.itm", "tier": tier,
            "occurrence": 1, "chance": 100, "enabled": True,
            "available": not absent,
            "availability_reason": ("original-source-has-no-matching-record" if absent else None),
            "binding_problem": ("original-source-has-no-matching-record" if absent else None),
            "adapter": adapter, "item_resref": item,
            "source_resource": (resource or ("itm:synth001" if adapter == "virtual"
                                             else "are:area")),
            "source_ids": source_ids or [], "replacement": "blank"}


def target_row(identity, ident, tier):
    return {"id": identity, "source_ref": "lists/locations/base/bg1.2da:1",
            "endpoint_id": "row-1", "endpoint_kind": "container",
            "endpoint_source_ref": "lists/locations/base/bg1.2da:1",
            "campaign": "bg1", "family": "base", "location": "area.are",
            "target": "Container", "area": "area", "ident": ident,
            "tier": tier, "occurrence": 1, "chance": 100,
            "area_resource": "are:area", "creature_resource": None,
            "container_name": "container", "members": [], "target_ids": [],
            "binding_problem": None, "enabled": True}


class OriginalConfigurationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="flir-original-configuration-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "game"
        item = bytearray(make_itm()) + bytearray(0x38)
        struct.pack_into("<IH", item, 0x64, 0x72, 1)
        struct.pack_into("<H", item, 0x72 + 0x22, 7)
        make_game(self.root, [("SYNTH001", bytes(item))])
        mark_eet(self.root)
        (self.root / "override" / "area.are").write_bytes(
            make_are([item_record()], [("Source", 0, 1), ("Target A", 1, 0),
                                       ("Target B", 1, 0)]))
        self.catalog = scan_game(self.root)
        self.world = scan_world(self.catalog)
        self.physical = next(row["id"] for row in self.world["sources"]
                             if row["resource"] == "are:area")

    def _draft(self, *, absent=False):
        return {"name": "Original", "choices": {"items": "standard", "targets": "standard"},
                "options": {"scrolls": False, "loss_mode": "some", "loss_percent": 15,
                            "unrandomized_percent": 30},
                "items": [source_row("source-1", source_ids=[self.physical], absent=absent),
                          source_row("virtual-1", adapter="virtual", ident="x", tier="25")],
                "targets": [target_row("target-1", "pair", "24"),
                            target_row("target-2", "pair", "25")]}

    def test_physical_and_virtual_rows_preserve_authored_metadata_and_charges(self):
        before = snapshot(self.root)
        with patch("flir_config.original_configuration.resolve_template",
                   return_value=self._draft()):
            result = build_original_configuration(self.catalog, self.world, "eet-original")
        plan = result["plan"]
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(result["profile"]["items"], ["synth001"])
        self.assertEqual(plan["original_choices"], {"items": "standard", "targets": "standard"})
        self.assertEqual(plan["original_options"]["unrandomized_percent"], 30)
        self.assertEqual(plan["randomization"]["loss_percent"], 15)
        self.assertEqual(plan["randomization"]["overflow"], True)
        self.assertEqual(plan["sources"][0]["source_ids"], [self.physical])
        virtual = plan["registrations"][0]
        self.assertEqual(virtual["adapter_id"], "virtual-stock-v1")
        self.assertEqual(virtual["payload"]["charges"], [7, 0, 0])
        self.assertEqual(plan["sources"][0]["registration_ids"], [virtual["id"]])
        self.assertEqual(len(plan["target_registrations"]), 2)
        physical_rule = plan["randomization"]["sources"][self.physical]
        self.assertEqual(physical_rule["tier"], "bg1:24")
        self.assertEqual(physical_rule["exclude_targets"], [
            {"id": "original-target:target-1", "ordinal": 1},
            {"id": "original-target:target-2", "ordinal": 1}])
        self.assertEqual(plan["randomization"]["targets"]["original-target:target-2"],
                         ["bg1:25"])
        self.assertEqual(plan["randomization"]["row_filters"], {
            "sources": {self.physical: {"chance": 100, "group": "bg1:pair"},
                        virtual["id"]: {"chance": 100, "group": ""}},
            "targets": {"original-target:target-1": {"chance": 100, "group": "bg1:pair"},
                        "original-target:target-2": {"chance": 100, "group": "bg1:pair"}}})

    def test_authored_chances_and_tome_variants_follow_exact_selected_row(self):
        draft = self._draft()
        draft["items"][0].update(chance=45, ident="b2c")
        draft["targets"][0].update(chance=60, ident="b2c")
        draft["options"]["totsc_tome"] = True
        with patch("flir_config.original_configuration.resolve_template", return_value=draft):
            plan = build_original_configuration(self.catalog, self.world, "eet-original")["plan"]
        self.assertEqual(plan["randomization"]["row_filters"]["sources"][self.physical],
                         {"chance": 45, "group": "bg1:b2c"})
        self.assertEqual(plan["randomization"]["row_filters"]["targets"]["original-target:target-1"],
                         {"chance": 60, "group": "bg1:b2c"})
        self.assertEqual(plan["randomization"]["sources"][self.physical]["reward_variants"],
                         [f"book0{i}" for i in range(3, 9)])
        virtual = plan["registrations"][0]["id"]
        self.assertNotIn("reward_variants", plan["randomization"]["sources"][virtual])
        draft["options"]["totsc_tome"] = False
        with patch("flir_config.original_configuration.resolve_template", return_value=draft):
            disabled = build_original_configuration(self.catalog, self.world, "eet-original")["plan"]
        self.assertNotIn("reward_variants", disabled["randomization"]["sources"][self.physical])

    def test_rewards_cir_did_not_prepare_stay_as_installed(self):
        draft = self._draft()
        draft["items"].append(source_row("pool", adapter="scripted", resource="bcs:blpool",
                                         item="blun12", ident="x"))
        draft["items"].append(source_row("unshey", adapter="unshey", resource="cre:ogreunsh",
                                         item="belt04", ident="x"))
        draft["targets"].append({**target_row("altar", "x", "24"), "target": "troalt.bcs"})
        before = snapshot(self.root)
        for statuses, expected in (
                (None, ["TROALT.BCS was not prepared: this version of Chriz Item Randomiser is not installed",
                        "BLPOOL.BCS was not prepared: this version of Chriz Item Randomiser is not installed",
                        "UNSHEY.DLG was not prepared: this version of Chriz Item Randomiser is not installed"]),
                ({"quest_pool": "grant-missing", "quest_altar": "absent", "unshey": "item-missing"},
                 ["TROALT.BCS was left as installed (the game has no such file)",
                  "BLPOOL.BCS was left as installed (it no longer gives the reward)",
                  "UNSHEY.DLG was left as installed (it no longer takes the quest item)"])):
            if statuses:
                write_registry(self.root, **statuses)
                before = snapshot(self.root)
            with self.subTest(statuses=statuses), \
                    patch("flir_config.original_configuration.resolve_template", return_value=draft):
                result = build_original_configuration(self.catalog, self.world, "eet-original")
            self.assertEqual(result["left_out"], expected)
            plan = result["plan"]
            self.assertEqual(result["profile"]["items"], ["synth001"])
            self.assertEqual(len(plan["target_registrations"]), 2)
            self.assertFalse(any(row["id"].endswith(("pool", "unshey", "altar"))
                                 for row in plan["registrations"] + plan["target_registrations"]))
            self.assertEqual(snapshot(self.root), before)

    def test_flail_heads_option_needs_the_prepared_conversation(self):
        draft = self._draft()
        draft["options"]["flail_heads"] = True
        for statuses in (None, {"flail_heads": "dialogue-unrecognised"}):
            if statuses:
                write_registry(self.root, **statuses)
            with self.subTest(statuses=statuses), \
                    patch("flir_config.original_configuration.resolve_template", return_value=draft), \
                    self.assertRaisesRegex(ProfileError, "flail heads option needs.*KPGLAI01.DLG"):
                build_original_configuration(self.catalog, self.world, "eet-original")
        write_registry(self.root)
        with patch("flir_config.original_configuration.resolve_template", return_value=draft):
            self.assertEqual(build_original_configuration(self.catalog, self.world, "eet-original")["left_out"], [])

    def test_preset_items_from_an_absent_mod_are_left_out_and_named(self):
        draft = self._draft()
        for identity, item in (("rr-1", "rr#buc03"), ("rr-2", "rr#bow06")):
            row = source_row(identity, item=item, ident="x")
            row.update(optional_mod="Rogue Rebalancing", available=False,
                       availability_reason="optional-item-absent", binding_problem="optional-item-absent")
            draft["items"].append(row)
        with patch("flir_config.original_configuration.resolve_template", return_value=draft):
            result = build_original_configuration(self.catalog, self.world, "eet-original")
        self.assertEqual(result["absent_mods"],
                         ["Rogue Rebalancing is not installed, so 2 rewards from it are left out"])
        self.assertEqual(result["profile"]["items"], ["synth001"])
        self.assertFalse(any(source["item"].startswith("rr#") for source in result["plan"]["sources"]))
        # Any other absent item does not fit this game either; it is left out and named.
        draft["items"][-1].update(optional_mod=None, availability_reason="item-definition-absent",
                                  binding_problem="item-definition-absent")
        with patch("flir_config.original_configuration.resolve_template", return_value=draft):
            result = build_original_configuration(self.catalog, self.world, "eet-original")
        self.assertEqual(result["absent_mods"],
                         ["Rogue Rebalancing is not installed, so 1 reward from it is left out"])
        self.assertEqual(result["not_used"], [{"id": "rr-2", "row": "lists/items/base/bg1.2da:1",
                                               "kind": "reward", "reason": "item-definition-absent"}])
        self.assertEqual(result["not_used_notes"], [
            "1 reward from the lists cannot be used in this game, so it is left out",
            "A left-out reward stays as the game has it"])

    def test_an_installed_mod_without_the_presets_items_is_not_called_absent(self):
        # IWDification is installed (it added CDIA101), but not the part that adds the preset's items.
        (self.root / "override" / "cdia101.itm").write_bytes(make_itm())
        catalog = scan_game(self.root)
        world = scan_world(catalog)
        draft = self._draft()
        for identity, item, mod in (("iwd-1", "cdia126", "IWDification"), ("iwd-2", "cdia226", "IWDification"),
                                    ("rr-1", "rr#buc03", "Rogue Rebalancing")):
            row = source_row(identity, item=item, ident="x")
            row.update(optional_mod=mod, available=False, availability_reason="optional-item-absent",
                       binding_problem="optional-item-absent")
            draft["items"].append(row)
        with patch("flir_config.original_configuration.resolve_template", return_value=draft):
            result = build_original_configuration(catalog, world, "eet-original")
        self.assertEqual(result["absent_mods"], [
            "2 rewards need items of IWDification that this game does not have, so they are left out",
            "Rogue Rebalancing is not installed, so 1 reward from it is left out"])
        draft["items"] = [row for row in draft["items"] if row["id"] != "iwd-2"]
        with patch("flir_config.original_configuration.resolve_template", return_value=draft):
            result = build_original_configuration(catalog, world, "eet-original")
        self.assertEqual(result["absent_mods"][0],
                         "1 reward needs an item of IWDification that this game does not have, so it is left out")

    def test_quests_cir_cannot_change_yet_are_left_out_and_named_apart(self):
        draft = self._draft()
        reward = source_row("jerlia", adapter="scripted", resource="dlg:jugjer01", item="sw1h26", ident="x")
        reward.update(available=False, availability_reason="item-definition-absent",
                      binding_problem="route-not-supported")
        king = target_row("sahuagin-king", "x", "24")
        thief = target_row("vampire-or-thief", "x", "24")
        for place, target, area in ((king, "tadpoles1.fl", "ar2300"), (thief, "chapter3.fl", "blank")):
            place.update(target=target, area=area, area_resource=f"are:{area}", container_name=None,
                         endpoint_id=None, endpoint_kind=None, binding_problem="route-not-supported")
        lost = target_row("lost-place", "x", "24")
        lost["area_resource"] = "are:gone"
        draft["items"].append(reward)
        draft["targets"] += [king, thief, lost]
        with patch("flir_config.original_configuration.resolve_template", return_value=draft):
            result = build_original_configuration(self.catalog, self.world, "eet-original")
        self.assertEqual([(row["id"], row["kind"], row["reason"]) for row in result["not_used"]], [
            ("jerlia", "reward", "route-not-supported"),
            ("sahuagin-king", "place", "route-not-supported"),
            ("vampire-or-thief", "place", "route-not-supported"),
            ("lost-place", "place", "area-resource-absent")])
        self.assertEqual(result["not_used_notes"], [
            "1 place from the lists cannot be used in this game, so it is left out",
            "1 reward and 2 places from the lists belong to quests that Chriz Item Randomiser "
            "cannot change yet, so they are left out",
            "A left-out reward stays as the game has it"])
        self.assertEqual(len(result["plan"]["target_registrations"]), 2)
        # Alone, one place: nothing is said about rewards.
        draft["items"].remove(reward)
        draft["targets"] = [row for row in draft["targets"] if row not in (thief, lost)]
        with patch("flir_config.original_configuration.resolve_template", return_value=draft):
            result = build_original_configuration(self.catalog, self.world, "eet-original")
        self.assertEqual(result["not_used_notes"], [
            "1 place from the lists belongs to a quest that Chriz Item Randomiser cannot change yet, "
            "so it is left out"])

    def test_rows_this_game_cannot_use_are_left_out_and_named(self):
        draft = self._draft()
        ambiguous = source_row("ambiguous", source_ids=[self.physical], ident="x")
        ambiguous["binding_problem"] = "ambiguous-source-records"
        twice = source_row("twice", source_ids=[self.physical], ident="x")
        lost = target_row("lost-place", "x", "24")
        lost["area_resource"] = "are:gone"
        draft["items"] += [ambiguous, twice]
        draft["targets"].append(lost)
        with patch("flir_config.original_configuration.resolve_template", return_value=draft):
            result = build_original_configuration(self.catalog, self.world, "eet-original",
                                                  exclude={"virtual-1": "runtime reason"})
        self.assertEqual([(row["id"], row["kind"], row["reason"]) for row in result["not_used"]], [
            ("virtual-1", "reward", "runtime reason"),
            ("ambiguous", "reward", "ambiguous-source-records"),
            ("twice", "reward", "source-record-used-twice"),
            ("lost-place", "place", "area-resource-absent")])
        self.assertEqual(result["not_used_notes"], [
            "3 rewards and 1 place from the lists cannot be used in this game, so they are left out",
            "A left-out reward stays as the game has it"])
        plan = result["plan"]
        self.assertEqual(plan["sources"], [{"item": "synth001", "policy": "relocate",
                                            "source_ids": [self.physical], "registration_ids": []}])
        self.assertEqual(plan["registrations"], [])
        self.assertEqual(result["row_ids"], {self.physical: "source-1",
                                             "original-target:target-1": "target-1",
                                             "original-target:target-2": "target-2"})

    def test_a_place_on_a_creature_that_never_appears_is_left_out_and_named(self):
        draft = self._draft()
        hidden = target_row("hidden-guard", "x", "24")
        hidden.update(target="guard.cre", creature_resource="cre:guard", container_name=None,
                      endpoint_kind=None, binding_problem="creature-never-appears")
        draft["targets"].append(hidden)
        with patch("flir_config.original_configuration.resolve_template", return_value=draft):
            result = build_original_configuration(self.catalog, self.world, "eet-original")
        self.assertEqual(result["not_used"], [{"id": "hidden-guard", "row": "lists/locations/base/bg1.2da:1",
                                               "kind": "place", "reason": "creature-never-appears"}])
        self.assertNotIn("original-target:hidden-guard",
                         {row["id"] for row in result["plan"]["target_registrations"]})

    def test_eet_without_its_rename_lists_is_named_and_uses_cirs_copy(self):
        shutil.rmtree(self.root / "EET")
        with patch("flir_config.original_configuration.resolve_template",
                   return_value=self._draft()) as resolve:
            result = build_original_configuration(self.catalog, self.world, "eet-original")
        names = resolve.call_args.kwargs["names"]
        self.assertFalse(names.installed)
        self.assertEqual(result["not_used_notes"], [
            "EET's rename lists (EET/lib/pcu_dict.tph) are not in this game, so Chriz Item Randomiser "
            "uses its own older copy; some BG1 and Siege of Dragonspear rows may not be found and are "
            "left out"])
        # With them, the game's own lists are used and nothing is said.
        mark_eet(self.root)
        with patch("flir_config.original_configuration.resolve_template",
                   return_value=self._draft()) as resolve:
            result = build_original_configuration(self.catalog, self.world, "eet-original")
        self.assertTrue(resolve.call_args.kwargs["names"].installed)
        self.assertEqual(result["not_used_notes"], [])

    def _fitting_game(self):
        """Scans and a draft whose fit takes three rounds: a reward without a
        place of its tier, then one the runtime check rejects."""
        from flir_config.templates import DEFAULT_OPTIONS

        visible = bytearray(make_itm())
        struct.pack_into("<I", visible, 0x18, 0x4c)
        (self.root / "override" / "synth002.itm").write_bytes(visible)
        (self.root / "override" / "area.are").write_bytes(make_are(
            [item_record(expiration=0, charges=(0, 0, 0), flags=1),
             item_record("SYNTH002", expiration=0, charges=(0, 0, 0), flags=1)],
            [("Source", 0, 1), ("Good", 1, 1), ("Target A", 2, 0), ("Target B", 2, 0)]))
        catalog = scan_game(self.root)
        world = scan_world(catalog)
        draft = self._draft()
        draft["options"] = DEFAULT_OPTIONS | draft["options"]
        # SYNTH001 cannot be shown in an inventory, so the runtime cannot move it.
        draft["items"] = [source_row("hidden", source_ids=["are:area/container/0/item/0"], ident="x"),
                          source_row("good", item="synth002", ident="x",
                                     source_ids=["are:area/container/1/item/1"]),
                          # No place of tier 25 is left for this reward.
                          source_row("no-place", adapter="virtual", item="synth002", ident="x",
                                     tier="25", resource="itm:synth002")]
        draft["targets"] = [target_row("place-a", "x", "24"), target_row("place-b", "x", "24")]
        for row, container in zip(draft["targets"], (2, 3)):
            row["target_ids"] = [f"are:area/container/{container}"]
        return catalog, world, draft

    def test_fitting_leaves_out_rows_the_runtime_check_rejects(self):
        from flir_config.campaigns import compile_campaign
        from flir_config.native_export import _compile
        from flir_config.original_configuration import fit_original_configuration
        from flir_config.profiles import validate_catalog
        from flir_config.sources import validate_world

        catalog, world, draft = self._fitting_game()
        with patch("flir_config.original_configuration.resolve_template", return_value=draft), \
                patch("flir_config.original_registrations.resolve_template", return_value=draft):
            result = fit_original_configuration(catalog, world, "eet-original")
            campaign = compile_campaign(result["profile"], catalog, world, result["plan"])
        self.assertEqual(result["profile"]["items"], ["synth002"])
        self.assertEqual(result["not_used"], [{
            "id": "hidden", "row": "lists/items/base/bg1.2da:1", "kind": "reward",
            "reason": "Native source requires an unexpired, visible droppable item: "
                      "are:area/container/0/item/0"},
            {"id": "no-place", "row": "lists/items/base/bg1.2da:1", "kind": "reward",
             "reason": "no-place-in-tier"}])
        self.assertEqual(result["plan"]["registrations"], [])
        # The fitted configuration passes the strict runtime check.
        _compile(campaign, world, validate_world(world, catalog), validate_catalog(catalog), catalog=catalog)

    def test_fitting_rounds_validate_the_scans_and_resolve_the_tables_once(self):
        from flir_config import native_export, original_configuration, sources
        from flir_config.original_configuration import fit_original_configuration
        from flir_config.sources import Scans

        catalog, world, draft = self._fitting_game()
        results, counts, validate = [], [], sources.validate_world
        for given in (None, Scans(catalog, world)):
            with patch("flir_config.original_configuration.resolve_template", return_value=draft) as build, \
                    patch("flir_config.original_registrations.resolve_template",
                          return_value=draft) as tables, \
                    patch.object(sources, "validate_world", wraps=validate) as validated, \
                    patch.object(original_configuration, "validate_world", wraps=validate) as built, \
                    patch.object(native_export, "validate_world", wraps=validate) as exported:
                results.append(fit_original_configuration(catalog, world, "eet-original", scans=given))
            counts.append((build.call_count, tables.call_count,
                           validated.call_count + built.call_count + exported.call_count))
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(results[0]["not_used"]), 2)
        # Three rounds: the tables are resolved once each for the build and
        # the binding, and the world is validated once, or not at all when the
        # caller has validated it.
        self.assertEqual(counts, [(1, 1, 1), (1, 1, 0)])

    def test_fitting_rounds_check_kangaxx_quest_files_once(self):
        """CIR's own change of Kangaxx's conversation is recognised from the
        file's bytes, read once for every round of the fit."""
        from flir_config import original_configuration, quest_options
        from flir_config.original_configuration import fit_original_configuration
        from test_quest_options import crypt_fixture, dialogue_fixture

        quest = {"hlskull.dlg": dialogue_fixture("hlskull.dlg"), "crypt526.bcs": crypt_fixture("AR0526"),
                 "crypt202.bcs": crypt_fixture("AR0202")}
        with patch.dict(quest_options.ORIGINAL_HASHES,
                        {name: hashlib.sha256(data).hexdigest() for name, data in quest.items()}):
            for name, data in quest.items():
                (self.root / "override" / name).write_bytes(data)
            (self.root / "override" / "hlskull.dlg").write_bytes(
                quest_options.patch_hlskull(quest["hlskull.dlg"], dict(enumerate(range(1000, 1022)))))
            catalog, world, draft = self._fitting_game()
            draft["options"]["kangaxx"] = True
            with patch("flir_config.original_configuration.resolve_template", return_value=draft), \
                    patch("flir_config.original_registrations.resolve_template", return_value=draft), \
                    patch.object(original_configuration, "kangaxx_problem",
                                 wraps=quest_options.kangaxx_problem) as checked:
                result = fit_original_configuration(catalog, world, "eet-original")
        # Three rounds, as above.
        self.assertEqual(len(result["not_used"]), 2)
        self.assertIs(result["plan"]["original_options"]["kangaxx"], True)
        self.assertEqual(checked.call_count, 1)

    def test_validated_scans_belong_to_their_own_catalogues(self):
        from flir_config.original_configuration import fit_original_configuration
        from flir_config.sources import Scans

        scans = Scans(self.catalog, self.world)
        self.assertIs(scans.indexes(self.catalog, self.world)[1], scans.index)
        with self.assertRaisesRegex(ValueError, "other catalogues"):
            fit_original_configuration(dict(self.catalog), self.world, "eet-original", scans=scans)
        with self.assertRaisesRegex(ValueError, "other catalogues"):
            scans.indexes(self.catalog, dict(self.world))

    def test_zero_match_source_omitted_while_target_rows_remain(self):
        with patch("flir_config.original_configuration.resolve_template",
                   return_value=self._draft(absent=True)):
            plan = build_original_configuration(self.catalog, self.world, "eet-original")["plan"]
        self.assertEqual(plan["sources"][0]["source_ids"], [])
        self.assertEqual(len(plan["registrations"]), 1)
        self.assertEqual(len(plan["target_registrations"]), 2)
        self.assertNotIn(self.physical, plan["randomization"]["sources"])

    def test_grouped_creature_records_are_one_authored_reward(self):
        (self.root / "override" / "group.cre").write_bytes(
            make_cre([item_record(), item_record()]))
        catalog = scan_game(self.root)
        world = scan_world(catalog)
        grouped_ids = sorted(row["id"] for row in world["sources"]
                             if row["resource"] == "cre:group")
        self.assertEqual(len(grouped_ids), 2)
        draft = self._draft()
        draft["items"] = [source_row("group-1", source_ids=grouped_ids,
                                     resource="cre:group")]
        with patch("flir_config.original_configuration.resolve_template", return_value=draft):
            plan = build_original_configuration(catalog, world, "eet-original")["plan"]
        registration = plan["registrations"][0]
        self.assertEqual(registration["adapter_id"], "original-source-v1")
        self.assertEqual(registration["copies"], 1)
        self.assertEqual(registration["physical_source_ids"], grouped_ids)
        self.assertEqual(registration["authored"]["match_policy"], "all_one_unit")
        self.assertEqual(registration["payload"]["charges"], [7, 3, 2])
        self.assertEqual(list(plan["randomization"]["sources"]), [registration["id"]])

    def test_single_slotless_creature_row_keeps_exact_record_as_authored_source(self):
        (self.root / "override" / "slotless.cre").write_bytes(make_cre([
            item_record("OTHER"), item_record(expiration=0, charges=(2, 3, 4), flags=0)],
            slots={21: 0}))
        catalog = scan_game(self.root)
        world = scan_world(catalog)
        selected = next(row for row in world["sources"] if row["id"] == "cre:slotless/item/1")
        draft = self._draft()
        authored = source_row("slotless-1", source_ids=[selected["id"]], resource="cre:slotless")
        authored["source"] = "slotless.cre"
        draft["items"] = [authored]
        with patch("flir_config.original_configuration.resolve_template", return_value=draft):
            plan = build_original_configuration(catalog, world, "eet-original")["plan"]
        registration = plan["registrations"][0]
        self.assertEqual(registration["physical_source_ids"], ["cre:slotless/item/1"])
        self.assertEqual(registration["payload"], {"resref": "synth001", "expiration": 0,
                                                   "charges": [2, 3, 4], "flags": 0})
        self.assertEqual(registration["resource_sha256"], next(
            row["sha256"] for row in world["resources"] if row["id"] == "cre:slotless"))
        self.assertEqual(registration["selector"], "slotless-1")
        self.assertEqual(plan["sources"][0]["source_ids"], [])
        self.assertEqual(plan["sources"][0]["registration_ids"], [registration["id"]])
        self.assertNotIn("match_policy", registration["authored"])

        from flir_config.original_registrations import is_unreferenced_creature_source
        from copy import deepcopy
        physical = {selected["id"]: selected}
        for concern in ("aliased-inventory-record", "missing-item-definition", "invalid-item-reference"):
            changed = deepcopy(physical)
            changed[selected["id"]]["concerns"].append(concern)
            self.assertFalse(is_unreferenced_creature_source(authored, changed))
        changed_row = deepcopy(authored)
        changed_row["replacement"] = "synth002"
        self.assertFalse(is_unreferenced_creature_source(changed_row, physical))

    def test_equipped_creature_blank_intent_and_absent_unshey(self):
        (self.root / "override" / "equip.cre").write_bytes(
            make_cre([item_record()], slots={0: 0}))
        catalog = scan_game(self.root)
        world = scan_world(catalog)
        equipped_id = next(row["id"] for row in world["sources"]
                           if row["resource"] == "cre:equip")
        draft = self._draft()
        draft["items"] = [
            source_row("equip-1", source_ids=[equipped_id], resource="cre:equip"),
            source_row("unshey-1", adapter="unshey", resource="cre:absent"),
            source_row("virtual-1", adapter="virtual", ident="x", tier="25")]
        with patch("flir_config.original_configuration.resolve_template", return_value=draft):
            plan = build_original_configuration(catalog, world, "eet-original")["plan"]
        self.assertEqual(plan["replacements"], {equipped_id: "blank"})
        self.assertEqual(len(plan["registrations"]), 1)
        self.assertTrue(all("unshey" not in identity
                            for identity in plan["randomization"]["sources"]))
        self.assertEqual(len(plan["target_registrations"]), 2)


if __name__ == "__main__":
    unittest.main()
