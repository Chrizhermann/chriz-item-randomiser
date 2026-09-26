"""Pure Lua campaign lifetime correlation tests across supported runtimes."""

from pathlib import Path
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT21


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "prototype" / "FLPRLife.lua").read_text(encoding="utf-8")
DROP = (ROOT / "prototype" / "FLPRDrop.lua").read_text(encoding="utf-8")
CASES = (ROOT / "tests" / "lua" / "test_campaign_lifetime_tracker.lua").read_text(encoding="utf-8")
NAMES = sorted(Lua51().execute(CASES).keys())


def make_case(name):
    def run(self):
        lua = self.runtime(unpack_returned_tuples=True)
        lua.execute(CASES)[name](lua.execute(MODULE), lua.execute(DROP))
    return run


for label, runtime in (("Lua51", Lua51), ("Lua53", Lua53), ("LuaJIT21", LuaJIT21)):
    globals()[label + "LifetimeTrackerTests"] = type(
        label + "LifetimeTrackerTests", (unittest.TestCase,),
        {"runtime": runtime, **{"test_" + name: make_case(name) for name in NAMES}},
    )


if __name__ == "__main__":
    unittest.main()
