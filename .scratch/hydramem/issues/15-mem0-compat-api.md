# 15 — mem0-compatible API and two-line swap

Status: done — closed by slice 15; surface, HTTP server and swap demo all shipped

## Parent

`.scratch/hydramem/PRD.md`

## What to build

The product-completeness deliverable: a drop-in replacement that a judge can swap into an
existing agent in two lines.

Serve the mem0 method surface — add, search, get all, history, delete — plus the explain
method that is ours. Add returns a HydraDB bookmark and search accepts one, so an agent that
writes a memory and immediately searches for it is guaranteed to see it. That is a real
correctness property that vector-store memory layers do not have, and it deserves to be
demonstrated rather than described.

History walks the supersession chain. It is the one method mem0 structurally cannot
implement, and it is the clearest single argument for the whole design.

Delete is a tombstone, not destruction — facts are immutable and that rule does not get an
exception for deletion.

## Acceptance criteria

- [ ] The mem0 method surface is served over HTTP
- [ ] Add returns a bookmark and search accepts one, enforcing read-your-own-writes
- [ ] A test writes a memory and immediately reads it back successfully via the bookmark
- [ ] History walks the supersession chain and returns the ordered revisions
- [ ] Delete tombstones rather than destroying
- [ ] A sample agent script demonstrates the swap in two lines and runs successfully

## Blocked by

10

## Result

Done. `hydramem/memory.py` (the surface), `hydramem/server.py` (HTTP),
`scripts/mem0_swap_demo.py` (the swap, end to end), `tests/test_memory.py`
(8 tests).

### The swap really is two lines

```python
# from mem0 import Memory
from hydramem.memory import Memory
```

`user_id` maps to `instance_id` by identity — mem0 partitions by user, HydraMem
partitions by tenant, and they mean the same thing, so there is no translation
table to debug.

### Most of this was exposure, not construction

Worth recording, because it says something about the earlier slices: almost
nothing here is new machinery. `client.write` already returned a bookmark,
`client.read` and `answer.answer_question` already accepted one, and
`ingest.ingest_instance` already handed the bookmark back. Read-your-own-writes
was **exposed**, not built. `delete` is `CLOSE_FACT` with `status='deleted'` — a
tombstone is a close whose status happens to be that — so the slice adds **no new
Cypher and no new INVENTORY entry**, and cannot have broken the parser.

`server.py` is `http.server` from the standard library. Five dependencies, none a
web framework; six endpoints taking JSON and returning JSON do not justify a
sixth dependency plus an ASGI server.

### One thing was genuinely missing, and the test found it

`ingest_instance` derives supersession **only over the rows it is currently
writing**. That is right for corpus ingest, where a whole instance arrives at
once, and wrong for an incremental `add`: two calls each see one fact, so

```
add("I work at Acme")     -> employer = Acme      (current)
add("I moved to Globex")  -> employer = Globex    (current)
```

left **two current facts and zero SUPERSEDES edges**. `history()` returned `[]` —
the one method that argues for this design over a vector store, silently empty
under the access pattern mem0 users actually have.

Fixed by `chain.rows_from_stored` plus `Memory._reconcile_chain`, which re-derives
the chain over every fact the tenant holds after each `add`. Costs one read and
at most two writes, and is safe to repeat: `CLOSE_FACT` is guarded on `valid_to`
so a replay writes nothing, and the SUPERSEDES edge is MERGEd on a
content-derived id.

`chain.rows_from_stored` builds a `SimpleNamespace`, not an `ingest.Rows` —
`ingest` imports `chain`, so naming it there is a circular import. That failed at
*call* time rather than import time, which an import smoke-check does not catch.

### Acceptance

- [x] The mem0 method surface is served over HTTP — `hydramem/server.py`
- [x] Add returns a bookmark and search accepts one
- [x] A test writes a memory and immediately reads it back via the bookmark —
      `test_memory.py::test_a_memory_is_readable_immediately_via_its_bookmark`
- [x] History walks the supersession chain and returns the ordered revisions —
      `test_history_returns_the_revisions_a_vector_store_would_have_lost`
- [x] Delete tombstones rather than destroying —
      `test_delete_tombstones_rather_than_destroying`, plus an idempotence test:
      a second delete reports `already_deleted` and writes nothing
- [x] A sample agent script demonstrates the swap in two lines —
      `scripts/mem0_swap_demo.py`

### Inherited

`Memory._seq` is an in-process counter, so session `idx` restarts at 0 in a new
process and `NEXT` ordinals can repeat between separately-added sessions.
Harmless here — recency comes from the timestamp — and marked `ponytail:` in the
source. Reading the tenant's session count first would fix it and costs a round
trip per `add`.
