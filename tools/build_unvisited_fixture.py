"""Stage a diagnostic, unvisited ARE from an explicitly supplied BG2600.ARE copy.

This does not select content from the original area or install anything. The
output keeps the input WED/art references and all original section bytes; only
the documented header fields and validated rest block change. Engine loading
and accessibility remain separate native checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from flir_config.world_formats import parse_are


MAX_INPUT_BYTES = 64 * 1024 * 1024
TARGETS = (("FLPRU1A", 1140, 650, 1, 2),
           ("FLPRU1B", 1200, 650, 3, 1),
           ("FLPRU1C", 1260, 650, 4, 0))
ITEMS = ("FLPRX1", "FLPRI1", "FLPRX1", "FLPRI2")
AREA_FILE = "FLPRU1.ARE"
REPORT_FILE = "unvisited-fixture-report.json"


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _item(name: str) -> bytes:
    return struct.pack("<8s4HI", name.encode("ascii"), 0, 0, 0, 0, 1)


def _container(name: str, x: int, y: int, first: int, count: int) -> bytes:
    record = bytearray(0xC0)
    struct.pack_into("<32s3H", record, 0, name.encode("ascii"), x, y, 4)
    struct.pack_into("<4H", record, 0x38, x - 12, y - 8, x + 12, y + 8)
    struct.pack_into("<II", record, 0x40, first, count)
    struct.pack_into("<I", record, 0x84, 0xFFFFFFFF)
    return bytes(record)


def make_unvisited_area(original: bytes) -> bytes:
    """Return a new area with only four authored items and three active piles."""
    if len(original) > MAX_INPUT_BYTES:
        raise ValueError("Input ARE exceeds the bounded fixture size")
    parse_are(original)  # Includes signature, section and item-run bounds.
    if b"FLPR" in original.upper():
        raise ValueError("Input ARE already contains an FLPR fixture marker")
    rest_offset = _u32(original, 0xC0)
    if rest_offset and rest_offset + 0xE4 > len(original):
        raise ValueError("Invalid ARE rest encounter bounds")

    result = bytearray(original)
    struct.pack_into("<HH", result, 0x58, 0, 0)  # Actors, regions.
    struct.pack_into("<I", result, 0x64, 0)      # Spawn points.
    result[0x94:0x9C] = bytes(8)                 # No area script.
    if rest_offset:
        result[rest_offset:rest_offset + 0xE4] = bytes(0xE4)

    container_offset = len(result)
    for target in TARGETS:
        result.extend(_container(*target))
    item_offset = len(result)
    for name in ITEMS:
        result.extend(_item(name))
    if len(result) > 0xFFFFFFFF:
        raise ValueError("Fixture ARE exceeds 32-bit section offsets")
    struct.pack_into("<IH", result, 0x70, container_offset, len(TARGETS))
    struct.pack_into("<HI", result, 0x76, len(ITEMS), item_offset)

    parsed = parse_are(result)
    if (parsed["script"] or parsed["actors"] or
            len(parsed["containers"]) != 3 or len(parsed["items"]) != 4 or
            parsed["orphan_item_indexes"] != [0]):
        raise ValueError("Generated ARE failed fixture validation")
    return bytes(result)


def _report(original: bytes, area: bytes, input_path: Path, output: Path) -> dict:
    parsed = parse_are(area)
    containers = []
    for row in parsed["containers"]:
        offset = row["byte_offset"]
        containers.append({"record_index": row["record_index"], "byte_offset": offset,
            "raw_hex": area[offset:offset + 0xC0].hex(), "name": row["name"],
            "type": row["type"], "x": row["x"], "y": row["y"],
            "first_item_index": row["first_item_index"], "item_count": row["item_count"],
            "item_indexes": row["item_indexes"]})
    items = []
    for row in parsed["items"]:
        offset = row["byte_offset"]
        items.append({"absolute_index": row["record_index"], "byte_offset": offset,
            "raw_hex": area[offset:offset + 20].hex(), "resref": row["resref"],
            "expiration": row["expiration"], "charges": row["charges"],
            "flags": row["flags"], "container_indexes": row["container_indexes"]})
    return {"state": "staged-not-installed", "purpose": "unvisited-area-native-diagnostic",
        "area": "FLPRU1", "input": {"path": str(input_path), "name": input_path.name,
            "size": len(original), "sha256": hashlib.sha256(original).hexdigest()},
        "output": {"path": str(output / "override" / AREA_FILE), "size": len(area),
            "sha256": hashlib.sha256(area).hexdigest()},
        "active_tables": {"container_offset": _u32(area, 0x70), "container_count": 3,
            "item_offset": _u32(area, 0x78), "item_count": 4,
            "actor_count": 0, "region_count": 0, "spawn_count": 0,
            "orphan_item_indexes": parsed["orphan_item_indexes"]},
        "containers": containers, "items": items,
        "limitations": ["Uses the input area's existing WED and art references",
            "Native engine loading, pile accessibility and save persistence are unverified"]}


def build(area_file: Path, output: Path) -> dict:
    area_file = area_file.resolve(strict=True)
    output = output.resolve()
    if not area_file.is_file() or area_file.name.upper() != "BG2600.ARE":
        raise ValueError("Input must be an explicitly supplied BG2600.ARE file")
    input_parent = area_file.parent
    input_tree = input_parent.parent if input_parent.name.lower() == "override" else input_parent
    if output.is_relative_to(input_tree) or input_tree.is_relative_to(output):
        raise ValueError("Fixture output must be outside, and not contain, the input parent/game tree")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Fixture output must be absent or empty; never overwrite existing files")
    if area_file.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("Input ARE exceeds the bounded fixture size")
    original = area_file.read_bytes()
    area = make_unvisited_area(original)
    report = _report(original, area, area_file, output)
    destination = output / "override"
    destination.mkdir(parents=True, exist_ok=False)
    with (destination / AREA_FILE).open("xb") as target:
        target.write(area)
    with (output / REPORT_FILE).open("xb") as target:
        target.write((json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--area-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.area_file, args.output), indent=2))


if __name__ == "__main__":
    main()
