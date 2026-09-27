"""The one reviewed scripted source: BLPOOL's Mace of Disruption grant.

This adapter is deliberately limited to the compiled BCS fixture recorded by
the 2026-09-24 world scan. A different installed script needs fresh review.
"""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy

from .profiles import ProfileError

SOURCE_ID = "bcs:blpool/grant/pool-reward"
RESOURCE = "bcs:blpool"
ITEM = "blun12"
ADAPTER_ID = "blpool-v1"
SELECTOR = "pool-reward"
ORIGINAL_SHA256 = "d9bf69e577a1ae8b5e3dbbec60fadbf9fdcf0246d0a51ac4da59e09e148a51a8"
PATCHED_SHA256 = "4353c57217da820f2ed4a862eee692d19e064c178b2b95a3eed01ff7f9bf3093"
SUPPORTED_HASHES = {ORIGINAL_SHA256, PATCHED_SHA256}
PAYLOAD = {"resref": ITEM, "expiration": 0, "charges": [0, 0, 0], "flags": 0}


def blpool_registration(world: dict) -> dict:
    """Return the exact authorable registration for the reviewed world scan."""
    resources = world.get("resources") if isinstance(world, dict) else None
    matches = [row for row in resources if isinstance(row, dict) and row.get("id") == RESOURCE] if isinstance(resources, list) else []
    if (len(matches) != 1 or matches[0].get("sha256") not in SUPPORTED_HASHES or
            matches[0].get("kind") != "bcs" or matches[0].get("resref") != "blpool"):
        raise ProfileError("BLPOOL adapter needs the reviewed BCS scan")
    return {"id": SOURCE_ID, "item": ITEM, "resource": RESOURCE,
            "resource_sha256": matches[0]["sha256"], "adapter_id": ADAPTER_ID,
            "selector": SELECTOR, "copies": 1, "optional": False}


def validate_registration(row: dict) -> None:
    if row.get("resource_sha256") not in SUPPORTED_HASHES or row != {"id": SOURCE_ID, "item": ITEM, "resource": RESOURCE,
               "resource_sha256": row["resource_sha256"], "adapter_id": ADAPTER_ID,
               "selector": SELECTOR, "copies": 1, "optional": False}:
        raise ProfileError("Unsupported registered script source")


def campaign_record(row: dict) -> dict:
    validate_registration(row)
    return {"id": SOURCE_ID, "item": ITEM, "resource": RESOURCE,
            "owner": RESOURCE, "kind": "scripted", "record": deepcopy(PAYLOAD),
            "adapter_id": ADAPTER_ID, "selector": SELECTOR, "concerns": []}


def patch_blpool(original: bytes) -> bytes:
    """Split only the reviewed reward block into selected/excluded branches."""
    actual_hash = hashlib.sha256(original).hexdigest()
    if actual_hash == PATCHED_SHA256:
        return original
    if actual_hash != ORIGINAL_SHA256:
        raise ProfileError("BLPOOL BCS changed; review its script adapter before export")
    newline = b"\r\n" if b"\r\n" in original else b"\n"
    if original.replace(newline, b"").find(b"\n") != -1:
        raise ProfileError("BLPOOL BCS has mixed line endings")
    starts = list(re.finditer(rb"(?m)^CR\r?\nCO\r?\n", original))
    if len(starts) != 4:
        raise ProfileError("BLPOOL BCS block structure changed")
    start, end = starts[1].start(), starts[2].start()
    reward = original[start:end]

    def line(value: bytes) -> bytes:
        return value + newline

    def once(data: bytes, old: bytes, new: bytes) -> bytes:
        if data.count(old) != 1:
            raise ProfileError("BLPOOL reward block changed; review its script adapter")
        return data.replace(old, new, 1)

    pool = line(b'16399 1 0 0 0 "AR0801Pool" "" OB')
    give = line(b'0 0 0 0 0"BLUN12" "" AC')
    display = line(b'35877 0 0 0 0"" "" AC')
    advance = line(b'2 0 0 0 0"AR0801Pool" "" AC')
    if any(reward.count(x) != 1 for x in (pool, give, display, advance)):
        raise ProfileError("BLPOOL reward block changed; review its script adapter")
    # Copy the compiled Global trigger and its object record; field three is
    # the engine's trigger negation bit. Copy the existing SetGlobal action.
    pool_trigger = line(b'TR') + pool + line(b'0 0 0 0 0 0 0 0 0 0 0 0 ""OB') + line(b'TR')
    if reward.count(pool_trigger) != 1:
        raise ProfileError("BLPOOL reward trigger changed")
    selected_guard = pool_trigger.replace(pool, line(b'16399 1 0 0 0 "GLOBALFLPR_QPOOL" "" OB'))
    excluded_guard = pool_trigger.replace(pool, line(b'16399 1 1 0 0 "GLOBALFLPR_QPOOL" "" OB'))
    delimiter = line(b'CO')
    if reward.count(delimiter) != 2:
        raise ProfileError("BLPOOL reward delimiter changed")
    selected = once(reward, pool_trigger + delimiter, pool_trigger + selected_guard + delimiter)
    excluded = once(reward, pool_trigger + delimiter, pool_trigger + excluded_guard + delimiter)
    # In the selected branch, the existing empty-pool line avoids falsely
    # claiming the mace was found. The bite and Pool quest advance stay intact.
    selected = once(selected, display, line(b'35878 0 0 0 0"" "" AC'))

    def action(start: bytes, parameter: bytes) -> bytes:
        if reward.count(start) != 1:
            raise ProfileError("BLPOOL reward action changed")
        begin = reward.index(start)
        end = reward.find(line(b'AC'), begin + len(start))
        if end < 0:
            raise ProfileError("BLPOOL reward action is unterminated")
        result = reward[begin:end + len(line(b'AC'))]
        if result.count(parameter) != 1:
            raise ProfileError("BLPOOL reward action parameters changed")
        return result

    grant_action = action(line(b'AC') + line(b'140OB'), give)
    advance_action = action(line(b'AC') + line(b'30OB'), advance)
    witness_action = once(advance_action, advance,
                          line(b'1 0 0 0 0"GLOBALFLPR_XPOOL" "" AC'))
    selected = once(selected, grant_action, witness_action)
    if selected.count(line(b'2020 0 0 0 0"" "" AC')) != 1:
        raise ProfileError("BLPOOL bite action changed")
    return original[:start] + selected + excluded + original[end:]
