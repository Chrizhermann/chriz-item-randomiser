"""Replay the reference tests' configurator calls through the Rust port.

Runs the Python test suite (tests/test_*.py) with the reference implementation
as usual. Every call the tests make to the configurator library -- game and
world scans, preset drafts and fitting with left-out rows, binding, campaign
compile and publish, native export -- is also run by the Rust port on the same
inputs and the same folder state. Results, errors and written files must match
exactly; the synthetic games are the ones the reference tests build.

    python configurator/parity/replay_tests.py [-p PATTERN] [-k NAME] [--report FILE]

Writing calls run the Rust port first on the real folders, which are then put
back as they were before the reference runs. A call is not compared (and is
counted as skipped, with the reason) when the test has patched the reference
in a way the Rust port cannot mirror, when an input is not plain JSON, or when
it writes outside the temporary folder or into a folder holding links or
read-only files. Patched hash tables are passed on as the Rust port's
test-only pin overrides; a patched `resolve_template` as the drafts it returns;
a patched `translations.LANGUAGES_DIR` as the folder the port reads its
translations from; a patched lists root (`templates.ROOT` and
`original_catalog.ROOT`) as the folder the port reads its tables from,
patched preset campaigns (`CAMPAIGNS`, `templates._PARTS`,
`platform.PRESET_CAMPAIGNS`) as the port's, and patched Lua constant limits
(`campaigns._LUAJIT_CONSTANTS`, `campaigns._LUA_PART`) as the port's.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import functools
import inspect
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

import common
from common import (ROOT, RustRunner, first_difference, is_temporary, json_native, outcome_line,
                    restore_tree, snapshot_tree, tree_differences)

STATE = threading.local()
ACTIVE_PATCHES: list = []
ACTIVE_DICTS: list = []
RUNNER: RustRunner | None = None
CURRENT_TEST = ["(setup)"]
COMPARED: Counter = Counter()
SKIPPED: Counter = Counter()
MISMATCHES: list[dict] = []
PIN_TABLES: dict[int, str] = {}
PIN_STARTUP: dict[str, dict] = {}
# Reference data tables whose single entries a test may replace (patch.dict).
DATA_TABLES: dict[int, str] = {}
DATA_STARTUP: dict[str, dict] = {}
# Patched names that cannot change a recorded call's result.
IRRELEVANT_PATCHES = {("flir_config.activation", "require_closed")}
# The lists root and the preset campaigns: passed on to the Rust port.
LIST_ROOTS = {("flir_config.templates", "ROOT"), ("flir_config.original_catalog", "ROOT")}
# The Lua constant limits a test lowers; the port takes both as CIR_PARITY_LUA_LIMITS.
LUA_LIMITS = {("flir_config.campaigns", "_LUAJIT_CONSTANTS"), ("flir_config.campaigns", "_LUA_PART")}
CAMPAIGN_NAMES = {("flir_config.templates", "CAMPAIGNS"), ("flir_config.platform", "CAMPAIGNS")}
STANDARD_MODULES = {"os", "os.path", "ntpath", "posixpath", "shutil", "builtins", "io", "pathlib",
                    "tempfile", "hashlib", "json", "struct", "stat"}


def track_patches() -> None:
    """Keep the list of active `unittest.mock` patches current."""
    enter, leave = mock._patch.__enter__, mock._patch.__exit__

    def tracked_enter(self):
        result = enter(self)
        ACTIVE_PATCHES.append(self)
        return result

    def tracked_exit(self, *info):
        if self in ACTIVE_PATCHES:
            ACTIVE_PATCHES.remove(self)
        return leave(self, *info)

    mock._patch.__enter__, mock._patch.__exit__ = tracked_enter, tracked_exit
    apply, undo = mock._patch_dict._patch_dict, mock._patch_dict._unpatch_dict

    def tracked_apply(self):
        result = apply(self)
        ACTIVE_DICTS.append(self)
        return result

    def tracked_undo(self):
        if self in ACTIVE_DICTS:
            ACTIVE_DICTS.remove(self)
        return undo(self)

    mock._patch_dict._patch_dict, mock._patch_dict._unpatch_dict = tracked_apply, tracked_undo


def register_pin_tables() -> None:
    from flir_config import mode2_rewards, quest_options
    for name, table in (("quest_options.ORIGINAL_HASHES", quest_options.ORIGINAL_HASHES),):
        PIN_TABLES[id(table)] = name
        PIN_STARTUP[name] = dict(table)
    for name, table in (("mode2_rewards.REWARDS", mode2_rewards.REWARDS),
                        ("mode2_rewards.TRANSFER_SPECS", mode2_rewards.TRANSFER_SPECS)):
        DATA_TABLES[id(table)] = name
        DATA_STARTUP[name] = dict(table)


def plain(value):
    """A table entry as JSON (tuples become lists, as in the Rust tables);
    None when it holds anything else that JSON cannot carry."""
    if isinstance(value, (list, tuple)):
        items = [plain(item) for item in value]
        return None if any(item is None and original is not None for item, original in zip(items, value)) else items
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            return None
        items = {key: plain(item) for key, item in value.items()}
        return None if any(items[key] is None and value[key] is not None for key in value) else items
    return value if json_native(value) else None


def reference_tables() -> set[int]:
    tables = set()
    for name, module in list(sys.modules.items()):
        if name.startswith("flir_config") and module is not None:
            tables.update(id(value) for value in vars(module).values() if isinstance(value, (dict, list, set)))
    return tables


def classify(call: str):
    """(reason to skip or None, overrides for the Rust run, mocked
    resolve_template or None, mocked registration resolve_template or None)."""
    from flir_config import campaigns as campaign_module, platform, templates
    resolve = registration = languages = None
    lua_limits = False
    roots: dict[str, object] = {}
    campaigns_patched = False
    for patcher in list(ACTIVE_PATCHES):
        target, attribute = getattr(patcher, "target", None), getattr(patcher, "attribute", "")
        if isinstance(target, types.ModuleType):
            module = target.__name__
            if (module, attribute) in IRRELEVANT_PATCHES:
                continue
            if (module, attribute) in LIST_ROOTS:
                roots[module] = getattr(target, attribute)
                continue
            if (module, attribute) in CAMPAIGN_NAMES:
                campaigns_patched = True
                continue
            if module == "flir_config.original_configuration" and attribute == "resolve_template":
                if call in ("build_original_configuration", "fit_original_configuration"):
                    resolve = getattr(target, attribute)
                continue
            if module == "flir_config.original_registrations" and attribute == "resolve_template":
                registration = getattr(target, attribute)
                continue
            if (module, attribute) in LUA_LIMITS:
                lua_limits = True
                continue
            if module == "flir_config.translations" and attribute == "LANGUAGES_DIR":
                folder = getattr(target, attribute)
                if not isinstance(folder, os.PathLike):
                    return "translations.LANGUAGES_DIR is not a path", None, None, None
                languages = str(folder)
                continue
            if module.startswith("flir_config") or module in STANDARD_MODULES:
                return f"patched {module}.{attribute}", None, None, None
            continue  # a test helper module
        if isinstance(target, type) and target.__module__ in STANDARD_MODULES:
            return f"patched {target.__name__}.{attribute}", None, None, None
    pins: dict[str, dict] = {}
    data: dict[str, dict] = {}
    tables = None
    for patcher in list(ACTIVE_DICTS):
        current = patcher.in_dict
        if current is templates._PARTS or current is platform.PRESET_CAMPAIGNS:
            campaigns_patched = True
            continue
        table = PIN_TABLES.get(id(current))
        if table is not None:
            if any(key not in current for key in PIN_STARTUP[table]):
                return f"cleared pin table {table}", None, None, None
            pins[table] = {key: value for key, value in current.items() if PIN_STARTUP[table].get(key) != value}
            continue
        table = DATA_TABLES.get(id(current))
        if table is not None:
            startup = DATA_STARTUP[table]
            if any(key not in current for key in startup):
                return f"cleared table {table}", None, None, None
            entries = {}
            for key, value in current.items():
                if startup.get(key) is not value:
                    entries[key] = plain(value)
                    if entries[key] is None:
                        return f"{table} entry is not plain JSON", None, None, None
            data[table] = entries
            continue
        if tables is None:
            tables = reference_tables()
        if id(current) in tables:
            return "patched a reference table", None, None, None
    lists = None
    if roots:
        # The port reads every table from one folder; both modules must name it.
        if len(roots) != len(LIST_ROOTS) or len({str(root) for root in roots.values()}) != 1:
            return "patched one module's lists root", None, None, None
        root = next(iter(roots.values()))
        if not isinstance(root, os.PathLike):
            return "the lists root is not a path", None, None, None
        lists = str(root)
    campaigns = None
    if campaigns_patched:
        # platform only looks labels up (dict(CAMPAIGNS)).
        if dict(platform.CAMPAIGNS) != dict(templates.CAMPAIGNS):
            return "templates and platform name other campaigns", None, None, None
        campaigns = {"campaigns": [list(row) for row in templates.CAMPAIGNS],
                     "parts": {key: list(value) for key, value in templates._PARTS.items()},
                     "preset_campaigns": {key: list(value) for key, value in platform.PRESET_CAMPAIGNS.items()}}
        if not json_native(campaigns):
            return "patched campaigns are not plain JSON", None, None, None
    overrides = {"pins": pins or None, "tables": data or None, "languages": languages, "lists": lists,
                 "lua_limits": (f"{campaign_module._LUAJIT_CONSTANTS},{campaign_module._LUA_PART}"
                                if lua_limits else None),
                 "campaigns": campaigns}
    return None, overrides, resolve, registration


def mocked_draft(resolve, values: dict):
    if not isinstance(resolve, mock.NonCallableMock):
        return None, "resolve_template replaced by a function"
    effect = resolve.side_effect
    if effect is None:
        draft = resolve.return_value
    elif callable(effect) and not isinstance(effect, type):
        draft = effect(values["catalog"], values["world"], values["template_id"], items=values.get("items"),
                       targets=values.get("targets"), options=values.get("options"))
    else:
        return None, "resolve_template raises or iterates"
    if not json_native(draft):
        return None, "draft is not plain JSON"
    return copy.deepcopy(draft), None


def present(**values) -> dict:
    return {key: value for key, value in values.items() if value is not None}


def file(value) -> dict:
    return RUNNER.file_argument(value)


class Spec:
    """A recorded call: its Rust name, its Rust arguments, the parameters that
    are paths, the folders it writes, the parameters the Rust API takes as
    text only (another type cannot be passed to it at all), those it takes as
    a mapping of text to text, those it takes as a sequence (a tuple becomes
    a JSON list), those that become JSON through a function (an object of the
    reference's), and how the reference's result becomes JSON."""

    def __init__(self, call: str, arguments, *, paths=(), writes=(), texts=(), text_maps=(), sequences=(),
                 converters=None, result=None):
        self.call, self.arguments, self.paths, self.writes = call, arguments, set(paths), tuple(writes)
        self.texts, self.text_maps, self.sequences = set(texts), set(text_maps), set(sequences)
        self.converters = converters or {}
        self.result = result


def names_value(names) -> dict:
    """game_names.EetNames as the Rust port takes and gives it."""
    from flir_config.game_names import EetNames
    if not isinstance(names, EetNames):
        raise TypeError("not EetNames")
    return {"tables": names.tables, "installed": names.installed, "notes": list(names.notes)}


SPECS = {
    ("flir_config.resources", "scan_game"): Spec(
        "scan_game", lambda v, d: present(game_root=str(v["game_root"]), language=v["language"]),
        paths=("game_root",)),
    ("flir_config.sources", "scan_world"): Spec("scan_world", lambda v, d: {"catalog": file(v["catalog"])}),
    ("flir_config.sources", "unreadable_notes"): Spec(
        "unreadable_notes", lambda v, d: {"world": file(v["world"])}),
    ("flir_config.original_catalog", "resolve_template"): Spec(
        "resolve_template", lambda v, d: present(
            catalog=file(v["catalog"]), world=file(v["world"]), template_id=v["template_id"], game=v["game"],
            items=v["items"], targets=v["targets"], options=v["options"], names=v["names"]),
        texts=("template_id", "game"), converters={"names": names_value}),
    ("flir_config.templates", "build_template"): Spec(
        "build_template", lambda v, d: present(template_id=v["template_id"], items=v["items"], targets=v["targets"],
                                               options=v["options"]),
        texts=("template_id",)),
    ("flir_config.templates", "campaign_tables"): Spec(
        "campaign_tables", lambda v, d: {"campaign": v["campaign"]}, texts=("campaign",), result=list),
    ("flir_config.templates", "template_tables"): Spec(
        "template_tables", lambda v, d: {"template_id": v["template_id"]}, texts=("template_id",), result=list),
    ("flir_config.templates", "recommended_preset"): Spec(
        "recommended_preset", lambda v, d: {"campaign": v["campaign"]}, texts=("campaign",)),
    ("flir_config.game_names", "eet_names"): Spec(
        "eet_names", lambda v, d: {"game_root": str(v["game_root"])}, paths=("game_root",), result=names_value),
    ("flir_config.game_names", "convert_resource"): Spec(
        "convert_resource", lambda v, d: present(resref=v["resref"], extension=v["extension"], campaign=v["campaign"],
                                                 game=v["game"], names=v["names"]),
        texts=("resref", "extension", "campaign", "game"), converters={"names": names_value}),
    ("flir_config.original_configuration", "build_original_configuration"): Spec(
        "build_original_configuration", lambda v, d: present(
            catalog=file(v["catalog"]), world=file(v["world"]), template_id=v["template_id"],
            items=v["items"], targets=v["targets"], options=v["options"], exclude=v["exclude"] or {},
            draft=None if d is None else file(d)),
        texts=("template_id",)),
    ("flir_config.original_configuration", "fit_original_configuration"): Spec(
        "fit_original_configuration", lambda v, d: present(
            catalog=file(v["catalog"]), world=file(v["world"]), template_id=v["template_id"],
            items=v["items"], targets=v["targets"], options=v["options"],
            draft=None if d is None else file(d))),
    ("flir_config.sources", "new_binding_plan"): Spec(
        "new_binding_plan", lambda v, d: {"profile": file(v["profile"]), "catalog": file(v["catalog"]),
                                          "world": file(v["world"])}),
    ("flir_config.sources", "bind_profile"): Spec(
        "bind_profile", lambda v, d: {"profile": file(v["profile"]), "catalog": file(v["catalog"]),
                                      "world": file(v["world"]), "plan": file(v["plan"])}),
    ("flir_config.sources", "prepare_bound_profile"): Spec(
        "prepare_bound_profile", lambda v, d: {"profile": file(v["profile"]), "catalog": file(v["catalog"]),
                                               "world": file(v["world"]), "plan": file(v["plan"]),
                                               "store": str(v["store"])},
        paths=("store",), writes=("store",)),
    ("flir_config.sources", "read_bound_profile"): Spec(
        "read_bound_profile", lambda v, d: present(store=str(v["store"]), revision=v["revision"]),
        paths=("store",), texts=("revision",)),
    ("flir_config.campaigns", "compile_campaign"): Spec(
        "compile_campaign", lambda v, d: {"profile": file(v["profile"]), "catalog": file(v["catalog"]),
                                          "world": file(v["world"]), "plan": file(v["plan"])}),
    ("flir_config.campaigns", "write_campaign"): Spec(
        "write_campaign", lambda v, d: {"profile": file(v["profile"]), "catalog": file(v["catalog"]),
                                        "world": file(v["world"]), "plan": file(v["plan"]),
                                        "store": str(v["store"])},
        paths=("store",), writes=("store",)),
    ("flir_config.campaigns", "read_campaign"): Spec(
        "read_campaign", lambda v, d: present(store=str(v["store"]), revision=v["revision"]),
        paths=("store",), texts=("revision",)),
    ("flir_config.native_export", "export_native"): Spec(
        "export_native", lambda v, d: present(store=str(v["store"]), catalog=file(v["catalog"]),
                                              world=file(v["world"]), output=str(v["output"]),
                                              revision=v["revision"], starts=v["starts"], main=v["main"]),
        paths=("store", "output"), writes=("output",), texts=("revision", "main"), text_maps=("starts",)),
    ("flir_config.starts", "write_starts"): Spec(
        "write_starts", lambda v, d: {"profile": file(v["profile"]), "plan": file(v["plan"]),
                                      "catalog": file(v["catalog"]), "world": file(v["world"]),
                                      "store": str(v["store"])},
        paths=("store",), writes=("store",)),
    ("flir_config.starts", "describe_starts"): Spec(
        "describe_starts", lambda v, d: {"written": v["written"]}),
    ("flir_config.platform", "game_platform"): Spec(
        "game_platform", lambda v, d: {"world": file(v["world"])}),
    ("flir_config.platform", "preset_game"): Spec(
        "preset_game", lambda v, d: {"world": file(v["world"]), "template_id": v["template_id"]},
        texts=("template_id",)),
    ("flir_config.platform", "area_part"): Spec(
        "area_part", lambda v, d: {"platform": v["platform"], "area": v["area"]}, texts=("platform", "area")),
    ("flir_config.platform", "has_sod"): Spec("has_sod", lambda v, d: {"world": file(v["world"])}),
    ("flir_config.platform", "preset_campaigns"): Spec(
        "preset_campaigns", lambda v, d: {"world": file(v["world"])}, result=list),
    ("flir_config.platform", "recommended_campaign"): Spec(
        "recommended_campaign", lambda v, d: {"world": file(v["world"])}),
    ("flir_config.templates", "list_templates"): Spec(
        "list_templates", lambda v, d: present(campaigns=None if v["campaigns"] is None else list(v["campaigns"])),
        sequences=("campaigns",)),
    ("flir_config.activation", "item_randomiser_conflict"): Spec(
        "item_randomiser_conflict", lambda v, d: {"game": str(v["game"])}, paths=("game",)),
    ("flir_config.activation", "validate_runtime"): Spec(
        "validate_runtime", lambda v, d: {"game": str(v["game"]), "catalog": file(v["catalog"])},
        paths=("game",), result=str),
    ("flir_config.activation", "adopt_revisions"): Spec(
        "adopt_revisions", lambda v, d: {"game": str(v["game"]), "output": str(v["output"])},
        paths=("game", "output"), writes=("output",)),
    ("flir_config.activation", "deploy_export"): Spec(
        "deploy_export", lambda v, d: {"game": str(v["game"]), "output": str(v["output"])},
        paths=("game", "output"), writes=("game", "output/..")),
}


def skip(call: str, reason: str) -> None:
    SKIPPED[f"{call}: {reason}"] += 1


def record(spec: Spec, original, signature, args, kwargs):
    try:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
    except TypeError:
        return original(*args, **kwargs)
    values = dict(bound.arguments)
    reason, pins, resolve, registration = classify(spec.call)
    if reason is None and spec.call == "deploy_export":
        # The port asks Windows whether the game runs, as the reference does
        # unpatched; a test that makes the check refuse cannot be mirrored.
        from flir_config import activation
        closed = activation.require_closed
        if isinstance(closed, mock.NonCallableMock) and closed.side_effect is not None:
            reason = "require_closed patched to refuse"
    draft = registration_draft = None
    if reason is None and resolve is not None:
        draft, reason = mocked_draft(resolve, values)
    if reason is None and registration is not None:
        if not isinstance(registration, mock.NonCallableMock) or registration.side_effect is not None:
            reason = "registration resolve_template is not a plain mock"
        elif not json_native(registration.return_value):
            reason = "registration draft is not plain JSON"
        else:
            registration_draft = registration.return_value
    if reason is None:
        for key, convert in spec.converters.items():
            if values.get(key) is not None:
                try:
                    values[key] = convert(values[key])
                except (AttributeError, TypeError):
                    reason = f"{key} cannot be passed to the Rust API"
    if reason is None:
        for key, value in values.items():
            if key in spec.paths:
                if not isinstance(value, (str, os.PathLike)):
                    reason = f"{key} is not a path"
            elif key in spec.texts and value is not None and not isinstance(value, str):
                reason = f"{key} is not text (the Rust API takes text)"
            elif key in spec.text_maps and value is not None:
                if not (type(value) is dict and all(isinstance(item, str) for item in value.values())):
                    reason = f"{key} is not a mapping of text (the Rust API takes one)"
            elif key in spec.sequences and value is not None:
                if not (isinstance(value, (list, tuple)) and json_native(list(value))):
                    reason = f"{key} is not a sequence of plain JSON"
            elif not json_native(value):
                reason = f"{key} is not plain JSON"
    # "name/.." is the folder holding that path (deploy_export keeps its
    # backup beside the export).
    roots = ([Path(values[key[:-3]]).parent if key.endswith("/..") else Path(values[key]) for key in spec.writes]
             if reason is None else [])
    if any(not is_temporary(root) for root in roots):
        reason = "writes outside the temporary folder"
    before = {}
    if reason is None:
        try:
            before = {root: snapshot_tree(root) for root in roots}
        except (ValueError, OSError):
            reason = "writes into a folder with links or read-only files"
    if reason is not None:
        skip(spec.call, reason)
        return original(*args, **kwargs)

    try:
        rust_line = RUNNER.run([{"call": spec.call, "args": spec.arguments(values, draft)}], pins["pins"],
                               registration_draft, pins["tables"], pins["languages"], pins["lists"],
                               pins["campaigns"], pins["lua_limits"])[0]
    except Exception as exc:  # the comparison reports it; the test goes on
        rust_line = json.dumps({"rust-failure": str(exc)})
    rust_after = {}
    for root in roots:
        try:
            rust_after[root] = snapshot_tree(root)
        except (ValueError, OSError) as exc:
            rust_after[root] = {"<unreadable>": str(exc).encode()}
        restore_tree(root, before[root])

    error = None
    try:
        result = original(*args, **kwargs)
    except Exception as exc:
        result, error = None, exc
    try:
        python_line = outcome_line(result if error is not None or spec.result is None else spec.result(result),
                                   error)
    except (TypeError, ValueError) as exc:
        python_line = json.dumps({"unserializable": str(exc)})
    python_after = {root: snapshot_tree(root) for root in roots}

    COMPARED[spec.call] += 1
    problems = []
    if python_line != rust_line:
        problems.append(first_difference(python_line, rust_line))
    for root in roots:
        problems += [f"{root}: {line}" for line in tree_differences(python_after[root], rust_after[root])]
    if problems:
        MISMATCHES.append({"test": CURRENT_TEST[0], "call": spec.call, "problems": problems,
                           "python": python_line[:4000], "rust": rust_line[:4000]})
    if error is not None:
        raise error
    return result


def make_wrapper(spec: Spec, original):
    signature = inspect.signature(original)

    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        if getattr(STATE, "active", False):
            return original(*args, **kwargs)
        STATE.active = True
        try:
            return record(spec, original, signature, args, kwargs)
        finally:
            STATE.active = False

    wrapper.__parity_original__ = original
    return wrapper


def install_wrappers() -> int:
    import importlib
    wrappers = {}
    for (module_name, name), spec in SPECS.items():
        original = getattr(importlib.import_module(module_name), name)
        wrappers[id(original)] = (original, make_wrapper(spec, original))
    tests = str(ROOT / "tests")
    replaced = 0
    for module_name, module in list(sys.modules.items()):
        if module is None:
            continue
        location = getattr(module, "__file__", None) or ""
        if not (module_name.startswith("flir_config") or location.startswith(tests)):
            continue
        for attribute, value in list(vars(module).items()):
            entry = wrappers.get(id(value))
            if entry is not None and entry[0] is value:
                setattr(module, attribute, entry[1])
                replaced += 1
    return replaced


class TrackingResult(unittest.TextTestResult):
    def startTest(self, test):
        CURRENT_TEST[0] = test.id()
        super().startTest(test)


def main() -> int:
    global RUNNER
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("-p", "--pattern", default="test_*.py", help="test files to run")
    parser.add_argument("-k", dest="names", action="append", help="only tests whose name contains this")
    parser.add_argument("--report", type=Path, help="write all mismatches as JSON here")
    parser.add_argument("--show", type=int, default=12, help="mismatches to print")
    arguments = parser.parse_args()
    common.require_built()
    scratch = Path(tempfile.mkdtemp(prefix="cir-parity-replay-"))
    try:
        RUNNER = RustRunner(scratch)
        track_patches()
        register_pin_tables()
        os.chdir(ROOT)
        loader = unittest.TestLoader()
        if arguments.names:
            loader.testNamePatterns = [f"*{name}*" for name in arguments.names]
        suite = loader.discover(str(ROOT / "tests"), pattern=arguments.pattern)
        replaced = install_wrappers()
        runner = unittest.TextTestRunner(verbosity=1, resultclass=TrackingResult)
        result = runner.run(suite)
        compared = sum(COMPARED.values())
        print(f"\nparity: {compared} calls compared ({replaced} references wrapped), {len(MISMATCHES)} mismatches")
        for call, count in sorted(COMPARED.items()):
            print(f"  compared {count:5} {call}")
        for reason, count in sorted(SKIPPED.items()):
            print(f"  skipped  {count:5} {reason}")
        for mismatch in MISMATCHES[:arguments.show]:
            print(f"\nMISMATCH {mismatch['call']} in {mismatch['test']}")
            for problem in mismatch["problems"][:6]:
                print("  " + problem)
        if arguments.report:
            arguments.report.write_text(json.dumps(MISMATCHES, indent=2), encoding="utf-8")
        ok = result.wasSuccessful() and not MISMATCHES and compared > 0
        print("\nparity OK" if ok else "\nparity FAILED")
        return 0 if ok else 1
    finally:
        # The runner removes its files after each batch; the folder goes too.
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
