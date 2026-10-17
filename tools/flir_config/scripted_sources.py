"""The one scripted source: BLPOOL's Mace of Disruption grant.

The installer splits the pool's reward block when it is installed
(lib/campaign_patches.tpa): the selected copy, guarded by FLPR_QPOOL, gives
nothing and sets FLPR_XPOOL; the excluded copy keeps the original grant. The
pool is offered only when override/FLPRPAT.lua lists that change as applied.
"""

from __future__ import annotations

from copy import deepcopy

from .prepared import applied
from .profiles import HEX256, ProfileError

SOURCE_ID = "bcs:blpool/grant/pool-reward"
RESOURCE = "bcs:blpool"
ITEM = "blun12"
ADAPTER_ID = "blpool-v1"
SELECTOR = "pool-reward"
# Item Randomiser's BG2 table lists the pool.
CAMPAIGN = "bg2"
PAYLOAD = {"resref": ITEM, "expiration": 0, "charges": [0, 0, 0], "flags": 0}


def blpool_registration(world: dict) -> dict:
    """Return the registration for the pool that the installer prepared."""
    resources = world.get("resources") if isinstance(world, dict) else None
    matches = [row for row in resources if isinstance(row, dict) and row.get("id") == RESOURCE] if isinstance(resources, list) else []
    if (len(matches) != 1 or matches[0].get("kind") != "bcs" or matches[0].get("resref") != "blpool" or
            not isinstance(matches[0].get("sha256"), str) or not HEX256.fullmatch(matches[0]["sha256"])):
        raise ProfileError("The scan has no BLPOOL.BCS")
    if not applied(world["game_root"], "quest_pool"):
        raise ProfileError("Chriz Item Randomiser did not prepare BLPOOL.BCS; reinstall it to use the pool reward")
    return {"id": SOURCE_ID, "item": ITEM, "resource": RESOURCE,
            "resource_sha256": matches[0]["sha256"], "adapter_id": ADAPTER_ID,
            "selector": SELECTOR, "copies": 1, "optional": False}


def validate_registration(row: dict) -> None:
    digest = row.get("resource_sha256") if isinstance(row, dict) else None
    if not isinstance(digest, str) or not HEX256.fullmatch(digest) or row != {
            "id": SOURCE_ID, "item": ITEM, "resource": RESOURCE, "resource_sha256": digest,
            "adapter_id": ADAPTER_ID, "selector": SELECTOR, "copies": 1, "optional": False}:
        raise ProfileError("Unsupported registered script source")


def campaign_record(row: dict) -> dict:
    validate_registration(row)
    return {"id": SOURCE_ID, "item": ITEM, "resource": RESOURCE,
            "owner": RESOURCE, "kind": "scripted", "record": deepcopy(PAYLOAD),
            "adapter_id": ADAPTER_ID, "selector": SELECTOR, "concerns": []}
