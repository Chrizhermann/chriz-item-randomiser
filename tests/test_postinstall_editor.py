"""Editor workflows reuse the configurator's synthetic installation fixture."""
from contextlib import ExitStack, contextmanager, nullcontext
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
from threading import Thread
from types import ModuleType
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from flir_config.campaigns import read_campaign, _render_lua
from flir_config.editor import Editor, create_server
from flir_config.native_export import NEXT_SCHEMA
from flir_config.original_catalog import resolve_template
from flir_config.original_options import SPELL_SHIELD_SOURCE, is_spell_shield_row
from flir_config.profiles import edit_profile, new_profile
from flir_config.profiles import ProfileError
from flir_config.resources import scan_game
from flir_config.sources import new_binding_plan, scan_world
from flir_config.templates import DEFAULT_OPTIONS
from tests import test_postinstall_native_export as fixtures
from flir_config import quest_options
from test_cursed_disguises import resources as cursed_resources
from test_quest_options import crypt_fixture, dialogue_fixture
from tests.test_postinstall_blpool_script import write_registry
from test_postinstall_resources import make_itm
from tests.test_postinstall_world_formats import make_are, make_cre, make_sto, item_record
from sod_table_fixtures import sod_tables


@contextmanager
def world_checks():
    """Spies on validate_world in each module an editor request uses."""
    from flir_config import native_export, original_configuration, sources, starts
    validate = sources.validate_world
    with ExitStack() as stack:
        yield [stack.enter_context(patch.object(module, "validate_world", wraps=validate))
               for module in (sources, original_configuration, starts, native_export)]


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.NativeExportTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.reset_editor()

    def reset_editor(self, source=None):
        fx = self.fx
        profile = edit_profile(new_profile(fx.catalog, "sample", "A small adventure"),
                               fx.catalog, include=["synth001"])
        plan = new_binding_plan(profile, fx.catalog, fx.world)
        plan["sources"] = [{"item": "synth001", "policy": "relocate", "registration_ids": [],
                            "source_ids": [source or fx.first]}]
        plan["targets"] = [{"id": "are:area/actor/1", "capacity": 1, "optional": False}]
        self.editor = Editor(fx.catalog, fx.world, fx.root / "editor", profile, plan)

    def test_save_reload_copy_and_incomplete_draft_keep_choices(self):
        e = self.editor
        draft = deepcopy(e.draft)
        draft["profile"]["shop_pricing"] = {"percent": 135, "base_prices": {"synth001": 650}}
        saved = e.post("/api/save", draft)
        self.assertEqual(saved["profile"]["shop_pricing"], draft["profile"]["shop_pricing"])
        self.assertEqual(saved["plan"], draft["plan"])
        copied = e.post("/api/save", {**draft, "copy": True})
        self.assertNotEqual(copied["profile"]["id"], draft["profile"]["id"])
        self.assertEqual(copied["profile"]["shop_pricing"], draft["profile"]["shop_pricing"])
        self.assertEqual(len(copied["saved"]), 2)
        incomplete = deepcopy(draft)
        incomplete["profile"]["items"].append("synth002")
        e.post("/api/save", incomplete)
        fresh = Editor(self.fx.catalog, self.fx.world, e.workspace)
        loaded = fresh.post("/api/load", {"id": "sample"})
        self.assertEqual(loaded["profile"], incomplete["profile"])
        self.assertEqual(loaded["plan"], draft["plan"])
        self.assertFalse(fresh.post("/api/validate", incomplete)["valid"])

    def test_presets_offered_are_those_of_the_game(self):
        fx = self.fx
        (fx.override / "EET.flag").unlink()
        for markers, presets, sod in ((("oh6000",), ["bg2-original"], False),
                                      (("oh1000",), ["bg1-original", "bg1-boss-loot"], False),
                                      # CIR bundles no SoD tables yet: Siege of Dragonspear adds nothing.
                                      (("oh1000", "bd0010"), ["bg1-original", "bg1-boss-loot"], False),
                                      # Presets that read SoD tables (synthetic here) need SoD's areas.
                                      (("oh1000",), ["bg1-original", "bg1-boss-loot"], True),
                                      (("oh1000", "bd0010"), ["bg1-original", "sod-original",
                                                              "bg1-sod-original", "bg1-boss-loot",
                                                              "bg1-sod-boss-loot"], True)):
            with self.subTest(markers=markers, sod=sod), (sod_tables() if sod else nullcontext()):
                for area in (*fx.override.glob("oh*.are"), *fx.override.glob("bd*.are")):
                    area.unlink()
                for marker in markers:
                    (fx.override / f"{marker}.are").write_bytes(make_are())
                fx.refresh()
                self.reset_editor()
                self.assertEqual([row["id"] for row in self.editor.get("/api/templates", {})], presets)
                self.assertEqual(self.editor.get("/api/recommended", {})["id"], presets[0])

    def test_records_and_recipients_show_and_find_their_parts_of_the_game(self):
        fx = self.fx
        record = item_record("SYNTH001", expiration=0, charges=(0, 0, 0), flags=1)
        # EET's area names tell the part of the game; "area" tells none.
        areas = {"bg2600": "bg1", "bd0010": "sod", "ar0602": "soa", "ar3000": "wk", "ar5500": "tob"}
        for area in areas:
            (fx.override / f"{area}.are").write_bytes(make_are([record], [("Chest", 0, 1)], [("Guard", "ACTOR")]))

        def editor(name):
            fx.refresh()
            profile = edit_profile(new_profile(fx.catalog, "parts", "Parts"), fx.catalog, include=["synth001"])
            plan = new_binding_plan(profile, fx.catalog, fx.world)
            plan["sources"] = [{"item": "synth001", "policy": "relocate", "registration_ids": ["original-source:x"],
                                "source_ids": [*(f"are:{area}/container/0/item/0" for area in areas), fx.first]}]
            plan["registrations"] = [{"id": "original-source:x", "item": "synth001", "resource": "cre:actor"}]
            plan["targets"] = [{"id": target, "capacity": 1, "optional": False} for target in
                               (*(f"are:{area}/container/0" for area in areas), "cre:actor", "original-target:x",
                                "are:missing/container/0")]
            plan["target_registrations"] = [{"id": "original-target:x", "resource": "are:ar5500",
                                             "authored": {"target": "Chest", "area": "ar5500"}}]
            return Editor(fx.catalog, fx.world, fx.root / name, profile, plan)

        every = list(areas.values())
        parts = editor("parts-editor")
        state = parts.get("/api/state", {})
        # A creature template is in every part where it is placed.
        self.assertEqual(state["source_parts"], {
            **{f"are:{area}/container/0/item/0": [part] for area, part in areas.items()},
            fx.first: [], "original-source:x": every})
        self.assertEqual([row["parts"] for row in state["targets"]],
                         [*([part] for part in areas.values()), every, ["tob"], []])
        found = parts.get("/api/targets", {"q": "tob chest"})
        self.assertEqual([(row["id"], row["parts"]) for row in found["records"]],
                         [("are:ar5500/container/0", ["tob"])])
        self.assertEqual({row["id"] for row in parts.get("/api/targets", {"q": "watcher's keep"})["records"]},
                         {"are:ar3000/container/0", "are:ar3000/actor/0", "cre:actor"})
        records = parts.get("/api/sources", {"item": "synth001"})["records"]
        self.assertEqual({row["id"]: row["parts"] for row in records},
                         {fx.first: [], **{f"are:{area}/container/0/item/0": [part] for area, part in areas.items()}})
        # A game no preset is for: its area names tell no part.
        (fx.override / "EET.flag").unlink()
        plain = editor("plain-editor")
        state = plain.get("/api/state", {})
        self.assertEqual(set(map(tuple, state["source_parts"].values())), {()})
        self.assertEqual({tuple(row["parts"]) for row in state["targets"]}, {()})
        self.assertEqual(plain.get("/api/targets", {"q": "tob"})["total"], 0)

    def test_preset_routes_replace_only_a_checked_successful_draft(self):
        editor = self.editor
        descriptors = editor.get("/api/templates", {})
        self.assertEqual([row["id"] for row in descriptors[:3]],
                         ["bg1-original", "bg2-original", "eet-original"])
        recommended = editor.get("/api/recommended", {})
        self.assertEqual(recommended["id"], "eet-original")
        self.assertIn(recommended["id"], [row["id"] for row in descriptors])
        # Without CIR's record, Glaicus's conversation is not prepared.
        self.assertFalse(recommended["options"]["flail_heads"])
        self.assertIn("KPGLAI01.DLG", recommended["unavailable"]["flail_heads"])
        self.assertTrue(recommended["options"]["totsc_tome"])
        write_registry(self.fx.game)
        recommended = editor.get("/api/recommended", {})
        self.assertTrue(recommended["options"]["flail_heads"] and recommended["options"]["totsc_tome"])
        self.assertNotIn("flail_heads", recommended["unavailable"])
        write_registry(self.fx.game, flail_heads="dialogue-unrecognised")
        recommended = editor.get("/api/recommended", {})
        self.assertFalse(recommended["options"]["flail_heads"])
        self.assertEqual(recommended["unavailable"]["flail_heads"],
                         "KPGLAI01.DLG was left as installed (the conversation has an unexpected form)")
        original = deepcopy(editor.draft)
        calls = []
        module = ModuleType("flir_config.original_configuration")
        target_id = "original-target:bg1:base:target:3"

        def build(catalog, world, template_id, *, items, targets, options, scans):
            calls.append((catalog, world, template_id, items, targets, options))
            # The fit reuses the scans the editor validated when it started.
            self.assertIs(scans.indexes(catalog, world)[1], editor.index)
            result = deepcopy(original)
            result["profile"]["name"] = "BG1 original"
            result["plan"]["randomization"] = {"tier_rows": ["original-choice"]}
            result["plan"]["original_options"] = {"scrolls": True}
            result["plan"]["original_template_id"] = "bg1-original"
            result["plan"]["target_registrations"] = [{
                "id": target_id, "capacity": 1,
                "authored": {"target": "container1", "area_resource": "are:bg5500"}}]
            result["plan"]["targets"] = [{"id": target_id, "capacity": 1, "optional": False}]
            result["left_out"] = ["BLPOOL.BCS was left as installed (it gives the reward more than once)"]
            result["absent_mods"] = ["Rogue Rebalancing is not installed, so 2 rewards from it are left out"]
            result["not_used_notes"] = ["1 place from the lists cannot be used in this game, so it is left out"]
            result["not_used"] = [{"id": "bg1:base:target:9", "row": "lists/locations/base/bg1.2da:9",
                                   "kind": "place", "reason": "area-resource-absent"}]
            return result

        module.fit_original_configuration = build
        with patch.dict(sys.modules, {module.__name__: module}):
            state = editor.post("/api/template", {
                "id": "bg1-original", "items": "extended", "targets": "standard",
                "options": {"scrolls": True}})
            self.assertEqual(calls[0][2:], ("bg1-original", "extended", "standard",
                                           {"scrolls": True}))
            self.assertEqual(state["profile"]["name"], "BG1 original")
            self.assertEqual(state["left_out"],
                             ["BLPOOL.BCS was left as installed (it gives the reward more than once)"])
            self.assertEqual(state["absent_mods"],
                             ["Rogue Rebalancing is not installed, so 2 rewards from it are left out"])
            self.assertNotIn("left_out", editor.draft)
            self.assertNotIn("absent_mods", editor.draft)
            self.assertEqual(state["not_used"],
                             ["1 place from the lists cannot be used in this game, so it is left out"])
            self.assertEqual(state["not_used_rows"][0]["reason"], "area-resource-absent")
            self.assertNotIn("not_used", editor.draft)
            self.assertNotEqual(state["profile"]["id"], original["profile"]["id"])
            self.assertEqual(editor.post("/api/save", editor.draft)["plan"]["randomization"],
                             {"tier_rows": ["original-choice"]})
            self.assertEqual(state["targets"][0]["label"], "container1 (are:bg5500)")
            self.assertEqual(editor.draft["plan"]["original_options"], {"scrolls": True})
            self.assertEqual(editor.draft["plan"]["original_template_id"], "bg1-original")
            self.assertEqual(editor.draft["plan"]["target_registrations"][0]["id"], target_id)

            before_error = deepcopy(editor.draft)
            def failed(*_, **__):
                raise ProfileError("Preset cannot be bound")
            module.fit_original_configuration = failed
            with self.assertRaisesRegex(ProfileError, "cannot be bound"):
                editor.post("/api/template", {"id": "bg2-original"})
            self.assertEqual(editor.draft, before_error)

            def malformed(*_, **__):
                result = deepcopy(original)
                result["plan"]["world_fingerprint"] = "another-scan"
                return result
            module.fit_original_configuration = malformed
            with self.assertRaisesRegex(ProfileError, "different scan"):
                editor.post("/api/template", {"id": "bg2-original"})
            self.assertEqual(editor.draft, before_error)

    def test_presets_offer_only_the_options_this_game_can_apply(self):
        """An option a game cannot apply is not offered, with the reason; asked
        for anyway, it is refused with that reason before anything is built."""
        fx = self.fx
        write_registry(fx.game)
        kangaxx = "Kangaxx items need HLSKULL.DLG, CRYPT526.BCS and CRYPT202.BCS, which this game does not have"
        shop = "More Spell Shield copies need the shop UDDUER01.STO, which this game does not have"
        tome = ("The ToTSC tome variant changes a reward of Baldur's Gate's lists, "
                "which no preset for this game uses")
        cursed = "Cursed disguise items need one of the cursed items they disguise, which this game does not have"
        quest = {"hlskull.dlg": dialogue_fixture("hlskull.dlg"), "crypt526.bcs": crypt_fixture("AR0526"),
                 "crypt202.bcs": crypt_fixture("AR0202")}
        reviewed = patch.dict(quest_options.ORIGINAL_HASHES,
                              {name: hashlib.sha256(data).hexdigest() for name, data in quest.items()})
        reviewed.start()
        self.addCleanup(reviewed.stop)

        def recommended():
            fx.refresh()
            self.reset_editor()
            preset = self.editor.get("/api/recommended", {})
            for option in preset["unavailable"]:
                self.assertIs(preset["options"][option], False)
            return preset["unavailable"]

        def refused(options, reason):
            with self.subTest(options=options), self.assertRaisesRegex(ProfileError, "^" + re.escape(reason) + "$"):
                self.editor.post("/api/template", {"id": "eet-original", "options": options})

        self.assertEqual(recommended(), {"kangaxx": kangaxx, "more_spell_shield": shop, "cursed_disguise": cursed})
        refused({"kangaxx": True}, kangaxx)
        refused({"more_spell_shield": True}, shop)
        # With Kangaxx's quest files, the Underdark shop and the cursed items,
        # every option is offered; also after CIR changed the quest's files.
        for name, data in quest.items():
            (fx.override / name).write_bytes(data)
        (fx.override / "udduer01.sto").write_bytes(bytes(make_sto(
            [(item_record("scrl8x", expiration=0, charges=(1, 1, 0), flags=0), 1, 0)])))
        (fx.override / "scrl8x.itm").write_bytes(make_itm())
        cursed_items = {}
        for name, data in cursed_resources().items():
            # The scan reads the item's ability without effects.
            cursed_items[name] = bytearray(data)
            struct.pack_into("<HH", cursed_items[name], 0x72 + 0x1E, 0, 0)
            (fx.override / f"{name}.itm").write_bytes(cursed_items[name])
        self.assertEqual(recommended(), {})
        (fx.override / "hlskull.dlg").write_bytes(
            quest_options.patch_hlskull(quest["hlskull.dlg"], dict(enumerate(range(1000, 1022)))))
        self.assertEqual(recommended(), {})
        # Another mod's version of a quest file.
        (fx.override / "hlskull.dlg").write_bytes(
            dialogue_fixture("hlskull.dlg", action=b'SetGlobal("KangaxxQuest","GLOBAL",2)\r\n'))
        changed = "Kangaxx items need HLSKULL.DLG as Baldur's Gate II has it; another mod changed it"
        self.assertEqual(recommended(), {"kangaxx": changed})
        refused({"kangaxx": True}, changed)
        (fx.override / "hlskull.dlg").write_bytes(quest["hlskull.dlg"])
        for name in ("crypt202.bcs", "scrl8x.itm", "brac06.itm"):
            (fx.override / name).unlink()
        self.assertEqual(recommended(), {
            "kangaxx": "Kangaxx items need CRYPT202.BCS, which this game does not have",
            "more_spell_shield": "More Spell Shield copies need SCRL8X.ITM, which this game does not have",
            "cursed_disguise": "Cursed disguise items need BRAC06.ITM to disguise BRAC08.ITM, "
                               "and this game does not have it"})
        (fx.override / "scrl8x.itm").write_bytes(make_itm())
        (fx.override / "brac06.itm").write_bytes(cursed_items["brac06"])
        (fx.override / "udduer01.sto").write_bytes(b"STORV1.0" + bytes(8))
        self.assertEqual(recommended()["more_spell_shield"],
                         "More Spell Shield copies need the shop UDDUER01.STO, which cannot be read")
        # BG:EE has neither Kangaxx nor the shop, and its installer could not
        # prepare Glaicus's conversation.
        (fx.override / "EET.flag").unlink()
        for name in ("hlskull.dlg", "crypt526.bcs", "udduer01.sto", "scrl8x.itm"):
            (fx.override / name).unlink()
        (fx.override / "oh1000.are").write_bytes(make_are())
        write_registry(fx.game, flail_heads="absent")
        self.assertEqual(recommended(), {
            "kangaxx": kangaxx, "flail_heads": "KPGLAI01.DLG was left as installed (the game has no such file)",
            "more_spell_shield": shop})
        # BG2:EE's presets have no Baldur's Gate lists for the tome variant.
        (fx.override / "oh1000.are").unlink()
        (fx.override / "oh6000.are").write_bytes(make_are())
        write_registry(fx.game)
        self.assertEqual(recommended(), {"kangaxx": kangaxx, "totsc_tome": tome, "more_spell_shield": shop})

    def test_preset_options_with_their_own_plan_fields_save_check_apply_and_reopen(self):
        """More Spell Shield scrolls and cursed disguises add their metadata to the plan."""
        fx = self.fx
        scroll = bytearray(make_itm())
        struct.pack_into("<I", scroll, 0x18, 0x4c)  # droppable, as the game's is
        (fx.override / "scrl8x.itm").write_bytes(scroll)
        (fx.override / "udduer01.sto").write_bytes(bytes(make_sto(
            [(item_record("scrl8x", expiration=0, charges=(1, 1, 0), flags=0), 1, 0)])))
        # A place of the Spell Shield scroll's tier for its randomised copies.
        (fx.override / "ar0808.are").write_bytes(make_are([], [("Chest1", 0, 0)]))
        for name, data in cursed_resources().items():
            item = bytearray(data)
            struct.pack_into("<HH", item, 0x72 + 0x1E, 0, 0)
            (fx.override / f"{name}.itm").write_bytes(item)
        # The disguises' texts go into the game's English text and journal table.
        language = fx.game / "lang/en_US"
        language.mkdir(parents=True)
        (language / "dialog.tlk").write_bytes((fx.game / "dialog.tlk").read_bytes())
        (fx.override / "BGEE.LUA").write_bytes(b"quests = {}\n")
        for name in ("override/M_FLPR.lua", "override/FLPRRun.menu", "randomiser-campaigns/next.lua"):
            path = fx.game / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("test runtime")
        catalog = scan_game(fx.game, language="en_US")
        world = scan_world(catalog)
        options = DEFAULT_OPTIONS | {"scrolls": True, "more_spell_shield": True, "cursed_disguise": True}
        rows = resolve_template(catalog, world, "bg2-original", items="extended", targets="extended",
                                options=options)
        rows["items"] = [row for row in rows["items"] if is_spell_shield_row(row)]
        rows["targets"] = [row for row in rows["targets"] if row["id"] == "bg2:base:target:309"]
        editor = Editor(catalog, world, fx.root / "options-editor", apply_game=fx.game)
        with patch("flir_config.original_configuration.resolve_template", return_value=rows), \
                world_checks() as spies:
            editor.post("/api/template", {"id": "bg2-original", "items": "extended", "targets": "extended",
                                          "options": options})
        # Requests reuse the scans the editor validated when it started.
        self.assertEqual([spy.call_count for spy in spies], [0, 0, 0, 0])
        draft = deepcopy(editor.draft)
        self.assertEqual(draft["plan"]["original_options"], options)
        self.assertEqual(draft["plan"]["spell_shield"]["source_id"], SPELL_SHIELD_SOURCE)
        self.assertEqual(draft["plan"]["cursed_disguises"]["game"], "eet")
        saved = editor.post("/api/save", draft)
        self.assertEqual((saved["profile"], saved["plan"]), (draft["profile"], draft["plan"]))
        loaded = Editor(catalog, world, editor.workspace).post("/api/load", {"id": draft["profile"]["id"]})
        self.assertEqual((loaded["profile"], loaded["plan"]), (draft["profile"], draft["plan"]))
        with world_checks() as spies:
            self.assertTrue(editor.post("/api/validate", draft)["runtime_supported"])
            with patch("flir_config.activation.require_closed"):
                applied = editor.post("/api/apply", draft)
        self.assertEqual([spy.call_count for spy in spies], [0, 0, 0, 0])
        campaign = read_campaign(editor.workspace / "prepared", applied["revision"])
        self.assertEqual(campaign["spell_shield"], draft["plan"]["spell_shield"])
        self.assertEqual(campaign["cursed_disguises"], draft["plan"]["cursed_disguises"])
        # The editor reopens what was applied.
        self.assertEqual(Editor(catalog, world, editor.workspace, apply_game=fx.game).draft, draft)

    def test_prepare_uses_existing_campaign_format_and_capacity_check(self):
        e = self.editor
        self.assertTrue(e.post("/api/validate", e.draft)["runtime_supported"])
        prepared = e.post("/api/prepare", e.draft)
        campaign = read_campaign(Path(prepared["store"]))
        self.assertEqual(campaign["revision"], prepared["revision"])
        self.assertEqual(campaign["sources"][0]["id"], self.fx.first)
        draft = deepcopy(e.draft)
        draft["targets"] = []  # Extra request fields do not replace plan targets.
        draft["plan"]["targets"] = []
        result = e.post("/api/validate", draft)
        self.assertFalse(result["valid"])
        self.assertIn("capacity", result["message"].lower())

    def test_apply_retains_old_campaign_and_reopens_saved_configuration(self):
        fx, original = self.fx, self.editor
        for name in ("override/M_FLPR.lua", "override/FLPRRun.menu", "randomiser-campaigns/next.lua"):
            path = fx.game / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("test runtime")
        unrelated = fx.override / "other-mod.txt"
        unrelated.write_bytes(b"preserve unrelated mod content")
        e = Editor(fx.catalog, fx.world, original.workspace, **original.draft, apply_game=fx.game)
        with patch("flir_config.activation.require_closed"):
            first = e.post("/api/apply", e.draft)
            old = fx.game / "randomiser-campaigns/campaign-store/revisions" / (first["revision"] + ".lua")
            before = old.read_bytes()
            draft = deepcopy(e.draft)
            draft["profile"]["name"] = "Changed next adventure"
            draft["profile"]["shop_pricing"] = {"percent": 140, "base_prices": {}}
            second = e.post("/api/apply", draft)
        self.assertNotEqual(first["revision"], second["revision"])
        self.assertEqual(old.read_bytes(), before)
        # This EET test area has no campaign name, so only a Baldur's Gate start reaches it.
        self.assertEqual(second["starts"], {"BG1": second["revision"]})
        self.assertIn("New games randomised: Baldur's Gate. Not randomised: Siege of Dragonspear",
                      second["message"])
        self.assertEqual((fx.game / "randomiser-campaigns/next.lua").read_bytes(),
                         _render_lua({"schema": NEXT_SCHEMA, "starts": second["starts"]}))
        self.assertEqual(unrelated.read_bytes(), b"preserve unrelated mod content")
        self.assertEqual((fx.override / "area.are").read_bytes(), fx.area)
        reopened = Editor(fx.catalog, fx.world, e.workspace, apply_game=fx.game)
        self.assertEqual(reopened.draft, draft)
        self.assertTrue(reopened.state()["apply_enabled"])
        pointer = (fx.game / "randomiser-campaigns/next.lua").read_bytes()
        with patch("flir_config.activation.require_closed", side_effect=ProfileError("Close this game")):
            with self.assertRaisesRegex(ProfileError, "Close this game"):
                e.post("/api/apply", original.draft)
        self.assertEqual((fx.game / "randomiser-campaigns/next.lua").read_bytes(), pointer)
        # Another workspace (the first one lost) takes both revisions from the
        # game for their saves and applies the first configuration again.
        revisions = fx.game / "randomiser-campaigns/campaign-store/revisions"
        kept = {path.name: path.read_bytes() for path in revisions.iterdir()}
        other = Editor(fx.catalog, fx.world, fx.root / "another-editor",
                       **original.draft, apply_game=fx.game)
        with patch("flir_config.activation.require_closed"):
            third = other.post("/api/apply", other.draft)
        self.assertEqual(third["revision"], first["revision"])
        self.assertEqual({path.name: path.read_bytes() for path in revisions.iterdir()}, kept)
        self.assertEqual((fx.game / "randomiser-campaigns/source-gate.lua").read_bytes(),
                         _render_lua(sorted([first["revision"], second["revision"]])))
        self.assertEqual((fx.game / "randomiser-campaigns/next.lua").read_bytes(),
                         _render_lua({"schema": NEXT_SCHEMA, "starts": first["starts"]}))

    def test_draft_editor_cannot_activate_game(self):
        self.assertFalse(self.editor.state()["apply_enabled"])
        with self.assertRaisesRegex(ProfileError, "not enabled"):
            self.editor.post("/api/apply", self.editor.draft)

    def test_multiple_originals_survive_save_copy_reload_and_prepare(self):
        fx = self.fx
        (fx.override / "actor.cre").write_bytes(make_cre([
            item_record("SYNTH001", expiration=0, charges=(0, 0, 0), flags=1)]))
        fx.refresh()
        self.reset_editor()
        e = self.editor
        source_ids = [fx.first, "cre:actor/item/0"]
        draft = deepcopy(e.draft)
        draft["plan"]["sources"][0]["source_ids"] = source_ids
        draft["plan"]["targets"][0]["capacity"] = 2
        saved = e.post("/api/save", draft)
        self.assertEqual(saved["plan"]["sources"][0]["source_ids"], source_ids)
        copied = e.post("/api/save", {**draft, "copy": True})
        self.assertEqual(copied["plan"]["sources"][0]["source_ids"], source_ids)
        reloaded = Editor(fx.catalog, fx.world, e.workspace).post("/api/load", {"id": "sample"})
        self.assertEqual(reloaded["plan"]["sources"][0]["source_ids"], source_ids)
        prepared = e.post("/api/prepare", draft)
        campaign = read_campaign(Path(prepared["store"]))
        self.assertEqual([row["id"] for row in campaign["sources"]], source_ids)

    def test_creature_source_can_be_saved_and_checked_for_test_runtime(self):
        fx = self.fx
        (fx.override / "actor.cre").write_bytes(make_cre([
            item_record("SYNTH001", expiration=0, charges=(0, 0, 0), flags=1)]))
        fx.refresh()
        self.reset_editor("cre:actor/item/0")
        e = self.editor
        self.assertEqual(e.post("/api/save", e.draft)["plan"], e.draft["plan"])
        result = e.post("/api/validate", e.draft)
        self.assertTrue(result["valid"], result)
        self.assertTrue(result["runtime_supported"], result)
        self.assertEqual(result["message"], "Configuration checks passed.")

    def test_recipient_names_aliases_and_type_filters(self):
        fx = self.fx
        actor = next(row for row in fx.world["targets"] if row["id"] == "are:area/actor/0")
        area = bytearray(fx.area)
        start = actor["selector"]["byte_offset"]
        area[start:start + 32] = b"Jon Irenicus".ljust(32, b"\0")
        (fx.override / "area.are").write_bytes(area)
        (fx.override / "merchant.sto").write_bytes(make_sto())
        fx.refresh()
        self.reset_editor()
        e = self.editor
        named = e.get("/api/targets", {"q": "Irenicus", "kind": "creature"})["records"]
        self.assertEqual([r["id"] for r in named], ["are:area/actor/0"])
        self.assertEqual(named[0]["label"], "Jon Irenicus (AREA)")
        self.assertEqual(named[0]["kind_label"], "Creature")
        aliases = e.get("/api/targets", {"q": "TestDV"})["records"]
        self.assertIn("are:area/actor/0", [r["id"] for r in aliases])
        self.assertEqual(e.get("/api/targets", {"q": "Irenicus", "kind": "container"})["total"], 0)
        containers = e.get("/api/targets", {"q": "Chest", "kind": "container"})["records"]
        self.assertEqual([r["kind_label"] for r in containers], ["Container", "Container"])
        shop = e.get("/api/targets", {"q": "merchant", "kind": "store"})["records"][0]
        self.assertEqual(shop["kind_label"], "Shop")
        draft = deepcopy(e.draft)
        draft["plan"]["targets"] = [{"id": containers[1]["id"], "capacity": 1, "optional": False}]
        self.assertEqual(e.post("/api/save", draft)["targets"][0]["kind_label"], "Container")
        result = e.post("/api/validate", draft)
        self.assertTrue(result["valid"], result)
        self.assertTrue(result["runtime_supported"], result)

    def test_http_search_load_and_check_share_the_real_editor(self):
        e = self.editor
        with create_server(e) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            def request(path, data=None, origin=None):
                headers = {"Content-Type": "application/json"}
                if origin:
                    headers["Origin"] = origin
                req = Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                              headers=headers)
                with urlopen(req, timeout=5) as response:
                    return json.load(response)["result"]
            try:
                self.assertEqual(request("/api/templates")[0]["id"], "bg1-original")
                self.assertEqual(request("/api/items")["items"], [])
                self.assertEqual(request("/api/items?q=synth001")["items"][0]["resref"], "synth001")
                source = request("/api/sources?item=synth001")["records"][0]
                self.assertEqual(source["id"], self.fx.first)
                self.assertEqual(source["quantity"], 1)
                self.assertIn("ChestOne", source["label"])
                self.assertEqual(request("/api/targets?q=EmbedDV")["records"][0]["id"], "are:area/actor/1")
                self.assertTrue(request("/api/validate", e.draft)["runtime_supported"])
                request("/api/save", e.draft)
                self.assertEqual(request("/api/load", {"id": "sample"})["plan"], e.draft["plan"])
                with self.assertRaises(HTTPError):
                    request("/api/save", e.draft, "https://unrelated.example")
            finally:
                server.shutdown()
                thread.join(timeout=5)

    def test_source_results_show_full_finite_shop_stock(self):
        fx = self.fx
        (fx.override / "shop.sto").write_bytes(make_sto([
            (item_record("SYNTH001", expiration=0, charges=(1, 0, 0), flags=1), 3, 0)]))
        fx.refresh()
        self.reset_editor()
        records = self.editor.get("/api/sources", {"item": "synth001"})["records"]
        shop = next(row for row in records if row["kind"] == "store")
        self.assertEqual(shop["quantity"], 3)
        self.assertEqual(next(row for row in records if row["kind"] == "container")["quantity"], 1)


if __name__ == "__main__":
    unittest.main()
