"""Build the release ZIP and its manifest from one commit's Git objects.

Neither the working tree, the index nor local Git settings affect the output:
the same commit always gives the same bytes with the same zlib version. The
file list is tools/release-package.json in that commit, unless --spec names
another.

Text files receive the conversion that a core.autocrlf=true checkout without
attributes applies (git convert.c: gather_stats, will_convert_lf_to_crlf). The
published 8.1.1 asset carries those bytes, and the configurator reads
translation text with its line endings intact. A tree with .gitattributes is
refused rather than half emulated. Entries are sorted files without directory
entries, stamped with the commit time, with fixed attributes, Deflate level 9
and the commit ID as the archive comment.

    python tools/build_release.py --commit HEAD --out <directory>
    python tools/build_release.py --commit <commit> --verify <zip>
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

SPEC_PATH = "tools/release-package.json"
SPEC_SCHEMA = "flir-release-package-v1"
MANIFEST_SCHEMA = "flir-release-manifest-v1"
COMPRESS_LEVEL = 9
FILE_ATTRIBUTES = 0o100644 << 16  # Unix regular file, rw-r--r--
REGULAR_MODES = {"100644", "100755"}
# Control bytes git counts as non-printable: all but BS, HT, LF, FF, CR and ESC, plus DEL.
NONPRINTABLE = bytes(c for c in range(32) if c not in (8, 9, 10, 12, 13, 27)) + b"\x7f"
VERSION = re.compile(r'(?m)^[ \t]*VERSION[ \t]+(?:"([^"\r\n]*)"|~([^~\r\n]*)~)')


class ReleaseError(RuntimeError):
    """The commit or package specification cannot be released as requested."""


def git(repository: Path, *args: str, stdin: bytes | None = None) -> bytes:
    result = subprocess.run(["git", "-C", str(repository), *args], input=stdin,
                            capture_output=True, check=False)
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise ReleaseError(f"git {args[0]} failed: {message}")
    return result.stdout


def autocrlf_checkout(data: bytes) -> bytes:
    """Return the bytes a core.autocrlf=true checkout writes for this blob."""
    # gather_stats pairs CR LF before counting lone CRs and LFs.
    crlf = data.count(b"\r\n")
    lonecr = data.count(b"\r") - crlf
    lonelf = data.count(b"\n") - crlf
    # Nothing to convert, or existing CRs: git leaves the blob untouched.
    if not lonelf or lonecr or crlf:
        return data
    # Without CRs every byte is an LF, a printable or a non-printable byte.
    nonprintable = len(data) - len(data.translate(None, NONPRINTABLE))
    printable = len(data) - lonelf - nonprintable
    if data.endswith(b"\x1a"):  # A trailing EOF byte is not counted.
        nonprintable -= 1
    if b"\0" in data or (printable >> 7) < nonprintable:
        return data  # convert_is_binary
    return data.replace(b"\n", b"\r\n")


def resolve_commit(repository: Path, revision: str) -> str:
    return git(repository, "rev-parse", "--verify", "--end-of-options",
               revision + "^{commit}").decode("ascii").strip()


def read_tree(repository: Path, commit: str) -> dict[str, tuple[str, str]]:
    """Map every tree path to (mode, object id)."""
    entries = {}
    for record in git(repository, "ls-tree", "-r", "-z", "--full-tree", commit).split(b"\0"):
        if not record:
            continue
        meta, path = record.split(b"\t", 1)
        mode, _kind, oid = meta.decode("ascii").split(" ")
        try:
            entries[path.decode("utf-8")] = (mode, oid)
        except UnicodeDecodeError as exc:
            raise ReleaseError(f"Path is not UTF-8: {path!r}") from exc
    return entries


def read_blobs(repository: Path, oids: list[str]) -> dict[str, bytes]:
    if not oids:
        return {}
    unique = sorted(set(oids))
    output = git(repository, "cat-file", "--batch", stdin=("\n".join(unique) + "\n").encode("ascii"))
    blobs, offset = {}, 0
    for oid in unique:
        end = output.index(b"\n", offset)
        header = output[offset:end].decode("ascii").split(" ")
        if len(header) != 3 or header[0] != oid or header[1] != "blob":
            raise ReleaseError(f"Unexpected object while reading {oid}")
        size = int(header[2])
        blobs[oid] = output[end + 1:end + 1 + size]
        offset = end + 1 + size + 1
    return blobs


def load_spec(data: bytes) -> dict:
    try:
        spec = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"Invalid package specification: {exc}") from exc
    if not isinstance(spec, dict) or set(spec) != {"schema", "archive_root", "tp2", "include"}:
        raise ReleaseError("The package specification needs exactly schema, archive_root, tp2 and include")
    if spec["schema"] != SPEC_SCHEMA:
        raise ReleaseError(f"Unsupported package specification schema {spec['schema']!r}")
    if not isinstance(spec["archive_root"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", spec["archive_root"]):
        raise ReleaseError("archive_root must be one plain folder name")
    for key in ("tp2", "include"):
        values = spec[key]
        if not isinstance(values, list) or not values or not all(isinstance(v, str) for v in values):
            raise ReleaseError(f"{key} must be a non-empty list of paths")
        if len({v.casefold() for v in values}) != len(values):
            raise ReleaseError(f"{key} lists a path twice")
        for value in values:
            plain = value[:-1] if value.endswith("/") else value
            parts = PurePosixPath(plain).parts
            if (not plain or "\\" in value or value.startswith("/") or plain != str(PurePosixPath(plain))
                    or any(part in (".", "..") for part in parts)):
                raise ReleaseError(f"Not a plain relative path in {key}: {value!r}")
    if any(value.endswith("/") for value in spec["tp2"]):
        raise ReleaseError("tp2 must list files")
    return spec


def select_files(spec: dict, tree: dict[str, tuple[str, str]]) -> list[str]:
    """Return the sorted repository paths the specification publishes."""
    if any(PurePosixPath(path).name.casefold() == ".gitattributes" for path in tree):
        raise ReleaseError(".gitattributes would change checkout line endings; the builder does not "
                           "emulate attributes")
    selected: dict[str, str] = {}
    for rule in spec["include"]:
        matches = [path for path in tree if (path.startswith(rule) if rule.endswith("/") else path == rule)]
        if not matches:
            raise ReleaseError(f"Nothing in the commit matches {rule!r}")
        for path in matches:
            if path in selected:
                raise ReleaseError(f"{path} is selected by both {selected[path]!r} and {rule!r}")
            if tree[path][0] not in REGULAR_MODES:
                raise ReleaseError(f"{path} is not a regular file (mode {tree[path][0]})")
            selected[path] = rule
    folded: dict[str, str] = {}
    for path in selected:
        key = path.casefold()
        if key in folded:
            raise ReleaseError(f"{folded[key]} and {path} differ only by case")
        folded[key] = path
    for path in selected:
        prefix = path.casefold() + "/"
        if any(other.startswith(prefix) for other in folded):
            raise ReleaseError(f"{path} is both a file and a folder")
    tp2 = sorted(path for path in selected if path.casefold().endswith(".tp2"))
    if tp2 != sorted(spec["tp2"]):
        raise ReleaseError(f"Published TP2 files {tp2} differ from the declared {sorted(spec['tp2'])}")
    return sorted(selected, key=lambda path: path.encode("utf-8"))


def tp2_version(data: bytes) -> str:
    match = VERSION.search(data.decode("utf-8", "replace"))
    version = match and (match[1] if match[1] is not None else match[2])
    if not version or not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]*", version):
        raise ReleaseError("The TP2 has no plain VERSION")
    return version


def commit_timestamp(repository: Path, commit: str) -> tuple[int, int, int, int, int, int]:
    seconds = int(git(repository, "show", "-s", "--format=%ct", commit).decode("ascii").strip())
    stamp = datetime.fromtimestamp(seconds, timezone.utc)
    if stamp.year < 1980:
        raise ReleaseError("ZIP timestamps cannot precede 1980")
    return stamp.timetuple()[:6]


def write_archive(files: list[tuple[str, bytes]], stamp: tuple[int, ...], comment: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files:
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.create_system = 3
            info.create_version = 20
            info.extract_version = 20
            info.external_attr = FILE_ATTRIBUTES
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=COMPRESS_LEVEL)
        archive.comment = comment
    return buffer.getvalue()


def build(repository: Path, revision: str, spec_data: bytes | None = None) -> tuple[str, bytes, dict]:
    """Return (archive name, archive bytes, manifest) for one commit."""
    repository = repository.resolve(strict=True)
    commit = resolve_commit(repository, revision)
    tree = read_tree(repository, commit)
    if spec_data is None:
        if SPEC_PATH not in tree:
            raise ReleaseError(f"The commit has no {SPEC_PATH}; pass --spec")
        spec_data = read_blobs(repository, [tree[SPEC_PATH][1]])[tree[SPEC_PATH][1]]
    spec = load_spec(spec_data)
    paths = select_files(spec, tree)
    blobs = read_blobs(repository, [tree[path][1] for path in paths])
    root = spec["archive_root"]
    version = tp2_version(blobs[tree[spec["tp2"][0]][1]])
    name = f"{root}-v{version}.zip"
    files = [(f"{root}/{path}", autocrlf_checkout(blobs[tree[path][1]])) for path in paths]
    stamp = commit_timestamp(repository, commit)
    archive = write_archive(files, stamp, commit.encode("ascii"))
    with zipfile.ZipFile(io.BytesIO(archive)) as check:
        ratios = [info.file_size / max(info.compress_size, 1) for info in check.infolist()]
    sizes = [len(data) for _, data in files]
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "version": version,
        "commit": commit,
        "archive": {
            "name": name,
            "sha256": hashlib.sha256(archive).hexdigest(),
            "size": len(archive),
            "root": root,
            "tp2": [f"{root}/{path}" for path in sorted(spec["tp2"])],
        },
        "build": {
            "text": "core.autocrlf=true checkout conversion, no attributes",
            "compression": f"deflate level {COMPRESS_LEVEL}",
            "zlib": zlib.ZLIB_RUNTIME_VERSION,
            "timestamp": "%04d-%02d-%02dT%02d:%02d:%02dZ" % stamp,
        },
        "totals": {
            "files": len(files),
            "uncompressed_bytes": sum(sizes),
            "largest_file_bytes": max(sizes),
            "max_compression_ratio": round(max(ratios), 2),
        },
        "files": [{"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                  for path, data in files],
    }
    return name, archive, manifest


def manifest_bytes(manifest: dict) -> bytes:
    return (json.dumps(manifest, indent=2, ensure_ascii=True) + "\n").encode("ascii")


def describe_difference(expected: bytes, actual: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(expected)) as left, zipfile.ZipFile(io.BytesIO(actual)) as right:
            names, other = left.namelist(), right.namelist()
            if names != other:
                missing = sorted(set(names) - set(other))[:5]
                extra = sorted(set(other) - set(names))[:5]
                return f"different entries (missing {missing}, unexpected {extra})"
            for name in names:
                if left.read(name) != right.read(name):
                    return f"different content in {name}"
    except zipfile.BadZipFile:
        return "the compared file is not a ZIP archive"
    return (f"same files and contents but different archive bytes; compare zlib "
            f"{zlib.ZLIB_RUNTIME_VERSION} and the archive metadata")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--spec", type=Path, help="package specification instead of the commit's own")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--out", type=Path, help="directory for the ZIP and its manifest")
    action.add_argument("--verify", type=Path, help="existing ZIP to compare byte for byte")
    args = parser.parse_args(argv)
    try:
        spec = args.spec.read_bytes() if args.spec else None
        name, archive, manifest = build(args.repository, args.commit, spec)
        if args.verify:
            actual = args.verify.read_bytes()
            if actual != archive:
                print(f"DIFFERENT {args.verify.name}: {describe_difference(archive, actual)}")
                return 1
            print(f"IDENTICAL {args.verify.name} sha256={manifest['archive']['sha256']}")
            return 0
        args.out.mkdir(parents=True, exist_ok=True)
        targets = [args.out / name, args.out / (name[:-4] + ".manifest.json")]
        existing = [path.name for path in targets if path.exists()]
        if existing:
            raise ReleaseError(f"Refusing to replace {', '.join(existing)}")
        targets[0].write_bytes(archive)
        targets[1].write_bytes(manifest_bytes(manifest))
    except (OSError, ReleaseError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    totals = manifest["totals"]
    print(f"BUILT {name} sha256={manifest['archive']['sha256']} size={manifest['archive']['size']} "
          f"files={totals['files']} uncompressed={totals['uncompressed_bytes']} commit={manifest['commit']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
