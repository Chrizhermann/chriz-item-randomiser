"""Quest and dialogue changes that randomiser.tp2 component 1500 installed.

Component 1500 makes these changes when it is installed (lib/campaign_patches.tpa),
so WeiDU restores the files when it is uninstalled. It lists each change in
override/FLPRPAT.lua with status "applied", or the reason it left the change
out. The configurator offers a change only when it is applied there and the
installed file still reads the change's saved GLOBALs; it never writes these
files itself.
"""
from __future__ import annotations

import re
from pathlib import Path

from .profiles import ProfileError

REGISTRY = Path("override") / "FLPRPAT.lua"
# Each change's installed resource and the GLOBALs its installed form reads.
# A file without them was replaced after component 1500 was installed.
CHANGES = {
    "quest_pool": ("bcs:blpool", (b"FLPR_QPOOL", b"FLPR_XPOOL", b"FLPR_EPOOL")),
    "quest_altar": ("bcs:troalt", (b"FLPR_QALTAR", b"FLPR_XALTAR", b"FLPR_EALTAR")),
    "quest_book": ("bcs:ppbook", (b"FLPR_QBOOK", b"FLPR_XBOOK", b"FLPR_EBOOK")),
    "unshey": ("dlg:unshey", (b"FLPR_QUNSHEY",)),
    "flail_heads": ("dlg:kpglai01", (b"FLPR_FLAIL",)),
    "totsc_tome": ("itm:flbhcryp", ()),
}
# The changes that the original mod's quest rewards depend on.
QUEST_CHANGES = {"blpool": "quest_pool", "troalt": "quest_altar", "ppbook": "quest_book"}
# Why component 1500 left a change out, as the registry records it; the same
# reasons as its installer notes (setup.tra @101521-@101528).
REASONS = {
    "absent": "the game has no such file",
    "prepared-elsewhere": "it already contains changes from an earlier version of this randomiser",
    "grant-missing": "it no longer gives the reward",
    "grant-ambiguous": "it gives the reward more than once",
    "block-unrecognised": "it gives the reward in an unexpected way",
    "item-missing": "it no longer takes the quest item",
    "journal-unrecognised": "its journal entries have an unexpected form",
    "dialogue-unrecognised": "the conversation has an unexpected form",
}
_ENTRY = re.compile(r"^\s*([a-z_]+)\s*=\s*\{(.*)\},?\s*$")
_FIELD = re.compile(r'([a-z_]+)\s*=\s*(?:"([^"\\]*)"|(-?[0-9]+))')


def read_prepared(game_root: str | Path) -> dict[str, dict]:
    """The registry's entries by change name; empty without the registry."""
    try:
        text = (Path(game_root) / REGISTRY).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    entries = {}
    for line in text.splitlines():
        match = _ENTRY.match(line)
        if match:
            entries[match[1]] = {key: int(number) if string == "" and number else string
                                 for key, string, number in _FIELD.findall(match[2])}
    return entries


def applied(game_root: str | Path, change: str) -> bool:
    if change not in CHANGES:
        raise ProfileError(f"Unknown installed change: {change}")
    return read_prepared(game_root).get(change, {}).get("status") == "applied"


def applied_changes(game_root: str | Path) -> set[str]:
    return {name for name, entry in read_prepared(game_root).items()
            if name in CHANGES and entry.get("status") == "applied"}


def left_out(game_root: str | Path, change: str) -> str:
    """Tell the player which file a change needed and why it was not made."""
    entry = read_prepared(game_root).get(change)
    kind, name = CHANGES[change][0].split(":")
    if entry is None:
        return (f"{name.upper()}.{kind.upper()} was not prepared: component 1500 of this "
                "version is not installed")
    resource = entry.get("resource") or f"{name.upper()}.{kind.upper()}"
    status = entry.get("status", "")
    return f"{resource} was left as installed ({REASONS.get(status, status or 'no reason recorded')})"


def require_installed(game_root: str | Path, change: str, data: bytes) -> None:
    """Refuse a change that is not applied, or whose file no longer carries it."""
    resource, markers = CHANGES[change]
    if not applied(game_root, change):
        raise ProfileError(f"Component 1500 did not prepare {resource}; reinstall it to use this option")
    if any(marker not in data for marker in markers):
        raise ProfileError(f"{resource} was replaced after component 1500 prepared it; "
                           "reinstall component 1500 to use this option")
