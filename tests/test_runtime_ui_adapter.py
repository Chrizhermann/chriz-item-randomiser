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
-- util.lua's popup and the string table it reads.
uiStrings = {CANCEL_BUTTON = "Cancel"}
popups = {}
function popup2Button(info, okText, okFunc, cancelText, cancelFunc)
    popups[#popups + 1] = {text = uiStrings[info], ok = uiStrings[okText], start = okFunc,
        cancel = uiStrings[cancelText], cancel_func = cancelFunc}
end
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
        Path("randomiser-campaigns").mkdir(exist_ok=True)
        Path("randomiser-campaigns/next.lua").write_text(text, encoding="ascii")

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

    def asks_before_starting_unrandomised(self, lua):
        """New Game shows the not-running popup; only its start choice reaches the engine."""
        self.assertIsNone(lua.eval("FLPRUI_NewGame()"))
        self.assertEqual(self.calls(lua), [])
        popups = lua.globals().popups
        self.assertEqual(len(popups), 1)
        shown = popups[1]
        self.assertEqual(shown.text, lua.eval('FLPRUI_Text("not_running")'))
        self.assertIn("InfinityLoader.exe", shown.text)
        self.assertEqual((shown.ok, shown.cancel, shown.cancel_func), ("Start without randomiser", "Cancel", None))
        shown.start()
        self.assertEqual(self.calls(lua), [("engine:new-game",)])

    def test_new_game_asks_when_a_configuration_has_no_runtime(self):
        self.configure()
        for name, module in RUNTIMES:
            for partial in (False, True):
                with self.subTest(name, partial=partial):
                    lua = self.lua(module)
                    if partial:  # M_FLPR.lua stopped before defining its entry points.
                        lua.execute("FLPR = {Disabled = 'INITIALIZING'}")
                    self.asks_before_starting_unrandomised(lua)
                    # Other character creation paths still reach the engine.
                    self.assertEqual(lua.eval("FLPRUI_AcceptCharacter(-1)"), "engine:accept")

    def test_the_start_choice_keeps_the_original_new_game_arguments(self):
        self.configure()
        for name, module in RUNTIMES:
            with self.subTest(name):
                lua = self.lua(module)
                self.assertIsNone(lua.execute("return FLPRUI_NewGame(7, 'x')"))
                lua.globals().popups[1].start()
                self.assertEqual(self.calls(lua), [("engine:new-game", 7, "x")])

    def test_a_ui_without_the_engine_popup_refuses_instead_of_starting_unrandomised(self):
        self.configure()
        for name, module in RUNTIMES:
            with self.subTest(name):
                lua = self.lua(module)
                lua.execute("popup2Button = nil")
                with self.assertRaisesRegex(module.LuaError, "not running"):
                    lua.eval("FLPRUI_NewGame()")
                self.assertEqual(self.calls(lua), [])

    def test_messages_use_the_installed_game_text_with_english_fallback(self):
        for name, module in RUNTIMES:
            with self.subTest(name):
                lua = self.lua(module)
                english = lua.eval('FLPRUI_Text("not_running")')
                lua.execute('''FLPR_TEXT = {not_running = 12, ready = 13, stopped = -1}
                    Infinity_FetchString = function(ref) return ({[12] = "Nie dziala.", [13] = ""})[ref] end''')
                self.assertEqual(lua.eval('FLPRUI_Text("not_running")'), "Nie dziala.")
                # Empty or invalid references, and keys without one, keep English.
                self.assertEqual(lua.eval('FLPRUI_Text("ready")'), "Item Randomiser: this adventure's items are randomised.")
                self.assertIn("stopped working", lua.eval('FLPRUI_Text("stopped")'))
                self.assertIn("Baldur's Gate (BG1)", lua.eval('FLPRUI_Text("other_campaign")'))
                lua.execute("Infinity_FetchString = nil")
                self.assertEqual(lua.eval('FLPRUI_Text("not_running")'), english)

    def test_engine_lua_without_io_detects_the_configuration_through_loadfile(self):
        for name, module in RUNTIMES:
            with self.subTest(name, configured=False):
                results, calls = self.press(self.lua(module, io=False))
                self.assertEqual(calls, [("engine:new-game",), ("engine:accept", -1)])
        for text in ('return "' + "b" * 64 + '"\n', "this is not Lua ("):
            self.configure(text)
            for name, module in RUNTIMES:
                with self.subTest(name, configured=text[:6]):
                    self.asks_before_starting_unrandomised(self.lua(module, io=False))

    def test_without_io_or_loadfile_a_configuration_cannot_be_seen(self):
        # Documented limit: such an engine Lua starts New Game unrandomised.
        self.configure()
        for name, module in RUNTIMES:
            with self.subTest(name):
                results, calls = self.press(self.lua(module, io=False, loadfile=False))
                self.assertEqual(calls, [("engine:new-game",), ("engine:accept", -1)])


if __name__ == "__main__":
    unittest.main()
