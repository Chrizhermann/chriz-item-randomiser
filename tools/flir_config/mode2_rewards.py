"""Campaign-gated quest rewards of the extended pools.

Item Randomiser's extended lists move rewards that quest dialogues and scripts
give. Each reward route here is recognised by its meaning, never by the bytes
of one reviewed game: the replies or script blocks that give the reward item,
the recipient they name and, for some routes, the text that names the item.
Everything else -- Beamdog's or WeiDU's compiled spacing, line ends and letter
case, other strrefs, replies and blocks that another mod added -- is kept as
found, and every reply or block that gives the item is gated.

Only the grant is suppressed; its surrounding actions, conditions, dialogue
replies and journal fields stay intact. The reward event captures the original
recipient at the grant position. A route whose grant cannot be told apart (no
grant, the item given twice by one reply or script response, or given another
way, such as through ActionOverride) is not prepared; the configuration check
leaves it out with that reason (reward_routes.py). Actions that only take,
show or use the item stay as they are. See lib/weidu_action.tpa's generic BCS/DLG
handlers and DDWELL special case.

Own patches are recognised too, so an exported revision re-exports byte for
byte: text routes and container dialogues carry a recovery trailer, scripts
are reconstructed exactly, and the other dialogues are checked by removing the
patch and applying it again.
"""
from __future__ import annotations

from collections import namedtuple
from copy import deepcopy
from itertools import product
import re
import struct

from .profiles import ProfileError
from .quest_options import _Dialogue, _texts
from .quest_rewards import _global_action, _global_guard
from .game_text import GameTextError, _append_texts
from .script_text import (argument, block_actions, block_parts, calls, integer_argument, joined,
                          named, naming, response_spans, script_blocks, string_argument)

ADAPTER_ID = "mode2-reward-v1"


def _spec(name, extension, item, source, target, recipient, charges, branches):
    suffix = f"M2_{source}"
    return {
        "quest": name, "resource": f"{extension}:{name}", "item": item,
        "source_ids": (f"original-source:bg2:mode2:item:{source}",),
        "target_ids": (f"bg2:mode2:target:{target}",),
        "selected_global": "FLPR_Q" + suffix, "consumed_global": "FLPR_X" + suffix,
        "event_global": "FLPR_E" + suffix,
        "recipient_local": None if extension == "bcs" and name == "asysta01" else "FLPR_R" + suffix,
        # The recipient and branches the reviewed EET resources use. The
        # adapter finds the grants and their recipients by meaning; the
        # synthetic test fixtures are built from these.
        "recipient": recipient, "branches": branches,
        "payload": {"resref": item, "expiration": 0, "charges": list(charges), "flags": 0},
    }


REWARDS = {
    "uhogre01": _spec("uhogre01", "dlg", "shld26", 14, 17, "LastTalkedToBy", (0, 0, 0), ((22, 0), (22, 1))),
    "asysta01": _spec("asysta01", "bcs", "helm19", 5, 21, "are:ar1512/container/0", (0, 0, 0), (0,)),
    "ddwell": _spec("ddwell", "bcs", "helm21", 27, 27, "LastTrigger", (0, 0, 0), (1,)),
    "mgkhol01": _spec("mgkhol01", "dlg", "belt02", 33, 33, "LastTalkedToBy", (0, 0, 0), ((4, 0),)),
    "ppstat01": _spec("ppstat01", "dlg", "ring31", 28, 35, "LastTalkedToBy", (1, 1, 1), ((5, 0),)),
    "riftg03": _spec("riftg03", "dlg", "shld27", 38, 40, "Player1", (0, 0, 0), ((30, 0),)),
    "trhmer01": _spec("trhmer01", "dlg", "shld25", 52, 44, "LastTalkedToBy", (0, 0, 0), ((12, 0), (37, 0))),
    "udsvir03": _spec("udsvir03", "dlg", "blun18", 50, 65, "LastTalkedToBy", (1, 1, 1), ((35, 0), (35, 1))),
    "ammayor": _spec("ammayor", "dlg", "compon16", 83, 84, "LastTalkedToBy", (1, 0, 0), ((12, 0),)),
}
DIRECT_REWARDS = tuple(REWARDS)


def _alias(key, item, source, target, alias, dialogues, *, charges=(0, 0, 0), recipient="LastTalkedToBy"):
    primary = next(iter(dialogues))
    spec = _spec(primary, "dlg", item, source, target, recipient, charges, dialogues[primary])
    spec.update(resources=tuple("dlg:" + name for name in dialogues), dialogues=dialogues,
                event_global="FLPR_EA_" + alias, recipient_local="FLPR_RA_" + alias)
    if target is None:
        spec["target_ids"] = ()
    REWARDS[key] = spec


_alias("bhoisig", "blun20", 12, 48, "TEMPLEMESS",
       {"bhoisig": ((33, 0),), "bhnalla": ((59, 0),), "bharval": ((62, 0),)})
_alias("hendak", "sw1h03", 30, 18, "COPPER",
       {"hendak": ((32, 0),), "lehtin": ((23, 0), (24, 0))})
_alias("udsilver", "xbow10", 44, 66, "EGGDEALIO", {"udsilver": ((41, 0),)}, charges=(1, 1, 1))
_alias("uddemon", "halb06", 65, None, "EGGDEALIO", {"uddemon": ((8, 2),)},
       charges=(0, 3, 0), recipient="Player1")
REWARDS["udsilver"]["resources"] = REWARDS["uddemon"]["resources"] = ("dlg:udsilver", "dlg:uddemon")
_alias("gortan1", "helm29", 80, 87, "FIENDS", {"gortan1": ((12, 0),), "gorbat1": ((13, 0),)})
_alias("gorlum2", "blun28", 77, 78, "GORLUM", {"gorlum2": ((16, 0),)}, recipient="Player1")
_alias("aran", "ring07", 47, 24, "ARAN1", {"aran": tuple((4, n) for n in range(8))})
_alias("aran2", "amul21", 56, 57, "ARAN2", {"aran": tuple((4, n) for n in range(8))})
_alias("ppdjinn", "plat12", 24, 30, "PPDJINN", {"ppdjinn": ((0, 0),)})
_alias("uhmay01", "leat14", 25, 46, "UHMAY01", {"uhmay01": ((30, 0), (30, 1))}, recipient="[PC]")
_alias("gorcamb", "sw1h68", 71, 72, "GORCAMB", {"gorcamb": ((56, 0),)}, charges=(0, 1, 0))
_alias("sctelwyn", "helm03", 22, 22, "SCULPTOR", {"sctelwyn": ((19, 0),)})
_alias("scsain", "amul23", 36, None, "SCULPTOR", {"scsain": ((19, 0),)},
       charges=(1, 1, 1), recipient="[PC]")
_alias("scyarryl", "blun20", 12, None, "SCULPTOR", {"scyarryl": ((19, 0),)})
# Item Randomiser's lists/duplicates/bg2.2da:26 attaches this inventory copy to
# BHOISIG source ident w8, and its lib/delete.tpa removes it only when that
# source is selected. The sculptor target independently converts its GiveItem
# into a created reward.
# This is an auxiliary guarded copy, never another randomized source unit.
TRANSFER_SPECS = {"scyarryl": REWARDS["scyarryl"]}
TRANSFER_SPECS["scyarryl"].update(
    authored_source_ids=("original-source:bg2:mode2:item:12",), source_ids=(),
    source_resource="cre:scyarryl",
    source_resource_sha256="15e6723046533520d542f00cb14fdc5c04d74d19b327424f212542b722f0d462",
    physical_source_ids=("cre:scyarryl/item/0",), grant_kind="transfer",
    source_record={"byte_offset": 1308, "record_index": 0, "resref": "blun20",
                   "expiration": 0, "charges": [0, 0, 0], "flags": 0, "slots": [9]})
for _sculptor in ("sctelwyn", "scsain", "scyarryl"):
    REWARDS[_sculptor]["resources"] = ("dlg:sctelwyn", "dlg:scsain", "dlg:scyarryl")
    REWARDS[_sculptor]["required_transfers"] = ("scyarryl",)
# Source selection alone removes its duplicate, even with no sculptor target.
REWARDS["bhoisig"]["required_transfers"] = ("scyarryl",)

# These rewards must remain carried by the NPC so theft and combat routes still
# work. Their dialogue transfers the carried assignment through a blocking Lua
# callback; it never creates a second item for the dialogue recipient.
for _name, _item, _source, _target, _area, _script, _branches, _record, _offset, _slot, _hash in (
    ("trgeni01", "sw1h23", 32, 32, "ar2014", "trgeni01", ((35, 0), (40, 0)), 0, 1152, 9,
     "cd31a342ec3e836a0e3cd2d85c183bd6b85c753c83a6083dcc4c2f21a0aa9c02"),
    ("loutcapt", "dagg11", 39, 42, "ar0514", "Dennis", ((26, 0), (30, 0)), 6, 1116, 11,
     "33bb5267dd0bb7b120e7e9b20c05e37b0cd8c73134e7ba642dbb8b075713e430"),
    ("amcler01", "ring43", 84, 85, "ar5500", "amcler01", ((7, 0), (8, 0)), 0, 1272, 4,
     "c8cdf695c04d6eb9cce075bff7fd4e2c528c480a360ffbac280ef5ec6b14d4fc"),
):
    _alias(_name, _item, _source, _target, _name.upper(), {_name: _branches})
    _specification = REWARDS[_name]
    _specification.update(
        grant_kind="transfer", authored_source_ids=_specification["source_ids"],
        inventory_target={"area": _area, "creature_resource": "cre:" + _name, "script_name": _script},
        source_resource="cre:" + _name, source_resource_sha256=_hash,
        physical_source_ids=(f"cre:{_name}/item/{_record}",), required_transfers=(_name,),
        source_record={"byte_offset": _offset, "record_index": _record, "resref": _item,
                       "expiration": 0, "charges": [0, 0, 0], "flags": 0, "slots": [_slot]})
    TRANSFER_SPECS[_name] = _specification
# Item Randomiser's lists/duplicates/bg2.2da:20 replaces the selected source's
# carried sword with BLUN05; keep that ordinary fallback separate from its
# randomized reward.
TRANSFER_SPECS["trgeni01"]["replacement"] = "blun05"

# Unlike the dialogue-source duplicates above, row42 is already a physical CRE
# source. Keep its existing primary guard/replacement and only link its selected
# flag to the dialogue. Do not register an additional scripted source or copy.
_alias("udlesa", "sw1h39", 42, 45, "UDLESA", {"udlesa": tuple((state, 0) for state in range(6, 12))})
REWARDS["udlesa"].update(
    source_ids=(), authored_source_ids=("original-source:bg2:mode2:item:42",), grant_kind="transfer",
    inventory_target={"area": "ar2202", "creature_resource": "cre:udlesa", "script_name": "udlesa"},
    source_resource="cre:udlesa",
    source_resource_sha256="f51eaafa2f7c4df7b0af9c76fac2576a243929c31b57a965a326fb58e2b6b8c4",
    physical_source_ids=("cre:udlesa/item/2",), replacement="dwsw1h02",
    source_record={"byte_offset": 3676, "record_index": 2, "resref": "sw1h39",
                   "expiration": 0, "charges": [0, 0, 0], "flags": 0, "slots": [9]})
PHYSICAL_SOURCE_REWARDS = {"cre:udlesa/item/2": "udlesa"}
for _name, _row in REWARDS.items():
    _row.setdefault("resources", (_row["resource"],))
RESOURCE_REWARDS = {}
for _name, _row in REWARDS.items():
    for _resource in _row.get("dialogues", {_row["resource"].split(":")[1]: ()}):
        _kind = _row["resource"].split(":")[0]
        RESOURCE_REWARDS.setdefault(_kind + ":" + _resource, []).append(_name)
RESOURCE_REWARDS = {resource: tuple(names) for resource, names in RESOURCE_REWARDS.items()}
SOURCE_REWARDS = {identity: name for name, spec in REWARDS.items() for identity in spec["source_ids"]}
TARGET_REWARDS = {identity: name for name, spec in REWARDS.items() for identity in spec["target_ids"]}

# Original generic descriptions from the matching special WeiDU handlers.
# Keys are the strrefs of the text that names the reward (the same in EET and
# BG2:EE), values are source TRA entries.
TEXT_REFS = {
    "bcs:ddwell": {40850: 399},
    "dlg:ppdjinn": {48995: 99},
    "dlg:uhmay01": {32062: 1394, 32063: 1449, 32176: 1395, 32177: 1396,
                    32185: 1397, 32207: 1398, 49560: 1399, 32191: 1393},
    "dlg:gorcamb": {63681: 497, 64609: 497, 63683: 498, 64611: 498, 63719: 499},
    "dlg:sctelwyn": {29325: 111},
    "dlg:scsain": {28489: 116},
    "dlg:scyarryl": {29378: 117},
    "dlg:trgeni01": {43514: 110},
    "dlg:amcler01": {74025: 100},
    "dlg:udlesa": {52664: 104},
}
ACTION_TEXT_REFS = {"dlg:amcler01": (74025,)}
_TEXT_TRAILER = b"FLPRM2T1"
# DestroySelf is instant during dialogue; LuaAction is queued. A speaker whose
# carried reward is handed over by a queued transfer leaves after it instead.
_DEPARTURE = (b'EEex_LuaAction("EEex_Action_QueueResponseStringOnAIBase('
              b'[[DestroySelf()]],EEex_LuaAction_Object)")')
_EMPTY_OBJECT = re.compile(rb'(?:0 )+""')
# The engine's actions that put an item somewhere (ACTION.IDS of the 2.7
# games): opcode -> name, the dialogue argument and the compiled string that
# name the item they give. Other actions that name the reward take, use, show
# or test it; after a moved reward they find it gone, as triggers do when the
# player sold it, so they never stop a route.
_GIVING = {140: ("GiveItemCreate", 0, 0), 82: ("CreateItem", 0, 0), 15: ("GiveItem", 0, 0),
           14: ("GetItem", 0, 0), 256: ("CreateItemGlobal", 2, 1), 278: ("TakeItemReplace", 0, 0),
           356: ("TransformItem", 1, 1), 9: ("DropItem", 0, 0), 257: ("PickUpItem", 0, 0),
           363: ("AddStoreItem", 1, 1)}
_GIVING_CALLS = {name.lower(): text for name, text, _ in _GIVING.values()}


def source_spec(source_id: str) -> dict:
    if source_id not in SOURCE_REWARDS:
        raise ProfileError(f"Unsupported extended reward source: {source_id}")
    return deepcopy(REWARDS[SOURCE_REWARDS[source_id]])


def target_spec(target_id: str) -> dict:
    if target_id not in TARGET_REWARDS:
        raise ProfileError(f"Unsupported extended reward recipient: {target_id}")
    return deepcopy(REWARDS[TARGET_REWARDS[target_id]])


def transfer_spec(name: str) -> dict:
    """Inventory copies which must be guarded alongside a scripted source."""
    if name not in TRANSFER_SPECS:
        raise ProfileError(f"Unsupported carried reward: {name}")
    return deepcopy(TRANSFER_SPECS[name])


def resource_specs(resource: str) -> tuple[dict, ...]:
    """All routes with a grant in a resource; includes both independent ARAN rewards.

    Each copy names its route (the REWARDS key) under "route".
    """
    resource = resource.lower()
    if resource not in RESOURCE_REWARDS:
        raise ProfileError(f"Unsupported extended reward resource: {resource}")
    result = []
    for key in RESOURCE_REWARDS[resource]:
        spec = deepcopy(REWARDS[key])
        spec["route"] = key
        spec["resource"] = resource
        spec["quest"] = resource.split(":")[1]
        spec["branches"] = spec.get("dialogues", {}).get(resource.split(":")[1], spec["branches"])
        result.append(spec)
    return tuple(result)


def _label(resource: str) -> str:
    kind, name = resource.split(":")
    return f"{name.upper()}.{kind.upper()}"


def _names(text: bytes, item: str) -> bool:
    """Whether script text names the item as a quoted resref."""
    return re.search(b'"' + re.escape(item.encode("ascii")) + b'"', text, re.IGNORECASE) is not None


def _read_dialogue(data: bytes, resource: str) -> _Dialogue:
    try:
        return _Dialogue(data, resource.split(":")[1])
    except (GameTextError, ProfileError) as exc:
        raise ProfileError(f"{_label(resource)} cannot be read as a dialogue") from exc


def _outgoing(dialogue: _Dialogue, state: bytes, label: str) -> tuple[int, int]:
    first, count = struct.unpack_from("<II", state, 4)
    if first + count > len(dialogue.transitions):
        raise ProfileError(f"{label} has a state whose replies are outside its tables")
    return first, count


def _string(table: list[bytes], index: int, label: str) -> bytes:
    if index >= len(table):
        raise ProfileError(f"{label} has a reply whose text is outside its tables")
    return table[index]


def _edited(text: bytes, edits: list[tuple[int, int, bytes]]) -> bytes:
    """Replace non-overlapping spans of the original text."""
    result, at = [], 0
    for start, end, replacement in sorted(edits):
        if start < at:
            raise ProfileError("Two changes of one reward action overlap")
        result += [text[at:start], replacement]
        at = end
    return b"".join(result) + text[at:]


def _guard(spec: dict, selected: bool) -> bytes:
    return f'{"" if selected else "!"}Global("{spec["selected_global"]}","GLOBAL",1)\r\n'.encode()


def _consumed(spec: dict) -> bytes:
    return f'SetGlobal("{spec["consumed_global"]}","GLOBAL",1)'.encode()


def _event(spec: dict) -> bytes:
    return f'SetGlobal("{spec["event_global"]}","GLOBAL",1)'.encode()


def _mark(spec: dict, recipient: bytes) -> bytes:
    return (b"ActionOverride(" + recipient +
            f',SetGlobal("{spec["recipient_local"]}","LOCALS",1))\r\n'.encode())


def _callback(spec: dict, numeric: bool) -> bytes:
    target = spec["target_ids"]
    match = re.fullmatch(r"bg2:mode2:target:([1-9][0-9]*)", target[0]) if len(target) == 1 else None
    if match is None:
        raise ProfileError("The carried reward's transfer target changed")
    # The dialogue parser strips spaces and colons even inside Lua strings.
    # Parenthesize return; pass the row.
    if numeric:
        return f'EEex_LuaAction("return(FLPR_Transfer(EEex_LuaAction_Object,{match[1]}))")'.encode()
    return ('EEex_LuaAction("return FLPR_Transfer(EEex_LuaAction_Object,'
            f'[[original-target:{target[0]}]])")').encode()


def _gives_item(text: bytes, call, item: str) -> bool:
    """Whether a dialogue action puts the item somewhere (see _GIVING)."""
    index = _GIVING_CALLS.get(call.name.decode("ascii").lower())
    return index is not None and (string_argument(text, call, index) or b"").lower() == item.encode()


def _dialogue_grants(dialogue: _Dialogue, spec: dict, label: str,
                     only: dict[int, set[int]] | None = None) -> dict[tuple[int, int], object]:
    """Every reply that gives the route's item, with the call that gives it.

    A created reward is given with GiveItemCreate, a carried one with
    GiveItem: that call is the grant, one per reply, and gating the grants
    gates every way this dialogue gives the item. A reply that gives it
    another way, with another action or through another actor, cannot be
    gated. <only> limits the search to these replies (state -> ordinals).
    """
    item = spec["item"]
    wanted = "GiveItem" if spec.get("grant_kind") == "transfer" else "GiveItemCreate"
    shown = item.upper()
    grants = {}
    for state_index, state in enumerate(dialogue.states):
        if only is not None and state_index not in only:
            continue
        first, count = _outgoing(dialogue, state, label)
        for ordinal in range(count):
            if only is not None and ordinal not in only[state_index]:
                continue
            flags, _, _, _, action_id = struct.unpack_from("<5I", dialogue.transitions[first + ordinal])
            if not flags & 4:
                continue
            text = _string(dialogue.actions, action_id, label)
            if not _names(text, item):
                continue
            where = f"{label} state {state_index}"
            found = calls(text)
            if found is None:
                raise ProfileError(f"{where} names {shown} in actions that cannot be read")
            given = []
            for call in found:
                if not _names(text[call.start:call.end], item):
                    continue
                if named(call, wanted) and (string_argument(text, call, 0) or b"").lower() == item.encode():
                    given.append(call)
                elif named(call, "ActionOverride"):
                    # Another actor runs the action, and its recipient means
                    # what it means to that actor.
                    inner = calls(text, *call.args[1]) if len(call.args) == 2 else None
                    if inner is None:
                        raise ProfileError(f"{where} names {shown} in actions that cannot be read")
                    if any(_gives_item(text, nested, item) for nested in inner):
                        raise ProfileError(f"{where} gives {shown} through ActionOverride(), "
                                           "whose recipient the route cannot mark")
                elif _gives_item(text, call, item):
                    raise ProfileError(f"{where} also gives {shown} with {call.name.decode('ascii')}(), "
                                       "which the route cannot gate")
            if len(given) > 1:
                raise ProfileError(f"{where} gives {shown} twice in one reply")
            if given:
                if not argument(text, given[0], 1):
                    raise ProfileError(f"{where} gives {shown} without a recipient")
                grants[(state_index, ordinal)] = given[0]
    if not grants:
        raise ProfileError(f"{label} has no reply that gives {shown}")
    return grants


def _display_edits(text: bytes, refs: dict[int, int], shown: set[int]) -> list[tuple[int, int, bytes]]:
    """Point every original strref a grant's actions show at its new text; <shown> collects them."""
    edits = []
    for call in calls(text) or []:
        for index, span in enumerate(call.args):
            old = integer_argument(text, call, index)
            if old in refs:
                edits.append((*span, str(refs[old]).encode()))
                shown.add(old)
    return edits


def _departure_edits(text: bytes, where: str) -> list[tuple[int, int, bytes]]:
    found = calls(text)
    if found is None:
        if re.search(rb"destroyself", text, re.IGNORECASE):
            raise ProfileError(f"{where} leaves in actions that cannot be read")
        return []
    return [(call.start, call.end, _DEPARTURE) for call in found
            if named(call, "DestroySelf") and not call.args]


def _following(dialogue: _Dialogue, rows: list[bytes], label: str) -> list[int]:
    """States this dialogue can continue to after the given replies."""
    name = dialogue.name.lower()

    def continuations(replies):
        for row in replies:
            flags, = struct.unpack_from("<I", row)
            next_state, = struct.unpack_from("<I", row, 28)
            if (not flags & 8 and bytes(row[20:28]).rstrip(b"\0").lower() == name and
                    next_state < len(dialogue.states)):
                yield next_state

    seen, todo = set(), list(continuations(rows))
    while todo:
        state = todo.pop()
        if state in seen:
            continue
        seen.add(state)
        first, count = _outgoing(dialogue, dialogue.states[state], label)
        todo.extend(continuations(dialogue.transitions[first:first + count]))
    return sorted(seen)


def _patch_dialogue(data: bytes, specs: tuple[dict, ...], *, action_refs=None,
                    defer_departure=True, numeric_target=True, reasons: dict | None = None,
                    only: dict[str, dict[int, set[int]]] | None = None, resource: str | None = None,
                    shown: set[int] | None = None) -> bytes:
    """Gate every reply that gives a route's item, for each recognised route.

    A route whose grant is not recognised keeps its replies as they are; its
    reason goes to <reasons>. With no route recognised, nothing is prepared.
    <only> limits a route to the given replies, as in a dialogue that holds
    copies of its reward replies (<resource> names that dialogue); <shown>
    collects the strrefs of <action_refs> that a grant shows.
    """
    resource = resource or specs[0]["resource"]
    label = _label(resource)
    dialogue = _read_dialogue(data, resource)
    shown = set() if shown is None else shown
    grants, failures = {}, {}
    for spec in specs:
        try:
            grants[spec["route"]] = _dialogue_grants(dialogue, spec, label,
                                                     None if only is None else only[spec["route"]])
        except ProfileError as exc:
            failures[spec["route"]] = str(exc)
    if reasons is not None:
        reasons.update(failures)
    active = [spec for spec in specs if spec["route"] in grants]
    if not active:
        raise ProfileError(failures[specs[0]["route"]])
    carried = defer_departure and any(spec.get("inventory_target") for spec in active)
    later = []
    if carried:
        granting = []
        for spec in active:
            for state, ordinal in grants[spec["route"]]:
                first, _ = _outgoing(dialogue, dialogue.states[state], label)
                granting.append(dialogue.transitions[first + ordinal])
        later = _following(dialogue, granting, label)
    for state_index, state in enumerate(dialogue.states):
        first, count = _outgoing(dialogue, state, label)
        rows = []
        for ordinal, original in enumerate(dialogue.transitions[first:first + count]):
            relevant = [spec for spec in active if (state_index, ordinal) in grants[spec["route"]]]
            if not relevant:
                rows.append(original)
                continue
            where = f"{label} state {state_index}"
            flags, _, _, trigger_id, action_id = struct.unpack_from("<5I", original)
            prior = _string(dialogue.triggers, trigger_id, label) if flags & 2 else b""
            text = dialogue.actions[action_id]
            departures = _departure_edits(text, where) if carried else []
            for choices in product((False, True), repeat=len(relevant)):
                branch = bytearray(original)
                condition, edits = prior, list(departures)
                for spec, selected in zip(relevant, choices):
                    condition = joined(condition, _guard(spec, selected))
                    call = grants[spec["route"]][(state_index, ordinal)]
                    grant = text[call.start:call.end]
                    replacement = _consumed(spec) if selected else grant
                    # Keep this beside its grant, before EscapeArea/DestroySelf.
                    if spec.get("inventory_target"):
                        changed = replacement + b"\r\n" + _callback(spec, numeric_target)
                    else:
                        changed = _mark(spec, argument(text, call, 1)) + replacement + b"\r\n" + _event(spec)
                    edits.append((call.start, call.end, changed))
                if any(choices) and action_refs:
                    edits += _display_edits(text, action_refs, shown)
                dialogue.triggers.append(condition)
                dialogue.actions.append(_edited(text, edits))
                struct.pack_into("<I", branch, 0, flags | 2)
                struct.pack_into("<II", branch, 12, len(dialogue.triggers) - 1, len(dialogue.actions) - 1)
                rows.append(branch)
        if len(rows) != count:
            struct.pack_into("<II", state, 4, len(dialogue.transitions), len(rows))
            dialogue.transitions.extend(rows)
    # Keep the speaker until its queued transfer has run. ActionOverride
    # departures belong to other actors and stay intact.
    for state_index in later:
        state = dialogue.states[state_index]
        first, count = _outgoing(dialogue, state, label)
        rows, changed = [], False
        for original in dialogue.transitions[first:first + count]:
            flags, _, _, _, action_id = struct.unpack_from("<5I", original)
            text = _string(dialogue.actions, action_id, label) if flags & 4 else b""
            edits = _departure_edits(text, f"{label} state {state_index}") if flags & 4 else []
            if not edits:
                rows.append(original)
                continue
            branch = bytearray(original)
            struct.pack_into("<I", branch, 16, len(dialogue.actions))
            dialogue.actions.append(_edited(text, edits))
            rows.append(branch)
            changed = True
        if changed:
            struct.pack_into("<II", state, 4, len(dialogue.transitions), len(rows))
            dialogue.transitions.extend(rows)
    return dialogue.render()


def _valid_refs(refs, resource: str) -> bool:
    return (isinstance(refs, dict) and set(refs) == set(TEXT_REFS[resource]) and
            all(type(value) is int and 0 <= value < 0xFFFFFFFF for value in refs.values()))


def _gate_start_states(dlg: _Dialogue, states: list, starts: list[tuple[int, int]],
                       clones: dict[int, int], flag: str) -> None:
    """Gate each cloned start state and its clone on the route's flag.

    The engine tries start states in the order of their conditions in the
    state trigger table, so the gated pair takes the original condition's
    place. When every other start condition comes before the cloned ones,
    appending the pairs keeps that order (the layout earlier exports used).
    """
    if not starts:
        return
    cloned = {index for index, _ in starts}
    entries = [entry for _, entry in starts]
    others = {struct.unpack_from("<I", state, 12)[0] for index, state in enumerate(states)
              if index not in cloned} - {0xFFFFFFFF}
    old = list(dlg.state_triggers)

    def gate(entry: int, selected: bool) -> bytes:
        return joined(old[entry], f'{"" if selected else "!"}Global("{flag}","GLOBAL",1)\r\n'.encode())

    if entries == sorted(set(entries)) and all(entry > other for entry in entries for other in others):
        for index, entry in starts:
            for selected, target in ((False, states[index]), (True, dlg.states[clones[index]])):
                dlg.state_triggers.append(gate(entry, selected))
                struct.pack_into("<I", target, 12, len(dlg.state_triggers) - 1)
        return
    table, moved, pairs = [], {}, {}
    for position, text in enumerate(old):
        if position in others or position not in entries:
            moved[position] = len(table)
            table.append(text)
        for index, entry in starts:
            if entry == position:
                pairs[index] = (len(table), len(table) + 1)
                table += [gate(entry, False), gate(entry, True)]
    for index, state in enumerate(states):
        entry = struct.unpack_from("<I", state, 12)[0]
        if index in pairs:
            struct.pack_into("<I", state, 12, pairs[index][0])
            struct.pack_into("<I", dlg.states[clones[index]], 12, pairs[index][1])
        elif entry != 0xFFFFFFFF:
            struct.pack_into("<I", state, 12, moved[entry])
    dlg.state_triggers[:] = table


def _patch_text_dialogue(data: bytes, resource: str, refs: dict[int, int], *,
                         defer_departure=True, numeric_target=True) -> bytes:
    if not _valid_refs(refs, resource):
        raise ProfileError(f"{_label(resource)} requires allocated original reward text")
    specs = resource_specs(resource)
    if len(specs) != 1:
        raise ProfileError("A text route needs exactly one source flag")
    flag = specs[0]["selected_global"]
    action_refs = {old: refs[old] for old in ACTION_TEXT_REFS.get(resource, ())}
    shown: set[int] = set()
    dlg = _read_dialogue(_patch_dialogue(data, specs, action_refs=action_refs,
                                         defer_departure=defer_departure,
                                         numeric_target=numeric_target, shown=shown), resource)
    label = _label(resource)
    original_states = list(dlg.states)
    clones, found, starts = {}, set(shown), []
    for index, state in enumerate(original_states):
        text, first, count, entry = struct.unpack("<4I", state)
        _outgoing(dlg, state, label)
        transitions = dlg.transitions[first:first + count]
        replies = {struct.unpack_from("<I", row, 4)[0] for row in transitions
                   if struct.unpack_from("<I", row)[0] & 1}
        affected = (replies | {text}) & refs.keys()
        if not affected:
            continue
        found.update(affected)
        clone = bytearray(state)
        struct.pack_into("<I", clone, 0, refs.get(text, text))
        rows = [bytearray(row) for row in transitions]
        for row in rows:
            if struct.unpack_from("<I", row)[0] & 1:
                reply = struct.unpack_from("<I", row, 4)[0]
                struct.pack_into("<I", row, 4, refs.get(reply, reply))
        struct.pack_into("<II", clone, 4, len(dlg.transitions), len(rows))
        dlg.transitions.extend(rows)
        if entry != 0xFFFFFFFF:
            _string(dlg.state_triggers, entry, label)
            starts.append((index, entry))
        clones[index] = len(dlg.states)
        dlg.states.append(clone)
    _gate_start_states(dlg, original_states, starts, clones, flag)
    if found != set(refs):
        missing = ", ".join(map(str, sorted(set(refs) - found)))
        raise ProfileError(f"{label} no longer shows the text that names the reward ({missing})")
    # Redirect each incoming internal edge, including paths starting at an
    # unchanged external entry (the temple messengers enter sculptor27/28).
    # Cached cross-dialogue links never enter a changed text state directly.
    for state in dlg.states:
        first, count = _outgoing(dlg, state, label)
        rows = []
        for transition in dlg.transitions[first:first + count]:
            flags, _, _, trigger = struct.unpack_from("<4I", transition)
            destination = struct.unpack_from("<I", transition, 28)[0]
            internal = bytes(transition[20:28]).rstrip(b"\0").lower() == dlg.name.lower()
            if flags & 8 or not internal or destination not in clones:
                rows.append(transition)
                continue
            prior = _string(dlg.triggers, trigger, label) if flags & 2 else b""
            for selected in (False, True):
                row = bytearray(transition)
                dlg.triggers.append(joined(prior,
                    f'{"" if selected else "!"}Global("{flag}","GLOBAL",1)\r\n'.encode()))
                struct.pack_into("<I", row, 0, flags | 2)
                struct.pack_into("<I", row, 12, len(dlg.triggers) - 1)
                if selected:
                    struct.pack_into("<I", row, 28, clones[destination])
                rows.append(row)
        if len(rows) != count:
            struct.pack_into("<II", state, 4, len(dlg.transitions), len(rows))
            dlg.transitions.extend(rows)
    trailer = struct.pack("<I52sI", len(data), data[:52], len(refs))
    trailer += b"".join(struct.pack("<II", old, new) for old, new in sorted(refs.items()))
    return dlg.render() + trailer + _TEXT_TRAILER + struct.pack("<I", len(trailer))


def _markers(specs: tuple[dict, ...]) -> list[bytes]:
    names = {spec[key] for spec in specs for key in
             ("selected_global", "consumed_global", "event_global", "recipient_local") if spec[key]}
    return [b'"' + name.encode("ascii") + b'"' for name in sorted(names)] + [_DEPARTURE]


def _carries_patch(dialogue: _Dialogue, specs: tuple[dict, ...]) -> bool:
    markers = _markers(specs)
    return any(marker in text for text in dialogue.triggers + dialogue.actions for marker in markers)


def _changed_patch(resource: str) -> ProfileError:
    return ProfileError(f"{_label(resource)} carries a changed copy of the reward route's own patch")


def _text_original(data: bytes, resource: str) -> tuple[bytes, dict[int, int]] | None:
    """The original and text refs of a text route this adapter patched; None if unpatched."""
    if len(data) < 12 or data[-12:-4] != _TEXT_TRAILER:
        if _carries_patch(_read_dialogue(data, resource), resource_specs(resource)):
            raise _changed_patch(resource)
        return None
    length = struct.unpack_from("<I", data, len(data) - 4)[0]
    if length != 60 + len(TEXT_REFS[resource]) * 8 or length > len(data) - 12:
        raise _changed_patch(resource)
    trailer = data[-12 - length:-12]
    size, header, count = struct.unpack_from("<I52sI", trailer)
    if size < 52 or size > len(data) - length - 12 or count != len(TEXT_REFS[resource]):
        raise _changed_patch(resource)
    original = header + data[52:size]
    refs = dict(struct.unpack_from("<II", trailer, 60 + n * 8) for n in range(count))
    if not _valid_refs(refs, resource):
        raise _changed_patch(resource)
    variants = [{}]
    # Accept only the exact prior inventory callback patches on a rescan.
    # TRGENI had exports both before and after its departure-order fix.
    if resource_specs(resource)[0].get("inventory_target"):
        variants += [{"defer_departure": departure, "numeric_target": False}
                     for departure in ((True, False) if resource == "dlg:trgeni01" else (True,))]
    try:
        if any(_patch_text_dialogue(original, resource, refs, **variant) == data for variant in variants):
            return original, refs
    except ProfileError:
        pass
    raise _changed_patch(resource)


def _guard_calls(text: bytes, routes: dict[bytes, dict]) -> list[tuple[object, dict, bool]] | None:
    """The trailing campaign guards of a reply trigger, in order; None if unreadable."""
    found = calls(text)
    if found is None:
        return None
    guards = []
    for call in reversed(found):
        name = call.name.lower()
        flag = string_argument(text, call, 0)
        if (name not in (b"global", b"!global") or flag not in routes or len(call.args) != 3 or
                string_argument(text, call, 1) != b"GLOBAL" or integer_argument(text, call, 2) != 1):
            break
        guards.append((call, routes[flag], name == b"global"))
    return guards[::-1]


def _strip_grant(text: bytes, spec: dict, resource: str) -> bytes:
    """Undo one route's additions around its grant in an unselected reply."""
    if spec.get("inventory_target"):
        additions = [b"\r\n" + _callback(spec, True)]
    else:
        mark = re.compile(rb'ActionOverride\([^\r\n]*?,SetGlobal\("' +
                          re.escape(spec["recipient_local"].encode()) + rb'","LOCALS",1\)\)\r\n')
        marks = mark.findall(text)
        if len(marks) != 1:
            raise _changed_patch(resource)
        additions = [marks[0], b"\r\n" + _event(spec)]
    for addition in additions:
        if text.count(addition) != 1:
            raise _changed_patch(resource)
        text = text.replace(addition, b"", 1)
    return text


def _logical(data: bytes, resource: str) -> tuple:
    """A dialogue's content as the engine reads it, without its table layout."""
    dialogue = _read_dialogue(data, resource)
    label = _label(resource)
    states = []
    for state in dialogue.states:
        text, first, count, entry = struct.unpack("<4I", state)
        _outgoing(dialogue, state, label)
        rows = []
        for row in dialogue.transitions[first:first + count]:
            flags, reply, journal, trigger, action, next_dialogue, next_state = struct.unpack("<5I8sI", row)
            rows.append((flags, reply, journal,
                         _string(dialogue.triggers, trigger, label) if flags & 2 else None,
                         _string(dialogue.actions, action, label) if flags & 4 else None,
                         next_dialogue, next_state))
        states.append((text, None if entry == 0xFFFFFFFF else _string(dialogue.state_triggers, entry, label),
                       tuple(rows)))
    return data[:8], data[48:52], tuple(states)


def _static_original(data: bytes, resource: str) -> bytes | None:
    """Undo this adapter's patch of a dialogue without a trailer; None if unpatched.

    The result reads as the original did. Patching it again must give the same
    dialogue as <data>, or <data> is not exactly this adapter's patch.
    """
    specs = resource_specs(resource)
    dialogue = _read_dialogue(data, resource)
    if not _carries_patch(dialogue, specs):
        return None
    try:
        original = _unpatched_dialogue(dialogue, specs, resource)
        same = _logical(_patch_dialogue(original, specs), resource) == _logical(data, resource)
    except ProfileError:
        raise _changed_patch(resource) from None
    if not same:
        raise _changed_patch(resource)
    return original


def _unpatched_dialogue(dialogue: _Dialogue, specs: tuple[dict, ...], resource: str) -> bytes:
    label = _label(resource)
    routes = {spec["selected_global"].encode(): spec for spec in specs}
    for state in dialogue.states:
        first, count = _outgoing(dialogue, state, label)
        rows, restored, at = dialogue.transitions[first:first + count], [], 0
        while at < len(rows):
            # A gated reply became 2^n replies, the first one with every
            # route unselected; it keeps each original grant.
            row = bytearray(rows[at])
            flags, _, _, trigger_id, action_id = struct.unpack_from("<5I", row)
            trigger = _string(dialogue.triggers, trigger_id, label) if flags & 2 else b""
            action = _string(dialogue.actions, action_id, label) if flags & 4 else b""
            guards = (_guard_calls(trigger, routes) or []) if flags & 2 else []
            if any(selected for _, _, selected in guards) or at + 2 ** len(guards) > len(rows):
                raise _changed_patch(resource)
            at += 2 ** len(guards)
            if guards:
                prior = trigger[:guards[0][0].start]
                for _, spec, _ in guards:
                    action = _strip_grant(action, spec, resource)
                if prior:
                    dialogue.triggers.append(prior)
                    struct.pack_into("<I", row, 12, len(dialogue.triggers) - 1)
                else:
                    struct.pack_into("<I", row, 0, flags & ~2)
            action = action.replace(_DEPARTURE, b"DestroySelf()")
            if flags & 4 and action != dialogue.actions[action_id]:
                dialogue.actions.append(action)
                struct.pack_into("<I", row, 16, len(dialogue.actions) - 1)
            restored.append(row)
        if restored != rows:
            struct.pack_into("<II", state, 4, len(dialogue.transitions), len(restored))
            dialogue.transitions.extend(restored)
    return dialogue.render()


def _marker_action(grant, spec: dict, newline: bytes) -> bytes:
    # In compiled ActionOverride the action's first OB is the actor override.
    # Copy GiveItemCreate's second OB verbatim, retaining every object selector.
    marker = _global_action(spec["recipient_local"], newline)
    marker = marker.replace(b'"GLOBAL', b'"LOCALS', 1)
    empty = b'0 0 0 0 0 0 0 0 0 0 0 0 ""'
    return marker.replace(empty + b"OB" + newline, grant.objects[1] + b"OB" + newline, 1)


def _with_integer(action: bytes, newline: bytes, value: int) -> bytes:
    """A compiled action with its first integer parameter replaced."""
    start = action.rindex(b"OB" + newline) + len(b"OB" + newline)
    number = re.match(rb"-?[0-9]+", action[start:])
    return action[:start] + str(value).encode() + action[start + number.end():]


def _script_grant_opcode(spec: dict) -> int:
    # A reward container creates the item in itself; others give it.
    return 82 if spec["recipient_local"] is None else 140


def _script_grants(script, spec: dict, label: str) -> dict[int, tuple]:
    """Each block that gives the route's item: its parts, its grants and displays.

    A block runs one of its alternative responses, so each response may give
    the item once, with the route's grant. The displays are the actions that
    show the text naming the reward.
    """
    item = spec["item"].encode("ascii").lower()
    container = spec["recipient_local"] is None
    opcode = _script_grant_opcode(spec)
    shown = spec["item"].upper()
    refs = TEXT_REFS.get(spec["resource"], {})
    found = {}
    for index, block in enumerate(script.blocks):
        if not _names(block, spec["item"]):
            continue
        where = f"{label} block {index}"
        head, conditions, responses = block_parts(block, script.newline)
        actions = block_actions(responses, script.newline)
        grants, displays = [], [action for action in actions if action.integer in refs]
        for first, last in response_spans(responses, script.newline):
            given = []
            for action in actions:
                name, _, string = _GIVING.get(action.opcode, (None, 0, 0))
                if not first <= action.start < last or name is None or action.strings[string].lower() != item:
                    continue
                if action.opcode != opcode:
                    raise ProfileError(f"{where} also gives {shown} with {name}(), which the route cannot gate")
                if not container and not _EMPTY_OBJECT.fullmatch(action.objects[0]):
                    # Its recipient means what it means to the other actor.
                    raise ProfileError(f"{where} gives {shown} through ActionOverride(), "
                                       "whose recipient the route cannot mark")
                given.append(action)
            if len(given) > 1:
                raise ProfileError(f"{where} gives {shown} twice in one response")
            grants += given
        if grants:
            found[index] = (head, conditions, responses, grants, displays)
    if not found:
        raise ProfileError(f"{label} has no block that gives {shown}")
    if refs and not any(row[4] for row in found.values()):
        missing = ", ".join(map(str, sorted(refs)))
        raise ProfileError(f"{label} no longer shows the text that names the reward ({missing})")
    return found


def _patch_script(data: bytes, spec: dict, text_ref: int | None = None) -> bytes:
    label = _label(spec["resource"])
    if spec["resource"] in TEXT_REFS and (type(text_ref) is not int or not 0 <= text_ref < 0xFFFFFFFF):
        raise ProfileError(f"{label} requires allocated original reward text")
    with naming(label):
        script = script_blocks(data)
        found = _script_grants(script, spec, label)
    newline = script.newline
    container = spec["recipient_local"] is None
    event = _global_action(spec["event_global"], newline)
    blocks = list(script.blocks)
    for index, (head, conditions, responses, grants, displays) in found.items():
        variants = []
        for selected in (False, True):
            edits = []
            for grant in grants:
                mark = b"" if container else _marker_action(grant, spec, newline)
                replacement = (_global_action(spec["consumed_global"], newline) if selected
                               else responses[grant.start:grant.end])
                edits.append((grant.start, grant.end, mark + replacement + event))
            if selected:
                edits += [(display.start, display.end,
                           _with_integer(responses[display.start:display.end], newline, text_ref))
                          for display in displays]
            variants.append(head + b"CO" + newline + conditions +
                            _global_guard(spec["selected_global"], selected, newline) + b"CO" + newline +
                            _edited(responses, edits))
        blocks[index] = b"".join(variants)
    return script.prefix + b"".join(blocks) + script.suffix


def _script_original(data: bytes, spec: dict) -> tuple[bytes, int | None, bool]:
    """The original of a reward script, its text ref and whether we patched it."""
    resource = spec["resource"]
    newline = b"\r\n" if b"\r\n" in data else b"\n"
    unselected = _global_guard(spec["selected_global"], False, newline)
    selected = _global_guard(spec["selected_global"], True, newline)
    if b'"GLOBAL' + spec["selected_global"].encode() + b'"' not in data:
        return data, None, False
    try:
        original, numbers = _unpatched_script(data, spec, newline, selected, unselected)
    except ProfileError:
        raise _changed_patch(resource) from None
    # The allocated text is one of the selected copies' numbers; only a
    # possible strref can be it.
    candidates = (sorted(number for number in numbers if 0 <= number < 0xFFFFFFFF)
                  if resource in TEXT_REFS else [None])
    for ref in candidates:
        try:
            if _patch_script(original, spec, ref) == data:
                return original, ref, True
        except ProfileError:
            break
    raise _changed_patch(resource)


def _unpatched_script(data: bytes, spec: dict, newline: bytes, selected: bytes,
                      unselected: bytes) -> tuple[bytes, set[int]]:
    """Drop the selected copies of gated blocks and undo the unselected ones.

    Also returns the integers of the selected copies' actions, among them the
    allocated reward text.
    """
    resource = spec["resource"]
    script = script_blocks(data)
    restored, numbers = [], set()
    container = spec["recipient_local"] is None
    event = _global_action(spec["event_global"], newline)
    for block in script.blocks:
        if selected in block:
            numbers.update(action.integer for action in block_actions(block_parts(block, newline)[2], newline))
            continue
        if unselected in block:
            head, conditions, responses = block_parts(block.replace(unselected, b"", 1), newline)
            item = spec["item"].encode("ascii").lower()
            opcode = _script_grant_opcode(spec)
            grants = [action for action in block_actions(responses, newline)
                      if action.opcode == opcode and action.strings[0].lower() == item]
            if not grants:
                raise _changed_patch(resource)
            # Each grant kept its marker before it and the event after it.
            parts, at = [], 0
            for grant in grants:
                mark = b"" if container else _marker_action(grant, spec, newline)
                start, end = grant.start - len(mark), grant.end + len(event)
                if start < at or responses[start:grant.start] != mark or responses[grant.end:end] != event:
                    raise _changed_patch(resource)
                parts += [responses[at:start], responses[grant.start:grant.end]]
                at = end
            block = head + b"CO" + newline + conditions + b"CO" + newline + b"".join(parts) + responses[at:]
        restored.append(block)
    return script.prefix + b"".join(restored) + script.suffix, numbers


_Examined = namedtuple("_Examined", "original own refs")


def _examine(resource: str, data: bytes) -> _Examined:
    """The unpatched resource (exact, or as the engine reads it), and our patch's text refs."""
    if not isinstance(data, bytes):
        raise ProfileError("Unsupported reward resource bytes")
    if resource.startswith("bcs:"):
        original, ref, own = _script_original(data, resource_specs(resource)[0])
        return _Examined(original, own, ref)
    if resource in TEXT_REFS:
        recovered = _text_original(data, resource)
        return _Examined(data, False, None) if recovered is None else _Examined(recovered[0], True, recovered[1])
    original = _static_original(data, resource)
    return _Examined(data, False, None) if original is None else _Examined(original, True, None)


def prepare_ddwell_text(tlk: bytes, language: str = "en_us") -> tuple[bytes, int]:
    """Append the original generic well description; never replace shared text."""
    updated, refs = prepare_reward_texts(tlk, ("bcs:ddwell",), language)
    return updated, refs["bcs:ddwell"][40850]


def prepare_reward_texts(tlk: bytes, resources, language: str = "en_us") -> tuple[bytes, dict]:
    """Allocate all requested original TRA descriptions in one append/dedup pass."""
    if language.lower() != "en_us":
        raise ProfileError("Item Randomiser's reward texts exist only in English")
    needed = sorted(set(resource.lower() for resource in resources) & TEXT_REFS.keys())
    source_texts, entries, texts = {}, [], []
    for resource in needed:
        path = "languages/english/" + ("ppdjinn.tra" if resource == "dlg:ppdjinn" else "in-game.tra")
        if path not in source_texts:
            source_texts[path] = _texts(path)
        for old, tra in TEXT_REFS[resource].items():
            entries.append((resource, old))
            texts.append(source_texts[path][tra])
    updated, refs = _append_texts(tlk, tuple(texts))
    result = {resource: {} for resource in needed}
    for (resource, old), ref in zip(entries, refs):
        result[resource][old] = ref
    return updated, result


def _resource_key(resource: str) -> str:
    name = resource.lower().split(":")[-1].split(".")[0]
    candidates = [key for key in RESOURCE_REWARDS if key.split(":")[1] == name]
    if len(candidates) != 1:
        raise ProfileError(f"Unsupported extended reward resource: {resource}")
    return candidates[0]


def own_patch(resource: str, data: bytes) -> bool:
    """Whether the bytes are this adapter's own patch, verified exactly.

    False for an unpatched resource; raises for a changed copy of our patch.
    """
    return _examine(_resource_key(resource), data).own


def route_reasons(resource: str, data: bytes) -> dict[str, str]:
    """Why each route with a grant in this resource cannot be prepared from these bytes.

    Routes missing from the result can be prepared; this runs the same patch
    that the export runs, with stand-in text references.
    """
    resource = _resource_key(resource)
    specs = resource_specs(resource)
    reasons: dict[str, str] = {}
    try:
        examined = _examine(resource, data)
        if resource.startswith("bcs:"):
            _patch_script(examined.original, specs[0], 0)
        elif resource in TEXT_REFS:
            _patch_text_dialogue(examined.original, resource, {old: old for old in TEXT_REFS[resource]})
        else:
            _patch_dialogue(examined.original, specs, reasons=reasons)
    except ProfileError as exc:
        return {spec["route"]: reasons.get(spec["route"], str(exc)) for spec in specs}
    return reasons


def patch_reward(resource: str, data: bytes, *, refs: dict[int, int] | None = None,
                 ddwell_ref: int | None = None) -> bytes:
    """Patch a resource or re-export our own patch; text routes need refs on their first export."""
    resource = _resource_key(resource)
    examined = _examine(resource, data)
    specs = resource_specs(resource)
    if resource.startswith("bcs:"):
        if refs is not None:
            if resource not in TEXT_REFS or set(refs) != set(TEXT_REFS[resource]):
                raise ProfileError(f"{_label(resource)} requires its allocated original reward text")
            ddwell_ref = next(iter(refs.values()))
        return _patch_script(examined.original, specs[0],
                             examined.refs if ddwell_ref is None else ddwell_ref)
    if resource in TEXT_REFS:
        return _patch_text_dialogue(examined.original, resource, examined.refs if refs is None else refs)
    if examined.own:
        return data
    return _patch_dialogue(data, specs)
