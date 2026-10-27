# Isolated native Mode 1 fixture status

The sections below preserve the initial synthetic fixture evidence from 26 September.
For the current container, creature and shop work, see
[integrated runtime results](campaign-source-guards-20260927.md).
The normal TP2 does not install this prototype; these samples are not acceptance
of the complete Randomiser.

The isolated install is `%USERPROFILE%\Games\EET-IR-Mode1-20260922\game`.
Its private profile is `EET IR Mode1 Test - 20260922`; the normal modded game
and player saves were not changed. All diagnostics under `work/` are local
generated state and must not be committed.

## Verified in the engine

- `tools/create_fixture_character.py --finish-prologue` creates the imported
  fighter through the existing game callbacks and advances the chapter screen.
  Startup movies are disabled in this private QA profile. The helper does not
  launch the game or use mouse/keyboard input. Its success is not loot acceptance.
- The BG1 New Game intent captures the chosen revision and a fresh seed at
  character acceptance, before entering the world. Loaded old saves do not
  acquire permission to capture simply from a template marker.
- Campaign A, revision
  `5e8c83b9f64341eeb28d8e5980c82a814f9244745d8f4a6bfcf5117f5c3b4d81`, seed
  `1850285926`, completed all three source suppressions and deliveries. Items
  `FLPRI1`, `FLPRI2`, `FLPRI3` were assigned to containers 7, 6, 5 respectively.
- The actual Prologue Start save contains all three items exactly once in those
  ground piles, with none on the original source actors or in party inventory.
  All six saved phases are verified, `FLPR_FRESH=0`, `FLPR_DONE=1`, and the
  operation journal is clear. Save inspection preserves and checks the full item
  payload; it does not substitute installation templates for saved actors.
- Container item creation uses a queued engine message. The native adapter binds
  that exact message to the operation, then verifies the actual resulting item
  across later game ticks. It never retries creation while waiting. See
  [message evidence](native-item-message-evidence.md).
- Normal-button events successfully loaded A and created named saves before
  and after pickup. All three ground items were collected through queued
  MoveToPoint/PickUpItem actions. Their actual native pointers moved to party
  inventory; the named `IR QA A Looted` save contains each item exactly once,
  with zero copies on sources or ground. Native pickup changed flags from 1
  to 3; charges and expiration remained zero. Campaign globals did not change.
- Selecting configuration B only added immutable manifest companions and
  changed the next-run pointer. No TP2 reinstall or item/area resource change
  was required. A's three manifest companions retained identical hashes.
  B's new campaign used revision
  `635385dbf8161ff8e4d93aaa56f5243d9f1ebe59d86983ca3f289a4f9ea7cb90`
  and fresh seed `321352238`. Its named save contains relocated items 1/2 once
  each, and item 3 exactly once on its original source actor.
- In the same process, with B still selected, the queued Load flow restored
  `IR QA A Looted`. A retained its revision, seed, assignments, every persisted
  campaign global, sequence 7, clear journal, and one copy of each collected
  item. `IR QA A Reloaded` independently persisted that state. The session's
  seed-allocation counter stayed at 1 across loading A, proving no recapture.

The initial autosave/prologue save response is delayed until completion in this
fixture. Ordinary unmarked campaigns retain the original save response.

## Save/load automation and scope

A manually named save submitted by the remote console wrote the game state but
then crashed while drawing a freed save-list portrait bitmap. See
[crash evidence](native-save-render-crash.md). This was a render-time Save call
in the QA automation. Moving that call to a later widget is not established safe.
`M_FLPR.lua` now blocks all remote polling while SAVE, SAVE_NEWSAVE, or LOAD is
on the stack, including background overlay routes. The isolated QA runner now
queues normal button events and observes each later transition; it never calls
native Save/Load directly from rendering. Do not submit remote commands while blocked: they would remain
pending for the next permitted screen. The sole unclaimed pickup command from
after the crash was quarantined before any restart. The later verified pickup
used a separate request after successful save/load. Its compiled six-action
sequence was checked before submission: this engine parser only compiled the
first action when actions were separated by spaces, and compiled all six when
separated by newlines. The incomplete first submission only moved the character;
read-only action/ownership checks established this before the corrected sequence.

The bounded acceptance sequence is complete. Generated evidence includes
`qa-a-saved.json`, `qa-a-looted.json`, `qa-b-saved.json`,
`qa-a-reloaded-saved.json`, and `qa-acceptance-summary.json` under
`work/mode1-native-slice/`. The read-only `verify_qa_acceptance.py` in that
directory asserts ownership, full item payloads, coverage, and unchanged
campaign globals. The named saves are 000000042 through 000000045 in the
private profile. They must not be confused with the earlier save from the
crashed session.

On 2026-09-26, B was separately reloaded in a fresh process while A was selected
for the next run. Every campaign global, assignment, seed and physical item was
preserved; the seed-allocation counter stayed zero. Two new campaigns under B
then received distinct seeds and preserved all three items exactly once, including
the excluded source item. Their initial autosaves were inspected and retained;
the second also passed a normal named save. The first encountered a native
save refusal consistent with an NPC dialogue state, which remains a manual-save limitation rather than
a successful save check. The QA runner now reports foreground information popups
instead of masking them as transition timeouts. See
[the exact acceptance and cleanup evidence](native-acceptance-20260926.md).

Pending operations on reload deliberately stop with `PENDING_RELOAD_UNPROVEN`;
this fixture does not claim crash recovery during relocation. Since 1.0.3 a
delivery on its way parks instead, with its counts before it, and the next visit
of its place settles it ([campaign runtime](campaign-runtime-prototype.md)); a
source suppression and a delivery begun by 1.0.2 still stop.
The [next engineering slice](next-native-slice.md) defines creature-drop and
unvisited-source checks using synthetic content, before owner review of real
items, destinations, and reward counts.
The creature variant now has bounded native acceptance: two rewards on one
creature and one on another, held-item save/reload, controlled death after
delivery, actual pickup, and looted save/reload. The unrelated item was retained.
Death changed item pointers, so the runtime still records rewards as held and
does not claim automatic verification of death drops. See
[creature acceptance](native-creature-acceptance-20260926.md). The accepted
ground-pile New Game area and pointer were restored after testing. General distant
sources, shop stock, other death routes, multiplayer, and full-world runtime
support remain outside this slice. The earlier EET
world scan stopped on `BDABIS1D.CRE`'s referenced empty item record. The scanner
now retains and flags that record shape, confirmed against the real resource on
2026-09-24. The read-only rescan indexed 3,544 items and diagnosed dangling-slot
references in `BDCORPSE.CRE`. A follow-up passed that file, then diagnosed a
malformed item name in `OHDFaer.CRE`. Both shapes now have explicit diagnostic
preservation and binding/compilation quarantine. The final read-only retry also
retains incomplete actor storage as unresolved and completed the physical scan:
41,577 source records and 24,305 target candidates. This is separate discovery
evidence, not broader native gameplay acceptance.
See [the rescan evidence](installation-scan-20260924.md).

## Automated checks

665 checks pass on 2026-09-26: 183 post-install scanner/profile tests; 97 logical campaign
checks; 87 host, 108 native container adapter, 60 native creature adapter,
31 fixture builder/helper/save-inspector, 90 queued QA runtime, and 9 QA menu
staging checks.
Lua behavior is exercised under Lua 5.1, Lua 5.3, and LuaJIT. Use unittest for
these Windows runs: pytest's faulthandler labels caught LuaJIT runtime-error
exceptions as fatal even when their assertions pass and the process exits zero.
No assertions or validations were disabled.
The latest run used `python -m unittest discover -s tests -q` and completed
in 14.619 seconds; the test suite itself used no game or installation access. Thirteen empty-inventory
checks reproduce the earlier scanner failure and guard source binding/compilation.
Four additional integration checks cover resource/embedded-actor error context
and failed scans preserving absent or preexisting output files.
Nine dangling-slot and seven malformed-CRE-item checks cover raw evidence,
ownership, external actor references, counts, and binding/compiler quarantine.
Eight unresolved-actor checks cover raw storage fields, quarantine, and rejection
of imported campaign actors with incomplete storage even after stripping markers.
