"""Checked-in original-table evidence for data-only source and target records."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .original_catalog import (is_spin621_source, is_spin621_target, chapter3_place, creature_versions_place,
                               mode2_alias_spec, container_target_spec, resolve_template)
from .original_options import is_spell_shield_row, validate_spell_shield_spec
from .platform import preset_game
from .profiles import HEX256, ProfileError
from .special_places import place as special_place, templates as special_templates
from .templates import POOL_CHOICES, list_templates

SOURCE_ADAPTER = "original-source-v1"
TARGET_ADAPTER = "original-target-v1"
SOURCE_FIELDS = ("id", "source_ref", "campaign", "family", "source", "ident",
                 "token", "tier", "occurrence", "chance")
TARGET_FIELDS = ("id", "source_ref", "endpoint_id", "endpoint_kind",
                 "endpoint_source_ref", "campaign", "family", "location",
                 "target", "area", "ident", "tier", "occurrence", "chance",
                 "area_resource", "creature_resource", "container_name",
                 "members", "target_ids")
PAYLOAD_FIELDS = {"resref", "expiration", "charges", "flags"}
SHIPPED_REPLACEMENT = "flbhcryp"
SHIPPED_REPLACEMENT_HASH = "b0b7c29014cc40a9ee118279c2e7c77bb7a4f46fbd5d2ed45481dbddb1114a62"
_SHIPPED_REPLACEMENT_PATH = Path(__file__).resolve().parents[2] / "copy" / "flbhcryp.itm"


def shipped_replacement_hash() -> str:
    """Require the original mod's exact authored replacement file."""
    try:
        actual = hashlib.sha256(_SHIPPED_REPLACEMENT_PATH.read_bytes()).hexdigest()
    except OSError as exc:
        raise ProfileError("Original flbhcryp replacement file is absent") from exc
    if actual != SHIPPED_REPLACEMENT_HASH:
        raise ProfileError("Original flbhcryp replacement file changed")
    return actual


def _fields(value: object, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise ProfileError(f"Invalid original {label} fields")
    return value


def _hash(value: object) -> None:
    if not isinstance(value, str) or HEX256.fullmatch(value) is None:
        raise ProfileError("Invalid original resource fingerprint")


def _resource(value: object, kinds: tuple[str, ...]) -> str:
    if not isinstance(value, str) or ":" not in value:
        raise ProfileError("Invalid original resource identity")
    kind, name = value.split(":", 1)
    if (kind not in kinds or not 1 <= len(name) <= 8 or name != name.lower() or
            any(ord(char) < 33 or ord(char) > 126 or char in '/\\:*?"<>|'
                for char in name)):
        raise ProfileError("Invalid original resource identity")
    return value


def _payload(value: object, item: str) -> dict:
    payload = _fields(value, PAYLOAD_FIELDS, "item payload")
    if (payload["resref"] != item or type(payload["expiration"]) is not int or
            not 0 <= payload["expiration"] <= 0xFFFF or
            type(payload["flags"]) is not int or not 0 <= payload["flags"] <= 0xFFFFFFFF or
            not isinstance(payload["charges"], list) or len(payload["charges"]) != 3 or
            any(type(charge) is not int or not 0 <= charge <= 0xFFFF
                for charge in payload["charges"])):
        raise ProfileError("Invalid original item payload")
    return payload


def table_rows(catalog: dict, world: dict, plan: dict, *, memo: dict | None = None) -> tuple[dict, dict]:
    """Resolve the exact checked-in pool choices recorded in a preset plan.

    ``memo`` (Scans.tables) keeps the tables already resolved for these scans;
    their rows are shared and must not be changed."""
    template_id = plan.get("original_template_id")
    choices = plan.get("original_choices")
    options = plan.get("original_options")
    if template_id not in {row["id"] for row in list_templates()}:
        raise ProfileError("Unknown original template identity")
    if (not isinstance(choices, dict) or set(choices) != {"items", "targets"} or
            any(value not in POOL_CHOICES for value in choices.values()) or
            not isinstance(options, dict)):
        raise ProfileError("Invalid original pool choices or options")
    key = None
    if memo is not None:
        try:
            key = json.dumps([template_id, choices, options], sort_keys=True)
        except (TypeError, ValueError):
            pass
    tables = memo.get(key) if key is not None else None
    if tables is None:
        draft = resolve_template(catalog, world, template_id, game=preset_game(world, template_id),
                                 items=choices["items"], targets=choices["targets"],
                                 options=options)
        tables = (draft["choices"], draft["options"], {row["id"]: row for row in draft["items"]},
                  {row["id"]: row for row in draft["targets"]})
        if key is not None:
            memo[key] = tables
    if tables[0] != choices or tables[1] != options:
        raise ProfileError("Original pool choices or options changed")
    return tables[2], tables[3]


def special_rows(catalog: dict, world: dict, *, memo: dict | None = None) -> dict:
    """The rows of the game's special places (special_places.place) by
    identity, from Item Randomiser's lists that hold them, enabled: any
    configuration may give them rewards, also one that is no preset, whatever
    option a preset needs for them.

    ``memo`` (Scans.tables) keeps them for these scans; they are shared and
    must not be changed."""
    rows = memo.get("special") if memo is not None else None
    if rows is None:
        rows = {}
        for template_id in special_templates(world):
            draft = resolve_template(catalog, world, template_id, game=preset_game(world, template_id),
                                     items="standard", targets="extended")
            for row in draft["targets"]:
                if special_place(row) is not None:
                    rows.setdefault(row["id"], {**row, "enabled": True})
        if memo is not None:
            memo["special"] = rows
    return rows


def is_unreferenced_creature_source(row: dict, physical: dict[str, dict]) -> bool:
    """Recognize one authored, slotless CRE record without weakening direct binding."""
    identities = row["source_ids"]
    if len(identities) != 1 or row["replacement"] != "blank":
        return False
    source = physical.get(identities[0])
    return (source is not None and source["kind"] == "cre-template" and
            source["resource"] == row["source_resource"] and source["item"] == row["item_resref"] and
            source["record"]["slots"] == [] and
            source["concerns"] == ["template-instance-scope", "unreferenced-inventory-record"])


def validate_source_registration(registration: dict, rows: dict, world: dict, *,
                                 spell_shield: dict | None = None) -> list[str]:
    """Validate an authored source, returning physical IDs it reserves."""
    _fields(registration, {"id", "item", "resource", "resource_sha256", "adapter_id",
                           "selector", "copies", "optional", "payload",
                           "physical_source_ids", "authored"}, "source registration")
    identity = registration["selector"]
    row = rows.get(identity)
    if row is None or not row["enabled"] or registration["adapter_id"] != SOURCE_ADAPTER:
        raise ProfileError("Original source is not an enabled table row")
    if is_spell_shield_row(row) and spell_shield is not None:
        validate_spell_shield_spec(spell_shield)
        physical = [spell_shield["original_source_id"]] if spell_shield["original_source_id"] else []
        expected = {"id": spell_shield["source_id"], "item": "scrl8x", "resource": "sto:udduer01",
                    "resource_sha256": spell_shield["resource_sha256"], "adapter_id": SOURCE_ADAPTER,
                    "selector": row["id"], "copies": spell_shield["quantity"], "optional": False,
                    "payload": spell_shield["payload"], "physical_source_ids": physical,
                    "authored": {key: row[key] for key in SOURCE_FIELDS}}
        if registration != expected:
            raise ProfileError("Original Spell Shield source differs from its option stock")
        return physical
    if (registration["id"] != "original-source:" + identity or
            registration["item"] != row["item_resref"] or
            registration["resource"] != row["source_resource"] or
            registration["copies"] != 1 or registration["optional"] is not False):
        raise ProfileError("Original source identity disagrees with its table row")
    spell_grant = (is_spin621_source(row) and registration["resource"] == "spl:spin621" and
                   registration["item"] == "ring41")
    _resource(registration["resource"], ("cre", "bcs", "dlg", "spl") if spell_grant
              else ("cre", "bcs", "dlg"))
    _hash(registration["resource_sha256"])
    authored = {key: row[key] for key in SOURCE_FIELDS}
    physical_ids = registration["physical_source_ids"]
    if not isinstance(physical_ids, list) or any(not isinstance(value, str) for value in physical_ids):
        raise ProfileError("Invalid original physical source IDs")
    if physical_ids != sorted(set(physical_ids)):
        raise ProfileError("Original physical source IDs must be distinct and sorted")
    if row["adapter"] in ("scripted", "unshey"):
        expected = (sorted(source["id"] for source in world["sources"]
                           if source["resource"] == row["source_resource"]
                           and source["item"] == row["item_resref"])
                    if row["adapter"] == "unshey" else [])
    elif len(row["source_ids"]) > 1 or is_unreferenced_creature_source(
            row, {source["id"]: source for source in world["sources"]}):
        expected = sorted(row["source_ids"])
        if len(expected) > 1:
            authored["match_policy"] = "all_one_unit"
            authored["expected_matches"] = len(expected)
    else:
        raise ProfileError("Original physical row needs a direct source selection")
    if registration["authored"] != authored or physical_ids != expected:
        raise ProfileError("Original source evidence disagrees with the scanned table row")
    payload = _payload(registration["payload"], row["item_resref"])
    if physical_ids:
        physical = {source["id"]: source for source in world["sources"]}
        for selected in physical_ids:
            record = physical[selected]["record"]
            if payload != {key: record[key] for key in PAYLOAD_FIELDS}:
                raise ProfileError("Original source payload differs from its physical record")
    elif payload["expiration"] != 0 or payload["flags"] != 0:
        raise ProfileError("Original scripted source must use a neutral item payload")
    return physical_ids


def validate_target_registration(registration: dict, rows: dict) -> None:
    _fields(registration, {"id", "resource", "resource_sha256", "adapter_id",
                           "selector", "capacity", "optional", "authored"},
            "target registration")
    identity = registration["selector"]
    row = rows.get(identity)
    if row is None or not row["enabled"] or registration["adapter_id"] != TARGET_ADAPTER:
        raise ProfileError("Original target is not an enabled table row")
    if (registration["id"] != "original-target:" + identity or
            registration["resource"] != row["area_resource"] or
            type(registration["capacity"]) is not int or
            not 1 <= registration["capacity"] <= 4096 or
            registration["optional"] is not False or
            registration["authored"] != {key: row[key] for key in TARGET_FIELDS}):
        raise ProfileError("Original target evidence disagrees with its table row")
    _target_resource(row, registration["resource"])
    _hash(registration["resource_sha256"])


def _target_resource(authored: dict, resource: str) -> None:
    reward = mode2_alias_spec(authored)
    if reward is not None:
        if resource != reward["resource"]:
            raise ProfileError("Original reward target must use its registered primary resource")
        _resource(resource, ("dlg", "bcs"))
    else:
        spell_target = is_spin621_target(authored) and resource == "spl:spin621"
        _resource(resource, ("spl",) if spell_target else ("are",))


def target_kind(authored: dict) -> str:
    if container_target_spec(authored) is not None:
        return "container"
    reward = mode2_alias_spec(authored)
    if reward is not None and reward.get("inventory_target"):
        return "creature-target"
    if is_spin621_target(authored) or reward is not None:
        return "scripted-target"
    if creature_versions_place(authored) is not None:
        # The first version of the creature (original_catalog.CREATURE_VERSIONS).
        return "creature-target"
    if chapter3_place(authored):
        # Its vampires and thieves (chapter3.py), one of whom takes each reward.
        return "group"
    kind = authored["endpoint_kind"]
    if kind is None:
        raw = authored["target"]
        if not isinstance(raw, str):
            raise ProfileError("Invalid original target identity")
        kind = ("group" if raw.endswith(".flg") else
                "creature" if raw.endswith(".cre") else
                "store" if raw.endswith(".sto") else
                "legacy_adapter" if raw.endswith((".bcs", ".dlg")) else
                "container")
    if kind not in {"creature", "legacy_adapter", "group", "container", "store"}:
        raise ProfileError("Invalid original target kind")
    return {"creature": "creature-target", "legacy_adapter": "scripted-target"}.get(kind, kind)


def source_record(registration: dict) -> dict:
    return {"id": registration["id"], "item": registration["item"],
            "resource": registration["resource"], "owner": registration["resource"],
            "kind": "store" if is_spell_shield_row(registration["authored"]) else "authored-source",
            "record": deepcopy(registration["payload"]),
            "adapter_id": SOURCE_ADAPTER, "selector": registration["selector"],
            "physical_source_ids": deepcopy(registration["physical_source_ids"]),
            "authored": deepcopy(registration["authored"]), "concerns": []}


def target_record(registration: dict) -> dict:
    return {"id": registration["id"], "resource": registration["resource"],
            "kind": target_kind(registration["authored"]),
            "selector": deepcopy(registration["authored"]),
            "adapter_id": TARGET_ADAPTER, "concerns": []}


def validate_campaign_source(row: dict, *, spell_shield: dict | None = None) -> int:
    _fields(row, {"id", "quantity", "record", "resource_sha256"}, "campaign source row")
    record = _fields(row.get("record"), {"id", "item", "resource", "owner", "kind",
                                          "record", "adapter_id", "selector",
                                          "physical_source_ids", "authored", "concerns"},
                     "campaign source")
    if is_spell_shield_row(record["authored"]) and spell_shield is not None:
        validate_spell_shield_spec(spell_shield)
        registration = {"id": spell_shield["source_id"], "item": "scrl8x", "resource": "sto:udduer01",
                        "resource_sha256": spell_shield["resource_sha256"], "adapter_id": SOURCE_ADAPTER,
                        "selector": "bg2:mode2:item:228", "copies": spell_shield["quantity"], "optional": False,
                        "payload": spell_shield["payload"],
                        "physical_source_ids": ([spell_shield["original_source_id"]]
                                                if spell_shield["original_source_id"] else []),
                        "authored": record["authored"]}
        if (set(record["authored"]) != set(SOURCE_FIELDS) or record != source_record(registration)
                or row["id"] != spell_shield["source_id"] or row["quantity"] != spell_shield["quantity"]
                or row["resource_sha256"] != spell_shield["resource_sha256"]):
            raise ProfileError("Invalid authored Spell Shield campaign source")
        return row["quantity"]
    if (not isinstance(record["selector"], str) or
            record["kind"] != "authored-source" or record["adapter_id"] != SOURCE_ADAPTER or
            record["owner"] != record["resource"] or record["concerns"] != [] or
            record["id"] != row.get("id") or record["id"] != "original-source:" + record["selector"] or
            type(row.get("quantity")) is not int or row["quantity"] != 1 or
            "replacement" in row):
        raise ProfileError("Invalid authored campaign source")
    _hash(row.get("resource_sha256"))
    spell_grant = (is_spin621_source(record["authored"]) and record["resource"] == "spl:spin621" and
                   record["item"] == "ring41")
    _resource(record["resource"], ("cre", "bcs", "dlg", "spl") if spell_grant
              else ("cre", "bcs", "dlg"))
    _resource("itm:" + record["item"] if isinstance(record["item"], str) else None,
              ("itm",))
    _payload(record["record"], record["item"])
    authored = record["authored"]
    if (not isinstance(authored, dict) or
            set(authored) not in (set(SOURCE_FIELDS), set(SOURCE_FIELDS) | {"match_policy", "expected_matches"}) or
            authored.get("id") != record["selector"] or
            not isinstance(record["physical_source_ids"], list) or
            any(not isinstance(identity, str) or not identity for identity in record["physical_source_ids"]) or
            record["physical_source_ids"] != sorted(set(record["physical_source_ids"]))):
        raise ProfileError("Invalid authored campaign source evidence")
    if "match_policy" in authored and (authored["match_policy"] != "all_one_unit" or
                                       type(authored["expected_matches"]) is not int or
                                       authored["expected_matches"] != len(record["physical_source_ids"]) or
                                       authored["expected_matches"] < 2):
        raise ProfileError("Invalid authored grouped source evidence")
    if spell_grant and record["physical_source_ids"]:
        raise ProfileError("Spell-grant source cannot reserve physical inventory records")
    return 1


def validate_campaign_target(row: dict) -> int:
    _fields(row, {"id", "capacity", "record", "resource_sha256"}, "campaign target row")
    record = _fields(row.get("record"), {"id", "resource", "kind", "selector",
                                          "adapter_id", "concerns"}, "campaign target")
    selector = record["selector"]
    if (record["adapter_id"] != TARGET_ADAPTER or record["concerns"] != [] or
            record["id"] != row.get("id") or
            not isinstance(selector, dict) or set(selector) != set(TARGET_FIELDS) or
            record["id"] != "original-target:" + str(selector.get("id")) or
            record["resource"] != selector.get("area_resource") or
            record["kind"] != target_kind(selector) or
            type(row.get("capacity")) is not int or not 1 <= row["capacity"] <= 4096):
        raise ProfileError("Invalid authored campaign target")
    _target_resource(selector, record["resource"])
    _hash(row.get("resource_sha256"))
    return row["capacity"]
