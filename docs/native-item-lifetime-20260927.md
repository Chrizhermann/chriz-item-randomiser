# Bounded item-lifetime diagnostic

This follows the [accepted controlled death-drop run](held-drop-receipts-20260926.md)
and the [offline destructor evidence](native-item-lifetime-evidence.md). It adds
an opt-in diagnostic, not an automatically installed campaign feature.

## Observation contract

`tools/probes/FLPRDrop.lua` retains its existing three-hook default. Calling its
loaded chunk with `{lifetime=true}` selects five hooks, a separate
`flpr-drop-lifetime-trace-v1` schema, and a 4,096-row bounded buffer. It requires
the corrected quiet fixture, no attached `FLPRHeld` observer, and one fresh
process. The caller must verify the pinned executable hash. Every code signature
is checked before any patch; capture starts disabled and cannot be rearmed.

The two extra sites are CItem destructor entries at RVAs `0x1FAA30` and
`0x1FAB10`. Each displaces only the complete five-byte `mov [rsp+8],rbx` and
replays it after the injected assembly restores registers, flags, and stack.
Their longer validation windows are 27 and 29 bytes. The hook copies primitive
item address, caller return address, current payload, and (only for the deleting
entry) EDX deletion flags. It calls neither Lua nor engine functions. Kind 4 is
the ordinary destructor; kind 5 is the deleting destructor. Entry observation
means destruction has begun, not that free has already returned.

`prototype/FLPRLife.lua` consumes this stopped, stable, non-overflowed trace.
It validates every global row and composes the existing pure death correlator
with explicit item-lifetime checks. It rejects destruction of a watched item
before its message constructor copied it, reuse of a still-live old address,
and destruction of a watched ground candidate. Expected original destruction
after copying is allowed, as are unrelated/embedded temporary CItems, including
empty ones. Returned candidate sequence numbers refer to the original combined
trace. Request tokens remain opaque and are retained by reference; no userdata
is dereferenced. Errors close that tracker until a different session token is
supplied. There is no world mutation or saved Core confirmation in this module.

## Offline validation

The full offline suite passed **812 tests**. New cases run on Lua 5.1, Lua 5.3,
and LuaJIT 2.1, including explicit destruction before address reuse, sequence
mapping, temporary objects, stale sessions, loss/overflow, and hook-install
refusal/partial failure. Exact five-byte boundaries were independently checked
against the copied executable and pinned `EEex_HookBeforeRestore` source.

## Native acceptance

A fresh quiet campaign with seed `1269344589` completed character creation and
the chapter screen on a separate non-input desktop. Before launch, no game or
installer process was active, the script-engine and installer chats were idle,
and the other active BG chat was discussing release links rather than running
game tests. The shared reservation was acquired. The installed hook helper's
SHA256 matched the inspected copy (`fa0b63523aa331557359666a38bd4c695c4f59c9e1da4b0f95fc0b6277c41033`).

The probe installed all five hooks once. The harness captured three existing
delivery bindings, then queued ordinary death for FLPRC1 and
`DestroyItem("flpri2")`, `Wait(1)`, `CreateItem("flpri2",0,0,0)`, `Wait(1)`,
`Kill(Myself)` for FLPRC2. Parsed action IDs were `131` and
`169,63,82,63,131`, matching the previously copied ACTION.IDS. Each mutation
sequence was issued once. No receipt observer was attached.

The stopped capture contained **21 contiguous events**, stable with no overflow:
12 death-transfer rows, four ordinary destructor entries, and five deleting
destructor entries. Analysis in the game returned:

| Watched original | Result | Transfer sequences | Original destruction |
| --- | --- | --- | --- |
| FLPRI1 | valid candidate | 2, 3, 6 | row 10, after copying and ground creation |
| FLPRI2 | rejected | no original transfer | row 1, `watched-item-destroyed-before-copy` |
| FLPRI3 | valid candidate | 4, 5, 8 | row 11, after copying and ground creation |

The original FLPRI2 at `1595483728` was destroyed first. Its replacement used
address `1595482320`, slot 34 rather than the original slot 19, and later became
ground item `1595482672`. Full item properties matched, but it did not acquire
the original's identity. **This run did not force or demonstrate reuse of the
destroyed item's address.** That case remains covered by offline tests and the
earlier ordinary-drop capture's address-reuse evidence.

The four ordinary-destructor rows referred to embedded message CItems at the
message address plus 16, after the corresponding consumer row. They were
distinct from the new ground items and did not invalidate those candidates.
One message address was reused after its earlier consumption and embedded
destruction. The five deleting-destructor rows observed the prematurely
destroyed original and all four later disposed drop originals.

A later independent world observation found the two accepted ground pointers
in their recorded type-4 container, plus one replacement FLPRI2 and the unrelated
control in the other container. Each of the four item types had one physical
copy in the observed area/party scope. The three stored delivery identities
remained `[5,6,7]`; all delivery phases remained held and the journal stayed
clear. This diagnostic neither confirmed a receipt nor created a recovery item.

An independent offline replay consumed the same saved trace on Lua 5.1, Lua 5.3,
and LuaJIT 2.1. All three reproduced both accepted candidates, their exact
original sequence numbers and destruction rows, and the rejection at row 1.
Their ground pointers and complete item properties matched the saved world
observation. The stopped and analyzed trace copies were identical, and campaign
assignments, delivery phases, and nonces were unchanged across the artifacts.
This replay adds saved-evidence validation, not another native run.

The game quit normally, both owned processes ended, and the desktop guard exited
successfully. All **18 staged files were restored** and **88 protected files**
(including the prior named saves) retained their hashes. The shared reservation
was removed. No installer ran, no ordinary desktop was selected, and no new named
save was requested; a normal startup autosave may have been refreshed.

Evidence and consumed helpers remain outside the installation in
`work/mode1-lifetime-20260927/`: `03-arm-actions.json`, `04-stop.json`,
`05-analyze.json`, `06-world.json`, source snapshots, helper identity, tests,
`replay_offline.py`, `replay_result.json`, `acceptance.md`, and
`native-released.json`. Do not rerun these consumed mutation requests.

## Remaining limits

- Native installation and both destructor entries worked in this one bounded
  capture. Broader engine paths, concurrent activity, and address-reuse variants
  are not established by it.
- A trace starts at its explicit observation boundary. It cannot establish an
  item's history before that boundary or recover lost evidence after loading.
- These endpoints do not observe in-place `SetResRef`, every ownership change,
  or unsupported native extensions. They do not establish full-game lifetime
  coverage or durable identity across save/load.
- No fallback reward policy, encounter eligibility, real item pool, or curse
  content is selected here.
