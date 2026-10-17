"""The configurator accepts a runtime that cir.tp2 installed.

Such an install has no configuration yet, so Apply must not require an
existing next-game pointer, while a hand-staged test runtime still needs one.
"""
from copy import deepcopy
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from flir_config.activation import validate_runtime
from flir_config.campaigns import _render_lua
from flir_config.editor import Editor
from flir_config.profiles import ProfileError
from tests import test_postinstall_editor as editor_tests

INSTALLED = {
    "override/M_FLPR.lua": b"-- runtime\n",
    "override/FLPRRun.menu": b"menu\n",
    "override/M_FLPRUI.lua": b"-- adapter\n",
    "override/flrandomiser3.mrk": b"\x01",
    "override/FLPRGateAsm.lua": b"return {}\n",
}


def write(game: Path, files: dict[str, bytes]) -> None:
    for name, data in files.items():
        path = game / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


class ValidateRuntimeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.game = Path(folder.name) / "game"
        self.game.mkdir()
        self.catalog = {"game_root": str(self.game)}

    def check(self, files: dict[str, bytes]) -> Path:
        write(self.game, files)
        return validate_runtime(self.game, self.catalog)

    def test_installed_runtime_needs_no_configuration(self):
        self.assertEqual(self.check(INSTALLED), self.game.resolve())

    def test_staged_runtime_still_needs_its_pointer(self):
        staged = {"override/M_FLPR.lua": b"-- lab\n", "override/FLPRRun.menu": b"menu\n"}
        with self.assertRaisesRegex(ProfileError, "randomiser-campaigns/next.lua"):
            self.check(staged)
        self.assertEqual(self.check({"randomiser-campaigns/next.lua": b"return nil\n"}), self.game.resolve())

    def test_incomplete_installs_are_refused(self):
        for missing, message in (("override/M_FLPRUI.lua", "randomiser-campaigns/next.lua"),
                                 ("override/flrandomiser3.mrk", "randomiser-campaigns/next.lua"),
                                 ("override/FLPRRun.menu", "override/FLPRRun.menu"),
                                 ("override/M_FLPR.lua", "override/M_FLPR.lua")):
            with self.subTest(missing):
                files = {name: data for name, data in INSTALLED.items() if name != missing}
                for name in INSTALLED:
                    (self.game / name).unlink(missing_ok=True)
                with self.assertRaisesRegex(ProfileError, message):
                    self.check(files)

    def test_item_randomiser_installed_later_is_refused(self):
        log = "// Log of Currently Installed WeiDU Mods\r\n{} // component\r\n"
        for name, files, message in (
                ("mode 1", {"override/flrandomiser1.mrk": b"\x01"}, "randomisation (Mode 1 or Mode 2)"),
                ("mode 2", {"override/flrandomiser2.mrk": b"\x01"}, "randomisation (Mode 1 or Mode 2)"),
                ("spell shields", {"WeiDU.log": log.format("~RANDOMISER/RANDOMISER.TP2~ #1 #560").encode()},
                 "More Spell Shield scrolls"),
                ("spell shields, old name", {"WeiDU.log": log.format(r"~randomiser\setup-randomiser.tp2~ #0 #560")
                                             .encode()}, "More Spell Shield scrolls")):
            with self.subTest(name):
                for stale in ("override/flrandomiser1.mrk", "override/flrandomiser2.mrk", "WeiDU.log"):
                    (self.game / stale).unlink(missing_ok=True)
                with self.assertRaisesRegex(ProfileError, "cannot be combined with Item Randomiser's "
                                            + re.escape(message) + "; uninstall it"):
                    self.check({**INSTALLED, **files})

    def test_other_components_and_mods_are_accepted(self):
        log = ("~RANDOMISER/RANDOMISER.TP2~ #0 #9000 // Cespenar\r\n~OTHER/OTHER.TP2~ #0 #560 // unrelated\r\n"
               "~CIR/CIR.TP2~ #0 #0 // Chriz Item Randomiser\r\n")
        self.assertEqual(self.check({**INSTALLED, "WeiDU.log": log.encode()}), self.game.resolve())

    def test_other_game_is_refused(self):
        other = self.game.parent / "other"
        other.mkdir()
        write(other, INSTALLED)
        with self.assertRaisesRegex(ProfileError, "used by this scan"):
            validate_runtime(other, self.catalog)


class InstalledRuntimeApplyTests(unittest.TestCase):
    def setUp(self):
        self.base = editor_tests.EditorTests("test_draft_editor_cannot_activate_game")
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)

    def test_first_apply_publishes_pointer_and_keeps_installed_files(self):
        fx, original = self.base.fx, self.base.editor
        write(fx.game, INSTALLED)
        self.assertFalse((fx.game / "randomiser-campaigns/next.lua").exists())
        editor = Editor(fx.catalog, fx.world, original.workspace, **deepcopy(original.draft),
                        apply_game=fx.game)
        self.assertTrue(editor.state()["apply_enabled"])
        with patch("flir_config.activation.require_closed"):
            result = editor.post("/api/apply", editor.draft)
        self.assertEqual((fx.game / "randomiser-campaigns/next.lua").read_bytes(), _render_lua(result["revision"]))
        for name, data in INSTALLED.items():
            self.assertEqual((fx.game / name).read_bytes(), data, name)

    def test_apply_copies_only_the_files_of_the_latest_export(self):
        fx, original = self.base.fx, self.base.editor
        write(fx.game, INSTALLED)
        editor = Editor(fx.catalog, fx.world, original.workspace, **deepcopy(original.draft),
                        apply_game=fx.game)
        # An older export left a quest script and game text in the reused folder.
        output = original.workspace / "native"
        write(output, {"resource-overrides/BLPOOL.BCS": b"older configurator patch",
                       "script-overrides/BLPOOL.BCS": b"older configurator patch",
                       "game-overrides/dialog.tlk": b"older game text"})
        game_text = (fx.game / "dialog.tlk").read_bytes()
        with patch("flir_config.activation.require_closed"):
            editor.post("/api/apply", editor.draft)
        self.assertEqual((fx.game / "dialog.tlk").read_bytes(), game_text)
        for name in ("override/BLPOOL.BCS", "randomiser-campaigns/script-overrides"):
            self.assertFalse((fx.game / name).exists(), name)
        listing = (output / "export-files.json")
        listing.write_bytes(b'{"schema": "flir-export-files-v1", "files": ["next.lua"]}')
        with patch("flir_config.activation.require_closed"), \
                self.assertRaisesRegex(ProfileError, "export is incomplete"):
            from flir_config.activation import deploy_export
            deploy_export(fx.game, output)


if __name__ == "__main__":
    unittest.main()
