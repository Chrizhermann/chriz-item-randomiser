"""Apply's own files in the game are not a change of the game's mods; no real games.

Apply writes the extended reward routes' patched dialogues and scripts into
override and appends their texts to the game text. The next start of the
configurator scans the game again: a file that still holds exactly what Apply
wrote is read as the game had it before, so the scan, and with it the
configuration applied last, stays as it was. Every other change of the game
still changes the scan.
"""

from contextlib import ExitStack, contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from flir_config import applied_files
from flir_config.activation import EXPORT_FILES, deploy_export
from flir_config.campaigns import _render_lua
from flir_config.editor import Editor
from flir_config.profiles import ProfileError
from flir_config.resources import RESOURCE_TYPES
from test_postinstall_resource_reader import biff, key
from test_postinstall_resources import make_itm, snapshot
from test_reward_routes import SOURCES, TARGETS, RouteGame, ogre_dialogue, well_script
from tests import test_postinstall_editor as editor_tests

RUNTIME = ("override/M_FLPR.lua", "override/FLPRRun.menu", "override/M_FLPRUI.lua", "override/flrandomiser3.mrk")
DATA = Path("randomiser-campaigns")
RECORD = DATA / "applied-files.json"
BEFORE = DATA / "before-apply"
ROUTE_FILES = ("uhogre01.dlg", "ddwell.bcs")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def install_runtime(game: Path) -> None:
    for name in RUNTIME:
        (game / name).parent.mkdir(parents=True, exist_ok=True)
        (game / name).write_text("installed runtime")


class AppliedGame(RouteGame):
    """A game with CIR installed and the ogre's and the well's extended routes in override."""

    sources, targets = SOURCES[:2], TARGETS[:2]

    def build(self, platform: str = "bg2ee", **game) -> None:
        super().build(platform, **{"resources": {"uhogre01.dlg": ogre_dialogue(), "ddwell.bcs": well_script()},
                                   **game})
        install_runtime(self.game)
        self.workspace = self.root / "configurations"

    @contextmanager
    def restricted(self):
        """The test's routes in place of the full preset, as RouteGame.fit has them."""
        draft = self.draft()
        with ExitStack() as stack:
            stack.enter_context(patch("flir_config.original_configuration.resolve_template", return_value=draft))
            stack.enter_context(patch("flir_config.original_registrations.resolve_template", return_value=draft))
            stack.enter_context(patch("flir_config.activation.require_closed"))
            yield

    def editor(self, **draft) -> Editor:
        with self.restricted():
            return Editor(self.catalog, self.world, self.workspace, apply_game=self.game, **draft)

    def apply_fitted(self) -> tuple[Editor, dict]:
        """Fit the routes and apply them from the editor; the editor and Apply's result."""
        fitted = self.fit()
        self.assertEqual(fitted["not_used"], [])
        editor = self.editor(profile=fitted["profile"], plan=fitted["plan"])
        with self.restricted():
            result = editor.post("/api/apply", editor.draft)
        self.assertTrue(result["message"].startswith("Saved and applied."), result["message"])
        self.assertNotIn("Saved games", result["message"])
        return editor, result

    def record(self) -> dict:
        return json.loads((self.game / RECORD).read_text(encoding="utf-8"))

    def override(self, name: str) -> Path:
        return self.game / "override" / name

    def rows(self) -> dict[str, str]:
        return {row["id"]: row["sha256"] for row in self.world["resources"]}


class ApplyThenRescanTests(AppliedGame):
    """The player applies the extended routes, closes the configurator and starts it again."""

    def test_the_configuration_applied_last_reopens_after_a_rescan(self):
        for platform in ("bg2ee", "eet"):
            with self.subTest(platform=platform):
                self.build(platform)
                catalog, world = self.catalog, self.world
                text = self.game / "lang" / "en_US" / "dialog.tlk"
                game_text, journal = text.read_bytes(), self.override("BGEE.LUA").read_bytes()
                editor, _ = self.apply_fitted()
                # Apply wrote the routes' patched files into override and their text into the game text.
                applied = {name: self.override(name).read_bytes() for name in ROUTE_FILES}
                for name in ROUTE_FILES:
                    self.assertNotEqual(applied[name], self.resources[name])
                self.assertGreater(len(text.read_bytes()), len(game_text))
                self.assertEqual(self.override("BGEE.LUA").read_bytes(), journal)
                self.rescan()
                self.assertEqual(self.catalog, catalog)
                self.assertEqual(self.world, world)
                reopened = self.editor()
                self.assertEqual(reopened.draft, editor.draft)
                with self.restricted():
                    self.assertTrue(reopened.check(reopened.draft)["runtime_supported"])
                # The record names each file Apply changed, as the export names
                # it, and keeps what the game had there; the journal table is
                # exported as the game has it.
                self.assertEqual(self.record(), {"schema": "flir-applied-files-v1", "files": {
                    "override/DDWELL.BCS": {"before": {"sha256": digest(self.resources["ddwell.bcs"]),
                                                       "size": len(self.resources["ddwell.bcs"])},
                                            "written": digest(applied["ddwell.bcs"])},
                    "override/UHOGRE01.DLG": {"before": {"sha256": digest(self.resources["uhogre01.dlg"]),
                                                         "size": len(self.resources["uhogre01.dlg"])},
                                              "written": digest(applied["uhogre01.dlg"])},
                    "lang/en_us/dialog.tlk": {"before": {"sha256": digest(game_text), "size": len(game_text),
                                                         "strings": 2},
                                              "written": digest(text.read_bytes())}}})
                self.assertEqual({path.name: path.read_bytes() for path in (self.game / BEFORE / "override").iterdir()},
                                 {"DDWELL.BCS": self.resources["ddwell.bcs"],
                                  "UHOGRE01.DLG": self.resources["uhogre01.dlg"]})
                self.assertEqual([path.name for path in (self.game / BEFORE).iterdir()], ["override"])

    def test_a_game_with_its_text_outside_a_language_folder(self):
        # Only the ogre's route can be used there; it adds no text.
        self.sources, self.targets = SOURCES[:1], TARGETS[:1]
        self.build(language=None)
        world, game_text = self.world, (self.game / "dialog.tlk").read_bytes()
        editor, _ = self.apply_fitted()
        self.assertEqual((self.game / "dialog.tlk").read_bytes(), game_text)
        self.assertEqual(list(self.record()["files"]), ["override/UHOGRE01.DLG"])
        self.rescan()
        self.assertEqual(self.world, world)
        self.assertEqual(self.editor().draft, editor.draft)

    def test_a_real_change_of_the_game_still_changes_the_scan(self):
        self.build()
        world = self.world
        self.apply_fitted()
        ogre = self.override("uhogre01.dlg")
        applied = ogre.read_bytes()
        # Another mod changes the ogre's dialogue that Apply wrote.
        ogre.write_bytes(applied + b"\0")
        self.rescan()
        self.assertNotEqual(self.world["fingerprint"], world["fingerprint"])
        self.assertEqual(self.rows()["dlg:uhogre01"], digest(applied + b"\0"))
        with self.assertRaisesRegex(ProfileError, "different scan"):
            self.editor()
        # That mod is uninstalled and puts back Apply's file: the game is as before.
        ogre.write_bytes(applied)
        self.rescan()
        self.assertEqual(self.world, world)
        # Another mod changes a file Apply never wrote.
        self.override("ar1100.are").write_bytes(self.override("ar1100.are").read_bytes() + b"\0")
        self.rescan()
        self.assertNotEqual(self.world["fingerprint"], world["fingerprint"])

    def test_the_earlier_file_back_in_place_is_the_game_as_before(self):
        # The player puts back the ogre's dialogue as it was before Apply: the
        # game is as the configuration was made for, so it still opens.
        self.build()
        world = self.world
        editor, _ = self.apply_fitted()
        self.override("uhogre01.dlg").write_bytes(self.resources["uhogre01.dlg"])
        self.rescan()
        self.assertEqual(self.world, world)
        self.assertEqual(self.editor().draft, editor.draft)

    def test_applying_again_after_a_rescan_offers_and_writes_the_routes_again(self):
        # Each route's file in override is now CIR's own copy: the routes are
        # still offered, Apply exports the same files, and the record keeps
        # the files the game had before CIR first wrote them.
        self.build()
        world = self.world
        self.apply_fitted()
        applied = {name: self.override(name).read_bytes() for name in ROUTE_FILES}
        record = self.record()
        self.rescan()
        self.workspace = self.root / "another-workspace"
        self.apply_fitted()
        exported = self.workspace / "native" / "resource-overrides"
        self.assertEqual({name: (exported / name.upper()).read_bytes() for name in ROUTE_FILES},
                         {name: applied[name] for name in ROUTE_FILES})
        self.assertEqual({name: self.override(name).read_bytes() for name in applied}, applied)
        self.assertEqual(self.record(), record)
        self.rescan()
        self.assertEqual(self.world, world)

    def test_older_saves_keep_their_revisions(self):
        # After the restart the player applies other settings in the same
        # workspace: the revision earlier saves use stays as it was, without a
        # note that its settings may no longer work.
        self.build()
        _, first = self.apply_fitted()
        revisions = self.game / DATA / "campaign-store" / "revisions"
        earlier = {path.relative_to(revisions).as_posix(): path.read_bytes()
                   for path in revisions.rglob("*") if path.is_file()}
        self.assertTrue(any(name.startswith(first["revision"] + ".") for name in earlier))
        self.rescan()
        self.sources, self.targets = SOURCES[:1], TARGETS[:1]
        _, second = self.apply_fitted()
        self.assertNotEqual(second["revision"], first["revision"])
        self.assertEqual({name: (revisions / name).read_bytes() for name in earlier}, earlier)
        self.assertEqual((self.game / DATA / "source-gate.lua").read_bytes(),
                         _render_lua(sorted({*first["starts"].values(), *second["starts"].values()})))


class RouteInArchiveTests(AppliedGame):
    """The routes' files only in the game's archive, as in a game without other mods there.

    Apply creates their override files; the game had none, so a scan reads the archive's.
    """

    def build(self, platform: str = "bg2ee", **game) -> None:
        super().build(platform, **game)
        records, stored = [("SYNTH001", RESOURCE_TYPES["itm"], 7)], []
        for index, (name, data) in enumerate(sorted(self.resources.items())):
            stem, extension = name.split(".")
            locator = (1 << 20) | (index + 1)
            records.append((stem.upper(), RESOURCE_TYPES[extension], locator))
            stored.append((locator, RESOURCE_TYPES[extension], data))
            self.override(name).unlink()
        (self.game / "data" / "world.bif").write_bytes(biff(stored))
        (self.game / "chitin.key").write_bytes(key(records, [b"data\\items.bif\0", b"data/world.bif\0"]))
        self.rescan()

    def test_files_apply_created_are_read_from_the_archive(self):
        self.build()
        world = self.world
        editor, _ = self.apply_fitted()
        self.assertEqual({name: entry["before"] for name, entry in self.record()["files"].items()
                          if name in ("override/DDWELL.BCS", "override/UHOGRE01.DLG")},
                         {"override/DDWELL.BCS": None, "override/UHOGRE01.DLG": None})
        self.assertFalse((self.game / BEFORE / "override" / "UHOGRE01.DLG").exists())
        self.rescan()
        self.assertEqual(self.world, world)
        self.assertEqual(self.editor().draft, editor.draft)
        # A mod puts its own version of the ogre's dialogue into override: a change.
        self.override("uhogre01.dlg").write_bytes(self.resources["uhogre01.dlg"] + b"\0")
        self.rescan()
        self.assertNotEqual(self.world["fingerprint"], world["fingerprint"])
        # Without it, the archive's copy is the game's again.
        self.override("uhogre01.dlg").unlink()
        self.rescan()
        self.assertEqual(self.world, world)


class EarlierVersionTests(AppliedGame):
    """A game where Chriz Item Randomiser 1.0.1 applied, which kept no record."""

    def test_the_first_apply_keeps_the_scan_the_player_has(self):
        self.build()
        self.apply_fitted()
        shutil.rmtree(self.game / BEFORE)
        (self.game / RECORD).unlink()
        # Its files count as the game's, as 1.0.1 scanned them.
        self.rescan()
        catalog, world = self.catalog, self.world
        self.assertEqual(self.rows()["dlg:uhogre01"], digest(self.override("uhogre01.dlg").read_bytes()))
        self.workspace = self.root / "after-update"
        editor, _ = self.apply_fitted()
        self.rescan()
        self.assertEqual((self.catalog, self.world), (catalog, world))
        self.assertEqual(self.editor().draft, editor.draft)


class RecordTests(AppliedGame):
    def test_a_record_that_cannot_be_read_is_not_used_and_the_next_apply_starts_a_new_one(self):
        self.build()
        world = self.world
        self.apply_fitted()
        applied = self.override("uhogre01.dlg").read_bytes()
        for damaged in (b"{", b'{"schema": "flir-applied-files-v2", "files": {}}', b"[]",
                        json.dumps({"schema": "flir-applied-files-v1", "files": {
                            "override/UHOGRE01.DLG": {"before": None, "written": digest(applied)},
                            "override/uhogre01.dlg": {"before": None, "written": digest(applied)}}}).encode()):
            with self.subTest(record=damaged):
                (self.game / RECORD).write_bytes(damaged)
                self.rescan()
                # Apply's files count as the game's then, as they are.
                self.assertNotEqual(self.world["fingerprint"], world["fingerprint"])
                self.assertEqual(self.rows()["dlg:uhogre01"], digest(applied))
        # The player puts back the ogre's earlier dialogue; Apply patches it again.
        self.override("uhogre01.dlg").write_bytes(self.resources["uhogre01.dlg"])
        self.rescan()
        restored = self.world
        self.workspace = self.root / "another-workspace"
        self.apply_fitted()
        self.assertEqual(self.override("uhogre01.dlg").read_bytes(), applied)
        self.assertEqual(self.record()["files"], {"override/UHOGRE01.DLG": {
            "before": {"sha256": digest(self.resources["uhogre01.dlg"]), "size": len(self.resources["uhogre01.dlg"])},
            "written": digest(applied)}})
        self.rescan()
        self.assertEqual(self.world, restored)

    def test_a_damaged_copy_of_an_earlier_file_leaves_that_file_as_it_is(self):
        self.build()
        world = self.world
        self.apply_fitted()
        copy = self.game / BEFORE / "override" / "UHOGRE01.DLG"
        for change, damaged in (("longer", copy.read_bytes() + b"\0"), ("changed", copy.read_bytes()[:-1] + b"\xff"),
                                ("missing", None)):
            with self.subTest(copy=change):
                if damaged is None:
                    copy.unlink()
                else:
                    copy.write_bytes(damaged)
                self.rescan()
                rows = self.rows()
                self.assertEqual(rows["dlg:uhogre01"], digest(self.override("uhogre01.dlg").read_bytes()))
                self.assertEqual(rows["bcs:ddwell"], digest(self.resources["ddwell.bcs"]))
                self.assertNotEqual(self.world["fingerprint"], world["fingerprint"])

    def test_apply_refuses_game_text_that_changed_since_the_export(self):
        # Apply would replace it without knowing how many texts it held.
        self.build()
        self.apply_fitted()
        (self.game / "lang" / "en_US" / "dialog.tlk").write_bytes(b"no game text")
        game = snapshot(self.game)
        with patch("flir_config.activation.require_closed"), \
                self.assertRaisesRegex(ProfileError, "^Game text changed since export; apply again: "
                                                     "lang/en_us/dialog.tlk$"):
            deploy_export(self.game, self.workspace / "native")
        self.assertEqual(snapshot(self.game), game)

    def test_an_export_that_would_replace_the_record_is_refused(self):
        self.build()
        self.apply_fitted()
        output = self.workspace / "native"
        listing = json.loads((output / EXPORT_FILES).read_text(encoding="utf-8"))
        record = (self.game / RECORD).read_bytes()
        for name in ("applied-files.json", "Applied-Files.json", "before-apply/override/UHOGRE01.DLG",
                     "Before-Apply/override/BGEE.LUA"):
            with self.subTest(name=name):
                (output / name).parent.mkdir(parents=True, exist_ok=True)
                (output / name).write_bytes(b"{}")
                (output / EXPORT_FILES).write_text(json.dumps({**listing, "files": listing["files"] + [name]}),
                                                   encoding="utf-8")
                with patch("flir_config.activation.require_closed"), \
                        self.assertRaisesRegex(ProfileError, "would replace Chriz Item Randomiser's record"):
                    deploy_export(self.game, output)
                self.assertEqual((self.game / RECORD).read_bytes(), record)

    def test_record_format(self):
        entry = {"before": None, "written": "a" * 64}
        good = {"schema": applied_files.SCHEMA, "files": {
            "override/X.DLG": entry,
            "lang/en_us/dialog.tlk": {"before": {"sha256": "b" * 64, "size": 3, "strings": 2}, "written": "c" * 64},
            "override/Y.BCS": {"before": {"sha256": "b" * 64, "size": 0}, "written": "c" * 64}}}
        self.assertEqual(applied_files.entries(good), good["files"])

        def files(name="override/X.DLG", value=entry, **more):
            return {"schema": applied_files.SCHEMA, "files": {name: value, **more}}

        bad = [
            [],
            {"schema": applied_files.SCHEMA},
            {"schema": applied_files.SCHEMA, "files": [], "other": 1},
            {"schema": "flir-applied-files-v2", "files": {}},
            files(**{"OVERRIDE/x.dlg": entry}),
            files("randomiser-campaigns/next.lua"),
            files("Randomiser-Campaigns/before-apply/override/X.DLG"),
            files("../override/X.DLG"),
            files("/override/X.DLG"),
            files("override//X.DLG"),
            files("override\\X.DLG"),
            files("C:/override/X.DLG"),
            files("override/./X.DLG"),
            files(value={"before": None, "written": "A" * 64}),
            files(value={"before": None}),
            files(value={"before": None, "written": "a" * 64, "other": 1}),
            files(value={"before": {"sha256": "b" * 64, "size": 1, "strings": 2}, "written": "c" * 64}),
            files("lang/en_us/dialog.tlk", {"before": {"sha256": "b" * 64, "size": 1}, "written": "c" * 64}),
            files(value={"before": {"sha256": "b" * 64, "size": True}, "written": "c" * 64}),
            files(value={"before": {"sha256": "b" * 64, "size": -1}, "written": "c" * 64}),
            files(value={"before": {"sha256": "b" * 64, "size": 2 ** 32}, "written": "c" * 64}),
            files(value={"before": {"sha256": "b" * 64, "size": 1.0}, "written": "c" * 64}),
            files(value={"before": [], "written": "c" * 64}),
        ]
        for record in bad:
            with self.subTest(record=record):
                self.assertIsNone(applied_files.entries(record))


class NothingOutsideTests(unittest.TestCase):
    def test_apply_that_writes_nothing_outside_its_folder_keeps_no_record(self):
        # Standard pools prepare no override files and no game text.
        base = editor_tests.EditorTests("test_draft_editor_cannot_activate_game")
        base.setUp()
        self.addCleanup(base.doCleanups)
        fx, original = base.fx, base.editor
        install_runtime(fx.game)
        editor = Editor(fx.catalog, fx.world, original.workspace, **deepcopy(original.draft), apply_game=fx.game)
        with patch("flir_config.activation.require_closed"):
            editor.post("/api/apply", editor.draft)
        self.assertTrue((fx.game / DATA / "next.lua").is_file())
        self.assertFalse((fx.game / RECORD).exists())
        self.assertFalse((fx.game / BEFORE).exists())


class GameTextTests(AppliedGame):
    def test_texts_apply_appended_name_no_item(self):
        # SYNTH002's name points past the end of the game text, as some mods'
        # items do; Apply appends its texts there.
        visible = bytearray(make_itm(identified=2))
        struct.pack_into("<I", visible, 0x18, 0x4C)
        self.build(extra={"synth002.itm": bytes(visible)})
        catalog, world = self.catalog, self.world
        name = next(item["name"] for item in catalog["items"] if item["resref"] == "synth002")
        self.apply_fitted()
        self.rescan()
        self.assertEqual((self.catalog, self.world), (catalog, world))
        # Another mod adds to the game text after Apply: it is read as it is.
        text = self.game / "lang" / "en_US" / "dialog.tlk"
        text.write_bytes(text.read_bytes() + b"\0")
        self.rescan()
        self.assertNotEqual(next(item["name"] for item in self.catalog["items"] if item["resref"] == "synth002"),
                            name)


class ReinstallTests(AppliedGame):
    """WeiDU reinstalls Chriz Item Randomiser after Apply.

    WeiDU puts back only the files CIR's installer changed and keeps the game
    text it added; Apply's other files and randomiser-campaigns stay. Of
    Apply's files only the BGEE.LUA journal table is also the installer's (it
    files Unshey's journal entry there), so a reinstall puts back the
    installer's version of it.
    """

    def deploy_journal(self, journal: bytes) -> None:
        """Deploy the last export again with <journal>, as the Kangaxx option files its journal entry."""
        output = self.workspace / "native"
        (output / "resource-overrides" / "BGEE.LUA").write_bytes(journal)
        with patch("flir_config.activation.require_closed"):
            deploy_export(self.game, output)

    def test_a_reinstall_puts_back_the_installers_journal_table(self):
        self.build()
        world = self.world
        editor, _ = self.apply_fitted()
        installer = self.override("BGEE.LUA").read_bytes()
        filed = installer + b"-- Kangaxx\n"
        self.deploy_journal(filed)
        record = self.record()
        self.assertEqual(record["files"]["override/BGEE.LUA"],
                         {"before": {"sha256": digest(installer), "size": len(installer)}, "written": digest(filed)})
        # The uninstall takes the runtime and restores the game's own journal
        # table; the install puts back both, the installer's journal table too.
        for name in RUNTIME:
            (self.game / name).unlink()
        self.override("BGEE.LUA").write_bytes(b"-- the game's own journal table\n")
        install_runtime(self.game)
        self.override("BGEE.LUA").write_bytes(installer)
        self.rescan()
        self.assertEqual(self.world, world)
        self.assertEqual(self.editor().draft, editor.draft)
        # The next Apply files the journal entry again; the record stays as it was.
        self.deploy_journal(filed)
        self.assertEqual(self.override("BGEE.LUA").read_bytes(), filed)
        self.assertEqual(self.record(), record)
        self.assertEqual((self.game / BEFORE / "override" / "BGEE.LUA").read_bytes(), installer)

    def test_another_installer_version_is_the_journal_table_the_next_apply_starts_from(self):
        self.build()
        self.apply_fitted()
        installer = self.override("BGEE.LUA").read_bytes()
        self.deploy_journal(installer + b"-- Kangaxx\n")
        record = self.record()["files"]
        newer = b"quests = {} -- Unshey\n"
        self.override("BGEE.LUA").write_bytes(newer)
        self.deploy_journal(newer + b"-- Kangaxx\n")
        files = self.record()["files"]
        self.assertEqual(files["override/BGEE.LUA"], {"before": {"sha256": digest(newer), "size": len(newer)},
                                                      "written": digest(newer + b"-- Kangaxx\n")})
        self.assertEqual((self.game / BEFORE / "override" / "BGEE.LUA").read_bytes(), newer)
        self.assertEqual({name: entry for name, entry in files.items() if name != "override/BGEE.LUA"},
                         {name: entry for name, entry in record.items() if name != "override/BGEE.LUA"})



if __name__ == "__main__":
    unittest.main()
