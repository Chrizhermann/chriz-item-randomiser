# Isolated save-menu render crash

On 2026-09-22, the isolated Mode 1 test crashed because test automation called
`saveScreen:SaveGame(#gameSaves.files, "IR Proof A Ground")` from the first
text widget rendered in the `SAVE` menu. Saving freed or replaced the save-list
bitmap resources while the same render pass still held their userdata. A later
portrait widget dereferenced an obsolete bitmap. This is a save-menu automation
lifetime error, not evidence that the native item adapter caused the crash.

## Preserved evidence

- Small dump:
  `%USERPROFILE%\Games\EET-IR-Mode1-20260922\game\InfinityLoader_Crash\InfinityLoader_Crash_0_small.dmp`
- Dump timestamp: `2026-09-22 23:21:12 Asia/Seoul`
  (`2026-09-22T14:21:12Z` in the minidump header).
- Size: `93,458,889` bytes.
- SHA-256:
  `064f1e644d2c88d66f3354214081a4a6f8bb3a0d45aeb1478f51f19dbc5dd591`.

Read-only analysis used the small dump, the installed executable, and the
installed EEex profiler function-name database. The large dump was not needed.

## Verified fault and bitmap identity

Exception thread `85664` raised a read access violation (`0xC0000005`) at
`0x140401DCA`, or `Baldur.exe+0x401DCA`, reading `0x10C787D9D0`.
The fault and the first two callers were verified against their instructions
and stack layout:

| Frame | Address | Function |
| --- | --- | --- |
| Fault | `0x140401DCA` | `dimmDemand+0x3A` |
| Caller return | `0x1403F65B8` | `CResBitmap::DemandLoadedBitmap+0x18` |
| Caller return | `0x1403D0FF3` | `drawItem+0x5A3` |

`drawItem` called `uiExecLuaUser` to evaluate the bitmap expression, then copied
the returned bitmap at `0x5C9EAE00` into its local structure at `0x15EDF0`.
The earlier remote SAVE query reported exactly that userdata address for
`gameSaves.files[1].portrait0`: `userdata: 0x5c9eae00`.

Both the source bitmap and copied structure contained prose fragments instead
of valid resource fields. In particular, resource-index field `+0x34` was
`0x6B636572`, the bytes for `reck`. The fault used that corrupted value as a
resource-array index. The rendered item geometry was `(310, 84, 60, 60)`, which
matches the SAVE list's `gameSaves.files[rowNumber].portrait0` bitmap in the
installed `override/UI.MENU` near line 11856. Together, the exact userdata
identity, overwritten fields, and verified bitmap-demand callers identify the
freed save-list portrait as the faulting resource.

## Timing and scope

- The native fixture's source removals and all three deliveries had completed
  before the save call. The earlier prologue autosave completed at `23:20:22`.
- Automation entered SAVE using `optionsScreen:SaveGame(0)`, then invoked the
  direct save call from the menu's first text-render callback. That call
  reported success at `23:21:06` after approximately 78 ms; the process crashed
  six seconds later. A successful native return did not establish safe
  completion of the remaining render pass.
- The separate pickup request was submitted at `23:21:45`, 33 seconds after
  the dump. It remained unclaimed and was quarantined. Pickup did not cause
  this crash and the pending mutation must not be replayed.
- Read-only saved-file inspection is recorded separately in
  `work/mode1-native-slice/acceptance-a-manual.json`. It is not a successful
  save/reload or pickup acceptance test.

## Verified queued replacement

`FLPRQA.lua` now drives the normal buttons through the game's built-in
`Infinity_ClickItem`, which queues SDL mouse-down/up events. Read-only analysis
of this installed executable verified the two SDL event pushes at
`0x1403C6F4F` and `0x1403C6F61`, with event types `0x401` and `0x402`. The
button action executes later in the normal event loop. There is no operating
system mouse or keyboard injection, focus change, direct SaveGame invocation,
or native hook in this replacement.

Each transition is queued once, then observed on a later frame. The runner
waits for menu animation to finish, verifies the menu and exact save folder,
refuses overwriting an existing named save, and stops on timeout or report-file
failure. Options requires a special guard: the stock screen pushes
`LEFT_SIDEBAR_BOTTOM` after `ESC_MENU`, so that sidebar is accepted only when
the native active engine is also `optionsScreen`.

The isolated process successfully loaded the completed A Prologue Start save,
saved `000000042-IR QA A Saved`, and returned to the world. After actual pickup
of all three items, it also saved `000000043-IR QA A Looted` and returned to
the world without crashing. Parsed saved GAM/ARE data and live item ownership
agree. See [fixture status](native-fixture-status.md) for subsequent acceptance.

Arbitrary remote-console commands remain blocked while SAVE, SAVE_NEWSAVE, or
LOAD is present. The finite QA runner alone progresses through those screens,
reporting to `flpr-lab/qa-status.json`. A command submitted while blocked
remains pending; do not resend it. Moving a render-time native Save/Load call
to the end of a menu is still not an established safe alternative.

Import Game (`start_import`) uses the same LOAD screen in import mode. There the
list names saves by path (`home:/save/<folder>`), and BG2:EE also lists BG:EE's
default profile (`home:/../Baldur's Gate - Enhanced Edition/save/...`). The runner
selects only a folder of the game's own profile; when the save is missing,
ambiguous or LOAD is in the other mode, it presses Back first, so the console is
free again, and then reports the failure.

On 2.7.3 a campaign choice (BG2:EE's logos, BG:EE's campaign buttons, EET's
campaign list) closes START and the engine finishes the reload, reopening START,
only on the next mouse event. A player's mouse provides one at once. A lab game
on a private desktop gets none, so the next queued click is used up by the
reload and its button does nothing. Lab drivers queue one click on an inert
label before the campaign choice and wait until START is back.
