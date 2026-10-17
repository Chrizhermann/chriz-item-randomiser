# Boss Loot (BG1 lists by aShuN)

These item and location lists for Baldur's Gate 1 are aShuN's work. aShuN shared
them with Christopher on Discord on 2026-10-14 ("what i managed to edit so far
for bg1"); they started from Item Randomiser's BG1 lists by Fredrik Lindgren
(Wisp). The configurator offers them as the "Boss Loot" presets (`bg1-boss-loot`
and `eet-boss-loot`, each also with Mode 2 additions). An EET preset uses these
lists for BG1 and the original lists for BG2.

aShuN's aims:

- the ability tomes on the main path, so a party need not clear every corner
  for them before BG2;
- powerful items at difficult boss fights instead of nooks;
- items placed by the classes that fit them (done for wizards: tier `ma`, wands,
  robes and rings at mage enemies);
- in Mode 2, shops that sell a few random items, with Rogue Rebalancing and
  IWDification items where those mods are installed.

aShuN considers the scroll part complete: in Mode 2, wizard scrolls go into the
pool, many of them to mage enemies, so a spell such as Sleep may turn up late;
the Emotion scrolls join the pool (Hopelessness, and with IWDification Courage,
Hope and Fear), while Stoneskin, Find Familiar and Spell Thrust stay where they
are. The item tiers and places outside the scrolls were a first pass. Chriz Item Randomiser looks after the lists from here, and
changes are noted below.

## Format

The same tables as `lists/items` and `lists/locations`: `items/{base,mode2}/bg1.2da`
and `locations/{base,mode2}/bg1.2da`. Mode 1 uses the `base` tables; Mode 2
additions add the `mode2` tables. An Ident other than `x` pairs one item with its
home place: when that item is kept where it was, its home gets no other item. As in
Item Randomiser's lists, each Ident names one item row and one place row.

Items that only Rogue Rebalancing (`rr#…`) or IWDification (`cdia…`) adds are left
out, with a note, in a game that does not have them.

## Changes made for Chriz Item Randomiser (2026-10-17)

Pairings: Idents follow Item Randomiser's again where a row is Item Randomiser's row,
so source selection, kept items and the Tales of the Sword Coast tome option work.
Tiers, places, chances and comments are aShuN's.

- Tomes: CHA (AR5102) `g1` → `bc`, DEX (AR0153) `g2` → `x`, INT (AR0138) `g3` → `b1c`,
  WIS in Candlekeep (AR2615) `g4` → `b1d`, STR (AR2615) `g5` → `b28`, WIS in Durlag's
  Tower (AR0504) `g6` → `b2c` (the TotSC tome option looks for it), Mode 2 CON (AR3601)
  `g7` → `vf`.
- Mode 2 Chain Mail +3 from `baresh2.cre`: `g9` → `b3e`.
- Places that are Item Randomiser's rows, moved to another tier, get their Ident back:
  Container 1 in AR5102 `x` → `bc`, Container 5 in AR2615 `x` → `b29`, Denak (now `ma`)
  `x` → `b5`, the tent group in AR1901 (now base tier 27) `x` → `v9`, the Zhentarim group
  in AR0704 (now Mode 2 tier 26) `bs2` → `b1b`, and Molkar's group in AR4000 (Mode 2
  tier 27) `v7` → `v2`.
- Idents copied to places they do not belong to become `x`: `v1` on the Candlekeep cave
  group (AR5506, three rows) and the Undercity group (AR0123, two rows). Of several rows
  of one group with the same Ident, Item Randomiser's row keeps it and the base rows
  added for Lamalha's group (`v1`) and Molkar's group (`v7`) get `x`.

Corrections from the game data (Baldur's Gate: Enhanced Edition, and EET):

- Raemon ("Tent fighter", base tier 25): area AR3000 → AR1901, where he stands.
- `OGREMA03` in AR1802 (Mode 2 tiers `sa`, `sb`): `OGREMA03.cre`; it is the ogre mage
  standing there, not a container.
- Mode 2 `staf18` from Laskal: replacement `staff01` → `staf01` (Quarterstaff);
  `staff01` does not exist.

Line ends are CRLF and trailing spaces are removed.

## Known limits

- Two Mode 2 rows name an area that holds their scroll twice: `scrl75` in AR2613 (two
  in Container 3) and `scrl84` in AR1803 (one each in Containers 7 and 9). The
  configurator cannot tell which copy to move, so with scrolls randomised it leaves
  these two rows out and says so; the scrolls stay where the game has them.
- Mode 2 additions are not playable in Chriz Item Randomiser yet, as for the original
  lists.
- A few items lost their home place because the place row was removed (Mulahey's
  Boots of Grounding `b17`, Davaeorn's Bracers to the Death `b18`, the Wand of
  Paralyzation from the pirate cave `b2a`). They are still randomised; only a kept
  item's home then also receives another item.
