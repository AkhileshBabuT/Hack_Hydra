# 04 — Idempotent re-ingest under regrouped batches

Status: done (2026-08-14)

## Parent

`.scratch/hydramem/PRD.md`

## What to build

Make a resumed ingest converge to the same graph regardless of how rows happen to be grouped
into batches on the second run.

Idempotency is enforced at two layers deliberately. Deterministic node and edge ids make each
merge a row-level no-op under any batching. The mutation idempotency key makes a replayed
request a no-op and, when a key is reused with genuinely different content, surfaces an
explicit non-retryable conflict from HydraDB.

That conflict must be surfaced as a loud error and never swallowed — it means two different
facts computed the same identity, which is a correctness bug, not a retry condition.

The test that matters deliberately regroups rows on the second pass, because that is what a
crash-and-resume actually does and it is the case a request-scoped key alone does not
cover.

## Acceptance criteria

- [x] Re-running a completed ingest changes no node or edge counts
- [x] Re-running with deliberately different batch groupings changes no node or edge counts
- [x] An idempotency-key conflict is raised as a loud non-retryable error rather than being swallowed
- [x] Interrupting an ingest partway and resuming converges to the same graph as an uninterrupted run

## Result

Three criteria hold as written, covered by `tests/test_ingest.py`: re-ingest
changes no counts, a second pass at `batch_size=1` changes no counts, and a
partial write followed by a full ingest converges.

The fourth does not hold as written, and the premise was wrong rather than the
code. HydraDB does **not** raise a conflict when a mutation idempotency key is
reused with different content on the Cypher path — verified live, and the source
confirms `IdempotencyConflict` belongs to storage-level imports and compaction,
not Bolt Cypher. So the guarantee is moved into the key: `batch_key` hashes the
rows it sends, so one key cannot name two payloads. The conflict mapping in
`client.py` stays as a net. See `docs/hydradb-notes.md`.

## Blocked by

03
