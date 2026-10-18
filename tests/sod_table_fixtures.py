"""Synthetic Siege of Dragonspear tables, plugged in the way Chris's lists will be.

CIR bundles no SoD tables until Chris has designed them (docs/sod-lists.md), but the
configurator reads a part's tables as soon as a preset campaign names them. These
fixtures copy the bundled lists to a temporary folder, add small SoD tables in the
original tables' format and name them in preset campaigns, so tests can check that
path without bundled SoD content.
"""

from contextlib import ExitStack, contextmanager
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from flir_config import original_catalog, platform, templates  # noqa: E402

ITEM_HEADER = "Item     Replacement Source       Tier Token Ident Chance Comment\n"
PLACE_HEADER = "Location Tier Target           Area   Ident Chance Comment\n"
# Two rows in two tiers; each item's home place has its Ident.
ITEMS = ITEM_HEADER + """
amul12   blank       bd0062.are   32   01    d1    100    first section
bow02    blank       bd1000.are   33   02    d2    100    second section
"""
PLACES = PLACE_HEADER + """
1        32   corpse           bd0062 d1    100    home of d1
2        33   pile03           bd1000 d2    100    home of d2
"""
# Preset campaigns that read the SoD tables, alone and with the others' tables,
# and the games that can offer them.
CAMPAIGNS = (("sod", "SoD"), ("bg1-sod", "BG1 + SoD"), ("eet-sod", "EET + SoD"))
PARTS = {"sod": ("sod",), "bg1-sod": ("bg1", "sod"), "eet-sod": ("bg1", "sod", "bg2")}
OFFERED = {"bgee": ("bg1-sod", "sod"), "eet": ("eet-sod", "bg1-sod", "sod")}
PRESETS = ("sod-original", "bg1-sod-original", "eet-sod-original", "bg1-sod-boss-loot",
           "eet-sod-boss-loot")


@contextmanager
def sod_tables(items: str = ITEMS, places: str = PLACES):
    """The bundled lists with SoD tables added, and preset campaigns naming them."""
    with tempfile.TemporaryDirectory(prefix="cir-sod-tables-") as temp, ExitStack() as stack:
        root = Path(temp)
        shutil.copytree(ROOT / "lists", root / "lists")
        lists = root / "lists"
        for path, text in ((lists / "items/base/sod.2da", items),
                           (lists / "locations/base/sod.2da", places),
                           (lists / "items/mode2/sod.2da", ITEM_HEADER),
                           (lists / "locations/mode2/sod.2da", PLACE_HEADER),
                           (lists / "sources/base/sod.2da",
                            "IDENT  OBJECT_OR_SLOT  MATCH_POLICY  EXPECTED_MATCHES\n")):
            path.write_text(text, encoding="utf-8")
        for module in (templates, original_catalog):
            stack.enter_context(patch.object(module, "ROOT", root))
        for module in (templates, platform):
            stack.enter_context(patch.object(module, "CAMPAIGNS", templates.CAMPAIGNS + CAMPAIGNS))
        stack.enter_context(patch.dict(templates._PARTS, PARTS))
        stack.enter_context(patch.dict(platform.PRESET_CAMPAIGNS,
                                       {game: platform.PRESET_CAMPAIGNS[game] + extra
                                        for game, extra in OFFERED.items()}))
        yield root
