"""Synthetic, read-only resource scanning tests; no real game discovery."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from flir_config.resources import ScanError, catalog_fingerprint, scan_game
from flir_config import resources as resource_module


def make_tlk(strings: list[str]) -> bytes:
    entries, payload = bytearray(), bytearray()
    for text in strings:
        raw = text.encode("utf-8")
        entries += struct.pack("<H8sIIII", 1, b"", 0, 0, len(payload), len(raw))
        payload += raw
    return b"TLK V1  " + struct.pack("<HII", 0, len(strings), 0x12 + len(entries)) + entries + payload


def make_itm(unidentified: int = 0, identified: int = 1, *, price: int = 300) -> bytes:
    data = bytearray(0x72)
    data[:8] = b"ITM V1  "
    struct.pack_into("<II", data, 8, unidentified, identified)
    struct.pack_into("<IH", data, 0x18, 0x44, 16)
    data[0x31] = 101
    struct.pack_into("<IH", data, 0x34, price, 1)
    struct.pack_into("<I", data, 0x60, 2)
    return bytes(data)


def make_biff(resources: list[tuple[str, bytes]], *, reverse_table: bool = False) -> bytes:
    # Payload before table, with sparse locators: validates offset/id use.
    payload = b"".join(data for _name, data in resources)
    table_offset = 0x14 + len(payload)
    entries, offset = [], 0x14
    for index, (_name, data) in enumerate(resources):
        entries.append(struct.pack("<IIIHH", 7 + index * 3, offset, len(data), 1005, 0))
        offset += len(data)
    if reverse_table:
        entries.reverse()
    return b"BIFFV1  " + struct.pack("<III", len(resources), 0, table_offset) + payload + b"".join(entries)


def make_key(resources: list[tuple[str, bytes]], bif_size: int, *, filename: bytes = b"data\\items.bif\0") -> bytes:
    resource_offset = 0x24
    filename_offset = resource_offset + 14 * len(resources)
    records = b"".join(struct.pack("<8sHI", name.encode("ascii"), 1005, 7 + i * 3) for i, (name, _data) in enumerate(resources))
    return (b"KEY V1  " + struct.pack("<IIII", 1, len(resources), 0x18, resource_offset)
            + struct.pack("<IIHH", bif_size, filename_offset, len(filename), 1)
            + records + filename)


def make_game(root: Path, resources: list[tuple[str, bytes]] | None = None) -> None:
    root.mkdir()
    (root / "data").mkdir()
    (root / "override").mkdir()
    if resources is None:
        resources = [("SYNTH001", make_itm())]
    bif = make_biff(resources, reverse_table=True)
    (root / "data" / "items.bif").write_bytes(bif)
    (root / "chitin.key").write_bytes(make_key(resources, len(bif)))
    (root / "dialog.tlk").write_bytes(make_tlk(["Blade", "Épée de test"]))


def mark_eet(root: Path) -> None:
    """Make a synthetic game EET, as WeiDU's GAME_IS tells it (override/EET.flag).

    Like a real EET it keeps its rename lists (EET/lib/pcu_dict.tph); they rename
    what Chriz Item Randomiser's own copy of them renames.
    """
    from flir_config.game_names import _eet_tables

    (root / "override" / "EET.flag").write_bytes(b"")
    write_eet_names(root, _eet_tables())


def write_eet_names(root: Path, tables: dict[str, dict[str, str]]) -> Path:
    """Write EET's rename lists in its own format, one array per resource type."""
    lib = root / "EET" / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    blocks = []
    for extension, entries in sorted(tables.items()):
        rows = "".join(f'\t"{old.upper()}"\t=>\t"{new.upper()}"\n' for old, new in sorted(entries.items()))
        blocks.append(f"ACTION_DEFINE_ASSOCIATIVE_ARRAY remapped_{extension} BEGIN\n{rows}END\n")
    path = lib / "pcu_dict.tph"
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def snapshot(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


class ResourceScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="flir-resource-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "game"
        make_game(self.root)

    def test_biff_metadata_names_and_read_only(self) -> None:
        before = snapshot(self.root)
        result = scan_game(self.root)
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(result["schema"], "flir-item-catalog-v1")
        self.assertEqual(result["game_root"], str(self.root.resolve()))
        self.assertEqual(result["language"], "default")
        item = result["items"][0]
        self.assertEqual(item["resref"], "synth001")
        self.assertEqual(item["name"], "Épée de test")
        self.assertEqual(item["unidentified_name"], "Blade")
        self.assertEqual(item["resource_origin"], "biff")
        self.assertEqual((item["category"], item["flags"], item["price"], item["stack_size"], item["enchantment"], item["proficiency"]), (16, 0x44, 300, 1, 2, 101))
        self.assertEqual(item["sha256"], hashlib.sha256(make_itm()).hexdigest())

    def _assert_change_after_archive_read(self, change) -> None:
        original = resource_module._biff_items
        def read_then_change(path, requested):
            result = original(path, requested)
            change()
            return result
        with patch.object(resource_module, "_biff_items", side_effect=read_then_change) as reader:
            with self.assertRaisesRegex(ScanError, "changed.*scanning"):
                scan_game(self.root)
            reader.assert_called_once()

    def test_override_appearing_after_archive_read_fails_without_retry(self) -> None:
        self._assert_change_after_archive_read(
            lambda: (self.root / "override" / "synth001.itm").write_bytes(make_itm(price=999))
        )

    def test_new_override_directory_after_enumeration_fails(self) -> None:
        (self.root / "override").rmdir()
        def change():
            (self.root / "override").mkdir()
            (self.root / "override" / "synth001.itm").write_bytes(make_itm(price=999))
        self._assert_change_after_archive_read(change)

    def test_key_tlk_or_used_archive_modification_during_scan_fails(self) -> None:
        for filename in ("chitin.key", "dialog.tlk", "data/items.bif"):
            with self.subTest(filename=filename):
                path = self.root / filename
                original = path.read_bytes()
                self._assert_change_after_archive_read(lambda: path.write_bytes(original + b"\0"))
                path.write_bytes(original)

    def test_effective_override_changed_removed_or_replaced_after_read_fails(self) -> None:
        target = self.root / "override" / "synth001.itm"
        original_reader = resource_module._read_file
        for action in ("modify", "remove", "replace"):
            with self.subTest(action=action):
                target.write_bytes(make_itm())
                def read_then_change(path):
                    result = original_reader(path)
                    if path == target:
                        before = path.stat()
                        if action in ("remove", "replace"):
                            path.unlink()
                        if action != "remove":
                            path.write_bytes(make_itm(price=999))
                            # Avoid dependence on the filesystem timestamp
                            # granularity for a same-size test mutation.
                            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
                    return result
                with patch.object(resource_module, "_read_file", side_effect=read_then_change):
                    with self.assertRaisesRegex(ScanError, "changed.*scanning"):
                        scan_game(self.root)

    def test_new_language_choice_during_scan_fails(self) -> None:
        (self.root / "dialog.tlk").unlink()
        english = self.root / "lang" / "en_US"
        english.mkdir(parents=True)
        (english / "dialog.tlk").write_bytes(make_tlk(["Blade", "English"]))
        def change():
            other = self.root / "lang" / "de_DE"
            other.mkdir()
            (other / "dialog.tlk").write_bytes(make_tlk(["Klinge", "Deutsch"]))
        self._assert_change_after_archive_read(change)

    def test_new_dlc_archive_after_dlc_inspection_fails(self) -> None:
        dlc = self.root / "dlc"
        dlc.mkdir()
        self._assert_change_after_archive_read(lambda: (dlc / "new.zip").write_bytes(b"new content"))

    def test_replacing_archive_path_directory_during_scan_fails(self) -> None:
        def change():
            data = self.root / "data"
            data.rename(self.root / "previous-data")
            data.mkdir()
            shutil.copy2(self.root / "previous-data" / "items.bif", data / "items.bif")
        self._assert_change_after_archive_read(change)

    def test_override_precedence_extra_item_and_sorted_identity(self) -> None:
        (self.root / "override" / "sYnTh001.ItM").write_bytes(make_itm(price=800))
        (self.root / "override" / "AAA#NEW.ITM").write_bytes(make_itm(0xFFFFFFFF, 0xFFFFFFFF))
        result = scan_game(self.root)
        self.assertEqual([item["resref"] for item in result["items"]], ["aaa#new", "synth001"])
        self.assertEqual(result["items"][0]["name"], "aaa#new")
        self.assertEqual(result["items"][1]["price"], 800)
        self.assertEqual(result["items"][1]["resource_origin"], "override")

    def test_long_override_names_are_left_out_and_named(self) -> None:
        fingerprint = scan_game(self.root)["fingerprint"]
        override = self.root / "override"
        for name in ("dw#marker_file.itm", "synth0012.itm", "synth001 - копия.itm"):
            (override / name).write_bytes(make_itm(price=1))
        result = scan_game(self.root)
        self.assertEqual([(item["resref"], item["price"]) for item in result["items"]], [("synth001", 300)])
        self.assertEqual(result["fingerprint"], fingerprint)
        self.assertEqual(result["warnings"], [
            "Override files with names longer than eight characters were left out (resource "
            "names have at most eight): dw#marker_file.itm, synth001 - копия.itm, synth0012.itm."])
        # A short name that is not ASCII stays ambiguous: the game's code page decides it.
        (override / "größe.itm").write_bytes(make_itm())
        with self.assertRaisesRegex(ScanError, "not ASCII"):
            scan_game(self.root)

    def test_uses_locator_not_entry_index_or_table_order(self) -> None:
        resources = [("SECOND", make_itm(price=200)), ("FIRST", make_itm(price=100))]
        bif = make_biff(resources, reverse_table=True)
        (self.root / "data" / "items.bif").write_bytes(bif)
        (self.root / "chitin.key").write_bytes(make_key(resources, len(bif)))
        self.assertEqual([(item["resref"], item["price"]) for item in scan_game(self.root)["items"]], [("first", 100), ("second", 200)])

    def test_high_locator_bits_select_the_correct_archive(self) -> None:
        second = make_biff([("OTHER", make_itm(price=900))])
        (self.root / "data" / "other.bif").write_bytes(second)
        first = (self.root / "data" / "items.bif").read_bytes()
        names = (b"data/items.bif\0", b"data/other.bif\0")
        resource_offset = 0x18 + 24
        filename_offset = resource_offset + 28
        key = (b"KEY V1  " + struct.pack("<IIII", 2, 2, 0x18, resource_offset)
               + struct.pack("<IIHH", len(first), filename_offset, len(names[0]), 1)
               + struct.pack("<IIHH", len(second), filename_offset + len(names[0]), len(names[1]), 1)
               + struct.pack("<8sHI", b"SYNTH001", 1005, 7)
               + struct.pack("<8sHI", b"OTHER", 1005, (1 << 20) | 7)
               + b"".join(names))
        (self.root / "chitin.key").write_bytes(key)
        self.assertEqual([(row["resref"], row["price"]) for row in scan_game(self.root)["items"]], [("other", 900), ("synth001", 300)])

    def test_distinct_key_resrefs_can_alias_the_same_archive_record(self) -> None:
        records = [("SYNTH001", make_itm()), ("ALIAS001", make_itm())]
        key = bytearray(make_key(records, (self.root / "data" / "items.bif").stat().st_size))
        struct.pack_into("<I", key, 0x24 + 14 + 10, 7)
        (self.root / "chitin.key").write_bytes(key)
        original_reader = resource_module._biff_items
        with patch.object(resource_module, "_biff_items", wraps=original_reader) as reader:
            items = scan_game(self.root)["items"]
            reader.assert_called_once()
            self.assertEqual(len(reader.call_args.args[1]), 1)
        self.assertEqual([item["resref"] for item in items], ["alias001", "synth001"])
        self.assertEqual(items[0]["sha256"], items[1]["sha256"])

    def test_biff_locator_and_type_disagreement_fail(self) -> None:
        path = self.root / "data" / "items.bif"
        original = path.read_bytes()
        table_offset = struct.unpack_from("<I", original, 0x10)[0]
        for offset, value, fmt, message in (
            (table_offset, 8, "<I", "locator is missing"),
            (table_offset + 12, 1010, "<H", "type disagrees"),
            (table_offset + 4, 0xFFFF, "<I", "resource data"),
        ):
            with self.subTest(message=message):
                data = bytearray(original)
                struct.pack_into(fmt, data, offset, value)
                path.write_bytes(data)
                with self.assertRaisesRegex(ScanError, message):
                    scan_game(self.root)
        path.write_bytes(original)

    def test_fingerprint_ignores_packaging_and_game_root(self) -> None:
        original = scan_game(self.root)
        copy = self.root.parent / "copy"
        shutil.copytree(self.root, copy)
        (copy / "override" / "SYNTH001.ITM").write_bytes(make_itm())
        self.assertEqual(original["fingerprint"], scan_game(copy)["fingerprint"])
        (copy / "override" / "SYNTH001.ITM").write_bytes(make_itm(price=700))
        self.assertNotEqual(original["fingerprint"], scan_game(copy)["fingerprint"])

    def test_fingerprint_includes_resolved_search_text(self) -> None:
        original = scan_game(self.root)["fingerprint"]
        (self.root / "dialog.tlk").write_bytes(make_tlk(["Blade", "Changed name"]))
        self.assertNotEqual(original, scan_game(self.root)["fingerprint"])

    def test_public_fingerprint_helper_uses_semantic_whitelist(self) -> None:
        result = scan_game(self.root)
        original = result["fingerprint"]
        result["game_root"] = "irrelevant"
        result["warnings"] = ["irrelevant"]
        result["items"][0]["resource_origin"] = "override"
        result["items"][0]["packaging_detail"] = "irrelevant"
        self.assertEqual(original, catalog_fingerprint(result))
        result["items"][0]["name"] = "Edited"
        self.assertNotEqual(original, catalog_fingerprint(result))
        del result["items"][0]["sha256"]
        with self.assertRaises(ScanError):
            catalog_fingerprint(result)

    def test_name_fallback_and_missing_strref_warning(self) -> None:
        (self.root / "override" / "synth001.itm").write_bytes(make_itm(0, 900))
        result = scan_game(self.root)
        self.assertEqual(result["items"][0]["name"], "Blade")
        self.assertIsNone(result["items"][0]["identified_name"])
        self.assertTrue(result["warnings"])

    def test_explicit_or_sole_language(self) -> None:
        language = self.root / "lang" / "en_US"
        language.mkdir(parents=True)
        (language / "DIALOG.TLK").write_bytes(make_tlk(["Unidentified", "English"]))
        self.assertEqual(scan_game(self.root, language="EN_us")["items"][0]["name"], "English")
        (self.root / "dialog.tlk").unlink()
        self.assertEqual(scan_game(self.root)["language"], "en_us")
        other = self.root / "lang" / "de_DE"
        other.mkdir()
        (other / "dialog.tlk").write_bytes(make_tlk(["Klinge", "Deutsch"]))
        with self.assertRaisesRegex(ScanError, "explicit language"):
            scan_game(self.root)
        with self.assertRaises(ScanError):
            scan_game(self.root, language="../en_US")

    def test_malformed_headers_and_tables_fail_without_writes(self) -> None:
        mutations = [
            ("chitin.key", 0, b"BAD!"),
            ("chitin.key", 12, struct.pack("<I", 0xFFFFFFFF)),
            ("data/items.bif", 0x10, struct.pack("<I", 0xFFFFFFF0)),
            ("dialog.tlk", 0xE, struct.pack("<I", 0xFFFFFFF0)),
            ("dialog.tlk", 0x12 + 0x16, struct.pack("<I", 0xFFFFFFF0)),
        ]
        for filename, offset, patch in mutations:
            with self.subTest(filename=filename, offset=offset):
                path = self.root / filename
                original = path.read_bytes()
                mutated = bytearray(original)
                mutated[offset:offset + len(patch)] = patch
                path.write_bytes(mutated)
                before = snapshot(self.root)
                with self.assertRaises(ScanError):
                    scan_game(self.root)
                self.assertEqual(snapshot(self.root), before)
                path.write_bytes(original)

    def test_truncated_itm_and_ability_data_fail(self) -> None:
        path = self.root / "override" / "synth001.itm"
        for payload in (b"ITM V1  ", make_itm()[:0x71]):
            path.write_bytes(payload)
            with self.assertRaises(ScanError):
                scan_game(self.root)
        malformed = bytearray(make_itm())
        struct.pack_into("<IH", malformed, 0x64, 0x72, 1)
        path.write_bytes(malformed)
        with self.assertRaisesRegex(ScanError, "ability table"):
            scan_game(self.root)

    def test_itm_effect_ranges_follow_indices_and_are_checked(self) -> None:
        # Equipping effects first; ability effects index 1, abilities after
        # both effect records. This valid layout must not be rejected.
        item = bytearray(make_itm() + b"\0" * (2 * 0x30 + 0x38))
        ability_offset = 0x72 + 2 * 0x30
        struct.pack_into("<IH", item, 0x64, ability_offset, 1)
        struct.pack_into("<IHH", item, 0x6A, 0x72, 0, 1)
        struct.pack_into("<HH", item, ability_offset + 0x1E, 1, 1)
        path = self.root / "override" / "synth001.itm"
        path.write_bytes(item)
        self.assertEqual(len(scan_game(self.root)["items"]), 1)
        struct.pack_into("<H", item, ability_offset + 0x20, 1000)
        path.write_bytes(item)
        with self.assertRaisesRegex(ScanError, "ITM effects"):
            scan_game(self.root)

    def test_invalid_utf8_name_fails_explicitly(self) -> None:
        tlk = bytearray(make_tlk(["Blade", "Name"]))
        tlk[-1] = 0xFF
        (self.root / "dialog.tlk").write_bytes(tlk)
        with self.assertRaisesRegex(ScanError, "UTF-8"):
            scan_game(self.root)

    def test_compressed_biff_fails_clearly(self) -> None:
        for signature in (b"BIFCV1.0", b"BIF V1.0"):
            (self.root / "data" / "items.bif").write_bytes(signature + b"\0" * 20)
            with self.assertRaisesRegex(ScanError, "Compressed BIF"):
                scan_game(self.root)

    def test_unmerged_dlc_is_rejected_without_reading_archive_members(self) -> None:
        archive = self.root / "package.zip"
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("chitin.key", b"not opened")
        with self.assertRaisesRegex(ScanError, "KEY-bearing ZIP"):
            scan_game(self.root)
        archive.unlink()
        dlc = self.root / "dlc"
        dlc.mkdir()
        (dlc / "package.zip").write_bytes(b"nonempty")
        with self.assertRaisesRegex(ScanError, "Unmerged DLC ZIP"):
            scan_game(self.root)

    def test_ordinary_mod_archive_is_not_treated_as_dlc(self) -> None:
        with zipfile.ZipFile(self.root / "mod.zip", "w") as package:
            package.writestr("mod/readme.txt", "not effective game resources")
        self.assertEqual(len(scan_game(self.root)["items"]), 1)

    def test_unsafe_key_paths_fail(self) -> None:
        for name in (b"../outside.bif\0", b"C:\\outside.bif\0", b"/outside.bif\0", b"data/../items.bif\0"):
            (self.root / "chitin.key").write_bytes(make_key([("SYNTH001", make_itm())], 0, filename=name))
            with self.assertRaisesRegex(ScanError, "unsafe archive path"):
                scan_game(self.root)

    def test_duplicate_key_items_fail_case_insensitively(self) -> None:
        resources = [("DUPLICAT", make_itm()), ("duplicat", make_itm())]
        (self.root / "chitin.key").write_bytes(make_key(resources, 0))
        with self.assertRaisesRegex(ScanError, "Duplicate case-insensitive KEY"):
            scan_game(self.root)

    def test_case_collision_override_detection(self) -> None:
        # Simulate the two directory entries returned on a case-sensitive FS;
        # Windows cannot physically create this ambiguity on ordinary NTFS.
        from unittest.mock import patch
        override = self.root / "override"
        first, second = override / "dual.itm", override / "DUAL.ITM"
        first.write_bytes(make_itm())
        original = Path.iterdir
        def entries(path):
            return iter((first, second)) if path == override else original(path)
        with patch.object(Path, "iterdir", entries):
            with self.assertRaisesRegex(ScanError, "Duplicate case-insensitive override"):
                scan_game(self.root)

    def test_symlink_escape_is_rejected(self) -> None:
        outside = self.root.parent / "outside.itm"
        outside.write_bytes(make_itm())
        link = self.root / "override" / "escape.itm"
        try:
            link.symlink_to(outside)
        except OSError:
            # The same containment check is independently exercised without
            # requiring Developer Mode or elevated Windows symlink privileges.
            from unittest.mock import patch
            original = Path.resolve
            def resolve(path, *args, **kwargs):
                if path == link:
                    return original(outside, *args, **kwargs)
                return original(path, *args, **kwargs)
            link.write_bytes(make_itm())
            with patch.object(Path, "resolve", resolve):
                with self.assertRaisesRegex(ScanError, "outside"):
                    scan_game(self.root)
        else:
            with self.assertRaisesRegex(ScanError, "outside"):
                scan_game(self.root)

    def test_missing_biff_is_irrelevant_when_every_item_overridden(self) -> None:
        (self.root / "override" / "synth001.itm").write_bytes(make_itm())
        (self.root / "data" / "items.bif").unlink()
        self.assertEqual(scan_game(self.root)["items"][0]["resource_origin"], "override")


if __name__ == "__main__":
    unittest.main()
