"""The reviewed Deck of Many Things Key spell's one Create Item effect.

Original Mode 2 replaces opcode 122 through an EFF in lib/weidu_action.tpa.
The native campaign instead invokes FLPRKEY at that same effect position;
the callback retains or suppresses the original ring and records its recipient.
"""
from __future__ import annotations

import hashlib
import struct

from .profiles import ProfileError

ORIGINAL_SHA256 = "01c39682e3f9d4d0c8274a893b784924ae5b8cd1464afdcc34d04d0190addb50"
PATCHED_SHA256 = "94aa22201f50a94c343dbb2f5ba2946f31ffa22b71d3d1ab23051f94f79869da"
EFFECT_OFFSET = 0x12A
SPELL = {
    "quest": "key", "resource": "spl:spin621", "item": "ring41",
    "selected_global": "FLPR_QKEY", "consumed_global": "FLPR_XKEY",
    "event_global": "FLPR_EKEY", "recipient_local": "FLPR_RKEY",
}
PAYLOAD = {"resref": "ring41", "expiration": 0, "charges": [1, 0, 0], "flags": 0}


def original_key_spell(data: bytes) -> bytes:
    """Accept the reviewed original or recover only our exact static patch."""
    digest = hashlib.sha256(data).hexdigest()
    if digest == ORIGINAL_SHA256:
        return data
    if digest == PATCHED_SHA256:
        original = bytearray(data)
        struct.pack_into("<H", original, EFFECT_OFFSET, 122)
        original[EFFECT_OFFSET + 0x14:EFFECT_OFFSET + 0x1C] = b"ring41\0\0"
        if hashlib.sha256(original).hexdigest() == ORIGINAL_SHA256:
            return bytes(original)
    raise ProfileError("Deck of Many Things Key spell changed; rescan and review it")


def patch_key_spell(data: bytes) -> bytes:
    """Replace only the item's opcode and resref; retain timing and targeting."""
    data = original_key_spell(data)
    if (len(data) != 394 or data[:8] != b"SPL V1  " or
            struct.unpack_from("<IHIHH", data, 0x64) != (114, 1, 154, 0, 0) or
            struct.unpack_from("<HH", data, 114 + 0x1E) != (5, 0)):
        raise ProfileError("Unsupported Deck of Many Things Key spell layout")
    expected = bytes.fromhex(
        "7a00020001000000000000000900000000006400"
        "72696e67343100000000000000000000000000000000000000000000"
    )
    if data[EFFECT_OFFSET:EFFECT_OFFSET + 0x30] != expected:
        raise ProfileError("Unsupported Deck of Many Things Key item effect")
    out = bytearray(data)
    struct.pack_into("<H", out, EFFECT_OFFSET, 402)
    out[EFFECT_OFFSET + 0x14:EFFECT_OFFSET + 0x1C] = b"FLPRKEY\0"
    if hashlib.sha256(out).hexdigest() != PATCHED_SHA256:
        raise ProfileError("Deck of Many Things Key patch differs from its reviewed bytes")
    return bytes(out)
