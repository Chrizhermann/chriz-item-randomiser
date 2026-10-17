"""Python/Rust comparison on a real installation, used read-only.

Scans the game with the reference CLI and the executable and compares the
item catalog and the world, then compares, for every preset and pool and a
few option sets: the fitted preset (with its left-out rows), the unfitted
build and the rows the native check refuses, the compiled campaign, the
native compilation, and for some of them the campaign store and the native
export written into the scratch folder.

    python configurator/parity/game_check.py --game <game folder> --scratch <empty folder>

The game folder is only read. Everything is written below --scratch, which
must be outside the game; the export holds copies of game resources, so keep
the scratch folder out of the repository.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import common
from common import RUST_CLI, RustRunner, first_difference, outcome_line, snapshot_tree, tree_differences
from cli_check import PYTHON

from flir_config.campaigns import compile_campaign, write_campaign
from flir_config.native_export import _compile, export_native
from flir_config.original_configuration import build_original_configuration, fit_original_configuration
from flir_config.profiles import read_json, validate_catalog
from flir_config.sources import validate_world
from flir_config.templates import list_templates

POOLS = ("standard", "extended")
OPTION_SETS = {
    "default": None,
    "recommended": {"flail_heads": True, "totsc_tome": True},
    "everything": {"scrolls": True, "kangaxx": True, "flail_heads": True, "totsc_tome": True,
                   "more_spell_shield": True, "cursed_disguise": True, "loss_mode": "some",
                   "loss_percent": 30, "unrandomized_percent": 25},
}


def cases() -> list[tuple[str, str, str, str]]:
    """(template, items, targets, options): every pool pair with the default
    options, the matching pairs with the other option sets."""
    result = []
    for template in (row["id"] for row in list_templates()):
        for items in POOLS:
            for targets in POOLS:
                result.append((template, items, targets, "default"))
        for options in ("recommended", "everything"):
            for pool in POOLS:
                result.append((template, pool, pool, options))
    return result


def python_outcome(function, *args, **kwargs) -> tuple[str, object]:
    try:
        result = function(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - every error class is compared
        return outcome_line(exc=exc), None
    return outcome_line(result), result


def native_compile(manifest, world, catalog, collect: bool) -> dict:
    items, index = validate_catalog(catalog), validate_world(world, catalog)
    problems = {} if collect else None
    native, required, pristine = _compile(manifest, world, index, items, problems)
    return {"native": native, "required": sorted(required), "pristine": sorted(pristine),
            "problems": dict(sorted(problems.items())) if collect else None}


class Check:
    def __init__(self, scratch: Path):
        self.scratch = scratch
        self.runner = RustRunner(scratch / "rust-calls")
        self.failures = 0
        self.compared = 0

    def compare(self, label: str, python_line: str, rust_line: str, masks=()) -> bool:
        self.compared += 1
        for old, new in masks:
            for form in (old, json.dumps(old)[1:-1], json.dumps(json.dumps(old)[1:-1])[1:-1]):
                python_line = python_line.replace(form, new)
                rust_line = rust_line.replace(form, new)
        if python_line == rust_line:
            print(f"same: {label}", flush=True)
            return True
        self.failures += 1
        print(f"DIFFERENT: {label}\n  {first_difference(python_line, rust_line)}", flush=True)
        return False

    def cli(self, label: str, args: list[str], python_out: Path, rust_out: Path) -> None:
        results = []
        for command, out in ((PYTHON, python_out), ([str(RUST_CLI)], rust_out)):
            started = time.monotonic()
            completed = subprocess.run(command + [part.replace("{out}", str(out)) for part in args],
                                       capture_output=True)
            text = (completed.stdout + completed.stderr).decode("utf-8", "replace")
            for form in (str(out), json.dumps(str(out))[1:-1], json.dumps(json.dumps(str(out))[1:-1])[1:-1]):
                text = text.replace(form, "<out>")
            results.append((completed.returncode, text, time.monotonic() - started))
        print(f"  {label}: python {results[0][2]:.1f}s, rust {results[1][2]:.1f}s", flush=True)
        self.compare(f"cli {label}", f"{results[0][0]} {results[0][1]}", f"{results[1][0]} {results[1][1]}")
        if results[0][0] != 0:
            raise SystemExit(f"The reference failed ({label}): {results[0][1]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--game", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--language", help="the game language folder, such as en_US")
    parser.add_argument("--template", action="append", help="limit to these template ids")
    parser.add_argument("--exports", type=int, default=2, help="store and export comparisons per template")
    options = parser.parse_args()
    common.require_built()
    sys.stdout.reconfigure(errors="backslashreplace")
    game, scratch = options.game.resolve(), options.scratch.resolve()
    if scratch == game or game in scratch.parents:
        raise SystemExit("--scratch must be outside the game folder")
    scratch.mkdir(parents=True, exist_ok=True)
    if any(scratch.iterdir()):
        raise SystemExit("--scratch must be empty")
    check = Check(scratch)
    sides = {"python": scratch / "py", "rust": scratch / "rs"}
    for folder in sides.values():
        folder.mkdir()

    language = ["--language", options.language] if options.language else []
    check.cli("scan", ["scan", "--game", str(game), *language, "--out", "{out}/catalog.json"],
              sides["python"], sides["rust"])
    check.cli("world scan", ["world", "scan", "--catalog", "{out}/catalog.json", "--out", "{out}/world.json"],
              sides["python"], sides["rust"])
    for name in ("catalog.json", "world.json"):
        same = (sides["python"] / name).read_bytes() == (sides["rust"] / name).read_bytes()
        check.compared += 1
        if not same:
            check.failures += 1
        print(f"{'same' if same else 'DIFFERENT'}: {name} bytes ({(sides['python'] / name).stat().st_size} bytes)",
              flush=True)
    catalog_path, world_path = sides["python"] / "catalog.json", sides["python"] / "world.json"
    catalog, world = read_json(catalog_path), read_json(world_path)
    print(f"catalog: {len(catalog['items'])} items; world: {len(world['sources'])} sources, "
          f"{len(world['targets'])} targets", flush=True)
    shared = {"catalog": {"$file": str(catalog_path)}, "world": {"$file": str(world_path)}}

    exports_left: dict[str, int] = {}
    for template, items, targets, option_set in cases():
        if options.template and template not in options.template:
            continue
        label = f"{template} items={items} targets={targets} options={option_set}"
        option_value = OPTION_SETS[option_set]
        started = time.monotonic()
        fit_line, fitted = python_outcome(fit_original_configuration, catalog, world, template,
                                          items=items, targets=targets, options=option_value)
        python_time = time.monotonic() - started
        calls = [{"call": "fit_original_configuration",
                  "args": {**shared, "template_id": template, "items": items, "targets": targets,
                           "options": option_value}}]
        python_lines = [fit_line]
        if option_set == "default" and items == targets:
            build_line, built = python_outcome(build_original_configuration, catalog, world, template,
                                               items=items, targets=targets, options=option_value)
            calls.append({"call": "build_original_configuration",
                          "args": {**shared, "template_id": template, "items": items, "targets": targets}})
            python_lines.append(build_line)
            if built is not None:
                campaign_line, campaign = python_outcome(compile_campaign, built["profile"], catalog, world,
                                                         built["plan"])
                calls.append({"call": "compile_campaign",
                              "args": {**shared, "profile": check.runner.file_argument(built["profile"]),
                                       "plan": check.runner.file_argument(built["plan"])}})
                python_lines.append(campaign_line)
                if campaign is not None:
                    native_line, _ = python_outcome(native_compile, campaign, world, catalog, True)
                    calls.append({"call": "native_compile",
                                  "args": {**shared, "manifest": check.runner.file_argument(campaign),
                                           "problems": True}})
                    python_lines.append(native_line)
        if fitted is not None:
            campaign_line, campaign = python_outcome(compile_campaign, fitted["profile"], catalog, world,
                                                     fitted["plan"])
            calls.append({"call": "compile_campaign",
                          "args": {**shared, "profile": check.runner.file_argument(fitted["profile"]),
                                   "plan": check.runner.file_argument(fitted["plan"])}})
            python_lines.append(campaign_line)
            if campaign is not None:
                native_line, _ = python_outcome(native_compile, campaign, world, catalog, False)
                calls.append({"call": "native_compile",
                              "args": {**shared, "manifest": check.runner.file_argument(campaign)}})
                python_lines.append(native_line)
        started = time.monotonic()
        rust_lines = check.runner.run(calls)
        rust_time = time.monotonic() - started
        summary = ""
        if fitted is not None:
            summary = (f" ({len(fitted['profile']['items'])} items, {len(fitted['left_out'])} left out, "
                       f"{len(fitted['not_used'])} not used)")
        print(f"{label}{summary}: python {python_time:.1f}s, rust {rust_time:.1f}s (all calls)", flush=True)
        for call, python_line, rust_line in zip(calls, python_lines, rust_lines):
            check.compare(f"{label} {call['call']}", python_line, rust_line)

        # The campaign store and the native export, written into scratch.
        if fitted is None or exports_left.setdefault(template, options.exports) <= 0:
            continue
        exports_left[template] -= 1
        case = scratch / "export-case"
        lines = {}
        for side in ("rs", "py"):
            store, output = case / side / "store", case / side / "export"
            if side == "rs":
                lines[side] = check.runner.run([
                    {"call": "write_campaign", "args": {**shared, "profile": check.runner.file_argument(fitted["profile"]),
                                                        "plan": check.runner.file_argument(fitted["plan"]),
                                                        "store": str(store)}},
                    {"call": "export_native", "args": {**shared, "store": str(store), "output": str(output)}}])
            else:
                written, _ = python_outcome(write_campaign, fitted["profile"], catalog, world, fitted["plan"], store)
                exported, _ = python_outcome(export_native, store, catalog, world, output)
                lines[side] = [written, exported]
        masks = [(str(case / "py"), "<case>"), (str(case / "rs"), "<case>")]
        for name, python_line, rust_line in zip(("write_campaign", "export_native"), lines["py"], lines["rs"]):
            check.compare(f"{label} {name}", python_line, rust_line, masks)
        for folder in ("store", "export"):
            differences = tree_differences(snapshot_tree(case / "py" / folder), snapshot_tree(case / "rs" / folder))
            check.compared += 1
            if differences:
                check.failures += 1
                print(f"DIFFERENT: {label} {folder} tree: " + "; ".join(differences[:10]), flush=True)
            else:
                count = sum(1 for path in (case / "py" / folder).rglob("*") if path.is_file())
                print(f"same: {label} {folder} tree ({count} files)", flush=True)
        shutil.rmtree(case)

    print(f"game parity: {check.compared} comparisons, {check.failures} failures")
    return 1 if check.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
