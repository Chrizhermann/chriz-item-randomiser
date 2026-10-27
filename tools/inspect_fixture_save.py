"""Read one explicitly named Mode 1 fixture save without modifying any files.

GAM V2.0 / SAV V1.0 layouts follow IESDP. The fixture inspector decompresses
only BG2600.ARE; party inventories come from their embedded GAM CRE records. No resource search,
game directory, implicit save selection, or external CRE fallback is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import zlib

from flir_config.resources import ScanError
from flir_config.world_formats import parse_are, parse_cre

ITEMS = {f"flpri{index}" for index in range(1, 4)}
UNRELATED_ITEM = "flprx1"
SOURCE_NAMES = {f"flprs{index}" for index in range(1, 4)}
GROUND_TARGET_NAMES = {f"flprt{index}" for index in range(1, 4)}
CREATURE_TARGET_NAMES = {"flprc1", "flprc2"}
VARIANTS = ("ground-piles", "creatures")
MAX_FILE = 512 * 1024 * 1024
MAX_AREA = 64 * 1024 * 1024


def span(data: bytes, offset: int, size: int, label: str, minimum: int = 0) -> bytes:
    if offset < minimum or offset > len(data) or size > len(data) - offset:
        raise ScanError(f"Invalid or truncated {label}.")
    return data[offset:offset + size]


def number(data: bytes, offset: int, kind: str = "I") -> int:
    return struct.unpack("<" + kind, span(data, offset, struct.calcsize("<" + kind), "numeric field"))[0]


def text(data: bytes, offset: int, size: int) -> str:
    return span(data, offset, size, "text field").split(b"\0", 1)[0].decode("latin-1")


def fixture_items(items: list[dict], variant: str = "ground-piles") -> list[dict]:
    selected = ITEMS if variant == "ground-piles" else ITEMS | {UNRELATED_ITEM}
    records = [dict(item) for item in items if item["resref"] in selected]
    if variant == "creatures":
        # CItem's wear word is called expiration in the shared binary parser.
        # Expose both names so a held-item comparison can check it explicitly.
        for record in records:
            record["wear"] = record["expiration"]
    return records


def parse_gam(data: bytes, variant: str = "ground-piles") -> dict:
    # BG2:EE and EET save GAME V2.0 (V2.1 in Throne of Bhaal), BG:EE GAME
    # V1.1: the same header, variable records and 0x160-byte party records.
    if len(data) < 0xB4 or data[:8] not in (b"GAMEV2.0", b"GAMEV2.1", b"GAMEV1.1"):
        raise ScanError("Expected a complete GAME V2.0, V2.1 or V1.1 header.")
    offset, count = number(data, 0x38), number(data, 0x3C)
    if count:
        span(data, offset, count * 84, "GAM global variables", 0xB4)
    variables = {}
    for index in range(count):
        base = offset + 84 * index
        name = text(data, base, 32).upper()
        if not name.startswith("FLPR_"):
            continue
        if name in variables:
            raise ScanError(f"Duplicate fixture GLOBAL variable: {name}.")
        variables[name] = number(data, base + 0x28, "i")
    party_offset, party_count = number(data, 0x20), number(data, 0x24)
    if party_count > 6:
        raise ScanError("This fixture inspector supports at most six party members.")
    if party_count:
        span(data, party_offset, party_count * 0x160, "GAM party records", 0xB4)
    party = []
    spans = [(offset, offset + count * 84, "globals")] if count else []
    if party_count:
        spans.append((party_offset, party_offset + party_count * 0x160, "party table"))
    for index in range(party_count):
        base = party_offset + index * 0x160
        cre_offset, cre_size = number(data, base + 4), number(data, base + 8)
        if not cre_size:
            raise ScanError("Party member has no embedded CRE inventory.")
        cre = parse_cre(span(data, cre_offset, cre_size, "party CRE", 0xB4))
        spans.append((cre_offset, cre_offset + cre_size, f"party CRE {index}"))
        party.append({"party_record_index": index, "party_order": number(data, base + 2, "H"),
                      "script_name": cre["script_name"], "area": text(data, base + 0x18, 8).lower(),
                      "x": number(data, base + 0x20, "H"), "y": number(data, base + 0x22, "H"),
                      "fixture_items": fixture_items(cre["items"], variant),
                      "unslotted_item_indexes": cre["unslotted_item_indexes"],
                      "aliased_item_indexes": cre["aliased_item_indexes"]})
    previous_end = 0
    for start, end, label in sorted(spans):
        if start < previous_end:
            raise ScanError(f"Overlapping GAM sections at {label}.")
        previous_end = end
    chunks = [variables.get(f"FLPR_R{index:02}") for index in range(1, 17)]
    revision = "".join(f"{value:04x}" for value in chunks) if all(
        value is not None and 0 <= value <= 65535 for value in chunks) else None
    phase_labels = {1: "fresh", 2: "inflight", 3: "verified", 4: "quarantined"}
    phases = {}
    for prefix, label in (("S", "sources"), ("D", "deliveries")):
        labels = ({**phase_labels, 6: "protected", 7: "kept"} if prefix == "S"
                  else {**phase_labels, 5: "held", 7: "kept", 8: "lost", 9: "parked"})
        phases[label] = {name: {"stored": value, "phase": labels.get(value, "invalid")}
                         for name, value in sorted(variables.items()) if re.fullmatch(f"FLPR_{prefix}[0-9]{{4}}", name)}
    return {"format": "GAME " + data[4:8].decode("ascii"), "game_time": number(data, 8),
            "current_area_header": text(data, 0x58, 8).lower(),
            "active_party_area_index": number(data, 0x1C, "h"),
            "globals": dict(sorted(variables.items())), "revision": revision,
            "revision_chunks": chunks, "seed": variables.get("FLPR_SEED"),
            "state": variables.get("FLPR_STATE"), "done": variables.get("FLPR_DONE"),
            "fresh": variables.get("FLPR_FRESH"), "phases": phases,
            "assignments": {name: value for name, value in sorted(variables.items())
                            if re.fullmatch(r"FLPR_A[0-9]{4}", name)}, "party": party}


def extract_area(data: bytes, area_name: str) -> tuple[bytes | None, int]:
    """Read one SAV area by ASCII resref (1-8 letters/digits/underscores).

    The caller may supply the resref alone or append ``.are``. Member matching
    is exact and case-insensitive; other members are counted but not inflated.
    """
    if not isinstance(area_name, str) or not re.fullmatch(
            r"[A-Za-z0-9_]{1,8}(?:\.are)?", area_name, re.IGNORECASE | re.ASCII):
        raise ScanError("Expected a safe 1-8 character ARE resref or <resref>.are.")
    resref = area_name[:-4] if area_name.lower().endswith(".are") else area_name
    label = resref.upper()
    member_name = f"{resref.lower()}.are"
    if data[:8] != b"SAV V1.0":
        raise ScanError("Expected SAV V1.0.")
    cursor, entry_count, selected = 8, 0, None
    while cursor < len(data):
        length = number(data, cursor); cursor += 4
        if not 1 <= length <= 1024:
            raise ScanError("Invalid SAV filename length.")
        name_bytes = span(data, cursor, length, "SAV filename"); cursor += length
        if name_bytes[-1:] != b"\0" or b"\0" in name_bytes[:-1]:
            raise ScanError("Invalid SAV filename terminator.")
        try:
            name = name_bytes[:-1].decode("ascii").lower()
        except UnicodeDecodeError as exc:
            raise ScanError("Non-ASCII SAV filename.") from exc
        unpacked, packed = number(data, cursor), number(data, cursor + 4); cursor += 8
        compressed = span(data, cursor, packed, "SAV compressed member"); cursor += packed
        entry_count += 1
        if name != member_name:
            continue
        if selected is not None:
            raise ScanError(f"Duplicate {label}.ARE members in save.")
        if unpacked > MAX_AREA:
            raise ScanError(f"Saved {label} exceeds the inspector size limit.")
        stream = zlib.decompressobj()
        try:
            selected = stream.decompress(compressed, unpacked + 1)
        except zlib.error as exc:
            raise ScanError(f"Invalid {label} zlib stream.") from exc
        if not stream.eof or stream.unused_data or stream.unconsumed_tail or len(selected) != unpacked:
            raise ScanError(f"{label} compressed length or declared size does not match.")
    return selected, entry_count


def extract_bg2600(data: bytes) -> tuple[bytes | None, int]:
    """Compatibility wrapper for the existing BG2600 fixture inspection."""
    return extract_area(data, "BG2600")


def inspect_area(data: bytes, variant: str = "ground-piles") -> dict:
    area = parse_are(data)
    sources, targets, elsewhere = [], [], []
    for actor in area["actors"]:
        embedded = actor["embedded_cre"]
        identity = embedded["script_name"].lower() if embedded else actor["name"].lower()
        names = {identity, actor["name"].lower()}
        entry = {"actor_record_index": actor["record_index"], "actor_name": actor["name"],
                 "script_name": embedded["script_name"] if embedded else None,
                 "cre_resref": actor["cre_resref"], "x": actor["x"], "y": actor["y"],
                 "inventory_observed": embedded is not None,
                 "fixture_items": fixture_items(embedded["items"], variant) if embedded else None}
        if variant == "creatures":
            entry["all_item_count"] = len(embedded["items"]) if embedded else None
            entry["unslotted_item_indexes"] = embedded["unslotted_item_indexes"] if embedded else None
            entry["aliased_item_indexes"] = embedded["aliased_item_indexes"] if embedded else None
        if names & SOURCE_NAMES:
            sources.append(entry)
        elif variant == "creatures" and names & CREATURE_TARGET_NAMES:
            # Retain each occurrence: an actor name alone is not a unique owner.
            targets.append(entry)
        elif embedded and entry["fixture_items"]:
            elsewhere.append({"kind": "other_actor", **entry})
    for container in area["containers"]:
        selected = fixture_items(container["items"], variant)
        entry = {key: container[key] for key in ("record_index", "name", "type", "flags", "x", "y")}
        entry["fixture_items"] = selected
        entry["all_item_count"] = container["item_count"]
        if variant == "ground-piles" and container["name"].lower() in GROUND_TARGET_NAMES:
            targets.append(entry)
        elif selected:
            elsewhere.append({"kind": "other_container", **entry})
    orphans = fixture_items([area["items"][index] for index in area["orphan_item_indexes"]], variant)
    if orphans:
        elsewhere.append({"kind": "unowned_area_records", "fixture_items": orphans})
    return {"format": "ARE V1.0", "sources": sources, "targets": targets,
            "elsewhere_in_area": elsewhere, "aliased_item_indexes": area["aliased_item_indexes"],
            "area_item_record_count": len(area["items"])}


def inspect_save(save: Path, variant: str = "ground-piles") -> dict:
    if variant not in VARIANTS:
        raise ScanError(f"Unknown fixture variant: {variant}.")
    directory = save.resolve(strict=True)
    if not directory.is_dir():
        raise ScanError("The named save must be a directory.")
    paths = {}
    for filename in ("baldur.gam", "baldur.sav"):
        matches = [path for path in directory.iterdir() if path.name.lower() == filename]
        if len(matches) != 1 or not matches[0].is_file():
            raise ScanError(f"Expected one {filename} in the named save.")
        paths[filename] = matches[0]
    def stamp(path):
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns, stat.st_ino
    before = {name: stamp(path) for name, path in paths.items()}
    if any(size > MAX_FILE for size, _, _ in before.values()):
        raise ScanError("Save file exceeds the inspector size limit.")
    blobs = {name: path.read_bytes() for name, path in paths.items()}
    gam = parse_gam(blobs["baldur.gam"], variant)
    area_data, members = extract_bg2600(blobs["baldur.sav"])
    area = inspect_area(area_data, variant) if area_data is not None else None
    if before != {name: stamp(path) for name, path in paths.items()}:
        raise ScanError("Save changed during inspection; inspect after saving has completed.")
    groups = {"sources": area["sources"] if area else [], "targets": area["targets"] if area else [],
              "party": gam["party"], "elsewhere_in_area": area["elsewhere_in_area"] if area else []}
    selected_items = ITEMS if variant == "ground-piles" else ITEMS | {UNRELATED_ITEM}
    counts = {group: {item: sum(1 for owner in owners for record in (owner["fixture_items"] or [])
                              if record["resref"] == item) for item in sorted(selected_items)}
              for group, owners in groups.items()}
    result = {"schema": "flpr-fixture-save-observation-v1", "save_directory": str(directory),
            "files": {name: {"sha256": hashlib.sha256(blobs[name]).hexdigest(), "size": before[name][0],
                              "mtime_ns": before[name][1]} for name in paths},
            "gam": gam, "sav_member_count": members, "bg2600": area, "observed_record_counts": counts,
            "coverage": {"bg2600_saved": area is not None,
                         "expected_sources": {name: {"matching_actors": sum(1 for actor in groups["sources"]
                            if (actor["script_name"] or actor["actor_name"]).lower() == name),
                            "observed_inventories": sum(1 for actor in groups["sources"]
                            if (actor["script_name"] or actor["actor_name"]).lower() == name and actor["inventory_observed"])}
                            for name in sorted(SOURCE_NAMES)},
                         "expected_targets": ({name: {"matching_actors": sum(1 for actor in groups["targets"]
                             if name in {actor["actor_name"].lower(), (actor["script_name"] or "").lower()}),
                             "observed_inventories": sum(1 for actor in groups["targets"]
                             if name in {actor["actor_name"].lower(), (actor["script_name"] or "").lower()}
                             and actor["inventory_observed"])} for name in sorted(CREATURE_TARGET_NAMES)}
                             if variant == "creatures" else
                             {name: sum(1 for target in groups["targets"]
                              if target["name"].lower() == name) for name in sorted(GROUND_TARGET_NAMES)})},
            "limits": ["Counts describe serialized physical item records, not item usability or general lootability.",
                       "Missing BG2600 or external actor CREs are unobserved; no install template is substituted.",
                       "Only party CREs and saved BG2600 are inspected; other areas, stores and bag contents are excluded."]}
    if variant == "creatures":
        result["variant"] = variant
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save", required=True, type=Path, help="Exact isolated save directory to read.")
    parser.add_argument("--variant", choices=VARIANTS, default="ground-piles",
                        help="Observe the accepted ground piles or the two creature recipients.")
    args = parser.parse_args()
    try:
        result = inspect_save(args.save, args.variant)
    except (OSError, ScanError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
