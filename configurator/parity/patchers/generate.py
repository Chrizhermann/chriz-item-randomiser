"""Generate byte-parity fixtures for the Rust ports of the resource patchers.

Run from the repository root:  python configurator/parity/patchers/generate.py

Every input is synthetic and built with the Python tests' own builders
(tests/test_*.py); no game resource is read. Each scenario becomes
configurator/cir-config/tests/fixtures/patchers/<scenario>.json (cases) and
<scenario>.pins.json (the pinned-hash overrides the reference's tests apply
with patch.dict / patch.object). Every case is evaluated twice: with those
overrides and with the reviewed tables; "independent" records whether both
agree, so the Rust tests can run it without the parity-overrides feature.

Bytes are stored once in "blobs" (by SHA-256) and referenced as
{"$blob": sha}. Python dicts with int keys become JSON objects with decimal
keys; tuples become lists.
"""
from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "tests"), str(ROOT)]

from flir_config import container_rewards, cursed_disguises, mode2_rewards, original_options  # noqa: E402
from flir_config import quest_options, quest_rewards, spell_rewards  # noqa: E402
from flir_config import creature_inventory, group_recipients, store_inventory  # noqa: E402
from flir_config.game_text import GameTextError  # noqa: E402
from flir_config.profiles import ProfileError  # noqa: E402
from flir_config.resources import ScanError  # noqa: E402
from flir_config.world_formats import parse_sto  # noqa: E402

import test_container_rewards as container_tests  # noqa: E402
import test_cursed_disguises as cursed_tests  # noqa: E402
import test_mode2_rewards as mode2_tests  # noqa: E402
import test_quest_options as quest_tests  # noqa: E402
import test_spell_rewards as spell_tests  # noqa: E402
from test_original_options import ALL_DISGUISE_ITEMS  # noqa: E402
from test_postinstall_resources import make_itm, make_tlk  # noqa: E402
from test_postinstall_world_formats import item_record, make_cre, make_sto  # noqa: E402

OUT = ROOT / "configurator" / "cir-config" / "tests" / "fixtures" / "patchers"
TABLES = {
    "quest_options.ORIGINAL_HASHES": quest_options.ORIGINAL_HASHES,
    "mode2_rewards.ORIGINAL_HASHES": mode2_rewards.ORIGINAL_HASHES,
    "mode2_rewards.PATCHED_HASHES": mode2_rewards.PATCHED_HASHES,
    "container_rewards.ORIGINAL_HASHES": container_rewards.ORIGINAL_HASHES,
}


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
            kind = {ProfileError: "Profile", ScanError: "Scan", GameTextError: "GameText",
                    KeyError: "Key", TypeError: "Type"}.get(type(error), "Value")
            return {"error": {"kind": kind, "message": str(error)}}

    def case(self, name: str, call: str, args: dict, function) -> None:
        with self.overrides():
            outcome = self.evaluate(function)
        reference = self.evaluate(function)
        self.cases.append({"name": name, "call": call, "args": self.encode(args),
                           "independent": reference == outcome, **outcome})

    def write(self) -> None:
        OUT.mkdir(parents=True, exist_ok=True)
        document = {"scenario": self.name, "cases": self.cases, "blobs": dict(sorted(self.blobs.items()))}
        (OUT / f"{self.name}.json").write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8", newline="\n")
        (OUT / f"{self.name}.pins.json").write_text(json.dumps(self.pins, indent=1, sort_keys=True) + "\n",
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


def quest_options_scenario(name: str, line_endings: str) -> Scenario:
    resources = quest_option_inputs(line_endings)
    s = Scenario(name, {"quest_options.ORIGINAL_HASHES": {key: sha(value) for key, value in resources.items()}})
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
    tlk = make_tlk(["old"])
    with s.overrides():
        output, prepared_tlk = quest_options.prepare_quest_options(resources, tlk, kangaxx=True)
    for label, args in (
            ("kangaxx", {"resources": resources, "tlk": tlk, "kangaxx": True, "language": "en_us"}),
            ("kangaxx gb", {"resources": resources, "tlk": tlk, "kangaxx": True, "language": "EN_GB"}),
            ("reexport", {"resources": output, "tlk": prepared_tlk, "kangaxx": True, "language": "english"}),
            ("none", {"resources": {}, "tlk": tlk, "kangaxx": False, "language": "en_US"}),
            ("german", {"resources": resources, "tlk": tlk, "kangaxx": True, "language": "de_DE"}),
            ("german none", {"resources": {}, "tlk": tlk, "kangaxx": False, "language": "de_DE"}),
            ("missing crypt", {"resources": {"hlskull.dlg": original}, "tlk": tlk, "kangaxx": True, "language": "en_us"}),
            ("bad tlk", {"resources": resources, "tlk": b"invalid", "kangaxx": True, "language": "en_us"})):
        s.case(f"prepare_quest_options {label}", "quest_options.prepare_quest_options", args,
               lambda args=args: quest_options.prepare_quest_options(
                   args["resources"], args["tlk"], kangaxx=args["kangaxx"], language=args["language"]))
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


# ---------------------------------------------------------------- mode 2


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


def mode2_inputs() -> tuple[dict, dict]:
    """Synthetic originals by resource, and the PATCHED overrides they need."""
    originals = {}
    for resource in mode2_rewards.RESOURCE_REWARDS:
        if resource.startswith("dlg:") and resource not in mode2_rewards.TEXT_REFS:
            originals[resource] = mode2_tests.dialogue_fixture(mode2_rewards.resource_specs(resource))
        elif resource in mode2_rewards.TEXT_REFS and resource.startswith("dlg:"):
            originals[resource] = text_variant(resource)
    originals["bcs:asysta01"] = mode2_tests.script_fixture("asysta01")
    originals["bcs:ddwell"] = mode2_tests.script_fixture("ddwell")
    hashes = {resource.split(":")[1]: sha(data) for resource, data in originals.items()}
    patched = {}
    with patch.dict(mode2_rewards.ORIGINAL_HASHES, hashes), \
            patch.dict(mode2_rewards.PATCHED_HASHES, {name: "" for name in mode2_rewards.PATCHED_HASHES}):
        for resource, data in originals.items():
            if resource not in mode2_rewards.TEXT_REFS:
                patched[resource.split(":")[1]] = sha(mode2_rewards.patch_reward(resource, data))
    return originals, {"mode2_rewards.ORIGINAL_HASHES": hashes, "mode2_rewards.PATCHED_HASHES": patched}


def registry_cases(s: Scenario) -> None:
    for table in ("REWARDS", "DIRECT_REWARDS", "TRANSFER_SPECS", "PHYSICAL_SOURCE_REWARDS", "RESOURCE_REWARDS",
                  "SOURCE_REWARDS", "TARGET_REWARDS", "TEXT_REFS", "ACTION_TEXT_REFS", "ADAPTER_ID"):
        s.case(table, f"mode2_rewards.{table}", {}, lambda table=table: getattr(mode2_rewards, table))
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


def grant_cases(s: Scenario) -> None:
    spec = mode2_rewards.resource_specs("dlg:ppstat01")[0]
    transfer = mode2_rewards.resource_specs("dlg:loutcapt")[0]
    for label, actions, which in (
            ("create", b'X()\r\r\nGiveItemCreate("ring31",LastTalkedToBy,1,1,1)\r\r\nY()', spec),
            ("upper", b'givEITEMcreate("RING31",lasttalkedtoby,1,1,1)\r\n', spec),
            ("give", b'GiveItem("ring31",LastTalkedToBy)', spec),
            ("both", b'GiveItem("ring31",LastTalkedToBy)\nGiveItemCreate("ring31",LastTalkedToBy,1,1,1)\n', spec),
            ("indented", b' GiveItemCreate("ring31",LastTalkedToBy,1,1,1)\n', spec),
            ("trailing text", b'GiveItemCreate("ring31",LastTalkedToBy,1,1,1) \n', spec),
            ("three cr", b'GiveItemCreate("ring31",LastTalkedToBy,1,1,1)\r\r\r\n', spec),
            ("cr end", b'GiveItemCreate("ring31",LastTalkedToBy,1,1,1)\r\r', spec),
            ("transfer create", b'GiveItemCreate("dagg11",LastTalkedToBy,0,0,0)\n', transfer),
            ("transfer give", b'A\nGiveItem("dagg11",LastTalkedToBy)\r\nB', transfer),
            ("none", b'Nothing()', spec)):
        s.case(f"dialogue_grant {label}", "mode2_rewards._dialogue_grant", {"actions": actions, "spec": which},
               lambda actions=actions, which=which: mode2_rewards._dialogue_grant(actions, which))
    odd = dict(spec, payload={"resref": "x", "charges": [1, "a", None, 2.5]}, recipient=None)
    s.case("dialogue_grant odd values", "mode2_rewards._dialogue_grant",
           {"actions": b'GiveItemCreate("ring31",None,1,a,None,2.5)', "spec": odd},
           lambda: mode2_rewards._dialogue_grant(b'GiveItemCreate("ring31",None,1,a,None,2.5)', odd))


def patch_dialogue_cases(s: Scenario, allocated: dict) -> None:
    for resource in mode2_rewards.RESOURCE_REWARDS:
        if not resource.startswith("dlg:"):
            continue
        specs = mode2_rewards.resource_specs(resource)
        original = mode2_tests.dialogue_fixture(specs)
        action_refs = ({old: allocated[resource][old] for old in mode2_rewards.ACTION_TEXT_REFS[resource]}
                       if resource in mode2_rewards.ACTION_TEXT_REFS else None)
        for defer, numeric in ((True, True), (True, False), (False, False)):
            args = {"data": original, "specs": specs, "action_refs": action_refs,
                    "defer_departure": defer, "numeric_target": numeric}
            s.case(f"patch_dialogue {resource} {defer} {numeric}", "mode2_rewards._patch_dialogue", args,
                   lambda args=args: mode2_rewards._patch_dialogue(
                       args["data"], args["specs"], action_refs=args["action_refs"],
                       defer_departure=args["defer_departure"], numeric_target=args["numeric_target"]))
    spec = mode2_rewards.resource_specs("dlg:trgeni01")[0]
    original = mode2_tests.dialogue_fixture(spec)
    for target_ids in (("bg1:mode2:target:32",), ("bg2:mode1:target:32",), ("bg2:mode2:target:32extra",),
                       ("bg2:mode2:target:32", "bg2:mode2:target:42"), ("bg2:mode2:target:032",), (),
                       ("bg2:mode2:target:9",), (32,)):
        invalid = dict(spec, target_ids=target_ids)
        s.case(f"patch_dialogue target {target_ids}", "mode2_rewards._patch_dialogue",
               {"data": original, "specs": [invalid], "action_refs": None, "defer_departure": True, "numeric_target": True},
               lambda invalid=invalid: mode2_rewards._patch_dialogue(original, (invalid,)))
    amcler = mode2_rewards.resource_specs("dlg:amcler01")
    amcler_original = mode2_tests.dialogue_fixture(amcler)
    for label, refs in (("missing display", {74025: 5, 1: 2}), ("two refs", {74025: 5})):
        s.case(f"patch_dialogue amcler {label}", "mode2_rewards._patch_dialogue",
               {"data": amcler_original, "specs": amcler, "action_refs": refs, "defer_departure": True,
                "numeric_target": True},
               lambda refs=refs: mode2_rewards._patch_dialogue(amcler_original, amcler, action_refs=refs))
    other = mode2_tests.dialogue_fixture(mode2_rewards.resource_specs("dlg:uhogre01"))
    ogre = mode2_rewards.resource_specs("dlg:mgkhol01")
    s.case("patch_dialogue branches changed", "mode2_rewards._patch_dialogue",
           {"data": other, "specs": ogre, "action_refs": None, "defer_departure": True, "numeric_target": True},
           lambda: mode2_rewards._patch_dialogue(other, ogre))
    no_actions = quest_options._Dialogue(mode2_tests.dialogue_fixture(ogre), "mgkhol01")
    first = struct.unpack_from("<I", no_actions.states[4], 4)[0]
    struct.pack_into("<I", no_actions.transitions[first], 0, 3)
    data = no_actions.render()
    s.case("patch_dialogue lost actions", "mode2_rewards._patch_dialogue",
           {"data": data, "specs": ogre, "action_refs": None, "defer_departure": True, "numeric_target": True},
           lambda: mode2_rewards._patch_dialogue(data, ogre))
    duel = quest_options._Dialogue(mode2_tests.dialogue_fixture(mode2_rewards.resource_specs("dlg:udlesa")), "udlesa")
    for state in range(6, 12):
        action_id = struct.unpack_from("<I", mode2_tests.outgoing(duel, state)[0], 16)[0]
        following = (f'SetGlobal("WaitingToDuel","GLOBAL",2)\r\nSetGlobal("DuelStarts","AR2202",3)\r\n'
                     f'ActionOverride(Player{state - 5},JumpToPoint([608.1123]))\r\n').encode()
        duel.actions[action_id] = duel.actions[action_id].replace(b"Wait(1)", following + b"Wait(1)")
    data = duel.render()
    udlesa = mode2_rewards.resource_specs("dlg:udlesa")
    s.case("patch_dialogue udlesa duel", "mode2_rewards._patch_dialogue",
           {"data": data, "specs": udlesa, "action_refs": None, "defer_departure": True, "numeric_target": True},
           lambda: mode2_rewards._patch_dialogue(data, udlesa))


def mode2_scenario() -> Scenario:
    originals, pins = mode2_inputs()
    s = Scenario("mode2_rewards", pins)
    registry_cases(s)
    grant_cases(s)
    text_resources = [resource for resource in mode2_rewards.TEXT_REFS if resource.startswith("dlg:")]
    base_tlk = make_tlk(["Unchanged text"])
    tlk, allocated = mode2_rewards.prepare_reward_texts(base_tlk, text_resources)
    patch_dialogue_cases(s, allocated)
    for label, resources, language, source in (
            ("dialogues", text_resources, "en_us", base_tlk), ("dialogues again", text_resources, "EN_US", tlk),
            ("all", list(mode2_rewards.RESOURCE_REWARDS) + ["DLG:PPDJINN"], "en_US", make_tlk([])),
            ("none", ["dlg:uhogre01"], "en_us", base_tlk), ("empty", [], "en_us", base_tlk),
            ("german", text_resources, "de_DE", base_tlk), ("english word", text_resources, "english", base_tlk),
            ("bad tlk", text_resources, "en_us", b"TLK V1  "), ("bad tlk none", [], "en_us", b"bad")):
        s.case(f"prepare_reward_texts {label}", "mode2_rewards.prepare_reward_texts",
               {"tlk": source, "resources": resources, "language": language},
               lambda resources=resources, language=language, source=source: (
                   lambda result: (result[0], {key: sorted_ints(value) for key, value in result[1].items()}))(
                   mode2_rewards.prepare_reward_texts(source, resources, language)))
    ddwell_tlk = make_tlk(["Existing unrelated text", "Existing helmet narrative"])
    updated, ref = mode2_rewards.prepare_ddwell_text(ddwell_tlk)
    for label, source, language in (("fresh", ddwell_tlk, "en_us"), ("again", updated, "en_us"),
                                    ("german", ddwell_tlk, "de_de")):
        s.case(f"prepare_ddwell_text {label}", "mode2_rewards.prepare_ddwell_text",
               {"tlk": source, "language": language},
               lambda source=source, language=language: mode2_rewards.prepare_ddwell_text(source, language))
    with s.overrides():
        results = {}
        for resource, data in originals.items():
            name = resource.split(":")[1]
            if resource in mode2_rewards.TEXT_REFS and resource.startswith("dlg:"):
                results[resource] = mode2_rewards.patch_reward(resource, data, refs=allocated[resource])
            else:
                results[resource] = mode2_rewards.patch_reward(resource, data, ddwell_ref=100001)
    for resource, data in originals.items():
        result = results[resource]
        refs = allocated.get(resource)
        name = resource.split(":")[1]
        args = {"resource": resource, "data": data, "refs": refs, "ddwell_ref": 100001 if name == "ddwell" else None}
        s.case(f"patch_reward {resource}", "mode2_rewards.patch_reward", args,
               lambda args=args: mode2_rewards.patch_reward(args["resource"], args["data"], refs=args["refs"],
                                                            ddwell_ref=args["ddwell_ref"]))
        for label, value in (("result", result), ("tampered", result[:-1] + b"tampered"),
                             ("damaged", tampered(result, 100))):
            args = {"resource": resource, "data": value, "refs": None, "ddwell_ref": None}
            s.case(f"patch_reward {label} {resource}", "mode2_rewards.patch_reward", args,
                   lambda resource=resource, value=value: mode2_rewards.patch_reward(resource, value))
            s.case(f"original_reward {label} {resource}", "mode2_rewards.original_reward",
                   {"resource": resource, "data": value},
                   lambda resource=resource, value=value: mode2_rewards.original_reward(resource, value))
        s.case(f"original_reward original {resource}", "mode2_rewards.original_reward",
               {"resource": resource, "data": data},
               lambda resource=resource, data=data: mode2_rewards.original_reward(resource, data))
        if refs:
            moved = {old: new + 1000 for old, new in refs.items()}
            with s.overrides():
                revised = mode2_rewards.patch_reward(resource, result, refs=moved)
            s.case(f"patch_reward moved {resource}", "mode2_rewards.patch_reward",
                   {"resource": resource, "data": result, "refs": moved, "ddwell_ref": None},
                   lambda resource=resource, result=result, moved=moved:
                   mode2_rewards.patch_reward(resource, result, refs=moved))
            s.case(f"original_reward moved {resource}", "mode2_rewards.original_reward",
                   {"resource": resource, "data": revised},
                   lambda resource=resource, revised=revised: mode2_rewards.original_reward(resource, revised))
            s.case(f"patch_reward unallocated {resource}", "mode2_rewards.patch_reward",
                   {"resource": resource, "data": data, "refs": None, "ddwell_ref": None},
                   lambda resource=resource, data=data: mode2_rewards.patch_reward(resource, data))
            for label, bad in (("extra", {**refs, 1: 2}), ("sentinel", {key: 0xFFFFFFFF for key in refs}),
                               ("negative", {key: -1 for key in refs}), ("empty", {})):
                s.case(f"patch_reward refs {label} {resource}", "mode2_rewards.patch_reward",
                       {"resource": resource, "data": data, "refs": bad, "ddwell_ref": None},
                       lambda resource=resource, data=data, bad=bad: mode2_rewards.patch_reward(resource, data, refs=bad))
            if mode2_rewards.resource_specs(resource)[0].get("inventory_target"):
                with s.overrides():
                    legacy = mode2_rewards._patch_text_dialogue(data, resource, refs, numeric_target=False)
                s.case(f"original_reward legacy {resource}", "mode2_rewards.original_reward",
                       {"resource": resource, "data": legacy},
                       lambda resource=resource, legacy=legacy: mode2_rewards.original_reward(resource, legacy))
                s.case(f"patch_reward legacy {resource}", "mode2_rewards.patch_reward",
                       {"resource": resource, "data": legacy, "refs": None, "ddwell_ref": None},
                       lambda resource=resource, legacy=legacy: mode2_rewards.patch_reward(resource, legacy))
            for defer, numeric in ((True, True), (True, False), (False, True)):
                args = {"data": data, "resource": resource, "refs": refs, "defer_departure": defer,
                        "numeric_target": numeric}
                s.case(f"patch_text_dialogue {resource} {defer} {numeric}", "mode2_rewards._patch_text_dialogue", args,
                       lambda args=args: mode2_rewards._patch_text_dialogue(
                           args["data"], args["resource"], args["refs"], defer_departure=args["defer_departure"],
                           numeric_target=args["numeric_target"]))
            s.case(f"original_text_dialogue {resource}", "mode2_rewards._original_text_dialogue",
                   {"data": result, "resource": resource},
                   lambda resource=resource, result=result: mode2_rewards._original_text_dialogue(result, resource))
    ddwell, ddwell_result = originals["bcs:ddwell"], results["bcs:ddwell"]
    for label, args in (
            ("ref", {"data": ddwell, "refs": None, "ddwell_ref": 7}),
            ("refs", {"data": ddwell, "refs": {40850: 8}, "ddwell_ref": None}),
            ("refs override", {"data": ddwell, "refs": {40850: 8}, "ddwell_ref": 9}),
            ("bad refs", {"data": ddwell, "refs": {40850: 8, 1: 1}, "ddwell_ref": None}),
            ("empty refs", {"data": ddwell, "refs": {}, "ddwell_ref": None}),
            ("unallocated", {"data": ddwell, "refs": None, "ddwell_ref": None}),
            ("sentinel", {"data": ddwell, "refs": None, "ddwell_ref": 0xFFFFFFFF}),
            ("negative", {"data": ddwell, "refs": None, "ddwell_ref": -2}),
            ("moved", {"data": ddwell_result, "refs": None, "ddwell_ref": 100002}),
            ("gone", {"data": ddwell_result.replace(b"AR1202DragonWell", b"AR1202DragonGone", 1), "refs": None,
                      "ddwell_ref": None}),
            ("crlf", {"data": crlf(ddwell), "refs": None, "ddwell_ref": 3})):
        s.case(f"patch_reward ddwell {label}", "mode2_rewards.patch_reward", {"resource": "BCS:DDWELL", **args},
               lambda args=args: mode2_rewards.patch_reward("BCS:DDWELL", args["data"], refs=args["refs"],
                                                            ddwell_ref=args["ddwell_ref"]))
    for label, data in (("result", ddwell_result), ("original", ddwell), ("short", ddwell_result[:40])):
        s.case(f"original_ddwell {label}", "mode2_rewards._original_ddwell", {"data": data},
               lambda data=data: mode2_rewards._original_ddwell(data))
    for resource in ("uhogre01.dlg", "UHOGRE01", "override/uhogre01.dlg", "dlg:aran.dlg", "x:y:ddwell", "nothing"):
        data = originals["dlg:uhogre01"] if "ogre" in resource or "aran" in resource else ddwell
        s.case(f"original_reward key {resource}", "mode2_rewards.original_reward",
               {"resource": resource, "data": data},
               lambda resource=resource, data=data: mode2_rewards.original_reward(resource, data))
    return s


def mode2_trgeni_scenario() -> Scenario:
    original = trgeni_terminal()
    s = Scenario("mode2_rewards_trgeni", {"mode2_rewards.ORIGINAL_HASHES": {"trgeni01": sha(original)}})
    resource, refs = "dlg:trgeni01", {43514: 100001}
    with s.overrides():
        legacy = mode2_rewards._patch_text_dialogue(original, resource, refs, defer_departure=False,
                                                    numeric_target=False)
        previous = mode2_rewards._patch_text_dialogue(original, resource, refs, numeric_target=False)
        result = mode2_rewards.patch_reward(resource, legacy)
    for label, data in (("legacy", legacy), ("previous", previous), ("result", result),
                        ("tampered legacy", legacy.replace(b"FLPR_Transfer(", b"FLPR_Tampered(", 1))):
        s.case(f"patch_reward {label}", "mode2_rewards.patch_reward",
               {"resource": resource, "data": data, "refs": None, "ddwell_ref": None},
               lambda data=data: mode2_rewards.patch_reward(resource, data))
        s.case(f"original_reward {label}", "mode2_rewards.original_reward", {"resource": resource, "data": data},
               lambda data=data: mode2_rewards.original_reward(resource, data))
    s.case("patch_reward original", "mode2_rewards.patch_reward",
           {"resource": resource, "data": original, "refs": refs, "ddwell_ref": None},
           lambda: mode2_rewards.patch_reward(resource, original, refs=refs))
    for defer, numeric in ((True, True), (False, False), (True, False), (False, True)):
        args = {"data": original, "resource": resource, "refs": refs, "defer_departure": defer,
                "numeric_target": numeric}
        s.case(f"patch_text_dialogue {defer} {numeric}", "mode2_rewards._patch_text_dialogue", args,
               lambda args=args: mode2_rewards._patch_text_dialogue(
                   original, resource, refs, defer_departure=args["defer_departure"],
                   numeric_target=args["numeric_target"]))
    spec = mode2_rewards.resource_specs(resource)[0]
    s.case("patch_dialogue terminal", "mode2_rewards._patch_dialogue",
           {"data": original, "specs": [spec], "action_refs": None, "defer_departure": True, "numeric_target": True},
           lambda: mode2_rewards._patch_dialogue(original, (spec,)))
    # A terminal state with two bare departures, and one without any.
    for label, replacement in (("double", b"DestroySelf()\r\r\nDestroySelf()"), ("none", b"Wait(2)")):
        dialogue = quest_options._Dialogue(original, "trgeni01")
        action_id = struct.unpack_from("<I", mode2_tests.outgoing(dialogue, 37)[0], 16)[0]
        actions = dialogue.actions[action_id]
        index = actions.rindex(b"DestroySelf()")
        dialogue.actions[action_id] = actions[:index] + replacement + actions[index + len(b"DestroySelf()"):]
        data = dialogue.render()
        s.case(f"patch_dialogue departure {label}", "mode2_rewards._patch_dialogue",
               {"data": data, "specs": [spec], "action_refs": None, "defer_departure": True, "numeric_target": True},
               lambda data=data: mode2_rewards._patch_dialogue(data, (spec,)))
    return s


def mode2_reviewed_scenario() -> Scenario:
    original = mode2_tests.dialogue_fixture(mode2_rewards.resource_specs("dlg:uhogre01"))
    script = mode2_tests.script_fixture("asysta01")
    s = Scenario("mode2_rewards_reviewed", {
        "mode2_rewards.ORIGINAL_HASHES": {"uhogre01": sha(original), "asysta01": sha(script), "newone": "0" * 64},
        "mode2_rewards.PATCHED_HASHES": {"uhogre01": "0" * 64, "asysta01": "1" * 64, "ddwell": ""}})
    for resource, data in (("dlg:uhogre01", original), ("bcs:asysta01", script),
                           ("bcs:asysta01", crlf(script)), ("dlg:mgkhol01", original)):
        s.case(f"patch_reward {resource} {len(data)}", "mode2_rewards.patch_reward",
               {"resource": resource, "data": data, "refs": None, "ddwell_ref": None},
               lambda resource=resource, data=data: mode2_rewards.patch_reward(resource, data))
        s.case(f"original_reward {resource} {len(data)}", "mode2_rewards.original_reward",
               {"resource": resource, "data": data},
               lambda resource=resource, data=data: mode2_rewards.original_reward(resource, data))
    return s


# ---------------------------------------------------------------- containers


def container_inputs(line_endings: str) -> dict:
    originals = {}
    for resource in container_rewards.SITES:
        if resource.startswith("bcs:"):
            originals[resource] = container_tests.script_fixture(resource)
            if line_endings == "crlf":
                originals[resource] = crlf(originals[resource])
        elif line_endings == "lf":
            originals[resource] = container_tests.dialogue_fixture(resource)
    return originals


def container_scenario(name: str, line_endings: str) -> Scenario:
    originals = container_inputs(line_endings)
    s = Scenario(name, {"container_rewards.ORIGINAL_HASHES": {key: sha(value) for key, value in originals.items()}})
    if line_endings == "lf":
        s.case("TARGETS", "container_rewards.TARGETS", {}, lambda: container_rewards.TARGETS)
        s.case("RESOURCE_TARGETS", "container_rewards.RESOURCE_TARGETS", {}, lambda: container_rewards.RESOURCE_TARGETS)
        for identity in list(container_rewards.TARGETS) + ["bg2:mode2:target:999"]:
            s.case(f"target_spec {identity}", "container_rewards.target_spec", {"target_id": identity},
                   lambda identity=identity: container_rewards.target_spec(identity))
        base = make_tlk(["Preserve existing text"])
        tlk, _ = container_rewards.prepare_container_texts(base, container_rewards.TEXT_RESOURCES)
        for label, source, resources, language in (
                ("all", base, sorted(container_rewards.TEXT_RESOURCES), "en_us"),
                ("again", tlk, ["bcs:kpgol05", "bcs:kpgol01"], "EN_US"),
                ("non-text", base, ["bcs:kpgol03", "dlg:samia"], "de_DE"),
                ("uppercase", base, ["BCS:KPGOL01"], "de_DE"), ("german", base, ["bcs:kpgol02"], "de_DE"),
                ("bad tlk", b"no tlk", ["bcs:kpgol02"], "en_us"), ("bad tlk none", b"no tlk", [], "en_us")):
            s.case(f"prepare_container_texts {label}", "container_rewards.prepare_container_texts",
                   {"tlk": source, "resources": resources, "language": language},
                   lambda source=source, resources=resources, language=language:
                   container_rewards.prepare_container_texts(source, resources, language))
        for args in ({"opcode": 16450, "value": 0, "negate": 1, "text": "BOOK03", "selector": 0, "newline": b"\n"},
                     {"opcode": 16521, "value": 2, "negate": 0, "text": "", "selector": 3, "newline": b"\r\n"}):
            s.case(f"trigger {args['opcode']}", "container_rewards._trigger", args,
                   lambda args=args: container_rewards._trigger(**args))
    for resource, original in originals.items():
        with s.overrides():
            result = container_rewards.patch_container_resource(resource, original, refs={38758: 100001})
        cases = [("patch", original, {38758: 100001}), ("reexport", result, None), ("moved", result, {38758: 7}),
                 ("empty refs", result, {}), ("other refs", result, {1: 2}),
                 ("unallocated", original, None), ("sentinel", original, {38758: 0xFFFFFFFF}),
                 ("flag", result.replace(b"FLPR_C", b"FLPR_Z", 1), None), ("appended", result + b"tamper", None),
                 ("upper", original, {38758: 5})]
        for label, data, refs in cases:
            name = resource.upper() if label == "upper" else resource
            s.case(f"patch_container_resource {label} {resource}", "container_rewards.patch_container_resource",
                   {"resource": name, "data": data, "refs": refs},
                   lambda name=name, data=data, refs=refs: container_rewards.patch_container_resource(name, data, refs=refs))
        for label, data in (("result", result), ("original", original), ("appended", result + b"x"),
                            ("short", result[:30])):
            s.case(f"original_container_resource {label} {resource}", "container_rewards.original_container_resource",
                   {"resource": resource, "data": data},
                   lambda resource=resource, data=data: container_rewards.original_container_resource(resource, data))
    s.case("patch_container_resource unknown", "container_rewards.patch_container_resource",
           {"resource": "bcs:nothing", "data": b"", "refs": None},
           lambda: container_rewards.patch_container_resource("bcs:nothing", b""))
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


def shop_world(records, *, sha256="1" * 64, extra_sources=(), resource_id="sto:udduer01"):
    store = parse_sto(bytes(make_sto(records)))
    sources = [{"id": f"sto:udduer01/item/{row['record_index']}", "resource": "sto:udduer01", "item": row["resref"],
                "record": row} for row in store["items"]]
    return {"resources": [{"id": "are:other", "sha256": "2" * 64}, {"id": resource_id, "sha256": sha256}],
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
        "missing key": {"resources": [{"id": "sto:udduer01", "sha256": "1" * 64}],
                        "sources": [{"id": "x", "resource": "sto:udduer01", "record": {"record_index": 0}}]},
        "no sources": {"resources": [{"id": "sto:udduer01", "sha256": "1" * 64}]},
        "quantity float": shop_world([(item_record("scrl8x"), 1, 0)]),
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
    tlk = make_tlk(["Existing text"])
    _, _, prepared = cursed_disguises.prepare_cursed_disguises(spec, data, tlk)
    for label, value, resources, source, language in (
            ("all", spec, data, tlk, "en_us"), ("again", spec, data, prepared, "EN_GB"),
            ("revisions", revisions, data, make_tlk([]), "english"), ("floats", floats, data, tlk, "en_us"),
            ("german", spec, data, tlk, "de_de"), ("changed", spec, dict(data, brac06=data["brac06"] + b"x"), tlk, "en_us"),
            ("missing", spec, {k: v for k, v in data.items() if k != "ring31"}, tlk, "en_us"),
            ("bad tlk", spec, data, b"bad", "en_us")):
        s.case(f"prepare {label}", "cursed_disguises.prepare_cursed_disguises",
               {"spec": value, "resources": resources, "tlk": source, "language": language},
               lambda value=value, resources=resources, source=source, language=language:
               cursed_disguises.prepare_cursed_disguises(value, resources, source, language=language))
    return s


def main() -> None:
    scenarios = [
        quest_options_scenario("quest_options", "lf"),
        quest_options_scenario("quest_options_crlf", "crlf"),
        spell_scenario("spell_rewards", spell_tests.spell_fixture()),
        spell_scenario("spell_rewards_layout", spell_tests.spell_fixture()[:-1]),
        spell_scenario("spell_rewards_effect", spell_tests.spell_fixture().replace(b"ring41", b"ring42")),
        spell_scenario("spell_rewards_reviewed", spell_tests.spell_fixture(), patched_hash="0" * 64),
        mode2_scenario(),
        mode2_trgeni_scenario(),
        mode2_reviewed_scenario(),
        container_scenario("container_rewards", "lf"),
        container_scenario("container_rewards_crlf", "crlf"),
        *inventory_scenarios(),
        original_options_scenario(),
        cursed_scenario(),
    ]
    for scenario in scenarios:
        scenario.write()


if __name__ == "__main__":
    main()
