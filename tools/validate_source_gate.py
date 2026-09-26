"""Validate a stopped source-gate trace against exact GAM and ARE inputs.

This accepts only the raw event sequence. Callers must separately establish
saved-area contents, reward state, and any live gameplay result.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct

from flir_config.world_formats import parse_are


MAX_EVENTS = 2048
MAX_GLOBALS = 16384
MAX_GAM_BYTES = 0x1000000
GLOBAL_NAME = b"FLPR_U1_MODE".ljust(32, b"\0")
SELECTED_NAME = b"FLPRU1A".ljust(32, b"\0")
SELECTED_PAYLOAD = struct.pack("<8s4HI", b"FLPRI1", 0, 0, 0, 0, 1)
ZERO_NAME = bytes(32).hex()
ZERO_PAYLOAD = bytes(20).hex()
POINTER = re.compile(r"[1-9][0-9]*\Z", re.ASCII)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def _uint(value: object) -> bool:
    return type(value) is int and 0 <= value <= 0xFFFFFFFFFFFFFFFF


def _pointer(value: object) -> bool:
    return isinstance(value, str) and POINTER.fullmatch(value) is not None and int(value) <= 0xFFFFFFFFFFFFFFFF


def _gam_mode(data: bytes) -> tuple[int, int]:
    _require(isinstance(data, bytes) and 0xB4 <= len(data) <= MAX_GAM_BYTES
             and data[:8] == b"GAMEV2.0", "Expected bounded GAMEV2.0 input")
    offset, count = struct.unpack_from("<II", data, 0x38)
    _require(count <= MAX_GLOBALS and offset >= 0xB4 and
             offset + count * 84 <= len(data),
             "Invalid GAM globals bounds")
    values = []
    for index in range(count):
        base = offset + index * 84
        if data[base:base + 32] == GLOBAL_NAME:
            values.append(struct.unpack_from("<i", data, base + 0x28)[0])
    _require(len(values) <= 1, "Duplicate FLPR_U1_MODE globals")
    _require(not values or values[0] in (1, 2), "Invalid FLPR_U1_MODE value")
    return (values[0] if values else 0), count


def _row(row: object, sequence: int) -> dict:
    _require(isinstance(row, dict) and row.get("sequence") == sequence
             and type(row.get("sequence")) is int, "Trace sequence gap")
    for key in ("kind", "generation", "mode", "decision", "index", "detail1", "detail2"):
        _require(_uint(row.get(key)), f"Invalid event {key}")
    for key in ("object_pointer", "other_value"):
        _require(_pointer(row.get(key)), f"Invalid event {key}")
    _require(isinstance(row.get("name_raw"), str) and
             re.fullmatch(r"[0-9a-f]{64}", row["name_raw"]) is not None,
             "Invalid raw container name")
    _require(isinstance(row.get("payload_raw"), str) and
             re.fullmatch(r"[0-9a-f]{40}", row["payload_raw"]) is not None,
             "Invalid raw item payload")
    return row


def _selected(container: dict, name_raw: bytes, item: bytes, index: int, lock: int) -> bool:
    return (name_raw == SELECTED_NAME and index == 1 and
            container["first_item_index"] == 1 and container["item_count"] == 2 and
            container["x"] == 1140 and container["y"] == 650 and
            container["type"] == 4 and lock == 0 and item == SELECTED_PAYLOAD)


def validate_gate_trace(snapshot: dict, loads: list[tuple[bytes, bytes | None]]) -> dict:
    """Accept only complete ordered trace rows for the supplied exact loads."""
    _require(isinstance(snapshot, dict), "Trace must be an object")
    _require(snapshot.get("schema") == "flpr-source-gate-trace-v1" and
             snapshot.get("installed") is True and snapshot.get("armed") is False and
             snapshot.get("stable") is True and snapshot.get("overflow") is False,
             "Trace must be installed, stopped, stable, and loss-free")
    _require(_uint(snapshot.get("mode")) and snapshot["mode"] == 0,
             "Source gate must be disarmed at stop")
    _require(isinstance(loads, list) and bool(loads), "Ordered exact load inputs are required")
    _require(_uint(snapshot.get("generation")) and snapshot["generation"] == len(loads),
             "Generation count does not match inputs")
    _require(_uint(snapshot.get("skips")) and _uint(snapshot.get("destroys")),
             "Invalid gate counters")
    events = snapshot.get("events")
    _require(isinstance(events, list) and 0 < len(events) <= MAX_EVENTS,
             "Invalid trace event count")
    for sequence, event in enumerate(events, 1):
        _row(event, sequence)

    cursor, total_skips, summaries = 0, 0, []
    for generation, pair in enumerate(loads, 1):
        _require(isinstance(pair, (tuple, list)) and len(pair) == 2,
                 "Each load requires exact GAM and optional ARE bytes")
        gam, area = pair
        mode, globals_count = _gam_mode(gam)
        _require(area is None or isinstance(area, bytes), "Expected exact ARE bytes or None")
        parsed = parse_are(area) if area is not None else None
        _require(cursor + 2 <= len(events), "Incomplete GAM entry/capture")
        first_sequence = cursor + 1
        entry, capture = events[cursor:cursor + 2]
        _require(entry["kind"] == 1 and capture["kind"] == 2,
                 "Expected GAM entry then capture")
        _require(entry["generation"] == capture["generation"] == generation,
                 "Wrong GAM generation")
        _require(entry["object_pointer"] == capture["object_pointer"] and
                 entry["other_value"] == capture["other_value"] and
                 int(entry["other_value"]) == len(gam),
                 "GAM pointer or byte length changed")
        _require(entry["mode"] == entry["decision"] == entry["index"] ==
                 entry["detail1"] == entry["detail2"] == 0 and
                 entry["name_raw"] == capture["name_raw"] == ZERO_NAME and
                 entry["payload_raw"] == capture["payload_raw"] == ZERO_PAYLOAD,
                 "Unexpected GAM entry/capture fields")
        expected_status = 1 if mode == 0 else 0
        expected_matches = 0 if mode == 0 else 1
        _require(capture["mode"] == mode and capture["decision"] == expected_status and
                 capture["index"] == globals_count and
                 capture["detail1"] == expected_matches and capture["detail2"] == mode,
                 "GAM capture does not match exact input globals")
        cursor += 2

        area_pointer = None
        owners = set()
        item_rows = expected_skips = 0
        if parsed is not None:
            item_offset = struct.unpack_from("<I", area, 0x78)[0]
            for container in parsed["containers"]:
                base = container["byte_offset"]
                name_raw = area[base:base + 32]
                lock = struct.unpack_from("<H", area, base + 0x26)[0]
                detail1 = struct.unpack_from("<Q", area, base + 0x40)[0]
                detail2 = struct.unpack_from("<Q", area, base + 0x20)[0]
                owner = None
                for index in container["item_indexes"]:
                    _require(cursor < len(events), "Incomplete raw item stream")
                    row = events[cursor]
                    _require(row["kind"] == 3 and row["generation"] == generation,
                             "Expected raw item row in load order")
                    _require(row["mode"] == mode and row["index"] == index and
                             row["name_raw"] == name_raw.hex() and
                             row["detail1"] == detail1 and row["detail2"] == detail2,
                             "Raw item container metadata/index mismatch")
                    item = area[item_offset + index * 20:item_offset + (index + 1) * 20]
                    _require(row["payload_raw"] == item.hex(), "Raw item payload mismatch")
                    area_pointer = area_pointer or row["object_pointer"]
                    owner = owner or row["other_value"]
                    _require(row["object_pointer"] == area_pointer and
                             row["other_value"] == owner,
                             "Area or container pointer changed within load")
                    selected = _selected(container, name_raw, item, index, lock)
                    skip = int(mode == 1 and selected)
                    _require(row["decision"] == skip, "Unexpected item decision")
                    expected_skips += skip
                    item_rows += 1
                    cursor += 1
                if owner is not None:
                    _require(owner not in owners, "Container pointer reused within load")
                    owners.add(owner)
        _require(expected_skips <= 1, "More than one selected original in a load")
        total_skips += expected_skips
        summaries.append({"generation": generation, "first_sequence": first_sequence,
            "last_sequence": cursor, "gam_sha256": hashlib.sha256(gam).hexdigest(),
            "area_sha256": hashlib.sha256(area).hexdigest() if area is not None else None,
            "mode": mode, "item_rows": item_rows, "expected_skips": expected_skips})
    _require(cursor == len(events), "Unexpected extra events or loads")
    _require(snapshot["skips"] == total_skips, "Skip counter does not match accepted rows")
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    return {"schema": "flpr-source-gate-acceptance-v1", "scope": "trace-only",
            "live_contents_proven": False, "early_reward_proven": False,
            "new_campaign_proven": False, "events": len(events), "skips": total_skips,
            "loads": summaries, "trace_sha256": hashlib.sha256(canonical).hexdigest()}
