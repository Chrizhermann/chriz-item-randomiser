"""Offline structural and refusal tests for naming native save/load buttons."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import stage_fixture_qa_menu as helper


FIXTURE = '''\ufeff-- menu { name 'FAKE' }
`local unused = {text = "menu { name 'FAKE' }"}`
menu
{
 name 'START'
 align center center
 button {
  text "SINGLEPLAYER_BUTTON"
  action "startEngine:SetEngineState(1) Infinity_TransitionMenu('START_SP')"
 }
}
menu {
 name 'START_SP'
 button {
  text "LOAD_GAME_BUTTON"
  action "Infinity_PopMenu(); startEngine:OnLoadGameButtonClick()"
 }
}
menu {
 name 'ESC_MENU'
 align center center
 onOpen "unrelated({1,2,3})"
 button { text "SAVE_GAME_BUTTON" action "optionsScreen:SaveGame(0)" }
 button { text "LOAD_GAME_BUTTON" action "popup2Button(19531, 'LOAD_BUTTON', function() optionsScreen:LoadGame() end)" }
 button { text "QUIT_GAME_BUTTON" action "
  if e:IsTouchUI() then
   popup3Button(16456, 'CANCEL_BUTTON', nil,
    'MAIN_MENU_BUTTON', function() optionsScreen:QuitGame() end,
    'SAVE_BUTTON', function() optionsScreen:SaveGame(0) end)
  else
   popup4Button(16456, 'CANCEL_BUTTON', nil,
    'MAIN_MENU_BUTTON', function() optionsScreen:QuitGame() end,
    'QUIT_BUTTON', function() Infinity_ShutdownGame() end,
    'SAVE_BUTTON', function() optionsScreen:SaveGame(0) end)
  end
 " }
}
menu {
 name 'LOAD'
 list {
  column { button { text "LOAD_BUTTON" action "wrongNestedAction()" } }
  actionDbl "loadScreen:LoadGame(gameSaves.files[currentSave].fileName)"
 }
 button { text "LOAD_BUTTON" action "loadScreen:LoadGame(gameSaves.files[currentSave].fileName)" }
}
menu {
 name 'SAVE'
 list { column { label { text "SAVE_BUTTON" } } }
 button {
  text "SAVE_BUTTON"
  action "
   --saveScreen:SaveGame(#saveFiles, 'Test Save ' .. #saveFiles)
   currentSave = 0
   Infinity_PushMenu( 'SAVE_NEWSAVE' );
  "
 }
}
menu {
 name 'SAVE_NEWSAVE'
 onOpen "bitmapbools = {hasScreenShot = 0} newSaveName = ''"
 edit { name "newSaveNameArea" action "completeOverwrite() completeSave()" }
 button { text "SAVE_BUTTON" action "completeSave()" }
 button { text "OVERWRITE_BUTTON" action "completeOverwrite()" }
}
menu {
 name 'POPUP_FOURBUTTON'
 modal
 button { text lua "t(Popup4Button.leftText)" action "Infinity_PopMenu() if (Popup4Button.leftFunc) then Popup4Button.leftFunc() end" }
 button { text lua "t(Popup4Button.rightText)" action "Infinity_PopMenu() if (Popup4Button.rightFunc) then Popup4Button.rightFunc() end" }
}
menu {
 name 'POPUP_TWOBUTTON'
 modal
 button {
  text lua "t(Popup2Button.cancelText or 'CANCEL_BUTTON')"
  action "Infinity_PopMenu('POPUP_TWOBUTTON') if (Popup2Button.cancelFunc) then Popup2Button.cancelFunc() end"
 }
 button {
  text lua "t(Popup2Button.okText or 'DONE_BUTTON')"
  action "Infinity_PopMenu('POPUP_TWOBUTTON') if (Popup2Button.okFunc) then Popup2Button.okFunc() end"
 }
}
'''


class FixtureQAMenuTests(unittest.TestCase):
    def test_only_insertions_preserve_every_existing_byte(self):
        for newline in ("\n", "\r\n"):
            source = FIXTURE.replace("\n", newline)
            updated, report = helper.enhance(source)
            self.assertEqual(report["insertions"], 13)
            self.assertEqual(report["added_polling"], ["ESC_MENU", "POPUP_TWOBUTTON", "POPUP_FOURBUTTON"])
            restored = updated
            for binding in helper.BINDINGS:
                marker = newline + f'\t\tname "{binding.name}"'
                self.assertEqual(restored.count(marker), 1)
                restored = restored.replace(marker, "", 1)
            widget = (f'\ttext{newline}\t{{{newline}\t\tarea 0 0 1 1{newline}'
                      f'\t\ttext lua "EEexRemote.Poll()"{newline}\t}}{newline}')
            self.assertEqual(restored.count(widget), 3)
            self.assertEqual(restored.replace(widget, ""), source)

    def test_end_polling_is_after_all_existing_widgets_and_attributes(self):
        updated, _ = helper.enhance(FIXTURE)
        tokens = helper.tokenize(updated)
        ends = helper.blocks(tokens)
        for opening in ends:
            direct = helper.direct_tokens(tokens, ends, opening)
            if helper.property_values(direct, "name") in (["ESC_MENU"], ["POPUP_TWOBUTTON"], ["POPUP_FOURBUTTON"]):
                tail = updated[tokens[opening].start:tokens[ends[opening]].start]
                self.assertTrue(tail.rstrip().endswith('text lua "EEexRemote.Poll()"\n\t}'))

    def test_repeated_enhancement_is_idempotent(self):
        once, _ = helper.enhance(FIXTURE)
        twice, report = helper.enhance(once)
        self.assertEqual(twice, once)
        self.assertEqual(report["insertions"], 0)

    def test_changed_action_refuses_instead_of_naming_a_different_operation(self):
        for original, replacement in (
            ("optionsScreen:SaveGame(0)", "optionsScreen:SaveGame(1)"),
            ("popup2Button(19531,", "popup2Button(15305,"),
            ('action "completeSave()"', 'action "completeOverwrite()"'),
            ("currentSave = 0", "currentSave = 1"),
            ("optionsScreen:QuitGame()", "Infinity_ShutdownGame()"),
            ("then Popup4Button.leftFunc() end", "then Popup4Button.rightFunc() end"),
        ):
            with self.subTest(original=original), self.assertRaisesRegex(helper.MenuError, "Unexpected action"):
                helper.enhance(FIXTURE.replace(original, replacement))

    def test_missing_duplicate_and_unexpected_names_are_rejected(self):
        button = 'button { text "SAVE_GAME_BUTTON" action "optionsScreen:SaveGame(0)" }'
        variants = (
            FIXTURE.replace(button, ""),
            FIXTURE.replace(button, button + button),
            FIXTURE.replace(button, button.replace("button {", 'button { name "OTHER"')),
            FIXTURE.replace(button, button.replace("button {", "button { name OTHER")),
            FIXTURE + "menu { name 'LOAD' }",
            FIXTURE + 'menu { name "UNRELATED" button { name "FLPR_QA_SAVE" } }',
        )
        for source in variants:
            with self.subTest(source=source[-150:]), self.assertRaises(helper.MenuError):
                helper.enhance(source)

    def test_273_menus_spelling_bg2ee_main_menu_and_unrelated_duplicates_are_accepted(self):
        # 2.7.3 writes popup texts with getUiString(...); BG2:EE has its single-player
        # button in START_MAIN; BG:EE 2.7.3 defines MESSAGE_SCREEN twice.
        source = (FIXTURE.replace('"t(Popup', '"getUiString(Popup')
                  .replace('text "SINGLEPLAYER_BUTTON"', 'text "MULTIPLAYER_BUTTON"')
                  + "menu { name 'MESSAGE_SCREEN' }\nmenu { name 'MESSAGE_SCREEN' }\n")
        updated, report = helper.enhance(source)
        self.assertEqual(report["unavailable"], [{"menu": "START", "name": "FLPR_QA_SINGLE"}])
        self.assertEqual([entry["name"] for entry in report["bindings"]],
                         [binding.name for binding in helper.BINDINGS if binding.name != "FLPR_QA_SINGLE"])
        self.assertEqual(updated.count('name "FLPR_QA_POPUP_CONFIRM"'), 1)
        self.assertEqual(updated.count('name "FLPR_QA_MAIN_MENU"'), 1)
        with self.assertRaisesRegex(helper.MenuError, "Duplicate menu: ESC_MENU"):
            helper.enhance(source + "menu { name 'ESC_MENU' }\n")

    def test_comment_mentions_do_not_count_as_poll_widgets(self):
        source = FIXTURE.replace("modal\n", 'modal\n -- EEexRemote.Poll()\n')
        _, report = helper.enhance(source)
        self.assertEqual(report["added_polling"], ["ESC_MENU", "POPUP_TWOBUTTON", "POPUP_FOURBUTTON"])

    def test_extract_inactive_menus_preserves_only_the_requested_complete_blocks(self):
        updated, _ = helper.enhance(FIXTURE)
        extracted = helper.extract_menus(updated, ("ESC_MENU", "POPUP_FOURBUTTON"))
        self.assertIn('name "FLPR_QA_QUIT"', extracted)
        self.assertIn('name "FLPR_QA_MAIN_MENU"', extracted)
        self.assertNotIn("name 'START'", extracted)
        self.assertNotIn("`local unused", extracted)
        self.assertEqual(extracted.count('text lua "EEexRemote.Poll()"'), 2)
        helper.blocks(helper.tokenize(extracted))
        for source, names in ((updated, ("MISSING",)),
                              (updated + "menu { name 'ESC_MENU' }", ("ESC_MENU",))):
            with self.assertRaises(helper.MenuError):
                helper.extract_menus(source, names)

    def test_malformed_syntax_is_rejected(self):
        for source in (FIXTURE + "}", FIXTURE + "{", FIXTURE + "'unterminated", FIXTURE + "--[[never closed"):
            with self.subTest(suffix=source[-25:]), self.assertRaises(helper.MenuError):
                helper.enhance(source)

    def test_stage_is_offline_and_refuses_overwrites_or_output_outside_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "game" / "UI.MENU"
            source.parent.mkdir()
            original = FIXTURE.replace("\n", "\r\n").encode("utf-8")
            source.write_bytes(original)
            work = root / "work"
            with patch.object(helper, "STAGING_ROOT", work):
                output = work / "staged" / "UI.MENU"
                report = helper.stage(source, output)
                self.assertEqual(report["insertions"], 13)
                self.assertEqual(source.read_bytes(), original)
                self.assertTrue(output.read_bytes().startswith(b"\xef\xbb\xbf"))
                before = output.read_bytes()
                for target in (source, source.parent / "new.menu", output):
                    with self.subTest(target=target), self.assertRaises(helper.MenuError):
                        helper.stage(source, target)
                self.assertEqual(output.read_bytes(), before)
                bad = root / "bad.menu"
                bad.write_text("menu { name 'BROKEN' }", encoding="utf-8")
                bad_output = work / "bad" / "UI.MENU"
                with self.assertRaises(helper.MenuError):
                    helper.stage(bad, bad_output)
                self.assertFalse(bad_output.exists())


if __name__ == "__main__":
    unittest.main()
