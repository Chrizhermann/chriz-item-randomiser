"""Explicit authored stock that has no physical inventory record to remove."""

from __future__ import annotations

import re
from copy import deepcopy

from .profiles import HEX256, ProfileError

ADAPTER_ID = "virtual-stock-v1"
_ITEM = re.compile(r"[a-z0-9#_-]{1,8}")
_SELECTOR = re.compile(r"[a-z0-9][a-z0-9:_/-]{0,63}")
_FIELDS = {"id", "item", "resource", "resource_sha256", "adapter_id",
           "selector", "copies", "optional", "payload"}
_PAYLOAD_FIELDS = {"resref", "expiration", "charges", "flags"}


def validate_registration(row: dict) -> None:
    """Require one exact, immutable authored row with caller-supplied ITM charges."""
    if not isinstance(row, dict) or set(row) != _FIELDS:
        raise ProfileError("Unsupported virtual source registration fields")
    item, selector = row["item"], row["selector"]
    if (not isinstance(item, str) or _ITEM.fullmatch(item) is None or
            not isinstance(selector, str) or _SELECTOR.fullmatch(selector) is None or
            row["id"] != "virtual:" + selector or row["resource"] != "itm:" + item or
            row["adapter_id"] != ADAPTER_ID or row["optional"] is not False or
            not isinstance(row["resource_sha256"], str) or
            HEX256.fullmatch(row["resource_sha256"]) is None or
            type(row["copies"]) is not int or not 1 <= row["copies"] <= 0xFFFFFFFF):
        raise ProfileError("Unsupported virtual source registration")
    payload = row["payload"]
    if (not isinstance(payload, dict) or set(payload) != _PAYLOAD_FIELDS or
            payload["resref"] != item or type(payload["expiration"]) is not int or
            payload["expiration"] != 0 or type(payload["flags"]) is not int or
            payload["flags"] != 0 or not isinstance(payload["charges"], list) or
            len(payload["charges"]) != 3 or
            any(type(charge) is not int or not 0 <= charge <= 0xFFFF
                for charge in payload["charges"])):
        raise ProfileError("Invalid virtual source item payload")


def virtual_registration(item: str, selector: str, copies: int, resource_sha256: str,
                         charges: list[int]) -> dict:
    """Build an authorable row from a selected item and its installed ITM charges."""
    row = {"id": "virtual:" + selector, "item": item, "resource": "itm:" + item,
           "resource_sha256": resource_sha256, "adapter_id": ADAPTER_ID,
           "selector": selector, "copies": copies, "optional": False,
           "payload": {"resref": item, "expiration": 0,
                       "charges": deepcopy(charges), "flags": 0}}
    validate_registration(row)
    return row


def campaign_record(row: dict) -> dict:
    validate_registration(row)
    return {"id": row["id"], "item": row["item"],
            "resource": row["resource"], "owner": row["resource"],
            "kind": "virtual", "record": deepcopy(row["payload"]),
            "adapter_id": ADAPTER_ID, "selector": row["selector"],
            "concerns": []}
