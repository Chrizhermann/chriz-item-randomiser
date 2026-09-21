"""Read-only, post-install item indexing for BG:EE/BG2:EE and merged EET.

Only effective ITM resources are read: override entries supersede chitin.key.
KEY/BIFF/TLK V1 and ITM V1 are supported; compressed archives are rejected.
No installation, save, source-location, or destination data is changed or emitted.
Format references: https://gibberlings3.github.io/iesdp/file_formats/index.htm
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
import zipfile
from pathlib import Path
from typing import BinaryIO


class ScanError(ValueError):
    """The supplied installation cannot be indexed unambiguously."""


class _ScanSnapshot:
    """Detect ordinary concurrent installation changes, without locking files.

    Stat identities and directory inventories are checked again before return.
    This is not an atomic filesystem snapshot or protection against deliberate
    timestamp spoofing. The installation should remain idle during a scan.
    """

    def __init__(self) -> None:
        self.files: dict[Path, tuple[int, ...]] = {}
        self.directories: dict[Path, tuple[tuple[int, ...], tuple[str, ...]]] = {}

    @staticmethod
    def _identity(path: Path) -> tuple[int, ...]:
        info = path.stat()
        return (
            info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns,
        )

    @classmethod
    def _directory(cls, path: Path) -> tuple[tuple[int, ...], tuple[str, ...]]:
        before = cls._identity(path)
        names = tuple(sorted(entry.name for entry in path.iterdir()))
        if before != cls._identity(path):
            raise ScanError("The installation changed during scanning; no catalog was produced.")
        return before, names

    def file(self, path: Path) -> None:
        if path not in self.files:
            self.files[path] = self._identity(path)

    def directory(self, path: Path) -> None:
        if path not in self.directories:
            self.directories[path] = self._directory(path)

    def verify(self) -> None:
        try:
            for path, expected in self.files.items():
                if self._identity(path) != expected:
                    raise ScanError("The installation changed during scanning; no catalog was produced.")
            # Check containing directories last to catch additions, removals,
            # replacements and new language/DLC choices after resource reads.
            for path, expected in reversed(tuple(self.directories.items())):
                if self._directory(path) != expected:
                    raise ScanError("The installation changed during scanning; no catalog was produced.")
        except OSError as exc:
            raise ScanError("The installation changed or became unreadable during scanning; no catalog was produced.") from exc


ITM_TYPE = 1005
ITEM_SEMANTIC_FIELDS = (
    "resref", "name", "unidentified_name", "identified_name", "category",
    "flags", "price", "stack_size", "enchantment", "proficiency", "sha256",
)


def catalog_fingerprint(catalog: dict) -> str:
    """Hash the catalog's semantic fields, independent of physical packaging.

    Callers validating imported catalogs must also validate their schema and
    field types. This digest is an integrity check, not an authenticity claim.
    """
    try:
        content = {
            "schema": catalog["schema"],
            "language": catalog["language"],
            "items": [
                {key: item[key] for key in ITEM_SEMANTIC_FIELDS}
                for item in catalog["items"]
            ],
        }
        encoded = json.dumps(
            content, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (KeyError, TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ScanError("The catalog cannot be fingerprinted; required semantic fields are invalid or missing.") from exc
    return hashlib.sha256(encoded).hexdigest()


def _span(size: int, offset: int, length: int, label: str) -> None:
    if offset < 0 or length < 0 or offset > size or length > size - offset:
        raise ScanError(f"Invalid or truncated {label}.")


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _contained(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ScanError("A resource path resolves outside the selected game directory.")
    return resolved


def _child(parent: Path, name: str, root: Path, *, required: bool = True) -> Path | None:
    matches = [entry for entry in parent.iterdir() if entry.name.casefold() == name.casefold()]
    if len(matches) > 1:
        raise ScanError(f"Ambiguous case-insensitive path: {name}.")
    if not matches:
        if required:
            raise ScanError(f"Required game resource is missing: {name}.")
        return None
    return _contained(matches[0], root)


def _relative_file(root: Path, name: str, snapshot: _ScanSnapshot) -> Path:
    # KEY paths use either slash convention. Never interpret an archive-supplied
    # drive, parent traversal, or absolute path, even if it happens to exist.
    parts = name.replace("\\", "/").split("/")
    if any(not part or part in (".", "..") or ":" in part for part in parts):
        raise ScanError("Invalid or unsafe archive path in chitin.key.")
    current = root
    for part in parts:
        snapshot.directory(current)
        current = _child(current, part, root)  # type: ignore[assignment]
    if not current.is_file():
        raise ScanError("A KEY archive path does not refer to a file.")
    return current


def _reject_unmerged_dlc(root: Path, snapshot: _ScanSnapshot) -> None:
    """Reject DLC resource layers that a root-KEY-only scan cannot include.

    Ordinary downloaded mod archives are not effective game resources. Inspect
    root ZIP directories only for KEY-bearing packages, without extracting or
    opening their members. Any nonempty ZIP in dlc/ is outside current scope.
    """
    candidates = [(entry, False) for entry in root.iterdir() if entry.suffix.casefold() == ".zip"]
    dlc = _child(root, "dlc", root, required=False)
    if dlc is not None:
        if not dlc.is_dir():
            raise ScanError("The DLC path is not a directory.")
        snapshot.directory(dlc)
        candidates.extend((entry, True) for entry in dlc.iterdir() if entry.suffix.casefold() == ".zip")
    for entry, in_dlc in candidates:
        archive = _contained(entry, root)
        if not archive.is_file():
            raise ScanError("A ZIP archive path does not refer to a file.")
        snapshot.file(archive)
        if archive.stat().st_size == 0:
            continue
        if in_dlc or entry.name.casefold() == "sod-dlc.zip":
            raise ScanError("Unmerged DLC ZIP archives are not supported; this scanner requires a merged root KEY resource set.")
        if not zipfile.is_zipfile(archive):
            continue
        try:
            with zipfile.ZipFile(archive) as package:
                if any(not member.is_dir() and member.filename.casefold().endswith(".key") for member in package.infolist()):
                    raise ScanError("A KEY-bearing ZIP archive requires DLC resource support; merge DLC before scanning.")
        except zipfile.BadZipFile as exc:
            raise ScanError("A root ZIP archive changed or became unreadable during scanning.") from exc


def _resref(raw: bytes) -> str:
    body, separator, padding = raw.partition(b"\0")
    if separator and padding.strip(b"\0"):
        raise ScanError("A resource reference has nonzero data after its terminator.")
    try:
        name = body.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ScanError("A resource reference is not ASCII.") from exc
    if not name or any(ord(c) < 33 or ord(c) > 126 or c in '/\\:*?"<>|' for c in name):
        raise ScanError("Invalid item resource reference.")
    return name.lower()


def _read_file(path: Path) -> bytes:
    if not path.is_file():
        raise ScanError("A required resource path is not a file.")
    return path.read_bytes()


def _key(data: bytes) -> tuple[list[str], dict[str, tuple[int, int]]]:
    _span(len(data), 0, 0x18, "KEY header")
    if data[:8] != b"KEY V1  ":
        raise ScanError("Unsupported KEY format; expected KEY V1.")
    archives, count, archive_offset, resource_offset = struct.unpack_from("<IIII", data, 8)
    if (archives and archive_offset < 0x18) or (count and resource_offset < 0x18):
        raise ScanError("KEY tables overlap their header.")
    _span(len(data), archive_offset, archives * 12, "KEY archive table")
    _span(len(data), resource_offset, count * 14, "KEY resource table")
    if archives and count and max(archive_offset, resource_offset) < min(archive_offset + archives * 12, resource_offset + count * 14):
        raise ScanError("KEY archive and resource tables overlap.")
    names = []
    for index in range(archives):
        base = archive_offset + index * 12
        offset, length = _u32(data, base + 4), _u16(data, base + 8)
        _span(len(data), offset, length, "KEY archive filename")
        raw = data[offset:offset + length]
        if not raw or raw[-1:] != b"\0" or b"\0" in raw[:-1]:
            raise ScanError("Invalid KEY archive filename terminator.")
        try:
            name = raw[:-1].decode("ascii")
        except UnicodeDecodeError as exc:
            raise ScanError("A KEY archive filename is not ASCII.") from exc
        parts = name.replace("\\", "/").split("/")
        if any(not part or part in (".", "..") or ":" in part for part in parts):
            raise ScanError("Invalid or unsafe archive path in chitin.key.")
        names.append(name)
    items = {}
    for index in range(count):
        base = resource_offset + index * 14
        if _u16(data, base + 8) != ITM_TYPE:
            continue
        name = _resref(data[base:base + 8])
        locator = _u32(data, base + 10)
        archive_index, resource_id = locator >> 20, locator & 0x3FFF
        if archive_index >= archives:
            raise ScanError("An item references an invalid KEY archive index.")
        if name in items:
            raise ScanError(f"Duplicate case-insensitive KEY item: {name}.")
        items[name] = (archive_index, resource_id)
    return names, items


def _read_at(handle: BinaryIO, size: int, offset: int, length: int, label: str) -> bytes:
    _span(size, offset, length, label)
    handle.seek(offset)
    data = handle.read(length)
    if len(data) != length:
        raise ScanError(f"The {label} changed or was truncated during scanning.")
    return data


def _biff_items(path: Path, requested: dict[int, str]) -> dict[str, bytes]:
    # Read only the index and requested records, not potentially multi-GB BIFs.
    with path.open("rb") as handle:
        size = path.stat().st_size
        header = _read_at(handle, size, 0, min(0x14, size), "BIFF header")
        if header[:4] in (b"BIFC", b"BIF "):
            raise ScanError("Compressed BIF archives are not supported by this scanner yet.")
        if len(header) < 0x14 or header[:8] != b"BIFFV1  ":
            raise ScanError("Invalid or unsupported BIFF header; expected BIFF V1.")
        count, tiles, offset = struct.unpack_from("<III", header, 8)
        if (count or tiles) and offset < 0x14:
            raise ScanError("BIFF tables overlap their header.")
        table = _read_at(handle, size, offset, count * 16, "BIFF file table")
        _span(size, offset + count * 16, tiles * 20, "BIFF tileset table")
        found = {}
        locators = set()
        for index in range(count):
            locator, data_offset, length, kind, _unknown = struct.unpack_from("<IIIHH", table, index * 16)
            resource_id = locator & 0x3FFF
            if resource_id in locators:
                raise ScanError("Duplicate resource locator in a BIFF file table.")
            locators.add(resource_id)
            _span(size, data_offset, length, "BIFF resource data")
            if length and (data_offset < 0x14 or max(data_offset, offset) < min(data_offset + length, offset + count * 16 + tiles * 20)):
                raise ScanError("BIFF resource data overlaps its header or tables.")
            if resource_id not in requested:
                continue
            if kind != ITM_TYPE:
                raise ScanError("The BIFF resource type disagrees with its KEY item entry.")
            found[requested[resource_id]] = _read_at(handle, size, data_offset, length, "ITM data")
        if len(found) != len(requested):
            raise ScanError("An item resource locator is missing from its BIFF archive.")
        return found


def _select_tlk(root: Path, language: str | None, snapshot: _ScanSnapshot) -> tuple[Path, str]:
    lang_dir = _child(root, "lang", root, required=False)
    if language is not None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", language):
            raise ScanError("Language must be a single language-directory name.")
        if lang_dir is None or not lang_dir.is_dir():
            raise ScanError("The requested language directory is missing.")
        snapshot.directory(lang_dir)
        selected = _child(lang_dir, language, root)
        if selected is None or not selected.is_dir():
            raise ScanError("The requested language directory is missing.")
        snapshot.directory(selected)
        tlk = _child(selected, "dialog.tlk", root)
        assert tlk is not None
        return tlk, language.casefold()
    root_tlk = _child(root, "dialog.tlk", root, required=False)
    if root_tlk is not None:
        return root_tlk, "default"
    candidates = []
    seen = set()
    if lang_dir is not None:
        if not lang_dir.is_dir():
            raise ScanError("The lang path is not a directory.")
        snapshot.directory(lang_dir)
        for entry in sorted(lang_dir.iterdir()):
            if not entry.is_dir():
                continue
            name = entry.name.casefold()
            if name in seen:
                raise ScanError("Ambiguous case-insensitive language directories.")
            seen.add(name)
            selected = _contained(entry, root)
            snapshot.directory(selected)
            candidate = _child(selected, "dialog.tlk", root, required=False)
            if candidate is not None:
                candidates.append((candidate, name))
    if len(candidates) != 1:
        raise ScanError("Select an explicit language; no unique dialog.tlk was found.")
    return candidates[0]


class _Tlk:
    def __init__(self, data: bytes):
        _span(len(data), 0, 0x12, "TLK header")
        if data[:8] != b"TLK V1  ":
            raise ScanError("Unsupported TLK format; expected TLK V1.")
        self.data = data
        self.count = _u32(data, 0xA)
        self.offset = _u32(data, 0xE)
        _span(len(data), 0x12, self.count * 0x1A, "TLK entry table")
        if self.offset < 0x12 + self.count * 0x1A:
            raise ScanError("TLK string data overlaps its entry table.")
        _span(len(data), self.offset, 0, "TLK string data")
        for index in range(self.count):
            base = 0x12 + index * 0x1A
            offset, length = struct.unpack_from("<II", data, base + 0x12)
            _span(len(data), self.offset + offset, length, "TLK string")

    def text(self, strref: int, warnings: set[str]) -> str | None:
        if strref == 0xFFFFFFFF:
            return None
        if strref >= self.count:
            warnings.add("Some item names reference missing TLK entries; available names or resource references are shown.")
            return None
        base = 0x12 + strref * 0x1A
        if not _u16(self.data, base) & 1:
            return None
        offset, length = struct.unpack_from("<II", self.data, base + 0x12)
        raw = self.data[self.offset + offset:self.offset + offset + length].split(b"\0", 1)[0]
        try:
            return raw.decode("utf-8") or None
        except UnicodeDecodeError as exc:
            raise ScanError("An item name is not valid UTF-8; this scanner supports Enhanced Edition text.") from exc


def _item(name: str, data: bytes, origin: str, tlk: _Tlk, warnings: set[str]) -> dict:
    _span(len(data), 0, 0x72, "ITM header")
    if data[:8] != b"ITM V1  ":
        raise ScanError(f"Unsupported ITM format for {name}; expected ITM V1.")
    ability_offset, ability_count = _u32(data, 0x64), _u16(data, 0x68)
    effect_offset = _u32(data, 0x6A)
    if ability_count:
        if ability_offset < 0x72:
            raise ScanError("ITM ability table overlaps its header.")
        _span(len(data), ability_offset, ability_count * 0x38, "ITM ability table")
    effect_ranges = [(_u16(data, 0x6E), _u16(data, 0x70))]
    for index in range(ability_count):
        base = ability_offset + index * 0x38
        effect_ranges.append((_u16(data, base + 0x20), _u16(data, base + 0x1E)))
    for index, count in effect_ranges:
        if count:
            if effect_offset < 0x72:
                raise ScanError("ITM effects overlap its header.")
            _span(len(data), effect_offset + index * 0x30, count * 0x30, "ITM effects")
            start, end = effect_offset + index * 0x30, effect_offset + (index + count) * 0x30
            if ability_count and max(start, ability_offset) < min(end, ability_offset + ability_count * 0x38):
                raise ScanError("ITM effects overlap its ability table.")
    unidentified = tlk.text(_u32(data, 8), warnings)
    identified = tlk.text(_u32(data, 12), warnings)
    return {
        "resref": name,
        "name": identified or unidentified or name,
        "unidentified_name": unidentified,
        "identified_name": identified,
        "category": _u16(data, 0x1C),
        "flags": _u32(data, 0x18),
        "price": _u32(data, 0x34),
        "stack_size": _u16(data, 0x38),
        "enchantment": _u32(data, 0x60),
        "proficiency": data[0x31],
        "sha256": hashlib.sha256(data).hexdigest(),
        "resource_origin": origin,
    }


def scan_game(game_root: Path, *, language: str | None = None) -> dict:
    """Index effective installed items without writing anything to the game.

    A root dialog.tlk takes precedence when language is omitted; otherwise a
    sole lang/*/dialog.tlk is selected. Multiple language choices need an
    explicit language. The fingerprint excludes physical paths and packaging.

    Relevant file stat identities and directory inventories are checked before
    returning. Concurrent changes fail without retry. This detects ordinary
    edits, not adversarial timestamp spoofing, and is not an atomic filesystem
    snapshot; callers should keep the installation idle for the scan.
    """
    try:
        root = Path(game_root).resolve(strict=True)
        if not root.is_dir():
            raise ScanError("The supplied game directory is not a directory.")
        snapshot = _ScanSnapshot()
        snapshot.directory(root)
        _reject_unmerged_dlc(root, snapshot)
        key_path = _child(root, "chitin.key", root)
        assert key_path is not None
        snapshot.file(key_path)
        archive_names, keyed_items = _key(_read_file(key_path))
        tlk_path, selected_language = _select_tlk(root, language, snapshot)
        snapshot.file(tlk_path)
        tlk = _Tlk(_read_file(tlk_path))
        overrides = {}
        override_dir = _child(root, "override", root, required=False)
        if override_dir is not None:
            if not override_dir.is_dir():
                raise ScanError("The override path is not a directory.")
            snapshot.directory(override_dir)
            for entry in override_dir.iterdir():
                if entry.suffix.casefold() != ".itm":
                    continue
                try:
                    encoded = entry.stem.encode("ascii")
                except UnicodeEncodeError as exc:
                    raise ScanError("An override item resource reference is not ASCII.") from exc
                if len(encoded) > 8:
                    raise ScanError("An override item resource reference exceeds eight bytes.")
                name = _resref(encoded)
                if name in overrides:
                    raise ScanError(f"Duplicate case-insensitive override item: {name}.")
                overrides[name] = _contained(entry, root)
                snapshot.file(overrides[name])
        effective = {}
        pending: dict[int, dict[int, list[str]]] = {}
        for name, (archive_index, resource_id) in keyed_items.items():
            if name in overrides:
                continue
            archive = pending.setdefault(archive_index, {})
            archive.setdefault(resource_id, []).append(name)
        for archive_index, requested in pending.items():
            archive_path = _relative_file(root, archive_names[archive_index], snapshot)
            snapshot.file(archive_path)
            # Distinct KEY names may alias one BIFF record. Read the record
            # once, retaining each independently searchable resource name.
            archive_data = _biff_items(
                archive_path, {locator: names[0] for locator, names in requested.items()},
            )
            for names in requested.values():
                for name in names:
                    effective[name] = (archive_data[names[0]], "biff")
        for name, path in overrides.items():
            effective[name] = (_read_file(path), "override")
        warnings: set[str] = set()
        items = [_item(name, *effective[name], tlk, warnings) for name in sorted(effective)]
        schema = "flir-item-catalog-v1"
        result = {"schema": schema, "game_root": str(root), "language": selected_language, "items": items}
        result["fingerprint"] = catalog_fingerprint(result)
        if warnings:
            result["warnings"] = sorted(warnings)
        snapshot.verify()
        return result
    except ScanError:
        raise
    except (OSError, RuntimeError) as exc:
        raise ScanError(f"Unable to read the supplied installation: {exc}") from exc
