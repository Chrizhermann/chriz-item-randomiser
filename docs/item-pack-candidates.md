# Future item-pack candidates — source inventory for player review

**Decision status:** candidates only. No inclusion, tier, chance, destination, replacement, or campaign rule is approved here. The 38 entries below comprise the 19 named Chosen of Cyric (CoC) rewards, 10 other Rogue Rebalancing (RR) items, six SoD items, and three EE companion-quest items. “Review” means the item looks like ordinary treasure in its documented context; “hold” means its access, quest role, or source binding needs a specific decision. Neither label is a tier.

**Evidence boundary.** CoC/RR names, stated powers, and encounter/merchant context come from [RR's CoC manual](https://mods.shsforums.net/readmes/rogue-rebalancing/rr_cyric.html), [RR's core manual](https://mods.shsforums.net/readmes/rogue-rebalancing/rr_core.html), and the upstream [`rr/setup-rr.tp2`](https://github.com/Spellhold-Studios/Rogue-Rebalancing/blob/master/rr/setup-rr.tp2) plus [`rr/rr_cyric/actors`](https://github.com/Spellhold-Studios/Rogue-Rebalancing/tree/master/rr/rr_cyric/actors) source. Resrefs and actor inventory were checked against those source files. The saved [catalog](../work/mode1-scan-20260924-0816/catalog.json) and [world scan](../work/mode1-scan-20260924-0816/world.json) provide SoD/EE resrefs, effective item names, and physical records. A [player extraction of SoD item descriptions on the Beamdog forum](https://forums.beamdog.com/discussion/50582/beamdog-siege-of-dragonspear-items) supplies the SoD power summaries; treat those summaries as secondary evidence until checked against the target installation's ITM bytes. The scan contains **zero `RR#` items**, so RR is **not verified installed** in that snapshot. The original Randomiser [manual](../readme-randomiser.html) and [BG1](../lists/items/base/bg1.2da)/[BG2](../lists/items/base/bg2.2da) lists show tier/source conventions, but do not assign these additions. Its compatibility example excludes a quest-triggering shield; quest dependency matters even for an otherwise ordinary ITM.

## Chosen of Cyric: complete named equipment list (19)

These are the manual's **combat loot**, not rewards from peaceful resolution. The component starts after Bodhi in Chapter 6; its party's equipment is usable during combat, so moving equipped originals would also change the fight unless an explicit replacement is supplied. Actor names below come from `rr/rr_cyric/actors/*.cre`; the `rr#` mappings come from `setup-rr.tp2`'s CoC item-copy block. Powers are shortened from the [CoC manual's item section](https://mods.shsforums.net/readmes/rogue-rebalancing/rr_cyric.html#New_items). **Review all for inclusion; leave their tiers open.**

| Item / resref | Role or defining effect | Original carrier |
|---|---|---|
| Barrier Amulet `rr#amulb` | Stoneskin + Minor Globe together, 3/day | Selina |
| Geminus `rr#rng01` | Ring doubles mage level 1–2 memorization | Zaeron |
| Venduris' Ring of Concealment `rr#vring` | Divination protection; Mass Invisibility 1/day | Venduris |
| Boots of the Fox `rr#bootf` | Movement speed and AC | Grok |
| Shadowed Boots `rr#shbts` | AC and thief stealth | Bodak |
| Venduris' Shadowcloak `rr#vclk` | AC/saves, stealth, backstab protection | Venduris |
| Blessed Leafcrown `rr#lcrwn` | Bless, disease/poison immunity; elf and alignment limits | Zaeron |
| Dead Man's Face `rr#helmd` | Fear immunity, critical-hit protection, Charisma cost | Grok |
| Rogue's Cowl `rr#rcowl` | Stealth; Blur + Mirror Image, 3/day | Bodak |
| Sune's Laurel of Favor `rr#helms` | Charisma, AC, critical-hit protection | Selina |
| Venduris' Luckstone `rr#vluck` | +3 Luck; head protection; alignment/class limits | Venduris |
| Abishai Hide `rr#abih` | Physical resistance and regeneration | Grok |
| Drow Chain Mail `rr#chn01` | Casting-speed and magic-resistance bonuses | Zaeron |
| Stormcharged Axe `rr#scaxe` | Electrical damage/resistance, chance to stun | Grok |
| Salamander's Tongue `rr#stong` | Fire/poison defenses, Non-Detection, fire on hit | Zaeron |
| Silent Death `rr#sdeat` | Poison/silence, rare percentage-HP strike; restricted assassin blade | Venduris |
| Spell Diver `rr#diver` | Magic defense and stacking spell-failure on hit | Zaeron |
| Hold Fast `rr#holdf` | Slashing AC; chance to cast Hold Monster on hit | Grok |
| Blindstrike Bow `rr#bow01` | Blindness chance; extra effect against beholders | Bodak |

**Explicit exclusions from that source block:** `rr#vward` is Venduris' ward-breaking scroll; the manual says the player needs it to leave the encounter area. Keep it at the original encounter. `rr#bt01` is the encounter's Boots of Speed copy, not one of the 19 specially described CoC rewards; handle generic Boots of Speed through the existing item policy instead of counting it as a new pack item. CoC ownership and reward availability must be rechecked if the chosen RR component/version or install changes.

## Other Rogue Rebalancing additions (10)

The [core manual's equipment section](https://mods.shsforums.net/readmes/rogue-rebalancing/rr_core.html#Additional_equipment_for_Thieves_and_Bards) describes these effects. [`setup-rr.tp2`](https://github.com/Spellhold-Studios/Rogue-Rebalancing/blob/master/rr/setup-rr.tp2) maps resrefs and stock/actor additions. “Marina” is the Black Market merchant in Athkatla's Shadow Thief guildhall; some stock also appears in BG1 stores or other locations depending on the game and component. Do not assume one universal original per resref.

| Item / resref | Role or defining effect | Documented original context | Review |
|---|---|---|---|
| Rogue's Ward `rr#rward` | Buckler: saves vs. death, stealth, Improved Invisibility | Marina stock; other RR placements possible | Review finite stock separately from any copies |
| Tri-souled Sapphire `rr#hel01` | Find Traps, Knock, Invisibility Purge; use causes fatigue | RR-added stock/placements | Review distinct originals |
| Cowl of Acuity `rr#hel02` | Find Traps, infravision, blindness immunity | Marina and BG1 Black Lily stock | Review |
| Harp of Descant `rr#hrp01` | Dispels Confusion, 3/day | Marina; BG1 Ulgoth's Beard stock | Review |
| Lyre of Progression `rr#lyre` | Bard spell memorization slots | Marina; BG1 Black Lily stock | Review |
| Masterbelt `rr#bel01` | Open Locks, Disarm Traps, Set Snares bonuses | Marina; BG1 Bergost stock | Review |
| Sparkburst `rr#bow06` | Composite short bow: electric proc and missile-AC penalty | Marina; BG1 Black Lily stock | Review |
| The Guardian `rr#guara` | Druid shield: HP/regeneration; level-scaled Shambler summon | RR-added area loot | Review item, then verify summon dependencies in target install |
| Plaguebearer `rr#plag` | Dagger: disease effects on hit | Booter in Shadow Thief guildhall basement | Review only with source/branch check; Booter can be killed or pickpocketed depending on path |
| Silverblaze `rr#silv` | Anti-vampire dagger; Negative Plane Protection | Arledrian and his Chapter 3 quest | **Hold**: multiple dialogue, theft, kill and quest paths award it; verify all scripted checks before moving |

The RR component also *changes* existing Dagger of Venom, Kiel's Buckler and Short Sword of Backstabbing. Those are not new resrefs for this pack; the revised Short Sword of Backstabbing moves to Aran Linvail and has explicit Chapter 6 dialogue/branch handling in the [core manual](https://mods.shsforums.net/readmes/rogue-rebalancing/rr_core.html), so it is held outside this shortlist. Crafting upgrades in RR's [upgrade component](https://mods.shsforums.net/readmes/rogue-rebalancing/rr_upgr.html) are likewise separate from ordinary loot.

## SoD examples with physical records (6)

The following records are in the saved world scan; the `owner` column is a **resource/record**, not a proof of how a quest exposes it. Four are static containers and two are finite store entries. They form a small review sample, not an exhaustive SoD pack. Effect summaries come from the [SoD item extraction](https://forums.beamdog.com/discussion/50582/beamdog-siege-of-dragonspear-items).

| Item / resref | Role or defining effect | Saved original owner | Review |
|---|---|---|---|
| Sable Cloak `bdclck02` | Cold resistance; slow immunity | `are:bd7210/container/0/item/2` | Review; static cave loot |
| Bard Hat `bdhelm11` | Silence/deafness immunity; bard song lingers | `are:bd1100/container/9/item/20` | Review; static dig-site loot |
| Fractal Blade +3 `bdsw1h14` | Illusion dispel on hit; defensive Mirror Images | `are:bd7230/container/2/item/8` | Review; static temple loot |
| Spell Breaker +2 `bdsw1h25` | Spell saves; dispel on critical hit | `are:bd0067/container/1/item/5` | Review; verify access/quest gating |
| Belt of the Skillful Blade `bdbelt02` | Slashing damage; Blade/Swashbuckler off-hand accuracy | `sto:bdwaizah/item/50` | Review finite shop stock |
| Peacock Cap `bdhelm06` | Charisma bonus, stronger for bards | `sto:bdkazzre/item/7` | Review finite shop stock |

## EE companion-quest examples (3)

| Item / resref | Role or defining effect | Saved original owner | Review |
|---|---|---|---|
| Big-Fisted Belt `belt15` | Cursed: Strength 19, Intelligence 6, Remove Curse to unequip | `are:oh3010/container/2/item/4` | **Hold**. It is in Rasaad's BG1 quest temple, physically ordinary chest loot, but its quest access and high early power need the user's inclusion/tier decision. [BG:EE item guide](https://forums.beamdog.com/discussion/8110/a-guide-to-new-magic-items-in-bg-ee-spoilers) corroborates context. |
| Rugged Leathers +2 `leat25` | Light armor with Move Silently penalty | `are:oh2010/container/0/item/5` | Review only after checking Neera-area access. The chest is physical loot; access to Adoy's Enclave is tied to Neera. [Area guide](https://www.gamebanshee.com/baldursgate/walkthrough/adoysenclave.php). |
| Amulet of Spell Warding `amul25` | Saves vs. spells | `cre:kryll/item/2` (also other creature templates/stores) | **Hold**. Kryll is tied to Dorn's BG1 story; the scan flags creature-template scope, and some other copies are undroppable. Bind the actual spawned copy and account for duplicates before considering it. [BG:EE item guide](https://forums.beamdog.com/discussion/8110/a-guide-to-new-magic-items-in-bg-ee-spoilers). |

**Before selection:** source records and item effects must be checked against the *chosen* installed mod stack. The saved catalog shows several SoD/EE resrefs as effective `override` resources, so their installed bytes can differ from Beamdog's published/base descriptions; RR source descriptions also cannot establish later patches by Item Revisions or other mods. The world scan is physical-record coverage, not a scripted-award or quest-safety certificate. For any approved candidate, identify the exact installed original, its duplicates/branch alternatives, whether removal affects an encounter or quest, and whether an equipped source needs a replacement. The user then chooses inclusion and tier for each campaign pack.
