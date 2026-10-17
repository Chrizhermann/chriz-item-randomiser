"""Check CIR's native hook locations against Baldur.exe builds.

prototype/FLPRSite.lua finds each hook location in the running game's
executable at start-up: its pattern must occur exactly once in the code section
and the hook's expected bytes must be there. This tool does the same offline,
so a new game version can be checked before anyone plays it:

    python tools/engine_patterns.py <Baldur.exe> [<Baldur.exe> ...]

Exit status 0 when every executable passes, 1 otherwise.
"""

from __future__ import annotations

import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE_PATH = ROOT / "prototype" / "FLPRSite.lua"
SPEC = re.compile(r"(\w+)=\{at=(\d+),pattern='([0-9A-F?]+)',\s*bytes='([0-9a-f?]+)'\}")


class PatternError(ValueError):
    """FLPRSite.lua or an executable cannot be read as expected."""


@dataclass(frozen=True)
class Spec:
    key: str
    at: int
    pattern: str
    expected: str


def parse_specs(text: str) -> list[Spec]:
    """FLPRSite.lua's Site.SPECS in file order."""
    match = re.search(r"^Site\.SPECS = \{\n(.*?)^\}", text, re.M | re.S)
    if not match:
        raise PatternError("Site.SPECS not found")
    body = re.sub(r"--[^\n]*", "", match.group(1))
    specs = [Spec(key, int(at), pattern, expected) for key, at, pattern, expected in SPEC.findall(body)]
    if not specs or re.sub(r"\s|,", "", SPEC.sub("", body)):
        raise PatternError("unreadable Site.SPECS entry")
    for spec in specs:
        if len(spec.pattern) % 2 or len(spec.expected) % 2:
            raise PatternError(f"{spec.key}: odd number of hex digits")
    return specs


def matches(data: bytes, expected: str) -> bool:
    return len(data) * 2 >= len(expected) and all(
        expected[n:n + 2] == "??" or int(expected[n:n + 2], 16) == data[n // 2]
        for n in range(0, len(expected), 2))


class Executable:
    def __init__(self, data: bytes):
        self.data = data
        if data[:2] != b"MZ" or len(data) < 0x40:
            raise PatternError("not a PE executable")
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0" or struct.unpack_from("<H", data, pe + 24)[0] != 0x20B:
            raise PatternError("not a 64-bit PE executable")
        count = struct.unpack_from("<H", data, pe + 6)[0]
        optional = struct.unpack_from("<H", data, pe + 20)[0]
        self.sections = []
        offset = pe + 24 + optional
        for _ in range(count):
            name = data[offset:offset + 8].rstrip(b"\0").decode("ascii", "replace")
            vsize, va, rsize, raw = struct.unpack_from("<IIII", data, offset + 8)
            self.sections.append((name, va, vsize, raw, rsize))
            offset += 40
        text = [s for s in self.sections if s[0] == ".text"]
        if not text:
            raise PatternError("no .text section")
        _, self.code_va, vsize, self.code_raw, rsize = text[0]
        self.code_size = min(vsize, rsize)

    def read(self, rva: int, size: int) -> bytes:
        for _, va, vsize, raw, rsize in self.sections:
            if va <= rva < va + max(vsize, rsize):
                start = raw + rva - va
                return self.data[start:start + size]
        raise PatternError(f"RVA {rva:#x} outside the image")

    def search(self, pattern: str, at: int = 0) -> list[int]:
        """RVAs of the hook for every match of pattern in the code section."""
        parts = [b"." if pattern[n:n + 2] == "??" else re.escape(bytes.fromhex(pattern[n:n + 2]))
                 for n in range(0, len(pattern), 2)]
        expression = re.compile(b"(?=(" + b"".join(parts) + b"))", re.S)
        code = self.data[self.code_raw:self.code_raw + self.code_size]
        return [self.code_va + match.start() + at for match in expression.finditer(code)]


def check(executable: Executable, specs: list[Spec]) -> list[str]:
    """One line per location; problems start with 'FAIL'."""
    lines = []
    for spec in specs:
        hits = executable.search(spec.pattern, spec.at)
        if len(hits) != 1:
            lines.append(f"FAIL {spec.key}: {len(hits)} matches")
            continue
        data = executable.read(hits[0], len(spec.expected) // 2)
        if not matches(data, spec.expected):
            lines.append(f"FAIL {spec.key}: at {hits[0]:#x} the bytes differ ({data.hex()})")
            continue
        lines.append(f"ok   {spec.key}: {hits[0]:#x}")
    return lines


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip())
        return 1
    specs = parse_specs(SITE_PATH.read_text(encoding="ascii"))
    status = 0
    for path in argv:
        print(f"== {path}")
        try:
            lines = check(Executable(Path(path).read_bytes()), specs)
        except (OSError, PatternError) as error:
            print(f"FAIL {error}")
            status = 1
            continue
        print("\n".join(lines))
        if any(line.startswith("FAIL") for line in lines):
            status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
