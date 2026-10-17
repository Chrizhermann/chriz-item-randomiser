"""Reviewed Mode 2 container quest conditions, gated per campaign.

The Host observes a delivered reward becoming absent from its loaded container
and sets the original saved latch. This module only patches the latch's consumers;
it never substitutes current party possession for having taken the treasure.
Set activation_global at campaign creation only for actual assignments. Observe
each assigned target only after confirmed delivery, and latch 0 -> 1 once; an
unloaded/unresolved container is not an empty container. Samia's two assignments
share one latch, so either observed absence suffices. Never clear it on item loss.
See Item Randomiser's lib/weidu_action.tpa and baf/flrkeep*.baf, flrsamia.baf
and flrsafan.baf.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re
import struct

from .profiles import ProfileError
from .quest_options import _Dialogue, _texts
from .quest_rewards import _blocks, _global_guard, _newline
from .game_text import _append_texts


def _target(game, row, area, container, item, flag, latch, scope, resources):
    return {"target_id": f"{game}:mode2:target:{row}", "area": area,
            "container_index": container, "physical_target_id": f"are:{area}/container/{container}",
            "original_item": item, "activation_global": flag,
            "latch_global": latch, "latch_scope": scope, "resources": resources}


_ROWS = (
    _target("bg2", 13, "ar1303", 0, "ax1h13", "FLPR_CKEEP1", "flRobbedGolems1", "AR1303",
            ("bcs:kpgol01", "bcs:kpgol03")),
    _target("bg2", 43, "ar1303", 1, "bow12", "FLPR_CKEEP2", "flRobbedGolems2", "AR1303",
            ("bcs:kpgol02", "bcs:kpgol04")),
    _target("bg2", 3, "ar1303", 2, "hamm04", "FLPR_CKEEP3", "flRobbedGolems3", "AR1303",
            ("bcs:kpgol05", "bcs:kpgol06")),
    # These are the handler's item names, not the source row's Ident pairing.
    _target("bg2", 53, "ar1202", 8, "shld21", "FLPR_CSAMIA", "flSamiaTreasure", "AR1202",
            ("bcs:ar1202", "bcs:samia", "dlg:samia")),
    _target("bg2", 28, "ar1202", 8, "sw1h32", "FLPR_CSAMIA", "flSamiaTreasure", "AR1202",
            ("bcs:ar1202", "bcs:samia", "dlg:samia")),
    _target("bg1", 22, "bg3601", 0, "book03", "FLPR_CSAFANA", "flSafanaTreasure", "GLOBAL",
            ("bcs:safana", "dlg:safanj")),
    _target("bg2", 61, "ar1514", 0, "bow19b", "FLPR_CPPCHST", "RobbedGolem", "GLOBAL",
            ("bcs:ppchest",)),
)
TARGETS = {row["target_id"]: row for row in _ROWS}
RESOURCE_TARGETS = {resource: tuple(row["target_id"] for row in _ROWS if resource in row["resources"])
                    for row in _ROWS for resource in row["resources"]}
# Compiled block indexes / DLG state-trigger table indexes in the reviewed EET.
SITES = {
    "bcs:kpgol01": (1, (0,)), "bcs:kpgol02": (1, (0,)), "bcs:kpgol03": (1, (0,)),
    "bcs:kpgol04": (1, (0,)), "bcs:kpgol05": (1, (0,)), "bcs:kpgol06": (1, (0,)),
    "bcs:ar1202": (24, (15, 16)), "bcs:samia": (1, (0,)),
    "bcs:safana": (16, (7, 8, 9)), "bcs:ppchest": (1, (0,)),
    "dlg:samia": (None, (1,)), "dlg:safanj": (None, (0, 2, 8)),
}
ORIGINAL_HASHES = {
    "bcs:kpgol01": "7e569c1e414c4afef74b274515d073c04dd92ba2fa0ab0f96b8bbccf2c80cfa6",
    "bcs:kpgol02": "3dd4220a9bc7ae4b5778f3b6a1dc7438d943b138cb9383aac26d8835489a5431",
    "bcs:kpgol03": "82e7a96eac8a9ad8b913982ca8d03bdc824346de42267d80065db5d3155077f0",
    "bcs:kpgol04": "d33714c44ebc89dd5c2fef834781cfbfe69f1f8cd0a9ff9de21fee823b4efc7d",
    "bcs:kpgol05": "d473eebb61a8a03d8bc0b1c993e0810c67a6e01225687b5cba035c6a5e067dde",
    "bcs:kpgol06": "157cc4c98135416842176cb76968280630160156e4039ba0e42cf744d33d0a66",
    "bcs:ar1202": "d6c3de8366f99088d41e1c01a4a8d8811add81771926d9d7d70ef5193fca2280",
    "bcs:samia": "823d03307573daaa08ad7c853879e1b62796d67212b4e62e6d6ba66f0cac32c9",
    "bcs:safana": "afc23b13dfd4e87978997e55e35c6f4f4d14238647ef1197653184e55539bf83",
    "bcs:ppchest": "446b07d5de81386e5e7f6b49efb323c1d617222cc4714f1eae400e1c08c049d2",
    "dlg:samia": "3572813dfb538c88953709b55e6bcd48139ae24c9a9b84e176f9920652a88254",
    "dlg:safanj": "8a890ecdc434267ed79661db132b140c1371a068de234d8caba541aa962da252",
}
TEXT_RESOURCES = frozenset(("bcs:kpgol01", "bcs:kpgol02", "bcs:kpgol05"))
_TRAILER = b"FLPRCON1"


def target_spec(target_id: str) -> dict:
    if target_id not in TARGETS:
        raise ProfileError(f"Unsupported container quest target: {target_id}")
    return deepcopy(TARGETS[target_id])


def prepare_container_texts(tlk: bytes, resources, language="en_us") -> tuple[bytes, dict]:
    needed = sorted(set(resources) & TEXT_RESOURCES)
    if not needed:
        return tlk, {}
    if language.lower() != "en_us":
        raise ProfileError("Container quest text currently requires the original English translation")
    updated, refs = _append_texts(tlk, (_texts("languages/english/in-game.tra")[101],))
    return updated, {resource: {38758: refs[0]} for resource in needed}


def _trigger(opcode, value=0, negate=0, text="", selector=0, newline=b"\n"):
    return newline.join((b"TR", f'{opcode} {value} {negate} 0 0 "{text}" "" OB'.encode(),
                         f'0 0 0 0 0 0 0 {selector} 0 0 0 0 ""OB'.encode(), b"TR", b""))


def _latch(spec, value, newline):
    return _trigger(16399, value, text=spec["latch_scope"] + spec["latch_global"], newline=newline)


def _item_condition(conditions, resource, spec, newline):
    """Return one exact original item predicate and the corresponding latch value."""
    if spec["activation_global"] == "FLPR_CSAMIA":
        for items in (("sw1h32", "shld21"), ("SW1H32", "SHLD21")):
            old = _trigger(16521, 2, newline=newline) + b"".join(
                _trigger(16450, text=item, newline=newline) for item in items)
            if conditions.count(old) == 1:
                return old, 1
    else:
        for negate in (0, 1):
            old = _trigger(16450, negate=negate, text=spec["original_item"].upper(), newline=newline)
            if conditions.count(old) == 1:
                return old, 1 - negate
    raise ProfileError(f"{resource} container quest item condition changed")


def _patch_script(resource, original, ref):
    count, sites = SITES[resource]
    newline = _newline(original)
    blocks, prefix, suffix = _blocks(original, count, newline)
    spec = TARGETS[RESOURCE_TARGETS[resource][0]]
    result = []
    for index, block in enumerate(blocks):
        if index not in sites:
            result.append(block)
            continue
        head, conditions, response = block.split(b"CO" + newline)
        excluded = (head + b"CO" + newline + conditions +
                    _global_guard(spec["activation_global"], False, newline) + b"CO" + newline + response)
        if resource != "bcs:ppchest":
            old, value = _item_condition(conditions, resource, spec, newline)
            changed = conditions.replace(old, _latch(spec, value, newline), 1)
            selected_response = response
            if resource in TEXT_RESOURCES:
                if type(ref) is not int or not 0 <= ref < 0xFFFFFFFF:
                    raise ProfileError("Container quest requires allocated golem text")
                old_text = b'38758 0 0 0 0"" "" AC' + newline
                if response.count(old_text) != 1:
                    raise ProfileError("Container quest golem display changed")
                selected_response = response.replace(old_text, str(ref).encode() + old_text[5:])
            result.append(head + b"CO" + newline + changed +
                          _global_guard(spec["activation_global"], True, newline) + b"CO" + newline + selected_response)
        # PPCHST's original setter is disabled only when the observer owns it.
        result.append(excluded)
    return prefix + b"".join(result) + suffix


def _patch_dialogue(resource, original):
    dialogue = _Dialogue(original, resource[4:])
    spec = TARGETS[RESOURCE_TARGETS[resource][0]]
    for index in SITES[resource][1]:
        prior = dialogue.state_triggers[index]
        pattern = (rb'(?im)^[ \t]*OR\(2\)\r?\nPartyHasItem\("sw1h32"\)\r?\nPartyHasItem\("shld21"\)\r?\n'
                   if resource == "dlg:samia" else rb'(?im)^!?PartyHasItem\("BOOK03"\)\r?\n')
        matches = list(re.finditer(pattern, prior))
        if len(matches) != 1:
            raise ProfileError(f"{resource} container quest dialogue condition changed")
        match = matches[0]
        old = match[0]
        negative = old.startswith(b"!")
        terms = old.strip().splitlines()
        if resource == "dlg:samia":
            terms = terms[1:]  # Flatten the old OR into the first new OR group.
        flag = spec["activation_global"]
        # (selected OR original) AND (!selected OR latch). No nested OR/AND.
        changed = f'OR({len(terms) + 1})\r\nGlobal("{flag}","GLOBAL",1)\r\n'.encode()
        changed += b"\r\n".join(terms) + b"\r\n"
        changed += (f'OR(2)\r\n!Global("{flag}","GLOBAL",1)\r\n'
                    f'Global("{spec["latch_global"]}","{spec["latch_scope"]}",{0 if negative else 1})\r\n').encode()
        dialogue.state_triggers[index] = prior[:match.start()] + changed + prior[match.end():]
    # _Dialogue appends tables and leaves the original body recoverable.
    return dialogue.render() + struct.pack("<I52s", len(original), original[:52]) + _TRAILER


def _recover(resource, data):
    if hashlib.sha256(data).hexdigest() == ORIGINAL_HASHES[resource]:
        return data, None
    ref = None
    if resource.startswith("dlg:"):
        if len(data) < 64 or not data.endswith(_TRAILER):
            raise ProfileError("Container quest dialogue changed")
        size, header = struct.unpack("<I52s", data[-64:-8])
        if not 52 <= size <= len(data) - 64:
            raise ProfileError("Container quest dialogue recovery bounds changed")
        original = header + data[52:size]
    else:
        newline = _newline(data)
        count, sites = SITES[resource]
        blocks, prefix, suffix = _blocks(data, count + (0 if resource == "bcs:ppchest" else len(sites)), newline)
        flag = TARGETS[RESOURCE_TARGETS[resource][0]]["activation_global"]
        selected, excluded = (_global_guard(flag, value, newline) for value in (True, False))
        restored = []
        for block in blocks:
            if selected in block:
                if resource in TEXT_RESOURCES:
                    matches = re.findall(rb'(?m)^151OB\r?\n(?:[^\n]*\n){5}(\d+) 0 0 0 0"" "" AC\r?$', block)
                    if len(matches) != 1:
                        raise ProfileError("Container quest golem text recovery changed")
                    ref = int(matches[0])
            else:
                restored.append(block.replace(excluded, b""))
        original = prefix + b"".join(restored) + suffix
    if hashlib.sha256(original).hexdigest() != ORIGINAL_HASHES[resource]:
        raise ProfileError("Container quest original resource changed")
    regenerated = (_patch_dialogue(resource, original) if resource.startswith("dlg:")
                   else _patch_script(resource, original, ref))
    if regenerated != data:
        raise ProfileError("Container quest patch changed")
    return original, ref


def original_container_resource(resource: str, data: bytes) -> bytes:
    """Recover only pristine reviewed bytes or an exact patch made by this module."""
    resource = resource.lower()
    if resource not in ORIGINAL_HASHES or not isinstance(data, bytes):
        raise ProfileError("Unsupported container quest resource")
    return _recover(resource, data)[0]


def patch_container_resource(resource: str, data: bytes, *, refs=None) -> bytes:
    """Canonical campaign-gated patch; refs maps original golem strref 38758."""
    resource = resource.lower()
    if resource not in ORIGINAL_HASHES or not isinstance(data, bytes):
        raise ProfileError("Unsupported container quest resource")
    original, old_ref = _recover(resource, data)
    if resource.startswith("dlg:"):
        return _patch_dialogue(resource, original)
    return _patch_script(resource, original, (refs or {}).get(38758, old_ref))
