# 23 — Widen the stored span cap, then ablate the appeal

Status: wontfix — measured 2026-08-17; the cap is not the constraint


## Parent

`.scratch/hydramem/PRD-appeal.md`

## Why this exists

Issue 22 gives the appeal a wider *render*. This gives it more to render.

The extractor caps each evidence span at 240 characters before it is written, so
the graph does not hold the rest. Widening the render alone can only reveal text
that was already stored — for a fact whose span was truncated at ingest, the
appeal has nothing extra to show. The render change is what makes the appeal a
different request; this change is what makes it a *better* one.

It is separated from 22 because it is a **migration, not a configuration
change**. Snippets are written at ingest and fact ids are content-derived, so a
new cap writes a second generation of facts beside the first and every count
silently inflates. Deletion is impractical here. The reset unit is a wipe.

That is also why the ablation lives here rather than in 22: this issue is the one
that owns a wipe-and-rescore cycle, and slice 17 already demonstrated what
happens when three changes share one cycle — the result was real and **not
separately attributable**. This runs the appeal off and on across the same store.

## What to build

A wider stored evidence span, a clean re-ingest onto it, and the two-run ablation
that says what the appeal was worth.

**Pick the cap from the data, not from taste.** Measure the distribution of span
lengths the extractor produces before choosing — a cap that nothing hits costs
tokens for nothing, and one that still truncates the long tail leaves the defect
in place. Record the chosen number and the distribution it came from.

**The wipe is the whole reset.** Stop the node, remove the store, bring it back,
re-ingest the evaluation slice. Do not attempt a partial or per-tenant reset.

**Then run the slice twice**, appeal off and appeal on, and report the delta on
the `not_in_graph` count specifically — not on overall accuracy, which cannot
distinguish "the appeal recovered false abstentions" from "the wider span helped
the first call too."

**Report the token cost of appeals separately** from the base cost, so the
existing 6.2x-fewer-tokens claim is restated honestly rather than defended.

**A null result is a result and gets written down.** If the appeal does not move
the 27 even with wider spans, the answer is genuinely absent from the extracted
facts, which localises the `single-session-assistant` failure to extraction
itself and closes off the retrieval explanation. CLAUDE.md currently records that
failure as "not located" — a negative result here locates it.

## Cost

One wipe, one full re-ingest of the evaluation slice, two scored runs of the
HydraMem arm. The baseline arms are unaffected by any of this and must not be
re-scored — their numbers are already generated and re-running them would burn a
cycle to reproduce what is on disk.

## Acceptance criteria

- [ ] The span-length distribution is measured and the chosen cap is justified
      against it, in `docs/extraction-quality.md`
- [ ] The node is wiped and the evaluation slice re-ingested clean — fact counts
      are consistent with one generation of facts, not two
- [ ] The slice is scored twice, appeal off and appeal on, over the same store
- [ ] The delta is reported on the per-reason `not_in_graph` count from issue 21,
      not on overall accuracy alone
- [ ] Appeal token cost is reported separately from base token cost
- [ ] Round trips per question are unchanged by the appeal, confirmed on the real
      run rather than assumed from the fixture suite
- [ ] `docs/eval/` is regenerated and CLAUDE.md's measured-result table updated,
      including the direction abstention precision and recall moved
- [ ] If the appeal does not move the 27, that is written down as a located
      failure rather than an inconclusive run

## Blocked by

- `22-appeal-path.md` — there is nothing to ablate until the appeal exists.
- **A live HydraDB node.** Docker Desktop was down for the whole 2026-08-16
  session. If the node fails on writes while passing on reads after it comes up,
  that is the stale writer lease, not this work — see
  `20-writer-lease-deadlock.md`, and delete the lease file rather than the store.

## Result — declined, with the measurement that declines it

**The premise was wrong.** This issue assumed evidence spans were being
truncated at `extract.MAX_SPAN = 240` and that widening would put more verbatim
text in front of the model. Measured across 4,000 stored snippets on the live
node:

| | |
|---|---|
| median length | **95** |
| mean | 98 |
| p90 | 173 |
| at or within 2 of the cap | **117 of 4,000 — 2.9%** |

The cap is barely binding. Raising it can reach 2.9% of facts, and the cost is a
wipe, a full re-ingest of 150 instances and a full re-score. That is the most
expensive cycle available in this project spent on 3% of the evidence.

**The extractor chooses short spans; it is not being cut off.** So the ceiling
this issue was meant to lift is not truncation, and lifting the cap would not
lift it.

### What this closes with it

`22-appeal-path.md` inherited "the appeal can only fire on a windowed question
until issue 23 raises the cap". That inheritance is now permanent rather than
temporary: `widens` requires a snippet longer than `FIRST_WIDTH`, almost none
are, and the cap is not moving. The appeal's width tier is dead code on this
corpus and should be described that way, not as pending.

### What would actually address the ceiling

Storing **raw turns**, which is what the cascade document's §2(a) actually says
and what was never built — a `:Span` node with the whole turn text, not the
extractor's chosen quote. That is a new node label plus an edge, which breaks
the six-edge-type budget, and a schema addition here is a migration rather than
a backfill. It is a real slice, not a cap change, and it is not scoped.

### Cost avoided

One wipe, one re-ingest of 150 instances (~350 uncached extraction calls), one
150-question re-score. Declining it on a 30-second measurement is the whole
value of this issue.
