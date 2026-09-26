"""Binary fixture construction only; these checks do not prove engine behavior."""

import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from build_mode1_fixture import (add_area_fixture, build, item_payload,
                                make_source_cre, mark_initial_gam)
from flir_config.campaigns import read_campaign
from flir_config.world_formats import parse_are, parse_cre
from test_postinstall_world_formats import item_record, make_are, make_cre


def make_gam():
    data = bytearray(0xB4)
    data[:8] = b"GAMEV2.0"
    struct.pack_into("<II", data, 0x38, len(data), 1)
    record = bytearray(84)
    struct.pack_into("<32sH", record, 0, b"EXISTING", 1)
    struct.pack_into("<i", record, 0x28, 47)
    return bytes(data + record + b"unrelated trailing section")


def make_itm():
    data = bytearray(0x72 + 0x38)
    data[:8] = b"ITM V1  "
    struct.pack_into("<I", data, 0x18, 0x2C)
    struct.pack_into("<H", data, 0x38, 1)
    struct.pack_into("<IH", data, 0x64, 0x72, 1)
    return bytes(data)


class Mode1FixtureTests(unittest.TestCase):
    def test_gam_marker_preserves_original_bytes_and_existing_variables(self):
        before = make_gam()
        after = mark_initial_gam(before)
        self.assertEqual(after[:0x38], before[:0x38])
        self.assertEqual(after[0x40:len(before)], before[0x40:])
        offset, count = struct.unpack_from("<II", after, 0x38)
        self.assertEqual((offset, count), (len(before), 2))
        self.assertEqual(after[offset:offset + 84], before[0xB4:0xB4 + 84])
        self.assertEqual(after[offset + 84:offset + 95], b"FLPR_FRESH\0")
        self.assertEqual(struct.unpack_from("<i", after, offset + 84 + 0x28)[0], 1)
        with self.assertRaisesRegex(ValueError, "already contains"):
            mark_initial_gam(after)

    def test_cre_source_has_one_unequipped_copy_no_scripts_or_effects(self):
        before = make_cre([item_record()], slots={2: 0})
        payload = item_payload("flpri1", make_itm())
        after = make_source_cre(before, "FLPRS1", payload)
        parsed = parse_cre(after)
        self.assertEqual(parsed["script_name"], "FLPRS1")
        self.assertEqual(len(parsed["items"]), 1)
        self.assertEqual(parsed["items"][0]["slots"], [21])
        self.assertEqual(parsed["items"][0]["flags"], 1)
        self.assertEqual(parsed["items"][0]["charges"], [0, 0, 0])
        self.assertEqual(after[0x248:0x270], bytes(40))
        self.assertEqual(after[0x2CC:0x2D4], bytes(8))

    def test_area_keeps_existing_tables_and_embedded_cre_offsets(self):
        before = bytes(make_are([item_record()], [("OldChest", 0, 1)],
            [("OldActor", make_cre([item_record()]))]))
        after = add_area_fixture(before)
        old, new = parse_are(before), parse_are(after)
        self.assertEqual(new["actors"][0]["embedded_cre_offset"], old["actors"][0]["embedded_cre_offset"])
        self.assertEqual(new["actors"][0]["embedded_cre"], old["actors"][0]["embedded_cre"])
        self.assertEqual(new["containers"][0]["items"], old["containers"][0]["items"])
        allowed = set(range(0x54, 0x5A)) | set(range(0x70, 0x76))
        self.assertTrue(all(a == b for index, (a, b) in enumerate(zip(before, after)) if index not in allowed))
        self.assertEqual([row["name"] for row in new["containers"][-3:]], ["FLPRT1", "FLPRT2", "FLPRT3"])
        self.assertTrue(all(row["type"] == 4 and row["item_count"] == 0 and row["flags"] == 0
                            for row in new["containers"][-3:]))
        with self.assertRaisesRegex(ValueError, "already contains"):
            add_area_fixture(after)

    def test_rejects_stacked_nondroppable_or_enchanted_item(self):
        for offset, fmt, value in ((0x38, "H", 20), (0x18, "I", 8), (0x60, "I", 1)):
            item = bytearray(make_itm())
            struct.pack_into("<" + fmt, item, offset, value)
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, "mundane"):
                item_payload("flpri1", item)

    def test_full_builder_writes_valid_store_and_does_not_modify_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game, output = root / "game", root / "payload"
            override = game / "override"
            override.mkdir(parents=True)
            originals = {"BALDUR.GAM": make_gam(), "COW.CRE": bytes(make_cre()),
                "BG2600.ARE": bytes(make_are()), "BGSTARTA.2DA": b"2DA V1.0\n*\n VALUE\nSTART_AREA BG2600\n"}
            originals.update({name + ".ITM": make_itm() for name in ("SW1H01", "AX1H01", "BLUN04")})
            for name, data in originals.items():
                (override / name).write_bytes(data)
            report = build(game, output)
            manifest = read_campaign(output / "campaign-store")
            self.assertEqual(len(manifest["sources"]), 3)
            self.assertEqual(len(manifest["targets"]), 3)
            self.assertEqual(manifest["revision"], report["revision"])
            native = json.loads((output / "fixture-native.json").read_text())
            self.assertTrue(all(row["record"]["record"]["flags"] == 1 for row in manifest["sources"]))
            self.assertTrue(all(row["payload"]["flags"] == 1 for row in native["sources"].values()))
            identities = [row["identity"] for key in ("sources", "targets") for row in native[key].values()]
            self.assertEqual(len(set(identities)), 6)
            self.assertEqual({path.name: path.read_bytes() for path in override.iterdir()}, originals)
            with self.assertRaisesRegex(ValueError, "never overwrite"):
                build(game, output)
            with self.assertRaisesRegex(ValueError, "outside"):
                build(game, game / "generated")


if __name__ == "__main__":
    unittest.main()
