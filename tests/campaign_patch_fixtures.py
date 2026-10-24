"""Synthetic quest scripts and dialogues for CIR's game changes.

Hand-written stand-ins with the shapes lib/campaign_patches.tpa handles, never
game resources: WeiDU compiles them in a scratch game with the minimal IDS
tables below. The expected scripts are what the installer should make of them;
dialogues are checked by structure, since their compiled bytes keep the
decompiler's line breaks.
"""

from __future__ import annotations

import struct
from typing import Mapping, Sequence

NL = "\r\n"

# Only the actions, triggers and objects the fixtures, the installer's own
# dialogue and the tweaks use, with the engine's numbers: WeiDU joins the
# strings of some opcodes (an area and a variable name), whatever the IDS
# calls them.
IDS = {
    "ACTION.IDS": ("1 ActionOverride(O:Actor*,A:Action*)", "15 GiveItem(S:Object*,O:Target*)",
                   "30 SetGlobal(S:Name*,S:Area*,I:Value*)",
                   "82 CreateItem(S:ResRef*,I:Usage1*,I:Usage2*,I:Usage3*)", "108 EscapeArea()",
                   "111 DestroySelf()", "116 TakePartyItem(S:Item*)", "117 TakePartyGold(I:Amount*)",
                   "140 GiveItemCreate(S:ResRef*,O:Object*,I:Usage1*,I:Usage2*,I:Usage3*)",
                   "150 StartStore(S:Store*,O:Target*)", "151 DisplayString(O:Object*,I:StrRef*)",
                   "169 DestroyItem(S:ResRef*)", "198 StartDialogueNoSet(O:Object*)",
                   "204 TakePartyItemNum(S:ResRef*,I:Num*)", "209 SpawnPtActivate(O:Object*)",
                   "260 DestroyGold(I:Gold*)", "263 EraseJournalEntry(I:STRREF*)"),
    "TRIGGER.IDS": ("0x0070 Clicked(O:Object*)", "0x400F Global(S:Name*,S:Area*,I:Value*)",
                    "0x4023 True()", "0x4030 False()", "0x4034 GlobalGT(S:Name*,S:Area*,I:Value*)",
                    "0x4039 NumTimesTalkedTo(I:Num*)", "0x4042 PartyHasItem(S:Item*)",
                    "0x404F PartyGoldGT(I:Amount*)", "0x4050 PartyGoldLT(I:Amount*)",
                    "0x4051 Dead(S:Name*)", "0x4061 HasItem(S:ResRef*,O:Object*)", "0x406C NumInPartyLT(I:Num*)",
                    "0x4089 OR(I:OrCount*)", "0x4090 InPartySlot(O:Object*,I:Slot*)"),
    "OBJECT.IDS": ("1 Myself", "17 LastTrigger", "19 LastTalkedToBy", "21 Player1"),
    "EA.IDS": ("0 ANYONE",), "GENERAL.IDS": ("0 GENERAL_ANY",), "RACE.IDS": ("0 RACE_ALL",),
    "CLASS.IDS": ("0 CLASS_ALL",), "SPECIFIC.IDS": ("0 SPECIFIC_ALL",), "GENDER.IDS": ("0 GENDER_ALL",),
    "ALIGNMEN.IDS": ("0 MASK_ALL",),
}


def ids_files() -> dict[str, bytes]:
    return {name: ("IDS V1.0" + NL + NL.join(lines) + NL).encode("ascii") for name, lines in IDS.items()}


def text_tlk(count: int = 40) -> bytes:
    """Game text whose entries 1.. read "Synthetic line <n>"."""
    strings = [b""] + [f"Synthetic line {number}".encode("ascii") for number in range(1, count)]
    entries, data = bytearray(), bytearray()
    for raw in strings:
        entries += struct.pack("<H8sIIII", 1 if raw else 0, b"", 0, 0, len(data), len(raw))
        data += raw
    return b"TLK V1  " + struct.pack("<HII", 0, len(strings), 0x12 + len(entries)) + bytes(entries + data)


def lines(*rows: str) -> str:
    return NL.join(rows) + NL


def block(conditions: Sequence[str], *responses: Sequence[str]) -> str:
    rows = ["IF", *("  " + row for row in conditions), "THEN"]
    for actions in responses:
        rows += ["  RESPONSE #100", *("    " + row for row in actions)]
    return lines(*rows, "END", "")


# The pool: the reward block also shows the line about the mace, which the
# selected copy swaps for "nothing else of interest" (35877 -> 35878).
POOL_CONDITIONS = ("Clicked([ANYONE])", 'Global("FLPoolTaken","GLOBAL",0)')
POOL_ACTIONS = ('SetGlobal("FLPoolTaken","GLOBAL",1)', "DisplayString(Myself,35877)")
POOL_LATER = block(("Clicked([ANYONE])", 'Global("FLPoolTaken","GLOBAL",1)'), ("DisplayString(Myself,35878)",))
BLPOOL = block(POOL_CONDITIONS, (*POOL_ACTIONS, 'GiveItemCreate("BLUN12",LastTrigger,0,0,0)')) + POOL_LATER
BLPOOL_SPLIT = (
    block((*POOL_CONDITIONS, 'Global("FLPR_QPOOL","GLOBAL",1)'),
          ('SetGlobal("FLPoolTaken","GLOBAL",1)', "DisplayString(Myself,35878)",
           'SetGlobal("FLPR_XPOOL","GLOBAL",1)', 'SetGlobal("FLPR_EPOOL","GLOBAL",1)'))
    + block((*POOL_CONDITIONS, '!Global("FLPR_QPOOL","GLOBAL",1)'),
            (*POOL_ACTIONS, 'GiveItemCreate("BLUN12",LastTrigger,0,0,0)', 'SetGlobal("FLPR_EPOOL","GLOBAL",1)'))
    + POOL_LATER)

# The altar as another mod left it: a block of its own first, the reward
# created in lower case, and a later block that only checks for the item.
ALTAR_FIRST = block(('Global("FLOtherMod","GLOBAL",0)',), ('SetGlobal("FLOtherMod","GLOBAL",1)',))
ALTAR_LATER = block(('Global("FLAltarReady","GLOBAL",2)', 'PartyHasItem("blun23")'), ("DisplayString(Myself,7)",))
TROALT = (ALTAR_FIRST + block(('Global("FLAltarReady","GLOBAL",1)',),
                              ('SetGlobal("FLAltarReady","GLOBAL",2)', 'CreateItem("blun23",0,0,0)'))
          + ALTAR_LATER)
TROALT_SPLIT = (
    ALTAR_FIRST
    + block(('Global("FLAltarReady","GLOBAL",1)', 'Global("FLPR_QALTAR","GLOBAL",1)'),
            ('SetGlobal("FLAltarReady","GLOBAL",2)', 'SetGlobal("FLPR_XALTAR","GLOBAL",1)',
             'SetGlobal("FLPR_EALTAR","GLOBAL",1)'))
    + block(('Global("FLAltarReady","GLOBAL",1)', '!Global("FLPR_QALTAR","GLOBAL",1)'),
            ('SetGlobal("FLAltarReady","GLOBAL",2)', 'CreateItem("blun23",0,0,0)',
             'SetGlobal("FLPR_EALTAR","GLOBAL",1)'))
    + ALTAR_LATER)

BOOK_CONDITIONS = ('PartyHasItem("FLBOOK")', 'Global("FLBookRead","GLOBAL",0)')
PPBOOK = block(BOOK_CONDITIONS, ('GiveItemCreate("ring09",Player1,1,0,0)', 'SetGlobal("FLBookRead","GLOBAL",1)'))
PPBOOK_SPLIT = (
    block((*BOOK_CONDITIONS, 'Global("FLPR_QBOOK","GLOBAL",1)'),
          ('SetGlobal("FLPR_XBOOK","GLOBAL",1)', 'SetGlobal("FLBookRead","GLOBAL",1)',
           'SetGlobal("FLPR_EBOOK","GLOBAL",1)'))
    + block((*BOOK_CONDITIONS, '!Global("FLPR_QBOOK","GLOBAL",1)'),
            ('GiveItemCreate("ring09",Player1,1,0,0)', 'SetGlobal("FLBookRead","GLOBAL",1)',
             'SetGlobal("FLPR_EBOOK","GLOBAL",1)')))
# The book as Apply extends the installer's version with a scroll's route.
PAGE_CONDITIONS = ('PartyHasItem("FLBOOK")', 'Global("FLBookRead","GLOBAL",1)')
PPBOOK_ROUTED = (
    PPBOOK_SPLIT
    + block((*PAGE_CONDITIONS, 'Global("FLPR_QM2_260","GLOBAL",1)'),
            ('SetGlobal("FLPR_XM2_260","GLOBAL",1)', 'SetGlobal("FLPR_EM2_260","GLOBAL",1)'))
    + block((*PAGE_CONDITIONS, '!Global("FLPR_QM2_260","GLOBAL",1)'),
            ('GiveItemCreate("scrl9b",Player1,1,0,0)', 'SetGlobal("FLPR_EM2_260","GLOBAL",1)')))

# Unshey adds quest entry #5 and erases it when she takes the girdle.
UNSHEY_JOURNAL = 5


def unshey(item: str = "BELT04", erased: int = UNSHEY_JOURNAL) -> str:
    return lines(
        "BEGIN ~UNSHEY~", "",
        "IF ~NumTimesTalkedTo(0)~ THEN BEGIN 0", "  SAY #1",
        "  IF ~~ THEN REPLY #2 GOTO 1", "  IF ~~ THEN REPLY #3 EXIT", "END", "",
        "IF ~~ THEN BEGIN 1", "  SAY #4",
        f"  IF ~~ THEN UNSOLVED_JOURNAL #{UNSHEY_JOURNAL} EXIT", "END", "",
        f'IF ~PartyHasItem("{item}")~ THEN BEGIN 2', "  SAY #6",
        f'  IF ~~ THEN REPLY #7 DO ~TakePartyItem("{item}")',
        f"EraseJournalEntry({erased})",
        'GiveItemCreate("ring05",LastTalkedToBy,1,0,0)~ SOLVED_JOURNAL #8 EXIT',
        "  IF ~~ THEN REPLY #9 EXIT", "END", "",
        "IF ~True()~ THEN BEGIN 3", "  SAY #10", "  IF ~~ THEN EXIT", "END")


QUEST_TABLE_BEFORE = "function buildQuestsTable()" + NL + "\tcreateQuest ( 900 )" + NL
QUEST_TABLE_AFTER = "\tcreateEntry ( 900, -1, 8, {}, nil )" + NL + "end" + NL


def quest_table(*entries: int) -> bytes:
    """BGEE.LUA's quest journal with Unshey's entries filed under quest 900."""
    rows = "".join(f"\tcreateEntry ( 900, -1, {entry}, {{}}, nil ){NL}" for entry in entries)
    return (QUEST_TABLE_BEFORE + rows + QUEST_TABLE_AFTER).encode("ascii")


# Glaicus: state 4 gives the first head and continues to 5, which continues to
# the farewell, 6. State 3 enters 4 the way the original game does; another
# mod added the reply in state 1 with an action, a journal entry and an extra
# flag. State 2's other branches also hand over the head: the original game's
# continues to 7, and other mods continue one in an NPC's dialogue and end one.
GLAICUS_GIVER, GLAICUS_AFTER, GLAICUS_FAREWELL = 4, 5, 6
GLAICUS_ENTRIES = ((1, 1), (3, 0))
GLAICUS_HEADS = ((2, 1), (2, 2), (2, 3))


def glaicus(*, give: str = "blun14c", extra: Sequence[str] = ()) -> str:
    gives = f'GiveItem("{give}",LastTalkedToBy)'
    return lines(
        "BEGIN ~KPGLAI01~", "",
        "IF ~NumTimesTalkedTo(0)~ THEN BEGIN 0", "  SAY #11", "  IF ~~ THEN EXIT", "END", "",
        'IF ~Global("FLGlaicus","GLOBAL",1)~ THEN BEGIN 1', "  SAY #12",
        '  IF ~~ THEN REPLY #13 DO ~SetGlobal("FLGlaicus","GLOBAL",2)~ GOTO 2',
        '  IF ~~ THEN REPLY #14 DO ~SetGlobal("FLHeads","GLOBAL",1)~ UNSOLVED_JOURNAL #15 FLAGS 512 GOTO 4',
        *extra, "END", "",
        "IF ~~ THEN BEGIN 2", "  SAY #16", '  IF ~!Dead("FLTroll")~ THEN GOTO 3',
        f'  IF ~Dead("FLTroll") Global("FLNpc","GLOBAL",0)~ THEN DO ~{gives}~ GOTO 7',
        f'  IF ~Dead("FLTroll") Global("FLNpc","GLOBAL",1)~ THEN DO ~{gives}~ EXTERN ~FLNPCJ~ 2',
        f'  IF ~Dead("FLTroll") Global("FLNpc","GLOBAL",2)~ THEN DO ~{gives} EscapeArea()~ EXIT',
        "END", "",
        "IF ~~ THEN BEGIN 3", "  SAY #17", "  IF ~~ THEN GOTO 4", "END", "",
        "IF ~~ THEN BEGIN 4", "  SAY #18", f"  IF ~~ THEN DO ~{gives}~ GOTO 5", "END", "",
        "IF ~~ THEN BEGIN 5", "  SAY #19", "  IF ~~ THEN GOTO 6", "END", "",
        "IF ~~ THEN BEGIN 6", "  SAY #20", "  IF ~~ THEN DO ~EscapeArea()~ UNSOLVED_JOURNAL #21 EXIT", "END", "",
        "IF ~~ THEN BEGIN 7", "  SAY #22", "  IF ~~ THEN DO ~EscapeArea()~ EXIT", "END")


# Compiled together per group: a dialogue's transitions name the dialogue, so
# each variant of one is compiled under its own name in its own group.
SOURCES = {
    "applied": {"blpool.baf": BLPOOL, "troalt.baf": TROALT, "ppbook.baf": PPBOOK,
                "xblpool.baf": BLPOOL_SPLIT, "xtroalt.baf": TROALT_SPLIT, "xppbook.baf": PPBOOK_SPLIT,
                "rppbook.baf": PPBOOK_ROUTED, "unshey.d": unshey(), "kpglai01.d": glaicus()},
    # Each quest file as another mod could leave it; CIR leaves them
    # as they are.
    "left-out": {
        # The reward is created in two places.
        "blpool.baf": BLPOOL + block(('Global("FLPoolTaken","GLOBAL",2)',),
                                     ('CreateItem("BLUN12",0,0,0)',)),
        # Another mod gives a different reward.
        "troalt.baf": TROALT.replace("blun23", "flaxe01"),
        # Still split by an earlier configurator.
        "ppbook.baf": PPBOOK_SPLIT,
        # Unshey asks for another item.
        "unshey.d": unshey(item="BELT05"),
        # Glaicus gives no flail head.
        "kpglai01.d": glaicus(give="flaxe01"),
    },
    "left-out-2": {
        # The entry Unshey erases is not the one she adds.
        "unshey.d": unshey(erased=6),
        # Glaicus was changed by an earlier configurator.
        "kpglai01.d": glaicus(extra=('  IF ~Global("FLPR_FLAIL","GLOBAL",1)~ THEN GOTO 4',)),
    },
}


# A DLG V1.0 as states with their transitions, strings decoded and whitespace
# collapsed, so the checks compare meaning rather than line breaks.
def _words(data: bytes) -> str:
    return " ".join(data.decode("latin-1").split())


def read_dialogue(data: bytes) -> list[dict]:
    if data[:8] != b"DLG V1.0":
        raise ValueError("not a DLG V1.0")
    (states, state_at, transitions, transition_at, state_triggers_at, state_triggers,
     triggers_at, triggers, actions_at, actions) = struct.unpack_from("<10I", data, 8)

    def strings(offset: int, count: int) -> list[str]:
        return [_words(data[start:start + size]) for start, size in
                (struct.unpack_from("<II", data, offset + index * 8) for index in range(count))]

    state_text, trigger_text = strings(state_triggers_at, state_triggers), strings(triggers_at, triggers)
    action_text = strings(actions_at, actions)
    rows = []
    for index in range(states):
        say, first, count, trigger = struct.unpack_from("<4I", data, state_at + index * 16)
        moves = []
        for number in range(first, first + count):
            flags, reply, journal, trigger_index, action, target, state = struct.unpack_from(
                "<5I8sI", data, transition_at + number * 32)
            moves.append({
                "flags": flags, "reply": reply if flags & 1 else None,
                "journal": journal if flags & 0x10 else None,
                "trigger": trigger_text[trigger_index] if flags & 2 else "",
                "action": action_text[action] if flags & 4 else "",
                "target": None if flags & 8 else (target.rstrip(b"\0").decode("ascii").upper(), state)})
        rows.append({"say": say, "trigger": "" if trigger == 0xFFFFFFFF else state_text[trigger],
                     "transitions": moves})
    return rows


def _guarded(trigger: str, guard: str) -> str:
    return " ".join((trigger + " " + guard).split())


def expected_unshey(original: Sequence[dict], *, item: str, replacement: str, old_journal: int,
                    journal: int, say: int) -> list[dict]:
    """Every state twice: the original under !Global("FLPR_QUNSHEY"), then a copy
    under Global("FLPR_QUNSHEY") that asks for <replacement> and uses the new
    journal text and the quest line of the original mod."""
    count = len(original)
    guard = 'Global("FLPR_QUNSHEY","GLOBAL",1)'
    kept, copies = [], []
    for state in original:
        kept.append({**state, "trigger": _guarded(state["trigger"], "!" + guard) if state["trigger"] else ""})
        adds = any(move["journal"] == old_journal and move["flags"] & 0x40 for move in state["transitions"])
        moves = []
        for move in state["transitions"]:
            copy = dict(move)
            for field in ("trigger", "action"):
                copy[field] = (copy[field].replace(f'"{item}"', f'"{replacement}"')
                               .replace(f"EraseJournalEntry({old_journal})", f"EraseJournalEntry({journal})"))
            if move["journal"] == old_journal and move["flags"] & 0x40:
                copy["journal"] = journal
            if move["target"] and move["target"][0] == "UNSHEY":
                copy["target"] = ("UNSHEY", move["target"][1] + count)
            moves.append(copy)
        copies.append({"say": say if adds else state["say"],
                       "trigger": (_guarded(state["trigger"].replace(f'"{item}"', f'"{replacement}"'), guard)
                                   if state["trigger"] else ""),
                       "transitions": moves})
    return kept + copies


def glaicus_problems(original: Sequence[dict], patched: Sequence[dict]) -> list[str]:
    """What differs from the flail-heads change, or nothing."""
    problems = []
    count = len(original)
    guard = 'Global("FLPR_FLAIL","GLOBAL",1)'
    changed = set(GLAICUS_ENTRIES) | set(GLAICUS_HEADS)
    for index, state in enumerate(original):
        moves = patched[index]["transitions"]
        added = [number for number in range(len(state["transitions"])) if (index, number) in changed]
        if len(moves) != len(state["transitions"]) + len(added):
            problems.append(f"state {index} has {len(moves)} transitions")
            continue
        if {key: patched[index][key] for key in ("say", "trigger")} != \
                {key: state[key] for key in ("say", "trigger")}:
            problems.append(f"state {index} changed")
        for number, move in enumerate(state["transitions"]):
            expected = dict(move)
            if (index, number) in changed:
                # ADD_TRANS_TRIGGER puts the guard first.
                expected["trigger"] = _guarded("!" + guard, move["trigger"])
                expected["flags"] = move["flags"] | 2
            if moves[number] != expected:
                problems.append(f"transition {index}.{number} is {moves[number]}")
        # A copy of an entry leads to the new states; any other copy keeps its
        # target and also hands over the other two heads.
        for position, number in enumerate(added):
            move = state["transitions"][number]
            copy = {**move, "flags": move["flags"] | 2, "trigger": _guarded(move["trigger"], guard)}
            if (index, number) in GLAICUS_ENTRIES:
                copy["target"] = ("KPGLAI01", count)
            else:
                copy["action"] = " ".join(('GiveItem("blun14a",LastTalkedToBy) '
                                           'GiveItem("blun14b",LastTalkedToBy) ' + move["action"]).split())
            if moves[len(state["transitions"]) + position] != copy:
                problems.append(f"copy of {index}.{number} is {moves[len(state['transitions']) + position]}")
    new = patched[count:]
    if len(new) != 10:
        return problems + [f"{len(new)} new states"]
    description = new[0]["transitions"]
    targets = [move["target"] for move in description]
    if len(description) != 8 or any(target is None or target[0] != "KPGLAI01"
                                     or not count <= target[1] < count + len(new) for target in targets):
        problems.append(f"the description continues to {targets}")
        return problems
    if any(f'HasItem("{head}",Myself)' not in move["trigger"]
           for move in description for head in ("blun14a", "blun14b", "blun14c")):
        problems.append(f"the description checks {[move['trigger'] for move in description]}")
    # The one-head state hands over what he holds and continues like the
    # original giver; every other branch ends at the farewell.
    one_head = targets[1][1]
    leaving = sorted((state, -1 if move["target"] is None else move["target"][1])
                     for state, row in enumerate(new, count) for move in row["transitions"]
                     if move["target"] is None or move["target"][1] < count)
    if len(leaving) != 4 or (one_head, GLAICUS_AFTER) not in leaving or any(
            target != GLAICUS_FAREWELL for state, target in leaving if state != one_head):
        problems.append(f"the new states return to {leaving}")
    return problems


def dialogue_problems(expected: Sequence[dict], patched: Sequence[dict]) -> list[str]:
    if len(patched) != len(expected):
        return [f"{len(patched)} states, expected {len(expected)}"]
    return [f"state {index} is {actual}, expected {wanted}"
            for index, (wanted, actual) in enumerate(zip(expected, patched)) if wanted != actual]


def tra_strings(text: str) -> Mapping[int, str]:
    """@number = ~text~ or "text" entries of a TRA file."""
    result, position = {}, 0
    while True:
        at = text.find("@", position)
        if at < 0:
            return result
        equals = text.find("=", at)
        number = text[at + 1:equals].strip()
        rest = text[equals + 1:].lstrip()
        start = equals + 1 + (len(text[equals + 1:]) - len(rest))
        if not number.isdigit() or not rest or rest[0] not in "~\"":
            position = at + 1
            continue
        delimiter = "~~~~~" if rest.startswith("~~~~~") else rest[0]
        end = text.find(delimiter, start + len(delimiter))
        result[int(number)] = text[start + len(delimiter):end]
        position = end + len(delimiter)
