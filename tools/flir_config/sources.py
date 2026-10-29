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
from .world_formats import _reference, parse_are, parse_cre, parse_sto
from .virtual_sources import ADAPTER_ID as VIRTUAL_ADAPTER_ID
from .virtual_sources import validate_registration as validate_virtual_registration
from .original_options import spell_shield_randomized, spell_shield_spec
from .cursed_disguises import cursed_disguise_spec, validate_cursed_disguise_spec
from .duplicates import expected_copies, holdings
from .game_names import eet_names
from .original_catalog import mode2_route_source
from .platform import preset_game
from .original_registrations import (SOURCE_ADAPTER as ORIGINAL_SOURCE_ADAPTER,
                                     TARGET_ADAPTER as ORIGINAL_TARGET_ADAPTER,
                                     SHIPPED_REPLACEMENT,
                                     table_rows as original_table_rows,
                                     target_record as original_target_record,
                                     shipped_replacement_hash,
                                     special_rows as special_target_rows,
                                     validate_source_registration as validate_original_source,
                                     validate_target_registration as validate_original_target)

WORLD_SCHEMA = "flir-world-catalog-v1"
DIAGNOSTIC_WORLD_SCHEMA = "flir-world-catalog-v2"
DANGLING_INVENTORY = "dangling-inventory-reference"
INVALID_INVENTORY = "invalid-inventory-reference"
INVALID_ITEM = "invalid-item-reference"
UNRESOLVED_STORAGE = "unresolved-creature-storage"
PLAN_SCHEMA = "flir-binding-plan-v1"
# A binding plan's fields; binding and the editor's draft check both use them.
PLAN_FIELDS = frozenset({"schema", "catalog_fingerprint", "world_fingerprint", "sources", "registrations",
                         "targets"})
# Creature replacements, randomisation rules, an original preset's settings and
# the record of what its fit made (original_configuration.FIT_RECORD), the
# metadata of its options (Spell Shield stock, cursed disguises) and the second
# copies of its moved rewards (duplicates.py).
PLAN_OPTIONAL_FIELDS = frozenset({"replacements", "randomization", "original_template_id", "original_choices",
                                  "original_options", "original_fit_sha256", "target_registrations",
                                  "spell_shield", "cursed_disguises", "duplicates"})
COVERAGE = "physical-records-and-bcs-dlg-hashes"
KINDS = ("itm", "cre", "are", "sto", "bcs", "dlg", "spl")
# The kinds whose records the scan reads. One it cannot read is left out.
READ_KINDS = ("cre", "are", "sto")
UNREADABLE_TEMPLATE = "unreadable-creature-template"


def world_fingerprint(world: dict) -> str:
    keys = ["schema", "catalog_fingerprint", "coverage", "resources", "sources", "targets"]
    # Only a scan that left files out lists them; other worlds keep their fingerprint.
    if "unreadable" in world:
        keys.append("unreadable")
    return _digest({key: world[key] for key in keys})


def unreadable_notes(world: dict) -> list[str]:
    """Tell the player which game files the scan could not read and left out."""
    rows = world.get("unreadable", [])
    if not rows:
        return []
    names = []
    for row in rows:
        kind, name = row["id"].split(":", 1)
        names.append(f"{name.upper()}.{kind.upper()} ({row['reason'].rstrip('.')})")
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    one = len(names) == 1
    return [f"{listed} cannot be read, so Chriz Item Randomiser leaves {'it' if one else 'them'} out: "
            f"{'its' if one else 'their'} items stay as the game has them and no reward goes there"]


def _source(resource: str, owner: str, kind: str, row: dict,
            concerns: list[str]) -> dict:
    return {"id": f"{owner}/item/{row['record_index']}",
            "item": row["resref"], "resource": resource, "owner": owner,
            "kind": kind, "record": deepcopy(row), "concerns": sorted(set(concerns))}


def _cre_sources(resource: str, owner: str, cre: dict, kind: str) -> list[dict]:
    result = []
    for item in cre["items"]:
        item = deepcopy(item)
        concerns = ["template-instance-scope"] if kind == "cre-template" else ["actor-availability"]
        if "inventory_diagnostics" in cre:
            item["inventory_diagnostics"] = deepcopy(cre["inventory_diagnostics"])
            concerns.extend(_inventory_concerns(cre["inventory_diagnostics"]))
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

    A CRE, ARE or STO whose structure cannot be read (another mod's damaged or
    unusual file) is left out instead of stopping the scan: it keeps its
    resource row, gives no sources or targets, and is listed in "unreadable"
    with the reason. Nothing is placed in it or taken from it.

    The dialogues, scripts and spells CIR's Apply wrote into override are read
    as the game had them before (applied_files.py): applying a configuration
    does not change the scan it was made with.
    """
    item_index = validate_catalog(catalog)
    world = {"schema": WORLD_SCHEMA, "game_root": catalog["game_root"],
             "catalog_fingerprint": catalog["fingerprint"], "coverage": COVERAGE,
             "resources": [], "sources": [], "targets": []}
    unreadable = {}
    with GameResources(Path(catalog["game_root"]), {key: RESOURCE_TYPES[key] for key in KINDS},
                       before_apply=True) as reader:
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
                    try:
                        cre = parse_cre(data, allow_dangling_slots=True, allow_invalid_items=True)
                    except ScanError as exc:
                        unreadable[resource] = str(exc)
                        continue
                    world["sources"].extend(_cre_sources(resource, resource, cre, "cre-template"))
                    selector = {"script_name": cre["script_name"]}
                    if "inventory_diagnostics" in cre:
                        selector["inventory_diagnostics"] = deepcopy(cre["inventory_diagnostics"])
                    world["targets"].append({"id": resource, "resource": resource,
                        "kind": "cre-template", "selector": selector,
                        "concerns": ["template-instance-scope", "lootability-unverified"]})
                elif kind == "are":
                    try:
                        area = parse_are(data, allow_dangling_slots=True, allow_invalid_items=True,
                                         allow_incomplete_actors=True)
                    except ScanError as exc:
                        unreadable[resource] = str(exc)
                        continue
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
                        if "storage_diagnostics" in actor:
                            concerns.append(UNRESOLVED_STORAGE)
                            world["schema"] = DIAGNOSTIC_WORLD_SCHEMA
                        elif actor["embedded_cre"] is not None:
                            world["sources"].extend(_cre_sources(resource, target_id, actor["embedded_cre"], "embedded-cre"))
                            selector["script_name"] = actor["embedded_cre"]["script_name"]
                            if "inventory_diagnostics" in actor["embedded_cre"]:
                                selector["inventory_diagnostics"] = deepcopy(actor["embedded_cre"]["inventory_diagnostics"])
                        else:
                            selector["template_resource"] = f"cre:{actor['cre_resref']}" if actor["cre_resref"] else None
                            concerns.append("external-cre-template")
                        world["targets"].append({"id": target_id, "resource": resource,
                            "kind": "actor", "selector": selector, "concerns": concerns})
                elif kind == "sto":
                    try:
                        store = parse_sto(data)
                    except ScanError as exc:
                        unreadable[resource] = str(exc)
                        continue
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
            if "raw_resref_hex" in row["record"]:
                row["concerns"].append(INVALID_ITEM)
            elif row["item"] == "":
                row["concerns"].append("empty-item-reference")
            elif row["item"] not in item_index:
                row["concerns"].append("missing-item-definition")
            row["concerns"] = sorted(set(row["concerns"]))
        target_index = {row["id"]: row for row in world["targets"]}
        for row in world["targets"]:
            reference = row["selector"].get("template_resource")
            template = target_index.get(reference)
            if template and "inventory_diagnostics" in template["selector"]:
                row["selector"]["template_inventory_diagnostics"] = deepcopy(template["selector"]["inventory_diagnostics"])
            if any(key in row["selector"] for key in ("inventory_diagnostics", "template_inventory_diagnostics")):
                diag = row["selector"].get("inventory_diagnostics", row["selector"].get("template_inventory_diagnostics"))
                row["concerns"].extend(_inventory_concerns(diag))
                world["schema"] = DIAGNOSTIC_WORLD_SCHEMA
            if row["kind"] == "actor" and "external-cre-template" in row["concerns"]:
                if reference not in resource_ids:
                    row["concerns"].append("missing-creature-template")
                elif reference in unreadable:
                    row["concerns"].append(UNREADABLE_TEMPLATE)
            row["concerns"] = sorted(set(row["concerns"]))
        for field in ("resources", "sources", "targets"):
            world[field].sort(key=lambda row: row["id"])
        if unreadable:
            world["unreadable"] = [{"id": resource, "reason": reason}
                                   for resource, reason in sorted(unreadable.items())]
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
            text != text.strip(" ") or text in (".", "..") or
            any(ord(c) > 126 or c in '/\\:*?"<>|' for c in text)):
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


def _inventory_concerns(diag: dict) -> list[str]:
    if not isinstance(diag, dict):
        raise ProfileError("Invalid inventory diagnostics")
    _integer(diag.get("item_count"), "inventory diagnostic item count")
    words = diag.get("slot_words")
    if not isinstance(words, list) or len(words) != 40:
        raise ProfileError("Inventory diagnostics require all 40 raw slot words")
    for word in words:
        _integer(word, "inventory diagnostic slot word", high=0xFFFF)
    if "invalid_item_indexes" in diag:
        _indexes(diag["invalid_item_indexes"], "invalid inventory item")
    concerns = []
    if any(word != 0xFFFF and word >= diag["item_count"] for word in diag["slot_words"][:38]):
        concerns.append(DANGLING_INVENTORY)
    if diag.get("invalid_item_indexes"):
        concerns.append(INVALID_INVENTORY)
    return concerns


def _source_concerns(row: dict, targets: dict, item_ids: set) -> list[str]:
    record = row["record"]
    concerns = []
    if row["kind"] in ("cre-template", "embedded-cre"):
        if "inventory_diagnostics" in record:
            concerns.extend(_inventory_concerns(record["inventory_diagnostics"]))
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
    if "raw_resref_hex" in record:
        concerns.append(INVALID_ITEM)
    elif row["item"] == "":
        concerns.append("empty-item-reference")
    elif row["item"] not in item_ids:
        concerns.append("missing-item-definition")
    return sorted(concerns)


def _validate_world_inventory_diagnostics(world: dict, resources: dict,
                                          sources: dict, targets: dict) -> None:
    """Check preserved raw slot tables and derive quarantine, including aliases."""
    owner_rows = {}
    for row in sources.values():
        if row["kind"] in ("cre-template", "embedded-cre"):
            owner_rows.setdefault(row["owner"], []).append(row["record"])
    direct_count = 0
    unresolved_count = 0
    for target in targets.values():
        selector = target["selector"]
        unresolved = "storage_diagnostics" in selector
        if unresolved:
            unresolved_count += 1
        direct = "inventory_diagnostics" in selector
        inherited = "template_inventory_diagnostics" in selector
        external = target["kind"] == "actor" and not selector["embedded_cre_size"] and not unresolved
        if direct:
            direct_count += 1
            if target["kind"] == "cre-template":
                base, end = 0, resources[target["resource"]]["byte_length"]
            elif target["kind"] == "actor" and not external:
                base = selector["embedded_cre_offset"]
                end = base + selector["embedded_cre_size"]
            else:
                raise ProfileError("Inventory diagnostics need a physical creature owner")
            diag = selector["inventory_diagnostics"]
            fields = {"cre_offset", "item_count", "items_offset", "item_slots_offset", "slot_words"}
            if isinstance(diag, dict) and "invalid_item_indexes" in diag:
                fields.add("invalid_item_indexes")
            _fields(diag, fields, "inventory diagnostics")
            for key in ("cre_offset", "item_count", "items_offset", "item_slots_offset"):
                _integer(diag[key], "inventory diagnostics " + key)
            if diag["cre_offset"] != base:
                raise ProfileError("Inventory diagnostics name the wrong creature offset")
            slot_start = base + diag["item_slots_offset"]
            if slot_start < base + 0x2D4 or slot_start + 80 > end:
                raise ProfileError("Inventory diagnostic slots lie outside their creature")
            words = diag["slot_words"]
            if not isinstance(words, list) or len(words) != 40:
                raise ProfileError("Inventory diagnostics require all 40 raw slot words")
            for word in words:
                _integer(word, "inventory diagnostic slot word", high=0xFFFF)
            count = diag["item_count"]
            rows = owner_rows.get(target["id"], [])
            if count != len(rows) or sorted(row["record_index"] for row in rows) != list(range(count)):
                raise ProfileError("Inventory diagnostics disagree with physical item records")
            invalid_indexes = sorted(row["record_index"] for row in rows if "raw_resref_hex" in row)
            if "invalid_item_indexes" in diag:
                _indexes(diag["invalid_item_indexes"], "invalid inventory item")
                if not diag["invalid_item_indexes"] or diag["invalid_item_indexes"] != invalid_indexes:
                    raise ProfileError("Inventory diagnostics disagree with invalid item records")
            elif invalid_indexes:
                raise ProfileError("Inventory diagnostics omit invalid item records")
            if not _inventory_concerns(diag):
                raise ProfileError("Inventory diagnostics contain no invalid references")
            # Empty tables have no byte span; their unused offset is retained.
            item_start, item_end = base + diag["items_offset"], base + diag["items_offset"] + count * 20
            if count and (item_start < base + 0x2D4 or item_end > end or
                          max(item_start, slot_start) < min(item_end, slot_start + 80)):
                raise ProfileError("Inventory diagnostic item and slot sections overlap or exceed their creature")
            for row in rows:
                index = row["record_index"]
                if (row["byte_offset"] != item_start + index * 20 or
                        row["slots"] != [slot for slot, word in enumerate(words[:38]) if word == index]):
                    raise ProfileError("Inventory diagnostics disagree with item offsets or slots")
        template = targets.get(selector.get("template_resource")) if external else None
        expected = template["selector"].get("inventory_diagnostics") if template else None
        if inherited != (expected is not None) or (inherited and selector["template_inventory_diagnostics"] != expected):
            raise ProfileError("Actor inventory diagnostics disagree with its template")
        diag = selector.get("inventory_diagnostics", selector.get("template_inventory_diagnostics"))
        expected_concerns = _inventory_concerns(diag) if diag is not None else []
        for concern in (DANGLING_INVENTORY, INVALID_INVENTORY):
            if (concern in target["concerns"]) != (concern in expected_concerns):
                raise ProfileError("Target concerns disagree with its inventory diagnostics")
    if (world["schema"] == DIAGNOSTIC_WORLD_SCHEMA) != bool(direct_count or unresolved_count):
        raise ProfileError("World schema disagrees with its inventory diagnostics")


def inventory_diagnostic_counts(world: dict) -> dict:
    """Aggregate direct owners only; external actor references are not copies."""
    diagnostics = [row["selector"]["inventory_diagnostics"] for row in world["targets"]
                   if "inventory_diagnostics" in row["selector"]]
    return {"dangling_inventory_owners": sum(DANGLING_INVENTORY in _inventory_concerns(diag) for diag in diagnostics),
            "dangling_inventory_slots": sum(word != 0xFFFF and word >= diag["item_count"]
                for diag in diagnostics for word in diag["slot_words"][:38]),
            "invalid_inventory_owners": sum(INVALID_INVENTORY in _inventory_concerns(diag) for diag in diagnostics),
            "invalid_item_records": sum("raw_resref_hex" in row["record"] for row in world["sources"]),
            "unresolved_actor_storage": sum("storage_diagnostics" in row["selector"] for row in world["targets"])}


def validate_world(world: dict, catalog: dict) -> dict:
    item_index = validate_catalog(catalog)
    _fields(world, {"schema", "game_root", "catalog_fingerprint", "coverage", "resources",
                    "sources", "targets", "fingerprint"}
            | ({"unreadable"} if isinstance(world, dict) and "unreadable" in world else set()),
            "world catalogue")
    if world["schema"] not in (WORLD_SCHEMA, DIAGNOSTIC_WORLD_SCHEMA) or world["coverage"] != COVERAGE:
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
    unreadable = {}
    if "unreadable" in world:
        # A scan lists the files it left out only when there are some.
        rows = _rows(world["unreadable"], "unreadable resource")
        if not rows:
            raise ProfileError("Invalid unreadable resource list")
        unreadable = _indexed(rows, "unreadable resource")
        for identity, row in unreadable.items():
            _fields(row, {"id", "reason"}, "unreadable resource")
            if identity not in resources or resources[identity]["kind"] not in READ_KINDS:
                raise ProfileError("An unreadable resource must be a scanned creature, area or store")
            _text(row["reason"], "unreadable resource reason")
    for row in list(sources.values()) + list(targets.values()):
        if not isinstance(row.get("resource"), str) or row["resource"] not in resources:
            raise ProfileError("A world record refers to an unknown resource")
        if row["resource"] in unreadable:
            raise ProfileError("A world record refers to a resource the scan left out")
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
        unresolved = "storage_diagnostics" in selector
        if (UNRESOLVED_STORAGE in row["concerns"]) != unresolved:
            raise ProfileError("Target concerns disagree with its creature storage")
        if unresolved and row["kind"] != "actor":
            raise ProfileError("Only area actors can have unresolved creature storage")
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
                incomplete = bool(selector["embedded_cre_offset"]) != bool(selector["embedded_cre_size"])
                if unresolved:
                    diag = _fields(selector["storage_diagnostics"], {"reason"}, "actor storage diagnostics")
                    if (diag["reason"] != "incomplete-embedded-cre" or not incomplete or selector["flags"] & 1 or
                            any(key in selector for key in ("template_resource", "inventory_diagnostics", "template_inventory_diagnostics"))):
                        raise ProfileError("Actor storage diagnostics disagree with raw storage fields")
                elif incomplete:
                    raise ProfileError("Incomplete actor storage is missing its diagnostics")
                elif not selector["embedded_cre_size"]:
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
        # Only physical observations can have an empty item reference. Keep
        # _resref strict for resources, selected items and compiled campaigns.
        if row["item"] != "":
            _resref(row["item"])
        if "raw_resref_hex" in record:
            raw = record["raw_resref_hex"]
            if (row["kind"] not in ("cre-template", "embedded-cre") or row["item"] != "" or
                    not isinstance(raw, str) or re.fullmatch(r"[0-9a-f]{16}", raw) is None or
                    "inventory_diagnostics" not in record):
                raise ProfileError("Invalid raw creature item reference")
            try:
                _reference(bytes.fromhex(raw), 0, optional=True)
            except ScanError:
                pass
            else:
                raise ProfileError("Raw invalid item reference is actually a valid or empty reference")
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
            owner_target = targets.get(owner)
            if owner_target is None:
                raise ProfileError("Creature source has no matching owner target")
            owner_selector = owner_target["selector"]
            if "storage_diagnostics" in owner_selector:
                raise ProfileError("Unresolved actor storage cannot own physical item records")
            if (("inventory_diagnostics" in record) != ("inventory_diagnostics" in owner_selector) or
                    record.get("inventory_diagnostics") != owner_selector.get("inventory_diagnostics")):
                raise ProfileError("Source inventory diagnostics disagree with its owner")
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
        elif "inventory_diagnostics" in record:
            raise ProfileError("Only creature sources can carry inventory diagnostics")
        if row["kind"] == "container":
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
    _validate_world_inventory_diagnostics(world, resources, sources, targets)
    if not isinstance(world["fingerprint"], str) or world["fingerprint"] != world_fingerprint(world):
        raise ProfileError("World catalogue content changed; scan again")
    return {"resources": resources, "sources": sources, "targets": targets,
            "unreadable": {identity: row["reason"] for identity, row in unreadable.items()}}


class Scans:
    """An item catalogue and the world catalogue scanned with it, validated
    once for a series of calls on them, such as one editor request (a preset
    fit, a check, applying a configuration). It also keeps the original tables
    those calls resolve for a plan, and why an original option cannot apply
    when they read the game's files to find out. Neither catalogue may change
    while it is in use; `items` and `index` are validate_catalog's and
    validate_world's results for these very catalogues when the caller
    already has them."""

    def __init__(self, catalog: dict, world: dict, *, items: dict | None = None,
                 index: dict | None = None):
        self.catalog, self.world = catalog, world
        self.items = validate_catalog(catalog) if items is None else items
        self.index = validate_world(world, catalog) if index is None else index
        self.tables: dict = {}
        self.option_problems: dict[str, str | None] = {}

    def indexes(self, catalog: dict, world: dict) -> tuple[dict, dict]:
        """The item index and the world index, for the catalogues these scans hold."""
        if catalog is not self.catalog or world is not self.world:
            raise ValueError("These validated scans belong to other catalogues")
        return self.items, self.index


def world_index(catalog: dict, world: dict, scans: Scans | None = None) -> dict:
    """validate_world's index of these catalogues, from `scans` when given."""
    return validate_world(world, catalog) if scans is None else scans.indexes(catalog, world)[1]


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


def new_binding_plan(profile: dict, catalog: dict, world: dict, *, scans: Scans | None = None) -> dict:
    validate_profile(profile, catalog)
    world_index(catalog, world, scans)
    return {"schema": PLAN_SCHEMA, "catalog_fingerprint": catalog["fingerprint"],
            "world_fingerprint": world["fingerprint"], "sources": [], "registrations": [], "targets": []}


UNUSABLE_SOURCE = {"unreferenced-inventory-record", "aliased-inventory-record", "unowned-item-record",
                   "shared-container-record", "infinite-stock", "empty-stock", "unknown-stock-supply",
                   "missing-item-definition", "empty-item-reference", DANGLING_INVENTORY,
                   INVALID_INVENTORY, INVALID_ITEM}


def bind_profile(profile: dict, catalog: dict, world: dict, plan: dict, *,
                 scans: Scans | None = None) -> dict:
    """Freeze explicit choices with evidence, never infer safe runtime behavior."""
    frozen = validate_profile(profile, catalog)
    indexes = world_index(catalog, world, scans)
    _fields(plan, PLAN_FIELDS | (set(plan) & PLAN_OPTIONAL_FIELDS if isinstance(plan, dict) else set()),
            "binding plan")
    replacements = plan.get("replacements", {})
    if not isinstance(replacements, dict):
        raise ProfileError("Creature replacements must map source identities to item resrefs or blank")
    catalog_items = {item["resref"]: item for item in catalog["items"]}
    if plan["schema"] != PLAN_SCHEMA or plan["catalog_fingerprint"] != catalog["fingerprint"] or plan["world_fingerprint"] != world["fingerprint"]:
        raise ProfileError("Binding plan is stale or unsupported; review against the current scans")
    # A preset's plan has all of its metadata and its original recipients; any
    # plan may have special places (special_places) as original recipients.
    original_keys = {"original_template_id", "original_choices", "original_options"}
    present_original = bool(set(plan) & original_keys)
    if present_original and not original_keys | {"target_registrations"} <= set(plan):
        raise ProfileError("Original plan metadata is incomplete")
    source_rows, target_rows = (original_table_rows(catalog, world, plan,
                                                    memo=None if scans is None else scans.tables)
                                if present_original else ({}, {}))
    shield = plan.get("spell_shield")
    if present_original and plan["original_options"].get("more_spell_shield"):
        # The scroll's shop row is randomised exactly when it is one of the plan's rewards.
        expected = spell_shield_spec(world, randomized=spell_shield_randomized(
            registration.get("authored") for registration in _rows(plan["registrations"], "registration")
            if registration.get("adapter_id") == ORIGINAL_SOURCE_ADAPTER))
        if shield != expected:
            raise ProfileError("Spell Shield stock metadata disagrees with the scan and the plan's rewards")
    elif "spell_shield" in plan:
        raise ProfileError("Spell Shield stock metadata requires its original option")
    if present_original and plan["original_options"].get("cursed_disguise"):
        disguise = plan.get("cursed_disguises")
        validate_cursed_disguise_spec(disguise)
        expected = cursed_disguise_spec({name: row["sha256"] for name, row in catalog_items.items()},
                                        game=disguise["game"], item_revisions=disguise["item_revisions"])
        if disguise != expected:
            raise ProfileError("Cursed-disguise resources differ from the scanned catalogue")
    elif "cursed_disguises" in plan:
        raise ProfileError("Cursed disguises require their original option")
    registrations = {}
    grant_sites = set()
    reserved_physical_ids = set()
    for registration in _rows(plan["registrations"], "registration"):
        if registration.get("adapter_id") == VIRTUAL_ADAPTER_ID:
            validate_virtual_registration(registration)
            item = catalog_items.get(registration["item"])
            if item is None or item["sha256"] != registration["resource_sha256"]:
                raise ProfileError("Virtual source must match the selected installed ITM")
        elif registration.get("adapter_id") == ORIGINAL_SOURCE_ADAPTER:
            if not present_original:
                raise ProfileError("Original source needs original plan metadata")
            physical_ids = validate_original_source(registration, source_rows, world, spell_shield=shield)
            resource = indexes["resources"].get(registration["resource"])
            if resource is None or resource["sha256"] != registration["resource_sha256"]:
                raise ProfileError("Original source resource changed or is absent")
            if registration["resource"] in indexes["unreadable"]:
                raise ProfileError(f"Original source resource cannot be read: {registration['resource']}")
            if reserved_physical_ids.intersection(physical_ids):
                raise ProfileError("Original source physical records overlap")
            reserved_physical_ids.update(physical_ids)
        else:
            _fields(registration, {"id", "item", "resource", "resource_sha256", "adapter_id", "selector", "copies", "optional"}, "registration")
        identity = _text(registration["id"], "registration identity")
        if identity in registrations:
            raise ProfileError("Duplicate registered source identity")
        for key in ("item", "resource", "adapter_id", "selector"):
            _text(registration[key], f"registration {key}")
        allowed_kinds = (("itm:",) if registration["adapter_id"] == VIRTUAL_ADAPTER_ID else
                         ("cre:", "bcs:", "dlg:", "spl:", "sto:") if registration["adapter_id"] == ORIGINAL_SOURCE_ADAPTER else
                         ("bcs:", "dlg:"))
        if not registration["resource"].startswith(allowed_kinds):
            raise ProfileError("Registered source has an unsupported resource kind")
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
            if identity in reserved_physical_ids:
                raise ProfileError("Direct physical source overlaps an original registration")
            if UNUSABLE_SOURCE.intersection(row["concerns"]):
                raise ProfileError("Selected physical source is not a finite, unambiguous inventory record")
            selected_ids.add(identity)
            selected_sources.append({"kind": "physical", "source": deepcopy(row),
                                     "resource_sha256": indexes["resources"][row["resource"]]["sha256"]})
            if identity in replacements:
                replacement = replacements[identity]
                if row["kind"] not in ("cre-template", "embedded-cre", "container"):
                    raise ProfileError("Replacement equipment requires a creature or container source")
                _resref(replacement)
                if replacement == item:
                    raise ProfileError("Replacement must differ from the relocated item")
                if replacement == "blank":
                    selected_sources[-1]["replacement"] = {"resref": "blank"}
                else:
                    replacement_hash = (catalog_items[replacement]["sha256"]
                                        if replacement in catalog_items else None)
                    if replacement_hash is None and replacement == SHIPPED_REPLACEMENT:
                        authored = source_rows.get("bg2:base:item:86")
                        if (authored is not None and authored["enabled"] and
                                authored["replacement"] == replacement and
                                identity in authored["source_ids"]):
                            replacement_hash = shipped_replacement_hash()
                    if replacement_hash is None:
                        raise ProfileError(f"Replacement item is absent: {replacement}")
                    selected_sources[-1]["replacement"] = {
                        "resref": replacement, "resource_sha256": replacement_hash}
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
    if set(replacements) - selected_ids:
        raise ProfileError("Replacement equipment refers to an unselected source")
    copies = _bound_copies(plan, world, indexes, catalog_items, source_rows, selected_sources) \
        if present_original else []
    if "duplicates" in plan and not present_original:
        raise ProfileError("Second copies of rewards need original plan metadata")
    original_targets = {}
    special = None
    for registration in _rows(plan.get("target_registrations", []), "original target registration"):
        rows = target_rows
        selector = registration.get("selector")
        row = target_rows.get(selector) if isinstance(selector, str) else None
        if row is None or not row["enabled"]:
            # A special place, which any plan may use, also where the preset's
            # options leave its row out.
            if special is None:
                special = special_target_rows(catalog, world, memo=None if scans is None else scans.tables)
            if isinstance(selector, str) and selector in special:
                rows = special
            elif not present_original:
                raise ProfileError("Original target needs original plan metadata")
        validate_original_target(registration, rows)
        identity = registration["id"]
        if identity in original_targets:
            raise ProfileError("Duplicate original target registration")
        resource = indexes["resources"].get(registration["resource"])
        if resource is None or resource["sha256"] != registration["resource_sha256"]:
            raise ProfileError("Original target area changed or is absent")
        if registration["resource"] in indexes["unreadable"]:
            raise ProfileError(f"Original target area cannot be read: {registration['resource']}")
        original_targets[identity] = registration
    targets, target_ids = [], set()
    used_original_targets = set()
    capacity = 0
    for choice in _rows(plan["targets"], "target choice"):
        _fields(choice, {"id", "capacity", "optional"}, "target choice")
        identity = _text(choice["id"], "target identity")
        if identity in original_targets:
            registration = original_targets[identity]
            if (identity in target_ids or choice["capacity"] != registration["capacity"] or
                    choice["optional"] is not False):
                raise ProfileError("Original target choice disagrees with its registration")
            target_ids.add(identity)
            used_original_targets.add(identity)
            targets.append({"target": original_target_record(registration),
                            "capacity": choice["capacity"],
                            "resource_sha256": registration["resource_sha256"]})
            capacity += choice["capacity"]
            continue
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
            # A file the scan left out has no targets; it is not absent from the game.
            unread = parts[0] in indexes["unreadable"]
            if not choice["optional"]:
                raise ProfileError(f"Required target's resource cannot be read: {parts[0]}" if unread
                                   else "Required target is absent")
            skipped.append({"kind": "target", "id": identity,
                            "reason": "optional-resource-unreadable" if unread else "optional-resource-absent"})
            continue
        if UNRESOLVED_STORAGE in target["concerns"]:
            raise ProfileError("Target has unresolved creature storage and cannot receive rewards")
        if target["kind"] == "actor" and not target["selector"]["embedded_cre_size"]:
            template = target["selector"]["template_resource"]
            if template not in indexes["resources"]:
                raise ProfileError("Target refers to a missing creature template")
            if template in indexes["unreadable"]:
                raise ProfileError("Target refers to a creature template that cannot be read")
        if {DANGLING_INVENTORY, INVALID_INVENTORY}.intersection(target["concerns"]):
            raise ProfileError("Target has invalid inventory references and cannot receive rewards")
        targets.append({"target": deepcopy(target), "capacity": choice["capacity"],
                        "resource_sha256": indexes["resources"][target["resource"]]["sha256"]})
        capacity += choice["capacity"]
    if used_original_targets != set(original_targets):
        raise ProfileError("Unused original target registrations must be removed")
    if reward_units == 0:
        raise ProfileError("No available reward records remain after optional source resolution")
    overflow = (isinstance(plan.get("randomization"), dict) and
                plan["randomization"].get("overflow") is True)
    if capacity < reward_units and not overflow:
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
    if copies:
        result["duplicates"] = copies
    result["fingerprint"] = _digest(result)
    return result


def _bound_copies(plan: dict, world: dict, indexes: dict, catalog_items: dict, source_rows: dict,
                  selected_sources: list[dict]) -> list[dict]:
    """The plan's second copies of its moved rewards (duplicates.py), checked
    against Item Randomiser's tables and the scan, with their fingerprints.

    Each selected source's original row, as the plan's chance groups name it,
    has exactly the copies the tables and this game give it."""
    game = preset_game(world, plan["original_template_id"])
    names = eet_names(world["game_root"]) if game == "eet" else None
    rules = plan.get("randomization")
    rules = rules.get("row_filters") if isinstance(rules, dict) else None
    rules = rules.get("sources") if isinstance(rules, dict) else None
    held, expected = holdings(indexes["sources"]), []
    for selected in selected_sources:
        source = selected["source"]
        rule = rules.get(source["id"]) if isinstance(rules, dict) else None
        group = rule.get("group") if isinstance(rule, dict) else None
        if not isinstance(group, str) or ":" not in group:
            continue
        campaign, ident = group.split(":", 1)
        route = mode2_route_source(source.get("authored")) if selected["kind"] == "registered" else None
        found = expected_copies(source["id"], campaign, ident, source["item"], indexes, held, game, names,
                                route=route)
        if found and not any(
                row["enabled"] and (row["campaign"], row["ident"], row["item_resref"]) == (campaign, ident, source["item"])
                and source["id"] in (f"original-source:{row['id']}", f"virtual:{row['id']}", *row["source_ids"])
                for row in source_rows.values()):
            raise ProfileError("Second copies of a reward need its original row")
        expected += found
    expected.sort(key=lambda copy: (copy["source_id"], copy["id"]))
    if plan.get("duplicates", []) != expected or plan.get("duplicates") == []:
        raise ProfileError("Second copies of the moved rewards disagree with Item Randomiser's "
                           "duplicates tables and the scan")
    result = []
    for copy in expected:
        bound = {**copy, "resource_sha256": indexes["resources"][copy["resource"]]["sha256"]}
        if "replacement" in copy:
            item = catalog_items.get(copy["replacement"])
            if item is None:
                raise ProfileError(f"Replacement item is absent: {copy['replacement']}")
            bound["replacement"] = {"resref": copy["replacement"], "resource_sha256": item["sha256"]}
        result.append(bound)
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
