"""Quick Python/Rust comparison on the source-binding test game.

Builds the synthetic installation of tests/test_postinstall_sources.py in a
temporary folder, runs the same commands with the reference CLI and the Rust
executable, and compares results and written files.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

from test_postinstall_resources import make_game  # noqa: E402
from test_postinstall_world_formats import item_record, make_are, make_cre, make_sto  # noqa: E402

PYTHON = [sys.executable, str(ROOT / "tools" / "randomiser_config.py")]
RUST = [os.environ.get("CIR_CONFIGURATOR", str(ROOT / "configurator" / "target" / "debug" / "cir-configurator.exe"))]


def build(game: Path) -> None:
    make_game(game)
    override = game / "override"
    item = item_record(flags=1)
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


def run(command: list[str], args: list[str]) -> tuple[int, str, str]:
    result = subprocess.run(command + args, capture_output=True)
    return result.returncode, result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace")


def main() -> int:
    failures = 0
    with tempfile.TemporaryDirectory(prefix="cir-parity-") as temp:
        root = Path(temp)
        game = root / "game"
        build(game)
        outputs = {"python": root / "py", "rust": root / "rs"}
        for out in outputs.values():
            out.mkdir()
        steps = [
            ["scan", "--game", str(game), "--out", "{out}/catalog.json"],
            ["world", "scan", "--catalog", "{out}/catalog.json", "--out", "{out}/world.json"],
            ["search", "--catalog", "{out}/catalog.json", "--query", "blade"],
            ["search", "--catalog", "{out}/catalog.json", "--query", ""],
            ["profile", "create", "--catalog", "{out}/catalog.json", "--id", "test", "--name", "Test adventure",
             "--out", "{out}/profile.json"],
            ["profile", "edit", "--catalog", "{out}/catalog.json", "--profile", "{out}/profile.json",
             "--include", "SYNTH001", "--out", "{out}/profile.json"],
            ["profile", "validate", "--catalog", "{out}/catalog.json", "--profile", "{out}/profile.json"],
            ["prepare", "--catalog", "{out}/catalog.json", "--profile", "{out}/profile.json", "--store", "{out}/prepared"],
            ["inspect", "--store", "{out}/prepared"],
            ["world", "sources", "--catalog", "{out}/catalog.json", "--world", "{out}/world.json", "--item", "synth001"],
            ["world", "targets", "--catalog", "{out}/catalog.json", "--world", "{out}/world.json", "--query", "chest"],
            ["world", "targets", "--catalog", "{out}/catalog.json", "--world", "{out}/world.json", "--kind", "actor",
             "--limit", "1", "--offset", "1"],
            ["binding", "create", "--catalog", "{out}/catalog.json", "--world", "{out}/world.json",
             "--profile", "{out}/profile.json", "--out", "{out}/plan.json"],
            ["templates"],
            ["templates", "--id", "eet-boss-loot", "--items", "extended", "--out", "{out}/template.json"],
            ["inspect", "--store", "{out}/missing"],
        ]
        for step in steps:
            results = {}
            for name, command in (("python", PYTHON), ("rust", RUST)):
                out = outputs[name]
                args = [part.replace("{out}", str(out)) for part in step]
                code, stdout, stderr = run(command, args)
                text = (stdout + stderr).replace(str(out), "<out>").replace(json.dumps(str(out))[1:-1], "<out>")
                results[name] = (code, text.strip())
            if results["python"] != results["rust"]:
                failures += 1
                print("DIFFERENT:", " ".join(step))
                for name, (code, text) in results.items():
                    print(f"  {name} [{code}]: {text[:1500]}")
            else:
                print("same:", " ".join(step), f"[{results['python'][0]}]")
        for path in sorted(outputs["python"].rglob("*")):
            if path.is_file():
                other = outputs["rust"] / path.relative_to(outputs["python"])
                if not other.is_file():
                    failures += 1
                    print("MISSING in rust:", path.relative_to(outputs["python"]))
                elif other.read_bytes() != path.read_bytes():
                    failures += 1
                    print("FILE DIFFERS:", path.relative_to(outputs["python"]))
                else:
                    print("same file:", path.relative_to(outputs["python"]))
    print("failures:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
