"""Rewrite a campaign-private CRE inventory without moving other sections."""

from __future__ import annotations

import struct

from .world_formats import parse_cre


def _replacement_bytes(resref: str) -> bytes:
    if (not isinstance(resref, str) or not 1 <= len(resref) <= 8 or
            resref != resref.lower() or
            any(ord(char) < 33 or ord(char) > 126 or char in '/\\:*?"<>|'
                for char in resref)):
        raise ValueError(f"Invalid replacement item resref: {resref!r}")
    return resref.encode("ascii").ljust(8, b"\0")


def rewrite_inventory(data: bytes, edits: dict[int, str | None]) -> bytes:
    """Replace or remove exact physical item indexes, keeping CRE offsets fixed.

    Item records are compacted inside their original table allocation; vacated
    records are zeroed. The 38 inventory slot words are remapped. The following
    two words select a weapon slot and ability, rather than an item index.
    """
    cre = parse_cre(data)
    count = len(cre["items"])
    if not isinstance(edits, dict):
        raise TypeError("CRE inventory edits must be a dictionary")
    replacements: dict[int, bytes | None] = {}
    for index, replacement in edits.items():
        if type(index) is not int or not 0 <= index < count:
            raise ValueError(f"CRE item index out of range: {index!r}")
        replacements[index] = (None if replacement is None else
                               _replacement_bytes(replacement))
    if not replacements:
        return bytes(data)

    item_offset = cre["items_offset"]
    slot_offset = cre["item_slots_offset"]
    old_to_new: dict[int, int] = {}
    kept: list[bytes] = []
    for index in range(count):
        if index in replacements and replacements[index] is None:
            continue
        record = bytearray(data[item_offset + 20 * index:item_offset + 20 * (index + 1)])
        if index in replacements:
            record[:8] = replacements[index]
        old_to_new[index] = len(kept)
        kept.append(bytes(record))

    result = bytearray(data)
    result[item_offset:item_offset + 20 * count] = (
        b"".join(kept) + b"\0" * (20 * (count - len(kept))))
    struct.pack_into("<I", result, 0x2C0, len(kept))
    for slot, old_index in enumerate(cre["slots"]):
        new_index = 0xFFFF if old_index is None else old_to_new.get(old_index, 0xFFFF)
        struct.pack_into("<H", result, slot_offset + 2 * slot, new_index)

    selected = cre["selected_weapon"]
    if selected < 4 and cre["slots"][9 + selected] in replacements:
        selected_index = cre["slots"][9 + selected]
        if replacements[selected_index] is None:
            first_weapon = next((slot for slot in range(9, 13)
                                 if struct.unpack_from("<H", result, slot_offset + 2 * slot)[0]
                                 != 0xFFFF), None)
            replacement_selector = 1000 if first_weapon is None else first_weapon - 9
            if replacement_selector != selected:
                struct.pack_into("<HH", result, slot_offset + 76,
                                 replacement_selector, 0)
    return bytes(result)
