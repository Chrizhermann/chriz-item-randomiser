"""Write the code page tables the Rust port decodes translations with.

Run from the repository root:  python configurator/parity/codepages.py

The reference reads a translation file with Python's strict codec for its
language's code page (translations.py). These tables are taken from those
codecs, so the port accepts and rejects exactly the bytes they do. Each code
page becomes configurator/cir-config/codepages/<code page>.bin:

    b"CPT1"
    256 bytes      what a first byte is: 0 one character, 1 the first of
                   two bytes, 2 never valid
    256 x u16 LE   the character of each one-byte character
    per lead byte, ascending:
        u8 lead, u8 first trail byte, u16 LE count, count x u16 LE
                   the character of each two-byte sequence from that trail
                   byte on, 0 where Python refuses it

Every character is in the Basic Multilingual Plane and none is U+0000 except
byte 0 itself. Python decodes these code pages one character at a time from
its first byte (CPython's Modules/cjkcodecs), so the one- and two-byte
sequences decide every text; the Rust tests compare all of them with a digest
from this script's Python (see digests()).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "configurator" / "cir-config" / "codepages"
CODE_PAGES = ("cp1250", "cp1251", "cp1252", "cp1254", "cp932", "cp936", "cp949")


def classify(encoding: str) -> tuple[list[int], list[int]]:
    """(class of each first byte, character of each one-byte character)."""
    classes, singles = [], []
    for byte in range(256):
        try:
            text = bytes([byte]).decode(encoding)
        except UnicodeDecodeError as exc:
            # A first byte that needs a second one is "incomplete" on its own.
            classes.append(1 if "incomplete" in exc.reason else 2)
            singles.append(0)
            continue
        assert len(text) == 1 and ord(text) <= 0xFFFF and (ord(text) or not byte), (encoding, byte)
        classes.append(0)
        singles.append(ord(text))
    return classes, singles


def table(encoding: str) -> bytes:
    classes, singles = classify(encoding)
    out = bytearray(b"CPT1") + bytes(classes) + struct.pack("<256H", *singles)
    for lead in range(256):
        if classes[lead] != 1:
            continue
        values = []
        for trail in range(256):
            try:
                text = bytes([lead, trail]).decode(encoding)
            except UnicodeDecodeError:
                values.append(0)
                continue
            assert len(text) == 1 and 0 < ord(text) <= 0xFFFF, (encoding, lead, trail, text)
            values.append(ord(text))
        valid = [trail for trail, value in enumerate(values) if value]
        first, last = (valid[0], valid[-1]) if valid else (0, -1)
        out += struct.pack("<BBH", lead, first, last - first + 1)
        out += struct.pack(f"<{last - first + 1}H", *values[first:last + 1])
    return bytes(out)


def outcome(data: bytes, encoding: str) -> bytes:
    """What strict decoding gives, as UTF-8, or a refusal marker."""
    try:
        return b"=" + data.decode(encoding).encode("utf-8", "surrogatepass") + b"\0"
    except UnicodeDecodeError:
        return b"!\0"


def digests() -> dict[str, str]:
    """For each code page, the SHA-256 of strict decoding every one- and two-byte sequence, in order."""
    result = {}
    for encoding in (*CODE_PAGES, "utf-8"):
        digest = hashlib.sha256()
        for first in range(256):
            digest.update(outcome(bytes([first]), encoding))
            for second in range(256):
                digest.update(outcome(bytes([first, second]), encoding))
        result[encoding] = digest.hexdigest()
    return result


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for encoding in CODE_PAGES:
        data = table(encoding)
        (OUT / f"{encoding}.bin").write_bytes(data)
        print(f"{encoding}.bin {len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()}")
    print(f"Python {sys.version.split()[0]}")


if __name__ == "__main__":
    main()
