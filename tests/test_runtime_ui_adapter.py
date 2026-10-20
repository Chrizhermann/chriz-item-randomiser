"""Routing of the UI adapter that CIR installs (M_FLPRUI.lua).

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
startEngine = {OnNewGameButtonClick = method("engine:new-game"),
    OnImportCharacterButtonClick = method("engine:import")}
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
    ImportCharacter = function(...) calls[#calls + 1] = {"runtime:import", ...}; return "runtime:import" end,
    AcceptCharacter = function(...) calls[#calls + 1] = {"runtime:accept", ...}; return "runtime:accept" end,
}
'''
# The shape of the dispatcher that mods built on DavidW's SFO library install
# (dwSystemCall), with a default function for character acceptance that calls
# the engine, as written anew by such a mod installed after CIR.
SFO = r'''
dwSystemCallDefault = {}
dwSystemCallDefault['createCharScreen:AcceptCharacter'] = function(arg1, arg2, arg3)
    createCharScreen:AcceptCharacter(arg1)
end
sfo_default = dwSystemCallDefault['createCharScreen:AcceptCharacter']
function dwSystemCall(fn, menu, arg1, arg2, arg3)
    dwSystemCallDefault[fn](arg1, arg2, arg3)
end
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

    def test_character_import_takes_the_new_game_route(self):
        for name, module in RUNTIMES:
            with self.subTest(name, configured=False):
                lua = self.lua(module, runtime=True)
                self.assertEqual(lua.eval("FLPRUI_ImportCharacter()"), "engine:import")
                self.assertEqual(self.calls(lua), [("engine:import",)])
        self.configure()
        for name, module in RUNTIMES:
            with self.subTest(name, configured=True):
                lua = self.lua(module, runtime=True)
                self.assertEqual(lua.eval("FLPRUI_ImportCharacter()"), "runtime:import")
                self.assertEqual(self.calls(lua), [("runtime:import",)])
                # Without the runtime it asks first; the start choice imports unrandomised.
                lua = self.lua(module)
                self.assertIsNone(lua.execute("return FLPRUI_ImportCharacter(3)"))
                self.assertEqual(self.calls(lua), [])
                shown = lua.globals().popups[1]
                self.assertEqual(shown.text, lua.eval('FLPRUI_Text("not_running")'))
                shown.start()
                self.assertEqual(self.calls(lua), [("engine:import", 3)])

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
                self.assertEqual(lua.eval('FLPRUI_Text("ready")'), "Chriz Item Randomiser: this adventure's items are randomised.")
                self.assertIn("stopped working", lua.eval('FLPRUI_Text("stopped")'))
                self.assertIn("nothing to randomise in this campaign", lua.eval('FLPRUI_Text("other_campaign")'))
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

    def test_a_new_adventure_points_the_sfo_dispatcher_at_the_adapter(self):
        self.configure()
        for name, module in RUNTIMES:
            for start, first in (("FLPRUI_NewGame()", "runtime:new-game"),
                                 ("FLPRUI_ImportCharacter()", "runtime:import")):
                with self.subTest(name, start=start):
                    lua = self.lua(module, runtime=True)
                    lua.execute(SFO)
                    lua.execute(start)
                    # Exactly one argument reaches the runtime, as from SFO's own default.
                    lua.execute("dwSystemCall('createCharScreen:AcceptCharacter', nil, -1, 'extra', 'more')")
                    self.assertEqual(self.calls(lua), [(first,), ("runtime:accept", -1)])
            with self.subTest(name, runtime=False):
                # Starting unrandomised after the not-running popup reaches the engine.
                lua = self.lua(module)
                lua.execute(SFO)
                lua.execute("FLPRUI_NewGame()")
                lua.globals().popups[1].start()
                lua.execute("dwSystemCall('createCharScreen:AcceptCharacter', nil, 3)")
                self.assertEqual(self.calls(lua), [("engine:new-game",), ("engine:accept", 3)])

    def test_the_sfo_dispatcher_stays_as_installed_without_a_configuration(self):
        for name, module in RUNTIMES:
            with self.subTest(name):
                lua = self.lua(module, runtime=True)
                lua.execute(SFO)
                lua.execute("FLPRUI_NewGame()")
                self.assertTrue(lua.eval("dwSystemCallDefault['createCharScreen:AcceptCharacter'] == sfo_default"))
                lua.execute("dwSystemCall('createCharScreen:AcceptCharacter', nil, -1)")
                self.assertEqual(self.calls(lua), [("engine:new-game",), ("engine:accept", -1)])

    def test_a_dispatcher_without_character_acceptance_is_left_alone(self):
        self.configure()
        for name, module in RUNTIMES:
            with self.subTest(name):
                lua = self.lua(module, runtime=True)
                lua.execute("dwSystemCallDefault = {['startEngine:OnSoAButtonClick'] = function() end}")
                lua.execute("FLPRUI_NewGame()")
                self.assertTrue(lua.eval("dwSystemCallDefault['createCharScreen:AcceptCharacter'] == nil"))
                lua.execute("dwSystemCallDefault = 'not a table'")
                self.assertEqual(lua.eval("FLPRUI_NewGame()"), "runtime:new-game")

    def test_cancel_after_a_start_button_reopens_the_start_menus(self):
        # The stock New Game and Import buttons pop START_SP before they call the
        # adapter. Without its menus again, Cancel left the start screen black.
        menus = r'''
        menus = {}
        local function find(name)
            for index = #menus, 1, -1 do if menus[index] == name then return index end end
        end
        function Infinity_IsMenuOnStack(name) return find(name) ~= nil end
        function Infinity_PopMenu(name) local index = find(name); if index then table.remove(menus, index) end end
        function Infinity_PushMenu(name)
            menus[#menus + 1] = name
            -- START's onOpen for single player: BG:EE and EET replace START, BG2:EE keeps it.
            if name == "START" then
                if pop_start then Infinity_PopMenu("START") end
                Infinity_PushMenu("START_SP")
            end
        end
        '''
        failing = r'''FLPR = {
            NewGame = function() FLPRUI_Ask("setup_failed", function() calls[#calls + 1] = {"plain"} end) end,
            ImportCharacter = function() FLPRUI_Ask("other_campaign", function() end) end,
            AcceptCharacter = function() FLPRUI_Ask("setup_failed", function() end) end,
        }'''
        self.configure()
        for name, module in RUNTIMES:
            for style, below in (("BG:EE and EET", []), ("BG2:EE", ["START"])):
                for runtime, start in ((False, "FLPRUI_NewGame()"), (False, "FLPRUI_ImportCharacter()"),
                                       (True, "FLPRUI_NewGame()"), (True, "FLPRUI_ImportCharacter()")):
                    with self.subTest(name, style=style, runtime=runtime, start=start):
                        lua = self.lua(module)
                        lua.execute(menus)
                        lua.execute(f"pop_start = {'true' if not below else 'false'}")
                        if runtime:
                            lua.execute(failing)
                        for menu in below:
                            lua.execute(f'menus[#menus + 1] = "{menu}"')
                        lua.execute(start)
                        popup = lua.globals().popups[1]
                        self.assertIsNotNone(popup.cancel_func)
                        popup.cancel_func()
                        stack = lua.globals().menus
                        self.assertEqual([stack[i] for i in range(1, len(stack) + 1)], below + ["START_SP"])
                        # Pressed again with the start menu open, Cancel changes nothing.
                        popup.cancel_func()
                        self.assertEqual([stack[i] for i in range(1, len(stack) + 1)], below + ["START_SP"])
                        self.assertEqual(self.calls(lua), [])
            with self.subTest(name, accept=True):
                # Character creation stays open under its popup: Cancel only closes it.
                lua = self.lua(module)
                lua.execute(menus)
                lua.execute(failing)
                lua.execute('menus[1] = "CHARGEN"')
                lua.execute("FLPRUI_AcceptCharacter(-1)")
                self.assertIsNone(lua.globals().popups[1].cancel_func)
                # A start button's own failure does not leave the start Cancel behind.
                lua.execute("FLPR.NewGame = function() error('broken runtime', 0) end")
                with self.assertRaisesRegex(module.LuaError, "broken runtime"):
                    lua.execute("FLPRUI_NewGame()")
                lua.execute("FLPRUI_AcceptCharacter(-1)")
                self.assertIsNone(lua.globals().popups[2].cancel_func)

    def test_without_io_or_loadfile_a_configuration_cannot_be_seen(self):
        # Documented limit: such an engine Lua starts New Game unrandomised.
        self.configure()
        for name, module in RUNTIMES:
            with self.subTest(name):
                results, calls = self.press(self.lua(module, io=False, loadfile=False))
                self.assertEqual(calls, [("engine:new-game",), ("engine:accept", -1)])


if __name__ == "__main__":
    unittest.main()
