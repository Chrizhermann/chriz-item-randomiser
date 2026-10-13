"""Reviewed compiled-BCS edits for the three original BG2 quest rewards.

The v2 pool edit extends, rather than changes, the legacy BLPOOL patch. Only
the known compiled scripts and their known patched forms are accepted.
"""

from __future__ import annotations

import hashlib
import re

from .profiles import ProfileError
from .scripted_sources import (ORIGINAL_SHA256 as POOL_ORIGINAL,
                               PATCHED_SHA256 as POOL_LEGACY,
                               SOURCE_ID as POOL_SOURCE_ID, patch_blpool)


ADAPTER_ID = "original-quest-v1"
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
EVENT_GLOBALS = {name: row["event_global"] for name, row in QUESTS.items()}
SOURCE_QUESTS = {source_id: name for name, row in QUESTS.items()
                 for source_id in row["source_ids"]}
ORIGINAL_HASHES = {
    "blpool": POOL_ORIGINAL,
    "troalt": "635891033de7ff97be9c41b85a8584ebfab7a2677f68f0eb657c75c1b37cd5dc",
    "ppbook": "ecf07b484ecb5fe79bb6cad7fd45047428d7c737e45d2e6b4a7ec9a380de90d9",
}
# Fixed from the reviewed isolated-lab compiled scripts.
PATCHED_HASHES = {
    "blpool": "a7f8c432c5f34481ba70495ee41137a45ab2f5cae825071f00ce682a1be74ad3",
    "troalt": "ce5b8060f5b0c3c7c9229f974158d70837e36b7bf33ccad067852a7b1e32aaa6",
    "ppbook": "39b1f5119d8abc96d5158eec54d212475e639516cb35d86de27e7d6e4cbbd515",
}


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


def _one(data: bytes, old: bytes, new: bytes, label: str) -> bytes:
    if data.count(old) != 1:
        raise ProfileError(f"{label} reward block changed; review quest adapter")
    return data.replace(old, new, 1)


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


def _event(block: bytes, name: str, newline: bytes) -> bytes:
    end = b"RE" + newline + b"RS" + newline + b"CR" + newline
    if not block.endswith(end):
        raise ProfileError("Quest reward response changed")
    return block[:-len(end)] + _global_action(name, newline) + end


def _split_reward(block: bytes, spec: dict, grant: bytes, newline: bytes) -> bytes:
    delimiter = b"CO" + newline
    if block.count(delimiter) != 2 or block.count(grant) != 1:
        raise ProfileError("Quest reward block changed")
    head, conditions, response = block.split(delimiter, 2)
    selected = (head + delimiter + conditions +
                _global_guard(spec["selected_global"], True, newline) + delimiter + response)
    excluded = (head + delimiter + conditions +
                _global_guard(spec["selected_global"], False, newline) + delimiter + response)
    selected = _one(selected, grant, _global_action(spec["consumed_global"], newline),
                    spec["quest"])
    return _event(selected, spec["event_global"], newline) + _event(
        excluded, spec["event_global"], newline)


def _patch_pool(data: bytes, newline: bytes) -> bytes:
    legacy = patch_blpool(data)
    blocks, prefix, suffix = _blocks(legacy, 5, newline)
    selected_guard = _line(b'16399 1 0 0 0 "GLOBALFLPR_QPOOL" "" OB', newline)
    excluded_guard = _line(b'16399 1 1 0 0 "GLOBALFLPR_QPOOL" "" OB', newline)
    if (selected_guard not in blocks[1] or excluded_guard not in blocks[2] or
            legacy.count(b"GLOBALFLPR_XPOOL") != 1):
        raise ProfileError("BLPOOL legacy reward branches changed")
    for index in (1, 2):
        if blocks[index].count(_line(b'2 0 0 0 0"AR0801Pool" "" AC', newline)) != 1 or \
                blocks[index].count(_line(b'2020 0 0 0 0"" "" AC', newline)) != 1:
            raise ProfileError("BLPOOL reward progression changed")
        blocks[index] = _event(blocks[index], QUESTS["blpool"]["event_global"], newline)
    return prefix + b"".join(blocks) + suffix


def _patch_altar(data: bytes, newline: bytes) -> bytes:
    blocks, prefix, suffix = _blocks(data, 1, newline)
    block = blocks[0]
    for value in (b'0 0 0 0 0"MISC9D" "" AC', b'1 0 0 0 0"GLOBALSecretThree" "" AC',
                  b'0 0 0 0 0"EFF_P92" "" AC', b'47724 0 0 0 0"" "" AC'):
        if block.count(_line(value, newline)) != 1:
            raise ProfileError("TROALT quest progression changed")
    grant = _grant_action(block, "BLUN23", 82, newline)
    return prefix + _split_reward(block, QUESTS["troalt"], grant, newline) + suffix


def _patch_book(data: bytes, newline: bytes) -> bytes:
    blocks, prefix, suffix = _blocks(data, 8, newline)
    matches = [i for i, block in enumerate(blocks) if
               _line(b'0 0 0 0 0"ring09" "" AC', newline) in block]
    if matches != [6]:
        raise ProfileError("PPBOOK page-five reward changed")
    block = blocks[6]
    for value in (b'16399 5 0 0 0 "AR1513page" "" OB',
                  b'1 0 0 0 0"AR1513page" "" AC',
                  b'0 0 0 0 0"scrl8z" "" AC', b'0 0 0 0 0"scrl9b" "" AC'):
        if block.count(_line(value, newline)) != 1:
            raise ProfileError("PPBOOK page-five progression changed")
    grant = _grant_action(block, "ring09", 140, newline)
    blocks[6] = _split_reward(block, QUESTS["ppbook"], grant, newline)
    return prefix + b"".join(blocks) + suffix


def patch_quest(resource: str, original: bytes) -> bytes:
    """Return the idempotent, exact v2 compiled script for one reviewed quest."""
    name = resource.lower().removeprefix("bcs:").removesuffix(".bcs")
    if name not in QUESTS or not isinstance(original, bytes):
        raise ProfileError("Unsupported original quest script")
    digest = hashlib.sha256(original).hexdigest()
    if digest == PATCHED_HASHES[name]:
        return original
    accepted = {ORIGINAL_HASHES[name]}
    if name == "blpool":
        accepted.add(POOL_LEGACY)
    if digest not in accepted:
        raise ProfileError(f"{name.upper()} BCS changed; review quest adapter")
    newline = _newline(original)
    patched = {"blpool": _patch_pool, "troalt": _patch_altar,
               "ppbook": _patch_book}[name](original, newline)
    if PATCHED_HASHES[name] and hashlib.sha256(patched).hexdigest() != PATCHED_HASHES[name]:
        raise ProfileError(f"{name.upper()} quest patch differs from reviewed bytes")
    return patched


def is_compatible_patch(expected: bytes, actual: bytes) -> bool:
    """Accept an old BLPOOL revision only under the exact reviewed v2 upgrade."""
    if actual == expected:
        return True
    if (not isinstance(expected, bytes) or not isinstance(actual, bytes) or
            hashlib.sha256(expected).hexdigest() != POOL_LEGACY or
            hashlib.sha256(actual).hexdigest() != PATCHED_HASHES["blpool"]):
        return False
    action = _global_action(EVENT_GLOBALS["blpool"], _newline(actual))
    return actual.count(action) == 2 and actual.replace(action, b"") == expected
