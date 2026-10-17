"""CIR's record of the quest and dialogue changes it installed."""

from pathlib import Path
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from flir_config import prepared
from flir_config.profiles import ProfileError

# As lib/campaign_patches.tpa writes it, with one change left out.
REGISTRY = """FLPR_PATCHES = {
    version = 1,
    quest_pool = { status = "applied", resource = "BLPOOL.BCS" },
    quest_altar = { status = "grant-ambiguous", resource = "TROALT.BCS" },
    quest_book = { status = "applied", resource = "PPBOOK.BCS" },
    unshey = { status = "applied", resource = "UNSHEY.DLG", item = "BELT04", journal = 412345 },
    flail_heads = { status = "applied", resource = "KPGLAI01.DLG" },
    totsc_tome = { status = "applied", resource = "FLBHCRYP.ITM" },
}
"""


class PreparedTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.game = Path(temporary.name)
        (self.game / "override").mkdir()

    def write(self, text):
        (self.game / "override" / "FLPRPAT.lua").write_text(text, encoding="utf-8")

    def test_record_lists_applied_and_left_out_changes(self):
        self.write(REGISTRY)
        entries = prepared.read_prepared(self.game)
        self.assertEqual(entries["unshey"], {"status": "applied", "resource": "UNSHEY.DLG",
                                             "item": "BELT04", "journal": 412345})
        self.assertEqual(prepared.applied_changes(self.game),
                         {"quest_pool", "quest_book", "unshey", "flail_heads", "totsc_tome"})
        self.assertTrue(prepared.applied(self.game, "quest_pool"))
        self.assertFalse(prepared.applied(self.game, "quest_altar"))
        self.assertEqual(prepared.left_out(self.game, "quest_altar"),
                         "TROALT.BCS was left as installed (it gives the reward more than once)")
        with self.assertRaisesRegex(ProfileError, "Unknown"):
            prepared.applied(self.game, "another_change")

    def test_without_a_record_nothing_is_applied(self):
        self.assertEqual(prepared.read_prepared(self.game), {})
        self.assertEqual(prepared.applied_changes(self.game), set())
        self.assertEqual(prepared.left_out(self.game, "flail_heads"),
                         "KPGLAI01.DLG was not prepared: this version of Chriz Item Randomiser is not installed")

    def test_installed_file_must_still_carry_the_change(self):
        self.write(REGISTRY)
        prepared.require_installed(self.game, "quest_pool",
                                   b"GLOBALFLPR_QPOOL GLOBALFLPR_XPOOL GLOBALFLPR_EPOOL")
        with self.assertRaisesRegex(ProfileError, "replaced after Chriz Item Randomiser"):
            prepared.require_installed(self.game, "quest_pool", b"GLOBALFLPR_QPOOL only")
        with self.assertRaisesRegex(ProfileError, "did not prepare bcs:troalt"):
            prepared.require_installed(self.game, "quest_altar",
                                       b"FLPR_QALTAR FLPR_XALTAR FLPR_EALTAR")

    def test_every_reason_the_installer_records_has_a_message(self):
        source = (Path(__file__).resolve().parents[1] / "lib" / "campaign_patches.tpa").read_text(encoding="utf-8")
        codes = set(re.findall(r"SPRINT flir_cp_[a-z_]+ ~([a-z]+(?:-[a-z]+)+|absent)~", source))
        self.assertTrue(codes)
        self.assertEqual(codes - {"applied", "ready"}, set(prepared.REASONS))
        tra = (Path(__file__).resolve().parents[1] / "languages" / "english" / "setup.tra").read_text(encoding="utf-8")
        for code, text in prepared.REASONS.items():
            with self.subTest(code):
                self.assertIn(f"~{code}~ BEGIN OUTER_SPRINT flir_cp_reason @", source)
                self.assertIn(f"=~{text}~", tra)


if __name__ == "__main__":
    unittest.main()
