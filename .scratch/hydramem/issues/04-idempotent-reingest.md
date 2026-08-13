# 04 — Idempotent re-ingest under regrouped batches

Status: ready-for-agent

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

- [ ] Re-running a completed ingest changes no node or edge counts
- [ ] Re-running with deliberately different batch groupings changes no node or edge counts
- [ ] An idempotency-key conflict is raised as a loud non-retryable error rather than being swallowed
- [ ] Interrupting an ingest partway and resuming converges to the same graph as an uninterrupted run

## Blocked by

03
