"""Original option data and byte patches, without choosing a campaign's rolls.

The definitions below come from Item Randomiser's component 560 and
the component-570 block of its lib/fixes.tpa. Callers supply installed resource bytes
and allocated TLK references; this module never opens or changes a game.
"""

from __future__ import annotations

import struct
from collections.abc import Collection
from copy import deepcopy

from .game_names import EetNames, convert_resource
from .profiles import HEX256, ProfileError
from .world_formats import parse_sto


def tome_variants(campaign: str = "bg1", game: str = "eet",
                  names: EetNames | None = None) -> list[str]:
    """Mode 1's b2c reward chooses one of all six stat books uniformly."""
    return [convert_resource(f"book{number:02}", "itm", campaign, game, names)
            for number in range(3, 9)]


def patch_spell_shield_stock(data: bytes) -> bytes:
    """Apply component 560 to UDDUER01, retaining all unrelated stock bytes.

    As in the original, the first SCRL8X row wins, including zero stock and
    infinite rows. Absent stock is inserted before the first matching anchor;
    the new sale table is appended so all other section offsets stay intact.
    """
    rows = parse_sto(data)["items"]
    result = bytearray(data)
    for row in rows:
        if row["resref"] == "scrl8x":
            struct.pack_into("<I", result, row["byte_offset"] + 20,
                             max(3, row["quantity"]))
            return bytes(result)
    records = [data[row["byte_offset"]:row["byte_offset"] + 28] for row in rows]
    insert = next((i for i, row in enumerate(rows)
                   if row["resref"] in {"scrlal", "scrl7q", "scrl7p"}), len(rows))
    records.insert(insert, struct.pack("<8s4H3I", b"scrl8x", 0, 0, 0, 0, 1, 3, 0))
    struct.pack_into("<II", result, 0x34, len(data), len(records))
    result.extend(b"".join(records))
    parse_sto(result)
    return bytes(result)


SPELL_SHIELD_SOURCE = "original-source:bg2:mode2:item:228"
_STOCK_PAYLOAD = ("resref", "expiration", "charges", "flags")


def is_spell_shield_row(row: dict) -> bool:
    """The sole original source affected by component 560, not other SCRL8X."""
    return isinstance(row, dict) and all(row.get(key) == value for key, value in {
        "id": "bg2:mode2:item:228", "source_ref": "lists/items/mode2/bg2.2da:228",
        "campaign": "bg2", "family": "mode2", "source": "udduer01.sto",
        "ident": "sw10", "tier": "s7", "token": "w01"}.items())


def spell_shield_randomized(rows) -> bool:
    """Whether the scroll's shop row is among these authored rows: the rewards
    a configuration finally holds, whatever its tables or pool choices."""
    return any(is_spell_shield_row(row) for row in rows)


def spell_shield_spec(world: dict, *, randomized: bool) -> dict:
    """Describe the effective stock without changing the scan or source IDs.

    Existing payload, supply flags and quantities above three remain exact.
    An absent row uses ADD_STORE_ITEM's authored identified, zero-charge payload.
    Source selection remains separate: the shop option also works without
    scroll randomisation or the extended item pool. <randomized> says whether
    the scroll's shop row is one of the configuration's rewards.
    """
    resource = next((row for row in world["resources"] if row["id"] == "sto:udduer01"), None)
    if resource is None:
        raise ProfileError("More Spell Shield copies need the shop UDDUER01.STO, which this game does not have")
    if any(row["id"] == "sto:udduer01" for row in world.get("unreadable", [])):
        raise ProfileError("More Spell Shield copies need the shop UDDUER01.STO, which cannot be read")
    if not any(row["id"] == "itm:scrl8x" for row in world["resources"]):
        raise ProfileError("More Spell Shield copies need SCRL8X.ITM, which this game does not have")
    rows = sorted((row for row in world["sources"] if row["resource"] == "sto:udduer01"),
                  key=lambda row: row["record"]["record_index"])
    if randomized and sum(row["item"] == "scrl8x" for row in rows) > 1:
        raise ProfileError("Spell Shield randomisation has ambiguous duplicate shop-source rows")
    first = next((row for row in rows if row["item"] == "scrl8x"), None)
    record = deepcopy(first["record"]) if first else {}
    index = (record["record_index"] if first else
             next((row["record"]["record_index"] for row in rows
                   if row["item"] in {"scrlal", "scrl7q", "scrl7p"}), len(rows)))
    result = {"resource": "sto:udduer01", "resource_sha256": resource["sha256"],
              "original_source_id": first["id"] if first else "", "original_record": record,
              "record_index": index, "quantity": max(3, record.get("quantity", 0)),
              "payload": ({key: deepcopy(record[key]) for key in _STOCK_PAYLOAD} if first else
                          {"resref": "scrl8x", "expiration": 0, "charges": [0, 0, 0], "flags": 1}),
              "source_id": SPELL_SHIELD_SOURCE if randomized else ""}
    validate_spell_shield_spec(result)
    return result


def validate_spell_shield_spec(spec: dict) -> None:
    """Check immutable campaign metadata; the exporter also compares the scan."""
    fields = {"resource", "resource_sha256", "original_source_id", "original_record",
              "record_index", "quantity", "payload", "source_id"}
    if (not isinstance(spec, dict) or set(spec) != fields or spec["resource"] != "sto:udduer01"
            or not isinstance(spec["resource_sha256"], str)
            or not HEX256.fullmatch(spec["resource_sha256"])
            or spec["source_id"] not in ("", SPELL_SHIELD_SOURCE)
            or type(spec["record_index"]) is not int or not 0 <= spec["record_index"] <= 0xFFFFFFFF
            or type(spec["quantity"]) is not int or not 3 <= spec["quantity"] <= 0xFFFFFFFF):
        raise ProfileError("Invalid Spell Shield stock metadata")
    record = spec["original_record"]
    if not isinstance(record, dict):
        raise ProfileError("Invalid Spell Shield original record")
    if record:
        if (set(record) != {"record_index", "byte_offset", "resref", "expiration", "charges", "flags",
                            "quantity", "infinite_supply", "infinite_supply_flag"}
                or record.get("resref") != "scrl8x"
                or type(record.get("record_index")) is not int
                or record["record_index"] != spec["record_index"]
                or spec["original_source_id"] != f"sto:udduer01/item/{record['record_index']}"
                or type(record.get("quantity")) is not int or not 0 <= record["quantity"] <= 0xFFFFFFFF
                or spec["quantity"] != max(3, record["quantity"])
                or type(record.get("infinite_supply_flag")) is not int
                or not 0 <= record["infinite_supply_flag"] <= 0xFFFFFFFF
                or type(record.get("infinite_supply")) is not bool
                or record["infinite_supply"] != bool(record["infinite_supply_flag"])
                or type(record.get("byte_offset")) is not int or record["byte_offset"] < 0x9C):
            raise ProfileError("Spell Shield metadata differs from its original stock")
        payload = {key: record[key] for key in _STOCK_PAYLOAD}
    else:
        if spec["original_source_id"] != "" or spec["quantity"] != 3:
            raise ProfileError("Invalid inserted Spell Shield stock")
        payload = {"resref": "scrl8x", "expiration": 0, "charges": [0, 0, 0], "flags": 1}
    if (spec["payload"] != payload or type(payload["expiration"]) is not int
            or not 0 <= payload["expiration"] <= 0xFFFF or type(payload["flags"]) is not int
            or not 0 <= payload["flags"] <= 0xFFFFFFFF or not isinstance(payload["charges"], list)
            or len(payload["charges"]) != 3
            or any(type(n) is not int or not 0 <= n <= 0xFFFF for n in payload["charges"])):
        raise ProfileError("Invalid Spell Shield stock payload")


def cursed_disguise_choices(available: Collection[str], *, game: str = "eet",
                           item_revisions: bool = False) -> list[dict]:
    """Return each original die face, including weighted fallback duplicates.

    In EE/EET the original TUTU_VAR is empty. BG1-only games lack some optional
    BG2 items: their original die faces fall back instead of being rerolled.
    Item Revisions suppresses PLAT17 and selects alternate LEAT06 descriptions.
    Sources absent from the install are omitted. Required donors must exist.
    """
    if game not in {"bgee", "bg2ee", "eet"}:
        raise ProfileError(f"Unsupported original-disguise game: {game}")
    if type(item_revisions) is not bool:
        raise ProfileError("Item Revisions presence must be a Boolean")
    names = set(available)
    result = []

    def choices(source: str, faces: list[tuple[str, int | None]], *,
                optional: set[str] | None = None, plate: bool = False) -> None:
        if source not in names:
            return
        fallback = faces[-1]
        variants = []
        for donor, description in faces:
            if donor not in names and donor in (optional or set()):
                donor, description = fallback
            if donor not in names:
                raise ProfileError(f"Cursed disguise items need {donor.upper()}.ITM to disguise "
                                   f"{source.upper()}.ITM, and this game does not have it")
            variants.append({"donor": donor, "description_tra": description,
                             "name_tra": 212 if plate else None,
                             "nerf_plate": plate})
        result.append({"source": source, "variants": variants})

    choices("brac08", [("brac06", 200), ("brac07", 201),
                       ("brac10", 203), ("brac09", 202)])
    choices("brac11", [("brac01", 205), ("brac02", 205), ("brac03", 205),
                       ("brac13", 205), ("brac14", 205), ("brac15", 205),
                       ("brac04", 206)], optional={"brac13", "brac14", "brac15"})
    choices("leat06", [("leat05", 210 if item_revisions else 208),
                       ("leat07", 210 if item_revisions else 208),
                       ("leat08", 211 if item_revisions else 209)])
    if not item_revisions:
        choices("plat17", [("plat02", 213), ("plat11", 214), ("plat10", 213)], plate=True)
    choices("ring04", [("ring06", 216), ("ring07", 216), ("ring22", 218),
                       ("ring09", 217), ("ring31", 219), ("ring08", 220)], optional={"ring31"})
    choices("ring23", [("ring06", 222), ("ring07", 222), ("ring08", 225),
                       ("ring09", 223), ("ring31", 224), ("ring08", 225)], optional={"ring31"})
    choices("sper03", [("sper05", None), ("sper02", None)], optional={"sper05"})
    choices("sw2h03", [("sw2h11", None), ("sw2h02", None)], optional={"sw2h11"})
    return result


def _strref(value: int, field: str) -> int:
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
        raise ProfileError(f"Invalid original option {field} string reference")
    return value


def patch_cursed_disguise(source: bytes, donor: bytes, *,
                         description_strref: int | None = None,
                         plate_name_strref: int | None = None) -> bytes:
    """Copy precisely the original appearance fields, plus authored text/plate fix.

    SPER03 and SW2H03 retain their identified descriptions. Other choices need
    the authored ``description_tra`` allocated into TLK by the caller. A plate
    name reference requests the original PLAT17 enchantment and base-AC adjustment.
    """
    for label, data in (("source", source), ("donor", donor)):
        if len(data) < 0x72 or data[:8] != b"ITM V1  ":
            raise ProfileError(f"Invalid cursed-disguise {label} ITM")
    result = bytearray(source)
    for offset, size in ((0x3A, 8), (0x50, 4), (0x58, 8)):
        result[offset:offset + size] = donor[offset:offset + size]
    if description_strref is not None:
        struct.pack_into("<I", result, 0x54, _strref(description_strref, "description"))
    if plate_name_strref is not None:
        struct.pack_into("<I", result, 0x0C, _strref(plate_name_strref, "plate name"))
        struct.pack_into("<I", result, 0x60, 0)
        effect_offset = struct.unpack_from("<I", source, 0x6A)[0]
        effect_count = struct.unpack_from("<H", source, 0x70)[0]
        if effect_count and (effect_offset < 0x72 or effect_offset + effect_count * 0x30 > len(source)):
            raise ProfileError("Invalid cursed plate global effect range")
        for index in range(effect_count):
            offset = effect_offset + index * 0x30
            if (struct.unpack_from("<H", source, offset)[0] == 0 and
                    struct.unpack_from("<I", source, offset + 8)[0] == 16):
                struct.pack_into("<I", result, offset + 4, 3)
    return bytes(result)
