"""Saved configurations carried over to new scans of the same game.

A configuration names the scans it was made with, and the editor opens only
the configurations of its own scans. After the game's mods change (a new
version of Chriz Item Randomiser among them) the start without arguments
scans the game again; rebase_drafts then carries each configuration of the
earlier scans over to the new ones.

A configuration saved unchanged from a preset (its plan's record of what the
preset's fit made still matches it) becomes that preset as it fits the new
scans, with the same pools and options: what the preset gained, in a new
version or with the new mods, is added.

Any other configuration keeps what the player chose: the items, which of
their records move, the places and their reward slots, a preset's pools and
options, tiers and shop prices. What the scans decide comes from the new
ones, as the editor would offer it now: a preset's rewards and places from
the preset fitted again with the same pools and options (a row of its lists
stays the same choice when the new version moves it in another way), a
special place from those this game can deliver, the pool's reward from the
prepared pool script, a record or recipient chosen in the editor from the new
world scan. A creature or container of an area, and a record in one, is
found as the game finds it: by the name the scan the configuration was made
with (``earlier_world``) shows, wherever it stands now; where it stood only
tells several of one name apart. Nothing is added; when its preset has
rewards or places it does not have, a note says so.

What no longer fits is left out and named. A configuration that cannot be
read, whose preset no longer fits the game, or of which no reward remains, is
not carried over.
"""
from __future__ import annotations

from copy import deepcopy
import json

from . import original_configuration
from .editor import checked_draft
from .game_names import eet_names
from .original_catalog import CARRIER_CAN_GO_MISSING, NO_ROUTE
from .original_options import spell_shield_randomized, spell_shield_spec
from .original_registrations import SOURCE_ADAPTER, target_record
from .platform import preset_game
from .prepared import applied
from .profiles import ProfileError, validate_profile
from .scripted_sources import (ADAPTER_ID as POOL_ADAPTER, ITEM as POOL_ITEM, RESOURCE as POOL_RESOURCE,
                               SOURCE_ID as POOL_SOURCE, blpool_registration)
from .sources import (DANGLING_INVENTORY, INVALID_INVENTORY, PLAN_FIELDS, PLAN_OPTIONAL_FIELDS, PLAN_SCHEMA,
                      UNRESOLVED_STORAGE, UNUSABLE_SOURCE, Scans)
from .virtual_sources import ADAPTER_ID as VIRTUAL_ADAPTER

# Why a choice is left out, beside the reasons a preset's fit gives.
ITEM_ABSENT = "item-absent"
NOT_AVAILABLE = "not-available"
SOURCE_ABSENT = "source-record-absent"
SOURCE_AMBIGUOUS = "source-record-ambiguous"
SOURCE_UNUSABLE = "source-record-unusable"
TARGET_ABSENT = "target-absent"
TARGET_AMBIGUOUS = "target-ambiguous"
TARGET_UNVERIFIED = "target-unverified"
TARGET_UNUSABLE = "target-unusable"
NO_PLACE = original_configuration.NO_PLACE_IN_TIER
# Why a configuration is not carried over.
UNREADABLE = "it cannot be read"
NOTHING_LEFT = "none of its rewards can be used with the game as it is now"
PRESET_UNFIT = "its preset cannot be used with the game as it is now: "
# What a configuration made from a preset is told when the preset has more now.
PRESET_FOLLOWED = "It was saved unchanged from its preset, so the preset's new rewards and places are added to it"
PRESET_HAS_MORE = ("Its preset has rewards or places that it does not have; choosing the preset again in the editor "
                   "gives them")
_PRESET_FIELDS = ("original_template_id", "original_choices", "original_options")
# A place's reward slots, as the binding check allows them.
_MAX_CAPACITY = 0xFFFFFFFF
# Where an area's creature or container stands, as the area file keeps it.
_MAX_COORDINATE = 0xFFFF


def rebase_drafts(catalog: dict, world: dict, drafts: list, *, earlier_world: dict | None = None,
                  scans: Scans | None = None) -> list[dict]:
    """Each saved configuration carried over to these scans, in order.

    A configuration these scans open already is returned as the editor checks
    it. Otherwise the result is ``{"draft", "left_out", "notes"}``: the
    configuration for these scans, the choices left out (``{"id", "kind",
    "reason"}``, kind ``item``, ``reward`` or ``place``) and what they are in
    plain words, with what its preset has more now; or ``{"draft": None,
    "reason"}`` when it is not carried over.
    ``earlier_world`` is the world scan the configurations were made with, if
    the caller has it. The fits of one preset are shared by the configurations
    made from it.
    """
    scans = Scans(catalog, world) if scans is None else scans
    carrier = _Carrier(catalog, world, scans, earlier_world)
    return [carrier.rebase(draft) for draft in drafts]


class _Preset:
    """A preset fitted to the new scans: what moves each of its rows now."""

    def __init__(self, resolved: dict, fitted: dict):
        self.profile, self.plan = fitted["profile"], fitted["plan"]
        self.registrations = {row["id"]: row for row in self.plan["registrations"]}
        self.places = {row["id"]: row for row in self.plan["target_registrations"]}
        # The item of each identity the fit moves, and the identity of each row it uses.
        self.items = {identity: choice["item"] for choice in self.plan["sources"]
                      for identity in choice["source_ids"] + choice["registration_ids"]}
        self.uses = {row: identity for identity, row in fitted["row_ids"].items()}
        self.reasons = {row["id"]: row["reason"] for row in fitted["not_used"]}
        # The rows of the preset's lists the fit considers (those its options
        # enable) by the records they move, the pool's row, and the places
        # its options enable.
        self.records: dict[str, str] = {}
        self.pool = None
        for row in resolved["items"]:
            if not row.get("enabled"):
                continue
            for identity in row.get("source_ids") or ():
                self.records.setdefault(identity, row["id"])
            if (self.pool is None and row.get("adapter") == "scripted" and
                    row.get("source_resource") == POOL_RESOURCE and row.get("item_resref") == POOL_ITEM):
                self.pool = row["id"]
        self.place_rows = {row["id"] for row in resolved["targets"] if row.get("enabled")}

    def row(self, identity: str, registered: bool, saved: dict | None) -> str | None:
        """The row of the preset's lists a saved reward choice stands for, if any."""
        if not registered:
            return self.records.get(identity)
        if saved is None:
            return None
        if saved.get("adapter_id") in (SOURCE_ADAPTER, VIRTUAL_ADAPTER) and isinstance(saved.get("selector"), str):
            return saved["selector"]
        return self.pool if saved.get("adapter_id") == POOL_ADAPTER else None


class _Scan:
    """A world scan as the carry-over looks things up in it: recipients and
    records by identity, each holder's records in the scan's order, and an
    area's creatures and containers by what the game finds them by."""

    def __init__(self, world):
        self.targets: dict[str, dict] = {}
        self.sources: dict[str, dict] = {}
        self.records: dict[str, list[dict]] = {}
        self.alike: dict[tuple, list[dict]] = {}
        for row in _listed(world, "targets"):
            if isinstance(row.get("id"), str):
                self.targets.setdefault(row["id"], row)
        for row in _listed(world, "sources"):
            if all(isinstance(row.get(key), str) for key in ("id", "owner", "item")) and row["id"] not in self.sources:
                self.sources[row["id"]] = row
                self.records.setdefault(row["owner"], []).append(row)
        for row in self.targets.values():
            handle = _handle(row, self.targets)
            if handle is not None:
                self.alike.setdefault(handle, []).append(row)


class _Carrier:
    def __init__(self, catalog: dict, world: dict, scans: Scans, earlier_world):
        self.catalog, self.world, self.scans = catalog, world, scans
        self.index = scans.indexes(catalog, world)[1]
        self.earlier_world = earlier_world
        self.presets: dict[str, _Preset | ValueError] = {}
        self.special: dict[str, dict] | None = None
        self.earlier: _Scan | None = None
        self.now: _Scan | None = None

    def rebase(self, draft) -> dict:
        try:
            return {"draft": checked_draft(self.catalog, self.world, draft), "left_out": [], "notes": []}
        except (KeyError, TypeError, AttributeError, ValueError):
            pass
        if not _readable(draft):
            return {"draft": None, "reason": UNREADABLE}
        try:
            return self._rebase(draft["profile"], draft["plan"])
        except ProfileError as exc:
            return {"draft": None, "reason": str(exc)}

    def preset(self, plan: dict) -> _Preset | ValueError:
        """The preset of a plan fitted to the new scans, or why it does not fit
        (also a preset or option this version no longer has)."""
        template_id, choices, options = (plan[key] for key in _PRESET_FIELDS)
        key = json.dumps([template_id, choices, options], sort_keys=True)
        if key not in self.presets:
            try:
                game = preset_game(self.world, template_id)
                names = eet_names(self.world["game_root"]) if game == "eet" else None
                resolved = original_configuration.resolve_template(
                    self.catalog, self.world, template_id, game=game, items=choices.get("items"),
                    targets=choices.get("targets"), options=options, names=names)
                fitted = original_configuration._fit(self.catalog, self.world, template_id, self.scans,
                                                     lambda game, names: resolved)
                self.presets[key] = _Preset(resolved, fitted)
            except ValueError as exc:
                self.presets[key] = exc
        return self.presets[key]

    def special_places(self) -> dict[str, dict]:
        """The special places this game can deliver, by identity; found once."""
        if self.special is None:
            try:
                rows = original_configuration.fit_special_places(self.catalog, self.world, scans=self.scans)
            except ProfileError:
                rows = []
            self.special = {row["id"]: row for row in rows}
        return self.special

    def earlier_scan(self, fingerprint: str) -> _Scan | None:
        """The scan a configuration was made with, if that is the earlier world."""
        world = self.earlier_world
        if not isinstance(world, dict) or world.get("fingerprint") != fingerprint:
            return None
        if self.earlier is None:
            self.earlier = _Scan(world)
        return self.earlier

    def scan(self) -> _Scan:
        """The new world scan, as the carry-over looks things up in it."""
        if self.now is None:
            self.now = _Scan(self.world)
        return self.now

    def same(self, identity: str, earlier: _Scan) -> tuple[str | None, str | None]:
        """Where an area's creature or container of the earlier scan is in the
        new one: (its identity now, None), or (None, why it is left out). The
        one the game finds by the same name is it, wherever it stands now. Of
        several of that name, it is the one nearest to where it stood, unless
        that one stands nearer to another of the earlier scan; two as near
        cannot be told apart."""
        row = earlier.targets[identity]
        handle = _handle(row, earlier.targets)
        now, before = self.scan().alike.get(handle, []), earlier.alike[handle]
        if not now:
            return None, TARGET_ABSENT
        if len(now) == 1 and len(before) == 1:
            return now[0]["id"], None
        nearest = _nearest(row, now)
        back = _nearest(nearest, before) if nearest is not None else None
        if back is None:
            return None, TARGET_AMBIGUOUS
        return (nearest["id"], None) if back["id"] == identity else (None, TARGET_ABSENT)

    def record(self, identity: str, item: str, fingerprint: str) -> tuple[str | None, str | None]:
        """Where a saved record is in the new scan: (its identity now, None) or
        (None, why it is left out). Its holder is found as a recipient is (an
        area's creature or container by name), the record among the holder's
        records of that item in the scan's order. Without the scan it was made
        with, the record keeps its identity when that still holds the item."""
        earlier = self.earlier_scan(fingerprint)
        before = earlier.sources.get(identity) if earlier is not None else None
        if before is None or before["item"] != item:
            row = self.index["sources"].get(identity)
            return (identity, None) if row is not None and row["item"] == item else (None, SOURCE_ABSENT)
        owner = before["owner"]
        holder = earlier.targets.get(owner)
        if holder is not None and _handle(holder, earlier.targets) is not None:
            owner, reason = self.same(owner, earlier)
            if owner is None:
                return None, SOURCE_AMBIGUOUS if reason == TARGET_AMBIGUOUS else SOURCE_ABSENT
        alike = [row["id"] for row in earlier.records[before["owner"]] if row["item"] == item]
        now = [row["id"] for row in self.scan().records.get(owner, ()) if row["item"] == item]
        place = alike.index(identity)
        return (now[place], None) if place < len(now) else (None, SOURCE_ABSENT)

    def source(self, identity: str, item: str, registered: bool, saved: dict | None, preset: _Preset | None,
               fingerprint: str) -> tuple[str | None, dict | None, str | None]:
        """A saved reward choice for these scans: (identity, registration or
        None for a record, None), or (None, None, why it is left out)."""
        current, reason = (identity, None) if registered else self.record(identity, item, fingerprint)
        if preset is not None and current is not None:
            row = preset.row(current, registered, saved)
            if row is not None:
                used = preset.uses.get(row)
                if used is not None and preset.items.get(used) == item:
                    return used, deepcopy(preset.registrations.get(used)), None
                return None, None, preset.reasons.get(row, NOT_AVAILABLE)
        if current is None:
            return None, None, reason
        if not registered:
            if UNUSABLE_SOURCE.intersection(self.index["sources"][current]["concerns"]):
                return None, None, SOURCE_UNUSABLE
            return current, None, None
        # The pool's reward, as the editor offers it for the pool's mace.
        if (saved is not None and saved.get("adapter_id") == POOL_ADAPTER and identity == POOL_SOURCE and
                item == POOL_ITEM and POOL_RESOURCE in self.index["resources"] and
                applied(self.world["game_root"], "quest_pool")):
            return identity, blpool_registration(self.world), None
        return None, None, NOT_AVAILABLE

    def place(self, identity: str, saved: dict | None, fingerprint: str,
              preset: _Preset | None) -> tuple[str | None, dict | None, str | None]:
        """A saved recipient for these scans: (its identity now, its
        registration or None for a recipient of the world scan, None), or
        (None, None, why it is left out)."""
        if saved is not None:
            row = saved.get("selector") if isinstance(saved.get("selector"), str) else None
            if preset is not None:
                if identity in preset.places:
                    return identity, deepcopy(preset.places[identity]), None
                if row in preset.place_rows:
                    return None, None, preset.reasons.get(row, NOT_AVAILABLE)
            special = self.special_places()
            if identity in special:
                return identity, deepcopy(special[identity]), None
            return None, None, NOT_AVAILABLE
        earlier = self.earlier_scan(fingerprint)
        before = earlier.targets.get(identity) if earlier is not None else None
        if before is not None and _handle(before, earlier.targets) is not None:
            # An area's creature or container: the one of the same name now.
            identity, reason = self.same(identity, earlier)
            if identity is None:
                return None, None, reason
            row = self.index["targets"][identity]
        else:
            row = self.index["targets"].get(identity)
            if row is None:
                return None, None, TARGET_ABSENT
            if row["kind"] in ("actor", "container"):
                # Without the scan it was made with, it cannot be told which one was chosen.
                return None, None, TARGET_UNVERIFIED
        if UNRESOLVED_STORAGE in row["concerns"] or {DANGLING_INVENTORY, INVALID_INVENTORY}.intersection(row["concerns"]):
            return None, None, TARGET_UNUSABLE
        if row["kind"] == "actor" and not row["selector"]["embedded_cre_size"]:
            template = row["selector"]["template_resource"]
            if template not in self.index["resources"] or template in self.index["unreadable"]:
                return None, None, TARGET_UNUSABLE
        return identity, None, None

    def _rebase(self, profile: dict, plan: dict) -> dict:
        left_out = []
        items = set()
        for item in profile["items"]:
            if item in self.scans.items:
                items.add(item)
            else:
                left_out.append({"id": item, "kind": "item", "reason": ITEM_ABSENT})
        if not items and profile["items"]:
            return {"draft": None, "reason": NOTHING_LEFT}
        preset = None
        if "original_template_id" in plan:
            preset = self.preset(plan)
            if isinstance(preset, ValueError):
                return {"draft": None, "reason": PRESET_UNFIT + str(preset)}
        draft = self._carry(profile, plan, items, preset, left_out)
        notes = []
        if preset is not None:
            more = _more(preset.plan, draft)
            if original_configuration.unchanged(profile, plan):
                # The preset as it fits now. A reward of it left out above
                # (its places gone) is in it when the preset has another place.
                draft = checked_draft(self.catalog, self.world, {
                    "profile": {**deepcopy(preset.profile), "id": profile.get("id"), "name": profile.get("name")},
                    "plan": deepcopy(preset.plan)})
                rewards = _held(preset.plan)[0]
                left_out = [row for row in left_out if row["kind"] != "reward" or row["id"] not in rewards]
                if more:
                    notes.append(PRESET_FOLLOWED)
            elif more:
                notes.append(PRESET_HAS_MORE)
        if draft is None:
            return {"draft": None, "reason": NOTHING_LEFT}
        return {"draft": draft, "left_out": left_out, "notes": _notes(left_out) + notes}

    def _carry(self, profile: dict, plan: dict, items: set[str], preset: _Preset | None,
               left_out: list[dict]) -> dict | None:
        """The player's choices for these scans, those left out added to
        ``left_out``; None when none of its rewards remains."""
        fingerprint = plan["world_fingerprint"]
        saved_registrations: dict[str, dict] = {}
        for row in plan["registrations"]:
            saved_registrations.setdefault(row["id"], row)
        # Each kept choice with what it moves now: (choice, records, registrations),
        # and the identity each saved reward has now.
        choices, evidence, seen, moves = [], {}, set(), {}
        for choice in plan["sources"]:
            item = choice["item"]
            if item not in items:
                continue
            if choice["policy"] != "relocate":
                choices.append((choice, None, None))
                continue
            records, registrations = [], []
            for registered, identities in ((False, choice["source_ids"]), (True, choice["registration_ids"])):
                for identity in identities:
                    kept, registration, reason = self.source(
                        identity, item, registered, saved_registrations.get(identity) if registered else None, preset,
                        fingerprint)
                    if kept is None:
                        left_out.append({"id": identity, "kind": "reward", "reason": reason})
                    elif kept not in seen:
                        seen.add(kept)
                        (records if registration is None else registrations).append(kept)
                        evidence[kept] = registration
                        moves[identity] = kept
            choices.append((choice, records, registrations))
        saved_places = {row["id"]: row for row in plan.get("target_registrations", [])}
        # The places kept, by their identity now, and the identity each saved place has now.
        targets, places, capacity, renamed, done = [], [], {}, {}, set()
        for choice in plan["targets"]:
            identity = choice["id"]
            if identity in done:
                continue
            done.add(identity)
            now, registration, reason = self.place(identity, saved_places.get(identity), fingerprint, preset)
            if reason is not None:
                left_out.append({"id": identity, "kind": "place", "reason": reason})
                continue
            if registration is not None:
                registration["capacity"] = choice["capacity"]
                places.append(registration)
            renamed[identity] = now
            targets.append({**deepcopy(choice), "id": now})
            capacity[now] = choice["capacity"]
        moved = [identity for _, records, registrations in choices if records is not None
                 for identity in records + registrations]
        rules = None
        if "randomization" in plan or preset is not None:
            rules = _rules(_renamed(plan.get("randomization", {}), moves, renamed),
                           preset.plan["randomization"] if preset else None, moved, capacity)
            if preset is not None:
                # A tier whose places are all left out keeps no reward, as the preset's fit has it.
                kinds = {row["id"]: target_record(row)["kind"] for row in places}
                for choice in targets:
                    if choice["id"] not in kinds:
                        kinds[choice["id"]] = self.index["targets"][choice["id"]]["kind"]
                placeless = set(original_configuration.placeless_sources(rules, kinds))
                for identity in moved:
                    if identity in placeless:
                        left_out.append({"id": identity, "kind": "reward", "reason": NO_PLACE})
                moved = [identity for identity in moved if identity not in placeless]
                for table in (rules["sources"], rules.get("row_filters", {}).get("sources", {})):
                    for identity in placeless:
                        table.pop(identity, None)
        kept = set(moved)
        sources, registrations = [], []
        for choice, records, registered in choices:
            if records is None:
                sources.append(deepcopy(choice))
                continue
            records = [identity for identity in records if identity in kept]
            registered = [identity for identity in registered if identity in kept]
            if (choice["source_ids"] or choice["registration_ids"]) and not (records or registered):
                # Nothing of this item moves any more; its rewards are named above.
                items.discard(choice["item"])
                continue
            sources.append({**deepcopy(choice), "source_ids": records, "registration_ids": registered})
            registrations += [evidence[identity] for identity in registered]
        if not items and profile["items"]:
            return None
        rebased = deepcopy(profile)
        rebased["items"] = [item for item in profile["items"] if item in items]
        rebased["catalog_fingerprint"] = self.catalog["fingerprint"]
        pricing = rebased.get("shop_pricing")
        if isinstance(pricing, dict) and isinstance(pricing.get("base_prices"), dict):
            pricing["base_prices"] = {item: price for item, price in pricing["base_prices"].items() if item in items}
        rebased = validate_profile(rebased, self.catalog)
        result = deepcopy(plan)
        result["catalog_fingerprint"] = self.catalog["fingerprint"]
        result["world_fingerprint"] = self.world["fingerprint"]
        result["sources"], result["registrations"], result["targets"] = sources, registrations, targets
        # It is no longer what a preset's fit made.
        result.pop(original_configuration.FIT_RECORD, None)
        if places or preset is not None:
            result["target_registrations"] = places
        else:
            # A configuration that is no preset has original recipients only for its special places.
            result.pop("target_registrations", None)
        if rules is not None:
            result["randomization"] = rules
        fresh = preset.plan if preset is not None else plan
        records = {identity for choice in sources for identity in choice.get("source_ids", ())}
        # A preset's creature replacements as it gives them now; the player's own for their records.
        offered = (fresh.get("replacements", {}) if preset is not None else
                   {moves[identity]: value for identity, value in plan.get("replacements", {}).items()
                    if identity in moves})
        replacements = {identity: replacement for identity, replacement in offered.items() if identity in records}
        duplicates = [deepcopy(copy) for copy in fresh.get("duplicates", []) if copy["source_id"] in kept]
        for field, value in (("replacements", replacements), ("duplicates", duplicates)):
            if value:
                result[field] = value
            else:
                result.pop(field, None)
        if preset is not None:
            if "spell_shield" in fresh:
                # The scroll's shop row is randomised exactly when it is one of the rewards kept.
                result["spell_shield"] = spell_shield_spec(self.world, randomized=spell_shield_randomized(
                    row.get("authored") for row in registrations if row.get("adapter_id") == SOURCE_ADAPTER))
            else:
                result.pop("spell_shield", None)
            if "cursed_disguises" in fresh:
                result["cursed_disguises"] = deepcopy(fresh["cursed_disguises"])
            else:
                result.pop("cursed_disguises", None)
        return checked_draft(self.catalog, self.world, {"profile": rebased, "plan": result})


def _held(plan: dict) -> tuple[set[str], set[str]]:
    """The rewards and the places of a configuration, by identity."""
    rewards = {identity for choice in plan["sources"]
               for identity in choice.get("source_ids", []) + choice.get("registration_ids", [])}
    return rewards, {choice["id"] for choice in plan["targets"]}


def _more(fresh: dict, draft: dict | None) -> bool:
    """Whether a preset as it fits now (``fresh``) has rewards or places that
    a configuration carried over from it does not have (none when None)."""
    rewards, places = _held(fresh)
    if draft is None:
        return bool(rewards or places)
    own_rewards, own_places = _held(draft["plan"])
    return bool(rewards - own_rewards or places - own_places)


def _rules(saved: dict, fresh: dict | None, moved: list[str], capacity: dict[str, int]) -> dict:
    """The randomisation rules of the rewards and places kept: a preset's rows
    as the preset gives them now (fresh), the player's own for the rest, and
    the tiers the player chose for a place's slots beyond the preset's. An
    exclusion names a slot of a place kept."""
    rules = deepcopy(fresh if fresh is not None else saved)
    fresh = fresh or {}
    tables = {}
    for name, identities in (("sources", moved), ("targets", capacity)):
        table = {}
        for identity in identities:
            if identity in fresh.get(name, {}):
                table[identity] = deepcopy(fresh[name][identity])
                own = saved.get(name, {}).get(identity)
                if name == "targets" and isinstance(own, list) and len(own) > len(table[identity]):
                    table[identity] += deepcopy(own[len(table[identity]):])
            elif identity in saved.get(name, {}):
                table[identity] = deepcopy(saved[name][identity])
        tables[name] = table
    for rule in tables["sources"].values():
        if "exclude_targets" in rule:
            rule["exclude_targets"] = [entry for entry in rule["exclude_targets"]
                                       if 1 <= entry["ordinal"] <= capacity.get(entry["id"], 0)]
    rules["sources"], rules["targets"] = tables["sources"], tables["targets"]
    filters = fresh.get("row_filters", saved.get("row_filters"))
    if filters is not None:
        kept = {}
        for name, identities in (("sources", moved), ("targets", capacity)):
            own = fresh.get("row_filters", {}).get(name, {})
            others = saved.get("row_filters", {}).get(name, {})
            kept[name] = {identity: deepcopy(own[identity] if identity in own else others[identity])
                          for identity in identities if identity in own or identity in others}
        rules["row_filters"] = kept
    return rules


def _renamed(rules: dict, sources: dict[str, str], targets: dict[str, str]) -> dict:
    """A configuration's own rules with its rewards and places as the new
    scans name them (``sources``, ``targets``: saved identity to the one
    now); those left out are no longer in them."""
    renamed = deepcopy(rules)
    for name, names in (("sources", sources), ("targets", targets)):
        for owner in (renamed, renamed.get("row_filters")):
            if isinstance(owner, dict) and name in owner:
                owner[name] = {names[identity]: value for identity, value in owner[name].items() if identity in names}
    for rule in renamed.get("sources", {}).values():
        rule["exclude_targets"] = [{**entry, "id": targets[entry["id"]]} for entry in rule["exclude_targets"]
                                   if entry["id"] in targets]
    return renamed


def _listed(world, key: str) -> list[dict]:
    """The objects of one of a scan's lists, as a scan read from a file has them."""
    rows = world.get(key) if isinstance(world, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _handle(row: dict, targets: dict[str, dict]) -> tuple | None:
    """What the game finds an area's creature or container by, letter case
    aside: a container's name, a creature's script name (its own CRE's or its
    template's), or for a creature without one (blank or None) its name in
    the area with its CRE. None for another kind of recipient."""
    selector, resource = row.get("selector"), row.get("resource")
    if row.get("kind") not in ("actor", "container") or not isinstance(selector, dict) or not isinstance(resource, str):
        return None
    name = selector.get("name")
    if not isinstance(name, str):
        return None
    if row["kind"] == "container":
        return resource, "container", name.lower(), ""
    template = selector.get("template_resource")
    template = targets.get(template) if isinstance(template, str) else None
    if template is not None and isinstance(template.get("selector"), dict):
        script = template["selector"].get("script_name")
    else:
        script = selector.get("script_name")
    if isinstance(script, str) and script and script.lower() != "none":
        return resource, "creature", script.lower(), ""
    creature = selector.get("cre_resref")
    return resource, "actor", name.lower(), creature.lower() if isinstance(creature, str) else ""


def _position(row: dict) -> tuple[int, int] | None:
    """Where an area's creature or container stands, if the scan says."""
    selector = row.get("selector")
    point = tuple(selector.get(key) for key in ("x", "y")) if isinstance(selector, dict) else ()
    if len(point) == 2 and all(type(value) is int and 0 <= value <= _MAX_COORDINATE for value in point):
        return point
    return None


def _nearest(row: dict, rows: list[dict]) -> dict | None:
    """The one of these rows nearest to where this one stands; None when two
    are as near, or where one stands is not known."""
    here = _position(row)
    least, nearest = None, None
    for other in rows:
        there = _position(other)
        if here is None or there is None:
            return None
        distance = (here[0] - there[0]) ** 2 + (here[1] - there[1]) ** 2
        if least is None or distance < least:
            least, nearest = distance, other
        elif distance == least:
            nearest = None
    return nearest


def _texts(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _rows(value, *keys: str) -> bool:
    """Whether a value is a list of objects with text in each of these keys."""
    return isinstance(value, list) and all(
        isinstance(row, dict) and all(isinstance(row.get(key), str) for key in keys) for row in value)


def _readable(draft) -> bool:
    """Whether a saved configuration has the parts the editor saves, of the
    kinds the carry-over reads; another is damaged or of a version that
    cannot be read."""
    profile = draft.get("profile") if isinstance(draft, dict) else None
    plan = draft.get("plan") if isinstance(draft, dict) else None
    if not isinstance(profile, dict) or not isinstance(plan, dict) or not _texts(profile.get("items")):
        return False
    if (plan.get("schema") != PLAN_SCHEMA or not PLAN_FIELDS <= set(plan) or
            not set(plan) <= PLAN_FIELDS | PLAN_OPTIONAL_FIELDS):
        return False
    if not isinstance(plan["world_fingerprint"], str) or not _rows(plan["sources"], "item"):
        return False
    for choice in plan["sources"]:
        if choice.get("policy") not in ("add", "relocate"):
            return False
        if choice["policy"] == "relocate" and not (_texts(choice.get("source_ids")) and
                                                   _texts(choice.get("registration_ids"))):
            return False
    if not _rows(plan["registrations"], "id") or not _rows(plan.get("target_registrations", []), "id"):
        return False
    if not _rows(plan["targets"], "id") or any(type(row.get("capacity")) is not int or
                                                not 1 <= row["capacity"] <= _MAX_CAPACITY for row in plan["targets"]):
        return False
    present = [key for key in _PRESET_FIELDS if key in plan]
    if present and (len(present) != len(_PRESET_FIELDS) or not isinstance(plan["original_template_id"], str) or
                    not isinstance(plan["original_choices"], dict) or
                    not isinstance(plan["original_options"], dict)):
        return False
    if "replacements" in plan and not isinstance(plan["replacements"], dict):
        return False
    if "duplicates" in plan and not _rows(plan["duplicates"], "source_id"):
        return False
    if "randomization" not in plan:
        return True
    rules = plan["randomization"]
    if not isinstance(rules, dict):
        return False
    sources, targets = rules.get("sources", {}), rules.get("targets", {})
    if not isinstance(sources, dict) or not isinstance(targets, dict):
        return False
    for rule in sources.values():
        # A reward's tier and the places its home claims, as the editor and the fit give every reward.
        if not isinstance(rule, dict) or not isinstance(rule.get("tier"), str):
            return False
        if not (_rows(rule.get("exclude_targets"), "id") and
                all(type(entry.get("ordinal")) is int for entry in rule["exclude_targets"])):
            return False
    if any(not _texts(tiers) for tiers in targets.values()):
        return False
    filters = rules.get("row_filters", {})
    return isinstance(filters, dict) and all(isinstance(filters.get(name, {}), dict)
                                             for name in ("sources", "targets"))


def _notes(rows: list[dict]) -> list[str]:
    """What the left-out choices are, in plain words: the items no longer in
    the game, the rewards and places this game cannot use as it is now, the
    places that could not be checked, those on creatures that can go missing,
    those whose quest CIR cannot change yet, then what becomes of a left-out
    reward."""
    items = [row for row in rows if row["kind"] == "item"]
    choices = [row for row in rows if row["kind"] != "item"]
    missing = [row for row in choices if row["reason"] == CARRIER_CAN_GO_MISSING]
    waiting = [row for row in choices if row["reason"] == NO_ROUTE]
    unverified = [row for row in choices if row["reason"] == TARGET_UNVERIFIED]
    unusable = [row for row in choices if row["reason"] not in (CARRIER_CAN_GO_MISSING, NO_ROUTE, TARGET_UNVERIFIED)]
    notes = []
    if items:
        one = len(items) == 1
        notes.append(f"{len(items)} {'item' if one else 'items'} from it {'is' if one else 'are'} no longer "
                     f"in the game, so {'it is' if one else 'they are'} left out")
    if unusable:
        notes.append(f"{original_configuration._counted(unusable)} from it cannot be used with the game as it "
                     "is now, so " + ("it is" if len(unusable) == 1 else "they are") + " left out")
    if unverified:
        notes.append(f"{original_configuration._counted(unverified)} from it could not be checked against the "
                     "scan it was made with, so " + ("it is" if len(unverified) == 1 else "they are") + " left out")
    if missing:
        notes.append(f"{original_configuration._counted(missing)} from it "
                     + ("is on a creature" if len(missing) == 1 else "are on creatures")
                     + " that may leave, vanish or never appear, so "
                     + ("it is" if len(missing) == 1 else "they are") + " left out")
    if waiting:
        notes.append(f"{original_configuration._counted(waiting)} from it "
                     + ("belongs to a quest" if len(waiting) == 1 else "belong to quests")
                     + " that Chriz Item Randomiser cannot change yet, so "
                     + ("it is" if len(waiting) == 1 else "they are") + " left out")
    if any(row["kind"] == "reward" for row in rows):
        notes.append("A left-out reward stays as the game has it")
    return notes
