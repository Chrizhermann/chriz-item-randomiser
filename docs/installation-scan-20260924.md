# Read-only installation scan, 2026-09-24

The item and complete physical world/source scans now succeed. The item catalogue
contains 3,544 items; the world catalogue contains 41,577 physical source records
and 24,305 target candidates. Inconsistent inventories and unresolved actors are
retained as diagnostics and excluded from binding. No installed resource or save
was repaired or changed, and no resource was silently skipped. These are discovery
results, not full-game randomizer or runtime-lootability acceptance.

## Scope and evidence

Only `%USERPROFILE%\Games\EET-IR-Mode1-20260922\game` was scanned. The two
specified BG tasks and other local tasks were idle, with no game or installer
process running before game access. No desktop control, game launch, remote
console, installer, or save access was used.

Generated local evidence is in `work/mode1-scan-20260924/`, outside the game:

- `scan-status.json`, `items-en-us.stdout.json`, and `world.stderr.json`: exit
  statuses and CLI output. Item scan exit 0; world scan exit 1.
- `catalog.json`: 3,544 effective items, fingerprint
  `313b991374b839d647f7808e23e667d23b127db59cafbd8cc1d82926ccad3219`.
- `bdabis1d-evidence.json`: raw record bytes, offsets, payloads, slot references,
  parser results, and all seven comparison checks passing.
- `world-failure-evidence.json`: exact failing resource identity, hash, item
  count, all 40 raw slot words, and both dangling references.

The first item-scan attempt required explicit language selection (multiple TLK
files). The successful retry used `en_US`, matching the installed language
configuration. Its warning about missing name-string entries remains: affected
items display an available name or resource identifier. No language data was
changed.

## Empty record confirmed

Effective override resource `BDABIS1D.CRE`, 1,932 bytes:

`ec3d723327653db33a9895991c0f780286fcf69a7bf9dfba6f0f331a4da4d45b`

The item table begins at byte 1,812 and contains two 20-byte records; the slot
table begins at 1,852.

| Physical record | Resref | Slot | Expiration | Charges | Flags |
| --- | --- | --- | --- | --- | --- |
| 0, byte 1812 | empty, eight NUL bytes | 0 | 0 | 0 / 0 / 0 | 1 |
| 1, byte 1832 | `S1-4` | 9 | 0 | 0 / 0 / 0 | 0 |

The parser retained both indexes and payloads and did not mutate the input bytes.
The empty-record classification/binding guards also have synthetic regressions;
this inspection establishes the real physical shape, not runtime lootability.

## Initial structural blocker

Effective override resource `BDCORPSE.CRE`, 1,076 bytes:

`35f510ea7a14a23a648d6f94a464e622c5c6dd41b4d26e01ff132f05b69ab721`

Both the item-table offset and slot-table offset are 996, but the declared item
count is **zero**. Slots 21 and 22 contain indexes 0 and 1 at byte offsets 1,038
and 1,040. There are no physical item rows to which either index can refer. The
remaining 36 inventory-reference words contain `0xFFFF`; the selected weapon
and ability words both contain zero.

The [IESDP CRE V1 format](https://gibberlings3.github.io/iesdp/file_formats/ie_formats/cre_v1.htm)
defines inventory slot words as either `0xFFFF` or an index into the item table.
The header and raw words therefore explain the existing parser rejection. The
shared offsets are not themselves an overlapping nonempty table: the item table
has no bytes. There is no evidence here identifying which mod or original asset
produced these references, nor evidence of how the game handles this creature.

The scanner stops at the first structural failure. The diagnostic rerun preserved
that behavior and recorded the failing resource without scanning past it or
creating a usable partial catalogue. It did not prove that later resources parse.

Parser failures now include the CRE/ARE/STO resource identity, and embedded CRE
failures also include the ARE actor record index. Dangling-slot errors report the
referenced index and declared item count. Strict rejection remained unchanged in
that first diagnostic-only change.
Four synthetic integration regressions cover these diagnostics and prove that a
failed CLI scan neither creates a partial output nor overwrites an existing one.
The full offline suite passed **531 tests** in 8.050 seconds (exit 0); output is
saved in `offline-tests.stderr.txt` and `offline-tests-status.json` in the local
evidence directory. One initial suite run caught an old error-message assertion;
retaining the original `absent item record` wording resolved that compatibility
failure without weakening the assertion or parser validation.

The initial follow-up stopped at this diagnosed failure. A subsequent authorized
change implements the [diagnostic representation](source-binding-drafts.md):
retain all 40 raw words and original header offsets, flag the physical owner and
its external actor references, and exclude affected sources/targets from binding
and compilation. Normal catalogues retain their v1 shape; diagnostic catalogues
use v2. Nothing repairs the installed CRE or invents missing item rows.

Synthetic checks cover empty and mixed inventories, embedded and external actors,
metadata/concern tampering, rejection of affected sources/targets, and continued
binding of unrelated valid records.

## Follow-up diagnostic scan

After the script-engine task became idle, the read-only retry in
`work/mode1-dangling-scan-20260924/` indexed the same 3,544 items (exit 0, identical
item fingerprint). The world pass got beyond `BDCORPSE.CRE`, then stopped at
`cre:ohdfaer: Invalid world resource reference.` (exit 1, total 22.7 seconds).
No world catalogue was published. `scan-status.json`, the separate item/world
CLI logs, and `ohdfaer-evidence.json` retain this intermediate result.

The effective archived `OHDFaer.CRE` is 1,320 bytes, hash
`62d8f68ac85579c5ae0b8fb4ea7c4786d8a2a60015e4a4b4a0d827378c222795`.
Its declared five-row item table starts at 1,140; its slot table starts at 1,240.
Row 1 at byte 1,160 contains raw name bytes `0100000000000000`, a control byte
rather than a valid item reference. All five complete 20-byte rows are recorded
in the evidence. This establishes malformed declared data, not its provenance or
the engine's behavior.

CRE discovery now retains malformed names as exact raw bytes and quarantines
their entire owner, including external actor references. It does not silently
turn them into empty items, normalize the bad bytes, or drop the record. Strict
parsing remains the default for other callers. The next installation retry was
deferred while `Begin script engine Task 1` became active again. General runtime
suppression, encounter delivery, shops, and reload acceptance remain separate gates.

That complete offline suite passed **547 tests** in 8.311 seconds (exit 0),
including nine dangling-slot and seven malformed-CRE-item regressions. Its logs
are `offline-tests-final.*` in `work/mode1-dangling-scan-20260924/`. A bounded wait
confirmed that the script-engine task remained active, so the scan resumed later
once the BG tasks and game/installer processes were idle.

## Completed physical catalogue

The final evidence directory is `work/mode1-scan-20260924-0816/`. The first pass
there verified both CRE changes and reached `BG2200.ARE`, whose actor records 30
and 31 have flags 0, embedded offset 8,988, and size 0. Their complete actor-field
evidence is in `bg2200-actor-evidence.json`. The area is 13,768 bytes, hash
`2bde2359c3e3a81d86d1c129c8f2b6d685296e50642f03432a856767d0a96cd2`.

[GemRB's ARE importer](https://github.com/gemrb/gemrb/blob/master/gemrb/plugins/AREImporter/AREImporter.cpp)
chooses embedded storage using offset and flag, then uses the declared size.
This does not justify silently interpreting a zero-size embedded entry as an
external creature. It also does not establish the proprietary engine's behavior.
Discovery now preserves such actors with `unresolved-creature-storage`, without
guessing a blob boundary, assigning an external template, or inventing inventory.
The normal parser remains strict; conflicting flags and nonempty overlapping or
truncated data still fail.

The guarded retry succeeded with exit **0** in **39.627 seconds**. It published
`world.json` with schema `flir-world-catalog-v2` and fingerprint:

`7f160c90e5e30fb96487884575cdcad0e84b338691191c935e7a7778a0812915`

| Observation | Count |
| --- | ---: |
| Effective items | 3,544 |
| Physical source records | 41,577 |
| Target candidates | 24,305 |
| Empty physical item records | 16 |
| Creature owners with dangling slots | 14 |
| Dangling slot references | 18 |
| Creature owners with malformed item names | 2 |
| Malformed item-name records | 2 |
| Actors with unresolved creature storage | 3 |

Diagnostic categories may overlap; external actor references are not counted as
additional physical inventories. Candidate counts do not imply that every target
is eligible or available at runtime. The item scan retained the earlier fingerprint
and missing-name-string warning. Successful world completion includes the resource
reader's final installation-snapshot verification and full world validation.

`world-retry1-status.json` and `world-retry1.stdout.json` retain the successful
exit and CLI result. `real-diagnostic-evidence.json` independently compares raw
headers, all 40 slot words, and every item row of `BDABIS1D`, `BDCORPSE`, and
`OHDFaer` against the parser; all checks passed. The real row indexes, payloads,
and invalid reference bytes were preserved.

`final-verification.json` independently revalidates the saved catalogue and
compares those three CRE inventories and both unresolved area actors with their
captured raw evidence. No mismatches were found. The complete resource counts are
1,016 ARE, 6,483 BCS, 8,209 CRE, 4,089 DLG, 3,544 ITM, and 310 STO: **23,651**
resources across all six supported kinds.

The complete offline suite now passes **555 tests** in **9.014 seconds** (exit 0),
including eight new unresolved-actor regressions. Logs are `offline-tests.*` in
the final evidence directory. Runtime source suppression, dependable encounter
drops, general shops, and campaign reload coverage remain separate work.
