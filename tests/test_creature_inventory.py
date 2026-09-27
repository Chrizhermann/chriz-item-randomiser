"""Campaign-private CRE inventory edits use synthetic resources only."""

import struct
import unittest

from tests.test_postinstall_world_formats import item_record, make_cre

from flir_config.creature_inventory import rewrite_inventory
from flir_config.world_formats import parse_cre


def with_selection(data: bytes, weapon: int, ability: int) -> bytes:
    result = bytearray(data)
    slots_offset = parse_cre(data)["item_slots_offset"]
    struct.pack_into("<HH", result, slots_offset + 76, weapon, ability)
    return bytes(result)


class CreatureInventoryRewriteTests(unittest.TestCase):
    def test_mixed_edits_compact_records_but_keep_other_sections_and_input(self):
        records = [item_record(name=f"ITEM{i:04}", expiration=i + 10,
                               charges=(i + 1, i + 2, i + 3), flags=0x100 + i)
                   for i in range(5)]
        original = bytearray(make_cre(records,
                                      slots={9: 1, 10: 2, 21: 0, 22: 3, 37: 4}, gap=7))
        # Noninventory spell and effect sections must remain byte-for-byte fixed.
        known_offset = len(original)
        original.extend(bytes(range(12)))
        effect_offset = len(original)
        original.extend(bytes(range(48)))
        struct.pack_into("<II", original, 0x2A0, known_offset, 1)
        struct.pack_into("<II", original, 0x2C4, effect_offset, 1)
        original = with_selection(bytes(original), 0, 6)
        before = bytes(original)

        rewritten = rewrite_inventory(original, {0: None, 1: "newitem", 3: None})
        parsed = parse_cre(rewritten)
        self.assertEqual(original, before)
        self.assertEqual(len(rewritten), len(original))
        self.assertEqual([row["resref"] for row in parsed["items"]],
                         ["newitem", "item0002", "item0004"])
        self.assertEqual(parsed["slots"][9:11], [0, 1])
        self.assertEqual([parsed["slots"][slot] for slot in (21, 22, 37)],
                         [None, None, 2])
        self.assertEqual((parsed["selected_weapon"], parsed["selected_weapon_ability"]),
                         (0, 6))
        self.assertEqual(rewritten[parsed["items_offset"]:parsed["items_offset"] + 20],
                         b"newitem\0" + records[1][8:])
        self.assertEqual(rewritten[parsed["items_offset"] + 60:
                                   parsed["items_offset"] + 100], b"\0" * 40)
        self.assertEqual(rewritten[known_offset:effect_offset], before[known_offset:effect_offset])
        self.assertEqual(rewritten[effect_offset:], before[effect_offset:])
        self.assertEqual(rewritten[:0x2A0], before[:0x2A0])

    def test_removed_selected_weapon_chooses_first_remaining_slot(self):
        original = with_selection(bytes(make_cre(
            [item_record("WEAPON00"), item_record("WEAPON01"), item_record("BACKPACK")],
            slots={9: 0, 11: 1, 21: 2})), 0, 4)
        rewritten = parse_cre(rewrite_inventory(original, {0: None}))
        self.assertEqual(rewritten["selected_weapon"], 2)
        self.assertEqual(rewritten["selected_weapon_ability"], 0)
        self.assertEqual(rewritten["slots"][11], 0)

    def test_removed_last_weapon_selects_fists_and_preserves_special_selector(self):
        original = with_selection(bytes(make_cre([item_record("WEAPON00")],
                                                 slots={9: 0})), 0, 3)
        rewritten = parse_cre(rewrite_inventory(original, {0: None}))
        self.assertEqual((rewritten["selected_weapon"], rewritten["selected_weapon_ability"]),
                         (1000, 0))
        special = with_selection(original, 0xFFFF, 5)
        preserved = parse_cre(rewrite_inventory(special, {0: None}))
        self.assertEqual((preserved["selected_weapon"], preserved["selected_weapon_ability"]),
                         (0xFFFF, 5))

    def test_invalid_index_and_replacement_are_rejected_without_mutation(self):
        original = bytes(make_cre([item_record()]))
        with self.assertRaises(ValueError):
            rewrite_inventory(original, {1: None})
        with self.assertRaises(ValueError):
            rewrite_inventory(original, {0: "UPPER"})
        self.assertEqual(parse_cre(original)["items"][0]["resref"], "synth001")


if __name__ == "__main__":
    unittest.main()
