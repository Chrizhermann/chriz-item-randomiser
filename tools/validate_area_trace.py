"""Validate stopped FLPRU1 load diagnostics against exact copied ARE inputs.

Each expected input represents one complete load, in observed order. A saved
ARE can have different physical indexes from its pristine resource. This checks
those indexes against that load's actual input; it never treats them as durable
item identities or proves suppression, lootability, or runtime item pointers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

from flir_config.world_formats import parse_are


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _integer(value):
    return type(value) is int and 0 <= value <= 0xFFFFFFFF


def _pointer(value):
    return isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value) is not None


def validate_trace(snapshot: dict, area_inputs: list[bytes]) -> dict:
    _require(isinstance(snapshot, dict), "Trace must be an object")
    _require(snapshot.get("schema") == "flpr-area-load-trace-v1"
             and snapshot.get("installed") is True and snapshot.get("stable") is True
             and snapshot.get("armed") is False and snapshot.get("overflow") is False,
             "Trace must be installed, stopped, stable, and loss-free")
    events = snapshot.get("events")
    _require(isinstance(events, list) and 0 < len(events) <= 2048, "Invalid trace event count")
    _require(isinstance(area_inputs, list) and area_inputs, "Expected area inputs are required")
    for sequence, row in enumerate(events, 1):
        _require(isinstance(row, dict) and type(row.get("sequence")) is int
                 and row["sequence"] == sequence, "Trace sequence gap")
        _require(type(row.get("kind")) is int and row["kind"] in (1, 2, 3), "Invalid event kind")
        _require(all(_pointer(row.get(k)) for k in ("area_pointer", "owner_pointer", "record_pointer")),
                 "Invalid event pointer")
        _require(all(_integer(row.get(k)) for k in ("index", "first", "count", "x", "y", "container_type", "lock")),
                 "Invalid event integer")
        _require(row.get("area_raw") == b"FLPRU1\0\0".hex(), "Wrong area in trace")

    cursor, loads = 0, []
    for data in area_inputs:
        parsed = parse_are(data)
        containers = parsed["containers"]
        _require(containers, "Expected area has no containers")
        load_area, container_base, item_base = None, None, None
        owners = set()
        start = cursor + 1
        for ordinal, container in enumerate(containers):
            _require(cursor < len(events), "Incomplete area load")
            begin = events[cursor]
            _require(begin["kind"] == 1 and begin["index"] == ordinal, "Expected container entry/index")
            load_area = load_area or begin["area_pointer"]
            _require(begin["area_pointer"] == load_area, "Area changed during load")
            _require(begin["owner_pointer"] not in owners, "Container pointer reused within load")
            owners.add(begin["owner_pointer"])
            base = int(begin["record_pointer"]) - ordinal * 0xC0
            _require(base > 0, "Invalid container table base")
            container_base = container_base or base
            _require(base == container_base, "Container table/index mismatch")
            offset = container["byte_offset"]
            fixed = {
                "area_pointer": load_area, "owner_pointer": begin["owner_pointer"],
                "record_pointer": begin["record_pointer"],
                "name_raw": data[offset:offset+32].hex(),
                "first": container["first_item_index"], "count": container["item_count"],
                "x": container["x"], "y": container["y"], "container_type": container["type"],
                "lock": struct.unpack_from("<H", data, offset+0x26)[0],
            }
            block = [begin]
            cursor += 1
            item_offset = struct.unpack_from("<I", data, 0x78)[0]
            for index in container["item_indexes"]:
                _require(cursor < len(events), "Incomplete item record stream")
                row = events[cursor]
                _require(row["kind"] == 2 and row["index"] == index, "Wrong physical item index")
                _require(_pointer(row.get("item_record_pointer")), "Invalid raw item pointer")
                base = int(row["item_record_pointer"]) - index * 20
                _require(base > 0, "Invalid item table base")
                item_base = item_base or base
                _require(base == item_base, "Item table/index mismatch")
                _require(row.get("payload_raw") == data[item_offset+index*20:item_offset+(index+1)*20].hex(),
                         "Raw item payload mismatch")
                block.append(row)
                cursor += 1
            _require(cursor < len(events), "Missing container exit")
            end = events[cursor]
            _require(end["kind"] == 3 and end["index"] == ordinal, "Expected container exit/index")
            block.append(end)
            for row in block:
                _require(all(row.get(key) == value for key, value in fixed.items()),
                         "Container identity or raw metadata changed")
                if row["kind"] != 2:
                    _require(row.get("item_record_pointer") == "0" and row.get("payload_raw") == "00"*20,
                             "Unexpected payload on container boundary")
            cursor += 1
        loads.append({"first_sequence": start, "last_sequence": cursor, "area_pointer": load_area,
                      "input_sha256": hashlib.sha256(data).hexdigest(), "containers": len(containers),
                      "referenced_items": sum(c["item_count"] for c in containers),
                      "orphan_item_indexes": parsed["orphan_item_indexes"]})
    _require(cursor == len(events), "Unexpected extra events/loads")
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    return {"schema": "flpr-area-load-acceptance-v1", "scope": "raw-container-loading-only",
            "suppression_proven": False, "events": len(events), "loads": loads,
            "trace_sha256": hashlib.sha256(canonical).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--area-file", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = validate_trace(json.loads(args.trace.read_text(encoding="utf-8")),
                            [path.read_bytes() for path in args.area_file])
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
