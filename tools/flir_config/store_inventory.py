"""Rewrite selected STO sale rows within their existing table allocation."""

from __future__ import annotations

import struct

from .world_formats import parse_sto


def rewrite_inventory(data: bytes, removals: set[int]) -> bytes:
    """Remove exact finite sale rows without shifting other STO sections."""
    store = parse_sto(data)
    items = store["items"]
    if not isinstance(removals, set):
        raise TypeError("STO inventory removals must be a set")
    for index in removals:
        if type(index) is not int or not 0 <= index < len(items):
            raise ValueError(f"STO sale index out of range: {index!r}")
        row = items[index]
        if row["quantity"] == 0 or row["infinite_supply_flag"] != 0:
            raise ValueError(f"STO sale row is not finite stock: {index!r}")
    if not removals:
        return bytes(data)

    offset = items[0]["byte_offset"]
    kept = [data[row["byte_offset"]:row["byte_offset"] + 28]
            for index, row in enumerate(items) if index not in removals]
    result = bytearray(data)
    result[offset:offset + 28 * len(items)] = (
        b"".join(kept) + b"\0" * (28 * len(removals)))
    struct.pack_into("<I", result, 0x38, len(kept))
    return bytes(result)
