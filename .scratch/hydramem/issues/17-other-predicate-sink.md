# 17 — The `other` predicate is a sink that disables gate 2 and the chain

Status: ready-for-agent

## Parent

`.scratch/hydramem/PRD.md`

## Why this exists

Slice 06 measured `other` at 24.4% of facts and closed it as *"the controlled
vocabulary was confirmed adequate, not extended — the off-vocabulary tail has no
cluster worth a new predicate."* That reading was about the tail's *shape*. It
did not look at what `other` does to the rest of the system, and slice 08's live
probe found that out.

Measured on instance `89941a93` (knowledge-update, 2 sessions 231 days apart,
22 facts, gold answer `4`):

- **20 of 22 facts are `other`.** Among them `three bikes` (Feb 2023) and
  `four bikes` (Oct 2023) — which *are* the knowledge update the instance is
  built around.
- **Gate 2 abstains on the instance's own question.** "How many bikes do I
  currently own?" wants `owns`; the entity holds no `owns` fact, so the answer
  is `no_such_relation: person:user has no owns`. A false abstention on a
  question the graph demonstrably answers — with the window narrowing from
  slice 08 in place, the model returns "four bikes" correctly.
- **The chain never forms.** `other` is not in `FUNCTIONAL_PREDICATES`, so it
  chains per distinct value; `three bikes` and `four bikes` are distinct
  values, so both stay `current`. 22 facts, **0** `SUPERSEDES` edges, on a
  knowledge-update instance. The supersession machinery slice 05 built is
  invisible on exactly the category it exists for.

So `other` is not a neutral overflow bucket. It is a hole that both gate 2 and
the chain fall through, and its 24.4% share is a measure of how much of the
graph is structurally inert.

## What to build

Not "add more predicates" reflexively — measure first, then decide.

1. Re-run `scripts/measure_extraction.py` and cluster the `other` values by
   what a question would *ask* for, not by surface similarity. Slice 06
   clustered by the latter and found nothing; `three bikes` and `four bikes`
   only cluster once you ask "which predicate would a question reach for".
2. Decide between two fixes, or both:
   - extend `extract.PREDICATES` with the clusters that recur (`owns` already
     exists and would have taken the bikes — this may be a prompt problem, not
     a vocabulary problem), or
   - make gate 2 aware that `other` is a sink, so an entity whose facts are
     overwhelmingly `other` cannot produce a confident `no_such_relation`.
     Note this widens the gate and costs abstention precision; measure it.
3. Whatever changes, the chain consequence has to be re-measured: does a
   knowledge-update instance now produce `SUPERSEDES` edges?

## Cost

**Changing the extractor prompt or vocabulary needs a wiped node.** Fact ids are
content-derived, so a new prompt writes a second generation of facts beside the
first and every count silently inflates. `docker compose down`,
`rm -rf hydradb-data`, `docker compose up -d`, then re-ingest. That is why slice
08 did not take this on.

## Acceptance criteria

- [ ] The `other` tail is clustered by *asked-for predicate*, and the result is written down
- [ ] A knowledge-update instance produces at least one `SUPERSEDES` edge
- [ ] "How many bikes do I currently own?" on `89941a93` does not abstain `no_such_relation`
- [ ] Whatever the gate-2 decision is, its effect on abstention precision is measured, not assumed
- [ ] `docs/extraction-quality.md` is regenerated against the wiped-and-reingested node

## Pinned by

`tests/test_gates.py::test_other_is_a_sink_that_silently_disables_gate_2`

## Blocked by

06, 08
