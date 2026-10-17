"""Reproducible release packages built from Git objects (tools/build_release.py).

Synthetic repositories are built with Git plumbing, so blob bytes, modes and
commit times are exact and no working tree or local configuration is involved.
Git itself is the oracle for the checkout line-ending conversion.
"""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_release
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
        self.assertTrue({"copy/ward.itm", "copy/flrward1.bam", "languages/english/hlskull.tra",
                         "lists/endpoints/fallbacks.2da"} <= named)
        self.assertEqual(sorted(named - self.selected), [])


if __name__ == "__main__":
    unittest.main()
