"""Read physical inventories from BG:EE/BG2:EE CRE/ARE/STO V1 resources.

These are structural observations, not proof that a template spawns, a record
becomes an obtainable item, or all scripted/dynamic grants have been found.
Byte offsets refer to the supplied resource; embedded CRE offsets are rebased
to their enclosing ARE. Item indexes always refer to their own item table.

Primary layouts: https://gibberlings3.github.io/iesdp/file_formats/ie_formats/
cre_v1.htm, are_v1.htm, sto_v1.htm. Only BG-family V1 layouts are supported.
GemRB's AREImporter::GetActor also documents the external-resref fallback for
actors with no embedded offset; CREImporter::ReadInventory preserves repeated
slot indexes. Conflicting active storage and overlapping embedded byte spans
remain unsupported ambiguity, rather than being guessed or silently skipped.
"""

from __future__ import annotations

import struct

from .resources import ScanError


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _header(data: bytes, signature: bytes, size: int) -> None:
    if len(data) < size:
        raise ScanError(f"Truncated {signature[:4].decode('ascii').strip()} header.")
    if data[:8] != signature:
        raise ScanError(f"Unsupported world resource format; expected {signature.decode('ascii')}.")


def _text(data: bytes, offset: int, size: int) -> str:
    # Non-TLK object names have no encoding tag. Latin-1 preserves every byte;
    # these labels must not be used as unique identities or localized names.
    return data[offset:offset + size].split(b"\0", 1)[0].decode("latin-1")


def _reference(data: bytes, offset: int, *, optional: bool = False,
               embedded_marker: bool = False) -> str:
    raw = data[offset:offset + 8]
    body, terminator, padding = raw.partition(b"\0")
    if terminator and padding.strip(b"\0"):
        raise ScanError("A world resource reference has nonzero bytes after its terminator.")
    if optional and not body:
        return ""
    try:
        name = body.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ScanError("A world resource reference is not ASCII.") from exc
    checked = name[1:] if embedded_marker and name.startswith("*") else name
    if not checked or any(ord(char) < 33 or ord(char) > 126 or char in '/\\:*?"<>|' for char in checked):
        raise ScanError("Invalid world resource reference.")
    return name.lower()


class _Sections:
    """Bounds-check nonempty sections and reject overlapping byte ownership."""

    def __init__(self, data: bytes, header_size: int):
        self.size = len(data)
        self.header_size = header_size
        self.spans: list[tuple[int, int, str]] = []

    def add(self, offset: int, count: int, stride: int, label: str) -> None:
        if not count:
            # Empty arrays have no bytes: writers may leave unused offsets.
            return
        length = count * stride
        if offset < self.header_size:
            raise ScanError(f"{label} overlaps its resource header.")
        if offset > self.size or length > self.size - offset:
            raise ScanError(f"Invalid or truncated {label}.")
        self.spans.append((offset, offset + length, label))

    def verify(self) -> None:
        previous_end, previous_label = 0, ""
        for start, end, label in sorted(self.spans):
            if start < previous_end:
                raise ScanError(f"Overlapping world sections: {previous_label} and {label}.")
            previous_end, previous_label = end, label


def _items(data: bytes, offset: int, count: int, stride: int, *, allow_invalid: bool = False) -> list[dict]:
    # An all-NUL reference is an observed empty physical record, not an item.
    # Keep its payload and index: later slot/container references still use the
    # original table. Malformed names fail by default; creature discovery may
    # preserve their exact eight bytes for quarantine without guessing a name.
    result = []
    for index in range(count):
        base = offset + index * stride
        row = {"record_index": index, "byte_offset": base,
               "expiration": _u16(data, base + 8),
               "charges": [_u16(data, base + at) for at in (10, 12, 14)],
               "flags": _u32(data, base + 16)}
        try:
            row["resref"] = _reference(data, base, optional=True)
        except ScanError:
            if not allow_invalid:
                raise
            row["resref"] = ""
            row["raw_resref_hex"] = data[base:base + 8].hex()
        result.append(row)
    return result


def parse_cre(data: bytes, *, allow_dangling_slots: bool = False,
              allow_invalid_items: bool = False) -> dict:
    """Read a CRE; only discovery may opt into retaining dangling slot words."""
    _header(data, b"CRE V1.0", 0x2D4)
    sections = _Sections(data, 0x2D4)
    for off, stride, label in (
        (0x2A0, 12, "CRE known spells"), (0x2A8, 16, "CRE spell memorization info"),
        (0x2B0, 12, "CRE memorized spells"),
    ):
        sections.add(_u32(data, off), _u32(data, off + 4), stride, label)
    slots_offset, items_offset, count = (_u32(data, off) for off in (0x2B8, 0x2BC, 0x2C0))
    sections.add(slots_offset, 40, 2, "CRE item slots")
    sections.add(items_offset, count, 20, "CRE items")
    effect_count = _u32(data, 0x2C8)
    effect_version = data[0x33]
    if effect_version not in (0, 1):
        raise ScanError("Unsupported CRE effect structure version.")
    sections.add(_u32(data, 0x2C4), effect_count, 48 if effect_version == 0 else 264, "CRE effects")
    sections.verify()

    items = _items(data, items_offset, count, 20, allow_invalid=allow_invalid_items)
    for item in items:
        item["slots"] = []
    slots: list[int | None] = []
    dangling = False
    for slot in range(38):
        item_index = _u16(data, slots_offset + slot * 2)
        if item_index == 0xFFFF:
            slots.append(None)
        elif item_index >= count:
            if not allow_dangling_slots:
                raise ScanError(f"CRE item slot {slot} refers to an absent item record: "
                                f"index {item_index}, table count {count}.")
            slots.append(item_index)
            dangling = True
        else:
            slots.append(item_index)
            items[item_index]["slots"].append(slot)
    result = {
        "format": "CRE V1.0", "name_strref": _u32(data, 8),
        "script_name": _text(data, 0x280, 32),
        "items": items, "slots": slots, "item_slots_offset": slots_offset,
        "items_offset": items_offset,
        "selected_weapon": _u16(data, slots_offset + 76),
        "selected_weapon_ability": _u16(data, slots_offset + 78),
        "unslotted_item_indexes": [item["record_index"] for item in items if not item["slots"]],
        "aliased_item_indexes": [item["record_index"] for item in items if len(item["slots"]) > 1],
    }
    invalid_items = [row["record_index"] for row in items if "raw_resref_hex" in row]
    if dangling or invalid_items:
        # Keep the header values and complete raw table. Do not invent a source
        # row or reinterpret an absent index as the empty-slot sentinel.
        result["inventory_diagnostics"] = {
            "cre_offset": 0, "item_count": count, "items_offset": items_offset,
            "item_slots_offset": slots_offset,
            "slot_words": [_u16(data, slots_offset + slot * 2) for slot in range(40)],
        }
        if invalid_items:
            result["inventory_diagnostics"]["invalid_item_indexes"] = invalid_items
    return result


def _are_sections(data: bytes) -> _Sections:
    sections = _Sections(data, 0x11C)
    # Header offsets, count offsets, count widths, record sizes. Reading other
    # section spans prevents accepting an inventory that aliases an actor,
    # door, script variable, or other structural bytes. Their content is not
    # interpreted as gameplay instructions.
    for off, count_off, width, stride, label in (
        (0x54, 0x58, 2, 0x110, "actors"), (0x5C, 0x5A, 2, 0xC4, "regions"),
        (0x60, 0x64, 4, 0xC8, "spawn points"), (0x68, 0x6C, 4, 0x68, "entrances"),
        (0x70, 0x74, 2, 0xC0, "containers"), (0x78, 0x76, 2, 0x14, "items"),
        (0x7C, 0x80, 2, 4, "vertices"), (0x84, 0x82, 2, 0xD4, "ambients"),
        (0x88, 0x8C, 4, 0x54, "variables"), (0xA0, 0x9C, 4, 1, "explored bitmask"),
        (0xA8, 0xA4, 4, 0xC8, "doors"), (0xB0, 0xAC, 4, 0x4C, "animations"),
        (0xB8, 0xB4, 4, 0x68, "tiled objects"), (0xC4, 0xC8, 4, 0x34, "automap notes"),
        (0xCC, 0xD0, 4, 0x1C, "projectile traps"),
    ):
        count = (_u16 if width == 2 else _u32)(data, count_off)
        sections.add(_u32(data, off), count, stride, f"ARE {label}")
    for off, size, label in ((0xBC, 0x90, "song entries"), (0xC0, 0xE4, "rest interruptions")):
        offset = _u32(data, off)
        sections.add(offset, int(offset != 0), size, f"ARE {label}")
    sections.verify()
    # A projectile effect block is outside its fixed-size record table.
    for index in range(_u32(data, 0xD0)):
        base = _u32(data, 0xCC) + index * 0x1C
        sections.add(_u32(data, base + 8), _u16(data, base + 12), 1, "ARE projectile effects")
    return sections


def parse_are(data: bytes, *, allow_dangling_slots: bool = False,
              allow_invalid_items: bool = False, allow_incomplete_actors: bool = False) -> dict:
    """Read ARE V1.0 physical records, preserving unresolved ownership."""
    _header(data, b"AREAV1.0", 0x11C)
    sections = _are_sections(data)
    count, offset = _u16(data, 0x76), _u32(data, 0x78)
    items = _items(data, offset, count, 20)
    for item in items:
        item["container_indexes"] = []
    containers = []
    for index in range(_u16(data, 0x74)):
        base = _u32(data, 0x70) + index * 0xC0
        first, number = _u32(data, base + 0x40), _u32(data, base + 0x44)
        if number and (first >= count or number > count - first):
            raise ScanError(f"ARE container {index} refers outside its item table.")
        indexes = list(range(first, first + number))
        for item_index in indexes:
            items[item_index]["container_indexes"].append(index)
        name = _text(data, base, 32)
        containers.append({
            "record_index": index, "byte_offset": base, "name": name,
            "x": _u16(data, base + 0x20), "y": _u16(data, base + 0x22),
            "type": _u16(data, base + 0x24), "flags": _u32(data, base + 0x28),
            "first_item_index": first, "item_count": number, "item_indexes": indexes,
            "items": [items[i] for i in indexes], "owner": _text(data, base + 0x58, 32),
            "trap_script": _reference(data, base + 0x48, optional=True),
            "dynamic_item_table": name if name.upper().startswith("T2") else None,
        })
    for item in items:
        owners = item["container_indexes"]
        item["container_index"] = owners[0] if len(owners) == 1 else None

    actors = []
    for index in range(_u16(data, 0x58)):
        base = _u32(data, 0x54) + index * 0x110
        flags = _u32(data, base + 0x28)
        cre_offset, cre_size = _u32(data, base + 0x88), _u32(data, base + 0x8C)
        if (flags & 1) and (cre_offset or cre_size):
            raise ScanError(f"ARE actor {index} has conflicting external and embedded CRE storage.")
        incomplete = bool(cre_offset) != bool(cre_size)
        if incomplete and not allow_incomplete_actors:
            raise ScanError(f"ARE actor {index} has incomplete embedded CRE storage.")
        # With no stored CRE, importers can use the external resref even if the
        # unloaded flag was not set. Do not manufacture an embedded inventory
        # from that flag alone. Any actual blob must have both offset and size.
        external = not cre_offset and not incomplete
        if not incomplete:
            sections.add(cre_offset, cre_size, 1, f"ARE actor {index} embedded CRE")
        actors.append({
            "record_index": index, "byte_offset": base, "name": _text(data, base, 32),
            "x": _u16(data, base + 0x20), "y": _u16(data, base + 0x22),
            "flags": flags, "appearance_schedule": _u32(data, base + 0x40),
            "cre_resref": _reference(data, base + 0x80, optional=not external, embedded_marker=not external),
            "embedded_cre_offset": cre_offset, "embedded_cre_size": cre_size,
            "embedded_cre": None,
        })
        if incomplete:
            # Preserve the raw offset/size instead of guessing a blob boundary
            # or silently treating this actor as a valid external template.
            actors[-1]["storage_diagnostics"] = {"reason": "incomplete-embedded-cre"}
    sections.verify()
    for actor in actors:
        if not actor["embedded_cre_size"] or "storage_diagnostics" in actor:
            continue
        start, size = actor["embedded_cre_offset"], actor["embedded_cre_size"]
        try:
            cre = parse_cre(data[start:start + size], allow_dangling_slots=allow_dangling_slots,
                            allow_invalid_items=allow_invalid_items)
        except ScanError as exc:
            raise ScanError(f"ARE actor {actor['record_index']} embedded CRE: {exc}") from exc
        cre["item_slots_offset"] += start
        if "inventory_diagnostics" in cre:
            cre["inventory_diagnostics"]["cre_offset"] = start
        # Empty item tables have no meaningful offset to rebase.
        if cre["items"]:
            cre["items_offset"] += start
        for item in cre["items"]:
            item["byte_offset"] += start
        actor["embedded_cre"] = cre
    return {
        "format": "ARE V1.0", "script": _reference(data, 0x94, optional=True),
        "items": items, "containers": containers, "actors": actors,
        "orphan_item_indexes": [item["record_index"] for item in items if not item["container_indexes"]],
        "aliased_item_indexes": [item["record_index"] for item in items if len(item["container_indexes"]) > 1],
    }


def parse_sto(data: bytes) -> dict:
    """Read store stock, keeping quantities and raw infinite-supply flags."""
    _header(data, b"STORV1.0", 0x9C)
    sections = _Sections(data, 0x9C)
    for off, stride, label in (
        (0x2C, 4, "purchased categories"), (0x34, 28, "sale items"),
        (0x4C, 20, "drinks"), (0x70, 12, "cures"),
    ):
        sections.add(_u32(data, off), _u32(data, off + 4), stride, f"STO {label}")
    sections.verify()
    items = _items(data, _u32(data, 0x34), _u32(data, 0x38), 28)
    for item in items:
        base = item["byte_offset"]
        item["quantity"] = _u32(data, base + 20)
        item["infinite_supply_flag"] = _u32(data, base + 24)
        item["infinite_supply"] = bool(item["infinite_supply_flag"])
    return {
        "format": "STO V1.0", "name_strref": _u32(data, 12),
        "type": _u32(data, 8), "flags": _u32(data, 16), "items": items,
    }
