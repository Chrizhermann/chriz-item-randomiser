"""Apply exported settings to a game where CIR's runtime is installed.

Runtime installation is separate. Keep exported revisions for older saves;
publish the next-game pointer last, with the game closed.
"""
from pathlib import Path, PurePosixPath
import functools
import hashlib
import os
import re
import shutil
from tempfile import TemporaryDirectory

from . import applied_files
from .campaigns import read_campaign, _lua_upgrade, _store_path, _write_immutable
from .profiles import HEX256, ProfileError, canonical_bytes, ensure_output_outside_game, read_json
from .resources import ScanError, _Tlk

# The files of the latest export in its folder. Apply copies only these, so a
# file an older export left there never reaches the game.
EXPORT_FILES = "export-files.json"
EXPORT_FILES_SCHEMA = "flir-export-files-v1"


def _gate_revisions(path: Path) -> set[str]:
    if not path.exists():
        return set()
    text = re.sub(r"\\([0-9]{3})", lambda m: chr(int(m[1])), path.read_text(encoding="utf-8"))
    if not re.fullmatch(r'return\s+(?:"[a-f0-9]{64}"|\{\s*(?:\[\d+\]\s*=\s*"[a-f0-9]{64}"\s*,?\s*)*\})\s*', text):
        raise ProfileError("Unrecognized source-gate settings; keep the existing test configuration")
    return set(re.findall(r'"([a-f0-9]{64})"', text))


# The game, its loader, WeiDU and mod installers (setup-<mod>.exe), by the
# file name of the process in any case.
_GAME_OR_INSTALLER = re.compile(r"(?:baldur|infinityloader|weidu)(?:\.exe|\Z)|setup-.*\.exe\Z",
                                re.IGNORECASE | re.ASCII | re.DOTALL)


@functools.cache
def _kernel32():
    """kernel32 with the signatures of the calls that list running programs,
    and its PROCESSENTRY32W."""
    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):  # PROCESSENTRY32W
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("process", wintypes.DWORD),
                    ("heap", ctypes.c_size_t), ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", wintypes.LONG), ("flags", wintypes.DWORD),
                    ("name", wintypes.WCHAR * 260)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for step in (kernel32.Process32FirstW, kernel32.Process32NextW):
        step.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry))
        step.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD))
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    return kernel32, ProcessEntry


def _listed_executable(process_id: int) -> list[str | None]:
    """The executable of a process the snapshot listed, or None where Windows
    does not tell it; nothing for one that has ended since."""
    import ctypes
    from ctypes import wintypes

    kernel32, _ = _kernel32()
    query_limited_information, invalid_parameter, still_active = 0x1000, 87, 259
    process = kernel32.OpenProcess(query_limited_information, False, process_id)
    if not process:
        # One that has ended, and that no handle keeps, is gone.
        return [] if ctypes.get_last_error() == invalid_parameter else [None]
    try:
        size = wintypes.DWORD(32768)
        path = ctypes.create_unicode_buffer(size.value)
        known = kernel32.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size))
        # One that has ended, kept by another program's handle, no longer runs;
        # Windows may not tell its file then.
        code = wintypes.DWORD()
        if kernel32.GetExitCodeProcess(process, ctypes.byref(code)) and code.value != still_active:
            return []
        return [path.value if known else None]
    finally:
        kernel32.CloseHandle(process)


def _game_or_installer_executables() -> list[str | None]:
    """The executable of each running game, loader, WeiDU or mod installer;
    None where Windows does not tell it, such as for another user's process.

    Windows' process snapshot, not WMI: other programs can keep WMI busy
    enough that it cancels the query.
    """
    import ctypes
    from ctypes import wintypes

    kernel32, ProcessEntry = _kernel32()
    snapshot_processes, no_more_files = 0x2, 18

    snapshot = kernel32.CreateToolhelp32Snapshot(snapshot_processes, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    executables = []
    try:
        entry = ProcessEntry(size=ctypes.sizeof(ProcessEntry))
        listed = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while listed:
            if _GAME_OR_INSTALLER.match(entry.name):
                executables += _listed_executable(entry.process)
            listed = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        if ctypes.get_last_error() != no_more_files:
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(snapshot)
    return executables


def require_closed(game: Path) -> None:
    """Refuse while the game, its loader, WeiDU or a mod installer runs from
    <game>, or from a place Windows does not tell."""
    if os.name != "nt":
        raise ProfileError("Applying settings currently works on Windows only")
    try:
        executables = _game_or_installer_executables()
    except OSError as exc:
        raise ProfileError("Could not check whether the game is closed; settings were not applied") from exc
    for executable in executables:
        if not executable or Path(executable).resolve().is_relative_to(game.resolve()):
            raise ProfileError("Close this game and its installer before applying settings")


# cir.tp2 installs the runtime with this marker and UI
# adapter, before any configuration exists. A hand-staged test runtime has no
# marker and is recognised by its existing next-game pointer instead.
INSTALLED_RUNTIME = ("override/flrandomiser3.mrk", "override/M_FLPRUI.lua")
# Applied settings and kept campaign revisions; prototype/M_FLPR.lua reads them
# here. Apply also records there the files it writes elsewhere in the game.
CAMPAIGN_DATA = applied_files.DATA


# Item Randomiser's own randomisation (Mode 1 or Mode 2) removes items when it is
# installed and assigns them itself; its More Spell Shield scrolls (component 560)
# change the stock a campaign restores. cir.tp2 refuses to install after them, but
# Item Randomiser does not know CIR and can be installed later.
ITEM_RANDOMISER_MARKERS = ("override/flrandomiser1.mrk", "override/flrandomiser2.mrk")


def item_randomiser_conflict(game: Path) -> str | None:
    if any((game / name).is_file() for name in ITEM_RANDOMISER_MARKERS):
        return "Item Randomiser's randomisation (Mode 1 or Mode 2)"
    log = game / "WeiDU.log"
    entries = re.findall(rb"(?im)^\s*~([^~\r\n]+)~\s+#\d+\s+#(\d+)\b", log.read_bytes()) if log.is_file() else []
    # As MOD_IS_INSTALLED randomiser.tp2 560, independent of path, prefix and language.
    if any(path.replace(b"\\", b"/").rsplit(b"/", 1)[-1].lower() in (b"randomiser.tp2", b"setup-randomiser.tp2")
           and int(component) == 560 for path, component in entries):
        return "Item Randomiser's More Spell Shield scrolls"
    return None


def validate_runtime(game: Path, catalog: dict) -> Path:
    game = game.resolve(strict=True)
    if game != Path(catalog["game_root"]).resolve():
        raise ProfileError("The activation game must be the game used by this scan")
    conflict = item_randomiser_conflict(game)
    if conflict:
        raise ProfileError(f"Chriz Item Randomiser cannot be combined with {conflict}; "
                           "uninstall it from this game before applying settings")
    required = ["override/M_FLPR.lua", "override/FLPRRun.menu"]
    if not all((game / name).is_file() for name in INSTALLED_RUNTIME):
        required.append(CAMPAIGN_DATA + "/next.lua")
    for name in required:
        if not (game / name).is_file():
            raise ProfileError("Install Chriz Item Randomiser in this game first: " + name)
    return game


def adopt_revisions(game: Path, output: Path) -> list[str]:
    """Take the campaign revisions applied in <game> into the export folder <output>.

    Saved games use the revisions the game holds, whichever configuration
    workspace exported them. An export keeps every revision of its folder for
    them and refuses where Apply would change what they need
    (native_export.export_native), so a workspace that did not export them,
    such as a new one after Windows was reinstalled, takes them from the game
    before it exports: each file as the game has it, never replacing another
    version of it, with the pristine area copies the runtime compares them
    with. Returns the revisions the folder did not have.
    """
    game = game.resolve()
    output = ensure_output_outside_game(output, {"game_root": str(game)})
    data = game / CAMPAIGN_DATA
    store = data / "campaign-store"
    keys = sorted(path.stem for path in (store / "revisions").glob("*.json") if HEX256.fullmatch(path.stem))
    missing = sorted(_gate_revisions(data / "source-gate.lua") - set(keys))
    if missing:
        raise ProfileError(f"This game's settings list campaign revision {missing[0][:12]} for older saves, but "
                           f"its files are missing from {CAMPAIGN_DATA}/campaign-store/revisions; "
                           "restore them before applying")
    for key in keys:
        try:
            read_campaign(store, key)
        except (OSError, ProfileError) as exc:
            raise ProfileError(f"This game's campaign revision {key[:12]} cannot be read: {exc}") from exc
    exported = output / "campaign-store"
    planned, adopted = [], set()
    for entry in sorted((store / "revisions").iterdir(), key=lambda path: path.name) if keys else ():
        key, dot, _ = entry.name.partition(".")
        if not dot or key not in keys:
            continue
        files = (sorted((path for path in entry.rglob("*") if path.is_file()), key=lambda path: path.as_posix())
                 if entry.is_dir() else [entry])
        for path in files:
            relative = path.relative_to(store)
            content = _store_path(store, relative).read_bytes()
            target = _store_path(exported, relative)
            if target.exists():
                existing = target.read_bytes()
                # Either may still be the refused Lua rendering of the same data;
                # the export publishes the current one.
                if existing != content and not (_lua_upgrade(path, existing, content) or
                                                _lua_upgrade(path, content, existing)):
                    raise ProfileError(
                        f"Campaign revision {key[:12]} in this configuration workspace differs from the game's "
                        f"({path.relative_to(entry.parent).as_posix()}); saved games need the game's, so nothing "
                        f"was applied. Move {output} aside, then apply again")
                continue
            # A revision's manifest last: the export reads a revision only once it is complete.
            manifest = relative.name == key + ".json"
            planned.append((manifest, relative.as_posix(), target, content))
            if manifest:
                adopted.add(key)
    for _, _, target, content in sorted(planned, key=lambda row: row[:2]):
        _write_immutable(target, content)
    # The runtime compares each revision's guarded areas with the one pristine
    # copy the game keeps of them; the export checks the revisions it keeps
    # against these copies.
    for path in sorted(data.glob("*.pristine.are"), key=lambda path: path.name):
        if not path.is_file():
            continue
        content = _store_path(data, Path(path.name)).read_bytes()
        target = _store_path(output, Path(path.name))
        if not target.is_file() or target.read_bytes() != content:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    return sorted(adopted)


def _applied(game: Path, changes: dict[Path, bytes], paths: dict[Path, str]) -> dict[Path, bytes]:
    """The copies of earlier files and the record to write with <changes> (applied_files.py).

    <paths> names, by their path in the game, the changes outside CIR's folder.
    A file that still holds what Apply wrote last keeps the earlier file its
    record names; any other file that Apply replaces is the earlier file now,
    since a scan reads it as it is.
    """
    data = game.resolve() / CAMPAIGN_DATA
    record = data / applied_files.RECORD
    files = applied_files.read(record) if record.is_file() else {}
    writes = {}
    for target, path in sorted(paths.items(), key=lambda row: row[1]):
        name = applied_files.find(files, path) or path
        entry = files.get(name)
        current = target.read_bytes() if target.is_file() else None
        if entry is not None and current is not None and hashlib.sha256(current).hexdigest() == entry["written"]:
            before = entry["before"]
        elif current is None:
            before = None
        else:
            before = {"sha256": hashlib.sha256(current).hexdigest(), "size": len(current)}
            if applied_files.is_text(name):
                # Apply only appends to game text: the earlier file is its first
                # entries. The export read it moments ago; one that is no game
                # text now changed since, and replacing it would lose that change.
                try:
                    before["strings"] = _Tlk(current).count
                except ScanError as exc:
                    raise ProfileError("Game text changed since export; apply again: " + path) from exc
            else:
                writes[_store_path(data, Path(applied_files.BEFORE, *name.split("/")))] = current
        files[name] = {"before": before, "written": hashlib.sha256(changes[target]).hexdigest()}
    if paths:
        content = canonical_bytes({"schema": applied_files.SCHEMA, "files": files})
        if not record.is_file() or record.read_bytes() != content:
            writes[record] = content
    return writes


def deploy_export(game: Path, output: Path) -> None:
    """Copy the existing export layout without touching saves or ordinary stock.

    The files it writes outside CIR's folder, and what the game had there
    before, go to CIR's record of them (applied_files.py), so that a scan reads
    them as the game had them before.
    """
    require_closed(game)
    try:
        listing = read_json(output / EXPORT_FILES)
    except (OSError, ProfileError) as exc:
        raise ProfileError("The runtime export is incomplete; export the settings again") from exc
    names = listing.get("files") if listing.get("schema") == EXPORT_FILES_SCHEMA else None
    if (not isinstance(names, list) or not all(isinstance(name, str) for name in names) or
            not {"next.lua", "source-gate.lua"} <= set(names) or
            not all((output / name).is_file() for name in names)):
        raise ProfileError("The runtime export is incomplete; export the settings again")
    missing = sorted(_gate_revisions(game / CAMPAIGN_DATA / "source-gate.lua") -
                     _gate_revisions(output / "source-gate.lua"))
    if missing:
        raise ProfileError(f"This export is missing campaign revision {missing[0][:12]}, which older saves of "
                           "this game need; apply the settings again")
    changes, outside = {}, {}
    for name in sorted(names):
        relative = PurePosixPath(name)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ProfileError("An export destination is outside the game folder")
        source = output.joinpath(*relative.parts)
        relative = Path(*relative.parts)
        if relative.parts[0] == "resource-overrides":
            relative = Path("override", *relative.parts[1:])
        elif relative.parts[0] == "game-overrides":
            relative = Path(*relative.parts[1:])
        else:
            relative = Path(CAMPAIGN_DATA) / relative
        target = (game / relative).resolve()
        if not target.is_relative_to(game.resolve()):
            raise ProfileError("An export destination is outside the game folder")
        inside = applied_files.key(relative.parts[0]) == CAMPAIGN_DATA
        if inside and len(relative.parts) > 1 and applied_files.key(relative.parts[1]) in (applied_files.RECORD,
                                                                                         applied_files.BEFORE):
            raise ProfileError("An export file would replace Chriz Item Randomiser's record of the files "
                               "Apply writes: " + relative.as_posix())
        content = source.read_bytes()
        if target in changes and changes[target] != content:
            raise ProfileError("Conflicting export files: " + str(relative))
        if target.exists():
            existing = target.read_bytes()
            if existing == content:
                continue
            # A refused Lua rendering is replaced by the current one of the same data.
            if (relative.as_posix().startswith(CAMPAIGN_DATA + "/campaign-store/revisions/") and
                    not _lua_upgrade(source, existing, content)):
                raise ProfileError("An older campaign revision differs: " + str(relative))
        changes[target] = content
        if not inside and applied_files.is_game_path(relative.as_posix()):
            outside[target] = relative.as_posix()
    data = game.resolve() / CAMPAIGN_DATA
    record, pointer = data / applied_files.RECORD, data / "next.lua"
    # The earlier files first, the record after the files it names, the next-game pointer last.
    writes = _applied(game, changes, outside)
    order = {**{path: 0 for path in writes}, record: 2, pointer: 3}
    changes.update(writes)
    ordered = sorted(changes, key=lambda path: (order.get(path, 1), str(path)))
    # Roll back an actual copy error; no persistent lab-recovery framework.
    with TemporaryDirectory(prefix="apply-backup-", dir=output.parent) as temporary:
        backups = {}
        written = []
        try:
            for index, target in enumerate(ordered):
                backup = Path(temporary) / str(index) if target.exists() else None
                if backup:
                    shutil.copyfile(target, backup)
                backups[target] = backup
                target.parent.mkdir(parents=True, exist_ok=True)
                written.append(target)
                target.write_bytes(changes[target])
        except OSError:
            for target in reversed(written):
                if backups[target]:
                    shutil.copyfile(backups[target], target)
                else:
                    target.unlink(missing_ok=True)
            raise
