"""Original Mode 1 group recipient preference, without making a random draw.

The eight masks follow ``read_itm_unusabilities`` and
``define_cre_unusabilities`` in ``lib/fl#gear.tpa``. The fallback to all
available creatures follows ``select_group_cre`` in ``lib/lib.tpa``.
"""

from __future__ import annotations

from collections.abc import Mapping


_ALIGNMENT = {17: 0x14, 33: 0x24, 49: 0x05,
              18: 0x18, 34: 0x28, 50: 0x09,
              19: 0x12, 35: 0x22, 51: 0x03}
_CLASS_2 = {17: 0x80, 8: 0x40, 7: 0x20, 16: 0x10,
            2: 0x08, 18: 0x04, 15: 0x02, 14: 0x01}
_CLASS_3 = {10: 0x01, 9: 0x02, 1: 0x04, 13: 0x08,
            6: 0x10, 12: 0x20, 4: 0x40}
_RACE_4 = {4: 0x01, 3: 0x02, 5: 0x04, 1: 0x08,
           6: 0x10, 7: 0x80}
_KIT_1 = {0x40130000: 0x01, 0x40140000: 0x02, 0x40150000: 0x04,
          0x40100000: 0x08, 0x40110000: 0x10, 0x40120000: 0x20,
          0x00004000: 0x40, 0x00008000: 0x80}
_KIT_2 = {0x40080000 + i * 0x10000: 1 << i for i in range(8)}
_KIT_3 = {0x01000000: 0x01, 0x02000000: 0x02, 0x04000000: 0x04,
          0x08000000: 0x08, 0x10000000: 0x10, 0x20000000: 0x20,
          0x40070000: 0x80}
_KIT_4 = {0x40010000: 0x01, 0x40020000: 0x02, 0x40030000: 0x04,
          0x40040000: 0x08, 0x40050000: 0x10, 0x40060000: 0x20,
          0x00400000: 0x40, 0x00800000: 0x80}


def _byte(data: bytes, offset: int) -> int:
    return data[offset] if len(data) > offset else 0


def _long(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 4], "little") if len(data) >= offset + 4 else 0


def _item_masks(item_bytes: bytes) -> tuple[int, ...]:
    if len(item_bytes) <= 0x71:
        return (0,) * 8
    return tuple(_byte(item_bytes, offset) for offset in
                 (0x1e, 0x1f, 0x20, 0x21, 0x29, 0x2b, 0x2d, 0x2f))


def _creature_masks(cre_bytes: bytes) -> tuple[int, ...]:
    kit = _long(cre_bytes, 0x244)
    race, klass, alignment = (_byte(cre_bytes, offset)
                              for offset in (0x272, 0x273, 0x27b))
    unuse_1 = _ALIGNMENT.get(alignment, 0)
    unuse_1 |= {5: 0x40, 3: 0x80}.get(klass, 0)
    unuse_2 = _CLASS_2.get(klass, 0)
    unuse_3 = _CLASS_3.get(klass, 0) | (0x80 if race == 2 else 0)
    unuse_4 = _RACE_4.get(race, 0)
    unuse_4 |= {20: 0x20, 11: 0x40}.get(klass, 0)
    kit_3 = _KIT_3.get(kit, 0) | (0x40 if kit not in (0, 0x40000000) else 0)
    return (unuse_1, unuse_2, unuse_3, unuse_4,
            _KIT_1.get(kit, 0), _KIT_2.get(kit, 0), kit_3,
            _KIT_4.get(kit, 0))


def usable(item_bytes: bytes, cre_bytes: bytes) -> bool:
    """Whether the old group selector would prefer this creature for the item."""
    return all(not (item_mask & cre_mask) for item_mask, cre_mask in
               zip(_item_masks(item_bytes), _creature_masks(cre_bytes)))


def preferred_members(item_bytes: bytes, members: Mapping[str, bytes]) -> list[str]:
    """Return sorted usable members, or all sorted members when none qualify."""
    names = sorted(members)
    preferred = [name for name in names if usable(item_bytes, members[name])]
    return preferred or names
