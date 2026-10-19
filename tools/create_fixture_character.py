r"""Create a named test character in a disposable lab game, for any campaign start.

Start the lab game at its main menu first. This helper never launches the game.
Each UI action is sent once through the sibling EEex Remote Console client, with
separate Lua and JSON evidence. A timeout, error or unexpected menu stops the
sequence; nothing is retried. Waiting for a menu the engine reopens later sends
read-only probes until it is open, within a time limit. New Game and character
acceptance go through the UI adapter (FLPRUI_*) that CIR hooks into UI.MENU, as
the game's own buttons do.

Starts, each through New Game with a pregenerated character: BG1 and SOD on
BG:EE with Siege of Dragonspear, SOA and TOB on BG2:EE, all four on EET. The
game must be a lab below the user's Games folder whose engine.lua names a
private lab profile ("CIR Lab ..."). On 2.7.3 the lab also needs console polling
in the start and character-creation menus and the inert START label
FLPR_LAB_IDLE: a campaign choice, and EET's launch, finish only on the next
mouse event, which a click on that label provides (docs/native-save-render-crash.md,
"Lab games on 2.7.3").

Example:
    py tools/create_fixture_character.py --game "%USERPROFILE%\Games\<lab>\eet\game" --start SOD

Successful completion means the character was accepted, the campaign captured
and the game entered. With --finish-prologue (BG1 starts), the helper also
dismisses CHAPTER through its actual Done button and checks the world menu;
this requires a console polling widget in CHAPTER. Randomized item results
still need separate verification.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from uuid import uuid4


# Each platform's campaign starts and the one its main menu offers first.
STARTS = {"bgee": ("BG1", "SOD"), "bg2ee": ("SOA", "TOB"), "eet": ("BG1", "SOD", "SOA", "TOB")}
MAIN_START = {"bgee": "BG1", "bg2ee": "SOA", "eet": "BG1"}
# A pregenerated fighter each platform listed for every one of its starts in lab
# test 8 on 2.7.3 (BG2:EE's Throne of Bhaal list had no BG1 Abdel); --character
# names another.
CHARACTERS = {"bgee": "Abdel - Fighter - Chaotic Good", "bg2ee": "Abdel - Fighter - Neutral Good",
              "eet": "Abdel - Fighter - Chaotic Good"}
# The only games this helper drives: disposable labs, which use private profiles.
LAB_PROFILE_PREFIX = "CIR Lab "
ENGINE_NAME = re.compile(r'engine_name\s*=\s*"([^"\\]*)"\s*(?:--.*)?')
ENGINE_MODE = re.compile(r"engine_mode\s*=\s*([0-9]+)\s*(?:--.*)?")
MENUS = (
    "START", "START_MAIN", "START_SP", "START_CAMPAIGN_SELECT", "CHARGEN", "CHARGEN_IMPORT",
    "CHARGEN_PROFICIENCIES", "POPUP_TWOBUTTON", "POPUP_THREEBUTTON", "POPUP_INFO", "CHARACTER_SELECT",
    "CHARACTER_COLOR", "CHARGEN_CUSTOMSOUNDS", "CHARGEN_NAME", "CHARGEN_DIFFICULTY", "IMPORTPARTY",
    "LOAD", "SAVE", "SAVE_NEWSAVE", "CHAPTER", "WORLD_ACTIONBAR", "WORLD_DIALOG", "FLPR_RUN",
)


class FixtureError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def lua_string(value: str) -> str:
    """Encode UTF-8 as fixed-width Lua decimal byte escapes, never Lua code."""
    return '"' + "".join(f"\\{byte:03d}" for byte in value.encode("utf-8")) + '"'


def validate_name(name: str) -> None:
    # Matches the actual CHARGEN_NAME edit's 20-character limit.
    if not name.strip() or len(name) > 20 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise FixtureError("Test name must contain 1-20 visible characters.")


def validate_character(character: str) -> None:
    if not character.strip() or len(character) > 80 or any(ord(c) < 32 or ord(c) == 127 for c in character):
        raise FixtureError("The pregenerated character's name must contain 1-80 visible characters.")


def validate_start(platform: str, start: str, finish_prologue: bool = False) -> None:
    if start not in STARTS[platform]:
        raise FixtureError(f"{platform} has no {start} start; it has {', '.join(STARTS[platform])}.")
    if finish_prologue and start != "BG1":
        raise FixtureError("--finish-prologue dismisses the chapter screen that BG1 starts open with; "
                           "it is for BG1 starts only.")


def games_folder() -> Path:
    return Path.home() / "Games"


def read_profile(game: Path) -> tuple[str, int]:
    """The profile name and engine mode of the game's engine.lua, which holds nothing else."""
    names, modes = [], []
    for line in (game / "engine.lua").read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("--"):
            continue
        if match := ENGINE_NAME.fullmatch(line):
            names.append(match[1])
        elif match := ENGINE_MODE.fullmatch(line):
            modes.append(int(match[1]))
        else:
            raise FixtureError("engine.lua holds more than a profile name and an engine mode.")
    if len(names) != 1 or len(modes) != 1:
        raise FixtureError("engine.lua must name exactly one profile and one engine mode.")
    return names[0], modes[0]


def detect_platform(game: Path, engine_mode: int) -> str:
    """EET marks its override with EET.flag (any case); BG:EE runs engine mode 0, BG2:EE 1."""
    override = game / "override"
    eet = override.is_dir() and any(path.name.lower() == "eet.flag" for path in override.iterdir())
    if engine_mode == 1:
        return "eet" if eet else "bg2ee"
    if engine_mode == 0:
        if eet:
            raise FixtureError("override/EET.flag marks EET, but engine mode 0 is BG:EE's.")
        return "bgee"
    raise FixtureError(f"Engine mode {engine_mode} is not a Baldur's Gate game this helper drives.")


def validate_game(game: Path) -> tuple[Path, str]:
    resolved = game.resolve(strict=True)
    games = games_folder().resolve()
    if resolved == games or not resolved.is_relative_to(games):
        raise FixtureError("This helper is limited to the disposable lab games below the user's Games folder.")
    profile, engine_mode = read_profile(resolved)
    if not profile.startswith(LAB_PROFILE_PREFIX):
        raise FixtureError(f"The game does not use a private lab profile ({LAB_PROFILE_PREFIX}...).")
    platform = detect_platform(resolved, engine_mode)
    for relative in ("override/M_FLPR.lua", "override/M_EEexRC.lua", "randomiser-campaigns/next.lua"):
        if not (resolved / relative).is_file():
            raise FixtureError(f"Missing fixture support: {relative}")
    return resolved, platform


def require_idle_ipc(game: Path) -> None:
    for name in ("eeex_remote_cmd.lua", "eeex_remote_cmd.lua.run"):
        if (game / "override" / name).exists():
            raise FixtureError(f"An EEex console command is already pending: {name}. Nothing was overwritten.")


PRELUDE = """local function is_true(value) return value ~= nil and value ~= false and value ~= 0 end
local function on(name) return is_true(Infinity_IsMenuOnStack(name)) end
local function need(name) assert(on(name), "EXPECTED_MENU:" .. name) end
local function global(name) return EEex_GameState_GetGlobalInt(name) end
local function intent()
    local i = FLPR and FLPR.NewGameIntent
    return i and { campaign = i.campaign, revision = i.revision, started = i.started, chargen = i.chargen } or nil
end
-- CIR's popup when it cannot randomise this start (prototype/M_FLPRUI.lua). It
-- stays open for the person to read; the helper stops.
local function cir_popup()
    if on("POPUP_TWOBUTTON") and type(Popup2Button) == "table" and Popup2Button.info == "FLPR_POPUP" then
        return tostring(type(uiStrings) == "table" and uiStrings.FLPR_POPUP or "FLPR_POPUP")
    end
end
-- 2.7.3 finishes a campaign choice, and EET its launch, only on the next mouse
-- event; a click on the lab's inert START label provides it.
local function idle_click()
    Infinity_ClickItem(assert(nameToItem and nameToItem.FLPR_LAB_IDLE, "IDLE_LABEL_NOT_STAGED"))
end"""

CHECK = """assert(not on("WORLD_ACTIONBAR"), "REFUSE_ACTIVE_WORLD")
for _, menu in ipairs({"START_SP", "CHARGEN", "CHARACTER_SELECT", "CHARGEN_DIFFICULTY", "IMPORTPARTY",
    "CHAPTER", "LOAD", "SAVE", "POPUP_TWOBUTTON", "POPUP_THREEBUTTON", "POPUP_INFO"}) do
    assert(not on(menu), "REFUSE_EXISTING_FLOW:" .. menu)
end
assert(FLPR and type(FLPR.NewGame) == "function" and type(FLPR.AcceptCharacter) == "function",
    "FIXTURE_UI_NOT_INSTALLED")
assert(type(FLPRUI_NewGame) == "function" and type(FLPRUI_AcceptCharacter) == "function",
    "UI_ADAPTER_NOT_INSTALLED")
assert(not FLPR.Disabled, tostring(FLPR.Disabled))"""

# EET 2.7.3 opens START after a launch only on the first mouse event.
WAKE = """local was_open = on("START")
if not was_open then idle_click() end
return { was_open = was_open }"""

# BG:EE's campaign popup at a profile's first launch; its BG button only dismisses it.
DISMISS_CAMPAIGN_SELECT = """need("START_CAMPAIGN_SELECT")
startEngine:DismissChooseCampaignPopup()"""

# START_MAIN's Back button: from a chosen BG2:EE campaign back to the logos.
BACK_TO_LOGOS = """need("START_MAIN")
e:GetObjectGame():SetCampaign("")
startEngine:SetEngineState(3)
Infinity_PopMenu("START_MAIN")"""

# Read-only probes for a menu the engine opens later; each answers whether it is open.
PROBES = {"await-start": "START", "await-chapter": "CHAPTER"}


def choose_campaign(platform: str, start: str) -> str:
    """The start's campaign button, after the idle click its reload needs; EET and BG:EE
    switch only away from another campaign, BG2:EE always picks a logo."""
    if platform == "eet":
        return f"""need("START")
local index
for row, data in ipairs(startCampaignData) do
    if data.id == "{start}" then
        assert(index == nil, "AMBIGUOUS_CAMPAIGN")
        index = row
    end
end
assert(index, "CAMPAIGN_NOT_FOUND")
local switched = currentCampaign ~= index
if switched then
    idle_click()
    onCampaignButton(index)
end
return {{ switched = switched }}"""
    if platform == "bgee":
        wanted, choose = (("START_CAMPAIGN_BG", "startEngine:OnSoAButtonClick(true)") if start == "BG1" else
                          ("START_CAMPAIGN_SOD",
                           'resetSodLanguageIfNeeded()\n    startEngine:OnCampaignButtonClick("SOD", true)'))
        return f"""need("START")
assert(const.{wanted} ~= nil, "CAMPAIGN_NOT_FOUND")
local switched = startEngine:GetCampaign() ~= const.{wanted}
if switched then
    idle_click()
    {choose}
end
return {{ switched = switched }}"""
    frame, button = (0, "OnSoAButtonClick") if start == "SOA" else (1, "OnToBButtonClick")
    return f"""need("START")
assert(not on("START_MAIN"), "START_MAIN_ALREADY_OPEN")
logoFrame = {frame}
idle_click()
startEngine:{button}(true)
Infinity_PushMenu("START_MAIN")
return {{ switched = true }}"""


def step_bodies(platform: str, start: str, name: str, character: str) -> dict[str, str]:
    validate_name(name)
    validate_character(character)
    validate_start(platform, start)
    started = f"""local started = intent()
assert(started and started.campaign == "{start}", "UNEXPECTED_START:" .. tostring(started and started.campaign))"""
    return {
        "check": CHECK,
        "wake": WAKE,
        **{probe: f'return {{ ready = on("{menu}") }}' for probe, menu in PROBES.items()},
        "dismiss-campaign-select": DISMISS_CAMPAIGN_SELECT,
        "back-to-logos": BACK_TO_LOGOS,
        "choose-campaign": choose_campaign(platform, start),
        "single-player": f"""need("{'START_MAIN' if platform == 'bg2ee' else 'START'}")
startEngine:SetEngineState(1)
Infinity_TransitionMenu("START_SP")""",
        "new-game": f"""need("START_SP")
Infinity_PopMenu("START_SP")
FLPRUI_NewGame()
local refused = cir_popup()
assert(not refused, "CIR_REFUSED_START:" .. tostring(refused))
need("CHARGEN")
{started}
return {{ intent = started, mode = createCharScreen:GetEngineState(), fresh = global("FLPR_FRESH"),
    state = global("flpr_state") }}""",
        # The popup has two buttons on BG:EE and three on BG2:EE and EET; one is the character file.
        "open-import": """need("CHARGEN")
createCharScreen:OnImportCharacterButtonClick()
local three = on("POPUP_THREEBUTTON") and type(Popup3Button) == "table" and
    Popup3Button.midText == "CHARACTER_FILE_BUTTON"
local two = on("POPUP_TWOBUTTON") and type(Popup2Button) == "table" and Popup2Button.okText == "CHARACTER_FILE_BUTTON"
assert(three ~= two, "UNEXPECTED_IMPORT_POPUP")
return { buttons = three and 3 or 2 }""",
        "choose-file": """local choose, popup
if on("POPUP_THREEBUTTON") and type(Popup3Button) == "table" and Popup3Button.midText == "CHARACTER_FILE_BUTTON" then
    choose, popup = Popup3Button.midFunc, "POPUP_THREEBUTTON"
elseif on("POPUP_TWOBUTTON") and type(Popup2Button) == "table" and Popup2Button.okText == "CHARACTER_FILE_BUTTON" then
    choose, popup = Popup2Button.okFunc, "POPUP_TWOBUTTON"
end
assert(type(choose) == "function", "MISSING_CHARACTER_FILE_ACTION")
Infinity_PopMenu(popup)
choose()
need("CHARACTER_SELECT")
local rows = {}
for _, entry in ipairs(character_select or {}) do rows[#rows + 1] = entry.name end
return { rows = rows }""",
        "pick-character": f"""need("CHARACTER_SELECT")
local row
for index, entry in ipairs(character_select) do
    if entry.name == {lua_string(character)} then
        assert(row == nil, "AMBIGUOUS_PREGENERATED_CHARACTER")
        row = index
    end
end
assert(row, "PREGENERATED_CHARACTER_NOT_FOUND")
selectedCharacter = row
Infinity_OnCharacterImportItemSelect(row - 1)
createCharScreen:OnDoneButtonClick()
Infinity_PopMenu("CHARACTER_SELECT")
need("CHARGEN")
-- The appearance (15) follows, or first the proficiencies (9) of a character
-- the start levels up (BG2:EE's Throne of Bhaal).
return {{ chargen_step = createCharScreen:GetCurrentStep() }}""",
        "open-proficiencies": """need("CHARGEN")
assert(createCharScreen:GetCurrentStep() == 9, "PROFICIENCY_STEP_REQUIRED")
createCharScreen:OnMenuButtonClick()
need("CHARGEN_PROFICIENCIES")
return { slots = chargen.extraProficiencySlots }""",
        # Spends the free slots in list order, at most two per row, as a player pressing
        # '+' does; a press the engine refuses moves on to the next row.
        "assign-proficiencies": """need("CHARGEN_PROFICIENCIES")
local spent = {}
for _, row in ipairs(chargen.proficiency) do
    for _ = 1, 2 do
        local before = chargen.extraProficiencySlots
        if before == 0 then break end
        createCharScreen:OnProficiencyPlusMinusButtonClick(row.id, true)
        if chargen.extraProficiencySlots >= before then break end
        spent[#spent + 1] = row.id
    end
end
assert(chargen.extraProficiencySlots == 0, "PROFICIENCY_SLOTS_LEFT")
assert(is_true(createCharScreen:IsDoneButtonClickable()), "PROFICIENCIES_NOT_COMPLETE")
createCharScreen:OnDoneButtonClick()
return { spent = spent, chargen_step = createCharScreen:GetCurrentStep() }""",
        "open-appearance": """need("CHARGEN")
assert(createCharScreen:GetCurrentStep() == 15, "APPEARANCE_STEP_REQUIRED")
createCharScreen:OnMenuButtonClick()
need("CHARACTER_COLOR")""",
        "accept-appearance": """need("CHARACTER_COLOR")
createCharScreen:OnDoneButtonClick()
Infinity_PopMenu("CHARACTER_COLOR")
need("CHARGEN_CUSTOMSOUNDS")""",
        "accept-sounds": """need("CHARGEN_CUSTOMSOUNDS")
assert(is_true(createCharScreen:IsDoneButtonClickable()), "SOUND_DONE_NOT_CLICKABLE")
Infinity_PopMenu("CHARGEN_CUSTOMSOUNDS")
createCharScreen:OnDoneButtonClick()
need("CHARGEN")
assert(createCharScreen:GetCurrentStep() == 17, "NAME_STEP_REQUIRED")""",
        "open-name": """need("CHARGEN")
assert(createCharScreen:GetCurrentStep() == 17, "NAME_STEP_REQUIRED")
createCharScreen:OnMenuButtonClick()
need("CHARGEN_NAME")""",
        "set-name": f"""need("CHARGEN_NAME")
charNameEdit = {lua_string(name)}
assert(is_true(createCharScreen:IsDoneButtonClickable()), "NAME_DONE_NOT_CLICKABLE")
Infinity_PopMenu("CHARGEN_NAME")
createCharScreen:OnDoneButtonClick()
need("CHARGEN")
assert(is_true(createCharScreen:IsChargenComplete()), "CHARACTER_NOT_COMPLETE")""",
        "capture": f"""need("CHARGEN")
assert(is_true(createCharScreen:IsChargenComplete()), "CHARACTER_NOT_COMPLETE")
{started}
assert(global("FLPR_FRESH") == 1, "FRESH_TEMPLATE_REQUIRED")
assert(global("flpr_state") == 0, "UNEXPECTED_CAMPAIGN_STATE")
FLPRUI_AcceptCharacter(-1)
local refused = cir_popup()
assert(not refused, "CIR_REFUSED_CAPTURE:" .. tostring(refused))
need("CHARGEN_DIFFICULTY")
assert(global("FLPR_FRESH") == 0, "FRESH_MARKER_NOT_CONSUMED")
assert(global("flpr_state") == 2, "CAMPAIGN_NOT_COMMITTED")
return {{ revision = started.revision, seed = global("FLPR_SEED") }}""",
        "enter": """need("CHARGEN_DIFFICULTY")
assert(global("FLPR_FRESH") == 0, "FRESH_MARKER_NOT_CONSUMED")
assert(global("flpr_state") == 2, "CAMPAIGN_NOT_COMMITTED")
Infinity_PopMenu("CHARGEN_DIFFICULTY")
FLPRUI_AcceptCharacter(3)
-- What follows (BG1's chapter screen, a cutscene, a conversation) may poll
-- nothing. Do not claim world or loot readiness here.""",
        "finish-prologue": """need("CHAPTER")
assert(is_true(chapterScreen:IsDoneButtonClickable()), "CHAPTER_DONE_NOT_CLICKABLE")
chapterScreen:OnDoneButtonClick()""",
        "check-world-menu": """need("WORLD_ACTIONBAR")
assert(not on("CHAPTER"), "CHAPTER_STILL_OPEN")
assert(global("flpr_state") == 2, "CAMPAIGN_NOT_COMMITTED")""",
    }


CREATION = ["single-player", "new-game", "open-import", "choose-file", "pick-character", "open-appearance",
            "accept-appearance", "accept-sounds", "open-name", "set-name", "capture", "enter"]
PROLOGUE = ["await-chapter", "finish-prologue", "check-world-menu"]


def planned_steps(finish_prologue: bool = False) -> list[str]:
    """The fixed course; follow_up() inserts what the game's answers call for."""
    return ["check", "choose-campaign", *CREATION, *(PROLOGUE if finish_prologue else [])]


def open_menus(value: dict) -> set[str]:
    menus = value.get("open")
    # The console writes an empty Lua table as an empty JSON object or array.
    if menus in ({}, None):
        return set()
    if not isinstance(menus, list) or not all(isinstance(menu, str) for menu in menus):
        raise FixtureError(f"{value.get('step')}: the response did not list the open menus.")
    return set(menus)


def follow_up(platform: str, step: str, value: dict) -> list[str]:
    """Steps to insert next, from what the game answered to STEP."""
    opened = open_menus(value)
    if step == "check":
        allowed = {"START", *{"bgee": ("START_CAMPAIGN_SELECT",), "bg2ee": ("START_MAIN",)}.get(platform, ())}
        unexpected = sorted(opened - allowed)
        if unexpected:
            raise FixtureError(f"{value['step']}: unexpected menus at the main menu: {', '.join(unexpected)}.")
        if "START" not in opened:
            if platform != "eet" or opened:
                raise FixtureError(f"{value['step']}: the main menu (START) is not open.")
            return ["wake", "await-start"]
        if "START_CAMPAIGN_SELECT" in opened:
            return ["dismiss-campaign-select"]
        if "START_MAIN" in opened:
            return ["back-to-logos"]
    elif step == "choose-campaign" and "START" not in opened:
        # The choice reloads the campaign and closes START until the reload is done.
        return ["await-start"]
    elif step == "pick-character":
        chargen_step = value.get("chargen_step")
        if chargen_step == 9:
            return ["open-proficiencies", "assign-proficiencies"]
        if chargen_step != 15:
            raise FixtureError(f"{value['step']}: character creation went on to step {chargen_step}, "
                               "not the appearance (15) or the proficiencies (9).")
    return []


def render_step(label: str, body: str) -> str:
    menu_values = ", ".join(lua_string(menu) for menu in MENUS)
    return f"""-- Fixture character helper: {label}
{PRELUDE}
local result = (function()
{body.strip()}
end)() or {{}}
result.step = {lua_string(label)}
result.screen = Infinity_GetCurrentScreenName()
result.open = {{}}
for _, name in ipairs({{{menu_values}}}) do
    if on(name) then result.open[#result.open + 1] = name end
end
return result
"""


# A probe after a campaign switch waits until a polled menu renders again, so it
# may take as long as the reload; probes stop after AWAIT_SECONDS altogether.
RELOAD_TIMEOUT = 120
AWAIT_SECONDS = 150
PROBE_PAUSE = 1


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sibling_remote_client(repository: Path = Path(__file__).resolve().parents[1]) -> Path:
    """The EEex Remote Console client in the checkout beside this repository.

    Worktrees resolve to their main checkout, so every worktree uses the same client.
    """
    common = subprocess.run(["git", "-C", str(repository), "rev-parse", "--git-common-dir"],
                            capture_output=True, text=True)
    checkout = (repository / common.stdout.strip()).resolve().parent if common.returncode == 0 else repository
    return checkout.parent / "eeex-remote-console" / "tools" / "eeex-remote.ps1"


def drive(game: Path, name: str, evidence: Path, powershell: str, *, platform: str, start: str | None = None,
          character: str | None = None, timeout: int = 30, client: Path | None = None,
          finish_prologue: bool = False, run=subprocess.run, clock=time.monotonic, pause=time.sleep) -> dict:
    """Submit each step once. `run`, `clock` and `pause` are injectable for offline tests."""
    start = MAIN_START[platform] if start is None else start
    character = CHARACTERS[platform] if character is None else character
    client = client or sibling_remote_client()
    evidence.mkdir(parents=True, exist_ok=False)
    report = {"schema": "flpr-character-creation-v2", "game": str(game), "platform": platform, "start": start,
              "name": name, "character": character, "started_at": utc_now(), "status": "running", "steps": []}
    report_path = evidence / "status.json"
    write_json(report_path, report)
    sent = 0

    def send(step_name: str, body: str, step_timeout: int) -> dict:
        nonlocal sent
        sent += 1
        label = f"{sent:02d}-{step_name}"
        require_idle_ipc(game)
        script = evidence / f"{label}.lua"
        script.write_text(render_step(label, body), encoding="ascii")
        step = {"step": label, "started_at": utc_now(), "status": "submitted"}
        report["steps"].append(step)
        write_json(report_path, report)
        try:
            result = run([powershell, "-NoProfile", "-NonInteractive", "-File", str(client),
                          str(game / "override"), "@" + str(script), str(step_timeout)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=step_timeout + 5, cwd=str(evidence))
        except subprocess.TimeoutExpired:
            step["status"] = "client-timeout"
            write_json(evidence / f"{label}.json", step)
            raise FixtureError(f"{label}: console client timed out. No retry was sent; any pending command was preserved.")
        step.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr,
                    finished_at=utc_now())
        try:
            response = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError):
            step["status"] = "invalid-response"
            write_json(evidence / f"{label}.json", step)
            raise FixtureError(f"{label}: console did not return valid JSON. No further actions were sent.")
        step["response"] = response
        if not isinstance(response, dict):
            step["status"] = "invalid-response"
            write_json(evidence / f"{label}.json", step)
            raise FixtureError(f"{label}: console response was not an object. No further actions were sent.")
        step["status"] = response.get("status", "invalid-response")
        write_json(evidence / f"{label}.json", step)
        if result.returncode != 0 or step["status"] != "ok":
            raise FixtureError(f"{label}: console status {step['status']}. No retry or later action was sent.")
        values = response.get("returnValues")
        value = values[0] if isinstance(values, list) and values else None
        if not isinstance(value, dict) or value.get("step") != label:
            step["status"] = "invalid-step-result"
            write_json(evidence / f"{label}.json", step)
            raise FixtureError(f"{label}: response did not identify this UI step. No further actions were sent.")
        write_json(report_path, report)
        print(label, flush=True)
        return value

    try:
        validate_start(platform, start, finish_prologue)
        bodies = step_bodies(platform, start, name, character)
        steps = planned_steps(finish_prologue)
        while steps:
            step_name = steps.pop(0)
            if step_name in PROBES:
                # Read-only: asking again whether the menu is open repeats no action.
                deadline = clock() + AWAIT_SECONDS
                while True:
                    value = send(step_name, bodies[step_name], RELOAD_TIMEOUT)
                    if value.get("ready") is True:
                        break
                    if clock() >= deadline:
                        raise FixtureError(f"{value['step']}: {PROBES[step_name]} did not open within "
                                           f"{AWAIT_SECONDS} seconds. No further actions were sent.")
                    pause(PROBE_PAUSE)
            else:
                value = send(step_name, bodies[step_name], timeout)
            steps[0:0] = follow_up(platform, step_name, value)
        report["status"] = "awaiting-world-confirmation"
        report["note"] = ("Character created and the game entered. The start's opening and the randomized items "
                          "still need checking.")
        if finish_prologue:
            report["status"] = "awaiting-loot-confirmation"
            report["note"] = "Character created and world menu reached. Randomized loot still needs separate verification."
    except (FixtureError, OSError) as exc:
        report["status"] = "stopped"
        report["error"] = str(exc)
        raise
    finally:
        report["finished_at"] = utc_now()
        write_json(report_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game", type=Path, required=True, help="The lab's game folder.")
    parser.add_argument("--start", type=str.upper, choices=("BG1", "SOD", "SOA", "TOB"),
                        help="Campaign start; default BG1, on BG2:EE SOA.")
    parser.add_argument("--name", default="FLPR QA", help="Test character name (1-20 characters).")
    parser.add_argument("--character", help="Pregenerated character to import; default the platform's Abdel.")
    parser.add_argument("--evidence", type=Path, help="New evidence directory outside the game.")
    parser.add_argument("--finish-prologue", action="store_true",
                        help="BG1 starts: dismiss CHAPTER once; requires its console polling widget.")
    parser.add_argument("--timeout", type=int, default=30, choices=range(5, 121), metavar="SECONDS",
                        help="Seconds each UI step may take (5-120). New Game loads for longer on a busy PC.")
    args = parser.parse_args(argv)
    lock = None
    try:
        validate_name(args.name)
        game, platform = validate_game(args.game)
        start = MAIN_START[platform] if args.start is None else args.start
        validate_start(platform, start, args.finish_prologue)
        character = CHARACTERS[platform] if args.character is None else args.character
        validate_character(character)
        require_idle_ipc(game)
        client = sibling_remote_client()
        if not client.is_file():
            raise FixtureError(f"Remote console client is missing: {client}")
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if not powershell:
            raise FixtureError("PowerShell is required for the sibling EEex Remote Console client.")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
        evidence = (args.evidence or Path(__file__).resolve().parents[1] / "work" / "mode1-native-slice" /
                    (f"character-{platform}-{start.lower()}-" + stamp + uuid4().hex[:8])).resolve()
        if evidence == game or evidence.is_relative_to(game):
            raise FixtureError("Evidence must be outside the game folder.")
        lock_name = "flpr-character-" + hashlib.sha256(str(game).lower().encode()).hexdigest()[:20] + ".lock"
        candidate = Path(tempfile.gettempdir()) / lock_name
        try:
            with candidate.open("x", encoding="utf-8") as stream:
                stream.write(str(evidence) + "\n")
            lock = candidate
        except FileExistsError:
            raise FixtureError(f"Another helper may be active. Inspect its lock before retrying: {candidate}")
        report = drive(game, args.name, evidence, powershell, platform=platform, start=start, character=character,
                       client=client, finish_prologue=args.finish_prologue, timeout=args.timeout)
        print(report["note"])
        print(f"Evidence: {evidence}")
        return 0
    except (FixtureError, OSError) as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 1
    finally:
        if lock is not None:
            lock.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
