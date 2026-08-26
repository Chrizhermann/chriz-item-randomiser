# Handover — Item Randomiser: lost-item restore + delivery redesign

Written 2026-08-26 for whichever agent picks this up (Claude, Codex, ChatGPT). Everything
below is verified against the live install/saves unless marked *hypothesis*.

## 0. Hard rules (from the owner)

- **Game dir `C:\Games\Baldur's Gate II Enhanced Edition modded\` is READ-ONLY.** No
  installs, no override writes, no WeiDU runs there. The only sanctioned write is the EEex
  remote-console IPC (`override/eeex_remote_cmd.lua` + result JSON), used by the client below.
- Never uninstall any WeiDU.log entry. Never hand-edit WeiDU.log, .dlg or .baf files.
- Saves live in `C:\Users\chris\OneDrive\Documents\Baldur's Gate - Enhanced Edition Trilogy\save\`
  (NOT the BG2EE folder). Live line = **`000000476-yaga dead`** (ToB, ch. 21). Never write
  to saves directly — the owner saves in-game after a console operation.
- **No item-location spoilers** for content the owner has not reached (they play blind).
  Content already cleared (SoA, Underdark, Watcher's Keep levels 1-4) may be discussed.
- **Hand-over criterion (owner-stated, final): give back ONLY items that a bug — the mod's
  or our earlier restore scripts' — made mechanically unobtainable. NEVER items that were
  delivered correctly and were missed or skipped by the player.** Belm, Water's Edge and
  the Sling of Arvoreen were delivered fine and are excluded on purpose.
- Only one remote-console client at a time (single IPC channel).
- Tooling note: Windows Python needs `C:/...` paths, Git-Bash tools need `/c/...`.
  `EET\bin\win32\x86_64\lua.exe` (Lua 5.3) syntax-checks Lua; the game itself runs LuaJIT 5.1.
  Avoid bash heredocs for prose containing apostrophes (the tool wrapper single-quotes it).

## 1. Task A — hand the owner the 12 bug-lost items

### 1.1 Status

**COMPLETED AND PERSISTED 2026-08-26.**
`research/2026-08-live-audit/handover_v4.lua` was executed exactly once with the game
loaded from **`000000476-yaga dead`** in `AR5203`. The remote result was
`{ok=true,n=12,area="AR5203"}`; a fresh live read showed all seven repair guards at `-1`,
the five delivered-sanity tokens still at `-1`, and the three Vongoethe watchpoint tokens
still at `12`, `10`, and `6`.

A distinct in-game save was then written to **`000000040-Interval-Save`**; the source slot
remains preserved. Read-only save forensics proved execution and persistence:

- current ARE item-table count increased **265 → 277**;
- each of the 12 exact target resrefs increased **0 → 1**;
- the 12 records are referenced by six new type-4 ground-pile containers (two items per
  party-member tile), proving they are lootable area drops rather than corpse inventory;
- saved charges match the effective ITMs (`halb10=0,2,0`, `wa2helm=1,0,0`, all others
  `0,0,0`);
- the seven repair guards persisted at `-1`, while all sanity/watchpoint tokens remained
  unchanged;
- new `BALDUR.gam` SHA-256:
  `E34FBF355413F0DC9F60F2CE7CEA408DD6A7D667BCC8E5615E065DFBE0E36093`.

**Do not execute the full v4 script again on this playthrough.** The drops are already
materialized and saved. If a later check finds a genuinely missing drop, inspect the saved
ARE first and use a separately guarded *drops-only* recovery; never reset or replay the
token mutation.

### 1.2 The list (12) and why each is a bug loss

| resref | item | why unobtainable |
|---|---|---|
| halb10 | Ravager +4 | delivered to `gorlic01` (Azamantes) mid-fight; boss script swap plus the pedestal gate (`ITFPEDC.BCS`, `PedCDone=2`) destroyed it; token already -1 |
| dagg23 | Ixil's Spike | same fight, same mechanism; token already -1 |
| sw2h21 | Psion's Blade +5 | our 2026-07-25 restore wrote `fl10t17=20` — no delivery slot has value 20, so it can never fire |
| ax1h10 | Azuredge +3 | our restore wrote `fl6t11=4` — no slot 4 → never fires. (A shop copy exists in this modpack by design; the randomiser copy is a deliberate second copy — the earlier "you sold it" claim was wrong and is retracted.) |
| sw1h66 | Yamato +4 | our restore put two tokens on slot 18 (`fl10t15=18`); slots are single-use and that one delivered `xbow15` first |
| ax1h16 | K'logarath +4 | pending `fl10t01=1` → target `gorgua01` is spawn-gated by a pedestal puzzle that is permanently closed |
| halb04 | Dragon's Bane +3 | pending `fl4t07=18` → target `sahramb3` can never spawn again (SoA area, era-locked) |
| ring46 | Ring of Anti-Venom | pending `fl11t09=13` → delivery creature and its target sit in different areas; `Exists()` can never be true |
| compon08 | Starfall Ore | pending `fl12t08=3` → same cross-area defect (also blocks a Watcher's Keep forge upgrade) |
| helm07 | Helm of Balduran | delivered **into the corpse** of `uddeath2` (the ar2402 exit-gate Demon Knight leader) after the kill — see §2.2 class 7; token -1 |
| halb05 | Dragon's Breath +4 | same corpse, same mechanism; token -1 |
| wa2helm | Vhailor's Helm | same corpse (token `fl7t03`, delivered by `fl7t11`); the owner never had it — absent from GAM, bags and every store |

Charges, read from the effective override ITMs: `halb10` = `0,2,0`; `wa2helm` = `1,0,0`;
all others `0,0,0`. `CreateItem("x",0,0,0)` creates charged items **empty** — never guess
charges, read ITM abilities at `LONG_AT 0x64` / count `0x68`, stride `0x38`, max charges
`SHORT_AT ability+0x22` (first three abilities only).

**Corpse-delivery proof** (the interesting one): in save `000000457` the dead `uddeath2`
still lists `helm07` (slot 21), `halb05` (slot 22) and `wa2helm` (slot 23) in its embedded
CRE, while its own install-time droppables `C0WLPL01` (slot 24) and `SW2H02` (slot 23) are
gone from the record. Dead creatures keep only what did NOT drop, so those three arrived
**after** the death-drop ran. The owner's stream VOD independently confirms the knights
dropped only their +1 two-handed swords.

### 1.3 What the script does, and its guards

1. Aborts unless the **Vongoethe watchpoint** tokens are still live: `fl10t13=12`,
   `fl10t16=10`, `fl12t15=6`. Those three deliveries (`staf21`, `sw1h70`, `compon15`) are
   healthy and fire during the upcoming Marlowe/Vongoethe quest — do not touch them.
   Re-verify after the owner finishes that quest and hand over only if the no-fight path
   loses them.
2. Aborts unless the already-lost tokens read -1: `fl10t06`, `fl13t06`, `fl5t08`,
   `fl7t12`, `fl7t03`.
3. Aborts unless the 7 pending guard tokens hold exactly the expected values (this is what
   proves the right save is loaded).
4. Sets those 7 guards to -1, so the broken deliveries can never double-fire later.
5. `CreateItem` + `DropItem` for each of the 12 items at a party member's feet,
   round-robin across the party so the drops spread over several tiles.
6. Returns JSON `{ok, n, placed[], after{}, area}`, or `{aborted=...}` naming the mismatch.

### 1.4 Execution record (historical — do not rerun)

Client repo: `C:\src\private\eeex-remote-console` (README has the full protocol).

This is the exact command that was used successfully, retained only for provenance:

```powershell
& "C:\src\private\eeex-remote-console\tools\eeex-remote.ps1" `
    "C:\Games\Baldur's Gate II Enhanced Edition modded\override" `
    '@C:\src\private\bgee-itemrandomiser-fix\research\2026-08-live-audit\handover_v4.lua' 30
```

Use proven-safe EEex primitives only: `EEex_GameState_GetGlobalInt` / `SetGlobalInt`,
`EEex_Sprite_GetInPortrait`, `sprite.m_pos`, `sprite.m_pArea.m_resref:get()`,
`EEex_Action_QueueResponseStringOnAIBase`. Do **not** pass a `CGameObjectType` int where the
API expects a `CAIObjectType*` — that caused two hard crashes in July. A client timeout
means the game crashed; check before retrying.

The post-save check is stronger than the original planned visual-only check: the distinct
save contains all 12 exact item records and correct charges. The owner can now pick up the
drops and continue from `000000040-Interval-Save`.

### 1.5 Evidence files (`research/2026-08-live-audit/`)

- `sweep.json`, `stage1.py`, `stage2.py` — full per-token audit of all 293 BG2 rows
  (`lists/items/base/bg2.2da`), reproducible.
- `tokens_live.json` — every `fl*` GLOBAL in yaga dead (276 delivered, 30 pending).
- `token_history.json` — per-token transitions across 225 saves.
- `creature_targets.json`, `deliveries.json` — every delivery creature block: area, target,
  slot value, item.
- `presence.json` — structural item-presence scan across areas, stores and GAM.
- `ASSIGNMENT.json`, `handover_final.json` — the 2026-07-25 restore plan (86 handed over,
  55 re-seeded) that introduced defect classes 3 and 5 below.

## 2. Task B — fix the randomiser (this fork)

### 2.0 Repository status

The working repository already exists locally at
`C:\src\private\bgee-itemrandomiser-fix`. Its history contains the unmodified import
(`0da5eff`), the EET/BGT `flSqueaked` fix (`b600e94`), and this audit/handover
(`a25c11e`, plus any later handover-status commit).

It is **not yet a GitHub fork/repository**: no git remote is configured, and no matching
repo currently exists under the owner's personal GitHub account. Do not re-import the
source or start another local repo. The next repository step is simply to create the
personal remote/fork, add it as a remote, and push this existing history when the owner
authorizes that external action.

### 2.1 How the mod delivers (verified from source and saves)

- **Assignment**: SSL "number cruncher" creatures (`ssl/fltier.ssl` → `baf/`) roll the item
  list at game start and set `GLOBAL fl<tier>t<token>` to a slot value N.
  `0` = never assigned, `N` = pending at slot N, `-1` = delivered.
- **Delivery**: `lib/delivery.tpa` emits one invisible one-shot creature per location,
  `fl<tier>t<V>.cre` + `.bcs`, placed in the ARE. **V is a per-tier sequential counter over
  the filtered runtime location list, not the raw 2DA token** — resolve destinations only
  via placed actors plus their BCS, never by indexing the 2DA. Each BCS holds one block per
  token: `Exists(target)` AND `Global("fl<t>t<tok>","GLOBAL",V)` →
  `GiveItemCreate(item, targetDV)` for creatures, or
  `ActionOverride(container, CreateItem(...))` for containers → `SetGlobal(...,-1)` →
  `DestroySelf`. Container object names match case- and space-insensitively
  (`container1` resolves `Container 1`).
- **Charges** come from `lib/lib.tpa:390-405` (`read_charge_array_on_itm`), consumed at
  `lib/delivery.tpa:69`.

### 2.2 Failure classes found (all verified on this run)

1. **Shared `flSqueaked` GLOBAL across the BG1 and BG2 passes (EET/BGT)** — the BG1 pass
   sets it in Candlekeep, the ungated `IF Global(flSqueaked,1) THEN DestroySelf` then makes
   the BG2 crunchers self-destruct on spawn, so ~143 BG2 items are never assigned.
   **FIXED in this fork @b600e94** (`fltier.ssl` → `flSqueakedAreaCode`, `flrtobc.baf` →
   `flSqueaked%BG2Area%` + EVAL). Install-tested? **No** — still needs a fresh EET test install.
2. **Target replaced or destroyed by a boss script** (Azamantes `gorlic01`): the item is
   delivered to a creature that is then swapped out or removed, and goes with it.
3. **Token value with no matching slot** — the mod's own `brac16 fl6t6=1`, and ours
   (`fl10t17=20`, `fl6t11=4`). Silently never delivers.
4. **`-1` written without verifying the item actually landed.**
5. **Slots are single-use; two tokens on one slot lose the second** (our `fl10t15=18`).
6. **Cross-area target** — delivery creature in one ARE, target static in another
   (slots in ar6107 vs `senill01` in ar6108) → `Exists()` never fires.
7. **Post-mortem delivery** — `Exists()` is true for corpses and the blocks carry no
   `!Dead()`, so a runtime-spawned target can die before the delivery creature's next pass;
   `GiveItemCreate` then lands in the corpse, after the death-drop, and the item never
   reaches the loot pile. Three unique items were lost this way in a single fight.

Also worth knowing: `CreateItem(...,0,0,0)` empties charged items, and `DestroySelf` on
first match means a slot serves exactly one token.

### 2.3 Recommended design (owner prefers EEex; their mod collection already requires it)

Replace the BCS creature deliveries with a single EEex module (`override/M_FLDLV.lua`,
roughly 200-300 lines) installed as a WeiDU component. Keep the SSL assignment — already
fixed — and keep the `fl*` GLOBAL contract so existing saves stay compatible.

- On area load (`EEex_Sprite_AddLoadedListener`, or an area-enter hook): for every pending
  token whose destination is this area, resolve the target. Living creature → give it.
  Dead or missing creature → place a ground pile at the recorded coordinates.
  Container → insert.
- Deliver only after confirming the item object exists, then mark -1 (verify-then-mark).
- Cross-area and spawn-gated targets become impossible by construction, because the
  fallback is always "deliver to coordinates".
- Add a stuck-token sweeper: any token pending on a slot value with no location, or still
  pending after its area has been visited N times, gets re-routed or handed to the party
  with a one-line log.
- Generate a manifest at install time (JSON or 2DA): token → `{area, target, x, y, item,
  charges}`. That is the only lookup the Lua needs at runtime.

**Minimal BCS-only alternative**, if EEex is rejected: add `!Dead(target)` to every
delivery block plus a `Dead(target)` fallback block that `CreateItem`s at the creature's
last position; drop cross-area rows from the location lists; assert at install time that
every emitted token value has a matching slot; and verify with `HasItem` before setting -1.

EEex references: skill KB at `~/.claude/skills/bg-modding/references/eeex-*.md` (sprites,
listeners, actions); the remote-console repo shows working IPC and action-queue code.

### 2.4 Test plan

1. Fresh EET test install via the `chriz-bg-collection` driver with a distinct
   `engine_name` in `engine.lua` — otherwise it shares the live user directory.
2. Start a game, dump all `fl*` GLOBALs (pattern in `stage1.py`); after the crunchers
   finish, every BG2 token must be non-zero (covers class 1).
3. Verify each delivery structurally: ARE item table, creature inventory, GAM. When
   scanning saved areas, match CRE resrefs as `'*' + resref[1:]` — the first character is
   replaced by `*` for embedded CREs. Never use substring scans; mod-prefixed resrefs
   (OHSW1H52, BDBOOK04) false-positive.
4. Kill a runtime-spawned delivery target before its delivery fires; the item must still
   end up lootable (covers class 7).

## 3. Reference: save and GAM offsets used by the audit

**GAM V2.0**: globals table `0x38` offset / `0x3C` count, 84-byte entries (name at +0,
32 bytes uppercase; int value at +0x28). Party table `0x20`/`0x24`, PC struct 0x160 bytes,
area resref +0x18, X/Y +0x20/+0x22. Out-of-party NPCs `0x30`/`0x34`. Current area `0x58`.

**BALDUR.SAV**: signature `SAV V1.0`, then per entry: nameLen, name, uncompressed length,
compressed length, raw zlib stream. No file count — parse to EOF.

**Saved ARE**: actors `0x54`/`0x58`, stride 0x110; CRE resref at +0x80 with the first
character replaced by `*`; embedded CRE offset/size +0x88/+0x8C. Area items: count at
`0x76` (word), offset at `0x78`, stride 20. Containers `0x70`/`0x74`, stride 0xC0.

**CRE**: items `0x2bc`/`0x2c0` (20-byte entries), slot table `0x2b8` (0 helm, 1 armor,
4-5 rings, 9-12 weapons, 21-36 backpack), state dword `0x20` (`0x800` = dead).
**ITM**: droppable = flags dword at `0x18`, bit `0x4`.

Key inference rule: **a dead creature's record lists only the items that did NOT drop.**

## 4. Open items

- Pick up the 12 already-saved drops in `AR5203` and continue from
  `000000040-Interval-Save`; preserve `000000476-yaga dead` as the pre-repair source.
- Vongoethe watchpoint (§1.3) once that quest is resolved.
- Create the personal GitHub remote/fork when authorized, push this existing local history,
  install-test @b600e94, then build §2.3. Upstream PR for class 1 is still pending.
