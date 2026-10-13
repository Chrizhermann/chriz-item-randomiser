"""Original-option semantics on synthetic resources, without a game install."""

from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from flir_config.original_options import (cursed_disguise_choices,
                                          patch_cursed_disguise,
                                          patch_spell_shield_stock,
                                          tome_variants)
from flir_config.profiles import ProfileError
from flir_config.world_formats import parse_sto
from test_postinstall_resources import make_itm
from test_postinstall_world_formats import item_record, make_sto


ALL_DISGUISE_ITEMS = {
    "brac08", "brac06", "brac07", "brac10", "brac09", "brac11", "brac01",
    "brac02", "brac03", "brac13", "brac14", "brac15", "brac04", "leat06",
    "leat05", "leat07", "leat08", "plat17", "plat02", "plat11", "plat10",
    "ring04", "ring23", "ring06", "ring07", "ring22", "ring09", "ring31",
    "ring08", "sper03", "sper05", "sper02", "sw2h03", "sw2h11", "sw2h02",
}


class OriginalOptionTests(unittest.TestCase):
    def test_totsc_tome_has_all_six_original_choices_on_each_supported_game(self):
        for game in ("bgee", "bg2ee", "eet"):
            self.assertEqual(tome_variants(game=game),
                             ["book03", "book04", "book05", "book06", "book07", "book08"])

    def test_spell_shield_only_changes_first_matching_quantity(self):
        for first_quantity in (0, 1, 3, 9):
            original = bytes(make_sto([
                (item_record("scrl8x", charges=(2, 3, 4), flags=9), first_quantity, 1),
                (item_record("scrl8x", charges=(9, 8, 7), flags=4), 1, 0),
                (item_record("other"), 5, 0)], gap=11))
            patched = patch_spell_shield_stock(original)
            expected = bytearray(original)
            offset = parse_sto(original)["items"][0]["byte_offset"]
            struct.pack_into("<I", expected, offset + 20, max(3, first_quantity))
            self.assertEqual(patched, bytes(expected))
            self.assertEqual(patch_spell_shield_stock(patched), patched)

    def test_spell_shield_insertion_preserves_other_sections_and_sale_payloads(self):
        original = make_sto([(item_record("other"), 4, 0),
                             (item_record("scrl7q"), 8, 1),
                             (item_record("scrlal"), 9, 0)], gap=13)
        # An unrelated purchased-category section after the sale table must
        # retain its absolute offset and exact bytes after insertion.
        struct.pack_into("<II", original, 0x2C, len(original), 1)
        original.extend(struct.pack("<I", 16))
        before = bytes(original)
        patched = patch_spell_shield_stock(before)
        rows = parse_sto(patched)["items"]
        self.assertEqual([row["resref"] for row in rows], ["other", "scrl8x", "scrl7q", "scrlal"])
        added = rows[1]
        self.assertEqual((added["quantity"], added["flags"], added["charges"],
                          added["infinite_supply_flag"], added["expiration"]), (3, 1, [0, 0, 0], 0, 0))
        expected_old_bytes = bytearray(before)
        expected_old_bytes[0x34:0x3C] = patched[0x34:0x3C]
        self.assertEqual(patched[:len(before)], bytes(expected_old_bytes))
        self.assertEqual(patch_spell_shield_stock(patched), patched)
        self.assertEqual(bytes(original), before)

    def test_spell_shield_can_populate_empty_store(self):
        patched = patch_spell_shield_stock(bytes(make_sto()))
        self.assertEqual(parse_sto(patched)["items"][0]["resref"], "scrl8x")

    def test_disguise_preserves_original_dice_and_optional_fallback_weights(self):
        rows = cursed_disguise_choices(ALL_DISGUISE_ITEMS)
        self.assertEqual([(row["source"], len(row["variants"])) for row in rows], [
            ("brac08", 4), ("brac11", 7), ("leat06", 3), ("plat17", 3),
            ("ring04", 6), ("ring23", 6), ("sper03", 2), ("sw2h03", 2)])
        ring = next(row for row in rows if row["source"] == "ring23")
        self.assertEqual([v["donor"] for v in ring["variants"]],
                         ["ring06", "ring07", "ring08", "ring09", "ring31", "ring08"])
        absent = ALL_DISGUISE_ITEMS - {"brac13", "brac14", "brac15", "ring31", "sper05", "sw2h11"}
        mapped = {row["source"]: row["variants"] for row in cursed_disguise_choices(absent)}
        self.assertEqual([v["donor"] for v in mapped["brac11"]],
                         ["brac01", "brac02", "brac03", "brac04", "brac04", "brac04", "brac04"])
        self.assertEqual(sum(v["donor"] == "ring08" for v in mapped["ring23"]), 3)
        self.assertEqual([v["donor"] for v in mapped["sper03"]], ["sper02", "sper02"])

    def test_item_revisions_keeps_plate_and_uses_its_authored_leather_descriptions(self):
        rows = {row["source"]: row for row in cursed_disguise_choices(ALL_DISGUISE_ITEMS, item_revisions=True)}
        self.assertNotIn("plat17", rows)
        self.assertEqual([v["description_tra"] for v in rows["leat06"]["variants"]], [210, 210, 211])
        with self.assertRaisesRegex(ProfileError, "needs brac06"):
            cursed_disguise_choices(ALL_DISGUISE_ITEMS - {"brac06"})

    def test_disguise_changes_only_appearance_and_requested_description(self):
        original = make_itm(15, 16, price=500)
        donor = bytearray(make_itm(25, 26, price=900))
        donor[0x3A:0x42] = b"INVICON1"
        donor[0x58:0x60] = b"CARICON1"
        struct.pack_into("<I", donor, 0x50, 123)
        expected = bytearray(original)
        expected[0x3A:0x42] = donor[0x3A:0x42]
        expected[0x50:0x54] = donor[0x50:0x54]
        expected[0x58:0x60] = donor[0x58:0x60]
        self.assertEqual(patch_cursed_disguise(original, donor), bytes(expected))
        struct.pack_into("<I", expected, 0x54, 456)
        self.assertEqual(patch_cursed_disguise(original, donor, description_strref=456), bytes(expected))

    def test_plate_fix_only_changes_original_owned_global_ac_effect(self):
        original = bytearray(make_itm())
        struct.pack_into("<IH", original, 0x6A, len(original), 0)
        struct.pack_into("<H", original, 0x70, 3)
        original.extend(b"\0" * (3 * 0x30))
        # Original code adjusts opcode 0 / parameter2 16, not other AC modes
        # or an unrelated opcode that happens to use parameter2 16.
        for index, (opcode, mode) in enumerate(((0, 16), (0, 1), (17, 16))):
            struct.pack_into("<H", original, 0x72 + index * 0x30, opcode)
            struct.pack_into("<II", original, 0x76 + index * 0x30, 9, mode)
        patched = patch_cursed_disguise(bytes(original), make_itm(), description_strref=77, plate_name_strref=88)
        self.assertEqual(struct.unpack_from("<I", patched, 0x0C)[0], 88)
        self.assertEqual(struct.unpack_from("<I", patched, 0x60)[0], 0)
        self.assertEqual([struct.unpack_from("<I", patched, 0x76 + i * 0x30)[0] for i in range(3)], [3, 9, 9])
        with self.assertRaisesRegex(ProfileError, "effect range"):
            patch_cursed_disguise(bytes(original[:-1]), make_itm(), plate_name_strref=88)


if __name__ == "__main__":
    unittest.main()
