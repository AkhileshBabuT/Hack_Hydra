# 05 — Supersession chain — derived, materialized, permutation-invariant

Status: done (2026-08-14)

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

- [x] Chain derivation is a pure function tested without a database
- [x] A shuffled fact set yields a chain identical to the sorted one
- [x] Ingesting a question instance's sessions in reverse order produces an identical supersession chain to forward order
- [x] The materialization pass writes SUPERSEDES edges, status and validity-end in batched statements
- [x] Guarded merge is used where replay must not move a timestamped value backward
- [x] New statements are registered in the statement inventory and pass the verify target

## Result

`hydramem/chain.py` derives the chain from the fact set and returns the rows that
materialize it; `build_rows` calls it, so the write path gained two batched
statements (`CLOSE_FACT`, `LINK_SUPERSEDES`) and no new pass. Permutation
invariance, misdated revisions and the restatement case are tested without a
database; reverse-order ingest and re-ingest are tested against a live node.

Two things the plan did not anticipate:

**Supersession cannot be written by rewriting the fact.** HydraDB applies a
guarded patch strictly when stored < incoming, so a replay carrying the same
`asserted_at` writes nothing. Materialization is a separate upsert guarded on
`valid_to`, which moves 0 -> timestamp. The strictness then works in our favour:
a re-ingest cannot reset `status` to `current`, which is tested.

**Only functional predicates may chain.** Chaining by (entity, predicate) alone
marked 193 of 220 facts superseded on a real instance — every `likes` across 40
sessions collapsed into one line, which would have left the multi-session
category answerable only with the most recent mention. Non-functional predicates
now chain per distinct value, so restatements collapse and different values
coexist: 20 of 220 on the same instance.

## Blocked by

03
