"""Editor workflows reuse the configurator's synthetic installation fixture."""
from copy import deepcopy
import json
from pathlib import Path
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
from flir_config.profiles import edit_profile, new_profile
from flir_config.profiles import ProfileError
from flir_config.sources import new_binding_plan
from tests import test_postinstall_native_export as fixtures
from tests.test_postinstall_blpool_script import write_registry
from tests.test_postinstall_world_formats import make_cre, make_sto, item_record


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

    def test_preset_routes_replace_only_a_checked_successful_draft(self):
        editor = self.editor
        descriptors = editor.get("/api/templates", {})
        self.assertEqual([row["id"] for row in descriptors[:2]],
                         ["bg1-mode1", "bg1-mode1-plus-mode2"])
        recommended = editor.get("/api/recommended", {})
        self.assertEqual(recommended["id"], "eet-mode1")
        self.assertIn(recommended["id"], [row["id"] for row in descriptors])
        # Without CIR's record, Glaicus's conversation is not prepared.
        self.assertFalse(recommended["options"]["flail_heads"])
        self.assertIn("KPGLAI01.DLG", recommended["unavailable"]["flail_heads"])
        self.assertTrue(recommended["options"]["totsc_tome"])
        write_registry(self.fx.game)
        recommended = editor.get("/api/recommended", {})
        self.assertTrue(recommended["options"]["flail_heads"] and recommended["options"]["totsc_tome"])
        self.assertEqual(recommended["unavailable"], {})
        write_registry(self.fx.game, flail_heads="dialogue-unrecognised")
        recommended = editor.get("/api/recommended", {})
        self.assertFalse(recommended["options"]["flail_heads"])
        self.assertEqual(recommended["unavailable"]["flail_heads"],
                         "KPGLAI01.DLG was left as installed (the conversation has an unexpected form)")
        original = deepcopy(editor.draft)
        calls = []
        module = ModuleType("flir_config.original_configuration")
        target_id = "original-target:bg1:base:target:3"

        def build(catalog, world, template_id, *, items, targets, options):
            calls.append((catalog, world, template_id, items, targets, options))
            result = deepcopy(original)
            result["profile"]["name"] = "BG1 original"
            result["plan"]["randomization"] = {"tier_rows": ["original-choice"]}
            result["plan"]["original_options"] = {"scrolls": True}
            result["plan"]["original_template_id"] = "bg1-mode1"
            result["plan"]["target_registrations"] = [{
                "id": target_id, "capacity": 1,
                "authored": {"target": "container1", "area_resource": "are:bg5500"}}]
            result["plan"]["targets"] = [{"id": target_id, "capacity": 1, "optional": False}]
            result["left_out"] = ["BLPOOL.BCS was left as installed (it gives the reward more than once)"]
            return result

        module.build_original_configuration = build
        with patch.dict(sys.modules, {module.__name__: module}):
            state = editor.post("/api/template", {
                "id": "bg1-mode1", "items": "mode1+mode2", "targets": "mode1",
                "options": {"scrolls": True}})
            self.assertEqual(calls[0][2:], ("bg1-mode1", "mode1+mode2", "mode1",
                                           {"scrolls": True}))
            self.assertEqual(state["profile"]["name"], "BG1 original")
            self.assertEqual(state["left_out"],
                             ["BLPOOL.BCS was left as installed (it gives the reward more than once)"])
            self.assertNotIn("left_out", editor.draft)
            self.assertNotEqual(state["profile"]["id"], original["profile"]["id"])
            self.assertEqual(editor.post("/api/save", editor.draft)["plan"]["randomization"],
                             {"tier_rows": ["original-choice"]})
            self.assertEqual(state["targets"][0]["label"], "container1 (are:bg5500)")
            self.assertEqual(editor.draft["plan"]["original_options"], {"scrolls": True})
            self.assertEqual(editor.draft["plan"]["original_template_id"], "bg1-mode1")
            self.assertEqual(editor.draft["plan"]["target_registrations"][0]["id"], target_id)

            before_error = deepcopy(editor.draft)
            def failed(*_, **__):
                raise ProfileError("Preset cannot be bound")
            module.build_original_configuration = failed
            with self.assertRaisesRegex(ProfileError, "cannot be bound"):
                editor.post("/api/template", {"id": "bg2-mode1"})
            self.assertEqual(editor.draft, before_error)

            def malformed(*_, **__):
                result = deepcopy(original)
                result["plan"]["world_fingerprint"] = "another-scan"
                return result
            module.build_original_configuration = malformed
            with self.assertRaisesRegex(ProfileError, "different scan"):
                editor.post("/api/template", {"id": "bg2-mode1"})
            self.assertEqual(editor.draft, before_error)

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
        self.assertEqual((fx.game / "randomiser-campaigns/next.lua").read_bytes(), _render_lua(second["revision"]))
        self.assertEqual(unrelated.read_bytes(), b"preserve unrelated mod content")
        self.assertEqual((fx.override / "area.are").read_bytes(), fx.area)
        reopened = Editor(fx.catalog, fx.world, e.workspace, apply_game=fx.game)
        self.assertEqual(reopened.draft, draft)
        self.assertTrue(reopened.state()["apply_enabled"])
        pointer = (fx.game / "randomiser-campaigns/next.lua").read_bytes()
        with patch("flir_config.activation.require_closed", side_effect=ProfileError("Close this test game")):
            with self.assertRaisesRegex(ProfileError, "Close this test game"):
                e.post("/api/apply", original.draft)
        self.assertEqual((fx.game / "randomiser-campaigns/next.lua").read_bytes(), pointer)
        other = Editor(fx.catalog, fx.world, fx.root / "another-editor",
                       **original.draft, apply_game=fx.game)
        with patch("flir_config.activation.require_closed"):
            with self.assertRaisesRegex(ProfileError, "older saves"):
                other.post("/api/apply", other.draft)
        self.assertEqual((fx.game / "randomiser-campaigns/next.lua").read_bytes(), pointer)

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
        self.assertIn("Game testing and deployment are separate", result["message"])

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
                self.assertEqual(request("/api/templates")[0]["id"], "bg1-mode1")
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
