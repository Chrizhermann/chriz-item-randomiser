"""Execute the experimental Lua protocol in Lua 5.1, Lua 5.3 and LuaJIT 2.1.

Requires the separately installed test-only `lupa` package. Missing runtimes fail
the suite rather than silently skipping its compatibility checks. No game APIs
are called: the host and save-level GLOBAL store are synthetic Lua tables.
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT21

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from flir_config.campaigns import read_campaign
from flir_config.profiles import edit_profile, new_profile, write_json
from flir_config.resources import scan_game
from flir_config.sources import new_binding_plan, scan_world
from test_postinstall_resources import make_game, snapshot
from test_postinstall_world_formats import item_record, make_cre
CORE_TEXT = (ROOT / "prototype" / "FLPRCore.lua").read_text(encoding="utf-8")
ADAPTER_TEXT = (ROOT / "prototype" / "FLPRGlob.lua").read_text(encoding="utf-8")
CASES_TEXT = (ROOT / "tests" / "lua" / "test_campaign_prototype.lua").read_text(encoding="utf-8")
CASE_NAMES = sorted(Lua51().execute(CASES_TEXT).keys())


def make_case(name):
    def run(self):
        lua = self.runtime(unpack_returned_tuples=True)
        core, adapter = lua.execute(CORE_TEXT), lua.execute(ADAPTER_TEXT)
        lua.execute(CASES_TEXT)[name](core, adapter)
    return run


for label, runtime in (("Lua51", Lua51), ("Lua53", Lua53), ("LuaJIT21", LuaJIT21)):
    globals()[label + "CampaignTests"] = type(label + "CampaignTests", (unittest.TestCase,),
        {"runtime": runtime, **{"test_" + name: make_case(name) for name in CASE_NAMES}})


class CampaignIntegrationTests(unittest.TestCase):
    def test_command_compiler_to_three_lua_runtimes_and_two_saved_campaigns(self):
        with tempfile.TemporaryDirectory(prefix="flir-runtime-integration-") as temporary:
            root = Path(temporary)
            game = root / "game"
            make_game(game)
            (game / "override" / "actor.cre").write_bytes(make_cre([item_record(flags=1), item_record(flags=4)]))
            before = snapshot(game)
            catalog = scan_game(game)
            world = scan_world(catalog)
            profile = edit_profile(new_profile(catalog, "integration", "Integration"), catalog, include=["synth001"])
            plan = new_binding_plan(profile, catalog, world)
            plan["sources"] = [{"item": "synth001", "policy": "relocate",
                                "source_ids": ["cre:actor/item/0"], "registration_ids": []}]
            plan["targets"] = [{"id": "cre:actor", "capacity": 2, "optional": False}]
            for name, body in (("catalog", catalog), ("world", world), ("profile", profile), ("plan", plan)):
                write_json(root / (name + ".json"), body)
            args = [sys.executable, str(ROOT / "tools" / "randomiser_config.py"), "prototype", "prepare"]
            for name in ("catalog", "world", "profile", "plan"):
                args += ["--" + name, str(root / (name + ".json"))]
            store = root / "prepared"
            args += ["--store", str(store)]
            first = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            first = json.loads(first.stdout)["result"]
            first_lua = Path(first["lua_path"]).read_text()
            plan["sources"][0]["source_ids"] = ["cre:actor/item/1"]
            write_json(root / "plan.json", plan)
            second = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            second = json.loads(second.stdout)["result"]
            second_lua = Path(second["lua_path"]).read_text()
            self.assertNotEqual(first["revision"], second["revision"])
            self.assertEqual(read_campaign(store, first["revision"])["revision"], first["revision"])
            inspect = subprocess.run([sys.executable, str(ROOT / "tools" / "randomiser_config.py"),
                                      "prototype", "inspect", "--store", str(store)], capture_output=True, text=True)
            self.assertEqual(inspect.returncode, 0, inspect.stderr)
            self.assertEqual(json.loads(inspect.stdout)["result"]["revision"], second["revision"])
            for runtime in (Lua51, Lua53, LuaJIT21):
                with self.subTest(runtime=runtime.__module__):
                    lua = runtime(unpack_returned_tuples=True)
                    core = lua.execute(CORE_TEXT)
                    old, new = lua.execute(first_lua), lua.execute(second_lua)
                    run = lua.eval('''function(Core, old, new)
                        local saves, current = {{}, {}}, 1
                        local library = {[old.revision] = old, [new.revision] = new}
                        local store = {}
                        function store:get(key) return saves[current][key] or 0 end
                        function store:set(key, value) saves[current][key] = value end
                        local controller = assert(Core.new(store, function(id) return library[id] end))
                        assert(controller:capture(old, 17, "confirmed-new-game"))
                        local a = controller:assignments()[1]
                        controller:destroy(); current = 2
                        assert(controller:capture(new, 23, "confirmed-new-game"))
                        local b = controller:assignments()[1]
                        assert(a.source_id ~= b.source_id)
                        controller:destroy(); current = 1
                        assert(controller:restore())
                        assert(controller.manifest.revision == old.revision)
                        assert(controller:assignments()[1].source_id == a.source_id)
                        assert(controller:assignments()[1].ordinal == a.ordinal)
                        controller:destroy(); current = 2
                        assert(controller:restore())
                        assert(controller.manifest.revision == new.revision)
                        return true
                    end''')
                    self.assertTrue(run(core, old, new))
            self.assertEqual(snapshot(game), before)


if __name__ == "__main__":
    unittest.main()
