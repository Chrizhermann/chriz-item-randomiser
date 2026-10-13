"""Resolve BG1 resource names using the checked-in bg1pal EET tables.

Only the resource types needed by the post-install campaign planner are exposed.
The authoritative collision lists remain in ``lib/fl#bg1pal.tpa``.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from .profiles import ProfileError

_LIBRARY = Path(__file__).resolve().parents[2] / "lib" / "fl#bg1pal.tpa"
_EXTENSIONS = {"are", "bcs", "cre", "itm", "sto", "dlg"}
_TABLE_EXTENSIONS = _EXTENSIONS - {"are"}
_ARRAY = re.compile(
    r"^ACTION_DEFINE_ASSOCIATIVE_ARRAY fl#bg1pal#eet#([a-z0-9]+) BEGIN\r?\n"
    r"(.*?)^END[ \t]*$", re.MULTILINE | re.DOTALL)
_PAIR = re.compile(r"([a-z0-9_#]{1,8})\s*=>\s*([a-z0-9_#]{1,8})")
_RESREF = re.compile(r"[a-z0-9_#]{1,8}")
_AREA = re.compile(r"ar[0-9]+n?")


@lru_cache(maxsize=1)
def _eet_tables() -> dict[str, dict[str, str]]:
    source = _LIBRARY.read_text(encoding="utf-8")
    tables: dict[str, dict[str, str]] = {}
    for match in _ARRAY.finditer(source):
        extension = match.group(1)
        if extension not in _TABLE_EXTENSIONS:
            continue
        if extension in tables:
            raise ProfileError(f"Duplicate bg1pal EET table: {extension}")
        entries: dict[str, str] = {}
        for raw_line in match.group(2).splitlines():
            line = raw_line.split("//", 1)[0].strip()
            if not line:
                continue
            pair = _PAIR.fullmatch(line)
            if pair is None or pair.group(1) in entries:
                raise ProfileError(f"Unsupported bg1pal EET {extension} mapping: {line}")
            entries[pair.group(1)] = pair.group(2)
        tables[extension] = entries
    if set(tables) != _TABLE_EXTENSIONS:
        raise ProfileError("Incomplete bg1pal EET resource mappings")
    return tables


def convert_resource(resref: str, extension: str, campaign: str,
                     game: str = "eet") -> str:
    """Return the effective resref for a BG1/BG2 campaign on supported games.

    EET changes BG1 ``ar[digits][n]`` ARE/BCS names to ``bg...`` and applies
    the source library's explicit collision maps. BG2 and vanilla EE names
    remain unchanged. BGT and Tutu are intentionally outside this adapter.
    """
    if not isinstance(game, str) or game.lower() not in {"eet", "bgee", "bg2ee"}:
        raise ProfileError(f"Unsupported game for resource conversion: {game}")
    if not isinstance(campaign, str) or campaign.lower() not in {"bg1", "bg2"}:
        raise ProfileError(f"Unsupported campaign for resource conversion: {campaign}")
    if not isinstance(extension, str) or extension.lower() not in _EXTENSIONS:
        raise ProfileError(f"Unsupported resource extension: {extension}")
    if not isinstance(resref, str):
        raise ProfileError("Invalid resource name")
    name = resref.lower()
    if _RESREF.fullmatch(name) is None:
        raise ProfileError(f"Invalid resource name: {resref}")
    if game.lower() != "eet" or campaign.lower() == "bg2":
        return name
    kind = extension.lower()
    if kind in {"are", "bcs"} and _AREA.fullmatch(name):
        return "bg" + name[2:]
    if kind == "are":
        return name
    return _eet_tables()[kind].get(name, name)
