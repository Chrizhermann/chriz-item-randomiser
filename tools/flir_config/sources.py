"""Physical resource discovery and explicit, non-playable source binding drafts.

An installed inventory record is not proof of a unique runtime instance. CRE
templates, conditional area actors, dynamic containers and scripted grants are
kept distinguishable. This module never removes or delivers an item.
"""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from pathlib import Path

from .profiles import (HEX256, ProfileError, _digest, ensure_output_outside_game,
                       read_json, validate_catalog, validate_profile, write_json)
from .resources import GameResources, ITEM_SEMANTIC_FIELDS, RESOURCE_TYPES, ScanError
from .world_formats import parse_are, parse_cre, parse_sto

WORLD_SCHEMA = "flir-world-catalog-v1"
PLAN_SCHEMA = "flir-binding-plan-v1"
COVERAGE = "physical-records-and-bcs-dlg-hashes"
KINDS = ("itm", "cre", "are", "sto", "bcs", "dlg")


def world_fingerprint(world: dict) -> str:
    return _digest({key: world[key] for key in
                    ("schema", "catalog_fingerprint", "coverage", "resources", "sources", "targets")})


def _source(resource: str, owner: str, kind: str, row: dict,
            concerns: list[str]) -> dict:
    return {"id": f"{owner}/item/{row['record_index']}",
            "item": row["resref"], "resource": resource, "owner": owner,
            "kind": kind, "record": deepcopy(row), "concerns": sorted(set(concerns))}


def _cre_sources(resource: str, owner: str, cre: dict, kind: str) -> list[dict]:
    result = []
    for item in cre["items"]:
        concerns = ["template-instance-scope"] if kind == "cre-template" else ["actor-availability"]
        if not item["slots"]:
            concerns.append("unreferenced-inventory-record")
        if len(item["slots"]) > 1:
            concerns.append("aliased-inventory-record")
        if item["flags"] & 8:
            concerns.append("undroppable-instance")
        result.append(_source(resource, owner, kind, item, concerns))
    return result


def scan_world(catalog: dict) -> dict:
    """Scan the explicitly named installation; require matching effective ITMs.

    BCS/DLG bytes are fingerprinted, not decompiled or interpreted. Other grant
    mechanisms (Lua, effects, dynamic tables) are outside this coverage contract.
    Names retain the selected item catalogue's localization snapshot.
    """
    item_index = validate_catalog(catalog)
    world = {"schema": WORLD_SCHEMA, "game_root": catalog["game_root"],
             "catalog_fingerprint": catalog["fingerprint"], "coverage": COVERAGE,
             "resources": [], "sources": [], "targets": []}
    with GameResources(Path(catalog["game_root"]), {key: RESOURCE_TYPES[key] for key in KINDS}) as reader:
        if reader.names("itm") != sorted(item_index):
            raise ScanError("Installed items changed; scan the item catalogue again before binding sources.")
        for kind in KINDS:
            for resref in reader.names(kind):
                data = reader.read(kind, resref)
                digest = hashlib.sha256(data).hexdigest()
                resource = f"{kind}:{resref}"
                if kind == "itm" and digest != item_index[resref]["sha256"]:
                    raise ScanError("Installed item bytes changed; scan the item catalogue again before binding sources.")
                world["resources"].append({"id": resource, "kind": kind,
                                           "resref": resref, "sha256": digest, "byte_length": len(data)})
                if kind == "cre":
                    cre = parse_cre(data)
                    world["sources"].extend(_cre_sources(resource, resource, cre, "cre-template"))
                    world["targets"].append({"id": resource, "resource": resource,
                        "kind": "cre-template", "selector": {"script_name": cre["script_name"]},
                        "concerns": ["template-instance-scope", "lootability-unverified"]})
                elif kind == "are":
                    area = parse_are(data)
                    containers = {row["record_index"]: row for row in area["containers"]}
                    for container in area["containers"]:
                        target_id = f"{resource}/container/{container['record_index']}"
                        concerns = ["container-access-unverified"]
                        if container["dynamic_item_table"]:
                            concerns.append("dynamic-container-items")
                        world["targets"].append({"id": target_id, "resource": resource,
                            "kind": "container", "selector": {key: deepcopy(value) for key, value in container.items() if key != "items"}, "concerns": concerns})
                    for item in area["items"]:
                        owners = item.get("container_indexes", [])
                        if not owners and item.get("container_index") is not None:
                            owners = [item["container_index"]]
                        concerns = []
                        if len(owners) != 1:
                            concerns.append("shared-container-record" if owners else "unowned-item-record")
                            owner = f"{resource}/unresolved"
                        else:
                            owner = f"{resource}/container/{owners[0]}"
                            if containers[owners[0]]["dynamic_item_table"]:
                                concerns.append("dynamic-container-items")
                        if item["flags"] & 8:
                            concerns.append("undroppable-instance")
                        world["sources"].append(_source(resource, owner, "container", item, concerns))
                    for actor in area["actors"]:
                        target_id = f"{resource}/actor/{actor['record_index']}"
                        selector = {key: deepcopy(value) for key, value in actor.items() if key != "embedded_cre"}
                        concerns = ["actor-availability", "lootability-unverified"]
                        if actor["embedded_cre"] is not None:
                            world["sources"].extend(_cre_sources(resource, target_id, actor["embedded_cre"], "embedded-cre"))
                            selector["script_name"] = actor["embedded_cre"]["script_name"]
                        else:
                            selector["template_resource"] = f"cre:{actor['cre_resref']}" if actor["cre_resref"] else None
                            concerns.append("external-cre-template")
                        world["targets"].append({"id": target_id, "resource": resource,
                            "kind": "actor", "selector": selector, "concerns": concerns})
                elif kind == "sto":
                    store = parse_sto(data)
                    world["targets"].append({"id": resource, "resource": resource,
                        "kind": "store", "selector": {key: value for key, value in store.items() if key != "items"},
                        "concerns": ["store-access-unverified"]})
                    for item in store["items"]:
                        concerns = []
                        if item["infinite_supply"]:
                            concerns.append("infinite-stock")
                        elif item["quantity"] == 0:
                            concerns.append("empty-stock")
                        if item["infinite_supply_flag"] not in (0, 1):
                            concerns.append("unknown-stock-supply")
                        world["sources"].append(_source(resource, resource, "store", item, concerns))
        resource_ids = {row["id"] for row in world["resources"]}
        for row in world["sources"]:
            if row["item"] not in item_index:
                row["concerns"].append("missing-item-definition")
            row["concerns"] = sorted(set(row["concerns"]))
        for row in world["targets"]:
            reference = row["selector"].get("template_resource")
            if row["kind"] == "actor" and "external-cre-template" in row["concerns"] and reference not in resource_ids:
                row["concerns"].append("missing-creature-template")
            row["concerns"] = sorted(set(row["concerns"]))
        for field in ("resources", "sources", "targets"):
            world[field].sort(key=lambda row: row["id"])
        world["fingerprint"] = world_fingerprint(world)
    validate_world(world, catalog)
    return world


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise ProfileError(f"Invalid {label}")
    return value


def _integer(value: object, label: str, low: int = 0, high: int = 0xFFFFFFFF) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ProfileError(f"Invalid {label}")
    return value


def _fields(value: object, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise ProfileError(f"Unsupported {label} fields")
    return value


def _rows(value: object, label: str) -> list:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ProfileError(f"Invalid {label} list")
    return value


def _indexed(rows: list[dict], label: str) -> dict:
    index = {}
    for row in rows:
        identity = _text(row.get("id"), f"{label} identity")
        if identity in index:
            raise ProfileError(f"Duplicate {label} identity: {identity}")
        index[identity] = row
    if list(index) != sorted(index):
        raise ProfileError(f"{label} records must be sorted")
    return index


def _resref(value: object) -> str:
    text = _text(value, "resource name")
    if (not 1 <= len(text) <= 8 or text != text.lower() or
            any(ord(c) > 126 or c in '/\\:*?"<>| ' for c in text)):
        raise ProfileError("Invalid resource name")
    return text


def _indexes(value: object, label: str, high: int = 0xFFFFFFFF) -> list:
    if not isinstance(value, list):
        raise ProfileError(f"Invalid {label} indexes")
    for number in value:
        _integer(number, label, high=high)
    if value != sorted(set(value)):
        raise ProfileError(f"Ambiguous {label} indexes")
    return value


def _source_concerns(row: dict, targets: dict, item_ids: set) -> list[str]:
    record = row["record"]
    concerns = []
    if row["kind"] in ("cre-template", "embedded-cre"):
        concerns.append("template-instance-scope" if row["kind"] == "cre-template" else "actor-availability")
        if not record["slots"]:
            concerns.append("unreferenced-inventory-record")
        elif len(record["slots"]) > 1:
            concerns.append("aliased-inventory-record")
    elif row["kind"] == "container":
        owners = record["container_indexes"]
        if len(owners) != 1:
            concerns.append("shared-container-record" if owners else "unowned-item-record")
        elif targets[row["owner"]]["selector"]["dynamic_item_table"]:
            concerns.append("dynamic-container-items")
    else:
        if record["infinite_supply_flag"]:
            concerns.append("infinite-stock")
        elif not record["quantity"]:
            concerns.append("empty-stock")
        if record["infinite_supply_flag"] not in (0, 1):
            concerns.append("unknown-stock-supply")
    if row["kind"] != "store" and record["flags"] & 8:
        concerns.append("undroppable-instance")
    if row["item"] not in item_ids:
        concerns.append("missing-item-definition")
    return sorted(concerns)


def validate_world(world: dict, catalog: dict) -> dict:
    item_index = validate_catalog(catalog)
    _fields(world, {"schema", "game_root", "catalog_fingerprint", "coverage", "resources",
                    "sources", "targets", "fingerprint"}, "world catalogue")
    if world["schema"] != WORLD_SCHEMA or world["coverage"] != COVERAGE:
        raise ProfileError("Unsupported world catalogue coverage")
    if world["catalog_fingerprint"] != catalog["fingerprint"]:
        raise ProfileError("World catalogue belongs to another item scan")
    if world["game_root"] != catalog["game_root"]:
        raise ProfileError("World and item catalogues name different installations")
    resources = _indexed(_rows(world["resources"], "resource"), "resource")
    sources = _indexed(_rows(world["sources"], "source"), "source")
    targets = _indexed(_rows(world["targets"], "target"), "target")
    for row in resources.values():
        _fields(row, {"id", "kind", "resref", "sha256", "byte_length"}, "resource")
        _resref(row["resref"])
        _integer(row["byte_length"], "resource byte length")
        if row["kind"] not in KINDS or row["id"] != f"{row['kind']}:{row['resref']}":
            raise ProfileError("Invalid resource identity")
        if not isinstance(row["sha256"], str) or not HEX256.fullmatch(row["sha256"]):
            raise ProfileError("Invalid resource fingerprint")
    installed_items = {row["resref"]: row["sha256"] for row in resources.values() if row["kind"] == "itm"}
    if installed_items != {key: row["sha256"] for key, row in item_index.items()}:
        raise ProfileError("World catalogue item resources disagree with the item catalogue")
    for row in list(sources.values()) + list(targets.values()):
        if not isinstance(row.get("resource"), str) or row["resource"] not in resources:
            raise ProfileError("A world record refers to an unknown resource")
        concerns = row.get("concerns")
        if not isinstance(concerns, list) or any(not isinstance(x, str) for x in concerns) or concerns != sorted(set(concerns)):
            raise ProfileError("Invalid world record concerns")
    for row in targets.values():
        _fields(row, {"id", "resource", "kind", "selector", "concerns"}, "target")
        if row["kind"] not in ("cre-template", "actor", "container", "store") or not isinstance(row["selector"], dict):
            raise ProfileError("Invalid target selector")
        resource_kind = resources[row["resource"]]["kind"]
        expected_kind = {"cre-template": "cre", "actor": "are", "container": "are", "store": "sto"}[row["kind"]]
        if resource_kind != expected_kind:
            raise ProfileError("Target kind disagrees with its resource")
        selector = row["selector"]
        if row["kind"] in ("actor", "container"):
            index = _integer(selector.get("record_index"), "target record index")
            _integer(selector.get("byte_offset"), "target byte offset")
            if row["id"] != f"{row['resource']}/{row['kind']}/{index}":
                raise ProfileError("Invalid target record identity")
            _integer(selector.get("flags"), "target flags")
            size = 0x110 if row["kind"] == "actor" else 0xC0
            if selector["byte_offset"] + size > resources[row["resource"]]["byte_length"]:
                raise ProfileError("Target lies outside its resource")
            if row["kind"] == "container":
                _indexes(selector.get("item_indexes"), "container item")
                marker = selector.get("dynamic_item_table")
                if marker is not None and not isinstance(marker, str):
                    raise ProfileError("Invalid dynamic container marker")
                if "dynamic_item_table" not in selector:
                    raise ProfileError("Missing dynamic container marker")
            else:
                _integer(selector.get("embedded_cre_offset"), "embedded creature offset")
                _integer(selector.get("embedded_cre_size"), "embedded creature size")
                template = selector.get("template_resource")
                if not selector["embedded_cre_size"]:
                    if not isinstance(template, str) or not template.startswith("cre:"):
                        raise ProfileError("Invalid external creature template")
                    _resref(template[4:])
                    if template != f"cre:{selector.get('cre_resref')}":
                        raise ProfileError("External creature reference mismatch")
                elif selector["embedded_cre_offset"] + selector["embedded_cre_size"] > resources[row["resource"]]["byte_length"]:
                    raise ProfileError("Embedded creature lies outside its area")
        elif row["id"] != row["resource"]:
            raise ProfileError("Invalid template target identity")
    physical_spans = {}
    inventory_slots = set()
    container_records = {}
    item_ids = set(item_index)
    for row in sources.values():
        _fields(row, {"id", "item", "resource", "owner", "kind", "record", "concerns"}, "source")
        if row["kind"] not in ("cre-template", "embedded-cre", "container", "store"):
            raise ProfileError("Invalid source kind")
        record = row["record"]
        if not isinstance(record, dict) or record.get("resref") != row["item"]:
            raise ProfileError("Invalid source item record")
        _resref(row["item"])
        for key in ("record_index", "byte_offset", "expiration", "flags"):
            _integer(record.get(key), f"source {key}")
        record_end = record["byte_offset"] + (28 if row["kind"] == "store" else 20)
        if record_end > resources[row["resource"]]["byte_length"]:
            raise ProfileError("Source lies outside its resource")
        physical_spans.setdefault(row["resource"], []).append((record["byte_offset"], record_end))
        owner = _text(row["owner"], "source owner")
        if row["id"] != f"{owner}/item/{record['record_index']}":
            raise ProfileError("Invalid source selector")
        if resources[row["resource"]]["kind"] != {"cre-template": "cre", "embedded-cre": "are", "container": "are", "store": "sto"}[row["kind"]]:
            raise ProfileError("Source kind disagrees with its resource")
        if row["kind"] in ("cre-template", "store") and owner != row["resource"]:
            raise ProfileError("Invalid source template owner")
        charges = record.get("charges")
        if not isinstance(charges, list) or len(charges) != 3:
            raise ProfileError("Source charges must contain three values")
        for charge in charges:
            _integer(charge, "source charge", high=0xFFFF)
        if row["kind"] in ("cre-template", "embedded-cre"):
            _indexes(record.get("slots"), "inventory slot", 37)
            for slot in record["slots"]:
                key = (owner, slot)
                if key in inventory_slots:
                    raise ProfileError("Distinct inventory records claim the same creature slot")
                inventory_slots.add(key)
            if row["kind"] == "embedded-cre":
                target = targets.get(owner)
                if (target is None or target["kind"] != "actor" or target["resource"] != row["resource"] or
                        not target["selector"]["embedded_cre_size"]):
                    raise ProfileError("Embedded source has no matching embedded actor")
                selector = target["selector"]
                if record["byte_offset"] < selector["embedded_cre_offset"] or record_end > selector["embedded_cre_offset"] + selector["embedded_cre_size"]:
                    raise ProfileError("Embedded inventory lies outside its actor")
        elif row["kind"] == "container":
            container_records[(row["resource"], record["record_index"])] = record
            owners = _indexes(record.get("container_indexes"), "container owner")
            if record.get("container_index") != (owners[0] if len(owners) == 1 else None):
                raise ProfileError("Inconsistent container ownership")
            expected_owner = f"{row['resource']}/container/{owners[0]}" if len(owners) == 1 else f"{row['resource']}/unresolved"
            if owner != expected_owner:
                raise ProfileError("Invalid container source owner")
            for number in owners:
                target = targets.get(f"{row['resource']}/container/{number}")
                if target is None or record["record_index"] not in target["selector"]["item_indexes"]:
                    raise ProfileError("Container source disagrees with its owner range")
        if row["kind"] == "store":
            _integer(record.get("quantity"), "store quantity")
            _integer(record.get("infinite_supply_flag"), "store supply flag")
            if type(record.get("infinite_supply")) is not bool or record["infinite_supply"] != bool(record["infinite_supply_flag"]):
                raise ProfileError("Invalid store infinite supply marker")
        if row["concerns"] != _source_concerns(row, targets, item_ids):
            raise ProfileError("Source concerns disagree with its inventory data")
    for spans in physical_spans.values():
        end = 0
        for start, next_end in sorted(spans):
            if start < end:
                raise ProfileError("Physical source records overlap or are duplicated")
            end = next_end
    for target in targets.values():
        if target["kind"] != "container":
            continue
        for index in target["selector"]["item_indexes"]:
            record = container_records.get((target["resource"], index))
            if record is None or target["selector"]["record_index"] not in record["container_indexes"]:
                raise ProfileError("Container owner range disagrees with its source records")
    if not isinstance(world["fingerprint"], str) or world["fingerprint"] != world_fingerprint(world):
        raise ProfileError("World catalogue content changed; scan again")
    return {"resources": resources, "sources": sources, "targets": targets}


def _page(rows: list[dict], offset: int, limit: int) -> dict:
    _integer(offset, "offset")
    _integer(limit, "limit", 1, 100)
    return {"total": len(rows), "offset": offset, "records": deepcopy(rows[offset:offset + limit]),
            "next_offset": offset + limit if offset + limit < len(rows) else None}


def inspect_sources(world: dict, catalog: dict, item: str, *, offset: int = 0, limit: int = 20) -> dict:
    validate_world(world, catalog)
    item = item.lower()
    if item not in {row["resref"] for row in catalog["items"]}:
        raise ProfileError("Unknown item identity")
    rows = [row for row in world["sources"] if row["item"] == item]
    result = _page(rows, offset, limit)
    result["discovery"] = "absent" if not rows else "single-record" if len(rows) == 1 else "multiple-records"
    result["runtime_availability"] = "unverified"
    return result


def search_targets(world: dict, catalog: dict, query: str, *, kind: str | None = None,
                   offset: int = 0, limit: int = 20) -> dict:
    validate_world(world, catalog)
    terms = query.strip().casefold().split()
    if not terms and kind is None:
        raise ProfileError("Enter a target search term or choose a target kind")
    if kind is not None and kind not in ("cre-template", "actor", "container", "store"):
        raise ProfileError("Unknown target kind")
    rows = []
    for row in world["targets"]:
        if kind is not None and row["kind"] != kind:
            continue
        haystack = " ".join(str(row["selector"].get(key, "")) for key in ("name", "script_name", "cre_resref"))
        haystack = (row["id"] + " " + haystack).casefold()
        if all(term in haystack for term in terms):
            rows.append(row)
    return _page(rows, offset, limit)


def new_binding_plan(profile: dict, catalog: dict, world: dict) -> dict:
    validate_profile(profile, catalog)
    validate_world(world, catalog)
    return {"schema": PLAN_SCHEMA, "catalog_fingerprint": catalog["fingerprint"],
            "world_fingerprint": world["fingerprint"], "sources": [], "registrations": [], "targets": []}


UNUSABLE_SOURCE = {"unreferenced-inventory-record", "aliased-inventory-record", "unowned-item-record",
                   "shared-container-record", "infinite-stock", "empty-stock", "unknown-stock-supply",
                   "missing-item-definition"}


def bind_profile(profile: dict, catalog: dict, world: dict, plan: dict) -> dict:
    """Freeze explicit choices with evidence, never infer safe runtime behavior."""
    frozen = validate_profile(profile, catalog)
    indexes = validate_world(world, catalog)
    _fields(plan, {"schema", "catalog_fingerprint", "world_fingerprint", "sources", "registrations", "targets"}, "binding plan")
    if plan["schema"] != PLAN_SCHEMA or plan["catalog_fingerprint"] != catalog["fingerprint"] or plan["world_fingerprint"] != world["fingerprint"]:
        raise ProfileError("Binding plan is stale or unsupported; review against the current scans")
    registrations = {}
    grant_sites = set()
    for registration in _rows(plan["registrations"], "registration"):
        _fields(registration, {"id", "item", "resource", "resource_sha256", "adapter_id", "selector", "copies", "optional"}, "registration")
        identity = _text(registration["id"], "registration identity")
        if identity in registrations:
            raise ProfileError("Duplicate registered source identity")
        for key in ("item", "resource", "adapter_id", "selector"):
            _text(registration[key], f"registration {key}")
        if not registration["resource"].startswith(("bcs:", "dlg:")):
            raise ProfileError("Registered sources must identify a BCS or DLG resource")
        _resref(registration["resource"][4:])
        if not isinstance(registration["resource_sha256"], str) or not HEX256.fullmatch(registration["resource_sha256"]):
            raise ProfileError("Invalid registered resource fingerprint")
        _integer(registration["copies"], "registered copies", 1)
        if type(registration["optional"]) is not bool:
            raise ProfileError("Invalid optional registration flag")
        site = (registration["resource"], registration["selector"])
        if site in grant_sites:
            raise ProfileError("A script grant site cannot be registered twice")
        grant_sites.add(site)
        resource = indexes["resources"].get(registration["resource"])
        if resource is None and not registration["optional"]:
            raise ProfileError("Required registered source resource is absent")
        if resource is not None and resource["sha256"] != registration["resource_sha256"]:
            raise ProfileError("Registered source resource changed; its adapter needs review")
        registrations[identity] = registration
    selected_sources, additions, skipped, selected_ids, used_registrations, mapped_items = [], [], [], set(), set(), set()
    reward_units = 0
    for choice in _rows(plan["sources"], "source choice"):
        item = _text(choice.get("item"), "chosen item")
        if item not in frozen["items"] or item in mapped_items:
            raise ProfileError("Each selected item needs exactly one source choice")
        mapped_items.add(item)
        if choice.get("policy") == "add":
            _fields(choice, {"item", "policy", "copies"}, "addition")
            reward_units += _integer(choice["copies"], "additional copies", 1)
            additions.append(deepcopy(choice))
            continue
        _fields(choice, {"item", "policy", "source_ids", "registration_ids"}, "relocation")
        if choice["policy"] != "relocate":
            raise ProfileError("Unknown source policy")
        for field in ("source_ids", "registration_ids"):
            values = choice[field]
            if not isinstance(values, list) or any(not isinstance(x, str) for x in values):
                raise ProfileError("Invalid source selection list")
            if len(values) != len(set(values)):
                raise ProfileError("A source cannot be selected twice")
        if not choice["source_ids"] and not choice["registration_ids"]:
            raise ProfileError("Relocation requires an explicit physical or registered source")
        for identity in choice["source_ids"]:
            row = indexes["sources"].get(identity)
            if row is None or row["item"] != item or identity in selected_ids:
                raise ProfileError("Selected physical source is missing, mismatched or duplicated")
            if UNUSABLE_SOURCE.intersection(row["concerns"]):
                raise ProfileError("Selected physical source is not a finite, unambiguous inventory record")
            selected_ids.add(identity)
            selected_sources.append({"kind": "physical", "source": deepcopy(row),
                                     "resource_sha256": indexes["resources"][row["resource"]]["sha256"]})
            reward_units += row["record"]["quantity"] if row["kind"] == "store" else 1
        for identity in choice["registration_ids"]:
            row = registrations.get(identity)
            if row is None or row["item"] != item or identity in used_registrations:
                raise ProfileError("Registered source is missing, mismatched or duplicated")
            used_registrations.add(identity)
            if row["resource"] not in indexes["resources"]:
                skipped.append({"kind": "source", "id": identity, "reason": "optional-resource-absent"})
                continue
            selected_sources.append({"kind": "registered", "source": deepcopy(row),
                                     "availability": "unverified"})
            reward_units += row["copies"]
    if mapped_items != set(frozen["items"]) or not mapped_items:
        raise ProfileError("Choose explicit sources or additions for every selected item")
    if used_registrations != set(registrations):
        raise ProfileError("Unused source registrations must be removed from this binding plan")
    targets, target_ids = [], set()
    capacity = 0
    for choice in _rows(plan["targets"], "target choice"):
        _fields(choice, {"id", "capacity", "optional"}, "target choice")
        identity = _text(choice["id"], "target identity")
        parts = identity.split("/")
        base = parts[0].split(":")
        if len(base) != 2 or base[0] not in ("cre", "are", "sto"):
            raise ProfileError("Invalid target identity")
        _resref(base[1])
        if base[0] == "are":
            if len(parts) != 3 or parts[1] not in ("actor", "container") or not re.fullmatch(r"0|[1-9][0-9]*", parts[2]):
                raise ProfileError("Invalid area target identity")
        elif len(parts) != 1:
            raise ProfileError("Invalid template target identity")
        if identity in target_ids:
            raise ProfileError("A target cannot be selected twice")
        target_ids.add(identity)
        _integer(choice["capacity"], "target capacity", 1)
        if type(choice["optional"]) is not bool:
            raise ProfileError("Invalid optional target flag")
        target = indexes["targets"].get(identity)
        if target is None:
            if not choice["optional"]:
                raise ProfileError("Required target is absent")
            skipped.append({"kind": "target", "id": identity, "reason": "optional-resource-absent"})
            continue
        if (target["kind"] == "actor" and not target["selector"]["embedded_cre_size"] and
                target["selector"]["template_resource"] not in indexes["resources"]):
            raise ProfileError("Target refers to a missing creature template")
        targets.append({"target": deepcopy(target), "capacity": choice["capacity"],
                        "resource_sha256": indexes["resources"][target["resource"]]["sha256"]})
        capacity += choice["capacity"]
    if reward_units == 0:
        raise ProfileError("No available reward records remain after optional source resolution")
    if capacity < reward_units:
        raise ProfileError(f"Target capacity {capacity} cannot hold {reward_units} selected reward records")
    result = {"schema": "flir-bound-draft-v1", "state": "draft-needs-runtime-adapter",
              "profile": frozen, "catalog_fingerprint": catalog["fingerprint"],
              "world_fingerprint": world["fingerprint"], "plan": deepcopy(plan),
              "items": [{key: row[key] for key in ITEM_SEMANTIC_FIELDS}
                        for row in catalog["items"] if row["resref"] in mapped_items],
              "sources": selected_sources, "additions": additions, "targets": targets,
              "skipped": skipped, "reward_units": reward_units, "target_capacity": capacity,
              "runtime_requirements": ["campaign-revision-persistence", "exact-source-suppression",
                  "new-game-assignment", "target-instance-resolution", "nonoverlapping-runtime-selectors",
                  "verified-lootable-delivery"]}
    result["fingerprint"] = _digest(result)
    return result


def prepare_bound_profile(profile: dict, catalog: dict, world: dict, plan: dict, store: Path) -> dict:
    body = bind_profile(profile, catalog, world, plan)
    store = ensure_output_outside_game(store, catalog)
    revision = body["fingerprint"]
    revision_path = ensure_output_outside_game(store / "revisions" / (revision + ".json"), catalog)
    pointer_path = ensure_output_outside_game(store / "next-bound-draft.json", catalog)
    write_json(revision_path, body, immutable=True)
    write_json(pointer_path, {"schema": "flir-next-bound-draft-v1", "revision": revision})
    return {"revision": revision, "state": body["state"], "reward_units": body["reward_units"],
            "target_capacity": body["target_capacity"], "skipped": body["skipped"],
            "revision_path": str(revision_path), "pointer_path": str(pointer_path)}


def read_bound_profile(store: Path, revision: str | None = None) -> dict:
    if revision is None:
        pointer = read_json(store / "next-bound-draft.json")
        _fields(pointer, {"schema", "revision"}, "bound draft pointer")
        if pointer["schema"] != "flir-next-bound-draft-v1":
            raise ProfileError("Unsupported bound draft pointer")
        revision = pointer["revision"]
    if not isinstance(revision, str) or not HEX256.fullmatch(revision):
        raise ProfileError("Invalid bound draft identity")
    body = read_json(store / "revisions" / (revision + ".json"))
    fingerprint = body.pop("fingerprint", None)
    if (body.get("schema") != "flir-bound-draft-v1" or
            body.get("state") != "draft-needs-runtime-adapter" or
            fingerprint != revision or _digest(body) != revision):
        raise ProfileError("Bound draft is unsupported or was modified")
    body["fingerprint"] = fingerprint
    return body
