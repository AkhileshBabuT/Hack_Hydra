# 25 — Honest-reporting correction: the arm is not more trustworthy when it answers

Status: done — closed 2026-08-17; the claim was never in a doc, the number that refutes it now is


## Parent

`.scratch/hydramem/PRD-appeal.md`

## Why this exists

There is an appealing framing for an abstention-first system that loses on
accuracy: *it answers less often, but it is more reliable when it does.* It was
proposed in this project on 2026-08-16 and the generated data refutes it.

`answerable_accuracy` — accuracy computed only over questions that have a gold
answer — is already a column in `docs/eval/oracle-per-category.csv`:

| arm | answerable_accuracy |
|---|---|
| full_context | 0.5750 |
| vector_rag | 0.5833 |
| hydramem | **0.4000** |

HydraMem is the least accurate arm **both** overall and on the subset it chose to
answer. The framing is not merely unproven; it is contradicted by a number that
has been sitting in a committed CSV.

This issue exists because that column is generated but never *read*. CLAUDE.md's
measured-result table reports accuracy, abstention precision and recall, and not
this. A figure nobody surfaces is a figure nobody can be corrected by, and the
next person to write the submission copy will reach for the same comfortable
sentence.

Two more findings from the same session are unwritten and belong here:

- **Gate 4 fires zero times** across the whole slice while still costing a round
  trip on multi-entity questions. CLAUDE.md calls it "close to vacuous"; the
  measured number makes it exact.
- **Gate 5 costs zero false abstentions.** No question in the slice was lost to
  `fabricated_citation` or `uncited_answer`. The strictest component in the
  cascade — the one a reader is most likely to call over-engineering — throws
  away nothing. That is a result in the submission's favour and it is not
  currently claimed.

## What to build

Reporting changes only. No behaviour changes, no model calls, no round trips.

**Surface `answerable_accuracy` wherever accuracy is quoted.** It is generated
already; it needs to appear in the measured-result table and the generated
write-up beside the headline accuracy, not below the fold.

**Write the refuted framing down as refuted.** A line stating plainly that
HydraMem is less accurate than both baselines on answerable questions, so the
"more trustworthy when it answers" claim is unsupported. The convention here is
that findings which cost a cycle to rediscover get recorded — this one costs a
credibility hit instead, which is worse.

**Record the two gate findings** in the gate 4 and gate 5 sections.

**Check the same claim is not already made elsewhere.** README, the generated
results write-up, `docs/architecture.md` and `docs/capability-map.md` all describe
the abstention thesis; any sentence implying reliability-when-answering needs
correcting, not just the absence of a new one.

## Acceptance criteria

- [ ] `answerable_accuracy` appears in CLAUDE.md's measured-result table and in
      the generated results write-up, for all three arms
- [ ] The "more trustworthy when it answers" framing is stated as refuted, with
      the three numbers, in CLAUDE.md
- [ ] `no_path = 0` recorded in the gate 4 section
- [ ] `fabricated_citation = 0` and `uncited_answer = 0` recorded in the gate 5
      section, as a result in the system's favour
- [ ] README, `docs/architecture.md`, `docs/capability-map.md` and the generated
      results document audited for any sentence implying the refuted claim, and
      corrected where found
- [ ] No number introduced that is not already generated into `docs/eval/`

## Blocked by

- `21-per-reason-abstention-counts.md` — the gate 4 and gate 5 zero-counts are
  quotable only once they are generated rather than derived in a chat session.
  The `answerable_accuracy` figures are already generated and need no blocker.

## Result

**The audit came back clean, which was not the expected outcome.** No document
made the refuted claim — `README.md`, `docs/architecture.md`,
`docs/capability-map.md` and `docs/eval/oracle-results.md` were all searched for
reliability-when-answering phrasing and none of them carries it. There was
nothing to retract.

What was actually wrong is narrower and more durable: `answerable_accuracy` was
**generated all along and never read**. It sat in
`docs/eval/oracle-per-category.csv` and in the generated write-up's table, and
was absent from CLAUDE.md's measured-result table — the one a person actually
reads before writing submission copy. A number nobody surfaces cannot correct
anybody.

### Done

- [x] `answerable_accuracy` added to CLAUDE.md's measured-result table for all
      three arms: full_context 0.5750, vector_rag 0.5833, hydramem **0.4000**
- [x] The framing written down as refuted, in the table's own paragraph rather
      than a footnote, with the instruction not to imply it either
- [x] `no_path = 0` in the gate 4 section — done in the slice-21 tick
- [x] `fabricated_citation = 0` and `uncited_answer = 0` in the gate 5 section,
      stated as a result in the system's favour — done in the slice-21 tick
- [x] README, architecture, capability-map and the generated results document
      audited; no correction needed
- [x] No number introduced that is not in `docs/eval/`. Verified by reading both
      CSVs back and checking the arithmetic: 27+15+16+2 = 60 false of
      41+25+18+2 = 86 abstentions

### Beyond the issue

The abstention breakdown itself was added to CLAUDE.md at the same time, for the
same reason — generated, and nowhere a reader would find it. It carries the one
sentence this build most needs stated plainly: **86 of 150 abstentions, 60 of
them wrong.** The accuracy gap is not a spread of small losses, it is one
failure repeated, and the table separates count from rate so the two different
problems in it stay visible — `not_in_graph` is the largest count,
`no_such_relation` is wrong 16 times in 18.

### Inherited

Nothing. The reporting is honest as of this tick; it stops being honest the next
time a run changes a number, so `make eval` must regenerate before any of these
figures is quoted again.
