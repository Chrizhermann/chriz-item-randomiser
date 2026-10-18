"""Representative checks against EET's rename lists and the checked-in bg1pal copy."""

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

from flir_config.game_names import _eet_tables, convert_resource, eet_names
from flir_config.profiles import ProfileError
from test_postinstall_resources import write_eet_names


class GameNameTests(unittest.TestCase):
    def test_bg1_eet_area_script_and_collision_mappings(self):
        cases = [
            ("AR0801", "ARE", "bg0801"),
            ("ar0801n", "bcs", "bg0801n"),
            ("branwen", "bcs", "branwen_"),
            ("imoend", "bcs", "imoen2d"),
            ("skelwa02", "cre", "skelwa2_"),
            ("bimoen", "dlg", "bimoen2"),
            ("slng03", "itm", "slng05"),
            ("bag02", "itm", "bag02_"),
            ("bag02", "sto", "bag02_"),
        ]
        for name, extension, expected in cases:
            with self.subTest(name=name, extension=extension):
                self.assertEqual(convert_resource(name, extension, "bg1"), expected)

    def test_noncollisions_and_bg2_or_vanilla_games_pass_through(self):
        self.assertEqual(convert_resource("blpool", "bcs", "bg1"), "blpool")
        self.assertEqual(convert_resource("blun12", "itm", "bg1"), "blun12")
        self.assertEqual(convert_resource("ar0801", "itm", "bg1"), "ar0801")
        self.assertEqual(convert_resource("ar0801", "are", "bg2"), "ar0801")
        self.assertEqual(convert_resource("slng03", "itm", "bg2"), "slng03")
        self.assertEqual(convert_resource("AR0801", "are", "bg1", "bgee"), "ar0801")
        self.assertEqual(convert_resource("slng03", "itm", "bg1", "bg2ee"), "slng03")
        self.assertEqual(convert_resource("bd0120", "are", "sod"), "bd0120")
        self.assertEqual(convert_resource("bdbart01", "cre", "sod", "bgee"), "bdbart01")

    def test_unknown_game_or_campaign_is_explicitly_rejected(self):
        for game in ("bgt", "tutu", "unknown"):
            with self.subTest(game=game), self.assertRaisesRegex(ProfileError, "Unsupported game"):
                convert_resource("ar0801", "are", "bg1", game)
        for campaign in ("soa", "tob", "eet"):
            with self.subTest(campaign=campaign), self.assertRaisesRegex(ProfileError, "Unsupported campaign"):
                convert_resource("ar0801", "are", campaign)
        with self.assertRaisesRegex(ProfileError, "Unsupported resource extension"):
            convert_resource("ar0801", "bmp", "bg1")


class InstalledNameTests(unittest.TestCase):
    """EET's own rename lists win over the older copy in lib/fl#bg1pal.tpa."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="flir-eet-names-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write(self, text: str) -> None:
        lib = self.root / "EET" / "lib"
        lib.mkdir(parents=True, exist_ok=True)
        (lib / "pcu_dict.tph").write_bytes(text.encode("utf-8"))

    def test_installed_lists_rename_bg1_and_sod_names(self):
        # As EET 14 writes them: several arrays of one type, quoted and bare
        # names, comments (an em dash among them) and arrays CIR does not read.
        self.write(
            "/* EET resource lists\n"
            "ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_itm BEGIN\n\t\"OLD\" => \"WRONG\"\nEND\n*/\n"
            "ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_mus BEGIN\n\t1\t=>\t107\t//CDay1\nEND\n"
            "ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_itm BEGIN\n"
            "\t\"SLNG03\"\t=>\t\"SLNG03_\"\t// Snare—here\n"
            "\t\"HELM15\"\t=>\t\"HELM15_\"\nEND\n"
            "ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_cre BEGIN\n"
            "\tSKELWA02\t=>\tBGSKEL02\n\t\"BDBART01\"\t=>\t\"BDBART0_\"\nEND\n"
            "ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_itm BEGIN\nEND\n")
        names = eet_names(self.root)
        self.assertTrue(names.installed)
        self.assertEqual(names.notes, ())
        self.assertEqual(names.tables["itm"], {"slng03": "slng03_", "helm15": "helm15_"})
        self.assertEqual(names.tables["sto"], {})
        cases = [("slng03", "itm", "bg1", "slng03_"), ("scrl1v", "itm", "bg1", "scrl1v"),
                 ("skelwa02", "cre", "bg1", "bgskel02"), ("bdbart01", "cre", "sod", "bdbart0_"),
                 ("bag02", "sto", "bg1", "bag02"), ("ar0801", "bcs", "sod", "bg0801"),
                 ("bd0120", "are", "sod", "bd0120"), ("slng03", "itm", "bg2", "slng03")]
        for name, extension, campaign, expected in cases:
            with self.subTest(name=name, extension=extension):
                self.assertEqual(convert_resource(name, extension, campaign, "eet", names), expected)
        self.assertEqual(convert_resource("slng03", "itm", "bg1", "bgee", names), "slng03")

    def test_a_later_entry_replaces_an_earlier_one_as_in_weidu(self):
        self.write("ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_itm BEGIN\n\tSLNG03 => A\nEND\n"
                   "ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_cre BEGIN\nEND\n"
                   "ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_itm BEGIN\n\tslng03 => B\nEND\n")
        self.assertEqual(eet_names(self.root).tables["itm"], {"slng03": "b"})

    def test_lists_written_like_cirs_copy_rename_the_same(self):
        write_eet_names(self.root, _eet_tables())
        names = eet_names(self.root)
        self.assertTrue(names.installed)
        self.assertEqual(names.tables, _eet_tables())

    def test_without_readable_lists_cirs_copy_is_used_and_named(self):
        cases = {
            "absent": None,
            "unexpected line": "ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_itm BEGIN\n\tSLNG03 SLNG05\nEND\n"
                               "ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_cre BEGIN\nEND\n",
            "no cre list": "ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_itm BEGIN\n\tSLNG03 => SLNG03_\nEND\n",
        }
        for case, text in cases.items():
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory(prefix="flir-eet-names-") as folder:
                    self.root = Path(folder)
                    if text is not None:
                        self.write(text)
                    names = eet_names(self.root)
                    self.assertFalse(names.installed)
                    self.assertIs(names.tables, _eet_tables())
                    self.assertEqual(len(names.notes), 1)
                    self.assertIn("EET's rename lists (EET/lib/pcu_dict.tph)", names.notes[0])
                    self.assertIn("uses its own older copy", names.notes[0])
                    self.assertEqual(convert_resource("slng03", "itm", "bg1", "eet", names), "slng05")

    def test_folder_names_in_any_letter_case_are_found(self):
        lib = self.root / "eet" / "LIB"
        lib.mkdir(parents=True)
        (lib / "PCU_DICT.TPH").write_text(
            "ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_itm BEGIN\n\tSLNG03 => SLNG03_\nEND\n"
            "ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_cre BEGIN\nEND\n", encoding="utf-8")
        self.assertEqual(eet_names(self.root).tables["itm"], {"slng03": "slng03_"})


if __name__ == "__main__":
    unittest.main()
