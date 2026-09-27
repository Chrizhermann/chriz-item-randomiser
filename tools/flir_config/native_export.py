"""Export reviewed campaigns for the experimental container-to-creature runtime.

This writes configuration outside the game. It does not install runtime hooks,
change resources, choose gameplay content, or establish live target eligibility.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path

from .campaigns import read_campaign, _render_lua, _store_path, _write_immutable
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
    required, pristine, selected_items = set(), set(), set()

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
    for selected in manifest["sources"]:
        source, identity = selected["record"], selected["id"]
        _require(source["kind"] == "container", f"Native export does not yet support this source: {identity}")
        _require(index["sources"].get(identity) == source and
                 index["resources"][source["resource"]]["sha256"] == selected["resource_sha256"],
                 f"Selected source differs from the scan: {identity}")
        owner = index["targets"][source["owner"]]
        selector, record = owner["selector"], source["record"]
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
        item = items[source["item"]]
        _require(re.fullmatch(r"[a-z0-9#_-]{1,8}", source["item"]) is not None and
                 record["expiration"] == 0 and record["flags"] in (0, 1) and
                 item["stack_size"] <= 1 and item["flags"] & 12 == 12,
                 f"Native source requires an unexpired, nonstackable, visible droppable item: {identity}")
        _require(source["item"] not in selected_items,
                 f"Native export does not yet support selecting two copies of {source['item']}")
        selected_items.add(source["item"])
        native["sources"][identity] = {
            "kind": "guarded-container", "area": area, "script_name": name,
            "x": selector["x"], "y": selector["y"], "container_type": selector["type"],
            "payload": {key: record[key] for key in ("resref", "expiration", "charges", "flags")},
            "identity": _digest({"source": selected, "owner": owner})}
        use(source["resource"]); use("itm:" + source["item"])
        pristine.add(source["resource"])

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
    pending, required, pristine = {}, set(), set()
    for key, manifest in sorted(manifests.items()):
        native, resources, areas = _compile(manifest, world, index, items)
        required.update(resources); pristine.update(areas)
        base = "campaign-store/revisions/" + key
        pending[base + ".json"] = canonical_bytes(manifest)
        pending[base + ".lua"] = _render_lua(manifest)
        pending[base + ".native.json"] = canonical_bytes(native)
        pending[base + ".native.lua"] = _render_lua(native)
    # Reuse the scanner's resource reader; only selected resources are demanded.
    with GameResources(Path(catalog["game_root"]), {k: RESOURCE_TYPES[k] for k in ("itm", "are", "cre")}) as reader:
        for resource in sorted(required):
            kind, name = resource.split(":", 1)
            data = reader.read(kind, name)
            _require(hashlib.sha256(data).hexdigest() == index["resources"][resource]["sha256"],
                     f"Installed resource changed; rescan before export: {resource}")
            if resource in pristine:
                pending[name.upper() + ".pristine.are"] = data
    for relative, data in pending.items():
        _write_immutable(_store_path(output, Path(relative)), data)
    revisions = sorted(manifests)
    _store_path(output, Path("source-gate.lua")).write_bytes(_render_lua(revisions))
    _store_path(output, Path("next.lua")).write_bytes(_render_lua(selected["revision"]))
    return {"revision": selected["revision"], "revisions": revisions,
            "config_path": str(output), "state": "experimental-native-config"}
