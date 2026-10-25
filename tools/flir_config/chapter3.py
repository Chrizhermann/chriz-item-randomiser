"""Item Randomiser's chapter 3 places (its chapter3.fl): a vampire or a shadow thief.

Rows 25, 49, 215 and 252 of the extended BG2 locations table. Item
Randomiser's handler (lib/weidu_action.tpa) gives each of their items to two
creatures, since the party fights one side of the guild war only: to one of
Bodhi's vampires, whom Aran's side fights in her lair (AR0801.BCS creates
them for Aran's last job), and to one of Aran's shadow thieves, whom Bodhi's
side fights in his guild house (AR0329; with Rogue Rebalancing in its own
guild, RR#010) or at the docks (Arkanis Gath, whom AR0300.BCS creates for
Bodhi's first job). It chooses each among those who can use the item, else
among all, the rule group_recipients.py has.

CIR gives each place one reward. It goes to the first of these creatures who
can take it, in this order: the vampire drawn for the reward, alive, into its
backpack; the thief drawn for it, only at his corpse (alive he is the party's
ally on Aran's side, and Rogue Rebalancing removes the living thieves once
Aran is dead); Aran Linvail, also at his corpse, whom Bodhi's side must kill
(BODHI.DLG asks for Dead("aran")). So on Aran's side the vampire carries it,
and on Bodhi's side it lies at the corpse of whichever of the thief and Aran
dies first (FLPRNat "first-of"). Nothing is given twice, so nothing needs
destroying afterwards, as Item Randomiser's script for the Alternatives mod
does with the second copy.

Not among Item Randomiser's creatures here: Tanova (BODTAN), whose vampire
script (VAMPIR01.BCS of the unmodded game) replaces her with a bat while she
lives once her hit points fall below 10, which would take a reward she
carries; and Dedral (ARNWAR07, RR#DEDRL), of whom AR0329's traps create more
(AMBUSH2.BCS, ARNSPW1.BCS) and who leaves in his conversation, so that his
corpse could not be told from another's.

Each creature must be where the runtime looks for it (native_export): once in
the areas Item Randomiser's handler names, as an actor that appears there or
as a creature its area's own script creates, and the only creature there of
its script name.
"""
from __future__ import annotations

from collections import Counter

from .script_text import block_actions, block_parts, naming, script_blocks

TARGET = "chapter3.fl"
# The place's own resource: the vampires' area.
AREA = "ar0801"
# The areas Item Randomiser's handler names; it adds its watcher to each.
AREAS = ("ar0801", "ar0329", "rr#010", "ar0300")
VAMPIRES = ("lassal02", "bodvam01", "bodvam02")
THIEVES = ("booter02", "arnwar06", "arkanisg")
# With Rogue Rebalancing (its RR#ARAN2.CRE): its guild's thieves, as Item
# Randomiser takes them.
RR_THIEVES = ("rr#bootr", "rr#haz", "arkanisg")
RR_MARK = "cre:rr#aran2"
ARAN = ("aran02", "rr#aran2")
# The actions that create a creature and name its file first. Not
# CreateCreatureAtLocation, which creates it at a point a variable keeps.
CREATIONS = frozenset({7, 227, 228, 231, 232, 233, 238, 250, 252, 295, 323})


def groups(resources) -> list[tuple[tuple[str, ...], bool, bool]]:
    """The creatures of each group in the order they take the reward, whether
    the group takes it only at a corpse, and whether one is drawn per reward."""
    thieves = RR_THIEVES if RR_MARK in resources else THIEVES
    return [(VAMPIRES, False, True), (thieves, True, True), (ARAN, True, False)]


def candidates(resources) -> list[str]:
    """Every creature of the place, as CRE resources in group order."""
    return ["cre:" + name for names, _, _ in groups(resources) for name in names]


def area_script(data: bytes) -> str | None:
    """The area's own script (the ARE header's resref at 0x94), lower case, or None."""
    name = data[0x94:0x9C].split(b"\0", 1)[0].decode("latin-1").strip().lower()
    return name or None


def created(data: bytes, label: str) -> Counter:
    """How many actions of a compiled script create each creature, by its file
    (lower case), whatever their conditions."""
    found = Counter()
    with naming(label):
        script = script_blocks(data)
        for block in script.blocks:
            for action in block_actions(block_parts(block, script.newline)[2], script.newline):
                if action.opcode in CREATIONS:
                    found[action.strings[0].decode("latin-1").lower()] += 1
    return found
