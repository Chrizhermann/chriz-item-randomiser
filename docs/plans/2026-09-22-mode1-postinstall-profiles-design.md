# Mode 1 post-install configuration and campaign profiles

Status: product direction approved by the owner on 2026-09-22; implementation in progress.

## Product contract

Mode 1 is the main product. A post-install scan reads the effective game after
mods that add, move, or modify items have finished. The player chooses a reusable
configuration. Every New Game uses that configuration to draw a fresh assignment.
Running configuration again changes the next adventure without reinstalling the
Randomiser TP2. Reconfiguration between runs is secondary to dependable repeated
new games with a saved configuration, but the architecture must accommodate it.

The collection can schedule scanning after its install finishes. Standalone users
must be told to scan after finishing their mod stack and rescan when it changes.
A scan is a read of installed resources, not another mod installation.

Christopher owns creative decisions: curated item pools, new destinations, tier
changes, encounter reward counts, and new curses. Engineering may prepare candidate
inventories and propose choices, but must not silently select new content for a
release. Initial technical tests use synthetic items and targets.

## Player workflow

1. Install the runtime/content support and finish the mod stack.
2. Scan effective items and source/destination resources for that installation.
3. Search for items and choose content packs, placement options, and curse settings.
4. Validate and prepare an immutable configuration revision.
5. Start a new game: capture that revision and draw fresh campaign assignments.
6. Continue existing saves using their captured revision and assignments.
7. Optionally return to configuration before another adventure; TP2 reinstall is
   not part of this workflow.

The collection UI belongs under My installs -> Randomiser, after installation.
Search covers all installed item resources, with bounded results and filters; the
selected pool is shown separately. A searchable item is not automatically a proven
relocatable item. Unknown/ambiguous physical sources and script-created copies need
an explicit source rule before runtime preparation can succeed.

The Randomiser owns the file/API contract so the collection UI and a standalone
configurator use the same validator and produce interchangeable data. The current
collection has per-install selection but no game-item index yet.

## Required boundaries

- Installed item bytes are authoritative, including modified vanilla and mod-added
  items. Override precedence, localized names, and archive-only resources matter.
- An item resource ID identifies a definition, not a world instance. Relocation and
  deliberate addition of a new copy are separate policies.
- Source binding must identify the exact original copies, preserve charges and
  quantities, and prove that each selected copy can be suppressed exactly once.
  Items excluded from the campaign retain their normal availability.
- A reusable configuration contains eligibility/settings, not final random
  assignments. Fresh assignment occurs for each new campaign, not each reload.
- A campaign owns a frozen configuration and assignment state. Loading an older
  campaign must not consult a newly selected global profile for its content.
- Source changes and delivery must be coordinated. No destination award may become
  active while its original copy remains unintentionally available.
- Full installation identity is larger than an item-catalogue fingerprint. Source,
  target, script, and relevant rules must be covered by the eventual prepared
  runtime revision. Item hashes alone do not certify installation compatibility.
- Scan/configuration never guesses a mod's author, power tier, campaign origin, or
  safe quest status from a filename prefix.
- The live game and user saves remain outside development writes and tests.

## Existing implementation and migration

The current backend loads a shared FLDLVMan.lua at process startup. SSL/BCS helpers
produce fl<tier>t<token> assignments, and the Lua controller delivers those values.
Source removal still happens in the WeiDU installer. The existing fingerprint is
part of an active delivery transaction, not permanent campaign-profile identity.

Consequently, replacing FLDLVMan.lua when a profile changes is not sufficient. It
would neither restore excluded originals nor protect idle older saves from changes.
Do not expose draft configuration as active gameplay until these boundaries work.

Keep the current supported runtime operational while introducing the new path.
Existing campaign tokens and delivery journals must retain their meanings. The
new path needs an explicit migration/opt-in contract; old saves must not silently
adopt freshly generated content or reinterpret a slot.

Use the existing implementation as the behavioral reference for each migrated
source family. For creatures, consult `lib/delete.tpa`, `del_cre_itm_stuff` and
`del_cre_itm` in `lib/lib.tpa`, and the current `lib/removal_plan.tpa`: configured
replacement equipment, duplicate-source handling and charges are part of that
behavior, not optional omissions. Reuse existing parsing, removal and delivery
where suitable. New tests should target changed behavior and reproduced defects;
representative integration checks suffice for unchanged paths.

Before calling the replacement feature-complete, make one bounded comparison of
the migrated modules against the old item/target selection rules, replacement
equipment, charges, duplicate sources and special quest/script handling. Record
actual missing behaviour as feature work; do not restart an exhaustive audit.

The previously approved August design's requirement to reinstall for catalog
changes is superseded for the new profile workflow. Its safety rules for stable
identities and verified delivery still apply. Mode 2 remains a source of optional
items/targets; expanding its install-time backend is not on this roadmap.

## Delivery and content direction

Reuse the Azamantes delivery work: no recipient action queues, verify an exact new
item instance, and commit only after a later engine observation. Test actual
lootability for early deaths, full inventories, scripted replacements, and reloads.
An unspawned target is deferred, not treated as a reason for an early free drop.
The legacy FLDLV resref/charge count delta is transport precedent, not proof of
the new runtime's required operation-bound instance, full payload, and lootability.

Add selected vanilla destinations missing from Mode 1, plus optional destinations
from the owner's SoD/other mods when installed. Several item assignments can share
one target using its explicit capacity. New target inclusion is a creative decision
and its reliability requires source-backed fixtures and isolated runtime evidence.

Content priority (owner direction, 2026-09-27): provide the original Mode 1 and
Mode 2 item and destination selections, with the original component options
represented in configuration. Both feed the new Mode 1 campaign workflow. Add
EE content, especially SoD, then CoC/RR; other popular mods can follow selectively.
EE companion items are candidates for explicit creative review, not blanket
inclusion. Availability follows the scanned installation. This content direction
does not promise parity with Mode 2's install-time backend.

Proposed preset organization, for owner review: separate item-pool templates and
destination templates, with ready-made configurations that combine both and their
rules. Optional content packs are independent switches rather than a separate
preset for every combination. Applying a template copies its choices into an
editable draft; Save a copy preserves a user's variation. Original component
options that change behavior (rather than membership) stay explicit rules.
Show which content is present, unavailable or awaiting runtime support, and check
the resulting pool against destination capacity and progression constraints.
Additional content does not implicitly enable cross-campaign placement. Exact
item membership, targets, tiers and default combinations remain owner decisions.

Create original curses for weapons, shields, armour/robes, helmets, cloaks, gloves,
boots, belts, rings, and amulets. Frequency, severity, and appearance variation are
separate options. Variants stay fixed within a campaign; identification and removal
behavior are explicit and tested. The owner approves the creative designs.

Future EET crossovers separate origin campaign, allowed destination campaigns, and
power/progression tier. This supports BG2 items in BG1/SoD and the reverse, including
both progression-aware and deliberately unrestricted profiles.

## Engineering milestones

1. **Post-install discovery and draft configuration.** Read effective ITM/TLK data,
   bounded search, reusable draft editing, explicit rebase after a changed scan,
   immutable prepared draft revisions, JSON interface for collection integration.
   No gameplay activation or source mutation. Implemented.
2. **Source and target binding.** Index exact inventory/container/store occurrences;
   model registered script/dialogue sources; bind compatible destinations and
   capacities; distinguish unknown, ambiguous, optional, and guaranteed sources.
   The offline draft layer is implemented; runtime guarantees remain gated on
   milestone 3. See [source binding drafts](../source-binding-drafts.md).
3. **Runtime source/assignment prototype.** Demonstrate the supported EEex surface
   for campaign-specific original-source handling, persistent revision capture,
   new-game assignment, and exact-once delivery. This is a required feasibility
   gate, not an API assumed to exist. Do not modify shared installed resources
   per campaign as a shortcut that changes older saves.
   The logical controller, immutable campaign compiler, and synthetic persistence
   tests are implemented. The native engine gate has passed for the bounded
   three-item starting-area fixture; general source/target support remains open. See
   [prototype contracts](../campaign-runtime-prototype.md) and
   [source evidence](../runtime-feasibility-evidence.md).
4. **Playable narrow vertical slice.** Two campaigns on one disposable installation,
   with different selected pools and no reinstall. Reload each, verify frozen
   assignments, correct originals for excluded items, exact quantities, and
   lootable rewards. The three-item, one-area fixture has native evidence for
   actual pickup in A, a two-item B configuration that preserves its excluded
   original, and A's reload while B was selected. On 2026-09-26, B also reloaded
   in a fresh process while A was selected; two further fresh B campaigns kept
   distinct seeds and exact saved item ownership without a reinstall. The first
   fresh B campaign's manual save was refused, so its preserved initial autosave
   is the evidence for that run; the second passed a normal named save. See
   [native fixture status](../native-fixture-status.md) and
   [26 September acceptance](../native-acceptance-20260926.md) for the exact
   evidence and limits. This does not accept general source/target adapters.
5. **Collection/standalone UI and approved content.** Integrate scan/configuration
   with the collection and add approved item/target packs and placement presets.
6. **Curses and crossovers.** Implement after creative review and per-feature checks.

## Initial tooling limits

Milestone 1 is implemented on `codex/mode1-postinstall-profiles`: scanner, search,
draft editing/rebase, immutable revisions, and a JSON command interface. On
2026-09-22 the first 47 synthetic scanner/profile tests passed. Milestone 2 adds
world-resource discovery, physical record binding, registered grant metadata,
explicit target capacities, and immutable bound drafts. Its synthetic tests cover
the resource reader, binary formats, binding rules and complete command workflow.
The campaign compiler brings the post-install suite to 138 synthetic tests on
2026-09-22. The separate campaign protocol suite passes 85 tests: 28 host scenarios
in each of Lua 5.1, Lua 5.3, and LuaJIT 2.1, plus a command-to-Lua integration test
covering all three runtimes and two synthetic save stores. These tests do not use
the game engine. Later native fixture results establish the bounded milestone 3
case and the listed milestone 4 checks, including fresh-process B reload and two
more fresh B campaigns. General source/target adapters and milestones 5-6 remain
open; the first fresh B campaign's manual-save refusal is covered by its retained
initial autosave, not a successful named save.
No production item/target/curse choices have been added.

The first tooling revision produces item catalogues and drafts only. It must label
item-only prepared outputs `draft-needs-source-binding`; bound outputs are
`draft-needs-runtime-adapter`. There is no active runtime pointer.
Compiled campaign experiments are `prototype-not-playable`; their separate
`prototype-next.json` pointer is not consumed by the installed mod.
It does not patch game resources, invoke WeiDU, edit saves, or claim that physical
record binding proves safe runtime suppression. Output remains outside the game tree.

Root KEY/uncompressed BIFF, effective override ITMs, and EE UTF-8 TLKs are the first
supported formats. Unsupported compressed archives or unmerged DLC must be detected
and rejected rather than silently producing an incomplete index. Missing localized
strings may retain searchable resource IDs with warnings.
