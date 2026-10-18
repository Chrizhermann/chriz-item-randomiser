"""Resolve BG1 and Siege of Dragonspear resource names on EET.

EET imports Baldur's Gate: Enhanced Edition, Siege of Dragonspear included, and
renames the BG:EE resources whose names Baldur's Gate II already uses. It keeps
its rename lists in its own mod folder (``EET/lib/pcu_dict.tph``), and those
lists name exactly what that EET version renamed. ``eet_names`` reads them; when
the folder is gone or the lists cannot be read, it falls back to the copy in
``lib/fl#bg1pal.tpa``, which predates Siege of Dragonspear and EET 14.

Only the resource types needed by the post-install campaign planner are exposed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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
# EET's rename lists, below the game folder.
_INSTALLED = ("eet", "lib", "pcu_dict.tph")
_INSTALLED_ARRAY = re.compile(
    r"^[ \t]*ACTION_DEFINE_ASSOCIATIVE_ARRAY[ \t]+remapped_([A-Za-z0-9]+)[ \t]+BEGIN[ \t]*\r?\n"
    r"(.*?)^[ \t]*END\b", re.MULTILINE | re.DOTALL)
# A rename: two resource names, bare or quoted as WeiDU allows.
_INSTALLED_PAIR = re.compile(r'[~"]?([^\s~"]{1,8})[~"]?[ \t]*=>[ \t]*[~"]?([^\s~"]{1,8})[~"]?')
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
# Lists an installed EET must have for its file to count as its rename lists.
_REQUIRED_INSTALLED = {"itm", "cre"}


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


def _child(parent: Path, name: str) -> Path | None:
    """The entry of <parent> with this name in any letter case, if there is exactly one."""
    try:
        matches = [entry for entry in parent.iterdir() if entry.name.casefold() == name]
    except OSError:
        return None
    return matches[0] if len(matches) == 1 else None


def _installed_path(game_root: Path) -> Path | None:
    path = game_root
    for name in _INSTALLED:
        path = _child(path, name)
        if path is None:
            return None
    return path if path.is_file() else None


@lru_cache(maxsize=4)
def _installed_tables(path: str, size: int, modified: int) -> dict[str, dict[str, str]]:
    """EET's own lists (``remapped_<type>`` arrays); <size> and <modified> key the cache."""
    del size, modified
    # Names are ASCII; comments may use any code page.
    text = _BLOCK_COMMENT.sub("", Path(path).read_bytes().decode("latin-1"))
    tables: dict[str, dict[str, str]] = {}
    for match in _INSTALLED_ARRAY.finditer(text):
        extension = match.group(1).lower()
        if extension not in _TABLE_EXTENSIONS:
            continue
        entries = tables.setdefault(extension, {})
        for raw_line in match.group(2).splitlines():
            line = raw_line.split("//", 1)[0].strip()
            if not line:
                continue
            pair = _INSTALLED_PAIR.fullmatch(line)
            if pair is None:
                raise ValueError(f"unexpected {extension} line {line!r}")
            # As in WeiDU, a later entry for a name replaces an earlier one.
            entries[pair.group(1).lower()] = pair.group(2).lower()
    missing = _REQUIRED_INSTALLED - set(tables)
    if missing:
        raise ValueError("no " + " or ".join(sorted(missing)) + " list")
    return {extension: tables.get(extension, {}) for extension in sorted(_TABLE_EXTENSIONS)}


@dataclass(frozen=True)
class EetNames:
    """EET's renames for one game: the installed lists, or CIR's copy with a note why."""

    tables: dict[str, dict[str, str]]
    installed: bool
    notes: tuple[str, ...] = field(default=())


def eet_names(game_root: Path | str) -> EetNames:
    """The renames this EET game made, from its own lists when they can be read."""
    path = _installed_path(Path(game_root))
    if path is None:
        return EetNames(_eet_tables(), False, (
            "EET's rename lists (EET/lib/pcu_dict.tph) are not in this game, so Chriz Item "
            "Randomiser uses its own older copy; some BG1 and Siege of Dragonspear rows may not "
            "be found and are left out",))
    try:
        status = path.stat()
        tables = _installed_tables(str(path), status.st_size, status.st_mtime_ns)
    except (OSError, ValueError) as exc:
        return EetNames(_eet_tables(), False, (
            f"EET's rename lists (EET/lib/pcu_dict.tph) cannot be read ({exc}), so Chriz Item "
            "Randomiser uses its own older copy; some BG1 and Siege of Dragonspear rows may not "
            "be found and are left out",))
    return EetNames(tables, True)


def convert_resource(resref: str, extension: str, campaign: str,
                     game: str = "eet", names: EetNames | None = None) -> str:
    """Return the effective resref for a BG1, SoD or BG2 table row on supported games.

    EET changes BG1 ``ar[digits][n]`` ARE/BCS names to ``bg...`` and renames the
    BG:EE resources it lists, Siege of Dragonspear's among them (<names>, from
    ``eet_names``; without it, CIR's own copy of the lists). BG2 and vanilla EE
    names remain unchanged. BGT and Tutu are intentionally outside this adapter.
    """
    if not isinstance(game, str) or game.lower() not in {"eet", "bgee", "bg2ee"}:
        raise ProfileError(f"Unsupported game for resource conversion: {game}")
    if not isinstance(campaign, str) or campaign.lower() not in {"bg1", "sod", "bg2"}:
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
    tables = names.tables if names is not None else _eet_tables()
    return tables[kind].get(name, name)
