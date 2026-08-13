# 06 — Extractor quality measurement on a hand-checked slice

Status: ready-for-human

## Parent

`.scratch/hydramem/PRD.md`

## What to build

Measure the extractor before committing the full corpus to it.

The chosen extraction model is days old with no independent benchmarks on structured
extraction specifically, and a reasoning model that emits thinking tokens breaks strict JSON
parsing badly enough to have destroyed published pipelines. Reasoning-off is the single
highest-value setting; this slice confirms that empirically rather than assuming it.

Hand-check a twenty-instance slice for schema validity and fact precision. This is human work
because fact precision requires reading transcripts and judging whether an extracted claim is
actually supported.

If quality is inadequate, escalate in this order: tighten the prompt and confirm reasoning is
genuinely off, then fall back to short sliding turn windows, then to a larger model. Record
which rung was needed.

Blocking: do not scale ingest to the full corpus until this passes.

## Acceptance criteria

- [ ] Schema-validity rate across a twenty-instance slice is recorded as a number
- [ ] Fact precision is hand-checked and recorded
- [ ] The controlled predicate vocabulary is confirmed adequate, or extended and re-measured
- [ ] If quality is inadequate, the escalation rung taken is recorded
- [ ] A go or no-go decision on scaling to the full corpus is recorded

## Blocked by

03
