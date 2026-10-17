"""One applied revision for each campaign start of the game (platform.STARTS).

The start that begins with the whole game uses the configuration as it is. A
later start (Siege of Dragonspear, Shadows of Amn on EET, Throne of Bhaal)
gets a revision of its own with only the rewards and places it reaches:

- An area tells its part of the game by its name (platform.area_part).
- A creature template is where the areas that place it are.
- Any other record (a shop, a script, a conversation, an added item) is where
  the Item Randomiser table that lists it puts it: BG1, or all of BG2. A
  Throne of Bhaal start therefore leaves BG2 shops and scripts as they are.
- A record whose place is unknown is left out of later starts.

A reward left out stays as the game has it; nothing is put where the start
cannot go. What is left is checked as the whole configuration is: a reward
whose tier has no place left, or that the runtime check names, is left out
too. A start with nothing left gets no revision, so New Game tells the player
that this start is not randomised.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from pathlib import Path

from .campaigns import OVERFLOW_TARGET_KINDS, compile_campaign, write_campaign
from .original_registrations import table_rows, target_record as original_target_record
from .platform import (FULL_START, MAIN_START, START_NAMES, STARTS, area_part, campaign_parts,
                       game_platform)
from .profiles import ProfileError, edit_profile, validate_catalog
from .scripted_sources import ADAPTER_ID as SCRIPTED_ADAPTER, CAMPAIGN as SCRIPTED_CAMPAIGN
from .sources import validate_world

# Rounds of leaving out what the check names before giving up on a start.
_FIT_ROUNDS = 8


def _table_campaign(selector: object) -> str | None:
    """The Item Randomiser table ("bg1" or "bg2") a row identity such as bg2:base:item:7 names."""
    prefix = selector.split(":", 1)[0] if isinstance(selector, str) else ""
    return prefix if prefix in ("bg1", "bg2") else None


class _Places:
    """Where the sources and targets of one configuration are, in one game."""

    def __init__(self, catalog: dict, world: dict, index: dict, plan: dict, platform: str):
        self.platform = platform
        self.sources, self.targets = index["sources"], index["targets"]
        placed = defaultdict(set)
        for target in world["targets"]:
            template = target["selector"].get("template_resource") if target["kind"] == "actor" else None
            if template:
                placed[template].add(area_part(platform, target["resource"][4:]))
        self.placed = {creature: frozenset(parts) for creature, parts in placed.items()}
        # The table each physical source an Item Randomiser row selects comes from.
        self.campaigns = {}
        if "original_template_id" in plan:
            for row in table_rows(catalog, world, plan)[0].values():
                for identity in row.get("source_ids") or ():
                    self.campaigns[identity] = row["campaign"]

    def parts(self, resource: str, campaign: str | None) -> frozenset | None:
        kind, _, name = resource.partition(":")
        if kind == "are":
            part = area_part(self.platform, name)
            if part is not None:
                return frozenset({part})
        elif kind == "cre":
            parts = self.placed.get(resource)
            if parts and None not in parts:
                return parts
        return campaign_parts(campaign) if campaign in ("bg1", "bg2") else None

    def reached(self, resource: str, campaign: str | None, reach: frozenset) -> bool:
        parts = self.parts(resource, campaign)
        return parts is not None and parts <= reach


def _without(profile: dict, plan: dict, catalog: dict, sources: set, targets: set) -> tuple[dict, dict]:
    """The configuration without these source and target identities."""
    plan = deepcopy(plan)
    choices, items = [], []
    for choice in plan["sources"]:
        choice["source_ids"] = [identity for identity in choice["source_ids"] if identity not in sources]
        choice["registration_ids"] = [identity for identity in choice["registration_ids"]
                                      if identity not in sources]
        if choice["source_ids"] or choice["registration_ids"]:
            choices.append(choice)
        else:
            items.append(choice["item"])
    plan["sources"] = choices
    plan["registrations"] = [row for row in plan["registrations"] if row["id"] not in sources]
    if "replacements" in plan:
        plan["replacements"] = {key: value for key, value in plan["replacements"].items()
                                if key not in sources}
    plan["targets"] = [row for row in plan["targets"] if row["id"] not in targets]
    if "target_registrations" in plan:
        plan["target_registrations"] = [row for row in plan["target_registrations"]
                                        if row["id"] not in targets]
    randomization = plan.get("randomization")
    if randomization:
        filters = randomization.get("row_filters", {"sources": {}, "targets": {}})
        for identity in sources:
            randomization["sources"].pop(identity, None)
            filters["sources"].pop(identity, None)
        for identity in targets:
            randomization["targets"].pop(identity, None)
            filters["targets"].pop(identity, None)
        for rule in randomization["sources"].values():
            rule["exclude_targets"] = [entry for entry in rule["exclude_targets"]
                                       if entry["id"] not in targets]
    shield = plan.get("spell_shield")
    if shield and shield["source_id"] in sources:
        # Without its randomised source the start keeps the shop as installed.
        plan["original_options"] = {**plan["original_options"], "more_spell_shield": False}
        del plan["spell_shield"]
    if items:
        profile = edit_profile(profile, catalog, exclude=items)
    return profile, plan


def _units(plan: dict, places: _Places) -> dict[str, int]:
    """Reward units of each source identity."""
    registrations = {row["id"]: row for row in plan["registrations"]}
    units = {}
    for choice in plan["sources"]:
        for identity in choice["source_ids"]:
            source = places.sources[identity]
            units[identity] = source["record"]["quantity"] if source["kind"] == "store" else 1
        for identity in choice["registration_ids"]:
            units[identity] = registrations[identity]["copies"]
    return units


def _without_place(plan: dict, places: _Places) -> list[str]:
    """Sources the remaining targets cannot take.

    With overflow (Item Randomiser's tables) each tier with rewards needs a
    place that can take any reward and, when originals may be kept, one that no
    reward's home claims; without overflow the targets must hold every reward.
    """
    randomization = plan.get("randomization")
    units = _units(plan, places)
    if randomization is None or not randomization.get("overflow"):
        capacity = sum(row["capacity"] for row in plan["targets"])
        dropped, total = [], sum(units.values())
        for identity in sorted(units, reverse=True):
            if total <= capacity:
                break
            dropped.append(identity)
            total -= units[identity]
        return dropped
    registered = {row["id"]: original_target_record(row)["kind"]
                  for row in plan.get("target_registrations", [])}
    kinds = {row["id"]: registered.get(row["id"]) or places.targets.get(row["id"], {}).get("kind")
             for row in plan["targets"]}
    slots = defaultdict(set)
    for identity, tiers in randomization["targets"].items():
        if kinds.get(identity) in OVERFLOW_TARGET_KINDS:
            for ordinal, tier in enumerate(tiers, 1):
                slots[tier].add((identity, ordinal))
    keeping = randomization["unrandomized_percent"] > 0
    rules, dropped = dict(randomization["sources"]), set()
    while True:
        claimed = {(entry["id"], entry["ordinal"])
                   for rule in rules.values() for entry in rule["exclude_targets"]}
        failing = {identity for identity, rule in rules.items()
                   if not (slots[rule["tier"]] - claimed if keeping else slots[rule["tier"]])}
        if not failing:
            return sorted(dropped)
        dropped |= failing
        for identity in failing:
            del rules[identity]


def restrict(profile: dict, plan: dict, catalog: dict, world: dict, platform: str,
             reach: frozenset) -> tuple[dict, dict] | None:
    """The configuration for a start that reaches these parts, or None if nothing is left."""
    from .native_export import _compile

    items, index = validate_catalog(catalog), validate_world(world, catalog)
    places = _Places(catalog, world, index, plan, platform)
    registrations = {row["id"]: row for row in plan["registrations"]}
    target_registrations = {row["id"]: row for row in plan.get("target_registrations", [])}
    sources, targets = set(), set()
    for choice in plan["sources"]:
        if choice.get("policy") != "relocate":
            raise ProfileError("Only relocated rewards can be applied")
        for identity in choice["source_ids"]:
            if not places.reached(places.sources[identity]["resource"],
                                  places.campaigns.get(identity), reach):
                sources.add(identity)
        for identity in choice["registration_ids"]:
            row = registrations[identity]
            campaign = (SCRIPTED_CAMPAIGN if row["adapter_id"] == SCRIPTED_ADAPTER else
                        row.get("authored", {}).get("campaign") or _table_campaign(row["selector"]))
            if not places.reached(row["resource"], campaign, reach):
                sources.add(identity)
    for choice in plan["targets"]:
        identity = choice["id"]
        if identity in target_registrations:
            row = target_registrations[identity]
            resource, campaign = row["resource"], row["authored"]["campaign"]
        elif identity in places.targets:
            resource, campaign = places.targets[identity]["resource"], None
        else:
            targets.add(identity)
            continue
        if not places.reached(resource, campaign, reach):
            targets.add(identity)
    profile, plan = _without(profile, plan, catalog, sources, targets)
    for _ in range(_FIT_ROUNDS):
        if not plan["sources"] or not plan["targets"]:
            return None
        placeless = _without_place(plan, places)
        if placeless:
            profile, plan = _without(profile, plan, catalog, set(placeless), set())
            continue
        problems: dict[str, str] = {}
        _compile(compile_campaign(profile, catalog, world, plan), world, index, items, problems, catalog=catalog)
        if not problems:
            return profile, plan
        source_ids = set(_units(plan, places))
        target_ids = {row["id"] for row in plan["targets"]}
        unknown = set(problems) - source_ids - target_ids
        if unknown:
            raise ProfileError(problems[min(unknown)])
        profile, plan = _without(profile, plan, catalog, set(problems) & source_ids,
                                 set(problems) & target_ids)
    raise ProfileError("The runtime check kept finding rewards or places this start cannot use")


def write_starts(profile: dict, plan: dict, catalog: dict, world: dict, store: Path) -> dict:
    """Write the revision of each campaign start of this game to the store.

    Returns the game, its starts and their revisions, the start the engine
    makes without a campaign row ("main", None on EET) and, for each start that
    gets no revision, the reason.
    """
    platform = game_platform(world)
    full = FULL_START[platform]
    revisions, skipped = {}, {}
    for start, reach in STARTS[platform].items():
        if start == full:
            continue
        try:
            restricted = restrict(profile, plan, catalog, world, platform, reach)
        except ProfileError as exc:
            skipped[start] = str(exc)
            continue
        if restricted is None:
            skipped[start] = "nothing in this configuration is reached from this start"
            continue
        start_profile, start_plan = restricted
        revisions[start] = write_campaign(start_profile, catalog, world, start_plan, store)["revision"]
    # Written last, so the store's own pointer names the whole configuration.
    revisions[full] = write_campaign(profile, catalog, world, plan, store)["revision"]
    return {"platform": platform, "full": full, "main": MAIN_START[platform],
            "starts": dict(sorted(revisions.items())), "skipped": skipped}


def describe_starts(written: dict) -> str:
    """Which new games the applied settings randomise, for the player."""
    randomised = [START_NAMES[start] for start in STARTS[written["platform"]] if start in written["starts"]]
    text = "New games randomised: " + ", ".join(randomised) + "."
    if written["skipped"]:
        text += " Not randomised: " + "; ".join(
            f"{START_NAMES[start]} ({reason})" for start, reason in written["skipped"].items()) + "."
    return text
