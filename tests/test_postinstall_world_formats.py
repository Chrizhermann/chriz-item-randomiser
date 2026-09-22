"""Synthetic CRE/ARE/STO inventories only; never discover or edit a game."""

from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from flir_config.resources import ScanError
from flir_config.world_formats import parse_are, parse_cre, parse_sto


def item_record(name="SYNTH001", expiration=13, charges=(7, 3, 2), flags=0x80000009):
    return struct.pack("<8s4HI", name.encode("ascii"), expiration, *charges, flags)


def make_cre(records=(), slots=None, *, gap=0, slots_first=False):
    header = bytearray(0x2D4)
    header[:8] = b"CRE V1.0"
    struct.pack_into("<I", header, 8, 123)
    header[0x280:0x280 + 7] = b"TestDV\0"
    slot_values = [0xFFFF] * 38 + [1000, 0xFFFF]
    if slots is None:
        slots = {21 + index: index for index in range(len(records))}
    for slot, index in slots.items():
        slot_values[slot] = index
    slot_bytes = struct.pack("<40H", *slot_values)
    record_bytes = b"".join(records)
    if slots_first:
        slot_offset, item_offset = len(header) + gap, len(header) + gap + 80 + gap
        result = header + b"\0" * gap + slot_bytes + b"\0" * gap + record_bytes
    else:
        item_offset, slot_offset = len(header) + gap, len(header) + gap + len(record_bytes) + gap
        result = header + b"\0" * gap + record_bytes + b"\0" * gap + slot_bytes
    struct.pack_into("<III", result, 0x2B8, slot_offset, item_offset, len(records))
    return result


def make_are(records=(), containers=(), actors=(), *, gap=0):
    """containers=(name, first, count); actors=(name, external resref|CRE bytes)."""
    data = bytearray(0x11C)
    data[:8] = b"AREAV1.0"
    data[0x94:0x9C] = b"ARSCRIPT"
    item_offset = len(data) + gap
    data.extend(b"\0" * gap + b"".join(records))
    struct.pack_into("<H", data, 0x76, len(records))
    struct.pack_into("<I", data, 0x78, item_offset)
    container_offset = len(data) + gap
    data.extend(b"\0" * gap + b"\0" * (len(containers) * 0xC0))
    struct.pack_into("<IH", data, 0x70, container_offset, len(containers))
    for index, (name, first, count) in enumerate(containers):
        base = container_offset + index * 0xC0
        struct.pack_into("<32sHHHHI", data, base, name.encode("ascii"), 111, 222, 2, 30, 5)
        struct.pack_into("<II", data, base + 0x40, first, count)
        struct.pack_into("<8s", data, base + 0x48, b"TRAP")
        struct.pack_into("<32s", data, base + 0x58, b"OwnerDV")
    actor_offset = len(data) + gap
    data.extend(b"\0" * gap + b"\0" * (len(actors) * 0x110))
    struct.pack_into("<IH", data, 0x54, actor_offset, len(actors))
    for index, (name, creature) in enumerate(actors):
        base = actor_offset + index * 0x110
        struct.pack_into("<32sHH", data, base, name.encode("ascii"), 333, 444)
        struct.pack_into("<I", data, base + 0x40, 0xFFFFFF)
        if isinstance(creature, str):
            struct.pack_into("<I", data, base + 0x28, 1)
            struct.pack_into("<8s", data, base + 0x80, creature.encode("ascii"))
        else:
            struct.pack_into("<8sII", data, base + 0x80, b"*EMBED", len(data) + gap, len(creature))
            data.extend(b"\0" * gap + creature)
    return data


def make_sto(records=(), *, gap=0):
    data = bytearray(0x9C + gap)
    data[:8] = b"STORV1.0"
    struct.pack_into("<III", data, 8, 5, 456, 3)
    struct.pack_into("<II", data, 0x34, len(data), len(records))
    for item, quantity, infinite in records:
        data.extend(item + struct.pack("<II", quantity, infinite))
    return data


class CreatureInventoryTests(unittest.TestCase):
    def test_repeated_copies_retain_distinct_records_and_exact_payload(self):
        data = make_cre([item_record(), item_record(charges=(0, 9, 65535), flags=4)], gap=7)
        before = bytes(data)
        parsed = parse_cre(data)
        self.assertEqual(bytes(data), before)
        self.assertEqual(parsed["script_name"], "TestDV")
        self.assertEqual(parsed["name_strref"], 123)
        self.assertEqual([item["record_index"] for item in parsed["items"]], [0, 1])
        self.assertEqual([item["resref"] for item in parsed["items"]], ["synth001", "synth001"])
        first, second = parsed["items"]
        self.assertEqual(first["byte_offset"], 0x2DB)
        self.assertEqual(first["expiration"], 13)
        self.assertEqual(first["charges"], [7, 3, 2])
        self.assertEqual(first["flags"], 0x80000009)
        self.assertEqual(first["slots"], [21])
        self.assertEqual(second["charges"], [0, 9, 65535])
        self.assertEqual(second["flags"], 4)
        self.assertEqual(parsed["selected_weapon"], 1000)
        self.assertEqual(parsed["selected_weapon_ability"], 65535)

    def test_slot_order_and_gap_are_not_assumed(self):
        parsed = parse_cre(make_cre([item_record()], gap=9, slots_first=True))
        self.assertEqual(parsed["item_slots_offset"], 0x2D4 + 9)
        self.assertEqual(parsed["items"][0]["byte_offset"], 0x2D4 + 9 + 80 + 9)

    def test_unslotted_records_and_slot_aliases_are_explicit(self):
        parsed = parse_cre(make_cre([item_record(), item_record()], slots={0: 0, 21: 0}))
        self.assertEqual(parsed["aliased_item_indexes"], [0])
        self.assertEqual(parsed["unslotted_item_indexes"], [1])
        self.assertEqual(parsed["items"][0]["slots"], [0, 21])
        self.assertEqual(parsed["items"][1]["slots"], [])

    def test_empty_inventory_does_not_treat_selection_words_as_references(self):
        data = make_cre()
        struct.pack_into("<I", data, 0x2BC, 0xFFFFFFFF)
        parsed = parse_cre(data)
        self.assertEqual(parsed["items"], [])
        self.assertEqual(parsed["slots"], [None] * 38)

    def test_invalid_slot_reference_fails(self):
        with self.assertRaisesRegex(ScanError, "absent item"):
            parse_cre(make_cre([item_record()], slots={3: 1}))

    def test_item_slot_overlap_fails(self):
        data = make_cre([item_record()])
        struct.pack_into("<I", data, 0x2BC, struct.unpack_from("<I", data, 0x2B8)[0])
        with self.assertRaisesRegex(ScanError, "Overlapping"):
            parse_cre(data)

    def test_out_of_bounds_count_and_truncation_fail(self):
        data = make_cre([item_record()])
        with self.assertRaises(ScanError):
            parse_cre(data[:-1])
        struct.pack_into("<I", data, 0x2C0, 0xFFFFFFFF)
        with self.assertRaisesRegex(ScanError, "truncated"):
            parse_cre(data)

    def test_effects_use_cre_body_size_and_cannot_alias_items(self):
        data = make_cre([item_record()])
        data[0x33] = 1
        struct.pack_into("<II", data, 0x2C4, len(data), 1)
        data.extend(b"\0" * 264)
        self.assertEqual(len(parse_cre(data)["items"]), 1)
        with self.assertRaises(ScanError):
            parse_cre(data[:-1])
        struct.pack_into("<I", data, 0x2C4, 0x2D4)
        with self.assertRaisesRegex(ScanError, "Overlapping"):
            parse_cre(data)

    def test_unknown_effect_layout_and_item_name_fail(self):
        data = make_cre([item_record()])
        data[0x33] = 2
        with self.assertRaisesRegex(ScanError, "effect structure"):
            parse_cre(data)
        data[0x33] = 0
        data[0x2D4] = 0xFF
        with self.assertRaisesRegex(ScanError, "ASCII"):
            parse_cre(data)


class AreaInventoryTests(unittest.TestCase):
    def test_containers_keep_ranges_repeated_items_and_legal_orphans(self):
        parsed = parse_are(make_are([item_record(), item_record(), item_record()], [("Chest", 1, 1), ("Pile", 2, 1)], gap=11))
        self.assertEqual(parsed["script"], "arscript")
        self.assertEqual(parsed["orphan_item_indexes"], [0])
        self.assertIsNone(parsed["items"][0]["container_index"])
        self.assertEqual(parsed["items"][1]["container_indexes"], [0])
        self.assertEqual(parsed["items"][2]["container_index"], 1)
        chest = parsed["containers"][0]
        self.assertEqual(chest["items"][0]["record_index"], 1)
        self.assertEqual((chest["x"], chest["y"], chest["flags"]), (111, 222, 5))
        self.assertEqual(chest["owner"], "OwnerDV")
        self.assertEqual(chest["trap_script"], "trap")

    def test_container_aliases_are_unresolved_not_claimed_as_unique(self):
        parsed = parse_are(make_are([item_record()], [("ChestA", 0, 1), ("ChestB", 0, 1)]))
        self.assertEqual(parsed["aliased_item_indexes"], [0])
        self.assertEqual(parsed["items"][0]["container_indexes"], [0, 1])
        self.assertIsNone(parsed["items"][0]["container_index"])

    def test_empty_containers_and_dynamic_table_marker(self):
        parsed = parse_are(make_are([], [("T2TEST", 0xFFFFFFFF, 0)]))
        self.assertEqual(parsed["containers"][0]["items"], [])
        self.assertEqual(parsed["containers"][0]["dynamic_item_table"], "T2TEST")
        self.assertEqual(parse_are(make_are())["items"], [])

    def test_container_out_of_bounds_reference_fails(self):
        for first, count in [(0, 2), (1, 1), (0xFFFFFFFF, 1)]:
            with self.subTest(first=first, count=count), self.assertRaisesRegex(ScanError, "item table"):
                parse_are(make_are([item_record()], [("Chest", first, count)]))

    def test_external_and_embedded_creatures_offsets_and_identity(self):
        embedded = make_cre([item_record()], gap=3)
        data = make_are([], [], [("External", "TARGET01"), ("Embedded", embedded)], gap=5)
        parsed = parse_are(data)
        external, actor = parsed["actors"]
        self.assertEqual(external["cre_resref"], "target01")
        self.assertIsNone(external["embedded_cre"])
        self.assertEqual((actor["name"], actor["cre_resref"]), ("Embedded", "*embed"))
        cre = actor["embedded_cre"]
        self.assertEqual(cre["script_name"], "TestDV")
        self.assertEqual(cre["items"][0]["byte_offset"], actor["embedded_cre_offset"] + 0x2D4 + 3)
        self.assertEqual(cre["items"][0]["record_index"], 0)
        self.assertEqual(data[cre["items"][0]["byte_offset"]:][:8], b"SYNTH001")
        self.assertEqual(cre["item_slots_offset"], actor["embedded_cre_offset"] + parse_cre(embedded)["item_slots_offset"])

    def test_embedded_creature_cannot_read_outside_declared_size(self):
        data = make_are([], [], [("Embedded", make_cre([item_record()]))])
        base = struct.unpack_from("<I", data, 0x54)[0]
        struct.pack_into("<I", data, base + 0x8C, 0x2D4)
        with self.assertRaisesRegex(ScanError, "truncated"):
            parse_are(data)

    def test_duplicate_embedded_byte_storage_is_rejected(self):
        data = make_are([], [], [("A", make_cre()), ("B", make_cre())])
        base = struct.unpack_from("<I", data, 0x54)[0]
        data[base + 0x110 + 0x88:base + 0x110 + 0x90] = data[base + 0x88:base + 0x90]
        with self.assertRaisesRegex(ScanError, "Overlapping"):
            parse_are(data)

    def test_incomplete_and_conflicting_actor_storage_fail(self):
        data = make_are([], [], [("External", "TARGET01")])
        base = struct.unpack_from("<I", data, 0x54)[0]
        struct.pack_into("<I", data, base + 0x88, 0x11C)
        with self.assertRaisesRegex(ScanError, "conflicting"):
            parse_are(data)
        struct.pack_into("<I", data, base + 0x88, 0)
        struct.pack_into("<I", data, base + 0x28, 0)
        struct.pack_into("<I", data, base + 0x8C, 40)
        with self.assertRaisesRegex(ScanError, "incomplete"):
            parse_are(data)

    def test_external_actor_without_unloaded_flag_still_uses_resref(self):
        data = make_are([], [], [("External", "TARGET01")])
        base = struct.unpack_from("<I", data, 0x54)[0]
        struct.pack_into("<I", data, base + 0x28, 0)
        actor = parse_are(data)["actors"][0]
        self.assertEqual(actor["cre_resref"], "target01")
        self.assertIsNone(actor["embedded_cre"])
        data[base + 0x80:base + 0x88] = b"\0" * 8
        with self.assertRaisesRegex(ScanError, "reference"):
            parse_are(data)

    def test_unrelated_section_cannot_overlap_inventory(self):
        data = make_are([item_record()] * 11)
        struct.pack_into("<H", data, 0x5A, 1)
        struct.pack_into("<I", data, 0x5C, 0x11C)
        with self.assertRaisesRegex(ScanError, "Overlapping"):
            parse_are(data)


class StoreInventoryTests(unittest.TestCase):
    def test_quantities_flags_charges_and_duplicate_rows_are_preserved(self):
        parsed = parse_sto(make_sto([(item_record(), 17, 0), (item_record(charges=(0, 0, 0)), 0, 1)], gap=13))
        self.assertEqual((parsed["type"], parsed["name_strref"], parsed["flags"]), (5, 456, 3))
        first, second = parsed["items"]
        self.assertEqual(first["quantity"], 17)
        self.assertFalse(first["infinite_supply"])
        self.assertEqual(first["byte_offset"], 0x9C + 13)
        self.assertEqual(first["charges"], [7, 3, 2])
        self.assertEqual(first["flags"], 0x80000009)
        self.assertEqual(second["quantity"], 0)
        self.assertTrue(second["infinite_supply"])
        self.assertEqual(second["infinite_supply_flag"], 1)
        self.assertEqual(second["record_index"], 1)

    def test_empty_store_zero_stock_and_raw_infinite_flags(self):
        self.assertEqual(parse_sto(make_sto())["items"], [])
        rows = parse_sto(make_sto([(item_record(), 0, 0), (item_record(), 4, 0xFFFFFFFF)]))["items"]
        self.assertEqual(rows[0]["quantity"], 0)
        self.assertFalse(rows[0]["infinite_supply"])
        self.assertEqual(rows[1]["infinite_supply_flag"], 0xFFFFFFFF)
        self.assertTrue(rows[1]["infinite_supply"])

    def test_overlap_purchased_categories_and_truncated_stock_fail(self):
        data = make_sto([(item_record(), 1, 0)])
        with self.assertRaisesRegex(ScanError, "truncated"):
            parse_sto(data[:-1])
        struct.pack_into("<II", data, 0x2C, 0x9C, 1)
        with self.assertRaisesRegex(ScanError, "Overlapping"):
            parse_sto(data)


class WorldFormatValidationTests(unittest.TestCase):
    def test_short_unknown_signatures_and_header_alias_fail(self):
        for parser, fixture, offset in [(parse_cre, make_cre([item_record()]), 0x2BC),
                                        (parse_are, make_are([item_record()]), 0x78),
                                        (parse_sto, make_sto([(item_record(), 1, 0)]), 0x34)]:
            with self.subTest(parser=parser.__name__):
                with self.assertRaises(ScanError):
                    parser(fixture[:7])
                unknown = bytearray(fixture)
                unknown[4:8] = b"V9.0"
                with self.assertRaisesRegex(ScanError, "Unsupported"):
                    parser(unknown)
                struct.pack_into("<I", fixture, offset, 8)
                with self.assertRaisesRegex(ScanError, "header"):
                    parser(fixture)


if __name__ == "__main__":
    unittest.main()
