"""Compile immutable inputs for the isolated logical campaign prototype.

These artifacts do not activate the installed Randomiser or prove engine source
suppression. Physical records and the reviewed BLPOOL grant are supported. The
Lua companion is generated data, never user-provided executable configuration.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections import Counter
from copy import deepcopy
from pathlib import Path

from .profiles import (DEFAULT_SHOP_PRICING, HEX256, PROFILE_ID, ProfileError,
                       _digest, canonical_bytes, ensure_output_outside_game,
                       read_json, validate_shop_pricing, write_json)
from .sources import (DANGLING_INVENTORY, INVALID_INVENTORY, UNRESOLVED_STORAGE,
                      UNUSABLE_SOURCE, bind_profile)
from .scripted_sources import (ITEM, RESOURCE, SOURCE_ID,
                               campaign_record, validate_registration)
from .virtual_sources import ADAPTER_ID as VIRTUAL_ADAPTER_ID
from .virtual_sources import campaign_record as virtual_campaign_record
from .virtual_sources import validate_registration as validate_virtual_registration
from .original_registrations import (SOURCE_ADAPTER as ORIGINAL_SOURCE_ADAPTER,
                                     TARGET_ADAPTER as ORIGINAL_TARGET_ADAPTER,
                                     SHIPPED_REPLACEMENT, SHIPPED_REPLACEMENT_HASH,
                                     source_record as original_source_record,
                                     validate_campaign_source as validate_original_campaign_source,
                                     validate_campaign_target as validate_original_campaign_target)
from .templates import POOL_CHOICES, _options as original_options, list_templates
from .original_options import SPELL_SHIELD_SOURCE, validate_spell_shield_spec
from .cursed_disguises import validate_cursed_disguise_spec

CAMPAIGN_SCHEMA = "flir-campaign-prototype-v1"
POINTER_SCHEMA = "flir-prototype-next-v1"
STATE = "prototype-not-playable"
MAX_REWARD_UNITS = 4096
MAX_TARGET_CAPACITY = 4096
# Target kinds that can take a reward beyond the authored pairing (overflow).
OVERFLOW_TARGET_KINDS = frozenset({"actor", "cre-template", "container", "store", "group",
                                   "creature-target"})
RUNTIME_REQUIREMENTS = [
    "campaign-revision-persistence", "exact-source-suppression",
    "new-game-assignment", "target-instance-resolution",
    "nonoverlapping-runtime-selectors", "verified-lootable-delivery",
]
_FIELDS = {"schema", "state", "revision", "bound_revision", "profile_id",
           "world_fingerprint", "sources", "targets", "runtime_requirements"}
_MAX_LUA_INTEGER = 2**53 - 1


def _fields(value: object, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise ProfileError(f"Unsupported campaign {label} fields")
    return value


def _integer(value: object, label: str, low: int = 0, high: int = 0xFFFFFFFF) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ProfileError(f"Invalid campaign {label}")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise ProfileError(f"Invalid campaign {label}")
    return value


def _hash(value: object, label: str) -> str:
    if not isinstance(value, str) or not HEX256.fullmatch(value):
        raise ProfileError(f"Invalid campaign {label}")
    return value


def _resref(value: object) -> str:
    value = _text(value, "resource name")
    if (not 1 <= len(value) <= 8 or value != value.lower() or
            any(ord(c) > 126 or c in '/\\:*?"<>| ' for c in value)):
        raise ProfileError("Invalid campaign resource name")
    return value


def _resource(value: object, kind: str) -> str:
    value = _text(value, "resource identity")
    if not value.startswith(kind + ":"):
        raise ProfileError("Campaign resource kind disagrees with its record")
    _resref(value[len(kind) + 1:])
    return value


def _concerns(record: dict) -> list:
    concerns = record.get("concerns")
    if (not isinstance(concerns, list) or any(not isinstance(x, str) for x in concerns)
            or concerns != sorted(set(concerns))):
        raise ProfileError("Invalid campaign record concerns")
    return concerns


def _validate_source(row: dict, *, spell_shield: dict | None = None) -> int:
    if (isinstance(row, dict) and isinstance(row.get("record"), dict) and
            row["record"].get("adapter_id") == ORIGINAL_SOURCE_ADAPTER):
        _fields(row, {"id", "quantity", "record", "resource_sha256"}, "source")
        return validate_original_campaign_source(row, spell_shield=spell_shield)
    _fields(row, {"id", "quantity", "record", "resource_sha256"}
            | ({"replacement"} if isinstance(row, dict) and "replacement" in row else set()), "source")
    _hash(row["resource_sha256"], "source resource fingerprint")
    quantity = _integer(row["quantity"], "source quantity", 1, MAX_REWARD_UNITS)
    source = _fields(row["record"], {"id", "item", "resource", "owner", "kind",
                                     "record", "concerns"} |
                     ({"adapter_id", "selector"} if isinstance(row, dict) and
                      isinstance(row.get("record"), dict) and
                      row["record"].get("kind") in ("scripted", "virtual") else set()), "source record")
    if source["kind"] == "scripted":
        # The fingerprint is BLPOOL.BCS as the installer left it; the
        # export checks it against the game.
        if (row["id"] != SOURCE_ID or row["quantity"] != 1 or
                source != campaign_record({"id": SOURCE_ID, "item": ITEM,
                    "resource": RESOURCE, "resource_sha256": row["resource_sha256"],
                    "adapter_id": "blpool-v1", "selector": "pool-reward",
                    "copies": 1, "optional": False}) or "replacement" in row):
            raise ProfileError("Unsupported scripted campaign source")
        return 1
    if source["kind"] == "virtual":
        registration = {"id": source["id"], "item": source["item"],
                        "resource": source["resource"],
                        "resource_sha256": row["resource_sha256"],
                        "adapter_id": source["adapter_id"],
                        "selector": source["selector"], "copies": quantity,
                        "optional": False, "payload": source["record"]}
        validate_virtual_registration(registration)
        if (row["id"] != source["id"] or source != virtual_campaign_record(registration)
                or "replacement" in row):
            raise ProfileError("Unsupported virtual campaign source")
        return quantity
    if source["kind"] not in ("cre-template", "embedded-cre", "container", "store"):
        raise ProfileError("Campaign prototype accepts only physical sources")
    if UNUSABLE_SOURCE.intersection(_concerns(source)):
        raise ProfileError("Campaign source is not finite and unambiguous")
    _resref(source["item"])
    owner = _text(source["owner"], "source owner")
    record = source["record"]
    if not isinstance(record, dict) or record.get("resref") != source["item"]:
        raise ProfileError("Invalid campaign inventory record")
    if "inventory_diagnostics" in record or "raw_resref_hex" in record:
        raise ProfileError("Campaign source has quarantined inventory diagnostics")
    for key in ("record_index", "byte_offset", "flags"):
        _integer(record.get(key), "source " + key)
    _integer(record.get("expiration"), "source expiration", high=0xFFFF)
    identity = _text(row["id"], "source identity")
    if identity != source["id"] or identity != f"{owner}/item/{record['record_index']}":
        raise ProfileError("Campaign source identity disagrees with its record")
    charges = record.get("charges")
    if not isinstance(charges, list) or len(charges) != 3:
        raise ProfileError("Campaign source needs three charge values")
    for charge in charges:
        _integer(charge, "source charge", high=0xFFFF)
    kind = source["kind"]
    if "replacement" in row:
        replacement = row["replacement"]
        if not isinstance(replacement, dict) or kind not in ("cre-template", "embedded-cre", "container"):
            raise ProfileError("Campaign replacement requires a creature or container source")
        name = _resref(replacement.get("resref"))
        _fields(replacement, {"resref"} if name == "blank" else {"resref", "resource_sha256"}, "replacement")
        if name == source["item"]:
            raise ProfileError("Campaign replacement must differ from the relocated item")
        if name != "blank":
            _hash(replacement["resource_sha256"], "replacement fingerprint")
            if (name == SHIPPED_REPLACEMENT and
                    replacement["resource_sha256"] != SHIPPED_REPLACEMENT_HASH):
                raise ProfileError("Original flbhcryp replacement fingerprint changed")
    resource = _resource(source["resource"], {"cre-template": "cre", "embedded-cre": "are",
                                           "container": "are", "store": "sto"}[kind])
    if kind in ("cre-template", "store") and owner != resource:
        raise ProfileError("Campaign source has a mismatched template owner")
    if kind in ("cre-template", "embedded-cre"):
        slots = record.get("slots")
        if not isinstance(slots, list) or len(slots) != 1:
            raise ProfileError("Campaign creature source needs one unambiguous slot")
        _integer(slots[0], "source slot", high=37)
    if kind == "embedded-cre":
        prefix = resource + "/actor/"
        if (not owner.startswith(prefix) or not owner[len(prefix):].isascii()
                or not owner[len(prefix):].isdecimal()
                or str(int(owner[len(prefix):])) != owner[len(prefix):]):
            raise ProfileError("Campaign embedded source has an invalid owner")
    if kind == "container":
        index = _integer(record.get("container_index"), "source container index")
        if (record.get("container_indexes") != [index]
                or owner != f"{resource}/container/{index}"):
            raise ProfileError("Campaign container source has ambiguous ownership")
    if kind == "store":
        if (record.get("quantity") != quantity or type(record.get("quantity")) is not int
                or record.get("infinite_supply") is not False
                or type(record.get("infinite_supply_flag")) is not int
                or record["infinite_supply_flag"] != 0):
            raise ProfileError("Campaign store quantity must match finite stock")
    elif quantity != 1:
        raise ProfileError("A physical non-store source contains one reward record")
    return quantity


def _validate_target(row: dict) -> int:
    if (isinstance(row, dict) and isinstance(row.get("record"), dict) and
            row["record"].get("adapter_id") == ORIGINAL_TARGET_ADAPTER):
        _fields(row, {"id", "capacity", "record", "resource_sha256"}, "target")
        return validate_original_campaign_target(row)
    _fields(row, {"id", "capacity", "record", "resource_sha256"}, "target")
    capacity = _integer(row["capacity"], "target capacity", 1, MAX_TARGET_CAPACITY)
    _hash(row["resource_sha256"], "target resource fingerprint")
    target = _fields(row["record"], {"id", "resource", "kind", "selector", "concerns"}, "target record")
    kind = target["kind"]
    if kind not in ("cre-template", "actor", "container", "store"):
        raise ProfileError("Invalid campaign target kind")
    if {DANGLING_INVENTORY, INVALID_INVENTORY, UNRESOLVED_STORAGE}.intersection(_concerns(target)):
        raise ProfileError("Campaign target has invalid inventory references")
    resource = _resource(target["resource"], {"cre-template": "cre", "actor": "are",
                                             "container": "are", "store": "sto"}[kind])
    identity = _text(row["id"], "target identity")
    selector = target["selector"]
    if not isinstance(selector, dict):
        raise ProfileError("Invalid campaign target selector")
    if any(key in selector for key in ("inventory_diagnostics", "template_inventory_diagnostics", "storage_diagnostics")):
        raise ProfileError("Campaign target has quarantined inventory diagnostics")
    if kind in ("actor", "container"):
        index = _integer(selector.get("record_index"), "target record index")
        _integer(selector.get("byte_offset"), "target byte offset")
        if kind == "actor":
            offset = _integer(selector.get("embedded_cre_offset"), "target embedded creature offset")
            size = _integer(selector.get("embedded_cre_size"), "target embedded creature size")
            if bool(offset) != bool(size):
                raise ProfileError("Campaign actor target has incomplete creature storage")
        expected = f"{resource}/{kind}/{index}"
    else:
        expected = resource
    if identity != target["id"] or identity != expected:
        raise ProfileError("Campaign target identity disagrees with its record")
    return capacity


def _validate_randomization(value: object, sources: list[dict], targets: list[dict]) -> None:
    metadata = _fields(value, {"sources", "targets", "loss_percent",
                               "unrandomized_percent"} |
                       (set(value) & {"overflow", "row_filters"} if isinstance(value, dict) else set()),
                       "randomization")
    overflow = metadata.get("overflow", False)
    if type(overflow) is not bool:
        raise ProfileError("Invalid randomization overflow flag")
    source_rows = {row["id"]: row for row in sources}
    target_rows = {row["id"]: row for row in targets}
    source_metadata, target_metadata = metadata["sources"], metadata["targets"]
    if (not isinstance(source_metadata, dict) or set(source_metadata) != set(source_rows)
            or not isinstance(target_metadata, dict) or set(target_metadata) != set(target_rows)):
        raise ProfileError("Randomization must name exactly the selected sources and targets")
    for key in ("loss_percent", "unrandomized_percent"):
        _integer(metadata[key], "randomization " + key, high=100)

    def tier(value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[a-z0-9:_-]{1,64}", value) is None:
            raise ProfileError("Invalid randomization tier")
        return value

    if "row_filters" in metadata:
        filters = _fields(metadata["row_filters"], {"sources", "targets"}, "row filters")
        for kind, selected in (("sources", source_rows), ("targets", target_rows)):
            rows = filters[kind]
            if not isinstance(rows, dict) or set(rows) != set(selected):
                raise ProfileError("Row filters must name exactly the selected sources and targets")
            for details in rows.values():
                _fields(details, {"chance", "group"}, "row filter")
                _integer(details["chance"], "row filter chance", high=100)
                group = details["group"]
                if group != "":
                    tier(group)

    target_capacity = Counter()
    eligible_slots = {}
    for identity, row in target_rows.items():
        slots = target_metadata[identity]
        if not isinstance(slots, list) or len(slots) != row["capacity"]:
            raise ProfileError("Randomization target tiers must match target capacity")
        for ordinal, raw_name in enumerate(slots, 1):
            name = tier(raw_name)
            target_capacity[name] += 1
            if row["record"]["kind"] in OVERFLOW_TARGET_KINDS:
                eligible_slots.setdefault(name, set()).add((identity, ordinal))

    source_units, keep_reserve = Counter(), Counter()
    all_exclusions = set()
    for identity, row in source_rows.items():
        details = _fields(source_metadata[identity],
                          {"tier", "loss_exempt", "exclude_targets"} |
                          ({"reward_variants"} if isinstance(source_metadata[identity], dict)
                           and "reward_variants" in source_metadata[identity] else set()),
                          "randomization source")
        if "reward_variants" in details:
            variants = details["reward_variants"]
            if (not isinstance(variants, list) or not 1 <= len(variants) <= MAX_REWARD_UNITS
                    or any(not isinstance(item, str) or
                           re.fullmatch(r"[a-z0-9#_-]{1,8}", item) is None for item in variants)
                    or len(set(variants)) != len(variants)):
                raise ProfileError("Invalid randomization reward variants")
        name = tier(details["tier"])
        if type(details["loss_exempt"]) is not bool:
            raise ProfileError("Invalid randomization loss exemption")
        exclusions = details["exclude_targets"]
        if not isinstance(exclusions, list):
            raise ProfileError("Invalid randomization target exclusions")
        seen_targets, excluded_tiers = set(), Counter()
        for entry in exclusions:
            _fields(entry, {"id", "ordinal"}, "randomization target exclusion")
            target_id = entry["id"]
            if not isinstance(target_id, str) or target_id not in target_rows:
                raise ProfileError("Randomization exclusions need distinct selected targets")
            _integer(entry["ordinal"], "randomization target ordinal",
                     1, target_rows[target_id]["capacity"])
            if (target_id, entry["ordinal"]) in seen_targets:
                raise ProfileError("Randomization exclusions need distinct selected targets")
            seen_targets.add((target_id, entry["ordinal"]))
            all_exclusions.add((target_id, entry["ordinal"]))
            excluded_tiers[target_metadata[target_id][entry["ordinal"] - 1]] += 1
        # Reserve for each possible keep decision independently. Shared exclusions
        # can overestimate this bound; no runtime overflow policy is installed yet.
        for excluded_tier, count in excluded_tiers.items():
            keep_reserve[excluded_tier] += max(0, count - (row["quantity"] if excluded_tier == name else 0))
        source_units[name] += row["quantity"]
    for name, count in source_units.items():
        if overflow:
            if not eligible_slots.get(name):
                raise ProfileError(f"Randomization overflow needs an eligible target in tier {name}")
            if (metadata["unrandomized_percent"] > 0 and
                    not (eligible_slots[name] - all_exclusions)):
                raise ProfileError(f"Randomization overflow needs an unpaired target in tier {name}")
            continue
        if target_capacity[name] < count:
            raise ProfileError(f"Randomization tier capacity is short for {name}: "
                               f"{target_capacity[name]} target slots for {count} source units")
        if (0 < metadata["unrandomized_percent"] < 100
                and target_capacity[name] < count + keep_reserve[name]):
            raise ProfileError(f"Randomization tier {name} needs more target capacity "
                               "to guarantee room after keeping originals")


def _validate_campaign(body: dict) -> dict:
    _fields(body, _FIELDS | ({"shop_pricing"} if isinstance(body, dict) and "shop_pricing" in body else set())
            | ({"randomization"} if isinstance(body, dict) and "randomization" in body else set())
            | (set(body) & {"original_template_id", "original_choices", "original_options", "spell_shield", "cursed_disguises"}
               if isinstance(body, dict) else set()), "artifact")
    if body["schema"] != CAMPAIGN_SCHEMA or body["state"] != STATE:
        raise ProfileError("Unsupported campaign prototype schema or state")
    for key in ("revision", "bound_revision", "world_fingerprint"):
        _hash(body[key], key)
    if not isinstance(body["profile_id"], str) or not PROFILE_ID.fullmatch(body["profile_id"]):
        raise ProfileError("Invalid campaign profile identity")
    if body["runtime_requirements"] != RUNTIME_REQUIREMENTS:
        raise ProfileError("Campaign runtime requirements are missing or unsupported")
    original_keys = {"original_template_id", "original_choices", "original_options"}
    present_original = set(body) & original_keys
    if present_original:
        choices = body.get("original_choices")
        try:
            valid_options = original_options(body.get("original_options"))
        except ValueError as exc:
            raise ProfileError("Invalid original campaign settings") from exc
        if (present_original != original_keys or
                body["original_template_id"] not in {entry["id"] for entry in list_templates()} or
                not isinstance(choices, dict) or set(choices) != {"items", "targets"} or
                any(value not in POOL_CHOICES for value in choices.values()) or
                not isinstance(body["original_options"], dict) or
                valid_options != body["original_options"]):
            raise ProfileError("Invalid original campaign settings")
    shield = body.get("spell_shield")
    if present_original and body["original_options"].get("more_spell_shield"):
        validate_spell_shield_spec(shield)
        randomized = (body["original_options"]["scrolls"] and
                      body["original_choices"]["items"] == "mode1+mode2" and
                      not body["original_template_id"].startswith("bg1-"))
        if shield["source_id"] != (SPELL_SHIELD_SOURCE if randomized else ""):
            raise ProfileError("Spell Shield source does not match original item pool choices")
    elif "spell_shield" in body:
        raise ProfileError("Spell Shield stock requires its original option")
    if present_original and body["original_options"].get("cursed_disguise"):
        validate_cursed_disguise_spec(body.get("cursed_disguises"))
    elif "cursed_disguises" in body:
        raise ProfileError("Cursed disguises require their original option")
    totals = {}
    for key, validator in (("sources", lambda row: _validate_source(row, spell_shield=shield)),
                           ("targets", _validate_target)):
        rows = body[key]
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_REWARD_UNITS:
            raise ProfileError(f"Campaign {key} must contain 1-{MAX_REWARD_UNITS} records")
        total, identities = 0, []
        for row in rows:
            total += validator(row)
            identities.append(row["id"])
        if identities != sorted(set(identities)):
            raise ProfileError(f"Campaign {key} must have unique, sorted identities")
        if total > MAX_REWARD_UNITS:
            raise ProfileError(f"Campaign {key} total exceeds prototype limit {MAX_REWARD_UNITS}")
        totals[key] = total
    if shield and shield["source_id"] and sum(row["id"] == shield["source_id"] for row in body["sources"]) != 1:
        raise ProfileError("Spell Shield option source is missing")
    if not present_original and any(
            row["record"].get("adapter_id") in
            (ORIGINAL_SOURCE_ADAPTER, ORIGINAL_TARGET_ADAPTER)
            for row in body["sources"] + body["targets"]):
        raise ProfileError("Authored records need original campaign settings")
    if "randomization" in body:
        _validate_randomization(body["randomization"], body["sources"], body["targets"])
    overflow = ("randomization" in body and body["randomization"].get("overflow") is True)
    if totals["targets"] < totals["sources"] and not overflow:
        raise ProfileError("Campaign target capacity cannot hold its selected reward records")
    if "shop_pricing" in body:
        selected = sorted({row["record"]["item"] for row in body["sources"]})
        validate_shop_pricing(body["shop_pricing"], selected,
                              {resref: {"price": 0} for resref in selected})
    payload = {key: value for key, value in body.items() if key != "revision"}
    if _digest(payload) != body["revision"]:
        raise ProfileError("Campaign prototype was modified")
    # Validate every nested field for the same scalar representation as Lua.
    _render_lua(body)
    return body


def compile_campaign(profile: dict, catalog: dict, world: dict, plan: dict) -> dict:
    """Compile reviewed physical selections, without choosing assignments."""
    bound = bind_profile(profile, catalog, world, plan)
    if bound["additions"]:
        raise ProfileError("Campaign prototype does not support additional item copies")
    for row in plan["registrations"]:
        if row.get("adapter_id") == VIRTUAL_ADAPTER_ID:
            validate_virtual_registration(row)
        elif row.get("adapter_id") == ORIGINAL_SOURCE_ADAPTER:
            # bind_profile already compared this record with the authored
            # table row, selected scan, and resource fingerprint.
            continue
        else:
            validate_registration(row)
    if any(row["kind"] not in ("physical", "registered") for row in bound["sources"]):
        raise ProfileError("Unsupported bound campaign source")
    if bound["reward_units"] > MAX_REWARD_UNITS:
        raise ProfileError(f"Campaign reward units exceed prototype limit {MAX_REWARD_UNITS}")
    # Reject oversized explicit choices even when an optional target is absent.
    if (sum(row["capacity"] for row in plan["targets"]) > MAX_TARGET_CAPACITY
            or bound["target_capacity"] > MAX_TARGET_CAPACITY):
        raise ProfileError(f"Campaign target capacity exceeds prototype limit {MAX_TARGET_CAPACITY}")
    body = {"schema": CAMPAIGN_SCHEMA, "state": STATE,
            "bound_revision": bound["fingerprint"], "profile_id": bound["profile"]["id"],
            "world_fingerprint": bound["world_fingerprint"], "sources": [], "targets": [],
            "shop_pricing": deepcopy(bound["profile"].get("shop_pricing", DEFAULT_SHOP_PRICING)),
            "runtime_requirements": deepcopy(bound["runtime_requirements"])}
    for selected in bound["sources"]:
        source = selected["source"]
        if selected["kind"] == "registered":
            if source["adapter_id"] == VIRTUAL_ADAPTER_ID:
                validate_virtual_registration(source)
                body["sources"].append({"id": source["id"],
                    "quantity": source["copies"], "record": virtual_campaign_record(source),
                    "resource_sha256": source["resource_sha256"]})
            elif source["adapter_id"] == ORIGINAL_SOURCE_ADAPTER:
                body["sources"].append({"id": source["id"], "quantity": source["copies"],
                    "record": original_source_record(source),
                    "resource_sha256": source["resource_sha256"]})
            else:
                validate_registration(source)
                body["sources"].append({"id": SOURCE_ID, "quantity": 1,
                    "record": campaign_record(source),
                    "resource_sha256": source["resource_sha256"]})
            continue
        body["sources"].append({"id": source["id"],
            "quantity": source["record"]["quantity"] if source["kind"] == "store" else 1,
            "record": deepcopy(source), "resource_sha256": selected["resource_sha256"]})
        if "replacement" in selected:
            body["sources"][-1]["replacement"] = deepcopy(selected["replacement"])
    for selected in bound["targets"]:
        target = selected["target"]
        body["targets"].append({"id": target["id"], "capacity": selected["capacity"],
            "record": deepcopy(target), "resource_sha256": selected["resource_sha256"]})
    for key in ("sources", "targets"):
        body[key].sort(key=lambda row: row["id"])
    if "randomization" in bound["plan"]:
        body["randomization"] = deepcopy(bound["plan"]["randomization"])
    if "original_template_id" in bound["plan"]:
        for key in ("original_template_id", "original_choices", "original_options"):
            body[key] = deepcopy(bound["plan"][key])
    if "spell_shield" in bound["plan"]:
        body["spell_shield"] = deepcopy(bound["plan"]["spell_shield"])
    if "cursed_disguises" in bound["plan"]:
        body["cursed_disguises"] = deepcopy(bound["plan"]["cursed_disguises"])
    body["revision"] = _digest(body)
    return _validate_campaign(body)


def _render_lua(value: object) -> bytes:
    """Render a data-only Lua table, preserving UTF-8 bytes without raw text.

    Fixed-width decimal escapes also prevent a following digit from joining an
    escape. JSON null becomes nil; null-valued object keys are therefore absent
    in Lua. Arrays use explicit one-based keys so nil never shifts later values.
    Floating point values and integers inexact in Lua 5.1 are unsupported.
    """
    def render(node: object, depth: int = 0) -> str:
        if depth > 32:
            raise ProfileError("Campaign data nesting exceeds prototype limit 32")
        if node is None:
            return "nil"
        if type(node) is bool:
            return "true" if node else "false"
        if type(node) is int and abs(node) <= _MAX_LUA_INTEGER:
            return str(node)
        if isinstance(node, str):
            try:
                return '"' + ''.join(f"\\{byte:03d}" for byte in node.encode("utf-8")) + '"'
            except UnicodeEncodeError as exc:
                raise ProfileError("Campaign strings must be valid UTF-8") from exc
        if isinstance(node, dict):
            if any(not isinstance(key, str) for key in node):
                raise ProfileError("Campaign object keys must be strings")
            entries = [(render(key, depth + 1), item) for key, item in sorted(node.items())]
        elif isinstance(node, list):
            entries = [(str(index), item) for index, item in enumerate(node, 1)]
        else:
            raise ProfileError("Campaign data must use strings, exact integers, booleans, nulls or tables")
        indent = "  " * (depth + 1)
        return "{\n" + "".join(f"{indent}[{key}] = {render(item, depth + 1)},\n"
                                for key, item in entries) + "  " * depth + "}"

    return ("return " + render(value) + "\n").encode("ascii")


def _store_path(store: Path, relative: Path) -> Path:
    """Reject linked artifacts and paths redirected outside this revision store."""
    store = store.resolve()
    path = store / relative
    resolved = path.resolve()
    if path.is_symlink() or store not in resolved.parents:
        raise ProfileError("Campaign artifact escapes its store or is a linked file")
    return resolved


def _write_immutable(path: Path, data: bytes) -> None:
    """Publish a complete revision with create-if-absent semantics."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ProfileError("Refusing to publish a linked campaign artifact")
    if path.exists():
        if path.read_bytes() != data:
            raise ProfileError("An immutable campaign revision has conflicting content")
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=".flir-", suffix=".tmp",
                                         dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        # A hard link publishes the fully written bytes without replacing an
        # existing immutable revision if another process won the same race.
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != data:
                raise ProfileError("An immutable campaign revision has conflicting content")
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def write_campaign(profile: dict, catalog: dict, world: dict, plan: dict, store: Path) -> dict:
    """Publish a verified JSON/Lua pair before updating a separate draft pointer."""
    body = compile_campaign(profile, catalog, world, plan)
    store = ensure_output_outside_game(store, catalog)
    revision = body["revision"]
    paths = {"revision_path": _store_path(store, Path("revisions") / (revision + ".json")),
             "lua_path": _store_path(store, Path("revisions") / (revision + ".lua")),
             "pointer_path": _store_path(store, Path("prototype-next.json"))}
    for path in paths.values():
        ensure_output_outside_game(path, catalog)
    _write_immutable(paths["revision_path"], canonical_bytes(body))
    _write_immutable(paths["lua_path"], _render_lua(body))
    write_json(paths["pointer_path"], {"schema": POINTER_SCHEMA, "revision": revision})
    return {"revision": revision, "state": STATE,
            **{key: str(path) for key, path in paths.items()}}


def read_campaign(store: Path, revision: str | None = None) -> dict:
    """Read a hash-validated prototype and require the matching Lua companion."""
    if revision is None:
        pointer = read_json(_store_path(store, Path("prototype-next.json")))
        _fields(pointer, {"schema", "revision"}, "pointer")
        if pointer["schema"] != POINTER_SCHEMA:
            raise ProfileError("Unsupported campaign prototype pointer")
        revision = pointer["revision"]
    _hash(revision, "revision identity")
    body = read_json(_store_path(store, Path("revisions") / (revision + ".json")))
    _validate_campaign(body)
    if body["revision"] != revision:
        raise ProfileError("Campaign revision identity disagrees with its filename")
    lua = _store_path(store, Path("revisions") / (revision + ".lua"))
    if lua.read_bytes() != _render_lua(body):
        raise ProfileError("Campaign JSON and Lua artifacts disagree")
    return body
