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

The previously approved August design's requirement to reinstall for catalog
changes is superseded for the new profile workflow. Its safety rules for stable
identities and verified delivery still apply. Mode 2 remains a source of optional
items/targets; expanding its install-time backend is not on this roadmap.

## Delivery and content direction

Reuse the Azamantes delivery work: no recipient action queues, verify an exact new
item instance, and commit only after a later engine observation. Test actual
lootability for early deaths, full inventories, scripted replacements, and reloads.
An unspawned target is deferred, not treated as a reason for an early free drop.

Add selected vanilla destinations missing from Mode 1, plus optional destinations
from the owner's SoD/other mods when installed. Several item assignments can share
one target using its explicit capacity. New target inclusion is a creative decision
and its reliability requires source-backed fixtures and isolated runtime evidence.

Optional item packs include existing Mode 2-only items, Rogue Rebalancing, and extra
EE items. Their availability is checked against the scanned installation. No promise
of full Mode 2 behavior parity is required.

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
4. **Playable narrow vertical slice.** Two campaigns on one disposable installation,
   with different selected pools and no reinstall. Reload each, verify frozen
   assignments, correct originals for excluded items, exact quantities, and
   lootable rewards. No live-game acceptance claim from synthetic tests alone.
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
The combined post-install suite passes 118 synthetic tests on 2026-09-22.
No game installation or live-engine validation was performed; milestone 3 onward
remains open. No production item/target/curse choices have been added.

The first tooling revision produces item catalogues and drafts only. It must label
item-only prepared outputs `draft-needs-source-binding`; bound outputs are
`draft-needs-runtime-adapter`. There is no active runtime pointer.
It does not patch game resources, invoke WeiDU, edit saves, or claim that physical
record binding proves safe runtime suppression. Output remains outside the game tree.

Root KEY/uncompressed BIFF, effective override ITMs, and EE UTF-8 TLKs are the first
supported formats. Unsupported compressed archives or unmerged DLC must be detected
and rejected rather than silently producing an incomplete index. Missing localized
strings may retain searchable resource IDs with warnings.
