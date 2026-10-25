"""Generate byte-parity fixtures for the Rust ports of the resource patchers.

Run from the repository root:  python configurator/parity/patchers/generate.py

Every input is synthetic and built with the Python tests' own builders
(tests/test_*.py); no game resource is read (except with --game, below). Each scenario becomes
configurator/cir-config/tests/fixtures/patchers/<scenario>.json (cases) and
<scenario>.pins.json (the pinned-hash overrides the reference's tests apply
with patch.dict / patch.object). Every case is evaluated twice: with those
overrides and with the reviewed tables; "independent" records whether both
agree, so the Rust tests can run it without the parity-overrides feature.

Bytes are stored once in "blobs" (by SHA-256) and referenced as
{"$blob": sha}. Python dicts with int keys become JSON objects with decimal
keys; tuples become lists.

    python configurator/parity/patchers/generate.py --game <game folder> --language en_US --out <folder>

records the reward routes of an installed game instead, which is only read,
as <folder>/game_routes.json: each route resource patched as the export
patches it, recognised before and after, and exported again. The fixture holds
the game's bytes, so the folder must be outside the repository;
CIR_PARITY_GAME_ROUTES=<folder>/game_routes.json runs it with the Rust tests.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "tests"), str(ROOT)]

from flir_config import container_rewards, cursed_disguises, mode2_rewards, original_catalog, original_options  # noqa: E402
from flir_config import quest_options, quest_rewards, reward_copies, spell_rewards, templates  # noqa: E402
from flir_config import creature_inventory, game_text, group_recipients, store_inventory, translations  # noqa: E402
from flir_config.game_text import GameText, GameTextError  # noqa: E402
from flir_config.profiles import ProfileError  # noqa: E402
from flir_config.resources import ScanError  # noqa: E402
from flir_config.script_text import script_blocks  # noqa: E402
from flir_config.world_formats import parse_sto  # noqa: E402

import test_container_rewards as container_tests  # noqa: E402
import test_cursed_disguises as cursed_tests  # noqa: E402
import test_mode2_rewards as mode2_tests  # noqa: E402
import test_quest_options as quest_tests  # noqa: E402
import test_reward_copies as copy_tests  # noqa: E402
import test_spell_rewards as spell_tests  # noqa: E402
from test_original_options import ALL_DISGUISE_ITEMS  # noqa: E402
from test_postinstall_resources import make_itm, make_tlk  # noqa: E402
from test_postinstall_world_formats import item_record, make_cre, make_sto  # noqa: E402
from configurator.parity import codepages  # noqa: E402

OUT = ROOT / "configurator" / "cir-config" / "tests" / "fixtures" / "patchers"
TABLES = {"quest_options.ORIGINAL_HASHES": quest_options.ORIGINAL_HASHES,
          "quest_options.BG2EE_HASHES": quest_options.BG2EE_HASHES}
ERROR_KINDS = ((ProfileError, "Profile"), (ScanError, "Scan"), (GameTextError, "GameText"), (KeyError, "Key"),
               (TypeError, "Type"))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Scenario:
    def __init__(self, name: str, pins: dict | None = None):
        self.name, self.pins, self.cases, self.blobs = name, pins or {}, [], {}

    def overrides(self) -> ExitStack:
        stack = ExitStack()
        for table, entries in self.pins.items():
            if table == "spell_rewards":
                for key, value in entries.items():
                    stack.enter_context(patch.object(spell_rewards, key, value))
            else:
                stack.enter_context(patch.dict(TABLES[table], entries))
        return stack

    def encode(self, value):
        if isinstance(value, (bytes, bytearray)):
            value = bytes(value)
            self.blobs[sha(value)] = value.hex()
            return {"$blob": sha(value)}
        if isinstance(value, dict):
            return {str(key): self.encode(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.encode(item) for item in value]
        if isinstance(value, (set, frozenset)):
            return [self.encode(item) for item in sorted(value)]
        return value

    def evaluate(self, function):
        try:
            return {"result": self.encode(function())}
        except (ProfileError, ScanError, GameTextError, KeyError, TypeError, ValueError) as error:
            # ProfileError subclasses (ScriptError, ChangedSinceScan) keep
            # their kind and name the subclass, as the Rust errors do.
            kind = next((name for cls, name in ERROR_KINDS if isinstance(error, cls)), "Value")
            outcome = {"kind": kind, "message": str(error)}
            if isinstance(error, ProfileError) and type(error) is not ProfileError:
                outcome["subclass"] = type(error).__name__
            return {"error": outcome}

    def case(self, name: str, call: str, args: dict, function) -> None:
        with self.overrides():
            outcome = self.evaluate(function)
        reference = self.evaluate(function)
        self.cases.append({"name": name, "call": call, "args": self.encode(args),
                           "independent": reference == outcome, **outcome})

    def write(self, out: Path = OUT) -> None:
        names = [case["name"] for case in self.cases]
        assert len(set(names)) == len(names), sorted(name for name in set(names) if names.count(name) > 1)
        out.mkdir(parents=True, exist_ok=True)
        document = {"scenario": self.name, "cases": self.cases, "blobs": dict(sorted(self.blobs.items()))}
        (out / f"{self.name}.json").write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8", newline="\n")
        (out / f"{self.name}.pins.json").write_text(json.dumps(self.pins, indent=1, sort_keys=True) + "\n",
                                                   encoding="utf-8", newline="\n")
        print(f"{self.name}: {len(self.cases)} cases, {len(self.blobs)} blobs")


def sorted_ints(mapping: dict) -> dict:
    return {key: mapping[key] for key in sorted(mapping)}


def tampered(data: bytes, offset: int) -> bytes:
    changed = bytearray(data)
    changed[offset] ^= 1
    return bytes(changed)


def crlf(data: bytes) -> bytes:
    return data.replace(b"\n", b"\r\n")


# ---------------------------------------------------------------- quest options


def quest_rewards_cases(s: Scenario) -> None:
    s.case("QUESTS", "quest_rewards.QUESTS", {}, lambda: quest_rewards.QUESTS)
    s.case("SOURCE_QUESTS", "quest_rewards.SOURCE_QUESTS", {}, lambda: quest_rewards.SOURCE_QUESTS)
    for source in ("bcs:blpool/grant/pool-reward", "original-source:bg2:base:item:9",
                   "original-source:bg2:base:item:109", "original-source:bg2:base:item:10"):
        s.case(f"quest_spec {source}", "quest_rewards.quest_spec", {"source_id": source},
               lambda source=source: quest_rewards.quest_spec(source))
    for label, data in (("lf", b"SC\nA\n"), ("crlf", b"SC\r\nA\r\n"), ("mixed", b"SC\r\nA\n"), ("none", b"SC"),
                        ("cr-only", b"SC\rA\r"), ("double-cr", b"SC\r\r\nA\r\n")):
        s.case(f"newline {label}", "quest_rewards._newline", {"data": data},
               lambda data=data: quest_rewards._newline(data))
    crypt = quest_tests.crypt_fixture("AR0526")
    for label, data, expected, newline in (
            ("crypt", crypt, 6, b"\n"), ("crypt wrong count", crypt, 5, b"\n"),
            ("crypt crlf", crlf(crypt), 6, b"\r\n"), ("crypt crlf as lf", crlf(crypt), 6, b"\n"),
            ("missing head", crypt[3:], 6, b"\n"), ("missing tail", crypt[:-3], 6, b"\n"),
            ("late first", b"SC\nX\nCR\nCO\nY\nCR\nSC\n", 1, b"\n"),
            ("open last", b"SC\nCR\nCO\nY\nSC\n", 1, b"\n")):
        s.case(f"blocks {label}", "quest_rewards._blocks", {"data": data, "expected": expected, "newline": newline},
               lambda data=data, expected=expected, newline=newline: quest_rewards._blocks(data, expected, newline))
    for name in ("FLPR_QPOOL", "FLPR_X_1", "FLPR_" + "A" * 27, "FLPR_" + "A" * 28, "FLPR_", "flpr_x", "FLPR_a",
                 "FLPR_É", "XFLPR_A"):
        for newline in (b"\n", b"\r\n"):
            s.case(f"global_action {name} {newline!r}", "quest_rewards._global_action",
                   {"name": name, "newline": newline},
                   lambda name=name, newline=newline: quest_rewards._global_action(name, newline))
    for name, selected in (("FLPR_KANGAXX", True), ("FLPR_KANGAXX", False), ("anything at all", True), ("Ünï", False)):
        s.case(f"global_guard {name} {selected}", "quest_rewards._global_guard",
               {"name": name, "selected": selected, "newline": b"\r\n"},
               lambda name=name, selected=selected: quest_rewards._global_guard(name, selected, b"\r\n"))
    script = mode2_tests.script_fixture("ddwell")
    blocks = quest_rewards._blocks(script, 3, b"\n")[0]
    asysta = quest_rewards._blocks(mode2_tests.script_fixture("asysta01"), 1, b"\n")[0][0]
    for label, block, item, opcode, newline in (
            ("ddwell", blocks[1], "helm21", 140, b"\n"), ("asysta", asysta, "Helm19", 82, b"\n"),
            ("wrong opcode", blocks[1], "helm21", 82, b"\n"), ("absent", blocks[0], "helm21", 140, b"\n"),
            ("twice", blocks[1] + blocks[1], "helm21", 140, b"\n"), ("no action head", blocks[1][-60:], "helm21", 140, b"\n"),
            ("crlf", crlf(blocks[1]), "helm21", 140, b"\r\n"), ("non-ascii item", blocks[1], "hélm", 140, b"\n")):
        s.case(f"grant_action {label}", "quest_rewards._grant_action",
               {"block": block, "item": item, "opcode": opcode, "newline": newline},
               lambda block=block, item=item, opcode=opcode, newline=newline:
               quest_rewards._grant_action(block, item, opcode, newline))


def quest_option_inputs(line_endings: str) -> dict:
    resources = {"hlskull.dlg": quest_tests.dialogue_fixture("hlskull.dlg"),
                 "crypt526.bcs": quest_tests.crypt_fixture("AR0526"),
                 "crypt202.bcs": quest_tests.crypt_fixture("AR0202")}
    if line_endings == "crlf":
        for name in ("crypt526.bcs", "crypt202.bcs"):
            resources[name] = crlf(resources[name])
    return resources


def kangaxx_cases(s: Scenario, resources: dict, patched: bytes, crypts: dict, bg2ee: bytes) -> None:
    """kangaxx_problem: which files it reads, and why a game cannot have the option."""
    ids = {"dlg:hlskull": "hlskull.dlg", "bcs:crypt526": "crypt526.bcs", "bcs:crypt202": "crypt202.bcs"}
    other = quest_tests.dialogue_fixture("hlskull.dlg", action=b'SetGlobal("KangaxxQuest","GLOBAL",2)\r\n')
    originals = {resource: resources[name] for resource, name in ids.items()}
    own = {"dlg:hlskull": patched, **{resource: crypts[name] for resource, name in ids.items() if name in crypts}}
    for label, files in (
            ("reviewed", originals), ("bg2ee dialogue", {**originals, "dlg:hlskull": bg2ee}),
            ("own dialogue", {**originals, "dlg:hlskull": patched}), ("own patches", own),
            ("other dialogue", {**originals, "dlg:hlskull": other}),
            ("other crypts", {**originals, "bcs:crypt526": originals["bcs:crypt526"] + b"x",
                              "bcs:crypt202": b"SC\nCR\nCO\nCO\nRS\nRS\nCR\nSC\n"}),
            ("other everything", {"dlg:hlskull": other, "bcs:crypt526": b"", "bcs:crypt202": patched}),
            ("missing crypt", {key: value for key, value in originals.items() if key != "bcs:crypt202"}),
            ("missing dialogue and crypt", {"bcs:crypt526": originals["bcs:crypt526"]}),
            ("missing all", {"are:other": b"area"})):
        hashes = {resource: sha(data) for resource, data in files.items()}

        def run(files=files, hashes=hashes):
            requested = []

            def read(wanted):
                requested.append(list(wanted))
                return {resource: files[resource] for resource in wanted}

            return {"problem": quest_options.kangaxx_problem(hashes, read), "read": requested}

        s.case(f"kangaxx_problem {label}", "quest_options.kangaxx_problem", {"hashes": hashes, "files": files}, run)
    # A file the read cannot supply.
    hashes = {resource: sha(data) for resource, data in {**originals, "dlg:hlskull": other}.items()}
    s.case("kangaxx_problem unread", "quest_options.kangaxx_problem", {"hashes": hashes, "files": {}},
           lambda: {"problem": quest_options.kangaxx_problem(hashes, lambda wanted: {}), "read": [["dlg:hlskull"]]})


def quest_options_scenario(name: str, line_endings: str) -> Scenario:
    resources = quest_option_inputs(line_endings)
    # BG2:EE's own HLSKULL.DLG: no final line break after its action.
    bg2ee = quest_tests.dialogue_fixture("hlskull.dlg", action=b'SetGlobal("KangaxxQuest","GLOBAL",1)')
    s = Scenario(name, {"quest_options.ORIGINAL_HASHES": {key: sha(value) for key, value in resources.items()},
                        "quest_options.BG2EE_HASHES": {"hlskull.dlg": sha(bg2ee)}})
    if line_endings == "lf":
        quest_rewards_cases(s)
        for path in ("languages/english/hlskull.tra", "languages/english/in-game.tra",
                     "languages/english/ppdjinn.tra", "languages/bgee/english/in-game.tra"):
            s.case(f"texts {path}", "quest_options._texts", {"path": path},
                   lambda path=path: sorted_ints(quest_options._texts(path)))
    refs = {index: 1000 + index for index in range(22)}
    moved = {index: 2000 + index for index in range(22)}
    with s.overrides():
        patched = quest_options.patch_hlskull(resources["hlskull.dlg"], refs)
        crypts = {key: quest_options.patch_crypt(key, resources[key], {1500: 100, 1501: 101})
                  for key in ("crypt526.bcs", "crypt202.bcs")}
    original = resources["hlskull.dlg"]
    for label, data in (("original", original), ("patched", patched)):
        s.case(f"dialogue parse {label}", "quest_options._Dialogue",
               {"data": data, "name": "HLSKULL"}, lambda data=data: dialogue_parts(data, "HLSKULL"))
    s.case("patch_hlskull original", "quest_options.patch_hlskull", {"data": original, "refs": refs},
           lambda: quest_options.patch_hlskull(original, refs))
    s.case("patch_hlskull reexport", "quest_options.patch_hlskull", {"data": patched, "refs": refs},
           lambda: quest_options.patch_hlskull(patched, refs))
    s.case("patch_hlskull moved refs", "quest_options.patch_hlskull", {"data": patched, "refs": moved},
           lambda: quest_options.patch_hlskull(patched, moved))
    with s.overrides():
        bg2ee_patched = quest_options.patch_hlskull(bg2ee, refs)
    for label, data in (("bg2ee", bg2ee), ("bg2ee reexport", bg2ee_patched)):
        s.case(f"patch_hlskull {label}", "quest_options.patch_hlskull", {"data": data, "refs": refs},
               lambda data=data: quest_options.patch_hlskull(data, refs))
        s.case(f"original_dialogue {label}", "quest_options.original_dialogue", {"name": "hlskull.dlg", "data": data},
               lambda data=data: quest_options.original_dialogue("hlskull.dlg", data))
    for label, bad in (("missing ref", {k: v for k, v in refs.items() if k != 21}),
                       ("ref sentinel", {**refs, 4: 0xFFFFFFFF}), ("negative ref", {**refs, 4: -1})):
        s.case(f"patch_hlskull {label}", "quest_options.patch_hlskull", {"data": original, "refs": bad},
               lambda bad=bad: quest_options.patch_hlskull(original, bad))
    s.case("original_dialogue patched", "quest_options.original_dialogue", {"name": "hlskull.dlg", "data": patched},
           lambda: quest_options.original_dialogue("hlskull.dlg", patched))
    s.case("original_dialogue original", "quest_options.original_dialogue", {"name": "hlskull.dlg", "data": original},
           lambda: quest_options.original_dialogue("hlskull.dlg", original))
    s.case("original_dialogue other name", "quest_options.original_dialogue", {"name": "other.dlg", "data": original},
           lambda: quest_options.original_dialogue("other.dlg", original))
    for offset in (0x34, len(original) + 3, len(patched) - 1, len(patched) - 5, len(patched) - 12):
        data = tampered(patched, offset)
        s.case(f"original_dialogue tampered {offset}", "quest_options.original_dialogue",
               {"name": "hlskull.dlg", "data": data},
               lambda data=data: quest_options.original_dialogue("hlskull.dlg", data))
    trailer_length = struct.unpack_from("<I", patched, len(patched) - 4)[0]
    for label, data in (("short", patched[-11:]), ("tiny trailer", patched[:-4] + struct.pack("<I", 12)),
                        ("huge trailer", patched[:-4] + struct.pack("<I", len(patched))),
                        ("bad count", patched[:-12 - trailer_length + 56] + struct.pack("<I", 23)
                         + patched[-12 - trailer_length + 60:])):
        s.case(f"original_dialogue {label}", "quest_options.original_dialogue", {"name": "hlskull.dlg", "data": data},
               lambda data=data: quest_options.original_dialogue("hlskull.dlg", data))
    for key in ("crypt526.bcs", "crypt202.bcs"):
        data, result = resources[key], crypts[key]
        s.case(f"patch_crypt {key}", "quest_options.patch_crypt",
               {"name": key, "data": data, "refs": {1500: 100, 1501: 101}},
               lambda key=key, data=data: quest_options.patch_crypt(key, data, {1500: 100, 1501: 101}))
        s.case(f"patch_crypt reexport {key}", "quest_options.patch_crypt",
               {"name": key, "data": result, "refs": {1500: 100, 1501: 101}},
               lambda key=key, result=result: quest_options.patch_crypt(key, result, {1500: 100, 1501: 101}))
        s.case(f"patch_crypt moved {key}", "quest_options.patch_crypt",
               {"name": key, "data": result, "refs": {1500: 7, 1501: 123456}},
               lambda key=key, result=result: quest_options.patch_crypt(key, result, {1500: 7, 1501: 123456}))
        s.case(f"original_crypt {key}", "quest_options.original_crypt", {"name": key, "data": result},
               lambda key=key, result=result: quest_options.original_crypt(key, result))
        s.case(f"patch_crypt changed {key}", "quest_options.patch_crypt",
               {"name": key, "data": data + b"changed", "refs": {1500: 1, 1501: 2}},
               lambda key=key, data=data: quest_options.patch_crypt(key, data + b"changed", {1500: 1, 1501: 2}))
        s.case(f"patch_crypt missing ref {key}", "quest_options.patch_crypt",
               {"name": key, "data": data, "refs": {1500: 1}},
               lambda key=key, data=data: quest_options.patch_crypt(key, data, {1500: 1}))
        s.case(f"original_crypt tampered {key}", "quest_options.original_crypt",
               {"name": key, "data": result.replace(b"flrward1", b"flrward2", 1)},
               lambda key=key, result=result: quest_options.original_crypt(key, result.replace(b"flrward1", b"flrward2", 1)))
    s.case("original_crypt unsupported", "quest_options.original_crypt",
           {"name": "crypt999.bcs", "data": resources["crypt526.bcs"]},
           lambda: quest_options.original_crypt("crypt999.bcs", resources["crypt526.bcs"]))
    s.case("original_crypt swapped", "quest_options.original_crypt",
           {"name": "crypt202.bcs", "data": crypts["crypt526.bcs"]},
           lambda: quest_options.original_crypt("crypt202.bcs", crypts["crypt526.bcs"]))
    s.case("ward_item", "quest_options.ward_item", {"refs": {1502: 5, 1503: 6, 1504: 7}},
           lambda: quest_options.ward_item({1502: 5, 1503: 6, 1504: 7}))
    s.case("ward_item missing", "quest_options.ward_item", {"refs": {1502: 5, 1503: 6}},
           lambda: quest_options.ward_item({1502: 5, 1503: 6}))
    text = GameText(make_tlk(["old"]))
    both = GameText(make_tlk(["old"]), make_tlk(["old (f)"]))
    with s.overrides():
        output, prepared = quest_options.prepare_quest_options(resources, text, kangaxx=True)
        _, polish = quest_options.prepare_quest_options(resources, both, kangaxx=True, language="pl_PL")
    for label, args in (
            ("kangaxx", {"resources": resources, "text": text, "kangaxx": True, "language": "en_us"}),
            ("kangaxx gb", {"resources": resources, "text": text, "kangaxx": True, "language": "EN_GB"}),
            ("reexport", {"resources": output, "text": prepared, "kangaxx": True, "language": "english"}),
            ("none", {"resources": {}, "text": text, "kangaxx": False, "language": "en_US"}),
            ("german", {"resources": resources, "text": text, "kangaxx": True, "language": "de_DE"}),
            ("german both", {"resources": resources, "text": both, "kangaxx": True, "language": "de_DE"}),
            ("german none", {"resources": {}, "text": text, "kangaxx": False, "language": "de_DE"}),
            ("polish both", {"resources": resources, "text": both, "kangaxx": True, "language": "pl_PL"}),
            ("polish again", {"resources": resources, "text": polish, "kangaxx": True, "language": "PL_pl"}),
            ("spanish", {"resources": resources, "text": both, "kangaxx": True, "language": "es_ES"}),
            ("default", {"resources": resources, "text": both, "kangaxx": True, "language": "default"}),
            ("unknown", {"resources": resources, "text": text, "kangaxx": True, "language": "nb_no"}),
            ("missing crypt", {"resources": {"hlskull.dlg": original}, "text": text, "kangaxx": True, "language": "en_us"}),
            ("bad tlk", {"resources": resources, "text": GameText(b"invalid"), "kangaxx": True, "language": "en_us"}),
            ("bad female", {"resources": resources, "text": GameText(make_tlk(["old"]), b"bad"), "kangaxx": True,
                            "language": "de_DE"})):
        s.case(f"prepare_quest_options {label}", "quest_options.prepare_quest_options", args,
               lambda args=args: quest_options.prepare_quest_options(
                   args["resources"], args["text"], kangaxx=args["kangaxx"], language=args["language"]))
    lua = (b'function buildQuestsTable()\r\n\tcreateQuest ( 74458 )\r\n'
           b'\tcreateEntry ( 74458, -1, 54293, {}, nil )\r\nend\r\n')
    with s.overrides():
        joined = quest_options.patch_kangaxx_journal(lua, patched)
    for label, journal, dialogue in (
            ("join", lua, patched), ("again", joined, patched), ("unpatched dialogue", lua, original),
            ("conflict", lua + b'createEntry ( 9, -1, 1020, {}, nil )\r\n', patched),
            ("lf spacing", b"createQuest(74458)\n  createEntry(74458,-1,54293,{},nil)  \nrest", patched),
            ("no entry", b"createQuest ( 74458 )\n", patched),
            ("two entries", lua + b'createEntry ( 74458, -1, 54293, {}, nil )\n', patched),
            ("missing quest", b'createEntry ( 74458, -1, 54293, {}, nil )\n', patched),
            ("quest after entry", b'createEntry ( 74458, -1, 54293, {}, nil )\ncreateQuest ( 74458 )\n', patched),
            ("ambiguous quest", b'createQuest ( 74458 )\ncreateQuest(74458)\ncreateEntry ( 74458, -1, 54293, {}, nil )\n',
             patched),
            ("other title quest", b'createQuest ( 744580 )\ncreateEntry ( 74458, -1, 54293, {}, nil )\n', patched),
            ("no newline", b'createQuest ( 74458 )\ncreateEntry ( 74458, -1, 54293, {}, nil )', patched),
            ("indented quest", b'  \tcreateQuest ( 74458 )\r\n   createEntry ( 74458, -1, 54293, {}, nil )\r\n', patched)):
        s.case(f"patch_kangaxx_journal {label}", "quest_options.patch_kangaxx_journal",
               {"lua": journal, "patched_dialogue": dialogue},
               lambda journal=journal, dialogue=dialogue: quest_options.patch_kangaxx_journal(journal, dialogue))
    kangaxx_cases(s, resources, patched, crypts, bg2ee)
    return s


def dialogue_parts(data: bytes, name: str) -> dict:
    dialogue = quest_options._Dialogue(data, name)
    return {"states": dialogue.states, "transitions": dialogue.transitions,
            "state_triggers": dialogue.state_triggers, "triggers": dialogue.triggers,
            "actions": dialogue.actions, "render": dialogue.render()}


# ---------------------------------------------------------------- spell


def spell_scenario(name: str, fixture: bytes, patched_hash: str | None = None) -> Scenario:
    expected = bytearray(spell_tests.spell_fixture())
    struct.pack_into("<H", expected, spell_rewards.EFFECT_OFFSET, 402)
    expected[spell_rewards.EFFECT_OFFSET + 0x14:spell_rewards.EFFECT_OFFSET + 0x1C] = b"FLPRKEY\0"
    expected = bytes(expected)
    s = Scenario(name, {"spell_rewards": {"ORIGINAL_SHA256": sha(fixture),
                                          "PATCHED_SHA256": patched_hash or sha(expected)}})
    if name == "spell_rewards":
        s.case("SPELL", "spell_rewards.SPELL", {}, lambda: spell_rewards.SPELL)
        s.case("PAYLOAD", "spell_rewards.PAYLOAD", {}, lambda: spell_rewards.PAYLOAD)
    for label, data in (("original", fixture), ("patched", expected), ("changed", expected[:-1] + b"changed"),
                        ("empty", b"")):
        for call in ("patch_key_spell", "original_key_spell"):
            s.case(f"{call} {label}", f"spell_rewards.{call}", {"data": data},
                   lambda call=call, data=data: getattr(spell_rewards, call)(data))
    return s


# ---------------------------------------------------------------- direct grants (mode2_rewards)


def text_variant(resource: str) -> bytes:
    """test_text_variants_preserve_original_routes_replies_and_verified_recovery."""
    name = resource.split(":")[1]
    d = quest_options._Dialogue(mode2_tests.dialogue_fixture(mode2_rewards.resource_specs(resource)), name)
    originals = list(mode2_rewards.TEXT_REFS[resource])
    for index, old_ref in enumerate(originals):
        if old_ref in mode2_rewards.ACTION_TEXT_REFS.get(resource, ()):
            continue
        if len(originals) > 1 and index == len(originals) - 1:
            struct.pack_into("<I", mode2_tests.outgoing(d, 0)[0], 4, old_ref)
        else:
            struct.pack_into("<I", d.states[index], 0, old_ref)
    d.state_triggers.append(b'NumTimesTalkedTo(0)\r\n')
    struct.pack_into("<I", d.states[0], 12, len(d.state_triggers) - 1)
    return d.render()


def trgeni_terminal() -> bytes:
    spec = mode2_rewards.resource_specs("dlg:trgeni01")[0]
    dialogue = quest_options._Dialogue(mode2_tests.dialogue_fixture(spec), "trgeni01")
    struct.pack_into("<I", dialogue.states[35], 0, 43514)
    return dialogue.render()


def mode2_inputs() -> dict:
    """The reference tests' synthetic originals by resource."""
    originals = {}
    for resource in mode2_rewards.RESOURCE_REWARDS:
        if resource.startswith("dlg:"):
            originals[resource] = (text_variant(resource) if resource in mode2_rewards.TEXT_REFS
                                   else mode2_tests.dialogue_fixture(mode2_rewards.resource_specs(resource)))
    originals["bcs:asysta01"] = mode2_tests.script_fixture("asysta01")
    originals["bcs:ddwell"] = mode2_tests.script_fixture("ddwell")
    originals["bcs:ppbook"] = mode2_tests.book_fixture()
    originals["bcs:sahtreas"] = mode2_tests.treasury_fixture()
    originals["bcs:tolger"] = mode2_tests.tolgerias_fixture()
    return originals


def sorted_refs(found):
    """A recovered (original, refs) with its refs in key order, or None."""
    return None if found is None else (found[0], sorted_ints(found[1]))


# How a game names the routes: EET with its rename lists as the tests give
# them (test_mode2_rewards.EET), EET with CIR's own copy, BG:EE, and a game
# CIR does not know.
NAMINGS = {"eet": mode2_tests.EET, "eet copy": {"game": "eet", "names": None},
           "bgee": {"game": "bgee", "names": None}, "bgt": {"game": "bgt", "names": None}}


def naming_args(naming: dict | None) -> dict:
    """The game and EET names keywords, as the Rust tests read them."""
    if not naming:
        return {}
    names = naming["names"]
    return {"game": naming["game"], "names": None if names is None else
            {"tables": names.tables, "installed": names.installed, "notes": list(names.notes)}}


def patched(s: Scenario, label: str, resource: str, data: bytes, refs=None, ddwell_ref=None, naming=None):
    """Record patch_reward; its result, or None when it raises."""
    naming = naming or {}
    s.case(f"patch_reward {label}", "mode2_rewards.patch_reward",
           {"resource": resource, "data": data, "refs": refs, "ddwell_ref": ddwell_ref, **naming_args(naming)},
           lambda: mode2_rewards.patch_reward(resource, data, refs=refs, ddwell_ref=ddwell_ref, **naming))
    try:
        return mode2_rewards.patch_reward(resource, data, refs=refs, ddwell_ref=ddwell_ref, **naming)
    except ProfileError:
        return None


def gives_case(s: Scenario, label: str, resource: str, data: bytes, key: str, naming=None) -> None:
    naming = naming or {}
    s.case(f"gives_reward {label}", "mode2_rewards.gives_reward",
           {"resource": resource, "data": data, "key": key, **naming_args(naming)},
           lambda: mode2_rewards.gives_reward(resource, data, key, **naming))


def reward_checks(s: Scenario, label: str, resource: str, data: bytes, naming=None) -> None:
    """What the check and Apply read from these bytes: own patch, reasons, re-export."""
    naming = naming or {}
    args = {"resource": resource, "data": data, **naming_args(naming)}
    s.case(f"own_patch {label}", "mode2_rewards.own_patch", args,
           lambda: mode2_rewards.own_patch(resource, data, **naming))
    s.case(f"route_reasons {label}", "mode2_rewards.route_reasons", args,
           lambda: mode2_rewards.route_reasons(resource, data, **naming))
    if resource.startswith("dlg:"):
        for key in mode2_rewards.RESOURCE_REWARDS.get(resource, ()):
            gives_case(s, f"{label} {key}", resource, data, key, naming)
    patched(s, f"again {label}", resource, data, naming=naming)


def patch_dialogue_case(s: Scenario, label: str, data: bytes, specs, action_refs=None, defer_departure=True,
                        numeric_target=True) -> None:
    """_patch_dialogue's result and the reasons of the routes it left out."""
    def run():
        reasons = {}
        result = mode2_rewards._patch_dialogue(data, tuple(specs), action_refs=action_refs,
                                               defer_departure=defer_departure, numeric_target=numeric_target,
                                               reasons=reasons)
        return result, reasons
    s.case(f"patch_dialogue {label}", "mode2_rewards._patch_dialogue",
           {"data": data, "specs": list(specs), "action_refs": action_refs, "defer_departure": defer_departure,
            "numeric_target": numeric_target}, run)


def text_original_case(s: Scenario, label: str, resource: str, data: bytes, naming=None) -> None:
    naming = naming or {}
    s.case(f"text_original {label}", "mode2_rewards._text_original",
           {"data": data, "resource": resource, **naming_args(naming)},
           lambda: sorted_refs(mode2_rewards._text_original(data, resource, **naming)))


def script_original_case(s: Scenario, label: str, resource: str, data: bytes) -> None:
    specs = mode2_rewards.resource_specs(resource)
    s.case(f"script_original {label}", "mode2_rewards._script_original", {"data": data, "specs": list(specs)},
           lambda: mode2_rewards._script_original(data, specs))


def unpatched_case(s: Scenario, label: str, resource: str, data: bytes) -> None:
    s.case(f"unpatched_script {label}", "mode2_rewards.unpatched_script", {"resource": resource, "data": data},
           lambda: mode2_rewards.unpatched_script(resource, data))


def book_cases(s: Scenario) -> None:
    """test_the_books_two_scrolls_are_gated_apart_beside_the_rings_quest."""
    gifts = mode2_tests.BOOK_GIFTS.encode()
    installed = mode2_tests.book_fixture()
    twice = installed.replace(gifts, gifts + mode2_tests.action(140, text="scrl9b", target=17).encode())
    for label, original in (("installed", installed), ("game's own", mode2_tests.book_fixture(False)),
                            ("crlf", crlf(installed)), ("twice", twice),
                            ("neither", installed.replace(gifts, b""))):
        result = patched(s, f"book {label}", "bcs:ppbook", original)
        reward_checks(s, f"book original {label}", "bcs:ppbook", original)
        script_original_case(s, f"book original {label}", "bcs:ppbook", original)
        unpatched_case(s, f"book original {label}", "PPBOOK", original)
        if result is None:
            continue
        reward_checks(s, f"book result {label}", "bcs:ppbook", result)
        script_original_case(s, f"book result {label}", "bcs:ppbook", result)
        unpatched_case(s, f"book result {label}", "ppbook.bcs", result)
        tampered = result.replace(b"FLPR_RM2_262", b"FLPR_OTHERS", 1)
        reward_checks(s, f"book tampered {label}", "bcs:ppbook", tampered)
        unpatched_case(s, f"book tampered {label}", "bcs:ppbook", tampered)
    unpatched_case(s, "dialogue", "dlg:uhogre01", b"")
    unpatched_case(s, "unknown", "bcs:nothing", installed)


def patch_destroys_case(s: Scenario, label: str, data: bytes, resource: str = "bcs:sahtreas") -> None:
    """_patch_destroys's result and the reasons of the routes it left out."""
    specs = mode2_rewards.resource_specs(resource)

    def run():
        reasons = {}
        return mode2_rewards._patch_destroys(data, specs, reasons=reasons), reasons
    s.case(f"patch_destroys {label}", "mode2_rewards._patch_destroys", {"data": data, "specs": list(specs)}, run)


def destroyed_case(s: Scenario, label: str, data: bytes, resource: str = "bcs:sahtreas") -> None:
    """The items a script destroys by name (destroyed_items)."""
    s.case(f"destroyed_items {label}", "mode2_rewards.destroyed_items", {"resource": resource, "data": data},
           lambda: mode2_rewards.destroyed_items(resource, data))


def prince_cases(s: Scenario) -> None:
    """test_the_princes_reward_reply_hands_over_what_he_carries."""
    game = ('AddXPObject(Player1,60500)\nSetGlobal("senityilijob","GLOBAL",2)\n'
            'SetGlobal("villynatyjob","GLOBAL",2)\nRunAwayFrom("sahkng01",15)')
    states = [
        [("True()", 'SetGlobal("villynatyjob","GLOBAL",1)', 1),
         ("", 'SetGlobal("villynatyjob","LOCALS",2)\nIncrementGlobal("VillynatyJob","Locals",1)', 1),
         ("", 'SetGlobal("villynatyjob2","GLOBAL",2)\nDestroySelf()', None)],
        [('Global("villynatyjob","GLOBAL",2)', 'DisplayStringHead(Myself,1)', None), ("", game, None)],
        [("", 'ActionOverride( "sahpr2" , SetGlobal( "VillynatyJob" , "global" , 2 ) )\nDestroySelf()', 3)],
        [("", 'DestroySelf()', None)],
    ]
    specs = mode2_rewards.resource_specs("dlg:sahpr2")
    # His two places, rows 59 and 60, in that order.
    first = b'EEex_LuaAction("return(FLPR_Transfer(EEex_LuaAction_Object,59))")\r\n'
    second = b'EEex_LuaAction("return(FLPR_Transfer(EEex_LuaAction_Object,60))")\r\n'
    transfer = first + second
    for beamdog in (True, False):
        layout = "beamdog" if beamdog else "weidu"
        original = mode2_tests.layout_dialogue("sahpr2", states, beamdog=beamdog)
        reward_checks(s, f"sahpr2 {layout}", "dlg:sahpr2", original)
        result = patched(s, f"sahpr2 {layout}", "dlg:sahpr2", original)
        reward_checks(s, f"sahpr2 {layout} result", "dlg:sahpr2", result)
        for departure in (True, False):
            patch_dialogue_case(s, f"sahpr2 {layout} departure {departure}", original, specs,
                                defer_departure=departure)
        patch_dialogue_case(s, f"sahpr2 {layout} string target", original, specs, numeric_target=False)
        after = quest_options._Dialogue(result, "sahpr2")
        job = struct.unpack_from("<I", mode2_tests.outgoing(after, 1)[1], 16)[0]
        other = struct.unpack_from("<I", mode2_tests.outgoing(after, 0)[0], 16)[0]
        rest = after.actions[job][len(transfer):]
        for label, changes in (
                ("another place", {job: after.actions[job].replace(b",59))", b",58))")}),
                ("other reply", {job: rest, other: transfer + after.actions[other]}),
                ("behind", {job: rest + b"\r\n" + transfer}),
                ("twice", {job: transfer + after.actions[job]}),
                ("gone", {job: rest}),
                ("one place", {job: first + rest}),
                ("the other place", {job: second + rest}),
                ("swapped", {job: second + first + rest})):
            tampered_dialogue = quest_options._Dialogue(result, "sahpr2")
            for index, text in changes.items():
                tampered_dialogue.actions[index] = text
            reward_checks(s, f"sahpr2 {layout} {label}", "dlg:sahpr2", tampered_dialogue.render())
    for label, changed in (
            ("no job end", {1: [("", 'SetGlobal("villynatyjob","GLOBAL",3)', None)], 2: [("", "NoAction()", None)]}),
            ("unreadable", {2: [("", 'SetGlobal("villynatyjob","GLOBAL",2', None)]}),
            ("override unreadable", {2: [("", 'ActionOverride("sahpr2",SetGlobal("villynatyjob","GLOBAL",2)', None)]}),
            ("unquoted scope", {2: [("", 'SetGlobal("villynatyjob",GLOBAL,2)', None)]}),
            ("incremented", {0: [("", 'IncrementGlobal("villynatyjob","GLOBAL",1)', 1)]}),
            ("override timer", {2: [("", 'ActionOverride("sahpr2",SetGlobalTimer("VILLYNATYJOB","global",2))', None)]})):
        variant = [changed.get(index, replies) for index, replies in enumerate(states)]
        reward_checks(s, f"sahpr2 {label}", "dlg:sahpr2", mode2_tests.layout_dialogue("sahpr2", variant))
    # Only the route's own row of Item Randomiser's table is its place.
    for label, targets in (("none", ()), ("two", ("bg2:mode2:target:59", "bg2:mode2:target:58")),
                           ("base", ("bg2:base:target:59",)), ("bg1", ("bg1:mode2:target:59",))):
        spec = dict(specs[0], target_ids=targets)
        patch_dialogue_case(s, f"sahpr2 target {label}", mode2_tests.dialogue_fixture(specs), (spec,))


def adalon_cases(s: Scenario) -> None:
    """test_adalon_hands_over_what_she_carries_when_she_takes_her_eggs and
    test_an_earlier_versions_patch_of_adalons_dialogue_is_made_again_with_her_route."""
    specs = mode2_rewards.resource_specs("dlg:udsilver")
    transfer = mode2_tests.ADALON_TRANSFER
    fixture = mode2_tests.dialogue_fixture(specs)
    for beamdog in (True, False):
        layout = "beamdog" if beamdog else "weidu"
        original = mode2_tests.layout_dialogue("udsilver", mode2_tests.ADALON_STATES, beamdog=beamdog)
        reward_checks(s, f"udsilver {layout}", "dlg:udsilver", original)
        result = patched(s, f"udsilver {layout}", "dlg:udsilver", original)
        reward_checks(s, f"udsilver {layout} result", "dlg:udsilver", result)
        for departure in (True, False):
            patch_dialogue_case(s, f"udsilver {layout} departure {departure}", original, specs,
                                defer_departure=departure)
        patch_dialogue_case(s, f"udsilver {layout} string target", original, specs, numeric_target=False)
        after = quest_options._Dialogue(result, "udsilver")
        job = struct.unpack_from("<I", mode2_tests.outgoing(after, 2)[0], 16)[0]
        other = struct.unpack_from("<I", mode2_tests.outgoing(after, 0)[0], 16)[0]
        rest = after.actions[job][len(transfer):]
        for label, changes in (
                ("another place", {job: after.actions[job].replace(b",72,2,1))", b",73,2,1))")}),
                ("another table", {job: after.actions[job].replace(b",72,2,1))", b",72))")}),
                ("other reply", {job: rest, other: transfer + after.actions[other]}),
                ("behind", {job: rest + b"\r\n" + transfer}),
                ("twice", {job: transfer + after.actions[job]}),
                ("gone", {job: rest})):
            tampered_dialogue = quest_options._Dialogue(result, "udsilver")
            for index, changed in changes.items():
                tampered_dialogue.actions[index] = changed
            reward_checks(s, f"udsilver {layout} {label}", "dlg:udsilver", tampered_dialogue.render())
    # An earlier version's patch, with one of her two routes: patched again
    # from the original; a changed copy of it is not ours.
    for name, original in (("fixture", fixture),
                           ("beamdog", mode2_tests.layout_dialogue("udsilver", mode2_tests.ADALON_STATES)),
                           ("weidu", mode2_tests.layout_dialogue("udsilver", mode2_tests.ADALON_STATES,
                                                                 beamdog=False))):
        for label, routes in (("crossbow", specs[:1]), ("place", specs[1:])):
            earlier = mode2_rewards._patch_dialogue(original, routes)
            reward_checks(s, f"udsilver {name} earlier {label}", "dlg:udsilver", earlier)
            reward_checks(s, f"udsilver {name} earlier {label} again", "dlg:udsilver",
                          mode2_rewards.patch_reward("dlg:udsilver", earlier))
        earlier = mode2_rewards._patch_dialogue(original, specs[:1])
        reward_checks(s, f"udsilver {name} earlier tampered", "dlg:udsilver",
                      earlier.replace(b'"FLPR_EA_EGGDEALIO","GLOBAL",1)', b'"FLPR_EA_EGGDEALIO","GLOBAL",2)', 1))
    # No reply takes her eggs, or one names them unreadably; her crossbow's
    # route cannot be prepared.
    for label, changed in (
            ("other item", {2: [("", 'TakePartyItem("misc9x")', 3), ("", "NoAction()", None)]}),
            ("no eggs taken", {1: [("", 'GiveItemCreate("xbow10",LastTalkedToBy,1,1,1)', 2)],
                               2: [("", 'TakePartyItem("misc9x")', 3), ("", "NoAction()", None)]}),
            ("unreadable", {2: [("", 'TakePartyItem("misc9t"', 3), ("", "NoAction()", None)]}),
            ("override unreadable", {2: [("", 'ActionOverride(Player1,TakePartyItem("misc9t")', 3),
                                         ("", "NoAction()", None)]}),
            ("unquoted", {2: [("", 'TakePartyItem(misc9t)', 3), ("", "NoAction()", None)]}),
            ("no crossbow", {1: [("", 'TakePartyItem("misc9t")', 2)]})):
        variant = [changed.get(index, replies) for index, replies in enumerate(mode2_tests.ADALON_STATES)]
        data = mode2_tests.layout_dialogue("udsilver", variant)
        reward_checks(s, f"udsilver {label}", "dlg:udsilver", data)
        result = patched(s, f"udsilver {label}", "dlg:udsilver", data)
        reward_checks(s, f"udsilver {label} result", "dlg:udsilver", result)
    # Only one numbered row of the base or extended table is her place.
    for label, targets in (("none", ()), ("two", ("bg2:base:target:72", "bg2:base:target:73")),
                           ("extended", ("bg2:mode2:target:72",)), ("bg1", ("bg1:base:target:72",)),
                           ("other table", ("bg2:other:target:72",)), ("zero", ("bg2:base:target:072",)),
                           ("no row", ("bg2:base:target:",)), ("bg3", ("bg3:base:target:72",))):
        spec = dict(specs[1], target_ids=targets)
        patch_dialogue_case(s, f"udsilver target {label}", fixture, (spec,))
        patch_dialogue_case(s, f"udsilver target {label} string", fixture, (spec,), numeric_target=False)


def treasury_cases(s: Scenario) -> None:
    """test_each_reply_that_pays_with_the_rod_hands_the_treasury_over_after_its_guards,
    test_the_treasury_destroys_the_kings_items_only_while_his_grants_are_unchanged and
    test_a_treasury_that_could_take_a_moved_reward_otherwise_leaves_its_route_out."""
    specs = mode2_rewards.resource_specs("dlg:sahkng01")
    rod = next(spec for spec in specs if spec["route"] == "sahrod")
    line = mode2_tests.handover_line(rod)
    original = mode2_tests.dialogue_fixture(specs)
    result = mode2_rewards.patch_reward("dlg:sahkng01", original)
    first = next(index for index, text in enumerate(quest_options._Dialogue(result, "sahkng01").triggers)
                 if line in text)
    for label, change in (("missing", lambda text: text.replace(line, b"")), ("twice", lambda text: text + line),
                          ("another place", lambda text: text.replace(b",6))", b",7))")),
                          ("before the guards", lambda text: line + text.replace(line, b""))):
        dialogue = quest_options._Dialogue(result, "sahkng01")
        dialogue.triggers[first] = change(dialogue.triggers[first])
        reward_checks(s, f"sahkng01 handover {label}", "dlg:sahkng01", dialogue.render())
    # A king who pays with the rod in another reply than with the bracer.
    king = quest_options._Dialogue(original, "sahkng01")
    king.actions = [text.replace(b'GiveItemCreate("rods02",LastTalkedToBy,0,0,0)\r\r\n', b"") for text in king.actions]
    split = mode2_tests.with_actions(king.render(), "sahkng01",
                                     {(80, 0): b'GiveItemCreate("rods02",LastTalkedToBy,0,0,0)\r\n'})
    reward_checks(s, "sahkng01 split", "dlg:sahkng01", split)
    reward_checks(s, "sahkng01 split result", "dlg:sahkng01", patched(s, "sahkng01 split", "dlg:sahkng01", split))
    # Only the route's own row of Item Randomiser's table is its place.
    for label, targets in (("own", rod["target_ids"]), ("none", ()),
                           ("two", ("bg2:mode2:target:6", "bg2:mode2:target:58")), ("base", ("bg2:base:target:6",)),
                           ("bg1", ("bg1:mode2:target:6",)), ("unnumbered", ("bg2:mode2:target:x",))):
        spec = dict(rod, target_ids=targets)
        s.case(f"handover {label}", "mode2_rewards._handover", {"spec": spec},
               lambda spec=spec: mode2_rewards._handover(spec))
    # SAHTREAS.BCS with other mods' blocks, other layouts, and actions the
    # route cannot gate.
    action, trigger, book_block = mode2_tests.action, mode2_tests.trigger, mode2_tests.book_block
    treasury, paid = mode2_tests.treasury_fixture, mode2_tests.KING_PAID
    other = book_block([trigger(16399, 1, "GLOBALOtherMod")], action(30, 2, "GLOBALOtherMod"))
    testing = book_block([trigger(16481, 0, "RODS02")], action(30, 3, "GLOBALOtherMod"))
    king_block = book_block([paid], mode2_tests.KING_DESTROYS)
    variants = (
        ("installed", treasury()), ("mixed", treasury(other, king_block, testing)),
        ("mixed crlf", crlf(treasury(other, king_block, testing))),
        ("lowercase", treasury(book_block([paid], action(169, text="rods02") + action(169, text="brac19")))),
        ("other actor", treasury(book_block([paid], action(169, text="BRAC19", actor=21) +
                                            action(30, 1, "GLOBALOtherMod")))),
        ("alternatives", treasury(mode2_tests.alternatives(action(169, text="RODS02"), action(169, text="RODS02") +
                                                           action(30, 1, "GLOBALOtherMod"),
                                                           condition="GLOBALsahkngjob"))),
        ("two blocks", treasury(book_block([paid], action(169, text="RODS02")),
                                book_block([trigger(16399, 3, "GLOBALsahkngjob")], action(169, text="BRAC19")))),
        ("rod only", treasury(book_block([paid], action(169, text="RODS02")))),
        ("another destroy", treasury(king_block, book_block([trigger(16399, 1, "GLOBALOtherMod")],
                                                            action(169, text="Sw1h01", actor=21)))),
        ("plain", treasury(other, testing)),
        ("giving", treasury(book_block([paid], mode2_tests.KING_DESTROYS + action(15, text="RODS02", target=21)))),
        ("both given", treasury(book_block([paid], action(15, text="RODS02", target=21) +
                                           action(15, text="BRAC19", target=21)))),
        ("created", treasury(book_block([paid], mode2_tests.KING_DESTROYS + action(82, text="BRAC19")))),
        ("second string", treasury(book_block([paid], mode2_tests.KING_DESTROYS +
                                              action(363, text="shop01", second="rods02")))),
        ("unreadable", treasury().replace(b"169OB", b"169XX", 1)),
        ("no blocks", b"SC\nSC\n"), ("no header", b"XX\nSC\n"), ("empty", b""))
    for label, data in variants:
        result = patched(s, f"sahtreas {label}", "bcs:sahtreas", data)
        reward_checks(s, f"sahtreas {label}", "bcs:sahtreas", data)
        unpatched_case(s, f"sahtreas {label}", "bcs:sahtreas", data)
        patch_destroys_case(s, label, data)
        destroyed_case(s, label, data)
        if result is None or result == data:
            continue
        reward_checks(s, f"sahtreas {label} result", "bcs:sahtreas", result)
        unpatched_case(s, f"sahtreas {label} result", "SAHTREAS.BCS", result)
        patch_destroys_case(s, f"{label} result", result)
        destroyed_case(s, f"{label} result", result)
    for game in ("eet", "eet copy", "bgt"):
        reward_checks(s, f"sahtreas {game}", "bcs:sahtreas", treasury(), NAMINGS[game])
    # Our patch changed: a block dropped, a destroy back, a guard turned or
    # one route's gates taken out.
    result = mode2_rewards.patch_reward("bcs:sahtreas", treasury())
    blocks = list(script_blocks(result).blocks)
    no_action = mode2_tests.NO_ACTION.encode()
    for label, changed in (
            ("dropped", treasury(*(block.decode() for block in blocks[:3]))),
            ("destroy back", result.replace(no_action, action(169, text="RODS02").encode(), 1)),
            ("turned", result.replace(mode2_tests.route_guard("FLPR_QM2_7", False).encode(),
                                      mode2_tests.route_guard("FLPR_QM2_7", True).encode(), 1)),
            ("bracer ungated", treasury(king_block.replace("CO\nRS", mode2_tests.route_guard("FLPR_QM2_7", False) +
                                                           "CO\nRS"),
                                        king_block.replace("CO\nRS", mode2_tests.route_guard("FLPR_QM2_7", True) +
                                                           "CO\nRS").replace(action(169, text="RODS02"),
                                                                             mode2_tests.NO_ACTION))),
            ("swapped", treasury(*(block.decode() for block in (blocks[1], blocks[0], *blocks[2:])))),
            ("crlf mixed", result.replace(b"\n", b"\r\n", 3))):
        reward_checks(s, f"sahtreas {label}", "bcs:sahtreas", changed)
        unpatched_case(s, f"sahtreas {label}", "bcs:sahtreas", changed)


def tolgerias_cases(s: Scenario) -> None:
    """test_tolgerias_destroys_his_ring_only_while_it_is_the_original: TOLGER.BCS
    with other mods' blocks, other layouts, and blocks the route cannot gate."""
    action, trigger, book_block = mode2_tests.action, mode2_tests.trigger, mode2_tests.book_block
    script, has, gone = mode2_tests.tolgerias_fixture, mode2_tests.HAS_RING, mode2_tests.RING_GONE
    destroy, more = mode2_tests.RING_DESTROYED, action(30, 2, "GLOBALOtherMod")
    other = book_block([trigger(16399, 1, "GLOBALOtherMod")], more)

    def tested(*responses: str) -> str:
        """A block that tests the ring, whose responses the engine chooses between."""
        return "CR\nCO\n" + has + gone + "CO\nRS\n" + "".join(f"RE\n50{text}RE\n" for text in responses) + "RS\nCR\n"

    variants = (
        ("installed", script()), ("crlf", crlf(script())),
        ("mixed", script(other, book_block([has, gone], destroy), other)),
        ("untested", script(book_block([gone], destroy))),
        ("tested and untested", script(book_block([has, gone], destroy), book_block([gone], destroy))),
        ("uppercase", script(book_block([trigger(16481, 0, "RING33"), gone], action(169, text="RING33")))),
        ("other actor", script(book_block([has, gone], action(169, text="ring33", actor=21)))),
        ("tested twice", script(book_block([has, gone], destroy + destroy))),
        ("tested alternatives", script(tested(destroy, destroy))),
        ("tested alternatives more", script(tested(destroy, destroy + more))),
        ("more", script(book_block([has, gone], destroy + more))),
        ("more before", script(book_block([has, gone], more + destroy))),
        ("giving", script(book_block([has, gone], destroy + action(15, text="ring33", target=21)))),
        ("testing only", script(book_block([has], more))),
        ("plain", script(book_block([trigger(16465, 0, "TOLGER2")], action(111)))),
        ("unreadable", script().replace(b"169OB", b"169XX", 1)),
        ("no blocks", b"SC\nSC\n"), ("empty", b""))
    for label, data in variants:
        result = patched(s, f"tolger {label}", "bcs:tolger", data)
        reward_checks(s, f"tolger {label}", "bcs:tolger", data)
        unpatched_case(s, f"tolger {label}", "bcs:tolger", data)
        patch_destroys_case(s, f"tolger {label}", data, "bcs:tolger")
        destroyed_case(s, f"tolger {label}", data, "bcs:tolger")
        if result is None or result == data:
            continue
        reward_checks(s, f"tolger {label} result", "bcs:tolger", result)
        unpatched_case(s, f"tolger {label} result", "TOLGER.BCS", result)
        patch_destroys_case(s, f"tolger {label} result", result, "bcs:tolger")
        destroyed_case(s, f"tolger {label} result", result, "bcs:tolger")
    for game in ("eet", "eet copy", "bgt"):
        reward_checks(s, f"tolger {game}", "bcs:tolger", script(), NAMINGS[game])
    # Our patch changed: the guard turned or dropped, the treasury's stand-in
    # added, the blocks swapped.
    result = mode2_rewards.patch_reward("bcs:tolger", script())
    first, gated, last = (block.decode() for block in script_blocks(result).blocks)
    unselected, selected = (mode2_tests.route_guard("FLPR_QM2_40", choice) for choice in (False, True))
    for label, changed in (
            ("turned", result.replace(unselected.encode(), selected.encode(), 1)),
            ("guard dropped", result.replace(unselected.encode(), b"", 1)),
            ("stand-in", script(first, gated, gated.replace(unselected, selected).replace(destroy, mode2_tests.NO_ACTION),
                                last)),
            ("swapped", script(gated, first, last)),
            ("crlf mixed", result.replace(b"\n", b"\r\n", 3))):
        reward_checks(s, f"tolger {label}", "bcs:tolger", changed)
        unpatched_case(s, f"tolger {label}", "bcs:tolger", changed)


def registry_cases(s: Scenario) -> None:
    for table in ("REWARDS", "DIRECT_REWARDS", "TRANSFER_SPECS", "RESOURCE_REWARDS",
                  "SOURCE_REWARDS", "TARGET_REWARDS", "TEXT_REFS", "ACTION_TEXT_REFS", "ADAPTER_ID"):
        s.case(table, f"mode2_rewards.{table}", {}, lambda table=table: getattr(mode2_rewards, table))
    # (resource, kind, item) keys as [resource, kind, item, route] rows.
    s.case("PHYSICAL_SOURCE_REWARDS", "mode2_rewards.PHYSICAL_SOURCE_REWARDS", {},
           lambda: [[*key, route] for key, route in mode2_rewards.PHYSICAL_SOURCE_REWARDS.items()])
    for identity in list(mode2_rewards.SOURCE_REWARDS) + ["original-source:bg2:mode2:item:42", "x"]:
        s.case(f"source_spec {identity}", "mode2_rewards.source_spec", {"source_id": identity},
               lambda identity=identity: mode2_rewards.source_spec(identity))
    for identity in list(mode2_rewards.TARGET_REWARDS) + ["bg2:mode2:target:1"]:
        s.case(f"target_spec {identity}", "mode2_rewards.target_spec", {"target_id": identity},
               lambda identity=identity: mode2_rewards.target_spec(identity))
    for name in list(mode2_rewards.TRANSFER_SPECS) + ["udlesa"]:
        s.case(f"transfer_spec {name}", "mode2_rewards.transfer_spec", {"name": name},
               lambda name=name: mode2_rewards.transfer_spec(name))
    for resource in list(mode2_rewards.RESOURCE_REWARDS) + ["DLG:ARAN", "dlg:nothing", "aran"]:
        s.case(f"resource_specs {resource}", "mode2_rewards.resource_specs", {"resource": resource},
               lambda resource=resource: mode2_rewards.resource_specs(resource))
    for key in list(mode2_rewards.REWARDS) + ["x"]:
        s.case(f"reward {key}", "mode2_rewards.reward", {"key": key}, lambda key=key: mode2_rewards.reward(key))
    # Each route as each game names it: EET renames BG1's items (Varci's
    # SHLD07A); BG2's carried routes check their places' names; an unknown
    # game is refused.
    for game, naming in NAMINGS.items():
        for key in mode2_rewards.REWARDS:
            s.case(f"reward {key} {game}", "mode2_rewards.reward", {"key": key, **naming_args(naming)},
                   lambda key=key, naming=naming: mode2_rewards.reward(key, **naming))
        for identity in ("original-source:bg1:mode2:item:20", "original-source:bg2:mode2:item:32", "x"):
            s.case(f"source_spec {identity} {game}", "mode2_rewards.source_spec",
                   {"source_id": identity, **naming_args(naming)},
                   lambda identity=identity, naming=naming: mode2_rewards.source_spec(identity, **naming))
        for identity in ("bg1:mode2:target:20", "bg2:mode2:target:45", "bg2:mode2:target:50"):
            s.case(f"target_spec {identity} {game}", "mode2_rewards.target_spec",
                   {"target_id": identity, **naming_args(naming)},
                   lambda identity=identity, naming=naming: mode2_rewards.target_spec(identity, **naming))
        for name in mode2_rewards.TRANSFER_SPECS:
            s.case(f"transfer_spec {name} {game}", "mode2_rewards.transfer_spec", {"name": name, **naming_args(naming)},
                   lambda name=name, naming=naming: mode2_rewards.transfer_spec(name, **naming))
        for resource in ("dlg:varci", "dlg:trgeni01", "bcs:asysta01", "dlg:sahkng01", "bcs:sahtreas", "dlg:tolger",
                         "bcs:tolger"):
            s.case(f"resource_specs {resource} {game}", "mode2_rewards.resource_specs",
                   {"resource": resource, **naming_args(naming)},
                   lambda resource=resource, naming=naming: mode2_rewards.resource_specs(resource, **naming))


def bg1_route_cases(s: Scenario) -> None:
    """test_eets_varci_gives_shld07a_to_an_object_function_and_the_route_gates_it, on every game."""
    for game, naming in NAMINGS.items():
        if game == "bgt":
            continue
        original = mode2_tests.dialogue_fixture(mode2_rewards.resource_specs("dlg:varci", **naming))
        reward_checks(s, f"varci {game}", "dlg:varci", original, naming)
        # The same dialogue read without the game's names, and as the other games name it.
        reward_checks(s, f"varci {game} unnamed", "dlg:varci", original)
        for other, other_naming in NAMINGS.items():
            if other != game:
                reward_checks(s, f"varci {game} as {other}", "dlg:varci", original, other_naming)
        result = mode2_rewards.patch_reward("dlg:varci", original, **naming)
        reward_checks(s, f"varci {game} result", "dlg:varci", result, naming)
        reward_checks(s, f"varci {game} result unnamed", "dlg:varci", result)
        reward_checks(s, f"varci {game} tampered", "dlg:varci", result.replace(b"FLPR_", b"OTHER", 1), naming)
    # A carried BG2 route's text patch on EET: its places keep their names.
    resource = "dlg:trgeni01"
    refs = {old: 200000 + old for old in mode2_rewards.TEXT_REFS[resource]}
    original = text_variant(resource)
    result = patched(s, "trgeni01 eet", resource, original, refs=refs, naming=mode2_tests.EET)
    reward_checks(s, "trgeni01 eet result", resource, result, mode2_tests.EET)
    text_original_case(s, "trgeni01 eet", resource, result, mode2_tests.EET)
    text_original_case(s, "trgeni01 bgt", resource, result, NAMINGS["bgt"])


def reason_cases(s: Scenario) -> None:
    """test_routes_the_check_cannot_recognise_name_their_reason, and more replies to judge."""
    original = mode2_tests.dialogue_fixture(mode2_rewards.resource_specs("dlg:uhogre01"))
    grant = 'GiveItemCreate("shld26",LastTalkedToBy,0,0,0)'
    for label, changes in (
            ("no grant", {(22, 0): b"GiveGoldForce(500)", (22, 1): b"GiveGoldForce(500)"}),
            ("twice", {(22, 0): f"{grant}\r\n{grant}".encode()}),
            ("taken", {(22, 1): f'{grant}\r\nTakePartyItem("shld26")'.encode()}),
            ("unreadable", {(22, 0): b'GiveItemCreate("shld26",LastTalkedToBy,0,0,0'}),
            ("no recipient", {(22, 0): b'GiveItemCreate("shld26")'}),
            ("nested", {(22, 0): b'ActionOverride(Player1,GiveItemCreate("shld26",Player1,0,0,0))'}),
            ("spaced", {(22, 0): b'  giveitemcreate ( "SHLD26" , lasttalkedtoby , 0 , 0 , 0 ) \n'}),
            ("quoted recipient", {(22, 0): b'GiveItemCreate("shld26","Player1",0,0,0)'}),
            ("object recipient", {(22, 0): b'GiveItemCreate("shld26",[PC.0.0.MAGE],0,0,0)'}),
            ("lf", {(22, 0): f"Wait(1)\n{grant}\nEscapeArea()".encode()}),
            ("string paren", {(22, 0): f'DisplayStringHead(Myself,"(x")\r\n{grant}'.encode()}),
            ("other item", {(22, 0): b'GiveItemCreate("shld26x",LastTalkedToBy,0,0,0)'}),
            ("created elsewhere", {(22, 0): b'CreateItem("shld26",0,0,0)'}),
            ("given", {(22, 0): b'GiveItem("shld26",LastTalkedToBy)'}),
            ("empty", {(22, 0): b"", (22, 1): b""}),
            ("byte", {(22, 0): b'GiveItemCreate("shld26",Pl\xe4yer1,0,0,0)'}),
            ("one gated", {(22, 1): b"GiveGoldForce(500)"}),
            # test_routes_the_check_cannot_recognise_name_their_reason (0eb5619).
            ("given other", {(22, 1): f'{grant}\r\nGiveItem("shld26",Player2)'.encode()}),
            ("take replace", {(23, 0): b'TakeItemReplace("Shld26","misc5x",LastTalkedToBy)'}),
            ("override", {(22, 0): b'ActionOverride(Player2,GiveItemCreate("shld26",Myself,0,0,0))'}),
            # test_actions_that_only_take_show_or_test_the_reward_never_stop_a_route.
            ("only taken", {(22, 1): f'TakePartyItem("misc5x")\r\n{grant}\r\nDestroyItem("SHLD26")'.encode(),
                            (23, 0): b'TakeItemReplace("misc5x","shld26",LastTalkedToBy)\r\nActionOverride(Player2,'
                                     b'DestroyItem("shld26"))\r\nSetGlobal("SHLD26","GLOBAL",1)'}),
            # Every other action that puts the item somewhere.
            ("created global", {(22, 0): f'{grant}\r\nCreateItemGlobal("Var","GLOBAL","shld26")'.encode()}),
            ("transformed", {(22, 0): f'{grant}\r\nTransformItem("misc5x","SHLD26")'.encode()}),
            ("dropped", {(22, 0): f'{grant}\r\nDropItem("shld26",[100.200])'.encode()}),
            ("picked up", {(22, 0): f'{grant}\r\nPickUpItem("shld26")'.encode()}),
            ("stocked", {(22, 0): f'{grant}\r\nAddStoreItem("shop01","shld26",1,0)'.encode()}),
            ("got", {(23, 0): b'GetItem("shld26",Player3)'}),
            ("other store item", {(22, 0): f'{grant}\r\nAddStoreItem("shld26","misc5x",1,0)'.encode()}),
            ("override unreadable", {(22, 0): f'{grant}\r\nActionOverride(Player2)'.encode()}),
            ("override others", {(22, 0): f'{grant}\r\nActionOverride(Player2,TakePartyItem("shld26"))'.encode()})):
        reward_checks(s, f"uhogre01 {label}", "dlg:uhogre01", mode2_tests.with_actions(original, "uhogre01", changes))
    reward_checks(s, "uhogre01 broken", "dlg:uhogre01", b"DLG V1.0" + bytes(10))
    reward_checks(s, "uhogre01 no bytes", "dlg:uhogre01", b"")
    # test_the_sahuagin_kings_reply_gates_the_bracer_and_the_treasury_rod_apart:
    # a king who gives no rod, or gives it in other ways.
    king = quest_options._Dialogue(mode2_tests.dialogue_fixture(mode2_rewards.resource_specs("dlg:sahkng01")), "sahkng01")
    king.actions = [text.replace(b'GiveItemCreate("rods02",LastTalkedToBy,0,0,0)\r\r\n', b"") for text in king.actions]
    without = king.render()
    reward_checks(s, "sahkng01 without rods02", "dlg:sahkng01", without)
    for label, text in (("taking", b'TakePartyItem("RODS02")\r\n'),
                        ("override", b'ActionOverride("sahpr2",GiveItemCreate("rods02",LastTalkedToBy,0,0,0))\r\n'),
                        ("created", b'CreateItem("rods02",0,0,0)\r\n'),
                        ("unreadable", b'GiveItemCreate("rods02",LastTalkedToBy,0,0,0\r\n')):
        reward_checks(s, f"sahkng01 {label}", "dlg:sahkng01",
                      mode2_tests.with_actions(without, "sahkng01", {(80, 0): text}))
    for label, resource, key in (("other route", "dlg:sahkng01", "aran"), ("script", "bcs:ddwell", "ddwell"),
                                 ("unknown", "dlg:nothing", "sahrod"), ("bare", "SAHKNG01.DLG", "sahrod")):
        gives_case(s, label, resource, without, key)
    transfer = mode2_rewards.resource_specs("dlg:loutcapt")[0]
    state, ordinal = transfer["branches"][0]
    loutcapt = mode2_tests.dialogue_fixture(transfer)
    for label, text in (("created", b'GiveItemCreate("dagg11",LastTalkedToBy,0,0,0)'),
                        ("given twice", b'GiveItem("dagg11",LastTalkedToBy)\r\nGiveItem("dagg11",Player1)'),
                        ("other recipient", b'GiveItem("dagg11",Player3)')):
        reward_checks(s, f"loutcapt {label}", "dlg:loutcapt",
                      mode2_tests.with_actions(loutcapt, "loutcapt", {(state, ordinal): text}))
    # A text route needs the text that names the reward where it gives it.
    reward_checks(s, "ppdjinn plain", "dlg:ppdjinn",
                  mode2_tests.dialogue_fixture(mode2_rewards.resource_specs("dlg:ppdjinn")))
    action, block, well_reward = mode2_tests.action, mode2_tests.block, mode2_tests.well_reward
    alternatives = mode2_tests.alternatives
    other = block(action(151, 40849, target=1))
    twice_shown = well_reward() + action(151, 40850, target=2) + action(116, text="HELM21")

    def well(*blocks):
        return ('SC\n' + "".join(blocks) + 'SC\n').encode()
    for label, data in (
            ("no block", well(other, other)),
            ("twice", well(other, block(well_reward() + action(140, text="HELM21", target=21)))),
            ("another actor", well(other, block(well_reward().replace(
                action(140, text="helm21", target=21), action(140, text="helm21", target=21, actor=17))))),
            ("other action", well(other, block(well_reward() + action(82, text="helm21")))),
            ("text twice", well(other, block(well_reward() + action(151, 40850, target=1)))),
            ("other text", well(other, block(well_reward(display=40849)))),
            ("open", b"SC\nCR\nCO\nSC\n"),
            ("mixed", mode2_tests.script_fixture("ddwell").replace(b"\n", b"\r\n", 3)),
            ("no blocks", b"SC\nSC\n"), ("no header", b"XX\nSC\n"), ("empty", b""),
            ("two grants", well(block(well_reward()), other, block(well_reward()))),
            ("text elsewhere", well(block(action(151, 40850, target=1)), block(action(140, text="helm21", target=21)))),
            ("crlf", crlf(mode2_tests.script_fixture("ddwell"))),
            ("add store", well(other, alternatives(well_reward(), action(363, text="shop01", second="helm21")))),
            ("alternatives", well(other, alternatives(well_reward(), twice_shown), other)),
            ("alternatives twice", well(other, alternatives(well_reward() + action(140, text="HELM21", target=21),
                                                            well_reward()))),
            ("alternatives one", well(other, alternatives(action(30, 1, "OtherModWell"), well_reward()))),
            ("created global", well(other, block(well_reward() + action(256, text="var", second="helm21")))),
            ("transformed", well(other, block(well_reward() + action(356, text="misc5x", second="HELM21")))),
            ("got", well(other, block(well_reward() + action(14, text="helm21", target=2))))):
        patched(s, f"ddwell {label} with ref", "bcs:ddwell", data, ddwell_ref=100001)
        reward_checks(s, f"ddwell {label}", "bcs:ddwell", data)
    asysta = mode2_tests.script_fixture("asysta01")
    for label, data in (
            ("other action", asysta.replace(action(82, text="Helm19").encode(), action(140, text="Helm19").encode())),
            ("twice", asysta.replace(action(82, text="Helm19").encode(), (action(82, text="Helm19") * 2).encode())),
            ("absent", asysta.replace(action(82, text="Helm19").encode(), b"")),
            ("crlf", crlf(asysta))):
        reward_checks(s, f"asysta01 {label}", "bcs:asysta01", data)


def layout_cases(s: Scenario) -> None:
    """test_grants_are_found_by_meaning_in_another_layout_with_another_mods_replies and
    test_script_blocks_are_found_by_meaning_in_another_layout."""
    states = [[("", 'SetGlobal("OgreTalk","LOCALS",1)', 9)] for _ in range(9)]
    states.append([
        ('PartyHasItem("misc5x")',
         'TakePartyItem("misc5x")\nGiveItemCreate("SHLD26",LastTalkedToBy,0,0,0)\nAddexperienceParty(9000)', None),
        ('!PartyHasItem("misc5x")', 'GIVEITEMCREATE("shld26",Player1,1,0,0)\nEscapeArea()', None),
        ('Global("OtherMod","GLOBAL",1)', 'SetGlobal("OtherMod","GLOBAL",2)', 12)])
    states += [[("", "", None)], [("", "", None)],
               [("", ' GiveItemCreate( "Shld26" , LastTalkedToBy , 0 , 0 , 0 ) \nSetGlobal("OtherMod","GLOBAL",3)',
                 None)]]
    for beamdog in (True, False):
        original = mode2_tests.layout_dialogue("uhogre01", states, beamdog=beamdog)
        result = patched(s, f"layout {beamdog}", "dlg:uhogre01", original, ddwell_ref=100001)
        reward_checks(s, f"layout original {beamdog}", "dlg:uhogre01", original)
        reward_checks(s, f"layout result {beamdog}", "dlg:uhogre01", result)
        reward_checks(s, f"layout tampered {beamdog}", "dlg:uhogre01", result.replace(b"FLPR_", b"OTHER", 1))
        patch_dialogue_case(s, f"layout {beamdog}", original, mode2_rewards.resource_specs("dlg:uhogre01"))
    action, block, well_reward = mode2_tests.action, mode2_tests.block, mode2_tests.well_reward
    added = block(action(30, 1, "OtherModWell"), "OtherModQuest")
    second = block(action(140, text="HELM21", target=17) + action(30, 3, "OtherModWell"), "OtherModQuest")
    for label, original in (
            ("crlf", ('SC\n' + added + block(well_reward(target=17)) + second + 'SC\n').encode().replace(b"\n", b"\r\n")),
            ("lf", ('SC\n' + added + block(well_reward(target=17)) + second + 'SC\n').encode())):
        result = patched(s, f"script layout {label}", "bcs:ddwell", original, ddwell_ref=100001)
        reward_checks(s, f"script layout original {label}", "bcs:ddwell", original)
        reward_checks(s, f"script layout result {label}", "bcs:ddwell", result)
        script_original_case(s, f"layout {label}", "bcs:ddwell", result)


def aran_cases(s: Scenario) -> None:
    """test_aran_route_left_out_alone_keeps_the_other_route."""
    original = mode2_tests.dialogue_fixture(mode2_rewards.resource_specs("dlg:aran"))
    dialogue = quest_options._Dialogue(original, "aran")
    dialogue.actions = [text.replace(b'GiveItemCreate("amul21",LastTalkedToBy,0,0,0)\r\r\n', b"")
                        for text in dialogue.actions]
    without = dialogue.render()
    result = patched(s, "aran without amul21", "dlg:aran", without)
    reward_checks(s, "aran without amul21", "dlg:aran", without)
    reward_checks(s, "aran without amul21 result", "dlg:aran", result)
    patch_dialogue_case(s, "aran without amul21", without, mode2_rewards.resource_specs("dlg:aran"))
    dialogue.actions = [text.replace(b'GiveItemCreate("ring07",LastTalkedToBy,0,0,0)\r\r\n', b"")
                        for text in dialogue.actions]
    reward_checks(s, "aran neither", "dlg:aran", dialogue.render())
    patch_dialogue_case(s, "aran neither", dialogue.render(), mode2_rewards.resource_specs("dlg:aran"))


def patch_dialogue_cases(s: Scenario, allocated: dict) -> None:
    for resource in mode2_rewards.RESOURCE_REWARDS:
        if not resource.startswith("dlg:"):
            continue
        specs = mode2_rewards.resource_specs(resource)
        original = mode2_tests.dialogue_fixture(specs)
        action_refs = ({old: allocated[resource][old] for old in mode2_rewards.ACTION_TEXT_REFS[resource]}
                       if resource in mode2_rewards.ACTION_TEXT_REFS else None)
        for defer, numeric in ((True, True), (True, False), (False, False)):
            patch_dialogue_case(s, f"{resource} {defer} {numeric}", original, specs, action_refs, defer, numeric)
    for name in ("trgeni01", "loutcapt", "amcler01", "jugjer01", "udlesa"):
        spec = mode2_rewards.resource_specs("dlg:" + name)[0]
        original = mode2_tests.dialogue_fixture(spec)
        for target_ids in (("bg1:mode2:target:32",), ("bg2:mode1:target:32",), ("bg2:mode2:target:32extra",),
                           ("bg2:mode2:target:32", "bg2:mode2:target:42"), ("bg2:mode2:target:032",), (),
                           ("bg2:mode2:target:9",), (32,)):
            patch_dialogue_case(s, f"{name} target {target_ids}", original, [dict(spec, target_ids=target_ids)])
    amcler = mode2_rewards.resource_specs("dlg:amcler01")
    amcler_original = mode2_tests.dialogue_fixture(amcler)
    for label, refs in (("missing display", {74025: 5, 1: 2}), ("two refs", {74025: 5}), ("empty", {})):
        patch_dialogue_case(s, f"amcler {label}", amcler_original, amcler, refs)
    other = mode2_tests.dialogue_fixture(mode2_rewards.resource_specs("dlg:uhogre01"))
    ogre = mode2_rewards.resource_specs("dlg:mgkhol01")
    patch_dialogue_case(s, "other dialogue", other, ogre)
    no_actions = quest_options._Dialogue(mode2_tests.dialogue_fixture(ogre), "mgkhol01")
    first = struct.unpack_from("<I", no_actions.states[4], 4)[0]
    struct.pack_into("<I", no_actions.transitions[first], 0, 3)
    patch_dialogue_case(s, "lost actions", no_actions.render(), ogre)
    duel = quest_options._Dialogue(mode2_tests.dialogue_fixture(mode2_rewards.resource_specs("dlg:udlesa")), "udlesa")
    for state in range(6, 12):
        action_id = struct.unpack_from("<I", mode2_tests.outgoing(duel, state)[0], 16)[0]
        following = (f'SetGlobal("WaitingToDuel","GLOBAL",2)\r\nSetGlobal("DuelStarts","AR2202",3)\r\n'
                     f'ActionOverride(Player{state - 5},JumpToPoint([608.1123]))\r\n').encode()
        duel.actions[action_id] = duel.actions[action_id].replace(b"Wait(1)", following + b"Wait(1)")
    patch_dialogue_case(s, "udlesa duel", duel.render(), mode2_rewards.resource_specs("dlg:udlesa"))
    spec = mode2_rewards.transfer_spec("scyarryl")
    patch_dialogue_case(s, "sculptor", mode2_tests.dialogue_fixture(spec), mode2_rewards.resource_specs("dlg:scyarryl"))


def text_route_cases(s: Scenario, originals: dict, allocated: dict) -> None:
    """test_text_variants_preserve_original_routes_replies_and_verified_recovery."""
    for resource, refs in allocated.items():
        if not resource.startswith("dlg:"):
            continue
        original = originals[resource]
        result = mode2_rewards.patch_reward(resource, original, refs=refs)
        text_original_case(s, f"result {resource}", resource, result)
        text_original_case(s, f"original {resource}", resource, original)
        moved = {old: new + 1000 for old, new in refs.items()}
        revised = patched(s, f"moved {resource}", resource, result, refs=moved)
        text_original_case(s, f"moved {resource}", resource, revised)
        bad_refs = [("extra", {**refs, 1: 2}), ("sentinel", {key: 0xFFFFFFFF for key in refs}),
                    ("negative", {key: -1 for key in refs}), ("empty", {})]
        if len(refs) > 1:
            bad_refs.append(("missing", dict(list(refs.items())[1:])))
        for label, bad in bad_refs:
            patched(s, f"refs {label} {resource}", resource, original, refs=bad)
        if mode2_rewards.resource_specs(resource)[0].get("inventory_target"):
            legacy = mode2_rewards._patch_text_dialogue(original, resource, refs, numeric_target=False)
            reward_checks(s, f"legacy {resource}", resource, legacy)
            text_original_case(s, f"legacy {resource}", resource, legacy)
        for defer, numeric in ((True, True), (True, False), (False, True), (False, False)):
            args = {"data": original, "resource": resource, "refs": refs, "defer_departure": defer,
                    "numeric_target": numeric}
            s.case(f"patch_text_dialogue {resource} {defer} {numeric}", "mode2_rewards._patch_text_dialogue", args,
                   lambda args=args: mode2_rewards._patch_text_dialogue(
                       args["data"], args["resource"], args["refs"], defer_departure=args["defer_departure"],
                       numeric_target=args["numeric_target"]))
        s.case(f"patch_text_dialogue unallocated {resource}", "mode2_rewards._patch_text_dialogue",
               {"data": original, "resource": resource, "refs": None, "defer_departure": True, "numeric_target": True},
               lambda original=original, resource=resource: mode2_rewards._patch_text_dialogue(original, resource, None))


def ddwell_cases(s: Scenario, ddwell: bytes, result: bytes) -> None:
    """test_well_uses_appended_original_text_only_for_selected_source_and_reexports."""
    for label, data, refs, ddwell_ref in (
            ("ref", ddwell, None, 7), ("refs", ddwell, {40850: 8}, None), ("refs override", ddwell, {40850: 8}, 9),
            ("bad refs", ddwell, {40850: 8, 1: 1}, None), ("other refs", ddwell, {40849: 8}, None),
            ("empty refs", ddwell, {}, None), ("unallocated", ddwell, None, None),
            ("sentinel", ddwell, None, 0xFFFFFFFF), ("negative", ddwell, None, -2),
            ("moved", result, None, 100002), ("moved refs", result, {40850: 100003}, None),
            ("gone", result.replace(b"AR1202DragonWell", b"AR1202DragonGone", 1), None, None),
            ("crlf", crlf(ddwell), None, 3)):
        patched(s, f"ddwell {label}", "BCS:DDWELL", data, refs=refs, ddwell_ref=ddwell_ref)
    asysta = mode2_tests.script_fixture("asysta01")
    for label, data in (("result", result), ("original", ddwell), ("short", result[:40]), ("crlf", crlf(result)),
                        ("gone", result.replace(b"AR1202DragonWell", b"AR1202DragonGone", 1))):
        script_original_case(s, f"ddwell {label}", "bcs:ddwell", data)
    asysta_result = mode2_rewards.patch_reward("bcs:asysta01", asysta)
    for label, data in (("result", asysta_result), ("original", asysta)):
        script_original_case(s, f"asysta01 {label}", "bcs:asysta01", data)
    for name, data in (("ddwell", ddwell), ("asysta01", asysta)):
        spec = mode2_rewards.resource_specs("bcs:" + name)[0]
        for text_ref in (None, 0, 5, -1, 0xFFFFFFFF, 0xFFFFFFFE):
            s.case(f"patch_script {name} {text_ref}", "mode2_rewards._patch_script",
                   {"data": data, "spec": spec, "text_ref": text_ref},
                   lambda data=data, spec=spec, text_ref=text_ref: mode2_rewards._patch_script(data, spec, text_ref))


def start_order_cases(s: Scenario) -> None:
    """test_a_cloned_start_state_keeps_its_place_among_the_start_conditions."""
    d = quest_options._Dialogue(mode2_tests.dialogue_fixture(mode2_rewards.resource_specs("dlg:ppdjinn")), "ppdjinn")
    struct.pack_into("<I", d.states[0], 0, 48995)
    d.state_triggers += [b'NumTimesTalkedTo(0)\r\n', b'Global("OtherModDjinn","GLOBAL",1)\r\n']
    for label, entries in (("first", (0, 1)), ("last", (1, 0))):
        struct.pack_into("<I", d.states[0], 12, entries[0])
        struct.pack_into("<I", d.states[1], 12, entries[1])
        original = d.render()
        result = patched(s, f"start order {label}", "dlg:ppdjinn", original, refs={48995: 100001})
        text_original_case(s, f"start order {label}", "dlg:ppdjinn", result)
        reward_checks(s, f"start order {label}", "dlg:ppdjinn", result)
        reward_checks(s, f"start order original {label}", "dlg:ppdjinn", original)
    # A start condition shared by the reward state and another state, and a
    # state without a condition before the reward state.
    struct.pack_into("<I", d.states[0], 12, 1)
    struct.pack_into("<I", d.states[1], 12, 1)
    patched(s, "start order shared", "dlg:ppdjinn", d.render(), refs={48995: 100001})
    struct.pack_into("<I", d.states[0], 12, 0)
    struct.pack_into("<I", d.states[1], 12, 0xFFFFFFFF)
    patched(s, "start order unconditioned", "dlg:ppdjinn", d.render(), refs={48995: 100001})


def journal_cases(s: Scenario) -> None:
    """test_bg1_texts_take_the_games_numbers_and_a_journal_entry_keeps_its_title and
    test_journal_lua_files_the_new_entry_under_the_quests_of_the_one_it_replaces."""
    games = (None, "bgee", "eet", "bg2ee", "bgt")
    for resource in ("dlg:aldeth", "dlg:jugjer01", "dlg:uhmay01", "bcs:ddwell", "dlg:varci", "dlg:nothing"):
        for game in games:
            args = {"resource": resource, **({} if game is None else {"game": game})}
            s.case(f"text_refs {resource} {game}", "mode2_rewards.text_refs", args,
                   lambda resource=resource, game=game: sorted_ints(mode2_rewards.text_refs(resource, game)))
            s.case(f"journal_refs {resource} {game}", "mode2_rewards.journal_refs", args,
                   lambda resource=resource, game=game: mode2_rewards.journal_refs(resource, game))
    for label, original in (
            ("lf", "The Merchant League\nOld words."), ("crlf twice", "Liga Kupiecka\r\n\r\nStare."),
            ("one line", "One line only"), ("blank rest", "Title\n \n"), ("no title first", "\nNo title first"),
            ("empty", ""), ("none", None), ("lone cr", "Title\rBody"), ("cr lf lf", "Title\r\n\nBody"),
            ("lf cr cr lf", "Title\n\r\r\nBody"), ("spaced title", "  Title  \n  Body"),
            ("blank title", " \t\nBody"), ("separator rest", "Title\n\x1c\x1f"), ("separator title", "\x1d\nBody"),
            ("nel", "Title\x85Body\nMore"), ("unicode", "Księga　\nTreść"), ("ideographic rest", "Title\n　"),
            ("form feed", "Title\x0c\nBody"), ("three lines", "Title\nOld\nWords")):
        s.case(f"_titled {label}", "mode2_rewards._titled", {"original": original, "body": "New words."},
               lambda original=original: mode2_rewards._titled(original, "New words."))
    lua = (b"createQuest( 87 )\r\n"
           b"createEntry    ( 87, -1, 26822, {}, nil )\r\n"
           b"createEntry    ( 87, -1, 26828, {}, nil )\r\n"
           b"createEntry    ( 87, -1, 126828, {}, nil )\r\n"
           b"\tcreateEntry(90,-1,26828,{1,2},nil)\r\n"
           b"createEntry    ( 91, -1, 268280, {}, nil )\r\n")
    aldeth = {"dlg:aldeth": {5740: 300000, 26828: 300001}}
    eet = {"dlg:aldeth": {205740: 400000, 226828: 400001}}
    listed = mode2_rewards.journal_lua(lua, aldeth)
    for label, data, texts, game in (
            ("listed", lua, aldeth, None), ("again", listed, aldeth, None), ("bgee", lua, aldeth, "bgee"),
            ("unlisted", b"createEntry    ( 87, -1, 26822, {}, nil )\n", aldeth, None),
            ("other route", lua, {"dlg:jugjer01": {33760: 300002, 33777: 300003}}, None),
            ("eet", b"createEntry    ( 87, -1, 226828, {}, nil )\n", eet, "eet"),
            ("eet numbers on bgee", b"createEntry    ( 87, -1, 226828, {}, nil )\n", eet, "bgee"),
            ("lf", lua.replace(b"\r\n", b"\n"), aldeth, None), ("lone cr", lua.replace(b"\r\n", b"\r"), aldeth, None),
            ("no line end", b"createEntry(87,-1,26828,{},nil)", aldeth, None),
            ("cr end", b"createEntry(87,-1,26828,{},nil)\r", aldeth, None),
            ("first line", b"createEntry(87,-1,26828,{},nil)\nx\n", aldeth, None),
            ("comment", b"-- createEntry(87,-1,26828,{},nil)\n", aldeth, None),
            ("no quest", b"createEntry(,-1,26828,{},nil)\n", aldeth, None),
            ("named quest", b"createEntry(q,-1,26828,{},nil)\n", aldeth, None),
            ("third argument", b"createEntry(87,26828,-1,{},nil)\n", aldeth, None),
            ("no rest", b"createEntry(87,-1,26828)\n", aldeth, None),
            ("other case", b"CreateEntry(87,-1,26828,{},nil)\n", aldeth, None),
            ("spaced", b"  createEntry \t( \t87 \t,\t-1 ,\t 26828 \t, {}, nil )   \r\n", aldeth, None),
            ("two quests", b"createEntry(87,-1,26828,{},nil)\ncreateEntry(88,-1,26828,{},nil)\n"
                           b"createEntry(88,-1,300001,{},nil)\n", aldeth, None),
            ("zero quest", b"createEntry(087,-1,26828,{},nil)\ncreateEntry(87,-1,300001,{},nil)\n", aldeth, None),
            ("missing text", lua, {"dlg:aldeth": {5740: 300000}}, None),
            ("unknown resource", lua, {"dlg:nothing": {26828: 300001}}, None),
            ("empty", b"", aldeth, None), ("no texts", lua, {}, None)):
        args = {"lua": data, "texts": texts, **({} if game is None else {"game": game})}
        s.case(f"journal_lua {label}", "mode2_rewards.journal_lua", args,
               lambda data=data, texts=texts, game=game: mode2_rewards.journal_lua(data, texts, game))


def mode2_scenario() -> Scenario:
    originals = mode2_inputs()
    s = Scenario("mode2_rewards")
    registry_cases(s)
    text_resources = [resource for resource in mode2_rewards.TEXT_REFS if resource.startswith("dlg:")]
    base_tlk = make_tlk(["Unchanged text"])
    text, allocated = mode2_rewards.prepare_reward_texts(GameText(base_tlk), text_resources)
    both = GameText(make_tlk(["a"]), make_tlk(["a (f)"]))
    polish, _ = mode2_rewards.prepare_reward_texts(both, text_resources + ["bcs:ddwell"], "pl_PL")
    every = list(mode2_rewards.RESOURCE_REWARDS) + ["DLG:PPDJINN"]
    for label, resources, language, source in (
            ("dialogues", text_resources, "en_us", GameText(base_tlk)), ("dialogues again", text_resources, "EN_US", text),
            ("all", every, "en_US", GameText(make_tlk([]))),
            ("none", ["dlg:uhogre01"], "en_us", GameText(base_tlk)), ("empty", [], "en_us", GameText(base_tlk)),
            ("german", text_resources, "de_DE", GameText(base_tlk)),
            ("german both", every, "de_DE", both), ("polish both", text_resources + ["bcs:ddwell"], "pl_PL", both),
            ("polish both again", text_resources + ["bcs:ddwell"], "PL_pl", polish),
            ("polish male only", every, "pl_PL", GameText(base_tlk)),
            ("ppdjinn polish", ["dlg:ppdjinn"], "pl_PL", GameText(make_tlk([]), make_tlk([]))),
            ("spanish", text_resources, "es_ES", GameText(base_tlk)), ("spanish none", [], "es_ES", both),
            ("default", text_resources, "default", both), ("unknown", text_resources, "nb_no", both),
            ("english word", text_resources, "english", GameText(base_tlk)),
            ("female count", text_resources, "pl_PL", GameText(make_tlk(["a"]), make_tlk(["a (f)", "b (f)"]))),
            ("bad female", text_resources, "pl_PL", GameText(make_tlk(["a"]), b"bad")),
            ("bad tlk", text_resources, "en_us", GameText(b"TLK V1  ")), ("bad tlk none", [], "en_us", GameText(b"bad"))):
        s.case(f"prepare_reward_texts {label}", "mode2_rewards.prepare_reward_texts",
               {"text": source, "resources": resources, "language": language},
               lambda resources=resources, language=language, source=source: (
                   lambda result: (result[0], {key: sorted_ints(value) for key, value in result[1].items()}))(
                   mode2_rewards.prepare_reward_texts(source, resources, language)))
    for resource in sorted(mode2_rewards.TEXT_REFS) + ["dlg:uhogre01"]:
        s.case(f"text_lines {resource}", "mode2_rewards.text_lines", {"resource": resource},
               lambda resource=resource: mode2_rewards.text_lines(resource))
    journal_cases(s)
    ddwell_tlk = make_tlk(["Existing unrelated text", "Existing helmet narrative"])
    updated, ref = mode2_rewards.prepare_ddwell_text(GameText(ddwell_tlk))
    for label, source, language in (("fresh", GameText(ddwell_tlk), "en_us"), ("again", updated, "en_us"),
                                    ("german", GameText(ddwell_tlk), "de_de"),
                                    ("polish both", GameText(ddwell_tlk, make_tlk(["x (f)", "y (f)"])), "pl_pl"),
                                    ("spanish", GameText(ddwell_tlk), "es_es")):
        s.case(f"prepare_ddwell_text {label}", "mode2_rewards.prepare_ddwell_text",
               {"text": source, "language": language},
               lambda source=source, language=language: mode2_rewards.prepare_ddwell_text(source, language))
    results = {}
    for resource, data in originals.items():
        text_dialogue = resource.startswith("dlg:") and resource in mode2_rewards.TEXT_REFS
        result = patched(s, resource, resource, data, refs=allocated[resource] if text_dialogue else None,
                         ddwell_ref=None if text_dialogue else 100001)
        results[resource] = result
        reward_checks(s, f"original {resource}", resource, data)
        reward_checks(s, f"result {resource}", resource, result)
        for label, value in (("tampered", result.replace(b"FLPR_", b"OTHER", 1)), ("appended", result + b"tampered"),
                             ("damaged", tampered(result, 100)),
                             ("trailer", result[:-20] + bytes([result[-20] ^ 1]) + result[-19:])):
            reward_checks(s, f"{label} {resource}", resource, value)
    patch_dialogue_cases(s, allocated)
    text_route_cases(s, originals, allocated)
    ddwell_cases(s, originals["bcs:ddwell"], results["bcs:ddwell"])
    book_cases(s)
    treasury_cases(s)
    tolgerias_cases(s)
    prince_cases(s)
    adalon_cases(s)
    reason_cases(s)
    layout_cases(s)
    aran_cases(s)
    start_order_cases(s)
    bg1_route_cases(s)
    for resource in ("uhogre01.dlg", "UHOGRE01", "override/uhogre01.dlg", "dlg:aran.dlg", "x:y:ddwell", "nothing",
                     "DLG:UHOGRE01", "bcs:uhogre01", "dlg:", ""):
        data = originals["dlg:uhogre01"] if "ogre" in resource or "aran" in resource else originals["bcs:ddwell"]
        reward_checks(s, f"key {resource}", resource, data)
    # Tolgerias's dialogue and script share a name: a file names its kind.
    for resource in ("TOLGER.DLG", "tolger.bcs", "DLG:TOLGER", "tolger", "itm:tolger", "tolger.cre", "tolger.dlg.bak",
                     "x:dlg:tolger", "bcs:tolger.dlg"):
        data = originals["bcs:tolger"] if "bcs" in resource.lower() else originals["dlg:tolger"]
        reward_checks(s, f"key {resource}", resource, data)
    return s


def mode2_trgeni_scenario() -> Scenario:
    """test_trgeni_terminal_departure_waits_for_transfer_and_upgrades_previous_patch."""
    original = trgeni_terminal()
    s = Scenario("mode2_rewards_trgeni")
    resource, refs = "dlg:trgeni01", {43514: 100001}
    legacy = mode2_rewards._patch_text_dialogue(original, resource, refs, defer_departure=False,
                                                numeric_target=False)
    previous = mode2_rewards._patch_text_dialogue(original, resource, refs, numeric_target=False)
    result = mode2_rewards.patch_reward(resource, legacy)
    for label, data in (("legacy", legacy), ("previous", previous), ("result", result), ("original", original),
                        ("tampered legacy", legacy.replace(b"FLPR_Transfer(", b"FLPR_Tampered(", 1))):
        reward_checks(s, label, resource, data)
        text_original_case(s, label, resource, data)
    patched(s, "original", resource, original, refs=refs)
    for defer, numeric in ((True, True), (False, False), (True, False), (False, True)):
        args = {"data": original, "resource": resource, "refs": refs, "defer_departure": defer,
                "numeric_target": numeric}
        s.case(f"patch_text_dialogue {defer} {numeric}", "mode2_rewards._patch_text_dialogue", args,
               lambda args=args: mode2_rewards._patch_text_dialogue(
                   original, resource, refs, defer_departure=args["defer_departure"],
                   numeric_target=args["numeric_target"]))
    spec = mode2_rewards.resource_specs(resource)[0]
    patch_dialogue_case(s, "terminal", original, [spec])
    # A terminal state with two bare departures, and one without any.
    for label, replacement in (("double", b"DestroySelf()\r\r\nDestroySelf()"), ("none", b"Wait(2)")):
        dialogue = quest_options._Dialogue(original, "trgeni01")
        action_id = struct.unpack_from("<I", mode2_tests.outgoing(dialogue, 37)[0], 16)[0]
        actions = dialogue.actions[action_id]
        index = actions.rindex(b"DestroySelf()")
        dialogue.actions[action_id] = actions[:index] + replacement + actions[index + len(b"DestroySelf()"):]
        patch_dialogue_case(s, f"departure {label}", dialogue.render(), [spec])
    # The departure that waits is the speaker's own, after the replies that
    # hand the reward over; an unreachable DestroySelf stays instant.
    unreached = quest_options._Dialogue(original, "trgeni01")
    struct.pack_into("<I", mode2_tests.outgoing(unreached, 36)[0], 28, 2)
    patch_dialogue_case(s, "unreached", unreached.render(), [spec])
    return s


# ---------------------------------------------------------------- containers


def container_inputs(line_endings: str) -> dict:
    originals = {}
    for resource in container_tests.FIXTURE_SITES:
        if resource.startswith("bcs:"):
            originals[resource] = container_tests.script_fixture(resource)
            if line_endings == "crlf":
                originals[resource] = crlf(originals[resource])
        elif line_endings == "lf":
            originals[resource] = container_tests.dialogue_fixture(resource)
    return originals


def container_patched(s: Scenario, label: str, resource: str, data: bytes, refs=None):
    s.case(f"patch_container_resource {label}", "container_rewards.patch_container_resource",
           {"resource": resource, "data": data, "refs": refs},
           lambda: container_rewards.patch_container_resource(resource, data, refs=refs))
    try:
        return container_rewards.patch_container_resource(resource, data, refs=refs)
    except ProfileError:
        return None


def container_checks(s: Scenario, label: str, resource: str, data: bytes) -> None:
    """What the check and Apply read from these bytes: own patch, reasons, recovery, re-export."""
    args = {"resource": resource, "data": data}
    s.case(f"own_patch {label}", "container_rewards.own_patch", args,
           lambda: container_rewards.own_patch(resource, data))
    s.case(f"route_reasons {label}", "container_rewards.route_reasons", args,
           lambda: container_rewards.route_reasons(resource, data))
    if resource in container_rewards.RESOURCE_TARGETS:
        s.case(f"recover {label}", "container_rewards._recover", args,
               lambda: container_rewards._recover(resource, data))
    container_patched(s, f"again {label}", resource, data)


def chest(area: str, index, item: str) -> dict:
    return {"kind": "container", "resource": f"are:{area}", "item": item,
            "id": f"are:{area}/container/{index}/item/0", "record": {"container_index": index}}


def treasure_cases(s: Scenario) -> None:
    """test_exact_target_selectors_shared_samia_latch_and_golem_text and
    test_the_quest_container_is_the_one_that_holds_the_treasure."""
    for identity in list(container_rewards.TARGETS) + ["bg2:mode2:target:999", "bg2:mode2:target:1"]:
        s.case(f"target_spec {identity}", "container_rewards.target_spec", {"target_id": identity},
               lambda identity=identity: container_rewards.target_spec(identity))
        s.case(f"treasure {identity}", "container_rewards.treasure", {"target_id": identity},
               lambda identity=identity: container_rewards.treasure(identity))
        for platform in ("eet", "bgee", "bg2ee", "iwdee"):
            s.case(f"treasure_area {identity} {platform}", "container_rewards.treasure_area",
                   {"target_id": identity, "platform": platform},
                   lambda identity=identity, platform=platform: container_rewards.treasure_area(identity, platform))
        for problem in ("the chest moved", ""):
            s.case(f"container_error {identity} {problem!r}", "container_rewards.container_error",
                   {"target_id": identity, "problem": problem},
                   lambda identity=identity, problem=problem: str(container_rewards.container_error(identity, problem)))
    samia = "bg2:mode2:target:28"
    sources = [chest("ar1202", 8, "misc01"), chest("ar1202", 9, "sw1h32"), chest("ar1202", 9, "shld21"),
               chest("ar1201", 0, "sw1h32"),
               {"kind": "cre-template", "resource": "cre:x", "item": "shld21", "record": {}}]
    moved = [dict(row, record={"container_index": row["record"].get("container_index", 0) + 1})
             if row["kind"] == "container" else row for row in sources]
    golem = [chest("ar1302", 0, "ax1h13"), chest("ar1303", 2, "ax1h13")]
    safana = "bg1:mode2:target:22"
    cases = (
        ("samia", samia, sources, "bg2ee"), ("samia eet", "bg2:mode2:target:53", sources, "eet"),
        ("samia moved", samia, moved, "bg2ee"), ("samia one item", samia, sources[:2], "bg2ee"),
        ("samia two chests", samia, sources + [chest("ar1202", 3, "sw1h32"), chest("ar1202", 3, "shld21")], "bg2ee"),
        ("golem", "bg2:mode2:target:13", golem, "bg2ee"),
        ("safana eet", safana, [chest("bg3601", 0, "book03")], "eet"),
        ("safana bgee", safana, [chest("ar3601", 0, "book03")], "bgee"),
        ("safana eet bg1 name", safana, [chest("ar3601", 0, "book03")], "eet"),
        ("aliased", "bg2:mode2:target:61",
         [{"kind": "container", "resource": "are:ar1514", "item": "bow19b", "record": {"container_index": None}}], "bg2ee"),
        ("no record", "bg2:mode2:target:61",
         [{"kind": "container", "resource": "are:ar1514", "item": "bow19b", "record": None},
          {"kind": "container", "resource": "are:ar1514", "item": "bow19b"}], "bg2ee"),
        ("string index", "bg2:mode2:target:61", [chest("ar1514", "4", "bow19b")], "bg2ee"),
        ("upper item", "bg2:mode2:target:61", [chest("ar1514", 0, "BOW19B")], "bg2ee"),
        ("empty", samia, [], "bg2ee"),
        ("missing kind", samia, [{"resource": "are:ar1202", "item": "sw1h32", "record": {"container_index": 9}}],
         "bg2ee"),
        ("unknown target", "bg2:mode2:target:1", sources, "bg2ee"))
    for label, identity, rows, platform in cases:
        s.case(f"treasure_container {label}", "container_rewards.treasure_container",
               {"target_id": identity, "sources": rows, "platform": platform},
               lambda identity=identity, rows=rows, platform=platform:
               container_rewards.treasure_container(identity, rows, platform))


def container_layout_cases(s: Scenario) -> None:
    """test_script_sites_are_found_by_meaning_in_another_layout and
    test_dialogue_conditions_are_found_by_meaning_in_another_layout."""
    trigger, action, block = container_rewards._trigger, container_tests.action, container_tests.script_block
    added = block(trigger(16399, 1, text="GLOBALOtherModGolems"), action(30, 2, "GLOBALOtherMod"))
    golem = block(trigger(16450, text="Ax1h13"), action(151, 38758) + action(10))
    samia = block(trigger(16521, 2) + trigger(16450, text="SHLD21") + trigger(16450, text="sw1h32"), action(10))
    for label, resource, original in (
            ("kpgol01 crlf", "bcs:kpgol01", crlf(b"SC\n" + added + added + golem + b"SC\n")),
            ("kpgol01 lf", "bcs:kpgol01", b"SC\n" + golem + added + b"SC\n"),
            ("samia crlf", "bcs:samia", crlf(b"SC\n" + samia + b"SC\n"))):
        result = container_patched(s, f"layout {label}", resource, original, refs={38758: 100001})
        container_checks(s, f"layout original {label}", resource, original)
        container_checks(s, f"layout result {label}", resource, result)
    conditions = {0: b'!PartyHasItem("book03")\nGlobal("Unrelated","GLOBAL",4)',
                  2: b'Global("Unrelated","GLOBAL",4)\nPartyHasItem("Book03")',
                  8: b'Global("Unrelated","GLOBAL",4)\n  !PartyHasItem("BOOK03")'}
    dialogue = quest_options._Dialogue(container_tests.dialogue_fixture("dlg:safanj", conditions), "safanj")
    dialogue.triggers.append(b'PartyHasItem("book03")\nGlobal("Unrelated","GLOBAL",4)')
    reply = dialogue.transition(None, action='SetGlobal("OtherMod","GLOBAL",2)')
    struct.pack_into("<I", reply, 0, struct.unpack_from("<I", reply)[0] | 2)
    struct.pack_into("<I", reply, 12, len(dialogue.triggers) - 1)
    dialogue.state(1235, [reply])
    original = dialogue.render()
    result = container_patched(s, "layout safanj", "dlg:safanj", original)
    container_checks(s, "layout original safanj", "dlg:safanj", original)
    container_checks(s, "layout result safanj", "dlg:safanj", result)


def container_reason_cases(s: Scenario) -> None:
    """test_routes_the_check_cannot_recognise_name_their_reason, and more conditions to judge."""
    trigger, action, block = container_rewards._trigger, container_tests.action, container_tests.script_block

    def blocks(*made):
        return b"SC\n" + b"".join(made) + b"SC\n"
    golem = action(151, 38758) + action(10)
    cases = (
        ("bcs:kpgol01", "no block", blocks(block(b"", golem))),
        ("bcs:kpgol01", "or", blocks(block(trigger(16521, 2) + trigger(16450, text="ax1h13") +
                                           trigger(16399, 1, text="GLOBALOther"), golem))),
        ("bcs:kpgol01", "no text", blocks(block(trigger(16450, text="ax1h13"), action(10)))),
        ("bcs:kpgol01", "text twice", blocks(block(trigger(16450, text="ax1h13"), action(151, 38758) + golem))),
        ("bcs:ppchest", "no latch", blocks(block(trigger(16501, negate=1, text="bow19b", selector=1), action(10)))),
        ("bcs:samia", "or other", blocks(block(trigger(16521, 2) + trigger(16450, text="shld21") +
                                               trigger(16399, 1, text="GLOBALOther"), action(10)))),
        ("bcs:kpgol01", "open", b"SC\nCR\nCO\nSC\n"),
        ("dlg:samia", "no condition", container_tests.dialogue_fixture("dlg:samia", {1: b'Global("Unrelated","GLOBAL",4)\r\n'})),
        ("dlg:safanj", "has item", container_tests.dialogue_fixture("dlg:safanj", {2: b'HasItem("book03",Myself)\r\n'})),
        ("dlg:safanj", "or", container_tests.dialogue_fixture(
            "dlg:safanj", {2: b'OR(2)\r\nPartyHasItem("book03")\r\nGlobal("X","GLOBAL",1)\r\n'})),
        ("dlg:safanj", "broken", b"DLG V1.0" + bytes(10)),
        # More layouts another mod could leave.
        ("bcs:kpgol01", "two blocks", blocks(block(trigger(16450, text="ax1h13"), golem),
                                             block(trigger(16450, text="ax1h13"), golem))),
        ("bcs:kpgol01", "negated", blocks(block(trigger(16450, negate=1, text="ax1h13"), golem))),
        ("bcs:ppchest", "other selector", blocks(block(trigger(16501, negate=1, text="bow19b") +
                                                       trigger(16399, text="GLOBALRobbedGolem"), action(10)))),
        ("bcs:samia", "or three", blocks(block(trigger(16521, 3) + trigger(16450, text="shld21") +
                                               trigger(16450, text="sw1h32"), action(10)))),
        ("bcs:samia", "one item", blocks(block(trigger(16450, text="shld21"), action(10)))),
        ("bcs:safana", "crlf", crlf(container_tests.script_fixture("bcs:safana"))),
        ("bcs:kpgol01", "empty", b""),
        ("dlg:safanj", "twice", container_tests.dialogue_fixture(
            "dlg:safanj", {2: b'PartyHasItem("book03")\r\nPartyHasItem("BOOK03")\r\n'})),
        ("dlg:samia", "or three", container_tests.dialogue_fixture(
            "dlg:samia", {1: b'OR(3)\r\nPartyHasItem("sw1h32")\r\nPartyHasItem("shld21")\r\nTrue()\r\n'})),
        ("dlg:safanj", "lf", container_tests.dialogue_fixture("dlg:safanj", {2: b'PartyHasItem("book03")'})),
        ("dlg:safanj", "spaced", container_tests.dialogue_fixture("dlg:safanj", {2: b'  ! PartyHasItem( "book03" )  \r\n'})),
        # test_routes_the_check_cannot_recognise_name_their_reason (0eb5619).
        ("bcs:kpgol01", "has item", blocks(block(trigger(16481, text="ax1h13", selector=2), golem))),
        ("bcs:kpgol01", "or count", blocks(block(trigger(16521, 3) + trigger(16450, text="ax1h13") +
                                                 trigger(16399, 1, text="GLOBALOther"), golem))),
        ("bcs:kpgol01", "nested or", blocks(block(trigger(16521, 2) + trigger(16521, 1) + trigger(16450, text="ax1h13") +
                                                  trigger(16399, 1, text="GLOBALOther"), golem))),
        ("bcs:samia", "separate", blocks(block(trigger(16450, text="shld21") + trigger(16450, text="sw1h32"), action(10)))),
        ("bcs:samia", "lacks one", blocks(block(trigger(16521, 2) + trigger(16450, negate=1, text="shld21") +
                                                trigger(16450, text="sw1h32"), action(10)))),
        ("dlg:samia", "separate", container_tests.dialogue_fixture(
            "dlg:samia", {1: b'PartyHasItem("shld21")\r\nPartyHasItem("sw1h32")\r\n'})),
        ("dlg:safanj", "object", container_tests.dialogue_fixture("dlg:safanj", {2: b'PartyHasItem("book03",Myself)\r\n'})),
        ("dlg:safanj", "or three", container_tests.dialogue_fixture(
            "dlg:safanj", {2: b'OR(3)\r\nPartyHasItem("book03")\r\nGlobal("X","GLOBAL",1)\r\n'})),
        ("dlg:safanj", "or unreadable", container_tests.dialogue_fixture(
            "dlg:safanj", {2: b'OR(X)\r\nPartyHasItem("book03")\r\nGlobal("X","GLOBAL",1)\r\n'})),
        ("dlg:safanj", "contains", container_tests.dialogue_fixture("dlg:safanj", {2: b'Contains("book03",Myself)\r\n'})),
        ("dlg:safanj", "num items", container_tests.dialogue_fixture("dlg:safanj", {2: b'NumItemsParty("BOOK03",1)\r\n'})),
        ("bcs:kpgol01", "num items", blocks(block(trigger(16506, 1, text="ax1h13"), golem))),
        # test_treasure_tests_inside_or_groups_and_repeated_golem_texts.
        ("bcs:kpgol01", "or golem responses", b"SC\nCR\nCO\n" + trigger(16521, 2) + trigger(16450, text="ax1h13") +
         trigger(16399, 1, text="GLOBALOther") + b"CO\nRS\nRE\n50" + action(151, 38758) + action(10) +
         b"RE\nRE\n50" + action(151, 38758) + b"RE\nRS\nCR\nSC\n"),
        ("bcs:kpgol01", "or after negated", b"SC\nCR\nCO\n" + trigger(16450, negate=1, text="ax1h13") +
         trigger(16521, 2) + trigger(16399, 1, text="GLOBALOther") + trigger(16450, text="AX1H13") +
         b"CO\nRS\nRE\n100" + action(151, 38758) + b"RE\nRS\nCR\nSC\n"),
        ("bcs:samia", "or three other", b"SC\nCR\nCO\n" + trigger(16521, 3) + trigger(16450, text="shld21") +
         trigger(16399, 1, text="GLOBALOther") + trigger(16450, text="sw1h32") + b"CO\nRS\nRE\n100" + action(10) +
         b"RE\nRS\nCR\nSC\n"),
        ("dlg:safanj", "or other", container_tests.dialogue_fixture("dlg:safanj", {index: (
            b'OR(2)\r\nPartyHasItem("book03")\r\nGlobal("Other","GLOBAL",1)\r\n') for index in (0, 2, 8)})),
        ("dlg:safanj", "or after negated", container_tests.dialogue_fixture("dlg:safanj", {index: (
            b'!PartyHasItem("book03")\nOR(2)\nGlobal("Other","GLOBAL",1)\nPartyHasItem("BOOK03")') for index in (0, 2, 8)})),
        ("dlg:samia", "or three other", container_tests.dialogue_fixture("dlg:samia", {1: (
            b'OR(3)\r\nPartyHasItem("sw1h32")\r\nGlobal("Other","GLOBAL",1)\r\nPartyHasItem("shld21")\r\n')})))
    for resource, label, data in cases:
        container_checks(s, f"reasons {label} {resource}", resource, data)
        container_patched(s, f"reasons {label} {resource} with refs", resource, data, refs={38758: 100001})


def container_scenario(name: str, line_endings: str) -> Scenario:
    originals = container_inputs(line_endings)
    s = Scenario(name)
    if line_endings == "lf":
        s.case("TARGETS", "container_rewards.TARGETS", {}, lambda: container_rewards.TARGETS)
        s.case("RESOURCE_TARGETS", "container_rewards.RESOURCE_TARGETS", {}, lambda: container_rewards.RESOURCE_TARGETS)
        s.case("TEXT_RESOURCES", "container_rewards.TEXT_RESOURCES", {}, lambda: container_rewards.TEXT_RESOURCES)
        treasure_cases(s)
        base = GameText(make_tlk(["Preserve existing text"]))
        text, _ = container_rewards.prepare_container_texts(base, container_rewards.TEXT_RESOURCES)
        both = GameText(base.male, make_tlk(["Preserve existing text (f)"]))
        polish, _ = container_rewards.prepare_container_texts(both, container_rewards.TEXT_RESOURCES, "pl_PL")
        for label, source, resources, language in (
                ("all", base, sorted(container_rewards.TEXT_RESOURCES), "en_us"),
                ("again", text, ["bcs:kpgol05", "bcs:kpgol01"], "EN_US"),
                ("non-text", base, ["bcs:kpgol03", "dlg:samia"], "de_DE"),
                ("uppercase", base, ["BCS:KPGOL01"], "de_DE"), ("german", base, ["bcs:kpgol02"], "de_DE"),
                ("german both", both, ["bcs:kpgol02"], "de_DE"),
                ("polish both", both, sorted(container_rewards.TEXT_RESOURCES), "pl_PL"),
                ("polish both again", polish, ["bcs:kpgol01"], "pl_pl"),
                ("spanish", base, ["bcs:kpgol02"], "es_ES"), ("ar1202 spanish", base, ["bcs:ar1202"], "es_ES"),
                ("default", both, ["bcs:kpgol02"], "default"), ("unknown", both, ["bcs:kpgol02"], "xx"),
                ("ar1202 german", base, ["bcs:ar1202"], "de_DE"),
                ("female count", GameText(base.male, make_tlk([])), ["bcs:kpgol02"], "pl_PL"),
                ("bad tlk", GameText(b"no tlk"), ["bcs:kpgol02"], "en_us"),
                ("bad tlk none", GameText(b"no tlk"), [], "en_us")):
            s.case(f"prepare_container_texts {label}", "container_rewards.prepare_container_texts",
                   {"text": source, "resources": resources, "language": language},
                   lambda source=source, resources=resources, language=language:
                   container_rewards.prepare_container_texts(source, resources, language))
        for args in ({"opcode": 16450, "value": 0, "negate": 1, "text": "BOOK03", "selector": 0, "newline": b"\n"},
                     {"opcode": 16521, "value": 2, "negate": 0, "text": "", "selector": 3, "newline": b"\r\n"}):
            s.case(f"trigger {args['opcode']}", "container_rewards._trigger", args,
                   lambda args=args: container_rewards._trigger(**args))
        for resource in ("bcs:other", "BCS:KPGOL01", "kpgol01", "dlg:kpgol01"):
            container_checks(s, f"key {resource}", resource, originals["bcs:kpgol01"])
        container_layout_cases(s)
        container_reason_cases(s)
    for resource, original in originals.items():
        result = container_rewards.patch_container_resource(resource, original, refs={38758: 100001})
        cases = [("patch", original, {38758: 100001}), ("reexport", result, None), ("moved", result, {38758: 7}),
                 ("empty refs", result, {}), ("other refs", result, {1: 2}),
                 ("unallocated", original, None), ("sentinel", original, {38758: 0xFFFFFFFF}),
                 ("negative", original, {38758: -1}),
                 ("flag", result.replace(b"FLPR_C", b"FLPR_Z", 1), None), ("appended", result + b"tamper", None),
                 ("upper", original, {38758: 5})]
        for label, data, refs in cases:
            container_patched(s, f"{label} {resource}", resource.upper() if label == "upper" else resource, data, refs)
        if resource.startswith("bcs:"):
            flag = container_rewards.TARGETS[container_rewards.RESOURCE_TARGETS[resource][0]]["activation_global"]
            newline = quest_rewards._newline(result)
            changed = result.replace(quest_rewards._global_guard(flag, False, newline),
                                     quest_rewards._global_guard(flag, True, newline), 1)
        else:
            changed = result.replace(b"FLPR_C", b"FLPR_Z", 1)
        for label, data in (("result", result), ("original", original), ("appended", result + b"x"),
                            ("short", result[:30]), ("changed", changed), ("tamper", result + b"tamper"),
                            ("moved", container_rewards.patch_container_resource(resource, result, refs={38758: 7}))):
            container_checks(s, f"{label} {resource}", resource, data)
    s.case("patch_container_resource unknown", "container_rewards.patch_container_resource",
           {"resource": "bcs:nothing", "data": b"", "refs": None},
           lambda: container_rewards.patch_container_resource("bcs:nothing", b""))
    return s


# ---------------------------------------------------------------- copies of reward replies


def ordered(value):
    """Dicts with their keys sorted at every level, as the Rust maps keep them."""
    if isinstance(value, dict):
        return {key: ordered(value[key]) for key in sorted(value)}
    return value


def copies_case(s: Scenario, label: str, files: dict, scanned: dict | None = None, *, limit: int | None = None,
                routes=(("uhogre01",),), naming=None) -> None:
    """RewardCopies over these dialogues: what it found and the patches of some routes."""
    scanned = ({"dlg:" + name: sha(data) for name, data in files.items()} if scanned is None else scanned)
    naming = naming or {}

    def run():
        with ExitStack() as stack:
            if limit is not None:
                stack.enter_context(patch.object(reward_copies, "LIMIT", limit))
            found = reward_copies.RewardCopies(lambda name: files.get(name), scanned, **naming)
        return {"copies": ordered(found.copies), "reasons": ordered(found.reasons),
                "checked": {key: sorted(found.checked(key)) for key in sorted(found.visited)},
                "changed": sorted(found.changed), "originals": ordered(found.originals),
                "patches": [ordered(found.patches(list(keys))) for keys in routes]}
    s.case(f"copies {label}", "reward_copies.RewardCopies",
           {"files": files, "scanned": scanned, "limit": limit, "routes": [list(keys) for keys in routes],
            **naming_args(naming)}, run)


def copies_scenario() -> Scenario:
    """tests/test_reward_copies.py, and more dialogues an interjection can leave."""
    s = Scenario("reward_copies")
    interject, link, empty = copy_tests.interject, copy_tests.link, copy_tests.empty_dialogue
    ogre = mode2_tests.dialogue_fixture(mode2_rewards.resource_specs("dlg:uhogre01")[0])
    route, npc, state = interject(ogre, "uhogre01", 22, "xnpcj")
    copies_case(s, "interjection", {"uhogre01": route, "xnpcj": npc}, routes=(("uhogre01",), ("mgkhol01",), ()))
    s.case("copy_spec", "reward_copies.copy_spec", {"key": "uhogre01", "resource": "dlg:xnpcj"},
           lambda: reward_copies.copy_spec("uhogre01", "dlg:xnpcj"))
    trans2, trans2_npc, _ = interject(ogre, "uhogre01", 22, "xnpcj", copy_actions=False)
    copies_case(s, "trans2", {"uhogre01": trans2, "xnpcj": trans2_npc})
    chained, first, _ = interject(ogre, "uhogre01", 22, "xnpca")
    first, second, other = interject(first, "xnpca", state, "xnpcb")
    second = link(second, "xnpcb", other, "xnpca", state)
    copies_case(s, "chained", {"uhogre01": chained, "xnpca": first, "xnpcb": second})
    aran = mode2_tests.dialogue_fixture(mode2_rewards.resource_specs("dlg:aran"))
    aran_route, sarah, _ = interject(aran, "aran", 4, "k#sarahj")
    copies_case(s, "two routes", {"aran": aran_route, "k#sarahj": sarah},
                routes=(("aran", "aran2"), ("aran",), ("aran2",)))

    def changed_reply(data, name, at, change):
        dialogue = quest_options._Dialogue(data, name)
        index = struct.unpack_from("<I", mode2_tests.outgoing(dialogue, at)[0], 16)[0]
        dialogue.actions[index] = change(dialogue.actions[index])
        return dialogue.render()
    copies_case(s, "also gives", {"uhogre01": route, "xnpcj": changed_reply(
        npc, "xnpcj", state, lambda text: text.replace(b"Wait(1)", b"Wait(2)"))})
    copies_case(s, "takes and shows", {"uhogre01": route, "xnpcj": changed_reply(
        npc, "xnpcj", state, lambda text: b'TakePartyItem("shld26")\r\nDisplayStringHead(Myself,1)\r\n')})
    copies_case(s, "unreadable", {"uhogre01": route, "xnpcj": changed_reply(
        npc, "xnpcj", state, lambda text: b'GiveItemCreate("shld26",Player1,0,0,0')})
    copies_case(s, "through another actor", {"uhogre01": route, "xnpcj": changed_reply(
        npc, "xnpcj", state, lambda text: b'ActionOverride(Player2,GiveItemCreate("shld26",Myself,0,0,0))')})
    copies_case(s, "override unreadable", {"uhogre01": route, "xnpcj": changed_reply(
        npc, "xnpcj", state, lambda text: b'ActionOverride(Player2)\r\nTakePartyItem("SHLD26")')})
    for name in ("hlskull", "mgkhol01", "samia"):
        changed, other_npc, _ = interject(ogre, "uhogre01", 22, name)
        copies_case(s, f"cir changes {name}", {"uhogre01": changed, name: other_npc})
    # test_a_reply_elsewhere_that_ends_the_princes_job_leaves_his_place_out:
    # a reply of another dialogue that ends his job, changes it another way,
    # or names it unreadably.
    prince = mode2_tests.dialogue_fixture(mode2_rewards.resource_specs("dlg:sahpr2"))
    prince_route, prince_npc, prince_state = interject(prince, "sahpr2", 5, "xnpcj")
    copies_case(s, "prince", {"sahpr2": prince_route, "xnpcj": prince_npc},
                routes=(("sahpr2",), ("sahpr2", "sahpr2b"), ()))
    for label, actions in (("override", b'ActionOverride("sahpr2",SetGlobal("VILLYNATYJOB","GLOBAL",2))\r\n'),
                           ("other value", b'SetGlobal("villynatyjob","GLOBAL",3)\r\n'),
                           ("local", b'IncrementGlobal("villynatyjob","LOCALS",1)\r\n'),
                           ("unreadable", b'SetGlobal("villynatyjob","GLOBAL",2\r\n'),
                           ("incremented", b'IncrementGlobal("villynatyjob","GLOBAL",1)\r\n')):
        copies_case(s, f"prince {label}", {"sahpr2": prince_route, "xnpcj": changed_reply(
            prince_npc, "xnpcj", prince_state, lambda text, actions=actions: actions)}, routes=(("sahpr2",),))
    copies_case(s, "prince alone", {"sahpr2": prince}, routes=(("sahpr2",),))
    # test_a_reply_elsewhere_that_takes_adalons_eggs_leaves_her_place_out: a
    # reply of another dialogue that takes her eggs, or names them unreadably.
    silver = mode2_tests.dialogue_fixture(mode2_rewards.resource_specs("dlg:udsilver"))
    silver_route, silver_npc, silver_state = interject(silver, "udsilver", 41, "xnpcj")
    copies_case(s, "adalon", {"udsilver": silver_route, "xnpcj": silver_npc},
                routes=(("udsilver",), ("adalon",), ("udsilver", "adalon"), ()))
    for label, actions in (("override", b'ActionOverride("udsilver",TakePartyItem("MISC9T"))\r\n'),
                           ("replaced", b'TakeItemReplace("misc9u","misc9t",Player1)\r\n'),
                           ("given", b'GiveItemCreate("misc9t",Player1,0,0,0)\r\n'),
                           ("unreadable", b'TakePartyItem("misc9t"\r\n'),
                           ("all", b'TakePartyItemAll("Misc9T")\r\n'),
                           ("other item", b'TakePartyItem("misc9x")\r\nDisplayStringHead(Myself,1)\r\n')):
        copies_case(s, f"adalon {label}", {"udsilver": silver_route, "xnpcj": changed_reply(
            silver_npc, "xnpcj", silver_state, lambda text, actions=actions: actions)},
            routes=(("udsilver",), ("adalon",)))
    copies_case(s, "adalon alone", {"udsilver": silver}, routes=(("udsilver", "adalon"),))
    genie = mode2_rewards.resource_specs("dlg:trgeni01")[0]
    genie_route, genie_npc, _ = interject(mode2_tests.dialogue_fixture(genie), "trgeni01", 35, "xnpcj")
    copies_case(s, "carried", {"trgeni01": genie_route, "xnpcj": genie_npc}, routes=(("trgeni01",),))
    linked = link(ogre, "uhogre01", 3, "broken", 0)
    copies_case(s, "broken", {"uhogre01": linked, "broken": b"DLG V1.0" + bytes(10)})
    copies_case(s, "missing", {"uhogre01": linked})
    copies_case(s, "missing state", {"uhogre01": link(ogre, "uhogre01", 3, "xnpcj", 7), "xnpcj": npc})
    chain = quest_options._Dialogue(empty("xnpcj"), "xnpcj")
    for number in range(5):
        chain.state(1, [chain.transition(number + 1, reply=1)])
    chain_route = link(ogre, "uhogre01", 3, "xnpcj", 0)
    for limit in (3, 4, 5, 6):
        copies_case(s, f"limit {limit}", {"uhogre01": chain_route, "xnpcj": chain.render()}, limit=limit)
    # A copy dialogue whose tables point outside themselves.
    outside = quest_options._Dialogue(npc, "xnpcj")
    struct.pack_into("<I", outside.states[state], 8, 99)
    copies_case(s, "replies outside", {"uhogre01": route, "xnpcj": outside.render()})
    outside = quest_options._Dialogue(npc, "xnpcj")
    first_row = struct.unpack_from("<I", outside.states[state], 4)[0]
    struct.pack_into("<I", outside.transitions[first_row], 16, 99)
    copies_case(s, "actions outside", {"uhogre01": route, "xnpcj": outside.render()})
    # The link's name in another letter case, padded or with a Latin-1 byte.
    upper = quest_options._Dialogue(route, "uhogre01")
    for row in upper.transitions:
        if bytes(row[20:28]).rstrip(b"\0") == b"XNPCJ":
            row[20:28] = b"xNpCj\0\0\0"
    copies_case(s, "link case", {"uhogre01": upper.render(), "xnpcj": npc})
    latin = link(ogre, "uhogre01", 3, "x\xc9npc", 0)
    copies_case(s, "latin-1 missing", {"uhogre01": latin})
    copies_case(s, "latin-1", {"uhogre01": latin, "x\xe9npc": npc})
    # The link names the dialogue in UTF-8 bytes; read as Latin-1 they are
    # not ASCII, which the dialogue reader refuses.
    copies_case(s, "latin-1 present", {"uhogre01": latin, "x\xc3\x89npc".lower(): npc})
    temple = mode2_tests.dialogue_fixture(mode2_rewards.resource_specs("dlg:bhoisig"))
    temple_files = {"bhoisig": link(temple, "bhoisig", 0, "scyarryl", 19),
                    "scyarryl": mode2_tests.dialogue_fixture(mode2_rewards.resource_specs("dlg:scyarryl")),
                    "bhnalla": mode2_tests.dialogue_fixture(mode2_rewards.resource_specs("dlg:bhnalla")),
                    "bharval": mode2_tests.dialogue_fixture(mode2_rewards.resource_specs("dlg:bharval"))}
    copies_case(s, "temple", temple_files, routes=(("bhoisig",), ("bhoisig", "scyarryl")))
    # Our own patch, before and after a rescan; a changed one; other changes.
    before = {"uhogre01": route, "xnpcj": npc}
    found = reward_copies.RewardCopies(before.get, {})
    applied = {"uhogre01": mode2_rewards.patch_reward("dlg:uhogre01", route),
               "xnpcj": found.patches(["uhogre01"])["dlg:xnpcj"]}
    before_hashes = {"dlg:" + name: sha(data) for name, data in before.items()}
    copies_case(s, "applied", applied)
    copies_case(s, "applied before rescan", applied, before_hashes)
    copies_case(s, "tampered", dict(applied, xnpcj=applied["xnpcj"].replace(b"FLPR_", b"OTHER", 1)))
    copies_case(s, "tampered event", dict(applied, xnpcj=applied["xnpcj"].replace(b"FLPR_EM2_14", b"FLPR_EM2_99")))
    copies_case(s, "changed since scan", dict(before, xnpcj=npc + b"\0"), before_hashes)
    copies_case(s, "gone", {"uhogre01": route}, {"dlg:uhogre01": sha(route), "dlg:xnpcj": sha(npc)})
    copies_case(s, "unscanned", before, {})
    aran_found = reward_copies.RewardCopies({"aran": aran_route, "k#sarahj": sarah}.get, {})
    aran_applied = {"aran": aran_route, "k#sarahj": aran_found.patches(["aran", "aran2"])["dlg:k#sarahj"]}
    copies_case(s, "two routes applied", aran_applied, routes=(("aran", "aran2"), ("aran",)))
    one_applied = {"aran": aran_route, "k#sarahj": aran_found.patches(["aran"])["dlg:k#sarahj"]}
    copies_case(s, "one of two routes applied", one_applied, routes=(("aran", "aran2"), ("aran",)))
    # A BG1 route's copies, found as the game names its reward (EET's SHLD07A).
    eet = mode2_tests.EET
    varci = mode2_tests.dialogue_fixture(mode2_rewards.resource_specs("dlg:varci", **eet))
    varci_route, varci_npc, _ = interject(varci, "varci", 11, "xnpcj")
    for game, naming in NAMINGS.items():
        copies_case(s, f"bg1 {game}", {"varci": varci_route, "xnpcj": varci_npc}, routes=(("varci",),), naming=naming)
    copies_case(s, "bg1 unnamed", {"varci": varci_route, "xnpcj": varci_npc}, routes=(("varci",),))
    varci_found = reward_copies.RewardCopies({"varci": varci_route, "xnpcj": varci_npc}.get, {}, **eet)
    varci_applied = {"varci": mode2_rewards.patch_reward("dlg:varci", varci_route, **eet),
                     "xnpcj": varci_found.patches(["varci"])["dlg:xnpcj"]}
    copies_case(s, "bg1 applied", varci_applied, routes=(("varci",),), naming=eet)
    for key, game in (("varci", "eet"), ("varci", "bgt"), ("uhogre01", "eet"), ("x", "eet")):
        s.case(f"copy_spec {key} {game}", "reward_copies.copy_spec",
               {"key": key, "resource": "dlg:xnpcj", **naming_args(NAMINGS[game])},
               lambda key=key, game=game: reward_copies.copy_spec(key, "dlg:xnpcj", **NAMINGS[game]))
    return s


# ---------------------------------------------------------------- inventories and options


def inventory_scenarios() -> list[Scenario]:
    store = Scenario("store_inventory")
    rows = [(item_record("FIRST", charges=(1, 2, 3)), 2, 0), (item_record("REMOVE", charges=(4, 5, 6)), 1, 0),
            (item_record("THIRD", charges=(7, 8, 9)), 5, 0), (item_record("ENDLESS"), 3, 1),
            (item_record("EMPTY"), 0, 0)]
    data = make_sto(rows, gap=9)
    data.extend(b"TAIL" + struct.pack("<I", 0x12345678))
    struct.pack_into("<II", data, 0x2C, len(data) - 4, 1)
    data = bytes(data)
    for removals in ([1], [0, 2], [0, 1, 2], [], [5], [-1], [3], [4], [2, 9]):
        store.case(f"rewrite {removals}", "store_inventory.rewrite_inventory", {"data": data, "removals": removals},
                   lambda removals=removals: store_inventory.rewrite_inventory(data, set(removals)))
    store.case("truncated", "store_inventory.rewrite_inventory", {"data": data[:0x9b], "removals": [0]},
               lambda: store_inventory.rewrite_inventory(data[:0x9b], {0}))
    store.case("bad table", "store_inventory.rewrite_inventory", {"data": data[:-12], "removals": []},
               lambda: store_inventory.rewrite_inventory(data[:-12], set()))

    creature = Scenario("creature_inventory")
    records = [item_record(name=f"ITEM{i:04}", expiration=i + 10, charges=(i + 1, i + 2, i + 3), flags=0x100 + i)
               for i in range(5)]
    base = bytearray(make_cre(records, slots={9: 1, 10: 2, 21: 0, 22: 3, 37: 4}, gap=7))
    known = len(base)
    base.extend(bytes(range(12)))
    effects = len(base)
    base.extend(bytes(range(48)))
    struct.pack_into("<II", base, 0x2A0, known, 1)
    struct.pack_into("<II", base, 0x2C4, effects, 1)

    def selection(data, weapon, ability):
        result = bytearray(data)
        offset = struct.unpack_from("<I", result, 0x2B8)[0]
        struct.pack_into("<HH", result, offset + 76, weapon, ability)
        return bytes(result)

    weapons = bytes(make_cre([item_record("WEAPON00"), item_record("WEAPON01"), item_record("BACKPACK")],
                             slots={9: 0, 11: 1, 21: 2}))
    single = bytes(make_cre([item_record("WEAPON00")], slots={9: 0}))
    for label, data, edits in (
            ("mixed", selection(base, 0, 6), [[0, None], [1, "newitem"], [3, None]]),
            ("replace selected", selection(base, 0, 6), [[1, "x"]]),
            ("remove selected", selection(weapons, 0, 4), [[0, None]]),
            ("remove second weapon", selection(weapons, 2, 1), [[1, None]]),
            ("remove all weapons", selection(weapons, 0, 4), [[0, None], [1, None]]),
            ("fists", selection(single, 0, 3), [[0, None]]),
            ("special selector", selection(single, 0xFFFF, 5), [[0, None]]),
            ("same selector", selection(weapons, 2, 9), [[0, None]]),
            ("repeat", selection(base, 0, 6), [[2, "a"], [4, None], [2, None]]),
            ("none", selection(base, 0, 6), []),
            ("index out of range", single, [[1, None]]), ("negative", single, [[-1, None]]),
            ("upper", single, [[0, "UPPER"]]), ("long", single, [[0, "toolongres"]]), ("empty name", single, [[0, ""]]),
            ("space", single, [[0, "a b"]]), ("star", single, [[0, "a*"]]), ("unicode", single, [[0, "ä"]]),
            ("first invalid wins", single, [[0, "BAD"], [3, None]]),
            ("eight", single, [[0, "abcdefgh"]])):
        creature.case(f"rewrite {label}", "creature_inventory.rewrite_inventory", {"data": data, "edits": edits},
                      lambda data=data, edits=edits:
                      creature_inventory.rewrite_inventory(data, {index: value for index, value in edits}))
    creature.case("truncated", "creature_inventory.rewrite_inventory", {"data": single[:100], "edits": [[0, None]]},
                  lambda: creature_inventory.rewrite_inventory(single[:100], {0: None}))

    group = Scenario("group_recipients")

    def mask(offset, value):
        data = bytearray(make_itm())
        data[offset] = value
        return bytes(data)

    def creature_bytes(klass=0, race=0, alignment=0, kit=0):
        data = make_cre([])
        struct.pack_into("<I", data, 0x244, kit)
        data[0x272], data[0x273], data[0x27b] = race, klass, alignment
        return bytes(data)

    items = [mask(offset, value) for offset in (0x1e, 0x1f, 0x20, 0x21, 0x29, 0x2b, 0x2d, 0x2f)
             for value in (0x01, 0x04, 0x20, 0x40, 0x80)] + [make_itm()[:0x71], b""]
    creatures = [creature_bytes(klass=k) for k in (0, 1, 2, 3, 5, 11, 20)]
    creatures += [creature_bytes(race=r) for r in (1, 2, 7)]
    creatures += [creature_bytes(alignment=a) for a in (17, 50, 51)]
    creatures += [creature_bytes(kit=k) for k in (0x40000000, 0x400d0000, 0x40070000, 0x00004000, 0x00800000, 0x12345678)]
    creatures += [b"", creature_bytes()[:0x246]]
    for index, item in enumerate(items):
        group.case(f"usable item {index}", "group_recipients.usable_all", {"item": item, "creatures": creatures},
                   lambda item=item: [group_recipients.usable(item, cre) for cre in creatures])
    members = {"mage": creature_bytes(klass=1), "fighter": creature_bytes(klass=2), "a": creature_bytes(klass=1),
               "z": creature_bytes(race=2)}
    for label, item, chosen in (("fighter", mask(0x20, 0x04), members),
                                ("fallback", mask(0x20, 0x84), {"z": members["z"], "a": members["a"]}),
                                ("all", make_itm(), members), ("empty", make_itm(), {})):
        group.case(f"preferred {label}", "group_recipients.preferred_members", {"item": item, "members": chosen},
                   lambda item=item, chosen=chosen: group_recipients.preferred_members(item, chosen))
    return [store, creature, group]


SCROLL_ITEM = {"id": "itm:scrl8x", "sha256": "3" * 64}


def shop_world(records, *, sha256="1" * 64, extra_sources=(), resource_id="sto:udduer01", scroll=SCROLL_ITEM):
    store = parse_sto(bytes(make_sto(records)))
    sources = [{"id": f"sto:udduer01/item/{row['record_index']}", "resource": "sto:udduer01", "item": row["resref"],
                "record": row} for row in store["items"]]
    return {"resources": [{"id": "are:other", "sha256": "2" * 64}, {"id": resource_id, "sha256": sha256}]
            + ([scroll] if scroll else []),
            "sources": list(reversed(sources)) + list(extra_sources)}


def original_options_scenario() -> Scenario:
    s = Scenario("original_options")
    for campaign, game in (("bg1", "eet"), ("bg2", "eet"), ("bg1", "bgee"), ("bg1", "bg2ee"), ("bg1", "iwdee"),
                           ("bg3", "eet")):
        s.case(f"tome_variants {campaign} {game}", "original_options.tome_variants",
               {"campaign": campaign, "game": game},
               lambda campaign=campaign, game=game: original_options.tome_variants(campaign, game))
    for quantity in (0, 1, 3, 9):
        data = bytes(make_sto([(item_record("scrl8x", charges=(2, 3, 4), flags=9), quantity, 1),
                               (item_record("scrl8x", charges=(9, 8, 7), flags=4), 1, 0),
                               (item_record("other"), 5, 0)], gap=11))
        s.case(f"spell shield stock {quantity}", "original_options.patch_spell_shield_stock", {"data": data},
               lambda data=data: original_options.patch_spell_shield_stock(data))
    insert = make_sto([(item_record("other"), 4, 0), (item_record("scrl7q"), 8, 1), (item_record("scrlal"), 9, 0)],
                      gap=13)
    struct.pack_into("<II", insert, 0x2C, len(insert), 1)
    insert.extend(struct.pack("<I", 16))
    for label, data in (("insert", bytes(insert)), ("empty", bytes(make_sto())),
                        ("no anchor", bytes(make_sto([(item_record("other"), 4, 0)]))),
                        ("truncated", bytes(make_sto())[:-1])):
        s.case(f"spell shield stock {label}", "original_options.patch_spell_shield_stock", {"data": data},
               lambda data=data: original_options.patch_spell_shield_stock(data))
    row = {"id": "bg2:mode2:item:228", "source_ref": "lists/items/mode2/bg2.2da:228", "campaign": "bg2",
           "family": "mode2", "source": "udduer01.sto", "ident": "sw10", "tier": "s7", "token": "w01", "extra": 1}
    for label, value in (("row", row), ("changed", dict(row, tier="s6")), ("missing", {"id": row["id"]}),
                         ("list", [row]), ("none", None)):
        s.case(f"is_spell_shield_row {label}", "original_options.is_spell_shield_row", {"row": value},
               lambda value=value: original_options.is_spell_shield_row(value))
    for label, rows in (("none", []), ("row", [row]), ("among others", [{"id": "x"}, None, row, "row"]),
                        ("changed", [dict(row, token="w02")]), ("others", [{"id": "x"}, [row]])):
        s.case(f"spell_shield_randomized {label}", "original_options.spell_shield_randomized", {"rows": rows},
               lambda rows=rows: original_options.spell_shield_randomized(rows))
    worlds = {
        "existing": shop_world([(item_record("other"), 4, 0), (item_record("scrl8x", charges=(1, 2, 3), flags=7), 9, 1)]),
        "zero": shop_world([(item_record("scrl8x"), 0, 0)]),
        "absent": shop_world([(item_record("other"), 4, 0), (item_record("scrl7p"), 8, 0)]),
        "absent no anchor": shop_world([(item_record("other"), 4, 0)]),
        "empty store": shop_world([]),
        "duplicate": shop_world([(item_record("scrl8x"), 1, 0), (item_record("scrl8x"), 2, 0)]),
        "no store": shop_world([], resource_id="sto:other"),
        "bad hash": shop_world([(item_record("scrl8x"), 1, 0)], sha256="ABC"),
        "string index": shop_world([(item_record("scrl8x"), 1, 0)], extra_sources=[
            {"id": "x", "resource": "sto:udduer01", "item": "other", "record": {"record_index": "a"}}]),
        "missing key": {"resources": [{"id": "sto:udduer01", "sha256": "1" * 64}, SCROLL_ITEM],
                        "sources": [{"id": "x", "resource": "sto:udduer01", "record": {"record_index": 0}}]},
        "no sources": {"resources": [{"id": "sto:udduer01", "sha256": "1" * 64}, SCROLL_ITEM]},
        # The option stocks SCRL8X.ITM, which another mod may have removed.
        "no scroll item": shop_world([(item_record("scrl8x"), 1, 0)], scroll=None),
        "scroll row without id": shop_world([(item_record("scrl8x"), 1, 0)], scroll={"sha256": "3" * 64}),
        "quantity float": shop_world([(item_record("scrl8x"), 1, 0)]),
        # The world scan left out a file it could not read (sources.scan_world).
        "unreadable store": dict(shop_world([]), unreadable=[{"id": "sto:udduer01", "reason": "Truncated STOR header."}]),
        "unreadable other": dict(shop_world([(item_record("scrl8x"), 1, 0)]),
                                 unreadable=[{"id": "are:other", "reason": "Truncated AREA header."}]),
        "unreadable no id": dict(shop_world([]), unreadable=[{"reason": "Truncated STOR header."}]),
        "unreadable none": dict(shop_world([]), unreadable=None),
    }
    worlds["quantity float"]["sources"][0]["record"]["quantity"] = 7.5
    for label, world in worlds.items():
        for randomized in (False, True):
            s.case(f"spell_shield_spec {label} {randomized}", "original_options.spell_shield_spec",
                   {"world": world, "randomized": randomized},
                   lambda world=world, randomized=randomized: original_options.spell_shield_spec(world,
                                                                                                randomized=randomized))
    valid = original_options.spell_shield_spec(worlds["existing"], randomized=True)
    inserted = original_options.spell_shield_spec(worlds["absent"], randomized=False)
    specs = {"valid": valid, "inserted": inserted, "list": [valid], "missing field": {k: v for k, v in valid.items()
                                                                                      if k != "payload"}}
    for key, values in (("quantity", (2, 10, 3.0, True, 0x100000000)), ("record_index", (-1, 1, "1")),
                        ("source_id", ("other", None)), ("resource_sha256", ("A" * 64, 5)),
                        ("original_source_id", ("sto:udduer01/item/0",)), ("original_record", ([], None)),
                        ("payload", ({"resref": "scrl8x"},))):
        for value in values:
            specs[f"{key}={value!r}"] = dict(valid, **{key: value})
    for key, value in (("resref", "scrl8y"), ("infinite_supply", 1), ("infinite_supply_flag", 0), ("byte_offset", 3),
                       ("quantity", -1), ("record_index", True), ("charges", [1, 2]), ("expiration", 70000)):
        changed = deepcopy(valid)
        changed["original_record"][key] = value
        if key in ("charges", "expiration"):
            changed["payload"][key] = value
        specs[f"record {key}={value!r}"] = changed
    for key, value in (("quantity", 4), ("original_source_id", "x"), ("payload", dict(inserted["payload"], flags=0))):
        specs[f"inserted {key}"] = dict(inserted, **{key: value})
    for label, spec in specs.items():
        s.case(f"validate_spell_shield_spec {label}", "original_options.validate_spell_shield_spec", {"spec": spec},
               lambda spec=spec: original_options.validate_spell_shield_spec(spec))
    absent = sorted(ALL_DISGUISE_ITEMS - {"brac13", "brac14", "brac15", "ring31", "sper05", "sw2h11"})
    for label, available, game, revisions in (
            ("all", sorted(ALL_DISGUISE_ITEMS), "eet", False), ("absent", absent, "bgee", False),
            ("revisions", sorted(ALL_DISGUISE_ITEMS), "bg2ee", True), ("needs donor", sorted(ALL_DISGUISE_ITEMS - {"brac06"}), "eet", False),
            ("no sources", ["brac06"], "eet", False), ("bad game", [], "iwdee", False),
            ("partial", ["ring23", "ring06", "ring07", "ring08", "ring09"], "eet", False)):
        s.case(f"cursed_disguise_choices {label}", "original_options.cursed_disguise_choices",
               {"available": available, "game": game, "item_revisions": revisions},
               lambda available=available, game=game, revisions=revisions:
               original_options.cursed_disguise_choices(available, game=game, item_revisions=revisions))
    original = make_itm(15, 16, price=500)
    donor = bytearray(make_itm(25, 26, price=900))
    donor[0x3A:0x42] = b"INVICON1"
    donor[0x58:0x60] = b"CARICON1"
    struct.pack_into("<I", donor, 0x50, 123)
    donor = bytes(donor)
    plate = bytearray(make_itm())
    struct.pack_into("<IH", plate, 0x6A, len(plate), 0)
    struct.pack_into("<H", plate, 0x70, 3)
    plate.extend(b"\0" * (3 * 0x30))
    for index, (opcode, mode) in enumerate(((0, 16), (0, 1), (17, 16))):
        struct.pack_into("<H", plate, 0x72 + index * 0x30, opcode)
        struct.pack_into("<II", plate, 0x76 + index * 0x30, 9, mode)
    plate = bytes(plate)
    for label, source, other, description, name in (
            ("appearance", original, donor, None, None), ("description", original, donor, 456, None),
            ("plate", plate, make_itm(), 77, 88), ("plate range", plate[:-1], make_itm(), None, 88),
            ("bad description", original, donor, -1, None), ("bad plate", plate, donor, None, 0x100000000),
            ("short source", original[:0x71], donor, None, None), ("bad donor", original, b"ITM V2  " + donor[8:], None, None),
            ("plate no effects", original, donor, None, 5)):
        s.case(f"patch_cursed_disguise {label}", "original_options.patch_cursed_disguise",
               {"source": source, "donor": other, "description_strref": description, "plate_name_strref": name},
               lambda source=source, other=other, description=description, name=name:
               original_options.patch_cursed_disguise(source, other, description_strref=description,
                                                      plate_name_strref=name))
    return s


def reward_route_rows_scenario() -> Scenario:
    """test_a_presets_rows_of_an_original_reward_route_are_that_route, on every
    extended row of the presets and original tables: a row is a reward route's
    own row, or a preset's row of the reward or place one original row names,
    and only as its table has it."""
    s = Scenario("reward_route_rows")
    options = {"scrolls": True, "kangaxx": True, "flail_heads": True, "totsc_tome": True}
    calls = {"items": "mode2_route_source", "targets": "mode2_route_target"}
    keys = {"items": ("id", "campaign", "source", "source_ref"),
            "targets": ("id", "campaign", "target", "area", "source_ref")}
    rows, places = {}, set()
    for template in ("eet-original", "eet-boss-loot"):
        draft = templates.build_template(template, items="extended", targets="extended", options=options)
        for kind in calls:
            standard = [row for row in draft[kind] if row.get("family") != "mode2"]
            rows.update(((kind, row["id"]), row) for row in draft[kind] if row.get("family") == "mode2")
            rows.update(((kind, row["id"]), row) for row in standard[:3])
            # The base table's rows that are a route's own (Adalon's place),
            # and a preset's rows at the same place.
            places.update((kind, row.get("target"), row.get("area")) for row in standard
                          if row["id"] in mode2_rewards.TARGET_REWARDS or row["id"] in mode2_rewards.SOURCE_REWARDS)
            rows.update(((kind, row["id"]), row) for row in standard
                        if (kind, row.get("target"), row.get("area")) in places)

    def check(kind: str, label: str, row) -> None:
        call = calls[kind]
        s.case(f"{call} {label}", f"original_catalog.{call}", {"row": row},
               lambda call=call, row=row: getattr(original_catalog, call)(row))

    for (kind, identity), row in sorted(rows.items()):
        check(kind, identity, row)
    # Boss Loot's rows of Aldeth's, Varci's and Nemphre's rewards and places,
    # an original row of each, and BG2's ogre place, which is no .fl place.
    changes = {"items": ({"tier": "27"}, {"source": "ALDETH.DLG"}, {"source": "varci.dlg"}, {"source": 5},
                         {"token": "w99"}, {"ident": "x"}, {"campaign": "bg2"}, {"id": "bg1:boss-loot:mode2:item:63"},
                         {"id": None}, {"source_ref": "lists/items/mode2/bg1.2da:62"}),
               "targets": ({"tier": "27"}, {"area": "AR0617"}, {"area": "ar0617"}, {"target": "aldeth"},
                           {"location": "w9"}, {"campaign": "bg2"}, {"id": "bg1:boss-loot:mode2:target:47"},
                           {"id": None}, {"source_ref": "lists/locations/mode2/bg1.2da:46"})}
    chosen = {"items": ("bg1:boss-loot:mode2:item:62", "bg1:boss-loot:mode2:item:71",
                        "bg1:boss-loot:mode2:item:339", "bg1:mode2:item:16"),
              "targets": ("bg1:boss-loot:mode2:target:46", "bg1:boss-loot:mode2:target:71",
                          "bg1:boss-loot:mode2:target:291", "bg1:mode2:target:16", "bg2:mode2:target:17")}
    for kind, identities in chosen.items():
        for identity in identities:
            row = rows[(kind, identity)]
            for change in changes[kind]:
                check(kind, f"{identity} {json.dumps(change, sort_keys=True)}", dict(row, **change))
            for key in keys[kind]:
                check(kind, f"{identity} without {key}", {name: value for name, value in row.items() if name != key})
    for label, value in (("none", None), ("list", [rows[("items", "bg1:mode2:item:16")]]), ("text", "bg1:mode2:item:16"),
                         ("empty", {}), ("number id", {"id": 16}), ("own id only", {"id": "bg1:mode2:item:16"}),
                         ("own place id only", {"id": "bg1:mode2:target:16"})):
        for kind in calls:
            check(kind, label, value)
    return s


def cursed_scenario() -> Scenario:
    s = Scenario("cursed_disguises")
    for label, log in (("core", b'// ignored\n~ITEM_REV/ITEM_REV.TP2~ #2 #0 // core\n'),
                       ("windows", b'~C:\\Mods\\item_rev.tp2~ #0 #000 // core\n'),
                       ("comment", b'// ~item_rev.tp2~ #0 #0'), ("component", b'~item_rev.tp2~ #0 #10'),
                       ("other", b'~other_item_rev.tp2~ #0 #0'), ("empty", b''),
                       ("blank lines", b'\n\n  \r\n\t~x/item_rev.tp2~\t#1\t#0\r\n'),
                       ("word after", b'~item_rev.tp2~ #0 #0x'), ("dash after", b'~item_rev.tp2~ #0 #0-'),
                       ("no space", b'~item_rev.tp2~#0 #0'), ("crlf path", b'~item\r_rev.tp2~ #0 #0'),
                       ("second line", b'~a.tp2~ #0 #0\n~ITEM_REV.TP2~ #0 #0\n')):
        s.case(f"item_revisions_installed {label}", "cursed_disguises.item_revisions_installed", {"weidu_log": log},
               lambda log=log: cursed_disguises.item_revisions_installed(log))
    data = cursed_tests.resources()
    hashes = {key: sha(value) for key, value in data.items()}
    spec = cursed_disguises.cursed_disguise_spec(hashes)
    absent = {key: value for key, value in hashes.items()
              if key not in ("brac13", "brac14", "brac15", "ring31", "sper05", "sw2h11")}
    for label, item_hashes, game, revisions in (
            ("all", hashes, "eet", False), ("absent", absent, "bgee", False), ("revisions", hashes, "eet", True),
            ("bad hash", dict(hashes, brac06="X" * 64), "eet", False), ("bad game", hashes, "bg1", False),
            ("extra", dict(hashes, other="nothex"), "eet", False)):
        s.case(f"cursed_disguise_spec {label}", "cursed_disguises.cursed_disguise_spec",
               {"item_hashes": item_hashes, "game": game, "item_revisions": revisions},
               lambda item_hashes=item_hashes, game=game, revisions=revisions:
               cursed_disguises.cursed_disguise_spec(item_hashes, game=game, item_revisions=revisions))
    changed = deepcopy(spec)
    changed["choices"][0]["variants"][0]["donor"] = "brac01"
    floats = deepcopy(spec)
    floats["choices"][0]["variants"][0]["description_tra"] = 200.0
    variants = {"valid": spec, "changed": changed, "floats": floats, "list": [spec],
                "missing": {key: value for key, value in spec.items() if key != "game"},
                "hashes list": dict(spec, resource_hashes=[]), "game int": dict(spec, game=5),
                "game none": dict(spec, game=None), "game list": dict(spec, game=["eet"]),
                "revisions int": dict(spec, item_revisions=0), "extra hash": dict(spec, resource_hashes=dict(
                    spec["resource_hashes"], other="1" * 64)),
                "game float": dict(spec, game=1.5), "game bool": dict(spec, game=True)}
    for label, value in variants.items():
        s.case(f"validate {label}", "cursed_disguises.validate_cursed_disguise_spec", {"spec": value},
               lambda value=value: cursed_disguises.validate_cursed_disguise_spec(value))
    for seed in (1, 1234567, 2147483646, 0, -1, 2147483647, True, 7.0, "1", 570, 2147483076):
        s.case(f"indices {seed!r}", "cursed_disguises.cursed_disguise_indices", {"spec": spec, "seed": seed},
               lambda seed=seed: cursed_disguises.cursed_disguise_indices(spec, seed))
    revisions = cursed_disguises.cursed_disguise_spec(hashes, item_revisions=True)
    text = GameText(make_tlk(["Existing text"]))
    both = GameText(make_tlk(["Existing text"]), make_tlk(["Existing text (f)"]))
    _, _, prepared = cursed_disguises.prepare_cursed_disguises(spec, data, text)
    for label, value, resources, source, language in (
            ("all", spec, data, text, "en_us"), ("again", spec, data, prepared, "EN_GB"),
            ("revisions", revisions, data, GameText(make_tlk([])), "english"), ("floats", floats, data, text, "en_us"),
            ("german", spec, data, text, "de_de"), ("german both", spec, data, both, "de_DE"),
            ("polish both", spec, data, both, "pl_PL"), ("polish revisions", revisions, data, both, "pl_PL"),
            ("spanish", spec, data, both, "es_ES"), ("default", spec, data, both, "default"),
            ("changed", spec, dict(data, brac06=data["brac06"] + b"x"), text, "en_us"),
            ("missing", spec, {k: v for k, v in data.items() if k != "ring31"}, text, "en_us"),
            ("bad tlk", spec, data, GameText(b"bad"), "en_us"),
            ("bad female", spec, data, GameText(make_tlk(["Existing text"]), b"bad"), "pl_PL")):
        s.case(f"prepare {label}", "cursed_disguises.prepare_cursed_disguises",
               {"spec": value, "resources": resources, "text": source, "language": language},
               lambda value=value, resources=resources, source=source, language=language:
               cursed_disguises.prepare_cursed_disguises(value, resources, source, language=language))
    return s


# ---------------------------------------------------------------- game text and translations


def written(files: dict) -> tuple[tempfile.TemporaryDirectory, Path]:
    """A temporary folder holding <files>: relative path -> bytes, or None for a folder ("name/")."""
    temporary = tempfile.TemporaryDirectory(prefix="flir-parity-")
    root = Path(temporary.name)
    for relative, data in files.items():
        path = root / relative
        if data is None:
            path.mkdir(parents=True, exist_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return temporary, root


def relative(path: Path | None, root: Path) -> str | None:
    return None if path is None else path.relative_to(root).as_posix()


def sounded(data: bytes, index: int, flags: int = 3, sound: bytes = b"VOX", volume: int = 0, pitch: int = 0) -> bytes:
    changed = bytearray(data)
    struct.pack_into("<H8sII", changed, 0x12 + index * 0x1A, flags, sound, volume, pitch)
    return bytes(changed)


def game_text_scenario() -> Scenario:
    s = Scenario("game_text")
    male = make_tlk(["Shared", "Same", "Gendered", "Voiced"])
    female = make_tlk(["Shared (f)", "Same", "Gendered (f)", "Voiced"])
    gendered = (("Same", "Same"), ("Gendered", "Gendered (f)"), ("Shared", "Shared"), ("New", "New (f)"),
                ("New", "New (f)"))
    first, _ = GameText(male, female).append(gendered)
    same = (("Same", "Same"),)
    for label, value, texts in (
            ("both", GameText(male, female), gendered), ("both again", first, (("New", "New (f)"),)),
            ("male only", GameText(male), (("Gendered", "Other"), ("New", "New (f)"))),
            ("empty", GameText(male, female), ()),
            ("unicode", GameText(male, female), (("Zvedáš", "Zvedáš (ž)"), ("Zvedáš", "Zvedáš"))),
            ("male sound", GameText(sounded(male, 1), female), same),
            ("female sound", GameText(male, sounded(female, 1)), same),
            ("female flags", GameText(male, sounded(female, 1, flags=5, sound=b"")), same),
            ("female volume", GameText(male, sounded(female, 1, flags=1, sound=b"", volume=1)), same),
            ("female pitch", GameText(male, sounded(female, 1, flags=1, sound=b"", pitch=2)), same),
            ("counts differ", GameText(male, make_tlk(["Shared (f)"])), (("New", "New"),)),
            ("bad male", GameText(b"invalid", female), (("New", "New"),)),
            ("bad female", GameText(male, b"TLK V1  "), (("New", "New"),)),
            ("bad male empty", GameText(b"bad"), ())):
        s.case(f"append {label}", "game_text.GameText.append", {"text": value, "texts": texts},
               lambda value=value, texts=texts: value.append(texts))
    # One entry's text in both files (test_string_reads_an_entry_from_both_files).
    unicode = make_tlk(["Zvedáš", "Bad X"]).replace(b"Bad X", b"Bad \xff")
    beyond = bytearray(make_tlk(["Short", "Other"]))
    struct.pack_into("<I", beyond, 0x12 + 0x16, 1000)
    for label, value, refs in (
            ("both", GameText(male, female), (0, 1, 2, 3, 4, -1)),
            ("male only", GameText(male), (0, 3, 4)),
            ("voiced", GameText(sounded(male, 1), sounded(female, 2, flags=2)), (1, 2)),
            ("no text flag", GameText(sounded(male, 1, flags=4, sound=b""), female), (1,)),
            ("unicode", GameText(unicode, unicode), (0, 1)),
            ("beyond", GameText(bytes(beyond), make_tlk(["Short (f)", "Other (f)"])), (0, 1)),
            ("short table", GameText(struct.pack("<8sHII", b"TLK V1  ", 0, 7, 0x12)), (0, 6, 7)),
            ("empty", GameText(make_tlk([])), (0,)),
            ("bad male", GameText(b"bad", female), (0,)),
            ("bad female", GameText(male, b"TLK V1  "), (0,)),
            ("other signature", GameText(b"TLK V2  " + male[8:]), (0,))):
        for ref in refs:
            s.case(f"string {label} {ref}", "game_text.GameText.string", {"text": value, "ref": ref},
                   lambda value=value, ref=ref: value.string(ref))
    one, two = make_tlk(["One", "Two"]), make_tlk(["One (f)", "Two (f)"])
    grown = bytearray(game_text._append_texts(one, ("Three", "Four"))[0])
    # Entries are copied as they are, sound and all.
    struct.pack_into("<H8sII", grown, 0x12 + 3 * 0x1A, 3, b"FOURVOX", 5, 6)
    grown = bytes(grown)
    copied = game_text.paired(grown, two).female
    for label, male_data, female_data in (
            ("grown", grown, two), ("again", grown, copied), ("equal", one, two), ("no female", one, None),
            ("female longer", one, copied), ("bad male", b"bad", two), ("bad female", one, b"bad"),
            ("no female bad male", b"bad", None)):
        s.case(f"paired {label}", "game_text.paired", {"male": male_data, "female": female_data},
               lambda male_data=male_data, female_data=female_data: game_text.paired(male_data, female_data))
    for label, data, texts in (("new", one, ("Three", "One", "Three")), ("none", one, ()),
                               ("bad", b"invalid", ("x",))):
        s.case(f"_append_texts {label}", "game_text._append_texts", {"data": data, "texts": texts},
               lambda data=data, texts=texts: game_text._append_texts(data, texts))
    tlk = make_tlk(["a", "b"])
    for label, files, language in (
            ("male only", {"lang/de_DE/dialog.tlk": tlk}, "de_DE"),
            ("upper female", {"lang/de_DE/dialog.tlk": tlk, "lang/de_DE/DIALOGF.TLK": tlk}, "de_DE"),
            ("folder case", {"lang/de_DE/dialog.tlk": tlk, "lang/de_DE/DIALOGF.TLK": tlk}, "DE_de"),
            ("mixed female", {"lang/pl_pl/dialog.tlk": tlk, "lang/pl_pl/dialogF.tlk": tlk}, "pl_pl"),
            ("default", {"dialog.tlk": tlk, "lang/de_DE/dialogF.tlk": tlk}, "default"),
            ("default female", {"dialog.tlk": tlk, "DialogF.tlk": tlk}, "default"),
            ("other files", {"lang/de_DE/dialog.tlk": tlk, "lang/de_DE/dialogf.tlk.bak": tlk,
                             "lang/de_DE/xdialogf.tlk": tlk}, "de_DE"),
            ("female folder", {"lang/de_DE/dialog.tlk": tlk, "lang/de_DE/dialogf.tlk/": None}, "de_DE"),
            ("no male", {"lang/de_DE/dialogF.tlk": tlk}, "de_DE")):
        def listed(files=files, language=language):
            temporary, root = written(files)
            with temporary:
                found = game_text.text_files(root, language)
                return relative(found[0], root), relative(found[1], root)
        s.case(f"text_files {label}", "game_text.text_files", {"files": files, "language": language}, listed)
    header = struct.pack("<8sHII", b"TLK V1  ", 0, 7, 0x12)
    for label, files, female_name in (
            ("no female", {"dialog.tlk": tlk}, None), ("nothing", {}, None),
            ("equal", {"dialog.tlk": tlk, "DIALOGF.TLK": make_tlk(["a (f)", "b (f)"])}, "DIALOGF.TLK"),
            ("shorter", {"dialog.tlk": tlk, "DIALOGF.TLK": make_tlk(["a (f)"])}, "DIALOGF.TLK"),
            ("longer", {"dialog.tlk": tlk, "dialogF.tlk": make_tlk(["a", "b", "c"])}, "dialogF.tlk"),
            ("female not tlk", {"dialog.tlk": tlk, "DIALOGF.TLK": b"TLK V1"}, "DIALOGF.TLK"),
            ("male not tlk", {"dialog.tlk": b"TLK V2  " + bytes(10), "dialogF.tlk": tlk}, "dialogF.tlk"),
            ("both empty", {"dialog.tlk": b"", "dialogF.tlk": b""}, "dialogF.tlk"),
            ("header only", {"dialog.tlk": header, "dialogF.tlk": tlk}, "dialogF.tlk")):
        def reason(files=files, female_name=female_name):
            temporary, root = written(files)
            with temporary:
                return game_text.unpaired(root / "dialog.tlk", None if female_name is None else root / female_name)
        s.case(f"unpaired {label}", "game_text.unpaired", {"files": files, "male": "dialog.tlk",
                                                            "female": female_name}, reason)
    return s


# Every line a text route or a golem chest shows.
LINES = (*(line for resource in sorted(mode2_rewards.TEXT_REFS) for line in mode2_rewards.text_lines(resource)),
         *container_rewards.TEXT_LINES)
# Every line the original options Kangaxx's items and cursed disguises show.
OPTION_LINES = (*quest_options.KANGAXX_LINES, *sorted({
    ("in-game.tra", face[key]) for item_revisions in (False, True)
    for row in original_options.cursed_disguise_choices(ALL_DISGUISE_ITEMS, item_revisions=item_revisions)
    for face in row["variants"] for key in ("description_tra", "name_tra") if face[key] is not None}))
CODES = ("cs_CZ", "de_DE", "en_US", "es_ES", "fr_FR", "hu_HU", "it_IT", "ja_JP", "ko_KR", "pl_PL", "pt_BR", "ru_RU",
         "tr_TR", "uk_UA", "zh_CN", "default", "DEFAULT", "nb_NO", "nb_no", "pl", "PL_pl", "", "en", "de-DE", "_de",
         "en_us_extra")
TRA_SOURCES = {
    "plain": '@1 = ~One~\r\n@2 = "Two"\r\n',
    "female": "@1 = ~He~ ~She~\r\n",
    "sound": "@1 = ~Text~ [SOUND1]\r\n",
    "sound female": "@1 = ~He~ [VOX1] ~She~ [VOX2]\r\n",
    "female next line": "@1 = ~He~\r\n~She~\r\n@2 = ~Two~\r\n",
    "unclosed female": "@1 = ~He~ ~She\r\n@2 = ~Two~\r\n",
    "unclosed sound": "@1 = ~He~ [VOX\r\n@2 = ~Two~\r\n",
    "sound then unclosed female": "@1 = ~He~ [VOX] ~She\r\n@2 = ~Two~\r\n",
    "stray tilde": "@1 = ~a~ ~ \r\n@2 = ~c~\r\n",
    "duplicate": "@1 = ~Old~\r\n@1 = ~New~ ~New (f)~\r\n",
    "unicode digits": "@１２ = ~Wide~\r\n",
    "arabic digits": "@١٠ = ~Ten~\r\n",
    "leading zeros": "@007 = ~Bond~\r\n",
    "no digits": "@ = ~x~\r\n@3 = ~y~\r\n",
    "not at line start": "x @1 = ~x~\r\n@2 = ~y~\r\n",
    "unicode space": "@1　= ~x~ ~y~\r\n",
    "separator space": "@1\x1c=\x1d~x~\x1e[s]\x1f~y~\r\n",
    "multiline": "@1 = ~line one\r\nline two~\r\n",
    "mixed delimiters": '@1 = ~a"b~ "c~d"\r\n',
    "empty": "",
    "comment between": "@1 = ~a~ // c\r\n~b~\r\n",
    "cr only": "@1 = ~a~\r@2 = ~b~\r",
    "byte order mark": "﻿@1 = ~a~\r\n@2 = ~b~",
    "no equals": "@1 ~a~\r\n@2 = ~b~\r\n",
    "huge number": "@" + "9" * 4301 + " = ~x~\r\n",
    "limit number": "@" + "0" * 4290 + "12 = ~x~\r\n",
}


def listed_lines(found: dict) -> list:
    return [[file, number, *pair] for (file, number), pair in sorted(found.items())]


def listed_texts(found: dict) -> list:
    return [[number, *pair] for number, pair in sorted(found.items())]


CZECH = "@101 = ~Zvedáš předmět.~\r\n@399 = ~Studna.~ ~Studna (ž).~\r\n"
FOLDERS = {
    "czech": {"czech/in-game.tra": CZECH.encode("cp1250")},
    "czech converted": {"czech/in-game.tra": CZECH.encode("utf-8"), "czech/" + translations.CONVERTED: b"marker"},
    "czech converted ee": {"czech/in-game.tra": CZECH.encode("utf-8"), "czech/" + translations.CONVERTED: b"marker",
                           "bgee/czech/in-game.tra": "@101 = ~Zvedáš věc.~\r\n".encode("utf-8")},
    "czech marker folder": {"czech/in-game.tra": CZECH.encode("cp1250"), "czech/" + translations.CONVERTED + "/": None},
    "czech converted not utf-8": {"czech/in-game.tra": CZECH.encode("cp1250"),
                                  "czech/" + translations.CONVERTED: b""},
    "czech partial": {"czech/in-game.tra": "@101 = ~Zvedáš předmět.~\r\n".encode("cp1250")},
    "czech ee only": {"bgee/czech/in-game.tra": "@101 = ~x~\r\n@399 = ~y~\r\n".encode("utf-8")},
    "czech is a file": {"czech": b"x"},
    "czech tra folder": {"czech/in-game.tra/": None},
    "polish not cp1250": {"polish/in-game.tra": b"@101 = ~\x81~\r\n"},
    "polish ee not utf-8": {"polish/in-game.tra": b"@101 = ~ok~\r\n", "bgee/polish/in-game.tra": b"@101 = ~\xff~\r\n"},
    "japanese not cp932": {"japanese/in-game.tra": b"@101 = ~\x81\x20~\r\n"},
    "japanese": {"japanese/in-game.tra": "@101 = ~アイテム~ ~アイテム（女）~\r\n@399 = ~井戸~\r\n".encode("cp932")},
    "chinese": {"schinese/in-game.tra": "@101 = ~物品~\r\n@399 = ~井~\r\n".encode("cp936")},
    "korean": {"korean/in-game.tra": "@101 = ~아이템~\r\n@399 = ~우물~\r\n".encode("cp949")},
    "russian": {"russian/in-game.tra": "@101 = ~Предмет~\r\n@399 = ~Колодец~\r\n".encode("cp1251")},
    "turkish": {"turkish/in-game.tra": "@101 = ~Eşya~\r\n@399 = ~Kuyu~\r\n".encode("cp1254")},
    "spanish empty": {"spanish/": None},
    "spanish huge number": {"spanish/in-game.tra": ("@" + "1" * 4301 + " = ~x~\r\n").encode("cp1252")},
    "sorted files": {"czech/b.tra": b"", "czech/A.tra": b"", "czech/c.TRA": b"", "czech/sub/x.tra": b"",
                     "bgee/czech/Z.tra": b"", "bgee/polish/in-game.tra": b"@101 = ~z~\r\n"},
    "nothing": {},
    "english": {"english/in-game.tra": b"@101 = ~Item~\r\n@399 = ~Well~ ~Well (f)~\r\n"},
    "czech english": {"english/in-game.tra": b"@101 = ~Item~\r\n@399 = ~Well~\r\n",
                      "czech/in-game.tra": CZECH.encode("cp1250")},
    "czech partial english": {"english/in-game.tra": b"@101 = ~Item~\r\n@399 = ~Well~\r\n",
                              "czech/in-game.tra": "@101 = ~Zvedáš předmět.~\r\n".encode("cp1250")},
    "polish not cp1250 english": {"english/in-game.tra": b"@101 = ~Item~\r\n@399 = ~Well~\r\n",
                                  "polish/in-game.tra": b"@101 = ~\x81~\r\n@399 = ~x~\r\n"},
    "polish ee not utf-8 english": {"english/in-game.tra": b"@101 = ~Item~\r\n@399 = ~Well~\r\n",
                                    "polish/in-game.tra": b"@101 = ~ok~\r\n@399 = ~ok~\r\n",
                                    "bgee/polish/in-game.tra": b"@101 = ~\xff~\r\n"},
}
FOLDER_CALLS = (
    ("czech", "cs_CZ"), ("czech converted", "cs_CZ"), ("czech converted ee", "cs_CZ"),
    ("czech marker folder", "cs_CZ"), ("czech converted not utf-8", "cs_CZ"), ("czech partial", "cs_CZ"),
    ("czech ee only", "cs_CZ"), ("czech is a file", "cs_CZ"), ("czech tra folder", "cs_CZ"),
    ("polish not cp1250", "pl_PL"), ("polish ee not utf-8", "pl_PL"), ("japanese not cp932", "ja_JP"),
    ("japanese", "ja_JP"), ("chinese", "zh_CN"), ("korean", "ko_KR"), ("russian", "ru_RU"), ("turkish", "tr_TR"),
    ("spanish empty", "es_ES"), ("spanish huge number", "es_ES"), ("sorted files", "cs_CZ"),
    ("sorted files", "default"), ("sorted files", "nb_NO"), ("nothing", "cs_CZ"), ("nothing", "default"),
    ("english", "cs_CZ"), ("english", "default"), ("english", "en_US"), ("czech english", "cs_CZ"),
    ("czech partial english", "cs_CZ"), ("polish not cp1250 english", "pl_PL"), ("polish ee not utf-8 english", "pl_PL"))


def folder_call(call: str, files: dict, code: str, wanted=None, name=None):
    """A translations function with LANGUAGES_DIR in a temporary folder holding <files>."""
    temporary, root = written({"languages/" + key: value for key, value in files.items()})
    with temporary, patch.object(translations, "LANGUAGES_DIR", root / "languages"):
        if call == "missing":
            return translations.missing(code, wanted)
        if call == "lines":
            return listed_lines(translations.lines(code, wanted))
        if call == "option_lines":
            return listed_lines(translations.option_lines(code, wanted))
        if call == "texts":
            return listed_texts(translations.texts(code, name))
        return [relative(path, root) for path in translations.sources(code)]


def translations_scenario() -> Scenario:
    s = Scenario("translations")
    s.case("codepage digests", "codepages.digests", {}, codepages.digests)
    for code in CODES:
        s.case(f"language {code!r}", "translations.language", {"code": code},
               lambda code=code: translations.language(code))
        s.case(f"missing {code!r}", "translations.missing", {"code": code, "wanted": LINES},
               lambda code=code: translations.missing(code, LINES))
    for encoding in ("utf-8", "cp1250", "cp1251", "cp1252", "cp1254", "cp932", "cp936", "cp949"):
        s.case(f"charset {encoding}", "translations.charset", {"encoding": encoding},
               lambda encoding=encoding: translations.charset(encoding))
    for code in ("en_US", "pl_PL", "de_DE", "es_ES", "default"):
        s.case(f"lines {code}", "translations.lines", {"code": code, "wanted": LINES},
               lambda code=code: listed_lines(translations.lines(code, LINES)))
        s.case(f"lines {code} none", "translations.lines", {"code": code, "wanted": []},
               lambda code=code: listed_lines(translations.lines(code, [])))
    for code in ("en_US", "pl_PL", "de_DE", "es_ES", "default", "nb_NO", "PL_pl", "english"):
        s.case(f"option_lines {code}", "translations.option_lines", {"code": code, "wanted": OPTION_LINES},
               lambda code=code: listed_lines(translations.option_lines(code, OPTION_LINES)))
        s.case(f"option_lines {code} none", "translations.option_lines", {"code": code, "wanted": []},
               lambda code=code: listed_lines(translations.option_lines(code, [])))
    # German lacks in-game.tra @102, so all three lines are English there.
    mixed = [("in-game.tra", 1503), ("hlskull.tra", 4), ("in-game.tra", 102)]
    for code in ("de_DE", "pl_PL", "es_ES"):
        s.case(f"option_lines {code} mixed", "translations.option_lines", {"code": code, "wanted": mixed},
               lambda code=code: listed_lines(translations.option_lines(code, mixed)))
    for code in ("en_US", "pl_PL", "de_DE", "es_ES", "default", "nb_NO"):
        for name in ("in-game.tra", "ppdjinn.tra", "hlskull.tra", "botsmith.tra", "kpglai01.tra", "udduer01.tra",
                     "setup.tra", "absent.tra"):
            s.case(f"texts {code} {name}", "translations.texts", {"code": code, "name": name},
                   lambda code=code, name=name: listed_texts(translations.texts(code, name)))
    wanted = [("in-game.tra", 101), ("in-game.tra", 399)]
    for label, code in FOLDER_CALLS:
        files = FOLDERS[label]
        for call, arguments in (("missing", {"wanted": wanted}), ("missing", {"wanted": [("ppdjinn.tra", 99)]}),
                                ("lines", {"wanted": wanted}), ("option_lines", {"wanted": wanted}),
                                ("texts", {"name": "in-game.tra"}), ("sources", {})):
            case = f"folder {call} {label} {code}" + (" ppdjinn" if arguments.get("wanted", wanted) != wanted else "")
            s.case(case, f"translations.{call}", {"files": files, "code": code, **arguments},
                   lambda call=call, files=files, code=code, arguments=arguments:
                   folder_call(call, files, code, **arguments))
    for label, source in TRA_SOURCES.items():
        s.case(f"tra_variants {label}", "quest_options.tra_variants", {"source": source},
               lambda source=source: listed_texts(quest_options.tra_variants(source)))
        s.case(f"tra_texts {label}", "quest_options.tra_texts", {"source": source},
               lambda source=source: sorted_ints(quest_options.tra_texts(source)))
    return s


# ---------------------------------------------------------------- a real game


def game_routes_scenario(game: Path, language: str) -> Scenario:
    """The reward routes' resources of an installed game, only read: each one
    patched with the texts the export allocates for it, recognised before and
    after, and exported again; and the copies of reward replies in the game's
    other dialogues, before and after Apply."""
    from flir_config.resources import RESOURCE_TYPES, GameResources
    routes = sorted({*mode2_rewards.RESOURCE_REWARDS, *container_rewards.RESOURCE_TARGETS})
    with GameResources(game, {kind: RESOURCE_TYPES[kind] for kind in ("bcs", "dlg")}) as reader:
        present = {kind: set(reader.names(kind)) for kind in ("bcs", "dlg")}
        originals = {resource: reader.read(*resource.split(":")) for resource in routes
                     if resource.split(":")[1] in present[resource.split(":")[0]]}
        # The dialogues the copies of reward replies are looked for in.
        dialogues: dict[str, bytes | None] = {}

        def capture(name: str) -> bytes | None:
            if name not in dialogues:
                dialogues[name] = reader.read("dlg", name) if name in present["dlg"] else None
            return dialogues[name]
        reward_copies.RewardCopies(capture, {})
    files = {name: data for name, data in dialogues.items() if data is not None}
    male, female = game_text.text_files(game, language)
    text = GameText(male.read_bytes(),
                    None if female is None or game_text.unpaired(male, female) else female.read_bytes())
    # The texts as native_export allocates them; without a translation the
    # text routes have none, and the check leaves them out.
    try:
        text, reward_texts = mode2_rewards.prepare_reward_texts(
            text, originals.keys() & mode2_rewards.TEXT_REFS.keys(), language)
        _, container_texts = container_rewards.prepare_container_texts(
            text, originals.keys() & container_rewards.TEXT_RESOURCES, language)
    except ProfileError:
        reward_texts, container_texts = {}, {}
    s = Scenario("game_routes")
    for resource, data in originals.items():
        if resource in mode2_rewards.RESOURCE_REWARDS:
            result = patched(s, resource, resource, data, refs=reward_texts.get(resource))
        else:
            result = container_patched(s, resource, resource, data, refs=container_texts.get(resource))
        for label, value in (("original", data), ("result", result)):
            if value is None:
                continue
            if resource not in mode2_rewards.RESOURCE_REWARDS:
                container_checks(s, f"{label} {resource}", resource, value)
                continue
            reward_checks(s, f"{label} {resource}", resource, value)
            if resource.startswith("bcs:"):
                script_original_case(s, f"{label} {resource}", resource, value)
            elif resource in mode2_rewards.TEXT_REFS:
                text_original_case(s, f"{label} {resource}", resource, value)
    # The copies as the scan before Apply finds them, and again in the game as
    # Apply leaves it: the route dialogues and the copies patched.
    keys = tuple(sorted(reward_copies._ROUTES))
    copies_case(s, "game", files, routes=(keys,))
    found = reward_copies.RewardCopies(files.get, {})
    applied = dict(files)
    for resource, data in found.patches(list(keys)).items():
        applied[resource[4:]] = data
    for resource, data in originals.items():
        if resource.startswith("dlg:") and resource in mode2_rewards.RESOURCE_REWARDS and resource[4:] in applied:
            try:
                applied[resource[4:]] = mode2_rewards.patch_reward(resource, data, refs=reward_texts.get(resource))
            except ProfileError:
                pass
    copies_case(s, "game applied", applied, {"dlg:" + name: sha(data) for name, data in files.items()}, routes=(keys,))
    print(f"game_routes: {len(originals)} of {len(routes)} reward route resources in the game; "
          f"{len(files)} dialogues read for copies, {len(found.copies)} routes with copies, "
          f"{len(found.reasons)} left out because of copies")
    return s


def main() -> None:
    parser = argparse.ArgumentParser(description="Record the patcher parity fixtures.")
    parser.add_argument("--game", type=Path, help="record the reward routes of this installed game instead "
                                                  "(only read)")
    parser.add_argument("--language", default="en_US", help="the game's language folder (with --game)")
    parser.add_argument("--out", type=Path, help="the folder for the game's fixture, outside the repository "
                                                 "and the game: it holds the game's bytes")
    options = parser.parse_args()
    if options.game is not None:
        if options.out is None:
            parser.error("--game needs --out")
        game, out = options.game.resolve(), options.out.resolve()
        if any(folder == out or folder in out.parents for folder in (ROOT, game)):
            parser.error("--out must be outside the repository and the game")
        game_routes_scenario(game, options.language).write(out)
        return
    scenarios = [
        quest_options_scenario("quest_options", "lf"),
        quest_options_scenario("quest_options_crlf", "crlf"),
        spell_scenario("spell_rewards", spell_tests.spell_fixture()),
        spell_scenario("spell_rewards_layout", spell_tests.spell_fixture()[:-1]),
        spell_scenario("spell_rewards_effect", spell_tests.spell_fixture().replace(b"ring41", b"ring42")),
        spell_scenario("spell_rewards_reviewed", spell_tests.spell_fixture(), patched_hash="0" * 64),
        mode2_scenario(),
        mode2_trgeni_scenario(), copies_scenario(),
        container_scenario("container_rewards", "lf"),
        container_scenario("container_rewards_crlf", "crlf"),
        *inventory_scenarios(),
        original_options_scenario(),
        reward_route_rows_scenario(),
        cursed_scenario(),
        game_text_scenario(),
        translations_scenario(),
    ]
    for scenario in scenarios:
        scenario.write()


if __name__ == "__main__":
    main()
