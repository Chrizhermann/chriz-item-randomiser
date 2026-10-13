"""Container quest latches retain both ordinary and randomized campaign routes."""
import hashlib
from itertools import product
from pathlib import Path
import re
import struct
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from flir_config import container_rewards as rewards
from flir_config.profiles import ProfileError
from flir_config.quest_options import _Dialogue
from flir_config.quest_rewards import _blocks, _global_guard, _newline
from flir_config.resources import _Tlk
from test_postinstall_resources import make_tlk


def compiled_conditions(data, globals_, items):
    rows = re.findall(rb'TR\n(\d+) (\d+) (\d+) 0 0 "([^"]*)" "" OB\n[^\n]*\nTR\n', data)
    values = []
    for opcode, value, negate, name in rows:
        opcode, value, negate = int(opcode), int(value), int(negate)
        if opcode == 16521:
            values.append(value)
        elif opcode == 16399:
            result = globals_.get(name.decode(), 0) == value
            values.append(not result if negate else result)
        elif opcode in (16450, 16501):
            result = name.decode().lower() in items
            values.append(not result if negate else result)
        else:
            values.append(True)  # Independently assert unrelated triggers are unchanged.
    return evaluate_groups(values)


def text_conditions(data, globals_, items):
    values = []
    for line in data.decode().splitlines():
        line = line.strip()
        if line.startswith("OR("):
            values.append(int(line[3:-1]))
        elif "PartyHasItem(" in line:
            result = re.search(r'"([^"]+)"', line)[1].lower() in items
            values.append(not result if line.startswith("!") else result)
        elif line.startswith(("Global(", "!Global(")):
            name, scope, value = re.search(r'"([^"]+)","([^"]+)",(\d+)', line).groups()
            result = globals_.get(scope + name, 0) == int(value)
            values.append(not result if line.startswith("!") else result)
        else:
            values.append(True)
    return evaluate_groups(values)


def evaluate_groups(values):
    result, index = True, 0
    while index < len(values):
        value = values[index]
        if type(value) is int:
            result = result and any(values[index + 1:index + value + 1])
            index += value + 1
        else:
            result = result and value
            index += 1
    return result


def action(opcode, value=0):
    obj = '0 0 0 0 0 0 0 0 0 0 0 0 ""OB\n'
    return (f'AC\n{opcode}OB\n{obj}OB\n{obj}OB\n{obj}{value} 0 0 0 0"" "" AC\n').encode()


def script_fixture(resource):
    spec = rewards.TARGETS[rewards.RESOURCE_TARGETS[resource][0]]
    count, sites = rewards.SITES[resource]
    blocks = []
    for index in range(count):
        condition = rewards._trigger(16399, 4, text="GLOBALUnrelated")
        if index in sites:
            if spec["activation_global"] == "FLPR_CSAMIA":
                condition += rewards._trigger(16521, 2) + rewards._trigger(16450, text="sw1h32") + rewards._trigger(16450, text="shld21")
            elif resource == "bcs:ppchest":
                condition += rewards._trigger(16501, negate=1, text="bow19b", selector=1)
                condition += rewards._trigger(16399, text="GLOBALRobbedGolem")
            else:
                negative = resource == "bcs:safana" and index != 9
                condition += rewards._trigger(16450, negate=int(negative), text=spec["original_item"].upper())
        actions = action(10)
        if resource in rewards.TEXT_RESOURCES:
            actions = action(151, 38758) + actions
        blocks.append(b"CR\nCO\n" + condition + b"CO\nRS\nRE\n100" + actions + b"RE\nRS\nCR\n")
    return b"SC\n" + b"".join(blocks) + b"SC\n"


def dialogue_fixture(resource):
    header = bytearray(b"DLG V1.0" + bytes(44))
    struct.pack_into("<10I", header, 8, 0, 52, 0, 52, 52, 0, 52, 0, 52, 0)
    dialogue = _Dialogue(bytes(header), resource[4:])
    for index in range(9):
        condition = b'Global("Unrelated","GLOBAL",4)\r\n'
        if index in rewards.SITES[resource][1]:
            if resource == "dlg:samia":
                condition += b'  OR(2)\r\nPartyHasItem("sw1h32")\r\nPartyHasItem("shld21")\r\n'
            else:
                condition += (b'!' if index != 2 else b'') + b'PartyHasItem("BOOK03")\r\n'
        dialogue.state_triggers.append(condition)
        state = dialogue.state(1234, [dialogue.transition(None, action='SetGlobal("QuestProgress","GLOBAL",2)', journal=9876)])
        struct.pack_into("<I", dialogue.states[state], 12, index)
    return dialogue.render()


class ContainerRewardTests(unittest.TestCase):
    def patched(self, resource, original):
        with patch.dict(rewards.ORIGINAL_HASHES, {resource: hashlib.sha256(original).hexdigest()}):
            result = rewards.patch_container_resource(resource, original, refs={38758: 100001})
            self.assertEqual(rewards.patch_container_resource(resource, result), result)
            self.assertEqual(rewards.original_container_resource(resource, result), original)
            with self.assertRaises(ProfileError):
                rewards.patch_container_resource(resource, result.replace(b"FLPR_C", b"FLPR_Z", 1))
            with self.assertRaises(ProfileError):
                rewards.patch_container_resource(resource, result + b"tamper")
        return result

    def check_script(self, resource, original, result):
        count, sites = rewards.SITES[resource]
        old = _blocks(original, count, _newline(original))[0]
        new = _blocks(result, count + (0 if resource == "bcs:ppchest" else len(sites)), _newline(result))[0]
        spec = rewards.TARGETS[rewards.RESOURCE_TARGETS[resource][0]]
        cursor = 0
        for index, before in enumerate(old):
            if index not in sites:
                self.assertEqual(new[cursor], before)
                cursor += 1
                continue
            selected = None
            if resource != "bcs:ppchest":
                selected = new[cursor]; cursor += 1
                response = selected.split(b"CO\n")[2]
                if resource in rewards.TEXT_RESOURCES:
                    response = response.replace(b'100001 0 0 0 0""', b'38758 0 0 0 0""')
                self.assertEqual(response, before.split(b"CO\n")[2])
            excluded = new[cursor]; cursor += 1
            self.assertEqual(excluded.replace(_global_guard(spec["activation_global"], False, b"\n"), b""), before)
            for active, taken, owns in product((False, True), repeat=3):
                state = {"GLOBALUnrelated": 4, "GLOBAL" + spec["activation_global"]: int(active),
                         spec["latch_scope"] + spec["latch_global"]: int(taken)}
                # Satisfy original independent Samia progression requirements.
                if resource == "bcs:samia":
                    state["AR1202SpawnSamia"] = 1
                if resource == "bcs:safana":
                    state["GLOBALPirateTreasure"] = 1 if index == 8 else 0
                items = {spec["original_item"]} if owns else set()
                positive = not (resource == "bcs:safana" and index in (7, 8))
                if selected is not None:
                    self.assertEqual(compiled_conditions(selected.split(b"CO\n")[1], state, items), active and (taken == positive))
                self.assertEqual(compiled_conditions(excluded.split(b"CO\n")[1], state, items),
                                 not active and compiled_conditions(before.split(b"CO\n")[1], state, items))

    def test_compiled_routes_preserve_actions_and_ignore_later_item_loss(self):
        for resource in rewards.SITES:
            if resource.startswith("bcs:"):
                with self.subTest(resource=resource):
                    original = script_fixture(resource)
                    self.check_script(resource, original, self.patched(resource, original))

    def check_dialogue(self, resource, original, result):
        before, after = _Dialogue(original, resource[4:]), _Dialogue(result, resource[4:])
        self.assertEqual(before.states, after.states)
        self.assertEqual(before.transitions, after.transitions)
        self.assertEqual(before.actions, after.actions)
        self.assertEqual(before.triggers, after.triggers)
        spec = rewards.TARGETS[rewards.RESOURCE_TARGETS[resource][0]]
        for index, condition in enumerate(before.state_triggers):
            if index not in rewards.SITES[resource][1]:
                self.assertEqual(after.state_triggers[index], condition)
                continue
            for active, taken, owns in product((False, True), repeat=3):
                state = {"GLOBALUnrelated": 4, "GLOBAL" + spec["activation_global"]: int(active),
                         spec["latch_scope"] + spec["latch_global"]: int(taken)}
                if resource == "dlg:safanj":
                    state["GLOBALPirateTreasure"] = 1 if index == 8 else 0
                items = {spec["original_item"]} if owns else set()
                positive = resource == "dlg:samia" or index == 2
                expected = taken == positive if active else text_conditions(condition, state, items)
                self.assertEqual(text_conditions(after.state_triggers[index], state, items), expected)

    def test_dialogue_truth_table_and_quest_journal_actions_are_preserved(self):
        for resource in ("dlg:samia", "dlg:safanj"):
            original = dialogue_fixture(resource)
            self.check_dialogue(resource, original, self.patched(resource, original))

    def test_exact_target_selectors_shared_samia_latch_and_golem_text(self):
        self.assertEqual(rewards.target_spec("bg2:mode2:target:13")["physical_target_id"], "are:ar1303/container/0")
        one, two = (rewards.target_spec("bg2:mode2:target:" + row) for row in ("53", "28"))
        self.assertEqual((one["original_item"], two["original_item"]), ("shld21", "sw1h32"))
        self.assertEqual(one["physical_target_id"], "are:ar1202/container/8")
        for key in ("physical_target_id", "activation_global", "latch_global", "latch_scope"):
            self.assertEqual(one[key], two[key])
        self.assertEqual(rewards.target_spec("bg1:mode2:target:22")["area"], "bg3601")
        original = make_tlk(["Preserve existing text"])
        tlk, refs = rewards.prepare_container_texts(original, rewards.TEXT_RESOURCES)
        self.assertEqual(len(set(row[38758] for row in refs.values())), 1)
        self.assertEqual(_Tlk(tlk).text(refs["bcs:kpgol01"][38758], []), "As you pick up the item, two of the golems come to life and attack.")
        self.assertEqual(rewards.prepare_container_texts(tlk, rewards.TEXT_RESOURCES), (tlk, refs))

    def test_cached_original_scripts_and_dialogues(self):
        cache = ROOT / "work/original-preset-runtime-20260929/mode2-resources"
        if not cache.exists():
            self.skipTest("Optional isolated-lab resource cache is not present")
        for resource, digest in rewards.ORIGINAL_HASHES.items():
            kind, name = resource.split(":")
            original = (cache / f"{name}.{kind}").read_bytes()
            with self.subTest(resource=resource):
                self.assertEqual(hashlib.sha256(original).hexdigest(), digest)
                result = self.patched(resource, original)
                if kind == "bcs":
                    self.check_script(resource, original, result)
                else:
                    self.check_dialogue(resource, original, result)


if __name__ == "__main__":
    unittest.main()
