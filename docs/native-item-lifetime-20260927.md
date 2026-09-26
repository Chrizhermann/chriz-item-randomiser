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

## Validation and native plan

The full offline suite passed **812 tests**. New cases run on Lua 5.1, Lua 5.3,
and LuaJIT 2.1, including explicit destruction before address reuse, sequence
mapping, temporary objects, stale sessions, loss/overflow, and hook-install
refusal/partial failure. Exact five-byte boundaries were independently checked
against the copied executable and pinned `EEex_HookBeforeRestore` source.

Native execution is pending. Prepared helpers are outside the installation in
`work/mode1-lifetime-20260927/`; none of these diagnostics is part of the normal
TP2. After competing tasks are idle and the shared reservation is acquired, the
planned fresh quiet campaign captures the three original delivery bindings,
then issues ordinary death for one recipient and `DestroyItem`, `Wait`,
`CreateItem`, `Wait`, `Kill` for the recipient holding FLPRI2. The expected result
is two valid normal chains and rejection of the destroyed/recreated FLPRI2
original, regardless of whether the allocator reuses its address. No receipt
should be confirmed by this diagnostic. It must stop, retain exact evidence,
close the owned game, restore staging, and release the reservation.

## Remaining limits

- Native installation, event ordering, and both destructor routes are unverified
  until an actual capture succeeds.
- A trace starts at its explicit observation boundary. It cannot establish an
  item's history before that boundary or recover lost evidence after loading.
- These endpoints do not observe in-place `SetResRef`, every ownership change,
  or unsupported native extensions. They do not establish full-game lifetime
  coverage or durable identity across save/load.
- No fallback reward policy, encounter eligibility, real item pool, or curse
  content is selected here.
