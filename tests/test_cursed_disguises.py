"""Component 570 assets and campaign choices, using only synthetic resources."""
from copy import deepcopy
import hashlib
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from flir_config.cursed_disguises import (cursed_disguise_indices, cursed_disguise_spec,
    item_revisions_installed, prepare_cursed_disguises, validate_cursed_disguise_spec)
from flir_config.profiles import ProfileError
from flir_config.quest_options import _texts
from flir_config.resources import _Tlk
from test_original_options import ALL_DISGUISE_ITEMS
from test_postinstall_resources import make_itm, make_tlk


def resources():
    result = {}
    for index, name in enumerate(sorted(ALL_DISGUISE_ITEMS), 1):
        data = bytearray(make_itm(index, index + 100, price=1000 + index))
        data[0x3A:0x42] = f"I{index:07}".encode()
        data[0x58:0x60] = f"C{index:07}".encode()
        struct.pack_into("<III", data, 0x4C, 20 + index, index + 200, index + 300)
        # One ability and three global effects: only the original plate's
        # opcode-0/base-AC effect is owned by component 570.
        struct.pack_into("<IHIHH", data, 0x64, 0x72, 1, 0xAA, 0, 3)
        data.extend(bytes([index]) * 0x38 + bytes(3 * 0x30))
        for at, (opcode, mode) in enumerate(((0, 16), (0, 1), (17, 16))):
            struct.pack_into("<H", data, 0xAA + at * 0x30, opcode)
            struct.pack_into("<II", data, 0xAE + at * 0x30, 9, mode)
        result[name] = bytes(data)
    return result


def spec_for(data, **kwargs):
    return cursed_disguise_spec({key: hashlib.sha256(value).hexdigest()
                                for key, value in data.items()}, **kwargs)


class CursedDisguiseTests(unittest.TestCase):
    def test_all_eight_items_and_exact_original_face_weights_are_frozen(self):
        data = resources()
        spec = spec_for(data)
        self.assertEqual([(r["source"], len(r["variants"])) for r in spec["choices"]], [
            ("brac08", 4), ("brac11", 7), ("leat06", 3), ("plat17", 3),
            ("ring04", 6), ("ring23", 6), ("sper03", 2), ("sw2h03", 2)])
        for name in ("brac13", "brac14", "brac15", "ring31", "sper05", "sw2h11"):
            del data[name]
        fallback = spec_for(data)
        validate_cursed_disguise_spec(fallback)
        by_name = {r["source"]: [v["donor"] for v in r["variants"]] for r in fallback["choices"]}
        self.assertEqual(by_name["brac11"], ["brac01", "brac02", "brac03"] + ["brac04"] * 4)
        self.assertEqual(by_name["ring23"].count("ring08"), 3)
        self.assertEqual(by_name["sper03"], ["sper02", "sper02"])
        changed = deepcopy(spec)
        changed["choices"][0]["variants"][0]["donor"] = "brac01"
        with self.assertRaisesRegex(ProfileError, "differs from original"):
            validate_cursed_disguise_spec(changed)

    def test_assets_copy_only_original_owned_bytes_and_exact_ee_text(self):
        data = resources()
        spec = spec_for(data)
        fixture, files, tlk = prepare_cursed_disguises(spec, data, make_tlk(["Existing text"]))
        self.assertEqual(len(files), 41)  # eight originals plus 33 die faces
        self.assertEqual(fixture["algorithm"], "park-miller-cursed-v1")
        strings = _Tlk(tlk)
        authored = _texts("languages/bgee/english/in-game.tra")
        self.assertEqual(strings.text(0, set()), "Existing text")
        for row, item in zip(spec["choices"], fixture["items"]):
            source = row["source"]
            self.assertEqual(files[source + ".original.itm"], data[source])
            self.assertEqual(item["resref"], source)
            for face_number, (face, metadata) in enumerate(zip(row["variants"], item["variants"]), 1):
                variant = files[f"{source}.{face_number}.itm"]
                self.assertEqual(len(variant), len(data[source]))
                self.assertEqual(metadata["sha256"], hashlib.sha256(variant).hexdigest())
                expected = bytearray(data[source])
                for offset, size in ((0x3A, 8), (0x50, 4), (0x58, 8)):
                    expected[offset:offset + size] = data[face["donor"]][offset:offset + size]
                if face["description_tra"] is not None:
                    ref = struct.unpack_from("<I", variant, 0x54)[0]
                    self.assertEqual(strings.text(ref, set()), authored[face["description_tra"]])
                    expected[0x54:0x58] = variant[0x54:0x58]
                if source == "plat17":
                    ref = struct.unpack_from("<I", variant, 0x0C)[0]
                    self.assertEqual(strings.text(ref, set()), authored[212])
                    struct.pack_into("<I", expected, 0x0C, ref)
                    struct.pack_into("<I", expected, 0x60, 0)  # enchantment, NOT weight
                    struct.pack_into("<I", expected, 0xAE, 3)
                self.assertEqual(variant, bytes(expected))
        # Re-export reuses old text references, keeping prior saved revisions valid.
        self.assertEqual(prepare_cursed_disguises(spec, data, tlk), (fixture, files, tlk))

    def test_item_revisions_component_zero_selects_exact_original_compatibility(self):
        self.assertTrue(item_revisions_installed(b'// ignored\n~ITEM_REV/ITEM_REV.TP2~ #2 #0 // core\n'))
        self.assertTrue(item_revisions_installed(b'~C:\\Mods\\item_rev.tp2~ #0 #000 // core\n'))
        for log in (b'// ~item_rev.tp2~ #0 #0', b'~item_rev.tp2~ #0 #10',
                    b'~other_item_rev.tp2~ #0 #0', b''):
            self.assertFalse(item_revisions_installed(log))
        data = resources()
        spec = spec_for(data, item_revisions=True)
        fixture, files, tlk = prepare_cursed_disguises(spec, data, make_tlk([]))
        self.assertNotIn("plat17", [row["resref"] for row in fixture["items"]])
        self.assertFalse(any(key.startswith("plat17.") for key in files))
        leather = next(row for row in spec["choices"] if row["source"] == "leat06")
        self.assertEqual([face["description_tra"] for face in leather["variants"]], [210, 210, 211])

    def test_seed_restoration_reconstructs_faces_without_changing_inputs(self):
        spec = spec_for(resources())
        before = deepcopy(spec)
        # Boundary seeds exercise the component salt's wraparound too.
        traces = [cursed_disguise_indices(spec, seed) for seed in (1, 1234567, 2147483646)]
        self.assertEqual([list(trace.values()) for trace in traces], [
            [1, 2, 3, 2, 6, 3, 2, 1], [4, 3, 2, 3, 2, 2, 2, 2],
            [2, 3, 2, 3, 3, 2, 2, 2]])
        self.assertEqual(traces, [cursed_disguise_indices(deepcopy(spec), seed)
                                 for seed in (1, 1234567, 2147483646)])
        self.assertEqual(spec, before)
        self.assertNotEqual(traces[0], traces[1])
        for seed in (0, -1, 2147483647, True):
            with self.assertRaisesRegex(ProfileError, "campaign seed"):
                cursed_disguise_indices(spec, seed)

    def test_changed_install_bytes_cannot_create_new_variants_for_saved_revision(self):
        data = resources()
        spec = spec_for(data)
        data["brac06"] += b"different mod data"
        with self.assertRaisesRegex(ProfileError, "resource changed.*brac06"):
            prepare_cursed_disguises(spec, data, make_tlk([]))


if __name__ == "__main__":
    unittest.main()
