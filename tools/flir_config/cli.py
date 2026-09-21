"""JSON command interface for a future collection UI and standalone tooling."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .profiles import (ProfileError, edit_profile, ensure_output_outside_game,
                       new_profile, prepare_profile, read_json, read_prepared,
                       rebase_profile, search_items, validate_catalog,
                       validate_profile, write_json)
from .resources import ScanError, scan_game


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Scan installed EE items and author draft Randomiser profiles. "
                    "This tool does not modify the game or activate randomisation.")
    commands = result.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="Read the effective installed item resources")
    scan.add_argument("--game", type=Path, required=True)
    scan.add_argument("--language")
    scan.add_argument("--out", type=Path, required=True)
    search = commands.add_parser("search", help="Search names or resource IDs without listing everything")
    search.add_argument("--catalog", type=Path, required=True)
    search.add_argument("--query", default="")
    search.add_argument("--category", type=int)
    search.add_argument("--offset", type=int, default=0)
    search.add_argument("--limit", type=int, default=20)
    profile = commands.add_parser("profile", help="Create, edit or validate a draft")
    operations = profile.add_subparsers(dest="operation", required=True)
    create = operations.add_parser("create")
    create.add_argument("--catalog", type=Path, required=True)
    create.add_argument("--id", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--out", type=Path, required=True)
    for operation in ("edit", "validate", "rebase"):
        child = operations.add_parser(operation)
        child.add_argument("--catalog", type=Path, required=True)
        child.add_argument("--profile", type=Path, required=True)
        if operation != "validate":
            child.add_argument("--out", type=Path, required=True)
        if operation == "edit":
            child.add_argument("--include", action="append", default=[])
            child.add_argument("--exclude", action="append", default=[])
            child.add_argument("--name")
    prepare = commands.add_parser("prepare", help="Freeze a draft revision; does not activate it")
    prepare.add_argument("--catalog", type=Path, required=True)
    prepare.add_argument("--profile", type=Path, required=True)
    prepare.add_argument("--store", type=Path, required=True)
    inspect = commands.add_parser("inspect", help="Read a verified prepared draft revision")
    inspect.add_argument("--store", type=Path, required=True)
    inspect.add_argument("--revision")
    return result


def run(args: argparse.Namespace) -> dict:
    if args.command == "scan":
        # Check before the potentially expensive scan and re-check its canonical root.
        ensure_output_outside_game(args.out, {"game_root": str(args.game.resolve())})
        catalog = scan_game(args.game, language=args.language)
        validate_catalog(catalog)
        destination = ensure_output_outside_game(args.out, catalog)
        write_json(destination, catalog)
        return {"catalog_path": str(destination), "fingerprint": catalog["fingerprint"],
                "item_count": len(catalog["items"]), "warnings": catalog.get("warnings", [])}
    if args.command == "inspect":
        return read_prepared(args.store, args.revision)
    catalog = read_json(args.catalog)
    validate_catalog(catalog)
    if args.command == "search":
        return search_items(catalog, args.query, category=args.category,
                            offset=args.offset, limit=args.limit)
    if args.command == "prepare":
        return prepare_profile(read_json(args.profile), catalog, args.store)
    if args.operation == "create":
        profile = new_profile(catalog, args.id, args.name)
    else:
        profile = read_json(args.profile)
        if args.operation == "validate":
            validate_profile(profile, catalog)
            return {"valid": True, "state": "draft-needs-source-binding",
                    "selected_count": len(profile["items"])}
        if args.operation == "rebase":
            profile = rebase_profile(profile, catalog)
        else:
            profile = edit_profile(profile, catalog, include=args.include,
                                   exclude=args.exclude, name=args.name)
    destination = ensure_output_outside_game(args.out, catalog)
    write_json(destination, profile)
    return {"profile_path": str(destination), "selected_count": len(profile["items"])}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = run(args)
    except (ProfileError, ScanError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=True, sort_keys=True))
    return 0
