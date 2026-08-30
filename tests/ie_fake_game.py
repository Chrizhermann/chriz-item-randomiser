#!/usr/bin/env python3
"""Hermetic Infinity Engine installer fixtures for the Item Randomiser.

The builder creates only synthetic KEY/BIFF/TLK games below a caller-supplied
temporary directory.  It never discovers, reads, or writes a real game.  Test
output uses opaque case names and deliberately omits item-to-location joins.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


FORBIDDEN_ACCESS_ROOT = Path(r"C:\Games")
FORBIDDEN_SCRATCH_ROOTS = (
    FORBIDDEN_ACCESS_ROOT,
    Path(r"C:\Users\chris\Games\EET-IR-Test-b600e94"),
    Path(
        r"C:\Users\chris\OneDrive\Documents\Baldur's Gate - Enhanced Edition Trilogy\save"
    ),
)
SCRATCH_PREFIX = "bgee-itemrandomiser-task11-"
EXPECTED_PUBLICATION_ASSETS = {
    "M_FLDLV.lua",
    "FLDLVCor.lua",
    "FLDLV.menu",
    "FLDLVMan.lua",
}
EXPECTED_REGISTRY = "fl#irreg.2da"
RESOURCE_TYPES = {
    "IDS": 1008,
    "ARE": 1010,
}


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


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def build_tlk() -> bytes:
    # One empty entry. The string-data offset follows the 0x12 header plus one
    # 0x1a entry. This is accepted by WeiDU 249 and is sufficient here.
    return b"TLK V1  " + struct.pack("<HII", 0, 1, 0x2C) + (b"\0" * 0x1A)


def tlk_entry_count(data: bytes) -> int:
    if len(data) < 0x12 or data[:8] != b"TLK V1  ":
        raise MatrixFailure("synthetic TLK signature is invalid")
    return struct.unpack_from("<I", data, 0x0A)[0]


def build_minimal_are() -> bytes:
    # ARE V1.0 header with every table empty and every table offset pointing
    # just past the fixed header. Production insert_actor can append to this
    # shape without any base-game data. Count and size fields remain zero.
    header_size = 0x11C
    song_size = 0x90
    rest_size = 0xE4
    song_offset = header_size
    rest_offset = song_offset + song_size
    terminal = rest_offset + rest_size
    data = bytearray(terminal)
    data[0:8] = b"AREAV1.0"
    # Empty counted sections begin immediately after the header, before the
    # two mandatory fixed-size song/rest structures.
    for offset in (
        0x54, 0x5C, 0x60, 0x68, 0x70, 0x78, 0x7C, 0x84, 0x88,
        0xA0, 0xA8, 0xB0, 0xB8,
    ):
        struct.pack_into("<I", data, offset, header_size)
    struct.pack_into("<H", data, 0x90, header_size)
    struct.pack_into("<I", data, 0xBC, song_offset)
    struct.pack_into("<I", data, 0xC0, rest_offset)
    struct.pack_into("<I", data, 0xC4, terminal)
    struct.pack_into("<I", data, 0xCC, terminal)
    return bytes(data)


def build_biff(resources: Sequence[tuple[str, str, bytes]]) -> bytes:
    header_size = 0x14
    table_offset = header_size
    payload_offset = table_offset + 0x10 * len(resources)
    entries = bytearray()
    payload = bytearray()
    for index, (_resref, extension, data) in enumerate(resources):
        entries += struct.pack(
            "<IIIHH",
            index,
            payload_offset + len(payload),
            len(data),
            RESOURCE_TYPES[extension],
            0,
        )
        payload += data
    return (
        b"BIFFV1  "
        + struct.pack("<III", len(resources), 0, table_offset)
        + bytes(entries)
        + bytes(payload)
    )


def build_key(resources: Sequence[tuple[str, str, bytes]], bif_size: int) -> bytes:
    bif_name = b"data/matrix.bif\0"
    bif_table_offset = 0x18
    resource_table_offset = bif_table_offset + 0x0C
    name_offset = resource_table_offset + 0x0E * len(resources)
    header = b"KEY V1  " + struct.pack(
        "<IIII", 1, len(resources), bif_table_offset, resource_table_offset
    )
    bif_entry = struct.pack("<IIHH", bif_size, name_offset, len(bif_name), 0)
    resource_entries = bytearray()
    for index, (resref, extension, _data) in enumerate(resources):
        encoded = resref.encode("ascii")
        if len(encoded) > 8:
            raise MatrixFailure("synthetic KEY resref exceeded eight bytes")
        resource_entries += struct.pack(
            "<8sHI", encoded.ljust(8, b"\0"), RESOURCE_TYPES[extension], index
        )
    return header + bif_entry + bytes(resource_entries) + bif_name


def build_fake_game(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=False)
    # GAME_IS only needs the marker resource to exist. Keep it opaque and use
    # build_minimal_are() solely for the functional synthetic area in override.
    resources = [("OH6000", "ARE", b"synthetic BG2EE marker")]
    bif = build_biff(resources)
    key = build_key(resources, len(bif))
    write_bytes(root / "data" / "matrix.bif", bif)
    write_bytes(root / "chitin.key", key)
    tlk = build_tlk()
    write_bytes(root / "dialog.tlk", tlk)
    write_bytes(root / "lang" / "en_US" / "dialog.tlk", tlk)
    write_bytes(root / "...blank", b"")
    (root / "override").mkdir()


def cre_with_inventory_item(
    source: bytes,
    resref: str,
    charges: tuple[int, int, int],
) -> bytes:
    if len(source) < 0x2C4 or source[:8] != b"CRE V1.0":
        raise MatrixFailure("synthetic CRE template is invalid")
    encoded = resref.encode("ascii")
    if not encoded or len(encoded) > 8:
        raise MatrixFailure("synthetic CRE item resref is invalid")
    data = bytearray(source)
    slot_offset = struct.unpack_from("<I", data, 0x2B8)[0]
    item_offset = struct.unpack_from("<I", data, 0x2BC)[0]
    item_count = struct.unpack_from("<I", data, 0x2C0)[0]
    insertion = item_offset + item_count * 0x14
    if insertion != slot_offset or slot_offset + 0x4C > len(data):
        raise MatrixFailure("synthetic CRE template has an unexpected item layout")
    data[insertion:insertion] = b"\0" * 0x14
    data[insertion : insertion + len(encoded)] = encoded
    struct.pack_into("<HHH", data, insertion + 0xA, *charges)
    struct.pack_into("<I", data, 0x2B8, slot_offset + 0x14)
    struct.pack_into("<I", data, 0x2C0, item_count + 1)
    # Inventory slots start at index 21; point the first one at the new item.
    struct.pack_into("<h", data, slot_offset + 0x14 + 21 * 2, item_count)
    return bytes(data)


def copy_tree_files(
    source: Path,
    destination: Path,
    *,
    excluded_top_level: Sequence[str] = (),
) -> None:
    if not source.is_dir():
        return
    excluded = {part.casefold() for part in excluded_top_level}
    for path in sorted(source.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(source)
        if relative.parts and relative.parts[0].casefold() in excluded:
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def stage_mod_source(source_root: Path, game_root: Path, harness: Path) -> None:
    mod_root = game_root / "randomiser"
    for directory in ("lib", "copy", "lists", "d"):
        copy_tree_files(source_root / directory, mod_root / directory)
    # The repository tracks pre-generated public-install BAFs. The synthetic
    # harness must compile only the one script it creates for this case.
    copy_tree_files(
        source_root / "baf",
        mod_root / "baf",
        excluded_top_level=("compile",),
    )
    (mod_root / "baf" / "compile").mkdir(parents=True, exist_ok=True)
    (mod_root / "tests").mkdir(parents=True, exist_ok=True)
    shutil.copy2(harness, mod_root / "tests" / harness.name)
    translation = harness.with_suffix(".tra")
    if translation.is_file():
        shutil.copy2(translation, mod_root / "tests" / translation.name)


def seed_common_override(
    source_root: Path,
    game_root: Path,
    existing_override: Mapping[str, str],
) -> None:
    override = game_root / "override"
    for name, text in existing_override.items():
        write_bytes(override / name, text.encode("utf-8"))
    write_bytes(override / "mtrxarea.are", build_minimal_are())
    source_cre = (source_root / "copy" / "flrandom.cre").read_bytes()
    source_itm = (source_root / "copy" / "ward.itm").read_bytes()
    write_bytes(override / "mtrxcre.cre", source_cre)
    write_bytes(
        override / "mtrxsrc.cre",
        cre_with_inventory_item(source_cre, "mtrxitem", (3, 2, 1)),
    )
    write_bytes(override / "mtrxitem.itm", source_itm)
    write_bytes(override / "sw1h01.itm", source_itm)
    write_bytes(
        override / "trigger.ids",
        b"IDS V1.0\n0x4000 Exists(O:Object*)\n0x4001 Global(S:Name*,S:Area*,I:Value*)\n",
    )
    write_bytes(
        override / "action.ids",
        b"IDS V1.0\n"
        b"1 GiveItemCreate(S:Item*,O:Target*,I:Usage1*,I:Usage2*,I:Usage3*)\n"
        b"2 SetGlobal(S:Name*,S:Area*,I:Value*)\n"
        b"3 DestroySelf()\n"
        b"4 CreateItem(S:Item*,I:Usage1*,I:Usage2*,I:Usage3*)\n"
        b"5 ActionOverride(O:Object*,A:Action*)\n",
    )
    write_bytes(override / "object.ids", b"IDS V1.0\n1 Myself\n")


CAPABILITY_SOURCES: Mapping[str, Sequence[str]] = {
    "EEex_Action.lua": (
        "function EEex_Action_QueueResponseStringOnAIBase(response, actor) end",
        "function EEex_Action_ParseResponseString(response) end",
        "function EEex_Action_QueueScriptFileResponseOnAIBase(response, actor) end",
    ),
    "EEex_Area.lua": (
        "function EEex_Area_GetVisible() end",
    ),
    "EEex_GameObject.lua": (
        "function EEex_GameObject_Get(objectID) end",
        "function EEex_GameObject_CastUserType(object) end",
        "function EEex_GameObject_IsSprite(object, includeDead) end",
    ),
    "EEex_GameState.lua": (
        "function EEex_GameState_GetGlobalInt(variableName) end",
        "function EEex_GameState_SetGlobalInt(variableName, value) end",
        "function EEex_GameState_AddDestroyedListener(listener) end",
    ),
    "EEex_Menu.lua": (
        "function EEex_Menu_Find(menuName, panel, state) end",
        "function EEex_Menu_GetItemFunction(funcRef) end",
        "function EEex_Menu_SetItemFunction(funcRef, func) end",
        "function EEex_Menu_LoadFile(resref) end",
        "function EEex_Menu_AddAfterMainFileLoadedListener(listener) end",
    ),
    "EEex_Resource.lua": (
        "function EEex_Resource_Fetch(resref, extension) end",
    ),
    "EEex_Sprite.lua": (
        "function EEex_Sprite_AddLoadedListener(listener) end",
        "function EEex_Sprite_GetInPortrait(portraitIndex) end",
    ),
    "EEex_Trigger.lua": (
        "function EEex_Trigger_EvalConditionalStringAsAIBase(trigger, actor) end",
    ),
    "EEex_Utility.lua": (
        "function EEex_Utility_IterateCPtrList(list) end",
    ),
}


def seed_eeex_capability(game_root: Path) -> None:
    override = game_root / "override"
    write_bytes(override / "M___EEex.lua", b"EEex_Active = true\n")
    for name, lines in CAPABILITY_SOURCES.items():
        write_bytes(override / name, (("\n".join(lines)) + "\n").encode("ascii"))
    write_bytes(
        game_root / "InfinityLoader.ini",
        b"[General]\r\nLuaPatchMode=REPLACE_INTERNAL_WITH_EXTERNAL\r\n",
    )


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


def snapshot_hashes(root: Path) -> dict[str, str]:
    return {key: sha256_bytes(value) for key, value in snapshot_tree(root).items()}


def _ascii_field(data: bytes, offset: int, size: int) -> str:
    if offset < 0 or offset + size > len(data):
        return ""
    return data[offset : offset + size].split(b"\0", 1)[0].decode(
        "ascii", errors="replace"
    )


def static_publication_assets_match(
    repository_root: Path,
    override: Mapping[str, bytes],
) -> bool:
    for name in ("M_FLDLV.lua", "FLDLVCor.lua", "FLDLV.menu"):
        source = repository_root / "copy" / name
        if not source.is_file() or not source.read_bytes():
            return False
        if override.get(name.casefold()) != source.read_bytes():
            return False
    return True


def decode_lua_decimal_escapes(text: str) -> str:
    def decode(match: re.Match[str]) -> str:
        value = int(match.group(1))
        if value > 255:
            raise MatrixFailure("generated manifest has an invalid Lua byte escape")
        return chr(value)

    return re.sub(r"\\([0-9]{3})", decode, text)


def manifest_has_synthetic_contract(data: bytes) -> bool:
    if not data:
        return False
    text = decode_lua_decimal_escapes(data.decode("ascii", errors="strict"))
    required = (
        'schema = "flir-delivery-manifest-v1"',
        'backend = "eeex-manifest-v1"',
        '["flm1t1"]',
        'unit_id = "matrix:unit-a"',
        'item_id = "matrix:item-a"',
        'item_resref = "mtrxitem"',
        "charges = {3, 2, 1}",
        '[1] = { slot_id = "matrix:slot-a"',
        'endpoint_id = "matrix:container-a"',
        '["matrix:container-a"]',
        'fallback_id = "matrix:ground-a"',
        '["matrix:ground-a"]',
        'area = "mtrxarea"',
    )
    return (
        all(fragment in text for fragment in required)
        and text.count("unit_id =") == 1
        and text.count("slot_id =") == 1
        and text.count("target_kind =") == 2
        and re.search(r"fingerprint = \{\d+, \d+, \d+, \d+\}", text) is not None
    )


def registry_has_synthetic_contract(
    data: bytes,
    backend: str = "eeex-manifest-v1",
) -> bool:
    try:
        lines = [
            tuple(line.split())
            for line in data.decode("ascii", errors="strict").splitlines()
            if line.strip()
        ]
    except UnicodeDecodeError:
        return False
    if lines[:3] != [
        ("2DA", "V1.0"),
        ("0",),
        ("CAMPAIGN", "TIER", "KIND", "STABLE_ID", "COMPACT", "ENABLED"),
    ]:
        return False
    rows = lines[3:]
    required = {
        ("@meta", "@schema", "schema", "flir-registry-v1", "1", "1"),
        ("@meta", "@backend", "backend", backend, "1", "1"),
        ("@meta", "@origin", "origin", "fresh-postextension-v1", "1", "1"),
        ("bg2", "m1", "slot", "legacy:slot-bg2-m1-1-matrix-1", "1", "1"),
        ("bg2", "m1", "unit", "matrix:unit-a", "1", "1"),
    }
    if len(rows) != 7 or not required.issubset(set(rows)):
        return False
    dynamic = {
        row[2]: row
        for row in rows
        if len(row) == 6 and row[:2] in (("@meta", "@catalog"), ("@meta", "@fingerprint"))
    }
    return (
        set(dynamic) == {"catalog", "fingerprint"}
        and all(row[3].isdigit() and row[4:] == ("1", "1") for row in dynamic.values())
    )


def runtime_assets_have_bootstrap_contract(override: Mapping[str, bytes]) -> bool:
    try:
        bootstrap = override["m_fldlv.lua"].decode("utf-8", errors="strict")
        core = override["fldlvcor.lua"].decode("utf-8", errors="strict")
        menu = override["fldlv.menu"].decode("utf-8", errors="strict")
    except (KeyError, UnicodeDecodeError):
        return False
    return (
        "FLDLV = FLDLV or {}" in bootstrap
        and "FLDLVMan" in bootstrap
        and "FLDLVCor" in bootstrap
        and "FLDLV" in menu
        and 'Core.MANIFEST_SCHEMA = "flir-delivery-manifest-v1"' in core
        and 'Core.MANIFEST_BACKEND = "eeex-manifest-v1"' in core
    )


def expected_single_ground_materialization(source: bytes, x: int, y: int) -> bytes:
    """Build the exact ARE bytes required by the synthetic ground contract.

    This is deliberately an independent byte oracle rather than a loose parser:
    the one allowed preexisting-file mutation must be the insertion of one
    authenticated empty type-4 pile and the corresponding table-offset shifts.
    """
    if len(source) < 0x11C or source[:8] != b"AREAV1.0":
        raise MatrixFailure("synthetic ground source is not an ARE V1.0 resource")
    declared_container_offset = struct.unpack_from("<I", source, 0x70)[0]
    container_count = struct.unpack_from("<H", source, 0x74)[0]
    item_count = struct.unpack_from("<H", source, 0x76)[0]
    if container_count != 0 or not (
        0x11C <= declared_container_offset <= len(source)
    ):
        raise MatrixFailure("synthetic ground source has an unexpected container table")
    if not (1 < x <= 32767 and 1 < y <= 32767):
        raise MatrixFailure("synthetic ground endpoint is outside the materializer domain")

    record_size = 0xC0
    # With no existing container table the production contract appends the
    # first record and makes 0x70 point at it. This avoids inserting into either
    # mandatory fixed-size structure in the minimal ARE.
    container_offset = len(source)
    expected = bytearray(source + (b"\0" * record_size))
    for header_offset in (
        0x54,
        0x5C,
        0x60,
        0x68,
        0x78,
        0x7C,
        0x84,
        0x88,
        0xA0,
        0xA8,
        0xB0,
        0xB8,
        0xBC,
        0xC0,
        0xC4,
        0xCC,
    ):
        original_offset = struct.unpack_from("<I", source, header_offset)[0]
        if original_offset >= container_offset and original_offset != 0:
            struct.pack_into(
                "<I", expected, header_offset, original_offset + record_size
            )
    tiled_flags_offset = struct.unpack_from("<H", source, 0x90)[0]
    if tiled_flags_offset >= container_offset and tiled_flags_offset != 0:
        relocated_tiled_flags = tiled_flags_offset + record_size
        if relocated_tiled_flags > 0xFFFF:
            raise MatrixFailure("synthetic tiled-object flags offset overflows its word")
        struct.pack_into("<H", expected, 0x90, relocated_tiled_flags)

    struct.pack_into("<H", expected, 0x74, 1)
    struct.pack_into("<I", expected, 0x70, container_offset)
    name = f"FLDLV_{x}_{y}".encode("ascii")
    if len(name) > 32:
        raise MatrixFailure("synthetic ground endpoint name exceeds the ARE field")
    expected[container_offset : container_offset + len(name)] = name
    struct.pack_into("<HHH", expected, container_offset + 0x20, x, y, 4)
    struct.pack_into("<HH", expected, container_offset + 0x34, x, y)
    struct.pack_into(
        "<HHHH",
        expected,
        container_offset + 0x38,
        max(0, x - 16),
        max(0, y - 16),
        min(32767, x + 16),
        min(32767, y + 16),
    )
    struct.pack_into("<II", expected, container_offset + 0x40, item_count, 0)
    struct.pack_into("<IH", expected, container_offset + 0x50, 0, 0)
    return bytes(expected)


def ground_area_has_exact_single_materialization(
    source: bytes, candidate: bytes, x: int, y: int
) -> bool:
    expected = expected_single_ground_materialization(source, x, y)
    if candidate != expected:
        return False
    container_offset = struct.unpack_from("<I", candidate, 0x70)[0]
    matching = []
    for index in range(struct.unpack_from("<H", candidate, 0x74)[0]):
        offset = container_offset + index * 0xC0
        if (
            struct.unpack_from("<H", candidate, offset + 0x24)[0] == 4
            and struct.unpack_from("<HH", candidate, offset + 0x20) == (x, y)
        ):
            matching.append(offset)
    if len(matching) != 1:
        return False
    offset = matching[0]
    return (
        _ascii_field(candidate, offset, 32) == f"FLDLV_{x}_{y}"
        and struct.unpack_from("<II", candidate, offset + 0x40)
        == (struct.unpack_from("<H", source, 0x76)[0], 0)
        and struct.unpack_from("<HH", candidate, offset + 0x34) == (x, y)
        and struct.unpack_from("<HHHH", candidate, offset + 0x38)
        == (
            max(0, x - 16),
            max(0, y - 16),
            min(32767, x + 16),
            min(32767, y + 16),
        )
        and struct.unpack_from("<IH", candidate, offset + 0x50) == (0, 0)
    )


def legacy_delivery_is_wired(override: Mapping[str, bytes]) -> bool:
    area = override.get("mtrxarea.are", b"")
    actor = override.get("flm1t1.cre", b"")
    script = override.get("flm1t1.bcs", b"")
    if len(area) < 0x5A or len(actor) < 0x250 or not script:
        return False
    actor_offset = struct.unpack_from("<I", area, 0x54)[0]
    actor_count = struct.unpack_from("<H", area, 0x58)[0]
    return (
        actor_count == 1
        and _ascii_field(area, actor_offset, 32).casefold() == "flm1t1"
        and _ascii_field(area, actor_offset + 0x80, 8).casefold() == "flm1t1"
        and _ascii_field(actor, 0x248, 8).casefold() == "flm1t1"
    )


def immutable_hashes(game_root: Path) -> dict[str, str]:
    paths = (
        "chitin.key",
        "data/matrix.bif",
        "dialog.tlk",
        "lang/en_US/dialog.tlk",
    )
    return {path: sha256_file(game_root / path) for path in paths}


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


def run_weidu(
    weidu: Path,
    game_root: Path,
    harness_name: str,
    arguments: Sequence[str],
    stdin_text: str | None = None,
) -> ProcessResult:
    relative_tp2 = f"randomiser/tests/{harness_name}"
    command = [
        str(weidu),
        relative_tp2,
        "--game",
        ".",
        *arguments,
        "--language",
        "0",
        "--use-lang",
        "en_US",
        "--no-exit-pause",
        "--quick-log",
    ]
    return run_process(
        command,
        game_root,
        f"WeiDU {harness_name}",
        stdin_text=stdin_text,
    )


def require_success(result: ProcessResult, case_name: str) -> None:
    if not result.installed:
        # Do not echo WeiDU's resource-level diagnostics: they can contain
        # source names from a caller's checkout. The opaque tail hash is enough
        # to correlate a failing hermetic run during debugging.
        digest = sha256_bytes(result.output.encode("utf-8", errors="replace"))[:16]
        diagnostic = ""
        if os.environ.get("FLIR_TASK11_DEBUG") == "1":
            diagnostic = f"\n{result.output}"
        raise MatrixFailure(
            f"{case_name} failed (exit={result.returncode}, transcript={digest})"
            f"{diagnostic}"
        )


def require_clean_uninstall(result: ProcessResult, case_name: str) -> None:
    upper = result.output.upper()
    clean = (
        result.returncode == 0
        and "ERROR" not in upper
        and "NOT UNINSTALLED" not in upper
        and "SKIPPING" not in upper
    )
    if not clean:
        digest = sha256_bytes(result.output.encode("utf-8", errors="replace"))[:16]
        raise MatrixFailure(
            f"{case_name} failed (exit={result.returncode}, transcript={digest})"
        )


def require_expected_failure(
    result: ProcessResult,
    case_name: str,
    *,
    expected_marker: str,
    attempted_marker: str,
) -> None:
    upper = result.output.upper()
    failed = (
        result.returncode != 0
        and "NOT INSTALLED DUE TO ERRORS" in upper
        and expected_marker.upper() in upper
        and attempted_marker.upper() in upper
    )
    if not failed:
        raise MatrixFailure(f"{case_name} did not fail at the controlled boundary")


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


def component_is_active(
    game_root: Path,
    harness_name: str,
    component: int,
) -> bool:
    expected_path = f"~randomiser/tests/{harness_name}~".casefold()
    component_pattern = re.compile(rf"\s#\d+\s+#{component}(?:\s|$)")
    return any(
        expected_path in line.casefold() and component_pattern.search(line)
        for line in active_weidu_log(game_root)
    )


def created_keys(before: Mapping[str, bytes], after: Mapping[str, bytes]) -> set[str]:
    return set(after) - set(before)


def changed_keys(before: Mapping[str, bytes], after: Mapping[str, bytes]) -> set[str]:
    return {key for key in set(before) & set(after) if before[key] != after[key]}


def load_contract(contract_path: Path) -> dict[str, object]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract.get("schema") != "flir-fake-game-contract-v1":
        raise MatrixFailure("unsupported fake-game contract schema")

    def require_basename(value: object, label: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9#_.-]+", value):
            raise MatrixFailure(f"{label} is not a safe resource basename")
        if value in {".", ".."} or Path(value).name != value:
            raise MatrixFailure(f"{label} is not a safe resource basename")
        return value

    assets_value = contract.get("publication_assets")
    if not isinstance(assets_value, list):
        raise MatrixFailure("publication_assets must be an array")
    assets = [require_basename(value, "publication asset") for value in assets_value]
    if set(assets) != EXPECTED_PUBLICATION_ASSETS or len(assets) != len(set(assets)):
        raise MatrixFailure("publication_assets does not match the fixed allowlist")

    registry = require_basename(contract.get("registry"), "registry")
    if registry != EXPECTED_REGISTRY:
        raise MatrixFailure("registry does not match the fixed allowlist")
    preserved_value = contract.get("preserved_state")
    if not isinstance(preserved_value, list):
        raise MatrixFailure("preserved_state must be an array")
    preserved = [require_basename(value, "preserved state") for value in preserved_value]
    if preserved != [EXPECTED_REGISTRY]:
        raise MatrixFailure("preserved_state does not match the fixed allowlist")

    if contract.get("ordinary_actor_pattern") != r"^flm1t[0-9]+\.cre$":
        raise MatrixFailure("ordinary_actor_pattern does not match the fixed contract")
    if contract.get("ordinary_script_pattern") != r"^flm1t[0-9]+\.bcs$":
        raise MatrixFailure("ordinary_script_pattern does not match the fixed contract")

    existing = contract.get("fixture_existing_override")
    if not isinstance(existing, dict):
        raise MatrixFailure("fixture_existing_override must be an object")
    for name, payload in existing.items():
        require_basename(name, "fixture override name")
        if not isinstance(payload, str):
            raise MatrixFailure("fixture override payload must be text")
    if existing != {
        "matrix.keep": "preexisting override bytes\n",
        "itemexcl.2da": "2DA V1.0\n0\n",
    }:
        raise MatrixFailure("fixture_existing_override does not match the fixed contract")

    if contract.get("mode2_components") != [1300, 1400]:
        raise MatrixFailure("mode2_components must be exactly 1300 and 1400")
    return contract


def create_publication_game(
    case_root: Path,
    repository_root: Path,
    harness: Path,
    contract: Mapping[str, object],
    eeex: bool,
) -> Path:
    game_root = case_root / "game"
    build_fake_game(game_root)
    stage_mod_source(repository_root, game_root, harness)
    existing = contract["fixture_existing_override"]
    if not isinstance(existing, dict):
        raise MatrixFailure("fixture_existing_override must be an object")
    seed_common_override(repository_root, game_root, existing)
    if eeex:
        seed_eeex_capability(game_root)
    return game_root


def assert_immutable(
    reporter: Reporter,
    name: str,
    game_root: Path,
    expected: Mapping[str, str],
) -> None:
    reporter.check(name, immutable_hashes(game_root) == dict(expected))


def run_special_transform_test(
    reporter: Reporter,
    repository_root: Path,
    weidu: Path,
    temp_root: Path,
) -> None:
    pwsh = shutil.which("pwsh")
    if not pwsh:
        raise MatrixFailure("pwsh is required for the special-transform boundary")
    script = repository_root / "tests" / "Test-DeliveryLegacy.ps1"
    result = run_process(
        (
            pwsh,
            "-NoProfile",
            "-File",
            str(script),
            "-WeiduPath",
            str(weidu),
            "-TempRoot",
            str(temp_root),
        ),
        repository_root,
        "special transformations",
    )
    reporter.check(
        "InstallerMatrix_SpecialTransformsRetained",
        result.returncode == 0 and "SUMMARY" in result.output,
    )


def run_installer_matrix(args: argparse.Namespace) -> int:
    reporter = Reporter()
    repository_root = require_existing_directory(args.repository_root, "repository root")
    weidu = require_existing_file(args.weidu, "WeiDU executable")
    temp_root = require_scratch_parent(args.temp_root)
    contract_path = require_existing_file(args.contract, "publication contract")
    contract = load_contract(contract_path)
    harness = require_existing_file(
        str(contract_path.parent / "installer-matrix.tp2"), "installer matrix harness"
    )
    publication_assets = {
        str(name).casefold() for name in contract["publication_assets"]  # type: ignore[index]
    }
    registry = str(contract["registry"]).casefold()
    preserved_state = {
        str(name).casefold() for name in contract["preserved_state"]  # type: ignore[index]
    }
    actor_pattern = re.compile(str(contract["ordinary_actor_pattern"]), re.IGNORECASE)
    script_pattern = re.compile(str(contract["ordinary_script_pattern"]), re.IGNORECASE)

    scratch = Path(tempfile.mkdtemp(prefix=SCRATCH_PREFIX, dir=str(temp_root))).resolve()
    if scratch.parent != temp_root or not scratch.name.startswith(SCRATCH_PREFIX):
        raise MatrixFailure("generated scratch directory escaped its verified parent")
    try:
        # EEex Mode 1: public seam, reinstall, and uninstall.
        eeex_game = create_publication_game(
            scratch / "eeex", repository_root, harness, contract, eeex=True
        )
        immutable_before = immutable_hashes(eeex_game)
        override_before = snapshot_tree(eeex_game / "override")
        install = run_weidu(
            weidu,
            eeex_game,
            harness.name,
            ("--force-install-list", "1100"),
        )
        require_success(install, "EEex Mode 1 publication")
        reporter.check(
            "InstallerMatrix_EEexComponentActive",
            component_is_active(eeex_game, harness.name, 1100),
        )
        override_installed = snapshot_tree(eeex_game / "override")
        installed_names = set(override_installed)
        reporter.check(
            "InstallerMatrix_EEexPublishesRuntimeAssets",
            publication_assets <= installed_names,
        )
        reporter.check(
            "InstallerMatrix_EEexPublishesRegistry",
            registry in installed_names,
        )
        reporter.check(
            "InstallerMatrix_StaticRuntimeAssetsByteExact",
            static_publication_assets_match(repository_root, override_installed),
        )
        reporter.check(
            "InstallerMatrix_RuntimeAssetsBootstrapContract",
            runtime_assets_have_bootstrap_contract(override_installed),
        )
        reporter.check(
            "InstallerMatrix_ManifestSyntheticContract",
            manifest_has_synthetic_contract(
                override_installed.get("fldlvman.lua", b"")
            ),
        )
        reporter.check(
            "InstallerMatrix_RegistrySyntheticContract",
            registry_has_synthetic_contract(override_installed.get(registry, b"")),
        )
        reporter.check(
            "InstallerMatrix_EEexPublishesNoOrdinaryActors",
            not any(actor_pattern.match(name) for name in installed_names),
        )
        eeex_created = created_keys(override_before, override_installed)
        expected_eeex_created = publication_assets | {registry}
        reporter.check(
            "InstallerMatrix_EEexPublicationAllowlist",
            eeex_created == expected_eeex_created,
            detail=f"created-count={len(eeex_created)}",
        )
        reporter.check(
            "InstallerMatrix_EEexGroundMutationAllowlist",
            changed_keys(override_before, override_installed) == {"mtrxarea.are"},
        )
        reporter.check(
            "InstallerMatrix_EEexGroundPileExactStructuralDelta",
            ground_area_has_exact_single_materialization(
                override_before["mtrxarea.are"],
                override_installed["mtrxarea.are"],
                100,
                200,
            ),
        )
        assert_immutable(
            reporter,
            "InstallerMatrix_EEexImmutableKeyBiffTlk",
            eeex_game,
            immutable_before,
        )
        registry_before_reinstall = override_installed[registry]
        reinstall = run_weidu(
            weidu,
            eeex_game,
            harness.name,
            (
                "--force-uninstall-list",
                "1100",
                "--force-install-list",
                "1100",
            ),
        )
        require_success(reinstall, "EEex Mode 1 compatible reinstall")
        reporter.check(
            "InstallerMatrix_ReinstalledComponentActive",
            component_is_active(eeex_game, harness.name, 1100),
        )
        override_reinstalled = snapshot_tree(eeex_game / "override")
        reporter.check(
            "InstallerMatrix_RegistrySurvivesCompatibleReinstall",
            override_reinstalled.get(registry) == registry_before_reinstall,
        )
        reporter.check(
            "InstallerMatrix_ReinstallPublicationStable",
            snapshot_hashes(eeex_game / "override")
            == {key: sha256_bytes(value) for key, value in override_installed.items()},
        )
        reporter.check(
            "InstallerMatrix_ReinstallGroundAreaByteIdempotent",
            override_reinstalled.get("mtrxarea.are")
            == override_installed["mtrxarea.are"],
        )
        assert_immutable(
            reporter,
            "InstallerMatrix_ReinstallImmutableKeyBiffTlk",
            eeex_game,
            immutable_before,
        )
        uninstall = run_weidu(
            weidu,
            eeex_game,
            harness.name,
            ("--force-uninstall-list", "1100"),
        )
        require_clean_uninstall(uninstall, "EEex Mode 1 uninstall")
        reporter.check(
            "InstallerMatrix_UninstalledComponentInactive",
            not component_is_active(eeex_game, harness.name, 1100),
        )
        override_uninstalled = snapshot_tree(eeex_game / "override")
        before_without_state = {
            key: value for key, value in override_before.items() if key != registry
        }
        after_without_state = {
            key: value for key, value in override_uninstalled.items() if key != registry
        }
        reporter.check(
            "InstallerMatrix_UninstallRestoresOverrideBytes",
            before_without_state == after_without_state,
        )
        reporter.check(
            "InstallerMatrix_UninstallRestoresGroundAreaBytes",
            override_uninstalled.get("mtrxarea.are")
            == override_before["mtrxarea.are"],
        )
        reporter.check(
            "InstallerMatrix_UninstallPreservesDeclaredState",
            all(
                name in override_reinstalled
                and override_uninstalled.get(name) == override_reinstalled[name]
                for name in preserved_state
            ),
        )
        assert_immutable(
            reporter,
            "InstallerMatrix_UninstallImmutableKeyBiffTlk",
            eeex_game,
            immutable_before,
        )

        # Legacy Mode 1 retains exactly one ordinary delivery actor/script and
        # publishes neither EEex runtime files nor a manifest.
        legacy_game = create_publication_game(
            scratch / "legacy", repository_root, harness, contract, eeex=False
        )
        legacy_immutable = immutable_hashes(legacy_game)
        legacy_before = snapshot_tree(legacy_game / "override")
        legacy_install = run_weidu(
            weidu,
            legacy_game,
            harness.name,
            ("--force-install-list", "1100"),
        )
        require_success(legacy_install, "legacy Mode 1 publication")
        reporter.check(
            "InstallerMatrix_LegacyComponentActive",
            component_is_active(legacy_game, harness.name, 1100),
        )
        legacy_after = snapshot_tree(legacy_game / "override")
        legacy_names = set(legacy_after)
        reporter.check(
            "InstallerMatrix_LegacyPublishesNoEEexAssets",
            publication_assets.isdisjoint(legacy_names),
        )
        reporter.check(
            "InstallerMatrix_LegacyRetainsOrdinaryActor",
            sum(1 for name in legacy_names if actor_pattern.match(name)) == 1,
        )
        reporter.check(
            "InstallerMatrix_LegacyRetainsOrdinaryScript",
            sum(1 for name in legacy_names if script_pattern.match(name)) == 1,
        )
        legacy_created = created_keys(legacy_before, legacy_after)
        expected_legacy_created = {registry}
        expected_legacy_created |= {
            name for name in legacy_created if actor_pattern.match(name) or script_pattern.match(name)
        }
        reporter.check(
            "InstallerMatrix_LegacyPublicationAllowlist",
            legacy_created == expected_legacy_created,
            detail=f"created-count={len(legacy_created)}",
        )
        reporter.check(
            "InstallerMatrix_LegacyMutationAllowlist",
            changed_keys(legacy_before, legacy_after) == {"mtrxarea.are"},
        )
        reporter.check(
            "InstallerMatrix_LegacyRegistrySyntheticContract",
            registry_has_synthetic_contract(
                legacy_after.get(registry, b""),
                backend="legacy-bcs-v1",
            ),
        )
        reporter.check(
            "InstallerMatrix_LegacyActorScriptWired",
            legacy_delivery_is_wired(legacy_after),
        )
        assert_immutable(
            reporter,
            "InstallerMatrix_LegacyImmutableKeyBiffTlk",
            legacy_game,
            legacy_immutable,
        )

        # A manifest validation failure must roll back all publication.
        failed_game = create_publication_game(
            scratch / "failed", repository_root, harness, contract, eeex=True
        )
        failed_immutable = immutable_hashes(failed_game)
        failed_before = snapshot_tree(failed_game / "override")
        failed_install = run_weidu(
            weidu,
            failed_game,
            harness.name,
            ("--force-install-list", "1199"),
        )
        require_expected_failure(
            failed_install,
            "failed validation",
            expected_marker="FLIR_MANIFEST_ERR NOT_PREPARED id=write",
            attempted_marker="Installing [Hermetic failed publication boundary]",
        )
        reporter.check(
            "InstallerMatrix_FailedComponentInactive",
            not component_is_active(failed_game, harness.name, 1199),
        )
        reporter.check(
            "InstallerMatrix_FailedValidationPublishesNothing",
            snapshot_tree(failed_game / "override") == failed_before,
        )
        assert_immutable(
            reporter,
            "InstallerMatrix_FailureImmutableKeyBiffTlk",
            failed_game,
            failed_immutable,
        )

        run_special_transform_test(reporter, repository_root, weidu, scratch)
        reporter.summary()
        return 0
    finally:
        if scratch.exists():
            if scratch.parent != temp_root or not scratch.name.startswith(SCRATCH_PREFIX):
                raise MatrixFailure("refusing to remove an unverified scratch directory")
            shutil.rmtree(scratch)


def git_bytes(repository_root: Path, commit: str, relative_path: str) -> bytes:
    result = subprocess.run(
        ("git", "show", f"{commit}:{relative_path}"),
        cwd=str(repository_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise MatrixFailure(f"baseline object unavailable for {relative_path}")
    return result.stdout


def extract_git_directories(
    repository_root: Path,
    commit: str,
    destination: Path,
    directories: Sequence[str],
) -> None:
    archive = subprocess.run(
        ("git", "archive", "--format=tar", commit, *directories),
        cwd=str(repository_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if archive.returncode != 0:
        raise MatrixFailure("could not materialize the baseline source closure")
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as bundle:
        for member in bundle.getmembers():
            if member.name.startswith("/") or ".." in Path(member.name).parts:
                raise MatrixFailure("baseline archive contained an unsafe path")
        bundle.extractall(destination)


def strip_block_comments_preserving_lines(text: str) -> str:
    return re.sub(
        r"/\*.*?\*/",
        lambda match: re.sub(r"[^\r\n]", " ", match.group(0)),
        text,
        flags=re.DOTALL,
    )


def component_body(tp2: str, component: int) -> str:
    active = strip_block_comments_preserving_lines(tp2)
    markers = list(
        re.finditer(
            r"(?im)^BEGIN\b[^\r\n]*\bDESIGNATED\s+(?P<number>\d+)\b[^\r\n]*",
            active,
        )
    )
    for index, marker in enumerate(markers):
        if int(marker.group("number")) != component:
            continue
        end = markers[index + 1].start() if index + 1 < len(markers) else len(active)
        body = active[marker.start() : end]
        return "\n".join(line.rstrip() for line in body.replace("\r\n", "\n").splitlines()).strip()
    raise MatrixFailure(f"component {component} is absent")


def normalize_text_bytes(data: bytes) -> bytes:
    """Compare tracked text independent of checkout line-ending policy."""
    return data.replace(b"\r\n", b"\n")


def prepare_mode2_source(
    repository_root: Path,
    destination: Path,
    harness: Path,
    baseline_commit: str | None,
) -> Path:
    source_root = destination / "source"
    if baseline_commit is None:
        source_root.mkdir(parents=True)
        for directory in ("lib", "copy", "lists", "baf", "d"):
            copy_tree_files(repository_root / directory, source_root / directory)
    else:
        extract_git_directories(
            repository_root,
            baseline_commit,
            source_root,
            ("lib", "copy", "lists", "baf", "d"),
        )
    # The test harness is current test infrastructure, not candidate product
    # behavior, so the identical bytes drive both source closures.
    (source_root / "tests").mkdir(parents=True, exist_ok=True)
    shutil.copy2(harness, source_root / "tests" / harness.name)
    return source_root


def create_mode2_game(
    case_root: Path,
    source_root: Path,
    harness: Path,
    contract: Mapping[str, object],
) -> Path:
    game_root = case_root / "game"
    build_fake_game(game_root)
    stage_mod_source(source_root, game_root, harness)
    existing = contract["fixture_existing_override"]
    if not isinstance(existing, dict):
        raise MatrixFailure("fixture_existing_override must be an object")
    seed_common_override(source_root, game_root, existing)
    return game_root


def cre_item_semantics(data: bytes) -> tuple[tuple[str, int, int, int], ...]:
    if len(data) < 0x2C4 or data[:4] != b"CRE ":
        return ()
    item_offset, item_count = struct.unpack_from("<II", data, 0x2BC)
    items: list[tuple[str, int, int, int]] = []
    for index in range(item_count):
        offset = item_offset + index * 0x14
        if offset + 0x14 > len(data):
            raise MatrixFailure("synthetic CRE item table is out of bounds")
        resref = data[offset : offset + 8].split(b"\0", 1)[0].decode("ascii", "replace").lower()
        # CRE item entries store a two-byte expiration field before charges.
        charge1, charge2, charge3 = struct.unpack_from("<HHH", data, offset + 0xA)
        items.append((resref, charge1, charge2, charge3))
    return tuple(items)


def mode2_semantic_snapshot(game_root: Path) -> dict[str, object]:
    override = snapshot_tree(game_root / "override")
    return {
        "target_items": cre_item_semantics(override.get("mtrxcre.cre", b"")),
        "source_items": cre_item_semantics(override.get("mtrxsrc.cre", b"")),
        "marker": sha256_bytes(override.get("flrandomiser2.mrk", b"")),
        "seed": sha256_bytes(override.get("fl#randomseed.2da", b"")),
        "removed": sha256_bytes(override.get("fl#removeditems.2da", b"")),
        "options": sha256_bytes(override.get("fl#randoptions.2da", b"")),
    }


def seed_loss_value(data: bytes) -> int:
    if len(data) < 6:
        raise MatrixFailure("synthetic random-seed state is truncated")
    return data[4]


def normalize_active_log(lines: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for line in lines:
        line = re.sub(r"^[^~]*~", "~", line)
        normalized.append(line)
    return tuple(normalized)


def run_mode2_parity(args: argparse.Namespace) -> int:
    reporter = Reporter()
    repository_root = require_existing_directory(args.repository_root, "repository root")
    weidu = require_existing_file(args.weidu, "WeiDU executable")
    temp_root = require_scratch_parent(args.temp_root)
    contract_path = require_existing_file(args.contract, "publication contract")
    contract = load_contract(contract_path)
    harness = require_existing_file(
        str(contract_path.parent / "mode2-parity.tp2"), "Mode 2 parity harness"
    )
    baseline_commit = args.baseline_commit

    baseline_tp2 = git_bytes(repository_root, baseline_commit, "randomiser.tp2").decode(
        "utf-8", "replace"
    )
    candidate_tp2 = (repository_root / "randomiser.tp2").read_text(
        encoding="utf-8", errors="replace"
    )
    for component in contract["mode2_components"]:  # type: ignore[index]
        reporter.check(
            f"Mode2Parity_Component{component}BodyMatchesBaseline",
            component_body(candidate_tp2, int(component))
            == component_body(baseline_tp2, int(component)),
        )

    exact_paths = (
        "lib/weidu_action.tpa",
        "lists/items/mode2/bg1.2da",
        "lists/items/mode2/bg2.2da",
        "lists/locations/mode2/bg1.2da",
        "lists/locations/mode2/bg2.2da",
    )
    for relative_path in exact_paths:
        reporter.check(
            "Mode2Parity_Protected_" + relative_path.replace("/", "_"),
            normalize_text_bytes((repository_root / relative_path).read_bytes())
            == normalize_text_bytes(
                git_bytes(repository_root, baseline_commit, relative_path)
            ),
        )

    scratch = Path(tempfile.mkdtemp(prefix=SCRATCH_PREFIX, dir=str(temp_root))).resolve()
    if scratch.parent != temp_root or not scratch.name.startswith(SCRATCH_PREFIX):
        raise MatrixFailure("generated scratch directory escaped its verified parent")
    try:
        baseline_source = prepare_mode2_source(
            repository_root,
            scratch / "baseline-source",
            harness,
            baseline_commit,
        )
        candidate_source = prepare_mode2_source(
            repository_root,
            scratch / "candidate-source",
            harness,
            None,
        )

        for component in (1300, 1400):
            baseline_game = create_mode2_game(
                scratch / f"baseline-{component}", baseline_source, harness, contract
            )
            candidate_game = create_mode2_game(
                scratch / f"candidate-{component}", candidate_source, harness, contract
            )
            expected_item = ("mtrxitem", 3, 2, 1)
            reporter.check(
                f"Mode2Parity_{component}_SourceFixtureContainsItem",
                expected_item
                in cre_item_semantics(
                    snapshot_tree(baseline_game / "override")["mtrxsrc.cre"]
                )
                and expected_item
                in cre_item_semantics(
                    snapshot_tree(candidate_game / "override")["mtrxsrc.cre"]
                ),
            )
            baseline_immutable = immutable_hashes(baseline_game)
            candidate_immutable = immutable_hashes(candidate_game)
            baseline_selected_tlk = baseline_game / "lang" / "en_US" / "dialog.tlk"
            candidate_selected_tlk = candidate_game / "lang" / "en_US" / "dialog.tlk"
            baseline_tlk_before = tlk_entry_count(baseline_selected_tlk.read_bytes())
            candidate_tlk_before = tlk_entry_count(candidate_selected_tlk.read_bytes())

            baseline_run = run_weidu(
                weidu,
                baseline_game,
                harness.name,
                ("--force-install-list", str(component)),
                stdin_text="100\n" if component == 1400 else None,
            )
            candidate_run = run_weidu(
                weidu,
                candidate_game,
                harness.name,
                ("--force-install-list", str(component)),
                stdin_text="100\n" if component == 1400 else None,
            )
            require_success(baseline_run, f"Mode 2 baseline component {component}")
            require_success(candidate_run, f"Mode 2 candidate component {component}")
            reporter.check(
                f"Mode2Parity_{component}_ComponentsActive",
                component_is_active(baseline_game, harness.name, component)
                and component_is_active(candidate_game, harness.name, component),
            )

            baseline_override = snapshot_tree(baseline_game / "override")
            candidate_override = snapshot_tree(candidate_game / "override")
            reporter.check(
                f"Mode2Parity_{component}_OverrideHashes",
                {key: sha256_bytes(value) for key, value in baseline_override.items()}
                == {key: sha256_bytes(value) for key, value in candidate_override.items()},
            )
            reporter.check(
                f"Mode2Parity_{component}_SemanticState",
                mode2_semantic_snapshot(baseline_game)
                == mode2_semantic_snapshot(candidate_game),
            )
            candidate_semantics = mode2_semantic_snapshot(candidate_game)
            target_items = candidate_semantics["target_items"]
            source_items = candidate_semantics["source_items"]
            reporter.check(
                f"Mode2Parity_{component}_ExpectedDistribution",
                (expected_item in target_items) == (component == 1300)
                and expected_item not in source_items,
            )
            expected_loss = 100 if component == 1400 else 0
            reporter.check(
                f"Mode2Parity_{component}_PersistedLossState",
                seed_loss_value(baseline_override["fl#randomseed.2da"])
                == expected_loss
                == seed_loss_value(candidate_override["fl#randomseed.2da"]),
            )
            reporter.check(
                f"Mode2Parity_{component}_ActiveWeiDULog",
                normalize_active_log(active_weidu_log(baseline_game))
                == normalize_active_log(active_weidu_log(candidate_game)),
            )
            baseline_tlk_after = tlk_entry_count(baseline_selected_tlk.read_bytes())
            candidate_tlk_after = tlk_entry_count(candidate_selected_tlk.read_bytes())
            reporter.check(
                f"Mode2Parity_{component}_TlkEntryDelta",
                baseline_tlk_after - baseline_tlk_before
                == candidate_tlk_after - candidate_tlk_before,
            )
            forbidden = {
                str(name).casefold() for name in contract["publication_assets"]  # type: ignore[index]
            }
            forbidden.add(str(contract["registry"]).casefold())
            reporter.check(
                f"Mode2Parity_{component}_NoEEexOrRegistryState",
                forbidden.isdisjoint(candidate_override),
            )
            assert_immutable(
                reporter,
                f"Mode2Parity_{component}_BaselineImmutableKeyBiffTlk",
                baseline_game,
                baseline_immutable,
            )
            assert_immutable(
                reporter,
                f"Mode2Parity_{component}_CandidateImmutableKeyBiffTlk",
                candidate_game,
                candidate_immutable,
            )

        reporter.check(
            "Mode2Parity_HermeticBoundaryDeclared",
            "narrower than a full Item Randomiser installation"
            in (contract_path.parent / "README.md").read_text(encoding="utf-8"),
        )
        reporter.summary()
        return 0
    finally:
        if scratch.exists():
            if scratch.parent != temp_root or not scratch.name.startswith(SCRATCH_PREFIX):
                raise MatrixFailure("refusing to remove an unverified scratch directory")
            shutil.rmtree(scratch)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("installer-matrix", "mode2-parity"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--repository-root", required=True)
        subparser.add_argument("--weidu", required=True)
        subparser.add_argument("--temp-root", required=True)
        subparser.add_argument("--contract", required=True)
        if name == "mode2-parity":
            subparser.add_argument("--baseline-commit", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "installer-matrix":
            return run_installer_matrix(args)
        if args.command == "mode2-parity":
            return run_mode2_parity(args)
        raise MatrixFailure("unknown command")
    except MatrixFailure as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
