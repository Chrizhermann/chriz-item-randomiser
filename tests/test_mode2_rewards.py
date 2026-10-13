"""Portable preservation checks and optional pinned-resource Mode 2 evidence."""
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import unittest
from unittest.mock import patch

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from flir_config import mode2_rewards as rewards
from flir_config.profiles import ProfileError
from flir_config.quest_options import _Dialogue
from flir_config.quest_rewards import _blocks
from flir_config.resources import _Tlk
from test_postinstall_resources import make_tlk


def outgoing(dialogue, state):
    first, count = struct.unpack_from("<II", dialogue.states[state], 4)
    return dialogue.transitions[first:first + count]


TRGENI_DEPARTURE = (b'EEex_LuaAction("EEex_Action_QueueResponseStringOnAIBase('
                   b'[[DestroySelf()]],EEex_LuaAction_Object)")')


def trgeni_terminal_actions():
    actions = ['SetGlobal("zahraajob","GLOBAL",2)', 'SetGlobal("geniesgone","GLOBAL",1)',
               'AddexperienceParty(10000)']
    effects = ['CreateVisualEffectObject("SPDISPMA",Myself)', 'Wait(1)',
               'CreateVisualEffectObject("SPFLESHS",Myself)', 'DestroySelf()']
    for other in ("trgeni02", "trgeni03"):
        actions.extend(f'ActionOverride("{other}",{action})' for action in effects)
    return "\r\r\n".join(actions + effects) + "\r\r\n"


def dialogue_fixture(specs):
    specs = (specs,) if isinstance(specs, dict) else specs
    spec = specs[0]
    header = bytearray(b"DLG V1.0" + bytes(44))
    struct.pack_into("<10I", header, 8, 0, 52, 0, 52, 52, 0, 52, 0, 52, 0)
    dialogue = _Dialogue(bytes(header), spec["quest"])
    required = set(row for spec in specs for row in spec["branches"])
    for state in range(max(row[0] for row in required) + 2):
        count = max((row[1] for row in required if row[0] == state), default=0) + 1
        transitions = []
        for ordinal in range(count):
            action = 'SetGlobal("Untouched","GLOBAL",8)\r\r\n'
            if (state, ordinal) in required:
                action += 'GiveItemCreate("misc42",Player2,0,0,0)\r\r\n'
                for grant_spec in specs:
                    if (state, ordinal) in grant_spec["branches"]:
                        charges = ",".join(map(str, grant_spec["payload"]["charges"]))
                        if grant_spec["quest"] == "amcler01":
                            action += 'DisplayStringHead(Myself,74025)\r\r\n'
                        if grant_spec.get("grant_kind") == "transfer":
                            action += f'GiveItem("{grant_spec["item"]}",{grant_spec["recipient"]})\r\r\n'
                        else:
                            action += f'GiveItemCreate("{grant_spec["item"]}",{grant_spec["recipient"]},{charges})\r\r\n'
            action += 'EraseJournalEntry(12345)\r\r\nWait(1)\r\r\nEscapeArea()'
            if spec["quest"] == "trgeni01" and state in (37, 38):
                action = trgeni_terminal_actions()
            transitions.append(dialogue.transition(2, reply=9876, journal=12345,
                               trigger=f'Global("OriginalCondition","GLOBAL",{ordinal})', action=action))
        dialogue.state(555 + state, transitions)
    return dialogue.render()


def obj(selector=0):
    return f'0 0 0 0 0 0 0 {selector} 0 0 0 0 ""OB\n'


def action(opcode, value=0, text="", target=0):
    return (f'AC\n{opcode}OB\n{obj()}OB\n{obj(target)}OB\n{obj()}'
            f'{value} 0 0 0 0"{text}" "" AC\n')


def block(actions):
    condition = f'TR\n16399 1 0 0 0 "AR1202Quest" "" OB\n{obj()}TR\n'
    return 'CR\nCO\n' + condition + 'CO\nRS\nRE\n100' + actions + 'RE\nRS\nCR\n'


def script_fixture(name):
    if name == "asysta01":
        return ('SC\n' + block(action(164, 20000) + action(82, text="Helm19") +
                                action(82, text="Misc8y") + action(30, 2, "AR1512PlantedItems")) + 'SC\n').encode()
    other = block(action(151, 40849, target=1))
    return ('SC\n' + other + block(action(30, 2, "AR1202DragonWell") +
            action(151, 40850, target=1) + action(140, text="helm21", target=21) +
            action(177)) + other + 'SC\n').encode()


class Mode2RewardTests(unittest.TestCase):
    def synthetic_patch(self, name, original):
        with patch.dict(rewards.ORIGINAL_HASHES, {name: hashlib.sha256(original).hexdigest()}), \
                patch.dict(rewards.PATCHED_HASHES, {name: ""}):
            result = rewards.patch_reward(name, original, ddwell_ref=100001)
            with patch.dict(rewards.PATCHED_HASHES, {name: hashlib.sha256(result).hexdigest()}):
                self.assertEqual(rewards.patch_reward(name, result), result)
                with self.assertRaisesRegex(ProfileError, "changed"):
                    rewards.patch_reward(name, result[:-1] + b"tampered")
        return result

    def check_dialogue(self, name, original, result, action_refs=None):
        spec = rewards.resource_specs("dlg:" + name)[0]
        before, after = _Dialogue(original, name), _Dialogue(result, name)
        self.assertEqual(after.state_triggers, before.state_triggers)
        self.assertEqual(after.actions[:len(before.actions)], before.actions)
        for index, state in enumerate(before.states):
            self.assertEqual(after.states[index][:4], state[:4])
            self.assertEqual(after.states[index][12:], state[12:])
            old, new = outgoing(before, index), outgoing(after, index)
            cursor = 0
            for ordinal, transition in enumerate(old):
                if (index, ordinal) not in spec["branches"]:
                    if name == "trgeni01" and index in (37, 38):
                        self.assertEqual(new[cursor][:16], transition[:16])
                        self.assertEqual(new[cursor][20:], transition[20:])
                        original_action = before.actions[struct.unpack_from("<I", transition, 16)[0]]
                        changed = after.actions[struct.unpack_from("<I", new[cursor], 16)[0]]
                        self.assertEqual(changed.count(TRGENI_DEPARTURE), 1)
                        self.assertEqual(changed.replace(TRGENI_DEPARTURE, b"DestroySelf()"), original_action)
                        self.assertNotIn(b"DestroySelf()", changed.splitlines())
                        for other in (b"trgeni02", b"trgeni03"):
                            self.assertIn(b'ActionOverride("' + other + b'",DestroySelf())', changed)
                    else:
                        self.assertEqual(new[cursor], transition)
                    cursor += 1
                    continue
                flags, _, _, trigger, action_id = struct.unpack_from("<5I", transition)
                original_action = before.actions[action_id]
                prior = before.triggers[trigger] if flags & 2 else b""
                for selected in (False, True):
                    branch = new[cursor]; cursor += 1
                    self.assertEqual(struct.unpack_from("<I", branch)[0], flags | 2)
                    self.assertEqual(branch[4:12], transition[4:12])  # Reply and journal.
                    self.assertEqual(branch[20:], transition[20:])  # Link/exit state.
                    trigger_id, action_id = struct.unpack_from("<II", branch, 12)
                    self.assertEqual(after.triggers[trigger_id], prior +
                        f'{"" if selected else "!"}Global("{spec["selected_global"]}","GLOBAL",1)\r\n'.encode())
                    changed = after.actions[action_id]
                    if spec.get("inventory_target"):
                        target = spec["target_ids"][0].rsplit(":", 1)[1]
                        event = (f'\r\nEEex_LuaAction("return(FLPR_Transfer(EEex_LuaAction_Object,{target}))")').encode()
                        self.assertEqual(changed.count(event), 1)
                        self.assertNotIn(spec["recipient_local"].encode(), changed)
                        self.assertNotIn(spec["event_global"].encode(), changed)
                        restored = changed.replace(event, b"", 1)
                        if selected and action_refs:
                            for old, new in action_refs.items():
                                self.assertEqual(restored.count(f'DisplayStringHead(Myself,{new})'.encode()), 1)
                                restored = restored.replace(f'DisplayStringHead(Myself,{new})'.encode(),
                                                            f'DisplayStringHead(Myself,{old})'.encode(), 1)
                    else:
                        marker = f'ActionOverride({spec["recipient"]},SetGlobal("{spec["recipient_local"]}","LOCALS",1))\r\n'.encode()
                        event = f'\r\nSetGlobal("{spec["event_global"]}","GLOBAL",1)'.encode()
                        self.assertEqual(changed.count(marker), 1)
                        self.assertEqual(changed.count(event), 1)
                        restored = changed.replace(marker, b"", 1).replace(event, b"", 1)
                    grant = rewards._dialogue_grant(original_action, spec)
                    if selected:
                        self.assertNotIn(grant, changed)
                        consumed = f'SetGlobal("{spec["consumed_global"]}","GLOBAL",1)'.encode()
                        restored = restored.replace(consumed, grant, 1)
                    self.assertEqual(restored, original_action)
                    if b"EscapeArea()" in changed:
                        self.assertLess(changed.index(event), changed.index(b"EscapeArea()"))
                    if b"DestroySelf()" in changed:
                        self.assertLess(changed.index(event), changed.index(b"DestroySelf()"))

    def test_dialogue_both_campaign_routes_keep_exact_recipient_and_surrounding_actions(self):
        for resource in rewards.RESOURCE_REWARDS:
            if resource.startswith("dlg:") and resource != "dlg:aran" and resource not in rewards.TEXT_REFS:
                name = resource[4:]
                with self.subTest(resource=name):
                    original = dialogue_fixture(rewards.resource_specs(resource))
                    self.check_dialogue(name, original, self.synthetic_patch(name, original))

    def test_compiled_grants_keep_owner_or_original_object_and_do_not_move_other_rewards(self):
        for name in ("asysta01", "ddwell"):
            with self.subTest(resource=name):
                original = script_fixture(name)
                result = self.synthetic_patch(name, original)
                spec = rewards.REWARDS[name]
                old, _, _ = _blocks(original, 1 if name == "asysta01" else 3, b"\n")
                new, _, _ = _blocks(result, len(old) + 1, b"\n")
                index = spec["branches"][0]
                self.assertEqual(new[:index], old[:index])
                self.assertEqual(new[index + 2:], old[index + 1:])
                for selected, branch in enumerate(new[index:index + 2]):
                    grant = action(82, text="Helm19") if name == "asysta01" else action(140, text="helm21", target=21)
                    self.assertEqual(branch.count(grant.encode()), 0 if selected else 1)
                    self.assertEqual(branch.count(spec["event_global"].encode()), 1)
                    self.assertEqual(branch.count(spec["consumed_global"].encode()), selected)
                    if name == "asysta01":
                        self.assertNotIn(b"LOCALSFLPR_R", branch)
                        self.assertIn(action(82, text="Misc8y").encode(), branch)
                        self.assertIn(action(30, 2, "AR1512PlantedItems").encode(), branch)
                    else:
                        marker = (f'AC\n30OB\n{obj(21)}OB\n{obj()}OB\n{obj()}'
                                  f'1 0 0 0 0"LOCALS{spec["recipient_local"]}" "" AC\n').encode()
                        self.assertIn(marker, branch)
                        self.assertLess(branch.index(marker), branch.index(b'GLOBAL' + spec["event_global"].encode()))
                        self.assertIn(action(151, 100001 if selected else 40850, target=1).encode(), branch)
                        self.assertIn(action(177).encode(), branch)

    def test_registry_exposes_authored_charges_and_separate_container_recipient(self):
        self.assertEqual(len(rewards.DIRECT_REWARDS), 9)
        spec = rewards.source_spec("original-source:bg2:mode2:item:28")
        self.assertEqual(spec["payload"], {"resref": "ring31", "expiration": 0, "charges": [1, 1, 1], "flags": 0})
        spec["payload"]["charges"][0] = 99
        self.assertEqual(rewards.target_spec("bg2:mode2:target:35")["payload"]["charges"], [1, 1, 1])
        container = rewards.target_spec("bg2:mode2:target:21")
        self.assertEqual(container["recipient"], "are:ar1512/container/0")
        self.assertIsNone(container["recipient_local"])
        self.assertEqual(rewards.target_spec("bg2:mode2:target:40")["recipient"], "Player1")
        temple = rewards.target_spec("bg2:mode2:target:48")
        self.assertEqual(temple["resources"], ("dlg:bhoisig", "dlg:bhnalla", "dlg:bharval"))
        demon = rewards.source_spec("original-source:bg2:mode2:item:65")
        silver = rewards.source_spec("original-source:bg2:mode2:item:44")
        self.assertEqual(demon["payload"]["charges"], [0, 3, 0])
        self.assertEqual(demon["recipient"], "Player1")
        self.assertEqual(demon["event_global"], silver["event_global"])
        self.assertEqual(demon["recipient_local"], silver["recipient_local"])
        self.assertNotEqual(demon["selected_global"], silver["selected_global"])

    def check_aran(self, original, result):
        before, after = _Dialogue(original, "aran"), _Dialogue(result, "aran")
        specs = rewards.resource_specs("dlg:aran")
        self.assertEqual([spec["quest"] for spec in specs], ["aran", "aran"])
        for state in range(len(before.states)):
            old, new = outgoing(before, state), outgoing(after, state)
            if state != 4:
                self.assertEqual(new, old)
                continue
            self.assertEqual(len(old), 8)
            self.assertEqual(len(new), 32)
            for ordinal, transition in enumerate(old):
                flags, _, _, trigger, action_id = struct.unpack_from("<5I", transition)
                prior = before.triggers[trigger] if flags & 2 else b""
                original_actions = before.actions[action_id]
                for bits, branch in enumerate(new[ordinal * 4:ordinal * 4 + 4]):
                    condition, restored = prior, after.actions[struct.unpack_from("<I", branch, 16)[0]]
                    self.assertEqual(branch[4:12], transition[4:12])
                    self.assertEqual(branch[20:], transition[20:])
                    for spec, selected in zip(specs, (bool(bits & 2), bool(bits & 1))):
                        condition += f'{"" if selected else "!"}Global("{spec["selected_global"]}","GLOBAL",1)\r\n'.encode()
                        marker = f'ActionOverride(LastTalkedToBy,SetGlobal("{spec["recipient_local"]}","LOCALS",1))\r\n'.encode()
                        event = f'\r\nSetGlobal("{spec["event_global"]}","GLOBAL",1)'.encode()
                        self.assertEqual(restored.count(marker), 1)
                        self.assertEqual(restored.count(event), 1)
                        restored = restored.replace(marker, b"", 1).replace(event, b"", 1)
                        grant = rewards._dialogue_grant(original_actions, spec)
                        if selected:
                            self.assertNotIn(grant, restored)
                            consumed = f'SetGlobal("{spec["consumed_global"]}","GLOBAL",1)'.encode()
                            restored = restored.replace(consumed, grant, 1)
                        else:
                            self.assertIn(grant, restored)
                    self.assertEqual(after.triggers[struct.unpack_from("<I", branch, 12)[0]], condition)
                    self.assertEqual(restored, original_actions)

    def test_aran_two_grants_have_all_four_independent_campaign_combinations(self):
        original = dialogue_fixture(rewards.resource_specs("dlg:aran"))
        self.check_aran(original, self.synthetic_patch("aran", original))

    def check_text_graph(self, resource, original, result, refs):
        name = resource.split(":")[1]
        specs = rewards.resource_specs(resource)
        # Grant branching is checked independently above. Check every text-route
        # edge against that graph, including unchanged external entry states.
        action_refs = {old: refs[old] for old in rewards.ACTION_TEXT_REFS.get(resource, ())}
        before = _Dialogue(rewards._patch_dialogue(original, specs, action_refs=action_refs), name)
        after = _Dialogue(result, name)
        flag = specs[0]["selected_global"]
        clones = {}
        for index, state in enumerate(before.states):
            affected = struct.unpack_from("<I", state)[0] in refs or any(
                struct.unpack_from("<I", row)[0] & 1 and struct.unpack_from("<I", row, 4)[0] in refs
                for row in outgoing(before, index))
            if affected:
                clones[index] = len(before.states) + len(clones)
        self.assertEqual(len(after.states), len(before.states) + len(clones))
        self.assertEqual(after.actions, before.actions)
        for index, state in enumerate(before.states):
            destinations = [(index, False)] + ([(clones[index], True)] if index in clones else [])
            old_text, _, _, old_entry = struct.unpack("<4I", state)
            for destination, selected_text in destinations:
                new_text, _, _, entry = struct.unpack("<4I", after.states[destination])
                self.assertEqual(new_text, refs.get(old_text, old_text) if selected_text else old_text)
                if old_entry != 0xFFFFFFFF and index in clones:
                    guard = f'{"" if selected_text else "!"}Global("{flag}","GLOBAL",1)\r\n'.encode()
                    self.assertEqual(after.state_triggers[entry], before.state_triggers[old_entry] + guard)
                else:
                    self.assertEqual(entry, old_entry)
                new_rows, cursor = outgoing(after, destination), 0
                for row in outgoing(before, index):
                    flags, reply, journal, trigger, action_id, next_dlg, next_state = struct.unpack("<5I8sI", row)
                    internal = next_dlg.rstrip(b"\0").lower() == name.encode()
                    reroute = not flags & 8 and internal and next_state in clones
                    for selected_edge in ((False, True) if reroute else (False,)):
                        new = new_rows[cursor]; cursor += 1
                        nf, nr, nj, nt, na, nd, ns = struct.unpack("<5I8sI", new)
                        self.assertEqual((nf, nr, nj, na, nd), (flags | 2 if reroute else flags,
                            refs.get(reply, reply) if selected_text and flags & 1 else reply,
                            journal, action_id, next_dlg))
                        self.assertEqual(ns, clones[next_state] if reroute and selected_edge else next_state)
                        if reroute:
                            prior = before.triggers[trigger] if flags & 2 else b""
                            guard = f'{"" if selected_edge else "!"}Global("{flag}","GLOBAL",1)\r\n'.encode()
                            self.assertEqual(after.triggers[nt], prior + guard)
                        else:
                            self.assertEqual(nt, trigger)
                self.assertEqual(cursor, len(new_rows))
        return clones

    def test_text_variants_preserve_original_routes_replies_and_verified_recovery(self):
        resources = [resource for resource in rewards.TEXT_REFS if resource.startswith("dlg:")]
        tlk, allocated = rewards.prepare_reward_texts(make_tlk(["Unchanged text"]), resources)
        self.assertEqual(rewards.prepare_reward_texts(tlk, resources), (tlk, allocated))
        self.assertEqual(_Tlk(tlk).text(0, set()), "Unchanged text")
        self.assertIn("<GABBER>", _Tlk(tlk).text(allocated["dlg:ppdjinn"][48995], set()))
        self.assertEqual(allocated["dlg:gorcamb"][63681], allocated["dlg:gorcamb"][64609])
        for resource in resources:
            name = resource.split(":")[1]
            with self.subTest(resource=resource):
                d = _Dialogue(dialogue_fixture(rewards.resource_specs(resource)), name)
                originals = list(rewards.TEXT_REFS[resource])
                # Exercise both state text and reply replacement; production
                # locations are independently exercised by the cached resources.
                for index, old_ref in enumerate(originals):
                    if old_ref in rewards.ACTION_TEXT_REFS.get(resource, ()):
                        continue
                    if len(originals) > 1 and index == len(originals) - 1:
                        struct.pack_into("<I", outgoing(d, 0)[0], 4, old_ref)
                    else:
                        struct.pack_into("<I", d.states[index], 0, old_ref)
                d.state_triggers.append(b'NumTimesTalkedTo(0)\r\n')
                struct.pack_into("<I", d.states[0], 12, len(d.state_triggers) - 1)
                original = d.render()
                with patch.dict(rewards.ORIGINAL_HASHES, {name: hashlib.sha256(original).hexdigest()}):
                    result = rewards.patch_reward(resource, original, refs=allocated[resource])
                    self.check_text_graph(resource, original, result, allocated[resource])
                    self.assertEqual(rewards.original_reward(resource, result), original)
                    self.assertEqual(rewards.patch_reward(resource, result), result)
                    moved = {old: new + 1000 for old, new in allocated[resource].items()}
                    revised = rewards.patch_reward(resource, result, refs=moved)
                    self.assertEqual(rewards.original_reward(resource, revised), original)
                    self.check_text_graph(resource, original, revised, moved)
                    damaged = bytearray(result); damaged[100] ^= 1
                    with self.assertRaisesRegex(ProfileError, "changed"):
                        rewards.original_reward(resource, bytes(damaged))
                    with self.assertRaisesRegex(ProfileError, "allocated"):
                        rewards.patch_reward(resource, original)
        self.assertEqual(rewards.target_spec("bg2:mode2:target:22")["required_transfers"], ("scyarryl",))
        self.assertEqual(rewards.REWARDS["sctelwyn"]["event_global"], "FLPR_EA_SCULPTOR")
        self.assertEqual(rewards.REWARDS["scsain"]["event_global"], "FLPR_EA_SCULPTOR")

    def test_sculptor_transfer_is_source12_auxiliary_copy_and_preserves_ordinary_giveitem(self):
        spec = rewards.transfer_spec("scyarryl")
        self.assertEqual(spec["authored_source_ids"], ("original-source:bg2:mode2:item:12",))
        self.assertEqual(spec["source_ids"], ())
        self.assertEqual(rewards.SOURCE_REWARDS[spec["authored_source_ids"][0]], "bhoisig")
        self.assertEqual(spec["physical_source_ids"], ("cre:scyarryl/item/0",))
        self.assertEqual(spec["source_record"], {"byte_offset": 1308, "record_index": 0,
                         "resref": "blun20", "expiration": 0, "charges": [0, 0, 0], "flags": 0, "slots": [9]})
        self.assertEqual(spec["selected_global"], rewards.REWARDS["bhoisig"]["selected_global"])
        self.assertEqual(rewards.source_spec(spec["authored_source_ids"][0])["required_transfers"], ("scyarryl",))
        self.assertEqual(spec["event_global"], "FLPR_EA_SCULPTOR")
        self.assertEqual(rewards.target_spec("bg2:mode2:target:22")["resources"],
                         ("dlg:sctelwyn", "dlg:scsain", "dlg:scyarryl"))
        original = dialogue_fixture(spec)
        # Test the transfer branching itself independently of selected narrative.
        result = rewards._patch_dialogue(original, rewards.resource_specs("dlg:scyarryl"))
        self.check_dialogue("scyarryl", original, result)
        dlg = _Dialogue(result, "scyarryl")
        ordinary, selected = outgoing(dlg, 19)
        ordinary_action = dlg.actions[struct.unpack_from("<I", ordinary, 16)[0]]
        selected_action = dlg.actions[struct.unpack_from("<I", selected, 16)[0]]
        self.assertIn(b'GiveItem("blun20",LastTalkedToBy)', ordinary_action)
        self.assertNotIn(b'GiveItem("blun20"', selected_action)
        self.assertNotIn(b'GiveItemCreate("blun20"', selected_action)
        self.assertIn(b'SetGlobal("FLPR_XM2_12","GLOBAL",1)', selected_action)
        scan = ROOT / "work/original-preset-runtime-20260929/scan-with-spells/world.json"
        if scan.exists():
            world = json.loads(scan.read_text(encoding="utf-8"))
            physical = next(row for row in world["sources"] if row["id"] == spec["physical_source_ids"][0])
            resource = next(row for row in world["resources"] if row["id"] == spec["source_resource"])
            self.assertEqual(physical["record"], spec["source_record"])
            self.assertEqual(resource["sha256"], spec["source_resource_sha256"])

    def test_carried_rewards_keep_sources_and_transfer_callbacks_before_later_actions(self):
        cases = (("trgeni01", 32, 32, "ar2014", "trgeni01", 0, 1152, 9),
                 ("loutcapt", 39, 42, "ar0514", "Dennis", 6, 1116, 11),
                 ("amcler01", 84, 85, "ar5500", "amcler01", 0, 1272, 4))
        tlk, allocated = rewards.prepare_reward_texts(make_tlk(["Unchanged"]),
                            ("dlg:trgeni01", "dlg:amcler01"))
        self.assertEqual(_Tlk(tlk).text(allocated["dlg:amcler01"][74025], set()), "Please, accept this gift.")
        scan = ROOT / "work/original-preset-runtime-20260929/scan-with-spells/world.json"
        world = json.loads(scan.read_text(encoding="utf-8")) if scan.exists() else None
        for name, source, target, area, script, index, offset, slot in cases:
            with self.subTest(resource=name):
                identity = f"original-source:bg2:mode2:item:{source}"
                spec = rewards.transfer_spec(name)
                self.assertEqual(spec, rewards.source_spec(identity))
                self.assertEqual(spec, rewards.target_spec(f"bg2:mode2:target:{target}"))
                self.assertEqual(spec["source_ids"], (identity,))
                self.assertEqual(spec["authored_source_ids"], (identity,))
                self.assertEqual(spec["required_transfers"], (name,))
                self.assertEqual(spec.get("replacement"), "blun05" if name == "trgeni01" else None)
                self.assertEqual(spec["inventory_target"], {"area": area, "creature_resource": "cre:" + name, "script_name": script})
                self.assertEqual(spec["physical_source_ids"], (f"cre:{name}/item/{index}",))
                self.assertEqual(spec["source_record"], {"byte_offset": offset, "record_index": index,
                    "resref": spec["item"], "expiration": 0, "charges": [0, 0, 0], "flags": 0, "slots": [slot]})
                self.assertEqual(spec["recipient"], "LastTalkedToBy")
                self.assertEqual(spec["grant_kind"], "transfer")
                original = dialogue_fixture(spec)
                action_refs = allocated.get("dlg:" + name) if name == "amcler01" else None
                result = rewards._patch_dialogue(original, rewards.resource_specs("dlg:" + name), action_refs=action_refs)
                self.check_dialogue(name, original, result, action_refs)
                for action_bytes in _Dialogue(result, name).actions:
                    self.assertNotIn(f'GiveItemCreate("{spec["item"]}"'.encode(), action_bytes)
                if world:
                    physical = next(row for row in world["sources"] if row["id"] == spec["physical_source_ids"][0])
                    resource = next(row for row in world["resources"] if row["id"] == spec["source_resource"])
                    self.assertEqual(physical["record"], spec["source_record"])
                    self.assertEqual(resource["sha256"], spec["source_resource_sha256"])

    def test_trgeni_terminal_departure_waits_for_transfer_and_upgrades_previous_patch(self):
        resource = "dlg:trgeni01"
        spec = rewards.resource_specs(resource)[0]
        dialogue = _Dialogue(dialogue_fixture(spec), "trgeni01")
        struct.pack_into("<I", dialogue.states[35], 0, 43514)
        original = dialogue.render()
        refs = {43514: 100001}
        with patch.dict(rewards.ORIGINAL_HASHES, {"trgeni01": hashlib.sha256(original).hexdigest()}):
            legacy = rewards._patch_text_dialogue(original, resource, refs,
                                                  defer_departure=False, numeric_target=False)
            self.assertEqual(rewards.original_reward(resource, legacy), original)
            result = rewards.patch_reward(resource, legacy)
            previous = rewards._patch_text_dialogue(original, resource, refs, numeric_target=False)
            self.assertEqual(rewards.patch_reward(resource, previous), result)
            self.assertNotEqual(result, legacy)
            self.assertEqual(result, rewards.patch_reward(resource, original, refs=refs))
            self.assertEqual(rewards.patch_reward(resource, result), result)
            self.assertEqual(rewards.original_reward(resource, result), original)
            branches = rewards._patch_dialogue(original, (spec,))
            self.check_dialogue("trgeni01", original, branches)
            self.check_text_graph(resource, original, result, refs)
            for state in (37, 38):
                before = _Dialogue(legacy, "trgeni01")
                old = before.actions[struct.unpack_from("<I", outgoing(before, state)[0], 16)[0]]
                after = _Dialogue(result, "trgeni01")
                changed = after.actions[struct.unpack_from("<I", outgoing(after, state)[0], 16)[0]]
                self.assertEqual(old, trgeni_terminal_actions().encode() + b"\r\n")
                self.assertEqual(changed.replace(TRGENI_DEPARTURE, b"DestroySelf()"), old)
            # An altered prior patch is not accepted merely because its original
            # body and recovery trailer still match.
            with self.assertRaisesRegex(ProfileError, "patch changed"):
                rewards.patch_reward(resource, legacy.replace(b"FLPR_Transfer(", b"FLPR_Tampered(", 1))

    def test_inventory_callback_survives_dialogue_parser_and_returns_lua_result(self):
        for name in ("trgeni01", "loutcapt", "amcler01", "udlesa"):
            spec = rewards.resource_specs("dlg:" + name)[0]
            original = dialogue_fixture(spec)
            patched = _Dialogue(rewards._patch_dialogue(original, (spec,)), name)
            target = int(spec["target_ids"][0].rsplit(":", 1)[1])
            chunks = []
            for state, _ordinal in spec["branches"]:
                for row in outgoing(patched, state):
                    actions = patched.actions[struct.unpack_from("<I", row, 16)[0]]
                    chunks.extend(re.findall(rb'EEex_LuaAction\("(return[^"\r\n]*)"\)', actions))
            self.assertEqual(len(chunks), len(spec["branches"]) * 2)
            for runtime in (Lua51, Lua53, LuaJIT):
                with self.subTest(resource=name, runtime=runtime.__module__):
                    lua = runtime(unpack_returned_tuples=True)
                    lua.execute('EEex_LuaAction_Object={};calls=0;result=0;'
                                'function FLPR_Transfer(actor,target) '
                                'assert(actor==EEex_LuaAction_Object);calls=calls+1;'
                                'seen_target=target;return result end')
                    for chunk in chunks:
                        self.assertEqual(chunk, f'return(FLPR_Transfer(EEex_LuaAction_Object,{target}))'.encode())
                        self.assertNotIn(b" ", chunk)
                        self.assertNotIn(b":", chunk)
                        parsed = chunk.replace(b" ", b"").replace(b":", b"").decode()
                        self.assertEqual(lua.execute(parsed), 0)
                        self.assertEqual(lua.globals().seen_target, target)
                        lua.globals().result = -1
                        self.assertEqual(lua.execute(parsed), -1)
                        lua.globals().result = 0
                    self.assertEqual(lua.globals().calls, len(chunks) * 2)
            for target_ids in (("bg1:mode2:target:32",), ("bg2:mode1:target:32",),
                               ("bg2:mode2:target:32extra",), ("bg2:mode2:target:32", "bg2:mode2:target:42")):
                invalid = dict(spec, target_ids=target_ids)
                with self.assertRaisesRegex(ProfileError, "transfer target changed"):
                    rewards._patch_dialogue(original, (invalid,))

    def test_udlesa_physical_source_and_six_distinct_duel_continuations(self):
        spec = rewards.target_spec("bg2:mode2:target:45")
        self.assertEqual(rewards.PHYSICAL_SOURCE_REWARDS["cre:udlesa/item/2"], "udlesa")
        self.assertNotIn("original-source:bg2:mode2:item:42", rewards.SOURCE_REWARDS)
        self.assertNotIn("udlesa", rewards.TRANSFER_SPECS)
        self.assertNotIn("required_transfers", spec)
        self.assertEqual(spec["source_ids"], ())
        self.assertEqual(spec["authored_source_ids"], ("original-source:bg2:mode2:item:42",))
        self.assertEqual(spec["replacement"], "dwsw1h02")
        self.assertEqual(spec["source_resource"], "cre:udlesa")
        self.assertEqual(spec["resource"], "dlg:udlesa")
        self.assertEqual(spec["inventory_target"], {"area": "ar2202", "creature_resource": "cre:udlesa", "script_name": "udlesa"})
        self.assertEqual(spec["physical_source_ids"], ("cre:udlesa/item/2",))
        self.assertEqual(spec["source_record"], {"byte_offset": 3676, "record_index": 2,
            "resref": "sw1h39", "expiration": 0, "charges": [0, 0, 0], "flags": 0, "slots": [9]})
        self.assertEqual(spec["selected_global"], "FLPR_QM2_42")
        self.assertEqual(spec["consumed_global"], "FLPR_XM2_42")
        self.assertEqual(spec["branches"], tuple((state, 0) for state in range(6, 12)))
        dialogue = _Dialogue(dialogue_fixture(spec), "udlesa")
        for state in range(6, 12):
            action_id = struct.unpack_from("<I", outgoing(dialogue, state)[0], 16)[0]
            following = (f'SetGlobal("WaitingToDuel","GLOBAL",2)\r\n'
                         f'SetGlobal("DuelStarts","AR2202",3)\r\n'
                         f'ActionOverride(Player{state - 5},JumpToPoint([608.1123]))\r\n').encode()
            dialogue.actions[action_id] = dialogue.actions[action_id].replace(b"Wait(1)", following + b"Wait(1)")
        original = dialogue.render()
        result = rewards._patch_dialogue(original, rewards.resource_specs("dlg:udlesa"))
        self.check_dialogue("udlesa", original, result)
        after = _Dialogue(result, "udlesa")
        for state in range(6, 12):
            for transition in outgoing(after, state):
                actions = after.actions[struct.unpack_from("<I", transition, 16)[0]]
                self.assertIn(f'ActionOverride(Player{state - 5},JumpToPoint([608.1123]))'.encode(), actions)
                self.assertLess(actions.index(b"FLPR_Transfer("), actions.index(b'SetGlobal("WaitingToDuel"'))
                self.assertNotIn(b'GiveItemCreate("sw1h39"', actions)
        scan = ROOT / "work/original-preset-runtime-20260929/scan-with-spells/world.json"
        if scan.exists():
            world = json.loads(scan.read_text(encoding="utf-8"))
            physical = next(row for row in world["sources"] if row["id"] == "cre:udlesa/item/2")
            resource = next(row for row in world["resources"] if row["id"] == "cre:udlesa")
            target = next(row for row in world["targets"] if row["id"] == "cre:udlesa")
            self.assertEqual(physical["record"], spec["source_record"])
            self.assertEqual(resource["sha256"], spec["source_resource_sha256"])
            self.assertEqual(target["selector"]["script_name"], spec["inventory_target"]["script_name"])

    def test_well_uses_appended_original_text_only_for_selected_source_and_reexports(self):
        tlk = make_tlk(["Existing unrelated text", "Existing helmet narrative"])
        updated, ref = rewards.prepare_ddwell_text(tlk)
        self.assertEqual(ref, 2)
        self.assertEqual(rewards.prepare_ddwell_text(updated), (updated, ref))
        text = _Tlk(updated)
        self.assertEqual(text.text(1, set()), "Existing helmet narrative")
        self.assertIn("You pull an item from the murky water", text.text(ref, set()))
        original = script_fixture("ddwell")
        with patch.dict(rewards.ORIGINAL_HASHES, {"ddwell": hashlib.sha256(original).hexdigest()}):
            result = rewards.patch_reward("ddwell", original, ddwell_ref=ref)
            self.assertEqual(rewards.patch_reward("ddwell", result), result)
            self.assertEqual(rewards._original_ddwell(result), (original, ref))
            changed_ref = rewards.patch_reward("ddwell", result, ddwell_ref=ref + 1)
            self.assertEqual(rewards._original_ddwell(changed_ref), (original, ref + 1))
            self.assertIn(action(151, 40850, target=1).encode(), result)
            self.assertIn(action(151, ref, target=1).encode(), result)
            with self.assertRaisesRegex(ProfileError, "changed"):
                rewards.patch_reward("ddwell", result.replace(b"AR1202DragonWell", b"AR1202DragonGone", 1))
            with self.assertRaisesRegex(ProfileError, "allocated"):
                rewards.patch_reward("ddwell", original)

    def test_pinned_cache_when_available_and_exact_reexport(self):
        cache = ROOT / "work/original-preset-runtime-20260929/mode2-resources"
        if not cache.exists():
            self.skipTest("Read-only installed-resource cache is not distributed")
        _tlk, allocated = rewards.prepare_reward_texts(make_tlk([]), rewards.RESOURCE_REWARDS)
        for resource in rewards.RESOURCE_REWARDS:
            kind, name = resource.split(":")
            with self.subTest(resource=name):
                original = (cache / (name + "." + kind)).read_bytes()
                self.assertEqual(hashlib.sha256(original).hexdigest(), rewards.ORIGINAL_HASHES[name])
                result = rewards.patch_reward(resource, original, refs=allocated.get(resource))
                if resource not in rewards.TEXT_REFS:
                    self.assertEqual(hashlib.sha256(result).hexdigest(), rewards.PATCHED_HASHES[name])
                self.assertEqual(rewards.patch_reward(resource, result), result)
                recovered = rewards.original_reward(resource, result)
                self.assertEqual(recovered, original if resource in rewards.TEXT_REFS else None)
                if kind == "dlg" and resource in rewards.TEXT_REFS:
                    clones = self.check_text_graph(resource, original, result, allocated[resource])
                    if name in ("trgeni01", "amcler01", "udlesa"):
                        action_refs = allocated[resource] if name == "amcler01" else None
                        branches = rewards._patch_dialogue(original, rewards.resource_specs(resource), action_refs=action_refs)
                        self.check_dialogue(name, original, branches, action_refs)
                        self.assertEqual(set(clones), {"trgeni01": {35}, "amcler01": set(), "udlesa": {6}}[name])
                        if name == "udlesa":
                            dlg = _Dialogue(branches, name)
                            for state in range(6, 12):
                                for row in outgoing(dlg, state):
                                    actions = dlg.actions[struct.unpack_from("<I", row, 16)[0]]
                                    self.assertIn(f'ActionOverride(Player{state - 5},JumpToPoint([608.1123]))'.encode(), actions)
                                    self.assertLess(actions.index(b"FLPR_Transfer("), actions.index(b'SetGlobal("WaitingToDuel"'))
                    if name in ("sctelwyn", "scsain", "scyarryl"):
                        self.assertEqual(set(clones), {19})
                        # External temple-messenger entry states stay in place.
                        self.assertEqual(_Dialogue(original, name).states[27][:4], _Dialogue(result, name).states[27][:4])
                    if name == "ppdjinn":
                        dlg = _Dialogue(result, name)
                        self.assertEqual(len(dlg.states), 2)
                        for state in dlg.states:
                            entry = struct.unpack_from("<I", state, 12)[0]
                            self.assertIn(b"NumTimesTalkedTo(0)", dlg.state_triggers[entry])
                        self.assertIn(b'ReallyForceSpell(Myself,DRYAD_TELEPORT)', b"".join(dlg.actions))
                elif name == "aran":
                    self.check_aran(original, result)
                elif kind == "dlg":
                    self.check_dialogue(name, original, result)


if __name__ == "__main__":
    unittest.main()
