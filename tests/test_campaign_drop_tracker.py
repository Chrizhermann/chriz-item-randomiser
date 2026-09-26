"""Pure Lua death-drop correlation tests across the three supported runtimes."""

from pathlib import Path
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT21


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "prototype" / "FLPRDrop.lua").read_text(encoding="utf-8")
CASES = (ROOT / "tests" / "lua" / "test_campaign_drop_tracker.lua").read_text(encoding="utf-8")
NAMES = sorted(Lua51().execute(CASES).keys())


def make_case(name):
    def run(self):
        lua = self.runtime(unpack_returned_tuples=True)
        lua.execute(CASES)[name](lua.execute(MODULE))
    return run


for label, runtime in (("Lua51", Lua51), ("Lua53", Lua53), ("LuaJIT21", LuaJIT21)):
    globals()[label + "DropTrackerTests"] = type(label + "DropTrackerTests", (unittest.TestCase,),
        {"runtime": runtime, **{"test_" + name: make_case(name) for name in NAMES}})


if __name__ == "__main__":
    unittest.main()
