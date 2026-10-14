"""Shared DLG table and game-text helpers; never discover or change a game install."""

from pathlib import Path
import struct
import sys
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
