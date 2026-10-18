"""Copies of the quest routes' reward replies in other dialogues.

WeiDU's INTERJECT_COPY_TRANS and INTERJECT_COPY_TRANS3 (and COPY_TRANS) copy
a state's replies, their actions included, into another dialogue: a party
member's interjection then ends with the quest's own reward replies, and the
player can take the reward there. A route gates those copies as it gates its
own replies; otherwise that path gives the original item, never sets the
reward event, and the reward placed there is lost. (INTERJECT_COPY_TRANS2
keeps the actions in the quest's dialogue, where the route finds them.)

The copies are found by following the links out of each route's dialogues
into other dialogues, through chained interjections too: each state once, at
most LIMIT states per route. A reply there whose actions are exactly those of
one of the route's reward replies is a copy. A reply there that gives the
reward another way cannot be told apart from another quest's reward, so the
route is left out with that reason, as it is for a copy it cannot gate: a
carried reward, which the copy's speaker does not carry, or a copy in a
dialogue that CIR changes for something else. Another route's own grants of
the same item (the sculptors' mace of the temple messengers) stay that
route's.

Container quests are not followed: their dialogues test the treasure in the
conditions of their states, which interjections do not copy.

A copy dialogue carries no trailer. As for the route dialogues, our patch is
recognised by removing it and applying it again.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
import hashlib
import struct
from typing import Callable

from . import container_rewards, mode2_rewards
from .game_text import GameTextError
from .profiles import ProfileError
from .quest_options import _Dialogue
from .script_text import calls, named

LIMIT = 10000
# Dialogues CIR changes for something other than a reward route.
_OTHER = frozenset(("dlg:hlskull", "dlg:unshey", "dlg:kpglai01"))
# The routes whose rewards dialogues give, with their dialogues.
_ROUTES = {key: tuple("dlg:" + name for name in spec.get("dialogues", {spec["quest"]: ()}))
           for key, spec in mode2_rewards.REWARDS.items() if spec["resource"].startswith("dlg:")}


def _label(resource: str) -> str:
    return resource[4:].upper() + ".DLG"


def copy_spec(key: str, resource: str) -> dict:
    """The route <key> as it gates its copies in the dialogue <resource>."""
    spec = deepcopy(mode2_rewards.REWARDS[key])
    spec.update(route=key, resource=resource, quest=resource[4:])
    return spec


def _gives(text: bytes, call, item: str) -> bool:
    """Whether a dialogue action puts the item somewhere, also through ActionOverride."""
    if named(call, "ActionOverride") and len(call.args) == 2:
        inner = calls(text, *call.args[1])
        return inner is None or any(mode2_rewards._gives_item(text, nested, item) for nested in inner)
    return mode2_rewards._gives_item(text, call, item)


def _present(dialogue: _Dialogue) -> list[str]:
    """The routes whose gates our copy patch put into a dialogue."""
    triggers, actions = b"".join(dialogue.triggers), b"".join(dialogue.actions)
    return [key for key in sorted(_ROUTES)
            if f'Global("{mode2_rewards.REWARDS[key]["selected_global"]}","GLOBAL",1)'.encode() in triggers and
            mode2_rewards._event(mode2_rewards.REWARDS[key]) in actions]


class RewardCopies:
    """The copies of every quest route's reward replies in the game's dialogues.

    <read> returns a dialogue's bytes by name, or None when the game has none;
    <scanned> maps resources to the hashes the scan recorded.

    copies: route -> copy dialogue -> state -> ordinals of its copied replies.
    reasons: route -> why the route cannot be prepared because of a copy.
    visited: route -> the other dialogues its links lead into (its checked files).
    changed: visited dialogues that differ from the scan, or are gone.
    originals: copy dialogue -> its bytes without our patch.
    """

    def __init__(self, read: Callable[[str], bytes | None], scanned: dict[str, str]):
        self._read, self._scanned = read, scanned
        self._loaded: dict[str, tuple] = {}
        self._patched: dict[str, tuple[bytes, list[str]]] = {}
        self._failed: set[tuple[str, str]] = set()
        self.copies: dict[str, dict[str, dict[int, set[int]]]] = {}
        self.reasons: dict[str, str] = {}
        self.visited: dict[str, set[str]] = {}
        self.changed: set[str] = set()
        self.originals: dict[str, bytes] = {}
        for key in sorted(_ROUTES):
            self._route(key)
        self._check_patches()
        for resource in sorted({resource for rows in self.visited.values() for resource in rows}):
            self._compare(resource)

    def _load(self, resource: str) -> tuple:
        """(dialogue without our patches or None, the problem reading it or None)."""
        if resource not in self._loaded:
            data = self._read(resource[4:])
            self._loaded[resource] = (data, *self._original(resource, data))
        return self._loaded[resource][1:]

    def _original(self, resource: str, data: bytes | None) -> tuple:
        if data is None:
            return None, None
        label = _label(resource)
        try:
            if resource in mode2_rewards.RESOURCE_REWARDS:
                original = mode2_rewards._examine(resource, data).original
            elif resource in container_rewards.RESOURCE_TARGETS:
                original = container_rewards._recover(resource, data)[0]
            else:
                original = self._unpatched(resource, data)
            return _Dialogue(original, resource[4:]), None
        except ProfileError as exc:
            if "changed copy" in str(exc):
                return None, str(exc)
            return None, f"{label} cannot be read as a dialogue, so the route cannot check it for copies of its reward"
        except GameTextError:
            return None, f"{label} cannot be read as a dialogue, so the route cannot check it for copies of its reward"

    def _unpatched(self, resource: str, data: bytes) -> bytes:
        """A dialogue without our copy patch; its routes are checked later."""
        dialogue = mode2_rewards._read_dialogue(data, resource)
        specs = tuple(copy_spec(key, resource) for key in sorted(_ROUTES))
        if not mode2_rewards._carries_patch(dialogue, specs):
            return data
        present = _present(dialogue)
        flags = [mode2_rewards.REWARDS[key]["selected_global"] for key in present]
        if not present or len(set(flags)) != len(flags):
            raise mode2_rewards._changed_patch(resource)
        try:
            original = mode2_rewards._unpatched_dialogue(
                dialogue, tuple(copy_spec(key, resource) for key in present), resource)
        except ProfileError:
            raise mode2_rewards._changed_patch(resource) from None
        self._patched[resource] = (data, present)
        return original

    def _grants(self, key: str) -> dict[bytes, str] | None:
        """The actions of the route's reward replies, with the dialogue each is in.

        None when a route dialogue is missing or its grant is not recognised:
        the route's own reasons say why it cannot be prepared.
        """
        texts = {}
        for resource in _ROUTES[key]:
            dialogue, problem = self._load(resource)
            if dialogue is None:
                return None
            spec = next(spec for spec in mode2_rewards.resource_specs(resource) if spec["route"] == key)
            try:
                grants = mode2_rewards._dialogue_grants(dialogue, spec, _label(resource))
            except ProfileError:
                return None
            for state, ordinal in grants:
                first, _ = struct.unpack_from("<II", dialogue.states[state], 4)
                action = struct.unpack_from("<I", dialogue.transitions[first + ordinal], 16)[0]
                texts.setdefault(dialogue.actions[action], resource)
        return texts

    def _route(self, key: str) -> None:
        texts = self._grants(key)
        if texts is None:
            return
        spec = mode2_rewards.REWARDS[key]
        own = set(_ROUTES[key])
        visited = self.visited.setdefault(key, set())
        found: dict[str, dict[int, set[int]]] = {}
        reason = None
        queue = deque()
        for resource in sorted(own):
            dialogue, _ = self._load(resource)
            queue.extend(link for index in range(len(dialogue.states))
                         for link in self._links(dialogue, index))
        seen = set()
        while queue:
            resource, index = queue.popleft()
            if resource in own or (resource, index) in seen:
                continue
            if len(seen) == LIMIT:
                reason = reason or (f"{'/'.join(map(_label, sorted(own)))} lead into more than {LIMIT} states "
                                    "of other dialogues; the route cannot check them all for copies of its reward")
                break
            seen.add((resource, index))
            visited.add(resource)
            dialogue, problem = self._load(resource)
            if problem:
                reason = reason or problem
                continue
            if dialogue is None or index >= len(dialogue.states):
                continue  # The engine cannot continue there either.
            where = f"{_label(resource)} state {index}"
            first, count = struct.unpack_from("<II", dialogue.states[index], 4)
            if first + count > len(dialogue.transitions):
                reason = reason or f"{where} has replies outside its tables"
                continue
            for ordinal, row in enumerate(dialogue.transitions[first:first + count]):
                flags, _, _, _, action = struct.unpack_from("<5I", row)
                if not flags & 4:
                    continue
                if action >= len(dialogue.actions):
                    reason = reason or f"{where} has a reply whose actions are outside its tables"
                    continue
                text = dialogue.actions[action]
                if not mode2_rewards._names(text, spec["item"]):
                    continue
                try:
                    if self._copy(key, resource, dialogue, index, ordinal, text, texts):
                        found.setdefault(resource, {}).setdefault(index, set()).add(ordinal)
                except ProfileError as exc:
                    reason = reason or str(exc)
            queue.extend(self._links(dialogue, index))
        if found:
            self.copies[key] = found
        if reason:
            self.reasons[key] = reason

    def _links(self, dialogue: _Dialogue, index: int):
        first, count = struct.unpack_from("<II", dialogue.states[index], 4)
        for row in dialogue.transitions[first:first + count]:
            if struct.unpack_from("<I", row)[0] & 8:
                continue
            name = bytes(row[20:28]).rstrip(b"\0").decode("latin-1").lower()
            if name:
                yield "dlg:" + name, struct.unpack_from("<I", row, 28)[0]

    def _copy(self, key, resource, dialogue, index, ordinal, text, texts) -> bool:
        """Whether a reply that names the reward copies a reward reply.

        Raises with the reason when the route cannot gate what the reply does.
        """
        spec = mode2_rewards.REWARDS[key]
        where = f"{_label(resource)} state {index}"
        shown = spec["item"].upper()
        if text in texts:
            origin = _label(texts[text])
            if resource in mode2_rewards.RESOURCE_REWARDS or resource in container_rewards.RESOURCE_TARGETS or \
                    resource in _OTHER:
                raise ProfileError(f"{where} copies a reward reply of {origin}, but CIR changes "
                                   f"{_label(resource)} for something else")
            if spec.get("grant_kind") == "transfer":
                raise ProfileError(f"{where} copies a reward reply of {origin}, but there another speaker "
                                   f"would hand over the {shown} that {origin}'s speaker carries")
            return True
        found = calls(text)
        if found is None:
            raise ProfileError(f"{where} names {shown} in actions that cannot be read")
        if not any(_gives(text, call, spec["item"]) for call in found):
            return False  # It takes, shows or tests the reward.
        for other in mode2_rewards.RESOURCE_REWARDS.get(resource, ()):
            other_spec = next(row for row in mode2_rewards.resource_specs(resource) if row["route"] == other)
            if other_spec["item"] != spec["item"]:
                continue
            try:
                if (index, ordinal) in mode2_rewards._dialogue_grants(dialogue, other_spec, _label(resource)):
                    return False  # Another route's own grant of the same item.
            except ProfileError:
                pass
        origins = ", ".join(sorted({_label(origin) for origin in texts.values()}))
        raise ProfileError(f"{where} also gives {shown}, but not with a copy of a reward reply of {origins}, "
                           "so the route cannot tell whether it is the same reward")

    def _check_patches(self) -> None:
        """Gate each copy dialogue for every route once; a route it fails for is left out."""
        for resource in sorted({resource for rows in self.copies.values() for resource in rows}):
            dialogue, _ = self._load(resource)
            self.originals[resource] = dialogue.data
            keys = sorted(key for key, rows in self.copies.items() if resource in rows)
            failures: dict[str, str] = {}
            try:
                self._patch(resource, keys, failures)
            except ProfileError:
                pass
            for key, failure in failures.items():
                self._failed.add((key, resource))
                self.reasons.setdefault(key, failure)
        # Our earlier patch of a copy dialogue must be exactly the patch of its original.
        for resource, (data, present) in sorted(self._patched.items()):
            visitors = sorted(key for key, rows in self.visited.items() if resource in rows)
            if not visitors:
                continue
            try:
                same = (all(resource in self.copies.get(key, {}) for key in present) and
                        mode2_rewards._logical(self._patch(resource, present), resource) ==
                        mode2_rewards._logical(data, resource))
            except ProfileError:
                same = False
            if not same:
                self._patched.pop(resource)
                for key in visitors:
                    self.reasons.setdefault(key, str(mode2_rewards._changed_patch(resource)))

    def _patch(self, resource: str, keys, failures: dict | None = None) -> bytes:
        specs = tuple(copy_spec(key, resource) for key in keys)
        return mode2_rewards._patch_dialogue(self.originals[resource], specs, reasons=failures,
                                             only={key: self.copies[key][resource] for key in keys},
                                             resource=resource)

    def _compare(self, resource: str) -> None:
        data = self._loaded[resource][0]
        if data is None:
            if resource in self._scanned:
                self.changed.add(resource)
            return
        if hashlib.sha256(data).hexdigest() == self._scanned.get(resource):
            return
        try:
            if resource in mode2_rewards.RESOURCE_REWARDS:
                own = mode2_rewards.own_patch(resource, data)
            elif resource in container_rewards.RESOURCE_TARGETS:
                own = container_rewards.own_patch(resource, data)
            else:
                own = resource in self._patched
        except ProfileError:
            own = True  # A changed copy of our patch: the reasons name it.
        if not own:
            self.changed.add(resource)

    def checked(self, key: str) -> set[str]:
        """The other dialogues whose bytes the route's copies depend on."""
        return set(self.visited.get(key, ()))

    def patches(self, routes) -> dict[str, bytes]:
        """Each copy dialogue of these routes, with each of their copies there gated.

        A dialogue that already carries exactly this patch keeps its bytes.
        """
        result = {}
        routes = set(routes)
        for resource in sorted({resource for key in routes for resource in self.copies.get(key, {})}):
            keys = sorted(key for key in routes if resource in self.copies.get(key, {}) and
                          (key, resource) not in self._failed)
            if keys and self._patched.get(resource, (None, None))[1] == keys:
                result[resource] = self._patched[resource][0]
            elif keys:
                result[resource] = self._patch(resource, keys)
        return result
