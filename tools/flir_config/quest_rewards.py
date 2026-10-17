"""The original mod's three BG2 quest rewards, and compiled-BCS helpers.

The installer splits each reward block when it is installed
(lib/campaign_patches.tpa); the configurator only binds the rewards. The
helpers below are shared by the Mode 2 and container reward adapters.
"""

from __future__ import annotations

import re

from .profiles import ProfileError
from .scripted_sources import SOURCE_ID as POOL_SOURCE_ID


QUESTS = {
    "blpool": {"quest": "pool", "resource": "bcs:blpool", "item": "blun12",
               "selected_global": "FLPR_QPOOL", "consumed_global": "FLPR_XPOOL",
               "event_global": "FLPR_EPOOL", "source_ids": (POOL_SOURCE_ID,)},
    "troalt": {"quest": "altar", "resource": "bcs:troalt", "item": "blun23",
               "selected_global": "FLPR_QALTAR", "consumed_global": "FLPR_XALTAR",
               "event_global": "FLPR_EALTAR",
               "source_ids": ("original-source:bg2:base:item:9",)},
    "ppbook": {"quest": "book", "resource": "bcs:ppbook", "item": "ring09",
               "selected_global": "FLPR_QBOOK", "consumed_global": "FLPR_XBOOK",
               "event_global": "FLPR_EBOOK",
               "source_ids": ("original-source:bg2:base:item:109",)},
}
SOURCE_QUESTS = {source_id: name for name, row in QUESTS.items()
                 for source_id in row["source_ids"]}


def quest_spec(source_id: str) -> dict:
    """Return the authored source's exact quest binding for native export."""
    quest = SOURCE_QUESTS.get(source_id)
    if quest is None:
        raise ProfileError(f"Unsupported original quest source: {source_id}")
    return dict(QUESTS[quest])


def _newline(data: bytes) -> bytes:
    newline = b"\r\n" if b"\r\n" in data else b"\n"
    if b"\n" in data.replace(newline, b""):
        raise ProfileError("Quest BCS has mixed line endings")
    return newline


def _blocks(data: bytes, expected: int, newline: bytes) -> tuple[list[bytes], bytes, bytes]:
    marker = b"CR" + newline + b"CO" + newline
    starts = [match.start() for match in re.finditer(re.escape(marker), data)]
    tail = b"SC" + newline
    if len(starts) != expected or not data.startswith(tail) or not data.endswith(tail):
        raise ProfileError("Quest BCS block structure changed")
    end = len(data) - len(tail)
    if starts[0] != len(tail) or not data[end - len(b"CR" + newline):end] == b"CR" + newline:
        raise ProfileError("Quest BCS block boundary changed")
    return ([data[start:starts[i + 1] if i + 1 < len(starts) else end]
             for i, start in enumerate(starts)], data[:starts[0]], data[end:])


def _line(value: bytes, newline: bytes) -> bytes:
    return value + newline


def _global_action(name: str, newline: bytes) -> bytes:
    if len(name) > 32 or not re.fullmatch(r"FLPR_[A-Z0-9_]+", name):
        raise ProfileError("Quest GLOBAL name is invalid")
    obj = _line(b'0 0 0 0 0 0 0 0 0 0 0 0 ""OB', newline)
    return (b"AC" + newline + b"30OB" + newline +
            obj + (b"OB" + newline + obj) * 2 +
            _line(b'1 0 0 0 0"GLOBAL' + name.encode("ascii") + b'" "" AC', newline))


def _global_guard(name: str, selected: bool, newline: bytes) -> bytes:
    negate = b"0" if selected else b"1"
    return (b"TR" + newline +
            _line(b'16399 1 ' + negate + b' 0 0 "GLOBAL' + name.encode("ascii") + b'" "" OB', newline) +
            _line(b'0 0 0 0 0 0 0 0 0 0 0 0 ""OB', newline) + b"TR" + newline)


def _grant_action(block: bytes, item: str, opcode: int, newline: bytes) -> bytes:
    parameter = _line(b'0 0 0 0 0"' + item.encode("ascii") + b'" "" AC', newline)
    if block.count(parameter) != 1:
        raise ProfileError("Quest reward grant changed")
    finish = block.index(parameter) + len(parameter)
    start = block.rfind(newline + b"AC" + newline, 0, finish)
    if start < 0:
        raise ProfileError("Quest reward grant action changed")
    action = block[start + len(newline):finish]
    if not action.startswith(_line(b"AC", newline) + _line(str(opcode).encode() + b"OB", newline)):
        raise ProfileError("Quest reward grant opcode changed")
    return action
