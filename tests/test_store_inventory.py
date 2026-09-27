"""STO stock rewrites use synthetic bytes; no game resources are touched."""

from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from flir_config.resources import ScanError
from flir_config.store_inventory import rewrite_inventory
from flir_config.world_formats import parse_sto
from tests.test_postinstall_world_formats import item_record, make_sto


class StoreInventoryRewriteTests(unittest.TestCase):
    def test_middle_row_removal_compacts_stock_without_moving_other_sections(self):
        rows = [(item_record("FIRST", charges=(1, 2, 3)), 2, 0),
                (item_record("REMOVE", charges=(4, 5, 6)), 1, 0),
                (item_record("THIRD", charges=(7, 8, 9)), 5, 0)]
        data = make_sto(rows, gap=9)
        # Place a purchased-category section after the sale table.
        data.extend(b"TAIL" + struct.pack("<I", 0x12345678))
        category_offset = len(data) - 4
        struct.pack_into("<II", data, 0x2C, category_offset, 1)
        before = bytes(data)
        sale_offset = struct.unpack_from("<I", data, 0x34)[0]

        result = rewrite_inventory(before, {1})

        self.assertEqual(len(result), len(before))
        self.assertEqual(result[:0x38], before[:0x38])
        self.assertEqual(result[0x3C:sale_offset], before[0x3C:sale_offset])
        self.assertEqual(result[category_offset:], before[category_offset:])
        self.assertEqual(struct.unpack_from("<II", result, 0x2C), (category_offset, 1))
        self.assertEqual(result[sale_offset + 56:sale_offset + 84], bytes(28))
        parsed = parse_sto(result)
        self.assertEqual([(row["resref"], row["charges"], row["quantity"])
                          for row in parsed["items"]],
                         [("first", [1, 2, 3], 2), ("third", [7, 8, 9], 5)])
        self.assertEqual(before, bytes(data))

    def test_invalid_indices_and_malformed_store_fail_before_rewrite(self):
        data = bytes(make_sto([(item_record(), 1, 0)]))
        for removals in ({1}, {-1}, {True}):
            with self.subTest(removals=removals), self.assertRaises(ValueError):
                rewrite_inventory(data, removals)
        self.assertEqual(rewrite_inventory(data, set()), data)
        with self.assertRaises(ScanError):
            rewrite_inventory(data[:-1], {0})


if __name__ == "__main__":
    unittest.main()
