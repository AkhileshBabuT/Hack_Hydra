# 03 — Ingest one session end-to-end and answer from the graph

Status: ready-for-agent

## Parent

`.scratch/hydramem/PRD.md`

## What to build

Take one full benchmark question instance from raw sessions to an answered question.

Extraction windows at the **session** level, not the turn level — one call per session
against a strict schema with reasoning off. Session-level windowing cuts call volume by
four to five times versus short turn windows, and coreference resolution is better with the
whole session in context.

Entity resolution stays deliberately cheap: normalize, build a typed key, exact-match, and
where a surface form is merely similar create an alias edge rather than merging. Alias edges
are reversible and auditable; merges are not.

Writes are batched, roughly five hundred rows per statement, one statement per node label
and one per edge type — HydraDB permits only one relationship pattern per batch and a vertex
upsert must be a merge on id followed by a set. A session ingest is therefore around eight
statements, not one.

The slice is complete when a hand-picked question about a single session is answered
correctly from the graph.

## Acceptance criteria

- [ ] A loader reads the benchmark corpus and yields whole sessions
- [ ] Extraction runs one call per session against a strict schema with reasoning off, and every result is cached
- [ ] Entity resolution normalizes, keys by type, exact-matches, and emits alias edges for near matches without merging
- [ ] Writes are batched and carry a mutation idempotency key within the documented length and character limits
- [ ] One full question instance ingests with zero errors and node and edge counts are sane
- [ ] A hand-picked single-session question is answered correctly from the graph
- [ ] All new statements are registered in the statement inventory and pass the verify target

## Blocked by

01, 02
