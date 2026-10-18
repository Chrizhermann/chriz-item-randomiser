"""Build the Windows standalone package and the mod ZIP from one commit.

Players unpack the standalone package into the game folder, install CIR with
setup-cir.exe after their other mods and then double-click
cir/cir-configurator.exe. The package holds the files of the mod ZIP, WeiDU as
setup-cir.exe and the configurator executable.

The mod ZIP is the one tools/build_release.py builds, byte for byte, and is
written next to the package; CEBG installs that one, so it must stay within
CEBG's archive limits. The executable does not fit them and is only in the
package.

WeiDU is the build the installer tests use, 249.00 for x86-64, checked against
the pin below; README-WeiDU allows distributing an unmodified binary copy. The
configurator is built with cargo from an export of the same commit with the mod
ZIP's text conversion, so it embeds the bytes the mod installs. It links the C
runtime statically (players need not have the Visual C++ runtime) and is
refused when it names a folder of the build machine. Its linker writes no build
time (/Brepro), so the same Rust and Visual C++ tools build the same
executable, and --verify compares a package with a fresh build.

    python tools/build_standalone.py --commit HEAD --weidu <weidu.exe> --out <directory>
    python tools/build_standalone.py --commit <commit> --weidu <weidu.exe> --verify <package ZIP>
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Mapping

import build_release
from build_release import ReleaseError

MANIFEST_SCHEMA = "flir-standalone-manifest-v1"
TARGET = "x86_64-pc-windows-msvc"
CONFIGURATOR = "cir-configurator.exe"
# The limits CEBG applies to this mod's ZIP (chriz-bg-collection: manifest/artifacts,
# engine/src/acquire/archive.rs). Depth counts the parts of an entry's path.
CEBG_LIMITS = {"entries": 1024, "depth": 8, "entry_bytes": 4 * 1024 * 1024,
               "total_bytes": 16 * 1024 * 1024, "ratio": 100}
# The WeiDU the installer tests use (tests/README.md), pinned as CEBG pins it
# (manifest/artifacts/weidu-249-amd64.toml): weidu.exe from WeiDU's release ZIP.
WEIDU = {
    "version": "249.00",
    "weidu_version": "24900",
    "size": 1364992,
    "sha256": "ad70f5897a6d0ba4b0d226f845a9b14cf345f56cc9697ca8d05cac9fe4932c1a",
    "origin": {
        "url": "https://github.com/WeiDUorg/weidu/releases/download/v249.00/WeiDU-Windows-249-amd64.zip",
        "size": 2154755,
        "sha256": "b156910cbec69359fc2e42f6739aa959d49047d6fd3dc172f6bed88ffad8f927",
        "member": "WeiDU-Windows/weidu.exe",
    },
    "license": "GPL-2.0; README-WeiDU allows distributing an unmodified binary copy",
    "source": "https://github.com/WeiDUorg/weidu/tree/v249.00",
}
# The Visual C++ runtime and the C runtime's DLLs; only the static CRT runs on every Windows.
CRT_DLL = re.compile(r"(?i)(vcruntime|msvcp|ucrtbase|concrt|vccorlib)[^\\/]*\.dll|api-ms-win-crt-[^\\/]*\.dll")
# The release build's own flags; the source and CARGO_HOME folders are remapped as well.
RUSTFLAGS = ["-Ctarget-feature=+crt-static", "-Clink-arg=/Brepro"]
# Environment that would change the configurator's build flags, profile or output folder.
BUILD_OVERRIDES = re.compile(r"RUSTFLAGS|CARGO_ENCODED_RUSTFLAGS|CARGO_BUILD_RUSTFLAGS|CARGO_BUILD_TARGET"
                             r"|CARGO_BUILD_TARGET_DIR|CARGO_TARGET_DIR|CARGO_INCREMENTAL|CARGO_PROFILE_.*")

Configurator = Callable[[Path], tuple[bytes, dict]]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_weidu(path: Path, pin: dict = WEIDU) -> bytes:
    data = path.read_bytes()
    if len(data) != pin["size"] or sha256(data) != pin["sha256"]:
        raise ReleaseError(f"{path.name} is not WeiDU {pin['version']} for x86-64 ({pin['origin']['member']} "
                           f"from {pin['origin']['url']}: {pin['size']} bytes, sha256 {pin['sha256']})")
    return data


def setup_name(tp2: list[str]) -> str:
    """Name WeiDU so that it finds the mod's TP2: setup-<name>.exe reads <name>/<name>.tp2."""
    if len(tp2) != 1:
        raise ReleaseError(f"The standalone package installs one TP2; the mod has {len(tp2)}")
    folder, _, file = tp2[0].partition("/")
    name = file[:-4] if file.casefold().endswith(".tp2") else ""
    if not name or "/" in file or folder.casefold() != name.casefold():
        raise ReleaseError(f"setup-{name or folder}.exe would not find {tp2[0]}; WeiDU looks for <name>/<name>.tp2")
    return f"setup-{name}.exe"


def package_name(mod_manifest: dict) -> str:
    return f"{mod_manifest['archive']['root']}-v{mod_manifest['version']}-windows.zip"


def cebg_limit_problems(archive: bytes, limits: Mapping[str, int] = CEBG_LIMITS) -> list[str]:
    """What keeps CEBG from installing this ZIP, checked as CEBG reads the central directory."""
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        infos = bundle.infolist()
    problems = []
    if len(infos) > limits["entries"]:
        problems.append(f"{len(infos)} entries, CEBG allows {limits['entries']}")
    total = 0
    for info in infos:
        depth = len(info.filename.replace("\\", "/").rstrip("/").split("/"))
        if depth > limits["depth"]:
            problems.append(f"{info.filename} has depth {depth}, CEBG allows {limits['depth']}")
        if info.is_dir():
            continue
        total += info.file_size
        if info.file_size > limits["entry_bytes"]:
            problems.append(f"{info.filename} has {info.file_size} bytes, CEBG allows {limits['entry_bytes']}")
        if info.file_size > max(info.compress_size, 1) * limits["ratio"]:
            problems.append(f"{info.filename} is compressed more than {limits['ratio']} to 1")
    if total > limits["total_bytes"]:
        problems.append(f"{total} uncompressed bytes, CEBG allows {limits['total_bytes']}")
    return problems


def export_commit(repository: Path, commit: str, destination: Path) -> int:
    """Write every file of the commit as a core.autocrlf=true checkout without attributes would."""
    tree = build_release.read_tree(repository, commit)
    for path, (mode, _oid) in sorted(tree.items()):
        parts = path.split("/")
        if PurePosixPath(path).name.casefold() == ".gitattributes":
            raise ReleaseError(".gitattributes would change checkout line endings; the export does not emulate "
                               "attributes")
        if mode not in build_release.REGULAR_MODES:
            raise ReleaseError(f"{path} is not a regular file (mode {mode}); the export writes files only")
        if any(part in ("", ".", "..") or part.casefold() == ".git" or ":" in part or "\\" in part
               for part in parts):
            raise ReleaseError(f"{path} cannot be written below the export folder")
    blobs = build_release.read_blobs(repository, [oid for _mode, oid in tree.values()])
    for path, (_mode, oid) in tree.items():
        target = destination.joinpath(*path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(build_release.autocrlf_checkout(blobs[oid]))
    return len(tree)


def cargo_home(environ: Mapping[str, str] = os.environ) -> Path:
    return Path(environ["CARGO_HOME"]) if environ.get("CARGO_HOME") else Path.home() / ".cargo"


def rustup_home(environ: Mapping[str, str] = os.environ) -> Path:
    return Path(environ["RUSTUP_HOME"]) if environ.get("RUSTUP_HOME") else Path.home() / ".rustup"


def cargo_command(source: Path, home: Path, environ: Mapping[str, str]) -> tuple[list[str], dict[str, str]]:
    """The cargo call and environment that build the configurator in an exported source tree."""
    workspace = source / "configurator"
    command = ["cargo", "build", "--release", "--locked", "--bin", CONFIGURATOR[:-4], "--target", TARGET,
               "--manifest-path", str(workspace / "Cargo.toml"), "--target-dir", str(workspace / "target")]
    flags = [*RUSTFLAGS, f"--remap-path-prefix={source}=cir", f"--remap-path-prefix={home}=cargo"]
    environment = {key: value for key, value in environ.items() if not BUILD_OVERRIDES.fullmatch(key.upper())}
    environment["CARGO_ENCODED_RUSTFLAGS"] = "\x1f".join(flags)
    return command, environment


def run(command: list[str], cwd: Path, environment: Mapping[str, str]) -> str:
    try:
        result = subprocess.run(command, cwd=cwd, env=dict(environment), capture_output=True, check=False)
    except OSError as exc:
        raise ReleaseError(f"{command[0]} cannot be started ({exc}); install Rust from https://rustup.rs") from exc
    if result.returncode != 0:
        lines = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ReleaseError(f"{' '.join(command[:2])} failed:\n" + "\n".join(lines[-20:]))
    return result.stdout.decode("utf-8", "replace")


def build_configurator(source: Path) -> tuple[bytes, dict]:
    """Build cir-configurator.exe in the exported source; return it and the toolchain that built it."""
    workspace = source / "configurator"
    command, environment = cargo_command(source, cargo_home(), os.environ)
    if shutil.which(command[0], path=environment.get("PATH")) is None:
        raise ReleaseError("cargo is not on PATH; install Rust from https://rustup.rs")
    version, *details = run(["rustc", "-vV"], workspace, environment).splitlines()
    rustc = dict(line.split(": ", 1) for line in details if ": " in line)
    toolchain = {
        "rustc": version,
        "rustc_commit": rustc.get("commit-hash", ""),
        "host": rustc.get("host", ""),
        "llvm": rustc.get("LLVM version", ""),
        "cargo": run(["cargo", "-V"], workspace, environment).strip(),
        "command": " ".join(command[:command.index("--manifest-path")]),
        "rustflags": [*RUSTFLAGS, "--remap-path-prefix=<source>=cir", "--remap-path-prefix=<CARGO_HOME>=cargo"],
    }
    run(command, workspace, environment)
    return (workspace / "target" / TARGET / "release" / CONFIGURATOR).read_bytes(), toolchain


def pe_info(data: bytes) -> dict:
    """Machine, format and imported DLLs of a Windows executable."""
    try:
        header = struct.unpack_from("<I", data, 0x3C)[0]
        if data[:2] != b"MZ" or data[header:header + 4] != b"PE\0\0":
            raise ValueError("no PE signature")
        machine, count = struct.unpack_from("<HH", data, header + 4)
        optional_size = struct.unpack_from("<H", data, header + 20)[0]
        optional = header + 24
        plus = struct.unpack_from("<H", data, optional)[0] == 0x20B
        directories = optional + (112 if plus else 96)
        imports = struct.unpack_from("<I", data, directories + 8)[0]
        sections = [struct.unpack_from("<8xIIII", data, optional + optional_size + 40 * index)
                    for index in range(count)]

        def offset(rva: int) -> int:
            for size, address, raw_size, raw in sections:
                if address <= rva < address + max(size, raw_size):
                    return raw + rva - address
            raise ValueError(f"address {rva:#x} is in no section")

        dlls, at = [], offset(imports) if imports else None
        while at is not None:
            descriptor = struct.unpack_from("<5I", data, at)
            if not any(descriptor):
                break
            start = offset(descriptor[3])
            dlls.append(data[start:data.index(b"\0", start)].decode("ascii"))
            at += 20
    except (struct.error, ValueError, UnicodeDecodeError) as exc:
        raise ReleaseError(f"Not a readable Windows executable: {exc}") from exc
    return {"machine": machine, "pe32+": plus, "dlls": dlls}


def named_folders(data: bytes, folders: Iterable[str]) -> list[str]:
    """The folders a binary names, as UTF-8 or UTF-16LE text, with either separator, in any ASCII case."""
    lowered = data.lower()
    found = []
    for folder in folders:
        spellings = {folder, folder.replace("\\", "/"), folder.replace("/", "\\")}
        for spelling in spellings:
            needles = {spelling.encode("utf-8"), spelling.encode("utf-16-le")}
            lowered_needles = {spelling.lower().encode("utf-8"), spelling.lower().encode("utf-16-le")}
            if any(needle in data for needle in needles) or any(needle in lowered for needle in lowered_needles):
                found.append(folder)
                break
    return found


def build_folders(repository: Path, work: Path) -> list[str]:
    """Folders of the build machine a published executable must not name."""
    folders = {Path.home(), work, repository, cargo_home(), rustup_home(), Path(tempfile.gettempdir())}
    names = set()
    for folder in folders:
        for spelling in (folder.absolute(), folder.resolve()):
            if len(spelling.parts) > 1:
                names.add(str(spelling))
    return sorted(names)


def check_configurator(data: bytes, folders: Iterable[str]) -> list[str]:
    """Refuse an executable that is not x86-64, needs the Visual C++ runtime or names a build folder."""
    pe = pe_info(data)
    if pe["machine"] != 0x8664 or not pe["pe32+"]:
        raise ReleaseError(f"{CONFIGURATOR} is not a 64-bit x86 Windows program")
    runtime = [dll for dll in pe["dlls"] if CRT_DLL.fullmatch(dll)]
    if runtime:
        raise ReleaseError(f"{CONFIGURATOR} needs {', '.join(runtime)}, which players may not have; it must "
                           f"link the C runtime statically")
    named = named_folders(data, folders)
    if named:
        raise ReleaseError(f"{CONFIGURATOR} names a folder of the build machine: {named[0]}")
    return pe["dlls"]


def package(repository: Path, mod: tuple[str, bytes, dict], weidu: Path, *,
            configurator: Configurator = build_configurator, pin: dict = WEIDU) -> tuple[str, bytes, dict]:
    """Return (archive name, archive bytes, manifest) of the standalone package for a built mod ZIP."""
    repository = repository.resolve(strict=True)
    mod_name, mod_archive, mod_manifest = mod
    problems = cebg_limit_problems(mod_archive)
    if problems:
        raise ReleaseError(f"{mod_name} exceeds CEBG's archive limits: {'; '.join(problems)}")
    root, commit = mod_manifest["archive"]["root"], mod_manifest["commit"]
    setup, program = setup_name(mod_manifest["archive"]["tp2"]), f"{root}/{CONFIGURATOR}"
    with zipfile.ZipFile(io.BytesIO(mod_archive)) as bundle:
        files = [(info.filename, bundle.read(info)) for info in bundle.infolist()]
    for name in (setup, program):
        key = name.casefold()
        if any(other.casefold() == key or other.casefold().startswith(key + "/") for other, _ in files):
            raise ReleaseError(f"{mod_name} already has {name}")
    weidu_data = read_weidu(weidu, pin)
    with tempfile.TemporaryDirectory(prefix="cir-standalone-", ignore_cleanup_errors=True) as folder:
        work = Path(folder)
        export_commit(repository, commit, work / "source")
        executable, toolchain = configurator(work / "source")
        dlls = check_configurator(executable, build_folders(repository, work))
    files = sorted([*files, (setup, weidu_data), (program, executable)], key=lambda row: row[0].encode("utf-8"))
    stamp = build_release.commit_timestamp(repository, commit)
    name = package_name(mod_manifest)
    archive = build_release.write_archive(files, stamp, commit.encode("ascii"))
    sizes = [len(data) for _, data in files]
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "version": mod_manifest["version"],
        "commit": commit,
        "archive": {
            "name": name,
            "sha256": sha256(archive),
            "size": len(archive),
            "root": root,
            "tp2": mod_manifest["archive"]["tp2"],
            "setup": setup,
            "configurator": program,
        },
        "mod_archive": {key: mod_manifest["archive"][key] for key in ("name", "sha256", "size")},
        "build": dict(mod_manifest["build"], configurator=toolchain),
        "binaries": [
            {"path": program, "kind": "configurator", "size": len(executable), "sha256": sha256(executable),
             "commit": commit, "target": TARGET, "imports": dlls},
            {"path": setup, "kind": "weidu", "size": len(weidu_data), "sha256": sha256(weidu_data),
             "version": pin["version"], "weidu_version": pin["weidu_version"], "origin": pin["origin"],
             "license": pin["license"], "source": pin["source"]},
        ],
        "totals": {
            "files": len(files),
            "uncompressed_bytes": sum(sizes),
            "largest_file_bytes": max(sizes),
        },
        "files": [{"path": path, "size": len(data), "sha256": sha256(data)} for path, data in files],
    }
    return name, archive, manifest


def build(repository: Path, revision: str, weidu: Path, spec_data: bytes | None = None, *,
          configurator: Configurator = build_configurator, pin: dict = WEIDU):
    """Return the mod ZIP and the standalone package, each as (name, archive bytes, manifest)."""
    mod = build_release.build(repository, revision, spec_data)
    return mod, package(repository, mod, weidu, configurator=configurator, pin=pin)


def main(argv: list[str] | None = None, *, configurator: Configurator = build_configurator,
         pin: dict = WEIDU) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--spec", type=Path, help="package specification instead of the commit's own")
    parser.add_argument("--weidu", type=Path, required=True,
                        help=f"weidu.exe {WEIDU['version']} ({WEIDU['origin']['member']} in {WEIDU['origin']['url']})")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--out", type=Path, help="directory for both ZIPs and their manifests")
    action.add_argument("--verify", type=Path, help="existing standalone package to compare byte for byte")
    args = parser.parse_args(argv)
    try:
        spec = args.spec.read_bytes() if args.spec else None
        mod = build_release.build(args.repository, args.commit, spec)
        if args.verify:
            name, archive, manifest = package(args.repository, mod, args.weidu, configurator=configurator, pin=pin)
            actual = args.verify.read_bytes()
            if actual != archive:
                print(f"DIFFERENT {args.verify.name}: {build_release.describe_difference(archive, actual)}")
                return 1
            print(f"IDENTICAL {args.verify.name} sha256={manifest['archive']['sha256']}")
            return 0
        names = [mod[0], package_name(mod[2])]
        targets = [args.out / name for zip_name in names for name in (zip_name, zip_name[:-4] + ".manifest.json")]
        existing = [path.name for path in targets if path.exists()]
        if existing:
            raise ReleaseError(f"Refusing to replace {', '.join(existing)}")
        standalone = package(args.repository, mod, args.weidu, configurator=configurator, pin=pin)
        args.out.mkdir(parents=True, exist_ok=True)
        for index, (_name, archive, manifest) in enumerate((mod, standalone)):
            targets[2 * index].write_bytes(archive)
            targets[2 * index + 1].write_bytes(build_release.manifest_bytes(manifest))
    except (OSError, ReleaseError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    for name, _archive, manifest in (mod, standalone):
        print(f"BUILT {name} sha256={manifest['archive']['sha256']} size={manifest['archive']['size']} "
              f"files={manifest['totals']['files']} commit={manifest['commit']}")
    program = standalone[2]["binaries"][0]
    print(f"CONFIGURATOR {program['path']} sha256={program['sha256']} size={program['size']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
