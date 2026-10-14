"""Read DLG tables and append game text, in memory.

Shared by the Kangaxx, Mode 2, container and cursed-disguise adapters. The
caller owns writing the returned bytes.
"""
from __future__ import annotations

import struct

from .resources import ScanError, _Tlk


class GameTextError(ValueError):
    pass


def _table(data: bytes, offset: int, count: int, stride: int) -> list[bytes]:
    if offset < 0x30 or offset + count * stride > len(data):
        raise GameTextError("Unsupported dialogue table layout")
    return [data[offset + stride * i:offset + stride * (i + 1)] for i in range(count)]


def _string(data: bytes, pair: bytes) -> bytes:
    offset, length = struct.unpack("<II", pair)
    if offset < 0x30 or offset + length > len(data):
        raise GameTextError("Unsupported dialogue string layout")
    return data[offset:offset + length]


def _append_texts(data: bytes, texts: tuple[str, ...]) -> tuple[bytes, list[int]]:
    """Append text-only entries, preserving all existing TLK entries and data."""
    try:
        tlk = _Tlk(data)
    except ScanError as exc:
        raise GameTextError(str(exc)) from exc
    table_end = 0x12 + tlk.count * 0x1A
    entries, strings, refs = bytearray(), bytearray(), []
    existing = {}
    for index in range(tlk.count):
        row = 0x12 + index * 0x1A
        flags, sound, volume, pitch, offset, length = struct.unpack_from("<H8sIIII", data, row)
        if flags == 1 and not sound.strip(b"\0") and volume == pitch == 0:
            existing.setdefault(data[tlk.offset + offset:tlk.offset + offset + length], index)
    for text in texts:
        raw = text.encode("utf-8")
        if raw not in existing:
            existing[raw] = tlk.count + len(entries) // 0x1A
            entries.extend(struct.pack("<H8sIIII", 1, b"", 0, 0,
                                       len(data) - tlk.offset + len(strings), len(raw)))
            strings.extend(raw)
        refs.append(existing[raw])
    if not entries:
        return data, refs
    out = bytearray(data[:table_end] + entries + data[table_end:] + strings)
    struct.pack_into("<II", out, 0xA, tlk.count + len(entries) // 0x1A,
                     tlk.offset + len(entries))
    return bytes(out), refs
