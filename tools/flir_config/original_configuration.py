"""Build a reviewed post-install draft from the original Randomiser tables.

Every enabled authored location remains a distinct target registration. Rows
that need runtime adapters carry only table data and scanned resource hashes;
this module never supplies executable code or chooses an assignment.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

from .campaigns import OVERFLOW_TARGET_KINDS
from . import carrier_hints
from .game_names import convert_resource, eet_names
from .original_catalog import (CARRIER_GOES_MISSING, NO_ROUTE, UNREADABLE_PLACE_PROBLEMS, mode2_route_source,
                               resolve_template)
from .original_options import is_spell_shield_row, spell_shield_randomized, spell_shield_spec, tome_variants
from .platform import game_platform, preset_campaigns, preset_game
from .quest_options import kangaxx_problem
from .templates import campaign_tables, optional_mod_prefixes
from .original_registrations import (SHIPPED_REPLACEMENT, is_unreferenced_creature_source,
                                     target_record as original_target_record)
from .mode2_rewards import source_spec as mode2_source_spec
from .cursed_disguises import cursed_disguise_spec, item_revisions_installed
from .duplicates import expected_copies, holdings, reviewed
from .prepared import QUEST_CHANGES, applied, applied_changes, left_out
from .profiles import ProfileError, edit_profile, new_profile, validate_catalog
from .resources import GameResources, RESOURCE_TYPES
from .scripted_sources import blpool_registration
from .sources import Scans, UNUSABLE_SOURCE, new_binding_plan, unreadable_notes, validate_world
from .special_places import place as special_place, templates as special_templates
from .virtual_sources import virtual_registration

_SOURCE_FIELDS = ("id", "source_ref", "campaign", "family", "source", "ident",
                  "token", "tier", "occurrence", "chance")
_TARGET_FIELDS = ("id", "source_ref", "endpoint_id", "endpoint_kind",
                  "endpoint_source_ref", "campaign", "family", "location",
                  "target", "area", "ident", "tier", "occurrence", "chance",
                  "area_resource", "creature_resource", "container_name",
                  "members", "target_ids")
_PAYLOAD_FIELDS = ("resref", "expiration", "charges", "flags")
_USABLE_PROBLEMS = (None, "scripted-source-requires-adapter", "unshey-requires-legacy-adapter")
# The original BG2 row that leaves the item shipped with Chriz Item Randomiser.
_SHIPPED_REPLACEMENT_ROW = "bg2:base:item:86"
# Rounds of leaving out rows the runtime check names before giving up.
_FIT_ROUNDS = 8


class _NotUsable(Exception):
    """One authored row this game cannot use; the reason is its message."""


def _tier(row: dict) -> str:
    return f"{row['campaign']}:{row['tier']}"


def _source_change(row: dict) -> str | None:
    """The change the installer makes that this reward row needs. An extended
    reward route needs none: Apply patches the game's own script for it, also
    the book that the installer prepared for the ring's quest."""
    if row["adapter"] == "unshey":
        return "unshey"
    resource = row["source_resource"] or ""
    if row["adapter"] == "scripted" and resource.startswith("bcs:") and mode2_route_source(row) is None:
        return QUEST_CHANGES.get(resource[4:])
    return None


def _target_change(row: dict) -> str | None:
    target = row["target"]
    return QUEST_CHANGES.get(target[:-4]) if target.endswith(".bcs") else None


def _item_defaults(catalog: dict, item_hashes: dict[str, str],
                   names: set[str]) -> dict[str, list[int]]:
    if not names:
        return {}
    result = {}
    with GameResources(Path(catalog["game_root"]), {"itm": RESOURCE_TYPES["itm"]}) as reader:
        for name in sorted(names):
            data = reader.read("itm", name)
            if hashlib.sha256(data).hexdigest() != item_hashes[name]:
                raise ProfileError(f"Installed ITM changed; rescan before building: {name}")
            charges = [0, 0, 0]
            if len(data) > 0x71:
                offset = int.from_bytes(data[0x64:0x68], "little")
                count = int.from_bytes(data[0x68:0x6A], "little")
                for index in range(min(count, 3)):
                    at = offset + index * 0x38 + 0x22
                    if at + 2 > len(data):
                        raise ProfileError(f"Installed ITM ability charges are invalid: {name}")
                    charges[index] = int.from_bytes(data[at:at + 2], "little")
            result[name] = charges
    return result


def _authored_source(row: dict) -> dict:
    return {key: row[key] for key in _SOURCE_FIELDS}


def _original_source(row: dict, resource_hash: str, payload: dict,
                     physical_source_ids: list[str], *, grouped: bool = False) -> dict:
    authored = _authored_source(row)
    if grouped:
        authored["match_policy"] = "all_one_unit"
        authored["expected_matches"] = len(physical_source_ids)
    return {"id": "original-source:" + row["id"], "item": row["item_resref"],
            "resource": row["source_resource"], "resource_sha256": resource_hash,
            "adapter_id": "original-source-v1", "selector": row["id"],
            "copies": 1, "optional": False, "payload": payload,
            "physical_source_ids": physical_source_ids, "authored": authored}


def _original_target(row: dict, resource_hash: str) -> dict:
    return {"id": "original-target:" + row["id"],
            "resource": row["area_resource"], "resource_sha256": resource_hash,
            "adapter_id": "original-target-v1", "selector": row["id"],
            "capacity": 1, "optional": False,
            "authored": {key: row[key] for key in _TARGET_FIELDS}}


def _kangaxx_problem(catalog: dict, world: dict, scans: Scans | None = None) -> str | None:
    """Why this game cannot have the Kangaxx option, or None; reads the quest
    files that are no reviewed original, once for the calls on ``scans``."""
    def read(resources: list[str]) -> dict[str, bytes]:
        with GameResources(Path(catalog["game_root"]),
                           {kind: RESOURCE_TYPES[kind] for kind in ("dlg", "bcs")}) as reader:
            return {resource: reader.read(*resource.split(":")) for resource in resources}

    found = {} if scans is None else scans.option_problems
    if "kangaxx" not in found:
        found["kangaxx"] = kangaxx_problem({row["id"]: row["sha256"] for row in world["resources"]}, read)
    return found["kangaxx"]


def _cursed_disguises(catalog: dict, game: str, item_hashes: dict[str, str]) -> dict:
    """Component 570's metadata for this game's items."""
    log = Path(catalog["game_root"]) / "WeiDU.log"
    return cursed_disguise_spec(item_hashes, game=game,
                                item_revisions=item_revisions_installed(log.read_bytes() if log.exists() else b""))


def unavailable_options(catalog: dict, world: dict) -> dict[str, str]:
    """The original options this game cannot apply, each with the reason the
    player is shown; the editor does not offer them.

    A preset that asks for one anyway is refused with its reason, except for
    the two that would change nothing here: the tome variant without Baldur's
    Gate's lists, and cursed disguises without a cursed item.
    """
    game_root = world["game_root"]
    game = game_platform(world)
    reasons = {}
    problem = _kangaxx_problem(catalog, world)
    if problem:
        reasons["kangaxx"] = problem
    if not applied(game_root, "flail_heads"):
        reasons["flail_heads"] = left_out(game_root, "flail_heads")
    if not any("bg1" in campaign_tables(campaign) for campaign in preset_campaigns(world)):
        reasons["totsc_tome"] = ("The ToTSC tome variant changes a reward of Baldur's Gate's lists, "
                                 "which no preset for this game uses")
    try:
        spell_shield_spec(world, randomized=False)
    except ProfileError as exc:
        reasons["more_spell_shield"] = str(exc)
    try:
        if not _cursed_disguises(catalog, game,
                                 {item["resref"]: item["sha256"] for item in catalog["items"]})["choices"]:
            reasons["cursed_disguise"] = ("Cursed disguise items need one of the cursed items they disguise, "
                                          "which this game does not have")
    except ProfileError as exc:
        reasons["cursed_disguise"] = str(exc)
    return reasons


def build_original_configuration(catalog: dict, world: dict, template_id: str, *,
                                 items: str | None = None, targets: str | None = None,
                                 options: dict | None = None,
                                 exclude: dict[str, str] | None = None,
                                 scans: Scans | None = None) -> dict:
    """Return a data-only ``{profile, plan}`` preserving enabled authored rows.

    A row this game cannot use is left out and listed in ``not_used`` instead of
    stopping the configuration: an item, area or creature the game lacks, a
    record that is not where the tables put it or is there more than once, and
    each row named in ``exclude`` (row id -> reason). So is a quest reward or
    place whose conversation or script CIR cannot change yet (NO_ROUTE), in
    every game; the notes name these apart. A left-out reward stays as the game
    has it. ``row_ids`` maps each plan identity to its authored row. ``scans``
    holds the catalogues already validated.
    """
    def resolve(game: str, names) -> dict:
        return resolve_template(catalog, world, template_id, game=game,
                                items=items, targets=targets, options=options, names=names)

    return _build(catalog, world, template_id, exclude or {}, scans, resolve, {})


def _build(catalog: dict, world: dict, template_id: str, exclude: dict[str, str],
           scans: Scans | None, resolve, item_defaults: dict[str, list[int]]) -> dict:
    """build_original_configuration with the draft from ``resolve(game, names)``;
    ``item_defaults`` keeps the charges of the items already read."""
    if scans is None:
        catalog_items = validate_catalog(catalog)
        index = validate_world(world, catalog)
    else:
        catalog_items, index = scans.indexes(catalog, world)
    game = preset_game(world, template_id)
    names = eet_names(world["game_root"]) if game == "eet" else None
    draft = resolve(game, names)
    if draft["options"].get("flail_heads") and not applied(world["game_root"], "flail_heads"):
        raise ProfileError("The flail heads option needs Glaicus's conversation as Chriz Item Randomiser "
                           "prepares it: " + left_out(world["game_root"], "flail_heads"))
    if draft["options"].get("kangaxx"):
        problem = _kangaxx_problem(catalog, world, scans)
        if problem:
            raise ProfileError(problem)
    # A reward whose script or conversation the installer could not prepare
    # stays as installed: its rows are left out, and the player is told why.
    prepared = applied_changes(world["game_root"])
    unprepared = defaultdict(list)
    enabled_sources, enabled_targets = [], []
    for rows, change_of, enabled in ((draft["items"], _source_change, enabled_sources),
                                     (draft["targets"], _target_change, enabled_targets)):
        for row in rows:
            change = change_of(row) if row["enabled"] else None
            if change and change not in prepared:
                unprepared[change].append(row["id"])
            elif row["enabled"]:
                enabled.append(row)
    # The shop's extra copies; whether the scroll's row is randomised too
    # follows from the rewards finally chosen, below.
    stock = spell_shield_spec(world, randomized=False) if draft["options"].get("more_spell_shield") else None
    not_used = []

    def leave_out(row: dict, kind: str, reason: str) -> None:
        not_used.append({"id": row["id"], "row": row["source_ref"], "kind": kind, "reason": reason})

    # The old installer omits a physical source row only when the exact item
    # has no matching record. Its authored location remains in the pool.
    physical = index["sources"]
    candidates = []
    absent_mods = defaultdict(int)
    for row in enabled_sources:
        if row["id"] in exclude:
            leave_out(row, "reward", exclude[row["id"]])
            continue
        if stock and is_spell_shield_row(row):
            candidates.append(row)
            continue
        if row["binding_problem"] == NO_ROUTE:
            leave_out(row, "reward", NO_ROUTE)
            continue
        if row["binding_problem"] == "original-source-has-no-matching-record":
            continue
        if row["binding_problem"] == "optional-item-absent":
            absent_mods[row["optional_mod"]] += 1
            continue
        if row["availability_reason"] == "source-resource-unreadable":
            # The scan could not read its file (the notes name it); it stays as the game has it.
            leave_out(row, "reward", row["availability_reason"])
            continue
        if row["adapter"] == "unshey" and not any(
                source["resource"] == row["source_resource"]
                and source["item"] == row["item_resref"]
                for source in physical.values()):
            continue
        if not row["available"] and row["adapter"] != "unshey":
            leave_out(row, "reward", row["availability_reason"])
        elif row["binding_problem"] not in _USABLE_PROBLEMS:
            leave_out(row, "reward", row["binding_problem"])
        else:
            candidates.append(row)

    wanted_defaults = {row["item_resref"] for row in candidates
                       if row["adapter"] in ("virtual", "scripted", "unshey")}
    unread = wanted_defaults - item_defaults.keys()
    item_defaults.update(_item_defaults(catalog, {name: catalog_items[name]["sha256"] for name in unread},
                                        unread))
    defaults = item_defaults

    def choose(row: dict) -> tuple[dict | None, str | None, str | None]:
        """Return (registration, physical source id, replacement) for one reward row."""
        item, resource = row["item_resref"], row["source_resource"]
        if stock and is_spell_shield_row(row):
            # The option changes the shop's first SCRL8X row; with another
            # one, which row the lists move is ambiguous.
            if sum(source["resource"] == stock["resource"] and source["item"] == item
                   for source in physical.values()) > 1:
                raise _NotUsable("ambiguous-source-records")
            registration = _original_source(row, stock["resource_sha256"], stock["payload"],
                                            [stock["original_source_id"]] if stock["original_source_id"] else [])
            registration["copies"] = stock["quantity"]
            return registration, None, None
        if row["adapter"] == "virtual":
            return virtual_registration(item, row["id"], 1, catalog_items[item]["sha256"],
                                        defaults[item]), None, None
        if row["adapter"] == "scripted" and resource == "bcs:blpool" and item == "blun12":
            return blpool_registration(world), None, None
        if row["adapter"] in ("scripted", "unshey"):
            if resource not in index["resources"]:
                raise _NotUsable("source-resource-absent")
            matched = sorted(source["id"] for source in physical.values()
                             if source["resource"] == resource and source["item"] == item)
            if row["adapter"] == "unshey" and len(matched) != 1:
                raise _NotUsable("ambiguous-unshey-records")
            payload = {"resref": item, "expiration": 0,
                       "charges": defaults[item], "flags": 0}
            if row["adapter"] == "unshey":
                payload = {key: physical[matched[0]]["record"][key]
                           for key in _PAYLOAD_FIELDS}
            elif resource == "spl:spin621":
                from .spell_rewards import PAYLOAD
                payload = {**PAYLOAD, "charges": list(PAYLOAD["charges"])}
            elif mode2_route_source(row) is not None:
                payload = mode2_source_spec(mode2_route_source(row), game=game, names=names)["payload"]
            return _original_source(row, index["resources"][resource]["sha256"], payload,
                                    matched if row["adapter"] == "unshey" else []), None, None
        ids = row["source_ids"]
        if not ids:
            raise _NotUsable("source-record-absent")
        if len(ids) > 1 or is_unreferenced_creature_source(row, physical):
            if any(physical[identity]["kind"] != "cre-template" for identity in ids):
                raise _NotUsable("ambiguous-source-records")
            payloads = [{key: physical[identity]["record"][key] for key in _PAYLOAD_FIELDS}
                        for identity in ids]
            if any(payload != payloads[0] for payload in payloads[1:]):
                raise _NotUsable("grouped-source-payload-mismatch")
            return _original_source(row, index["resources"][resource]["sha256"], payloads[0],
                                    sorted(ids), grouped=len(ids) > 1), None, None
        source = physical[ids[0]]
        # Binding accepts only a finite, unambiguous inventory record.
        if UNUSABLE_SOURCE.intersection(source["concerns"]):
            raise _NotUsable("source-record-unusable")
        if row["replacement"] != "blank":
            replacement = convert_resource(row["replacement"], "itm", row["campaign"], game, names)
            if (source["kind"] not in ("cre-template", "embedded-cre", "container")
                    or replacement == item):
                raise _NotUsable("replacement-not-applicable")
            if replacement not in catalog_items and not (
                    replacement == SHIPPED_REPLACEMENT and row["id"] == _SHIPPED_REPLACEMENT_ROW):
                raise _NotUsable("replacement-item-absent")
            return None, ids[0], replacement
        if (source["kind"] in ("cre-template", "embedded-cre")
                and any(slot < 21 or slot > 36 for slot in source["record"]["slots"])):
            return None, ids[0], "blank"
        return None, ids[0], None

    chosen, claimed = [], set()
    held = holdings(physical)
    for row in candidates:
        try:
            registration, physical_id, replacement = choose(row)
            # Item Randomiser's second copies of this reward, removed when it moves.
            copies = expected_copies(registration["id"] if registration is not None else physical_id,
                                     row["campaign"], row["ident"], row["item_resref"], index, held, game, names,
                                     route=mode2_route_source(registration.get("authored"))
                                     if registration is not None else None)
            if any(copy.get("replacement", row["item_resref"]) not in catalog_items for copy in copies):
                raise _NotUsable("replacement-item-absent")
            if not all(reviewed(copy) for copy in copies):
                raise _NotUsable("second-copy-name-unreviewed")
        except _NotUsable as exc:
            leave_out(row, "reward", str(exc))
            continue
        records = [physical_id] if physical_id else registration.get("physical_source_ids", [])
        # One physical record can be moved by one row only.
        if claimed.intersection(records):
            leave_out(row, "reward", "source-record-used-twice")
            continue
        claimed.update(records)
        chosen.append((row, registration, physical_id, replacement, copies))
    places = []
    for row in enabled_targets:
        if row["id"] in exclude:
            leave_out(row, "place", exclude[row["id"]])
        elif row["binding_problem"] == NO_ROUTE:
            leave_out(row, "place", NO_ROUTE)
        elif row["area_resource"] not in index["resources"]:
            leave_out(row, "place", "area-resource-absent")
        elif row["binding_problem"] in UNREADABLE_PLACE_PROBLEMS:
            leave_out(row, "place", row["binding_problem"])
        elif row["binding_problem"] in ("creature-never-appears", CARRIER_GOES_MISSING):
            leave_out(row, "place", row["binding_problem"])
        else:
            places.append(row)
    if not chosen or not places:
        raise ProfileError("Original template has no enabled sources or locations")

    profile = edit_profile(new_profile(catalog, template_id, draft["name"]), catalog,
                           include=sorted({row["item_resref"] for row, *_ in chosen}))
    plan = new_binding_plan(profile, catalog, world, scans=scans)
    plan["original_template_id"] = template_id
    plan["original_choices"] = draft["choices"].copy()
    plan["original_options"] = draft["options"].copy()
    if stock is not None:
        plan["spell_shield"] = spell_shield_spec(world, randomized=spell_shield_randomized(
            row for row, *_ in chosen))
    if draft["options"].get("cursed_disguise"):
        plan["cursed_disguises"] = _cursed_disguises(
            catalog, game, {name: item["sha256"] for name, item in catalog_items.items()})
    plan["target_registrations"] = []
    randomization = {"sources": {}, "targets": {},
                     "row_filters": {"sources": {}, "targets": {}},
                     "loss_percent": (draft["options"]["loss_percent"]
                                      if draft["options"]["loss_mode"] == "some" else 0),
                     "unrandomized_percent": draft["options"]["unrandomized_percent"],
                     "overflow": True}
    plan["randomization"] = randomization
    by_item = defaultdict(lambda: {"source_ids": [], "registration_ids": []})
    source_rows, row_ids = [], {}
    copies = sorted((copy for *_, row_copies in chosen for copy in row_copies),
                    key=lambda copy: (copy["source_id"], copy["id"]))
    if copies:
        plan["duplicates"] = copies
    for row, registration, physical_id, replacement, _ in chosen:
        item_choices = by_item[row["item_resref"]]
        if registration is not None:
            identity = registration["id"]
            plan["registrations"].append(registration)
            item_choices["registration_ids"].append(identity)
        else:
            identity = physical_id
            item_choices["source_ids"].append(identity)
            if replacement is not None:
                plan.setdefault("replacements", {})[identity] = replacement
        source_rows.append((identity, row))
        row_ids[identity] = row["id"]

    for item, choices in sorted(by_item.items()):
        plan["sources"].append({"item": item, "policy": "relocate",
                                "source_ids": sorted(choices["source_ids"]),
                                "registration_ids": sorted(choices["registration_ids"])})
    target_rows = []
    for row in places:
        registration = _original_target(row, index["resources"][row["area_resource"]]["sha256"])
        plan["target_registrations"].append(registration)
        plan["targets"].append({"id": registration["id"], "capacity": 1, "optional": False})
        target_rows.append((registration["id"], row))
        row_ids[registration["id"]] = row["id"]

    targets_by_ident = defaultdict(list)
    for identity, row in target_rows:
        if row["ident"] != "x":
            targets_by_ident[(row["campaign"], row["ident"])].append(identity)
        randomization["targets"][identity] = [_tier(row)]
        randomization["row_filters"]["targets"][identity] = {
            "chance": row["chance"],
            "group": "" if row["ident"] == "x" else f"{row['campaign']}:{row['ident']}"}
    for identity, row in source_rows:
        excluded = [{"id": target_id, "ordinal": 1}
                    for target_id in targets_by_ident.get((row["campaign"], row["ident"]), [])]
        randomization["sources"][identity] = {
            "tier": _tier(row), "loss_exempt": row["tier"] in ("fa", "ka"),
            "exclude_targets": excluded}
        randomization["row_filters"]["sources"][identity] = {
            "chance": row["chance"],
            "group": "" if row["ident"] == "x" else f"{row['campaign']}:{row['ident']}"}
        if (draft["options"].get("totsc_tome", False) and
                row["campaign"] == "bg1" and row["ident"] == "b2c"):
            randomization["sources"][identity]["reward_variants"] = tome_variants(row["campaign"], game,
                                                                                 names)
    plan["registrations"].sort(key=lambda row: row["id"])
    plan["target_registrations"].sort(key=lambda row: row["id"])
    plan["targets"].sort(key=lambda row: row["id"])
    return {"profile": profile, "plan": plan,
            "left_out": [left_out(world["game_root"], change) for change in sorted(unprepared)],
            "absent_mods": [_absent_mod_note(mod, count, catalog_items)
                            for mod, count in sorted(absent_mods.items())],
            "not_used": not_used,
            "not_used_notes": (unreadable_notes(world) + list(names.notes if names else ())
                               + _hint_notes(places, game, names)
                               + _not_used_notes(not_used)),
            "row_ids": row_ids}


def _absent_mod_note(mod: str, count: int, catalog_items) -> str:
    """Why <count> rewards of a preset's optional <mod> are left out: the mod is
    not installed, or (when the game has any item of it) not the part of it
    that adds them."""
    prefixes = optional_mod_prefixes(mod)
    if not any(name.startswith(prefixes) for name in catalog_items):
        return (f"{mod} is not installed, so {count} {'reward' if count == 1 else 'rewards'} from it "
                f"{'is' if count == 1 else 'are'} left out")
    return (f"{count} {'reward needs an item' if count == 1 else 'rewards need items'} of {mod} "
            f"that this game does not have, so {'it is' if count == 1 else 'they are'} left out")


def _hint_notes(places: list[dict], game: str, names) -> list[str]:
    """How many of the places kept the editor marks as places whose item may
    not show up (carrier_hints), by the tables of their rows."""
    hints = carrier_hints.found(dict.fromkeys(row["campaign"] for row in places), game, names)
    count = sum(any((row["area_resource"], creature) in hints for creature in carrier_hints.carried_by(row))
                for row in places)
    if not count:
        return []
    return [f"{count} {'place is' if count == 1 else 'places are'} marked “{carrier_hints.LABEL}”: "
            f"{'its' if count == 1 else 'their'} carrier may leave, flee or not appear, depending on how you play"]


def _not_used_notes(rows: list[dict]) -> list[str]:
    """What the left-out rows are: those this game cannot use, the places on
    creatures that always go missing, those whose quest CIR cannot change yet,
    then what becomes of a left-out reward."""
    unusable = [row for row in rows if row["reason"] not in (NO_ROUTE, CARRIER_GOES_MISSING)]
    missing = [row for row in rows if row["reason"] == CARRIER_GOES_MISSING]
    waiting = [row for row in rows if row["reason"] == NO_ROUTE]
    notes = []
    if unusable:
        notes.append(f"{_counted(unusable)} from the lists cannot be used in this game, so "
                     + ("it is" if len(unusable) == 1 else "they are") + " left out")
    if missing:
        notes.append(f"{_counted(missing)} from the lists "
                     + ("is on a creature that is never there or always escapes" if len(missing) == 1
                        else "are on creatures that are never there or always escape")
                     + ", so " + ("it is" if len(missing) == 1 else "they are") + " left out")
    if waiting:
        notes.append(f"{_counted(waiting)} from the lists "
                     + ("belongs to a quest" if len(waiting) == 1 else "belong to quests")
                     + " that Chriz Item Randomiser cannot change yet, so "
                     + ("it is" if len(waiting) == 1 else "they are") + " left out")
    if any(row["kind"] == "reward" for row in rows):
        notes.append("A left-out reward stays as the game has it")
    return notes


def _counted(rows: list[dict]) -> str:
    """How many rewards and places <rows> are, as "2 rewards and 1 place"."""
    rewards = sum(row["kind"] == "reward" for row in rows)
    return " and ".join(f"{count} {noun if count == 1 else noun + 's'}"
                        for count, noun in ((rewards, "reward"), (len(rows) - rewards, "place")) if count)


def _placeless_sources(plan: dict) -> list[str]:
    """Source identities whose tier lacks a place the campaign check accepts.

    With overflow, each tier with rewards needs a place that can take any reward
    and, when originals may be kept, one that no reward's home claims. Without a
    reward of such a tier its claims go too, so this repeats until stable.
    """
    randomization = plan["randomization"]
    kinds = {registration["id"]: original_target_record(registration)["kind"]
             for registration in plan["target_registrations"]}
    slots = defaultdict(set)
    for identity, tiers in randomization["targets"].items():
        if kinds.get(identity) in OVERFLOW_TARGET_KINDS:
            for ordinal, tier in enumerate(tiers, 1):
                slots[tier].add((identity, ordinal))
    keeping = randomization["unrandomized_percent"] > 0
    rules, placeless = dict(randomization["sources"]), set()
    while True:
        claimed = {(entry["id"], entry["ordinal"])
                   for rule in rules.values() for entry in rule["exclude_targets"]}
        failing = {identity for identity, rule in rules.items()
                   if not (slots[rule["tier"]] - claimed if keeping else slots[rule["tier"]])}
        if not failing:
            return sorted(placeless)
        placeless |= failing
        for identity in failing:
            del rules[identity]


def fit_original_configuration(catalog: dict, world: dict, template_id: str, *,
                               items: str | None = None, targets: str | None = None,
                               options: dict | None = None, scans: Scans | None = None) -> dict:
    """Build from the original tables, leaving out the rows the runtime cannot use.

    The runtime check of the built configuration names every row it cannot use
    (a quest reward without a reviewed adapter, an item its creature cannot
    drop, a recipient it cannot address); those rows are left out and listed in
    ``not_used`` and the configuration is built again until the check passes.
    The rounds share the validated catalogues (``scans``, if the caller has
    them), the resolved tables and the item charges they read.
    """
    scans = Scans(catalog, world) if scans is None else scans
    drafts: list[dict] = []

    def resolve(game: str, names) -> dict:
        # The same template, game and names in every round.
        if not drafts:
            drafts.append(resolve_template(catalog, world, template_id, game=game, items=items,
                                           targets=targets, options=options, names=names))
        return drafts[0]

    return _fit(catalog, world, template_id, scans, resolve)


def fit_special_places(catalog: dict, world: dict, *, scans: Scans | None = None) -> list[dict]:
    """The special places (special_places.place) this game can deliver, one
    target registration of each, as a preset's fit builds and checks them.

    Item Randomiser's lists of each preset campaign whose tables hold them
    (special_places.templates) are fitted with the standard rewards and the
    places of the extended pools, every special place among them also where
    an option brings it: a place's option concerns the rewards its tier takes,
    not how it delivers them. The ordinary places take what a special place
    cannot (a tier's rewards need a place that can hold any number). A place
    the runtime check or the game cannot use is left out, as the fit leaves it
    out. Of the rows of one place (a group's, the chapter 3 places) the first
    is kept: a custom configuration gives it more reward slots instead. Lists
    with none of their special places in the game's areas need no fit.
    """
    scans = Scans(catalog, world) if scans is None else scans
    index = scans.indexes(catalog, world)[1]
    found: dict[str, tuple] = {}
    for template_id in special_templates(world):
        game = preset_game(world, template_id)
        draft = deepcopy(resolve_template(catalog, world, template_id, game=game, items="standard",
                                          targets="extended",
                                          names=eet_names(world["game_root"]) if game == "eet" else None))
        special = [row for row in draft["targets"] if special_place(row) is not None]
        if not any(row["area_resource"] in index["resources"] for row in special):
            continue
        for row in special:
            row["enabled"] = True
        fitted = _fit(catalog, world, template_id, scans, lambda game, names, draft=draft: draft)
        for registration in fitted["plan"]["target_registrations"]:
            key = special_place(registration["authored"])
            if key is None:
                continue
            authored = registration["authored"]
            order = (authored["campaign"], authored["family"], int(authored["id"].rsplit(":", 1)[1]))
            if key not in found or order < found[key][0]:
                found[key] = (order, registration)
    return [registration for _, registration in sorted(found.values(), key=lambda entry: entry[0])]


def _fit(catalog: dict, world: dict, template_id: str, scans: Scans, resolve) -> dict:
    """fit_original_configuration with the draft from ``resolve(game, names)``."""
    from .campaigns import compile_campaign
    from .native_export import _compile

    catalog_items, index = scans.indexes(catalog, world)
    item_defaults: dict[str, list[int]] = {}
    exclude: dict[str, str] = {}
    for _ in range(_FIT_ROUNDS):
        generated = _build(catalog, world, template_id, exclude, scans, resolve, item_defaults)
        placeless = _placeless_sources(generated["plan"])
        if placeless:
            for identity in placeless:
                exclude[generated["row_ids"][identity]] = "no-place-in-tier"
            continue
        campaign = compile_campaign(generated["profile"], catalog, world, generated["plan"], scans=scans)
        problems: dict[str, str] = {}
        _compile(campaign, world, index, catalog_items, problems, catalog=catalog)
        if not problems:
            return generated
        for identity, reason in sorted(problems.items()):
            if identity not in generated["row_ids"]:
                raise ProfileError(reason)
            exclude[generated["row_ids"][identity]] = reason
    raise ProfileError("The original tables could not be fitted to this game; "
                       "the runtime check kept finding rows it cannot use")
