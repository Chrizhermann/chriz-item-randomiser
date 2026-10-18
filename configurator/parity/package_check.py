"""The standalone package as a player uses it.

Unpacks a package built by tools/build_standalone.py into synthetic BG2:EE,
BG:EE and EET games (the installer suite's, with a readable area and the
content of the configurator checks' games), installs CIR with the package's
setup-cir.exe and starts its cir/cir-configurator.exe without arguments on a
desktop of its own, with LOCALAPPDATA in the temporary folder and
CIR_NO_BROWSER set.

- The package has setup-cir.exe at its root and everything else in cir/.
- setup-cir.exe finds cir/cir.tp2 by its own name and installs component 0.
- The executable finds the game; its scans equal the reference `scan` and
  `world scan`, and its editor's state the reference editor's with
  --apply-game. Nothing is written into the game before Apply; Apply works.

    python configurator/parity/package_check.py <package ZIP> --temp-root <short folder>

WeiDU has path-length limits, so the games go into a new folder below
--temp-root, which should be short.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import zipfile

from common import snapshot_tree
from cli_check import contents
from player_check import (APPLIED, EXE, Checks, Desktop, Start, apply, declare, found_game, reference,
                          workspace_of)

from campaign_runtime_installer import build_game  # noqa: E402
from test_postinstall_world_formats import make_are  # noqa: E402

SETUP = "setup-cir.exe"
PLATFORMS = ("bg2ee", "bgee", "eet")


def install(check: Checks, game: Path, label: str) -> bool:
    """setup-cir.exe as a batch installer runs it: component 0, language 0, no prompts."""
    result = subprocess.run([str(game / SETUP), "--force-install-list", "0", "--language", "0",
                             "--use-lang", "en_US", "--no-exit-pause", "--noautoupdate"],
                            cwd=game, capture_output=True, timeout=900)
    output = result.stdout.decode("utf-8", "replace") + result.stderr.decode("utf-8", "replace")
    log = (game / "weidu.log").read_text(encoding="latin-1") if (game / "weidu.log").is_file() else ""
    entries = [line for line in log.splitlines() if line.casefold().startswith("~cir/cir.tp2~")]
    installed = (result.returncode == 0 and "SUCCESSFULLY INSTALLED" in output.upper()
                 and [entry.split("//")[0].split() for entry in entries] == [["~CIR/CIR.TP2~", "#0", "#0"]])
    check(f"{label}: setup-cir.exe installs cir/cir.tp2 component 0", installed,
          f"exit {result.returncode}, weidu.log {entries}\n{output[-2000:]}")
    check(f"{label}: setup-cir.exe writes its own debug log", (game / "setup-cir.debug").is_file())
    return installed


def cases(check: Checks, desktop: Desktop, package: Path, root: Path, starts: list) -> None:
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        archive.extractall(root / "package")
    tops = sorted({PurePosixPath(name).parts[0] for name in names})
    check("package: setup-cir.exe and cir/ at its root", tops == ["cir", SETUP], tops)
    check("package: the configurator and the TP2 in cir/", {f"cir/{EXE}", "cir/cir.tp2"} <= set(names))
    appdata, logs = root / "appdata", root / "logs"
    logs.mkdir()
    environment = {**os.environ, "LOCALAPPDATA": str(appdata), "CIR_NO_BROWSER": "1"}
    for platform in PLATFORMS:
        game = root / platform
        # The installer fixture's marker area is not a readable area; the world scan reads one.
        marker = "oh1000.are" if platform == "bgee" else "oh6000.are"
        build_game(game, root / "package" / "cir", game / SETUP, platform=platform,
                   override={marker: make_are(), **contents()})
        shutil.copy2(root / "package" / SETUP, game / SETUP)
        if not install(check, game, platform):
            continue
        expected = reference(game, root / f"reference-{platform}", "en_us", root / f"reference-{platform}-workspace")
        before = snapshot_tree(game)
        started = Start(desktop, game / "cir" / EXE, game / "cir", environment, logs, platform)
        starts.append(started)
        editor, state = found_game(check, platform, started, game, workspace_of(appdata, game), expected)
        check(f"{platform}: nothing written into the game before Apply", snapshot_tree(game) == before)
        status, text = apply(editor, state, f"package-{platform}")
        check(f"{platform}: Apply", status == 200 and APPLIED in text, text[:600])
        started.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("package", type=Path, help="cir-v<VERSION>-windows.zip")
    parser.add_argument("--temp-root", type=Path, required=True, help="short folder for the games")
    args = parser.parse_args(argv)
    if os.name != "nt":
        raise SystemExit("package_check.py runs the package's Windows programs; run it on Windows.")
    declare()
    check = Checks()
    desktop = Desktop()
    starts: list[Start] = []
    args.temp_root.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="cir-pkg-", dir=args.temp_root) as temp:
            try:
                cases(check, desktop, args.package.resolve(strict=True), Path(temp).resolve(), starts)
            finally:
                # Before the folder goes: a running executable keeps its file.
                for started in starts:
                    started.stop()
    finally:
        desktop.close()
    print(f"{check.passed} checks passed, {check.failed} failed")
    return 1 if check.failed else 0


if __name__ == "__main__":
    sys.exit(main())
