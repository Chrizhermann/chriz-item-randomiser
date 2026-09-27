"""Compile immutable inputs for the isolated logical campaign prototype.

These artifacts do not activate the installed Randomiser or prove engine source
suppression. Only already-bound physical inventory records are supported. The
Lua companion is generated data, never user-provided executable configuration.
"""

from __future__ import annotations

import os
import tempfile
from copy import deepcopy
from pathlib import Path

from .profiles import (HEX256, PROFILE_ID, ProfileError, _digest, canonical_bytes,
                       ensure_output_outside_game, read_json, write_json)
from .sources import (DANGLING_INVENTORY, INVALID_INVENTORY, UNRESOLVED_STORAGE,
                      UNUSABLE_SOURCE, bind_profile)

CAMPAIGN_SCHEMA = "flir-campaign-prototype-v1"
POINTER_SCHEMA = "flir-prototype-next-v1"
STATE = "prototype-not-playable"
MAX_REWARD_UNITS = 4096
MAX_TARGET_CAPACITY = 4096
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


def _validate_source(row: dict) -> int:
    _fields(row, {"id", "quantity", "record", "resource_sha256"}
            | ({"replacement"} if isinstance(row, dict) and "replacement" in row else set()), "source")
    _hash(row["resource_sha256"], "source resource fingerprint")
    quantity = _integer(row["quantity"], "source quantity", 1, MAX_REWARD_UNITS)
    source = _fields(row["record"], {"id", "item", "resource", "owner", "kind",
                                     "record", "concerns"}, "source record")
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
        if not isinstance(replacement, dict) or kind not in ("cre-template", "embedded-cre"):
            raise ProfileError("Campaign replacement requires a creature source")
        name = _resref(replacement.get("resref"))
        _fields(replacement, {"resref"} if name == "blank" else {"resref", "resource_sha256"}, "replacement")
        if name == source["item"]:
            raise ProfileError("Campaign replacement must differ from the relocated item")
        if name != "blank":
            _hash(replacement["resource_sha256"], "replacement fingerprint")
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


def _validate_campaign(body: dict) -> dict:
    _fields(body, _FIELDS, "artifact")
    if body["schema"] != CAMPAIGN_SCHEMA or body["state"] != STATE:
        raise ProfileError("Unsupported campaign prototype schema or state")
    for key in ("revision", "bound_revision", "world_fingerprint"):
        _hash(body[key], key)
    if not isinstance(body["profile_id"], str) or not PROFILE_ID.fullmatch(body["profile_id"]):
        raise ProfileError("Invalid campaign profile identity")
    if body["runtime_requirements"] != RUNTIME_REQUIREMENTS:
        raise ProfileError("Campaign runtime requirements are missing or unsupported")
    totals = {}
    for key, validator in (("sources", _validate_source), ("targets", _validate_target)):
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
    if totals["targets"] < totals["sources"]:
        raise ProfileError("Campaign target capacity cannot hold its selected reward records")
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
    if plan["registrations"] or any(row["kind"] != "physical" for row in bound["sources"]):
        raise ProfileError("Campaign prototype does not support registered script sources")
    if bound["reward_units"] > MAX_REWARD_UNITS:
        raise ProfileError(f"Campaign reward units exceed prototype limit {MAX_REWARD_UNITS}")
    # Reject oversized explicit choices even when an optional target is absent.
    if (sum(row["capacity"] for row in plan["targets"]) > MAX_TARGET_CAPACITY
            or bound["target_capacity"] > MAX_TARGET_CAPACITY):
        raise ProfileError(f"Campaign target capacity exceeds prototype limit {MAX_TARGET_CAPACITY}")
    body = {"schema": CAMPAIGN_SCHEMA, "state": STATE,
            "bound_revision": bound["fingerprint"], "profile_id": bound["profile"]["id"],
            "world_fingerprint": bound["world_fingerprint"], "sources": [], "targets": [],
            "runtime_requirements": deepcopy(bound["runtime_requirements"])}
    for selected in bound["sources"]:
        source = selected["source"]
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
