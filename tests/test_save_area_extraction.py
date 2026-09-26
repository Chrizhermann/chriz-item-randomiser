"""Synthetic SAV member selection; no game or profile files are read."""
from pathlib import Path
import struct
import sys
import unittest
import zlib


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from flir_config.resources import ScanError
from inspect_fixture_save import MAX_AREA, extract_area, extract_bg2600


def member(name: str, payload: bytes, *, declared: int | None = None,
           packed: bytes | None = None) -> bytes:
    filename = name.encode("ascii") + b"\0"
    compressed = zlib.compress(payload) if packed is None else packed
    return (struct.pack("<I", len(filename)) + filename
            + struct.pack("<II", len(payload) if declared is None else declared, len(compressed))
            + compressed)


def sav(*members: bytes) -> bytes:
    return b"SAV V1.0" + b"".join(members)


class SaveAreaExtractionTests(unittest.TestCase):
    def test_exact_named_area_only_is_inflated_and_members_are_counted(self):
        wanted = b"FLPRU1 area bytes\0\xff"
        archive = sav(member("FLPRU1.ARE.BAK", b"wrong suffix"),
                      member("../FLPRU1.ARE", b"traversal"),
                      member("FLPRU1A.ARE", b"wrong resref"),
                      member("OTHER.ARE", b"", packed=b"bad"),
                      member("fLpRu1.ArE", wanted))
        self.assertEqual(extract_area(archive, "FLPRU1"), (wanted, 5))
        self.assertEqual(extract_area(archive, "flpru1.are"), (wanted, 5))

    def test_missing_area_returns_none_and_total_member_count(self):
        archive = sav(member("BG2600.ARE", b"other"), member("FLPRU2.ARE", b"other"))
        self.assertEqual(extract_area(archive, "FLPRU1"), (None, 2))
        self.assertEqual(extract_area(sav(), "FLPRU1.ARE"), (None, 0))

    def test_duplicate_area_members_are_rejected_case_insensitively(self):
        archive = sav(member("FLPRU1.ARE", b"first"), member("flpru1.are", b"second"))
        with self.assertRaisesRegex(ScanError, r"Duplicate FLPRU1\.ARE"):
            extract_area(archive, "FlPrU1")

    def test_unsafe_resrefs_and_traversal_targets_are_rejected(self):
        archive = sav(member("FLPRU1.ARE", b"safe"))
        for target in ("", "ABCDEFGHI", "FLPRU1.ARE.BAK", "../FLPRU1", "..\\FLPRU1",
                       "FLPRU1/", "FLPRU1.", "FLPRU1\0", "FLPR-1", "é", 42):
            with self.subTest(target=target), self.assertRaisesRegex(ScanError, "safe 1-8"):
                extract_area(archive, target)

    def test_malformed_archive_bounds_and_filename_are_rejected(self):
        good = member("FLPRU1.ARE", b"ok")
        malformed = (
            b"NOT SAV!" + good,
            b"SAV V1.0" + struct.pack("<I", 0),
            b"SAV V1.0" + struct.pack("<I", 1025),
            b"SAV V1.0" + struct.pack("<I", 11) + b"FLPRU1.ARE",  # no NUL
            b"SAV V1.0" + struct.pack("<I", 12) + b"FLPR\0U1.ARE\0",
            b"SAV V1.0" + struct.pack("<I", 12) + b"FLPRU1.ARE\0",  # size fields absent
            b"SAV V1.0" + good[:-1],
        )
        for archive in malformed:
            with self.subTest(archive=archive[:25]), self.assertRaises(ScanError):
                extract_area(archive, "FLPRU1")

    def test_selected_area_size_and_zlib_checks_are_preserved(self):
        for entry in (
            member("FLPRU1.ARE", b"ok", declared=MAX_AREA + 1),
            member("FLPRU1.ARE", b"ok", declared=1),
            member("FLPRU1.ARE", b"ok", packed=b"bad"),
            member("FLPRU1.ARE", b"ok", packed=zlib.compress(b"ok") + b"tail"),
            member("FLPRU1.ARE", b"ok", packed=zlib.compress(b"ok")[:-1]),
        ):
            with self.subTest(entry=entry[-16:]), self.assertRaises(ScanError):
                extract_area(sav(entry), "FLPRU1")

    def test_bg2600_wrapper_keeps_original_selection_and_errors(self):
        archive = sav(member("FLPRU1.ARE", b"new"), member("BG2600.ARE", b"old"))
        self.assertEqual(extract_bg2600(archive), (b"old", 2))
        self.assertEqual(extract_bg2600(archive), extract_area(archive, "BG2600"))
        with self.assertRaisesRegex(ScanError, r"Duplicate BG2600\.ARE"):
            extract_bg2600(sav(member("BG2600.ARE", b"one"), member("bg2600.are", b"two")))


if __name__ == "__main__":
    unittest.main()
