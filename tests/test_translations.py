"""Item Randomiser's reward texts in the game's language; never discover or change a game install."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from flir_config import container_rewards, mode2_rewards, translations
from flir_config.game_text import GameText
from flir_config.resources import _Tlk
from test_postinstall_resources import make_tlk

# Every line a text route or a golem chest shows.
LINES = (*(line for resource in sorted(mode2_rewards.TEXT_REFS) for line in mode2_rewards.text_lines(resource)),
         *container_rewards.TEXT_LINES)
# The game languages BG:EE, BG2:EE and EET ship (2.7.3.0).
SHIPPED = ("cs_CZ", "de_DE", "en_US", "es_ES", "fr_FR", "hu_HU", "it_IT", "ja_JP", "ko_KR", "pl_PL", "pt_BR",
           "ru_RU", "tr_TR", "uk_UA", "zh_CN")
# The languages with every reward text; a new translation adds its language
# (docs/translating-reward-texts.md).
COMPLETE = ("en_US", "pl_PL", "de_DE")


class ShippedTranslationTests(unittest.TestCase):
    def test_the_complete_translations_have_every_reward_text(self):
        for code in COMPLETE:
            with self.subTest(language=code):
                self.assertIsNone(translations.missing(code, LINES))

    def test_every_shipped_language_has_a_translation_folder_or_a_reason(self):
        for code in SHIPPED:
            name, folder, _ = translations.language(code)
            reason = translations.missing(code, LINES)
            with self.subTest(language=code):
                if not (ROOT / "languages" / folder).is_dir():
                    self.assertEqual(reason, f"Item Randomiser's reward texts have no {name} translation yet")
                elif code not in COMPLETE:
                    self.assertIn(f"Item Randomiser's {name} translation has no reward text", reason)

    def test_texts_are_read_in_their_code_page_with_their_female_wording(self):
        # Polish is Windows-1250 and words a female protagonist's lines itself.
        polish = translations.lines("pl_PL", [("in-game.tra", 101), ("in-game.tra", 399)])
        self.assertIn("podniosłeś", polish["in-game.tra", 101][0])
        self.assertIn("podniosłaś", polish["in-game.tra", 101][1])
        self.assertEqual(*polish["in-game.tra", 399])
        german = translations.lines("de_DE", [("in-game.tra", 104), ("ppdjinn.tra", 99)])
        self.assertIn("der bessere Kämpfer", german["in-game.tra", 104][0])
        self.assertIn("die bessere Kämpferin", german["in-game.tra", 104][1])
        self.assertIn("<GABBER>", german["ppdjinn.tra", 99][0])
        english = translations.lines("en_US", [("in-game.tra", 101)])
        self.assertEqual(english["in-game.tra", 101],
                         ("As you pick up the item, two of the golems come to life and attack.",) * 2)

    def test_polish_reward_texts_go_to_both_files(self):
        text, refs = mode2_rewards.prepare_reward_texts(GameText(make_tlk(["a"]), make_tlk(["a (f)"])),
                                                        ("dlg:sctelwyn", "bcs:ddwell"), "pl_PL")
        for resource, old, number in (("dlg:sctelwyn", 29325, 111), ("bcs:ddwell", 40850, 399)):
            with self.subTest(resource=resource):
                ref = refs[resource][old]
                self.assertEqual((_Tlk(text.male).text(ref, set()), _Tlk(text.female).text(ref, set())),
                                 translations.lines("pl_PL", [("in-game.tra", number)])["in-game.tra", number])
        self.assertEqual(mode2_rewards.prepare_reward_texts(text, ("dlg:sctelwyn", "bcs:ddwell"), "pl_PL"),
                         (text, refs))
        golems, golem_refs = container_rewards.prepare_container_texts(text, ("bcs:kpgol01",), "pl_PL")
        ref = golem_refs["bcs:kpgol01"][38758]
        self.assertNotEqual(_Tlk(golems.male).text(ref, set()), _Tlk(golems.female).text(ref, set()))

    def test_a_game_language_without_a_translation(self):
        self.assertEqual(translations.missing("default", LINES),
                         "the game keeps its text outside a language directory, so CIR cannot tell which "
                         "translation of Item Randomiser's reward texts it needs")
        self.assertEqual(translations.missing("nb_NO", LINES),
                         "Item Randomiser's reward texts have no translation for the game language nb_NO")
        self.assertEqual(translations.texts("nb_NO", "in-game.tra"), {})


class TranslationFileTests(unittest.TestCase):
    """Translation folders as WeiDU leaves them, in a copy outside the mod."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="flir-translations-")
        self.addCleanup(temporary.cleanup)
        self.languages = Path(temporary.name) / "languages"
        self.languages.mkdir()
        patcher = patch.object(translations, "LANGUAGES_DIR", self.languages)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, relative: str, data: bytes) -> Path:
        path = self.languages / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_a_folder_weidu_converted_to_utf8_and_the_ee_texts(self):
        czech = "@101 = ~Zvedáš předmět.~\r\n@399 = ~Studna.~ ~Studna (ž).~\r\n"
        self.write("czech/in-game.tra", czech.encode("cp1250"))
        self.assertEqual(translations.lines("cs_CZ", [("in-game.tra", 101), ("in-game.tra", 399)]), {
            ("in-game.tra", 101): ("Zvedáš předmět.", "Zvedáš předmět."),
            ("in-game.tra", 399): ("Studna.", "Studna (ž).")})
        # Installed in Czech, the folder is UTF-8 and carries WeiDU's marker.
        self.write("czech/in-game.tra", czech.encode("utf-8"))
        self.write("czech/" + translations.CONVERTED, b"marker")
        self.assertEqual(translations.lines("cs_CZ", [("in-game.tra", 101)])["in-game.tra", 101][0], "Zvedáš předmět.")
        # The Enhanced Edition texts are UTF-8 as written and win.
        self.write("bgee/czech/in-game.tra", "@101 = ~Zvedáš věc.~\r\n".encode("utf-8"))
        self.assertEqual(translations.lines("cs_CZ", [("in-game.tra", 101)])["in-game.tra", 101][0], "Zvedáš věc.")

    def test_a_partial_or_unreadable_translation_names_what_is_missing(self):
        self.write("czech/in-game.tra", "@101 = ~Zvedáš předmět.~\r\n".encode("cp1250"))
        self.assertEqual(translations.missing("cs_CZ", [("in-game.tra", 101), ("in-game.tra", 399)]),
                         "Item Randomiser's Czech translation has no reward text @399 in czech/in-game.tra")
        self.assertEqual(translations.missing("cs_CZ", [("ppdjinn.tra", 99)]),
                         "Item Randomiser's Czech translation has no reward text @99 in czech/ppdjinn.tra")
        self.write("polish/in-game.tra", b"@101 = ~\x81~\r\n")
        self.assertEqual(translations.missing("pl_PL", [("in-game.tra", 101)]),
                         "languages/polish/in-game.tra is not Windows-1250 text, which this translation file must be")
        self.write("japanese/in-game.tra", b"@101 = ~\x81\x20~\r\n")
        self.assertEqual(translations.missing("ja_JP", [("in-game.tra", 101)]),
                         "languages/japanese/in-game.tra is not Shift-JIS (Windows-932) text, which this "
                         "translation file must be")

    def test_sources_cover_what_decides_the_texts(self):
        self.assertEqual(translations.sources("cs_CZ"), [self.languages])
        tra = self.write("czech/in-game.tra", b"@101 = ~x~\r\n")
        ee = self.write("bgee/czech/in-game.tra", b"@101 = ~y~\r\n")
        self.write("bgee/polish/in-game.tra", b"@101 = ~z~\r\n")
        self.assertEqual(translations.sources("cs_CZ"), [
            self.languages, self.languages / "bgee", self.languages / "czech", self.languages / "bgee" / "czech",
            tra, ee])
        self.assertEqual(translations.sources("default"), [self.languages, self.languages / "bgee"])


if __name__ == "__main__":
    unittest.main()
