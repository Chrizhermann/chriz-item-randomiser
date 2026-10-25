"""Campaign-gated quest rewards of the extended pools.

Item Randomiser's extended lists move rewards that quest dialogues and scripts
give. Each reward route here is recognised by its meaning, never by the bytes
of one reviewed game: the replies or script blocks that give the reward item,
the recipient they name and, for some routes, the text that names the item.
Everything else -- Beamdog's or WeiDU's compiled spacing, line ends and letter
case, other strrefs, replies and blocks that another mod added -- is kept as
found, and every reply or block that gives the item is gated.

Only the grant is suppressed; its surrounding actions, conditions, dialogue
replies and journal fields stay intact (a text route shows Item Randomiser's
words instead of the texts that name the reward, journal entries among them,
where the reward moved: TEXT_REFS). The reward event captures the original
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

The routes are registered as each campaign's own game names its resources
(BG:EE's names for BG1's routes). EET renames BG1's areas and the BG1 items
whose names Baldur's Gate II uses (game_names.py): the functions that take a
<game> and its EET <names> return a route as that game names it. Without a
game, a route keeps its campaign's own names.
"""
from __future__ import annotations

from collections import namedtuple
from copy import deepcopy
from itertools import product
import re
import struct

from . import translations
from .game_names import EetNames, convert_resource
from .profiles import ProfileError
from .quest_options import _Dialogue
from .quest_rewards import _global_action, _global_guard
from .game_text import GameText, GameTextError
from .script_text import (argument, block_actions, block_parts, calls, integer_argument, joined,
                          named, naming, response_spans, script_blocks, string_argument)

ADAPTER_ID = "mode2-reward-v1"


def _spec(name, extension, item, source, target, recipient, charges, branches, campaign="bg2"):
    # A BG1 route's flags are its own: B1_<row> beside BG2's M2_<row>.
    suffix = f"{'B1' if campaign == 'bg1' else 'M2'}_{source}"
    return {
        "quest": name, "resource": f"{extension}:{name}", "item": item, "campaign": campaign,
        "source_ids": (f"original-source:{campaign}:mode2:item:{source}",),
        "target_ids": (f"{campaign}:mode2:target:{target}",),
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
    # The Sahuagin king's bracer (Item Randomiser's tadpoles1.fl). The same
    # reply also gives the treasury's rod: see "sahrod" below.
    "sahkng01": _spec("sahkng01", "dlg", "brac19", 57, 58, "LastTalkedToBy", (0, 0, 0), ((81, 0),)),
    # Varci's payment for Casson's rescue (Item Randomiser's varci.fl), the
    # shield EET names SHLD07A.
    "varci": _spec("varci", "dlg", "shld07", 20, 20, "LastTalkedToBy(Myself)", (0, 0, 0), ((11, 0),),
                   campaign="bg1"),
}
DIRECT_REWARDS = tuple(REWARDS)


def _alias(key, item, source, target, alias, dialogues, *, charges=(0, 0, 0), recipient="LastTalkedToBy",
           campaign="bg2"):
    primary = next(iter(dialogues))
    spec = _spec(primary, "dlg", item, source, target, recipient, charges, dialogues[primary], campaign)
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
# Each copy is the creature's records of the reward item, as the scan finds
# them (native_export.py), whatever their place or flags in this game.
TRANSFER_SPECS = {"scyarryl": REWARDS["scyarryl"]}
TRANSFER_SPECS["scyarryl"].update(
    authored_source_ids=("original-source:bg2:mode2:item:12",), source_ids=(),
    source_resource="cre:scyarryl", grant_kind="transfer")
for _sculptor in ("sctelwyn", "scsain", "scyarryl"):
    REWARDS[_sculptor]["resources"] = ("dlg:sctelwyn", "dlg:scsain", "dlg:scyarryl")
    REWARDS[_sculptor]["required_transfers"] = ("scyarryl",)
# Source selection alone removes its duplicate, even with no sculptor target.
REWARDS["bhoisig"]["required_transfers"] = ("scyarryl",)

# These rewards must remain carried by the NPC so theft and combat routes still
# work. Their dialogue transfers the carried assignment through a blocking Lua
# callback; it never creates a second item for the dialogue recipient.
for _name, _item, _source, _target, _area, _script, _branches, _charges, _campaign in (
    ("trgeni01", "sw1h23", 32, 32, "ar2014", "trgeni01", ((35, 0), (40, 0)), (0, 0, 0), "bg2"),
    ("loutcapt", "dagg11", 39, 42, "ar0514", "Dennis", ((26, 0), (30, 0)), (0, 0, 0), "bg2"),
    ("amcler01", "ring43", 84, 85, "ar5500", "amcler01", ((7, 0), (8, 0)), (0, 0, 0), "bg2"),
    # Jermien's sword (Item Randomiser's jugjer01.fl), with its daily charge.
    ("jugjer01", "sw1h26", 31, 31, "ar1103", "Jermien", ((23, 0),), (1, 1, 0), "bg2"),
    # Aldeth's sword (Item Randomiser's aldeth.fl). He thanks the party in his
    # estate (AR0128), whose script creates him there.
    ("aldeth", "sw1h03", 16, 16, "ar0128", "Aldeth", ((27, 0),), (0, 0, 0), "bg1"),
    # Nemphre's scroll for Arkion's amulet (Item Randomiser's nemphr.fl).
    # Item Randomiser's lists give her place scrolls only (tier sc), so her
    # words ("take this scroll") stay true.
    ("nemphr", "scrl1q", 159, 136, "ar0813", "Nemphre", ((3, 0),), (0, 0, 0), "bg1"),
):
    _alias(_name, _item, _source, _target, _name.upper(), {_name: _branches}, charges=_charges,
           campaign=_campaign)
    _specification = REWARDS[_name]
    _specification.update(
        grant_kind="transfer", authored_source_ids=_specification["source_ids"],
        inventory_target={"area": _area, "creature_resource": "cre:" + _name, "script_name": _script},
        source_resource="cre:" + _name, required_transfers=(_name,))
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
    source_resource="cre:udlesa", replacement="dwsw1h02")

# The Sahuagin treasury's rod (row7, whose row names sahkng01.dlg and
# sahtreas.bcs) is an ordinary container source, but the king's reward reply
# creates another one (SAHTREAS.BCS then destroys the treasury's). As with
# UDLESA's sword, the reply follows the source's kept/relocated decision.
# The rod's place (row 6, Item Randomiser's sahtreas.fl) is the treasury, the
# AR2300 container whose script is SAHTREAS: its reward lies there until the
# king's reward reply hands it to the one he pays (_handover), and goes to
# that payee once he has paid. On the prince's side it stays to be looted.
_alias("sahrod", "rods02", 7, 6, "SAHROD", {"sahkng01": ((81, 0),)})
REWARDS["sahrod"].update(source_ids=(), authored_source_ids=("original-source:bg2:mode2:item:7",),
                         handover={"area": "ar2300", "script": "sahtreas"})
# Once the king's job is done (sahkngjob 2, which he sets just before his
# reward reply), SAHTREAS.BCS destroys the items that reply gives
# (DestroyItem), the treasury's rod among them, with every AI update of the
# treasury. A reward that moved there under such a name must stay: each
# destroy runs only while its route's grant is the original one
# (_patch_destroys). A game without the script needs no gate.
DESTROY_GATES = {"bcs:sahtreas": ("sahkng01", "sahrod")}
for _key in DESTROY_GATES["bcs:sahtreas"]:
    REWARDS[_key]["resources"] = ("dlg:sahkng01", "bcs:sahtreas")
# The two scrolls that the book of Lavok's sphere (PPBOOK.BCS, AR1513) gives
# with its ring on the last page (Item Randomiser's rows 260 and 262). The
# book's places are the ring's quest (the installer splits that page for the
# ring: quest_rewards.QUESTS), whose event delivers what they receive; a
# scroll's route only drops its grant where its row moved, so it has no place
# of its own. The installed book gives them in two blocks, 6 and 7.
for _key, _item, _source in (("ppbook", "scrl9b", 260), ("ppbook2", "scrl8z", 262)):
    REWARDS[_key] = _spec("ppbook", "bcs", _item, _source, None, "LastTrigger", (0, 0, 0), (6, 7))
    REWARDS[_key]["target_ids"] = ()
# Physical sources whose reward dialogue also gives their item, by (owner
# resource, kind, item) as the catalog binds the original row: the creature's
# or the area's container record of the item. A game whose dialogue gives
# no such item needs no route (gives_reward).
PHYSICAL_SOURCE_REWARDS = {("cre:udlesa", "cre-template", "sw1h39"): "udlesa",
                           ("are:ar2300", "container", "rods02"): "sahrod"}
for _name, _row in REWARDS.items():
    _row.setdefault("resources", (_row["resource"],))
RESOURCE_REWARDS = {}
for _name, _row in REWARDS.items():
    for _resource in _row.get("dialogues", {_row["resource"].split(":")[1]: ()}):
        _kind = _row["resource"].split(":")[0]
        RESOURCE_REWARDS.setdefault(_kind + ":" + _resource, []).append(_name)
RESOURCE_REWARDS.update(DESTROY_GATES)
RESOURCE_REWARDS = {resource: tuple(names) for resource, names in RESOURCE_REWARDS.items()}
SOURCE_REWARDS = {identity: name for name, spec in REWARDS.items() for identity in spec["source_ids"]}
TARGET_REWARDS = {identity: name for name, spec in REWARDS.items() for identity in spec["target_ids"]}

# Original generic descriptions from the matching special WeiDU handlers.
# Keys are the strrefs of the text that names the reward (BG2's the same in
# EET and BG2:EE, BG1's as BG:EE numbers them: see text_refs), values are
# source TRA entries.
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
    "dlg:jugjer01": {33760: 698, 33777: 699},
    # Aldeth's thanks and the journal entry his reward reply adds.
    "dlg:aldeth": {5740: 1649, 26828: 1650},
}
ACTION_TEXT_REFS = {"dlg:amcler01": (74025,)}
# Journal entries among the TEXT_REFS: their new text keeps the original's
# title line, and BGEE.LUA lists it with the original's quest (journal_lua).
JOURNAL_REFS = {"dlg:aldeth": (26828,)}
# EET numbers BG:EE's own texts 200000 higher than BG:EE does.
_EET_BG1_TEXTS = 200000
_TEXT_TRAILER = b"FLPRM2T1"
# The texts a reply shows: (its flag, the strref's offset), for its own text
# and its journal entry.
_TEXT_FIELDS = ((1, 4), (0x10, 8))
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


def _in_game(spec: dict, game: str | None, names: EetNames | None) -> dict:
    """The route as the scanned game names its items and places (EET renames BG1's).

    Its conversations keep their names: EET renames only BG1 resources whose
    names Baldur's Gate II uses, which no route's conversation does. Were one
    renamed, its rows would name another conversation than the route and be
    left out.
    """
    if game is None:
        return spec

    def name(resref: str, kind: str) -> str:
        return convert_resource(resref, kind, spec["campaign"], game, names)

    spec["item"] = spec["payload"]["resref"] = name(spec["item"], "itm")
    if spec.get("replacement"):
        spec["replacement"] = name(spec["replacement"], "itm")
    if spec.get("source_resource"):
        kind, resref = spec["source_resource"].split(":")
        spec["source_resource"] = f"{kind}:{name(resref, kind)}"
    placement = spec.get("inventory_target")
    if placement:
        placement["area"] = name(placement["area"], "are")
        placement["creature_resource"] = "cre:" + name(placement["creature_resource"][4:], "cre")
    if spec.get("handover"):
        spec["handover"]["area"] = name(spec["handover"]["area"], "are")
    return spec


def reward(key: str, *, game: str | None = None, names: EetNames | None = None) -> dict:
    """The route <key> (a REWARDS key) as the game names it."""
    if key not in REWARDS:
        raise ProfileError(f"Unsupported extended reward route: {key}")
    return _in_game(deepcopy(REWARDS[key]), game, names)


def source_spec(source_id: str, *, game: str | None = None, names: EetNames | None = None) -> dict:
    if source_id not in SOURCE_REWARDS:
        raise ProfileError(f"Unsupported extended reward source: {source_id}")
    return _in_game(deepcopy(REWARDS[SOURCE_REWARDS[source_id]]), game, names)


def target_spec(target_id: str, *, game: str | None = None, names: EetNames | None = None) -> dict:
    if target_id not in TARGET_REWARDS:
        raise ProfileError(f"Unsupported extended reward recipient: {target_id}")
    return _in_game(deepcopy(REWARDS[TARGET_REWARDS[target_id]]), game, names)


def transfer_spec(name: str, *, game: str | None = None, names: EetNames | None = None) -> dict:
    """Inventory copies which must be guarded alongside a scripted source."""
    if name not in TRANSFER_SPECS:
        raise ProfileError(f"Unsupported carried reward: {name}")
    return _in_game(deepcopy(TRANSFER_SPECS[name]), game, names)


def resource_specs(resource: str, *, game: str | None = None, names: EetNames | None = None) -> tuple[dict, ...]:
    """All routes with a grant in a resource; includes both independent ARAN rewards.

    Each copy names its route (the REWARDS key) under "route".
    """
    resource = resource.lower()
    if resource not in RESOURCE_REWARDS:
        raise ProfileError(f"Unsupported extended reward resource: {resource}")
    result = []
    for key in RESOURCE_REWARDS[resource]:
        spec = _in_game(deepcopy(REWARDS[key]), game, names)
        spec["route"] = key
        spec["resource"] = resource
        spec["quest"] = resource.split(":")[1]
        spec["branches"] = spec.get("dialogues", {}).get(resource.split(":")[1], spec["branches"])
        result.append(spec)
    return tuple(result)


def _text_shift(resource: str, game: str | None) -> int:
    """How much higher the game numbers a route resource's texts than its campaign does."""
    campaigns = {REWARDS[key]["campaign"] for key in RESOURCE_REWARDS[resource]}
    return _EET_BG1_TEXTS if game == "eet" and campaigns == {"bg1"} else 0


def text_refs(resource: str, game: str | None = None) -> dict[int, int]:
    """TEXT_REFS[<resource>] as the game numbers its texts (EET numbers BG1's higher)."""
    shift = _text_shift(resource, game)
    return {old + shift: tra for old, tra in TEXT_REFS[resource].items()}


def journal_refs(resource: str, game: str | None = None) -> tuple[int, ...]:
    """The journal entries among the resource's text_refs, as the game numbers them."""
    shift = _text_shift(resource, game)
    return tuple(old + shift for old in JOURNAL_REFS.get(resource, ()))


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
    match = re.fullmatch(r"(bg[12]):mode2:target:([1-9][0-9]*)", target[0]) if len(target) == 1 else None
    if match is None:
        raise ProfileError("The carried reward's transfer target changed")
    # The dialogue parser strips spaces and colons even inside Lua strings.
    # Parenthesize return; pass the row, and a BG1 row's campaign (1).
    if numeric:
        campaign = ",1" if match[1] == "bg1" else ""
        return f'EEex_LuaAction("return(FLPR_Transfer(EEex_LuaAction_Object,{match[2]}{campaign}))")'.encode()
    return ('EEex_LuaAction("return FLPR_Transfer(EEex_LuaAction_Object,'
            f'[[original-target:{target[0]}]])")').encode()


def _handover(spec: dict) -> bytes:
    """The condition by which a reward reply hands its place's treasury to the
    payee (M_FLPR's FLPR_Handover). It is true whatever happens there, so the
    reply always shows; without CIR's runtime it does nothing."""
    target = spec["target_ids"]
    match = re.fullmatch(r"(bg[12]):mode2:target:([1-9][0-9]*)", target[0]) if len(target) == 1 else None
    if match is None:
        raise ProfileError("The treasury's place changed")
    # The dialogue parser strips spaces and colons, as in _callback.
    campaign = ",1" if match[1] == "bg1" else ""
    return (f'EEex_LuaTrigger("return(FLPR_Handover==nil)or(FLPR_Handover('
            f'EEex_LuaTrigger_Object,{match[2]}{campaign}))")\r\n').encode()


def _gives_item(text: bytes, call, item: str) -> bool:
    """Whether a dialogue action puts the item somewhere (see _GIVING)."""
    index = _GIVING_CALLS.get(call.name.decode("ascii").lower())
    return index is not None and (string_argument(text, call, index) or b"").lower() == item.encode()


def _gives(text: bytes, call, item: str) -> bool:
    """Whether a dialogue action puts the item somewhere, also through ActionOverride."""
    if named(call, "ActionOverride") and len(call.args) == 2:
        inner = calls(text, *call.args[1])
        return inner is None or any(_gives_item(text, nested, item) for nested in inner)
    return _gives_item(text, call, item)


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
                # After the campaign guards, which the recovery reads from the
                # end (_guard_calls). Every variant pays, so each hands over;
                # a second call finds nothing left in the treasury.
                for spec in relevant:
                    if spec.get("handover"):
                        condition = joined(condition, _handover(spec))
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


def _valid_refs(refs, resource: str, game: str | None) -> bool:
    return (isinstance(refs, dict) and set(refs) == set(text_refs(resource, game)) and
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
                         defer_departure=True, numeric_target=True, game=None, names=None) -> bytes:
    if not _valid_refs(refs, resource, game):
        raise ProfileError(f"{_label(resource)} requires allocated original reward text")
    specs = resource_specs(resource, game=game, names=names)
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
        texts = {struct.unpack_from("<I", row, offset)[0] for row in transitions for bit, offset in _TEXT_FIELDS
                 if struct.unpack_from("<I", row)[0] & bit}
        affected = (texts | {text}) & refs.keys()
        if not affected:
            continue
        found.update(affected)
        clone = bytearray(state)
        struct.pack_into("<I", clone, 0, refs.get(text, text))
        rows = [bytearray(row) for row in transitions]
        for row in rows:
            for bit, offset in _TEXT_FIELDS:
                if struct.unpack_from("<I", row)[0] & bit:
                    old = struct.unpack_from("<I", row, offset)[0]
                    struct.pack_into("<I", row, offset, refs.get(old, old))
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


def _text_original(data: bytes, resource: str, *, game=None,
                   names=None) -> tuple[bytes, dict[int, int]] | None:
    """The original and text refs of a text route this adapter patched; None if unpatched."""
    specs = resource_specs(resource, game=game, names=names)
    if len(data) < 12 or data[-12:-4] != _TEXT_TRAILER:
        if _carries_patch(_read_dialogue(data, resource), specs):
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
    if not _valid_refs(refs, resource, game):
        raise _changed_patch(resource)
    variants = [{}]
    # Accept only the exact prior inventory callback patches on a rescan.
    # TRGENI had exports both before and after its departure-order fix.
    if specs[0].get("inventory_target"):
        variants += [{"defer_departure": departure, "numeric_target": False}
                     for departure in ((True, False) if resource == "dlg:trgeni01" else (True,))]
    try:
        if any(_patch_text_dialogue(original, resource, refs, game=game, names=names, **variant) == data
               for variant in variants):
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


def _static_original(data: bytes, resource: str, *, game=None, names=None) -> bytes | None:
    """Undo this adapter's patch of a dialogue without a trailer; None if unpatched.

    The result reads as the original did. Patching it again must give the same
    dialogue as <data>, or <data> is not exactly this adapter's patch.
    """
    specs = resource_specs(resource, game=game, names=names)
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
            # The handover conditions follow the guards of their routes.
            handed = []
            while flags & 2:
                tail = next((_handover(spec) for spec in routes.values() if spec.get("handover") and
                             trigger.endswith(_handover(spec))), None)
                if tail is None:
                    break
                trigger = trigger[:-len(tail)]
                handed.insert(0, tail)
            guards = (_guard_calls(trigger, routes) or []) if flags & 2 else []
            if (any(selected for _, _, selected in guards) or at + 2 ** len(guards) > len(rows) or
                    handed != [_handover(spec) for _, spec, _ in guards if spec.get("handover")]):
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
    # The script text routes are BG2's, which every game numbers alike (text_refs).
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


def _patch_scripts(data: bytes, specs: tuple[dict, ...], text_ref: int | None = None, *,
                   reasons: dict[str, str] | None = None) -> bytes:
    """Gate the blocks that give each route's item, one route after the other.

    A route whose grant is not recognised keeps its blocks as they are; its
    reason goes to <reasons>. With no route recognised, nothing is prepared.
    """
    failures = {}
    for spec in specs:
        try:
            data = _patch_script(data, spec, text_ref)
        except ProfileError as exc:
            failures[spec["route"]] = str(exc)
    if reasons is not None:
        reasons.update(failures)
    if len(failures) == len(specs):
        raise ProfileError(failures[specs[0]["route"]])
    return data


def _script_original(data: bytes, specs: tuple[dict, ...]) -> tuple[bytes, int | None, bool]:
    """The original of a reward script, its text ref and whether we patched it.

    The routes' patches are undone in the reverse order of _patch_scripts;
    patching the result again must give <data> exactly.
    """
    resource = specs[0]["resource"]
    newline = b"\r\n" if b"\r\n" in data else b"\n"
    patched = [spec for spec in specs if b'"GLOBAL' + spec["selected_global"].encode() + b'"' in data]
    if not patched:
        return data, None, False
    original, numbers = data, set()
    try:
        for spec in reversed(patched):
            original, found = _unpatched_script(original, spec, newline,
                                                _global_guard(spec["selected_global"], True, newline),
                                                _global_guard(spec["selected_global"], False, newline))
            numbers |= found
    except ProfileError:
        raise _changed_patch(resource) from None
    # The allocated text is one of the selected copies' numbers; only a
    # possible strref can be it.
    candidates = (sorted(number for number in numbers if 0 <= number < 0xFFFFFFFF)
                  if resource in TEXT_REFS else [None])
    for ref in candidates:
        try:
            if _patch_scripts(original, specs, ref) == data:
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


def _no_action(newline: bytes) -> bytes:
    obj = b'0 0 0 0 0 0 0 0 0 0 0 0 ""OB' + newline
    return b"AC" + newline + b"0OB" + newline + obj + (b"OB" + newline + obj) * 2 + b'0 0 0 0 0"" "" AC' + newline


def _gate_destroys(data: bytes, spec: dict) -> bytes:
    """Gate the route's DestroyItem of its item: a block that destroys it
    (itself or through ActionOverride(), where the moved reward may be too)
    becomes two, the original while the route's grant is unchanged and one
    with NoAction() instead of each of those destroys while it is moved.
    Conditions may test the item; any other action with it could take a
    moved reward, so the route cannot be used."""
    label = _label(spec["resource"])
    item = spec["item"].encode("ascii").lower()
    with naming(label):
        script = script_blocks(data)
        newline, blocks = script.newline, list(script.blocks)
        for index, block in enumerate(script.blocks):
            if not _names(block, spec["item"]):
                continue
            head, conditions, responses = block_parts(block, newline)
            using = [action for action in block_actions(responses, newline)
                     if item in (text.lower() for text in action.strings)]
            if any(action.opcode != 169 or action.strings[0].lower() != item for action in using):
                raise ProfileError(f"{label} block {index} uses {spec['item'].upper()} in an action other "
                                   "than DestroyItem(), which the route cannot gate")
            if not using:
                continue
            blocks[index] = b"".join(
                head + b"CO" + newline + conditions + _global_guard(spec["selected_global"], selected, newline) +
                b"CO" + newline + _edited(responses, [(action.start, action.end, _no_action(newline))
                                                     for action in using] if selected else [])
                for selected in (False, True))
    return script.prefix + b"".join(blocks) + script.suffix


def _patch_destroys(data: bytes, specs: tuple[dict, ...], *, reasons: dict[str, str] | None = None) -> bytes:
    """Gate each route's DestroyItem of its item (DESTROY_GATES), one route
    after the other; everything else stays as found. A route whose destroys
    cannot be gated keeps its blocks as they are; its reason goes to
    <reasons>. With no route gateable, nothing is prepared."""
    failures = {}
    for spec in specs:
        try:
            data = _gate_destroys(data, spec)
        except ProfileError as exc:
            failures[spec["route"]] = str(exc)
    if reasons is not None:
        reasons.update(failures)
    if len(failures) == len(specs):
        raise ProfileError(failures[specs[0]["route"]])
    return data


def _destroys_original(data: bytes, specs: tuple[dict, ...]) -> tuple[bytes, bool]:
    """The script our destroy gates were made from, and whether <data> carries
    them; patching the result again must give <data> exactly."""
    resource = specs[0]["resource"]
    patched = [spec for spec in specs if b'"GLOBAL' + spec["selected_global"].encode() + b'"' in data]
    if not patched:
        return data, False
    original = data
    try:
        with naming(_label(resource)):
            for spec in reversed(patched):
                script = script_blocks(original)
                selected, unselected = (_global_guard(spec["selected_global"], choice, script.newline)
                                        for choice in (True, False))
                original = script.prefix + b"".join(block.replace(unselected, b"", 1) for block in script.blocks
                                                    if selected not in block) + script.suffix
        if _patch_destroys(original, specs) == data:
            return original, True
    except ProfileError:
        pass
    raise _changed_patch(resource)


_Examined = namedtuple("_Examined", "original own refs")


def _examine(resource: str, data: bytes, *, game=None, names=None) -> _Examined:
    """The unpatched resource (exact, or as the engine reads it), and our patch's text refs."""
    if not isinstance(data, bytes):
        raise ProfileError("Unsupported reward resource bytes")
    if resource in DESTROY_GATES:
        original, own = _destroys_original(data, resource_specs(resource, game=game, names=names))
        return _Examined(original, own, None)
    if resource.startswith("bcs:"):
        original, ref, own = _script_original(data, resource_specs(resource, game=game, names=names))
        return _Examined(original, own, ref)
    if resource in TEXT_REFS:
        recovered = _text_original(data, resource, game=game, names=names)
        return _Examined(data, False, None) if recovered is None else _Examined(recovered[0], True, recovered[1])
    original = _static_original(data, resource, game=game, names=names)
    return _Examined(data, False, None) if original is None else _Examined(original, True, None)


def prepare_ddwell_text(text: GameText, language: str = "en_us") -> tuple[GameText, int]:
    """Append the original generic well description; never replace shared text."""
    updated, refs = prepare_reward_texts(text, ("bcs:ddwell",), language)
    return updated, refs["bcs:ddwell"][40850]


def text_lines(resource: str) -> tuple[tuple[str, int], ...]:
    """The (TRA file, number) lines of Item Randomiser's reward text that a text route shows."""
    file = "ppdjinn.tra" if resource == "dlg:ppdjinn" else "in-game.tra"
    return tuple((file, number) for number in sorted(set(TEXT_REFS[resource].values())))


# A journal entry's title line and the line breaks after it (one or two,
# as the language's texts have them), before its text.
_TITLE = re.compile(r"[^\r\n]*\S[^\r\n]*(?:\r?\n)+(?=[\s\S]*\S)")


def _titled(original: str | None, body: str) -> str:
    """<body> under the title of the journal entry <original>, as the Enhanced
    Editions' quest entries begin; <body> alone if it has none."""
    title = _TITLE.match(original or "")
    return title[0] + body if title else body


def prepare_reward_texts(text: GameText, resources, language: str = "en_us", *,
                         game: str | None = None) -> tuple[GameText, dict]:
    """Allocate the requested reward texts, in the game's language, in one append/dedup pass.

    A text's female wording goes to the game's dialogf.tlk. A journal entry
    (JOURNAL_REFS) keeps the title line of the entry it replaces, in the
    game's own words. Raises translations.MissingTranslation when the
    language has no translation of one of them. The result maps each
    resource's texts as the <game> numbers them (text_refs) to the new ones.
    """
    needed = sorted(set(resource.lower() for resource in resources) & TEXT_REFS.keys())
    if not needed:
        return text, {}
    found = translations.lines(language, [line for resource in needed for line in text_lines(resource)])
    entries, texts = [], []
    for resource in needed:
        file = text_lines(resource)[0][0]
        journals = journal_refs(resource, game)
        for old, tra in text_refs(resource, game).items():
            entries.append((resource, old))
            wording = found[file, tra]
            if old in journals:
                wording = tuple(_titled(original, new) for original, new in zip(text.string(old), wording))
            texts.append(wording)
    updated, refs = text.append(texts)
    result = {resource: {} for resource in needed}
    for (resource, old), ref in zip(entries, refs):
        result[resource][old] = ref
    return updated, result


def _journal_entries(lua: bytes, ref: int) -> list[re.Match]:
    """BGEE.LUA's createEntry lines that list the journal text <ref>: (start, quest, rest)."""
    return list(re.finditer(rb"(?m)^([ \t]*createEntry[ \t]*\([ \t]*(\d+)[ \t]*,[^,\r\n]*,[ \t]*)"
                            + str(ref).encode() + rb"([ \t]*,[^\r\n]*\r?\n)", lua))


def journal_lua(lua: bytes, texts: dict[str, dict[int, int]], game: str | None = None) -> bytes:
    """BGEE.LUA with each new journal text listed as the text it replaces is.

    <texts> are prepare_reward_texts' result. The Enhanced Editions' journal
    files an entry under its quest by BGEE.LUA's createEntry lines: the new
    text gets a copy of each line that lists the original, beside it. An
    original that no quest lists stays unlisted, and so does its new text.
    """
    for resource, refs in sorted(texts.items()):
        for old in journal_refs(resource, game):
            new = refs[old]
            listed = {entry[2] for entry in _journal_entries(lua, new)}
            for entry in reversed(_journal_entries(lua, old)):
                if entry[2] in listed:
                    continue
                line = entry[1] + str(new).encode() + entry[3]
                lua = lua[:entry.end()] + line + lua[entry.end():]
    return lua


def _resource_key(resource: str) -> str:
    name = resource.lower().split(":")[-1].split(".")[0]
    candidates = [key for key in RESOURCE_REWARDS if key.split(":")[1] == name]
    if len(candidates) != 1:
        raise ProfileError(f"Unsupported extended reward resource: {resource}")
    return candidates[0]


def own_patch(resource: str, data: bytes, *, game: str | None = None, names: EetNames | None = None) -> bool:
    """Whether the bytes are this adapter's own patch, verified exactly.

    False for an unpatched resource; raises for a changed copy of our patch.
    """
    return _examine(_resource_key(resource), data, game=game, names=names).own


def unpatched_script(resource: str, data: bytes, *, game: str | None = None,
                     names: EetNames | None = None) -> bytes:
    """The script this adapter's patch was made from, exactly; <data> itself
    when it is not our patch. Raises for a changed copy of our patch."""
    resource = _resource_key(resource)
    if not resource.startswith("bcs:"):
        raise ProfileError(f"{_label(resource)} is not a reward script")
    return _examine(resource, data, game=game, names=names).original


def route_reasons(resource: str, data: bytes, *, game: str | None = None,
                  names: EetNames | None = None) -> dict[str, str]:
    """Why each route with a grant in this resource cannot be prepared from these bytes.

    Routes missing from the result can be prepared; this runs the same patch
    that the export runs, with stand-in text references.
    """
    resource = _resource_key(resource)
    specs = resource_specs(resource, game=game, names=names)
    reasons: dict[str, str] = {}
    try:
        examined = _examine(resource, data, game=game, names=names)
        if resource in DESTROY_GATES:
            _patch_destroys(examined.original, specs, reasons=reasons)
        elif resource.startswith("bcs:"):
            _patch_scripts(examined.original, specs, 0, reasons=reasons)
        elif resource in TEXT_REFS:
            _patch_text_dialogue(examined.original, resource, {old: old for old in text_refs(resource, game)},
                                 game=game, names=names)
        else:
            _patch_dialogue(examined.original, specs, reasons=reasons)
    except ProfileError as exc:
        return {spec["route"]: reasons.get(spec["route"], str(exc)) for spec in specs}
    return reasons


def gives_reward(resource: str, data: bytes, key: str, *, game: str | None = None,
                 names: EetNames | None = None) -> bool:
    """Whether the dialogue gives the item of the route <key> at all, in a way
    the route can gate or not.

    A route that follows a physical source (PHYSICAL_SOURCE_REWARDS) is
    needed where its dialogue gives the item.
    A reply whose actions cannot be read counts as giving it; route_reasons
    says why the route cannot be prepared then.
    """
    resource = _resource_key(resource)
    specs = [spec for spec in resource_specs(resource, game=game, names=names) if spec["route"] == key]
    if not resource.startswith("dlg:") or not specs:
        raise ProfileError(f"Unsupported extended reward route in {_label(resource)}: {key}")
    item, label = specs[0]["item"], _label(resource)
    dialogue = _read_dialogue(_examine(resource, data, game=game, names=names).original, resource)
    for state in dialogue.states:
        first, count = _outgoing(dialogue, state, label)
        for row in dialogue.transitions[first:first + count]:
            flags, _, _, _, action_id = struct.unpack_from("<5I", row)
            if not flags & 4:
                continue
            text = _string(dialogue.actions, action_id, label)
            if not _names(text, item):
                continue
            found = calls(text)
            if found is None or any(_gives(text, call, item) for call in found):
                return True
    return False


def destroyed_items(resource: str, data: bytes) -> frozenset[str]:
    """The items the script <resource> destroys by name: each DestroyItem(),
    also through ActionOverride(), in any block and letter case.

    The Sahuagin treasury's script runs with every AI update of its
    container, trapped or not (2.7.3.0: CGameContainer's constructor sets its
    script, AIUpdate ends in ProcessAI), so a reward of such a name could be
    destroyed there (native_export).
    """
    resource = _resource_key(resource)
    with naming(_label(resource)):
        script = script_blocks(data)
        found = set()
        for block in script.blocks:
            responses = block_parts(block, script.newline)[2]
            found.update(action.strings[0].lower().decode("latin-1")
                         for action in block_actions(responses, script.newline) if action.opcode == 169)
    return frozenset(found)


def patch_reward(resource: str, data: bytes, *, refs: dict[int, int] | None = None,
                 ddwell_ref: int | None = None, game: str | None = None, names: EetNames | None = None) -> bytes:
    """Patch a resource or re-export our own patch; text routes need refs on their first export."""
    resource = _resource_key(resource)
    examined = _examine(resource, data, game=game, names=names)
    specs = resource_specs(resource, game=game, names=names)
    if resource in DESTROY_GATES:
        return _patch_destroys(examined.original, specs)
    if resource.startswith("bcs:"):
        if refs is not None:
            if resource not in TEXT_REFS or set(refs) != set(text_refs(resource, game)):
                raise ProfileError(f"{_label(resource)} requires its allocated original reward text")
            ddwell_ref = next(iter(refs.values()))
        return _patch_scripts(examined.original, specs,
                              examined.refs if ddwell_ref is None else ddwell_ref)
    if resource in TEXT_REFS:
        return _patch_text_dialogue(examined.original, resource, examined.refs if refs is None else refs,
                                    game=game, names=names)
    if examined.own:
        return data
    return _patch_dialogue(data, specs)
