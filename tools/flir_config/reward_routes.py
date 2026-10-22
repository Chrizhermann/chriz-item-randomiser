"""Which extended reward routes a game can prepare, read once per scan.

The configuration check and Apply run the same recognition
(mode2_rewards.route_reasons, container_rewards.route_reasons and the copies
of reward replies in other dialogues, reward_copies.py) on the game's current
bytes. A route the check accepts is one the export can prepare; one it cannot
prepare is left out of the configuration with its reason instead of failing
at Apply.

A quest route needs its grant recognised in each of its resources, and so does
every route that shares its reward event (the egg deal's two dialogues, the
three sculptors); copies of their reward replies elsewhere must be gateable
too. Routes with Item Randomiser's reward text need its translation into the
game's language (translations.py), a dialogf.tlk as long as dialog.tlk where
the language has one (game_text.py) and the BGEE.LUA journal table, as the
export does.

The resources are read once per scan, which keeps the repeated checks of a fit
cheap. Each check run first compares the files they came from, the game text
files and CIR's translations with what was read (their stat identities, see
resources.file_identity) and reads them again when one changed; a resource
whose bytes then differ from the scan stops the check. Apply still reads and
verifies the bytes it patches.

The routes are recognised as the scanned game names their items (EET renames
BG1's): `game` and `names` say how, for the export too.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from . import container_rewards, game_text, mode2_rewards, translations
from .game_names import eet_names
from .platform import game_platform
from .profiles import ProfileError
from .resources import GameResources, RESOURCE_TYPES, file_identity
from .reward_copies import RewardCopies


class ChangedSinceScan(ProfileError):
    """A reward resource changed since the scan; the check stops instead of leaving rows out."""


_CACHE: dict[tuple[str, str, str], "RewardRoutes"] = {}
# Routes that follow a physical source (Lesa's sword, the treasury's rod):
# needed where their dialogue gives the item at all (mode2_rewards.gives_reward).
_LINKS = frozenset(mode2_rewards.PHYSICAL_SOURCE_REWARDS.values())


def reward_routes(catalog: dict, world: dict) -> "RewardRoutes":
    """The reward routes of the scanned game, read again when its files changed."""
    key = (catalog["game_root"], catalog["language"], world["fingerprint"])
    if key in _CACHE and not _CACHE[key].current():
        del _CACHE[key]
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
        self.game = game_platform(world)
        self.names = eet_names(game_root) if self.game == "eet" else None
        naming = {"game": self.game, "names": self.names}
        self.language = language
        self.changed: set[str] = set()
        self.reasons: dict[tuple[str, str], str] = {}
        self.giving: set[str] = set()
        self._files: dict[Path, tuple] = {}
        male, female = game_text.text_files(game_root, language)
        for path in [male.parent, male, *([female] if female else []), *translations.sources(language)]:
            self._files[path] = file_identity(path)
        # Why an appended text could not have the same number in dialogf.tlk.
        self.unpaired = game_text.unpaired(male, female)
        self._translated: dict[tuple, str | None] = {}
        with GameResources(game_root, {kind: RESOURCE_TYPES[kind] for kind in ("bcs", "dlg", "lua")}) as reader:
            present = {kind: set(reader.names(kind)) for kind in ("bcs", "dlg", "lua")}

            def read(kind: str, name: str) -> bytes | None:
                if name not in present[kind]:
                    return None
                path = reader.location(kind, name)
                if path not in self._files:
                    self._files[path] = file_identity(path)
                return reader.read(kind, name)

            for path in reader.lookup_paths():
                self._files[path] = file_identity(path)
            self.journal = "bgee" in present["lua"]
            for resource in resources:
                kind, name = resource.split(":")
                data = read(kind, name)
                if data is None:
                    self.changed.add(resource)
                    continue
                quest = resource in mode2_rewards.RESOURCE_REWARDS
                adapter = mode2_rewards if quest else container_rewards
                named = naming if quest else {}
                try:
                    own = adapter.own_patch(resource, data, **named)
                except ProfileError:
                    own = True  # A changed copy of our patch: route_reasons names it.
                if not own and hashlib.sha256(data).hexdigest() != scanned[resource]:
                    self.changed.add(resource)
                    continue
                for key, reason in adapter.route_reasons(resource, data, **named).items():
                    self.reasons[resource, key] = reason
                for key in sorted(_LINKS.intersection(mode2_rewards.RESOURCE_REWARDS.get(resource, ()))):
                    try:
                        gives = mode2_rewards.gives_reward(resource, data, key, **naming)
                    except ProfileError:
                        gives = True  # Its route's reasons say why it cannot be read.
                    if gives:
                        self.giving.add(key)
            self.copies = RewardCopies(lambda name: read("dlg", name), scanned, **naming)
        self.changed |= self.copies.changed

    def current(self) -> bool:
        """Whether every file these routes were read from is as it was."""
        try:
            return all(file_identity(path) == identity for path, identity in self._files.items())
        except OSError:
            return False

    def _unchanged(self, resources) -> None:
        for resource in resources:
            if resource in self.changed:
                raise ChangedSinceScan(f"Installed resource changed; rescan before export: {resource}")

    def _texts(self, lines: tuple, prefix: str) -> None:
        """Raise unless the export can show Item Randomiser's (file, number) <lines> in the game."""
        if not lines:
            return
        if lines not in self._translated:
            self._translated[lines] = translations.missing(self.language, lines)
        reason = self._translated[lines] or self.unpaired
        if reason:
            raise ProfileError(prefix + reason)
        if not self.journal:
            raise ProfileError(prefix + "the game has no BGEE.LUA journal table")

    def _sharing(self, key: str) -> list[str]:
        """The route and every route that shares its reward event in its resources."""
        spec = mode2_rewards.REWARDS[key]
        return sorted({key} | {other for resource in spec["resources"]
                               for other in mode2_rewards.RESOURCE_REWARDS.get(resource, ())
                               if mode2_rewards.REWARDS[other]["event_global"] == spec["event_global"]})

    def copy_reason(self, key: str) -> str | None:
        """Why a copy of the route's reward replies elsewhere cannot be gated, if one cannot."""
        return next((self.copies.reasons[other] for other in self._sharing(key) if other in self.copies.reasons),
                    None)

    def require_route(self, key: str, *, copies: bool = True) -> None:
        """Raise unless the quest route <key> (a REWARDS key) can be prepared.

        With copies False, a copy of its reward replies that cannot be gated
        does not stop it (see copy_reason); a revision exported before CIR
        looked for copies keeps its route.
        """
        spec = mode2_rewards.REWARDS[key]
        prefix = f"The extended reward route for {key.upper()} cannot be used: "
        sharing = self._sharing(key)
        self._unchanged([*spec["resources"], *sorted(set().union(*(self.copies.checked(other)
                                                                     for other in sharing)))])
        for resource in spec["resources"]:
            for other in mode2_rewards.RESOURCE_REWARDS.get(resource, ()):
                if other in sharing and (resource, other) in self.reasons:
                    raise ProfileError(prefix + self.reasons[resource, other])
        reason = self.copy_reason(key)
        if copies and reason:
            raise ProfileError(prefix + reason)
        self._texts(tuple(line for resource in spec["resources"] if resource in mode2_rewards.TEXT_REFS
                          for line in mode2_rewards.text_lines(resource)), prefix)

    def gives(self, key: str) -> bool:
        """Whether a dialogue of the route <key> (one of _LINKS) gives its item at
        all; a game without the dialogue gives it nowhere."""
        self._unchanged(mode2_rewards.REWARDS[key]["resources"])
        return key in self.giving

    def require_container(self, target_id: str) -> None:
        """Raise unless the container quest target can be prepared."""
        spec = container_rewards.TARGETS[target_id]
        self._unchanged(spec["resources"])
        for resource in spec["resources"]:
            if (resource, target_id) in self.reasons:
                raise container_rewards.container_error(target_id, self.reasons[resource, target_id])
        prefix = str(container_rewards.container_error(target_id, ""))
        self._texts(container_rewards.TEXT_LINES if any(resource in container_rewards.TEXT_RESOURCES
                                                        for resource in spec["resources"]) else (), prefix)
