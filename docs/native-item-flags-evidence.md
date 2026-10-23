# A reward keeps its original's flags, stolen included (2.7.3.0)

An item record in a CRE, ARE or STO has flags: 1 identified, 2 unstealable, 4 stolen,
8 undroppable. Mods also set other bits. Until CIR 1.0.2 a native source needed flags
0 to 3, so two rows were left out ("Native source requires an unexpired, visible
droppable item"):

- Baeloth's items (`BAELOTH.CRE`, boss loot BG1 row 6): identified and stolen (5).
- Koshi's sword `SW1H51` (`HLKOSHI.CRE`, BG2 base row 141) on Chris's test install:
  0x3E7, from a mod.

Since 1.0.3 CIR takes any flags except undroppable. The reward keeps them, as it
always kept identified and unstealable, so Baeloth's cloak arrives stolen, as it is
at Baeloth. An undroppable original stays left out: such a reward would never leave a
creature recipient. The rule is the same in the export (`native_export.py` and its
Rust port) and in the runtime (`FLPRNat.lua`, `FLPRGat.lua`).

## What the game does (EET 2.7.3.0 lab, 2026-10-24)

CIR's adapter (this change with the corpse-ground delivery) ran by hand in a BG1 area
of a lab game with CIR 1.0.2 running, with virtual sources: Baeloth's cloak `CLCK17`
with flags 5, the amulet `AMUL12` (lore 0) with 0x3E6, and 10 darts `DART11` with
flags 5.

1. **Creature.** Both reached a spawned rat through `GiveItemCreate` with their exact
   flags; the queued message already carried them. Receipts confirmed.
2. **Container.** Both reached an area container (a bag) with their exact flags;
   lootable receipts.
3. **Death drop.** Killed, the rat dropped both into a new pile. The cloak kept 5.
   The engine identified the amulet (0x3E7), as it does for every lore-0 item; its
   other bits stayed.
4. **Corpse ground.** The darts went into that pile as one entry of 10, flags 5.
   Lootable receipt.
5. **Save and load.** The container and the pile kept every flag: the cloak and the
   darts 5, the amulet 0x3E7.

A second run, from the same save, added Koshi's sword exactly as the test install's
`HLKOSHI.CRE` has it (`SW1H51`, usage counts 0/1/1, flags 0x3E7) and a lore-0
potion `POTN08` with bit 31 set (0x80000004):

6. **Creature and container.** Both reached a spawned rat and the area container
   with their exact counts and flags. The engine's item field reads bit 31 back as
   unsigned (2147483652), as CIR checks.
7. **Save and load.** The sword kept 0x3E7 in both places. The potion kept bits 31
   and 2; the engine identified it (0x80000005), as any lore-0 item.

Both times the running campaign of the lab game kept running, and the lab was
restored afterwards.

## Not covered

- What the stolen flag means in play was not tried in the lab. Going by the IESDP,
  only a shop flagged "Buy Fenced Goods" (STO flag bit 12) buys such an item.
- Shop targets were not run in the lab. A shop's new stock row gets the reward's
  flags from CIR's own hook (`FLPRShop.lua`), identified like the shop's own stock, so
  the engine does not choose them.
- EET only (its BG1 part); BG:EE and BG2:EE run the same engine version.
