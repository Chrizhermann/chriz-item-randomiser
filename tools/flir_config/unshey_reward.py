"""Build the reviewed EET Unshey quest resources outside the game, in memory.

FLPR_QUNSHEY=1 selects the original mod's plain-belt quest. Other campaigns
retain the installed BELT04 quest, including its reward and journal actions.
The caller owns setting the campaign flag and writing the returned resources.
"""
from __future__ import annotations

import hashlib
import re
import struct

from .resources import ScanError, _Tlk

ORIGINAL_SHA256 = "f781403db358acf40f2a3f76e94fb4e4c828693915fa5700e734d409ab6fd5ca"
SELECTED_GLOBAL = "FLPR_QUNSHEY"
_TRAILER_MAGIC = b"FLPRUNS1"
_TRAILER_SIZE = 8 + 4 + 0x34

# Exact English @112-115 from languages/english/in-game.tra. The BG:EE @115
# override is identical. Preserve the TRA files' CRLF inside multiline strings.
QUEST_TEXT = (
    "Have you heard? There's a rogue ogre with a belt fetish to the south of the "
    "Friendly Arm Inn. I had to bargain my girdle for my life, out there. Wasn't "
    "even interested in an autographed copy of my book. Hey, if you can get that "
    "belt back to me, I'd be mighty grateful."
)
JOURNAL_TEXT = (
    "A rogue ogre\r\n\r\nAccording to a dwarven cleric by the name of Unshey, a rogue "
    "ogre with a belt fetish is making a name for himself just south of the "
    "Friendly Arm Inn.  As Unshey claims to be an author, maybe she'd give me a "
    "copy of her book if I can get her girdle back..."
)
ITEM_NAME = "Unshey's girdle"
ITEM_DESCRIPTION = (
    "This elegant girdle was seemingly made with great care.  The silver buckle "
    "alone should make it quite valuable.  The name 'Unshey' is embroidered on it."
    "\r\n\r\nSTATISTICS:\r\n\r\nWeight:  2"
)


class UnsheyPatchError(ValueError):
    pass


def _table(data: bytes, offset: int, count: int, stride: int) -> list[bytes]:
    if offset < 0x30 or offset + count * stride > len(data):
        raise UnsheyPatchError("Unsupported Unshey dialogue table layout")
    return [data[offset + stride * i:offset + stride * (i + 1)] for i in range(count)]


def _string(data: bytes, pair: bytes) -> bytes:
    offset, length = struct.unpack("<II", pair)
    if offset < 0x30 or offset + length > len(data):
        raise UnsheyPatchError("Unsupported Unshey dialogue string layout")
    return data[offset:offset + length]


def _replace_once(value: bytes, old: bytes, new: bytes) -> bytes:
    if value.count(old) != 1:
        raise UnsheyPatchError("Unsupported Unshey belt trigger or action")
    return value.replace(old, new, 1)


def _patch_unshey(data: bytes, expected_sha256: str, quest_strref: int,
                  journal_strref: int) -> bytes:
    if hashlib.sha256(data).hexdigest() != expected_sha256 or data[:8] != b"DLG V1.0":
        raise UnsheyPatchError("Unshey dialogue changed; rescan and review it")
    if len(data) < 0x34:
        raise UnsheyPatchError("Unsupported Unshey dialogue header")
    if any(type(ref) is not int or not 0 <= ref < 0xFFFFFFFF
           for ref in (quest_strref, journal_strref)):
        raise UnsheyPatchError("Unshey quest text needs valid TLK references")
    (state_count, state_off, trans_count, trans_off,
     state_trig_off, state_trig_count, trans_trig_off, trans_trig_count,
     action_off, action_count) = struct.unpack_from("<10I", data, 8)
    if (state_count, trans_count, state_trig_count,
            trans_trig_count, action_count) != (9, 9, 7, 0, 2):
        raise UnsheyPatchError("Unsupported Unshey dialogue table counts")
    states = _table(data, state_off, state_count, 16)
    transitions = _table(data, trans_off, trans_count, 32)
    state_triggers = _table(data, state_trig_off, state_trig_count, 8)
    _table(data, trans_trig_off, trans_trig_count, 8)
    actions = _table(data, action_off, action_count, 8)
    for index, state in enumerate(states):
        first, count, trigger = struct.unpack_from("<III", state, 4)
        if (first, count) != (index, 1) or trigger not in (*range(7), 0xFFFFFFFF):
            raise UnsheyPatchError("Unsupported Unshey quest state layout")
    # All reviewed transitions exit, so graph duplication needs no state jumps.
    if any(not struct.unpack_from("<I", row)[0] & 8 for row in transitions):
        raise UnsheyPatchError("Unsupported Unshey non-exit transition")
    for state_id, action_id in ((4, 0), (5, 1)):
        flags, _reply, _journal, _trigger, action = struct.unpack_from("<5I", transitions[state_id])
        if flags != 284 or action != action_id:
            raise UnsheyPatchError("Unsupported Unshey hand-in transition")
    if struct.unpack_from("<I", transitions[1])[0] != 88:
        raise UnsheyPatchError("Unsupported Unshey unsolved journal transition")
    old_journal = struct.unpack_from("<I", transitions[1], 8)[0]

    original_triggers = [_string(data, row) for row in state_triggers]
    selected_triggers = list(original_triggers)
    for index in (2, 3, 4):
        selected_triggers[index] = _replace_once(
            selected_triggers[index], b'PartyHasItem("BELT04")', b'PartyHasItem("flrbelt1")')
    selected_actions = []
    for row in actions:
        action = _replace_once(_string(data, row), b'TakePartyItem("BELT04")',
                               b'TakePartyItem("flrbelt1")')
        selected_actions.append(_replace_once(
            action, f"EraseJournalEntry({old_journal})".encode("ascii"),
            f"EraseJournalEntry({journal_strref})".encode("ascii")))

    selected_states, selected_transitions = [], []
    for index, state in enumerate(states):
        row = bytearray(state)
        struct.pack_into("<I", row, 4, index + 9)
        trigger = struct.unpack_from("<I", row, 12)[0]
        if trigger != 0xFFFFFFFF:
            struct.pack_into("<I", row, 12, trigger + 7)
        if index == 1:
            struct.pack_into("<I", row, 0, quest_strref)
        selected_states.append(bytes(row))
    for index, transition in enumerate(transitions):
        row = bytearray(transition)
        if index == 1:
            struct.pack_into("<I", row, 8, journal_strref)
        if struct.unpack_from("<I", row)[0] & 4:
            struct.pack_into("<I", row, 16, struct.unpack_from("<I", row, 16)[0] + 2)
        selected_transitions.append(bytes(row))

    out = bytearray(data)

    def append_string(value: bytes) -> bytes:
        position = len(out)
        out.extend(value)
        return struct.pack("<II", position, len(value))

    guard = f'Global("{SELECTED_GLOBAL}","GLOBAL",1)\r\n'.encode("ascii")
    # Gate the True() fallback too, or it preempts the appended selected states.
    state_triggers = [append_string(value + b"!" + guard) for value in original_triggers]
    state_triggers += [append_string(value + guard) for value in selected_triggers]
    actions += [append_string(value) for value in selected_actions]

    def append_table(rows: list[bytes]) -> int:
        out.extend(b"\x00" * (-len(out) % 4))
        position = len(out)
        out.extend(b"".join(rows))
        return position

    offsets = (append_table(states + selected_states), append_table(transitions + selected_transitions),
               append_table(state_triggers), append_table(actions))
    struct.pack_into("<10I", out, 8, 18, offsets[0], 18, offsets[1],
                     offsets[2], 14, offsets[3], 0, offsets[3], 4)
    # The original body remains above; retain its header to support re-export
    # after a scan sees our installed patch. Recovery is verified by regeneration.
    out.extend(_TRAILER_MAGIC + struct.pack("<I", len(data)) + data[:0x34])
    return bytes(out)


def original_unshey(data: bytes) -> bytes:
    """Recover only the reviewed original or an exactly regenerated own patch."""
    if hashlib.sha256(data).hexdigest() == ORIGINAL_SHA256:
        return data
    if len(data) < _TRAILER_SIZE or data[-_TRAILER_SIZE:-_TRAILER_SIZE + 8] != _TRAILER_MAGIC:
        raise UnsheyPatchError("Unshey dialogue changed; rescan and review it")
    trailer = data[-_TRAILER_SIZE:]
    size = struct.unpack_from("<I", trailer, 8)[0]
    if not 0x34 <= size <= len(data) - _TRAILER_SIZE:
        raise UnsheyPatchError("Unshey dialogue has an invalid recovery trailer")
    original = trailer[12:] + data[0x34:size]
    if hashlib.sha256(original).hexdigest() != ORIGINAL_SHA256:
        raise UnsheyPatchError("Unshey dialogue original data changed")
    state_count, state_off, trans_count, trans_off = struct.unpack_from("<4I", data, 8)
    if (state_count, trans_count) != (18, 18):
        raise UnsheyPatchError("Unshey dialogue patch changed")
    states = _table(data, state_off, state_count, 16)
    transitions = _table(data, trans_off, trans_count, 32)
    old_quest = struct.unpack_from("<I", states[10])[0]
    old_journal = struct.unpack_from("<I", transitions[10], 8)[0]
    if _patch_unshey(original, ORIGINAL_SHA256, old_quest, old_journal) != data:
        raise UnsheyPatchError("Unshey dialogue patch changed")
    return original


def patch_unshey(data: bytes, *, quest_strref: int, journal_strref: int) -> bytes:
    """Patch reviewed/own EET DLG using @112/@113 refs in the exported TLK."""
    return _patch_unshey(original_unshey(data), ORIGINAL_SHA256, quest_strref, journal_strref)


def patch_unshey_journal(lua: bytes, patched_dialogue: bytes) -> bytes:
    """Register the appended unsolved entry in the existing BGEE.LUA quest.

    This is the EE journal-table equivalent of the original ADD_JOURNAL EXISTING
    TITLE operation. Original and solved entries stay registered; exact existing
    registrations are reused on re-export, including after our own DLG patch.
    """
    original = original_unshey(patched_dialogue)
    if original == patched_dialogue:
        return lua
    original_trans = struct.unpack_from("<I", original, 20)[0]
    patched_trans = struct.unpack_from("<I", patched_dialogue, 20)[0]
    old_ref = struct.unpack_from("<I", original, original_trans + 32 + 8)[0]
    new_ref = struct.unpack_from("<I", patched_dialogue, patched_trans + 10 * 32 + 8)[0]

    def entry_pattern(ref: int) -> bytes:
        return (rb"(?m)^([ \t]*)createEntry[ \t]*\([ \t]*(\d+),[ \t]*-1,[ \t]*"
                + str(ref).encode("ascii") + rb",[ \t]*\{\},[ \t]*nil[ \t]*\)[ \t]*(\r?\n)")

    matches = list(re.finditer(entry_pattern(old_ref), lua))
    if len(matches) != 1:
        raise UnsheyPatchError("Unshey journal needs one existing BGEE.LUA entry")
    anchor = matches[0]
    title = anchor[2]
    quest_pattern = rb"(?m)^[ \t]*createQuest[ \t]*\([ \t]*" + title + rb"[ \t]*\)"
    if len(re.findall(quest_pattern, lua[:anchor.start()])) != 1:
        raise UnsheyPatchError("Unshey journal quest title is missing or ambiguous")
    existing = list(re.finditer(entry_pattern(new_ref), lua))
    if existing:
        if len(existing) != 1 or existing[0][2] != title:
            raise UnsheyPatchError("Unshey journal text is registered to another quest")
        return lua
    entry = (anchor[1] + b"createEntry    ( " + title + b", -1, "
             + str(new_ref).encode("ascii") + b", {}, nil )" + anchor[3])
    return lua[:anchor.end()] + entry + lua[anchor.end():]


def _append_texts(data: bytes, texts: tuple[str, ...]) -> tuple[bytes, list[int]]:
    """Append text-only entries, preserving all existing TLK entries and data."""
    try:
        tlk = _Tlk(data)
    except ScanError as exc:
        raise UnsheyPatchError(str(exc)) from exc
    table_end = 0x12 + tlk.count * 0x1A
    entries, strings, refs = bytearray(), bytearray(), []
    existing = {}
    for index in range(tlk.count):
        row = 0x12 + index * 0x1A
        flags, sound, volume, pitch, offset, length = struct.unpack_from("<H8sIIII", data, row)
        if flags == 1 and not sound.strip(b"\0") and volume == pitch == 0:
            existing.setdefault(data[tlk.offset + offset:tlk.offset + offset + length], index)
    for text in texts:
        raw = text.encode("utf-8")
        if raw not in existing:
            existing[raw] = tlk.count + len(entries) // 0x1A
            entries.extend(struct.pack("<H8sIIII", 1, b"", 0, 0,
                                       len(data) - tlk.offset + len(strings), len(raw)))
            strings.extend(raw)
        refs.append(existing[raw])
    if not entries:
        return data, refs
    out = bytearray(data[:table_end] + entries + data[table_end:] + strings)
    struct.pack_into("<II", out, 0xA, tlk.count + len(entries) // 0x1A,
                     tlk.offset + len(entries))
    return bytes(out), refs


def prepare_unshey_resources(dialogue: bytes, item_template: bytes, tlk: bytes,
                             *, language: str = "en_us") -> tuple[bytes, bytes, bytes]:
    """Return (UNSHEY.DLG, FLRBELT1.ITM, dialog.tlk) without writing files.

    Supply copy/flrbelt1.itm and the active installed TLK. English EE text is
    supported. Original @112-115 texts are appended or reused exactly; existing
    TLK entries are never replaced. Installed XP, gold, book and solved-journal
    rewards survive. The campaign must set SELECTED_GLOBAL before dialogue.
    """
    if language.casefold() not in ("en_us", "en_gb", "english"):
        raise UnsheyPatchError("Unshey quest export currently requires English game text")
    if len(item_template) < 0x72 or item_template[:8] != b"ITM V1  ":
        raise UnsheyPatchError("Unsupported Unshey replacement item")
    appended_tlk, refs = _append_texts(tlk, (QUEST_TEXT, JOURNAL_TEXT, ITEM_NAME, ITEM_DESCRIPTION))
    patched_dialogue = patch_unshey(dialogue, quest_strref=refs[0], journal_strref=refs[1])
    item = bytearray(item_template)
    # Match lib/delete.tpa SAY fields; the ordinary BELT04 resource is not used.
    for offset, ref in ((8, refs[2]), (12, refs[2]), (0x50, refs[3])):
        struct.pack_into("<I", item, offset, ref)
    return patched_dialogue, bytes(item), appended_tlk
