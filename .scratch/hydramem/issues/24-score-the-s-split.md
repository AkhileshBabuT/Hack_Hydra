# 24 — Score the `_s` split

Status: blocked — measured 2026-08-17; 18+ hours of extraction for the smallest useful slice


## Parent

`.scratch/hydramem/PRD-appeal.md`

## Why this exists

Every published LongMemEval figure anyone will compare this against was measured
on `_s`. The harness has only ever scored `oracle`.

That does not make the oracle numbers wrong — it makes them *illegible to an
outside reader*. Oracle holds evidence sessions only, so retrieval has almost
nothing to discriminate against; `_s` gives each question 30–50 haystack sessions
and is where a retrieval layer either earns its keep or does not. A memory system
that only reports oracle has reported the easy half.

It is also where this build's cost claim is most likely to hold up and most
likely to be doubted. Full-context on `_s` reads the whole haystack; HydraMem
reads a graph. The token ratio should widen sharply — or it should not, and that
would be worth knowing before a judge finds it.

## What to build

The existing three arms, over the existing stratified slice, on `_s` instead of
`oracle`. No new arms, no new judge, no new metrics.

**This is a long run and the harness already supports it.** Runs are resumable —
each scored question is appended as it lands — and arms are independent
processes, so they may run in parallel. Two processes must never share an arm.

**Expect it to be slow and plan for that rather than fighting it.** One instance
on `_s` carries 30–50 sessions against oracle's 1–3, so extraction is tens of
uncached calls per instance on a cold cache. A single question that finished in
280s on oracle ran past a ten-minute timeout on `_s`. Warm the extraction cache
deliberately; do not discover this by timeout.

**Report it as a separate table, never merged with oracle.** They are different
difficulties over the same questions. Averaging them, or quoting one where the
other was measured, produces a number that describes neither.

**Overflow routing must be counted, not assumed.** On oracle, 0 of 150 questions
overflowed the context window. `_s` is where that branch might finally execute,
and if it does the count belongs in the table.

## Acceptance criteria

- [ ] All three arms scored over the stratified `_s` slice, same judge, same
      answering model, differing only in retrieval layer
- [ ] Results land in `docs/eval/` as their own tables, clearly labelled `_s`,
      never merged or averaged with oracle
- [ ] Per-category accuracy reported, with `n` on every row
- [ ] Per-reason abstention breakdown from issue 21 present for `_s` too
- [ ] Tokens per question and round trips per question reported — the cost claim
      restated on the split where it matters
- [ ] Context-overflow count reported explicitly, including if it is zero
- [ ] The run is resumable and was resumed at least once without double-scoring a
      question
- [ ] CLAUDE.md updated to say which split each headline number came from

## Blocked by

- **A live HydraDB node**, plus a warmed extraction cache. Independent of the
  appeal work — 21, 22 and 23 do not block this and it does not block them.

## Result — not attempted, with the measurement that stops it

Probed before committing a cycle, which is the whole point of probing.

| | |
|---|---|
| `_s` slice at `--per-type 2` (the smallest useful cap) | 42 questions |
| sessions in that slice | **2,034** |
| sessions per instance | min 43, median **49**, max 53 |
| **one instance, 52 sessions, cold cache** | **1,581 s — 26 minutes** |

### Why it is not a scoping problem

42 instances at 26 minutes is **over 18 hours of extraction alone**, before
ingest, before scoring, and before `full_context` and `vector_rag` — which must
also run on `_s` or the comparison means nothing. Shrinking `--per-type`
further does not help: the cost is per *session*, and `_s` instances carry 25x
more sessions than oracle ones. There is no bounded slice that is both cheap
and representative.

### RETRACTED: the "2 facts" figure was a measurement bug, not a defect

The first version of this Result claimed `_s` extraction yielded 2 facts from 52
sessions and speculated about loader failures. **That was wrong and the mistake
was mine.** `ingest.extract_instance` returns a tuple `(out, failures)`; the
probe called `len()` on the tuple and printed **2** — the number of elements in
a return value, not a fact count.

Probed properly afterwards: session 0 of the same instance yields **10 facts**
(`name | Nataraja`, `name | Ardhanarishvara`, `name | Mahakala`). Extraction on
`_s` behaves normally. There is no extraction defect here and nothing to
investigate.

The 26-minutes-per-instance timing is unaffected and is the real blocker: it was
wall-clock around the same call and did not depend on the return value.

### Consequence for the submission

The oracle result stands alone and must be labelled as such everywhere — it
already is. Any comparison to a published LongMemEval figure is a comparison to
a number measured on a different split, and that was already forbidden. This
issue changes nothing about what may be claimed; it removes the option of
claiming more.
