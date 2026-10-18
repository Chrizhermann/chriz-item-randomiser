# Siege of Dragonspear lists

Item Randomiser (Wisp) has lists for Baldur's Gate 1 and Baldur's Gate II only. Chriz
Item Randomiser adds its own item and location lists for Siege of Dragonspear (SoD), so
a SoD start, and SoD after a BG1 start, are randomised too.

## Tables

In the original tables' format, under the table campaign `sod`:

- `lists/items/base/sod.2da`: the items (standard item pool);
- `lists/locations/base/sod.2da`: the places (standard place pool);
- `lists/sources/base/sod.2da`: source selectors (none needed so far);
- `lists/items/mode2/sod.2da`, `lists/locations/mode2/sod.2da`: the extended pools'
  additions (empty so far; the folder name follows Item Randomiser's tables).

Row identities are `sod:<family>:<kind>:<line>`, tiers `sod:<tier>`. The tables have no
authored endpoints: a place's target names its kind (`.cre` a creature, `.sto` a shop,
anything else a container, matched by name as Item Randomiser's places are).

Each item row has its home place: the place row with the same Ident (`d1`, `d2`, ...).
As in Item Randomiser's lists, when an item is kept where it was, its home gets no other
item. Each item's home is where the game puts it, so a tier has as many places as items.

The rows were read from Baldur's Gate: Enhanced Edition 2.7.3.0 with SoD and checked
on EET 2.7.3.0, where the same rows bind under EET's names (see below).

## Tiers

One tier per chapter section, so each item stays in the part of SoD where the game puts
it. This is for balance only: mixed sections would hand a later item out early, or an
earlier one late, but lose nothing. Every source is guarded from the new game on, and a
container gets its item when its area loads, wherever the item came from.

| Tier | Section | Areas |
| --- | --- | --- |
| 32 | Prologue and Baldur's Gate (Korlasz's tomb, the city) | `BD0###` |
| 33 | Coast Way (crossing, dwarven dig and tomb, forest) | `BD1###`, `BD70##` |
| 34 | The way north (Troll Claw Woods, Forest of Wyrms, Boareskyr Bridge, Bridgefort) | `BD2###`, `BD71##`, `BD72##` |
| 35 | Coalition camp and surroundings (Dead Man's Pass, Bloodbark Grove, underground river, Kanaglym) | `BD3###`, `BD5###`, `BD73##`, `BD74##` |
| 36 | Dragonspear Castle and Avernus | `BD4###`, `BD6###` |

The numbers follow BG1's tiers (24-31), so a later option can line the campaigns up.

## Presets

| Preset | Tables |
| --- | --- |
| `sod-original` | SoD |
| `bg1-sod-original` | BG1 (Item Randomiser) and SoD |
| `bg1-sod-boss-loot` | BG1 (Boss Loot) and SoD |
| `eet-sod-original` | BG1, SoD and BG2 (Item Randomiser for BG1 and BG2) |
| `eet-sod-boss-loot` | BG1 (Boss Loot), SoD and BG2 (Item Randomiser) |

The editor offers them where the game has SoD's areas: BG:EE with Siege of Dragonspear
merged, and EET. BG:EE without SoD and BG2:EE do not get them, and building one there is
refused ("this game does not have Siege of Dragonspear"). The recommended presets are
still the game's own `-original` preset.

Each campaign start keeps what it reaches (`docs/plans/2026-10-14-mode1-packaging.md`,
"Campaign starts"): SoD rows are in `BD` areas, so BG:EE's BG1 and SoD starts and EET's
BG1 and SoD starts take them; EET's SoA and ToB starts do not.

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

## Mixing campaigns later

Rows keep their table campaign and tier apart (`sod:32`, `bg1:24`), so nothing mixes
now. A later option can map tiers across campaigns, for example to let BG1 rewards land
in SoD places; the presets that read several campaigns' tables are where it would apply.
