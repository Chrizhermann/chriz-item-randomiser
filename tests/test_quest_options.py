"""Finite original-option DLG graphs and compiled crypt branches, offline."""
import hashlib
from pathlib import Path
import re
import struct
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from flir_config import quest_options as options, translations
from flir_config.game_text import GameText
from flir_config.profiles import ProfileError
from flir_config.resources import _Tlk
from test_postinstall_resources import make_tlk
from test_game_text import parse_dialogue


def dialogue_fixture(name, action=b'SetGlobal("KangaxxQuest","GLOBAL",1)\r\n'):
    kangaxx = name == "hlskull.dlg"
    states, transitions = [], []
    count = 16 if kangaxx else 10
    resource = name.split(".")[0].upper().encode()
    for index in range(count):
        first = len(transitions)
        n = 2 if kangaxx else (3 if index == 0 else 1)
        for ordinal in range(n):
            target = 8 if kangaxx and index in (5, 6) else 6 if not kangaxx and index == 5 else 0
            if kangaxx and index == 10 and ordinal == 0:
                transitions.append(struct.pack("<5I8sI", 92, 0xFFFFFFFF, 54293, 0, 0, b"", 0))
            else:
                transitions.append(struct.pack("<5I8sI", 0, 0xFFFFFFFF, 0, 0, 0, resource, target))
        states.append(struct.pack("<4I", 500 + index, first, n, 0xFFFFFFFF))
    data = bytearray(b"DLG V1.0" + bytes(0x34 - 8))
    so = len(data); data.extend(b"".join(states))
    to = len(data); data.extend(b"".join(transitions))
    text = len(data); data.extend(action)
    ao = len(data); data.extend(struct.pack("<II", text, len(action)))
    struct.pack_into("<10I", data, 8, len(states), so, len(transitions), to,
                     ao, 0, ao, 0, ao, 1)
    return bytes(data)


def crypt_fixture(area):
    empty = '0 0 0 0 0 0 0 0 0 0 0 0 ""OB\n'
    def trigger(value):
        return 'TR\n' + value + '\n' + empty + 'TR\n'
    def action(opcode, args, myself=False):
        obj = '0 0 0 0 0 0 0 1 0 0 0 0 ""OB\n' if myself else empty
        return 'AC\n' + str(opcode) + 'OB\n' + empty + 'OB\n' + obj + 'OB\n' + empty + args + ' AC\n'
    first = ('CR\nCO\n' + trigger('112 0 0 0 0 "" "" OB') +
             trigger('16408 12 0 0 0 "" "" OB') +
             trigger('16399 0 0 0 0 "' + area + 'UsedCrypt" "" OB') +
             'CO\nRS\nRE\n100' + action(30, '1 0 0 0 0"' + area + 'UsedCrypt" ""') +
             action(269, '35858 0 0 0 0"" ""', True) + 'RE\nRS\nCR\n')
    later = 'CR\nCO\n' + trigger('16399 3 0 0 0 "' + area + 'UsedCrypt" "" OB') + 'CO\nRS\nRE\n100' + action(269, '123 0 0 0 0"" ""', True) + 'RE\nRS\nCR\n'
    return ('SC\n' + first + later * 5 + 'SC\n').encode()


def outgoing(dialogue, state):
    first, count = dialogue["states"][state][1:3]
    return dialogue["transitions"][first:first + count]


class OriginalQuestOptionTests(unittest.TestCase):
    def setUp(self):
        self.resources = {"hlskull.dlg": dialogue_fixture("hlskull.dlg")}
        self.resources.update({"crypt526.bcs": crypt_fixture("AR0526"),
                               "crypt202.bcs": crypt_fixture("AR0202")})
        patched = patch.dict(options.ORIGINAL_HASHES,
                             {name: hashlib.sha256(data).hexdigest() for name, data in self.resources.items()})
        patched.start(); self.addCleanup(patched.stop)
        self.refs = dict(enumerate(range(1000, 1022)))

    def test_kangaxx_original_dialogue_choices_and_actions_remain_for_excluded_campaign(self):
        old = parse_dialogue(self.resources["hlskull.dlg"])
        data = options.patch_hlskull(self.resources["hlskull.dlg"], self.refs)
        new = parse_dialogue(data)
        self.assertEqual(new["transitions"][:32], old["transitions"])
        self.assertEqual(new["actions"][:len(old["actions"])], old["actions"])
        for state in (5, 6, 10):
            original = outgoing(old, state)[0]
            excluded, selected = outgoing(new, state)[:2]
            self.assertEqual(excluded[:3], (original[0] | 2, original[1], original[2]))
            self.assertEqual(excluded[4:], original[4:])
            self.assertEqual(new["transition_triggers"][excluded[3]], b'!Global("FLPR_KANGAXX","GLOBAL",1)\r\n')
            self.assertEqual(new["transition_triggers"][selected[3]], b'Global("FLPR_KANGAXX","GLOBAL",1)\r\n')
            if state in (5, 6): self.assertEqual(selected[6], 16)
            else: self.assertEqual(selected[2], self.refs[20])
        self.assertEqual(options.original_dialogue("hlskull.dlg", data), self.resources["hlskull.dlg"])
        self.assertEqual(options.patch_hlskull(data, self.refs), data)

    def test_kangaxx_takes_bg2ees_own_dialogue_too(self):
        """BG2:EE's own HLSKULL.DLG has EET's states and transitions; only the
        texts of its triggers and actions differ, which the patch copies."""
        game = dialogue_fixture("hlskull.dlg", action=b'SetGlobal("KangaxxQuest","GLOBAL",1)')
        eet = parse_dialogue(options.patch_hlskull(self.resources["hlskull.dlg"], self.refs))
        with self.assertRaisesRegex(ProfileError, "^hlskull.dlg changed; review original-option dialogue$"):
            options.patch_hlskull(game, self.refs)
        with patch.dict(options.BG2EE_HASHES, {"hlskull.dlg": hashlib.sha256(game).hexdigest()}):
            data = options.patch_hlskull(game, self.refs)
            new = parse_dialogue(data)
            self.assertEqual((new["states"], new["transitions"], new["transition_triggers"]),
                             (eet["states"], eet["transitions"], eet["transition_triggers"]))
            self.assertEqual(new["actions"][0], b'SetGlobal("KangaxxQuest","GLOBAL",1)')
            self.assertEqual(new["actions"][1:], eet["actions"][1:])
            self.assertEqual(options.original_dialogue("hlskull.dlg", data), game)
            self.assertEqual(options.patch_hlskull(data, self.refs), data)
            journal = (b'function buildQuestsTable()\r\n\tcreateQuest ( 74458 )\r\n'
                       b'\tcreateEntry ( 74458, -1, 54293, {}, nil )\r\nend\r\n')
            self.assertIn(b'createEntry    ( 74458, -1, 1020, {}, nil )', options.patch_kangaxx_journal(journal, data))
            output, _ = options.prepare_quest_options({**self.resources, "hlskull.dlg": game},
                                                      GameText(make_tlk(["old"])), kangaxx=True)
            self.assertEqual(options.original_dialogue("hlskull.dlg", output["hlskull.dlg"]), game)

    def test_kangaxx_new_key_dialogue_has_exact_choices_and_journal_exit(self):
        new = parse_dialogue(options.patch_hlskull(self.resources["hlskull.dlg"], self.refs))
        self.assertEqual(len(new["states"]), 23)
        self.assertEqual([row[1] for row in outgoing(new, 16)], [self.refs[1], self.refs[2], 56194])
        keys = outgoing(new, 18)
        self.assertEqual([row[6] for row in keys], [10, 21, 19, 20, 6])
        self.assertEqual(new["transition_triggers"][keys[2][3]], b'PartyHasItem("flrward1")\r\n')
        self.assertEqual(new["transition_triggers"][keys[3][3]], b'!PartyHasItem("flrward1")\r\n')
        final = outgoing(new, 22)[0]
        self.assertEqual(final[0], 92)
        self.assertEqual(final[2], self.refs[20])
        self.assertEqual(new["actions"][final[4]], b'SetGlobal("KangaxxQuest","GLOBAL",1)\r\n')

    def test_crypt_enforces_one_ward_and_quest_only_for_selected_campaign(self):
        for name in ("crypt526.bcs", "crypt202.bcs"):
            original = self.resources[name]
            data = options.patch_crypt(name, original, {1500: 100, 1501: 101})
            blocks, _prefix, _suffix = options._blocks(data, 8, b"\n")
            self.assertIn(b'16399 1 0 0 0 "GLOBALFLPR_KANGAXX"', blocks[0])
            self.assertIn(b'16521 2 0 0 0 "" "" OB', blocks[0])
            self.assertIn(b'16450 0 1 0 0 "flrward1"', blocks[0])
            self.assertIn(b'16399 1 1 0 0 "GLOBALKangaxxQuest"', blocks[0])
            self.assertNotIn(b'116OB', blocks[0])
            self.assertIn(b'16450 0 0 0 0 "flrward1"', blocks[1])
            self.assertIn(b'16399 1 0 0 0 "GLOBALKangaxxQuest"', blocks[1])
            self.assertEqual(blocks[1].count(b'116OB'), 1)
            self.assertIn(b'16399 1 1 0 0 "GLOBALFLPR_KANGAXX"', blocks[2])
            self.assertNotIn(b'flrward1', blocks[2])
            old_blocks, _, _ = options._blocks(original, 6, b"\n")
            self.assertEqual(blocks[3:], old_blocks[1:])
            self.assertEqual(options.original_crypt(name, data), original)
            self.assertEqual(options.patch_crypt(name, data, {1500: 100, 1501: 101}), data)

    def test_complete_prepare_uses_authored_last_ward_text_and_is_idempotent(self):
        output, text = options.prepare_quest_options(self.resources, GameText(make_tlk(["old"])), kangaxx=True)
        self.assertEqual(set(output), set(self.resources) | {"flrward1.itm", "flrward1.bam"})
        self.assertIsNone(text.female)
        parsed = _Tlk(text.male)
        ward = output["flrward1.itm"]
        self.assertEqual(ward[0x3A:0x42], b"flrward1")
        self.assertEqual(parsed.text(struct.unpack_from("<I", ward, 8)[0], set()), "Ward Stone")
        description = parsed.text(struct.unpack_from("<I", ward, 0x54)[0], set())
        self.assertTrue(description.startswith("The cardinal points"))
        self.assertEqual(options.prepare_quest_options(output, text, kangaxx=True), (output, text))

    def test_no_selected_options_changes_nothing(self):
        original = GameText(make_tlk(["old"]), make_tlk(["old"]))
        self.assertEqual(options.prepare_quest_options({}, original), ({}, original))
        self.assertEqual(options.prepare_quest_options({}, original, language="de_de"), ({}, original))

    def test_kangaxx_texts_are_the_games_language_or_english(self):
        """German and Polish games get their own texts, a female protagonist her
        wording in dialogf.tlk; any other language gets the English ones."""
        def prepared(language):
            text = GameText(make_tlk(["old"]), make_tlk(["old (f)"]))
            output, text = options.prepare_quest_options(self.resources, text, kangaxx=True, language=language)
            male, female = _Tlk(text.male), _Tlk(text.female)
            self.assertEqual(male.count, female.count)
            appended = [(male.text(ref, set()), female.text(ref, set())) for ref in range(1, male.count)]
            ward = output["flrward1.itm"]
            return male.text(struct.unpack_from("<I", ward, 8)[0], set()), appended

        def lines(language):
            return translations.texts(language, "hlskull.tra"), translations.texts(language, "in-game.tra")

        for language, name, own in (("de_DE", "Bannstein", "de_DE"), ("pl_PL", None, "pl_PL"),
                                    ("es_ES", "Ward Stone", "en_US"), ("default", "Ward Stone", "en_US")):
            with self.subTest(language=language):
                ward, appended = prepared(language)
                dialogue, items = lines(own)
                expected = [dialogue[key] for key in range(22)] + [items[key] for key in range(1500, 1505)]
                # Each text once (two of the conversation's replies read the same).
                self.assertEqual(appended, list(dict.fromkeys(expected)))
                self.assertEqual(ward, items[1503][0])
                if name:
                    self.assertEqual(ward, name)
        # Polish addresses a female protagonist differently in four lines.
        polish = [key for key, (male, female) in lines("pl_PL")[0].items() if male != female]
        self.assertEqual(polish, [4, 7, 16, 20])
        self.assertIn(lines("pl_PL")[0][4], prepared("pl_PL")[1])

    def test_new_kangaxx_journal_joins_existing_quest_and_rejects_conflicting_registration(self):
        dialogue = options.patch_hlskull(self.resources["hlskull.dlg"], self.refs)
        original = (b'function buildQuestsTable()\r\n\tcreateQuest ( 74458 )\r\n'
                    b'\tcreateEntry ( 74458, -1, 54293, {}, nil )\r\nend\r\n')
        patched = options.patch_kangaxx_journal(original, dialogue)
        self.assertIn(b'createEntry    ( 74458, -1, 1020, {}, nil )', patched)
        self.assertEqual(options.patch_kangaxx_journal(patched, dialogue), patched)
        self.assertEqual(patched.replace(b'\tcreateEntry    ( 74458, -1, 1020, {}, nil )\r\n', b''), original)
        with self.assertRaisesRegex(ProfileError, 'another quest'):
            options.patch_kangaxx_journal(original + b'createEntry ( 9, -1, 1020, {}, nil )\r\n', dialogue)

    def test_changed_source_or_own_generated_bytes_fail_closed(self):
        dlg = options.patch_hlskull(self.resources["hlskull.dlg"], self.refs)
        for offset in (0x34, len(self.resources["hlskull.dlg"]) + 3, len(dlg) - 1):
            changed = bytearray(dlg); changed[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises((ProfileError, ValueError)):
                options.original_dialogue("hlskull.dlg", bytes(changed))
        with self.assertRaises(ProfileError):
            options.patch_crypt("crypt526.bcs", self.resources["crypt526.bcs"] + b"changed", {1500: 1, 1501: 2})


if __name__ == "__main__":
    unittest.main()
