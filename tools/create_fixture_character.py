"""Create a named test character through the observed isolated EET UI flow.

Start a disposable lab game at its main menu first. This helper never
launches the game. Each UI action is sent once through
the sibling EEex Remote Console client, with separate Lua and JSON evidence.
A timeout or unexpected menu stops the sequence; it is never retried. New Game
and character acceptance go through the UI adapter (FLPRUI_*) that CIR hooks
into UI.MENU, as the game's own buttons do.

Example:
    py tools/create_fixture_character.py --name "FLPR Run 2"

Successful completion means character creation was submitted. With
--finish-prologue, the helper also dismisses CHAPTER through its actual Done
button and checks the world menu; this requires a console polling widget in
CHAPTER. Randomized item results still need separate verification.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from uuid import uuid4


# The only games this helper drives: disposable labs and their private profile names.
LABS = {
    Path.home() / "Games" / "IR-Lab-266-20261017" / "game": "IR Lab 266 - 20261017",
    Path.home() / "Games" / "IR-Lab-266-20261014" / "game": "IR Lab 266 - 20261014",
}
CHARACTER_NAME = "Abdel - Fighter - Chaotic Good"
MENUS = (
    "START", "START_SP", "START_CAMPAIGN_SELECT", "WORLD_ACTIONBAR",
    "CHARGEN", "CHARGEN_IMPORT", "POPUP_THREEBUTTON", "CHARACTER_SELECT",
    "CHARACTER_COLOR", "CHARGEN_CUSTOMSOUNDS", "CHARGEN_NAME",
    "CHARGEN_DIFFICULTY", "IMPORTPARTY", "LOAD", "SAVE", "SAVE_NEWSAVE",
    "CHAPTER", "FLPR_RUN", "EEEX_REMOTE",
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
        raise FixtureError("Test name must contain 1–20 visible characters.")


def profile_text(name: str) -> str:
    return f'engine_name = "{name}"\nengine_mode = 1\n'


def validate_game(game: Path) -> Path:
    resolved = game.resolve(strict=True)
    labs = {lab.resolve(): name for lab, name in LABS.items() if lab.exists()}
    if resolved not in labs:
        raise FixtureError("This helper is limited to the disposable lab games.")
    if (resolved / "engine.lua").read_text(encoding="utf-8") != profile_text(labs[resolved]):
        raise FixtureError("The game does not use the expected private test profile.")
    for relative in ("override/M_FLPR.lua", "override/M_EEexRC.lua", "randomiser-campaigns/next.lua"):
        if not (resolved / relative).is_file():
            raise FixtureError(f"Missing fixture support: {relative}")
    return resolved


def require_idle_ipc(game: Path) -> None:
    for name in ("eeex_remote_cmd.lua", "eeex_remote_cmd.lua.run"):
        if (game / "override" / name).exists():
            raise FixtureError(f"An EEex console command is already pending: {name}. Nothing was overwritten.")


def build_steps(name: str, finish_prologue: bool = False) -> list[tuple[str, str]]:
    validate_name(name)
    steps = [
        ("01-check-main-menu", """
need_menu("START")
assert(not has_menu("WORLD_ACTIONBAR"), "REFUSE_ACTIVE_WORLD")
for _, menu in ipairs({"CHARGEN", "CHARGEN_DIFFICULTY", "IMPORTPARTY", "LOAD", "SAVE"}) do
    assert(not has_menu(menu), "REFUSE_EXISTING_FLOW:" .. menu)
end
assert(FLPR and type(FLPR.NewGame) == "function" and type(FLPR.AcceptCharacter) == "function",
    "FIXTURE_UI_NOT_INSTALLED")
assert(type(FLPRUI_NewGame) == "function" and type(FLPRUI_AcceptCharacter) == "function",
    "UI_ADAPTER_NOT_INSTALLED")
assert(not FLPR.Disabled, tostring(FLPR.Disabled))
"""),
        # Switching the campaign reloads the menus, and the console with them,
        # so the helper switches only when BG1 is not active already.
        ("02-select-bg1", """
need_menu("START")
assert(not has_menu("WORLD_ACTIONBAR"), "REFUSE_ACTIVE_WORLD")
local campaign_switched = false
if startCampaignData[currentCampaign].id ~= "BG1" then
    local bg1
    for index, campaign in ipairs(startCampaignData) do
        if campaign.id == "BG1" then
            assert(bg1 == nil, "AMBIGUOUS_BG1_CAMPAIGN")
            bg1 = index
        end
    end
    assert(bg1, "BG1_CAMPAIGN_NOT_FOUND")
    onCampaignButton(bg1)
    campaign_switched = true
end
assert(startCampaignData[currentCampaign].id == "BG1", "BG1_CAMPAIGN_REQUIRED")
"""),
        ("03-start-new-game", """
assert(has_menu("START") or has_menu("START_SP"), "MAIN_MENU_REQUIRED")
assert(startCampaignData[currentCampaign].id == "BG1", "BG1_CAMPAIGN_REQUIRED")
if has_menu("START_SP") then Infinity_PopMenu("START_SP") end
FLPRUI_NewGame()
need_menu("CHARGEN")
assert(EEex_GameState_GetGlobalInt("FLPR_FRESH") == 1, "FRESH_TEMPLATE_REQUIRED")
assert(EEex_GameState_GetGlobalInt("flpr_state") == 0, "UNEXPECTED_CAMPAIGN_STATE")
"""),
        ("04-open-import", """
need_menu("CHARGEN")
createCharScreen:OnImportCharacterButtonClick()
need_menu("POPUP_THREEBUTTON")
assert(Popup3Button.midText == "CHARACTER_FILE_BUTTON", "UNEXPECTED_IMPORT_POPUP")
"""),
        ("05-choose-character-file", """
need_menu("POPUP_THREEBUTTON")
assert(Popup3Button.midText == "CHARACTER_FILE_BUTTON", "UNEXPECTED_IMPORT_POPUP")
local choose_file = Popup3Button.midFunc
assert(type(choose_file) == "function", "MISSING_CHARACTER_FILE_ACTION")
Infinity_PopMenu("POPUP_THREEBUTTON")
choose_file()
need_menu("CHARACTER_SELECT")
"""),
        ("06-import-fighter", f"""
need_menu("CHARACTER_SELECT")
local row
for index, entry in ipairs(character_select) do
    if entry.name == {lua_string(CHARACTER_NAME)} then
        assert(row == nil, "AMBIGUOUS_PREGENERATED_CHARACTER")
        row = index
    end
end
assert(row, "PREGENERATED_CHARACTER_NOT_FOUND")
selectedCharacter = row
Infinity_OnCharacterImportItemSelect(row - 1)
createCharScreen:OnDoneButtonClick()
Infinity_PopMenu("CHARACTER_SELECT")
need_menu("CHARGEN")
assert(createCharScreen:GetCurrentStep() == 15, "UNEXPECTED_POST_IMPORT_STEP")
"""),
        ("07-open-appearance", """
need_menu("CHARGEN")
assert(createCharScreen:GetCurrentStep() == 15, "APPEARANCE_STEP_REQUIRED")
createCharScreen:OnMenuButtonClick()
need_menu("CHARACTER_COLOR")
"""),
        ("08-accept-appearance", """
need_menu("CHARACTER_COLOR")
createCharScreen:OnDoneButtonClick()
Infinity_PopMenu("CHARACTER_COLOR")
need_menu("CHARGEN_CUSTOMSOUNDS")
"""),
        ("09-accept-sounds", """
need_menu("CHARGEN_CUSTOMSOUNDS")
assert(is_true(createCharScreen:IsDoneButtonClickable()), "SOUND_DONE_NOT_CLICKABLE")
Infinity_PopMenu("CHARGEN_CUSTOMSOUNDS")
createCharScreen:OnDoneButtonClick()
need_menu("CHARGEN")
assert(createCharScreen:GetCurrentStep() == 17, "NAME_STEP_REQUIRED")
"""),
        ("10-open-name", """
need_menu("CHARGEN")
assert(createCharScreen:GetCurrentStep() == 17, "NAME_STEP_REQUIRED")
createCharScreen:OnMenuButtonClick()
need_menu("CHARGEN_NAME")
"""),
        ("11-set-name", f"""
need_menu("CHARGEN_NAME")
charNameEdit = {lua_string(name)}
assert(is_true(createCharScreen:IsDoneButtonClickable()), "NAME_DONE_NOT_CLICKABLE")
Infinity_PopMenu("CHARGEN_NAME")
createCharScreen:OnDoneButtonClick()
need_menu("CHARGEN")
assert(is_true(createCharScreen:IsChargenComplete()), "CHARACTER_NOT_COMPLETE")
"""),
        ("12-capture-campaign", """
need_menu("CHARGEN")
assert(is_true(createCharScreen:IsChargenComplete()), "CHARACTER_NOT_COMPLETE")
assert(FLPR.NewGameIntent, "NEW_GAME_INTENT_REQUIRED")
assert(EEex_GameState_GetGlobalInt("FLPR_FRESH") == 1, "FRESH_TEMPLATE_REQUIRED")
assert(EEex_GameState_GetGlobalInt("flpr_state") == 0, "UNEXPECTED_CAMPAIGN_STATE")
FLPRUI_AcceptCharacter(-1)
need_menu("CHARGEN_DIFFICULTY")
assert(EEex_GameState_GetGlobalInt("FLPR_FRESH") == 0, "FRESH_MARKER_NOT_CONSUMED")
assert(EEex_GameState_GetGlobalInt("flpr_state") == 2, "CAMPAIGN_NOT_COMMITTED")
"""),
        ("13-enter-game", """
need_menu("CHARGEN_DIFFICULTY")
assert(EEex_GameState_GetGlobalInt("FLPR_FRESH") == 0, "FRESH_MARKER_NOT_CONSUMED")
assert(EEex_GameState_GetGlobalInt("flpr_state") == 2, "CAMPAIGN_NOT_COMMITTED")
Infinity_PopMenu("CHARGEN_DIFFICULTY")
FLPRUI_AcceptCharacter(3)
-- The engine may next show the prologue without a polling widget. Do not
-- issue another console command or claim world/loot readiness here.
"""),
    ]
    if finish_prologue:
        steps.extend([
            ("14-finish-prologue", """
need_menu("CHAPTER")
assert(is_true(chapterScreen:IsDoneButtonClickable()), "CHAPTER_DONE_NOT_CLICKABLE")
chapterScreen:OnDoneButtonClick()
"""),
            ("15-check-world-menu", """
need_menu("WORLD_ACTIONBAR")
assert(not has_menu("CHAPTER"), "CHAPTER_STILL_OPEN")
assert(EEex_GameState_GetGlobalInt("flpr_state") == 2, "CAMPAIGN_NOT_COMMITTED")
"""),
        ])
    return steps


def render_step(label: str, body: str) -> str:
    menu_values = ", ".join(lua_string(menu) for menu in MENUS)
    return f"""-- Fixture character helper: {label}
local function is_true(value) return value ~= nil and value ~= false and value ~= 0 end
local function has_menu(name) return is_true(Infinity_IsMenuOnStack(name)) end
local function need_menu(name) assert(has_menu(name), "EXPECTED_MENU:" .. name) end
{body.strip()}
local screens = {{}}
for _, menu in ipairs({{{menu_values}}}) do
    if has_menu(menu) then screens[#screens + 1] = menu end
end
return {{ step = {lua_string(label)}, screens = screens,
    screen = Infinity_GetCurrentScreenName(), campaign_switched = campaign_switched,
    fixture = FLPR and FLPR.Inspect and FLPR.Inspect() or nil }}
"""


# After a campaign switch the main menu, and the console's polling with it,
# returns only once the engine has reloaded the campaign: one command waits
# for that, longer than an ordinary step.
RELOADED = ("02b-await-reloaded-menu", """
need_menu("START")
assert(startCampaignData[currentCampaign].id == "BG1", "BG1_CAMPAIGN_REQUIRED")
""")
RELOAD_TIMEOUT = 120


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


def drive(game: Path, name: str, evidence: Path, powershell: str, *, timeout: int = 10,
          client: Path | None = None, finish_prologue: bool = False, run=subprocess.run) -> dict:
    """Submit each step once. `run` is injectable for offline failure tests."""
    client = client or sibling_remote_client()
    evidence.mkdir(parents=True, exist_ok=False)
    report = {"schema": "flpr-character-creation-v1", "game": str(game), "name": name,
              "started_at": utc_now(), "status": "running", "steps": []}
    report_path = evidence / "status.json"
    write_json(report_path, report)
    try:
        steps = build_steps(name, finish_prologue)
        index = 0
        while index < len(steps):
            label, body = steps[index]
            index += 1
            step_timeout = RELOAD_TIMEOUT if label == RELOADED[0] else timeout
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
            if label == "02-select-bg1" and value.get("campaign_switched") is True:
                steps.insert(index, RELOADED)
            write_json(report_path, report)
            print(label, flush=True)
        report["status"] = "awaiting-prologue-world-confirmation"
        report["note"] = "Character creation submitted. Prologue completion and randomized loot are not yet verified."
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
    parser.add_argument("--game", type=Path, default=next(iter(LABS)))
    parser.add_argument("--name", default="FLPR QA", help="Test character name (1–20 characters).")
    parser.add_argument("--evidence", type=Path, help="New evidence directory outside the game.")
    parser.add_argument("--finish-prologue", action="store_true",
                        help="Dismiss CHAPTER once; requires its console polling widget.")
    parser.add_argument("--timeout", type=int, default=10, choices=range(5, 121), metavar="SECONDS",
                        help="Seconds each UI step may take (5-120). New Game loads for longer on a busy PC.")
    args = parser.parse_args(argv)
    lock = None
    try:
        validate_name(args.name)
        game = validate_game(args.game)
        require_idle_ipc(game)
        client = sibling_remote_client()
        if not client.is_file():
            raise FixtureError(f"Remote console client is missing: {client}")
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if not powershell:
            raise FixtureError("PowerShell is required for the sibling EEex Remote Console client.")
        evidence = (args.evidence or Path(__file__).resolve().parents[1] / "work" / "mode1-native-slice" /
                    ("character-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8])).resolve()
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
        report = drive(game, args.name, evidence, powershell, client=client,
                       finish_prologue=args.finish_prologue, timeout=args.timeout)
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
