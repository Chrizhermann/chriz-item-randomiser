"""Validated draft profiles and immutable preparation records.

Preparation is deliberately not runtime activation. Source binding and the
campaign adapter must establish relocation safety before a draft can run.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from .resources import ITEM_SEMANTIC_FIELDS, catalog_fingerprint

PROFILE_SCHEMA = "flir-profile-v1"
PREPARED_SCHEMA = "flir-prepared-profile-v1"
HEX256 = re.compile(r"^[0-9a-f]{64}$")
PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
PROFILE_FIELDS = {"schema", "id", "name", "catalog_fingerprint", "items"}


class ProfileError(ValueError):
    """Invalid, stale, or conflicting configuration data."""


def canonical_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                           allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise ProfileError("Configuration cannot be represented as finite UTF-8 JSON") from exc


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProfileError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def read_json(path: Path) -> dict:
    def reject_constant(value: str) -> None:
        raise ProfileError(f"Non-finite JSON number: {value}")

    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ProfileError("JSON number is outside the finite range")
        return number

    try:
        result = json.loads(path.read_text(encoding="utf-8-sig"),
                            object_pairs_hook=_unique_pairs,
                            parse_constant=reject_constant, parse_float=finite_float)
    except ProfileError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ProfileError(f"Invalid JSON in {path.name}: {exc}") from exc
    if not isinstance(result, dict):
        raise ProfileError("Expected a JSON object")
    pending = [result]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
        elif isinstance(current, str):
            try:
                current.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ProfileError("JSON strings must be valid Unicode") from exc
    return result


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def validate_catalog(catalog: dict) -> dict[str, dict]:
    if catalog.get("schema") != "flir-item-catalog-v1":
        raise ProfileError("Unsupported item catalogue schema")
    fingerprint = catalog.get("fingerprint")
    if not isinstance(fingerprint, str) or not HEX256.fullmatch(fingerprint):
        raise ProfileError("Invalid item catalogue fingerprint")
    if not isinstance(catalog.get("game_root"), str) or not Path(catalog["game_root"]).is_absolute():
        raise ProfileError("Catalogue must name an absolute game root")
    if not isinstance(catalog.get("language"), str) or not catalog["language"]:
        raise ProfileError("Catalogue language is missing")
    if not isinstance(catalog.get("items"), list):
        raise ProfileError("Catalogue items must be a list")
    index = {}
    for row in catalog["items"]:
        if not isinstance(row, dict):
            raise ProfileError("Invalid catalogue item")
        resref = row.get("resref")
        if (not isinstance(resref, str) or not 1 <= len(resref) <= 8 or
                resref != resref.lower() or
                any(ord(c) < 33 or ord(c) > 126 or c in '/\\:*?"<>|' for c in resref) or
                resref in index):
            raise ProfileError("Invalid or duplicate catalogue item identity")
        if not isinstance(row.get("name"), str) or not row["name"]:
            raise ProfileError(f"Missing item name: {resref}")
        for field in ("unidentified_name", "identified_name"):
            if row.get(field) is not None and not isinstance(row[field], str):
                raise ProfileError(f"Invalid item name field: {resref}")
        for field in ("category", "flags", "price", "stack_size", "enchantment", "proficiency"):
            value = row.get(field)
            if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
                raise ProfileError(f"Invalid item {field}: {resref}")
        if not isinstance(row.get("sha256"), str) or not HEX256.fullmatch(row["sha256"]):
            raise ProfileError(f"Invalid item fingerprint: {resref}")
        if row.get("resource_origin") not in ("override", "biff"):
            raise ProfileError(f"Invalid resource origin: {resref}")
        index[resref] = row
    if list(index) != sorted(index):
        raise ProfileError("Catalogue items must be ordered by resource identity")
    if fingerprint != catalog_fingerprint(catalog):
        raise ProfileError("Catalogue content changed; scan the installation again")
    return index


def validate_profile(profile: dict, catalog: dict, *, allow_rebase: bool = False) -> dict:
    index = validate_catalog(catalog)
    if set(profile) != PROFILE_FIELDS or profile.get("schema") != PROFILE_SCHEMA:
        raise ProfileError("Unsupported profile schema or fields")
    if not isinstance(profile["id"], str) or not PROFILE_ID.fullmatch(profile["id"]):
        raise ProfileError("Profile ID must use 1-64 lowercase letters, digits, hyphens or underscores")
    if not isinstance(profile["name"], str) or not profile["name"].strip() or len(profile["name"]) > 120:
        raise ProfileError("Profile name must contain 1-120 characters")
    try:
        profile["name"].encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ProfileError("Profile name must be valid Unicode") from exc
    if any(ord(character) < 32 for character in profile["name"]):
        raise ProfileError("Profile name must not contain control characters")
    fingerprint = profile["catalog_fingerprint"]
    if not isinstance(fingerprint, str) or not HEX256.fullmatch(fingerprint):
        raise ProfileError("Invalid profile catalogue fingerprint")
    if not allow_rebase and fingerprint != catalog["fingerprint"]:
        raise ProfileError("Profile belongs to another scan; explicitly rebase it before preparing")
    if not isinstance(profile["items"], list) or any(not isinstance(x, str) for x in profile["items"]):
        raise ProfileError("Profile items must be a list of resource identities")
    if profile["items"] != sorted(set(profile["items"])):
        raise ProfileError("Profile items must be unique and sorted")
    missing = [x for x in profile["items"] if x not in index]
    if missing:
        raise ProfileError("Selected items absent from this scan: " + ", ".join(missing))
    return deepcopy(profile)


def new_profile(catalog: dict, profile_id: str, name: str) -> dict:
    profile = {"schema": PROFILE_SCHEMA, "id": profile_id, "name": name,
               "catalog_fingerprint": catalog.get("fingerprint"), "items": []}
    return validate_profile(profile, catalog)


def edit_profile(profile: dict, catalog: dict, *, include: list[str] | None = None,
                 exclude: list[str] | None = None, name: str | None = None) -> dict:
    edited = validate_profile(profile, catalog)
    included = {x.lower() for x in include or []}
    excluded = {x.lower() for x in exclude or []}
    if included & excluded:
        raise ProfileError("An item cannot be included and excluded in the same edit")
    known = {row["resref"] for row in catalog["items"]}
    unknown = (included | excluded) - known
    if unknown:
        raise ProfileError("Unknown item identities: " + ", ".join(sorted(unknown)))
    edited["items"] = sorted((set(edited["items"]) | included) - excluded)
    if name is not None:
        edited["name"] = name
    return validate_profile(edited, catalog)


def rebase_profile(profile: dict, catalog: dict) -> dict:
    rebased = validate_profile(profile, catalog, allow_rebase=True)
    rebased["catalog_fingerprint"] = catalog["fingerprint"]
    return validate_profile(rebased, catalog)


def search_items(catalog: dict, query: str, *, category: int | None = None,
                 offset: int = 0, limit: int = 20) -> dict:
    validate_catalog(catalog)
    query = query.strip().casefold()
    if not query and category is None:
        raise ProfileError("Enter a search term or choose an item category")
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise ProfileError("Search limit must be 1-100 and offset must be nonnegative")
    if category is not None and (type(category) is not int or category < 0):
        raise ProfileError("Category must be a nonnegative number")
    terms = query.split()
    matches = []
    for row in catalog["items"]:
        if category is not None and row["category"] != category:
            continue
        haystack = " ".join(str(row.get(x) or "") for x in
                            ("resref", "name", "identified_name", "unidentified_name")).casefold()
        if all(term in haystack for term in terms):
            matches.append(row)
    page = deepcopy(matches[offset:offset + limit])
    return {"total": len(matches), "offset": offset, "items": page,
            "next_offset": offset + limit if offset + limit < len(matches) else None}


def ensure_output_outside_game(path: Path, catalog: dict) -> Path:
    output = path.resolve()
    game_root = Path(catalog["game_root"]).resolve()
    if output == game_root or game_root in output.parents:
        raise ProfileError("Configuration output must be outside the scanned game directory")
    return output


def write_json(path: Path, value: dict, *, immutable: bool = False) -> None:
    """Publish a complete file; never expose a half-written pointer or revision."""
    data = canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ProfileError("Refusing to replace a linked configuration file")
    if immutable and path.exists():
        if path.read_bytes() != data:
            raise ProfileError("An immutable configuration revision has conflicting content")
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=".flir-", suffix=".tmp",
                                         dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def prepare_profile(profile: dict, catalog: dict, store: Path) -> dict:
    frozen_profile = validate_profile(profile, catalog)
    if not frozen_profile["items"]:
        raise ProfileError("Select at least one item before preparing a draft")
    store = ensure_output_outside_game(store, catalog)
    selected = set(frozen_profile["items"])
    item_rows = [{key: row[key] for key in ITEM_SEMANTIC_FIELDS}
                 for row in catalog["items"] if row["resref"] in selected]
    body = {"schema": PREPARED_SCHEMA, "state": "draft-needs-source-binding",
            "profile": frozen_profile, "items": item_rows}
    revision = _digest(body)
    revision_path = ensure_output_outside_game(store / "revisions" / (revision + ".json"), catalog)
    pointer_path = ensure_output_outside_game(store / "next-draft.json", catalog)
    write_json(revision_path, body, immutable=True)
    pointer = {"schema": "flir-next-draft-v1", "revision": revision}
    write_json(pointer_path, pointer)
    return {"revision": revision, "state": body["state"],
            "revision_path": str(revision_path), "pointer_path": str(pointer_path)}


def read_prepared(store: Path, revision: str | None = None) -> dict:
    if revision is None:
        pointer = read_json(store / "next-draft.json")
        if set(pointer) != {"schema", "revision"} or pointer["schema"] != "flir-next-draft-v1":
            raise ProfileError("Invalid draft pointer")
        revision = pointer["revision"]
    if not isinstance(revision, str) or not HEX256.fullmatch(revision):
        raise ProfileError("Invalid revision identity")
    body = read_json(store / "revisions" / (revision + ".json"))
    if body.get("schema") != PREPARED_SCHEMA or body.get("state") != "draft-needs-source-binding":
        raise ProfileError("Unsupported prepared profile")
    if _digest(body) != revision:
        raise ProfileError("Prepared profile was modified")
    return body
