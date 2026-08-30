# Synchronous EEex item delivery design

**Date:** 2026-08-31
**Status:** Approved

## Problem

The manifest-driven Mode 1 backend currently compiles `GiveItemCreate` or
`CreateItem` plus an `EEex_LuaAction` acknowledgement, then appends both actions
to the recipient's engine action queue. A controlled Azamantes test proved that
the engine can discard that queue during combat: the journal remained
`QUEUED`, the item was absent, and the recipient had neither a current nor a
queued delivery action.

The same test also disproved the earlier broad corpse-replacement theory. Both
test items left Azamantes's dead CRE inventory normally. The prior empty-corpse
test had instead allowed delivery to select a ground fallback before the
spawn-gated creature existed.

## Hard requirements

- Never use `EEex_Action_Queue*` for delivery.
- Never use a queued `EEex_LuaAction` acknowledgement.
- Preserve the generated manifest and existing `fl*` assignment-global
  contract.
- Commit an assignment to `-1` only after observing one exact new item instance
  with the expected resref and three charge counts.
- Treat a missing or not-yet-spawned endpoint as deferred, not as evidence that
  fallback is required.
- Keep the live game read-only. Install and runtime testing use only the
  disposable EET copy.

## Selected transport

Use EEex's official instantaneous action executor. The installed EET
`INSTANT.IDS` explicitly includes `CreateItem` (82) and `GiveItemCreate` (140).
`EEex_Action_ExecuteScriptFileResponseAsAIBaseInstantly()` executes such an
action directly through `virtual_ExecuteAction()`, restores the recipient's
current action, and never inserts into `m_queuedActions`.

Each delivery contains exactly one parsed item action. There is no engine-queue
submission, acknowledgement action, callback map, or generation guard.

Direct `CGameItem` allocation/list mutation was rejected because the installed
EEex surface provides no supported high-level insertion API and low-level
ownership mistakes could corrupt engine state.

## Endpoint policy

- Living creature with inventory space: execute `GiveItemCreate` instantly on
  that creature.
- Dead or inventory-full creature: select its manifest ground fallback and
  execute `CreateItem` instantly on the resolved type-4 ground pile.
- Resolved container or ground endpoint: execute `CreateItem` instantly.
- Missing or not-yet-spawned primary endpoint: defer until a later sprite-load,
  area poll, or area visit.
- Ambiguous endpoint: fail closed and quarantine; never guess which object is
  correct.

An eventual visited-area/stuck-token sweeper remains separate work. This change
must not make a permanent-removal guess merely because a scripted creature has
not spawned yet.

## Transaction and recovery

The durable journal keeps the existing numeric globals and manifest identity,
but its phases become truthful synchronous states:

- `NONE = 0`: no transaction.
- `PREPARED = 1`: identity, endpoint, signature, and baseline are durable;
  execution has not begun.
- `EXECUTING = 2`: instant execution may have begun; recovery must observe.
- `VERIFIED = 3`: the exact count increase was observed; token commit remains.
- `QUARANTINED = 4`: the transaction cannot safely progress automatically.

Normal flow is: observe baseline, persist `PREPARED`, set `EXECUTING`, execute
one instant action, re-resolve the locked endpoint, observe the exact count,
set `VERIFIED`, set the assignment to `-1`, then clear the journal.

Recovery rules:

- `PREPARED`: clear and retry after normal endpoint selection.
- `EXECUTING`: exact count increase commits; unchanged count on the same
  observable endpoint clears for a safe later retry; an unobservable endpoint
  remains locked and quarantined.
- `VERIFIED`: finish the token commit and clear the journal.
- Item-resource, slot, fingerprint, ambiguity, or observation failures keep the
  assignment positive and record a reason.

Multiple assignments may share a creature, container, or fallback pile. Each
transaction uses its own exact signature and baseline, so an existing matching
item cannot be mistaken for the new instance.

## Scope and tests

Production changes are limited to the transaction core, EEex adapter, their
focused fakes/tests, the runtime transport probe, and concise documentation.
Manifest generation and installer structure remain unchanged unless a test
proves a necessary compatibility adjustment.

Test-first coverage must demonstrate:

1. no delivery queue API or Lua-action acknowledgement is required;
2. instant creature, container, and ground delivery commits only after an exact
   count increase;
3. missing spawn-gated targets defer while dead/full targets use fallback;
4. execution failure, ambiguity, and missing resources keep tokens positive;
5. `PREPARED`, `EXECUTING`, and `VERIFIED` recovery is deterministic;
6. sequential items can share one endpoint or ground pile;
7. a disposable runtime probe changes neither the recipient's current action
   nor its queued-action list.

Verification includes the Lua suite, full installer/unit matrix, a fresh
component reinstall in the disposable EET installation, and the contained
runtime probe. Repeating the Azamantes encounter is unnecessary unless that
probe reveals a discrepancy.
