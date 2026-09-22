# Runtime feasibility evidence

Status: source review on 2026-09-22. This is evidence for the experimental
campaign controller, not a claim that the engine integration has been proved.
No live game, save, engine console, or installed resource was accessed for this
review. The lifecycle and source-mutation probes below have not been run.

## Sources and version boundary

The official EEex checkout inspected was
`a0502166ea7aaeacc39c6349efe2fb40bbbe203e`; the official documentation checkout
was `35445db362f56095156e3b43aa8f6f0f50f728a0`. Both matched their remote HEAD
when checked. File links below are pinned to those revisions. EEex's development
branch is not a tested minimum release requirement for this mod. A production
adapter must establish its supported release versions separately.

Local project findings refer to the existing delivery implementation present at
the start of this milestone on the development branch, commit `38d04d0`. It is separate from the new
post-install campaign prototype.

## Session lifecycle

| Surface | Observed behavior | Integration consequence |
| --- | --- | --- |
| `EEex_GameState_AddInitializedListener` | Runs after Lua initialization during engine startup, once per process; registration after initialization invokes the listener immediately. | Not a New Game or Load Game signal. |
| `EEex_GameState_AddDestroyedListener` | Runs after game-session cleanup, including quitting to the main menu and loading a save. | Discard cached campaign state and object references. It does not identify the next session. |
| `EEex_Sprite_AddLoadedListener` | Dispatches after the sprite's effect list has been unmarshalled, from inside sprite unmarshalling. | Per-object notification, not proof the campaign, inventory, or area is ready. |
| Includes/menu callbacks | Concern Lua/UI resource loading or rebuilding. | A menu reload must not initialize a campaign or reroll assignments. |

The first two contracts are explicit in
[EEex_GameState.lua:157-187](https://github.com/Bubb13/EEex/blob/a0502166ea7aaeacc39c6349efe2fb40bbbe203e/EEex/copy/EEex_scripts/EEex_GameState.lua#L157-L187).
Their engine patch sites are respectively after `CLUAConsole::LuaInit()` and the
last call in `CInfGame::DestroyGame()`:
[EEex_GameState_Patch.lua:6-48](https://github.com/Bubb13/EEex/blob/a0502166ea7aaeacc39c6349efe2fb40bbbe203e/EEex/copy/EEex_scripts/EEex_GameState_Patch.lua#L6-L48).
The sprite callback dispatch is at
[EEex_Sprite.lua:1604-1619](https://github.com/Bubb13/EEex/blob/a0502166ea7aaeacc39c6349efe2fb40bbbe203e/EEex/copy/EEex_scripts/EEex_Sprite.lua#L1604-L1619),
called through the effect-list unmarshal patch at
[EEex_Sprite_Patch.lua:209-219](https://github.com/Bubb13/EEex/blob/a0502166ea7aaeacc39c6349efe2fb40bbbe203e/EEex/copy/EEex_scripts/EEex_Sprite_Patch.lua#L209-L219).

There is an internal native hook immediately after GLOBAL variables are
unmarshalled, used by EEex itself. The inspected Lua API does not expose a
registration function for it:
[EEex_GameState_Patch.lua:50-72](https://github.com/Bubb13/EEex/blob/a0502166ea7aaeacc39c6349efe2fb40bbbe203e/EEex/copy/EEex_scripts/EEex_GameState_Patch.lua#L50-L72).
No public New Game-versus-Load Game listener was found in the inspected scripts.
This is a bounded search result, not proof that a native extension cannot add one.

The engine's New Game button entry point is documented as
[`startEngine:OnNewGameButtonClick()`](https://github.com/Bubb13/EEex-Docs/blob/35445db362f56095156e3b43aa8f6f0f50f728a0/source/EE%20Game%20Lua%20Functions/startEngine/startEngine_OnNewGameButtonClick.rst).
That is a user-interface command, not a completion callback. Character creation
can be cancelled, and multiplayer, imports, tutorial starts, alternate UIs, and
EET transitions need their own coverage. Merely observing a click is insufficient
to authorize campaign writes. The documented
[`m_bFromNewGame` field](https://github.com/Bubb13/EEex-Docs/blob/35445db362f56095156e3b43aa8f6f0f50f728a0/source/EE%20Game%20Structures%20%28x64%29/CI/index.rst#L477)
is likewise not an established lifecycle contract; its reset/order semantics were
not proved.

Consequently, the prototype requires `capture(manifest, seed, "confirmed-new-game")`
from its trusted caller. No automatic `M_` loader or zero-marker heuristic supplies that handoff.
An unmarked save remains inactive. A trusted engine bridge must prove the new-game
boundary before this method can be wired to gameplay.

## Persistence and campaign identity

`EEex_GameState_GetGlobalInt` and `EEex_GameState_SetGlobalInt` access the game
object's GLOBAL variable table. A missing integer returns zero:
[read](https://github.com/Bubb13/EEex/blob/a0502166ea7aaeacc39c6349efe2fb40bbbe203e/EEex/copy/EEex_scripts/EEex_GameState.lua#L25-L36),
[write](https://github.com/Bubb13/EEex/blob/a0502166ea7aaeacc39c6349efe2fb40bbbe203e/EEex/copy/EEex_scripts/EEex_GameState.lua#L82-L92).
The [GAM format](https://gibberlings3.github.io/iesdp/file_formats/ie_formats/gam_v2.0.htm)
has a dedicated GLOBAL-variable table with 32-byte names and four-byte integer
values. These are save-level records rather than CRE-local state.

This supports the prototype's choice of numeric GLOBALs for a committed campaign
marker, complete revision identifier, seed, and explicit assignments. Store a
256-bit revision as bounded integer chunks, not as one Lua number. Write the
commit marker only after all required fields, and reject incomplete or inconsistent
data on resume. Zero does not distinguish an absent variable from a deliberately
zero-valued variable, so zero-sensitive fields require explicit presence encoding
or independent validation.

The complete immutable configuration remains in its exact revision file. A saved
revision identifier is an anchor to that file, not an embedded copy of the entire
profile. Missing or changed revision content must disable the experimental
campaign path; it must never silently use the current next-run configuration.
Copying a save to another installation therefore also requires retaining its
matching configuration revision. A single seed is not a unique campaign ID:
independent runs can intentionally use the same seed and configuration. Their
progress remains separate because it lives in their own save GLOBALs.

There is no public arbitrary-size campaign marshal-handler API in the inspected
Lua surface. Structured profile and assignment data can be represented by the
sprite marshal format, but that format belongs to a CRE. Its table walk supports
strings, numeric values, nested tables, and, in the inspected version, booleans:
[EEex_Sprite.lua:1082-1204](https://github.com/Bubb13/EEex/blob/a0502166ea7aaeacc39c6349efe2fb40bbbe203e/EEex/copy/EEex_scripts/EEex_Sprite.lua#L1082-L1204).
The serializer writes `X-BIV1.0` data in extra effect-list storage:
[EEex_Sprite.lua:1284-1345](https://github.com/Bubb13/EEex/blob/a0502166ea7aaeacc39c6349efe2fb40bbbe203e/EEex/copy/EEex_scripts/EEex_Sprite.lua#L1284-L1345).
This is not evidence that saving a large arbitrary profile on a party member is
safe. The value encoding uses integer writers and terminated strings, so arbitrary
JSON numbers or strings must not be presumed lossless:
[EEex_Sprite.lua:1207-1235](https://github.com/Bubb13/EEex/blob/a0502166ea7aaeacc39c6349efe2fb40bbbe203e/EEex/copy/EEex_scripts/EEex_Sprite.lua#L1207-L1235).
Capacity, unsupported values, cyclic tables, and save/load behavior require
separate validation; older EEex versions have different boolean support.

EEex explicitly strips sprite UUIDs around clone creation and character export,
and suppresses UUID generation for item-comparison copies:
[EEex_Sprite_Patch.lua:711-753](https://github.com/Bubb13/EEex/blob/a0502166ea7aaeacc39c6349efe2fb40bbbe203e/EEex/copy/EEex_scripts/EEex_Sprite_Patch.lua#L711-L753).
Its own marshal handler suppresses summoner metadata when
`EEex.IsMarshallingCopy()` is true:
[EEex_Marshal.lua:2-15](https://github.com/Bubb13/EEex/blob/a0502166ea7aaeacc39c6349efe2fb40bbbe203e/EEex/copy/EEex_scripts/EEex_Marshal.lua#L2-L15).
The native implementation of that predicate was not established here; it must not
be described as specifically a quicksave-preview filter. A party slot, object ID,
sprite UUID, or imported character is therefore not a proven campaign identity.
UDAux is also deleted with its object:
[EEex_GameObject.lua:488-506](https://github.com/Bubb13/EEex/blob/a0502166ea7aaeacc39c6349efe2fb40bbbe203e/EEex/copy/EEex_scripts/EEex_GameObject.lua#L488-L506).

## Sources, targets, and exact item mutation

`EEex_Resource_Fetch` locates a resource in the engine resource table, and
`EEex_Resource_Demand` demands that resource's data:
[EEex_Resource.lua:95-155](https://github.com/Bubb13/EEex/blob/a0502166ea7aaeacc39c6349efe2fb40bbbe203e/EEex/copy/EEex_scripts/EEex_Resource.lua#L95-L155).
These are not per-campaign resource overlays. Mutating the demanded resource is
not a demonstrated way to keep two campaigns with different configurations
independent within one process. No public resource-demand interception or
campaign-specific replacement API was found in this script surface.

The store iterator traverses file-format inventory entries:
[EEex_Resource.lua:210-237](https://github.com/Bubb13/EEex/blob/a0502166ea7aaeacc39c6349efe2fb40bbbe203e/EEex/copy/EEex_scripts/EEex_Resource.lua#L210-L237).
That does not prove access to the current save-cached shop inventory.
`EEex_Area_GetVisible` obtains the currently visible area, not an area-load event:
[EEex_Area.lua:2-11](https://github.com/Bubb13/EEex/blob/a0502166ea7aaeacc39c6349efe2fb40bbbe203e/EEex/copy/EEex_scripts/EEex_Area.lua#L2-L11).
No public container-loaded or store-loaded listener was identified.

The existing project's `copy/M_FLDLV.lua:338-362` reads visible creature slots and
container items for delivery verification, collecting resref and charges. Source
suppression needs more: the exact owner instance, record or slot, full item
signature including wear/flags, and a mutation primitive with verified ownership
and lifetime behavior. A source record in CRE/ARE/STO is not itself proof of how
many runtime objects or copies will exist. The engine can reuse a creature
resource for multiple spawns.

The current x64 structure documentation exposes `m_useCount1` through
`m_useCount3`, `m_wear`, and `m_flags` as distinct instance fields:
[CItem structure](https://github.com/Bubb13/EEex-Docs/blob/35445db362f56095156e3b43aa8f6f0f50f728a0/source/EE%20Game%20Structures%20%28x64%29/CI/index.rst#L1011-L1043).
Observing those fields is not proof that directly changing or freeing the object
will update inventory bookkeeping correctly.

No supported exact source-removal helper was established in the current official
Lua scripts. Methods appearing only in the documentation's **x86** class catalog
are not sufficient evidence of callable x64 Lua bindings. General script actions
that select an item by resref cannot substitute for selecting one bound copy when
matching copies coexist. Source removal remains behind a no-mutation adapter
boundary until the exact API and event ordering are proved.

The current delivery controller is also insufficient as a campaign anchor:
`copy/FLDLVCor.lua:634-649` clears its transaction fingerprint after completion,
and `:662-686` writes it only as part of a delivery transaction. The new permanent
campaign revision must be separate. Existing token semantics must not be
reinterpreted by swapping a shared runtime manifest.

## Deferred read-only lifecycle probe

Use an explicitly selected disposable game, with the new source-removal and
delivery adapters disabled. An observer should record a sequence number and
engine ticks for the already-supported initialization, destruction, sprite-loaded,
and world-open observations. Keep logs outside game/save resources; the observer
must make no GLOBAL, inventory, resource, or save writes. Do not retain sprite userdata after
the callback; any later observation must reacquire the object.

The test operator records which action was requested. Cover process startup,
New Game through character creation, cancelled character creation, normal Load,
Continue, quickload, returning to the menu, two new runs in one process, imported
characters, and supported EET transitions. Observe which campaign fields and
GLOBAL reads are valid at each boundary. Reading an undocumented candidate field
does not make it the classifier; compare its lifecycle across the complete set
before relying on it.

This probe establishes ordering and distinguishes candidate signals. A subsequent
separate disposable-engine test is still required to prove persistent writes,
save/reload continuity, exact-copy suppression before player access, reliable
delivery, and isolation between campaigns. Synthetic controller tests can prove
the state-machine contract but cannot prove any of these engine behaviors.
