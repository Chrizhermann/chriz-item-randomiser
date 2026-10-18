"""The game a scan belongs to, its campaign starts and the parts each start reaches.

Chriz Item Randomiser installs on BG:EE (with Siege of Dragonspear merged),
BG2:EE and EET. The game is identified as WeiDU's GAME_IS does it: EET by its
override/EET.flag, BG2:EE by OH6000.ARE, BG:EE by OH1000.ARE (EET has both
areas, BG2:EE has no OH1000).

Each campaign start that the runtime can tell apart (M_FLPR.lua) gets its own
revision. The start that begins with the whole game (BG1, or SoA in BG2:EE)
uses the whole configuration; a later start (Siege of Dragonspear, Shadows of
Amn on EET, Throne of Bhaal) uses only rewards and places it can reach. A
reward that start cannot take stays as the game has it, and no reward is put
where the player cannot go.
"""

from __future__ import annotations

import re
from pathlib import Path

from .profiles import ProfileError
from .templates import CAMPAIGNS, campaign_tables, template_campaign

PLATFORMS = {"bgee": "BG:EE", "bg2ee": "BG2:EE", "eet": "EET"}
# The recommended preset campaign of each game (templates.CAMPAIGNS).
PRESET_CAMPAIGN = {"bgee": "bg1", "bg2ee": "bg2", "eet": "eet"}
# Preset campaigns whose tables a game can have; those with the SoD tables only
# when it has Siege of Dragonspear (preset_campaigns).
PRESET_CAMPAIGNS = {"bgee": ("bg1", "bg1-sod", "sod"), "bg2ee": ("bg2",),
                    "eet": ("eet", "eet-sod", "bg1", "bg1-sod", "sod", "bg2")}
# The parts of the game each table campaign (bg1, sod, bg2) covers.
TABLE_PARTS = {"bg1": frozenset({"bg1"}), "sod": frozenset({"sod"}),
               "bg2": frozenset({"soa", "wk", "tob"})}

# Campaign starts by the runtime's names (CAMPAIGN.2DA rows; BG2:EE's Throne
# of Bhaal by its flag) and the parts of the game each reaches. "wk" is
# Watcher's Keep, which Shadows of Amn and Throne of Bhaal both reach.
_ALL = frozenset({"bg1", "sod", "soa", "wk", "tob"})
STARTS = {
    "eet": {"BG1": _ALL, "SOD": frozenset({"sod", "soa", "wk", "tob"}),
            "SOA": frozenset({"soa", "wk", "tob"}), "TOB": frozenset({"wk", "tob"})},
    "bgee": {"BG1": frozenset({"bg1", "sod"}), "SOD": frozenset({"sod"})},
    "bg2ee": {"SOA": frozenset({"soa", "wk", "tob"}), "TOB": frozenset({"wk", "tob"})},
}
# The start the engine makes without a campaign row (M_FLPR.lua's "MAIN").
# EET names every start; a start without one is not randomised there.
MAIN_START = {"eet": None, "bgee": "BG1", "bg2ee": "SOA"}
# The start that reaches the whole game: it keeps the whole configuration.
FULL_START = {"eet": "BG1", "bgee": "BG1", "bg2ee": "SOA"}
START_NAMES = {"BG1": "Baldur's Gate", "SOD": "Siege of Dragonspear",
               "SOA": "Shadows of Amn", "TOB": "Throne of Bhaal"}

# BG:EE's own areas with an OH name (its new companions' quests and the Black
# Pits); EET keeps their names. BG2:EE's OH areas belong to Shadows of Amn.
_BGEE_OH = frozenset({"oh1000", "oh2000", "oh2010", "oh3000", "oh3010", "oh3020",
                      "oh3100", "oh9310", "oh9350", "oh9360"})
_AREA = re.compile(r"(ar|bg|bd|oh)([0-9]{4})n?")


def game_platform(world: dict) -> str:
    """"eet", "bg2ee" or "bgee" for a scanned game; any other game is refused."""
    override = Path(world["game_root"]) / "override"
    if override.is_dir() and any(entry.name.casefold() == "eet.flag" for entry in override.iterdir()):
        return "eet"
    resources = {row["id"] for row in world["resources"]}
    if "are:oh6000" in resources:
        return "bg2ee"
    if "are:oh1000" in resources:
        return "bgee"
    raise ProfileError("Chriz Item Randomiser works with Baldur's Gate: Enhanced Edition, "
                       "Baldur's Gate II: Enhanced Edition and the Enhanced Edition Trilogy (EET); "
                       "this game is none of them")


def has_sod(world: dict) -> bool:
    """Whether the scanned game has Siege of Dragonspear's areas."""
    platform = game_platform(world)
    return any(row["id"].startswith("are:") and area_part(platform, row["id"][4:]) == "sod"
               for row in world["resources"])


def preset_campaigns(world: dict) -> tuple[str, ...]:
    """The preset campaigns whose tables the scanned game has."""
    platform = game_platform(world)
    campaigns = PRESET_CAMPAIGNS[platform]
    if has_sod(world):
        return campaigns
    return tuple(campaign for campaign in campaigns if "sod" not in campaign_tables(campaign))


def recommended_campaign(world: dict) -> str:
    """The preset campaign the editor recommends for the scanned game."""
    return PRESET_CAMPAIGN[game_platform(world)]


def preset_game(world: dict, template_id: str) -> str:
    """The scanned game, for a preset whose tables it has (an EET preset needs EET)."""
    platform = game_platform(world)
    campaign = template_campaign(template_id)
    if campaign not in PRESET_CAMPAIGNS[platform]:
        raise ProfileError(f"This preset is for {dict(CAMPAIGNS)[campaign]}; "
                           f"this game is {PLATFORMS[platform]}")
    if campaign not in preset_campaigns(world):
        raise ProfileError(f"This preset is for {dict(CAMPAIGNS)[campaign]}; "
                           "this game does not have Siege of Dragonspear")
    return platform


def area_part(platform: str, area: str) -> str | None:
    """The part of the game an area belongs to, or None when its name does not tell."""
    match = _AREA.fullmatch(area.lower())
    if match is None:
        return None
    prefix, number = match.group(1), int(match.group(2))
    if prefix == "bd":
        return "sod" if platform in ("bgee", "eet") else None
    if platform == "bgee":
        return "bg1" if prefix in ("ar", "oh") else None
    if prefix == "bg":
        return "bg1" if platform == "eet" else None
    if prefix == "oh":
        if area.lower()[:6] in _BGEE_OH:
            return "bg1" if platform == "eet" else None
        return "soa"
    # Baldur's Gate II areas: Watcher's Keep, Throne of Bhaal, else Shadows of Amn.
    if 3000 <= number < 3100:
        return "wk"
    if 4000 <= number < 7000:
        return "tob"
    return "soa"


def campaign_parts(campaign: str) -> frozenset[str]:
    """The parts a table campaign (bg1, sod or bg2) covers."""
    if campaign not in TABLE_PARTS:
        raise ProfileError(f"Unknown table campaign: {campaign}")
    return TABLE_PARTS[campaign]
