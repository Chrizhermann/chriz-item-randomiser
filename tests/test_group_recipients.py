"""The original WeiDU group preference uses item and CRE usability masks."""

from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from flir_config.group_recipients import preferred_members, usable  # noqa: E402
from test_postinstall_resources import make_itm  # noqa: E402
from test_postinstall_world_formats import make_cre  # noqa: E402


def item_mask(offset, value):
    data = bytearray(make_itm())
    data[offset] = value
    return bytes(data)


def creature(*, klass=0, race=0, alignment=0, kit=0):
    data = make_cre([])
    struct.pack_into("<I", data, 0x244, kit)
    data[0x272], data[0x273], data[0x27b] = race, klass, alignment
    return bytes(data)


class GroupRecipientTests(unittest.TestCase):
    def test_class_and_race_masks_follow_old_byte_positions(self):
        # fl#gear.tpa: class 1 -> unuse3 bit 2; race 2 -> unuse3 bit 7.
        self.assertFalse(usable(item_mask(0x20, 0x04), creature(klass=1)))
        self.assertFalse(usable(item_mask(0x20, 0x80), creature(race=2)))
        self.assertTrue(usable(item_mask(0x20, 0x04), creature(klass=2)))

    def test_alignment_and_kit_masks_include_original_special_cases(self):
        # Alignment 50 is 0b0001001 in the original; 0x400d0000 is kit2 bit 5.
        self.assertFalse(usable(item_mask(0x1e, 0x08), creature(alignment=50)))
        self.assertFalse(usable(item_mask(0x2b, 0x20), creature(kit=0x400d0000)))
        self.assertFalse(usable(item_mask(0x2d, 0x40), creature(kit=0x400d0000)))
        self.assertTrue(usable(item_mask(0x2d, 0x40), creature(kit=0x40000000)))

    def test_preferred_members_sorted_and_falls_back_when_none_usable(self):
        item = item_mask(0x20, 0x04)
        members = {"mage": creature(klass=1), "fighter": creature(klass=2)}
        self.assertEqual(preferred_members(item, members), ["fighter"])
        self.assertEqual(preferred_members(item, {"z": creature(klass=1),
                                                  "a": creature(klass=1)}), ["a", "z"])


if __name__ == "__main__":
    unittest.main()
