"""Python/Rust comparison of the command line on a synthetic installation.

Builds a small game with the reference tests' builders in a temporary folder,
runs the same commands with the reference CLI (tools/randomiser_config.py) and
the Rust executable, and compares exit codes, printed results and every file
written: scan, world, search, profiles, draft and binding revisions, presets,
campaign prototypes and the native export. It also compares the usage and
help text of every command at several terminal widths and argparse's
handling of unusual arguments.

    python configurator/parity/cli_check.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

import common
from common import ROOT, RUST_CLI

from test_postinstall_resources import make_game, make_itm, mark_eet  # noqa: E402
from test_postinstall_world_formats import item_record, make_are, make_cre, make_sto  # noqa: E402

# The reference runs under the executable's name, so usage and help text
# (which wrap around the program name) compare exactly. Like the executable
# built with parity-overrides, it takes the pinned hashes a phase changes from
# the JSON file CIR_PIN_OVERRIDES names ({"module.TABLE": {key: hash}}).
REFERENCE = f"""\
import importlib, json, os, sys
sys.argv[0] = sys.argv[1]
del sys.argv[1]
sys.path.insert(0, {str(ROOT / 'tools')!r})
if os.environ.get("CIR_PIN_OVERRIDES"):
    with open(os.environ["CIR_PIN_OVERRIDES"], encoding="utf-8") as stream:
        for table, entries in json.load(stream).items():
            module, name = table.rsplit(".", 1)
            getattr(importlib.import_module("flir_config." + module), name).update(entries)
from flir_config.cli import main
raise SystemExit(main())
"""
PYTHON = [sys.executable, "-c", REFERENCE, RUST_CLI.name]
RUST = [str(RUST_CLI)]


def sources(area: str = "area") -> tuple[str, str]:
    """The chest's item and the embedded actor of the synthetic area."""
    return f"are:{area}/container/0/item/0", f"are:{area}/actor/1"


def build(game: Path, platform: str = "eet", area: str = "area") -> None:
    """A synthetic game of this platform (as WeiDU's GAME_IS tells them
    apart: EET by its flag, BG2:EE by OH6000, BG:EE by OH1000) with one area
    of this name."""
    make_game(game)
    override = game / "override"
    if platform == "eet":
        mark_eet(game)
    marker = {"bg2ee": "oh6000", "bgee": "oh1000"}.get(platform)
    if marker:
        (override / f"{marker}.are").write_bytes(make_are())
    for name, data in contents(area).items():
        (override / name).write_bytes(data)


def contents(area: str = "area") -> dict[str, bytes]:
    """The override files of a synthetic game: an item, a creature, an area,
    a store, a script and a dialogue."""
    # A visible, droppable item, so the native export has a complete path.
    item_data = bytearray(make_itm())
    struct.pack_into("<I", item_data, 0x18, 0x4C)
    item = item_record("SYNTH001", expiration=0, charges=(0, 0, 0), flags=1)
    # Native targets need actor script names that are unique in their area.
    embedded = bytearray(make_cre([item]))
    embedded[0x280:0x288] = b"EmbedDV\0"
    return {
        "synth001.itm": bytes(item_data),
        "actor.cre": make_cre([item, item_record(charges=(2, 9, 0), flags=4)]),
        f"{area}.are": make_are([item], [("Chest", 0, 1)],
                                [("External", "ACTOR"), ("Embedded", bytes(embedded))]),
        "shop.sto": make_sto([(item, 3, 0)]),
        "grant.bcs": b"SC\nsynthetic grant\nSC\n",
        "grant.dlg": b"DLG V1.0synthetic grant",
    }


def damage(game: Path) -> None:
    """Files another mod left damaged or of another version, which the world
    scan leaves out with a note: an area, a creature and a store."""
    override = game / "override"
    (override / "broken.are").write_bytes(bytes(make_are())[:0x100])
    (override / "badhead.cre").write_bytes(b"CRE V9.9" + bytes(make_cre())[8:])
    (override / "oldshop.sto").write_bytes(b"STORV1.1" + bytes(make_sto())[8:])


def bind_plan(out: Path) -> None:
    """Choose the chest's item for the embedded actor, the same on both sides."""
    path = out / "plan.json"
    plan = json.loads(path.read_text(encoding="utf-8"))
    source, target = sources()
    plan["sources"] = [{"item": "synth001", "policy": "relocate", "source_ids": [source], "registration_ids": []}]
    plan["targets"] = [{"id": target, "capacity": 1, "optional": False}]
    path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    (out / "options.json").write_text(json.dumps({"scrolls": True, "loss_mode": "some", "loss_percent": 20}),
                                      encoding="utf-8")


CATALOG = ["--catalog", "{out}/catalog.json"]
WORKFLOW = [
    ["scan", "--game", "{game}", "--out", "{out}/catalog.json"],
    ["world", "scan", *CATALOG, "--out", "{out}/world.json"],
    ["search", *CATALOG, "--query", "blade"],
    ["search", *CATALOG, "--query", ""],
    ["search", *CATALOG, "--query", "synth", "--category", "1", "--limit", "1"],
    ["search", *CATALOG, "--query", "synth", "--offset", "-1"],
    ["profile", "create", *CATALOG, "--id", "test", "--name", "Test adventure", "--out", "{out}/profile.json"],
    ["profile", "create", *CATALOG, "--id", "Not Valid", "--name", "x", "--out", "{out}/invalid.json"],
    ["profile", "edit", *CATALOG, "--profile", "{out}/profile.json", "--include", "SYNTH001",
     "--out", "{out}/profile.json"],
    ["profile", "edit", *CATALOG, "--profile", "{out}/profile.json", "--include", "nosuch",
     "--out", "{out}/unknown.json"],
    ["profile", "validate", *CATALOG, "--profile", "{out}/profile.json"],
    ["prepare", *CATALOG, "--profile", "{out}/profile.json", "--store", "{out}/prepared"],
    ["inspect", "--store", "{out}/prepared"],
    ["world", "sources", *CATALOG, "--world", "{out}/world.json", "--item", "synth001"],
    ["world", "targets", *CATALOG, "--world", "{out}/world.json", "--query", "chest"],
    ["world", "targets", *CATALOG, "--world", "{out}/world.json", "--kind", "actor", "--limit", "1", "--offset", "1"],
    ["binding", "create", *CATALOG, "--world", "{out}/world.json", "--profile", "{out}/profile.json",
     "--out", "{out}/plan.json"],
    "bind",
    ["binding", "validate", *CATALOG, "--world", "{out}/world.json", "--profile", "{out}/profile.json",
     "--plan", "{out}/plan.json"],
    ["binding", "prepare", *CATALOG, "--world", "{out}/world.json", "--profile", "{out}/profile.json",
     "--plan", "{out}/plan.json", "--store", "{out}/bound"],
    ["binding", "inspect", "--store", "{out}/bound"],
    ["prototype", "prepare", "--store", "{out}/campaigns", *CATALOG, "--world", "{out}/world.json",
     "--profile", "{out}/profile.json", "--plan", "{out}/plan.json"],
    ["prototype", "inspect", "--store", "{out}/campaigns"],
    ["prototype", "export", "--store", "{out}/campaigns", *CATALOG, "--world", "{out}/world.json",
     "--out", "{out}/native"],
    ["prototype", "export", "--store", "{out}/campaigns", *CATALOG, "--world", "{out}/world.json",
     "--out", "{out}/native"],
    ["prototype", "export", "--store", "{out}/campaigns", *CATALOG, "--world", "{out}/world.json",
     "--revision", "0" * 64, "--out", "{out}/other"],
    ["prototype", "export", "--store", "{out}/campaigns", *CATALOG, "--world", "{out}/world.json",
     "--out", "{game}/inside"],
    "damage",
    ["world", "scan", *CATALOG, "--out", "{out}/damaged-world.json"],
    ["world", "targets", *CATALOG, "--world", "{out}/damaged-world.json", "--query", "chest"],
    ["prototype", "export", "--store", "{out}/campaigns", *CATALOG, "--world", "{out}/damaged-world.json",
     "--out", "{out}/damaged-native"],
    ["templates"],
    ["templates", "--id", "eet-boss-loot", "--items", "extended", "--out", "{out}/template.json"],
    ["templates", "--id", "eet-original", "--options", "{out}/options.json", "--out", "{out}/options-template.json"],
    ["inspect", "--store", "{out}/missing"],
]

# argparse's handling of unusual arguments: abbreviations, `--`, explicit
# values, integers as Python's int() reads them, unknown arguments. Without
# any argument the executable opens the editor for its own game instead of
# printing usage (player_check.py).
ARGUMENTS = [
    ["nosuch"],
    ["profile"],
    ["profile", "nosuch"],
    ["templates", "--id", "nosuch"],
    ["templates", "--id"],
    ["templates", "--id="],
    ["templates", "--i", "x"],
    ["templates", "--i=x"],
    ["templates", "--o", "x"],
    ["templates", "--it", "extended", "--ta=standard"],
    ["-hfoo"],
    ["-h=foo"],
    ["-h-x"],
    ["--he"],
    ["search", "--he"],
    ["search", "-hh"],
    ["search", *CATALOG, "--limit", "many"],
    ["search", *CATALOG, "--limit=--"],
    ["search", *CATALOG, "--query", "-x"],
    ["search", *CATALOG, "--query=-x", "--limit", "3"],
    ["search", *CATALOG, "--query", "-x y"],
    ["search", *CATALOG, "extra", "more"],
    ["--bogus", "search", *CATALOG],
    ["search", "--", *CATALOG],
    ["--", "search"],
    ["search", *CATALOG, "--limit", " 5 "],
    ["search", *CATALOG, "--limit", "1_0"],
    ["search", *CATALOG, "--limit", "1__0"],
    ["search", *CATALOG, "--limit", "١٢"],
    ["search", *CATALOG, "--limit", "\x1c5"],
    ["search", *CATALOG, "--offset", "99999999999999999999999"],
    ["search", *CATALOG, "--limit", "1" * 4301],
    ["search", *CATALOG, "--limit", "1" * 4301 + "x"],
    ["search", *CATALOG, "-x"],
    ["search", *CATALOG, "-5"],
    ["search", "-c", "x"],
    ["search", "--catalog"],
    ["world", "targets", *CATALOG, "--world", "{out}/world.json", "--kind", "bad"],
]

COMMAND_PATHS = [[], ["templates"], ["scan"], ["search"], ["profile"], ["profile", "create"], ["profile", "edit"],
                 ["profile", "validate"], ["profile", "rebase"], ["prepare"], ["inspect"], ["world"],
                 ["world", "scan"], ["world", "sources"], ["world", "targets"], ["binding"],
                 ["binding", "create"], ["binding", "validate"], ["binding", "prepare"], ["binding", "inspect"],
                 ["prototype"], ["prototype", "prepare"], ["prototype", "inspect"], ["prototype", "export"]]
# `ui` is left out: the executable adds --no-browser, which shows in its usage.
HELP = ([(path + ["-h"], columns) for path in COMMAND_PATHS for columns in ("80", "40", "200", "12")] +
        [(path + ["--help"], "80") for path in ([], ["templates"], ["world", "targets"])] +
        [(["search"], columns) for columns in ("0", "abc", "-3", "٢٠")])


def run(command: list[str], args: list[str], columns: str) -> tuple[int, str]:
    environment = dict(os.environ, COLUMNS=columns)
    result = subprocess.run(command + args, capture_output=True, env=environment)
    return result.returncode, (result.stdout + result.stderr).decode("utf-8", "replace")


def masked(text: str, replacements: dict[str, str]) -> str:
    for old, new in replacements.items():
        for form in (old, json.dumps(old)[1:-1], json.dumps(json.dumps(old)[1:-1])[1:-1]):
            text = text.replace(form, new)
    return text.strip()


def main() -> int:
    common.require_built()
    sys.stdout.reconfigure(errors="backslashreplace")
    failures = 0
    commands = 0
    with tempfile.TemporaryDirectory(prefix="cir-parity-cli-") as temp:
        root = Path(temp)
        game = root / "game"
        build(game)
        outputs = {"python": root / "py", "rust": root / "rs"}
        for out in outputs.values():
            out.mkdir()
        steps = [(step, "80") for step in WORKFLOW] + [(step, "80") for step in ARGUMENTS] + HELP
        for step, columns in steps:
            if step == "bind":
                for out in outputs.values():
                    bind_plan(out)
                continue
            if step == "damage":
                damage(game)
                continue
            commands += 1
            results = {}
            for name, command in (("python", PYTHON), ("rust", RUST)):
                out = outputs[name]
                args = [part.replace("{out}", str(out)).replace("{game}", str(game)) for part in step]
                code, text = run(command, args, columns)
                results[name] = (code, masked(text, {str(out): "<out>", str(game): "<game>"}))
            label = " ".join(step)[:200] + ("" if columns == "80" else f" (COLUMNS={columns})")
            if results["python"] != results["rust"]:
                failures += 1
                print("DIFFERENT:", label)
                for name, (code, text) in results.items():
                    print(f"  {name} [{code}]: {text[:1500]}")
            else:
                print("same:", label, f"[{results['python'][0]}]")
        for path in sorted(outputs["python"].rglob("*")):
            if path.is_file():
                relative = path.relative_to(outputs["python"])
                other = outputs["rust"] / relative
                if not other.is_file():
                    failures += 1
                    print("MISSING in rust:", relative)
                elif other.read_bytes() != path.read_bytes():
                    failures += 1
                    print("FILE DIFFERS:", relative)
        for path in sorted(outputs["rust"].rglob("*")):
            if path.is_file() and not (outputs["python"] / path.relative_to(outputs["rust"])).is_file():
                failures += 1
                print("ONLY in rust:", path.relative_to(outputs["rust"]))
        compared = sum(1 for path in outputs["python"].rglob("*") if path.is_file())
    print(f"cli parity: {commands} commands, {compared} files compared, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
