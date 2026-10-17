"""Which extended reward routes a game can prepare, read once per scan.

The configuration check and Apply run the same recognition
(mode2_rewards.route_reasons, container_rewards.route_reasons) on the game's
current bytes. A route the check accepts is one the export can prepare; one it
cannot prepare is left out of the configuration with its reason instead of
failing at Apply.

A quest route needs its grant recognised in each of its resources, and so does
every route that shares its reward event (the egg deal's two dialogues, the
three sculptors). Routes with Item Randomiser's reward text need the English
game text and the BGEE.LUA journal table, as the export does. Reading the
resources once per scan keeps the repeated checks of a fit cheap; Apply still
reads and verifies the bytes it patches.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from . import container_rewards, mode2_rewards
from .profiles import ProfileError
from .resources import GameResources, RESOURCE_TYPES


class ChangedSinceScan(ProfileError):
    """A reward resource changed since the scan; the check stops instead of leaving rows out."""


_CACHE: dict[tuple[str, str, str], "RewardRoutes"] = {}


def reward_routes(catalog: dict, world: dict) -> "RewardRoutes":
    """The reward routes of the scanned game, read on first use per scan."""
    key = (catalog["game_root"], catalog["language"], world["fingerprint"])
    if key not in _CACHE:
        if len(_CACHE) >= 4:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = RewardRoutes(Path(catalog["game_root"]), catalog["language"], world)
    return _CACHE[key]


class RewardRoutes:
    def __init__(self, game_root: Path, language: str, world: dict):
        scanned = {row["id"]: row["sha256"] for row in world["resources"]}
        resources = sorted(resource for resource in
                           {*mode2_rewards.RESOURCE_REWARDS, *container_rewards.RESOURCE_TARGETS}
                           if resource in scanned)
        self.english = language.lower() == "en_us"
        self.changed: set[str] = set()
        self.reasons: dict[tuple[str, str], str] = {}
        with GameResources(game_root, {kind: RESOURCE_TYPES[kind] for kind in ("bcs", "dlg", "lua")}) as reader:
            self.journal = "bgee" in reader.names("lua")
            present = {kind: set(reader.names(kind)) for kind in ("bcs", "dlg")}
            for resource in resources:
                kind, name = resource.split(":")
                if name not in present[kind]:
                    self.changed.add(resource)
                    continue
                data = reader.read(kind, name)
                adapter = mode2_rewards if resource in mode2_rewards.RESOURCE_REWARDS else container_rewards
                try:
                    own = adapter.own_patch(resource, data)
                except ProfileError:
                    own = True  # A changed copy of our patch: route_reasons names it.
                if not own and hashlib.sha256(data).hexdigest() != scanned[resource]:
                    self.changed.add(resource)
                    continue
                for key, reason in adapter.route_reasons(resource, data).items():
                    self.reasons[resource, key] = reason

    def _unchanged(self, resources) -> None:
        for resource in resources:
            if resource in self.changed:
                raise ChangedSinceScan(f"Installed resource changed; rescan before export: {resource}")

    def _texts(self, needed: bool, prefix: str) -> None:
        if needed and not self.english:
            raise ProfileError(prefix + "Item Randomiser's reward texts exist only in English")
        if needed and not self.journal:
            raise ProfileError(prefix + "the game has no BGEE.LUA journal table")

    def require_route(self, key: str) -> None:
        """Raise unless the quest route <key> (a REWARDS key) can be prepared."""
        spec = mode2_rewards.REWARDS[key]
        prefix = f"The extended reward route for {key.upper()} cannot be used: "
        self._unchanged(spec["resources"])
        for resource in spec["resources"]:
            for other in mode2_rewards.RESOURCE_REWARDS.get(resource, ()):
                if other != key and mode2_rewards.REWARDS[other]["event_global"] != spec["event_global"]:
                    continue
                if (resource, other) in self.reasons:
                    raise ProfileError(prefix + self.reasons[resource, other])
        self._texts(any(resource in mode2_rewards.TEXT_REFS for resource in spec["resources"]), prefix)

    def require_container(self, target_id: str) -> None:
        """Raise unless the container quest target can be prepared."""
        spec = container_rewards.TARGETS[target_id]
        self._unchanged(spec["resources"])
        for resource in spec["resources"]:
            if (resource, target_id) in self.reasons:
                raise container_rewards.container_error(target_id, self.reasons[resource, target_id])
        prefix = str(container_rewards.container_error(target_id, ""))
        self._texts(any(resource in container_rewards.TEXT_RESOURCES for resource in spec["resources"]), prefix)
