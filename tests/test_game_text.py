"""Shared DLG table and game-text helpers; never discover or change a game install."""

from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from flir_config import game_text
from flir_config.resources import _Tlk
from test_postinstall_resources import make_tlk


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


class GameTextTests(unittest.TestCase):
    def test_new_text_is_appended_once_without_rewriting_existing_entries(self):
        original = bytearray(make_tlk(["An ordinary magical belt", "old journal", "Reused text"]))
        # A voiced entry with the same text is not reused for text-only output.
        struct.pack_into("<H8sII", original, 0x12, 3, b"BELTVOX", 11, 12)
        # Real TLKs can have padding between entries and string data.
        data_offset = struct.unpack_from("<I", original, 0xE)[0]
        original[data_offset:data_offset] = b"PADDING"
        struct.pack_into("<I", original, 0xE, data_offset + 7)
        original = bytes(original)
        tlk, refs = game_text._append_texts(
            original, ("New text", "Reused text", "An ordinary magical belt", "New text"))
        before, after = _Tlk(original), _Tlk(tlk)
        self.assertEqual(after.count, before.count + 2)
        self.assertEqual(refs, [3, 2, 4, 3])
        self.assertEqual(tlk[8:10], original[8:10])
        self.assertEqual(tlk[0x12:data_offset], original[0x12:data_offset])
        self.assertEqual(tlk[data_offset + 2 * 26:after.offset], b"PADDING")
        self.assertTrue(tlk[after.offset:].startswith(original[before.offset:]))
        for ref, text in ((3, "New text"), (4, "An ordinary magical belt"), (2, "Reused text")):
            self.assertEqual(after.text(ref, set()), text)
        self.assertEqual(game_text._append_texts(tlk, ("New text",)), (tlk, [3]))

    def test_a_text_gets_one_number_in_both_files_with_its_female_wording(self):
        male = make_tlk(["Shared", "Same", "Gendered", "Voiced"])
        female = make_tlk(["Shared (f)", "Same", "Gendered (f)", "Voiced"])
        text, refs = game_text.GameText(male, female).append((
            ("Same", "Same"), ("Gendered", "Gendered (f)"),
            # dialogf.tlk words entry 0 otherwise: a new entry.
            ("Shared", "Shared"),
            ("New", "New (f)"), ("New", "New (f)")))
        self.assertEqual(refs, [1, 2, 4, 5, 5])
        new_male, new_female = _Tlk(text.male), _Tlk(text.female)
        self.assertEqual([new_male.text(n, set()) for n in range(new_male.count)],
                         ["Shared", "Same", "Gendered", "Voiced", "Shared", "New"])
        self.assertEqual([new_female.text(n, set()) for n in range(new_female.count)],
                         ["Shared (f)", "Same", "Gendered (f)", "Voiced", "Shared", "New (f)"])
        self.assertEqual(text.append((("New", "New (f)"),)), (text, [5]))
        # Without dialogf.tlk the game has one text: the female wording is not used.
        alone, refs = game_text.GameText(male).append((("Gendered", "Other"), ("New", "New (f)")))
        self.assertEqual((alone.female, refs), (None, [2, 4]))
        self.assertEqual(alone.male, game_text._append_texts(male, ("Gendered", "New"))[0])
        with self.assertRaisesRegex(game_text.GameTextError, "different numbers of strings"):
            game_text.GameText(male, make_tlk(["Shared (f)"])).append((("New", "New"),))

    def test_string_reads_an_entry_from_both_files(self):
        male = bytearray(make_tlk(["One", "Two", "Three", "x"]))
        female = make_tlk(["One (f)", "Two (f)", "Three (f)", "x"])
        # dialog.tlk's entry 2 has no text; entry 3's is not UTF-8.
        struct.pack_into("<H", male, 0x12 + 2 * 0x1A, 0)
        male[-1:] = b"\xe9"
        text = game_text.GameText(bytes(male), female)
        self.assertEqual(text.string(1), ("Two", "Two (f)"))
        self.assertEqual(text.string(2), (None, "Three (f)"))
        self.assertEqual(text.string(3), (None, "x"))
        for missing in (4, -1):
            self.assertEqual(text.string(missing), (None, None))
        self.assertEqual(game_text.GameText(bytes(male)).string(0), ("One", None))
        with self.assertRaisesRegex(game_text.GameTextError, "TLK V1"):
            game_text.GameText(b"invalid").string(0)

    def test_paired_copies_what_dialog_tlk_alone_gained(self):
        male, female = make_tlk(["One", "Two"]), make_tlk(["One (f)", "Two (f)"])
        grown = bytearray(game_text._append_texts(male, ("Three", "Four"))[0])
        # Entries are copied as they are, sound and all.
        struct.pack_into("<H8sII", grown, 0x12 + 3 * 0x1A, 3, b"FOURVOX", 5, 6)
        grown = bytes(grown)
        text = game_text.paired(grown, female)
        self.assertEqual(text.male, grown)
        copied = _Tlk(text.female)
        self.assertEqual([copied.text(n, set()) for n in range(copied.count)], ["One (f)", "Two (f)", "Three", "Four"])
        self.assertEqual(text.female[0x12 + 3 * 0x1A:0x12 + 3 * 0x1A + 0x12], grown[0x12 + 3 * 0x1A:0x12 + 3 * 0x1A + 0x12])
        self.assertEqual(game_text.paired(grown, text.female), text)
        self.assertEqual(game_text.paired(male, None), game_text.GameText(male))
        with self.assertRaisesRegex(game_text.GameTextError, "beyond dialog.tlk's end"):
            game_text.paired(male, text.female)

    def test_the_game_text_files_of_a_language(self):
        with tempfile.TemporaryDirectory(prefix="flir-game-text-") as temporary:
            root = Path(temporary)
            folder = root / "lang" / "de_DE"
            folder.mkdir(parents=True)
            male = folder / "dialog.tlk"
            male.write_bytes(make_tlk(["a", "b"]))
            self.assertEqual(game_text.text_files(root, "de_DE"), (male, None))
            self.assertIsNone(game_text.unpaired(male, None))
            (folder / "DIALOGF.TLK").write_bytes(make_tlk(["a (f)", "b (f)"]))
            self.assertEqual(game_text.text_files(root, "de_DE"), (male, folder / "DIALOGF.TLK"))
            female = folder / "DIALOGF.TLK"
            self.assertIsNone(game_text.unpaired(male, female))
            female.write_bytes(make_tlk(["a (f)"]))
            self.assertEqual(game_text.unpaired(male, female), "the game's DIALOGF.TLK and dialog.tlk hold different "
                             "numbers of strings (1 and 2), so a new text cannot get the same number in both")
            female.write_bytes(b"TLK V1")
            self.assertEqual(game_text.unpaired(male, female), "the game's DIALOGF.TLK is not a TLK V1 file")
            (root / "dialog.tlk").write_bytes(make_tlk([]))
            self.assertEqual(game_text.text_files(root, "default"), (root / "dialog.tlk", None))

    def test_invalid_game_text_and_dialogue_layouts_are_rejected(self):
        with self.assertRaisesRegex(game_text.GameTextError, "TLK"):
            game_text._append_texts(b"invalid", ("text",))
        data = b"DLG V1.0" + bytes(0x40)
        with self.assertRaisesRegex(game_text.GameTextError, "table"):
            game_text._table(data, 0x30, 2, 16)
        with self.assertRaisesRegex(game_text.GameTextError, "string"):
            game_text._string(data, struct.pack("<II", 0x40, 16))
        self.assertEqual(game_text._table(data, 0x30, 1, 16), [bytes(16)])
        self.assertEqual(game_text._string(data, struct.pack("<II", 0x30, 8)), bytes(8))


if __name__ == "__main__":
    unittest.main()
