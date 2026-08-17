# 12 — Thesis gate — stratified slice, eval harness, full-context baseline

Status: done — GO on 2 of 3, abstention precision lost

## Parent

`.scratch/hydramem/PRD.md`

## What to build

**This is the highest-stakes slice in the plan and it runs early on purpose.** If the central
claim is wrong, this must surface with days left to react, not hours.

Build a category-stratified slice of roughly one hundred and fifty questions. Stratified, not
random: the abstention, temporal-reasoning and knowledge-update buckets carry the entire
thesis and each needs enough cases to say anything at all. Record the count per category in
every table produced.

Run the strongest full-context baseline the free tier permits, with pre-flight token counting
and the overflow tail routed to the fallback long-context provider. The most likely attack on
this submission is that it only beat a weak baseline, so use the strongest one available. Use
the **same answering model** for every arm — only the retrieval layer may differ.

Evaluation pins strong consistency so scores are reproducible.

Marked for human implementation because it ends in a go or no-go judgement. If HydraMem does
not beat the baseline on abstention and knowledge-update, everything stops: no UI, no README,
no new features. Diagnose in this order — predicate gate too permissive, then entity gate
missing aliases, then citation check failing to downgrade. Those three account for nearly all
abstention-precision failures.

## Acceptance criteria

- [ ] A category-stratified slice of roughly one hundred and fifty questions exists with counts recorded per category
- [ ] Both arms run the slice unattended end to end
- [ ] The same answering model is used across arms and this is stated in the results
- [ ] Questions exceeding the measured context ceiling are counted and routed to the fallback provider, and the split is recorded
- [ ] A per-category results table is committed as a CSV
- [ ] Abstention precision and recall are reported per category alongside accuracy
- [ ] Evaluation runs pin strong consistency
- [ ] A go or no-go decision against the thesis is recorded, with diagnosis if it failed

## Blocked by

11

## Result

Run on the **oracle split**, 150 questions, three arms, 450 scored questions.
Regenerate with `make eval`. Tables: `docs/eval/oracle-per-category.csv`,
`docs/eval/oracle-cost.csv`, `docs/eval/oracle-results.md`.

### The decision: GO, on two of three checks, and the third is named

| check | HydraMem | full-context | |
|---|---|---|---|
| abstention **recall** | **0.9333** | 0.8000 | won |
| knowledge-update accuracy | **0.6154** | 0.5769 | won |
| abstention **precision** | 0.2800 | 0.4211 | **lost** |

`verdict()` calls GO at two of three. That is the honest reading and it is not a
clean win: **HydraMem catches more unanswerable questions than any other arm and
is wrong more often when it says so.** Overall accuracy is 0.4333 against 0.6200
(full-context) and 0.6467 (vector RAG).

The thesis — *structural abstention beats reading everything* — is **not proven
on accuracy**. It is proven on recall and on cost, and it is contradicted on
precision. Nothing in this repository should claim otherwise.

### Every arm, n=150

| arm | acc | abs precision | abs recall | tokens/q | round trips/q | median | p95 |
|---|---|---|---|---|---|---|---|
| full_context | 0.6200 | 0.4211 | 0.8000 | 5,540 | 0 | 26,445 ms | 65,046 ms |
| vector_rag | 0.6467 | 0.4154 | 0.9000 | 2,494 | 0 | 23,718 ms | 97,734 ms |
| hydramem | 0.4333 | 0.2800 | **0.9333** | **605** | 2.96 | **1,858 ms** | **2,358 ms** |

Same answering model on every arm (`nemotron-3-ultra-550b-a55b`), recorded per
row rather than asserted. Judge is `nemotron-3-super-120b-a12b`, so no arm grades
its own output. Evaluation pinned **strong** consistency throughout.

**Cost is where the thesis holds without qualification.** 9.2x fewer tokens than
full-context, 4.1x fewer than vector RAG; 14x lower median latency and **28x
lower p95** than full-context. The round-trip claim is measured, not argued: 10
questions cost 2, 136 cost 3, 4 cost 4. **Never five.**

### Per category

| category | n | full_context | vector_rag | hydramem |
|---|---|---|---|---|
| knowledge-update | 26 | 0.577 | 0.654 | 0.615 |
| multi-session | 32 | 0.500 | 0.469 | 0.469 |
| single-session-assistant | 20 | 1.000 | 0.900 | **0.000** |
| single-session-preference | 20 | 0.450 | 0.500 | 0.450 |
| single-session-user | 26 | 0.885 | 0.962 | 0.577 |
| temporal-reasoning | 26 | 0.385 | 0.462 | 0.385 |

**`single-session-assistant` is 0 of 20** while both baselines are near-perfect.
That single category is 13% of the slice and accounts for most of the accuracy
gap. It is not a retrieval failure — it is policy. The extraction prompt forbids
filing assistant content as a user fact, so on a session that is "user asks a
generic question, assistant explains" there are no facts about the user and the
graph stores **nothing**. Two instances ingested zero facts with zero parse
failures.

The fix is to *attribute* assistant content rather than discard it — issue 19's
unbuilt half — and it needs a node wipe. Not done here.

### Diagnosis, in the order this issue prescribes

The issue names three suspects. Measured against the run, the order was wrong for
this codebase and the real one is recorded here:

1. **Entity gate, and it was the largest.** Not "missing aliases" — gate 1
   required every name to exist as an `Entity` node, and ingest only creates
   nodes for subjects and entity-valued objects. Pre-fix: **61% false abstention
   (38 of 62), 27 of them gate 1**, accuracy 0.339. Closed as issue 18; see that
   issue for the full measurement.
2. **Predicate gate too permissive — inverted.** It is too *strict*. Post-fix it
   is the largest remaining source of false abstention (22 of 52), and half of
   those fire on entities that hold `other` facts. Gate 2 refuses while sitting
   on content it could not label. Issue 17.
3. **Citation check failing to downgrade — did not occur.** No
   `fabricated_citation` and no `uncited_answer` in 150 questions. Gate 5 is not
   the problem.

### Overflow tail

`llm.measured_ceiling()` is unset because slice 02 found no wall at 1,000,007
tokens. The routing branch exists and ran on every question; **0 of 150
overflowed**, largest prompt ~8k tokens on the oracle split. Recorded as measured
rather than assumed. The `_s` split is where this branch would earn its keep.

### Two defects the run surfaced, both fixed

- **Duplicate facts hard-failed an ingest.** Two extractions with the same
  (session, turn, predicate, subject, value) but different evidence spans hash to
  one vertex id with two `snippet`s; HydraDB rejects the whole batch as
  `conflicting metadata values`. Deduplicated by `vid` in `build_rows`, before
  `chain.materialize` so the chain derives from what is written. Pinned in
  `test_ingest.py`.
- **A collapsed decode killed a 150-question arm.** Slice 06's greedy-decode
  failure, reaching the evaluation arms this time as a truncated run of Arabic
  text where an English answer belonged. The arms called `complete_json` directly
  and inherited none of `extract`'s retry. `eval.ask` now retries once at
  `RETRY_TEMPERATURE`; the disk cache makes an ordinary retry useless, so
  temperature is the only lever.

Also: `llm.complete` retries raised 5 -> 8. Five attempts give up after ~15s and
NIM's transient 404s recover on the order of minutes — measured, an arm died on
one and the model answered a probe seconds later.

## Acceptance criteria

- [x] A category-stratified slice of roughly one hundred and fifty questions exists with counts recorded per category
- [x] Both arms run the slice unattended end to end
- [x] The same answering model is used across arms and this is stated in the results
- [x] Questions exceeding the measured context ceiling are counted and routed to the fallback provider, and the split is recorded (0 of 150; no ceiling was reached)
- [x] A per-category results table is committed as a CSV
- [x] Abstention precision and recall are reported per category alongside accuracy
- [x] Evaluation runs pin strong consistency
- [x] A go or no-go decision against the thesis is recorded, with diagnosis if it failed
