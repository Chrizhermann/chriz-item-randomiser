"""Portable Unshey quest/TLK checks; never discover or change a game install."""

import hashlib
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
from flir_config import unshey_reward as unshey
from flir_config.resources import _Tlk
from test_postinstall_resources import make_tlk


def dialogue_fixture():
    """Synthetic reviewed layout with distinct strrefs and modified XP reward."""
    data = bytearray(b"DLG V1.0" + bytes(0x34 - 8))
    triggers = [
        b"ReactionLT(LastTalkedToBy,NEUTRAL_LOWER)\r\nNumTimesTalkedTo(0)\r\n",
        b"ReactionGT(LastTalkedToBy,HOSTILE_UPPER)\r\nNumTimesTalkedTo(0)\r\n",
        b'!NumTimesTalkedTo(0)\r\n!PartyHasItem("BELT04")\r\n',
        b'PartyHasItem("BELT04")\r\nReactionLT(LastTalkedToBy,FRIENDLY_LOWER)\r\n',
        b'PartyHasItem("BELT04")\r\nReactionGT(LastTalkedToBy,NEUTRAL_UPPER)\r\n',
        b"False()\r\n", b"True()\r\n",
    ]
    actions = [
        b'AddexperienceParty(800)\r\nGivePartyGold(70)\r\nTakePartyItem("BELT04")\r\nEraseJournalEntry(20)\r\n',
        b'AddexperienceParty(800)\r\nGivePartyGold(95)\r\nTakePartyItem("BELT04")\r\nEraseJournalEntry(20)\r\nGiveItem("BOOK32",LastTalkedToBy)\r\n',
    ]

    def text_rows(strings):
        rows = []
        for text in strings:
            rows.append(struct.pack("<II", len(data), len(text)))
            data.extend(text)
        return rows

    def table(rows):
        offset = len(data)
        data.extend(b"".join(rows))
        return offset

    trigger_rows, action_rows = text_rows(triggers), text_rows(actions)
    state_off = table([struct.pack("<4I", 10 + i, i, 1, trigger)
                       for i, trigger in enumerate((0, 1, 2, 0xFFFFFFFF, 3, 4, 0xFFFFFFFF, 5, 6))])
    trans_off = table([struct.pack("<5I8sI", flags, 0xFFFFFFFF,
                                   20 if i == 1 else 30 + i, 0, 1 if i == 5 else 0, b"", 0)
                       for i, flags in enumerate((8, 88, 8, 8, 284, 284, 280, 8, 8))])
    trigger_off, action_off = table(trigger_rows), table(action_rows)
    struct.pack_into("<10I", data, 8, 9, state_off, 9, trans_off,
                     trigger_off, 7, action_off, 0, action_off, 2)
    return bytes(data)


def parse_dialogue(data):
    n, s, m, t, st, sn, tt, tn, a, an = struct.unpack_from("<10I", data, 8)

    def strings(offset, count):
        return [data[start:start + size] for start, size in
                (struct.unpack_from("<II", data, offset + i * 8) for i in range(count))]

    return {
        "states": [struct.unpack_from("<4I", data, s + i * 16) for i in range(n)],
        "transitions": [struct.unpack_from("<5I8sI", data, t + i * 32) for i in range(m)],
        "triggers": strings(st, sn), "actions": strings(a, an),
        "transition_triggers": strings(tt, tn),
    }


class UnsheyRewardTests(unittest.TestCase):
    def setUp(self):
        self.original = dialogue_fixture()
        replacement = patch.object(unshey, "ORIGINAL_SHA256", hashlib.sha256(self.original).hexdigest())
        replacement.start()
        self.addCleanup(replacement.stop)
        self.item = (ROOT / "copy" / "flrbelt1.itm").read_bytes()

    def test_excluded_campaign_preserves_ordinary_belt_rewards_and_journal(self):
        old = parse_dialogue(self.original)
        new = parse_dialogue(unshey.patch_unshey(self.original, quest_strref=100, journal_strref=101))
        self.assertEqual(new["states"][:9], old["states"])
        self.assertEqual(new["transitions"][:9], old["transitions"])
        self.assertEqual(new["actions"][:2], old["actions"])
        self.assertEqual(new["transition_triggers"], [])
        for before, after in zip(old["triggers"], new["triggers"][:7]):
            self.assertEqual(after, before + b'!Global("FLPR_QUNSHEY","GLOBAL",1)\r\n')

    def test_selected_campaign_gets_accurate_intro_and_both_hand_in_branches(self):
        old = parse_dialogue(self.original)
        new = parse_dialogue(unshey.patch_unshey(self.original, quest_strref=100, journal_strref=101))
        self.assertEqual(new["states"][10][0], 100)
        self.assertEqual(new["transitions"][10][2], 101)
        self.assertEqual(new["transitions"][10][0], 88)  # Unsolved journal + exit.
        for state_id, action_id in ((4, 0), (5, 1)):
            with self.subTest(reaction_branch=state_id):
                state = new["states"][9 + state_id]
                self.assertEqual(state[:3], (old["states"][state_id][0], 9 + state_id, 1))
                trigger = new["triggers"][state[3]]
                self.assertIn(b'PartyHasItem("flrbelt1")', trigger)
                self.assertNotIn(b"BELT04", trigger)
                self.assertTrue(trigger.endswith(b'Global("FLPR_QUNSHEY","GLOBAL",1)\r\n'))
                transition = new["transitions"][state[1]]
                self.assertEqual(transition[:4], old["transitions"][state_id][:4])
                self.assertEqual(new["actions"][transition[4]],
                                 old["actions"][action_id].replace(b'"BELT04"', b'"flrbelt1"')
                                 .replace(b"EraseJournalEntry(20)", b"EraseJournalEntry(101)"))
        # A relocated magical belt does not complete the selected plain-belt quest.
        self.assertIn(b'!PartyHasItem("flrbelt1")', new["triggers"][9])
        self.assertNotIn(b"BELT04", new["triggers"][9])
        # Original fallback cannot swallow selected-campaign introductions.
        self.assertIn(b'!Global("FLPR_QUNSHEY"', new["triggers"][6])
        self.assertIn(b'Global("FLPR_QUNSHEY"', new["triggers"][13])

    def test_complete_resources_append_exact_text_without_rewriting_existing_tlk(self):
        original_tlk = bytearray(make_tlk(["An ordinary magical belt", "old journal", "old description"]))
        struct.pack_into("<H8sII", original_tlk, 0x12, 3, b"BELTVOX", 11, 12)
        # Real TLKs can have padding between entries and string data.
        old_data_offset = struct.unpack_from("<I", original_tlk, 0xE)[0]
        original_tlk[old_data_offset:old_data_offset] = b"PADDING"
        struct.pack_into("<I", original_tlk, 0xE, old_data_offset + 7)
        original_tlk = bytes(original_tlk)
        dlg, item, tlk = unshey.prepare_unshey_resources(self.original, self.item, original_tlk)
        before, after = _Tlk(original_tlk), _Tlk(tlk)
        self.assertEqual(after.count, before.count + 4)
        self.assertEqual(tlk[8:10], original_tlk[8:10])
        self.assertEqual(tlk[0x12:old_data_offset], original_tlk[0x12:old_data_offset])
        self.assertEqual(tlk[old_data_offset + 4 * 26:after.offset], b"PADDING")
        self.assertTrue(tlk[after.offset:].startswith(original_tlk[before.offset:]))
        for offset, text in ((8, unshey.ITEM_NAME), (12, unshey.ITEM_NAME), (0x50, unshey.ITEM_DESCRIPTION)):
            self.assertEqual(after.text(struct.unpack_from("<I", item, offset)[0], set()), text)
        restored_item = bytearray(item)
        for offset in (8, 12, 0x50):
            restored_item[offset:offset + 4] = self.item[offset:offset + 4]
        self.assertEqual(restored_item, self.item)
        parsed = parse_dialogue(dlg)
        self.assertEqual(after.text(parsed["states"][10][0], set()), unshey.QUEST_TEXT)
        self.assertEqual(after.text(parsed["transitions"][10][2], set()), unshey.JOURNAL_TEXT)
        # Re-export from the original DLG reuses exactly the existing text refs.
        self.assertEqual(unshey.prepare_unshey_resources(self.original, item, tlk), (dlg, item, tlk))
        self.assertEqual(unshey.prepare_unshey_resources(dlg, item, tlk), (dlg, item, tlk))

    def test_own_patch_recovery_checks_original_and_every_generated_byte(self):
        first = unshey.patch_unshey(self.original, quest_strref=100, journal_strref=101)
        self.assertEqual(unshey.original_unshey(first), self.original)
        moved = unshey.patch_unshey(first, quest_strref=200, journal_strref=201)
        self.assertEqual(moved, unshey.patch_unshey(self.original, quest_strref=200, journal_strref=201))
        # Fail closed for original-body, generated-body, and trailer alterations.
        for offset in (0x34, len(self.original) + 5, len(first) - 1):
            altered = bytearray(first)
            altered[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(unshey.UnsheyPatchError):
                unshey.original_unshey(bytes(altered))
        with self.assertRaises(unshey.UnsheyPatchError):
            unshey.original_unshey(first + b"extra")

    def test_strings_match_original_english_translations(self):
        source = (ROOT / "languages" / "english" / "in-game.tra").read_bytes()
        for number, text in ((112, unshey.QUEST_TEXT), (113, unshey.JOURNAL_TEXT),
                             (114, unshey.ITEM_NAME), (115, unshey.ITEM_DESCRIPTION)):
            value = re.search(rb"@" + str(number).encode() + rb'="(.*?)"', source, re.S)[1]
            self.assertEqual(value, text.encode("utf-8"))

    def test_ee_journal_registers_new_entry_under_original_quest_without_losing_rewards(self):
        original = (b"function buildQuestsTable()\n"
                    b"\tcreateQuest    ( 231285 )\n"
                    b"\tcreateEntry    ( 231285, -1, 20, {}, nil )\n"
                    b"\tcreateEntry    ( 231285, -1, 34, {}, nil )\n"
                    b"\tcreateEntry    ( 231285, -1, 35, {}, nil )\nend\n")
        dlg = unshey.patch_unshey(self.original, quest_strref=100, journal_strref=101)
        patched = unshey.patch_unshey_journal(original, dlg)
        added = b"\tcreateEntry    ( 231285, -1, 101, {}, nil )\n"
        self.assertEqual(patched.replace(added, b""), original)
        self.assertEqual(unshey.patch_unshey_journal(patched, dlg), patched)
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime()
            lua.execute("registered={}\n"
                        "function createQuest(title) registered[title]={} end\n"
                        "function createEntry(title,group,ref) registered[title][ref]=group end\n")
            lua.execute(patched.decode("ascii"))
            lua.execute("buildQuestsTable()")
            for ref in (20, 34, 35, 101):
                self.assertEqual(lua.globals().registered[231285][ref], -1)
        with self.assertRaisesRegex(unshey.UnsheyPatchError, "existing"):
            unshey.patch_unshey_journal(original.replace(b"-1, 20,", b"-1, 21,"), dlg)

    def test_unknown_dialogue_and_invalid_tlk_or_language_are_rejected(self):
        with self.assertRaisesRegex(unshey.UnsheyPatchError, "changed"):
            unshey.patch_unshey(self.original + b"changed", quest_strref=100, journal_strref=101)
        with self.assertRaisesRegex(unshey.UnsheyPatchError, "TLK"):
            unshey.prepare_unshey_resources(self.original, self.item, b"invalid")
        with self.assertRaisesRegex(unshey.UnsheyPatchError, "English"):
            unshey.prepare_unshey_resources(self.original, self.item, make_tlk([]), language="de_de")


if __name__ == "__main__":
    unittest.main()
