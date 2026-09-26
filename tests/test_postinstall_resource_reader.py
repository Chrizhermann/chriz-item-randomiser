"""Synthetic world-resource access and shared-snapshot tests; no real games."""

from __future__ import annotations

import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from flir_config import resources as resource_module
from flir_config.resources import GameResources, RESOURCE_TYPES, ScanError


def biff(records: list[tuple[int, int, bytes]]) -> bytes:
    """Store payloads before a reversed table, using explicit sparse locators."""
    payload = b"".join(data for _locator, _kind, data in records)
    entries, offset = [], 0x14
    for locator, kind, data in records:
        entries.append(struct.pack("<IIIHH", locator, offset, len(data), kind, 0))
        offset += len(data)
    return b"BIFFV1  " + struct.pack("<III", len(records), 0, offset) + payload + b"".join(reversed(entries))


def key(records: list[tuple[str, int, int]], names: list[bytes] | None = None) -> bytes:
    if names is None:
        names = [b"data/world.bif\0"]
    resource_offset = 0x18 + 12 * len(names)
    filename_offset = resource_offset + 14 * len(records)
    archives = bytearray()
    for name in names:
        archives += struct.pack("<IIHH", 0, filename_offset, len(name), 1)
        filename_offset += len(name)
    return (b"KEY V1  " + struct.pack("<IIII", len(names), len(records), 0x18, resource_offset)
            + archives + b"".join(struct.pack("<8sHI", name.encode("ascii"), kind, locator)
                                   for name, kind, locator in records) + b"".join(names))


class ResourceReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="flir-reader-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "game"
        self.root.mkdir()
        (self.root / "data").mkdir()
        (self.root / "override").mkdir()
        self.records = [("ACTOR", 1009, 17), ("AREA", 1010, 3), ("SHOP", 1014, 9),
                        ("ACTOR", 1007, 23), ("DIALOG", 1011, 2), ("SWORD", 1005, 11)]
        self.payloads = {("cre", "actor"): b"CRE V1.0synthetic", ("are", "area"): b"ARE synthetic",
                         ("sto", "shop"): b"STO synthetic", ("bcs", "actor"): b"SC\nCR\nSC",
                         ("dlg", "dialog"): b"DLG synthetic", ("itm", "sword"): b"ITM synthetic"}
        extensions = {kind: ext for ext, kind in RESOURCE_TYPES.items()}
        (self.root / "data" / "world.bif").write_bytes(biff([
            (locator, kind, self.payloads[extensions[kind], name.lower()]) for name, kind, locator in self.records
        ]))
        (self.root / "chitin.key").write_bytes(key(self.records))

    def snapshot(self) -> dict[str, str]:
        return {str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.root.rglob("*") if path.is_file()}

    def test_canonical_type_codes(self) -> None:
        self.assertEqual(RESOURCE_TYPES, {"itm": 0x3ED, "bcs": 0x3EF, "cre": 0x3F1,
                                          "are": 0x3F2, "dlg": 0x3F3, "sto": 0x3F6})

    def test_all_selected_types_are_separate_case_insensitive_and_read_only(self) -> None:
        before = self.snapshot()
        with GameResources(self.root, RESOURCE_TYPES) as game:
            self.assertEqual(game.root, self.root.resolve())
            for (extension, name), payload in self.payloads.items():
                self.assertEqual(game.names(extension.upper()), [name])
                self.assertEqual(game.read(extension.upper(), name.upper()), payload)
                self.assertEqual(game.origin(extension, name), "biff")
        self.assertEqual(self.snapshot(), before)

    def test_override_precedence_extra_resources_and_sorted_names(self) -> None:
        override = self.root / "override"
        (override / "AcToR.CrE").write_bytes(b"modified creature")
        (override / "Z#new.cre").write_bytes(b"new creature")
        (override / "AAA.cre").write_bytes(b"early creature")
        with GameResources(self.root, {"cre": 1009, "bcs": 1007}) as game:
            self.assertEqual(game.names("cre"), ["aaa", "actor", "z#new"])
            self.assertEqual(game.read("cre", "ACTOR"), b"modified creature")
            self.assertEqual(game.origin("cre", "actor"), "override")
            self.assertEqual(game.read("bcs", "actor"), self.payloads["bcs", "actor"])
            with self.assertRaisesRegex(ScanError, "not selected"):
                game.names("sto")

    def test_internal_spaces_keep_exact_names_in_key_override_and_lookup(self) -> None:
        names = [("MONKTU 8", 1011, 2), ("TEST CRE", 1009, 17), ("TEST ITM", 1005, 11)]
        (self.root / "chitin.key").write_bytes(key(names))
        with GameResources(self.root, RESOURCE_TYPES) as game:
            for extension, name in (("dlg", "monktu 8"), ("cre", "test cre"), ("itm", "test itm")):
                self.assertEqual(game.names(extension), [name])
                self.assertEqual(game.origin(extension, name.upper()), "biff")
                self.assertTrue(game.read(extension, name.upper()))
        (self.root / "override" / "MONKTU 8.DLG").write_bytes(b"effective dialogue")
        before = self.snapshot()
        with GameResources(self.root, RESOURCE_TYPES) as game:
            self.assertEqual(game.read("dlg", "monktu 8"), b"effective dialogue")
            self.assertEqual(game.origin("dlg", "monktu 8"), "override")
        self.assertEqual(self.snapshot(), before)

    def test_key_and_override_reject_edge_spaces_controls_and_path_metacharacters(self) -> None:
        invalid = (" BAD", "BAD ", " ", ".", "..", "A/B", "A\\B", "A:B", "A*B", "A?B",
                   'A"B', "A<B", "A>B", "A|B", "A\tB", "A\nB", "A\x7fB")
        for name in invalid:
            with self.subTest(name=name):
                (self.root / "chitin.key").write_bytes(key([(name, 1009, 17)]))
                with self.assertRaisesRegex(ScanError, "Invalid resource reference"):
                    with GameResources(self.root, {"cre": 1009}):
                        pass
                # Mock unsafe directory-entry names so this safety test does
                # not depend on the host permitting them as real filenames.
                entry = unittest.mock.Mock()
                entry.suffix, entry.stem = ".cre", name
                with patch.object(Path, "iterdir", return_value=iter([entry])), \
                        patch.object(resource_module, "_child", return_value=self.root / "override"):
                    with self.assertRaisesRegex(ScanError, "Invalid resource reference"):
                        resource_module._override_resources(self.root, {"cre": 1009}, unittest.mock.Mock())


    def test_key_aliases_share_locator_but_keep_names_and_index_is_cached(self) -> None:
        (self.root / "chitin.key").write_bytes(key(self.records + [("ALIAS", 1009, 17)]))
        with patch.object(resource_module, "_biff_index", wraps=resource_module._biff_index) as index:
            with GameResources(self.root, RESOURCE_TYPES) as game:
                index.assert_not_called()
                self.assertEqual(game.names("cre"), ["actor", "alias"])
                for extension in RESOURCE_TYPES:
                    for name in game.names(extension):
                        self.assertIsInstance(game.read(extension, name), bytes)
                self.assertEqual(game.read("cre", "actor"), game.read("cre", "alias"))
                index.assert_called_once()

    def test_archive_index_bits_choose_correct_file_and_index_caches_by_path(self) -> None:
        (self.root / "data" / "other.bif").write_bytes(biff([(17, 1009, b"second actor")]))
        (self.root / "chitin.key").write_bytes(key([
            ("ACTOR", 1009, 17), ("SECOND", 1009, (1 << 20) | 17),
            ("ALIAS", 1009, (2 << 20) | 17),
        ], [b"data/world.bif\0", b"data/other.bif\0", b"DATA/WORLD.BIF\0"]))
        with patch.object(resource_module, "_biff_index", wraps=resource_module._biff_index) as index:
            with GameResources(self.root, {"cre": 1009}) as game:
                self.assertEqual(game.read("cre", "second"), b"second actor")
                self.assertEqual(game.read("cre", "actor"), game.read("cre", "alias"))
                self.assertEqual(index.call_count, 2)

    def test_missing_unneeded_archive_is_not_opened(self) -> None:
        (self.root / "override" / "actor.cre").write_bytes(b"effective")
        (self.root / "data" / "world.bif").unlink()
        with GameResources(self.root, {"cre": 1009}) as game:
            self.assertEqual(game.read("cre", "actor"), b"effective")

    def test_unselected_resource_payloads_do_not_get_read(self) -> None:
        # The caller receives opaque bytes; there is no TLK or payload-format
        # dependency, and unrelated file types do not become effective inputs.
        (self.root / "override" / "too-long-unselected.itm").write_bytes(b"invalid item")
        with patch.object(resource_module, "_read_at", wraps=resource_module._read_at) as read:
            with GameResources(self.root, {"bcs": 1007}) as game:
                self.assertEqual(game.read("bcs", "actor"), self.payloads["bcs", "actor"])
                self.assertEqual([call.args[-1] for call in read.call_args_list],
                                 ["BIFF header", "BIFF file table", "BCS data"])

    def test_snapshot_detects_change_during_caller_parse_after_last_read(self) -> None:
        with self.assertRaisesRegex(ScanError, "changed.*scanning"):
            with GameResources(self.root, {"cre": 1009}) as game:
                game.read("cre", "actor")
                (self.root / "override" / "actor.cre").write_bytes(b"new override")

    def test_snapshot_includes_archives_before_the_first_caller_read(self) -> None:
        path = self.root / "data" / "world.bif"
        with self.assertRaisesRegex(ScanError, "changed.*scanning"):
            with GameResources(self.root, {"cre": 1009}) as game:
                path.write_bytes(path.read_bytes() + b"concurrent edit")
                self.assertEqual(game.read("cre", "actor"), self.payloads["cre", "actor"])

    def test_snapshot_includes_overrides_not_yet_read(self) -> None:
        path = self.root / "override" / "actor.cre"
        path.write_bytes(b"before")
        with self.assertRaisesRegex(ScanError, "changed.*scanning"):
            with GameResources(self.root, {"cre": 1009}) as game:
                path.write_bytes(b"different afterwards")
                self.assertEqual(game.read("cre", "actor"), b"different afterwards")

    def test_snapshot_detects_key_changes_even_if_only_names_are_requested(self) -> None:
        path = self.root / "chitin.key"
        with self.assertRaisesRegex(ScanError, "changed.*scanning"):
            with GameResources(self.root, {"cre": 1009}) as game:
                self.assertEqual(game.names("cre"), ["actor"])
                path.write_bytes(path.read_bytes() + b"changed")

    def test_snapshot_detects_new_override_directory_and_dlc(self) -> None:
        (self.root / "override").rmdir()
        with self.assertRaisesRegex(ScanError, "changed.*scanning"):
            with GameResources(self.root, {"cre": 1009}) as game:
                game.read("cre", "actor")
                (self.root / "override").mkdir()
        (self.root / "dlc").mkdir()
        with self.assertRaisesRegex(ScanError, "changed.*scanning"):
            with GameResources(self.root, {"cre": 1009}) as game:
                game.read("cre", "actor")
                (self.root / "dlc" / "new.zip").write_bytes(b"new package")

    def test_caller_parse_error_propagates_and_context_cannot_be_reused(self) -> None:
        game = GameResources(self.root, {"cre": 1009})
        with self.assertRaisesRegex(ScanError, "inside their scan context"):
            game.names("cre")
        failure = ValueError("caller parser failed")
        with self.assertRaises(ValueError) as caught:
            with game:
                game.read("cre", "actor")
                raise failure
        self.assertIs(caught.exception, failure)
        with self.assertRaisesRegex(ScanError, "inside their scan context"):
            game.read("cre", "actor")
        with self.assertRaisesRegex(ScanError, "more than once"):
            with game:
                pass

    def test_missing_selected_archive_and_resource_have_clear_errors(self) -> None:
        with GameResources(self.root, {"cre": 1009}) as game:
            with self.assertRaisesRegex(ScanError, "resource is missing"):
                game.read("cre", "absent")
        (self.root / "data" / "world.bif").unlink()
        with self.assertRaisesRegex(ScanError, "resource is missing"):
            with GameResources(self.root, {"cre": 1009}):
                pass

    def test_invalid_arguments_fail_as_scan_errors(self) -> None:
        for mapping in ({}, {".cre": 1009}, {"cre": True}, {"cre": -1}, {"cre": 65536},
                        {"CRE": 1009, "cre": 1009}, {"cre": 1009, "actor": 1009}, {"tis": 1003}):
            with self.subTest(mapping=mapping), self.assertRaises(ScanError):
                GameResources(self.root, mapping)
        with GameResources(self.root, {"CRE": 1009}) as game:
            for name in ("../actor", "actor.cre", "actor\0", "", "é", None):
                with self.subTest(name=name), self.assertRaises(ScanError):
                    game.read("cre", name)

    def test_compressed_archives_rejected_when_read(self) -> None:
        for signature in (b"BIFCV1.0", b"BIF V1.0"):
            with self.subTest(signature=signature):
                (self.root / "data" / "world.bif").write_bytes(signature + bytes(20))
                with self.assertRaisesRegex(ScanError, "Compressed BIF"):
                    with GameResources(self.root, {"cre": 1009}) as game:
                        game.read("cre", "actor")

    def test_unmerged_dlc_rejected_and_normal_mod_zip_is_allowed(self) -> None:
        archive = self.root / "mod.zip"
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("mod/readme.txt", "ordinary mod archive")
        with GameResources(self.root, {"cre": 1009}) as game:
            self.assertEqual(game.names("cre"), ["actor"])
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("chitin.key", "DLC resource layer")
        with self.assertRaisesRegex(ScanError, "KEY-bearing ZIP"):
            with GameResources(self.root, {"cre": 1009}):
                pass

    def test_duplicate_key_names_fail_per_type_but_same_name_different_types_is_valid(self) -> None:
        # The normal fixture already shares actor.cre and actor.bcs.
        (self.root / "chitin.key").write_bytes(key(self.records + [("actor", 1009, 99)]))
        with self.assertRaisesRegex(ScanError, "Duplicate case-insensitive KEY"):
            with GameResources(self.root, RESOURCE_TYPES):
                pass

    def test_duplicate_override_names_fail_case_insensitively(self) -> None:
        override = self.root / "override"
        first, second = override / "actor.cre", override / "ACTOR.CRE"
        first.write_bytes(b"actor")
        original = Path.iterdir
        def entries(path):
            return iter((first, second)) if path == override else original(path)
        with patch.object(Path, "iterdir", entries):
            with self.assertRaisesRegex(ScanError, "Duplicate case-insensitive override"):
                with GameResources(self.root, {"cre": 1009}):
                    pass

    def test_unsafe_archive_paths_and_invalid_override_names_fail(self) -> None:
        for filename in (b"../outside.bif\0", b"C:\\outside.bif\0", b"/outside.bif\0"):
            with self.subTest(filename=filename):
                (self.root / "chitin.key").write_bytes(key(self.records, [filename]))
                with self.assertRaisesRegex(ScanError, "unsafe archive path"):
                    with GameResources(self.root, {"cre": 1009}):
                        pass
        (self.root / "chitin.key").write_bytes(key(self.records))
        (self.root / "override" / "too-long-name.cre").write_bytes(b"creature")
        with self.assertRaisesRegex(ScanError, "exceeds eight"):
            with GameResources(self.root, {"cre": 1009}):
                pass

    def test_symlink_escape_is_rejected_without_platform_privileges(self) -> None:
        outside = self.root.parent / "outside.cre"
        outside.write_bytes(b"outside")
        link = self.root / "override" / "actor.cre"
        link.write_bytes(b"placeholder")
        original = Path.resolve
        def resolve(path, *args, **kwargs):
            return original(outside, *args, **kwargs) if path == link else original(path, *args, **kwargs)
        with patch.object(Path, "resolve", resolve):
            with self.assertRaisesRegex(ScanError, "outside"):
                with GameResources(self.root, {"cre": 1009}):
                    pass

    def test_archive_locator_type_and_data_bounds_are_checked(self) -> None:
        path = self.root / "data" / "world.bif"
        original = path.read_bytes()
        table_offset = struct.unpack_from("<I", original, 16)[0]
        # actor is the last entry because the fixture reverses the BIFF table.
        base = table_offset + 5 * 16
        for offset, value, fmt, message in (
            (base, 101, "<I", "locator is missing"),
            (base + 12, 1014, "<H", "type disagrees"),
            (base + 4, 0xFFFFFFFE, "<I", "resource data"),
            (base + 4, table_offset, "<I", "overlaps"),
            (base, 11, "<I", "Duplicate resource locator"),
        ):
            with self.subTest(message=message):
                data = bytearray(original)
                struct.pack_into(fmt, data, offset, value)
                path.write_bytes(data)
                with self.assertRaisesRegex(ScanError, message):
                    with GameResources(self.root, {"cre": 1009}) as game:
                        game.read("cre", "actor")
        path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
