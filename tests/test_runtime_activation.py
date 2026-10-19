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
from flir_config.activation import adopt_revisions, deploy_export, validate_runtime
from flir_config.campaigns import _render_lua
from flir_config.editor import Editor
from flir_config.native_export import NEXT_SCHEMA
from flir_config.profiles import ProfileError, edit_profile, new_profile
from flir_config.sources import new_binding_plan
from tests import test_postinstall_editor as editor_tests
from tests.test_postinstall_resources import snapshot
from tests.test_postinstall_world_formats import item_record, make_are, make_cre, make_sto

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
        self.assertEqual((fx.game / "randomiser-campaigns/next.lua").read_bytes(),
                         _render_lua({"schema": NEXT_SCHEMA, "starts": {"BG1": result["revision"]}}))
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
            deploy_export(fx.game, output)


class OtherWorkspaceApplyTests(unittest.TestCase):
    """Apply from a workspace that did not apply the game's earlier settings.

    A player who reinstalls Windows, moves to another PC or clears AppData
    loses the configuration workspace; the game keeps the revisions its saves
    use, and a new workspace applies with them.
    """

    def setUp(self):
        base = editor_tests.EditorTests("test_draft_editor_cannot_activate_game")
        base.setUp()
        self.addCleanup(base.doCleanups)
        self.fx = base.fx
        write(self.fx.game, INSTALLED)
        self.data = self.fx.game / "randomiser-campaigns"

    def apply(self, workspace: str, item: str = "synth001", source: str | None = None,
              target: str = "are:area/actor/1") -> dict:
        """Apply one original of <item> from the editor of <workspace>, as its own configuration."""
        fx = self.fx
        profile = edit_profile(new_profile(fx.catalog, workspace, f"Adventure of {workspace}"),
                               fx.catalog, include=[item])
        plan = new_binding_plan(profile, fx.catalog, fx.world)
        plan["sources"] = [{"item": item, "policy": "relocate", "registration_ids": [],
                            "source_ids": [source or fx.first]}]
        plan["targets"] = [{"id": target, "capacity": 1, "optional": False}]
        editor = Editor(fx.catalog, fx.world, fx.root / workspace, profile, plan, apply_game=fx.game)
        with patch("flir_config.activation.require_closed"):
            return editor.post("/api/apply", editor.draft)

    def revision_files(self, revision: str) -> dict[str, bytes]:
        folder = self.data / "campaign-store" / "revisions"
        return {path.relative_to(folder).as_posix(): path.read_bytes()
                for path in folder.rglob("*") if path.is_file() and path.name.startswith(revision + ".")}

    def test_a_new_workspace_applies_and_keeps_the_revisions_older_saves_use(self):
        fx = self.fx
        first = self.apply("workspace-a")
        kept = self.revision_files(first["revision"])
        self.assertIn(first["revision"] + ".native.lua", kept)
        # Workspace A is lost. A new one scans the game as A left it and applies other settings.
        fx.refresh()
        second = self.apply("workspace-b", "synth002", fx.second)
        self.assertNotEqual(second["revision"], first["revision"])
        self.assertEqual(self.revision_files(first["revision"]), kept)
        self.assertTrue(self.revision_files(second["revision"]))
        self.assertEqual((self.data / "source-gate.lua").read_bytes(),
                         _render_lua(sorted([first["revision"], second["revision"]])))
        self.assertEqual((self.data / "next.lua").read_bytes(),
                         _render_lua({"schema": NEXT_SCHEMA, "starts": second["starts"]}))
        self.assertEqual((self.data / "AREA.pristine.are").read_bytes(), fx.area)
        self.assertNotIn("Saved games", second["message"])
        # The new workspace now holds A's revision as the game has it.
        adopted = fx.root / "workspace-b" / "native" / "campaign-store" / "revisions"
        self.assertEqual({name: (adopted / name).read_bytes() for name in kept}, kept)
        # Workspace A comes back (a backup) and applies again: B's saves keep theirs too.
        later = self.revision_files(second["revision"])
        third = self.apply("workspace-a", "synth002", fx.second, target="are:area/actor/0")
        self.assertEqual((self.revision_files(first["revision"]), self.revision_files(second["revision"])),
                         (kept, later))
        self.assertEqual((self.data / "source-gate.lua").read_bytes(),
                         _render_lua(sorted([first["revision"], second["revision"], third["revision"]])))

    def test_adopting_copies_each_revision_once_as_the_game_has_it(self):
        first = self.apply("workspace-a")
        output = self.fx.root / "workspace-b" / "native"
        self.assertEqual(adopt_revisions(self.fx.game, output), [first["revision"]])
        self.assertEqual(snapshot(output / "campaign-store"), snapshot(self.data / "campaign-store"))
        self.assertEqual((output / "AREA.pristine.are").read_bytes(), self.fx.area)
        self.assertEqual(adopt_revisions(self.fx.game, output), [])
        # A game without applied settings has nothing to adopt.
        empty = self.fx.root / "empty-game"
        empty.mkdir()
        self.assertEqual(adopt_revisions(empty, self.fx.root / "workspace-c" / "native"), [])
        self.assertFalse((self.fx.root / "workspace-c").exists())
        with self.assertRaisesRegex(ProfileError, "outside the scanned game"):
            adopt_revisions(self.fx.game, self.data / "export")

    def test_another_version_of_a_revision_in_the_workspace_refuses(self):
        first = self.apply("workspace-a")
        revision = first["revision"]
        output = self.fx.root / "workspace-b" / "native"
        # Workspace B holds another export of the same revision, such as one restored from a backup.
        adopt_revisions(self.fx.game, output)
        native = output / "campaign-store" / "revisions" / f"{revision}.native.lua"
        native.write_bytes(native.read_bytes() + b"-- another export\n")
        game, workspace = snapshot(self.fx.game), snapshot(output)
        with self.assertRaisesRegex(ProfileError, re.escape(
                f"Campaign revision {revision[:12]} in this configuration workspace differs from the game's "
                f"({revision}.native.lua); saved games need the game's, so nothing was applied. "
                f"Move {output.resolve()} aside, then apply again")):
            self.apply("workspace-b", "synth002", self.fx.second)
        self.assertEqual((snapshot(self.fx.game), snapshot(output)), (game, workspace))

    def test_deploy_still_refuses_another_version_and_a_missing_revision(self):
        first = self.apply("workspace-a")
        revision = first["revision"]
        output = self.fx.root / "workspace-a" / "native"
        game = snapshot(self.fx.game)
        lua = output / "campaign-store" / "revisions" / f"{revision}.lua"
        original = lua.read_bytes()
        lua.write_bytes(original + b"-- another export\n")
        with patch("flir_config.activation.require_closed"), self.assertRaisesRegex(
                ProfileError, re.escape("An older campaign revision differs: randomiser-campaigns")):
            deploy_export(self.fx.game, output)
        lua.write_bytes(original)
        (output / "source-gate.lua").write_bytes(_render_lua([]))
        with patch("flir_config.activation.require_closed"), self.assertRaisesRegex(
                ProfileError, re.escape(f"This export is missing campaign revision {revision[:12]}, which older "
                                        "saves of this game need; apply the settings again")):
            deploy_export(self.fx.game, output)
        self.assertEqual(snapshot(self.fx.game), game)

    def test_a_listed_revision_the_game_lost_or_cannot_read_refuses(self):
        first = self.apply("workspace-a")
        revision = first["revision"]
        folder = self.data / "campaign-store" / "revisions"
        lua = (folder / f"{revision}.lua").read_bytes()
        (folder / f"{revision}.lua").write_bytes(lua.replace(b"return {", b"return  {", 1))
        game = snapshot(self.fx.game)
        with self.assertRaisesRegex(ProfileError, re.escape(
                f"This game's campaign revision {revision[:12]} cannot be read: "
                "Campaign JSON and Lua artifacts disagree")):
            self.apply("workspace-b", "synth002", self.fx.second)
        self.assertEqual(snapshot(self.fx.game), game)
        (folder / f"{revision}.json").unlink()
        game = snapshot(self.fx.game)
        with self.assertRaisesRegex(ProfileError, re.escape(
                f"This game's settings list campaign revision {revision[:12]} for older saves, but its files are "
                "missing from randomiser-campaigns/campaign-store/revisions; restore them before applying")):
            self.apply("workspace-b", "synth002", self.fx.second)
        self.assertEqual(snapshot(self.fx.game), game)
        self.assertFalse((self.fx.root / "workspace-b" / "native").exists())

    def test_saves_of_a_revision_another_workspace_applied_are_protected_as_its_own_would(self):
        fx = self.fx
        (fx.override / "shop.sto").write_bytes(make_sto([
            (item_record("SYNTH002", expiration=0, charges=(0, 0, 0), flags=1), 1, 0)]))
        fx.refresh()
        first = self.apply("workspace-a")
        revision = first["revision"]
        kept = self.revision_files(revision)
        # A mod then changes the chest's item. A new workspace's settings that
        # leave the area alone apply; A's saves keep their settings, with the
        # pristine area copy the game kept for them.
        embedded = bytearray(make_cre([]))
        embedded[0x280:0x288] = b"EmbedDV\0"
        (fx.override / "area.are").write_bytes(make_are(
            [item_record("SYNTH001", expiration=0, charges=(0, 0, 0), flags=3),
             item_record("SYNTH002", expiration=0, charges=(0, 0, 0), flags=1)],
            [("ChestOne", 0, 1), ("ChestTwo", 1, 1)], [("External", "ACTOR"), ("Embedded", bytes(embedded))]))
        fx.refresh()
        shop = next(row["id"] for row in fx.world["sources"] if row["kind"] == "store")
        second = self.apply("workspace-b", "synth002", shop, target="are:area/actor/0")
        self.assertIn(f"Saved games of revision {revision[:12]} keep their settings, but "
                      f"{fx.first} may no longer work in them: Selected source differs from the scan",
                      second["message"])
        self.assertEqual(self.revision_files(revision), kept)
        self.assertEqual((self.data / "AREA.pristine.are").read_bytes(), fx.area)
        self.assertEqual((self.data / "source-gate.lua").read_bytes(),
                         _render_lua(sorted([revision, second["revision"]])))
        # Settings that would replace that area copy stop, as in A's own workspace.
        game = snapshot(fx.game)
        with self.assertRaisesRegex(ProfileError, re.escape(
                f"Saved games of revision {revision[:12]} need AREA.ARE as it was when they were exported, "
                "but it changed in the game since then")):
            self.apply("workspace-c", "synth002", fx.second, target="are:area/actor/0")
        self.assertEqual(snapshot(fx.game), game)


if __name__ == "__main__":
    unittest.main()
