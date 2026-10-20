"""The game files Apply writes outside CIR's folder, and what the game had there before.

Apply (activation.deploy_export) writes a configuration's prepared resources
into override: the extended reward routes' dialogues and scripts and the
copies of their reward replies, the Kangaxx option's dialogue and scripts, the
Deck of Many Things key spell and the BGEE.LUA journal table. It appends its
texts to the game text (dialog.tlk, dialogf.tlk). These files are what a
configuration prepares, not a change of the game's mods, so the scans
(resources.scan_game, sources.scan_world) read each of them, while it still
holds exactly the bytes Apply wrote, as the game had it before CIR first
wrote it. A file Apply wrote that another mod or the player changed since is
read as it is, like every other file. Route recognition and the export read
the game's files as they are and recognise CIR's own patches there.

randomiser-campaigns/applied-files.json lists each file by its path in the
game, as the export names it ("override/SAFANJ.DLG", "lang/en_us/dialog.tlk"):
"written" is the SHA-256 of the bytes Apply wrote last, "before" what the game
had there before CIR first wrote it: null when there was no such file, else
its SHA-256 and size. CIR keeps a copy of each earlier file under
randomiser-campaigns/before-apply/ by the same path. Apply only appends to
game text, so an earlier game text file is the first "strings" entries of the
one Apply wrote; no copy is kept of it.

A record that cannot be read is not used: the scans then read every file as
it is, and the next Apply starts a new record.
"""
from __future__ import annotations

import re
import string
from pathlib import Path

# CIR's folder in the game (activation.CAMPAIGN_DATA).
DATA = "randomiser-campaigns"
RECORD = "applied-files.json"
SCHEMA = "flir-applied-files-v1"
# The files as the game had them before, by their path in the game.
BEFORE = "before-apply"
TEXT_SUFFIX = ".tlk"
_PART = re.compile(r'[^\x00-\x1f\\/:*?"<>|]+')
_DIGEST = re.compile(r"[0-9a-f]{64}")
_LIMIT = 0xFFFFFFFF
_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def key(path: str) -> str:
    """A game path compared as Windows compares file names (ASCII letters without case)."""
    return path.translate(_LOWER)


def is_text(path: str) -> bool:
    """Whether the file is game text, to which Apply only appends."""
    return key(path).endswith(TEXT_SUFFIX)


def is_game_path(path: object) -> bool:
    """Whether <path> names a file of the game outside CIR's folder, as the record keeps it."""
    if not isinstance(path, str):
        return False
    parts = path.split("/")
    return (all(_PART.fullmatch(part) and part not in (".", "..") for part in parts) and
            key(parts[0]) != DATA)


def _count(value: object) -> bool:
    return type(value) is int and 0 <= value <= _LIMIT


def _digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def entries(record: object) -> dict[str, dict] | None:
    """The files of a record by their game path; None unless it is a complete record."""
    if not isinstance(record, dict) or set(record) != {"schema", "files"} or record["schema"] != SCHEMA:
        return None
    files = record["files"]
    if not isinstance(files, dict):
        return None
    seen = set()
    for path, entry in files.items():
        if not is_game_path(path) or key(path) in seen:
            return None
        seen.add(key(path))
        if not isinstance(entry, dict) or set(entry) != {"before", "written"} or not _digest(entry["written"]):
            return None
        before = entry["before"]
        if before is None:
            continue
        fields = {"sha256", "size", "strings"} if is_text(path) else {"sha256", "size"}
        if (not isinstance(before, dict) or set(before) != fields or not _digest(before["sha256"]) or
                not all(_count(before[field]) for field in fields - {"sha256"})):
            return None
    return files


def read(path: Path) -> dict[str, dict]:
    """The files of the record at <path>; none when it is absent or cannot be read."""
    # profiles imports resources, which reads the record through this module.
    from .profiles import ProfileError, read_json
    try:
        return entries(read_json(path)) or {}
    except (OSError, ProfileError):
        return {}


def find(files: dict[str, dict], path: str) -> str | None:
    """The record's own spelling of a game path, if it lists that file."""
    wanted = key(path)
    return next((name for name in files if key(name) == wanted), None)
