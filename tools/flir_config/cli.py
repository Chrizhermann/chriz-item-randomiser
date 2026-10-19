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
from .campaigns import read_campaign, write_campaign
from .native_export import export_native
from .templates import list_templates, build_template, POOL_CHOICES
from .sources import (bind_profile, inventory_diagnostic_counts, inspect_sources, new_binding_plan,
                      prepare_bound_profile, read_bound_profile, scan_world,
                      search_targets, unreadable_notes)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Scan installed EE items and author draft Randomiser profiles. "
                    "Game changes require the editor's explicit --apply-game opt-in.")
    commands = result.add_subparsers(dest="command", required=True)
    templates = commands.add_parser("templates", help="List or export original pool drafts; not playable settings yet")
    templates.add_argument("--id", choices=[row["id"] for row in list_templates()])
    templates.add_argument("--items", choices=POOL_CHOICES,
                           help="standard (default) or extended: also the larger mode2 tables")
    templates.add_argument("--targets", choices=POOL_CHOICES,
                           help="standard (default) or extended: also the larger mode2 tables")
    templates.add_argument("--options", type=Path, help="JSON object of original component options")
    templates.add_argument("--out", type=Path)
    ui = commands.add_parser("ui", help="Open the local configuration editor over existing scans")
    for field in ("catalog", "world", "workspace"):
        ui.add_argument("--" + field, type=Path, required=True)
    ui.add_argument("--profile", type=Path)
    ui.add_argument("--plan", type=Path)
    ui.add_argument("--port", type=int, default=0)
    ui.add_argument("--apply-game", type=Path,
                    help="Enable applying settings to this game folder, where CIR is installed")
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
    world = commands.add_parser("world", help="Discover physical sources and candidate targets (contains location information)")
    world_ops = world.add_subparsers(dest="operation", required=True)
    for operation in ("scan", "sources", "targets"):
        child = world_ops.add_parser(operation)
        child.add_argument("--catalog", type=Path, required=True)
        if operation == "scan":
            child.add_argument("--out", type=Path, required=True)
        else:
            child.add_argument("--world", type=Path, required=True)
            child.add_argument("--offset", type=int, default=0)
            child.add_argument("--limit", type=int, default=20)
        if operation == "sources":
            child.add_argument("--item", required=True)
        elif operation == "targets":
            child.add_argument("--query", default="")
            child.add_argument("--kind", choices=("cre-template", "actor", "container", "store"))
    binding = commands.add_parser("binding", help="Author explicit source and target choices; no game activation")
    binding_ops = binding.add_subparsers(dest="operation", required=True)
    for operation in ("create", "validate", "prepare", "inspect"):
        child = binding_ops.add_parser(operation)
        if operation == "inspect":
            child.add_argument("--store", type=Path, required=True)
            child.add_argument("--revision")
            continue
        child.add_argument("--catalog", type=Path, required=True)
        child.add_argument("--world", type=Path, required=True)
        child.add_argument("--profile", type=Path, required=True)
        if operation == "create":
            child.add_argument("--out", type=Path, required=True)
        else:
            child.add_argument("--plan", type=Path, required=True)
        if operation == "prepare":
            child.add_argument("--store", type=Path, required=True)
    prototype = commands.add_parser("prototype", help="Compile experimental campaign data; never activates gameplay")
    prototype_ops = prototype.add_subparsers(dest="operation", required=True)
    for operation in ("prepare", "inspect", "export"):
        child = prototype_ops.add_parser(operation)
        child.add_argument("--store", type=Path, required=True)
        if operation == "inspect":
            child.add_argument("--revision")
        elif operation == "export":
            child.add_argument("--catalog", type=Path, required=True)
            child.add_argument("--world", type=Path, required=True)
            child.add_argument("--revision")
            child.add_argument("--out", type=Path, required=True)
        else:
            for field in ("catalog", "world", "profile", "plan"):
                child.add_argument("--" + field, type=Path, required=True)
    return result


def run(args: argparse.Namespace) -> dict:
    if args.command == "templates":
        if args.id is None:
            return {"templates": list_templates()}
        try:
            draft = build_template(args.id, items=args.items, targets=args.targets,
                                   options=read_json(args.options) if args.options else None)
        except ValueError as exc:
            raise ProfileError(str(exc)) from exc
        if args.out:
            write_json(args.out, draft)
            return {"path": str(args.out), "state": draft["state"], "counts": draft["counts"]}
        return draft
    if args.command == "ui":
        from .editor import serve
        return serve(args.catalog, args.world, args.workspace, args.port, args.profile, args.plan,
                     args.apply_game)
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
    if args.command == "binding" and args.operation == "inspect":
        return read_bound_profile(args.store, args.revision)
    if args.command == "prototype" and args.operation == "inspect":
        return read_campaign(args.store, args.revision)
    catalog = read_json(args.catalog)
    validate_catalog(catalog)
    if args.command == "prototype":
        if args.operation == "export":
            return export_native(args.store, catalog, read_json(args.world), args.out, args.revision)
        return write_campaign(read_json(args.profile), catalog, read_json(args.world), read_json(args.plan), args.store)
    if args.command == "world":
        if args.operation == "scan":
            destination = ensure_output_outside_game(args.out, catalog)
            world = scan_world(catalog)
            write_json(destination, world)
            result = {"world_path": str(destination), "fingerprint": world["fingerprint"],
                      "source_records": len(world["sources"]), "target_candidates": len(world["targets"]),
                      "empty_item_records": sum("empty-item-reference" in row["concerns"]
                                                for row in world["sources"]),
                      **inventory_diagnostic_counts(world)}
            # Game files the scan could not read and left out, as the editor's notes name them.
            if "unreadable" in world:
                result["warnings"] = unreadable_notes(world)
            return result
        world = read_json(args.world)
        if args.operation == "sources":
            return inspect_sources(world, catalog, args.item, offset=args.offset, limit=args.limit)
        return search_targets(world, catalog, args.query, kind=args.kind, offset=args.offset, limit=args.limit)
    if args.command == "binding":
        world, profile = read_json(args.world), read_json(args.profile)
        if args.operation == "create":
            plan = new_binding_plan(profile, catalog, world)
            destination = ensure_output_outside_game(args.out, catalog)
            write_json(destination, plan)
            return {"plan_path": str(destination), "state": "needs-explicit-source-and-target-choices"}
        plan = read_json(args.plan)
        if args.operation == "prepare":
            return prepare_bound_profile(profile, catalog, world, plan, args.store)
        body = bind_profile(profile, catalog, world, plan)
        return {"valid": True, "state": body["state"], "reward_units": body["reward_units"],
                "target_capacity": body["target_capacity"], "skipped": body["skipped"]}
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
