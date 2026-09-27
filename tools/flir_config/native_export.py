"""Export reviewed container/creature sources for the experimental native runtime.

This writes configuration outside the game. It does not install runtime hooks,
change resources, choose gameplay content, or establish live target eligibility.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path

from .campaigns import read_campaign, _render_lua, _store_path, _write_immutable
from .creature_inventory import rewrite_inventory
from .store_inventory import rewrite_inventory as rewrite_store
from .profiles import HEX256, ProfileError, _digest, canonical_bytes, ensure_output_outside_game, validate_catalog
from .resources import GameResources, RESOURCE_TYPES
from .sources import validate_world


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProfileError(message)


def _name(value: str, identity: str) -> str:
    _require(isinstance(value, str) and 1 <= len(value) <= 32 and
             all(32 <= ord(c) < 127 for c in value),
             f"Native endpoint needs a nonempty ASCII script name: {identity}")
    return value


def _area(resource: str) -> str:
    _require(re.fullmatch(r"are:[a-z0-9#_-]{1,8}", resource) is not None,
             f"Native export needs an ARE resource: {resource}")
    return resource[4:].upper()


def _compile(manifest: dict, world: dict, index: dict, items: dict) -> tuple[dict, set[str], set[str]]:
    _require(manifest["world_fingerprint"] == world["fingerprint"],
             "Campaign and world scan differ; prepare against this scan before exporting")
    native = {"schema": "flir-native-fixture-v1", "revision": manifest["revision"],
              "sources": {}, "targets": {}, "resource_hashes": {},
              "ready_detail": "have been assigned to the selected recipients."}
    required, pristine, selected_items, selected_actors, selected_stores = set(), set(), set(), set(), set()

    def use(resource: str) -> None:
        required.add(resource)
        kind, name = resource.split(":", 1)
        native["resource_hashes"][name.upper() + "." + kind.upper()] = index["resources"][resource]["sha256"]

    def actor_name(target: dict) -> str | None:
        selector = target["selector"]
        template = index["targets"].get(selector.get("template_resource"))
        return (template["selector"].get("script_name") if template else selector.get("script_name"))

    container_names = Counter((t["resource"], t["selector"]["name"].lower())
                              for t in index["targets"].values() if t["kind"] == "container")
    actor_names = Counter((t["resource"], (actor_name(t) or "").lower())
                          for t in index["targets"].values() if t["kind"] == "actor")
    container_items = Counter((s["owner"], s["item"]) for s in index["sources"].values()
                              if s["kind"] == "container")
    creature_items = Counter((s["owner"], s["item"]) for s in index["sources"].values()
                             if s["kind"] in ("cre-template", "embedded-cre"))
    for selected in manifest["sources"]:
        source, identity = selected["record"], selected["id"]
        _require(source["kind"] in ("container", "cre-template", "embedded-cre", "store"),
                 f"Native export does not yet support this source: {identity}")
        _require(index["sources"].get(identity) == source and
                 index["resources"][source["resource"]]["sha256"] == selected["resource_sha256"],
                 f"Selected source differs from the scan: {identity}")
        record = source["record"]
        item = items[source["item"]]
        _require(re.fullmatch(r"[a-z0-9#_-]{1,8}", source["item"]) is not None and
                 record["expiration"] == 0 and record["flags"] in (0, 1) and
                 item["stack_size"] <= 1 and item["flags"] & 12 == 12,
                 f"Native source requires an unexpired, nonstackable, visible droppable item: {identity}")
        _require(source["item"] not in selected_items,
                 f"Native export does not yet support selecting two copies of {source['item']}")
        selected_items.add(source["item"])
        if source["kind"] == "store":
            _require(source["resource"] not in selected_stores,
                     f"Native export currently supports one selected stock row per shop: {identity}")
            selected_stores.add(source["resource"])
            _require(sum(s["owner"] == source["owner"] and s["item"] == source["item"]
                         for s in index["sources"].values()) == 1,
                     f"Native source shop holds another row of {source['item']}: {identity}")
            native["sources"][identity] = {
                "kind": "guarded-store", "store": source["resource"][4:],
                "record_index": record["record_index"], "quantity": selected["quantity"],
                "payload": {key: record[key] for key in ("resref", "expiration", "charges", "flags")},
                "identity": _digest({"source": selected})}
            use(source["resource"]); use("itm:" + source["item"])
            continue
        elif source["kind"] == "container":
            owner = index["targets"][source["owner"]]
            selector = owner["selector"]
            area, name = _area(source["resource"]), _name(selector["name"], identity)
            _require(not selector["dynamic_item_table"] and
                     container_names[source["resource"], name.lower()] == 1,
                     f"Native source needs a unique static container name: {identity}")
            _require(0 <= selector["x"] <= 32767 and 0 <= selector["y"] <= 32767,
                     f"Native container coordinates are unsupported: {identity}")
            # The current native confirmation counts this resref in its owner.
            # An unselected duplicate must not prevent confirmation indefinitely.
            _require(container_items[source["owner"], source["item"]] == 1,
                     f"Native source container holds another copy of {source['item']}: {identity}")
            native["sources"][identity] = {
                "kind": "guarded-container", "area": area, "script_name": name,
                "x": selector["x"], "y": selector["y"], "container_type": selector["type"],
                "payload": {key: record[key] for key in ("resref", "expiration", "charges", "flags")},
                "identity": _digest({"source": selected, "owner": owner})}
        else:
            if source["kind"] == "cre-template":
                actors = [target for target in index["targets"].values()
                          if target["kind"] == "actor" and
                          target["selector"].get("template_resource") == source["resource"]]
                _require(len(actors) == 1,
                         f"Native creature source needs exactly one static ARE actor for template "
                         f"{source['resource']} (found {len(actors)}): {identity}")
                owner = actors[0]
            else:
                owner = index["targets"][source["owner"]]
            selector = owner["selector"]
            area, name = _area(owner["resource"]), _name(actor_name(owner), identity)
            _require(actor_names[owner["resource"], name.lower()] == 1,
                     f"Native creature source needs a unique actor script name in its area: {identity}")
            _require(owner["id"] not in selected_actors,
                     f"Native export does not yet support two selected sources on one actor: {owner['id']}")
            selected_actors.add(owner["id"])
            _require(creature_items[source["owner"], source["item"]] == 1,
                     f"Native source creature holds another copy of {source['item']}: {identity}")
            _require(len(record["slots"]) == 1,
                     f"Native creature source requires one inventory slot: {identity}")
            replacement = selected.get("replacement")
            _require(21 <= record["slots"][0] <= 36 or replacement is not None,
                     f"Equipped creature source needs explicit replacement equipment (or blank): {identity}")
            if replacement and replacement["resref"] != "blank":
                resource = "itm:" + replacement["resref"]
                _require(resource in index["resources"] and
                         index["resources"][resource]["sha256"] == replacement["resource_sha256"],
                         f"Replacement item changed or is absent: {identity}")
                use(resource)
            native["sources"][identity] = {
                "kind": "guarded-creature", "area": area, "script_name": name,
                "actor_index": selector["record_index"],
                "creature_resource": source["resource"], "actor_owner": owner["id"],
                "payload": {key: record[key] for key in ("resref", "expiration", "charges", "flags")},
                "identity": _digest({"source": selected, "owner": owner})}
            if replacement:
                native["sources"][identity]["replacement"] = replacement["resref"]
            use(owner["resource"])
        use(source["resource"]); use("itm:" + source["item"])
        pristine.add(owner["resource"])

    for selected in manifest["targets"]:
        target, identity = selected["record"], selected["id"]
        _require(target["kind"] == "actor", f"Native export needs an ARE actor target: {identity}")
        _require(index["targets"].get(identity) == target and
                 index["resources"][target["resource"]]["sha256"] == selected["resource_sha256"],
                 f"Selected target differs from the scan: {identity}")
        name = _name(actor_name(target), identity)
        _require(actor_names[target["resource"], name.lower()] == 1,
                 f"Native target needs a unique actor script name in its area: {identity}")
        template = target["selector"].get("template_resource")
        use(target["resource"])
        if template:
            use(template)
        native["targets"][identity] = {"kind": "creature", "area": _area(target["resource"]),
            "script_name": name, "identity": _digest({"target": selected,
                "template": index["resources"].get(template)})}

    native["source_gate"] = {"schema": "flpr-unvisited-fixture-gate-v1",
                             "source_ids": [s["id"] for s in manifest["sources"]]}
    return native, required, pristine


def export_native(store: Path, catalog: dict, world: dict, output: Path,
                  revision: str | None = None) -> dict:
    """Export next-run settings while retaining already exported saved revisions."""
    items, index = validate_catalog(catalog), validate_world(world, catalog)
    output = ensure_output_outside_game(output, catalog)
    selected = read_campaign(store, revision)
    exported = output / "campaign-store"
    manifests = {p.stem: read_campaign(exported, p.stem)
                 for p in (exported / "revisions").glob("*.json") if HEX256.fullmatch(p.stem)}
    manifests[selected["revision"]] = selected
    pending, required, pristine, compiled = {}, set(), set(), {}
    for key, manifest in sorted(manifests.items()):
        native, resources, areas = _compile(manifest, world, index, items)
        compiled[key] = native
        required.update(resources); pristine.update(areas)
        base = "campaign-store/revisions/" + key
        pending[base + ".json"] = canonical_bytes(manifest)
        pending[base + ".lua"] = _render_lua(manifest)
        pending[base + ".native.json"] = canonical_bytes(native)
        pending[base + ".native.lua"] = _render_lua(native)
    # Reuse the scanner's resource reader; only selected resources are demanded.
    resource_bytes = {}
    with GameResources(Path(catalog["game_root"]), {k: RESOURCE_TYPES[k] for k in ("itm", "are", "cre", "sto")}) as reader:
        for resource in sorted(required):
            kind, name = resource.split(":", 1)
            data = reader.read(kind, name)
            _require(hashlib.sha256(data).hexdigest() == index["resources"][resource]["sha256"],
                     f"Installed resource changed; rescan before export: {resource}")
            if kind in ("are", "cre", "sto"):
                resource_bytes[resource] = data
            if resource in pristine:
                pending[name.upper() + ".pristine.are"] = data
    for key, manifest in sorted(manifests.items()):
        for source_index, selected_source in enumerate(manifest["sources"], 1):
            source = selected_source["record"]
            compiled_source = compiled[key]["sources"][selected_source["id"]]
            base = f"campaign-store/revisions/{key}.source-{source_index}"
            if compiled_source["kind"] == "guarded-store":
                original = resource_bytes[source["resource"]]
                pending[base + ".original.sto"] = original
                pending[base + ".removed.sto"] = rewrite_store(original, {source["record"]["record_index"]})
                continue
            if compiled_source["kind"] != "guarded-creature":
                continue
            resource = source["resource"]
            if source["kind"] == "cre-template":
                original = resource_bytes[resource]
            else:
                selector = index["targets"][compiled_source["actor_owner"]]["selector"]
                offset, size = selector["embedded_cre_offset"], selector["embedded_cre_size"]
                original = resource_bytes[resource][offset:offset + size]
            pending[base + ".original.cre"] = original
            replacement = selected_source.get("replacement", {}).get("resref", "blank")
            pending[base + ".removed.cre"] = rewrite_inventory(
                original, {source["record"]["record_index"]:
                           None if replacement == "blank" else replacement})
    for relative, data in pending.items():
        _write_immutable(_store_path(output, Path(relative)), data)
    revisions = sorted(manifests)
    _store_path(output, Path("source-gate.lua")).write_bytes(_render_lua(revisions))
    _store_path(output, Path("next.lua")).write_bytes(_render_lua(selected["revision"]))
    return {"revision": selected["revision"], "revisions": revisions,
            "config_path": str(output), "state": "experimental-native-config"}
