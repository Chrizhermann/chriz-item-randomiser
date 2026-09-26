"""Independent byte-built GAM/ARE fixtures for source-gate trace acceptance."""

from copy import deepcopy
import hashlib
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from validate_source_gate import validate_gate_trace
from test_postinstall_world_formats import make_are


def gam(variables=()):
    data = bytearray(0xB4 + 84 * len(variables))
    data[:8] = b"GAMEV2.0"
    struct.pack_into("<II", data, 0x38, 0xB4, len(variables))
    for index, (name, value) in enumerate(variables):
        base = 0xB4 + index * 84
        struct.pack_into("<32sH", data, base, name.encode("ascii"), 1)
        struct.pack_into("<i", data, base + 0x28, value)
    return bytes(data)


def item(name):
    return struct.pack("<8s4HI", name.encode("ascii"), 0, 0, 0, 0, 1)


def area(with_original=True):
    names = ("FLPRX1", "FLPRI1", "FLPRX1", "FLPRI2") if with_original else ("FLPRI2", "FLPRX1")
    containers = [("FLPRU1A", 1, 2), ("FLPRU1B", 3, 1), ("FLPRU1C", 4, 0)] if with_original else [
        ("FLPRU1C", 0, 0), ("FLPRU1B", 0, 1), ("FLPRU1A", 1, 1)]
    data = make_are([item(name) for name in names], containers)
    offset = struct.unpack_from("<I", data, 0x70)[0]
    for ordinal, (name, _, _) in enumerate(containers):
        x, y = {"FLPRU1A": (1140, 650), "FLPRU1B": (1200, 650), "FLPRU1C": (1260, 650)}[name]
        base = offset + ordinal * 0xC0
        struct.pack_into("<HHHH", data, base + 0x20, x, y, 4, 0)
    return bytes(data)


def raw_rows(gam_bytes, area_bytes, mode, generation):
    """Generate native-shaped rows from raw byte offsets, without the validator."""
    gam_pointer = str(100000 + generation * 100000)
    common = {"generation": generation, "object_pointer": gam_pointer,
              "other_value": str(len(gam_bytes)), "name_raw": "00" * 32,
              "payload_raw": "00" * 20}
    rows = [dict(common, kind=1, mode=0, decision=0, index=0, detail1=0, detail2=0)]
    glob_count = struct.unpack_from("<I", gam_bytes, 0x3C)[0]
    rows.append(dict(common, kind=2, mode=mode, decision=1 if mode == 0 else 0,
                     index=glob_count, detail1=0 if mode == 0 else 1, detail2=mode))
    if area_bytes is None:
        return rows
    container_offset, count = struct.unpack_from("<IH", area_bytes, 0x70)
    item_offset = struct.unpack_from("<I", area_bytes, 0x78)[0]
    for ordinal in range(count):
        base = container_offset + ordinal * 0xC0
        first, number = struct.unpack_from("<II", area_bytes, base + 0x40)
        name = area_bytes[base:base + 32]
        packed_first_count = struct.unpack_from("<Q", area_bytes, base + 0x40)[0]
        packed_geometry = struct.unpack_from("<Q", area_bytes, base + 0x20)[0]
        for index in range(first, first + number):
            payload = area_bytes[item_offset + index * 20:item_offset + (index + 1) * 20]
            selected = (mode == 1 and name == b"FLPRU1A".ljust(32, b"\0") and
                        index == 1 and first == 1 and number == 2 and
                        packed_geometry == int.from_bytes(struct.pack("<4H", 1140, 650, 4, 0), "little") and
                        payload == item("FLPRI1"))
            rows.append({"kind": 3, "generation": generation, "mode": mode,
                         "decision": int(selected), "object_pointer": str(900000 + generation * 100000),
                         "other_value": str(500000 + generation * 100000 + ordinal * 1000),
                         "index": index, "name_raw": name.hex(),
                         "payload_raw": payload.hex(), "detail1": packed_first_count,
                         "detail2": packed_geometry})
    return rows


def snapshot(loads):
    rows = []
    for generation, (gam_bytes, area_bytes) in enumerate(loads, 1):
        name = b"FLPR_U1_MODE".ljust(32, b"\0")
        offset, count = struct.unpack_from("<II", gam_bytes, 0x38)
        values = [struct.unpack_from("<i", gam_bytes, offset + i * 84 + 0x28)[0]
                  for i in range(count) if gam_bytes[offset + i * 84:offset + i * 84 + 32] == name]
        mode = values[0] if values else 0
        rows.extend(raw_rows(gam_bytes, area_bytes, mode, generation))
    for sequence, row in enumerate(rows, 1):
        row["sequence"] = sequence
    return {"schema": "flpr-source-gate-trace-v1", "installed": True, "armed": False,
            "stable": True, "overflow": False, "mode": 0, "generation": len(loads),
            "skips": sum(row["decision"] for row in rows if row["kind"] == 3),
            "destroys": 0, "events": rows}


def renumber(trace):
    for sequence, row in enumerate(trace["events"], 1):
        row["sequence"] = sequence


class SourceGateValidationTests(unittest.TestCase):
    def setUp(self):
        self.selected = (gam([("FLPR_U1_MODE", 1)]), area())
        self.excluded = (gam([("FLPR_U1_MODE", 2)]), area())
        self.missing = (gam(), None)
        self.loads = [self.selected, self.excluded, self.missing]
        self.trace = snapshot(self.loads)

    def reject(self, trace=None, loads=None):
        with self.assertRaises(ValueError):
            validate_gate_trace(trace if trace is not None else self.trace,
                                loads if loads is not None else self.loads)

    def test_selected_excluded_and_missing_modes_accept_only_raw_trace(self):
        result = validate_gate_trace(self.trace, self.loads)
        self.assertEqual((result["schema"], result["scope"], result["events"], result["skips"]),
                         ("flpr-source-gate-acceptance-v1", "trace-only", 12, 1))
        self.assertFalse(result["live_contents_proven"] or result["early_reward_proven"] or
                         result["new_campaign_proven"])
        self.assertEqual([(load["generation"], load["mode"], load["item_rows"],
                           load["expected_skips"], load["first_sequence"], load["last_sequence"])
                          for load in result["loads"]],
                         [(1, 1, 3, 1, 1, 5), (2, 2, 3, 0, 6, 10),
                          (3, 0, 0, 0, 11, 12)])
        self.assertEqual(result["loads"][0]["gam_sha256"], hashlib.sha256(self.selected[0]).hexdigest())
        self.assertEqual(result["loads"][1]["area_sha256"], hashlib.sha256(self.excluded[1]).hexdigest())
        self.assertIsNone(result["loads"][2]["area_sha256"])

    def test_selected_mode_with_serialized_area_missing_original_has_no_skip(self):
        load = (gam([("FLPR_U1_MODE", 1)]), area(with_original=False))
        trace = snapshot([load])
        result = validate_gate_trace(trace, [load])
        self.assertEqual((result["loads"][0]["item_rows"], result["skips"]), (2, 0))
        trace["events"][2]["decision"] = 1
        trace["skips"] = 1
        self.reject(trace, [load])

    def test_full_width_global_name_and_excluded_decision(self):
        alias = (gam([("FLPR_U1_MODE_X", 1)]), area())
        trace = snapshot([alias])
        result = validate_gate_trace(trace, [alias])
        self.assertEqual((result["loads"][0]["mode"], result["skips"]), (0, 0))
        trace = snapshot([self.excluded])
        trace["events"][2]["decision"] = 1
        trace["skips"] = 1
        self.reject(trace, [self.excluded])

    def test_area_and_owner_fields_cannot_be_swapped(self):
        trace = deepcopy(self.trace)
        for row in trace['events']:
            if row['kind'] == 3:
                row['object_pointer'], row['other_value'] = row['other_value'], row['object_pointer']
        self.reject(trace)

    def test_actual_emulated_rows_match_the_validator_contract(self):
        # Cross-check the actual producer instructions. A hand-written trace
        # oracle alone previously inverted area/owner together with the reader.
        from test_source_gate_assembly import GateMachine, BUFFER, INPUT, CONTAINER, ITEMS, OWNER, STACK
        machine = GateMachine()
        gam_bytes, area_bytes = self.selected
        machine.write(INPUT, gam_bytes)
        machine.run('enter', length=len(gam_bytes))
        machine.run('capture')
        container_offset, count = struct.unpack_from('<IH', area_bytes, 0x70)
        item_count, item_offset = struct.unpack_from('<HI', area_bytes, 0x76)
        machine.write(ITEMS, area_bytes[item_offset:item_offset+item_count*20])
        for ordinal in range(count):
            start = container_offset + ordinal*0xC0
            machine.write(CONTAINER, area_bytes[start:start+0xC0])
            machine.write_q(STACK+0x8000-0x68, OWNER+ordinal*256)
            first, number = struct.unpack_from('<II', area_bytes, start+0x40)
            for index in range(first, first+number):
                machine.run('item', index=index)
        rows=[]
        for index in range(machine.read_q(BUFFER+8)):
            row=BUFFER+128+index*128
            rows.append(dict(sequence=index+1, kind=machine.read_q(row),
                generation=machine.read_q(row+8),mode=machine.read_q(row+16),
                decision=machine.read_q(row+24),object_pointer=str(machine.read_q(row+32)),
                other_value=str(machine.read_q(row+40)),index=machine.read_q(row+48),
                name_raw=bytes(machine.uc.mem_read(row+56,32)).hex(),
                payload_raw=bytes(machine.uc.mem_read(row+88,20)).hex(),
                detail1=machine.read_q(row+112),detail2=machine.read_q(row+120)))
        trace=dict(schema='flpr-source-gate-trace-v1', installed=True, armed=False,
            stable=True,overflow=False,mode=0,generation=1,destroys=0,
            skips=machine.read_q(BUFFER+96),events=rows)
        report=validate_gate_trace(trace,[self.selected])
        self.assertEqual((report['events'],report['skips']),(5,1))

    def test_missing_extra_and_reordered_events_are_rejected_without_resync(self):
        for position in (0, 1, 2, 4, 5, 11):
            with self.subTest(missing=position):
                trace = deepcopy(self.trace)
                trace["events"].pop(position)
                renumber(trace)
                self.reject(trace)
        trace = deepcopy(self.trace)
        trace["events"].append(deepcopy(trace["events"][-1]))
        renumber(trace)
        self.reject(trace)
        trace = deepcopy(self.trace)
        trace["events"][2], trace["events"][3] = trace["events"][3], trace["events"][2]
        renumber(trace)
        self.reject(trace)

    def test_payload_index_mode_pointer_and_count_drift_are_rejected(self):
        edits = ((2, "payload_raw", "ff" * 20), (2, "index", 0),
                 (2, "name_raw", "ff" * 32), (2, "mode", 2),
                 (2, "object_pointer", "0"), (3, "object_pointer", "123456"),
                 (3, "other_value", "123456"), (2, "detail1", 999),
                 (2, "detail2", 999), (1, "index", 2),
                 (1, "detail1", 0), (1, "detail2", 2),
                 (0, "other_value", "999"), (0, "generation", 2),
                 (2, "decision", 3), (2, "decision", 2))
        for position, field, value in edits:
            with self.subTest(position=position, field=field, value=value):
                trace = deepcopy(self.trace)
                trace["events"][position][field] = value
                self.reject(trace)
        for field, value in (("generation", 2), ("mode", 1), ("skips", 0)):
            trace = deepcopy(self.trace)
            trace[field] = value
            self.reject(trace)
        trace = deepcopy(self.trace)
        trace["events"][1]["object_pointer"] = "88888"
        self.reject(trace)

    def test_duplicate_invalid_and_malformed_gam_inputs_refuse_acceptance(self):
        duplicate = gam([("FLPR_U1_MODE", 1), ("FLPR_U1_MODE", 2)])
        invalid = gam([("FLPR_U1_MODE", 3)])
        with self.assertRaisesRegex(ValueError, "Duplicate FLPR_U1_MODE"):
            validate_gate_trace(snapshot([(duplicate, self.selected[1])]),
                                [(duplicate, self.selected[1])])
        with self.assertRaisesRegex(ValueError, "Invalid FLPR_U1_MODE value"):
            validate_gate_trace(snapshot([(invalid, self.selected[1])]),
                                [(invalid, self.selected[1])])
        bad_inputs = [duplicate, invalid, b"not a GAM",
                      gam([("FLPR_U1_MODE", 1)])[:-1]]
        too_many = bytearray(gam())
        struct.pack_into("<I", too_many, 0x3C, 16385)
        bad_inputs.append(bytes(too_many))
        bad_offset = bytearray(gam())
        struct.pack_into("<I", bad_offset, 0x38, 0)
        bad_inputs.append(bytes(bad_offset))
        for bad in bad_inputs:
            with self.subTest(length=len(bad)):
                self.reject(loads=[(bad, self.selected[1])])

    def test_unstable_overflow_or_incomplete_status_rejected(self):
        for field, value in (("installed", False), ("armed", True),
                             ("stable", False), ("overflow", True),
                             ("schema", "wrong")):
            with self.subTest(field=field):
                trace = deepcopy(self.trace)
                trace[field] = value
                self.reject(trace)
        trace = deepcopy(self.trace)
        trace["events"] = trace["events"] * 171
        renumber(trace)
        self.reject(trace)


if __name__ == "__main__":
    unittest.main()
