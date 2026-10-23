"""Apply waits until the game, its loader, WeiDU and mod installers are closed.

The configurator lists the running programs with Windows' process snapshot,
not WMI: other programs can keep WMI busy enough that it cancels the query,
and Apply then could not run at all.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from flir_config.activation import (_GAME_OR_INSTALLER, _game_or_installer_executables, _listed_executable,
                                    require_closed)
from flir_config.profiles import ProfileError

OPEN = "Close this game and its installer before applying settings"
UNCHECKED = "Could not check whether the game is closed; settings were not applied"


class ProgramNameTests(unittest.TestCase):
    def test_the_game_its_loader_weidu_and_mod_installers_in_any_case(self):
        for name in ("Baldur.exe", "BALDUR.EXE", "baldur", "InfinityLoader.exe", "weidu.exe", "WeiDU.EXE",
                     "setup-cir.exe", "Setup-Item_Randomiser.EXE", "setup-.exe", "Baldur.exe.old"):
            self.assertTrue(_GAME_OR_INSTALLER.match(name), name)

    def test_other_programs(self):
        for name in ("", "BaldurGate.exe", "Baldur-launcher.exe", "baldur.ex", "setup.exe", "setup-cir.exe.log",
                     "mysetup-cir.exe", "weidu64.exe", "cir-configurator.exe", "python.exe",
                     "İnfinityLoader.exe"):
            self.assertFalse(_GAME_OR_INSTALLER.match(name), name)


@unittest.skipUnless(os.name == "nt", "Apply runs on Windows")
class RequireClosedTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.game = self.root / "game"
        self.game.mkdir()

    def running(self, *executables: str | None) -> None:
        with patch("flir_config.activation._game_or_installer_executables", return_value=list(executables)):
            require_closed(self.game)

    def test_programs_of_other_games_do_not_matter(self):
        self.running()
        self.running(str(self.root / "other game" / "Baldur.exe"), str(self.root / "gamer" / "weidu.exe"))

    def test_a_program_in_the_game_or_in_a_place_windows_does_not_tell_refuses(self):
        for executables in ([str(self.game / "Baldur.exe")], [str(self.game / "mod" / "setup-mod.exe")],
                            [str(self.root / "other game" / "Baldur.exe"), None], [""]):
            with self.subTest(executables), self.assertRaisesRegex(ProfileError, OPEN):
                self.running(*executables)

    def test_a_failed_process_list_refuses(self):
        with patch("flir_config.activation._game_or_installer_executables",
                   side_effect=OSError(5, "Access is denied")), self.assertRaisesRegex(ProfileError, UNCHECKED):
            require_closed(self.game)

    def test_running_programs(self):
        """A copy of Windows' findstr, waiting for input, runs under each name."""
        def start(path: Path) -> subprocess.Popen:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(os.environ["SystemRoot"]) / "System32" / "findstr.exe", path)
            process = subprocess.Popen([str(path), "x"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            self.addCleanup(process.communicate)
            self.addCleanup(process.kill)
            return process

        elsewhere = self.root / "other game" / "Baldur.exe"
        start(elsewhere)
        self.assertIn(elsewhere.resolve(),
                      {Path(path).resolve() for path in _game_or_installer_executables() if path})
        require_closed(self.game)
        installer = start(self.game / "setup-mod.exe")
        with self.assertRaisesRegex(ProfileError, OPEN):
            require_closed(self.game)
        self.assertEqual([Path(path).resolve() for path in _listed_executable(installer.pid)],
                         [(self.game / "setup-mod.exe").resolve()])
        # Listed, then it ends before its file is asked for. The handle this
        # test holds keeps the ended process, as another program's would; it
        # does not run, though Windows may no longer tell its file.
        installer.kill()
        installer.wait()
        self.assertEqual(_listed_executable(installer.pid), [])
        require_closed(self.game)


if __name__ == "__main__":
    unittest.main()
