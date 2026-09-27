"""Editor workflows reuse the configurator's synthetic installation fixture."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from flir_config.campaigns import read_campaign
from flir_config.editor import Editor, create_server
from flir_config.profiles import edit_profile, new_profile
from flir_config.sources import new_binding_plan
from tests import test_postinstall_native_export as fixtures
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
        self.assertFalse(result["runtime_supported"])

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
                self.assertEqual(request("/api/items")["items"], [])
                self.assertEqual(request("/api/items?q=synth001")["items"][0]["resref"], "synth001")
                source = request("/api/sources?item=synth001")["records"][0]
                self.assertEqual(source["id"], self.fx.first)
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


if __name__ == "__main__":
    unittest.main()
