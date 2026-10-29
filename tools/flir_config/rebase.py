"""Saved configurations carried over to new scans of the same game.

A configuration names the scans it was made with, and the editor opens only
the configurations of its own scans. After the game's mods change (a new
version of Chriz Item Randomiser among them) the start without arguments
scans the game again; rebase_drafts then carries each configuration of the
earlier scans over to the new ones.

It keeps what the player chose: the items, which of their records move, the
places and their reward slots, a preset's pools and options, tiers and shop
prices. What the scans decide comes from the new ones, as the editor would
offer it now: a preset's rewards and places from the preset fitted again with
the same pools and options (a row of its lists stays the same choice when the
new version moves it in another way), a special place from those this game
can deliver, the pool's reward from the prepared pool script, a record or
recipient chosen in the editor from the new world scan. A creature or
container chosen in an area is kept when the scan the configuration was made
with (``earlier_world``) shows the same one there.

What no longer fits is left out and named; nothing is added. A configuration
that cannot be read, whose preset no longer fits the game, or of which no
reward remains, is not carried over.
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
SOURCE_UNUSABLE = "source-record-unusable"
TARGET_ABSENT = "target-absent"
TARGET_CHANGED = "target-changed"
TARGET_UNVERIFIED = "target-unverified"
TARGET_UNUSABLE = "target-unusable"
NO_PLACE = original_configuration.NO_PLACE_IN_TIER
# Why a configuration is not carried over.
UNREADABLE = "it cannot be read"
NOTHING_LEFT = "none of its rewards can be used with the game as it is now"
PRESET_UNFIT = "its preset cannot be used with the game as it is now: "
_PRESET_FIELDS = ("original_template_id", "original_choices", "original_options")
# A place's reward slots, as the binding check allows them.
_MAX_CAPACITY = 0xFFFFFFFF


def rebase_drafts(catalog: dict, world: dict, drafts: list, *, earlier_world: dict | None = None,
                  scans: Scans | None = None) -> list[dict]:
    """Each saved configuration carried over to these scans, in order.

    A configuration these scans open already is returned as the editor checks
    it. Otherwise the result is ``{"draft", "left_out", "notes"}``: the
    configuration for these scans, the choices left out (``{"id", "kind",
    "reason"}``, kind ``item``, ``reward`` or ``place``) and what they are in
    plain words; or ``{"draft": None, "reason"}`` when it is not carried over.
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
        self.plan = fitted["plan"]
        self.registrations = {row["id"]: row for row in self.plan["registrations"]}
        self.places = {row["id"]: row for row in self.plan["target_registrations"]}
        # The item of each identity the fit moves, and the identity of each row it uses.
        self.items = {identity: choice["item"] for choice in self.plan["sources"]
                      for identity in choice["source_ids"] + choice["registration_ids"]}
        self.uses = {row: identity for identity, row in fitted["row_ids"].items()}
        self.reasons = {row["id"]: row["reason"] for row in fitted["not_used"]}
        # The rows of the preset's lists by the records they move, the pool's
        # row, and the places its options enable.
        self.records: dict[str, str] = {}
        self.pool = None
        for row in resolved["items"]:
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


class _Carrier:
    def __init__(self, catalog: dict, world: dict, scans: Scans, earlier_world):
        self.catalog, self.world, self.scans = catalog, world, scans
        self.index = scans.indexes(catalog, world)[1]
        self.earlier_world = earlier_world
        self.presets: dict[str, _Preset | ValueError] = {}
        self.special: dict[str, dict] | None = None
        self.earlier: dict[str, dict] | None = None

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

    def earlier_targets(self, fingerprint: str) -> dict[str, dict] | None:
        """The recipients of the scan a configuration was made with, if that is the earlier world."""
        world = self.earlier_world
        if (not isinstance(world, dict) or world.get("fingerprint") != fingerprint or
                not isinstance(world.get("targets"), list)):
            return None
        if self.earlier is None:
            self.earlier = {}
            for row in world["targets"]:
                if isinstance(row, dict) and isinstance(row.get("id"), str):
                    self.earlier.setdefault(row["id"], row)
        return self.earlier

    def source(self, identity: str, item: str, registered: bool, saved: dict | None,
               preset: _Preset | None) -> tuple[str | None, dict | None, str | None]:
        """A saved reward choice for these scans: (identity, registration or
        None for a record, None), or (None, None, why it is left out)."""
        if preset is not None:
            row = preset.row(identity, registered, saved)
            if row is not None:
                used = preset.uses.get(row)
                if used is not None and preset.items.get(used) == item:
                    return used, deepcopy(preset.registrations.get(used)), None
                return None, None, preset.reasons.get(row, NOT_AVAILABLE)
        if not registered:
            row = self.index["sources"].get(identity)
            if row is None or row["item"] != item:
                return None, None, SOURCE_ABSENT
            if UNUSABLE_SOURCE.intersection(row["concerns"]):
                return None, None, SOURCE_UNUSABLE
            return identity, None, None
        # The pool's reward, as the editor offers it for the pool's mace.
        if (saved is not None and saved.get("adapter_id") == POOL_ADAPTER and identity == POOL_SOURCE and
                item == POOL_ITEM and POOL_RESOURCE in self.index["resources"] and
                applied(self.world["game_root"], "quest_pool")):
            return identity, blpool_registration(self.world), None
        return None, None, NOT_AVAILABLE

    def place(self, identity: str, saved: dict | None, fingerprint: str,
              preset: _Preset | None) -> tuple[dict | None, str | None]:
        """A saved recipient for these scans: (its registration, or None for a
        recipient of the world scan, None), or (None, why it is left out)."""
        if saved is not None:
            row = saved.get("selector") if isinstance(saved.get("selector"), str) else None
            if preset is not None:
                if identity in preset.places:
                    return deepcopy(preset.places[identity]), None
                if row in preset.place_rows:
                    return None, preset.reasons.get(row, NOT_AVAILABLE)
            special = self.special_places()
            if identity in special:
                return deepcopy(special[identity]), None
            return None, NOT_AVAILABLE
        row = self.index["targets"].get(identity)
        if row is None:
            return None, TARGET_ABSENT
        if row["kind"] in ("actor", "container"):
            # A record of an area: the same creature or container only when the earlier scan shows it.
            earlier = self.earlier_targets(fingerprint)
            if earlier is None:
                return None, TARGET_UNVERIFIED
            if not _same_place(earlier.get(identity), row):
                return None, TARGET_CHANGED
        if UNRESOLVED_STORAGE in row["concerns"] or {DANGLING_INVENTORY, INVALID_INVENTORY}.intersection(row["concerns"]):
            return None, TARGET_UNUSABLE
        if row["kind"] == "actor" and not row["selector"]["embedded_cre_size"]:
            template = row["selector"]["template_resource"]
            if template not in self.index["resources"] or template in self.index["unreadable"]:
                return None, TARGET_UNUSABLE
        return None, None

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
        saved_registrations: dict[str, dict] = {}
        for row in plan["registrations"]:
            saved_registrations.setdefault(row["id"], row)
        # Each kept choice with what it moves now: (choice, records, registrations).
        choices, evidence, seen = [], {}, set()
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
                        identity, item, registered, saved_registrations.get(identity) if registered else None, preset)
                    if kept is None:
                        left_out.append({"id": identity, "kind": "reward", "reason": reason})
                    elif kept not in seen:
                        seen.add(kept)
                        (records if registration is None else registrations).append(kept)
                        evidence[kept] = registration
            choices.append((choice, records, registrations))
        saved_places = {row["id"]: row for row in plan.get("target_registrations", [])}
        targets, places, capacity = [], [], {}
        for choice in plan["targets"]:
            identity = choice["id"]
            if identity in capacity:
                continue
            registration, reason = self.place(identity, saved_places.get(identity), plan["world_fingerprint"],
                                              preset)
            if reason is not None:
                left_out.append({"id": identity, "kind": "place", "reason": reason})
                continue
            if registration is not None:
                registration["capacity"] = choice["capacity"]
                places.append(registration)
            targets.append(deepcopy(choice))
            capacity[identity] = choice["capacity"]
        moved = [identity for _, records, registrations in choices if records is not None
                 for identity in records + registrations]
        rules = None
        if "randomization" in plan or preset is not None:
            rules = _rules(plan.get("randomization", {}), preset.plan["randomization"] if preset else None,
                           moved, capacity)
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
            return {"draft": None, "reason": NOTHING_LEFT}
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
        if places or preset is not None:
            result["target_registrations"] = places
        else:
            # A configuration that is no preset has original recipients only for its special places.
            result.pop("target_registrations", None)
        if rules is not None:
            result["randomization"] = rules
        fresh = preset.plan if preset is not None else plan
        records = {identity for choice in sources for identity in choice.get("source_ids", ())}
        replacements = {identity: replacement for identity, replacement in fresh.get("replacements", {}).items()
                        if identity in records}
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
        draft = checked_draft(self.catalog, self.world, {"profile": rebased, "plan": result})
        return {"draft": draft, "left_out": left_out, "notes": _notes(left_out)}


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


def _same_place(before, row: dict) -> bool:
    """Whether an area's creature or container in the earlier scan is the same one now."""
    if not isinstance(before, dict) or before.get("kind") != row["kind"] or before.get("resource") != row["resource"]:
        return False
    selector = before.get("selector")
    if not isinstance(selector, dict):
        return False
    keys = ("name", "cre_resref", "script_name") if row["kind"] == "actor" else ("name", "type")
    return all(selector.get(key) == row["selector"].get(key) for key in keys)


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
    the game, the rewards and places this game cannot use as it is now, those
    on creatures that can go missing, those whose quest CIR cannot change yet,
    then what becomes of a left-out reward."""
    items = [row for row in rows if row["kind"] == "item"]
    choices = [row for row in rows if row["kind"] != "item"]
    missing = [row for row in choices if row["reason"] == CARRIER_CAN_GO_MISSING]
    waiting = [row for row in choices if row["reason"] == NO_ROUTE]
    unusable = [row for row in choices if row["reason"] not in (CARRIER_CAN_GO_MISSING, NO_ROUTE)]
    notes = []
    if items:
        one = len(items) == 1
        notes.append(f"{len(items)} {'item' if one else 'items'} from it {'is' if one else 'are'} no longer "
                     f"in the game, so {'it is' if one else 'they are'} left out")
    if unusable:
        notes.append(f"{original_configuration._counted(unusable)} from it cannot be used with the game as it "
                     "is now, so " + ("it is" if len(unusable) == 1 else "they are") + " left out")
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
