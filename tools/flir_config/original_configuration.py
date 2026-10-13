"""Build a reviewed post-install draft from the original Randomiser tables.

Every enabled authored location remains a distinct target registration. Rows
that need runtime adapters carry only table data and scanned resource hashes;
this module never supplies executable code or chooses an assignment.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

from .game_names import convert_resource
from .original_catalog import resolve_template
from .original_options import is_spell_shield_row, spell_shield_spec, tome_variants
from .original_registrations import is_unreferenced_creature_source
from .mode2_rewards import SOURCE_REWARDS as MODE2_SOURCES, source_spec as mode2_source_spec
from .cursed_disguises import cursed_disguise_spec, item_revisions_installed
from .profiles import ProfileError, edit_profile, new_profile, validate_catalog
from .resources import GameResources, RESOURCE_TYPES
from .scripted_sources import blpool_registration
from .sources import new_binding_plan, validate_world
from .virtual_sources import virtual_registration

_SOURCE_FIELDS = ("id", "source_ref", "campaign", "family", "source", "ident",
                  "token", "tier", "occurrence", "chance")
_TARGET_FIELDS = ("id", "source_ref", "endpoint_id", "endpoint_kind",
                  "endpoint_source_ref", "campaign", "family", "location",
                  "target", "area", "ident", "tier", "occurrence", "chance",
                  "area_resource", "creature_resource", "container_name",
                  "members", "target_ids")
_PAYLOAD_FIELDS = ("resref", "expiration", "charges", "flags")


def _tier(row: dict) -> str:
    return f"{row['campaign']}:{row['tier']}"


def _item_defaults(catalog: dict, item_hashes: dict[str, str],
                   names: set[str]) -> dict[str, list[int]]:
    if not names:
        return {}
    result = {}
    with GameResources(Path(catalog["game_root"]), {"itm": RESOURCE_TYPES["itm"]}) as reader:
        for name in sorted(names):
            data = reader.read("itm", name)
            if hashlib.sha256(data).hexdigest() != item_hashes[name]:
                raise ProfileError(f"Installed ITM changed; rescan before building: {name}")
            charges = [0, 0, 0]
            if len(data) > 0x71:
                offset = int.from_bytes(data[0x64:0x68], "little")
                count = int.from_bytes(data[0x68:0x6A], "little")
                for index in range(min(count, 3)):
                    at = offset + index * 0x38 + 0x22
                    if at + 2 > len(data):
                        raise ProfileError(f"Installed ITM ability charges are invalid: {name}")
                    charges[index] = int.from_bytes(data[at:at + 2], "little")
            result[name] = charges
    return result


def _authored_source(row: dict) -> dict:
    return {key: row[key] for key in _SOURCE_FIELDS}


def _original_source(row: dict, resource_hash: str, payload: dict,
                     physical_source_ids: list[str], *, grouped: bool = False) -> dict:
    authored = _authored_source(row)
    if grouped:
        authored["match_policy"] = "all_one_unit"
        authored["expected_matches"] = len(physical_source_ids)
    return {"id": "original-source:" + row["id"], "item": row["item_resref"],
            "resource": row["source_resource"], "resource_sha256": resource_hash,
            "adapter_id": "original-source-v1", "selector": row["id"],
            "copies": 1, "optional": False, "payload": payload,
            "physical_source_ids": physical_source_ids, "authored": authored}


def _original_target(row: dict, resource_hash: str) -> dict:
    return {"id": "original-target:" + row["id"],
            "resource": row["area_resource"], "resource_sha256": resource_hash,
            "adapter_id": "original-target-v1", "selector": row["id"],
            "capacity": 1, "optional": False,
            "authored": {key: row[key] for key in _TARGET_FIELDS}}


def build_original_configuration(catalog: dict, world: dict, template_id: str, *,
                                 items: str | None = None, targets: str | None = None,
                                 options: dict | None = None) -> dict:
    """Return a data-only ``{profile, plan}`` preserving enabled authored rows."""
    catalog_items = validate_catalog(catalog)
    index = validate_world(world, catalog)
    draft = resolve_template(catalog, world, template_id,
                             items=items, targets=targets, options=options)
    enabled_sources = [row for row in draft["items"] if row["enabled"]]
    enabled_targets = [row for row in draft["targets"] if row["enabled"]]
    shield = (spell_shield_spec(world, randomized=any(is_spell_shield_row(row) for row in enabled_sources))
              if draft["options"].get("more_spell_shield") else None)
    # The old installer omits a physical source row only when the exact item
    # has no matching record. Its authored location remains in the pool.
    physical = index["sources"]
    sources = []
    for row in enabled_sources:
        if shield and is_spell_shield_row(row):
            if row["item_resref"] not in catalog_items:
                raise ProfileError("More Spell Shield scrolls requires SCRL8X.ITM")
            sources.append(row)
            continue
        if row["binding_problem"] == "original-source-has-no-matching-record":
            continue
        if row["adapter"] == "unshey" and not any(
                source["resource"] == row["source_resource"]
                and source["item"] == row["item_resref"]
                for source in physical.values()):
            continue
        if not row["available"] and row["adapter"] != "unshey":
            raise ProfileError(f"{row['source_ref']}: {row['availability_reason']}")
        if row["binding_problem"] not in (None, "scripted-source-requires-adapter",
                                      "unshey-requires-legacy-adapter"):
            raise ProfileError(f"{row['source_ref']}: {row['binding_problem']}")
        sources.append(row)
    if not sources or not enabled_targets:
        raise ProfileError("Original template has no enabled sources or locations")

    profile = edit_profile(new_profile(catalog, template_id, draft["name"]), catalog,
                           include=sorted({row["item_resref"] for row in sources}))
    plan = new_binding_plan(profile, catalog, world)
    plan["original_template_id"] = template_id
    plan["original_choices"] = draft["choices"].copy()
    plan["original_options"] = draft["options"].copy()
    if shield is not None:
        plan["spell_shield"] = shield
    if draft["options"].get("cursed_disguise"):
        log = Path(catalog["game_root"]) / "WeiDU.log"
        plan["cursed_disguises"] = cursed_disguise_spec(
            {name: item["sha256"] for name, item in catalog_items.items()},
            item_revisions=item_revisions_installed(log.read_bytes() if log.exists() else b""))
    plan["target_registrations"] = []
    randomization = {"sources": {}, "targets": {},
                     "row_filters": {"sources": {}, "targets": {}},
                     "loss_percent": (draft["options"]["loss_percent"]
                                      if draft["options"]["loss_mode"] == "some" else 0),
                     "unrandomized_percent": draft["options"]["unrandomized_percent"],
                     "overflow": True}
    plan["randomization"] = randomization
    wanted_defaults = {row["item_resref"] for row in sources
                       if row["adapter"] in ("virtual", "scripted", "unshey")}
    defaults = _item_defaults(catalog,
                              {name: catalog_items[name]["sha256"] for name in wanted_defaults},
                              wanted_defaults)
    by_item = defaultdict(lambda: {"source_ids": [], "registration_ids": []})
    source_rows = []
    for row in sources:
        item, resource = row["item_resref"], row["source_resource"]
        item_choices = by_item[item]
        registration = None
        if shield and is_spell_shield_row(row):
            registration = _original_source(row, shield["resource_sha256"], shield["payload"],
                                            [shield["original_source_id"]] if shield["original_source_id"] else [])
            registration["copies"] = shield["quantity"]
        elif row["adapter"] == "virtual":
            registration = virtual_registration(item, row["id"], 1,
                                                catalog_items[item]["sha256"], defaults[item])
        elif row["adapter"] == "scripted" and resource == "bcs:blpool" and item == "blun12":
            registration = blpool_registration(world)
        elif row["adapter"] in ("scripted", "unshey"):
            if resource not in index["resources"]:
                raise ProfileError(f"{row['source_ref']}: source-resource-absent")
            matched = sorted(source["id"] for source in physical.values()
                             if source["resource"] == resource and source["item"] == item)
            if row["adapter"] == "unshey" and len(matched) != 1:
                raise ProfileError(f"{row['source_ref']}: ambiguous-unshey-records")
            payload = {"resref": item, "expiration": 0,
                       "charges": defaults[item], "flags": 0}
            if row["adapter"] == "unshey":
                payload = {key: physical[matched[0]]["record"][key]
                           for key in _PAYLOAD_FIELDS}
            elif resource == "spl:spin621":
                from .spell_rewards import PAYLOAD
                payload = {**PAYLOAD, "charges": list(PAYLOAD["charges"])}
            elif "original-source:" + row["id"] in MODE2_SOURCES:
                payload = mode2_source_spec("original-source:" + row["id"])["payload"]
            registration = _original_source(row, index["resources"][resource]["sha256"],
                                            payload, matched if row["adapter"] == "unshey" else [])
        else:
            ids = row["source_ids"]
            if not ids:
                raise ProfileError(f"{row['source_ref']}: physical source selector has no record")
            if len(ids) > 1 or is_unreferenced_creature_source(row, physical):
                if any(physical[identity]["kind"] != "cre-template" for identity in ids):
                    raise ProfileError(f"{row['source_ref']}: ambiguous-source-records")
                payloads = [{key: physical[identity]["record"][key] for key in _PAYLOAD_FIELDS}
                            for identity in ids]
                if any(payload != payloads[0] for payload in payloads[1:]):
                    raise ProfileError(f"{row['source_ref']}: grouped-source-payload-mismatch")
                registration = _original_source(row, index["resources"][resource]["sha256"],
                                                payloads[0], sorted(ids), grouped=len(ids) > 1)
            else:
                item_choices["source_ids"].append(ids[0])
                source_rows.append((ids[0], row))
                if row["replacement"] != "blank":
                    replacement = convert_resource(row["replacement"], "itm", row["campaign"])
                    plan.setdefault("replacements", {})[ids[0]] = replacement
                elif (physical[ids[0]]["kind"] in ("cre-template", "embedded-cre")
                      and any(slot < 21 or slot > 36
                              for slot in physical[ids[0]]["record"]["slots"])):
                    plan.setdefault("replacements", {})[ids[0]] = "blank"
        if registration is not None:
            plan["registrations"].append(registration)
            item_choices["registration_ids"].append(registration["id"])
            source_rows.append((registration["id"], row))

    for item, choices in sorted(by_item.items()):
        plan["sources"].append({"item": item, "policy": "relocate",
                                "source_ids": sorted(choices["source_ids"]),
                                "registration_ids": sorted(choices["registration_ids"])})
    target_rows = []
    for row in enabled_targets:
        resource = row["area_resource"]
        if resource not in index["resources"]:
            raise ProfileError(f"{row['source_ref']}: area-resource-absent")
        registration = _original_target(row, index["resources"][resource]["sha256"])
        plan["target_registrations"].append(registration)
        plan["targets"].append({"id": registration["id"], "capacity": 1, "optional": False})
        target_rows.append((registration["id"], row))

    targets_by_ident = defaultdict(list)
    for identity, row in target_rows:
        if row["ident"] != "x":
            targets_by_ident[(row["campaign"], row["ident"])].append(identity)
        randomization["targets"][identity] = [_tier(row)]
        randomization["row_filters"]["targets"][identity] = {
            "chance": row["chance"],
            "group": "" if row["ident"] == "x" else f"{row['campaign']}:{row['ident']}"}
    for identity, row in source_rows:
        excluded = [{"id": target_id, "ordinal": 1}
                    for target_id in targets_by_ident.get((row["campaign"], row["ident"]), [])]
        randomization["sources"][identity] = {
            "tier": _tier(row), "loss_exempt": row["tier"] in ("fa", "ka"),
            "exclude_targets": excluded}
        randomization["row_filters"]["sources"][identity] = {
            "chance": row["chance"],
            "group": "" if row["ident"] == "x" else f"{row['campaign']}:{row['ident']}"}
        if (draft["options"].get("totsc_tome", False) and
                row["campaign"] == "bg1" and row["ident"] == "b2c"):
            randomization["sources"][identity]["reward_variants"] = tome_variants(row["campaign"])
    plan["registrations"].sort(key=lambda row: row["id"])
    plan["target_registrations"].sort(key=lambda row: row["id"])
    plan["targets"].sort(key=lambda row: row["id"])
    return {"profile": profile, "plan": plan}
