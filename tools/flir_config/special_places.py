"""Item Randomiser's special places, as the editor shows them.

A special place is a row of Item Randomiser's location tables whose reward
does not simply lie in a container, sit in a shop or go into one creature's
backpack: a quest reward that someone hands over, a carrier who hands it over
or else drops it, a reward that waits at a corpse, a treasure whose taking
wakes its guards, or one of several possible carriers (a group, the chapter 3
vampires and thieves). Each has a short label in game terms: who has the
item, and what the player sees on each path.

The labels are English, as the editor is; their wording is Chris's to choose.
A place is recognised as the configurator recognises its delivery
(original_registrations.target_kind, native_export): an extended reward route
(mode2_rewards), a container quest (container_rewards), the Deck of Many
Things' Key, one of Item Randomiser's three quest scripts (quest_rewards), the
chapter 3 places (chapter3.py), a creature in several versions
(original_catalog.CREATURE_VERSIONS) or a group of Item Randomiser's group
tables. A preset's row at such a place (Boss Loot's Aldeth) is the same place.
"""
from __future__ import annotations

from .original_catalog import (chapter3_place, container_target_spec, creature_versions_place,
                               is_spin621_target, mode2_route_target)
from .mode2_rewards import REWARDS, TARGET_REWARDS
from .platform import TABLE_PARTS, preset_campaigns
from .profiles import ProfileError
from .quest_rewards import QUESTS
from .templates import campaign_tables

KIND = "special"
KIND_LABEL = "Special place"
# Carriers whose reward reply hands over what they carry (mode2_rewards'
# inventory_target): theft and combat still reach it.
_CARRIED = "you can also steal it or take it from the body."
# The extended reward routes' places, by route (mode2_rewards.REWARDS).
ROUTES = {
    "uhogre01": ("Madulf the ogre (Umar Hills)", "He hands it over with his reward."),
    "asysta01": ("Chest of the statues (Spellhold maze)",
                 "It appears in the chest when you place the last item in its statue."),
    "ddwell": ("The well (Firkraag's dungeon)", "You pull it out of the well with the bucket."),
    "mgkhol01": ("Khollynnus Paac (planar sphere)", "He hands it over when you send the Solamnic knights home."),
    "ppstat01": ("Statue of Riddles (Spellhold maze)", "It gives it to you when you have answered its riddles."),
    "riftg03": ("Avatar of Amaunator", "It gives it to you when you return the Rift Device."),
    "trhmer01": ("Guildmistress Busya (Trademeet)", "She hands it over with her reward."),
    "udsvir03": ("Goldander Blackenrock (svirfneblin village)", "He hands it over with his payment."),
    "ammayor": ("Omar Haraad (Amkethran)", "He hands it over when you save his daughter Asana."),
    "sahkng01": ("King Ixilthetocal (Sahuagin city)",
                 "He pays it to you when you help him; otherwise it lies at his corpse."),
    "varci": ("Varci Roaringhorn (Baldur's Gate)", "Varci pays it to you for rescuing Casson."),
    "bhoisig": ("Temple reward (Temple District)",
                "The temple you work for hands it over with its reward: High Watcher Oisig, "
                "Stormherald Nallabir or High Mornmaster Arval."),
    "hendak": ("Hendak or Lehtinan (Copper Coronet)", "Whichever of them rewards you hands it over."),
    "udsilver": ("Adalon or the Lesser Demon Lord (Underdark)",
                 "Adalon hands it over when you return her eggs; the demon lord gives it "
                 "when you ask him for a magic item."),
    "gortan1": ("Tahazzar or Ka'rashur (Watcher's Keep)", "Whichever of the two you help pays it to you."),
    "gorlum2": ("Machine of Lum the Mad (Watcher's Keep)",
                "It appears in your pack when the machine gives you an item."),
    "aran": ("Aran Linvail, first bonus item",
             "He hands it over with the bonus he pays when you start working for him."),
    "aran2": ("Aran Linvail, second bonus item",
              "He hands it over with the bonus he pays when you start working for him."),
    "ppdjinn": ("Genie (Spellhold maze)", "The genie gives it to you when the stone commands him."),
    "uhmay01": ("Minister Lloyd (Umar Hills)", "He gives it to you when you end the murders."),
    "gorcamb": ("Aesgareth the cambion (Watcher's Keep)",
                "You win it in his game, in place of the Spectral Brand."),
    "sctelwyn": ("Guardian Telwyn, Dawnbringer Sain or Talon Yarryl",
                 "Whichever of the three rewards you first hands it over."),
    "trgeni01": ("Khan Zahraa (Trademeet)", "He carries it and hands it over with his reward; " + _CARRIED),
    "loutcapt": ("Captain Dennis", "He carries it and hands it over with his reward; " + _CARRIED),
    "amcler01": ("Priest of Waukeen (Amkethran)", "He carries it and hands it over with his thanks; " + _CARRIED),
    "jugjer01": ("Jermien (Umar Hills)",
                 "He carries it and hands it over for the mimic's blood; " + _CARRIED),
    "aldeth": ("Aldeth Sashenstar (Baldur's Gate)",
               "He carries it and thanks you with it at his estate; " + _CARRIED),
    "nemphr": ("Nemphre (Baldur's Gate)",
               "She carries it and gives it to you for Arkion's amulet; " + _CARRIED),
    "udlesa": ("Lasaonar (Ust Natha)",
               "Lasaonar carries it and hands it over on yielding the duel; " + _CARRIED),
    "sahrod": ("Sahuagin treasury (Sahuagin city)",
               "It lies in the treasury. If King Ixilthetocal pays you, it comes with his payment; "
               "otherwise take it from the treasury."),
    "tolger": ("Tolgerias", "He pays it to you for Valygar; otherwise it lies at his corpse in the planar sphere."),
    "sahpr2": ("Prince Villynaty (Sahuagin city)",
               "He carries it and hands it over when you help him take the throne; "
               "otherwise it lies at his corpse."),
    "sahpr2b": ("Prince Villynaty, king-or-prince place (Sahuagin city)",
                "He carries it and hands it over when you help him take the throne; "
                "otherwise it lies at his corpse."),
}
# The container quests' places (container_rewards.TARGETS).
_GOLEMS = "It lies on the golems' stand; picking it up brings two of the golems to life."
CONTAINER_QUESTS = {
    "bg2:mode2:target:13": ("Golem treasure, Frostreaver's place (de'Arnise Keep)", _GOLEMS),
    "bg2:mode2:target:43": ("Golem treasure, the bow's place (de'Arnise Keep)", _GOLEMS),
    "bg2:mode2:target:3": ("Golem treasure, the hammer's place (de'Arnise Keep)", _GOLEMS),
    "bg2:mode2:target:53": ("King Strohm's treasure, the shield's place (Windspear Hills)",
                            "It lies in the tomb's chest; once you have taken it, Samia turns on you."),
    "bg2:mode2:target:28": ("King Strohm's treasure, the sword's place (Windspear Hills)",
                            "It lies in the tomb's chest; once you have taken it, Samia turns on you."),
    "bg1:mode2:target:22": ("Safana's pirate treasure",
                            "It lies in the treasure chest; taking it completes Safana's treasure hunt."),
    "bg2:mode2:target:61": ("Chest in the golem room (Spellhold maze)",
                            "Taking it from the chest wakes the iron golem."),
}
# Item Randomiser's three quest scripts (quest_rewards.QUESTS).
SCRIPTED_QUESTS = {
    "blpool": ("Blood pool (Bodhi's lair)", "You find it at the bottom when you enter the pool."),
    "troalt": ("Troll altar (Spellhold)", "It appears on the altar when you place the head on it."),
    "ppbook": ("Ancient tome (Spellhold maze)",
               "The tome creates it once you have turned its pages and beaten the monsters they call."),
}
SPELL = ("Deck of Many Things (Watcher's Keep)", "The Key card gives it when you draw it.")
CHAPTER3 = ("Bodhi's vampires or Aran's thieves (chapter 3)",
            "On Aran's side a vampire in Bodhi's lair carries it (Lassal, Durst or Gellal, drawn for each game). "
            "On Bodhi's side it lies at the corpse of a drawn shadow thief (Booter, Haz or Arkanis Gath) "
            "or of Aran Linvail, whoever dies first.")
# The creatures in several versions (original_catalog.CREATURE_VERSIONS), by
# the first version.
CREATURES = {
    "pptroll1": ("Spirit Troll (Spellhold maze)",
                 "It carries it; it drops when the troll dies for good (finish it with fire or acid)."),
    "torgal": ("TorGal (de'Arnise Keep)",
               "He carries it; it drops when he dies for good (finish him with fire or acid)."),
}
GROUP_NOTE = ("One of them carries it, one who can use it if possible (drawn for each new game); "
              "it drops when that one dies.")
# Names a group's label shows before "and N more".
_GROUP_NAMES = 4


def place(authored: object) -> str | None:
    """Which special place a resolved original row (a target registration's
    authored fields) is, or None for an ordinary one. A preset's row at a
    route's or container quest's place is that place."""
    if not isinstance(authored, dict):
        return None
    container = container_target_spec(authored)
    if container is not None:
        return "container:" + container["target_id"]
    if is_spin621_target(authored):
        return "spell:" + authored["id"]
    # As the export delivers it: also a route whose place is its conversation
    # or script itself (Madulf's), which mode2_alias_spec does not name.
    route = mode2_route_target(authored)
    if route is not None:
        return "route:" + route
    creature = creature_versions_place(authored)
    if creature is not None:
        return f"creature:{authored['campaign']}:{creature}"
    if chapter3_place(authored):
        return "chapter3"
    target = authored.get("target")
    if not isinstance(target, str):
        return None
    if target.endswith(".bcs") and target[:-4] in QUESTS:
        return "quest:" + target[:-4]
    kind = authored.get("endpoint_kind")
    if kind == "group" or (kind is None and target.endswith(".flg")):
        return f"group:{authored.get('campaign')}:{target}:{authored.get('area_resource')}"
    return None


def templates(world: dict) -> tuple[str, ...]:
    """Item Randomiser's lists (a preset campaign's original template) that
    hold the scanned game's special places: each table of the game in one of
    them, EET's in its own. None for a game no preset is for."""
    try:
        campaigns = preset_campaigns(world)
    except ProfileError:
        return ()
    result: list[str] = []
    covered: set[str] = set()
    for campaign in campaigns:
        tables = set(campaign_tables(campaign))
        if not tables <= covered:
            covered |= tables
            result.append(f"{campaign}-original")
    return tuple(result)


def unique(key: str) -> bool:
    """Whether a configuration can have only one row of the place <key>: an
    extended reward route or a container quest, whose game patches and
    runtime name one row (FLPRNat's route_target). Rows of Item Randomiser's
    quest scripts, groups and chapter 3 places are each a place of their own."""
    return key.startswith(("route:", "container:"))


def describe(authored: dict, names=None) -> dict | None:
    """The label and note of a special place (place), or None.

    <names>(area, creature) gives the names a group's members have in the
    area (the scan's actors), for its label."""
    key = place(authored)
    if key is None:
        return None
    kind = key.split(":", 1)[0]
    if kind == "route":
        label, note = ROUTES[TARGET_REWARDS[key[6:]]]
    elif kind == "container":
        label, note = CONTAINER_QUESTS[key[10:]]
    elif kind == "spell":
        label, note = SPELL
    elif kind == "creature":
        label, note = CREATURES[key.rsplit(":", 1)[1]]
    elif kind == "chapter3":
        label, note = CHAPTER3
    elif kind == "quest":
        label, note = SCRIPTED_QUESTS[key[6:]]
    else:
        label, note = _group_label(authored, names), GROUP_NOTE
    return {"place": key, "label": label, "note": note}


def _group_label(authored: dict, names) -> str:
    """"One of: Gall, Gaius, Rengaa, Berkref and 2 more (AR0701)": the
    group's members by the names they have in its area."""
    area = authored.get("area_resource")
    members = authored.get("members")
    shown: list[str] = []
    for member in members if isinstance(members, list) else ():
        if not isinstance(member, str):
            continue
        found = list(names(area, member)) if names is not None and isinstance(area, str) else []
        for name in found or [member.split(":", 1)[-1].upper()]:
            if name not in shown:
                shown.append(name)
    listed = shown[:_GROUP_NAMES]
    more = len(shown) - len(listed)
    text = ", ".join(listed) + (f" and {more} more" if more else "")
    where = area.split(":", 1)[1].upper() if isinstance(area, str) and ":" in area else ""
    return f"One of: {text}" + (f" ({where})" if where else "")


def area(authored: dict) -> str | None:
    """The area the place is in, as an ARE resource, where its row tells it;
    a reward route's area, where its row has none (Tolgerias's sphere)."""
    resource = authored.get("area_resource")
    if isinstance(resource, str) and resource.startswith("are:"):
        return resource
    raw = authored.get("area")
    if isinstance(raw, str) and raw != "blank" and authored.get("campaign") == "bg2":
        # Baldur's Gate II's areas keep their names in every game.
        return "are:" + raw
    key = place(authored)
    if key is not None and key.startswith("route:") and authored.get("campaign") == "bg2":
        spec = REWARDS[TARGET_REWARDS[key[6:]]]
        for field in ("inventory_target", "holder", "handover"):
            if spec.get(field):
                return "are:" + spec[field]["area"]
    return None


def table_parts(authored: dict) -> frozenset[str]:
    """The parts of the game a place of Baldur's Gate's or Siege of
    Dragonspear's tables is in; EET renames their areas."""
    campaign = authored.get("campaign")
    return TABLE_PARTS[campaign] if campaign in ("bg1", "sod") else frozenset()
