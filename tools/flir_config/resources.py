"""Read-only, post-install resource access for BG:EE/BG2:EE and merged EET.

scan_game indexes effective ITMs; GameResources reads caller-selected resource
types. Override entries supersede chitin.key. KEY/BIFF/TLK V1 and ITM V1 are
supported; compressed archives are rejected. Neither API changes game data.
The scans read the files CIR's Apply wrote as the game had them before
(applied_files.py).
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

from . import applied_files


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


# Ordinary (non-tileset) resource types used by the configurator. Verified from
# https://gibberlings3.github.io/iesdp/file_formats/general.htm
RESOURCE_TYPES = {"itm": 1005, "spl": 1006, "bcs": 1007, "cre": 1009, "are": 1010, "dlg": 1011, "sto": 1014,
                  "lua": 1033}
ITM_TYPE = RESOURCE_TYPES["itm"]
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
    # Real EET resources include internal spaces (for example MONKTU 8.DLG).
    # Preserve those exact identities in KEY, override and caller lookups; do
    # not strip whitespace or admit path components / Windows metacharacters.
    if (not name or name != name.strip(" ") or name in (".", "..") or
            any(ord(c) < 32 or ord(c) > 126 or c in '/\\:*?"<>|' for c in name)):
        raise ScanError("Invalid resource reference.")
    return name.lower()


def file_identity(path: Path) -> tuple:
    """A file's stat identity, or a directory's with its entry names (see _ScanSnapshot).

    Equal identities mean the installation did not change there, as far as
    ordinary installs show; it is no protection against deliberate spoofing.
    """
    if path.is_dir():
        return _ScanSnapshot._directory(path)
    return _ScanSnapshot._identity(path)


def _read_file(path: Path) -> bytes:
    if not path.is_file():
        raise ScanError("A required resource path is not a file.")
    return path.read_bytes()


def _key_resources(data: bytes, resource_types: dict[str, int]) -> tuple[list[str], dict[str, dict[str, tuple[int, int]]]]:
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
    extensions = {kind: extension for extension, kind in resource_types.items()}
    resources: dict[str, dict[str, tuple[int, int]]] = {extension: {} for extension in resource_types}
    for index in range(count):
        base = resource_offset + index * 14
        extension = extensions.get(_u16(data, base + 8))
        if extension is None:
            continue
        name = _resref(data[base:base + 8])
        locator = _u32(data, base + 10)
        archive_index, resource_id = locator >> 20, locator & 0x3FFF
        if archive_index >= archives:
            raise ScanError("A resource references an invalid KEY archive index.")
        if name in resources[extension]:
            raise ScanError(f"Duplicate case-insensitive KEY resource: {name}.{extension}.")
        resources[extension][name] = (archive_index, resource_id)
    return names, resources


def _key(data: bytes) -> tuple[list[str], dict[str, tuple[int, int]]]:
    names, resources = _key_resources(data, {"itm": ITM_TYPE})
    return names, resources["itm"]


def _read_at(handle: BinaryIO, size: int, offset: int, length: int, label: str) -> bytes:
    _span(size, offset, length, label)
    handle.seek(offset)
    data = handle.read(length)
    if len(data) != length:
        raise ScanError(f"The {label} changed or was truncated during scanning.")
    return data


def _biff_index(handle: BinaryIO, size: int) -> dict[int, tuple[int, int, int]]:
    """Read one archive's ordinary-resource index without reading payloads."""
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
    records = {}
    for index in range(count):
        locator, data_offset, length, kind, _unknown = struct.unpack_from("<IIIHH", table, index * 16)
        resource_id = locator & 0x3FFF
        if resource_id in records:
            raise ScanError("Duplicate resource locator in a BIFF file table.")
        _span(size, data_offset, length, "BIFF resource data")
        if length and (data_offset < 0x14 or max(data_offset, offset) < min(data_offset + length, offset + count * 16 + tiles * 20)):
            raise ScanError("BIFF resource data overlaps its header or tables.")
        records[resource_id] = (data_offset, length, kind)
    return records


def _biff_record(records: dict[int, tuple[int, int, int]], resource_id: int, kind: int) -> tuple[int, int]:
    if resource_id not in records:
        raise ScanError("A resource locator is missing from its BIFF archive.")
    offset, length, actual_kind = records[resource_id]
    if actual_kind != kind:
        raise ScanError("The BIFF resource type disagrees with its KEY entry.")
    return offset, length


def _biff_items(path: Path, requested: dict[int, str]) -> dict[str, bytes]:
    # Keep the legacy batched item-reader entry point, sharing format validation
    # with GameResources, whose individual reads do not retain resource bytes.
    with path.open("rb") as handle:
        size = path.stat().st_size
        records = _biff_index(handle, size)
        found = {}
        for resource_id, name in requested.items():
            offset, length = _biff_record(records, resource_id, ITM_TYPE)
            found[name] = _read_at(handle, size, offset, length, "ITM data")
        return found


def _override_resources(root: Path, resource_types: dict[str, int], snapshot: _ScanSnapshot,
                        long_names: list[str] | None = None) -> dict[str, dict[str, Path]]:
    resources: dict[str, dict[str, Path]] = {extension: {} for extension in resource_types}
    override_dir = _child(root, "override", root, required=False)
    if override_dir is None:
        return resources
    if not override_dir.is_dir():
        raise ScanError("The override path is not a directory.")
    snapshot.directory(override_dir)
    for entry in override_dir.iterdir():
        extension = entry.suffix[1:].casefold()
        if extension not in resources:
            continue
        if len(entry.stem) > 8:
            # Game data names resources with at most eight characters, so it
            # cannot name these files. Mods keep templates for WeiDU under such
            # names (SCS's dw#marker_file.itm); they are left out, not refused.
            if long_names is not None:
                long_names.append(entry.name)
            continue
        try:
            encoded = entry.stem.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ScanError("An override resource reference is not ASCII.") from exc
        name = _resref(encoded)
        if name in resources[extension]:
            raise ScanError(f"Duplicate case-insensitive override resource: {name}.{extension}.")
        path = _contained(entry, root)
        if not path.is_file():
            raise ScanError("An override resource path is not a file.")
        resources[extension][name] = path
        snapshot.file(path)
    return resources


def _applied_record(root: Path, snapshot: _ScanSnapshot) -> tuple[Path | None, dict[str, dict]]:
    """CIR's folder in the game and its record of the files Apply wrote (applied_files.py).

    Only the record and the copies a scan reads are part of its snapshot: the
    runtime writes other files to this folder while the game runs.
    """
    try:
        data = _child(root, applied_files.DATA, root, required=False)
    except ScanError:
        return None, {}
    if data is None or not data.is_dir():
        return None, {}
    try:
        record = _child(data, applied_files.RECORD, root, required=False)
    except ScanError:
        return data, {}
    if record is None or not record.is_file():
        return data, {}
    snapshot.file(record)
    return data, applied_files.read(record)


def _before_copy(root: Path, data: Path, parts: list[str]) -> Path | None:
    """CIR's copy of a file as the game had it before Apply wrote it, if it has one."""
    current = data
    try:
        for part in (applied_files.BEFORE, *parts):
            if not current.is_dir():
                return None
            current = _child(current, part, root, required=False)
            if current is None:
                return None
    except ScanError:
        return None
    return current if current.is_file() else None


def _applied_overrides(root: Path, data: Path | None, files: dict[str, dict],
                       overrides: dict[str, dict[str, Path]], snapshot: _ScanSnapshot) -> None:
    """Read the override files Apply wrote as the game had them before (applied_files.py).

    A file that holds exactly the bytes Apply wrote last is read from CIR's
    copy of the earlier file, or left out where there was none, so that the
    archive's copy, if any, is the game's again. Any other file stays as it is,
    as does one whose earlier copy is missing or damaged.
    """
    for path, entry in sorted(files.items()):
        parts = path.split("/")
        if len(parts) != 2 or applied_files.key(parts[0]) != "override":
            continue
        stem, dot, extension = parts[1].rpartition(".")
        extension = applied_files.key(extension)
        if not dot or extension not in overrides:
            continue
        try:
            name = _resref(stem.encode("ascii"))
        except (UnicodeEncodeError, ScanError):
            continue
        current = overrides[extension].get(name)
        if current is None or hashlib.sha256(_read_file(current)).hexdigest() != entry["written"]:
            continue
        before = entry["before"]
        if before is None:
            del overrides[extension][name]
            continue
        copy = _before_copy(root, data, parts)
        if copy is None:
            continue
        content = _read_file(copy)
        if len(content) != before["size"] or hashlib.sha256(content).hexdigest() != before["sha256"]:
            continue
        snapshot.file(copy)
        overrides[extension][name] = copy


def _applied_text(root: Path, files: dict[str, dict], path: Path, data: bytes, tlk: _Tlk) -> None:
    """Read game text Apply appended to as the game had it before, without Apply's entries."""
    name = applied_files.find(files, path.relative_to(root).as_posix())
    if name is None or not applied_files.is_text(name):
        return
    entry = files[name]
    before = entry["before"]
    if (before is None or hashlib.sha256(data).hexdigest() != entry["written"] or
            before["strings"] > tlk.count):
        return
    tlk.count = before["strings"]


class GameResources:
    """Read effective ordinary resources during one checked scan context.

    Pass a mapping such as ``{\"cre\": RESOURCE_TYPES[\"cre\"]}``. Extensions and
    resrefs accepted by the methods are case-insensitive and omit the dot.
    ``names`` returns sorted lowercase resrefs; ``read`` returns bytes on demand.
    ``origin`` returns ``override`` or ``biff`` without reading the payload.

    One installation snapshot spans setup, all reads, and caller parsing until
    successful context exit. Changed files make exit fail; caller exceptions
    propagate unchanged. Archive indexes are cached, but resource payloads are
    not. No files are written and no archives stay open between calls. Only
    root KEY V1 and uncompressed BIFF V1 ordinary resources are supported; the
    caller is responsible for validating each resource's own format. Override
    files with names longer than eight characters are not resources and are
    left out. With ``before_apply`` the override files CIR's Apply wrote are
    read as the game had them before (applied_files.py), as the scans read them.
    """

    def __init__(self, game_root: Path, resource_types: dict[str, int], *, before_apply: bool = False):
        self._game_root = game_root
        self._before_apply = before_apply
        if not isinstance(resource_types, dict) or not resource_types:
            raise ScanError("Select at least one ordinary resource type.")
        self._types: dict[str, int] = {}
        for extension, kind in resource_types.items():
            if not isinstance(extension, str) or not re.fullmatch(r"[A-Za-z0-9]+", extension):
                raise ScanError("Resource extensions must be ASCII names without a dot.")
            extension = extension.lower()
            if extension in self._types or type(kind) is not int or not 0 < kind <= 0xFFFF:
                raise ScanError("Resource type mappings must have unique extensions and valid numeric types.")
            if kind == 1003 or kind in self._types.values():
                raise ScanError("Resource types must be distinct ordinary types; tilesets are unsupported.")
            self._types[extension] = kind
        self._active = False
        self._used = False
        self._indexes: dict[Path, tuple[int, dict[int, tuple[int, int, int]]]] = {}

    def __enter__(self) -> GameResources:
        if self._used:
            raise ScanError("A GameResources context cannot be entered more than once.")
        self._used = True
        try:
            self.root = Path(self._game_root).resolve(strict=True)
            if not self.root.is_dir():
                raise ScanError("The supplied game directory is not a directory.")
            self._snapshot = _ScanSnapshot()
            self._snapshot.directory(self.root)
            _reject_unmerged_dlc(self.root, self._snapshot)
            key_path = _child(self.root, "chitin.key", self.root)
            assert key_path is not None
            self._snapshot.file(key_path)
            archive_names, self._keyed = _key_resources(_read_file(key_path), self._types)
            self._overrides = _override_resources(self.root, self._types, self._snapshot)
            if self._before_apply:
                _applied_overrides(self.root, *_applied_record(self.root, self._snapshot), self._overrides,
                                   self._snapshot)
            self._archives: dict[int, Path] = {}
            # Capture every effective archive before handing control to the
            # caller, including archives it has not read yet.
            for extension, entries in self._keyed.items():
                for name, (archive_index, _resource_id) in entries.items():
                    if name in self._overrides[extension] or archive_index in self._archives:
                        continue
                    path = _relative_file(self.root, archive_names[archive_index], self._snapshot)
                    self._snapshot.file(path)
                    self._archives[archive_index] = path
            self._active = True
            return self
        except (OSError, RuntimeError) as exc:
            raise ScanError(f"Unable to read the supplied installation: {exc}") from exc

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self._active = False
        try:
            if exc_type is None:
                self._snapshot.verify()
        finally:
            self._indexes.clear()
        return False

    def _extension(self, extension: str) -> str:
        if not self._active:
            raise ScanError("Resources can only be accessed inside their scan context.")
        if not isinstance(extension, str) or extension.lower() not in self._types:
            raise ScanError("The requested resource type was not selected for this scan.")
        return extension.lower()

    def _name(self, extension: str, resref: str) -> str:
        if not isinstance(resref, str):
            raise ScanError("A resource reference must be a string.")
        try:
            raw = resref.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ScanError("A resource reference is not ASCII.") from exc
        if not 1 <= len(raw) <= 8 or b"\0" in raw:
            raise ScanError("Resource references must be between one and eight ASCII characters.")
        name = _resref(raw)
        if name not in self._keyed[extension] and name not in self._overrides[extension]:
            raise ScanError(f"The requested resource is missing: {name}.{extension}.")
        return name

    def names(self, extension: str) -> list[str]:
        extension = self._extension(extension)
        return sorted(self._keyed[extension].keys() | self._overrides[extension].keys())

    def origin(self, extension: str, resref: str) -> str:
        extension = self._extension(extension)
        name = self._name(extension, resref)
        return "override" if name in self._overrides[extension] else "biff"

    def location(self, extension: str, resref: str) -> Path:
        """The file that holds a resource: its override file or its archive."""
        extension = self._extension(extension)
        name = self._name(extension, resref)
        if name in self._overrides[extension]:
            return self._overrides[extension][name]
        return self._archives[self._keyed[extension][name][0]]

    def lookup_paths(self) -> list[Path]:
        """The files and directory that decide where each resource comes from."""
        if not self._active:
            raise ScanError("Resources can only be accessed inside their scan context.")
        override = _child(self.root, "override", self.root, required=False)
        return [_child(self.root, "chitin.key", self.root)] + ([override] if override is not None else [])

    def read(self, extension: str, resref: str) -> bytes:
        extension = self._extension(extension)
        name = self._name(extension, resref)
        try:
            if name in self._overrides[extension]:
                return _read_file(self._overrides[extension][name])
            archive_index, resource_id = self._keyed[extension][name]
            path = self._archives[archive_index]
            with path.open("rb") as handle:
                if path not in self._indexes:
                    size = path.stat().st_size
                    self._indexes[path] = (size, _biff_index(handle, size))
                size, records = self._indexes[path]
                offset, length = _biff_record(records, resource_id, self._types[extension])
                return _read_at(handle, size, offset, length, f"{extension.upper()} data")
        except (OSError, RuntimeError) as exc:
            raise ScanError(f"Unable to read the supplied installation: {exc}") from exc


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

    Files CIR's Apply wrote are read as the game had them before
    (applied_files.py): its texts appended to dialog.tlk name no item.
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
        tlk_data = _read_file(tlk_path)
        tlk = _Tlk(tlk_data)
        data, applied = _applied_record(root, snapshot)
        _applied_text(root, applied, tlk_path, tlk_data, tlk)
        long_names: list[str] = []
        overrides = _override_resources(root, {"itm": ITM_TYPE}, snapshot, long_names)
        _applied_overrides(root, data, applied, overrides, snapshot)
        overrides = overrides["itm"]
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
        if long_names:
            warnings.add("Override files with names longer than eight characters were left out "
                         "(resource names have at most eight): "
                         + ", ".join(sorted(long_names, key=str.casefold)) + ".")
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
