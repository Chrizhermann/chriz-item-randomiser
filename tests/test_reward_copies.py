"""Copies of quest routes' reward replies in other dialogues (WeiDU interjections)."""
import hashlib
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from flir_config import mode2_rewards as rewards
from flir_config import reward_copies
from flir_config.quest_options import _Dialogue
from flir_config.reward_copies import RewardCopies
from test_mode2_rewards import dialogue_fixture, outgoing


def empty_dialogue(name):
    header = bytearray(b"DLG V1.0" + bytes(44))
    struct.pack_into("<10I", header, 8, 0, 52, 0, 52, 52, 0, 52, 0, 52, 0)
    return _Dialogue(bytes(header), name).render()


def interject(data, name, state, npc, npc_data=None, *, copy_actions=True):
    """WeiDU's INTERJECT_COPY_TRANS (or _TRANS2 without <copy_actions>) on a state.

    The state's replies are copied into a new state of the NPC's dialogue,
    which the state now enters first. Returns both dialogues and the new state.
    """
    route = _Dialogue(data, name)
    first, count = struct.unpack_from("<II", route.states[state], 4)
    originals = [bytes(row) for row in route.transitions[first:first + count]]
    other = _Dialogue(npc_data or empty_dialogue(npc), npc)
    copies, moved = [], []
    for row in originals:
        flags, _, _, trigger, action = struct.unpack_from("<5I", row)
        copy = bytearray(row)
        if flags & 2:
            other.triggers.append(route.triggers[trigger])
            struct.pack_into("<I", copy, 12, len(other.triggers) - 1)
        if flags & 4 and copy_actions:
            other.actions.append(route.actions[action])
            struct.pack_into("<I", copy, 16, len(other.actions) - 1)
        elif flags & 4:
            moved.append(route.actions[action])  # _TRANS2 keeps them with the jump.
            struct.pack_into("<I", copy, 0, flags & ~4)
        copies.append(copy)
    target = other.state(99999, copies)
    route.triggers.append(f'InParty("{npc}")\r\nGlobal("{npc}Talk","GLOBAL",0)\r\n'.encode())
    route.actions.append(f'SetGlobal("{npc}Talk","GLOBAL",1)\r\n'.encode() + b"".join(moved[:1]))
    jump = struct.pack("<5I8sI", 2 | 4, 0xFFFFFFFF, 0, len(route.triggers) - 1, len(route.actions) - 1,
                       npc.upper().encode().ljust(8, b"\0"), target)
    rows = [bytearray(jump)] + [bytearray(row) for row in originals]
    struct.pack_into("<II", route.states[state], 4, len(route.transitions), len(rows))
    route.transitions.extend(rows)
    return route.render(), other.render(), target


def link(data, name, state, other, other_state):
    """Add a reply to <state> that continues in another dialogue."""
    dialogue = _Dialogue(data, name)
    first, count = struct.unpack_from("<II", dialogue.states[state], 4)
    row = struct.pack("<5I8sI", 1, 4242, 0, 0, 0, other.upper().encode().ljust(8, b"\0"), other_state)
    rows = dialogue.transitions[first:first + count] + [bytearray(row)]
    struct.pack_into("<II", dialogue.states[state], 4, len(dialogue.transitions), len(rows))
    dialogue.transitions.extend(rows)
    return dialogue.render()


def analyse(files, scanned=None):
    scanned = {"dlg:" + name: hashlib.sha256(data).hexdigest() for name, data in files.items()} \
        if scanned is None else scanned
    return RewardCopies(lambda name: files.get(name), scanned)


def actions_of(data, name, state):
    dialogue = _Dialogue(data, name)
    return [(dialogue.triggers[struct.unpack_from("<I", row, 12)[0]],
             dialogue.actions[struct.unpack_from("<I", row, 16)[0]] if struct.unpack_from("<I", row)[0] & 4 else b"")
            for row in outgoing(dialogue, state)]


class RewardCopyTests(unittest.TestCase):
    def ogre(self):
        spec = rewards.resource_specs("dlg:uhogre01")[0]
        return spec, dialogue_fixture(spec)

    def test_interjection_copies_are_found_and_gated_like_the_route_dialogue(self):
        spec, original = self.ogre()
        route, npc, state = interject(original, "uhogre01", 22, "xnpcj")
        files = {"uhogre01": route, "xnpcj": npc}
        found = analyse(files)
        self.assertEqual(found.copies, {"uhogre01": {"dlg:xnpcj": {state: {0, 1}}}})
        self.assertEqual(found.reasons, {})
        self.assertEqual(found.checked("uhogre01"), {"dlg:xnpcj"})
        self.assertEqual(found.changed, set())
        patched = found.patches(["uhogre01"])["dlg:xnpcj"]
        self.assertEqual(found.patches(["mgkhol01"]), {})
        # Each copy is gated exactly as the route dialogue gates the original.
        gated = rewards.patch_reward("dlg:uhogre01", route)
        self.assertEqual(actions_of(patched, "xnpcj", state), actions_of(gated, "uhogre01", 22)[1:])
        self.assertEqual(sum(b"GiveItemCreate(\"shld26\"" in text for _, text in actions_of(patched, "xnpcj", state)),
                         2)
        self.assertTrue(all(b'Global("FLPR_QM2_14","GLOBAL",1)' in trigger
                            for trigger, _ in actions_of(patched, "xnpcj", state)))
        # The jump into the interjection gives nothing and stays as it was.
        self.assertEqual(outgoing(_Dialogue(gated, "uhogre01"), 22)[0], outgoing(_Dialogue(route, "uhogre01"), 22)[0])

    def test_interject_copy_trans2_keeps_the_grant_in_the_route_dialogue(self):
        spec, original = self.ogre()
        route, npc, state = interject(original, "uhogre01", 22, "xnpcj", copy_actions=False)
        found = analyse({"uhogre01": route, "xnpcj": npc})
        self.assertEqual((found.copies, found.reasons), ({}, {}))
        self.assertEqual(rewards.route_reasons("dlg:uhogre01", route), {})
        gated = _Dialogue(rewards.patch_reward("dlg:uhogre01", route), "uhogre01")
        # The jump gives the reward now, so it is gated as well: three replies, two variants each.
        self.assertEqual(len(outgoing(gated, 22)), 6)

    def test_chained_interjections_and_cycles(self):
        spec, original = self.ogre()
        route, first, state = interject(original, "uhogre01", 22, "xnpca")
        first, second, other = interject(first, "xnpca", state, "xnpcb")
        second = link(second, "xnpcb", other, "xnpca", state)  # back into the first one
        files = {"uhogre01": route, "xnpca": first, "xnpcb": second}
        found = analyse(files)
        # The first one's state now starts with the jump into the second one.
        self.assertEqual(found.copies, {"uhogre01": {"dlg:xnpca": {state: {1, 2}}, "dlg:xnpcb": {other: {0, 1}}}})
        self.assertEqual(found.reasons, {})
        self.assertEqual(set(found.patches(["uhogre01"])), {"dlg:xnpca", "dlg:xnpcb"})

    def test_two_routes_in_one_reply_are_gated_together_in_the_copy(self):
        specs = rewards.resource_specs("dlg:aran")
        original = dialogue_fixture(specs)
        route, npc, state = interject(original, "aran", 4, "k#sarahj")
        found = analyse({"aran": route, "k#sarahj": npc})
        self.assertEqual(found.copies, {key: {"dlg:k#sarahj": {state: set(range(8))}} for key in ("aran", "aran2")})
        patched = found.patches(["aran", "aran2"])["dlg:k#sarahj"]
        gated = rewards.patch_reward("dlg:aran", route)
        self.assertEqual(actions_of(patched, "k#sarahj", state), actions_of(gated, "aran", 4)[1:])
        self.assertEqual(len(actions_of(patched, "k#sarahj", state)), 32)

    def test_replies_the_route_cannot_gate_leave_it_out_with_their_reason(self):
        spec, original = self.ogre()
        route, npc, state = interject(original, "uhogre01", 22, "xnpcj")
        dialogue = _Dialogue(npc, "xnpcj")
        index = struct.unpack_from("<I", outgoing(dialogue, state)[0], 16)[0]
        dialogue.actions[index] = dialogue.actions[index].replace(b"Wait(1)", b"Wait(2)")
        changed = dialogue.render()
        found = analyse({"uhogre01": route, "xnpcj": changed})
        self.assertEqual(found.reasons, {"uhogre01": (
            "XNPCJ.DLG state 0 also gives SHLD26, but not with a copy of a reward reply of UHOGRE01.DLG, "
            "so the route cannot tell whether it is the same reward")})
        # The exact copy there is still gated, for revisions exported earlier.
        self.assertEqual(found.copies, {"uhogre01": {"dlg:xnpcj": {state: {1}}}})
        # Taking, showing or testing the reward elsewhere is no conflict.
        dialogue.actions[index] = b'TakePartyItem("shld26")\r\nDisplayStringHead(Myself,1)\r\n'
        self.assertEqual(analyse({"uhogre01": route, "xnpcj": dialogue.render()}).reasons, {})
        # A copy where CIR changes the dialogue for something else.
        route, npc, state = interject(original, "uhogre01", 22, "hlskull")
        self.assertEqual(analyse({"uhogre01": route, "hlskull": npc}).reasons, {"uhogre01": (
            "HLSKULL.DLG state 0 copies a reward reply of UHOGRE01.DLG, but CIR changes HLSKULL.DLG "
            "for something else")})
        # A carried reward: the copy's speaker does not carry it.
        genie = rewards.resource_specs("dlg:trgeni01")[0]
        route, npc, state = interject(dialogue_fixture(genie), "trgeni01", 35, "xnpcj")
        self.assertEqual(analyse({"trgeni01": route, "xnpcj": npc}).reasons, {"trgeni01": (
            "XNPCJ.DLG state 0 copies a reward reply of TRGENI01.DLG, but there another speaker would hand "
            "over the SW1H23 that TRGENI01.DLG's speaker carries")})
        # A dialogue the route leads into that cannot be read.
        route = link(original, "uhogre01", 3, "broken", 0)
        self.assertEqual(analyse({"uhogre01": route, "broken": b"DLG V1.0" + bytes(10)}).reasons, {"uhogre01": (
            "BROKEN.DLG cannot be read as a dialogue, so the route cannot check it for copies of its reward")})
        # Too many states to follow.
        chain = _Dialogue(empty_dialogue("xnpcj"), "xnpcj")
        for number in range(5):
            chain.state(1, [chain.transition(number + 1, reply=1)])
        route = link(original, "uhogre01", 3, "xnpcj", 0)
        with patch.object(reward_copies, "LIMIT", 3):
            self.assertEqual(analyse({"uhogre01": route, "xnpcj": chain.render()}).reasons, {"uhogre01": (
                "UHOGRE01.DLG lead into more than 3 states of other dialogues; the route cannot check them all "
                "for copies of its reward")})

    def test_another_routes_own_grant_of_the_same_item_stays_that_routes(self):
        # The temple messengers lead into the sculptor, who gives the mace for his own quest.
        temple = dialogue_fixture(rewards.resource_specs("dlg:bhoisig"))
        sculptor = dialogue_fixture(rewards.resource_specs("dlg:scyarryl"))
        files = {"bhoisig": link(temple, "bhoisig", 0, "scyarryl", 19), "scyarryl": sculptor,
                 "bhnalla": dialogue_fixture(rewards.resource_specs("dlg:bhnalla")),
                 "bharval": dialogue_fixture(rewards.resource_specs("dlg:bharval"))}
        found = analyse(files)
        self.assertNotIn("bhoisig", found.reasons)
        self.assertNotIn("bhoisig", found.copies)
        self.assertIn("dlg:scyarryl", found.checked("bhoisig"))

    def test_our_copy_patch_is_recognised_and_a_changed_one_named(self):
        spec, original = self.ogre()
        route, npc, state = interject(original, "uhogre01", 22, "xnpcj")
        before = {"uhogre01": route, "xnpcj": npc}
        found = analyse(before)
        applied = {"uhogre01": rewards.patch_reward("dlg:uhogre01", route),
                   "xnpcj": found.patches(["uhogre01"])["dlg:xnpcj"]}
        # The scan before Apply, and a rescan after it, both accept our patch.
        for scanned in (None, {"dlg:" + name: hashlib.sha256(data).hexdigest() for name, data in before.items()}):
            again = analyse(applied, scanned)
            self.assertEqual((again.copies, again.reasons, again.changed), (found.copies, {}, set()))
            self.assertEqual(again.patches(["uhogre01"]), {"dlg:xnpcj": applied["xnpcj"]})
        tampered = dict(applied, xnpcj=applied["xnpcj"].replace(b"FLPR_", b"OTHER", 1))
        again = analyse(tampered)
        self.assertEqual(again.reasons, {"uhogre01": "XNPCJ.DLG carries a changed copy of the reward route's own patch"})
        # Another change since the scan.
        again = analyse(dict(before, xnpcj=npc + b"\0"), {"dlg:" + name: hashlib.sha256(data).hexdigest()
                                                           for name, data in before.items()})
        self.assertEqual(again.changed, {"dlg:xnpcj"})
        gone = analyse({"uhogre01": route}, {"dlg:uhogre01": hashlib.sha256(route).hexdigest(),
                                             "dlg:xnpcj": hashlib.sha256(npc).hexdigest()})
        self.assertEqual(gone.changed, {"dlg:xnpcj"})


if __name__ == "__main__":
    unittest.main()
