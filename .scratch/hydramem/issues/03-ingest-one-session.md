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

## Model configuration

Extraction uses `nvidia/nemotron-3.5-lightning-30b-a3b` with **reasoning OFF**, set
through the chat template rather than a prompt instruction. Reasoning models emit
thinking tokens that break strict JSON parsing; one published study saw 80% of
outputs fail to parse for this reason. This is the single highest-value setting in
the pipeline and it is not optional.

Model access goes through `hydramem/llm.py` — do not construct a provider client
anywhere else, or the disk cache stops guaranteeing free reruns.

Extraction windows at the **session** level. Only fall back to sliding 3-turn
windows if a session exceeds the ceiling measured in slice 02, or if slice 06 shows
session-level quality is materially worse.

## Blocked by

01, 02
