"""Container quest latches retain both ordinary and randomized campaign routes."""
import hashlib
from itertools import product
from pathlib import Path
import re
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from flir_config import container_rewards as rewards
from flir_config.profiles import ProfileError
from flir_config.quest_options import _Dialogue
from flir_config.quest_rewards import _blocks, _global_guard, _newline
from flir_config.resources import _Tlk
from test_postinstall_resources import make_tlk

# Where the reviewed EET resources test the treasure: (blocks, sites) of each
# script, (None, conditions) of each dialogue. The fixtures below copy them;
# the adapter finds the sites by meaning.
FIXTURE_SITES = {
    "bcs:kpgol01": (1, (0,)), "bcs:kpgol02": (1, (0,)), "bcs:kpgol03": (1, (0,)),
    "bcs:kpgol04": (1, (0,)), "bcs:kpgol05": (1, (0,)), "bcs:kpgol06": (1, (0,)),
    "bcs:ar1202": (24, (15, 16)), "bcs:samia": (1, (0,)), "bcs:safana": (16, (7, 8, 9)),
    "bcs:ppchest": (1, (0,)), "dlg:samia": (None, (1,)), "dlg:safanj": (None, (0, 2, 8)),
}
# The reviewed EET resources and their exact patches with the golem text
# appended to an empty dialog.tlk (regression evidence; nothing pins these).
REVIEWED_EET = {
    "bcs:ar1202": ("d6c3de8366f99088d41e1c01a4a8d8811add81771926d9d7d70ef5193fca2280",
                   "fbe5e57bf1fcabc548cdc6260a358ffe4d314859a4ee8ee048af5b269942463d"),
    "bcs:kpgol01": ("7e569c1e414c4afef74b274515d073c04dd92ba2fa0ab0f96b8bbccf2c80cfa6",
                    "b082abf5ec04f97c6a568e9e2f40fedec429ebe80526114c69819ff736cbd478"),
    "bcs:kpgol02": ("3dd4220a9bc7ae4b5778f3b6a1dc7438d943b138cb9383aac26d8835489a5431",
                    "3c7bbfef7fb61f6c782809adc9b02f2f8454ebaddf9575741fd6b15c98a4b13f"),
    "bcs:kpgol03": ("82e7a96eac8a9ad8b913982ca8d03bdc824346de42267d80065db5d3155077f0",
                    "7e8b982fb769aefc855ed9d7b7a7eeb47fdba1e7064a4273356765279f8edb48"),
    "bcs:kpgol04": ("d33714c44ebc89dd5c2fef834781cfbfe69f1f8cd0a9ff9de21fee823b4efc7d",
                    "b38a8656ccefc2a160de98c204ed91ba9747128c44072e5b499c6d7ef8fb78fe"),
    "bcs:kpgol05": ("d473eebb61a8a03d8bc0b1c993e0810c67a6e01225687b5cba035c6a5e067dde",
                    "f470815225c342cbf1032ac8bc930e61e6448af2e45e88d14e4c52fb9189f844"),
    "bcs:kpgol06": ("157cc4c98135416842176cb76968280630160156e4039ba0e42cf744d33d0a66",
                    "c15a788983784a852441eaf814333afd685a1a3fac4b07ba0cf7ea5f3272827c"),
    "bcs:ppchest": ("446b07d5de81386e5e7f6b49efb323c1d617222cc4714f1eae400e1c08c049d2",
                    "392a2634832669cdebbfde3bd65c1000c7f18c02adaa5fa539b9e8ac329ec6de"),
    "bcs:safana": ("afc23b13dfd4e87978997e55e35c6f4f4d14238647ef1197653184e55539bf83",
                   "c94870e206bfb3b9977e7de957731e6a7a7a32a552c1f6b39abbb38a1ac42376"),
    "bcs:samia": ("823d03307573daaa08ad7c853879e1b62796d67212b4e62e6d6ba66f0cac32c9",
                  "2a780431c511cb9fa4b729f95fc7f1d4d0f01ac9aebd72fdacb07dcb687c3ee4"),
    "dlg:safanj": ("8a890ecdc434267ed79661db132b140c1371a068de234d8caba541aa962da252",
                   "75051dcf0ba1fb37461ab814007279c8055622e9ed2004ec95692554fcde7ecc"),
    "dlg:samia": ("3572813dfb538c88953709b55e6bcd48139ae24c9a9b84e176f9920652a88254",
                  "27de3f4b6f9714547362bb5d9589c980ff1927648f5b17c21ebab2f1e0c6a400"),
}


def compiled_conditions(data, globals_, items):
    rows = re.findall(rb'TR\r?\n(\d+) (\d+) (\d+) 0 0 "([^"]*)" "" OB\r?\n[^\n]*\nTR\r?\n', data)
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
        elif line:
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


def action(opcode, value=0, text=""):
    obj = '0 0 0 0 0 0 0 0 0 0 0 0 ""OB\n'
    return (f'AC\n{opcode}OB\n{obj}OB\n{obj}OB\n{obj}{value} 0 0 0 0"{text}" "" AC\n').encode()


def site_condition(resource, index):
    spec = rewards.TARGETS[rewards.RESOURCE_TARGETS[resource][0]]
    if spec["activation_global"] == "FLPR_CSAMIA":
        return (rewards._trigger(16521, 2) + rewards._trigger(16450, text="sw1h32") +
                rewards._trigger(16450, text="shld21"))
    if resource == "bcs:ppchest":
        return (rewards._trigger(16501, negate=1, text="bow19b", selector=1) +
                rewards._trigger(16399, text="GLOBALRobbedGolem"))
    negative = resource == "bcs:safana" and index != 9
    return rewards._trigger(16450, negate=int(negative), text=spec["original_item"].upper())


def script_block(condition, actions):
    unrelated = rewards._trigger(16399, 4, text="GLOBALUnrelated")
    return b"CR\nCO\n" + unrelated + condition + b"CO\nRS\nRE\n100" + actions + b"RE\nRS\nCR\n"


def site_actions(resource):
    if resource == "bcs:ppchest":
        # PPCHEST sets the latch itself when the chest no longer holds the bow.
        return action(30, 1, "GLOBALRobbedGolem")
    return (action(151, 38758) if resource in rewards.TEXT_RESOURCES else b"") + action(10)


def script_fixture(resource):
    count, sites = FIXTURE_SITES[resource]
    blocks = [script_block(site_condition(resource, index), site_actions(resource)) if index in sites
              else script_block(b"", action(10)) for index in range(count)]
    return b"SC\n" + b"".join(blocks) + b"SC\n"


def dialogue_fixture(resource, conditions=None):
    """Nine states; <conditions> replaces the state conditions by index."""
    header = bytearray(b"DLG V1.0" + bytes(44))
    struct.pack_into("<10I", header, 8, 0, 52, 0, 52, 52, 0, 52, 0, 52, 0)
    dialogue = _Dialogue(bytes(header), resource[4:])
    for index in range(9):
        condition = b'Global("Unrelated","GLOBAL",4)\r\n'
        if index in FIXTURE_SITES[resource][1]:
            if resource == "dlg:samia":
                condition += b'  OR(2)\r\nPartyHasItem("sw1h32")\r\nPartyHasItem("shld21")\r\n'
            else:
                condition += (b'!' if index != 2 else b'') + b'PartyHasItem("BOOK03")\r\n'
        dialogue.state_triggers.append((conditions or {}).get(index, condition))
        state = dialogue.state(1234, [dialogue.transition(None, action='SetGlobal("QuestProgress","GLOBAL",2)', journal=9876)])
        struct.pack_into("<I", dialogue.states[state], 12, index)
    return dialogue.render()


class ContainerRewardTests(unittest.TestCase):
    def patched(self, resource, original, ref=100001):
        targets = set(rewards.RESOURCE_TARGETS[resource])
        result = rewards.patch_container_resource(resource, original, refs={38758: ref})
        self.assertEqual(rewards.patch_container_resource(resource, result), result)
        golem = ref if resource in rewards.TEXT_RESOURCES else None
        self.assertEqual(rewards._recover(resource, result), (original, golem, True))
        self.assertFalse(rewards.own_patch(resource, original))
        self.assertTrue(rewards.own_patch(resource, result))
        self.assertEqual(rewards.route_reasons(resource, original), {})
        self.assertEqual(rewards.route_reasons(resource, result), {})
        # Changes that keep our marker but not our patch: a selected copy where
        # the excluded one was, a changed recovery record, bytes after the end.
        if resource.startswith("bcs:"):
            flag, newline = rewards.TARGETS[next(iter(targets))]["activation_global"], _newline(result)
            changed = result.replace(_global_guard(flag, False, newline), _global_guard(flag, True, newline), 1)
        else:
            changed = result.replace(b"FLPR_C", b"FLPR_Z", 1)
        for tampered in (changed, result + b"tamper"):
            with self.assertRaisesRegex(ProfileError, "carries a changed copy of the container route's own patch"):
                rewards.patch_container_resource(resource, tampered)
            reasons = rewards.route_reasons(resource, tampered)
            self.assertEqual(set(reasons), targets)
            self.assertTrue(all("changed copy" in reason for reason in reasons.values()))
        return result

    def check_script(self, resource, original, result, sites=None, ref=100001):
        count, fixture_sites = FIXTURE_SITES[resource]
        sites = fixture_sites if sites is None else sites
        newline = _newline(original)
        count = len(_blocks(original, original.count(b"CR" + newline + b"CO" + newline), newline)[0])
        old = _blocks(original, count, newline)[0]
        new = _blocks(result, count + (0 if resource == "bcs:ppchest" else len(sites)), _newline(result))[0]
        spec = rewards.TARGETS[rewards.RESOURCE_TARGETS[resource][0]]
        cursor = 0
        for index, before in enumerate(old):
            if index not in sites:
                self.assertEqual(new[cursor], before)
                cursor += 1
                continue
            selected = None
            boundary = b"CO" + newline
            if resource != "bcs:ppchest":
                selected = new[cursor]; cursor += 1
                response = selected.split(boundary)[2]
                if resource in rewards.TEXT_RESOURCES:
                    response = response.replace(f'{ref} 0 0 0 0""'.encode(), b'38758 0 0 0 0""')
                self.assertEqual(response, before.split(boundary)[2])
            excluded = new[cursor]; cursor += 1
            self.assertEqual(excluded.replace(_global_guard(spec["activation_global"], False, newline), b""), before)
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
                    self.assertEqual(compiled_conditions(selected.split(boundary)[1], state, items),
                                     active and (taken == positive))
                self.assertEqual(compiled_conditions(excluded.split(boundary)[1], state, items),
                                 not active and compiled_conditions(before.split(boundary)[1], state, items))
        self.assertEqual(cursor, len(new))

    def test_compiled_routes_preserve_actions_and_ignore_later_item_loss(self):
        for resource in FIXTURE_SITES:
            if resource.startswith("bcs:"):
                with self.subTest(resource=resource):
                    original = script_fixture(resource)
                    self.check_script(resource, original, self.patched(resource, original))

    def test_script_sites_are_found_by_meaning_in_another_layout(self):
        # CRLF, two blocks another mod added before the golem block, the item
        # named in another letter case; Samia's two items in the other order.
        crlf = lambda data: data.replace(b"\n", b"\r\n")
        added = script_block(rewards._trigger(16399, 1, text="GLOBALOtherModGolems"), action(30, 2, "GLOBALOtherMod"))
        golem = script_block(rewards._trigger(16450, text="Ax1h13"), action(151, 38758) + action(10))
        original = crlf(b"SC\n" + added + added + golem + b"SC\n")
        result = self.patched("bcs:kpgol01", original)
        self.check_script("bcs:kpgol01", original, result, sites=(2,))
        samia = crlf(b"SC\n" + script_block(rewards._trigger(16521, 2) + rewards._trigger(16450, text="SHLD21") +
                                            rewards._trigger(16450, text="sw1h32"), action(10)) + b"SC\n")
        self.check_script("bcs:samia", samia, self.patched("bcs:samia", samia))

    def check_dialogue(self, resource, original, result, sites=None, reply_sites=()):
        sites = FIXTURE_SITES[resource][1] if sites is None else sites
        before, after = _Dialogue(original, resource[4:]), _Dialogue(result, resource[4:])
        self.assertEqual(before.states, after.states)
        self.assertEqual(before.transitions, after.transitions)
        self.assertEqual(before.actions, after.actions)
        spec = rewards.TARGETS[rewards.RESOURCE_TARGETS[resource][0]]
        for table, changed, places in ((before.state_triggers, after.state_triggers, sites),
                                       (before.triggers, after.triggers, reply_sites)):
            self.assertEqual(len(changed), len(table))
            for index, condition in enumerate(table):
                if index not in places:
                    self.assertEqual(changed[index], condition)
                    continue
                for active, taken, owns in product((False, True), repeat=3):
                    state = {"GLOBALUnrelated": 4, "GLOBAL" + spec["activation_global"]: int(active),
                             spec["latch_scope"] + spec["latch_global"]: int(taken)}
                    if resource == "dlg:safanj":
                        state["GLOBALPirateTreasure"] = 1 if index == 8 else 0
                    items = {spec["original_item"]} if owns else set()
                    positive = resource == "dlg:samia" or b"!PartyHasItem" not in condition
                    expected = taken == positive if active else text_conditions(condition, state, items)
                    self.assertEqual(text_conditions(changed[index], state, items), expected)

    def test_dialogue_truth_table_and_quest_journal_actions_are_preserved(self):
        for resource in ("dlg:samia", "dlg:safanj"):
            original = dialogue_fixture(resource)
            self.check_dialogue(resource, original, self.patched(resource, original))

    def test_dialogue_conditions_are_found_by_meaning_in_another_layout(self):
        # Beamdog's compiled text: LF between lines and none after the last, the
        # treasure test first or last; another mod's reply that tests it too.
        conditions = {0: b'!PartyHasItem("book03")\nGlobal("Unrelated","GLOBAL",4)',
                      2: b'Global("Unrelated","GLOBAL",4)\nPartyHasItem("Book03")',
                      8: b'Global("Unrelated","GLOBAL",4)\n  !PartyHasItem("BOOK03")'}
        dialogue = _Dialogue(dialogue_fixture("dlg:safanj", conditions), "safanj")
        dialogue.triggers.append(b'PartyHasItem("book03")\nGlobal("Unrelated","GLOBAL",4)')
        reply = dialogue.transition(None, action='SetGlobal("OtherMod","GLOBAL",2)')
        struct.pack_into("<I", reply, 0, struct.unpack_from("<I", reply)[0] | 2)
        struct.pack_into("<I", reply, 12, len(dialogue.triggers) - 1)
        dialogue.state(1235, [reply])
        original = dialogue.render()
        result = self.patched("dlg:safanj", original)
        self.check_dialogue("dlg:safanj", original, result, reply_sites=(len(dialogue.triggers) - 1,))
        after = _Dialogue(result, "safanj")
        self.assertTrue(after.state_triggers[0].endswith(b'\nGlobal("Unrelated","GLOBAL",4)'))
        self.assertTrue(after.triggers[-1].endswith(b'\nGlobal("Unrelated","GLOBAL",4)'))

    def test_routes_the_check_cannot_recognise_name_their_reason(self):
        def blocks(resource, *made):
            return b"SC\n" + b"".join(made) + b"SC\n"
        golem = action(151, 38758) + action(10)
        unrecognised = "tests {} in a way the container route does not recognise"
        cases = (
            ("bcs:kpgol01", blocks("bcs:kpgol01", script_block(b"", golem)), "KPGOL01.BCS has no block that tests AX1H13"),
            ("bcs:kpgol01", blocks("bcs:kpgol01", script_block(
                rewards._trigger(16521, 2) + rewards._trigger(16450, text="ax1h13") +
                rewards._trigger(16399, 1, text="GLOBALOther"), golem)),
             "KPGOL01.BCS block 0 " + unrecognised.format("AX1H13")),
            ("bcs:kpgol01", blocks("bcs:kpgol01", script_block(rewards._trigger(16450, text="ax1h13"), action(10))),
             r"KPGOL01.BCS no longer shows the golem's text that names the treasure \(38758\)"),
            ("bcs:kpgol01", blocks("bcs:kpgol01", script_block(rewards._trigger(16450, text="ax1h13"),
                                                                action(151, 38758) + golem)),
             "KPGOL01.BCS block 0 shows text 38758 twice"),
            ("bcs:ppchest", blocks("bcs:ppchest", script_block(
                rewards._trigger(16501, negate=1, text="bow19b", selector=1), action(10))),
             "PPCHEST.BCS block 0 " + unrecognised.format("BOW19B")),
            ("bcs:samia", blocks("bcs:samia", script_block(
                rewards._trigger(16521, 2) + rewards._trigger(16450, text="shld21") +
                rewards._trigger(16399, 1, text="GLOBALOther"), action(10))),
             "SAMIA.BCS block 0 " + unrecognised.format("SHLD21/SW1H32")),
            ("bcs:kpgol01", b"SC\nCR\nCO\nSC\n",
             "KPGOL01.BCS cannot be read as a compiled script: its last block is not closed"),
            ("dlg:samia", dialogue_fixture("dlg:samia", {1: b'Global("Unrelated","GLOBAL",4)\r\n'}),
             "SAMIA.DLG has no condition that tests SHLD21/SW1H32"),
            ("dlg:safanj", dialogue_fixture("dlg:safanj", {2: b'HasItem("book03",Myself)\r\n'}),
             "SAFANJ.DLG condition 2 " + unrecognised.format("BOOK03")),
            ("dlg:safanj", dialogue_fixture("dlg:safanj", {2: b'OR(2)\r\nPartyHasItem("book03")\r\nGlobal("X","GLOBAL",1)\r\n'}),
             "SAFANJ.DLG condition 2 " + unrecognised.format("BOOK03")),
            ("dlg:safanj", b"DLG V1.0" + bytes(10), "SAFANJ.DLG cannot be read as a dialogue"),
        )
        for resource, data, reason in cases:
            with self.subTest(reason=reason):
                reasons = rewards.route_reasons(resource, data)
                self.assertEqual(set(reasons), set(rewards.RESOURCE_TARGETS[resource]))
                for text in reasons.values():
                    self.assertRegex(text, "^" + reason + "$")
                with self.assertRaisesRegex(ProfileError, reason):
                    rewards.patch_container_resource(resource, data, refs={38758: 100001})

    def test_exact_target_selectors_shared_samia_latch_and_golem_text(self):
        one, two = (rewards.target_spec("bg2:mode2:target:" + row) for row in ("53", "28"))
        self.assertEqual((one["original_item"], two["original_item"]), ("shld21", "sw1h32"))
        for key in ("area", "activation_global", "latch_global", "latch_scope"):
            self.assertEqual(one[key], two[key])
        self.assertEqual(rewards.treasure("bg2:mode2:target:53"), ("shld21", "sw1h32"))
        self.assertEqual(rewards.treasure("bg2:mode2:target:13"), ("ax1h13",))
        self.assertEqual(rewards.target_spec("bg1:mode2:target:22")["area"], "ar3601")
        self.assertEqual(rewards.treasure_area("bg1:mode2:target:22", "eet"), "bg3601")
        self.assertEqual(rewards.treasure_area("bg1:mode2:target:22", "bgee"), "ar3601")
        self.assertEqual(rewards.treasure_area("bg2:mode2:target:13", "eet"), "ar1303")
        with self.assertRaisesRegex(ProfileError, "Unsupported extended reward container"):
            rewards.target_spec("bg2:mode2:target:1")
        with self.assertRaisesRegex(ProfileError, "Unsupported extended reward container resource"):
            rewards.own_patch("bcs:other", b"SC\nSC\n")
        original = make_tlk(["Preserve existing text"])
        tlk, refs = rewards.prepare_container_texts(original, rewards.TEXT_RESOURCES)
        self.assertEqual(len(set(row[38758] for row in refs.values())), 1)
        self.assertEqual(_Tlk(tlk).text(refs["bcs:kpgol01"][38758], []), "As you pick up the item, two of the golems come to life and attack.")
        self.assertEqual(rewards.prepare_container_texts(tlk, rewards.TEXT_RESOURCES), (tlk, refs))
        with self.assertRaisesRegex(ProfileError, "reward texts exist only in English"):
            rewards.prepare_container_texts(original, rewards.TEXT_RESOURCES, "de_DE")
        self.assertEqual(rewards.prepare_container_texts(original, ("bcs:ar1202",), "de_DE"), (original, {}))

    def test_the_quest_container_is_the_one_that_holds_the_treasure(self):
        def chest(area, index, item):
            return {"kind": "container", "resource": f"are:{area}", "item": item,
                    "id": f"are:{area}/container/{index}/item/0", "record": {"container_index": index}}
        samia = "bg2:mode2:target:28"
        # BG2:EE and EET keep Samia's treasure in AR1202's tenth container
        # (Item Randomiser's container10 of the original game, renamed since).
        sources = [chest("ar1202", 8, "misc01"), chest("ar1202", 9, "sw1h32"), chest("ar1202", 9, "shld21"),
                   chest("ar1201", 0, "sw1h32"),
                   {"kind": "cre-template", "resource": "cre:x", "item": "shld21", "record": {}}]
        self.assertEqual(rewards.treasure_container(samia, sources, "bg2ee"), "are:ar1202/container/9")
        self.assertEqual(rewards.treasure_container("bg2:mode2:target:53", sources, "eet"), "are:ar1202/container/9")
        # A mod added a chest before it: the treasure still names it.
        moved = [dict(row, record={"container_index": row["record"].get("container_index", 0) + 1})
                 if row["kind"] == "container" else row for row in sources]
        self.assertEqual(rewards.treasure_container(samia, moved, "bg2ee"), "are:ar1202/container/10")
        for changed, reason in (
                (sources[:2], "AR1202 has no container that holds SHLD21/SW1H32"),
                (sources + [chest("ar1202", 3, "sw1h32"), chest("ar1202", 3, "shld21")],
                 "AR1202 has more than one container that holds SHLD21/SW1H32")):
            with self.subTest(reason=reason), self.assertRaisesRegex(ProfileError, "^" + reason + "$"):
                rewards.treasure_container(samia, changed, "bg2ee")
        # The golems' treasures are in AR1303, whatever the table's area column says.
        golem = [chest("ar1302", 0, "ax1h13"), chest("ar1303", 2, "ax1h13")]
        self.assertEqual(rewards.treasure_container("bg2:mode2:target:13", golem, "bg2ee"), "are:ar1303/container/2")
        # EET names Safana's BG1 area BG3601.
        safana = "bg1:mode2:target:22"
        self.assertEqual(rewards.treasure_container(safana, [chest("bg3601", 0, "book03")], "eet"),
                         "are:bg3601/container/0")
        self.assertEqual(rewards.treasure_container(safana, [chest("ar3601", 0, "book03")], "bgee"),
                         "are:ar3601/container/0")
        # An item record that two containers share belongs to neither.
        aliased = [{"kind": "container", "resource": "are:ar1514", "item": "bow19b", "record": {"container_index": None}}]
        with self.assertRaisesRegex(ProfileError, "^AR1514 has no container that holds BOW19B$"):
            rewards.treasure_container("bg2:mode2:target:61", aliased, "bg2ee")
        scan = ROOT / "work/original-preset-runtime-20260929/scan-with-spells/world.json"
        if scan.exists():
            import json
            world = json.loads(scan.read_text(encoding="utf-8"))
            self.assertEqual({target: rewards.treasure_container(target, world["sources"], "eet")
                              for target in rewards.TARGETS}, {
                "bg2:mode2:target:13": "are:ar1303/container/0", "bg2:mode2:target:43": "are:ar1303/container/1",
                "bg2:mode2:target:3": "are:ar1303/container/2", "bg2:mode2:target:53": "are:ar1202/container/9",
                "bg2:mode2:target:28": "are:ar1202/container/9", "bg1:mode2:target:22": "are:bg3601/container/0",
                "bg2:mode2:target:61": "are:ar1514/container/0"})

    def test_reviewed_eet_scripts_and_dialogues_when_available(self):
        cache = ROOT / "work/original-preset-runtime-20260929/mode2-resources"
        if not cache.exists():
            self.skipTest("Optional isolated-lab resource cache is not present")
        self.assertEqual(set(REVIEWED_EET), set(rewards.RESOURCE_TARGETS))
        _tlk, golem = rewards.prepare_container_texts(make_tlk([]), rewards.TEXT_RESOURCES)
        for resource, (reviewed, patched) in REVIEWED_EET.items():
            kind, name = resource.split(":")
            original = (cache / f"{name}.{kind}").read_bytes()
            with self.subTest(resource=resource):
                self.assertEqual(hashlib.sha256(original).hexdigest(), reviewed)
                exact = rewards.patch_container_resource(resource, original, refs=golem.get(resource))
                self.assertEqual(hashlib.sha256(exact).hexdigest(), patched)
                result = self.patched(resource, original)
                if kind == "bcs":
                    self.check_script(resource, original, result)
                else:
                    self.check_dialogue(resource, original, result)


if __name__ == "__main__":
    unittest.main()
