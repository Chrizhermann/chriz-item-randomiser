"""Offline safety checks for UI sequencing; no game or remote client is run."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT21

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import create_fixture_character as helper

ENGINES = (Lua51, Lua53, LuaJIT21)
# Character creation after the campaign choice, as lab test 8 ran it on 2.7.3.
CREATION = ["single-player", "new-game", "open-import", "choose-file", "pick-character", "open-appearance",
            "accept-appearance", "accept-sounds", "open-name", "set-name", "capture", "enter"]
CHAOTIC, NEUTRAL = "Abdel - Fighter - Chaotic Good", "Abdel - Fighter - Neutral Good"
# Just enough engine for the start and import steps; calls records what they did.
FAKE_ENGINE = """
calls, menus, globals = {}, {}, {}
local function log(entry) calls[#calls + 1] = entry end
function Infinity_IsMenuOnStack(name) if menus[name] then return 1 end end
function Infinity_GetCurrentScreenName() return "EEEX_REMOTE" end
function Infinity_PopMenu(name) log("pop " .. tostring(name)); menus[name] = nil end
function Infinity_PushMenu(name) log("push " .. name); menus[name] = true end
function Infinity_ClickItem(item) log("click " .. tostring(item)) end
function EEex_GameState_GetGlobalInt(name) return globals[name] or 0 end
nameToItem = { FLPR_LAB_IDLE = "idle" }
const = { START_CAMPAIGN_BG = 1, START_CAMPAIGN_SOD = 3 }
startCampaignData = { { id = "BG1" }, { id = "SOD" }, { id = "SOA" }, { id = "TOB" } }
currentCampaign, campaign = 1, 1
function onCampaignButton(index) log("campaign " .. index) end
function resetSodLanguageIfNeeded() log("sod language") end
startEngine, createCharScreen = {}, {}
function startEngine:GetCampaign() return campaign end
function startEngine:OnSoAButtonClick(reload) log("soa") end
function startEngine:OnToBButtonClick(reload) log("tob") end
function startEngine:OnCampaignButtonClick(id, reload) log("campaign " .. id) end
function createCharScreen:GetEngineState() return 1 end
"""


def lua_list(table):
    return [table[index] for index in range(1, len(table) + 1)]


class Console:
    """Answers each request as a lab game does at that step: the menus then open."""

    def __init__(self, platform, menus=("START",), switch=True, chargen_step=15, closed_probes=0):
        self.platform, self.menus, self.switch = platform, list(menus), switch
        self.chargen_step, self.closed_probes = chargen_step, closed_probes
        self.calls = []

    def __call__(self, argv, **kwargs):
        label = Path(argv[-2][1:]).stem
        step = label.split("-", 1)[1]
        self.calls.append((step, argv[-1], kwargs["timeout"]))
        value = {"step": label, "open": ["START"]}
        if step == "check":
            value["open"] = self.menus
        elif step == "choose-campaign" and self.switch:
            # The reload closes START; BG2:EE's logo has pushed START_MAIN.
            value["open"] = ["START_MAIN"] if self.platform == "bg2ee" else []
        elif step in helper.PROBES:
            value["ready"] = self.closed_probes == 0
            self.closed_probes = max(0, self.closed_probes - 1)
        elif step == "pick-character":
            value["chargen_step"] = self.chargen_step
        return subprocess.CompletedProcess(argv, 0, json.dumps({"status": "ok", "returnValues": [value]}), "")

    @property
    def steps(self):
        return [call[0] for call in self.calls]


class FixtureCharacterTests(unittest.TestCase):
    def run_fake(self, run, *, pending=None, **options):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        game = root / "game"
        (game / "override").mkdir(parents=True)
        if pending:
            (game / "override" / pending).write_text("already queued", encoding="utf-8")
        evidence = root / "evidence"
        options.setdefault("platform", "eet")

        def invoke():
            with redirect_stdout(io.StringIO()):
                return helper.drive(game, "FLPR Test", evidence, "unused-powershell", run=run,
                                    pause=lambda seconds: None, **options)
        return game, evidence, invoke

    def test_every_step_is_literal_lua_in_all_supported_engines(self):
        value = '가"\\;error([[bad]])'
        helper.validate_name(value)
        for engine in ENGINES:
            lua = engine()
            self.assertEqual(lua.eval(helper.lua_string(value)), value)
            compile_lua = lua.eval("loadstring or load")
            for platform, starts in helper.STARTS.items():
                for start in starts:
                    for name, body in helper.step_bodies(platform, start, value, value + " - Fighter").items():
                        result = compile_lua(helper.render_step("01-" + name, body))
                        self.assertTrue(callable(result), (engine, platform, start, name, result))

    def test_new_game_and_acceptance_use_the_installed_ui_adapter(self):
        script = "\n".join(helper.step_bodies("eet", "BG1", "FLPR Test", CHAOTIC).values())
        self.assertIn("FLPRUI_NewGame()", script)
        self.assertEqual(script.count("FLPRUI_AcceptCharacter("), 2)
        self.assertNotIn("FLPR.NewGame()", script)
        self.assertNotIn("FLPR.AcceptCharacter(", script)

    def test_invalid_requests_are_rejected_before_any_actions(self):
        cases = [{"name": value} for value in ("", " ", "x" * 21, "test\nname", "test\0", "test\x7f")]
        cases += [{"character": value} for value in ("", "x" * 81, "Abdel\n")]
        cases += [{"platform": "bgee", "start": "SOA"}, {"platform": "bg2ee", "start": "BG1"},
                  {"start": "BP1"}, {"start": "SOD", "finish_prologue": True}]
        for case in cases:
            calls = []
            options = dict(case)
            name = options.pop("name", "FLPR Test")
            _, evidence, _ = self.run_fake(None)
            with self.subTest(case=case), self.assertRaises(helper.FixtureError):
                with redirect_stdout(io.StringIO()):
                    helper.drive(evidence.parent / "game", name, evidence, "unused-powershell",
                                 platform=options.pop("platform", "eet"), run=lambda *a, **k: calls.append(a),
                                 **options)
            self.assertEqual(calls, [])
            self.assertEqual(json.loads((evidence / "status.json").read_text())["status"], "stopped")

    def test_only_lab_games_are_driven(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            games = root / "Games"
            engine = 'engine_name = "{}"\nengine_mode = {} -- 0 = BGEE, 1 = BG2EE, 2 = IWDEE\n'

            def lab(folder, text, eet_flag=False, support=True):
                game = root / folder
                (game / "override").mkdir(parents=True)
                (game / "randomiser-campaigns").mkdir()
                (game / "engine.lua").write_text(text, encoding="utf-8")
                if eet_flag:
                    (game / "override" / "eet.FLAG").touch()
                for name in ("override/M_FLPR.lua", "override/M_EEexRC.lua", "randomiser-campaigns/next.lua")[
                        :None if support else 2]:
                    (game / name).touch()
                return game

            with patch.object(helper, "games_folder", return_value=games):
                for folder, profile, mode, eet_flag, platform in (
                        ("Games/Lab/eet/game", "CIR Lab 273 EET", 1, True, "eet"),
                        ("Games/Lab/bgee/game", "CIR Lab 273 BGEE", 0, False, "bgee"),
                        ("Games/Other Lab/bg2ee/game", "CIR Lab 273 Alpha BG2EE", 1, False, "bg2ee")):
                    game = lab(folder, engine.format(profile, mode), eet_flag)
                    self.assertEqual(helper.validate_game(game), (game, platform))
                refusals = (
                    ("Elsewhere/game", engine.format("CIR Lab Elsewhere", 1), {}, "below the user's Games folder"),
                    ("Games/Player/game", engine.format("Baldur's Gate II - Enhanced Edition", 1), {},
                     "private lab profile"),
                    ("Games/Two/game", engine.format("CIR Lab Two", 1) + 'engine_name = "CIR Lab Other"\n', {},
                     "exactly one"),
                    ("Games/Lua/game", engine.format("CIR Lab Lua", 1) + "dofile('other.lua')\n", {}, "more than"),
                    ("Games/Mode/game", 'engine_name = "CIR Lab Mode"\n', {}, "exactly one"),
                    ("Games/IWD/game", engine.format("CIR Lab IWD", 2), {}, "Engine mode 2"),
                    ("Games/Flag/game", engine.format("CIR Lab Flag", 0), {"eet_flag": True}, "EET.flag marks EET"),
                    ("Games/Bare/game", engine.format("CIR Lab Bare", 1), {"support": False},
                     "randomiser-campaigns/next.lua"),
                )
                for folder, text, options, error in refusals:
                    with self.subTest(folder=folder), self.assertRaisesRegex(helper.FixtureError, error):
                        helper.validate_game(lab(folder, text, **options))

    def test_every_platform_and_start_follows_the_proven_course(self):
        cases = (
            # EET opens START after a launch only on a mouse event; its campaign list
            # reloads only to switch away from another campaign.
            ("eet", "BG1", Console("eet", menus=[], switch=False), ["check", "wake", "await-start", "choose-campaign"]),
            ("eet", "SOD", Console("eet"), ["check", "choose-campaign", "await-start"]),
            ("eet", "SOA", Console("eet", menus=[]),
             ["check", "wake", "await-start", "choose-campaign", "await-start"]),
            ("eet", "TOB", Console("eet"), ["check", "choose-campaign", "await-start"]),
            # BG:EE: the campaign popup at a profile's first launch.
            ("bgee", "BG1", Console("bgee", menus=["START", "START_CAMPAIGN_SELECT"], switch=False),
             ["check", "dismiss-campaign-select", "choose-campaign"]),
            ("bgee", "SOD", Console("bgee"), ["check", "choose-campaign", "await-start"]),
            # BG2:EE: a logo always reloads; Throne of Bhaal levels the character up.
            ("bg2ee", "SOA", Console("bg2ee"), ["check", "choose-campaign", "await-start"]),
            ("bg2ee", "TOB", Console("bg2ee", menus=["START", "START_MAIN"], chargen_step=9),
             ["check", "back-to-logos", "choose-campaign", "await-start"]),
        )
        characters = {"eet": CHAOTIC, "bgee": CHAOTIC, "bg2ee": NEUTRAL}
        for platform, start, console, opening in cases:
            with self.subTest(platform=platform, start=start):
                _, evidence, invoke = self.run_fake(console, platform=platform, start=start)
                report = invoke()
                creation = list(CREATION)
                if console.chargen_step == 9:
                    creation[5:5] = ["open-proficiencies", "assign-proficiencies"]
                self.assertEqual(console.steps, opening + creation)
                self.assertEqual(report["status"], "awaiting-world-confirmation")
                self.assertEqual(len(list(evidence.glob("*.lua"))), len(console.calls))
                self.assertIn(f'"{start}"', next(evidence.glob("*-new-game.lua")).read_text())
                self.assertIn(helper.lua_string(characters[platform]),
                              next(evidence.glob("*-pick-character.lua")).read_text())

    def test_prologue_is_finished_on_bg1_starts(self):
        for platform in ("eet", "bgee"):
            with self.subTest(platform=platform):
                console = Console(platform, switch=False)
                _, _, invoke = self.run_fake(console, platform=platform, start="BG1", finish_prologue=True)
                report = invoke()
                self.assertEqual(console.steps[-4:], ["enter", "await-chapter", "finish-prologue", "check-world-menu"])
                self.assertEqual(report["status"], "awaiting-loot-confirmation")

    def test_probes_repeat_read_only_until_the_menu_opens(self):
        console = Console("eet", closed_probes=2)
        _, _, invoke = self.run_fake(console, start="SOD")
        invoke()
        self.assertEqual(console.steps[:6], ["check", "choose-campaign", "await-start", "await-start", "await-start",
                                             "single-player"])
        actions = [step for step in console.steps if step not in helper.PROBES]
        self.assertEqual(len(actions), len(set(actions)))

        console = Console("eet", closed_probes=10 ** 6)
        ticks = iter(range(0, 10 ** 6, 60))
        _, evidence, invoke = self.run_fake(console, start="SOD", clock=lambda: next(ticks))
        with self.assertRaisesRegex(helper.FixtureError, "START did not open"):
            invoke()
        self.assertEqual(console.steps, ["check", "choose-campaign", "await-start", "await-start", "await-start"])
        self.assertEqual(json.loads((evidence / "status.json").read_text())["status"], "stopped")

    def test_unexpected_answers_stop_the_course(self):
        cases = (
            ("bgee", "check", {"open": []}, r"\(START\) is not open"),
            ("bg2ee", "check", {"open": ["START_MAIN"]}, r"\(START\) is not open"),
            ("eet", "check", {"open": ["START", "START_MAIN"]}, "unexpected menus"),
            ("bgee", "check", {"open": ["START", "CHARGEN_IMPORT"]}, "unexpected menus"),
            ("bg2ee", "pick-character", {"open": ["CHARGEN"], "chargen_step": 12}, "step 12"),
            ("eet", "choose-campaign", {"open": "START"}, "open menus"),
        )
        for platform, step, value, error in cases:
            with self.subTest(platform=platform, step=step, value=value):
                with self.assertRaisesRegex(helper.FixtureError, error):
                    helper.follow_up(platform, step, {"step": "01-" + step, **value})

    def test_campaign_choice_clicks_the_idle_label_first_and_only_to_switch(self):
        cases = (
            ("eet", "SOD", "", ["click idle", "campaign 2"]),
            ("eet", "BG1", "", []),
            ("bgee", "SOD", "", ["click idle", "sod language", "campaign SOD"]),
            ("bgee", "BG1", "", []),
            ("bgee", "BG1", "campaign = 3", ["click idle", "soa"]),
            ("bg2ee", "SOA", "", ["click idle", "soa", "push START_MAIN"]),
            ("bg2ee", "TOB", "", ["click idle", "tob", "push START_MAIN"]),
            ("eet", "SOD", "nameToItem = {}", None),
        )
        for engine in ENGINES:
            for platform, start, setup, expected in cases:
                with self.subTest(engine=engine, platform=platform, start=start, setup=setup):
                    lua = engine()
                    lua.execute(FAKE_ENGINE + "menus.START = true\n" + setup)
                    script = helper.render_step("01-choose-campaign", helper.choose_campaign(platform, start))
                    if expected is None:
                        with self.assertRaisesRegex(Exception, "IDLE_LABEL_NOT_STAGED"):
                            lua.execute(script)
                        self.assertEqual(lua_list(lua.globals().calls), [])
                    else:
                        self.assertEqual(lua.execute(script)["switched"], bool(expected))
                        self.assertEqual(lua_list(lua.globals().calls), expected)

    def test_character_file_is_chosen_from_either_import_popup(self):
        bodies = helper.step_bodies("eet", "BG1", "FLPR Test", CHAOTIC)
        popups = {"POPUP_TWOBUTTON": 'Popup2Button = { okText = "CHARACTER_FILE_BUTTON", okFunc = choose_file }',
                  "POPUP_THREEBUTTON": 'Popup3Button = { midText = "CHARACTER_FILE_BUTTON", midFunc = choose_file }'}
        for engine in ENGINES:
            for buttons, (menu, popup) in enumerate(popups.items(), start=2):
                with self.subTest(engine=engine, menu=menu):
                    lua = engine()
                    lua.execute(FAKE_ENGINE + f"""
menus.CHARGEN = true
character_select = {{ {{ name = "{CHAOTIC}" }} }}
function choose_file() calls[#calls + 1] = "file"; menus.CHARACTER_SELECT = true end
function createCharScreen:OnImportCharacterButtonClick() menus.{menu} = true; {popup} end
""")
                    opened = lua.execute(helper.render_step("01-open-import", bodies["open-import"]))
                    self.assertEqual(opened["buttons"], buttons)
                    chosen = lua.execute(helper.render_step("02-choose-file", bodies["choose-file"]))
                    self.assertEqual(lua_list(lua.globals().calls), [f"pop {menu}", "file"])
                    self.assertEqual(lua_list(chosen["rows"]), [CHAOTIC])

    def test_new_game_stops_at_cir_refusal_or_another_start(self):
        body = helper.step_bodies("bgee", "SOD", "FLPR Test", CHAOTIC)["new-game"]
        cases = (
            ("""function FLPRUI_NewGame()
    menus.POPUP_TWOBUTTON = true
    Popup2Button = { info = "FLPR_POPUP" }
    uiStrings = { FLPR_POPUP = "nothing to randomise here" }
end""", "CIR_REFUSED_START:nothing to randomise here"),
            ('function FLPRUI_NewGame() menus.CHARGEN = true; FLPR = { NewGameIntent = { campaign = "BG1" } } end',
             "UNEXPECTED_START:BG1"),
            ('function FLPRUI_NewGame() menus.CHARGEN = true; FLPR = { NewGameIntent = { campaign = "SOD" } } end',
             None),
        )
        for engine in ENGINES:
            for setup, error in cases:
                with self.subTest(engine=engine, error=error):
                    lua = engine()
                    lua.execute(FAKE_ENGINE + "menus.START_SP = true\n" + setup)
                    script = helper.render_step("01-new-game", body)
                    if error:
                        with self.assertRaisesRegex(Exception, error):
                            lua.execute(script)
                    else:
                        self.assertEqual(lua.execute(script)["intent"]["campaign"], "SOD")

    def test_proficiencies_spend_every_free_slot(self):
        body = helper.step_bodies("bg2ee", "TOB", "FLPR Test", NEUTRAL)["assign-proficiencies"]
        setup = """menus.CHARGEN_PROFICIENCIES = true
chargen = { extraProficiencySlots = SLOTS, proficiency = { { id = 89 }, { id = 90 }, { id = 91 } } }
-- The engine refuses the second row.
function createCharScreen:OnProficiencyPlusMinusButtonClick(id, plus)
    if id ~= 90 then chargen.extraProficiencySlots = chargen.extraProficiencySlots - 1 end
end
function createCharScreen:IsDoneButtonClickable() return chargen.extraProficiencySlots == 0 end
function createCharScreen:OnDoneButtonClick() calls[#calls + 1] = "done" end
function createCharScreen:GetCurrentStep() return 15 end
"""
        script = helper.render_step("01-assign-proficiencies", body)
        for engine in ENGINES:
            with self.subTest(engine=engine):
                lua = engine()
                lua.execute(FAKE_ENGINE + setup.replace("SLOTS", "3"))
                self.assertEqual(lua_list(lua.execute(script)["spent"]), [89, 89, 91])
                self.assertEqual(lua_list(lua.globals().calls), ["done"])
                lua = engine()
                lua.execute(FAKE_ENGINE + setup.replace("SLOTS", "5"))
                with self.assertRaisesRegex(Exception, "PROFICIENCY_SLOTS_LEFT"):
                    lua.execute(script)
                self.assertEqual(lua_list(lua.globals().calls), [])

    def test_pending_or_claimed_command_is_never_overwritten(self):
        for pending in ("eeex_remote_cmd.lua", "eeex_remote_cmd.lua.run"):
            calls = []
            game, evidence, invoke = self.run_fake(lambda *a, **k: calls.append(a), pending=pending)
            with self.assertRaisesRegex(helper.FixtureError, "pending"):
                invoke()
            self.assertEqual(calls, [])
            self.assertEqual((game / "override" / pending).read_text(), "already queued")
            self.assertEqual(json.loads((evidence / "status.json").read_text())["status"], "stopped")

    def test_timeout_stops_without_retry_and_preserves_pending_command(self):
        calls = []
        console = Console("eet")

        def run(argv, **kwargs):
            calls.append(argv)
            if len(calls) == 1:
                return console(argv, **kwargs)
            (game / "override" / "eeex_remote_cmd.lua").write_text("pending UI action")
            return subprocess.CompletedProcess(argv, 2, '{"status":"timeout"}', "")
        game, evidence, invoke = self.run_fake(run)
        with self.assertRaisesRegex(helper.FixtureError, "No retry"):
            invoke()
        self.assertEqual(len(calls), 2)
        self.assertEqual((game / "override" / "eeex_remote_cmd.lua").read_text(), "pending UI action")
        report = json.loads((evidence / "status.json").read_text())
        self.assertEqual(report["steps"][-1]["status"], "timeout")
        self.assertEqual(sorted(path.name for path in evidence.glob("*.lua")),
                         ["01-check.lua", "02-choose-campaign.lua"])

    def test_step_timeout_reaches_the_console_client(self):
        console = Console("eet")
        _, _, invoke = self.run_fake(console, timeout=60)
        invoke()
        probes = {(timeout, process) for step, timeout, process in console.calls if step in helper.PROBES}
        actions = {(timeout, process) for step, timeout, process in console.calls if step not in helper.PROBES}
        self.assertEqual(actions, {("60", 65)})
        self.assertEqual(probes, {(str(helper.RELOAD_TIMEOUT), helper.RELOAD_TIMEOUT + 5)})

    def test_client_timeout_is_recorded_without_another_call(self):
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            raise subprocess.TimeoutExpired(argv, 15)
        _, evidence, invoke = self.run_fake(run)
        with self.assertRaisesRegex(helper.FixtureError, "No retry"):
            invoke()
        self.assertEqual(len(calls), 1)
        detail = json.loads((evidence / "01-check.json").read_text())
        self.assertEqual(detail["status"], "client-timeout")

    def test_invalid_and_mismatched_responses_stop_the_sequence(self):
        for response in ('not json', '[]', '{"status":"ok","returnValues":[]}',
                         '{"status":"ok","returnValues":[{"step":"another-step"}]}',
                         '{"status":"error","error":"EXPECTED_MENU:START"}'):
            calls = []

            def run(argv, **kwargs):
                calls.append(argv)
                return subprocess.CompletedProcess(argv, 0, response, "")
            _, evidence, invoke = self.run_fake(run)
            with self.subTest(response=response), self.assertRaises(helper.FixtureError):
                invoke()
            self.assertEqual(len(calls), 1)
            self.assertEqual(json.loads((evidence / "status.json").read_text())["status"], "stopped")

    def test_remote_client_is_beside_the_main_checkout_from_any_worktree(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        main, worktree = root / "repository", root / "elsewhere" / "worktree"
        subprocess.run(["git", "init", "-q", str(main)], check=True)
        subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", "--orphan", "-b", "other",
                        str(worktree)], check=True)
        expected = root / "eeex-remote-console" / "tools" / "eeex-remote.ps1"
        self.assertEqual(helper.sibling_remote_client(main), expected)
        self.assertEqual(helper.sibling_remote_client(worktree), expected)


if __name__ == "__main__":
    unittest.main()
