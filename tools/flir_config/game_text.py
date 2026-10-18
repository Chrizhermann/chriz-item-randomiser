"""Read DLG tables and append game text, in memory.

Shared by the Kangaxx, extended reward, container and cursed-disguise adapters. The
caller owns writing the returned bytes.

A language's game text is its dialog.tlk and, in most languages other than
English, dialogf.tlk, which the engine reads instead for a female protagonist.
Both hold a text under the same number (WeiDU appends to both), so an appended
text gets one number in both files, with its female wording where it has one.
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import NamedTuple

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


# Flags, sound, volume and pitch of an appended entry: text only.
_TEXT_ONLY = struct.pack("<H8sII", 1, b"", 0, 0)


def _tlk(data: bytes) -> _Tlk:
    try:
        return _Tlk(data)
    except ScanError as exc:
        raise GameTextError(str(exc)) from exc


def _plain(data: bytes, tlk: _Tlk) -> dict[int, bytes]:
    """The text of every text-only entry (no sound), by number."""
    result = {}
    for index in range(tlk.count):
        flags, sound, volume, pitch, offset, length = struct.unpack_from("<H8sIIII", data, 0x12 + index * 0x1A)
        if flags == 1 and not sound.strip(b"\0") and volume == pitch == 0:
            result[index] = data[tlk.offset + offset:tlk.offset + offset + length]
    return result


def _extended(data: bytes, tlk: _Tlk, rows: list[tuple[bytes, bytes]]) -> bytes:
    """<data> with entries appended, each (flags, sound, volume and pitch packed, text)."""
    if not rows:
        return data
    table_end = 0x12 + tlk.count * 0x1A
    entries, strings = bytearray(), bytearray()
    for head, raw in rows:
        entries.extend(head + struct.pack("<II", len(data) - tlk.offset + len(strings), len(raw)))
        strings.extend(raw)
    out = bytearray(data[:table_end] + entries + data[table_end:] + strings)
    struct.pack_into("<II", out, 0xA, tlk.count + len(rows), tlk.offset + len(entries))
    return bytes(out)


class GameText(NamedTuple):
    """A language's dialog.tlk and its dialogf.tlk, if it has one."""
    male: bytes
    female: bytes | None = None

    def append(self, texts) -> tuple["GameText", list[int]]:
        """Append (male, female) texts as text-only entries, preserving all existing entries and data.

        An existing text-only entry is used again where it reads the same in
        both files; without dialogf.tlk the female wording is not used.
        """
        male = _tlk(self.male)
        female = None if self.female is None else _tlk(self.female)
        if female is not None and female.count != male.count:
            raise GameTextError("dialog.tlk and dialogf.tlk hold different numbers of strings")
        female_texts = None if female is None else _plain(self.female, female)
        existing = {}
        for index, raw in _plain(self.male, male).items():
            existing.setdefault((raw, None if female_texts is None else female_texts.get(index)), index)
        male_rows, female_rows, refs = [], [], []
        for male_text, female_text in texts:
            key = (male_text.encode("utf-8"), None if female is None else female_text.encode("utf-8"))
            if key not in existing:
                existing[key] = male.count + len(male_rows)
                male_rows.append((_TEXT_ONLY, key[0]))
                female_rows.append((_TEXT_ONLY, key[1]))
            refs.append(existing[key])
        return GameText(_extended(self.male, male, male_rows),
                        None if female is None else _extended(self.female, female, female_rows)), refs


def paired(male: bytes, female: bytes | None) -> GameText:
    """dialog.tlk and dialogf.tlk, with the entries dialog.tlk holds beyond dialogf.tlk's end copied into it.

    For texts appended to dialog.tlk alone (they have no female wording) and
    numbered after the strings both files share.
    """
    if female is None:
        return GameText(male)
    source, target = _tlk(male), _tlk(female)
    if source.count < target.count:
        raise GameTextError("dialogf.tlk holds strings beyond dialog.tlk's end")
    rows = []
    for index in range(target.count, source.count):
        row = 0x12 + index * 0x1A
        offset, length = struct.unpack_from("<II", male, row + 0x12)
        rows.append((male[row:row + 0x12], male[source.offset + offset:source.offset + offset + length]))
    return GameText(male, _extended(female, target, rows))


def text_files(root: Path, language: str) -> tuple[Path, Path | None]:
    """The game's dialog.tlk for the scan's <language>, and the dialogf.tlk next to it if it has one."""
    folder = root if language == "default" else root / "lang" / language
    female = next((entry for entry in folder.iterdir() if entry.name.casefold() == "dialogf.tlk"), None)
    return folder / "dialog.tlk", female


def _count(path: Path) -> int:
    with path.open("rb") as stream:
        head = stream.read(0x12)
    if len(head) < 0x12 or head[:8] != b"TLK V1  ":
        raise GameTextError(f"the game's {path.name} is not a TLK V1 file")
    return struct.unpack_from("<I", head, 0xA)[0]


def unpaired(male: Path, female: Path | None) -> str | None:
    """Why a text appended to dialog.tlk <male> cannot get the same number in dialogf.tlk <female>.

    None when it can, or when the game has no dialogf.tlk.
    """
    if female is None:
        return None
    try:
        counts = _count(male), _count(female)
    except GameTextError as exc:
        return str(exc)
    if counts[0] == counts[1]:
        return None
    return (f"the game's {female.name} and {male.name} hold different numbers of strings "
            f"({counts[1]} and {counts[0]}), so a new text cannot get the same number in both")


def _append_texts(data: bytes, texts: tuple[str, ...]) -> tuple[bytes, list[int]]:
    """Append text-only entries to dialog.tlk alone, preserving all existing TLK entries and data."""
    text, refs = GameText(data).append((value, value) for value in texts)
    return text.male, refs
