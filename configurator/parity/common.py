"""Shared helpers for the Python/Rust parity checks.

The reference is tools/flir_config (Python 3.10+, standard library only). The
Rust side is the `parity_call` example of the cir-config crate for library
calls and the cir-configurator executable for the CLI and the editor. Build
both with the test-only pin overrides first:

    cargo build --manifest-path configurator/Cargo.toml --features cir-config/parity-overrides --bins --examples

Each library call yields one JSON line, `{"result": ...}` or
`{"error": {"kind": ..., "message": ...}}`, written like the reference's
`json.dumps(..., ensure_ascii=True)`; the checks compare these lines exactly.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
for _path in (ROOT / "tests", ROOT / "tools", ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

_EXE = ".exe" if os.name == "nt" else ""
TARGET = Path(os.environ.get("CIR_PARITY_TARGET", ROOT / "configurator" / "target" / "debug"))
RUST_CALL = TARGET / "examples" / f"parity_call{_EXE}"
RUST_CLI = TARGET / f"cir-configurator{_EXE}"
BUILD = ("cargo build --manifest-path configurator/Cargo.toml "
         "--features cir-config/parity-overrides --bins --examples")


def require_built() -> None:
    missing = [str(path) for path in (RUST_CALL, RUST_CLI) if not path.is_file()]
    if missing:
        raise SystemExit(f"Build the Rust side first:\n  {BUILD}\nMissing: {', '.join(missing)}")


def error_kind(exc: BaseException) -> str:
    """The Rust error kind name for a reference exception."""
    from flir_config.game_text import GameTextError
    from flir_config.profiles import ProfileError
    from flir_config.resources import ScanError
    for cls, name in ((ProfileError, "ProfileError"), (ScanError, "ScanError"),
                      (GameTextError, "GameTextError"), (OverflowError, "OverflowError"),
                      (KeyError, "KeyError"), (TypeError, "TypeError"), (OSError, "OSError"),
                      (ValueError, "ValueError")):
        if isinstance(exc, cls):
            return name
    return type(exc).__name__


def outcome_line(result=None, exc: BaseException | None = None) -> str:
    if exc is not None:
        return json.dumps({"error": {"kind": error_kind(exc), "message": str(exc)}}, ensure_ascii=True)
    return json.dumps({"result": result}, ensure_ascii=True)


def json_native(value) -> bool:
    """Whether a value survives a JSON round trip unchanged in type."""
    if value is None or isinstance(value, (bool, str)):
        return True
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return value == value and value not in (float("inf"), float("-inf"))
    if type(value) is list:
        return all(json_native(item) for item in value)
    if type(value) is dict:
        return all(type(key) is str and json_native(item) for key, item in value.items())
    return False


class RustRunner:
    """Runs library calls with the Rust port (one process per batch)."""

    def __init__(self, scratch: Path):
        self.scratch = scratch
        self.scratch.mkdir(parents=True, exist_ok=True)
        self.count = 0
        self.files: list[Path] = []

    def file_argument(self, value) -> dict:
        """Pass a JSON value through a file, as the CLI reads its inputs."""
        self.count += 1
        path = self.scratch / f"argument-{self.count}.json"
        path.write_text(json.dumps(value, ensure_ascii=True), encoding="utf-8")
        self.files.append(path)
        return {"$file": str(path)}

    def run(self, calls: list[dict], pins: dict | None = None, registration_draft=None,
            tables: dict | None = None, languages: str | None = None, lists: str | None = None,
            campaigns: dict | None = None, lua_limits: str | None = None) -> list[str]:
        self.count += 1
        request = self.scratch / f"calls-{self.count}.json"
        outcomes = self.scratch / f"outcomes-{self.count}.jsonl"
        request.write_text(json.dumps({"calls": calls}, ensure_ascii=True), encoding="utf-8")
        self.files += [request, outcomes]
        environment = {key: value for key, value in os.environ.items() if key != "CIR_PIN_OVERRIDES"}
        if pins:
            pin_file = self.scratch / f"pins-{self.count}.json"
            pin_file.write_text(json.dumps(pins, ensure_ascii=True), encoding="utf-8")
            self.files.append(pin_file)
            environment["CIR_PIN_OVERRIDES"] = str(pin_file)
        environment.pop("CIR_PARITY_REGISTRATION_DRAFT", None)
        environment.pop("CIR_PARITY_TABLES", None)
        environment.pop("CIR_PARITY_LANGUAGES", None)
        environment.pop("CIR_PARITY_LISTS", None)
        environment.pop("CIR_PARITY_CAMPAIGNS", None)
        environment.pop("CIR_PARITY_LUA_LIMITS", None)
        if lua_limits is not None:
            # "constants,part" as a test patches campaigns._LUAJIT_CONSTANTS and _LUA_PART.
            environment["CIR_PARITY_LUA_LIMITS"] = lua_limits
        if languages is not None:
            # The translations folder a test points translations.LANGUAGES_DIR at.
            environment["CIR_PARITY_LANGUAGES"] = languages
        if lists is not None:
            # The folder whose lists/ a test points templates.ROOT and original_catalog.ROOT at.
            environment["CIR_PARITY_LISTS"] = lists
        if campaigns is not None:
            campaign_file = self.scratch / f"campaigns-{self.count}.json"
            campaign_file.write_text(json.dumps(campaigns, ensure_ascii=True), encoding="utf-8")
            self.files.append(campaign_file)
            environment["CIR_PARITY_CAMPAIGNS"] = str(campaign_file)
        if tables:
            table_file = self.scratch / f"tables-{self.count}.json"
            table_file.write_text(json.dumps(tables, ensure_ascii=True), encoding="utf-8")
            self.files.append(table_file)
            environment["CIR_PARITY_TABLES"] = str(table_file)
        if registration_draft is not None:
            draft_file = self.scratch / f"registration-draft-{self.count}.json"
            draft_file.write_text(json.dumps(registration_draft, ensure_ascii=True), encoding="utf-8")
            self.files.append(draft_file)
            environment["CIR_PARITY_REGISTRATION_DRAFT"] = str(draft_file)
        try:
            completed = subprocess.run([str(RUST_CALL), str(request), str(outcomes)],
                                       env=environment, capture_output=True)
            if completed.returncode != 0:
                raise RuntimeError("parity_call failed: " + completed.stderr.decode("utf-8", "replace"))
            return outcomes.read_text(encoding="utf-8").splitlines()
        finally:
            for path in self.files:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            self.files.clear()


def is_temporary(path: Path) -> bool:
    """Whether a folder lies inside the system temporary folder."""
    try:
        temporary = Path(tempfile.gettempdir()).resolve()
        resolved = Path(path).resolve()
    except OSError:
        return False
    return temporary in resolved.parents


def _special(path: Path) -> bool:
    """Links, junctions and read-only files cannot be restored faithfully."""
    try:
        info = path.lstat()
    except OSError:
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    return (stat.S_ISLNK(info.st_mode) or bool(attributes & 0x400) or
            (not stat.S_ISDIR(info.st_mode) and not info.st_mode & stat.S_IWRITE))


def snapshot_tree(root: Path):
    """Files (bytes) and folders (None) below root, None when root is absent.

    Raises ValueError when the tree holds something a restore would change
    (a link, a junction or a read-only file)."""
    if not os.path.lexists(root):
        return None
    if _special(root) or not root.is_dir():
        raise ValueError(f"cannot snapshot {root}")
    tree = {}
    for current, folders, files in os.walk(root):
        base = Path(current)
        for name in folders + files:
            path = base / name
            if _special(path):
                raise ValueError(f"cannot snapshot {path}")
            relative = path.relative_to(root).as_posix()
            tree[relative] = None if name in folders else path.read_bytes()
    return tree


def restore_tree(root: Path, tree) -> None:
    import shutil
    if os.path.lexists(root):
        shutil.rmtree(root)
    if tree is None:
        return
    root.mkdir(parents=True)
    for relative, data in sorted(tree.items()):
        path = root / relative
        if data is None:
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


def tree_differences(expected, actual) -> list[str]:
    expected, actual = expected or {}, actual or {}
    differences = []
    for relative in sorted(set(expected) | set(actual)):
        if relative not in actual:
            differences.append(f"missing in Rust: {relative}")
        elif relative not in expected:
            differences.append(f"only in Rust: {relative}")
        elif expected[relative] != actual[relative]:
            differences.append(f"content differs: {relative}")
    return differences


def first_difference(expected: str, actual: str, context: int = 160) -> str:
    """A short excerpt around the first differing character."""
    index = next((i for i, (a, b) in enumerate(zip(expected, actual)) if a != b),
                 min(len(expected), len(actual)))
    start = max(0, index - context // 2)
    return (f"at {index}: python …{expected[start:index + context // 2]}…\n"
            f"           rust   …{actual[start:index + context // 2]}…")
