"""Synthetic files for the optional tweaks of cir.tp2 (components 9000-10300).

Hand-written stand-ins with the shapes the original game's files have where
the tweaks change them, never game resources: Cespenar's dialogue and script,
the Duergar merchant's dialogue, Watcher's Keep's first-level script and
stores. Each also has something another mod could have added. WeiDU compiles
the sources in a scratch game; dialogues are checked by structure.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Callable, Collection, Mapping, Sequence

from campaign_patch_fixtures import NL, block, lines, read_dialogue

# Cespenar: state 4 looks through the party's belongings; the last valid of its
# transitions without text is taken, True() leading to "nothing found". Another
# mod has added a recipe (flother). State 12 closes his own forging, after the
# greeting (state 2) in the order of state triggers.
BOTSMITH = lines(
    "BEGIN ~BOTSMITH~", "",
    'IF ~Global("TalkedToBotsmith","GLOBAL",0) InPartySlot(LastTalkedToBy,0)~ THEN BEGIN 0', "  SAY #1",
    '  IF ~~ THEN REPLY #2 DO ~SetGlobal("TalkedToBotsmith","GLOBAL",1)~ GOTO 2', "END", "",
    "IF ~~ THEN BEGIN 1", "  SAY #3", "  IF ~~ THEN GOTO 2", "END", "",
    'IF ~Global("TalkedToBotsmith","GLOBAL",1) Global("ItemMaker","GLOBAL",0) InPartySlot(LastTalkedToBy,0)~'
    " THEN BEGIN 2", "  SAY #4", "  IF ~~ THEN REPLY #5 GOTO 4", "  IF ~~ THEN REPLY #6 GOTO 3", "END", "",
    "IF ~~ THEN BEGIN 3", "  SAY #7", "  IF ~~ THEN EXIT", "END", "",
    "IF ~~ THEN BEGIN 4", "  SAY #8", "  IF ~True()~ THEN GOTO 5", '  IF ~PartyHasItem("compon10")~ THEN GOTO 6',
    '  IF ~PartyHasItem("flother")~ THEN GOTO 7', "END", "",
    "IF ~~ THEN BEGIN 5", "  SAY #9", "  IF ~~ THEN EXIT", "END", "",
    "IF ~~ THEN BEGIN 6", "  SAY #10", "  IF ~PartyGoldLT(5000)~ THEN REPLY #11 GOTO 10",
    '  IF ~PartyGoldGT(4999)~ THEN REPLY #12 DO ~SetGlobal("ItemMaker","GLOBAL",1) TakePartyGold(5000)~ GOTO 11',
    "END", "",
    "IF ~~ THEN BEGIN 7", "  SAY #13", "  IF ~~ THEN EXIT", "END", "",
    "IF ~~ THEN BEGIN 8", "  SAY #14", "  IF ~~ THEN EXIT", "END", "",
    "IF ~~ THEN BEGIN 9", "  SAY #14", "  IF ~~ THEN EXIT", "END", "",
    "IF ~~ THEN BEGIN 10", "  SAY #15", "  IF ~~ THEN EXIT", "END", "",
    "IF ~~ THEN BEGIN 11", "  SAY #16", '  IF ~~ THEN DO ~SetGlobal("ForgeStarted","GLOBAL",1)~ EXIT', "END", "",
    'IF ~GlobalGT("ItemMaker","GLOBAL",0) InPartySlot(LastTalkedToBy,0)~ THEN BEGIN 12', "  SAY #17",
    '  IF ~~ THEN DO ~SetGlobal("ItemMaker","GLOBAL",0)~ EXIT', "END", "",
    "IF ~!InPartySlot(LastTalkedToBy,0)~ THEN BEGIN 13", "  SAY #18", "  IF ~~ THEN EXIT", "END")

# As on the Enhanced Editions, Cespenar already makes armour of the Shadow
# Dragon's scales (state 14), found when he looks through the belongings.
_LOOKS = '  IF ~PartyHasItem("flother")~ THEN GOTO 7' + NL
BOTSMITH_SHADOW = BOTSMITH.replace(_LOOKS, _LOOKS + '  IF ~PartyHasItem("scaleb")~ THEN GOTO 14' + NL) + lines(
    "", "IF ~~ THEN BEGIN 14", "  SAY #19", "  IF ~PartyGoldLT(5000)~ THEN REPLY #11 GOTO 10",
    '  IF ~PartyGoldGT(4999)~ THEN REPLY #12 DO ~TakePartyItemNum("scaleb",1) DestroyItem("scaleb")'
    ' SetGlobal("ItemMaker","GLOBAL",2) TakePartyGold(5000)~ GOTO 11', "END")

BOTSMITH_SCRIPT = (
    block(('Global("ItemMaker","GLOBAL",1)', 'Global("ImpForgeStuff","GLOBAL",1)'),
          ('SetGlobal("ImpForgeStuff","GLOBAL",0)', 'GiveItemCreate("sw1h59",Player1,0,0,0)',
           "StartDialogueNoSet(Player1)"))
    + block(('Global("ItemMaker","GLOBAL",2)', 'Global("ImpForgeStuff","GLOBAL",1)'),
            ('SetGlobal("ImpForgeStuff","GLOBAL",0)', 'GiveItemCreate("sw1h61",Player1,0,0,0)',
             "StartDialogueNoSet(Player1)")))

# The Duergar merchant: state 12 greets a returning party, state 14 answers
# that there is no news; both open the special store (UDDUER02) for a guest of
# the drow and the regular one (UDDUER01) for anyone else.
_STORE = 'DO ~StartStore("{0}",LastTalkedToBy(Myself))~ EXIT'
UDDUER01 = lines(
    "BEGIN ~UDDUER01~", "",
    "IF ~NumTimesTalkedTo(0)~ THEN BEGIN 0", "  SAY #20", "  IF ~~ THEN GOTO 10", "END", "",
    *(row for state in range(1, 10) for row in (f"IF ~~ THEN BEGIN {state}", "  SAY #21", "  IF ~~ THEN EXIT", "END", "")),
    "IF ~~ THEN BEGIN 10", "  SAY #22",
    '  IF ~Global("DrowGuest","GLOBAL",0)~ THEN REPLY #23 ' + _STORE.format("udduer01"),
    '  IF ~Global("DrowGuest","GLOBAL",1)~ THEN REPLY #23 ' + _STORE.format("udduer02"), "END", "",
    "IF ~~ THEN BEGIN 11", "  SAY #24", "  IF ~~ THEN EXIT", "END", "",
    'IF ~Global("PlayerLooksLikeDrow","GLOBAL",0)~ THEN BEGIN 12', "  SAY #25",
    '  IF ~Global("DrowGuest","GLOBAL",0)~ THEN REPLY #26 ' + _STORE.format("udduer01"),
    "  IF ~~ THEN REPLY #27 GOTO 14", "  IF ~~ THEN REPLY #28 GOTO 13",
    '  IF ~Global("DrowGuest","GLOBAL",1)~ THEN REPLY #29 ' + _STORE.format("udduer02"), "END", "",
    "IF ~~ THEN BEGIN 13", "  SAY #30", "  IF ~~ THEN EXIT", "END", "",
    "IF ~~ THEN BEGIN 14", "  SAY #31",
    '  IF ~Global("DrowGuest","GLOBAL",0)~ THEN REPLY #32 ' + _STORE.format("udduer01"),
    "  IF ~~ THEN REPLY #33 GOTO 13",
    '  IF ~Global("DrowGuest","GLOBAL",1)~ THEN REPLY #34 ' + _STORE.format("udduer02"), "END", "",
    'IF ~Global("PlayerLooksLikeDrow","GLOBAL",1)~ THEN BEGIN 15', "  SAY #35",
    "  IF ~~ THEN REPLY #36 " + _STORE.format("udduer02"), "END")

# Watcher's Keep's first level. The original game's difficulty adjustment
# destroys statues for a small party; another mod has split part of it into a
# second block under OR(), whose False() does not stop it. A quest of another
# mod destroys a statue for a reason of its own, and an EE Fixpack style block
# tests the same variable to activate spawns; both stay as they are.
_FIRST = block(('Global("FLWkVisited","AR3001",0)',), ('SetGlobal("FLWkVisited","AR3001",1)',))
_ADJUST = (('Global("SetDifficulty","AR3001",0)', "NumInPartyLT(5)"),
           ('SetGlobal("SetDifficulty","AR3001",1)', 'ActionOverride("gorsta03",DestroySelf())',
            'ActionOverride("gorsta04",DestroySelf())', 'SetGlobal("SPRITE_IS_DEADgorsta03","GLOBAL",1)',
            'SpawnPtActivate("SpawnPoint5")'))
_SPLIT = (('Global("SetDifficulty","AR3001",0)', "OR(2)", "  NumInPartyLT(4)", "  False()"),
          ('ActionOverride("gorsta11",DestroySelf())',))
_QUEST = block(('Global("FLStatueFreed","GLOBAL",1)',),
               ('SetGlobal("FLStatueFreed","GLOBAL",2)', 'ActionOverride("gorsta05",DestroySelf())'))
_SPAWNS = block(('Global("SetDifficulty","AR3001",0)', 'Global("cdActivateSpawns","AR3001",0)'),
                ('SetGlobal("cdActivateSpawns","AR3001",1)', 'SpawnPtActivate("SpawnPoint1")'))


def _off(conditions: Sequence[str]) -> tuple[str, ...]:
    return ("False()", *conditions)


AR3001 = _FIRST + block(*_ADJUST) + _QUEST + block(*_SPLIT) + _SPAWNS
AR3001_KEPT = _FIRST + block(_off(_ADJUST[0]), _ADJUST[1]) + _QUEST + block(_off(_SPLIT[0]), _SPLIT[1]) + _SPAWNS
# SCS has already replaced the adjustment's variable test with False().
AR3001_STOPPED = _FIRST + block(("False()", *_ADJUST[0][1:]), _ADJUST[1]) + _SPAWNS
# Another mod has removed the adjustment.
AR3001_NONE = _FIRST + _QUEST + _SPAWNS


def sources(mod: Path) -> dict[str, dict[str, str]]:
    """The fixtures, and the expected scripts, by compile group."""
    added = (mod / "baf" / "botsmith.baf").read_text(encoding="utf-8")
    return {"tweaks": {"botsmith.d": BOTSMITH, "udduer01.d": UDDUER01, "botsmith.baf": BOTSMITH_SCRIPT,
                       "xbotsmith.baf": added.replace("\r\n", "\n").replace("\n", "\r\n") + "\r\n" + BOTSMITH_SCRIPT,
                       "ar3001.baf": AR3001, "xar3001.baf": AR3001_KEPT, "sar3001.baf": AR3001_STOPPED,
                       "nar3001.baf": AR3001_NONE},
            # The same dialogue name as the other Cespenar, so a group of its own.
            "tweaks-shadow": {"botsmith.d": BOTSMITH_SHADOW}}


# A STOR V1.0 store: header, then its tables in the order given.
SECTIONS = ("sale", "purchased", "drinks", "cures")


def store(items: Sequence[str], order: Sequence[str] = SECTIONS) -> bytes:
    header = bytearray(0x9C)
    header[:8] = b"STORV1.0"
    struct.pack_into("<IIIIII", header, 0x08, 5, 1234, 0x1F, 150, 50, 10)
    tables = {
        "sale": b"".join(struct.pack("<8sHHHHIII", name.encode("ascii"), 0, 3, 0, 0, 1, 2, 0) for name in items),
        "purchased": struct.pack("<II", 11, 12),
        "drinks": struct.pack("<8sIII", b"RUMOUR", 4321, 8, 20),
        "cures": struct.pack("<8sI", b"SPPR103", 100),
    }
    places = {"sale": (0x34, 0x1C), "purchased": (0x2C, 4), "drinks": (0x4C, 0x14), "cures": (0x70, 0x0C)}
    body = bytearray()
    for name in order:
        at, size = places[name]
        struct.pack_into("<II", header, at, 0x9C + len(body), len(tables[name]) // size)
        body += tables[name]
    return bytes(header + body)


def without_scrolls(items: Sequence[str]) -> list[str]:
    return [item for item in items if item.casefold() != "scrl09"]


def trigger_order(data: bytes) -> list[int]:
    """The states with a trigger, in the order the engine evaluates them."""
    states, state_at = struct.unpack_from("<II", data, 8)
    order = {}
    for index in range(states):
        trigger = struct.unpack_from("<I", data, state_at + index * 16 + 12)[0]
        if trigger != 0xFFFFFFFF:
            order[trigger] = index
    return [order[key] for key in sorted(order)]


def _words(text: str) -> str:
    return " ".join(text.split())


# The labels of d/botsmith.d in the order it appends them.
BOTSMITH_STATES = ("main", "bug", "menu1", "dragon", "dragonr", "dragonb", "dragon2", "menu3", "crom", "crom2",
                   "crom3", "crom4", "crom5", "menu4", "eq", "eq2", "eq3", "eq4", "eq5", "eq6", "eq7", "eq8",
                   "menu5", "gesen", "gesen2", "gesen3", "gesen4", "menu6", "wave", "wave2", "wave3", "wave4",
                   "menu7", "silver", "silver2", "silver3", "silver4", "menu8", "imod", "imod2", "imod3", "done")


def _scale(item: str, taken: Collection[str]) -> str:
    """The test for a dragon scale: False() where his dialogue already takes it for a recipe."""
    return "False()" if item in taken else f'PartyHasItem("{item}")'


def botsmith_recipes(taken: Collection[str] = ()) -> tuple[tuple[str, str], ...]:
    """The recipes state 4 offers, last first, with their tests."""
    return (("imod", 'PartyHasItem("blun12")'),
            ("silver", 'OR(2) PartyHasItem("misc8u") PartyHasItem("sw2h15a")'),
            ("wave", 'OR(2) PartyHasItem("halb09a") PartyHasItem("halb09b")'),
            ("gesen", 'OR(2) PartyHasItem("bow19a") PartyHasItem("bow19b")'),
            ("eq", 'OR(3) PartyHasItem("sw1h54a") PartyHasItem("sw1h54b") PartyHasItem("sw1h54c")'),
            ("crom", 'OR(2) PartyHasItem("scrlag") PartyHasItem("hamm07")'),
            ("dragon", f'OR(2) {_scale("scaler", taken)} {_scale("scaleb", taken)}'),
            ("bug", 'PartyHasItem("misc12")'))


def botsmith_problems(original: bytes, patched: bytes, say: Callable[[int], int],
                      taken: Collection[str] = ()) -> list[str]:
    """What differs from Cespenar's SoA recipes, or nothing; say(n) is the game text of @n.

    taken: the dragon scales his dialogue already takes for a recipe, which
    are left to it.
    """
    before, after = read_dialogue(original), read_dialogue(patched)
    count = len(before)
    state = {label: count + index for index, label in enumerate(BOTSMITH_STATES)}
    problems = []
    if len(after) != count + len(BOTSMITH_STATES):
        return [f"{len(after) - count} new states"]
    for index, row in enumerate(before):
        if index != 4 and after[index] != row:
            problems.append(f"state {index} changed")
    looks, kept = after[4], len(before[4]["transitions"])
    if {**looks, "transitions": looks["transitions"][:kept]} != before[4]:
        problems.append("state 4 lost what it had")
    added = looks["transitions"][kept:]
    recipes = botsmith_recipes(taken)
    expected = [{"flags": 2, "reply": None, "journal": None, "trigger": _words(trigger), "action": "",
                 "target": ("BOTSMITH", state[label])} for label, trigger in recipes]
    if added != expected:
        problems.append(f"state 4 adds {added}")
    # The way on from the ankheg plate (menu1) tests the scales the same way,
    # and each scale leads to the recipe for it.
    onward = [move["trigger"] for move in after[state["menu1"]]["transitions"]
              if move["target"] == ("BOTSMITH", state["dragon"])]
    if onward != [dict(recipes)["dragon"]]:
        problems.append(f"the ankheg plate leads on to the dragon scales by {onward}")
    red, shadow = _scale("scaler", taken), _scale("scaleb", taken)
    scales = [(move["trigger"], move["target"]) for move in after[state["dragon"]]["transitions"]]
    if scales != [(shadow, ("BOTSMITH", state["dragonb"])), (red, ("BOTSMITH", state["dragonr"])),
                  (f"{red} {shadow}", ("BOTSMITH", state["dragon2"]))]:
        problems.append(f"the dragon scales lead on by {scales}")
    main = after[state["main"]]
    if main["say"] != say(0) or main["transitions"] != before[4]["transitions"]:
        problems.append(f"the way back to his own recipes is {main}")
    done = after[state["done"]]
    if done != {"say": 70888, "trigger": 'GlobalGT("flItemMaker","GLOBAL",0) InPartySlot(LastTalkedToBy,0)',
                "transitions": [{"flags": 0x0C, "reply": None, "journal": None, "trigger": "",
                                 "action": 'SetGlobal("flItemMaker","GLOBAL",0)', "target": None}]}:
        problems.append(f"the closing state is {done}")
    order = trigger_order(patched)
    if order != [state["done"], *trigger_order(original)]:
        problems.append(f"the state triggers are evaluated in the order {order}")
    # Each recipe sets its own number and starts the original game's forging
    # (11); the party that has both dragon scales chooses one.
    forged = sorted(int(move["action"].split('"flItemMaker","GLOBAL",')[1].split(")")[0])
                    for row in after[count:] for move in row["transitions"]
                    if 'SetGlobal("flItemMaker","GLOBAL",' in move["action"] and move["target"] == ("BOTSMITH", 11))
    if forged != [1, 2, 2, 3, 3, 4, 5, 6, 7, 8, 9]:
        problems.append(f"the recipes forge {forged}")
    # Apart from the copy of state 4, the new states lead only to each other
    # and to the original game's "not enough gold" (10) and forging (11).
    for row in after[count:]:
        if row is main:
            continue
        for move in row["transitions"]:
            target = move["target"]
            if target is not None and (target[0] != "BOTSMITH" or not (target[1] in (10, 11) or target[1] >= count)):
                problems.append(f"a new state continues to {target}")
    return problems


def udduer01_problems(original: bytes, patched: bytes, say: Callable[[int], int]) -> list[str]:
    """What differs from the guest's greeting with both stores, or nothing."""
    before, after = read_dialogue(original), read_dialogue(patched)
    count = len(before)
    problems = []
    for index, row in enumerate(before):
        expected = dict(row)
        if index == 12:
            # ADD_STATE_TRIGGER puts the condition first.
            expected["trigger"] = _words('Global("DrowGuest","GLOBAL",0) ' + row["trigger"])
        if after[index] != expected:
            problems.append(f"state {index} is {after[index]}")
    new = after[count:]
    says = [row["say"] for row in new]
    if sorted(says) != sorted([say(0), say(1), say(2), say(3), 50230]):
        return problems + [f"the new states say {says}"]
    number = {text: count + says.index(text) for text in says}
    store = 'StartStore("{0}",LastTalkedToBy(Myself))'

    def reply(text: int, action: str = "", target: int | None = None) -> dict:
        return {"flags": 0x01 | (0x04 if action else 0) | (0x08 if target is None else 0), "reply": text,
                "journal": None, "trigger": "", "action": action,
                "target": None if target is None else ("UDDUER01", target)}

    greeting = after[number[say(0)]]
    expected = {"say": say(0), "trigger": 'Global("PlayerLooksLikeDrow","GLOBAL",0) Global("DrowGuest","GLOBAL",1)',
                "transitions": [
                    {"flags": 0x06, "reply": None, "journal": None, "trigger": 'Global("flgoodstuff","LOCALS",0)',
                     "action": 'SetGlobal("flgoodstuff","LOCALS",1)', "target": ("UDDUER01", number[say(1)])},
                    {"flags": 0x02, "reply": None, "journal": None, "trigger": 'Global("flgoodstuff","LOCALS",1)',
                     "action": "", "target": ("UDDUER01", number[say(3)])}]}
    if greeting != expected:
        problems.append(f"the guest's greeting is {greeting}")
    wares = after[number[say(3)]]["transitions"]
    if wares != [reply(50226, target=number[50230]), reply(50227, target=13),
                 reply(say(4), store.format("udduer01")), reply(say(5), store.format("udduer02"))]:
        problems.append(f"the guest's choices are {wares}")
    news = after[number[50230]]["transitions"]
    if news != [reply(say(6), store.format("udduer01")), reply(say(7), store.format("udduer02")),
                reply(50232, target=13)]:
        problems.append(f"the guest's choices without news are {news}")
    if trigger_order(patched) != [*trigger_order(original), number[say(0)]]:
        problems.append(f"the state triggers are evaluated in the order {trigger_order(patched)}")
    return problems
