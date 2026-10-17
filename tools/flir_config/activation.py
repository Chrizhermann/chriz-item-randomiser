"""Apply exported settings to an explicitly selected, preinstalled test runtime.

Runtime installation is separate. Keep exported revisions for older saves;
publish the next-game pointer last, with the game closed.
"""
from pathlib import Path, PurePosixPath
import json
import os
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory

from .profiles import ProfileError, read_json

# The files of the latest export in its folder. Apply copies only these, so a
# file an older export left there never reaches the game.
EXPORT_FILES = "export-files.json"
EXPORT_FILES_SCHEMA = "flir-export-files-v1"


def _gate_revisions(path: Path) -> set[str]:
    if not path.exists():
        return set()
    text = re.sub(r"\\([0-9]{3})", lambda m: chr(int(m[1])), path.read_text(encoding="utf-8"))
    if not re.fullmatch(r'return\s+(?:"[a-f0-9]{64}"|\{\s*(?:\[\d+\]\s*=\s*"[a-f0-9]{64}"\s*,?\s*)*\})\s*', text):
        raise ProfileError("Unrecognized source-gate settings; keep the existing test configuration")
    return set(re.findall(r'"([a-f0-9]{64})"', text))


def require_closed(game: Path) -> None:
    if os.name != "nt":
        raise ProfileError("Test-game activation currently supports Windows only")
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "$ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Process | Where-Object { "
             "$_.Name -match '^(Baldur|InfinityLoader|weidu)(\\.exe|$)' -or "
             "$_.Name -like 'setup-*.exe' } | Select-Object Name,ExecutablePath) | ConvertTo-Json -Compress"],
            capture_output=True, text=True, check=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.SubprocessError as exc:
        raise ProfileError("Could not check whether the test game is closed; settings were not applied") from exc
    processes = json.loads(result.stdout.strip() or "[]")
    if isinstance(processes, dict):
        processes = [processes]
    for process in processes:
        executable = process.get("ExecutablePath")
        if not executable or Path(executable).resolve().is_relative_to(game.resolve()):
            raise ProfileError("Close this test game and its installer before applying settings")


# cir.tp2 installs the runtime with this marker and UI
# adapter, before any configuration exists. A hand-staged test runtime has no
# marker and is recognised by its existing next-game pointer instead.
INSTALLED_RUNTIME = ("override/flrandomiser3.mrk", "override/M_FLPRUI.lua")
# Applied settings and kept campaign revisions; prototype/M_FLPR.lua reads them here.
CAMPAIGN_DATA = "randomiser-campaigns"


def validate_runtime(game: Path, catalog: dict) -> Path:
    game = game.resolve(strict=True)
    if game != Path(catalog["game_root"]).resolve():
        raise ProfileError("The activation game must be the game used by this scan")
    required = ["override/M_FLPR.lua", "override/FLPRRun.menu"]
    if not all((game / name).is_file() for name in INSTALLED_RUNTIME):
        required.append(CAMPAIGN_DATA + "/next.lua")
    for name in required:
        if not (game / name).is_file():
            raise ProfileError("Install Chriz Item Randomiser in this game first: " + name)
    return game


def deploy_export(game: Path, output: Path) -> None:
    """Copy the existing export layout without touching saves or ordinary stock."""
    require_closed(game)
    try:
        listing = read_json(output / EXPORT_FILES)
    except (OSError, ProfileError) as exc:
        raise ProfileError("The runtime export is incomplete; export the settings again") from exc
    names = listing.get("files") if listing.get("schema") == EXPORT_FILES_SCHEMA else None
    if (not isinstance(names, list) or not all(isinstance(name, str) for name in names) or
            not {"next.lua", "source-gate.lua"} <= set(names) or
            not all((output / name).is_file() for name in names)):
        raise ProfileError("The runtime export is incomplete; export the settings again")
    if not _gate_revisions(game / CAMPAIGN_DATA / "source-gate.lua") <= _gate_revisions(output / "source-gate.lua"):
        raise ProfileError("Use this game's previous configuration workspace: this export is missing "
                           "settings needed by older saves")
    changes = {}
    for name in sorted(names):
        relative = PurePosixPath(name)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ProfileError("An export destination is outside the test game")
        source = output.joinpath(*relative.parts)
        relative = Path(*relative.parts)
        if relative.parts[0] == "resource-overrides":
            relative = Path("override", *relative.parts[1:])
        elif relative.parts[0] == "game-overrides":
            relative = Path(*relative.parts[1:])
        else:
            relative = Path(CAMPAIGN_DATA) / relative
        target = (game / relative).resolve()
        if not target.is_relative_to(game.resolve()):
            raise ProfileError("An export destination is outside the test game")
        content = source.read_bytes()
        if target in changes and changes[target] != content:
            raise ProfileError("Conflicting export files: " + str(relative))
        if target.exists():
            if target.read_bytes() == content:
                continue
            if relative.as_posix().startswith(CAMPAIGN_DATA + "/campaign-store/revisions/"):
                raise ProfileError("An older campaign revision differs: " + str(relative))
        changes[target] = content
    pointer = game.resolve() / CAMPAIGN_DATA / "next.lua"
    ordered = sorted(changes, key=lambda path: (path == pointer, str(path)))
    # Roll back an actual copy error; no persistent lab-recovery framework.
    with TemporaryDirectory(prefix="apply-backup-", dir=output.parent) as temporary:
        backups = {}
        written = []
        try:
            for index, target in enumerate(ordered):
                backup = Path(temporary) / str(index) if target.exists() else None
                if backup:
                    shutil.copyfile(target, backup)
                backups[target] = backup
                target.parent.mkdir(parents=True, exist_ok=True)
                written.append(target)
                target.write_bytes(changes[target])
        except OSError:
            for target in reversed(written):
                if backups[target]:
                    shutil.copyfile(backups[target], target)
                else:
                    target.unlink(missing_ok=True)
            raise
