"""Places whose item may not show up, as the editor marks them.

Item Randomiser's lists put some items on creatures that can leave alive,
flee or not appear, as the player plays (lists/carriers, Shows "maybe"). Such
a place stays, as Item Randomiser has it, and the editor marks it with LABEL
and a line on why, from the row's reason. A place on a creature that never
keeps its item is left out instead (original_catalog.CARRIER_GOES_MISSING).

The texts are English, as the editor is; their wording is Chris's to choose.
"""
from __future__ import annotations

from .game_names import EetNames, eet_names
from .original_catalog import carriers
from .platform import game_platform, preset_campaigns
from .profiles import ProfileError
from .templates import campaign_tables

LABEL = "May not show up"
# What the carrier may do, by lists/carriers' reason; "{}" is who carries it.
REASONS = {
    "conversation": "{} may leave after a conversation",
    "reward": "{} may reward you and leave",
    "flees": "{} may flee",
    "story": "{} may leave or not appear, depending on the quest",
    "difficulty": "{} may not appear, depending on the difficulty",
    "party": "{} may not appear, depending on your party",
    "transformed": "{} may turn into another creature",
}
# Names a group's line shows before "and N more", as special_places' label.
_NAMES = 4


def found(tables, game: str, names: EetNames | None) -> dict[tuple[str, str], str]:
    """The (area, creature) resources whose place's item may not show up, of
    lists/carriers of the table campaigns <tables> in <game>'s names, each
    with its reason."""
    result = {}
    for table in tables:
        for key, (reason, shows) in carriers(table, game, names).items():
            if shows == "maybe":
                result[key] = reason
    return result


def hints(world: dict) -> dict[tuple[str, str], str]:
    """found() for the scanned game: the tables of every preset campaign it
    has. None for a game no preset is for."""
    try:
        platform = game_platform(world)
        campaigns = preset_campaigns(world)
    except ProfileError:
        return {}
    tables = dict.fromkeys(table for campaign in campaigns for table in campaign_tables(campaign))
    return found(tables, platform, eet_names(world["game_root"]) if platform == "eet" else None)


def carried_by(authored: dict) -> list[str]:
    """The creatures that can carry the item of a resolved original row (a
    target registration's authored fields): its creature, or a group's members."""
    creature = authored.get("creature_resource")
    if isinstance(creature, str):
        return [creature]
    members = authored.get("members")
    return [member for member in members if isinstance(member, str)] if isinstance(members, list) else []


def mark(area: object, creatures: list[str], hints_found: dict[tuple[str, str], str],
         names=None) -> dict | None:
    """The mark {"label", "note"} of a place in <area> whose item one of
    <creatures> carries, or None when none of them may go missing.

    <names>(area, creature) gives the names a creature has in the area (the
    scan's actors)."""
    if not isinstance(area, str):
        return None
    by_reason: dict[str, list[str]] = {}
    for creature in creatures:
        reason = hints_found.get((area, creature))
        if reason is not None and creature not in by_reason.setdefault(reason, []):
            by_reason[reason].append(creature)
    lines = [REASONS[reason].format(_who(area, carriers_of, len(creatures) == 1, names)) + "."
             for reason, carriers_of in by_reason.items()]
    return {"label": LABEL, "note": " ".join(lines)} if lines else None


def _who(area: str, creatures: list[str], alone: bool, names) -> str:
    """"Desreta", "Necre and Taibela": the creatures by the names they have in
    the area; "The carrier" for a place's only creature that has none."""
    shown: list[str] = []
    for creature in creatures:
        known = list(names(area, creature)) if names is not None else []
        if not known and alone:
            return "The carrier"
        for name in known or [creature.split(":", 1)[-1].upper()]:
            if name not in shown:
                shown.append(name)
    listed = shown[:_NAMES]
    more = len(shown) - len(listed)
    if more:
        return ", ".join(listed) + f" and {more} more"
    return listed[0] if len(listed) == 1 else ", ".join(listed[:-1]) + " and " + listed[-1]
