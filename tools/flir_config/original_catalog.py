"""Bind original Randomiser table rows to one scanned installation.

This keeps the complete authored tables, including rows whose source or
destination is absent from the physical scan. Runtime adapters and option
filters remain separate from structural binding.
"""

from __future__ import annotations

from collections import defaultdict
import re

from .game_names import EetNames, convert_resource, eet_names
from .mode2_rewards import DESTROY_GATES, SOURCE_REWARDS, TARGET_REWARDS, target_spec
from .container_rewards import (TARGETS as CONTAINER_TARGETS, target_spec as container_spec,
                                treasure_area, treasure_container)
from .profiles import ProfileError
from .quest_rewards import SOURCE_QUESTS
from .scripted_sources import ITEM as POOL_ITEM, RESOURCE as POOL_RESOURCE
from .templates import ENDPOINT_CAMPAIGNS, PRESETS, ROOT, _table_rows, build_template


_PHYSICAL_SOURCES = {"are", "cre", "sto"}
_SCRIPTED_SOURCES = {"bcs", "dlg"}
# A place whose file the scan could not read (sources.scan_world) is left out.
UNREADABLE_PLACE_PROBLEMS = ("area-resource-unreadable", "creature-resource-unreadable",
                             "store-resource-unreadable")
# A quest reward or place whose conversation or script CIR cannot change yet:
# its row is left out in every game, whatever else the game lacks.
NO_ROUTE = "route-not-supported"


def is_spin621_source(row: dict) -> bool:
    """Only the authored Mode 2 Deck of Many Things item-grant source."""
    return isinstance(row, dict) and all(row.get(key) == value for key, value in {
        "id": "bg2:mode2:item:69", "source_ref": "lists/items/mode2/bg2.2da:69",
        "campaign": "bg2", "family": "mode2", "source": "spin621.spl",
        "ident": "w26", "tier": "10", "token": "w01"}.items())


def is_spin621_target(row: dict) -> bool:
    """Only the paired authored spell target, which has no physical area."""
    return isinstance(row, dict) and all(row.get(key) == value for key, value in {
        "id": "bg2:mode2:target:70", "source_ref": "lists/locations/mode2/bg2.2da:70",
        "campaign": "bg2", "family": "mode2", "target": "spin621.spl",
        "area": "blank", "ident": "w26", "tier": "10", "location": "w1"}.items())


def _table_place(row: dict, relative: str, prefix: str) -> bool:
    """Whether <row> is line N of the extended locations table <relative>, as
    it has it, with the identity <prefix>:target:N."""
    campaign = row.get("campaign")
    for line, fields in _table_rows(ROOT / relative, 6):
        if row["id"] != f"{prefix}:target:{line}":
            continue
        location, tier, target, area, ident = fields[:5]
        expected = {"source_ref": f"{relative}:{line}", "campaign": campaign,
                    "family": "mode2", "location": location.lower(), "tier": tier.lower(),
                    "target": target.lower(), "area": area.lower(), "ident": ident.lower()}
        return all(row.get(key) == value for key, value in expected.items())
    return False


def _table_item(row: dict, relative: str, prefix: str) -> str | None:
    """The item of line N of the extended items table <relative> when <row> is
    that line, as the table has it, with the identity <prefix>:item:N."""
    campaign = row.get("campaign")
    for line, fields in _table_rows(ROOT / relative, 7):
        if row["id"] != f"{prefix}:item:{line}":
            continue
        item, _, source, tier, token, ident = fields[:6]
        expected = {"source_ref": f"{relative}:{line}", "campaign": campaign,
                    "family": "mode2", "source": source.lower(), "tier": tier.lower(),
                    "token": token.lower(), "ident": ident.lower()}
        return item.lower() if all(row.get(key) == value for key, value in expected.items()) else None
    return None


def _same_lines(relative: str, columns: int, fields: tuple[int, int], values: tuple) -> list[int]:
    """The lines of table <relative> whose two <fields> are <values> (lower case)."""
    return [line for line, row in _table_rows(ROOT / relative, columns)
            if (row[fields[0]].lower(), row[fields[1]].lower()) == values]


def _authored_mode2_target(row: dict) -> bool:
    campaign = row.get("campaign")
    if campaign not in ("bg1", "bg2"):
        return False
    return _table_place(row, f"lists/locations/mode2/{campaign}.2da", f"{campaign}:mode2")


def _preset_mode2_place(row: dict) -> str | None:
    """The original extended row of the place a preset's .fl row names: the one
    row of the original table with its target and area, as a preset's standard
    places take the endpoint of the same place (templates._place_endpoints)."""
    campaign, target = row.get("campaign"), row.get("target")
    if campaign not in ("bg1", "bg2") or not isinstance(target, str) or not target.endswith(".fl"):
        return None
    if not any(campaign in spec["campaigns"] and
               _table_place(row, f"lists/presets/{preset}/locations/mode2/{campaign}.2da",
                            f"{campaign}:{preset}:mode2") for preset, spec in PRESETS.items()):
        return None
    same = [f"{campaign}:mode2:target:{line}"
            for line, fields in _table_rows(ROOT / f"lists/locations/mode2/{campaign}.2da", 6)
            if (fields[2].lower(), fields[3].lower()) == (target, row.get("area"))]
    return same[0] if len(same) == 1 else None


def mode2_route_source(row: dict) -> str | None:
    """The source of the extended reward route (mode2_rewards.SOURCE_REWARDS)
    that a row is: its own original row, or a preset's row of the reward that
    one original row names (its conversation or script and item), as Boss
    Loot's row of Aldeth's sword is his route. One reward has one row: a preset
    with two rows of it has no route for either."""
    if not isinstance(row, dict) or not isinstance(row.get("id"), str):
        return None
    if "original-source:" + row["id"] in SOURCE_REWARDS:
        return "original-source:" + row["id"]
    campaign, source = row.get("campaign"), row.get("source")
    if campaign not in ("bg1", "bg2") or not isinstance(source, str):
        return None
    for preset, spec in PRESETS.items():
        relative = f"lists/presets/{preset}/items/mode2/{campaign}.2da"
        item = (_table_item(row, relative, f"{campaign}:{preset}:mode2")
                if campaign in spec["campaigns"] else None)
        if item is not None:
            break
    else:
        return None
    if len(_same_lines(relative, 7, (2, 0), (source, item))) != 1:
        return None
    same = _same_lines(f"lists/items/mode2/{campaign}.2da", 7, (2, 0), (source, item))
    identity = f"original-source:{campaign}:mode2:item:{same[0]}" if len(same) == 1 else None
    return identity if identity in SOURCE_REWARDS else None


def mode2_route_target(row: dict) -> str | None:
    """The place of the extended reward route (mode2_rewards.TARGET_REWARDS)
    that a row is: its own original row, or a preset's row at the .fl place of
    one (_preset_mode2_place). One place has one row: a preset with two rows
    there has no route for either."""
    if not isinstance(row, dict):
        return None
    if row.get("id") in TARGET_REWARDS:
        return row["id"]
    original = _preset_mode2_place(row)
    if original not in TARGET_REWARDS:
        return None
    relative = row["source_ref"].rsplit(":", 1)[0]
    return original if len(_same_lines(relative, 6, (2, 3), (row["target"], row["area"]))) == 1 else None


def mode2_alias_spec(row: dict, game: str | None = None, names: EetNames | None = None) -> dict | None:
    """Return a registered reward only for its exact authored .fl location, or
    a preset's row there (mode2_route_target), as the game names it."""
    if not isinstance(row, dict) or not isinstance(row.get("target"), str) or not row["target"].endswith(".fl"):
        return None
    if row.get("id") in TARGET_REWARDS and not _authored_mode2_target(row):
        return None
    target = mode2_route_target(row)
    return None if target is None else target_spec(target, game=game, names=names)


def container_target_spec(row: dict) -> dict | None:
    """The container quest at an authored place: its row of the original
    extended table, or a preset's row at the same place (Boss Loot's Safana).
    The quest is the spec's target_id."""
    if not isinstance(row, dict):
        return None
    if row.get("id") in CONTAINER_TARGETS:
        return container_spec(row["id"]) if _authored_mode2_target(row) else None
    original = _preset_mode2_place(row)
    return container_spec(original) if original in CONTAINER_TARGETS else None
# The two authored Spellhold scroll selectors have legacy suffixes absent
# from the scanned EET AR0411 container names. Each has one same-item record
# under the corresponding base name; retain exact-name precedence so a game
# that still has the legacy name never silently switches source containers.
_SOURCE_NAME_ALIASES = {
    ("bg2", "are:ar0411", "scrl6u", "is32", "toolct13"): "toolct1",
    ("bg2", "are:ar0411", "scrl6u", "is33", "body1er7"): "body1",
}

# AR1202's original Container8 (Tazok's table) and Container9 (the closet)
# are named Container9 / Container10 in the reviewed EET scan. The authored
# coordinates distinguish them even though Container9 still matches a name:
# locations/base/bg2.2da:250 -> endpoints/base/bg2.2da:234 -> fallbacks.2da:310;
# locations/base/bg2.2da:74,248,279 -> endpoints/base/bg2.2da:72,232,262
# -> fallbacks.2da:321. The paired SCRL6N / SCRL6P,SCRL6Q sources agree.
_TARGET_COORDINATE_ALIASES = {
    ("bg2", "are:ar1202", "ep-bg2-ca49c7b59b05"): (2103, 812),
    ("bg2", "are:ar1202", "ep-bg2-dc7accff0b4e"): (1949, 716),
}


def _normal_name(value: str) -> str:
    # WeiDU/IE names can be presented with spaces and mixed case in the scan.
    # Keep every letter and digit: aliases that differ otherwise need review.
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _source_selectors(campaign: str) -> dict[str, tuple[str, str, int]]:
    path = ROOT / "lists" / "sources" / "base" / f"{campaign}.2da"
    result = {}
    for _, fields in _table_rows(path, 4):
        ident, object_or_slot, policy, expected = fields[:4]
        result[ident.lower()] = (object_or_slot, policy, int(expected))
    return result


def _endpoint_rows(campaign: str) -> dict[str, tuple[str, str, str]]:
    result = {}
    if campaign not in ENDPOINT_CAMPAIGNS:
        return result
    path = ROOT / "lists" / "endpoints" / "base" / f"{campaign}.2da"
    for _, fields in _table_rows(path, 17):
        # A stable endpoint can be reused by many original location rows.
        identity = (fields[9], fields[10], fields[11])
        prior = result.setdefault(fields[6], identity)
        if prior != identity:
            raise ValueError(f"Conflicting endpoint identity: {fields[6]}")
    return result


def _group_members(campaign: str, group: str, game: str, names: EetNames | None) -> list[str]:
    path = ROOT / "lists" / "groups" / f"{campaign}.2da"
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0].lower() == group.lower():
            name = convert_resource(fields[1], "cre", campaign, game, names)
            identity = f"cre:{name}"
            if identity not in result:
                result.append(identity)
    return result


def _source_owner_name(source: dict, targets_by_id: dict[str, dict]) -> str:
    owner = targets_by_id.get(source.get("owner", ""), {})
    return owner.get("selector", {}).get("name", "")


def _routed_source(row: dict, resource: str, item: str) -> bool:
    """Whether CIR has a route for the reward a conversation or script gives:
    an extended reward route, one of the original quests, or the pool."""
    return (mode2_route_source(row) is not None or "original-source:" + row["id"] in SOURCE_QUESTS
            or (resource, item) == (POOL_RESOURCE, POOL_ITEM))


def _unreadable_source(row: dict) -> None:
    row["available"] = False
    row["availability_reason"] = "source-resource-unreadable"
    row["binding_problem"] = row["binding_problem"] or "source-resource-unreadable"


def _resolve_source(row: dict, catalog_items: set[str], resources: set[str],
                    unreadable: set[str], sources: dict[tuple[str, str], list[dict]],
                    targets_by_id: dict[str, dict], game: str,
                    selectors: dict[str, dict[str, tuple[str, str, int]]],
                    names: EetNames | None) -> None:
    campaign = row["campaign"]
    item = convert_resource(row["resref"], "itm", campaign, game, names)
    row.update(item_resref=item, source_resource=None, source_ids=[],
               adapter=None, available=True, availability_reason=None,
               binding_problem=None)
    if item not in catalog_items:
        # A preset's item from another mod is absent when that mod is not installed;
        # any other absent item means the tables do not fit this game.
        reason = "optional-item-absent" if row.get("optional_mod") else "item-definition-absent"
        row["available"] = False
        row["availability_reason"] = reason
        row["binding_problem"] = reason

    raw = row["source"]
    if raw == "blank":
        row["adapter"] = "virtual"
        return
    if raw == "unshey.fl":
        row["adapter"] = "unshey"
        name = convert_resource("ogreunsh", "cre", campaign, game, names)
        row["source_resource"] = f"cre:{name}"
        if row["source_resource"] in unreadable:
            _unreadable_source(row)
            return
        row["binding_problem"] = row["binding_problem"] or "unshey-requires-legacy-adapter"
        return
    if "." not in raw:
        row["binding_problem"] = row["binding_problem"] or "unsupported-source-kind"
        return
    name, extension = raw.rsplit(".", 1)
    spell_grant = extension == "spl" and is_spin621_source(row) and item == "ring41"
    if extension not in _PHYSICAL_SOURCES | _SCRIPTED_SOURCES and not spell_grant:
        row["binding_problem"] = row["binding_problem"] or "unsupported-source-kind"
        return
    converted = name if spell_grant else convert_resource(name, extension, campaign, game, names)
    resource = f"{extension}:{converted}"
    row["source_resource"] = resource
    if extension in _SCRIPTED_SOURCES or spell_grant:
        row["adapter"] = "scripted"
        if spell_grant or _routed_source(row, resource, item):
            row["binding_problem"] = row["binding_problem"] or "scripted-source-requires-adapter"
        else:
            # Named before an absent item or file: no game could give its reward to CIR.
            row["binding_problem"] = NO_ROUTE
        if resource not in resources:
            row["available"] = False
            row["availability_reason"] = "source-resource-absent"
        return
    if resource not in resources:
        row["available"] = False
        row["availability_reason"] = "source-resource-absent"
        row["binding_problem"] = row["binding_problem"] or "source-resource-absent"
        return
    if resource in unreadable:
        _unreadable_source(row)
        return

    candidates = sources.get((resource, item), [])
    object_name, policy, expected = (selectors[campaign].get(row["ident"],
                                      ("*", "single", 1))
                                    if row["family"] == "base"
                                    else ("*", "single", 1))
    matches = candidates
    if object_name != "*" and extension == "are":
        normalized = _normal_name(object_name)
        matches = [source for source in candidates
                   if _normal_name(_source_owner_name(source, targets_by_id))
                   == normalized]
        if not matches:
            alias = _SOURCE_NAME_ALIASES.get((campaign, resource, item,
                                              row["ident"], normalized))
            if alias is not None:
                matches = [source for source in candidates
                           if _normal_name(_source_owner_name(source, targets_by_id))
                           == alias]
    if policy == "all_one_unit":
        row["source_ids"] = sorted(source["id"] for source in matches)
        if len(matches) != expected:
            row["binding_problem"] = row["binding_problem"] or "source-match-count-mismatch"
        return
    if not matches:
        if candidates:
            row["binding_problem"] = row["binding_problem"] or "source-selector-not-matched"
        else:
            row["available"] = False
            row["availability_reason"] = "original-source-has-no-matching-record"
            row["binding_problem"] = row["binding_problem"] or row["availability_reason"]
        return
    row["source_ids"] = sorted(source["id"] for source in matches)
    if len(matches) != 1:
        row["binding_problem"] = row["binding_problem"] or "ambiguous-source-records"


def _resolve_target(row: dict, resources: set[str], unreadable: set[str],
                    targets_by_resource: dict[str, list[dict]], game: str,
                    endpoints: dict[str, dict[str, tuple[str, str, str]]], sources: list[dict],
                    names: EetNames | None) -> None:
    campaign = row["campaign"]
    area = convert_resource(row["area"], "are", campaign, game, names)
    row.update(area_resource=f"are:{area}", target_ids=[], creature_resource=None,
               container_name=None, members=[], binding_problem=None)
    container = container_target_spec(row)
    if container is not None:
        # The container that holds the quest's treasure, in the quest's own
        # area: the table's area column is not where every handler puts it.
        quest = container["target_id"]
        row["area_resource"] = f"are:{treasure_area(quest, game)}"
        try:
            physical_id = treasure_container(quest, sources, game)
        except ProfileError:
            physical_id = None
        matches = [target for target in targets_by_resource.get(row["area_resource"], [])
                   if target["id"] == physical_id and target["kind"] == "container"]
        if len(matches) == 1:
            row["target_ids"] = [physical_id]
            row["container_name"] = matches[0]["selector"]["name"]
        row["binding_problem"] = ("area-resource-absent" if row["area_resource"] not in resources else
                                  "area-resource-unreadable" if row["area_resource"] in unreadable else
                                  "treasure-container-not-found" if len(matches) != 1 else
                                  "target-resource-absent" if any(resource not in resources
                                    for resource in container["resources"]) else None)
        return
    if is_spin621_target(row):
        row["area_resource"] = "spl:spin621"
        row["binding_problem"] = ("legacy-target-requires-adapter" if "spl:spin621" in resources
                                  else "target-resource-absent")
        return
    reward = mode2_alias_spec(row, game, names)
    if reward is not None:
        row["area_resource"] = reward["resource"]
        # A script that would destroy the reward needs no gate where it is absent.
        needed = [resource for resource in reward["resources"] if resource not in DESTROY_GATES]
        handover, treasuries = reward.get("handover"), []
        if handover:
            # The reward waits in the one container of its area whose script
            # it is (the Sahuagin treasury), wherever a mod moved or renamed it.
            area = "are:" + handover["area"]
            needed.append(area)
            treasuries = [target for target in targets_by_resource.get(area, []) if target["kind"] == "container"
                          and target["selector"].get("trap_script") == handover["script"]]
        row["binding_problem"] = ("target-resource-absent" if any(resource not in resources for resource in needed)
                                  else "area-resource-unreadable" if handover and area in unreadable
                                  else "treasure-container-not-found" if handover and len(treasuries) != 1
                                  else "legacy-target-requires-adapter")
        return
    if row["target"].endswith(".fl"):
        # One of Item Randomiser's own quest handlers (its lib/weidu_action.tpa)
        # that the routes above do not cover; it names no container or creature.
        row["binding_problem"] = NO_ROUTE
        return
    endpoint = endpoints[campaign].get(row["endpoint_id"]) if row["endpoint_id"] else None
    kind = endpoint[0] if endpoint else None
    identity = endpoint[2] if endpoint else row["target"].split(".", 1)[0]
    raw = row["target"]
    if kind is None:
        if raw.endswith(".cre"):
            kind = "creature"
        elif raw.endswith(".sto"):
            kind = "store"
        elif raw.endswith(".flg"):
            kind = "group"
        elif raw.endswith((".bcs", ".dlg")):
            kind = "legacy_adapter"
        else:
            kind = "container"
    if row["area_resource"] not in resources:
        row["binding_problem"] = "area-resource-absent"
    elif row["area_resource"] in unreadable:
        row["binding_problem"] = "area-resource-unreadable"
    area_targets = targets_by_resource.get(row["area_resource"], [])
    if kind == "container":
        row["container_name"] = identity
        matches = [target for target in area_targets if target["kind"] == "container"
                   and _normal_name(target["selector"].get("name", ""))
                   == _normal_name(identity)]
        coordinates = _TARGET_COORDINATE_ALIASES.get(
            (campaign, row["area_resource"], row["endpoint_id"]))
        if coordinates is not None:
            matches = [target for target in area_targets if target["kind"] == "container"
                       and (target["selector"].get("x"), target["selector"].get("y"))
                       == coordinates]
        if len(matches) > 1 and row["endpoint_id"]:
            # Original endpoint coordinates identify e.g. Firkraag's golem
            # stash among two containers both named "Container 11". This
            # selects an existing container; it never creates a fallback pile.
            for _, fields in _table_rows(ROOT / "lists/endpoints/fallbacks.2da", 10):
                if (fields[2] == row["endpoint_id"] and
                        convert_resource(fields[3], "are", campaign, game, names) == area):
                    # The fallback type describes a newly created ground
                    # pile, not the type of the original chest/shelf.
                    pointed = [target for target in matches if
                               (target["selector"]["x"], target["selector"]["y"]) ==
                               (int(fields[4]), int(fields[5]))]
                    if len(pointed) == 1:
                        matches = pointed
                    break
    elif kind == "creature":
        name = raw.rsplit(".", 1)[0]
        name = convert_resource(name, "cre", campaign, game, names)
        row["creature_resource"] = f"cre:{name}"
        matches = [target for target in area_targets if target["kind"] == "actor"
                   and target["selector"].get("template_resource") == row["creature_resource"]]
        if row["creature_resource"] not in resources:
            row["binding_problem"] = row["binding_problem"] or "creature-resource-absent"
        elif row["creature_resource"] in unreadable:
            row["binding_problem"] = row["binding_problem"] or "creature-resource-unreadable"
        elif matches and all(target["selector"].get("appearance_schedule") == 0 for target in matches):
            # Placed for no hour of the day, the creature never appears (how mods
            # such as SoD Remix take a creature out); a reward there would be lost.
            row["binding_problem"] = row["binding_problem"] or "creature-never-appears"
    elif kind == "store":
        name = convert_resource(raw.rsplit(".", 1)[0], "sto", campaign, game, names)
        identity = f"sto:{name}"
        matches = [target for target in targets_by_resource.get(identity, [])
                   if target["id"] == identity and target["kind"] == "store"]
        if identity in unreadable:
            row["binding_problem"] = row["binding_problem"] or "store-resource-unreadable"
    elif kind == "group":
        row["members"] = _group_members(campaign, raw, game, names)
        allowed = set(row["members"])
        matches = [target for target in area_targets if target["kind"] == "actor"
                   and target["selector"].get("template_resource") in allowed]
        row["binding_problem"] = row["binding_problem"] or "group-requires-runtime-selection"
    else:
        matches = []
        row["binding_problem"] = row["binding_problem"] or "legacy-target-requires-adapter"
    row["target_ids"] = sorted(target["id"] for target in matches)
    if not matches and row["binding_problem"] is None:
        row["binding_problem"] = ("creature-not-static-in-area" if kind == "creature"
                                  else "target-not-in-scan")
    elif len(matches) > 1 and row["binding_problem"] is None:
        row["binding_problem"] = "ambiguous-target-records"


def resolve_template(catalog: dict, world: dict, template_id: str, *,
                     game: str = "eet", items: str | None = None,
                     targets: str | None = None, options: dict | None = None,
                     names: EetNames | None = None) -> dict:
    """Augment every original table row with exact installed scan candidates.

    On EET, BG1 and SoD names follow <names> (game_names.eet_names), by default
    the lists of the scanned game; a world without a game folder uses CIR's copy.
    """
    draft = build_template(template_id, items=items, targets=targets, options=options)
    if names is None and game == "eet" and "game_root" in world:
        names = eet_names(world["game_root"])
    catalog_items = {row["resref"] for row in catalog["items"]}
    resources = {row["id"] for row in world["resources"]}
    # Files the scan left out are in the game, but their records are not known.
    unreadable = {row["id"] for row in world.get("unreadable", [])}
    sources_by_identity: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for source in world["sources"]:
        sources_by_identity[(source["resource"], source["item"])].append(source)
    targets_by_resource: dict[str, list[dict]] = defaultdict(list)
    targets_by_id = {}
    for target in world["targets"]:
        targets_by_resource[target["resource"]].append(target)
        targets_by_id[target["id"]] = target
    selectors = {campaign: _source_selectors(campaign)
                 for campaign in draft["campaigns"]}
    endpoints = {campaign: _endpoint_rows(campaign)
                 for campaign in draft["campaigns"]}
    for row in draft["items"]:
        _resolve_source(row, catalog_items, resources, unreadable, sources_by_identity,
                        targets_by_id, game, selectors, names)
    for row in draft["targets"]:
        _resolve_target(row, resources, unreadable, targets_by_resource, game, endpoints,
                        world["sources"], names)
    return draft
