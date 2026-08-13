# 15 — mem0-compatible API and two-line swap

Status: ready-for-agent

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
