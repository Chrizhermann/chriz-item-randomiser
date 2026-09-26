# Item lifetime endpoints: offline evidence

Research on 2026-09-26 identified two native destruction endpoints that could
help distinguish an original reward from a replacement allocated at its old
address. No lifetime hooks were installed or executed. These findings do not
remove the [controlled receipt observer's limitations](held-drop-receipts-20260926.md).

Only the previously copied BG2:EE executable was inspected: SHA256
`fc821a4806a0305b84fd85f1aad2bd472c8db642ed34b4494ae62351cae1c580`, preferred
image base `0x140000000`. Profiler labels were navigation hints; disassembly,
constructor vtable writes, and actual call instructions support the routes below.
They are not Lua-callable API claims.

## Endpoints and ordering

| Preferred address | Observed role |
| --- | --- |
| `0x1401FAA30` | Ordinary CItem destructor. RCX is the live item at entry; fields are then reset and internal resources cleaned. Does not free `this`. |
| `0x1401FAB10` | Virtual deleting destructor. Contains its own cleanup body rather than calling the ordinary destructor. EDX bit 0 controls final deallocation. |
| `0x140599010` | Item vtable installed by the inspected constructors; first entry is `0x1401FAB10`. |
| `0x140292445` | Deferred disposal drain calls the item's vtable slot 0 with EDX=1. A hook only on the ordinary destructor would miss this route. |

Both destructor entries precede payload reset, making primitive pointer/event
capture plausible. Their safe hook boundaries, register preservation, ordering,
and runtime coverage still need validation. Embedded CItems in item-delivery
messages are destroyed separately from the destination heap item and must not
invalidate the latter's watch.

`CInfGame::AddDisposableItem` (`0x14026D400`) only appends the pointer to the
disposal list at game offset `0x9470`. It is not destruction: transform code still
reads the old item's charges after enqueue. `RemoveDisposableItem` can remove an
entry without destroying it, and some routines append directly without calling
the enqueue helper. Queue membership therefore cannot establish item death.

## Demonstrated routes and limits

The inspected script DestroyItem, slot destruction, RemoveItem message,
ReplaceItem message, and TransformItem message paths clear or replace ownership
and enqueue their original item. Their eventual disposal follows the drain above.
Whole equipment and container cleanup instead directly call the same virtual
deleting destructor at `0x140343DC0` and `0x140190A54`, respectively.

This is useful allocation-lifetime evidence, not ownership continuity.
`CGameContainer::SetItem` overwrites a list entry without necessarily destroying
its previous item; `CItem::SetResRef` can change an existing item's resource
without either destructor. Movement, payload changes, temporary copies,
save/load, native extensions, and unsupported routes require separate treatment.
A linear instruction sweep is not a complete control-flow graph.

The next bounded native diagnostic should record both destructor entries in the
same loss-detecting trace as death-drop transfer events. It must distinguish the
expected destruction of an already-copied original from premature destruction,
test allocator address reuse and unrelated temporary copies, and preserve world
session boundaries. No live userdata may be retained after destruction. This
still would not persist unfinished transfer evidence across save/load.

Ignored reproducible evidence is under
`work/mode1-held-receipts-20260926/item-lifetime/`: `analysis.md`, input hashes and
vtable bytes, route disassembly, cross-reference candidates, analysis script,
and candidate instruction signatures. Signature lengths are validation windows,
not overwrite lengths; some end partway through an instruction.
