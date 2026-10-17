"""Campaign-gated original Mode 2 direct grants and plain dialogue aliases.

The resource hashes pin the reviewed EET scripts. Only the grant is suppressed;
its surrounding actions, conditions, dialogue replies and journal fields stay
intact. The reward event captures the original recipient at the grant position.
See lib/weidu_action.tpa's generic BCS/DLG handlers and DDWELL special case.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from itertools import product
import re
import struct

from .profiles import ProfileError
from .quest_options import _Dialogue, _texts
from .quest_rewards import _blocks, _global_action, _global_guard, _grant_action, _newline
from .game_text import _append_texts

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
ORIGINAL_HASHES = {
    "uhogre01": "8706469468f7277542ba0e6d7fcf44b47a15d36c3879f505464e0a119be00b97",
    "asysta01": "f0f1773357f2280b7b6a05695ff278650110dc60898e2c7ddc1241867782fadd",
    "ddwell": "4d2232892ce30f7e55333f696bb2717e5208e3d588e0ef77e990fc0077955544",
    "mgkhol01": "d4762221de29275f29a3516980cf2b6360612a1670ad5733f3e599a1ba72e3ea",
    "ppstat01": "7022962710da01f3d66088d8f7c5aa2a7963d7e994ff0d55f61dc5b64c9673ae",
    "riftg03": "4bb8a0866e3470ae558c108160ebef18c33de9f14ad3181ccf33ebd0d5e4912a",
    "trhmer01": "e5651fc5ef72780fe2ce9bb6ec8c3632665a5279d41cf79244fbef393304752e",
    "udsvir03": "5918919ea366eceb27f4096f025cde79c44310a9e57bcd49d123fdbb48352994",
    "ammayor": "d37409df94285e4bdce88bdfdb38382790dca567dc6304744244d9d5504030d6",
    "bhoisig": "5a55df4f2a0b814027735467fbf69017d9a13b0cd749d8566cf4f57c1f932377",
    "bhnalla": "038818ff7d8e4fc3a62e10530b74764657e26b3f44203ebd1877db7535d17ab3",
    "bharval": "ef4e704dc616f3658410cb798bb48b323b49b3b419986ff0345eb81979fb2730",
    "hendak": "0f5a4956e7ee05cb5d6a02e2d61bcf8be0ab575ecd921af31656578330b4a804",
    "lehtin": "a067828ca2055d4c3fd406c43672d9da3547e92b3da67a5eb84b341fdbebd2d1",
    "udsilver": "48f697db47942b74b4530975a5a4a46cac1a0cc850453b2b9d50ab173b01090c",
    "uddemon": "582fbcfecf3e71363dff1aa9bcef4dbc202d28c3af6e66e6a29840b342f1bdfd",
    "gortan1": "27a8575386e30a36a1f00f63c2004a095f301b511cd310eb03e36606265f3f3c",
    "gorbat1": "c1da7c158296ed5e553942a735ebc84773007b099a4eadb1cc78a50142a0088a",
    "gorlum2": "8228c02ac6dee8ac4652ad550a0fef83a8ddf2d59400fcb7c1e025f6395b5e9c",
    "aran": "da4bebc9ceb58fad2d34cf261b73d458ece55ad70128c656ede9e44a8631cee1",
    "ppdjinn": "06c1671065b549921263594db378d7610223cb16b70b7f718b3885b45b32af38",
    "uhmay01": "4213656595fdde6afeefee881597ddd62814ba886747bc4d60cfca25a2d852be",
    "gorcamb": "76cdb11e99cdbd676d7af08f48713fceb01061ea6180e762e6be1c997f3b1de4",
    "sctelwyn": "d5dd048c43e16ba937ff583eef8f5966e549fb0c4e3f92679883d35fcdda1759",
    "scsain": "c2a43b0d67eca9b531aa591463f74d5333225a95f0be87f8d03ed1cf91f8dafe",
    "scyarryl": "f96880bdabcfe8393d9ad6df502d3d001e2d79913736e480f6cc717f012dd367",
    "trgeni01": "4220763c55e065bfb2f500aa9d615a1a1e112ae3fc60eee061a3d9f3f59f7549",
    "loutcapt": "1bb3dc1337c087cc049f0d4fa431f4a7f909dbfacabf4dd30087d8b4f52f2d98",
    "amcler01": "5bac12a183463f6faa912b15e80c45c321cd2189f318c5056cf38b2150a6d79e",
    "udlesa": "6c518483372ebe4073986dc2d41e4f7ae73a7bce133aab4289a29ef1be7c7e50",
}
PATCHED_HASHES = {
    "uhogre01": "722213d1f53966780ab8bba2e2dbe400e64c14ced6da2ff9f48b43749b629e25",
    "asysta01": "26bf2a67793e7b05fe35781f85ec4885aac806520927c35deba9aed5a2f5b841",
    # DDWELL includes a caller-allocated TLK reference, verified by reconstruction.
    "mgkhol01": "3a47fea6c2cdb1405267342bad65ecc58741481006153dd3a15b43c3807decfd",
    "ppstat01": "69505c964ee036a6d9b974407e34cbd4ff35f4cebd10373819c5ef2f5d50f73a",
    "riftg03": "34e7c5e57d9e69394b40ce09f4c5cd5a5e6249d871b676c82bafbd3d707f3ff5",
    "trhmer01": "21aac581490c963ecf778f2ffc760bb3c56cce5be72fd637f783e70eccdbdd75",
    "udsvir03": "2003095671c146bb3d06b92fcc17df06dc18e5be4ab59a0e5d17bfd6db412e66",
    "ammayor": "d71af3922a33954154f7f2745df7466647465ad3920b6a04548b6a9df73a2e05",
    "bhoisig": "43ca1ecf37968c0737109cb06b8c9fbb522a2becbe51a89662ef1d14df3c52f7",
    "bhnalla": "69fa93487cb7f0f4aa9883733e5d8a784155dbb726e06d88c595f591dfd763d5",
    "bharval": "d1ca99803daf9f609ea13f438eb75fe65ffb87d0f417b483a4873ece6006f31a",
    "hendak": "b19099dbd5a42cbb653b1a7abba30aeda7dbdc5697ba0e95106fe39ba7b1b7c4",
    "lehtin": "980b634e457adec11ba48405c65dbc5f9c6d032eac0d51bfcf8703d1eb6c44bf",
    "udsilver": "593cdf5a0313db7065fb2c1eaafc8efc309485d3b5a654c24b585f397e94917b",
    "uddemon": "b1976c5f2f0b948ec00f972348b5ef208b0ba127bbc5678297b133747836a042",
    "gortan1": "0a70f5274d7eacfd4c0c1941266c7bd9be3e5332026f24116c3cadfa6c441ede",
    "gorbat1": "39d7e944e7d9efb983bb34013e7be3a9ab7cca2825d18010f16a22da60443c27",
    "gorlum2": "28a2a669d4388c155abfcece1857611260628717e38c3bb49b1af245ce1e4ca5",
    "aran": "26551e099ce0936a8e0da8e128ae41546650d058685091849cc6406a882d50a7",
    "loutcapt": "9a7db245aa8f59ff1743421ed4621b96391521f48e8f6eeafb0d44070a1ddd25",
}

# Original generic descriptions from the matching special WeiDU handlers.
# Keys are installed EET strrefs, values are source TRA entries.
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


def source_spec(source_id: str) -> dict:
    if source_id not in SOURCE_REWARDS:
        raise ProfileError(f"Unsupported Mode 2 scripted source: {source_id}")
    return deepcopy(REWARDS[SOURCE_REWARDS[source_id]])


def target_spec(target_id: str) -> dict:
    if target_id not in TARGET_REWARDS:
        raise ProfileError(f"Unsupported Mode 2 scripted target: {target_id}")
    return deepcopy(REWARDS[TARGET_REWARDS[target_id]])


def transfer_spec(name: str) -> dict:
    """Inventory copies which must be guarded alongside a scripted source."""
    if name not in TRANSFER_SPECS:
        raise ProfileError(f"Unsupported Mode 2 inventory transfer: {name}")
    return deepcopy(TRANSFER_SPECS[name])


def resource_specs(resource: str) -> tuple[dict, ...]:
    """All grant edits for a resource; includes both independent ARAN rewards."""
    resource = resource.lower()
    if resource not in RESOURCE_REWARDS:
        raise ProfileError(f"Unsupported Mode 2 reward resource: {resource}")
    result = []
    for key in RESOURCE_REWARDS[resource]:
        spec = deepcopy(REWARDS[key])
        spec["resource"] = resource
        spec["quest"] = resource.split(":")[1]
        spec["branches"] = spec.get("dialogues", {}).get(resource.split(":")[1], spec["branches"])
        result.append(spec)
    return tuple(result)


def _dialogue_grant(actions: bytes, spec: dict) -> bytes:
    """Match one reviewed top-level create/transfer action, usable by aliases too."""
    charges = ",".join(map(str, spec["payload"]["charges"]))
    create = f'GiveItemCreate("{spec["item"]}",{spec["recipient"]},{charges})'
    transfer = f'GiveItem("{spec["item"]}",{spec["recipient"]})'
    alternatives = [transfer] if spec.get("grant_kind") == "transfer" else [create, transfer]
    matches = list(re.finditer(rb"(?im)^(?:" + b"|".join(re.escape(value.encode()) for value in alternatives) +
                              rb")(?=\r?$|\r\r?$)", actions))
    if len(matches) != 1:
        raise ProfileError("Mode 2 dialogue reward action changed")
    return matches[0][0]


def _patch_dialogue(data: bytes, specs: tuple[dict, ...], *, action_refs=None,
                    defer_departure=True, numeric_target=True) -> bytes:
    dialogue = _Dialogue(data, specs[0]["resource"].split(":")[1])
    expected = {spec["selected_global"]: set(spec["branches"]) for spec in specs}
    found = {key: set() for key in expected}
    for state_index, state in enumerate(dialogue.states):
        first, count = struct.unpack_from("<II", state, 4)
        rows = []
        for ordinal, original in enumerate(dialogue.transitions[first:first + count]):
            flags, _, _, trigger_id, action_id = struct.unpack_from("<5I", original)
            relevant = [spec for spec in specs if (state_index, ordinal) in expected[spec["selected_global"]]]
            if not relevant:
                rows.append(original)
                continue
            if not flags & 4:
                raise ProfileError("Mode 2 dialogue reward transition lost its actions")
            prior = dialogue.triggers[trigger_id] if flags & 2 else b""
            actions = dialogue.actions[action_id]
            for choices in product((False, True), repeat=len(relevant)):
                branch = bytearray(original)
                condition, changed = prior, actions
                for spec, selected in zip(relevant, choices):
                    condition += f'{"" if selected else "!"}Global("{spec["selected_global"]}","GLOBAL",1)\r\n'.encode()
                    grant = _dialogue_grant(actions, spec)
                    replacement = (f'SetGlobal("{spec["consumed_global"]}","GLOBAL",1)'.encode()
                                   if selected else grant)
                    # Keep this beside its grant, before EscapeArea/DestroySelf.
                    if spec.get("inventory_target"):
                        target = spec["target_ids"]
                        match = re.fullmatch(r"bg2:mode2:target:([1-9][0-9]*)", target[0]) if len(target) == 1 else None
                        if match is None:
                            raise ProfileError("Mode 2 inventory transfer target changed")
                        # The dialogue parser strips spaces and colons even
                        # inside Lua strings. Parenthesize return; pass the row.
                        callback = (f'EEex_LuaAction("return(FLPR_Transfer(EEex_LuaAction_Object,{match[1]}))")'
                                    if numeric_target else
                                    'EEex_LuaAction("return FLPR_Transfer(EEex_LuaAction_Object,'
                                    f'[[original-target:{target[0]}]])")').encode()
                        changed = changed.replace(grant, replacement + b"\r\n" + callback, 1)
                    else:
                        mark = f'ActionOverride({spec["recipient"]},SetGlobal("{spec["recipient_local"]}","LOCALS",1))\r\n'.encode()
                        event = f'SetGlobal("{spec["event_global"]}","GLOBAL",1)'.encode()
                        changed = changed.replace(grant, mark + replacement + b"\r\n" + event, 1)
                    if selected and action_refs:
                        for old, new in action_refs.items():
                            display = f'DisplayStringHead(Myself,{old})'.encode()
                            if changed.count(display) != 1:
                                raise ProfileError("Mode 2 original reward display action changed")
                            changed = changed.replace(display, f'DisplayStringHead(Myself,{new})'.encode(), 1)
                dialogue.triggers.append(condition)
                dialogue.actions.append(changed)
                struct.pack_into("<I", branch, 0, flags | 2)
                struct.pack_into("<II", branch, 12, len(dialogue.triggers) - 1, len(dialogue.actions) - 1)
                rows.append(branch)
            for spec in relevant:
                found[spec["selected_global"]].add((state_index, ordinal))
        if len(rows) != count:
            struct.pack_into("<II", state, 4, len(dialogue.transitions), len(rows))
            dialogue.transitions.extend(rows)
    if found != expected:
        raise ProfileError("Mode 2 dialogue reward branches changed")
    if specs[0]["resource"] == "dlg:trgeni01" and defer_departure:
        # DestroySelf is instant during dialogue; LuaAction is queued. Keep the
        # speaker alive until its earlier blocking reward transfer has run.
        # ActionOverride departures belong to the other genies and stay intact.
        departure = (b'EEex_LuaAction("EEex_Action_QueueResponseStringOnAIBase('
                     b'[[DestroySelf()]],EEex_LuaAction_Object)")')
        for index in (37, 38):
            if index >= len(dialogue.states):
                raise ProfileError("TRGENI01 departure state changed")
            state = dialogue.states[index]
            first, count = struct.unpack_from("<II", state, 4)
            if count != 1:
                raise ProfileError("TRGENI01 departure transition changed")
            branch = bytearray(dialogue.transitions[first])
            flags, _, _, _, action_id = struct.unpack_from("<5I", branch)
            if not flags & 4:
                raise ProfileError("TRGENI01 departure actions changed")
            actions, replaced = re.subn(rb"(?m)^DestroySelf\(\)(?=\r*$)", departure,
                                        dialogue.actions[action_id])
            if replaced != 1:
                raise ProfileError("TRGENI01 bare departure changed")
            struct.pack_into("<I", branch, 16, len(dialogue.actions))
            dialogue.actions.append(actions)
            struct.pack_into("<I", state, 4, len(dialogue.transitions))
            dialogue.transitions.append(branch)
    return dialogue.render()


def _patch_text_dialogue(data: bytes, resource: str, refs: dict[int, int], *,
                         defer_departure=True, numeric_target=True) -> bytes:
    if (not isinstance(refs, dict) or set(refs) != set(TEXT_REFS[resource]) or
            any(type(value) is not int or not 0 <= value < 0xFFFFFFFF for value in refs.values())):
        raise ProfileError("Mode 2 dialogue requires allocated original reward text")
    specs = resource_specs(resource)
    if len(specs) != 1:
        raise ProfileError("Mode 2 text route needs one reviewed source flag")
    flag = specs[0]["selected_global"]
    action_refs = {old: refs[old] for old in ACTION_TEXT_REFS.get(resource, ())}
    dlg = _Dialogue(_patch_dialogue(data, specs, action_refs=action_refs,
                                  defer_departure=defer_departure,
                                  numeric_target=numeric_target), resource.split(":")[1])
    original_states = list(dlg.states)
    clones, found = {}, set(action_refs)
    for index, state in enumerate(original_states):
        text, first, count, entry = struct.unpack("<4I", state)
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
            prior = dlg.state_triggers[entry]
            for selected, target in ((False, state), (True, clone)):
                dlg.state_triggers.append(prior +
                    f'{"" if selected else "!"}Global("{flag}","GLOBAL",1)\r\n'.encode())
                struct.pack_into("<I", target, 12, len(dlg.state_triggers) - 1)
        clones[index] = len(dlg.states)
        dlg.states.append(clone)
    if found != set(refs):
        raise ProfileError("Mode 2 original reward text locations changed")
    # Redirect each incoming internal edge, including paths starting at an
    # unchanged external entry (the temple messengers enter sculptor27/28).
    # Cached cross-dialogue links never enter a changed text state directly.
    for state in dlg.states:
        first, count = struct.unpack_from("<II", state, 4)
        rows = []
        for transition in dlg.transitions[first:first + count]:
            flags, _, _, trigger = struct.unpack_from("<4I", transition)
            destination = struct.unpack_from("<I", transition, 28)[0]
            internal = bytes(transition[20:28]).rstrip(b"\0").lower() == dlg.name.lower()
            if flags & 8 or not internal or destination not in clones:
                rows.append(transition)
                continue
            prior = dlg.triggers[trigger] if flags & 2 else b""
            for selected in (False, True):
                row = bytearray(transition)
                dlg.triggers.append(prior +
                    f'{"" if selected else "!"}Global("{flag}","GLOBAL",1)\r\n'.encode())
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


def _original_text_dialogue(data: bytes, resource: str) -> tuple[bytes, dict[int, int] | None]:
    name = resource.split(":")[1]
    if hashlib.sha256(data).hexdigest() == ORIGINAL_HASHES[name]:
        return data, None
    if len(data) < 12 or data[-12:-4] != _TEXT_TRAILER:
        raise ProfileError("Mode 2 text dialogue changed")
    length = struct.unpack_from("<I", data, len(data) - 4)[0]
    if length != 60 + len(TEXT_REFS[resource]) * 8 or length > len(data) - 12:
        raise ProfileError("Mode 2 text dialogue recovery bounds changed")
    trailer = data[-12 - length:-12]
    size, header, count = struct.unpack_from("<I52sI", trailer)
    if size < 52 or size > len(data) - length - 12 or count != len(TEXT_REFS[resource]):
        raise ProfileError("Mode 2 text dialogue original bounds changed")
    original = header + data[52:size]
    refs = dict(struct.unpack_from("<II", trailer, 60 + n * 8) for n in range(count))
    if hashlib.sha256(original).hexdigest() != ORIGINAL_HASHES[name]:
        raise ProfileError("Mode 2 text dialogue original changed")
    if _patch_text_dialogue(original, resource, refs) != data:
        # Accept only the exact prior inventory callback patches on a rescan.
        # TRGENI had exports both before and after its departure-order fix.
        departures = (True, False) if resource == "dlg:trgeni01" else (True,)
        if (not resource_specs(resource)[0].get("inventory_target") or not any(
                _patch_text_dialogue(original, resource, refs, defer_departure=departure,
                                     numeric_target=False) == data for departure in departures)):
            raise ProfileError("Mode 2 text dialogue patch changed")
    return original, refs


def _marker_action(grant: bytes, spec: dict, newline: bytes) -> bytes:
    # In compiled ActionOverride the action's first OB is the actor override.
    # Copy GiveItemCreate's second OB verbatim, retaining every object selector.
    objects = grant.split(b"OB" + newline)
    if len(objects) != 7:
        raise ProfileError("Mode 2 BCS reward object layout changed")
    empty = b'0 0 0 0 0 0 0 0 0 0 0 0 ""'
    if objects[1] != empty:
        raise ProfileError("Mode 2 BCS reward already has an actor override")
    marker = _global_action(spec["recipient_local"], newline)
    marker = marker.replace(b'"GLOBAL', b'"LOCALS', 1)
    return marker.replace(empty + b"OB" + newline, objects[3] + b"OB" + newline, 1)


def _patch_script(data: bytes, spec: dict, ddwell_ref: int | None = None) -> bytes:
    newline = _newline(data)
    container = spec["quest"] == "asysta01"
    blocks, prefix, suffix = _blocks(data, 1 if container else 3, newline)
    index = spec["branches"][0]
    block = blocks[index]
    grant = _grant_action(block, "Helm19" if container else "helm21", 82 if container else 140, newline)
    mark = b"" if container else _marker_action(grant, spec, newline)
    event = _global_action(spec["event_global"], newline)
    head, conditions, response = block.split(b"CO" + newline)
    variants = []
    for selected in (False, True):
        replacement = _global_action(spec["consumed_global"], newline) if selected else grant
        changed = response.replace(grant, mark + replacement + event, 1)
        if selected and not container:
            old = b'40850 0 0 0 0"" "" AC' + newline
            if changed.count(old) != 1 or type(ddwell_ref) is not int or not 0 <= ddwell_ref < 0xFFFFFFFF:
                raise ProfileError("DDWELL requires an allocated original @399 TLK reference")
            changed = changed.replace(old, str(ddwell_ref).encode() + old[5:], 1)
        variants.append(head + b"CO" + newline + conditions +
                        _global_guard(spec["selected_global"], selected, newline) + b"CO" + newline + changed)
    blocks[index] = b"".join(variants)
    return prefix + b"".join(blocks) + suffix


def prepare_ddwell_text(tlk: bytes, language: str = "en_us") -> tuple[bytes, int]:
    """Append the original generic well description; never replace shared text."""
    updated, refs = prepare_reward_texts(tlk, ("bcs:ddwell",), language)
    return updated, refs["bcs:ddwell"][40850]


def prepare_reward_texts(tlk: bytes, resources, language: str = "en_us") -> tuple[bytes, dict]:
    """Allocate all requested original TRA descriptions in one append/dedup pass."""
    if language.lower() != "en_us":
        raise ProfileError("Mode 2 reward text currently requires the original English translation")
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


def _original_ddwell(data: bytes) -> tuple[bytes, int | None]:
    if hashlib.sha256(data).hexdigest() == ORIGINAL_HASHES["ddwell"]:
        return data, None
    spec, newline = REWARDS["ddwell"], _newline(data)
    blocks, prefix, suffix = _blocks(data, 4, newline)
    original_block = blocks[1]
    grant = _grant_action(original_block, "helm21", 140, newline)
    for addition in (_global_guard(spec["selected_global"], False, newline),
                     _marker_action(grant, spec, newline), _global_action(spec["event_global"], newline)):
        if original_block.count(addition) != 1:
            raise ProfileError("DDWELL patched reward changed")
        original_block = original_block.replace(addition, b"", 1)
    original = prefix + blocks[0] + original_block + blocks[3] + suffix
    # Read only the selected branch's DisplayString reference, then prove every
    # byte of both branches by regenerating from the hash-verified original.
    text = re.findall(rb'151OB' + newline + rb'.*?' + newline +
                      rb'([0-9]+) 0 0 0 0"" "" AC' + newline, blocks[2], re.DOTALL)
    if hashlib.sha256(original).hexdigest() != ORIGINAL_HASHES["ddwell"] or len(text) != 1:
        raise ProfileError("DDWELL patched reward changed")
    ref = int(text[0])
    if _patch_script(original, spec, ref) != data:
        raise ProfileError("DDWELL patched reward changed")
    return original, ref


def _resource_key(resource: str) -> str:
    name = resource.lower().split(":")[-1].split(".")[0]
    candidates = [key for key in RESOURCE_REWARDS if key.split(":")[1] == name]
    if len(candidates) != 1:
        raise ProfileError("Unsupported Mode 2 scripted reward")
    return candidates[0]


def original_reward(resource: str, data: bytes) -> bytes | None:
    """Validate known bytes, recovering originals for patches with dynamic text.

    Fixed-hash static patches have no recovery metadata and return None after
    verification. Unknown/tampered bytes always raise instead of being accepted.
    """
    resource = _resource_key(resource)
    name = resource.split(":")[1]
    if not isinstance(data, bytes):
        raise ProfileError("Unsupported Mode 2 reward bytes")
    if name == "ddwell":
        return _original_ddwell(data)[0]
    if resource in TEXT_REFS:
        return _original_text_dialogue(data, resource)[0]
    digest = hashlib.sha256(data).hexdigest()
    if digest == ORIGINAL_HASHES[name]:
        return data
    if digest == PATCHED_HASHES.get(name):
        return None
    raise ProfileError(f"{name.upper()} changed; review the Mode 2 reward adapter")


def patch_reward(resource: str, data: bytes, *, refs: dict[int, int] | None = None,
                 ddwell_ref: int | None = None) -> bytes:
    """Patch reviewed/own bytes; text resources need refs on their first export."""
    resource = _resource_key(resource)
    name = resource.split(":")[1]
    if not isinstance(data, bytes):
        raise ProfileError("Unsupported Mode 2 reward bytes")
    if name == "ddwell":
        if refs is not None:
            if set(refs) != {40850}:
                raise ProfileError("DDWELL requires its allocated original reward text")
            ddwell_ref = refs[40850]
        original, prior_ref = _original_ddwell(data)
        return _patch_script(original, REWARDS[name], prior_ref if ddwell_ref is None else ddwell_ref)
    if resource in TEXT_REFS:
        original, prior_refs = _original_text_dialogue(data, resource)
        return _patch_text_dialogue(original, resource, prior_refs if refs is None else refs)
    digest = hashlib.sha256(data).hexdigest()
    if digest == PATCHED_HASHES.get(name):
        return data
    if digest != ORIGINAL_HASHES[name]:
        raise ProfileError(f"{name.upper()} changed; review the Mode 2 reward adapter")
    specs = resource_specs(resource)
    patched = (_patch_dialogue(data, specs) if resource.startswith("dlg:")
               else _patch_script(data, specs[0]))
    if PATCHED_HASHES.get(name) and hashlib.sha256(patched).hexdigest() != PATCHED_HASHES[name]:
        raise ProfileError("Mode 2 reward patch differs from reviewed bytes")
    return patched
