"""Exact offline ARE fixture checks; no native engine behavior is inferred."""

import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_unvisited_fixture import (AREA_FILE, ITEMS, REPORT_FILE, TARGETS,
                                     build, make_unvisited_area)
from flir_config.resources import ScanError
from flir_config.world_formats import parse_are
from test_postinstall_world_formats import item_record, make_are


def copied_area():
    """Populated original data must stay inert and byte-identical after staging."""
    data = make_are([item_record("OLDITEM", charges=(7, 8, 9))],
                    [("OldChest", 0, 1)], [("OldActor", "COW")], gap=7)
    spawn_offset = len(data)
    data.extend(b"\x5a" * 0xC8)
    struct.pack_into("<II", data, 0x60, spawn_offset, 1)
    region_offset = len(data)
    data.extend(b"\x35" * 0xC4)
    struct.pack_into("<HI", data, 0x5A, 1, region_offset)
    rest_offset = len(data)
    data.extend(b"\x77" * 0xE4)
    struct.pack_into("<I", data, 0xC0, rest_offset)
    data.extend(b"original trailing bytes")
    parse_are(data)
    return bytes(data)


class UnvisitedFixtureTests(unittest.TestCase):
    def test_exact_active_indexes_payloads_and_preserved_original_bytes(self):
        before = copied_area()
        after = make_unvisited_area(before)
        parsed = parse_are(after)
        self.assertEqual(parsed["script"], "")
        self.assertEqual(parsed["actors"], [])
        self.assertEqual(struct.unpack_from("<H", after, 0x5A)[0], 0)
        self.assertEqual(struct.unpack_from("<I", after, 0x64)[0], 0)
        self.assertEqual(parsed["orphan_item_indexes"], [0])
        self.assertEqual(parsed["aliased_item_indexes"], [])
        self.assertEqual([(row["name"], row["x"], row["y"], row["type"],
                           row["first_item_index"], row["item_count"], row["item_indexes"])
                          for row in parsed["containers"]],
                         [(name, x, y, 4, first, count, list(range(first, first + count)))
                          for name, x, y, first, count in TARGETS])
        self.assertEqual([(row["record_index"], row["resref"], row["expiration"],
                           row["charges"], row["flags"], row["container_indexes"])
                          for row in parsed["items"]],
                         [(0, "flprx1", 0, [0, 0, 0], 1, []),
                          (1, "flpri1", 0, [0, 0, 0], 1, [0]),
                          (2, "flprx1", 0, [0, 0, 0], 1, [0]),
                          (3, "flpri2", 0, [0, 0, 0], 1, [1])])
        self.assertEqual(struct.unpack_from("<IH", after, 0x70), (len(before), 3))
        self.assertEqual(struct.unpack_from("<HI", after, 0x76), (4, len(before) + 3 * 0xC0))
        self.assertEqual(len(after), len(before) + 3 * 0xC0 + 4 * 20)
        for row in parsed["containers"]:
            base = row["byte_offset"]
            x, y = row["x"], row["y"]
            self.assertEqual(struct.unpack_from("<4H", after, base + 0x38),
                             (x - 12, y - 8, x + 12, y + 8))
            self.assertEqual(struct.unpack_from("<I", after, base + 0x84)[0], 0xFFFFFFFF)
        for index, name in enumerate(ITEMS):
            offset = len(before) + 3 * 0xC0 + index * 20
            self.assertEqual(after[offset:offset + 20],
                             struct.pack("<8s4HI", name.encode("ascii"), 0, 0, 0, 0, 1))
        rest_offset = struct.unpack_from("<I", before, 0xC0)[0]
        allowed = (set(range(0x58, 0x5C)) | set(range(0x64, 0x68)) |
                   set(range(0x70, 0x7C)) | set(range(0x94, 0x9C)) |
                   set(range(rest_offset, rest_offset + 0xE4)))
        self.assertTrue(all(left == right for i, (left, right) in
                            enumerate(zip(before, after)) if i not in allowed))
        self.assertEqual(after[rest_offset:rest_offset + 0xE4], bytes(0xE4))
        with self.assertRaisesRegex(ValueError, "already contains"):
            make_unvisited_area(after)

    def test_report_and_output_safety(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "copied-game"
            source = game / "override" / "BG2600.ARE"
            source.parent.mkdir(parents=True)
            original = copied_area()
            source.write_bytes(original)
            output = root / "staged"
            report = build(source, output)
            area = (output / "override" / AREA_FILE).read_bytes()
            saved = json.loads((output / REPORT_FILE).read_text(encoding="utf-8"))
            self.assertEqual(saved, report)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(report["input"]["sha256"], hashlib.sha256(original).hexdigest())
            self.assertEqual(report["output"]["sha256"], hashlib.sha256(area).hexdigest())
            self.assertEqual(report["active_tables"]["orphan_item_indexes"], [0])
            self.assertEqual([(row["first_item_index"], row["item_count"])
                              for row in report["containers"]], [(1, 2), (3, 1), (4, 0)])
            for row in report["items"]:
                self.assertEqual(bytes.fromhex(row["raw_hex"]), area[row["byte_offset"]:row["byte_offset"] + 20])
            for row in report["containers"]:
                self.assertEqual(bytes.fromhex(row["raw_hex"]), area[row["byte_offset"]:row["byte_offset"] + 0xC0])
            with self.assertRaisesRegex(ValueError, "never overwrite"):
                build(source, output)
            with self.assertRaisesRegex(ValueError, "outside"):
                build(source, game / "fixture")
            with self.assertRaisesRegex(ValueError, "outside"):
                build(source, root)

    def test_invalid_or_already_marked_inputs_do_not_write_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "copy" / "BG2600.ARE"
            source.parent.mkdir()
            output = root / "output"
            for bad in (b"not an ARE", bytes(make_are())[:-3]):
                source.write_bytes(bad)
                with self.assertRaises((ValueError, ScanError)):
                    build(source, output)
                self.assertFalse(output.exists())
            source.write_bytes(make_unvisited_area(copied_area()))
            with self.assertRaisesRegex(ValueError, "already contains"):
                build(source, output)
            self.assertFalse(output.exists())
            source.write_bytes(copied_area())
            wrong = source.with_name("WRONG.ARE")
            wrong.write_bytes(source.read_bytes())
            with self.assertRaisesRegex(ValueError, "BG2600.ARE"):
                build(wrong, output)

    def test_rejects_out_of_bounds_rest_block_and_marker_in_old_raw_data(self):
        original = bytearray(copied_area())
        struct.pack_into("<I", original, 0xC0, len(original) - 0xE3)
        with self.assertRaises(ScanError):
            make_unvisited_area(original)
        original = bytearray(copied_area())
        original[-4:] = b"FLPR"
        with self.assertRaisesRegex(ValueError, "already contains"):
            make_unvisited_area(original)


if __name__ == "__main__":
    unittest.main()
