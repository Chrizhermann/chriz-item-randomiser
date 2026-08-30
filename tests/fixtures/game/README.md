# Synthetic installer fixture

This directory defines a hermetic publication contract and a small WeiDU harness. The Python
builder creates a fresh KEY/BIFF/TLK game around it for each case. All resources and identities
used by the harness are synthetic; it contains no base-game item-to-location assignments.

The harness is intentionally narrower than a full Item Randomiser installation. A full public EET
component needs the EET resource graph, compiler-complete IDS inputs, campaign scripts/dialogues,
and the installation's existing TLK state; inventing those in a marker-only game would make a
passing result misleading.

The publication matrix therefore invokes the production backend selector, registry, endpoint,
manifest, runtime-asset publication, and classic ordinary-delivery seams with synthetic catalog
rows. Its allowlists still cover every resulting override file and byte-check KEY, BIFF, and both
TLKs. The existing legacy-delivery test covers the campaign-specific special transformations.

The Mode 2 runner invokes the production random-seed, deletion, shared-fix, and WeiDU-distribution
libraries with a fixed seed and synthetic arrays. Identical clones from `b600e94` and the candidate
are compared by active WeiDU log, the complete effective override hashes, CRE item semantics, TLK
entry delta for the selected synthetic language (zero because this seam adds no strings), and
absence of EEex/backend state for components 1300 and 1400. `missing_items.tpa`, `arrays.tpa`,
campaign-specific dialogue/script fixes, public-component language wiring, and the public EET
component preamble remain full-install acceptance work; they are not silently represented by this
fixture.
