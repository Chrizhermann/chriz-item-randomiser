"""Reproducible release packages built from Git objects: the mod ZIP
(tools/build_release.py) and the Windows standalone package around it
(tools/build_standalone.py).

Synthetic repositories are built with Git plumbing, so blob bytes, modes and
commit times are exact and no working tree or local configuration is involved.
Git itself is the oracle for the checkout line-ending conversion. The
standalone tests stand a synthetic executable in for the cargo build and a
synthetic WeiDU in for the pinned one.
"""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import random
import re
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_release
import build_standalone
from build_release import ReleaseError

COMMIT_TIME = 1760000000  # 2025-10-09 08:53:20 UTC
SPEC = {
    "schema": "flir-release-package-v1",
    "archive_root": "mod",
    "tp2": ["mod.tp2"],
    "include": ["mod.tp2", "lib/", "copy/item.itm"],
}
# Line-ending cases for Git's autocrlf heuristics, including the >> 7 boundary.
TEXTS = {
    "lf.txt": b"one\ntwo\n",
    "crlf.txt": b"one\r\ntwo\r\n",
    "mixed.txt": b"one\r\ntwo\n",
    "lone-cr.txt": b"one\rtwo\nthree\n",
    "zero-byte.txt": b"one\0\ntwo\n",
    "no-newline.txt": b"no newline",
    "empty.txt": b"",
    "newlines-only.txt": b"\n\n",
    "eof-marker.txt": b"one\ntwo\n\x1a",
    "printable-controls.txt": b"\t\x1b[0m\x08\x0c\n",
    "utf8.txt": "Zażółć\n".encode("utf-8"),
    "binary-controls.bin": b"\x01\x02\x03\n" * 8,
    "del.bin": b"\x7f\x7fx\n",
    "boundary-text.txt": b"a" * 128 + b"\x01\n",
    "boundary-binary.bin": b"a" * 127 + b"\x01\n",
}


def git(repository: Path, *args: str, stdin: bytes | None = None, env: dict | None = None) -> bytes:
    environment = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", **(env or {}))
    result = subprocess.run(["git", "-C", str(repository), *args], input=stdin, capture_output=True,
                            env=environment, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr.decode("utf-8", "replace"))
    return result.stdout


def make_repository(root: Path, files: dict[str, bytes | tuple[str, bytes]], *,
                    spec: dict | bytes | None = SPEC) -> tuple[Path, str]:
    """Commit exact blobs (path -> bytes, or (mode, bytes)) without a working tree."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    git(root, "config", "core.autocrlf", "false")
    entries = dict(files)
    if spec is not None:
        entries["tools/release-package.json"] = spec if isinstance(spec, bytes) else json.dumps(spec).encode()
    # Write every blob in one process and stage them all in one more.
    blobs = root / ".git" / "synthetic-blobs"
    blobs.mkdir()
    rows = [(path, *(value if isinstance(value, tuple) else ("100644", value)))
            for path, value in entries.items()]
    hashed = [index for index, (_, mode, _) in enumerate(rows) if mode != "160000"]
    for index in hashed:
        (blobs / str(index)).write_bytes(rows[index][2])
    oids = git(root, "hash-object", "-w", "--no-filters", "--stdin-paths",
               stdin="".join(f"{blobs / str(index)}\n" for index in hashed).encode()).decode().split()
    oid_of = dict(zip(hashed, oids))
    listing = "".join(f"{mode} {oid_of.get(index) or data.decode('ascii')}\t{path}\n"
                      for index, (path, mode, data) in enumerate(rows))
    env = {"GIT_INDEX_FILE": str(root / ".git" / "build-index")}
    git(root, "update-index", "--add", "--index-info", stdin=listing.encode("utf-8"), env=env)
    tree = git(root, "write-tree", env=env).decode().strip()
    stamp = f"@{COMMIT_TIME} +0200"
    commit = git(root, "commit-tree", tree, "-m", "synthetic release", env={
        "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid", "GIT_AUTHOR_DATE": stamp,
        "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_DATE": stamp}).decode().strip()
    git(root, "update-ref", "HEAD", commit)
    return root, commit


def base_files() -> dict[str, bytes]:
    files = {"mod.tp2": b"BACKUP ~mod/backup~\nVERSION ~1.2.3-test~\nBEGIN ~one~\n",
             "lib/a.tpa": b"// library\nOUTER_SET x = 1\n",
             "lib/nested/b.tph": b"// nested\r\n",
             "copy/item.itm": bytes(range(256)),
             "copy/unpublished.itm": b"not selected",
             "docs/notes.md": b"# Not published\n"}
    files.update({"lib/eol/" + name: data for name, data in TEXTS.items()})
    return files


class CheckoutConversionTests(unittest.TestCase):
    def test_matches_git_autocrlf_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            repository, commit = make_repository(Path(folder) / "repo", base_files())
            archive = git(repository, "-c", "core.autocrlf=true", "archive", "--format=tar", commit)
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                expected = {member.name: tar.extractfile(member).read()
                            for member in tar.getmembers() if member.isfile()}
            blobs = base_files()
            self.assertGreater(len(expected), len(TEXTS))
            for path, data in blobs.items():
                with self.subTest(path):
                    self.assertEqual(build_release.autocrlf_checkout(data), expected[path])

    def test_conversion_cases(self):
        convert = build_release.autocrlf_checkout
        self.assertEqual(convert(TEXTS["lf.txt"]), b"one\r\ntwo\r\n")
        self.assertEqual(convert(TEXTS["boundary-text.txt"]), b"a" * 128 + b"\x01\r\n")
        for name in ("crlf.txt", "mixed.txt", "lone-cr.txt", "zero-byte.txt", "no-newline.txt",
                     "empty.txt", "binary-controls.bin", "del.bin", "boundary-binary.bin"):
            with self.subTest(name):
                self.assertEqual(convert(TEXTS[name]), TEXTS[name])


class BuildTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.repository, self.commit = make_repository(self.folder / "repo", base_files())

    def test_archive_layout_is_fixed(self):
        name, data, manifest = build_release.build(self.repository, "HEAD")
        self.assertEqual(name, "mod-v1.2.3-test.zip")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            self.assertEqual(archive.comment, self.commit.encode("ascii"))
            stamp = datetime.fromtimestamp(COMMIT_TIME, timezone.utc).timetuple()[:6]
            for info in infos:
                self.assertEqual(info.date_time[:5], stamp[:5])
                self.assertEqual((info.create_system, info.create_version, info.extract_version),
                                 (3, 20, 20))
                self.assertEqual(info.external_attr, 0o100644 << 16)
                self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)
                self.assertEqual((info.flag_bits, info.extra), (0, b""))
                self.assertFalse(info.is_dir())
            self.assertEqual(names, sorted(names, key=lambda value: value.encode("utf-8")))
            expected = {"mod/" + path: build_release.autocrlf_checkout(blob)
                        for path, blob in base_files().items()
                        if path == "mod.tp2" or path.startswith("lib/") or path == "copy/item.itm"}
            self.assertEqual({info.filename: archive.read(info) for info in infos}, expected)
        self.assertEqual(manifest["archive"]["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(manifest["archive"]["size"], len(data))
        self.assertEqual(manifest["archive"]["tp2"], ["mod/mod.tp2"])
        self.assertEqual(manifest["commit"], self.commit)
        self.assertEqual(manifest["build"]["timestamp"], "2025-10-09T08:53:20Z")
        self.assertEqual([row["path"] for row in manifest["files"]], names)
        for row in manifest["files"]:
            self.assertEqual(row["sha256"], hashlib.sha256(expected[row["path"]]).hexdigest())
        self.assertEqual(manifest["totals"]["files"], len(names))
        self.assertEqual(manifest["totals"]["uncompressed_bytes"], sum(map(len, expected.values())))

    def cli(self, *args: str) -> tuple[int, str]:
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            code = build_release.main(["--repository", str(self.repository), *args])
        return code, output.getvalue()

    def test_builds_are_byte_identical_and_verifiable(self):
        first, second = self.folder / "first", self.folder / "second"
        for out in (first, second):
            self.assertEqual(self.cli("--out", str(out))[0], 0)
        names = sorted(path.name for path in first.iterdir())
        self.assertEqual(names, ["mod-v1.2.3-test.manifest.json", "mod-v1.2.3-test.zip"])
        for name in names:
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
        archive = first / "mod-v1.2.3-test.zip"
        self.assertEqual(self.cli("--verify", str(archive)), (0, f"IDENTICAL {archive.name} sha256="
                                                               f"{hashlib.sha256(archive.read_bytes()).hexdigest()}\n"))
        changed = bytearray(archive.read_bytes())
        changed[-1] ^= 1
        tampered = self.folder / "tampered.zip"
        tampered.write_bytes(bytes(changed))
        code, output = self.cli("--verify", str(tampered))
        self.assertEqual(code, 1)
        self.assertTrue(output.startswith("DIFFERENT tampered.zip"), output)
        code, output = self.cli("--out", str(first))
        self.assertEqual(code, 1)
        self.assertIn("Refusing to replace", output)
        self.assertEqual((first / names[1]).read_bytes(), (second / names[1]).read_bytes())

    def test_working_tree_and_local_configuration_are_ignored(self):
        _, expected, _ = build_release.build(self.repository, self.commit)
        (self.repository / "mod.tp2").write_bytes(b"VERSION ~9~\n")
        git(self.repository, "config", "core.autocrlf", "input")
        _, actual, _ = build_release.build(self.repository, self.commit)
        self.assertEqual(actual, expected)

    def test_external_specification(self):
        spec = dict(SPEC, include=["mod.tp2", "docs/"])
        _, data, manifest = build_release.build(self.repository, "HEAD", json.dumps(spec).encode())
        self.assertEqual([row["path"] for row in manifest["files"]], ["mod/docs/notes.md", "mod/mod.tp2"])


class RefusalTests(unittest.TestCase):
    def refuses(self, message, files=None, spec=SPEC):
        with tempfile.TemporaryDirectory() as folder:
            repository, _ = make_repository(Path(folder) / "repo", base_files() if files is None else files,
                                            spec=spec)
            with self.assertRaisesRegex(ReleaseError, message):
                build_release.build(repository, "HEAD")

    def test_unsafe_trees(self):
        files = base_files()
        self.refuses("gitattributes", {**files, "lib/.gitattributes": b"* text=auto\n"})
        self.refuses("not a regular file", {**files, "lib/link": ("120000", b"a.tpa")})
        self.refuses("not a regular file", {**files, "lib/module": ("160000", b"1" * 40)})
        self.refuses("differ only by case", {**files, "lib/A.tpa": b"other\n"})
        self.refuses("Published TP2 files", {**files, "lib/extra.tp2": b"BEGIN ~x~\n"})
        self.refuses("no plain VERSION", {**files, "mod.tp2": b"BEGIN ~no version~\n"})

    def test_bad_specifications(self):
        self.refuses("Nothing in the commit matches", spec=dict(SPEC, include=["mod.tp2", "lib/", "gone/"]))
        self.refuses("selected by both", spec=dict(SPEC, include=["mod.tp2", "lib/", "lib/a.tpa"]))
        self.refuses("plain relative path", spec=dict(SPEC, include=["mod.tp2", "../lib/"]))
        self.refuses("plain relative path", spec=dict(SPEC, include=["mod.tp2", "lib\\a.tpa"]))
        self.refuses("exactly schema", spec=dict(SPEC, extra=True))
        self.refuses("Unsupported", spec=dict(SPEC, schema="other"))
        self.refuses("Invalid package specification", spec=b"{not json")
        self.refuses("has no tools/release-package.json", spec=None)


WEIDU_BYTES = b"MZ synthetic WeiDU\0" * 16
WEIDU_PIN = dict(build_standalone.WEIDU, size=len(WEIDU_BYTES), sha256=hashlib.sha256(WEIDU_BYTES).hexdigest())


def make_pe(dlls: list[str], extra: bytes = b"", machine: int = 0x8664) -> bytes:
    """A minimal 64-bit Windows image whose one section holds the import directory."""
    table = 20 * (len(dlls) + 1)
    names, descriptors = b"", b""
    for dll in dlls:
        descriptors += struct.pack("<5I", 0, 0, 0, 0x1000 + table + len(names), 0)
        names += dll.encode("ascii") + b"\0"
    section = descriptors + bytes(20) + names
    optional = bytearray(240)
    struct.pack_into("<H", optional, 0, 0x20B)  # PE32+
    struct.pack_into("<I", optional, 108, 16)  # number of data directories
    struct.pack_into("<II", optional, 120, 0x1000, len(section))  # import directory
    head = (b"MZ" + bytes(0x3A) + struct.pack("<I", 0x40) + b"PE\0\0"
            + struct.pack("<HHIIIHH", machine, 1, 0, 0, 0, len(optional), 0x22) + bytes(optional)
            + struct.pack("<8sIIIIIIHHI", b".idata", len(section), 0x1000, len(section), 0x200, 0, 0, 0, 0, 0))
    return head + bytes(0x200 - len(head)) + section + extra


class StandaloneTests(unittest.TestCase):
    """The Windows package around the unchanged mod ZIP (tools/build_standalone.py)."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.files = {**base_files(), "configurator/Cargo.toml": b"[workspace]\n"}
        self.repository, self.commit = make_repository(self.folder / "repo", self.files)
        self.weidu = self.folder / "weidu.exe"
        self.weidu.write_bytes(WEIDU_BYTES)
        self.executable = make_pe(["KERNEL32.dll", "WS2_32.dll"])
        self.exports = []

    def configurator(self, source: Path) -> tuple[bytes, dict]:
        """Stand in for the cargo build: record the exported source, return the executable."""
        self.exports.append({path.relative_to(source).as_posix(): path.read_bytes()
                             for path in source.rglob("*") if path.is_file()})
        return self.executable, {"rustc": "synthetic"}

    def build(self, repository: Path | None = None):
        return build_standalone.build(repository or self.repository, "HEAD", self.weidu,
                                      configurator=self.configurator, pin=WEIDU_PIN)

    def refuses(self, message: str, repository: Path | None = None, executable: bytes | None = None):
        if executable is not None:
            self.executable = executable
        with self.assertRaisesRegex(ReleaseError, message):
            self.build(repository)

    def cli(self, *args: str) -> tuple[int, str]:
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            code = build_standalone.main(["--repository", str(self.repository), "--weidu", str(self.weidu), *args],
                                         configurator=self.configurator, pin=WEIDU_PIN)
        return code, output.getvalue()

    def test_package_holds_the_mod_zip_weidu_and_the_configurator(self):
        mod, (name, data, manifest) = self.build()
        self.assertEqual(mod, build_release.build(self.repository, "HEAD"))
        self.assertEqual(name, "mod-v1.2.3-test-windows.zip")
        with zipfile.ZipFile(io.BytesIO(mod[1])) as archive:
            expected = {info.filename: archive.read(info) for info in archive.infolist()}
        expected.update({"setup-mod.exe": WEIDU_BYTES, "mod/cir-configurator.exe": self.executable})
        stamp = datetime.fromtimestamp(COMMIT_TIME, timezone.utc).timetuple()[:6]
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            self.assertEqual(names, sorted(expected, key=lambda value: value.encode("utf-8")))
            self.assertEqual({info.filename: archive.read(info) for info in infos}, expected)
            self.assertEqual(archive.comment, self.commit.encode("ascii"))
            for info in infos:
                self.assertEqual(info.date_time[:5], stamp[:5])
                self.assertEqual((info.create_system, info.create_version, info.extract_version, info.external_attr,
                                  info.compress_type, info.flag_bits, info.extra),
                                 (3, 20, 20, 0o100644 << 16, zipfile.ZIP_DEFLATED, 0, b""))
        self.assertEqual(manifest["schema"], "flir-standalone-manifest-v1")
        self.assertEqual((manifest["version"], manifest["commit"]), ("1.2.3-test", self.commit))
        self.assertEqual(manifest["archive"], {
            "name": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "root": "mod",
            "tp2": ["mod/mod.tp2"], "setup": "setup-mod.exe", "configurator": "mod/cir-configurator.exe"})
        self.assertEqual(manifest["mod_archive"], {key: mod[2]["archive"][key] for key in ("name", "sha256", "size")})
        self.assertEqual(manifest["build"], dict(mod[2]["build"], configurator={"rustc": "synthetic"}))
        binaries = {row["path"]: row for row in manifest["binaries"]}
        self.assertEqual(sorted(binaries), ["mod/cir-configurator.exe", "setup-mod.exe"])
        for path, row in binaries.items():
            self.assertEqual((row["size"], row["sha256"]),
                             (len(expected[path]), hashlib.sha256(expected[path]).hexdigest()))
        self.assertEqual((binaries["mod/cir-configurator.exe"]["kind"], binaries["mod/cir-configurator.exe"]["commit"],
                          binaries["mod/cir-configurator.exe"]["imports"]),
                         ("configurator", self.commit, ["KERNEL32.dll", "WS2_32.dll"]))
        weidu = binaries["setup-mod.exe"]
        self.assertEqual((weidu["kind"], weidu["version"], weidu["weidu_version"], weidu["origin"]),
                         ("weidu", "249.00", "24900", build_standalone.WEIDU["origin"]))
        self.assertEqual(manifest["files"], [{"path": path, "size": len(expected[path]),
                                              "sha256": hashlib.sha256(expected[path]).hexdigest()} for path in names])
        self.assertEqual(manifest["totals"], {"files": len(names), "uncompressed_bytes": sum(map(len, expected.values())),
                                              "largest_file_bytes": max(map(len, expected.values()))})

    def test_configurator_is_built_from_the_converted_commit(self):
        self.build()
        # The repository has no working tree: every file comes from the commit's objects.
        files = {**self.files, "tools/release-package.json": json.dumps(SPEC).encode()}
        self.assertEqual(self.exports, [{path: build_release.autocrlf_checkout(data) for path, data in files.items()}])

    def test_export_matches_git_autocrlf_checkout(self):
        destination = self.folder / "export"
        count = build_standalone.export_commit(self.repository, self.commit, destination)
        archive = git(self.repository, "-c", "core.autocrlf=true", "archive", "--format=tar", self.commit)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            expected = {member.name: tar.extractfile(member).read() for member in tar.getmembers() if member.isfile()}
        actual = {path.relative_to(destination).as_posix(): path.read_bytes()
                  for path in destination.rglob("*") if path.is_file()}
        self.assertEqual(actual, expected)
        self.assertEqual(count, len(expected))

    def test_builds_are_byte_identical_and_verifiable_and_keep_the_mod_zip(self):
        first, second, mod_only = (self.folder / name for name in ("first", "second", "mod"))
        for out in (first, second):
            code, output = self.cli("--out", str(out))
            self.assertEqual(code, 0, output)
        names = sorted(path.name for path in first.iterdir())
        self.assertEqual(names, ["mod-v1.2.3-test-windows.manifest.json", "mod-v1.2.3-test-windows.zip",
                                 "mod-v1.2.3-test.manifest.json", "mod-v1.2.3-test.zip"])
        for name in names:
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
        # tools/build_release.py writes the same mod ZIP and manifest.
        with redirect_stdout(io.StringIO()):
            self.assertEqual(build_release.main(["--repository", str(self.repository), "--out", str(mod_only)]), 0)
        self.assertEqual(sorted(path.name for path in mod_only.iterdir()), names[2:])
        for path in mod_only.iterdir():
            self.assertEqual(path.read_bytes(), (first / path.name).read_bytes())
        package = first / "mod-v1.2.3-test-windows.zip"
        self.assertEqual(self.cli("--verify", str(package)),
                         (0, f"IDENTICAL {package.name} sha256={hashlib.sha256(package.read_bytes()).hexdigest()}\n"))
        self.executable += b"\0"
        code, output = self.cli("--verify", str(package))
        self.assertEqual(code, 1)
        self.assertTrue(output.startswith(f"DIFFERENT {package.name}: different content in mod/cir-configurator.exe"),
                        output)
        builds = len(self.exports)
        code, output = self.cli("--out", str(first))
        self.assertEqual(code, 1)
        self.assertIn("Refusing to replace", output)
        self.assertEqual(len(self.exports), builds)  # refused before the configurator is built

    def test_pins_and_cargo_command(self):
        self.assertEqual({key: build_standalone.WEIDU[key] for key in ("version", "weidu_version", "size", "sha256")},
                         {"version": "249.00", "weidu_version": "24900", "size": 1364992,
                          "sha256": "ad70f5897a6d0ba4b0d226f845a9b14cf345f56cc9697ca8d05cac9fe4932c1a"})
        source, home = Path("C:/build/source"), Path("C:/home/.cargo")
        command, environment = build_standalone.cargo_command(source, home, {
            "PATH": "tools", "CARGO_HOME": "C:/home/.cargo", "RUSTFLAGS": "-Cdebuginfo=2",
            "CARGO_ENCODED_RUSTFLAGS": "-Copt-level=0", "CARGO_PROFILE_RELEASE_DEBUG": "true",
            "CARGO_TARGET_DIR": "elsewhere", "CARGO_BUILD_TARGET": "i686-pc-windows-msvc", "CARGO_INCREMENTAL": "1",
            "CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER": "lld-link"})
        workspace = source / "configurator"
        self.assertEqual(command, ["cargo", "build", "--release", "--locked", "--bin", "cir-configurator",
                                   "--target", "x86_64-pc-windows-msvc", "--manifest-path",
                                   str(workspace / "Cargo.toml"), "--target-dir", str(workspace / "target")])
        self.assertEqual(environment, {
            "PATH": "tools", "CARGO_HOME": "C:/home/.cargo", "CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER": "lld-link",
            "CARGO_ENCODED_RUSTFLAGS": "\x1f".join(["-Ctarget-feature=+crt-static", "-Clink-arg=/Brepro",
                                                    f"--remap-path-prefix={source}=cir",
                                                    f"--remap-path-prefix={home}=cargo"])})

    def test_weidu_must_be_the_pinned_build(self):
        for data in (WEIDU_BYTES[:-1] + b"!", WEIDU_BYTES + b"\0"):
            self.weidu.write_bytes(data)
            self.refuses("weidu.exe is not WeiDU 249.00 for x86-64")
        self.assertEqual(self.exports, [])

    def test_configurator_must_run_on_any_windows(self):
        self.assertEqual(build_standalone.pe_info(make_pe(["KERNEL32.dll", "ole32.dll"])),
                         {"machine": 0x8664, "pe32+": True, "dlls": ["KERNEL32.dll", "ole32.dll"]})
        self.refuses("VCRUNTIME140.dll, which players may not have",
                     executable=make_pe(["KERNEL32.dll", "VCRUNTIME140.dll"]))
        self.refuses("needs api-ms-win-crt-runtime-l1-1-0.dll", executable=make_pe(["api-ms-win-crt-runtime-l1-1-0.dll"]))
        self.refuses("not a 64-bit x86 Windows program", executable=make_pe(["KERNEL32.dll"], machine=0xAA64))
        self.refuses("Not a readable Windows executable", executable=b"MZ and nothing else")

    def test_configurator_must_not_name_build_folders(self):
        spellings = {"utf-8": lambda path: path.encode("utf-8"), "utf-16": lambda path: path.encode("utf-16-le"),
                     "upper": lambda path: path.upper().encode("utf-8"),
                     "slashes": lambda path: path.replace("\\", "/").encode("utf-16-le")}
        for label, spell in spellings.items():
            def configurator(source: Path, spell=spell):
                return make_pe(["KERNEL32.dll"], extra=b"panic in " + spell(str(source / "cir-config" / "lib.rs"))), {}
            with self.subTest(label), self.assertRaisesRegex(ReleaseError, "names a folder of the build machine"):
                build_standalone.build(self.repository, "HEAD", self.weidu, configurator=configurator, pin=WEIDU_PIN)
        for folder in (Path.home(), self.repository):
            with self.subTest(folder=folder.name):
                self.refuses("names a folder of the build machine",
                             executable=make_pe(["KERNEL32.dll"], extra=str(folder).encode("utf-16-le")))
        self.assertEqual(build_standalone.named_folders("C:\\Build\\Tree".encode("utf-16-le"), ["C:\\Build", "D:\\x"]),
                         ["C:\\Build"])

    def test_package_names_are_free_and_found_by_weidu(self):
        repository, _ = make_repository(self.folder / "taken", {**self.files, "CIR-Configurator.exe": b"MZ"},
                                        spec=dict(SPEC, include=[*SPEC["include"], "CIR-Configurator.exe"]))
        self.refuses("mod-v1.2.3-test.zip already has mod/cir-configurator.exe", repository)
        repository, _ = make_repository(self.folder / "renamed", self.files, spec=dict(SPEC, archive_root="pkg"))
        self.refuses("setup-mod.exe would not find pkg/mod.tp2", repository)
        repository, _ = make_repository(self.folder / "two", {**self.files, "lib/extra.tp2": b"BEGIN ~x~\n"},
                                        spec=dict(SPEC, tp2=["mod.tp2", "lib/extra.tp2"]))
        self.refuses("installs one TP2; the mod has 2", repository)
        self.assertEqual(self.exports, [])

    def test_mod_zip_must_stay_within_cebg_limits(self):
        repository, _ = make_repository(self.folder / "large", {
            **self.files, "lib/large.bin": random.Random(1).randbytes(4 * 1024 * 1024 + 1)})
        self.refuses("mod-v1.2.3-test.zip exceeds CEBG's archive limits: mod/lib/large.bin has 4194305 bytes, "
                     "CEBG allows 4194304", repository)
        self.assertEqual(self.exports, [])

    def test_export_writes_regular_files_only(self):
        repository, _ = make_repository(self.folder / "link", {**self.files, "docs/link": ("120000", b"notes.md")})
        self.refuses("docs/link is not a regular file", repository)
        self.assertEqual(self.exports, [])
        repository, commit = make_repository(self.folder / "attributes",
                                             {**self.files, "docs/.gitattributes": b"* -text\n"})
        with self.assertRaisesRegex(ReleaseError, "gitattributes"):
            build_standalone.export_commit(repository, commit, self.folder / "export")


class CebgLimitTests(unittest.TestCase):
    """CEBG's limits for the mod ZIP, checked on its central directory as CEBG does."""

    @staticmethod
    def archive(entries: list[tuple[str, bytes]], compression: int = zipfile.ZIP_STORED) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression) as archive:
            for name, data in entries:
                archive.writestr(name, data)
        return buffer.getvalue()

    def test_limits_are_cebgs(self):
        self.assertEqual(build_standalone.CEBG_LIMITS, {"entries": 1024, "depth": 8, "entry_bytes": 4194304,
                                                        "total_bytes": 16777216, "ratio": 100})

    def test_entries_and_depth(self):
        problems = build_standalone.cebg_limit_problems
        self.assertEqual(problems(self.archive([(f"mod/{index}.txt", b"x") for index in range(1024)])), [])
        self.assertEqual(problems(self.archive([(f"mod/{index}.txt", b"x") for index in range(1025)])),
                         ["1025 entries, CEBG allows 1024"])
        self.assertEqual(problems(self.archive([("a/b/c/d/e/f/g/h.txt", b"x"), ("a/b/c/d/e/f/g/h/", b"")])), [])
        self.assertEqual(problems(self.archive([("a/b/c/d/e/f/g/h/i.txt", b"x")])),
                         ["a/b/c/d/e/f/g/h/i.txt has depth 9, CEBG allows 8"])

    def test_sizes_and_ratio(self):
        problems = build_standalone.cebg_limit_problems
        mib = 1024 * 1024
        self.assertEqual(problems(self.archive([("mod/four.bin", bytes(4 * mib)), ("mod/empty.txt", b"")])), [])
        self.assertEqual(problems(self.archive([("mod/big.bin", bytes(4 * mib + 1))])),
                         ["mod/big.bin has 4194305 bytes, CEBG allows 4194304"])
        self.assertEqual(problems(self.archive([(f"mod/{index}.bin", bytes(4 * mib)) for index in range(4)]
                                               + [("mod/more.bin", b"x")])),
                         ["16777217 uncompressed bytes, CEBG allows 16777216"])
        self.assertEqual(problems(self.archive([("mod/zeros.bin", bytes(mib))], zipfile.ZIP_DEFLATED)),
                         ["mod/zeros.bin is compressed more than 100 to 1"])


class RepositoryPackageTests(unittest.TestCase):
    """The repository's own specification covers what cir.tp2 installs and the configurator reads."""

    def setUp(self):
        self.spec = build_release.load_spec((ROOT / build_release.SPEC_PATH).read_bytes())
        listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "-c", "-o", "--exclude-standard"],
                                capture_output=True, check=True).stdout.decode("utf-8")
        self.tree = {path: ("100644", "") for path in listed.split("\0") if path and (ROOT / path).is_file()}
        self.selected = set(build_release.select_files(self.spec, self.tree))

    def test_specification_selects_existing_files(self):
        self.assertEqual((self.spec["archive_root"], self.spec["tp2"]), ("cir", ["cir.tp2"]))
        self.assertTrue({"LICENSE", "cir.ini", "readme-cir.html", "tools/flir_config/ui/index.html"} <= self.selected)
        self.assertFalse([path for path in self.selected if path.startswith(("tests/", "docs/"))])
        self.assertNotIn("tools/probes/FLPRDrop.lua", self.selected)
        # Lab automation is staged by labs only, never installed or published.
        self.assertEqual(sorted(path for path in self.selected if path.startswith("tools/lab/")), [])

    def test_installed_sources_are_published(self):
        # cir.tp2 names files from the archive root, the libraries from %MOD_FOLDER%;
        # names built from variables (%LANGUAGE%, %s) are covered by their folders.
        named = re.findall(r"~cir/([^~%]+)~", (ROOT / "cir.tp2").read_text(encoding="utf-8"))
        for library in ("lib/campaign_runtime.tpa", "lib/campaign_patches.tpa"):
            named += re.findall(r"~%MOD_FOLDER%/([^~%]+)~", (ROOT / library).read_text(encoding="utf-8"))
        self.assertTrue({"lib/campaign_runtime.tpa", "lib/campaign_patches.tpa", "prototype/M_FLPR.lua",
                         "copy/flbhcryp.itm", "languages/polish/in-game.tra"} <= set(named))
        # WeiDU creates the backup folder; the languages folder is read whole.
        self.assertEqual(sorted(set(named) - self.selected - {"backup", "languages"}), [])

    def test_english_texts_are_utf8(self):
        # cir.tp2 converts only translations; English game text must already be UTF-8.
        texts = sorted(path for path in self.selected
                       if re.fullmatch(r"languages/(bgee/)?english/[^/]+\.tra", path))
        self.assertIn("languages/english/in-game.tra", texts)
        for path in texts:
            with self.subTest(path=path):
                (ROOT / path).read_bytes().decode("utf-8")

    def test_configurator_data_is_published(self):
        sources = "".join(path.read_text(encoding="utf-8") for path in (ROOT / "tools/flir_config").glob("*.py"))
        # Literal names (a list row's ":line" removed); names built in code are added below.
        named = {re.sub(r":\d+$", "", path)
                 for path in re.findall(r'"((?:copy|languages|lib|lists)/[^"{}]+)"', sources)
                 if not path.endswith("/")}
        named |= {f"lists/{kind}/{family}/{campaign}.2da" for kind in ("items", "locations")
                  for family in ("base", "mode2") for campaign in ("bg1", "bg2")}
        named |= {f"lists/{kind}/{campaign}.2da" for kind in ("sources/base", "endpoints/base", "groups")
                  for campaign in ("bg1", "bg2")}
        from flir_config.templates import PRESETS
        named |= {f"lists/presets/{preset}/{kind}/{family}/{campaign}.2da"
                  for preset, spec in PRESETS.items() for campaign in spec["campaigns"]
                  for kind in ("items", "locations") for family in ("base", "mode2")}
        named |= {"lib/fl#bg1pal.tpa", "copy/flbhcryp.itm", "languages/english/ppdjinn.tra"}
        # Kangaxx's texts are read by name in the game's language (translations.texts),
        # English where it has no translation; the languages folder is published whole.
        from flir_config.quest_options import KANGAXX_LINES
        named |= {f"languages/english/{file}" for file, _ in KANGAXX_LINES}
        self.assertTrue({"copy/ward.itm", "copy/flrward1.bam", "languages/english/hlskull.tra",
                         "lists/endpoints/fallbacks.2da"} <= named)
        self.assertEqual(sorted(named - self.selected), [])

    def test_mod_zip_fits_cebg_and_setup_cir_finds_the_tp2(self):
        # The release bytes: a CRLF checkout keeps its line endings, an LF one is converted.
        files = [(f"cir/{path}", build_release.autocrlf_checkout((ROOT / path).read_bytes()))
                 for path in sorted(self.selected)]
        archive = build_release.write_archive(files, (2026, 1, 1, 0, 0, 0), b"")
        self.assertEqual(build_standalone.cebg_limit_problems(archive), [])
        self.assertEqual(build_standalone.setup_name([f"cir/{path}" for path in self.spec["tp2"]]), "setup-cir.exe")


if __name__ == "__main__":
    unittest.main()
