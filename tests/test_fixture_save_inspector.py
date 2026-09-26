"""Synthetic save structure tests. Fixtures live only in temporary directories."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from inspect_fixture_save import extract_bg2600, inspect_area, inspect_save, parse_gam
from flir_config.resources import ScanError
from test_postinstall_world_formats import item_record, make_are, make_cre


def gam(variables=(), party=()):
    data = bytearray(0xB4)
    data[:8] = b"GAMEV2.0"
    data[0x58:0x60] = b"BG2600\0\0"
    struct.pack_into("<II", data, 0x20, len(data), len(party))
    data.extend(b"\0" * (len(party) * 0x160))
    for index, creature in enumerate(party):
        base = 0xB4 + index * 0x160
        struct.pack_into("<HII", data, base + 2, index, len(data), len(creature))
        data[base + 0x18:base + 0x20] = b"BG2600\0\0"
        data.extend(creature)
    struct.pack_into("<II", data, 0x38, len(data), len(variables))
    for name, value in variables:
        record = bytearray(84)
        record[:len(name)] = name.encode("ascii")
        struct.pack_into("<i", record, 0x28, value)
        data.extend(record)
    return data


def sav(entries):
    data = bytearray(b"SAV V1.0")
    for name, payload in entries:
        encoded = name.encode("ascii") + b"\0"
        packed = zlib.compress(payload)
        data.extend(struct.pack("<I", len(encoded)) + encoded + struct.pack("<II", len(payload), len(packed)) + packed)
    return data


def named_cre(name, records=()):
    creature = make_cre(records)
    creature[0x280:0x2A0] = name.encode("ascii").ljust(32, b"\0")
    return creature


class FixtureSaveInspectorTests(unittest.TestCase):
    def test_revision_numeric_globals_signed_journal_and_encoded_phases(self):
        variables = [(f"flpr_r{index:02}", index) for index in range(1, 17)]
        variables += [("FLPR_SEED", 7654), ("FLPR_STATE", 2), ("FLPR_DONE", 1), ("FLPR_FRESH", 0),
                      ("FLPR_JKIND", -1), ("FLPR_S0001", 3), ("FLPR_D0001", 4), ("FLPR_A0001", 2),
                      ("UNRELATED", 99)]
        result = parse_gam(gam(variables))
        self.assertEqual(result["revision"], "".join(f"{index:04x}" for index in range(1, 17)))
        self.assertEqual(result["globals"]["FLPR_JKIND"], -1)
        self.assertNotIn("UNRELATED", result["globals"])
        self.assertEqual(result["phases"]["sources"]["FLPR_S0001"]["phase"], "verified")
        self.assertEqual(result["phases"]["deliveries"]["FLPR_D0001"]["phase"], "quarantined")
        self.assertEqual(result["seed"], 7654)
        self.assertEqual(result["assignments"], {"FLPR_A0001": 2})

    def test_missing_or_invalid_revision_chunk_is_not_assumed_zero(self):
        for missing in ([], [("FLPR_R01", -1)], [("FLPR_R01", 65536)]):
            with self.subTest(missing=missing):
                self.assertIsNone(parse_gam(gam(missing))["revision"])

    def test_full_inventory_payloads_are_grouped_and_files_unchanged(self):
        source = named_cre("FLPRS1", [item_record("FLPRI1", expiration=7, charges=(2, 3, 4), flags=9)])
        area = make_are([item_record("FLPRI2", expiration=0, charges=(0, 0, 0), flags=0)],
                        [("FLPRT1", 0, 1)], [("FLPRS1", source)])
        container = struct.unpack_from("<I", area, 0x70)[0]
        struct.pack_into("<H", area, container + 0x24, 4)
        party = named_cre("Player1", [item_record("FLPRI3", charges=(5, 6, 7), flags=1)])
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "BALDUR.gam").write_bytes(gam(party=[party]))
            (folder / "BALDUR.SAV").write_bytes(sav([("BG2600.ARE", area)]))
            before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in folder.iterdir()}
            result = inspect_save(folder)
            after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in folder.iterdir()}
            self.assertEqual(before, after)
        self.assertEqual(result["observed_record_counts"]["sources"]["flpri1"], 1)
        self.assertEqual(result["observed_record_counts"]["targets"]["flpri2"], 1)
        self.assertEqual(result["observed_record_counts"]["party"]["flpri3"], 1)
        record = result["bg2600"]["sources"][0]["fixture_items"][0]
        self.assertEqual((record["expiration"], record["charges"], record["flags"], record["slots"]),
                         (7, [2, 3, 4], 9, [21]))
        self.assertEqual(result["bg2600"]["targets"][0]["type"], 4)
        self.assertEqual(result["coverage"]["expected_sources"]["flprs1"]["observed_inventories"], 1)

    def test_external_source_is_unobserved_instead_of_substituted_from_install(self):
        result = inspect_area(make_are(actors=[("FLPRS1", "FLPRS1")]))
        self.assertFalse(result["sources"][0]["inventory_observed"])
        self.assertIsNone(result["sources"][0]["fixture_items"])

    def test_duplicate_actor_occurrences_are_preserved(self):
        creature = named_cre("FLPRS1", [item_record("FLPRI1")])
        result = inspect_area(make_are(actors=[("FLPRS1", creature), ("FLPRS1", creature)]))
        self.assertEqual(len(result["sources"]), 2)

    def test_only_requested_area_is_decompressed(self):
        area = make_are()
        unrelated_name = b"AR0001.ARE\0"
        corrupt_other = struct.pack("<I", len(unrelated_name)) + unrelated_name + struct.pack("<II", 123, 3) + b"bad"
        archive = bytearray(b"SAV V1.0") + corrupt_other + sav([("BG2600.ARE", area)])[8:]
        extracted, count = extract_bg2600(archive)
        self.assertEqual(extracted, area)
        self.assertEqual(count, 2)

    def test_duplicate_area_or_wrong_uncompressed_size_is_rejected(self):
        with self.assertRaisesRegex(ScanError, "Duplicate"):
            extract_bg2600(sav([("BG2600.ARE", make_are()), ("bg2600.are", make_are())]))
        archive = sav([("BG2600.ARE", make_are())])
        struct.pack_into("<I", archive, 8 + 4 + len(b"BG2600.ARE\0"), 1)
        with self.assertRaisesRegex(ScanError, "size"):
            extract_bg2600(archive)

    def test_duplicate_globals_and_truncated_tables_are_rejected(self):
        with self.assertRaisesRegex(ScanError, "Duplicate"):
            parse_gam(gam([("FLPR_SEED", 1), ("flpr_seed", 2)]))
        damaged = gam([("FLPR_SEED", 1)])[:-1]
        with self.assertRaisesRegex(ScanError, "truncated"):
            parse_gam(damaged)

    def test_missing_bg2600_reports_absence_not_empty_area(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "BALDUR.gam").write_bytes(gam())
            (folder / "BALDUR.SAV").write_bytes(sav([]))
            result = inspect_save(folder)
        self.assertIsNone(result["bg2600"])
        self.assertFalse(result["coverage"]["bg2600_saved"])


if __name__ == "__main__":
    unittest.main()
