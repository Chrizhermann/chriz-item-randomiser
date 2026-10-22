"""Item Randomiser's second copies of the rewards it moves.

Some rewards are in the game twice: a shop, a creature or an area container
holds a second copy of the item that a table row moves. Item Randomiser's
``lists/duplicates/bg1.2da`` and ``bg2.2da`` name these copies by the row's
Ident and item; when that row's item moves, its installer removes the copy
(``lib/delete.tpa``), leaving the table's replacement where a creature carried
it. Chriz Item Randomiser does the same while a game runs: the copies belong to
their moved source, and the source gate removes them unless the campaign keeps
the source in place (``prototype/FLPRGat.lua``).

A copy is found by meaning in the scanned game: every record of the moved item
in the holder, with EET's names for BG1 rows, whatever its slot, charges or
flags. A holder this game lacks, or one without the item, has no copy to remove.
Where a reward's route already hands over the creature's own copy
(``mode2_rewards.TRANSFER_SPECS``), that copy is not named twice.

The runtime removes only rows it knows, under the names reviewed for them
(``reviewed``): as authored and, for a BG1 row, as EET names them in Chriz Item
Randomiser's own copy of EET's lists. An EET whose lists rename one otherwise
leaves that reward out.
"""

from __future__ import annotations

import re
from collections import defaultdict

from . import templates
from .game_names import EetNames, convert_resource
from .mode2_rewards import REWARDS, SOURCE_REWARDS, transfer_spec

# The scanned records of each holder kind: area containers, creature files, shops.
KINDS = {"are": "container", "cre": "cre-template", "sto": "store"}
CAMPAIGNS = ("bg1", "bg2")
COPY_ID = re.compile(r"(bg1|bg2):duplicate:([1-9][0-9]*)")


def table(campaign: str) -> dict[int, dict]:
    """The rows of one campaign's table by line, as authored but in lower case."""
    if campaign not in CAMPAIGNS:
        return {}
    path = templates.ROOT / "lists" / "duplicates" / f"{campaign}.2da"
    rows, header = {}, False
    for line, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fields = text.lower().split()
        if not fields:
            continue
        if not header:
            header = True  # Item Source Replacement Ident
            continue
        if len(fields) != 4 or "." not in fields[1] or fields[1].rsplit(".", 1)[1] not in KINDS:
            raise ValueError(f"Invalid duplicate row: lists/duplicates/{campaign}.2da:{line}")
        item, source, replacement, ident = fields
        rows[line] = {"item": item, "source": source, "replacement": replacement, "ident": ident}
    return rows


def row(identity: str) -> tuple[str, dict] | None:
    """The campaign and authored row a copy identity ("bg2:duplicate:5") names."""
    match = COPY_ID.fullmatch(identity) if isinstance(identity, str) else None
    if match is None:
        return None
    found = table(match.group(1)).get(int(match.group(2)))
    return (match.group(1), found) if found is not None else None


def accepted(campaign: str, entry: dict) -> dict[str, frozenset[str]]:
    """The reviewed names of a row's holder ("sto:bernard"), item and replacement."""
    def names(resref: str, extension: str) -> frozenset[str]:
        return frozenset({resref, convert_resource(resref, extension, campaign, "eet", None)})

    name, extension = entry["source"].rsplit(".", 1)
    result = {"resource": frozenset(f"{extension}:{resref}" for resref in names(name, extension)),
              "item": names(entry["item"], "itm")}
    if entry["replacement"] != "blank":
        result["replacement"] = names(entry["replacement"], "itm")
    return result


def reviewed(copy: dict) -> bool:
    """Whether a copy (of a plan, or a campaign's with fingerprints) is a shipped
    row under names reviewed for it (FLPRGat.lua)."""
    found = row(copy.get("id"))
    if found is None:
        return False
    names = accepted(*found)
    replacement = copy.get("replacement")
    if isinstance(replacement, dict):
        replacement = replacement.get("resref")
    return (copy.get("resource") in names["resource"] and copy.get("item") in names["item"] and
            replacement in names.get("replacement", {None}))


def holdings(sources: dict[str, dict]) -> dict[tuple[str, str], list[dict]]:
    """Each holder's scanned records of each item, in record order."""
    result = defaultdict(list)
    for source in sources.values():
        if KINDS.get(source["resource"].split(":", 1)[0]) == source["kind"]:
            result[source["resource"], source["item"]].append(source)
    for records in result.values():
        records.sort(key=lambda source: source["record"]["record_index"])
    return result


def _route_copies(identity: str, game: str, names: EetNames | None) -> set[tuple[str, str]]:
    """(creature, item) of the copies the reward's own route already guards, as
    the game names them."""
    spec = REWARDS.get(SOURCE_REWARDS.get(identity, ""), {})
    result = set()
    for name in spec.get("required_transfers", ()):
        transfer = transfer_spec(name, game=game, names=names)
        if identity in transfer["authored_source_ids"]:
            result.add((transfer["source_resource"], transfer["item"]))
    return result


def expected_copies(identity: str, campaign: str, ident: str, item: str, index: dict,
                    held: dict[tuple[str, str], list[dict]], game: str,
                    names: EetNames | None) -> list[dict]:
    """The copies of <item> that move with source <identity> of table row <ident>.

    A row names a copy of the same item; its holder must be in the game and
    hold the item, or be a file the scan could not read (the export then
    leaves the reward out, since its copy cannot be found).
    """
    result = []
    covered = _route_copies(identity, game, names)
    for line, entry in sorted(table(campaign).items()):
        if entry["ident"] != ident or convert_resource(entry["item"], "itm", campaign, game, names) != item:
            continue
        name, extension = entry["source"].rsplit(".", 1)
        holder = f"{extension}:{convert_resource(name, extension, campaign, game, names)}"
        if (holder, item) in covered or (holder not in index["unreadable"] and not held.get((holder, item))):
            continue
        copy = {"id": f"{campaign}:duplicate:{line}", "source_id": identity,
                "resource": holder, "item": item}
        if entry["replacement"] != "blank":
            copy["replacement"] = convert_resource(entry["replacement"], "itm", campaign, game, names)
        result.append(copy)
    return result
