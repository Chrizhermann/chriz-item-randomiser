"""Representative checks against the checked-in bg1pal EET mappings."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from flir_config.game_names import convert_resource
from flir_config.profiles import ProfileError


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

    def test_unknown_game_or_campaign_is_explicitly_rejected(self):
        for game in ("bgt", "tutu", "unknown"):
            with self.subTest(game=game), self.assertRaisesRegex(ProfileError, "Unsupported game"):
                convert_resource("ar0801", "are", "bg1", game)
        with self.assertRaisesRegex(ProfileError, "Unsupported campaign"):
            convert_resource("ar0801", "are", "sod")
        with self.assertRaisesRegex(ProfileError, "Unsupported resource extension"):
            convert_resource("ar0801", "bmp", "bg1")


if __name__ == "__main__":
    unittest.main()
