# Mode 1 fixture acceptance, 26 September 2026

The isolated fixture passed a fresh-process B reload and two fresh campaigns
under unchanged configuration B. This remains a three-item, one-area engineering
test, not acceptance of the full Randomiser or a normal playthrough.

## Verified results

Configuration B includes `FLPRI1` and `FLPRI2`, and excludes `FLPRI3`.
Its immutable revision is
`635385dbf8161ff8e4d93aaa56f5243d9f1ebe59d86983ca3f289a4f9ea7cb90`.

| Case | Seed | FLPRI1 / FLPRI2 target containers | Saved evidence |
| --- | --- | --- | --- |
| Existing B, loaded in a fresh process | 321352238 | 7 / 6 | `000000046-IR QA B Reloaded 0926` |
| Fresh B campaign 1 | 1106903324 | 5 / 6 | Initial Prologue Start autosave, preserved outside the game |
| Fresh B campaign 2 | 1910876369 | 7 / 5 | Initial autosave and `000000047-IR QA B Fresh2 0926` |

Each saved observation contains exactly one of every fixture item. Included
items exist only in their assigned target containers; the excluded item remains
only on `FLPRS3`. Full charges, expiration and flags match the B baseline.
All expected actors and containers are observed, both source and delivery phases
are verified, the journal is clear, and no aliased or otherwise located fixture
records are present in the observed area and party inventories.

The next-run pointer still selected A when B was loaded. Every saved B campaign
global, assignment and seed remained identical to the original B save. The
fresh process's seed-allocation counter stayed at zero across that load, then
became one and two for the new campaigns. Selecting B for the new campaigns
changed only `flpr-fixture/next.lua`; no TP2 reinstall was performed.

The offline verifier requires every case, compares saved physical ownership
to the manifest's target assignments, and compares the additional named Fresh2
save to its initial autosave. It does not require random assignments to differ
by chance; these observed assignments did differ.

## Save refusal and QA correction

The first fresh campaign's manual-save attempt did not open SAVE. The engine
displayed `POPUP_INFO`, reference `15729528`, "You cannot save at this time."
The old QA runner waited until `QA_TRANSITION_TIMEOUT:save-list`, hiding the
useful engine reason. No named save was created by that attempt.

Read-only native inspection found Imoen's internal last-action field at action
8 (`Dialogue`) while her current action was zero. Disassembly of this installed
executable's `CGameSprite::CanSaveGame` contains a rejection branch for this
last-action value with the observed message. This is consistent with a
dialogue-related refusal; the precise failing return was not instrumented.
Resuming the world did not clear the value in the later observation.
No NPC actions, save eligibility checks or
campaign state were forcibly cleared. The initial autosave is the accepted
Fresh1 evidence; successful manual saving from that state remains unverified.

The QA runner now stops immediately on a foreground information popup, preserves
its reference and displayed reason, and sends no further clicks. It rejects
starting a flow with an existing information popup. It neither dismisses nor
retries the refusal. This correction passed the three Lua runtime test variants.

The revised QA runner was loaded explicitly from the worktree into the test
process, with the same read-only popup callback as the revised bootstrap.
Fresh2's subsequent normal named save succeeded. An attempted live refusal-path
check therefore observed success instead of a refusal; it is evidence for the
updated runner's normal save path, **not native acceptance of its refusal branch**.
The installed prototype files were not replaced. The next ordinary launch still
uses their previous versions until explicitly staged.

An early attempt to begin the second character helper while the first QA flow
was unfinished failed its first main-menu assertion, before any character
action. Its evidence is retained. The successful helper invocation was started
only after the separate return-to-main-menu operation had completed. Future
orchestration must await command completion, not merely receipt of a session ID.

## Background operation and cleanup

The script-engine task granted an exclusive game/installer window. A shared
reservation at `%USERPROFILE%\.codex\coordination\bg-game-install.lock`
identified its owner and a random token; its advisory deadline did not authorize
another task to steal it. Other game/installer processes were checked before
launch. No conflicting task was interrupted.

Tests ran only in
`%USERPROFILE%\Games\EET-IR-Mode1-20260922\game`, using its private
`EET IR Mode1 Test - 20260922` profile. The loader and game ran on a separate,
non-input Windows desktop. A synthetic child/grandchild probe first verified
desktop inheritance; the actual game window was then observed on that desktop.
The user's desktop was never switched, and no OS mouse or keyboard input was
sent. Character creation and the chapter screen used the existing helper.

The first launch exited before the game opened, without a new crash report.
The successful launch retained the desktop handle throughout the session and
placed the owned process tree in a kill-on-close job with a bounded lifetime.
The first exit's exact code was not retained; it is not claimed to be a diagnosed
engine crash. The desktop launcher is local evidence, not a general released tool.

The game later exited through its ordinary Quit button, InfinityLoader exited
zero, and the guard exited zero. No Baldur/InfinityLoader process or pending
console request remained. Original A selection was restored byte-for-byte;
preexisting named saves 41–45, immutable manifests, and the installed UI retained
their hashes. The private profile's normal settings file and initial autosaves
were updated by gameplay. Both fresh initial saves were preserved outside the
game before later New Games could replace them.

The reservation was removed only after verifying the owner token and cleanup,
and the script-engine task was notified that the window was released. No
follow-up polling automation was needed.

## Evidence and limits

Local generated evidence is under `work/mode1-overnight-20260926/` and must not
be committed. Key files are `acceptance-summary.json`, `verify_acceptance.py`,
the three `b-*-save.json` observations, `fresh2-named-save.json`, both preserved
`fresh*-prologue-save/` directories, `offline-tests-status.json`, and
`reservation-released.json`. The full offline suite passed **564 tests** in
9.984 seconds, exit zero. Nine additional checks cover information-popup handling.

General creature drops, distant sources, shops, multiple rewards per target,
pending-operation recovery, multiplayer, real-item configuration and the final
configuration UI remain outside this acceptance. Creative item/target selection
still belongs to Christopher. No normal player installation or saves were used.
