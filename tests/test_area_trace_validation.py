"""Offline, raw-byte oracle for stopped FLPRU1 area-load trace validation."""

from copy import deepcopy
import hashlib
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_unvisited_fixture import make_unvisited_area
from validate_area_trace import validate_trace
from test_postinstall_world_formats import make_are


ITEM_SIZE = 20
CONTAINER_SIZE = 0xC0


def fixture_area():
    return make_unvisited_area(bytes(make_are()))


def reserialized_area(original):
    """Model a second saved ARE with another orphan before the authored items."""
    area = bytearray(original)
    old_offset = struct.unpack_from("<I", area, 0x78)[0]
    old_count = struct.unpack_from("<H", area, 0x76)[0]
    old_items = bytes(area[old_offset:old_offset + old_count * ITEM_SIZE])
    new_offset = len(area)
    area.extend(struct.pack("<8s4HI", b"OTHER", 0, 4, 5, 6, 1))
    area.extend(old_items)
    struct.pack_into("<HI", area, 0x76, old_count + 1, new_offset)
    container_offset, count = struct.unpack_from("<IH", area, 0x70)
    for ordinal in range(count):
        base = container_offset + ordinal * CONTAINER_SIZE
        first = struct.unpack_from("<I", area, base + 0x40)[0]
        struct.pack_into("<I", area, base + 0x40, first + 1)
    return bytes(area)


def raw_load(area, load_number=0):
    """Build expected native-shaped rows by reading ARE offsets, not parse_are."""
    container_offset, count = struct.unpack_from("<IH", area, 0x70)
    item_count, item_offset = struct.unpack_from("<HI", area, 0x76)
    rows = []
    area_pointer = str(100000 + load_number * 1000000)
    container_pointer_base = 200000 + load_number * 1000000
    item_pointer_base = 400000 + load_number * 1000000
    for ordinal in range(count):
        base = container_offset + ordinal * CONTAINER_SIZE
        first, number = struct.unpack_from("<II", area, base + 0x40)
        assert first + number <= item_count
        common = {"area_pointer": area_pointer,
            "owner_pointer": str(500000 + load_number * 1000000 + ordinal * 1000),
            "record_pointer": str(container_pointer_base + ordinal * CONTAINER_SIZE),
            "area_raw": b"FLPRU1\0\0".hex(), "name_raw": area[base:base + 32].hex(),
            "first": first, "count": number,
            "x": struct.unpack_from("<H", area, base + 0x20)[0],
            "y": struct.unpack_from("<H", area, base + 0x22)[0],
            "container_type": struct.unpack_from("<H", area, base + 0x24)[0],
            "lock": struct.unpack_from("<H", area, base + 0x26)[0]}
        rows.append(dict(common, kind=1, index=ordinal,
                         payload_raw="00" * ITEM_SIZE, item_record_pointer="0"))
        for index in range(first, first + number):
            payload = area[item_offset + index * ITEM_SIZE:item_offset + (index + 1) * ITEM_SIZE]
            rows.append(dict(common, kind=2, index=index,
                             payload_raw=payload.hex(),
                             item_record_pointer=str(item_pointer_base + index * ITEM_SIZE)))
        rows.append(dict(common, kind=3, index=ordinal,
                         payload_raw="00" * ITEM_SIZE, item_record_pointer="0"))
    return rows


def snapshot(*loads):
    rows = [deepcopy(row) for number, area in enumerate(loads)
            for row in raw_load(area, number)]
    for sequence, row in enumerate(rows, 1):
        row["sequence"] = sequence
    return {"schema": "flpr-area-load-trace-v1", "installed": True, "stable": True,
            "armed": False, "overflow": False, "events": rows}


def renumber(trace):
    for sequence, row in enumerate(trace["events"], 1):
        row["sequence"] = sequence


class AreaTraceValidationTests(unittest.TestCase):
    def setUp(self):
        self.first = fixture_area()
        self.trace = snapshot(self.first)

    def reject(self, change, *inputs):
        trace = deepcopy(self.trace)
        change(trace)
        with self.assertRaises(ValueError):
            validate_trace(trace, list(inputs or (self.first,)))

    def test_complete_load_preserves_nonzero_indexes_orphan_and_empty_container(self):
        report = validate_trace(self.trace, [self.first])
        self.assertEqual(report["schema"], "flpr-area-load-acceptance-v1")
        self.assertEqual(report["scope"], "raw-container-loading-only")
        self.assertIs(report["suppression_proven"], False)
        self.assertEqual(report["events"], 9)
        self.assertEqual([row["index"] for row in self.trace["events"] if row["kind"] == 2],
                         [1, 2, 3])
        self.assertEqual([row["kind"] for row in self.trace["events"][-2:]], [1, 3])
        self.assertEqual(report["loads"], [{"first_sequence": 1, "last_sequence": 9,
            "area_pointer": "100000", "input_sha256": hashlib.sha256(self.first).hexdigest(),
            "containers": 3, "referenced_items": 3, "orphan_item_indexes": [0]}])

    def test_two_loads_require_each_actual_serialized_are(self):
        second = reserialized_area(self.first)
        trace = snapshot(self.first, second)
        self.assertEqual([row["index"] for row in trace["events"] if row["kind"] == 2],
                         [1, 2, 3, 2, 3, 4])
        report = validate_trace(trace, [self.first, second])
        self.assertEqual(report["events"], 18)
        self.assertEqual([(row["first_sequence"], row["last_sequence"],
                           row["orphan_item_indexes"]) for row in report["loads"]],
                         [(1, 9, [0]), (10, 18, [0, 1])])
        self.assertEqual(report["loads"][1]["input_sha256"], hashlib.sha256(second).hexdigest())
        with self.assertRaises(ValueError):
            validate_trace(trace, [self.first, self.first])
        with self.assertRaises(ValueError):
            validate_trace(trace, [self.first])

    def test_rejects_loss_incomplete_stream_and_extra_events(self):
        def remove(trace, position):
            trace["events"].pop(position)
            renumber(trace)
        self.reject(lambda t: remove(t, 2))
        self.reject(lambda t: remove(t, -1))
        def add_extra(trace):
            trace["events"].append(deepcopy(trace["events"][-1]))
            renumber(trace)
        self.reject(add_extra)
        self.reject(lambda t: t["events"].__setitem__(2, dict(t["events"][1], sequence=3)))
        self.reject(lambda t: t["events"].__setitem__(0, dict(t["events"][0], sequence=2)))

    def test_requires_installed_stopped_stable_loss_free_snapshot(self):
        for key, value in (("installed", False), ("armed", True),
                           ("stable", False), ("overflow", True),
                           ("schema", "other")):
            with self.subTest(key=key):
                self.reject(lambda t, key=key, value=value: t.__setitem__(key, value))
        self.reject(lambda t: t.__setitem__("events", []))
        self.reject(lambda t: t.__setitem__("events", t["events"] * 228))

    def test_rejects_raw_metadata_payload_identity_index_and_pointer_drift(self):
        changes = (
            (1, "payload_raw", "ff" * ITEM_SIZE),
            (0, "name_raw", "ff" * 32),
            (0, "area_raw", "ff" * 8),
            (2, "owner_pointer", "888888"),
            (4, "area_pointer", "888888"),
            (5, "record_pointer", "888888"),
            (1, "index", 0),
            (6, "first", 0),
            (0, "item_record_pointer", "123456"),
            (1, "item_record_pointer", "400999"),
            (0, "record_pointer", "0"),
            (0, "owner_pointer", "bad"),
        )
        for position, field, value in changes:
            with self.subTest(position=position, field=field):
                self.reject(lambda t, p=position, f=field, v=value:
                            t["events"][p].__setitem__(f, v))
        def reused_owner(trace):
            # Keep the whole second container block internally consistent;
            # sharing an owner with container A still cannot be one load.
            first_owner = trace["events"][0]["owner_pointer"]
            for row in trace["events"][4:7]:
                row["owner_pointer"] = first_owner
        self.reject(reused_owner)


if __name__ == "__main__":
    unittest.main()
