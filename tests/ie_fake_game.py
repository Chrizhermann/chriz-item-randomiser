#!/usr/bin/env python3
"""Hermetic installer test helpers for Chriz Item Randomiser.

Tests build only synthetic KEY/BIFF/TLK games below a caller-supplied temporary
directory. Nothing here discovers, reads, or writes a real game.
"""

from __future__ import annotations

import hashlib
import os
import re
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


FORBIDDEN_ACCESS_ROOT = Path(r"C:\Games")
# Derived from the current profile; Documents may or may not be redirected to OneDrive.
_PROFILE = Path.home()
_EET_SAVES = Path("Baldur's Gate - Enhanced Edition Trilogy") / "save"
FORBIDDEN_SCRATCH_ROOTS = (
    FORBIDDEN_ACCESS_ROOT,
    _PROFILE / "Games",
    _PROFILE / "OneDrive" / "Documents" / _EET_SAVES,
    _PROFILE / "Documents" / _EET_SAVES,
)


class MatrixFailure(RuntimeError):
    """Expected assertion failure with a spoiler-safe diagnostic."""


@dataclass(frozen=True)
class ProcessResult:
    command_name: str
    returncode: int
    output: str

    @property
    def installed(self) -> bool:
        upper = self.output.upper()
        return (
            self.returncode == 0
            and "SUCCESSFULLY INSTALLED" in upper
            and "NOT INSTALLED" not in upper
            and "SKIPPING" not in upper
        )


class Reporter:
    def __init__(self) -> None:
        self.passed = 0

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if not condition:
            suffix = f" ({detail})" if detail else ""
            raise MatrixFailure(f"FAIL {name}{suffix}")
        self.passed += 1
        print(f"PASS {name}")

    def summary(self) -> None:
        print(f"SUMMARY passed={self.passed} failed=0")


def _normcase(path: Path) -> str:
    # Forbidden roots are compared lexically so merely running a hermetic test
    # never opens or resolves anything inside a game tree. Caller-controlled
    # existing paths are resolved before they reach this comparison.
    return os.path.normcase(os.path.abspath(str(path)))


def _is_within(candidate: Path, parent: Path) -> bool:
    candidate_text = _normcase(candidate)
    parent_text = _normcase(parent).rstrip("\\/")
    return candidate_text == parent_text or candidate_text.startswith(parent_text + os.sep)


def require_existing_file(path_text: str, label: str) -> Path:
    path = Path(path_text).resolve(strict=True)
    if not path.is_file():
        raise MatrixFailure(f"{label} is not a file")
    if _is_within(path, FORBIDDEN_ACCESS_ROOT):
        raise MatrixFailure(f"{label} resolves inside the forbidden game root")
    return path


def require_existing_directory(path_text: str, label: str) -> Path:
    path = Path(path_text).resolve(strict=True)
    if not path.is_dir():
        raise MatrixFailure(f"{label} is not a directory")
    if _is_within(path, FORBIDDEN_ACCESS_ROOT):
        raise MatrixFailure(f"{label} resolves inside the forbidden game root")
    return path


def require_scratch_parent(path_text: str) -> Path:
    path = require_existing_directory(path_text, "temporary root")
    for forbidden in FORBIDDEN_SCRATCH_ROOTS:
        if _is_within(path, forbidden):
            raise MatrixFailure("temporary root resolves inside a forbidden game tree")
    dangerous_parts = {"save", "saves", "mpsave", "override"}
    if dangerous_parts.intersection(part.casefold() for part in path.parts):
        raise MatrixFailure("temporary root resembles a game or save directory")
    if any(
        (path / marker).exists()
        for marker in ("chitin.key", "override", "save", "saves", "mpsave")
    ):
        raise MatrixFailure("temporary root contains game or save markers")
    return path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def build_tlk() -> bytes:
    # One empty entry. The string-data offset follows the 0x12 header plus one
    # 0x1a entry. This is accepted by WeiDU 249 and is sufficient here.
    return b"TLK V1  " + struct.pack("<HII", 0, 1, 0x2C) + (b"\0" * 0x1A)


def snapshot_tree(root: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    if not root.exists():
        return snapshot
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        key = relative.casefold()
        if key in snapshot:
            raise MatrixFailure("case-folding collision in synthetic tree")
        snapshot[key] = path.read_bytes()
    return snapshot


def run_process(
    args: Sequence[str],
    cwd: Path,
    command_name: str,
    stdin_text: str | None = None,
) -> ProcessResult:
    try:
        completed = subprocess.run(
            list(args),
            cwd=str(cwd),
            stdin=subprocess.DEVNULL if stdin_text is None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            input=stdin_text,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MatrixFailure(f"{command_name} timed out after 120 seconds") from exc
    return ProcessResult(command_name, completed.returncode, completed.stdout)


def active_weidu_log(game_root: Path) -> tuple[str, ...]:
    log_path = game_root / "WeiDU.log"
    if not log_path.is_file():
        return ()
    active: list[str] = []
    for raw_line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        active.append(re.sub(r"\\", "/", line))
    return tuple(active)
