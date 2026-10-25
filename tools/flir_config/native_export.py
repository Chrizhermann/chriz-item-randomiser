"""Export reviewed physical and BLPOOL sources for the experimental native runtime.

This writes configuration outside the game. It does not install runtime hooks,
change resources, choose gameplay content, or establish live target eligibility.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path

from .activation import EXPORT_FILES, EXPORT_FILES_SCHEMA
from .campaigns import read_campaign, _refused_lua, _render_lua, _store_path, _write_immutable
from .creature_inventory import rewrite_inventory
from .store_inventory import rewrite_inventory as rewrite_store
from .profiles import (DEFAULT_SHOP_PRICING, HEX256, MAX_ITEM_PRICE, ProfileError,
                       _digest, canonical_bytes, ensure_output_outside_game,
                       validate_catalog)
from .resources import GameResources, RESOURCE_TYPES, _Tlk
from .sources import Scans, validate_world, DANGLING_INVENTORY, INVALID_INVENTORY, UNRESOLVED_STORAGE
from .scripted_sources import (ADAPTER_ID, ITEM, PAYLOAD, RESOURCE, SELECTOR, SOURCE_ID,
                               campaign_record)
from .quest_rewards import QUESTS, quest_spec
from .prepared import QUEST_CHANGES, applied, require_installed
from .quest_options import (KANGAXX_RESOURCES, prepare_quest_options, patch_kangaxx_journal,
                            original_dialogue, original_crypt)
from .spell_rewards import (SPELL, PAYLOAD as KEY_PAYLOAD, patch_key_spell,
                            ORIGINAL_SHA256 as KEY_ORIGINAL_HASH,
                            PATCHED_SHA256 as KEY_PATCHED_HASH)
from .original_options import (SPELL_SHIELD_SOURCE, spell_shield_randomized, spell_shield_spec,
                               patch_spell_shield_stock)
from .mode2_rewards import (SOURCE_REWARDS as MODE2_SOURCES, TARGET_REWARDS as MODE2_TARGETS,
                            RESOURCE_REWARDS as MODE2_RESOURCES, source_spec as mode2_source_spec,
                            target_spec as mode2_target_spec, patch_reward as patch_mode2_reward,
                            prepare_reward_texts, own_patch as own_mode2_patch, journal_lua as mode2_journal_lua,
                            TEXT_REFS as MODE2_TEXT_REFS, transfer_spec as mode2_transfer_spec, unpatched_script,
                            resource_specs as mode2_resource_specs)
from .mode2_rewards import DESTROY_GATES, PHYSICAL_SOURCE_REWARDS, REWARDS as MODE2_REWARDS
from .container_rewards import (RESOURCE_TARGETS as CONTAINER_RESOURCES,
                                TEXT_RESOURCES as CONTAINER_TEXT_RESOURCES, container_error,
                                patch_container_resource, own_patch as own_container_patch,
                                prepare_container_texts, treasure_area, treasure_container)
from .platform import game_platform
from .reward_copies import RewardCopies
from .game_text import GameTextError, paired, text_files, unpaired
from .reward_routes import ChangedSinceScan, reward_routes
from .original_catalog import container_target_spec, mode2_route_source, mode2_route_target
from .cursed_disguises import validate_cursed_disguise_spec, prepare_cursed_disguises
from .duplicates import holdings

# next.lua: the revision of each campaign start (read by M_FLPR.lua).
NEXT_SCHEMA = "flir-next-v2"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProfileError(message)


def _name(value: str, identity: str) -> str:
    _require(isinstance(value, str) and 1 <= len(value) <= 32 and
             all(32 <= ord(c) < 127 for c in value),
             f"Native endpoint needs a nonempty ASCII script name: {identity}")
    return value


def _area(resource: str) -> str:
    _require(re.fullmatch(r"are:[a-z0-9#_-]{1,8}", resource) is not None,
             f"Native export needs an ARE resource: {resource}")
    return resource[4:].upper()


def _compile(manifest: dict, world: dict, index: dict, items: dict,
             problems: dict[str, str] | None = None, *, catalog: dict, stored: bool = False,
             notes: list[str] | None = None, routed: set[str] | None = None,
             quests: set[str] | None = None) -> tuple[dict, set[str], set[str]]:
    """Compile a campaign for the native runtime.

    Without ``problems`` any unsupported row stops the compile. With a dict,
    each source or target row the runtime cannot use is recorded there by its
    identity instead, and the other rows are still checked. Extended reward
    routes are recognised in the game's bytes as the export recognises them
    (reward_routes.py); a reward resource changed since the scan stops the
    compile.

    A ``stored`` revision was exported before and belongs to running saves.
    While the game's files are unchanged it keeps what it recorded: its
    container quests' bindings, and its reward routes when CIR's newer check
    finds a copy of their reward replies that cannot be gated (said in
    ``notes``).

    ``routed`` collects the resources that the campaign's reward routes
    patch, ``quests`` the quest scripts it needs as CIR's installer prepared
    them (both among the required resources).
    """
    # A rescan can discover unrelated resources (or a new resource type).
    # Compare the selected records and resource hashes below; existing native
    # revisions must still reproduce byte-for-byte when exported again.
    native = {"schema": "flir-native-fixture-v1", "revision": manifest["revision"],
              "sources": {}, "targets": {}, "resource_hashes": {},
              "ready_detail": "have been assigned to the selected recipients."}
    # Existing actor-only native artifacts are immutable. Their campaigns lack
    # pricing, so recompilation must retain the exact old native field set.
    has_shop_pricing = "shop_pricing" in manifest or any(
        row["record"]["kind"] == "store" for row in manifest["targets"])
    if has_shop_pricing:
        native["shop_pricing"] = deepcopy(manifest.get("shop_pricing", DEFAULT_SHOP_PRICING))
        native["shop_prices"] = {}
    required, pristine, selected_items, selected_actors = set(), set(), set(), set()
    selected_owners = Counter(s["record"]["resource"] for s in manifest["sources"]
                              if s["record"]["kind"] in ("cre-template", "store"))
    # Item Randomiser's second copies of the moved rewards (duplicates.py). The
    # gate rewrites a creature or shop that holds one as a whole, so a source
    # in it is rewritten with it.
    copies = manifest.get("duplicates")
    if copies is not None:
        native["duplicates"] = []
        selected_owners.update(copy["resource"] for copy in copies if not copy["resource"].startswith("are:"))

    def readable(resource: str) -> None:
        # A file the scan left out is in the game, but nothing in it is known.
        _require(resource not in index["unreadable"], f"Required reward resource cannot be read: {resource}")

    def use(resource: str) -> None:
        readable(resource)
        required.add(resource)
        kind, name = resource.split(":", 1)
        # The installer adds the mod's stand-in items with its other changes.
        _require(resource in index["resources"] or resource not in ("itm:flbhcryp", "itm:flrbelt1"),
                 f"Chriz Item Randomiser did not install {resource}; reinstall it, then rescan")
        _require(resource in index["resources"], f"Required reward resource is absent: {resource}")
        digest = index["resources"][resource]["sha256"]
        native["resource_hashes"][name.upper() + "." + kind.upper()] = digest
        return digest

    def use_route(resource: str) -> None:
        # Apply writes this resource with the reward routes' patch.
        use(resource)
        if routed is not None:
            routed.add(resource)

    def route_resources(spec: dict) -> list[str]:
        # The resources a route patches in this game: a script that would
        # destroy its item needs no gate where the game has none.
        return [resource for resource in spec["resources"]
                if resource not in DESTROY_GATES or resource in index["resources"]]

    def use_quest(resource: str) -> None:
        # The quest script as the installer prepared it (or as Apply extended it).
        use(resource)
        if quests is not None:
            quests.add(resource)

    routes = []

    def require_route(name: str) -> None:
        # One look at the reward routes per compile: each check run sees the
        # game's files as they are now.
        if not routes:
            routes.append(reward_routes(catalog, world))
        routes[0].require_route(name, copies=not stored)
        reason = routes[0].copy_reason(name) if stored else None
        if reason and notes is not None:
            notes.append(f"Saved games of revision {manifest['revision'][:12]} keep the extended reward route "
                         f"for {name.upper()}, although {reason}")

    def require_container(target_id: str) -> None:
        if not routes:
            routes.append(reward_routes(catalog, world))
        routes[0].require_container(target_id)

    def gives(name: str) -> bool:
        if not routes:
            routes.append(reward_routes(catalog, world))
        return routes[0].gives(name)

    def naming() -> dict:
        # The routes' items as the game names them (EET renames BG1's).
        if not routes:
            routes.append(reward_routes(catalog, world))
        return {"game": routes[0].game, "names": routes[0].names}

    def require_treasury_names(identity: str, treasury: dict, script: str) -> None:
        # The treasury's script runs with every AI update of its container,
        # trapped or not (2.7.3.0), and destroys the king's reward items by
        # name once his job is done: a reward of such a name is safe there
        # only as the source whose move switches that destroy off (_gate_destroys).
        # The king's handover takes a reward by its name: every other item
        # that can lie in the treasury under that name, its own or another
        # reward, must be an equal copy, or it could not tell which one to
        # take. A randomization puts each reward only into places of its tier
        # (FLPRCore), with overflow several into one place; without one each
        # reward has a place of its own. Run once every place is compiled.
        resource = "bcs:" + script.lower()
        if not routes:
            routes.append(reward_routes(catalog, world))
        destroyed = routes[0].destroyed(resource)
        gates = {spec["item"].lower(): spec["selected_global"]
                 for spec in mode2_resource_specs(resource, **naming())} if resource in DESTROY_GATES else {}
        held = defaultdict(list)
        for row in index["sources"].values():
            if row["owner"] == treasury["id"]:
                held[row["item"]].append(row)
        randomization = manifest.get("randomization")
        rules, tiers = (randomization or {}).get("sources", {}), (randomization or {}).get("targets", {})

        def spot(endpoint: dict) -> tuple:
            return (endpoint["area"], endpoint["script_name"].lower(), endpoint["x"], endpoint["y"],
                    endpoint["container_type"])

        # The other places in the treasury, and whether its own place takes
        # several rewards (its capacity, or overflow).
        beside = [target["id"] for target in manifest["targets"] if target["id"] != identity and
                  native["targets"].get(target["id"], {}).get("kind") == "container" and
                  spot(native["targets"][target["id"]]) == spot(native["targets"][identity])]
        capacity = next(target["capacity"] for target in manifest["targets"] if target["id"] == identity)
        crowded = capacity > 1 or (randomization or {}).get("overflow") is True

        def takes(target_id: str, source_id: str) -> bool:
            # Whether the place can get the reward (an unknown tier: it can).
            slots, tier = tiers.get(target_id), rules.get(source_id, {}).get("tier")
            return randomization is None or slots is None or tier is None or tier in slots

        def beside_it(source_id: str) -> bool:
            # Whether the reward can lie in the treasury beside this place's.
            if randomization is None:
                return bool(beside) or capacity > 1
            return any(takes(other, source_id) for other in beside) or (crowded and takes(identity, source_id))

        rewards = []
        for selected in manifest["sources"]:
            endpoint = native["sources"].get(selected["id"])
            if endpoint is not None:  # Else left out in this round.
                source = selected["record"]
                rewards.append((selected["id"], source, endpoint,
                                {source["item"], *rules.get(selected["id"], {}).get("reward_variants", ())},
                                [source["record"][key] for key in ("charges", "flags", "expiration")]))
        for source_id, source, endpoint, names, payload in rewards:
            if not takes(identity, source_id):
                continue  # Never this place's reward.
            guard = endpoint.get("reward_selected_global", endpoint.get("selected_global"))
            for name in sorted(names & destroyed):
                _require(gates.get(name) is not None and gates[name] == guard,
                         f"{script.upper()}.BCS destroys every {name.upper()} in the treasury, and the reward "
                         f"{source_id} can be one: {identity}")
            own = {source_id, *source.get("physical_source_ids", ())}
            for name in sorted(names):
                _require(all([row["record"][key] for key in ("charges", "flags", "expiration")] == payload
                             for row in held.get(name, ()) if row["id"] not in own),
                         f"The treasury holds another {name.upper()}, which the king's handover could not tell "
                         f"from the reward {source_id}: {identity}")
                for other_id, _, _, other_names, other_payload in rewards:
                    _require(other_id == source_id or name not in other_names or other_payload == payload or
                             not beside_it(other_id),
                             f"The reward {other_id} can lie in the treasury as another {name.upper()}, which the "
                             f"king's handover could not tell from the reward {source_id}: {identity}")

    def actor_name(target: dict) -> str | None:
        selector = target["selector"]
        template = index["targets"].get(selector.get("template_resource"))
        return (template["selector"].get("script_name") if template else selector.get("script_name"))

    shield = manifest.get("spell_shield")
    if shield is not None:
        randomized = spell_shield_randomized(source["record"].get("authored") for source in manifest["sources"])
        _require(shield == spell_shield_spec(world, randomized=randomized), "Spell Shield stock differs from the scan")
        native["spell_shield"] = deepcopy(shield)
        use(shield["resource"])
    if "cursed_disguises" in manifest:
        disguise = manifest["cursed_disguises"]
        validate_cursed_disguise_spec(disguise)
        for name, digest in disguise["resource_hashes"].items():
            _require(use("itm:" + name) == digest, f"Cursed item appearance resource changed: {name}")

    container_names = Counter((t["resource"], t["selector"]["name"].lower(),
                               t["selector"]["x"], t["selector"]["y"], t["selector"]["type"])
                              for t in index["targets"].values() if t["kind"] == "container")
    actor_names = Counter((t["resource"], (actor_name(t) or "").lower())
                          for t in index["targets"].values() if t["kind"] == "actor")
    container_items = Counter((s["owner"], s["item"]) for s in index["sources"].values()
                              if s["kind"] == "container")
    physical_items = Counter((s["resource"], s["kind"], s["item"]) for s in index["sources"].values())
    # The records of each item in each creature file, in record order.
    template_records = defaultdict(list)
    for row in sorted((row for row in index["sources"].values() if row["kind"] == "cre-template"),
                      key=lambda row: row["record"]["record_index"]):
        template_records[row["resource"], row["item"]].append(row)
    creature_items = Counter((s["owner"], s["item"]) for s in index["sources"].values()
                             if s["kind"] in ("cre-template", "embedded-cre"))
    owner_items = Counter((s["owner"], s["item"]) for s in index["sources"].values())
    template_actors = defaultdict(list)
    for target in index["targets"].values():
        if target["kind"] == "actor":
            template_actors[target["selector"].get("template_resource")].append(target)

    def attempt(identity: str, step, *args) -> None:
        # With ``problems``, a row the runtime cannot use is recorded and left
        # out of the result instead of stopping the whole configuration.
        if problems is None:
            step(*args)
            return
        if identity in problems:
            return
        actors = set(selected_actors)
        try:
            step(*args)
        except ChangedSinceScan:
            raise
        except ProfileError as exc:
            problems[identity] = str(exc)
            selected_actors.clear()
            selected_actors.update(actors)
            native["sources"].pop(identity, None)
            native["targets"].pop(identity, None)
            if "duplicates" in native:
                native["duplicates"] = [copy for copy in native["duplicates"] if copy["source_id"] != identity]

    def compile_source(selected: dict) -> None:
        source, identity = selected["record"], selected["id"]
        _require(source["kind"] in ("container", "cre-template", "embedded-cre", "store", "scripted", "virtual", "authored-source"),
                 f"Native export does not yet support this source: {identity}")
        # The extended reward route this row is: its own, or a preset's row of it.
        route = mode2_route_source(source["authored"]) if source["kind"] == "authored-source" else None
        if source["kind"] == "virtual":
            _require(source["resource"] == "itm:" + source["item"] and
                     index["resources"][source["resource"]]["sha256"] == selected["resource_sha256"],
                     f"Virtual item changed since the scan: {identity}")
        elif source["kind"] == "authored-source" or (shield and identity == SPELL_SHIELD_SOURCE):
            # A post-install rescan sees our campaign-aware DLG/BCS patch,
            # also in a quest script that the installer prepared (the book's).
            # Reward route bytes are recognised below (reward_routes.py) and
            # again by the export, before writing any output.
            _require(index["resources"][source["resource"]]["sha256"] == selected["resource_sha256"] or
                     source["resource"] in MODE2_RESOURCES,
                     f"Authored source changed since the scan: {identity}")
        elif source["kind"] == "scripted":
            _require(applied(world["game_root"], "quest_pool"),
                     f"Chriz Item Randomiser did not prepare BLPOOL.BCS; reinstall it, then rescan: {identity}")
            _require(identity == SOURCE_ID and selected["quantity"] == 1 and
                     "replacement" not in selected and source == campaign_record({
                         "id": SOURCE_ID, "item": ITEM, "resource": RESOURCE,
                         "resource_sha256": selected["resource_sha256"], "adapter_id": ADAPTER_ID,
                         "selector": SELECTOR, "copies": 1, "optional": False}) and
                     index["resources"].get(RESOURCE, {}).get("sha256") == selected["resource_sha256"],
                     f"Scripted source differs from the reviewed scan: {identity}")
        else:
            _require(index["sources"].get(identity) == source and
                     index["resources"][source["resource"]]["sha256"] == selected["resource_sha256"],
                     f"Selected source differs from the scan: {identity}")
        record = source["record"]
        item = items[source["item"]]
        # The reward keeps the source's flags (identified, unstealable, stolen
        # and bits another mod set). An undroppable one (8) would never leave
        # a creature target.
        flags = record["flags"]
        _require(re.fullmatch(r"[a-z0-9#_-]{1,8}", source["item"]) is not None and
                 record["expiration"] == 0 and type(flags) is int and 0 <= flags <= 0xFFFFFFFF and
                 not flags & 8 and item["flags"] & 12 == 12,
                 f"Native source requires an unexpired, visible droppable item: {identity}")
        selected_items.add(source["item"])
        if source["kind"] == "authored-source" and source["resource"].startswith("cre:"):
            records = [index["sources"][key] for key in source["physical_source_ids"]]
            _require(bool(records) and all(row["resource"] == source["resource"] and
                     row["item"] == source["item"] for row in records),
                     f"Authored creature original differs from its physical records: {identity}")
            endpoint = {"kind": "guarded-template", "creature_resource": source["resource"],
                        "record_indices": [row["record"]["record_index"] for row in records],
                        "payload": deepcopy(record), "identity": _digest({"source": selected})}
            if source["authored"]["source"] == "unshey.fl":
                endpoint["replacement"] = "flrbelt1"
                use("itm:flrbelt1")
                use("dlg:unshey")
            native["sources"][identity] = endpoint
            use(source["resource"]); use("itm:" + source["item"])
            return
        if source["kind"] == "authored-source" and source["resource"] == SPELL["resource"]:
            _require(identity == "original-source:bg2:mode2:item:69" and record == KEY_PAYLOAD and
                     selected["resource_sha256"] in (KEY_ORIGINAL_HASH, KEY_PATCHED_HASH),
                     f"Deck of Many Things source differs from reviewed spell: {identity}")
            native["sources"][identity] = {
                "kind": "guarded-script", "adapter_id": "spell-key-v1",
                **{key: SPELL[key] for key in ("quest", "resource", "selected_global", "consumed_global")},
                "payload": deepcopy(record), "identity": _digest({"source": selected})}
            use(source["resource"]); use("itm:" + source["item"])
            native["resource_hashes"]["SPIN621.SPL"] = KEY_PATCHED_HASH
            return
        if route is not None:
            spec = mode2_source_spec(route, **naming())
            _require(source["resource"] == spec["resource"] and record == spec["payload"],
                     f"The extended reward source differs from its route: {identity}")
            require_route(MODE2_SOURCES[route])
            native["sources"][identity] = {
                "kind": "guarded-script", "adapter_id": "mode2-reward-v1",
                **{key: spec[key] for key in ("quest", "resource", "selected_global", "consumed_global")},
                "payload": deepcopy(record), "identity": _digest({"source": selected})}
            if route != identity:
                # A preset's row: the runtime knows the route's flags and
                # copies by the route's own row.
                native["sources"][identity]["route_source"] = route
            for name in spec.get("required_transfers", ()):
                duplicate = mode2_transfer_spec(name, **naming())
                if route not in duplicate["authored_source_ids"]:
                    continue
                # The creature's own copies of the reward, wherever this game
                # keeps them; the runtime checks each record's bytes again.
                resource, item = duplicate["source_resource"], duplicate["item"]
                creature = resource[4:].upper() + ".CRE"
                # The runtime accepts each copy under its registered names
                # (FLPRGat.lua's auxiliary_sources), which EET keeps.
                registered = mode2_transfer_spec(name)
                _require((resource, item) == (registered["source_resource"], registered["item"]),
                         f"This game renames {registered['source_resource'][4:].upper()}.CRE or "
                         f"{registered['item'].upper()}, which Chriz Item Randomiser's runtime knows "
                         f"only by those names: {identity}")
                records = template_records[resource, item]
                if not records and route not in duplicate["source_ids"]:
                    continue  # A copy of another route's reward that this game does not have.
                _require(bool(records), f"{creature} does not carry {item.upper()}, which the reward of "
                                        f"{duplicate['quest'].upper()}.DLG hands over: {identity}")
                resource_sha256 = use(resource)
                payloads = [{key: row["record"][key] for key in ("resref", "expiration", "charges", "flags")}
                            for row in records]
                _require(all(payload == payloads[0] for payload in payloads),
                         f"{creature} carries {item.upper()} in records that differ: {identity}")
                auxiliary = {
                    "resource": resource, "resource_sha256": resource_sha256,
                    "record_indices": [row["record"]["record_index"] for row in records],
                    "payload": payloads[0], "selected_global": duplicate["selected_global"]}
                if duplicate.get("replacement"):
                    auxiliary["replacement"] = duplicate["replacement"]
                    use("itm:" + duplicate["replacement"])
                native["sources"][identity].setdefault("auxiliary_creatures", []).append(auxiliary)
            # All alternate quest routes share the same saved selection flag.
            # Their actual bytes are reviewed by patch_mode2_reward at export.
            resources = route_resources(spec)
            if len(resources) > 1:
                native["sources"][identity]["resources"] = resources
            for resource in resources:
                use_route(resource)
            use("itm:" + source["item"])
            return
        if source["kind"] == "authored-source":
            spec = quest_spec(identity)
            _require(source["resource"] == spec["resource"] and source["item"] == spec["item"] and
                     applied(world["game_root"], QUEST_CHANGES[spec["resource"][4:]]),
                     f"Chriz Item Randomiser did not prepare this quest reward; reinstall it, then rescan: {identity}")
            native["sources"][identity] = {
                "kind": "guarded-script", "adapter_id": "original-quest-v1",
                **{key: spec[key] for key in ("quest", "resource", "selected_global", "consumed_global")},
                "payload": deepcopy(record), "identity": _digest({"source": selected})}
            use_quest(source["resource"]); use("itm:" + source["item"])
            return
        if source["kind"] == "virtual":
            native["sources"][identity] = {"kind": "virtual", "payload": deepcopy(record),
                                             "identity": _digest({"source": selected})}
            use(source["resource"])
            return
        if source["kind"] == "scripted":
            native["sources"][identity] = {
                "kind": "guarded-script", "adapter_id": ADAPTER_ID,
                "resource": "BLPOOL", "selector": SELECTOR,
                "payload": deepcopy(PAYLOAD),
                "identity": _digest({"source": selected})}
            use_quest(RESOURCE); use("itm:" + source["item"])
            return
        if source["kind"] == "store":
            stock_option = shield is not None and identity == shield["source_id"]
            stock_rows = owner_items[source["owner"], source["item"]]
            _require(stock_rows <= 1 if stock_option else stock_rows == 1,
                     f"Native source shop holds another row of {source['item']}: {identity}")
            native["sources"][identity] = {
                "kind": "guarded-store", "store": source["resource"][4:],
                "record_index": shield["record_index"] if stock_option else record["record_index"],
                "quantity": selected["quantity"],
                "payload": {key: record[key] for key in ("resref", "expiration", "charges", "flags")},
                "identity": _digest({"source": selected})}
            if stock_option:
                native["sources"][identity]["stock_option"] = "more_spell_shield"
            if selected_owners[source["resource"]] > 1 or (shield and source["resource"] == shield["resource"]):
                native["sources"][identity]["multiple_stock"] = True
            use(source["resource"]); use("itm:" + source["item"])
            return
        elif source["kind"] == "container":
            owner = index["targets"][source["owner"]]
            selector = owner["selector"]
            area, name = _area(source["resource"]), _name(selector["name"], identity)
            _require(not selector["dynamic_item_table"] and
                     container_names[source["resource"], name.lower(), selector["x"], selector["y"], selector["type"]] == 1,
                     f"Native source needs a unique static container name: {identity}")
            _require(0 <= selector["x"] <= 32767 and 0 <= selector["y"] <= 32767,
                     f"Native container coordinates are unsupported: {identity}")
            # The current native confirmation counts this resref in its owner.
            # An unselected duplicate must not prevent confirmation indefinitely.
            _require(container_items[source["owner"], source["item"]] == 1,
                     f"Native source container holds another copy of {source['item']}: {identity}")
            native["sources"][identity] = {
                "kind": "guarded-container", "area": area, "script_name": name,
                "x": selector["x"], "y": selector["y"], "container_type": selector["type"],
                "payload": {key: record[key] for key in ("resref", "expiration", "charges", "flags")},
                "identity": _digest({"source": selected, "owner": owner})}
            if selected.get("replacement", {}).get("resref", "blank") != "blank":
                replacement = selected["replacement"]
                use("itm:" + replacement["resref"])
                native["sources"][identity]["replacement"] = replacement["resref"]
        else:
            if source["kind"] == "cre-template":
                actors = template_actors.get(source["resource"], [])
                if (len(actors) != 1 or selected_owners[source["resource"]] > 1 or
                        actor_names[actors[0]["resource"], (actor_name(actors[0]) or "").lower()] != 1):
                    replacement = selected.get("replacement")
                    _require(len(record["slots"]) == 1 and
                             (21 <= record["slots"][0] <= 36 or replacement is not None),
                             f"Equipped creature source needs explicit replacement equipment (or blank): {identity}")
                    endpoint = {"kind": "guarded-template", "creature_resource": source["resource"],
                                "payload": {key: record[key] for key in ("resref", "expiration", "charges", "flags")},
                                "identity": _digest({"source": selected})}
                    if replacement:
                        endpoint["replacement"] = replacement["resref"]
                        if replacement["resref"] != "blank":
                            _require(use("itm:" + replacement["resref"]) == replacement["resource_sha256"],
                                     f"Replacement item changed: {identity}")
                    native["sources"][identity] = endpoint
                    use(source["resource"]); use("itm:" + source["item"])
                    return
                owner = actors[0]
            else:
                owner = index["targets"][source["owner"]]
            selector = owner["selector"]
            area, name = _area(owner["resource"]), _name(actor_name(owner), identity)
            _require(actor_names[owner["resource"], name.lower()] == 1,
                     f"Native creature source needs a unique actor script name in its area: {identity}")
            _require(owner["id"] not in selected_actors,
                     f"Native export does not yet support two selected sources on one actor: {owner['id']}")
            selected_actors.add(owner["id"])
            _require(creature_items[source["owner"], source["item"]] == 1,
                     f"Native source creature holds another copy of {source['item']}: {identity}")
            _require(len(record["slots"]) == 1,
                     f"Native creature source requires one inventory slot: {identity}")
            replacement = selected.get("replacement")
            _require(21 <= record["slots"][0] <= 36 or replacement is not None,
                     f"Equipped creature source needs explicit replacement equipment (or blank): {identity}")
            if replacement and replacement["resref"] != "blank":
                resource = "itm:" + replacement["resref"]
                _require(use(resource) == replacement["resource_sha256"],
                         f"Replacement item changed or is absent: {identity}")
                use(resource)
            native["sources"][identity] = {
                "kind": "guarded-creature", "area": area, "script_name": name,
                "actor_index": selector["record_index"],
                "creature_resource": source["resource"], "actor_owner": owner["id"],
                "payload": {key: record[key] for key in ("resref", "expiration", "charges", "flags")},
                "identity": _digest({"source": selected, "owner": owner})}
            if replacement:
                native["sources"][identity]["replacement"] = replacement["resref"]
            use(owner["resource"])
        use(source["resource"]); use("itm:" + source["item"])
        pristine.add(owner["resource"])

    for selected in manifest["sources"]:
        attempt(selected["id"], compile_source, selected)

    def compile_variants(rule: dict) -> None:
        for name in rule.get("reward_variants", []):
            _require(name in items, f"Original reward variant is absent from the scan: {name}")
            use("itm:" + name)
            selected_items.add(name)

    for identity, rule in manifest.get("randomization", {}).get("sources", {}).items():
        attempt(identity, compile_variants, rule)

    if has_shop_pricing:
        percent = native["shop_pricing"]["percent"]
        overrides = native["shop_pricing"]["base_prices"]
        for resref in sorted(selected_items):
            base = overrides.get(resref, items[resref]["price"])
            effective = base * percent // 100
            _require(effective <= MAX_ITEM_PRICE,
                     f"Experimental native shop price exceeds 32-bit gold range: {resref}")
            native["shop_prices"][resref] = effective

    # UDLESA's sword is an ordinary physical source. Its conversation must use
    # the same kept/relocated decision without registering a second reward unit.
    # So must the Sahuagin king's reward reply, which creates another treasury rod.
    def compile_inventory_reward(selected: dict) -> None:
        source = selected["record"]
        key = (source["resource"], source["kind"], source["item"])
        name = PHYSICAL_SOURCE_REWARDS.get(key)
        # Where nothing else gives the item, it moves as any other source.
        if not name or not gives(name):
            return
        reward = MODE2_REWARDS[name]
        # Both records would set the one flag the dialogue reads.
        kind, resref = source["resource"].split(":")
        _require(physical_items[key] == 1,
                 f"{resref.upper()}.{kind.upper()} holds another {source['item'].upper()} that the reward of "
                 f"{reward['quest'].upper()}.DLG could stand for: {selected['id']}")
        require_route(name)
        native["sources"][selected["id"]]["reward_selected_global"] = reward["selected_global"]
        for resource in route_resources(reward):
            use_route(resource)

    for selected in manifest["sources"]:
        attempt(selected["id"], compile_inventory_reward, selected)

    # The records the sources move, and the copies a reward route or an
    # earlier row already removes.
    moved, claimed = set(), set()
    for selected in manifest["sources"]:
        if selected["record"]["kind"] in ("container", "cre-template", "embedded-cre", "store"):
            moved.add(selected["id"])
        moved.update(selected["record"].get("physical_source_ids", ()))
    for endpoint in native["sources"].values():
        for auxiliary in endpoint.get("auxiliary_creatures", ()):
            claimed.update(f"{auxiliary['resource']}/item/{record}" for record in auxiliary["record_indices"])
    held = holdings(index["sources"]) if copies else {}

    def compile_copy(copy: dict) -> None:
        # Every record of the moved item in the holder, as the scan finds it.
        identity, holder, item = copy["source_id"], copy["resource"], copy["item"]
        label, kind, named = _filename(holder), holder.split(":", 1)[0], item.upper()
        _require(holder not in index["unreadable"],
                 f"{label} cannot be read, so Item Randomiser's second copy of {named} there cannot be "
                 f"found: {identity}")
        _require(index["resources"].get(holder, {}).get("sha256") == copy["resource_sha256"],
                 f"{label} changed since the scan: {identity}")
        records = held.get((holder, item), [])
        _require(bool(records), f"{label} no longer holds Item Randomiser's second copy of {named}: {identity}")
        fields = ("resref", "expiration", "charges", "flags")
        stock = ("quantity", "infinite_supply_flag") if kind == "sto" else ()
        payloads = [{key: row["record"][key] for key in fields + stock} for row in records]
        _require(all(payload == payloads[0] for payload in payloads),
                 f"{label} holds Item Randomiser's second copy of {named} in records that differ: {identity}")
        ids = {row["id"] for row in records}
        _require(not moved & ids, f"{label}'s {named} moves as a reward of its own, so it cannot also be "
                                  f"the second copy of another: {identity}")
        _require(not claimed & ids, f"{label}'s {named} is already the second copy of another reward: {identity}")
        concerns = set().union(*(row["concerns"] for row in records))
        # The area loader identifies a container's record by its index (FLPRGat.lua).
        _require(kind != "are" or not concerns & {"shared-container-record", "unowned-item-record",
                                                   "dynamic-container-items"},
                 f"{label} keeps its second copy of {named} outside a container with fixed contents: {identity}")
        # Removing a creature's record renumbers the slots that name later records.
        _require(kind != "cre" or "replacement" in copy or not concerns & {DANGLING_INVENTORY, INVALID_INVENTORY},
                 f"{label} has inventory slots that name no record, so its second copy of {named} cannot "
                 f"be removed: {identity}")
        entry = {"id": copy["id"], "source_id": identity, "resource": holder, "resource_sha256": use(holder),
                 "record_indices": [row["record"]["record_index"] for row in records],
                 "payload": {key: payloads[0][key] for key in fields}}
        if stock:
            entry["stock"] = {key: payloads[0][key] for key in stock}
        if "replacement" in copy:
            replacement = copy["replacement"]
            _require(use("itm:" + replacement["resref"]) == replacement["resource_sha256"],
                     f"Replacement item changed: {identity}")
            entry["replacement"] = replacement["resref"]
        if kind == "are":
            pristine.add(holder)
        claimed.update(ids)
        native["duplicates"].append(entry)

    for copy in copies or ():
        attempt(copy["source_id"], compile_copy, copy)

    # The Sahuagin treasury's place: its container and script (require_treasury_names).
    treasuries: dict[str, tuple[dict, str]] = {}

    def compile_target(selected: dict) -> None:
        target, identity = selected["record"], selected["id"]
        authored = target.get("adapter_id") == "original-target-v1"
        container_quest = container_target_spec(target["selector"]) if authored else None
        # The extended reward route's place this row is: its own, or a preset's row there.
        route = mode2_route_target(target["selector"]) if authored else None
        _require((authored or index["targets"].get(identity) == target) and
                 (index["resources"][target["resource"]]["sha256"] == selected["resource_sha256"] or
                  (route is not None and target["resource"] in MODE2_RESOURCES)),
                 f"Selected target differs from the scan: {identity}")
        if authored:
            selector = target["selector"]
            use(target["resource"])
            reward = mode2_target_spec(route, **naming()) if route is not None else None
            if reward:
                require_route(MODE2_TARGETS[route])
            if reward and reward.get("inventory_target"):
                placement = reward["inventory_target"]
                readable(placement["creature_resource"])
                readable("are:" + placement["area"].lower())
                creature = index["targets"].get(placement["creature_resource"])
                _require(creature is not None and
                         creature["selector"]["script_name"].lower() == placement["script_name"].lower() and
                         not {DANGLING_INVENTORY, INVALID_INVENTORY, UNRESOLVED_STORAGE}.intersection(creature["concerns"]),
                         f"Original reward recipient changed: {identity}")
                for resource in route_resources(reward):
                    use_route(resource)
                for resource in (placement["creature_resource"], "are:" + placement["area"].lower()):
                    use(resource)
                native["targets"][identity] = {
                    "kind": "creature", "area": placement["area"].upper(),
                    "script_name": _name(placement["script_name"], identity),
                    "inventory_reward": True, "reward_resource": reward["resource"],
                    "identity": _digest({"target": selected, "creature": creature})}
                if "original-target:" + route != identity:
                    # A preset's row: the reward reply names the route's own row.
                    native["targets"][identity]["route_target"] = "original-target:" + route
                return
            if target["kind"] in ("group", "creature-target"):
                resources = selector["members"] if target["kind"] == "group" else [selector["creature_resource"]]
                members = []
                for resource in resources:
                    if resource not in index["resources"] or resource in index["unreadable"]:
                        # Original group tables also filter absent CREs; one the
                        # scan could not read cannot be addressed either.
                        continue
                    creature = index["targets"][resource]
                    _require(not {DANGLING_INVENTORY, INVALID_INVENTORY, UNRESOLVED_STORAGE}.intersection(creature["concerns"]),
                             f"Original recipient has inventory diagnostics: {resource}")
                    name = _name(creature["selector"]["script_name"], identity)
                    use(resource)
                    members.append({"resref": resource[4:], "script_name": name})
                _require(bool(members), f"Original recipient has no installed member: {identity}")
                endpoint = {"kind": "group" if target["kind"] == "group" else "creature",
                            "area": _area(target["resource"]), "identity": _digest({"target": selected})}
                if target["kind"] == "group":
                    endpoint["members"] = members
                else:
                    endpoint["script_name"] = members[0]["script_name"]
                native["targets"][identity] = endpoint
                return
            if target["kind"] == "scripted-target":
                if route is not None:
                    spec = mode2_target_spec(route, **naming())
                    for resource in route_resources(spec):
                        use_route(resource)
                    if spec.get("handover"):
                        # The Sahuagin treasury: the one container of its area
                        # whose script it is, wherever a mod moved or renamed it.
                        handover = spec["handover"]
                        area = "are:" + handover["area"]
                        use(area)
                        found = [row for row in index["targets"].values() if row["resource"] == area and
                                 row["kind"] == "container" and row["selector"]["trap_script"] == handover["script"]]
                        _require(len(found) == 1, f"{area[4:].upper()}.ARE needs one container with the script "
                                                  f"{handover['script'].upper()}, the treasury: {identity}")
                        selection = found[0]["selector"]
                        name = _name(selection["name"], identity)
                        _require(not selection["dynamic_item_table"] and
                                 container_names[area, name.lower(), selection["x"], selection["y"],
                                                 selection["type"]] == 1,
                                 f"Native target needs a unique static container name: {identity}")
                        _require(0 <= selection["x"] <= 32767 and 0 <= selection["y"] <= 32767,
                                 f"Native container coordinates are unsupported: {identity}")
                        treasuries[identity] = (found[0], handover["script"])
                        native["targets"][identity] = {
                            "kind": "container", "area": _area(area), "script_name": name,
                            "x": selection["x"], "y": selection["y"], "container_type": selection["type"],
                            "handover": {"event_global": spec["event_global"],
                                         "recipient_local": spec["recipient_local"]},
                            "reward_resource": spec["resource"],
                            "identity": _digest({"target": selected, "container": found[0]})}
                        if "original-target:" + route != identity:
                            # A preset's row: the reward reply names the route's own row.
                            native["targets"][identity]["route_target"] = "original-target:" + route
                    elif spec["recipient_local"] is None:
                        readable(spec["recipient"].split("/", 1)[0])
                        physical = index["targets"].get(spec["recipient"])
                        _require(physical is not None and physical["kind"] == "container" and
                                 physical["selector"]["trap_script"] == spec["quest"],
                                 f"The extended reward container changed: {identity}")
                        selection = physical["selector"]
                        use(physical["resource"])
                        native["targets"][identity] = {
                            "kind": "container", "area": _area(physical["resource"]),
                            "script_name": _name(selection["name"], identity),
                            "x": selection["x"], "y": selection["y"], "container_type": selection["type"],
                            "claim_global": spec["event_global"], "reward_resource": spec["resource"],
                            "identity": _digest({"target": selected, "container": physical})}
                    elif spec.get("holder"):
                        # The Sahuagin king's place, Tolgerias's: the reward
                        # reply pays it; until then it waits for the holder's
                        # death, at his corpse. Each version of the holder the
                        # area can have must carry the name the runtime finds
                        # him by.
                        holder = spec["holder"]
                        area = "are:" + holder["area"]
                        use(area)
                        creatures = []
                        for resource in holder["creature_resources"]:
                            use(resource)
                            creature = index["targets"].get(resource)
                            _require(creature is not None and
                                     creature["selector"]["script_name"].lower() == holder["script_name"],
                                     f"Original reward recipient changed: {identity}")
                            creatures.append(creature)
                        native["targets"][identity] = {
                            "kind": "creature", "area": _area(area),
                            "script_name": _name(holder["script_name"], identity), "corpse_only": True,
                            "handover": {"event_global": spec["event_global"],
                                         "recipient_local": spec["recipient_local"]},
                            "reward_resource": spec["resource"],
                            "identity": _digest({"target": selected, "creatures": creatures})}
                    else:
                        native["targets"][identity] = {
                            "kind": "quest", "area": "*", "adapter_id": "mode2-reward-v1",
                            "resource": spec["resource"], "event_global": spec["event_global"],
                            "recipient_local": spec["recipient_local"], "identity": _digest({"target": selected})}
                    return
                if target["resource"] == SPELL["resource"]:
                    _require(identity == "original-target:bg2:mode2:target:70" and
                             selected["resource_sha256"] in (KEY_ORIGINAL_HASH, KEY_PATCHED_HASH),
                             f"Unreviewed spell recipient: {identity}")
                    native["targets"][identity] = {
                        "kind": "quest", "area": "*", "resource": SPELL["resource"],
                        "event_global": SPELL["event_global"], "recipient_local": SPELL["recipient_local"],
                        "identity": _digest({"target": selected})}
                    native["resource_hashes"]["SPIN621.SPL"] = KEY_PATCHED_HASH
                    return
                name = selector["target"].removesuffix(".bcs")
                _require(name in QUESTS, f"Unreviewed original quest recipient: {identity}")
                _require(applied(world["game_root"], QUEST_CHANGES[name]),
                         f"Chriz Item Randomiser did not prepare this quest reward; reinstall it, then rescan: {identity}")
                use_quest("bcs:" + name)
                native["targets"][identity] = {"kind": "quest", "area": _area(target["resource"]),
                    "resource": "bcs:" + name, "event_global": QUESTS[name]["event_global"],
                    "identity": _digest({"target": selected})}
                return
            physical = [index["targets"][key] for key in selector["target_ids"] if key in index["targets"]]
            if container_quest:
                quest = container_quest["target_id"]
                if stored:
                    # Its area is unchanged (checked above): keep the container
                    # it was exported with, even where a better rule now finds
                    # another one (Samia's chest before the EE renaming).
                    area = "are:" + treasure_area(quest, game_platform(world))
                    _require(len(physical) == 1 and physical[0]["kind"] == "container" and
                             physical[0]["resource"] == area,
                             f"Original container quest binding changed: {identity}")
                else:
                    # The container that holds the quest's treasure in this
                    # scan, found as the template binds it.
                    try:
                        chest = treasure_container(quest, index["sources"].values(), game_platform(world))
                    except ProfileError as exc:
                        raise container_error(quest, str(exc)) from None
                    _require([target["id"] for target in physical] == [chest],
                             f"Original container quest binding changed: {identity}")
                require_container(quest)
                for resource in container_quest["resources"]:
                    use(resource)
            _require(len(physical) == 1, f"Original recipient needs one physical binding: {identity}")
            target = physical[0]
        if target["kind"] == "store":
            _require(target["selector"]["flags"] & 1,
                     f"Experimental native store target does not allow the player to buy: {identity}")
            use(target["resource"])
            native["targets"][identity] = {"kind": "store", "store": target["resource"][4:],
                                           "identity": _digest({"target": selected})}
            return
        if target["kind"] == "container":
            selector = target["selector"]
            name = _name(selector["name"], identity)
            _require(not selector["dynamic_item_table"] and
                     container_names[target["resource"], name.lower(), selector["x"], selector["y"], selector["type"]] == 1,
                     f"Native target needs a unique static container name: {identity}")
            _require(0 <= selector["x"] <= 32767 and 0 <= selector["y"] <= 32767,
                     f"Native container coordinates are unsupported: {identity}")
            use(target["resource"])
            native["targets"][identity] = {
                "kind": "container", "area": _area(target["resource"]),
                "script_name": name, "x": selector["x"], "y": selector["y"],
                "container_type": selector["type"],
                "identity": _digest({"target": selected})}
            if container_quest:
                native["targets"][identity]["loot_latch"] = {
                    key: container_quest[key] for key in
                    ("activation_global", "latch_global", "latch_scope")}
            return
        _require(target["kind"] == "actor", f"Native export needs an ARE actor, container or store target: {identity}")
        name = _name(actor_name(target), identity)
        _require(actor_names[target["resource"], name.lower()] == 1,
                 f"Native target needs a unique actor script name in its area: {identity}")
        template = target["selector"].get("template_resource")
        use(target["resource"])
        if template:
            use(template)
        native["targets"][identity] = {"kind": "creature", "area": _area(target["resource"]),
            "script_name": name, "identity": _digest({"target": selected,
                "template": index["resources"].get(template)})}

    for selected in manifest["targets"]:
        attempt(selected["id"], compile_target, selected)
    # The treasury's rewards, once every place in its container is known.
    for identity, (treasury, script) in treasuries.items():
        if identity in native["targets"]:
            attempt(identity, require_treasury_names, identity, treasury, script)

    guarded_ids = [s["id"] for s in manifest["sources"] if s["record"]["kind"] != "virtual"]
    if guarded_ids or shield or "cursed_disguises" in manifest or native.get("duplicates"):
        native["source_gate"] = {"schema": "flpr-unvisited-fixture-gate-v1", "source_ids": guarded_ids}
    options = manifest.get("original_options", {})
    if options.get("kangaxx"):
        for resource in KANGAXX_RESOURCES:
            use(resource)
    if options.get("flail_heads"):
        use("dlg:kpglai01")
    return native, required, pristine


def _export_tlk(installed: bytes, prepared: Path) -> bytes:
    """Keep text references already used by saved, exported configurations."""
    if not prepared.exists():
        return installed
    previous = prepared.read_bytes()
    def extends(new: bytes, old: bytes) -> bool:
        a, b = _Tlk(new), _Tlk(old)
        return (a.count >= b.count and new[:10] == old[:10] and
                new[18:18 + b.count * 26] == old[18:18 + b.count * 26] and
                new[a.offset:a.offset + len(old) - b.offset] == old[b.offset:])
    if extends(installed, previous):
        return installed
    _require(extends(previous, installed), "Game text changed since export; rescan into a fresh export directory")
    return previous


_CHECKED = "the runtime checks it at every start and would stop for every game"


def _stored_native(exported: Path, key: str) -> dict:
    return json.loads((exported / "revisions" / f"{key}.native.json").read_text(encoding="utf-8"))


def _staged_scripts(key: str, manifest: dict, native: dict) -> list[tuple[str, str, bool]]:
    """(resource, staged file, quest) for each file a revision's guarded scripts need in override.

    At every start the runtime compares these staged bytes with the game's
    override file (FLPRScript.lua). A quest script that the installer
    prepared (<quest>) may also be the version Apply extended it to
    (_extended_scripts).
    """
    result = []
    for number, source in enumerate(manifest["sources"], 1):
        endpoint = native["sources"].get(source["id"], {})
        if endpoint.get("kind") != "guarded-script":
            continue
        base = f"campaign-store/revisions/{key}.source-{number}"
        if endpoint.get("resources"):
            result += [(resource, f"{base}.route-{route}.patched.{resource.split(':')[0]}", False)
                       for route, resource in enumerate(endpoint["resources"], 1)]
        else:
            resource = source["record"]["resource"]
            result.append((resource, f"{base}.patched.{resource.split(':')[0]}",
                           endpoint.get("adapter_id") == "original-quest-v1"))
    return result


def _extended_scripts(resource: str) -> tuple[str, str]:
    """The files that keep an installer-prepared quest script and the version
    Apply extended it to with reward routes (read by M_FLPR.lua)."""
    name = resource.split(":")[1].upper()
    return f"{name}.installed.bcs", f"{name}.applied.bcs"


def _gate_areas(native: dict) -> set[str]:
    """The areas whose shared pristine copy the runtime compares with a revision's record (M_FLPR.lua)."""
    gate = native.get("source_gate") or {}
    ids = gate.get("source_ids") or ([gate["source_id"]] if gate.get("source_id") else [])
    return {native["sources"][identity]["area"].upper() for identity in ids
            if native["sources"][identity]["kind"] not in ("guarded-template", "guarded-script", "guarded-store") and
            native["sources"][identity].get("area")} | {
        copy["resource"][4:].upper() for copy in native.get("duplicates", ()) if copy["resource"].startswith("are:")}


def _filename(resource: str) -> str:
    kind, name = resource.split(":", 1)
    return name.upper() + "." + kind.upper()


def export_native(store: Path, catalog: dict, world: dict, output: Path,
                  revision: str | None = None, *, starts: dict[str, str] | None = None,
                  main: str | None = None, scans: Scans | None = None) -> dict:
    """Export next-run settings while retaining already exported saved revisions.

    <starts> names the revision of each campaign start (starts.write_starts)
    and <main> the start the engine makes without a campaign row; next.lua
    then lists them. Without <starts>, next.lua names <revision> (the store's
    current one by default), which the runtime reads as a BG1 start.

    A revision exported before that this export does not select belongs to
    running saves; its files never change. When the game changed so that one
    of its rows can no longer be prepared, or its files would now differ, it
    keeps the files it was exported with and the result's "notes" say so; the
    other revisions are exported as usual. The runtime checks every listed
    revision at each start, so a kept revision whose guarded script or
    guarded area changed in the game stops the export instead. A selected
    revision must be prepared completely. ``scans`` holds the catalogues
    already validated.
    """
    if scans is None:
        items, index = validate_catalog(catalog), validate_world(world, catalog)
    else:
        items, index = scans.indexes(catalog, world)
    output = ensure_output_outside_game(output, catalog)
    if starts is not None:
        _require(isinstance(starts, dict) and starts and revision is None and
                 (main is None or main in starts), "Invalid campaign start revisions")
        selected_starts = {start: read_campaign(store, key) for start, key in starts.items()}
    else:
        _require(main is None, "A main start needs campaign start revisions")
        selected = read_campaign(store, revision)
        selected_starts = {None: selected}
    exported = output / "campaign-store"
    manifests = {p.stem: read_campaign(exported, p.stem)
                 for p in (exported / "revisions").glob("*.json") if HEX256.fullmatch(p.stem)}
    for manifest in selected_starts.values():
        manifests[manifest["revision"]] = manifest
    selected_keys = {manifest["revision"] for manifest in selected_starts.values()}
    stored = {key for key in manifests if key not in selected_keys and
              (exported / "revisions" / f"{key}.native.json").is_file()}
    pending, required, pristine, compiled = {}, set(), set(), {}
    # The resources each revision's reward routes patch, and the quest scripts
    # it needs as the installer prepared them.
    routed_by, quests_by = {}, {}
    # The data of each pending Lua artifact, for the refused rendering it replaces.
    lua_values = {}
    notes, kept = [], set()
    for key, manifest in sorted(manifests.items()):
        problems = {} if key in stored else None
        routed_by[key], quests_by[key] = set(), set()
        native, resources, areas = _compile(manifest, world, index, items, problems, catalog=catalog,
                                            stored=key in stored, notes=notes, routed=routed_by[key],
                                            quests=quests_by[key])
        compiled[key] = native
        required.update(resources); pristine.update(areas)
        if problems:
            # The other rows of its saves still need their prepared resources.
            kept.add(key)
            notes += [f"Saved games of revision {key[:12]} keep their settings, but {identity} may no longer "
                      f"work in them: {reason}" for identity, reason in sorted(problems.items())]
            continue
        base = "campaign-store/revisions/" + key
        pending[base + ".json"] = canonical_bytes(manifest)
        pending[base + ".lua"] = _render_lua(manifest)
        lua_values[base + ".lua"] = manifest
    routed, quest_scripts = set().union(*routed_by.values()), set().union(*quests_by.values())
    # The quest routes' items as the game names them (EET renames BG1's).
    naming = {}
    if required & MODE2_RESOURCES.keys():
        checked = reward_routes(catalog, world)
        naming = {"game": checked.game, "names": checked.names}
    # Reuse the scanner's resource reader; only selected resources are demanded.
    # The quest scripts as the installer prepared them, before any route's patch.
    resource_bytes, installed_scripts = {}, {}
    with GameResources(Path(catalog["game_root"]), {**{k: RESOURCE_TYPES[k] for k in ("itm", "are", "cre", "sto", "bcs", "dlg", "spl")},
                                                    "lua": RESOURCE_TYPES["lua"]}) as reader:
        # The EE journal table: EET installs it in override, BG:EE and BG2:EE
        # keep it in their archives.
        journal = reader.read("lua", "bgee") if "bgee" in reader.names("lua") else None
        for resource in sorted(required):
            kind, name = resource.split(":", 1)
            data = reader.read(kind, name)
            actual_hash = hashlib.sha256(data).hexdigest()
            expected_hash = index["resources"][resource]["sha256"]
            own_patch = False
            if actual_hash != expected_hash:
                recover = {"dlg:hlskull": lambda value: original_dialogue("hlskull.dlg", value),
                           "bcs:crypt526": lambda value: original_crypt("crypt526.bcs", value),
                           "bcs:crypt202": lambda value: original_crypt("crypt202.bcs", value)}.get(resource)
                if recover:
                    own_patch = hashlib.sha256(recover(data)).hexdigest() == expected_hash
                if resource == SPELL["resource"]:
                    own_patch = expected_hash == KEY_ORIGINAL_HASH and actual_hash == KEY_PATCHED_HASH
                # A reward route is recognised again from these bytes below;
                # the scan holds nothing else that depends on them.
                if resource in MODE2_RESOURCES:
                    own_patch = own_mode2_patch(resource, data, **naming)
                if resource in CONTAINER_RESOURCES:
                    own_patch = own_container_patch(resource, data)
            _require(actual_hash == expected_hash or own_patch,
                     f"Installed resource changed; rescan before export: {resource}")
            # The installer changed these files when it was installed; the
            # export only checks that they still carry its changes. Apply's
            # reward routes keep them (the book's scrolls).
            if resource in quest_scripts:
                require_installed(catalog["game_root"], QUEST_CHANGES[name], data)
                installed_scripts[resource] = (unpatched_script(resource, data, **naming)
                                               if resource in MODE2_RESOURCES else data)
            elif resource == "dlg:unshey":
                require_installed(catalog["game_root"], "unshey", data)
            elif resource == "dlg:kpglai01":
                require_installed(catalog["game_root"], "flail_heads", data)
            resource_bytes[resource] = data
            if resource in pristine:
                pending[name.upper() + ".pristine.are"] = data
        # The quest routes' reward replies that other dialogues copy are gated
        # there too; the dialogues they read must be as scanned.
        dialogues = set(reader.names("dlg"))
        copies = RewardCopies(lambda name: reader.read("dlg", name) if name in dialogues else None,
                              {resource: row["sha256"] for resource, row in index["resources"].items()}, **naming)
        routes = sorted({key for resource in routed & MODE2_RESOURCES.keys() for key in MODE2_RESOURCES[resource]})
        changed = sorted(set().union(*(copies.checked(key) for key in routes)) & copies.changed)
        _require(not changed, f"Installed resource changed; rescan before export: {changed[0] if changed else ''}")
        # What the runtime finds in override for the guarded scripts of the
        # revisions exported before; checked below for those that are kept.
        live = {}
        for key in sorted(stored):
            for resource, _, _ in _staged_scripts(key, manifests[key], _stored_native(exported, key)):
                kind, name = resource.split(":", 1)
                live[resource] = (reader.read(kind, name) if name in reader.names(kind) and
                                  reader.origin(kind, name) == "override" else None)
    kangaxx = any(m.get("original_options", {}).get("kangaxx") for m in manifests.values())
    cursed = any("cursed_disguises" in m for m in manifests.values())
    reward_texts = {}
    container_texts = {}
    text_rewards = required & MODE2_TEXT_REFS.keys()
    container_text_resources = required & CONTAINER_TEXT_RESOURCES
    if kangaxx or cursed or text_rewards or container_text_resources:
        root = Path(catalog["game_root"])
        language = catalog["language"]
        male, female = text_files(root, language)
        tlk_relative = male.relative_to(root)
        tlk = _export_tlk(male.read_bytes(), output / "game-overrides" / tlk_relative)
        # The language's dialogf.tlk gets every appended text under the same
        # number. Where it holds another number of strings than dialog.tlk, the
        # configuration check left the reward text routes out, and other
        # appended texts leave it as it is.
        reason = unpaired(male, female)
        _require(not (reason and (text_rewards or container_text_resources)),
                 f"Item Randomiser's reward texts cannot be prepared: {reason}")
        female_relative = None if female is None or reason else female.relative_to(root)
        female_tlk = (None if female_relative is None else
                      _export_tlk(female.read_bytes(), output / "game-overrides" / female_relative))
        try:
            text = paired(tlk, female_tlk)
        except GameTextError:
            raise ProfileError("Game text changed since export; rescan into a fresh export directory") from None
        _require(journal is not None, "The game has no BGEE.LUA journal table")
        patches, text = prepare_quest_options(
            {resource[4:] + "." + resource[:3]: data for resource, data in resource_bytes.items()},
            text, kangaxx=kangaxx, language=language)
        if kangaxx:
            journal = patch_kangaxx_journal(journal, patches["hlskull.dlg"])
        if text_rewards:
            text, reward_texts = prepare_reward_texts(text, text_rewards, language, game=naming.get("game"))
            journal = mode2_journal_lua(journal, reward_texts, naming.get("game"))
        if container_text_resources:
            text, container_texts = prepare_container_texts(text, container_text_resources, language)
        for revision, manifest in sorted(manifests.items()):
            if "cursed_disguises" not in manifest:
                continue
            fixture, assets, text = prepare_cursed_disguises(manifest["cursed_disguises"],
                {name[4:]: data for name, data in resource_bytes.items() if name.startswith("itm:")},
                text, language=language)
            compiled[revision]["cursed_disguises"] = fixture
            for name, data in assets.items():
                pending[f"campaign-store/revisions/{revision}.cursed/{name}"] = data
        pending["resource-overrides/BGEE.LUA"] = journal
        pending["game-overrides/" + tlk_relative.as_posix()] = text.male
        if female_relative is not None:
            pending["game-overrides/" + female_relative.as_posix()] = text.female
        for name, data in patches.items():
            pending["resource-overrides/" + name.upper()] = data
        for native in compiled.values():
            for name, data in patches.items():
                if name.upper() in native["resource_hashes"]:
                    native["resource_hashes"][name.upper()] = hashlib.sha256(data).hexdigest()

    # Each reward route patch reads campaign flags. Prepare it once,
    # retaining the same bytes for every revision that shares the resource.
    # A quest script that the installer prepared (the book's) is written only
    # when a revision's routes patch it. Revisions that need the installer's
    # version still record that one: the runtime also accepts the version
    # Apply extended it to, which Apply keeps beside it (_extended_scripts).
    mode2_patches = {}
    for resource in MODE2_RESOURCES:
        if resource not in routed:
            continue
        patched = patch_mode2_reward(resource, resource_bytes[resource], refs=reward_texts.get(resource), **naming)
        mode2_patches[resource] = patched
        kind, name = resource.split(":")
        filename = name.upper() + "." + kind.upper()
        pending["resource-overrides/" + filename] = patched
        for key, native in compiled.items():
            if filename in native["resource_hashes"] and resource in routed_by[key]:
                native["resource_hashes"][filename] = hashlib.sha256(patched).hexdigest()
        if resource in quest_scripts:
            installed, applied = _extended_scripts(resource)
            pending[installed], pending[applied] = installed_scripts[resource], patched
    # A revision that needs only the installer's version of a quest script
    # records that version, whichever the game has.
    for key, native in compiled.items():
        for resource in quests_by[key] - routed_by[key]:
            native["resource_hashes"][_filename(resource)] = hashlib.sha256(installed_scripts[resource]).hexdigest()

    for resource, patched in copies.patches(routes).items():
        filename = resource[4:].upper() + ".DLG"
        _require("resource-overrides/" + filename not in pending,
                 f"Two reward changes would replace the same dialogue: {filename}")
        pending["resource-overrides/" + filename] = patched

    for resource in sorted(required & CONTAINER_RESOURCES.keys()):
        patched = patch_container_resource(resource, resource_bytes[resource], refs=container_texts.get(resource))
        kind, name = resource.split(":")
        filename = name.upper() + "." + kind.upper()
        pending["resource-overrides/" + filename] = patched
        for native in compiled.values():
            if filename in native["resource_hashes"]:
                native["resource_hashes"][filename] = hashlib.sha256(patched).hexdigest()

    for key, manifest in sorted(manifests.items()):
        if key in kept:
            continue
        native = compiled[key]
        shield = manifest.get("spell_shield")
        if shield:
            prefix = f"campaign-store/revisions/{key}.spell-shield"
            original = resource_bytes[shield["resource"]]
            effective = patch_spell_shield_stock(original)
            pending[prefix + ".original.sto"] = original
            pending[prefix + ".effective.sto"] = effective
        for endpoint in native["targets"].values():
            mode2_resource = (endpoint.get("resource") if endpoint.get("adapter_id") == "mode2-reward-v1"
                              else endpoint.get("reward_resource"))
            if mode2_resource:
                _require(mode2_resource in mode2_patches, "The reward route's recipient patch is missing")
            if endpoint["kind"] == "quest":
                resource = endpoint["resource"]
                # An original quest's script was prepared by the installer.
                if mode2_resource:
                    pass  # The original grant has just been staged above.
                elif resource == SPELL["resource"]:
                    pending["resource-overrides/SPIN621.SPL"] = patch_key_spell(resource_bytes[resource])
            if endpoint["kind"] == "group":
                from .group_recipients import preferred_members
                creatures = {member["resref"]: resource_bytes["cre:" + member["resref"]]
                             for member in endpoint["members"]}
                endpoint["eligible"] = {}
                reward_items = {s["record"]["item"] for s in manifest["sources"]}
                for rule in manifest.get("randomization", {}).get("sources", {}).values():
                    reward_items.update(rule.get("reward_variants", []))
                for item in sorted(reward_items):
                    preferred = preferred_members(resource_bytes["itm:" + item], creatures)
                    endpoint["eligible"][item] = [n for n, member in enumerate(endpoint["members"], 1)
                                                 if member["resref"] in preferred]
        base = "campaign-store/revisions/" + key
        pending[base + ".native.json"] = canonical_bytes(native)
        pending[base + ".native.lua"] = _render_lua(native)
        lua_values[base + ".native.lua"] = native
        # The creature or shop of each second copy as the game has it; an
        # area's is its shared pristine copy.
        for number, copy in enumerate(native.get("duplicates", ()), 1):
            kind = copy["resource"].split(":", 1)[0]
            if kind != "are":
                pending[f"{base}.duplicate-{number}.original.{kind}"] = resource_bytes[copy["resource"]]
        for source_index, selected_source in enumerate(manifest["sources"], 1):
            source = selected_source["record"]
            compiled_source = compiled[key]["sources"][selected_source["id"]]
            base = f"campaign-store/revisions/{key}.source-{source_index}"
            for number, auxiliary in enumerate(compiled_source.get("auxiliary_creatures", ()), 1):
                pending[base + f".duplicate-{number}.original.cre"] = resource_bytes[auxiliary["resource"]]
            if compiled_source["kind"] == "guarded-script":
                raw = resource_bytes[source["resource"]]
                if compiled_source["adapter_id"] == "mode2-reward-v1":
                    kind, name = source["resource"].split(":")
                    patched = mode2_patches[source["resource"]]
                    pending[base + ".patched." + kind] = patched
                    for number, resource in enumerate(compiled_source.get("resources", ()), 1):
                        extension = resource.split(":")[0]
                        pending[base + f".route-{number}.patched." + extension] = mode2_patches[resource]
                    pending["resource-overrides/" + name.upper() + "." + kind.upper()] = patched
                    continue
                if compiled_source["adapter_id"] == "spell-key-v1":
                    patched = patch_key_spell(raw)
                    pending[base + ".patched.spl"] = patched
                    pending["resource-overrides/SPIN621.SPL"] = patched
                    continue
                # The installer's script: the runtime checks that the
                # game still has exactly these bytes, or the version Apply
                # extended them to (_extended_scripts).
                pending[base + ".patched.bcs"] = installed_scripts.get(source["resource"], raw)
                continue
            if compiled_source["kind"] == "guarded-store":
                original = resource_bytes[source["resource"]]
                if compiled_source.get("stock_option") == "more_spell_shield":
                    original = patch_spell_shield_stock(original)
                pending[base + ".original.sto"] = original
                pending[base + ".removed.sto"] = rewrite_store(original, {compiled_source["record_index"]})
                continue
            if compiled_source["kind"] == "guarded-template":
                pending[base + ".original.cre"] = resource_bytes[source["resource"]]
                continue
            if compiled_source["kind"] != "guarded-creature":
                continue
            resource = source["resource"]
            if source["kind"] == "cre-template":
                original = resource_bytes[resource]
            else:
                selector = index["targets"][compiled_source["actor_owner"]]["selector"]
                offset, size = selector["embedded_cre_offset"], selector["embedded_cre_size"]
                original = resource_bytes[resource][offset:offset + size]
            pending[base + ".original.cre"] = original
            replacement = selected_source.get("replacement", {}).get("resref", "blank")
            pending[base + ".removed.cre"] = rewrite_inventory(
                original, {source["record"]["record_index"]:
                           None if replacement == "blank" else replacement})
    def replaceable(relative: str):
        # A refused Lua rendering holds the same data as the current one.
        return (lambda value=lua_values[relative]: _refused_lua(value)) if relative in lua_values else None

    def changed(relative: str) -> bool:
        path = _store_path(output, Path(relative))
        if not path.is_file():
            return False
        existing, earlier = path.read_bytes(), replaceable(relative)
        return existing != pending[relative] and (earlier is None or existing != earlier())

    # Where the game changed so that a revision exported earlier would now get
    # other files, its saves keep the files they were exported with.
    for key in sorted(stored - kept):
        prefix = f"campaign-store/revisions/{key}."
        own = [relative for relative in pending if relative.startswith(prefix)]
        if not any(changed(relative) for relative in own):
            continue
        kept.add(key)
        for relative in own:
            del pending[relative]
        before = _stored_native(exported, key)
        hashes = before.get("resource_hashes", {}), compiled[key]["resource_hashes"]
        changed = sorted(name for name in {*hashes[0], *hashes[1]} if hashes[0].get(name) != hashes[1].get(name))
        notes.append(f"Saved games of revision {key[:12]} keep the settings exported earlier, although "
                     f"{', '.join(changed) or 'game files they use'} changed since then")
    # At every start the runtime checks each listed revision: the staged bytes
    # of its guarded scripts against the game's override files, and the area
    # each guarded area source recorded against the shared pristine copy. A
    # kept revision that would fail there stops the runtime for every game.
    for key in sorted(kept):
        before = _stored_native(exported, key)
        # (file, as the game has it, as Apply would leave it, as the revision
        # needs it, and the installer's and Apply's versions that also meet a
        # quest script's need)
        needed = []
        for resource, staged, quest in _staged_scripts(key, manifests[key], before):
            filename = _filename(resource)
            extended = tuple(pending.get(name) for name in _extended_scripts(resource)) if quest else (None, None)
            needed.append((filename, live.get(resource), pending.get("resource-overrides/" + filename, live.get(resource)),
                           _store_path(output, Path(staged)).read_bytes(), extended))
        for area in sorted(_gate_areas(before)):
            # The pristine copy is the game's area as Apply prepares it now.
            copy = pending.get(area + ".pristine.are")
            if copy is None and _store_path(output, Path(area + ".pristine.are")).is_file():
                copy = _store_path(output, Path(area + ".pristine.are")).read_bytes()
            digest = copy and hashlib.sha256(copy).hexdigest()
            needed.append((area + ".ARE", digest, digest, before["resource_hashes"].get(area + ".ARE"), (None, None)))
        for filename, current, installed, expected, (base, extended) in needed:
            def meets(version, expected=expected, base=base, extended=extended):
                return version == expected or (base is not None and base == expected and version == extended)
            if not meets(current):
                change = f"it changed in the game since then; {_CHECKED}. Restore {filename} before applying"
            else:
                change = f"Apply would now replace it with another version; {_CHECKED}"
            _require(meets(installed), f"Saved games of revision {key[:12]} need {filename} as it was when "
                                       f"they were exported, but {change}")
    for relative, data in pending.items():
        path = _store_path(output, Path(relative))
        if relative.startswith("campaign-store/"):
            _write_immutable(path, data, replaceable(relative))
        else:
            # These are the prepared common resources for all exported
            # revisions; adding a compatible quest branch or TLK entry can
            # update them without altering any campaign's assignments.
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    revisions = sorted(manifests)
    listed = set(pending)
    for key in kept:
        # Its files stay as they were exported: list them, and read its gate.
        compiled[key] = _stored_native(exported, key)
        for path in (exported / "revisions").iterdir():
            if path.name.startswith(key + "."):
                files = [path] if path.is_file() else [inner for inner in path.rglob("*") if inner.is_file()]
                listed.update(inner.relative_to(output).as_posix() for inner in files)
    guarded_revisions = [key for key in revisions if "source_gate" in compiled[key] or "spell_shield" in compiled[key]]
    _store_path(output, Path("source-gate.lua")).write_bytes(_render_lua(guarded_revisions))
    if starts is None:
        pointer = selected["revision"]
    else:
        pointer = {"schema": NEXT_SCHEMA, "starts": dict(sorted(starts.items()))}
        if main is not None:
            pointer["main"] = main
    _store_path(output, Path("next.lua")).write_bytes(_render_lua(pointer))
    _store_path(output, Path(EXPORT_FILES)).write_bytes(canonical_bytes(
        {"schema": EXPORT_FILES_SCHEMA, "files": sorted({*listed, "source-gate.lua", "next.lua"})}))
    result = {"revisions": revisions, "config_path": str(output), "state": "experimental-native-config"}
    if notes:
        result["notes"] = sorted(set(notes))
    if starts is None:
        result["revision"] = selected["revision"]
    else:
        result["starts"] = dict(sorted(starts.items()))
    return result
