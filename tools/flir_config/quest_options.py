"""Campaign-gated original Kangaxx quest alterations.

Only the reviewed compiled resources are accepted. Old dialogue transitions
remain under the inverse campaign flag; new routes follow d/hlskull.d
exactly. All functions operate on bytes supplied by the caller. Component
1500 makes the Flail of Ages change itself when it is installed
(lib/campaign_patches.tpa).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import struct

from .profiles import ProfileError
from .quest_rewards import _blocks, _global_guard, _newline
from .game_text import _append_texts, _string, _table

ROOT = Path(__file__).resolve().parents[2]
KANGAXX_GLOBAL = "FLPR_KANGAXX"
ORIGINAL_HASHES = {
    "hlskull.dlg": "c7ea5cd17d5cffe9368bd980200a05fee7e78eacadf3a6cb41de882df62f56bd",
    "crypt526.bcs": "9230a20a9177fe1eef1723fba0e8daa07e949663682a3bddf18e585c0482efe6",
    "crypt202.bcs": "ac8fd01b37e902f4484f0cf136def8411be5682aa396ebaabcb1c3723f748aaf",
}
_TRAILER = b"FLPROPT1"


def _check(data: bytes, expected: str, label: str) -> None:
    if hashlib.sha256(data).hexdigest() != expected:
        raise ProfileError(f"{label} changed; review the original option adapter")


def _texts(path: str) -> dict[int, str]:
    # Match original delimiters, including multiline text and duplicate @1502
    # (WeiDU uses the last definition). Retain authored CRLFs.
    with (ROOT / path).open(encoding="utf-8", newline="") as stream:
        source = stream.read()
    return {int(match[1]): match[3] for match in re.finditer(
        r'(?m)^@(\d+)\s*=\s*([~"])(.*?)\2', source, re.DOTALL)}


def _refs(refs: dict[int, int], required: range | tuple[int, ...]) -> None:
    if any(type(refs.get(key)) is not int or not 0 <= refs[key] < 0xFFFFFFFF
           for key in required):
        raise ProfileError("Original quest option requires allocated TLK text")


def _seal(data: bytes, original: bytes, refs: dict[int, int]) -> bytes:
    trailer = struct.pack("<I52sI", len(original), original[:0x34], len(refs))
    trailer += b"".join(struct.pack("<II", key, value) for key, value in sorted(refs.items()))
    return data + trailer + _TRAILER + struct.pack("<I", len(trailer))


def original_dialogue(name: str, data: bytes) -> bytes:
    """Recover only exact original bytes or a fully verified patch made here."""
    if name != "hlskull.dlg":
        raise ProfileError("Unsupported original-option dialogue")
    if hashlib.sha256(data).hexdigest() == ORIGINAL_HASHES[name]:
        return data
    if len(data) < 12 or data[-12:-4] != _TRAILER:
        raise ProfileError(f"{name} changed; review original-option dialogue")
    length = struct.unpack_from("<I", data, len(data) - 4)[0]
    if length < 60 or length > len(data) - 12:
        raise ProfileError("Invalid original-option dialogue recovery trailer")
    trailer = data[-12 - length:-12]
    size, header, count = struct.unpack_from("<I52sI", trailer)
    if size < 0x34 or size > len(data) - length - 12 or length != 60 + count * 8:
        raise ProfileError("Invalid original-option dialogue recovery bounds")
    refs = dict(struct.unpack_from("<II", trailer, 60 + index * 8) for index in range(count))
    if len(refs) != count:
        raise ProfileError("Invalid original-option dialogue recovery references")
    original = header + data[0x34:size]
    _check(original, ORIGINAL_HASHES[name], name)
    if _patch_hlskull(original, refs, ORIGINAL_HASHES[name]) != data:
        raise ProfileError("Original-option dialogue patch changed")
    return original


class _Dialogue:
    def __init__(self, data: bytes, name: str):
        if len(data) < 0x34 or data[:8] != b"DLG V1.0":
            raise ProfileError("Invalid original-option DLG header")
        self.data, self.name = data, name.encode("ascii")
        ns, so, nt, to, sto, nst, tto, ntt, ao, na = struct.unpack_from("<10I", data, 8)
        self.states = [bytearray(row) for row in _table(data, so, ns, 16)]
        self.transitions = [bytearray(row) for row in _table(data, to, nt, 32)]
        self.state_triggers = [_string(data, row) for row in _table(data, sto, nst, 8)]
        self.triggers = [_string(data, row) for row in _table(data, tto, ntt, 8)]
        self.actions = [_string(data, row) for row in _table(data, ao, na, 8)]

    def transition(self, target: int | None, *, reply: int | None = None,
                   trigger: str = "", action: str = "", journal: int | None = None) -> bytearray:
        flags = (1 if reply is not None else 0) | (2 if trigger else 0) | (4 if action else 0)
        flags |= 8 if target is None else 0
        flags |= 0x50 if journal is not None else 0
        trigger_id, action_id = 0, 0
        if trigger:
            trigger_id = len(self.triggers)
            self.triggers.append((trigger + "\r\n").encode("ascii"))
        if action:
            action_id = len(self.actions)
            self.actions.append((action + "\r\n").encode("ascii"))
        return bytearray(struct.pack("<5I8sI", flags, 0xFFFFFFFF if reply is None else reply,
                                     journal or 0, trigger_id, action_id,
                                     self.name if target is not None else b"", target or 0))

    def state(self, text: int, transitions: list[bytearray]) -> int:
        index = len(self.states)
        self.states.append(bytearray(struct.pack("<4I", text, len(self.transitions),
                                                len(transitions), 0xFFFFFFFF)))
        self.transitions.extend(transitions)
        return index

    def branch(self, state: int, ordinal: int, flag: str, *, target: int | None = None,
               journal: int | None = None) -> None:
        row = self.states[state]
        first, count = struct.unpack_from("<II", row, 4)
        if ordinal >= count or first + count > len(self.transitions):
            raise ProfileError("Original option dialogue transition changed")
        original = self.transitions[first + ordinal]
        flags, _reply, _journal, trigger_id = struct.unpack_from("<4I", original)
        prior = self.triggers[trigger_id] if flags & 2 else b""
        replacement = []
        for selected in (False, True):
            branch = bytearray(original)
            guard = f'{"" if selected else "!"}Global("{flag}","GLOBAL",1)\r\n'.encode("ascii")
            self.triggers.append(prior + guard)
            struct.pack_into("<I", branch, 0, flags | 2)
            struct.pack_into("<I", branch, 12, len(self.triggers) - 1)
            if selected and target is not None:
                struct.pack_into("<8sI", branch, 20, self.name, target)
            if selected and journal is not None:
                if not flags & 0x50 == 0x50:
                    raise ProfileError("Original quest unsolved journal changed")
                struct.pack_into("<I", branch, 8, journal)
            replacement.append(branch)
        new_rows = (self.transitions[first:first + ordinal] + replacement +
                    self.transitions[first + ordinal + 1:first + count])
        struct.pack_into("<II", row, 4, len(self.transitions), len(new_rows))
        self.transitions.extend(new_rows)

    def render(self) -> bytes:
        out = bytearray(self.data)

        def strings(values: list[bytes]) -> list[bytes]:
            result = []
            for value in values:
                result.append(struct.pack("<II", len(out), len(value)))
                out.extend(value)
            return result

        st, tt, actions = strings(self.state_triggers), strings(self.triggers), strings(self.actions)

        def table(rows: list[bytes]) -> int:
            out.extend(b"\0" * (-len(out) % 4))
            position = len(out)
            out.extend(b"".join(rows))
            return position

        so, to, sto, tto, ao = (table(self.states), table(self.transitions),
                               table(st), table(tt), table(actions))
        struct.pack_into("<10I", out, 8, len(self.states), so, len(self.transitions), to,
                         sto, len(st), tto, len(tt), ao, len(actions))
        return bytes(out)


def _patch_hlskull(data: bytes, refs: dict[int, int], expected: str) -> bytes:
    _check(data, expected, "HLSKULL.DLG")
    _refs(refs, range(22))
    dlg = _Dialogue(data, "HLSKULL")
    if len(dlg.states) != 16 or len(dlg.transitions) != 32:
        raise ProfileError("Original Kangaxx dialogue layout changed")
    branch, keys, keys2, onekey, nokey, body, altexit = range(16, 23)
    tr = dlg.transition
    dlg.state(refs[0], [tr(keys, reply=refs[1]), tr(body, reply=refs[2]), tr(6, reply=56194)])
    dlg.state(refs[3], [tr(keys2)])
    dlg.state(refs[4], [tr(10, reply=refs[5]), tr(body, reply=refs[6]),
                         tr(onekey, reply=refs[7], trigger='PartyHasItem("flrward1")'),
                         tr(nokey, reply=refs[7], trigger='!PartyHasItem("flrward1")'), tr(6, reply=refs[8])])
    dlg.state(refs[9], [tr(10, reply=refs[10]), tr(6, reply=refs[11])])
    dlg.state(refs[12], [tr(altexit, reply=refs[13]), tr(body, reply=refs[14]), tr(6, reply=refs[15])])
    dlg.state(refs[16], [tr(10, reply=refs[17]), tr(keys, reply=refs[21]), tr(6, reply=refs[18])])
    dlg.state(refs[19], [tr(None, action='SetGlobal("KangaxxQuest","GLOBAL",1)', journal=refs[20])])
    for state in (5, 6):
        dlg.branch(state, 0, KANGAXX_GLOBAL, target=branch)
    dlg.branch(10, 0, KANGAXX_GLOBAL, journal=refs[20])
    return _seal(dlg.render(), data, refs)


def patch_hlskull(data: bytes, refs: dict[int, int]) -> bytes:
    return _patch_hlskull(original_dialogue("hlskull.dlg", data), refs, ORIGINAL_HASHES["hlskull.dlg"])


def patch_kangaxx_journal(lua: bytes, patched_dialogue: bytes) -> bytes:
    """Register the appended journal under the installed Kangaxx quest title."""
    original = original_dialogue("hlskull.dlg", patched_dialogue)
    if original == patched_dialogue:
        return lua
    old, new = _Dialogue(original, "HLSKULL"), _Dialogue(patched_dialogue, "HLSKULL")
    old_first = struct.unpack_from("<I", old.states[10], 4)[0]
    new_first = struct.unpack_from("<I", new.states[10], 4)[0]
    old_ref = struct.unpack_from("<I", old.transitions[old_first], 8)[0]
    new_ref = struct.unpack_from("<I", new.transitions[new_first + 1], 8)[0]

    def pattern(ref: int) -> bytes:
        return (rb"(?m)^([ \t]*)createEntry[ \t]*\([ \t]*(\d+),[ \t]*-1,[ \t]*"
                + str(ref).encode() + rb",[ \t]*\{\},[ \t]*nil[ \t]*\)[ \t]*(\r?\n)")

    matches = list(re.finditer(pattern(old_ref), lua))
    if len(matches) != 1:
        raise ProfileError("Kangaxx journal needs one existing BGEE.LUA entry")
    anchor = matches[0]
    title = anchor[2]
    quest = rb"(?m)^[ \t]*createQuest[ \t]*\([ \t]*" + title + rb"[ \t]*\)"
    if len(re.findall(quest, lua[:anchor.start()])) != 1:
        raise ProfileError("Kangaxx journal quest title is missing or ambiguous")
    existing = list(re.finditer(pattern(new_ref), lua))
    if existing:
        if len(existing) != 1 or existing[0][2] != title:
            raise ProfileError("Kangaxx journal text is registered to another quest")
        return lua
    entry = (anchor[1] + b"createEntry    ( " + title + b", -1, " + str(new_ref).encode()
             + b", {}, nil )" + anchor[3])
    return lua[:anchor.end()] + entry + lua[anchor.end():]


def _crypt_patch(data: bytes, area: str, refs: dict[int, int]) -> bytes:
    _refs(refs, (1500, 1501))
    nl = _newline(data)
    blocks, prefix, suffix = _blocks(data, 6, nl)
    first = blocks[0]
    zero = b'0 0 0 0 0 0 0 0 0 0 0 0 ""OB' + nl

    def trigger(value: bytes) -> bytes:
        return b"TR" + nl + value + nl + zero + b"TR" + nl

    ward = trigger(b'16450 0 0 0 0 "flrward1" "" OB')  # PartyHasItem, trigger.ids 0x4042.
    quest = trigger(b'16399 1 0 0 0 "GLOBALKangaxxQuest" "" OB')
    guard = _global_guard(KANGAXX_GLOBAL, True, nl)
    excluded_guard = _global_guard(KANGAXX_GLOBAL, False, nl)
    boundary = b"CO" + nl
    if first.count(boundary) != 2:
        raise ProfileError("Original crypt block changed")
    head, conditions, response = first.split(boundary, 2)
    used = trigger(b'16399 0 0 0 0 "' + area.encode() + b'UsedCrypt" "" OB')
    advance = b'1 0 0 0 0"' + area.encode() + b'UsedCrypt" "" AC' + nl
    display = b'35858 0 0 0 0"" "" AC' + nl
    if conditions.count(used) != 1 or response.count(advance) != 1 or response.count(display) != 1:
        raise ProfileError("Original crypt gate changed")
    take = b"AC" + nl + b"116OB" + nl + zero + (b"OB" + nl + zero) * 2
    take += b'0 0 0 0 0"flrward1" "" AC' + nl
    selected_response = response.replace(advance, advance + take, 1).replace(
        display, str(refs[1501]).encode() + b' 0 0 0 0"" "" AC' + nl, 1)
    selected = head + boundary + conditions + guard + ward + quest + boundary + selected_response
    excluded = head + boundary + conditions + excluded_guard + boundary + response
    # The first original block's second action is DisplayStringHead(Myself).
    # Reuse its full object/parameter encoding for the sealed-crypt response.
    action_start = response.index(b"AC" + nl + b"269OB" + nl)
    display_action = response[action_start:response.index(display) + len(display)]
    display_action = display_action.replace(display, str(refs[1500]).encode() + b' 0 0 0 0"" "" AC' + nl)
    # The first action follows RESPONSE's weight directly (100AC).
    blocked_response = b"RS" + nl + b"RE" + nl + b"100" + display_action + b"RE" + nl + b"RS" + nl + b"CR" + nl
    either_missing = trigger(b'16521 2 0 0 0 "" "" OB')  # OR, trigger.ids 0x4089.
    either_missing += ward.replace(b'16450 0 0 ', b'16450 0 1 ', 1)
    either_missing += quest.replace(b'16399 1 0 ', b'16399 1 1 ', 1)
    blocked = head + boundary + conditions + guard + either_missing + boundary + blocked_response
    return prefix + blocked + selected + excluded + b"".join(blocks[1:]) + suffix


def original_crypt(name: str, data: bytes) -> bytes:
    if name not in ("crypt526.bcs", "crypt202.bcs"):
        raise ProfileError("Unsupported original crypt script")
    expected = ORIGINAL_HASHES[name]
    if hashlib.sha256(data).hexdigest() == expected:
        return data
    nl = _newline(data)
    blocks, prefix, suffix = _blocks(data, 8, nl)
    excluded_guard = _global_guard(KANGAXX_GLOBAL, False, nl)
    if blocks[2].count(excluded_guard) != 1:
        raise ProfileError("Original crypt recovery branch changed")
    original = prefix + blocks[2].replace(excluded_guard, b"", 1) + b"".join(blocks[3:]) + suffix
    _check(original, expected, name)
    refs = {}
    for index, key in ((0, 1500), (1, 1501)):
        values = re.findall(rb'(?m)^(\d+) 0 0 0 0"" "" AC\r?$', blocks[index])
        if len(values) != 1:
            raise ProfileError("Original crypt recovery display changed")
        refs[key] = int(values[0])
    area = "AR0526" if name == "crypt526.bcs" else "AR0202"
    if _crypt_patch(original, area, refs) != data:
        raise ProfileError("Original crypt patch changed")
    return original


def patch_crypt(name: str, data: bytes, refs: dict[int, int]) -> bytes:
    original = original_crypt(name, data)
    return _crypt_patch(original, "AR0526" if name == "crypt526.bcs" else "AR0202", refs)


def ward_item(refs: dict[int, int]) -> bytes:
    """Original copy/ward.itm with component-520 name, descriptions and icon."""
    _refs(refs, (1502, 1503, 1504))
    item = bytearray((ROOT / "copy/ward.itm").read_bytes())
    for offset, ref in ((8, refs[1503]), (12, refs[1503]), (0x50, refs[1504]), (0x54, refs[1502])):
        struct.pack_into("<I", item, offset, ref)
    item[0x3A:0x42] = b"flrward1"
    return bytes(item)


def prepare_quest_options(resources: dict[str, bytes], tlk: bytes, *,
                          kangaxx: bool = False,
                          language: str = "en_us") -> tuple[dict[str, bytes], bytes]:
    """Return selected patched resources and TLK, leaving file writes to caller.

    Input resource keys are lowercase filenames. Kangaxx needs HLSKULL.DLG and
    CRYPT526/CRYPT202.BCS. Generated ward ITM/BAM are included in Kangaxx
    output. The caller sets the campaign flag explicitly.
    """
    if type(kangaxx) is not bool:
        raise ProfileError("Original quest option selections must be Booleans")
    if language.casefold() not in {"en_us", "en_gb", "english"}:
        raise ProfileError("Original quest options currently require English game text")
    output = {}

    def append(path: str, keys: range | tuple[int, ...]) -> dict[int, int]:
        nonlocal tlk
        texts = _texts(path)
        tlk, refs = _append_texts(tlk, tuple(texts[key] for key in keys))
        return dict(zip(keys, refs))

    def resource(name: str) -> bytes:
        if name not in resources:
            raise ProfileError(f"Original quest option requires {name}")
        return resources[name]

    if kangaxx:
        dialogue_refs = append("languages/english/hlskull.tra", range(22))
        item_refs = append("languages/english/in-game.tra", tuple(range(1500, 1505)))
        output["hlskull.dlg"] = patch_hlskull(resource("hlskull.dlg"), dialogue_refs)
        for name in ("crypt526.bcs", "crypt202.bcs"):
            output[name] = patch_crypt(name, resource(name), item_refs)
        output["flrward1.itm"] = ward_item(item_refs)
        output["flrward1.bam"] = (ROOT / "copy/flrward1.bam").read_bytes()
    return output, tlk
