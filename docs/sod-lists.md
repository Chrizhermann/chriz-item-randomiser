# Siege of Dragonspear lists

Item Randomiser (Wisp) has lists for Baldur's Gate 1 and Baldur's Gate II only. Chriz
Item Randomiser's lists for Siege of Dragonspear (SoD) are Chris's to design. As in Item
Randomiser, they are a curated item list and a curated location list, both by tier, and
an item lands on a location of its tier. Once they exist, they are on in the recommended
presets by default.

## The withdrawn draft

Pull request #5 brought a draft read from the game's own placements: 74 SoD rows, with one
tier per chapter section. It was not that design, so it was withdrawn on 2026-10-18,
before the alpha. CIR no longer offers its presets (`sod-original`, `bg1-sod-original`,
`bg1-sod-boss-loot`, `eet-sod-original`, `eet-sod-boss-loot`) or its tables
(`lists/*/sod.2da`); git history keeps them (merge 373b77b).

Until Chris's lists exist, every preset leaves Siege of Dragonspear's items where the game
puts them, on every platform. A SoD start on BG:EE then has nothing to randomise: the
configurator names it when it applies a configuration, and New Game says so.

## How SoD lists plug in

The code that reads SoD tables as a part of their own (table campaign `sod`) stayed:

- **Tables**, in the original tables' format: `lists/items/base/sod.2da`,
  `lists/locations/base/sod.2da` and `lists/sources/base/sod.2da`, plus the extended pools'
  `lists/items/mode2/sod.2da` and `lists/locations/mode2/sod.2da`. Row identities are
  `sod:<family>:<kind>:<line>` and tiers `sod:<tier>`, apart from BG1's and BG2's.
- **No endpoints:** the tables need no authored endpoints (`templates.ENDPOINT_CAMPAIGNS`).
  A place's target names its kind: `.cre` is a creature, `.sto` a shop, and anything else a
  container, matched by name as Item Randomiser's places are. On EET, SoD names follow
  EET's renames (below).
- **Starts:** each campaign start keeps what it reaches (`platform.TABLE_PARTS`,
  `starts.py`). An area tells its part by its name (`BD` areas are SoD's); any other record
  is where its table puts it. BG:EE's BG1 and SoD starts and EET's BG1 and SoD starts take
  SoD rows; EET's SoA and ToB starts don't.
- **Presets:** a preset campaign names the tables it reads (`templates.CAMPAIGNS` and
  `_PARTS`). A preset campaign that reads SoD's tables is offered only where the game has
  SoD's areas (`platform.has_sod`, `preset_campaigns`; `PRESET_CAMPAIGNS` lists the games
  that can have it). Building one elsewhere is refused with "this game does not have Siege
  of Dragonspear". The More Spell Shield option follows the tables a preset reads.

Preset names and descriptions for SoD come with Chris's design.
`tests/sod_table_fixtures.py` plugs small synthetic SoD tables in this way, and the tests
use it to check the whole path.

## Names on EET

EET renames the BG:EE resources (Siege of Dragonspear's among them) whose names BG2
already uses, and keeps its rename lists in its own folder, `EET/lib/pcu_dict.tph`.
The configurator reads them there (`game_names.eet_names`), so BG1 and SoD rows bind
under the names this EET version gave them. Without that file, or when it cannot be read,
it uses its own older copy in `lib/fl#bg1pal.tpa` (from before SoD and EET 14) and the
preset dialog says so; rows whose names that copy gets wrong are then left out. On
EET 2.7.3.0 the game's lists bind 8 more of `eet-original`'s BG1 rewards than the old
copy did (9 with the extended pools), among them Arla's Dragonbane +3, Aule's Staff +3,
Pellan's Shield +2 and Ankheg Plate Mail; the copy named them wrongly, so they were left
out without a word.

## Places that never appear

A place on a creature whose every placement in the area has appearance schedule 0
(present at no hour; SoD Remix takes creatures out this way) is left out and named
(`creature-never-appears`): a reward there would never be found.
