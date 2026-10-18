"""Campaign-gated conditions of the extended pools' container quests.

The Host observes a delivered reward becoming absent from its loaded container
and sets the original saved latch. This module only patches the latch's consumers;
it never substitutes current party possession for having taken the treasure.
Set activation_global at campaign creation only for actual assignments. Observe
each assigned target only after confirmed delivery, and latch 0 -> 1 once; an
unloaded/unresolved container is not an empty container. Samia's two assignments
share one latch, so either observed absence suffices. Never clear it on item loss.
See Item Randomiser's lib/weidu_action.tpa and baf/flrkeep*.baf, flrsamia.baf
and flrsafan.baf.

The conditions are found by their meaning, never by the bytes of one reviewed
game: every script block and dialogue condition that tests whether the party
has the treasure, and the block that sets the latch itself when the container
no longer holds it. Blocks and conditions another mod added stay as found.

The container is found by its meaning too: the one of the quest's area that
holds the whole treasure (treasure_container). Item Randomiser names the
chests as the original game did, which the EE games renamed in AR1202, and its
tables put the golems' treasures in AR1302 although its handler and the
treasures use AR1303; a container index would name another chest in a game
where a mod added one.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import re
import struct

from .game_names import convert_resource
from .profiles import ProfileError
from .quest_options import _Dialogue, _texts
from .quest_rewards import _global_guard
from .game_text import GameTextError, _append_texts
from .script_text import (ScriptError, block_actions, block_parts, block_triggers, calls,
                          integer_argument, named, naming, script_blocks, string_argument)


def _target(campaign, row, area, item, flag, latch, scope, resources):
    # <area> is named as in the campaign's own game (BG:EE for bg1).
    return {"target_id": f"{campaign}:mode2:target:{row}", "campaign": campaign, "area": area,
            "original_item": item, "activation_global": flag,
            "latch_global": latch, "latch_scope": scope, "resources": resources}


_ROWS = (
    _target("bg2", 13, "ar1303", "ax1h13", "FLPR_CKEEP1", "flRobbedGolems1", "AR1303",
            ("bcs:kpgol01", "bcs:kpgol03")),
    _target("bg2", 43, "ar1303", "bow12", "FLPR_CKEEP2", "flRobbedGolems2", "AR1303",
            ("bcs:kpgol02", "bcs:kpgol04")),
    _target("bg2", 3, "ar1303", "hamm04", "FLPR_CKEEP3", "flRobbedGolems3", "AR1303",
            ("bcs:kpgol05", "bcs:kpgol06")),
    # These are the handler's item names, not the source row's Ident pairing.
    _target("bg2", 53, "ar1202", "shld21", "FLPR_CSAMIA", "flSamiaTreasure", "AR1202",
            ("bcs:ar1202", "bcs:samia", "dlg:samia")),
    _target("bg2", 28, "ar1202", "sw1h32", "FLPR_CSAMIA", "flSamiaTreasure", "AR1202",
            ("bcs:ar1202", "bcs:samia", "dlg:samia")),
    _target("bg1", 22, "ar3601", "book03", "FLPR_CSAFANA", "flSafanaTreasure", "GLOBAL",
            ("bcs:safana", "dlg:safanj")),
    _target("bg2", 61, "ar1514", "bow19b", "FLPR_CPPCHST", "RobbedGolem", "GLOBAL",
            ("bcs:ppchest",)),
)
TARGETS = {row["target_id"]: row for row in _ROWS}
RESOURCE_TARGETS = {resource: tuple(row["target_id"] for row in _ROWS if resource in row["resources"])
                    for row in _ROWS for resource in row["resources"]}
TEXT_RESOURCES = frozenset(("bcs:kpgol01", "bcs:kpgol02", "bcs:kpgol05"))
_GOLEM_TEXT = 38758
_TRAILER = b"FLPRCON1"
_PARTY_HAS_ITEM, _CONTAINS, _OR, _SET_GLOBAL = 16450, 16501, 16521, 30
# TRIGGER.IDS names of the triggers that test an item, for the reasons.
_TRIGGERS = {1: "Acquired", 9: "Unusable", 16450: "PartyHasItem", 16481: "HasItem", 16501: "Contains",
             16503: "NumItems", 16504: "NumItemsGT", 16505: "NumItemsLT", 16506: "NumItemsParty",
             16507: "NumItemsPartyGT", 16508: "NumItemsPartyLT", 16511: "HasItemEquiped",
             16553: "PartyHasItemIdentified", 16578: "HasItemEquipedReal", 16643: "CheckItemSlot",
             16644: "CurrentAmmo"}


def target_spec(target_id: str) -> dict:
    if target_id not in TARGETS:
        raise ProfileError(f"Unsupported extended reward container: {target_id}")
    return deepcopy(TARGETS[target_id])


def treasure(target_id: str) -> tuple[str, ...]:
    """The quest's treasure: every item of the targets sharing its latch (Samia's two)."""
    flag = target_spec(target_id)["activation_global"]
    return tuple(sorted({row["original_item"] for row in _ROWS if row["activation_global"] == flag}))


def container_error(target_id: str, problem: str) -> ProfileError:
    """Why the quest's container cannot be used, as the check leaves it out."""
    shown = "/".join(item.upper() for item in treasure(target_id))
    return ProfileError(f"The extended reward container of {shown} cannot be used: {problem}")


def treasure_area(target_id: str, platform: str) -> str:
    """The quest's area as this game names it (EET renames BG1's)."""
    spec = target_spec(target_id)
    return convert_resource(spec["area"], "are", spec["campaign"], platform)


def treasure_container(target_id: str, sources, platform: str) -> str:
    """The scanned container of the quest's area that holds its whole treasure.

    <sources> are the scan's sources. Raises when no container, or more than
    one, holds every item of the treasure: then the quest's container is not
    there as the quest expects it (another mod moved or added the treasure).
    """
    area = treasure_area(target_id, platform)
    items = set(treasure(target_id))
    holding = defaultdict(set)
    for source in sources:
        index = (source.get("record") or {}).get("container_index")
        if (source["kind"] == "container" and source["resource"] == "are:" + area and
                source["item"] in items and index is not None):
            holding[f"are:{area}/container/{index}"].add(source["item"])
    chests = sorted(chest for chest, found in holding.items() if found == items)
    if len(chests) != 1:
        shown = "/".join(item.upper() for item in sorted(items))
        raise ProfileError(f"{area.upper()} has {'no container' if not chests else 'more than one container'} "
                           f"that holds {shown}")
    return chests[0]


def prepare_container_texts(tlk: bytes, resources, language="en_us") -> tuple[bytes, dict]:
    needed = sorted(set(resources) & TEXT_RESOURCES)
    if not needed:
        return tlk, {}
    if language.lower() != "en_us":
        raise ProfileError("Item Randomiser's reward texts exist only in English")
    updated, refs = _append_texts(tlk, (_texts("languages/english/in-game.tra")[101],))
    return updated, {resource: {_GOLEM_TEXT: refs[0]} for resource in needed}


def _label(resource: str) -> str:
    kind, name = resource.split(":")
    return f"{name.upper()}.{kind.upper()}"


def _spec(resource: str) -> dict:
    return TARGETS[RESOURCE_TARGETS[resource][0]]


def _items(resource: str) -> tuple[bytes, ...]:
    """The treasure the resource's conditions test."""
    return tuple(item.encode("ascii") for item in treasure(RESOURCE_TARGETS[resource][0]))


def _names(text: bytes, items: tuple[bytes, ...]) -> bool:
    return any(re.search(b'"' + re.escape(item) + b'"', text, re.IGNORECASE) for item in items)


def _trigger(opcode, value=0, negate=0, text="", selector=0, newline=b"\n"):
    return newline.join((b"TR", f'{opcode} {value} {negate} 0 0 "{text}" "" OB'.encode(),
                         f'0 0 0 0 0 0 0 {selector} 0 0 0 0 ""OB'.encode(), b"TR", b""))


def _latch(spec, value, newline):
    return _trigger(16399, value, text=spec["latch_scope"] + spec["latch_global"], newline=newline)


def _with_integer(action: bytes, newline: bytes, value: int) -> bytes:
    """A compiled action with its first integer parameter replaced."""
    start = action.rindex(b"OB" + newline) + len(b"OB" + newline)
    number = re.match(rb"-?[0-9]+", action[start:])
    return action[:start] + str(value).encode() + action[start + number.end():]


def _with_count(trigger: bytes, newline: bytes, count: int) -> bytes:
    """A compiled OR() trigger that joins <count> conditions."""
    start = len(b"TR" + newline)
    number = re.match(rb"[0-9]+ (-?[0-9]+)", trigger[start:])
    return trigger[:start + number.start(1)] + str(count).encode() + trigger[start + number.end(1):]


def _groups(counts: list) -> list[tuple[int | None, list[int], bool]]:
    """The groups of a condition list: (OR position or None, members, complete).

    <counts> has an OR()'s count at its position and None elsewhere. OR(n)
    joins the n conditions after it; any other condition stands alone. Only
    the last group can be incomplete, when fewer conditions follow its OR().
    """
    groups, at = [], 0
    while at < len(counts):
        if counts[at] is None:
            groups.append((None, [at], True))
            at += 1
            continue
        wanted = max(counts[at], 0)
        members = list(range(at + 1, min(at + 1 + wanted, len(counts))))
        groups.append((at, members, len(members) == wanted))
        at += 1 + len(members)
    return groups


def _checked_group(where, shown, items, first, members, complete, tested, negated, nested) -> None:
    """Raise unless the route can rewrite a group that holds treasure tests.

    <tested> is the set of items the group's tests name, <negated> whether one
    of them tests that the party lacks it, <nested> whether the group negates
    its OR() or holds another one.
    """
    if not complete:
        raise ProfileError(f"{where} tests {shown} in an OR() group that counts more conditions than follow it")
    if nested:
        raise ProfileError(f"{where} tests {shown} in an OR() group that is negated or holds another OR(), "
                           "which the route cannot read")
    if len(items) > 1:
        # The shared latch records that one of the treasures was taken: it can
        # stand only for "the party has one of them".
        if first is None or tested != set(items):
            raise ProfileError(f"{where} does not test {shown} together in one OR() group; "
                               "their shared latch records only that one of them was taken")
        if negated:
            raise ProfileError(f"{where} tests whether the party lacks one of {shown}; "
                               "their shared latch records only that one of them was taken")


def _script_sites(script, resource: str) -> dict[int, tuple]:
    """Each block that tests the treasure: its parts, its edits and its displays.

    A consumer block tests whether the party has the treasure; each test
    becomes the latch (edits of its conditions), wherever it stands in its
    block. A setter block (edits None) sets the latch itself when the
    container no longer holds the treasure; the Host takes that over.
    """
    spec, items, label = _spec(resource), _items(resource), _label(resource)
    shown = "/".join(item.decode().upper() for item in items)
    newline = script.newline
    latch = (spec["latch_scope"] + spec["latch_global"]).lower().encode("ascii")
    sites = {}
    for index, block in enumerate(script.blocks):
        if not _names(block, items):
            continue
        head, conditions, responses = block_parts(block, newline)
        triggers = block_triggers(conditions, newline)
        tests = [number for number, trigger in enumerate(triggers) if trigger.strings[0].lower() in items]
        if not tests:
            continue
        where = f"{label} block {index}"
        actions = block_actions(responses, newline)
        groups = _groups([trigger.value if trigger.opcode == _OR else None for trigger in triggers])
        if groups and not groups[-1][2]:
            # The route adds its own condition at the end of the block.
            raise ProfileError(f"{where} ends in an OR() group that counts more conditions than follow it, "
                               "so a condition added after it would join it")
        if (all(triggers[number].opcode == _CONTAINS and triggers[number].negated for number in tests) and
                any(action.opcode == _SET_GLOBAL and action.strings[0].lower() == latch for action in actions)):
            sites[index] = (head, conditions, responses, None, [])
            continue
        other = next((triggers[number] for number in tests if triggers[number].opcode != _PARTY_HAS_ITEM), None)
        if other is not None:
            name = _TRIGGERS.get(other.opcode, f"trigger {other.opcode}")
            raise ProfileError(f"{where} tests {other.strings[0].decode('latin-1').upper()} with {name}(), "
                               "which the route cannot replace by the container's latch")
        edits = []
        for first, members, complete in groups:
            hit = [number for number in members if number in tests]
            if not hit:
                continue
            nested = first is not None and (triggers[first].negated or
                                            any(triggers[number].opcode == _OR for number in members))
            _checked_group(where, shown, items, first, members, complete,
                           {triggers[number].strings[0].lower() for number in hit},
                           any(triggers[number].negated for number in hit), nested)
            if len(items) == 1:
                edits += [(triggers[number].start, triggers[number].end,
                           _latch(spec, 0 if triggers[number].negated else 1, newline)) for number in hit]
            elif len(hit) == len(members):
                # OR(n) of the treasure tests only: the latch replaces the group.
                edits.append((triggers[first].start, triggers[members[-1]].end, _latch(spec, 1, newline)))
            else:
                group = triggers[first]
                edits.append((group.start, group.end, _with_count(conditions[group.start:group.end], newline,
                                                                  len(members) - len(hit) + 1)))
                edits.append((triggers[hit[0]].start, triggers[hit[0]].end, _latch(spec, 1, newline)))
                edits += [(triggers[number].start, triggers[number].end, b"") for number in hit[1:]]
        displays = [action for action in actions if action.integer == _GOLEM_TEXT]
        sites[index] = (head, conditions, responses, edits, displays)
    if not sites:
        raise ProfileError(f"{label} has no block that tests {shown}")
    if resource in TEXT_RESOURCES and not any(site[4] for site in sites.values()):
        raise ProfileError(f"{label} no longer shows the golem's text that names the treasure ({_GOLEM_TEXT})")
    return sites


def _replaced(data: bytes, edits) -> bytes:
    for start, end, replacement in sorted(edits, reverse=True):
        data = data[:start] + replacement + data[end:]
    return data


def _patch_script(resource, original, ref):
    if resource in TEXT_RESOURCES and (type(ref) is not int or not 0 <= ref < 0xFFFFFFFF):
        raise ProfileError(f"{_label(resource)} requires allocated golem text")
    with naming(_label(resource)):
        script = script_blocks(original)
        sites = _script_sites(script, resource)
    newline = script.newline
    spec = _spec(resource)
    blocks = list(script.blocks)
    for index, (head, conditions, responses, edits, displays) in sites.items():
        excluded = (head + b"CO" + newline + conditions +
                    _global_guard(spec["activation_global"], False, newline) + b"CO" + newline + responses)
        variants = []
        if edits is not None:
            selected_response = responses
            if resource in TEXT_RESOURCES:
                # Every response that shows the golem's text shows the new one.
                selected_response = _replaced(responses, [
                    (display.start, display.end,
                     _with_integer(responses[display.start:display.end], newline, ref)) for display in displays])
            variants.append(head + b"CO" + newline + _replaced(conditions, edits) +
                            _global_guard(spec["activation_global"], True, newline) + b"CO" + newline +
                            selected_response)
        # A setter block (PPCHST's) is disabled only when the observer owns it.
        variants.append(excluded)
        blocks[index] = b"".join(variants)
    return script.prefix + b"".join(blocks) + script.suffix


def _condition_edits(text: bytes, resource: str, where: str) -> list[tuple[int, int, bytes]]:
    """The rewrites of the treasure tests in a dialogue condition; none without one.

    Each group of conditions that tests the treasure (an OR() group, or a
    test standing alone) becomes two: (selected OR the group) AND (!selected
    OR the group with the latch in place of its tests). Every other group
    stays as written.
    """
    items = _items(resource)
    if not _names(text, items):
        return []
    shown = "/".join(item.decode().upper() for item in items)
    found = calls(text)
    if found is None:
        raise ProfileError(f"{where} tests {shown} in a way the container route does not recognise")
    is_or = [call.name.lstrip(b"!").lower() == b"or" for call in found]
    counts = [integer_argument(text, call, 0) if group else None for call, group in zip(found, is_or)]
    if any(group and count is None for group, count in zip(is_or, counts)):
        raise ProfileError(f"{where} has an OR() whose count the route cannot read")
    tests = [number for number, call in enumerate(found) if _names(text[call.start:call.end], items)]
    for number in tests:
        call = found[number]
        name = call.name.decode("latin-1").lstrip("!")
        if not named(call, "PartyHasItem", "!PartyHasItem"):
            item = next(item for item in items if _names(text[call.start:call.end], (item,)))
            raise ProfileError(f"{where} tests {item.decode().upper()} with {name}(), "
                               "which the route cannot replace by the container's latch")
        if len(call.args) != 1 or (string_argument(text, call, 0) or b"").lower() not in items:
            raise ProfileError(f"{where} tests {shown} in a way the container route does not recognise")
    spec = _spec(resource)
    flag = spec["activation_global"]

    def latch(negative):
        return f'Global("{spec["latch_global"]}","{spec["latch_scope"]}",{0 if negative else 1})'.encode()

    def written(number):
        return text[found[number].start:found[number].end]

    edits = []
    for first, members, complete in _groups(counts):
        hit = [number for number in members if number in tests]
        if not hit:
            continue
        nested = first is not None and (found[first].name.startswith(b"!") or
                                        any(is_or[number] for number in members))
        _checked_group(where, shown, items, first, members, complete,
                       {(string_argument(text, found[number], 0) or b"").lower() for number in hit},
                       any(named(found[number], "!PartyHasItem") for number in hit), nested)
        if len(items) == 1:
            latched = [latch(named(found[number], "!PartyHasItem")) if number in hit else written(number)
                       for number in members]
        else:
            # The shared latch stands for all of the group's treasure tests.
            latched = [latch(False) if number == hit[0] else written(number)
                       for number in members if number == hit[0] or number not in hit]
        # Take the whole lines: their indentation before and the line end after.
        start = found[members[0] if first is None else first].start
        line = max(text.rfind(b"\n", 0, start), text.rfind(b"\r", 0, start)) + 1
        if not text[line:start].strip(b" \t"):
            start = line
        end = found[members[-1]].end
        for newline in (b"\r\n", b"\n"):
            if text.startswith(newline, end):
                end += len(newline)
                break
        # (selected OR original) AND (!selected OR latched). No nested OR/AND.
        changed = f'OR({len(members) + 1})\r\nGlobal("{flag}","GLOBAL",1)\r\n'.encode()
        changed += b"\r\n".join(written(number) for number in members) + b"\r\n"
        changed += f'OR({len(latched) + 1})\r\n!Global("{flag}","GLOBAL",1)\r\n'.encode()
        changed += b"\r\n".join(latched) + b"\r\n"
        edits.append((start, end, changed))
    return edits


def _read_dialogue(data: bytes, resource: str) -> _Dialogue:
    try:
        return _Dialogue(data, resource[4:])
    except (GameTextError, ProfileError) as exc:
        raise ProfileError(f"{_label(resource)} cannot be read as a dialogue") from exc


def _patch_dialogue(resource, original):
    dialogue = _read_dialogue(original, resource)
    label, sites = _label(resource), 0
    for kind, table in (("condition", dialogue.state_triggers), ("reply condition", dialogue.triggers)):
        for index, text in enumerate(table):
            edits = _condition_edits(text, resource, f"{label} {kind} {index}")
            if edits:
                table[index] = _replaced(text, edits)
                sites += 1
    if not sites:
        shown = "/".join(item.decode().upper() for item in _items(resource))
        raise ProfileError(f"{label} has no condition that tests {shown}")
    # _Dialogue appends tables and leaves the original body recoverable.
    return dialogue.render() + struct.pack("<I52s", len(original), original[:52]) + _TRAILER


def _changed_patch(resource: str) -> ProfileError:
    return ProfileError(f"{_label(resource)} carries a changed copy of the container route's own patch")


def _recover(resource, data):
    """The original, the golem text ref and whether the bytes are our own patch."""
    spec = _spec(resource)
    marker = spec["activation_global"].encode("ascii")
    if resource.startswith("dlg:"):
        if not data.endswith(_TRAILER):
            if marker in data:
                raise _changed_patch(resource)
            return data, None, False
        if len(data) < 64:
            raise _changed_patch(resource)
        size, header = struct.unpack("<I52s", data[-64:-8])
        if not 52 <= size <= len(data) - 64:
            raise _changed_patch(resource)
        original = header + data[52:size]
        try:
            if _patch_dialogue(resource, original) == data:
                return original, None, True
        except ProfileError:
            pass
        raise _changed_patch(resource)
    if b'"GLOBAL' + marker + b'"' not in data:
        return data, None, False
    restored, numbers = [], set()
    try:
        script = script_blocks(data)
        newline = script.newline
        selected, excluded = (_global_guard(spec["activation_global"], value, newline) for value in (True, False))
        for block in script.blocks:
            if selected in block:
                numbers.update(action.integer for action in block_actions(block_parts(block, newline)[2], newline))
            else:
                restored.append(block.replace(excluded, b"", 1))
    except ScriptError:
        raise _changed_patch(resource) from None
    original = script.prefix + b"".join(restored) + script.suffix
    candidates = (sorted(number for number in numbers if 0 <= number < 0xFFFFFFFF)
                  if resource in TEXT_RESOURCES else [None])
    for ref in candidates:
        try:
            if _patch_script(resource, original, ref) == data:
                return original, ref, True
        except ProfileError:
            break
    raise _changed_patch(resource)


def _resource(resource: str, data) -> str:
    resource = resource.lower()
    if resource not in RESOURCE_TARGETS or not isinstance(data, bytes):
        raise ProfileError(f"Unsupported extended reward container resource: {resource}")
    return resource


def own_patch(resource: str, data: bytes) -> bool:
    """Whether the bytes are this module's own patch, verified exactly.

    False for an unpatched resource; raises for a changed copy of our patch.
    """
    resource = _resource(resource, data)
    return _recover(resource, data)[2]


def route_reasons(resource: str, data: bytes) -> dict[str, str]:
    """Why each container target using this resource cannot be prepared from these bytes."""
    resource = _resource(resource, data)
    try:
        original = _recover(resource, data)[0]
        if resource.startswith("dlg:"):
            _patch_dialogue(resource, original)
        else:
            _patch_script(resource, original, _GOLEM_TEXT)
    except ProfileError as exc:
        return {target: str(exc) for target in RESOURCE_TARGETS[resource]}
    return {}


def patch_container_resource(resource: str, data: bytes, *, refs=None) -> bytes:
    """Canonical campaign-gated patch; refs maps original golem strref 38758."""
    resource = _resource(resource, data)
    original, old_ref, _own = _recover(resource, data)
    if resource.startswith("dlg:"):
        return _patch_dialogue(resource, original)
    return _patch_script(resource, original, (refs or {}).get(_GOLEM_TEXT, old_ref))
