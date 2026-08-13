# 05 — Supersession chain — derived, materialized, permutation-invariant

Status: ready-for-agent

## Parent

`.scratch/hydramem/PRD.md`

## What to build

Facts are never mutated. A revision is a new Fact plus a SUPERSEDES edge, which is what makes
knowledge-update and temporal-reasoning questions answerable by traversal rather than by
prompting.

The chain is computed as a **pure function of the fact set**: group by entity and predicate,
sort by asserted-at with the identifier as tiebreak, pair adjacent facts. Permutation
invariance is then a property of a function that can be unit-tested with no database, rather
than an emergent property of write ordering that can only be tested against a live node.

A batched post-ingest pass materializes that derivation into SUPERSEDES edges, a status
property and a validity end, so the chain exists as graph structure for traversal, for the
history method, and for the demo.

Where a replayed session must not move a timestamped value backward, use HydraDB's guarded
merge. It is reachable from Cypher through marker properties inside the vertex upsert, though
it is currently undocumented — note this for the upstream issue in slice 16.

## Acceptance criteria

- [ ] Chain derivation is a pure function tested without a database
- [ ] A shuffled fact set yields a chain identical to the sorted one
- [ ] Ingesting a question instance's sessions in reverse order produces an identical supersession chain to forward order
- [ ] The materialization pass writes SUPERSEDES edges, status and validity-end in batched statements
- [ ] Guarded merge is used where replay must not move a timestamped value backward
- [ ] New statements are registered in the statement inventory and pass the verify target

## Blocked by

03
