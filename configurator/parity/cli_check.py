"""Python/Rust comparison of the command line on a synthetic installation.

Builds a small game with the reference tests' builders in a temporary folder,
runs the same commands with the reference CLI (tools/randomiser_config.py) and
the Rust executable, and compares exit codes, printed results and every file
written: scan, world, search, profiles, draft and binding revisions, presets,
campaign prototypes and the native export.

    python configurator/parity/cli_check.py
"""
from __future__ import annotations

import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

import common
from common import ROOT, RUST_CLI

from test_postinstall_resources import make_game, make_itm  # noqa: E402
from test_postinstall_world_formats import item_record, make_are, make_cre, make_sto  # noqa: E402

PYTHON = [sys.executable, str(ROOT / "tools" / "randomiser_config.py")]
RUST = [str(RUST_CLI)]
SOURCE = "are:area/container/0/item/0"
TARGET = "are:area/actor/1"


def build(game: Path) -> None:
    make_game(game)
    override = game / "override"
    # A visible, droppable item, so the native export has a complete path.
    item_data = bytearray(make_itm())
    struct.pack_into("<I", item_data, 0x18, 0x4C)
    (override / "synth001.itm").write_bytes(item_data)
    item = item_record("SYNTH001", expiration=0, charges=(0, 0, 0), flags=1)
    payloads = {
        "actor.cre": make_cre([item, item_record(charges=(2, 9, 0), flags=4)]),
        "area.are": make_are([item], [("Chest", 0, 1)],
                             [("External", "ACTOR"), ("Embedded", make_cre([item]))]),
        "shop.sto": make_sto([(item, 3, 0)]),
        "grant.bcs": b"SC\nsynthetic grant\nSC\n",
        "grant.dlg": b"DLG V1.0synthetic grant",
    }
    for name, data in payloads.items():
        (override / name).write_bytes(data)


def bind_plan(out: Path) -> None:
    """Choose the chest's item for the embedded actor, the same on both sides."""
    path = out / "plan.json"
    plan = json.loads(path.read_text(encoding="utf-8"))
    plan["sources"] = [{"item": "synth001", "policy": "relocate", "source_ids": [SOURCE], "registration_ids": []}]
    plan["targets"] = [{"id": TARGET, "capacity": 1, "optional": False}]
    path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    (out / "options.json").write_text(json.dumps({"scrolls": True, "loss_mode": "some", "loss_percent": 20}),
                                      encoding="utf-8")


STEPS = [
    ["scan", "--game", "{game}", "--out", "{out}/catalog.json"],
    ["world", "scan", "--catalog", "{out}/catalog.json", "--out", "{out}/world.json"],
    ["search", "--catalog", "{out}/catalog.json", "--query", "blade"],
    ["search", "--catalog", "{out}/catalog.json", "--query", ""],
    ["search", "--catalog", "{out}/catalog.json", "--query", "synth", "--category", "1", "--limit", "1"],
    ["search", "--catalog", "{out}/catalog.json", "--query", "synth", "--offset", "-1"],
    ["profile", "create", "--catalog", "{out}/catalog.json", "--id", "test", "--name", "Test adventure",
     "--out", "{out}/profile.json"],
    ["profile", "create", "--catalog", "{out}/catalog.json", "--id", "Not Valid", "--name", "x",
     "--out", "{out}/invalid.json"],
    ["profile", "edit", "--catalog", "{out}/catalog.json", "--profile", "{out}/profile.json",
     "--include", "SYNTH001", "--out", "{out}/profile.json"],
    ["profile", "edit", "--catalog", "{out}/catalog.json", "--profile", "{out}/profile.json",
     "--include", "nosuch", "--out", "{out}/unknown.json"],
    ["profile", "validate", "--catalog", "{out}/catalog.json", "--profile", "{out}/profile.json"],
    ["prepare", "--catalog", "{out}/catalog.json", "--profile", "{out}/profile.json", "--store", "{out}/prepared"],
    ["inspect", "--store", "{out}/prepared"],
    ["world", "sources", "--catalog", "{out}/catalog.json", "--world", "{out}/world.json", "--item", "synth001"],
    ["world", "targets", "--catalog", "{out}/catalog.json", "--world", "{out}/world.json", "--query", "chest"],
    ["world", "targets", "--catalog", "{out}/catalog.json", "--world", "{out}/world.json", "--kind", "actor",
     "--limit", "1", "--offset", "1"],
    ["binding", "create", "--catalog", "{out}/catalog.json", "--world", "{out}/world.json",
     "--profile", "{out}/profile.json", "--out", "{out}/plan.json"],
    "bind",
    ["binding", "validate", "--catalog", "{out}/catalog.json", "--world", "{out}/world.json",
     "--profile", "{out}/profile.json", "--plan", "{out}/plan.json"],
    ["binding", "prepare", "--catalog", "{out}/catalog.json", "--world", "{out}/world.json",
     "--profile", "{out}/profile.json", "--plan", "{out}/plan.json", "--store", "{out}/bound"],
    ["binding", "inspect", "--store", "{out}/bound"],
    ["prototype", "prepare", "--store", "{out}/campaigns", "--catalog", "{out}/catalog.json",
     "--world", "{out}/world.json", "--profile", "{out}/profile.json", "--plan", "{out}/plan.json"],
    ["prototype", "inspect", "--store", "{out}/campaigns"],
    ["prototype", "export", "--store", "{out}/campaigns", "--catalog", "{out}/catalog.json",
     "--world", "{out}/world.json", "--out", "{out}/native"],
    ["prototype", "export", "--store", "{out}/campaigns", "--catalog", "{out}/catalog.json",
     "--world", "{out}/world.json", "--out", "{out}/native"],
    ["prototype", "export", "--store", "{out}/campaigns", "--catalog", "{out}/catalog.json",
     "--world", "{out}/world.json", "--out", "{game}/inside"],
    ["templates"],
    ["templates", "--id", "eet-boss-loot", "--items", "extended", "--out", "{out}/template.json"],
    ["templates", "--id", "eet-original", "--options", "{out}/options.json", "--out", "{out}/options-template.json"],
    ["templates", "--id", "nosuch"],
    ["inspect", "--store", "{out}/missing"],
    ["search", "--catalog", "{out}/catalog.json", "--limit", "many"],
    ["profile"],
]


def run(command: list[str], args: list[str]) -> tuple[int, str]:
    result = subprocess.run(command + args, capture_output=True)
    return result.returncode, (result.stdout + result.stderr).decode("utf-8", "replace")


def masked(text: str, replacements: dict[str, str]) -> str:
    for old, new in replacements.items():
        for form in (old, json.dumps(old)[1:-1], json.dumps(json.dumps(old)[1:-1])[1:-1]):
            text = text.replace(form, new)
    return text.strip()


def main() -> int:
    common.require_built()
    failures = 0
    with tempfile.TemporaryDirectory(prefix="cir-parity-cli-") as temp:
        root = Path(temp)
        game = root / "game"
        build(game)
        outputs = {"python": root / "py", "rust": root / "rs"}
        for out in outputs.values():
            out.mkdir()
        for step in STEPS:
            if step == "bind":
                for out in outputs.values():
                    bind_plan(out)
                continue
            results = {}
            for name, command in (("python", PYTHON), ("rust", RUST)):
                out = outputs[name]
                args = [part.replace("{out}", str(out)).replace("{game}", str(game)) for part in step]
                code, text = run(command, args)
                results[name] = (code, masked(text, {str(out): "<out>", str(game): "<game>"}))
            if results["python"] != results["rust"]:
                failures += 1
                print("DIFFERENT:", " ".join(step))
                for name, (code, text) in results.items():
                    print(f"  {name} [{code}]: {text[:1500]}")
            else:
                print("same:", " ".join(step), f"[{results['python'][0]}]")
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
    print(f"cli parity: {len(STEPS) - 1} commands, {compared} files compared, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
