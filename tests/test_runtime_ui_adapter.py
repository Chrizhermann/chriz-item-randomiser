"""Routing of the UI adapter that component 1500 installs (M_FLPRUI.lua).

The real adapter runs in Lua 5.1, Lua 5.3 and LuaJIT 2.1 against stub engine
objects, with and without the io library the engine's own Lua lacks. The
configuration pointer is looked up relative to the working directory, as in
the game folder. No engine is launched.
"""
import os
from pathlib import Path
import tempfile
import unittest

import lupa.lua51
import lupa.lua53
import lupa.luajit21

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = (ROOT / "prototype/M_FLPRUI.lua").read_text(encoding="utf-8")
RUNTIMES = (("Lua 5.1", lupa.lua51), ("Lua 5.3", lupa.lua53), ("LuaJIT 2.1", lupa.luajit21))
ENGINE = r'''
calls = {}
local function method(name)
    return function(self, ...) calls[#calls + 1] = {name, ...}; return name end
end
startEngine = {OnNewGameButtonClick = method("engine:new-game")}
createCharScreen = {AcceptCharacter = method("engine:accept")}
'''
RUNTIME = r'''
FLPR = {
    NewGame = function(...) calls[#calls + 1] = {"runtime:new-game", ...}; return "runtime:new-game" end,
    AcceptCharacter = function(...) calls[#calls + 1] = {"runtime:accept", ...}; return "runtime:accept" end,
}
'''


class UiAdapterTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="flprui-")
        self.addCleanup(folder.cleanup)
        previous = os.getcwd()
        os.chdir(folder.name)
        self.addCleanup(os.chdir, previous)

    def configure(self, text='return "' + "a" * 64 + '"\n'):
        Path("flpr-fixture").mkdir(exist_ok=True)
        Path("flpr-fixture/next.lua").write_text(text, encoding="ascii")

    def lua(self, module, *, runtime=False, io=True, loadfile=True):
        lua = module.LuaRuntime()
        lua.execute(ENGINE)
        if runtime:
            lua.execute(RUNTIME)
        if not io:
            lua.execute("io = nil")
        if not loadfile:
            lua.execute("loadfile = nil")
        lua.execute(ADAPTER)
        return lua

    @staticmethod
    def calls(lua):
        calls = lua.globals().calls
        return [tuple(calls[i][j] for j in range(1, len(calls[i]) + 1)) for i in range(1, len(calls) + 1)]

    def press(self, lua):
        """New Game, then character acceptance with an explicit difficulty."""
        results = (lua.eval("FLPRUI_NewGame()"), lua.eval("FLPRUI_AcceptCharacter(-1)"))
        return results, self.calls(lua)

    def test_without_configuration_the_engine_handles_both_calls(self):
        for name, module in RUNTIMES:
            for runtime in (False, True):
                with self.subTest(name, runtime=runtime):
                    results, calls = self.press(self.lua(module, runtime=runtime))
                    self.assertEqual(results, ("engine:new-game", "engine:accept"))
                    self.assertEqual(calls, [("engine:new-game",), ("engine:accept", -1)])

    def test_a_configuration_routes_both_calls_through_the_runtime(self):
        self.configure()
        for name, module in RUNTIMES:
            with self.subTest(name):
                lua = self.lua(module, runtime=True)
                results, calls = self.press(lua)
                self.assertEqual(results, ("runtime:new-game", "runtime:accept"))
                self.assertEqual(calls, [("runtime:new-game",), ("runtime:accept", -1)])
                lua.eval("FLPRUI_AcceptCharacter(currentDifficulty)")
                self.assertEqual(self.calls(lua)[-1], ("runtime:accept",))

    def test_new_game_refuses_a_configuration_without_its_runtime(self):
        self.configure()
        for name, module in RUNTIMES:
            for partial in (False, True):
                with self.subTest(name, partial=partial):
                    lua = self.lua(module)
                    if partial:  # M_FLPR.lua stopped before defining its entry points.
                        lua.execute("FLPR = {Disabled = 'INITIALIZING'}")
                    with self.assertRaisesRegex(module.LuaError, "campaign runtime is not running"):
                        lua.eval("FLPRUI_NewGame()")
                    self.assertEqual(self.calls(lua), [])
                    # Other character creation paths still reach the engine.
                    self.assertEqual(lua.eval("FLPRUI_AcceptCharacter(-1)"), "engine:accept")

    def test_engine_lua_without_io_detects_the_configuration_through_loadfile(self):
        for name, module in RUNTIMES:
            with self.subTest(name, configured=False):
                results, calls = self.press(self.lua(module, io=False))
                self.assertEqual(calls, [("engine:new-game",), ("engine:accept", -1)])
        for text in ('return "' + "b" * 64 + '"\n', "this is not Lua ("):
            self.configure(text)
            for name, module in RUNTIMES:
                with self.subTest(name, configured=text[:6]):
                    lua = self.lua(module, io=False)
                    with self.assertRaisesRegex(module.LuaError, "campaign runtime is not running"):
                        lua.eval("FLPRUI_NewGame()")
                    self.assertEqual(self.calls(lua), [])

    def test_without_io_or_loadfile_a_configuration_cannot_be_seen(self):
        # Documented limit: such an engine Lua starts New Game unrandomised.
        self.configure()
        for name, module in RUNTIMES:
            with self.subTest(name):
                results, calls = self.press(self.lua(module, io=False, loadfile=False))
                self.assertEqual(calls, [("engine:new-game",), ("engine:accept", -1)])


if __name__ == "__main__":
    unittest.main()
