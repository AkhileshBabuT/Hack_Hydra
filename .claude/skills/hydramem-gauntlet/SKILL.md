---
name: hydramem-gauntlet
description: One tick of the HydraMem gauntlet loop — pick the weakest measured item, fan out sub-agents to fix it, have a harsh critic re-score it against the real harness, log the tick. Designed to be driven by /loop. Use when the user says "gauntlet", "/hydramem-gauntlet", or "run the gauntlet loop".
---

# HydraMem Gauntlet Loop

One **tick**. Not the whole loop — `/loop` supplies the repetition, this file
supplies the method. Invoking this yourself once is also valid.

    /loop /hydramem-gauntlet          # self-paced
    /loop 45m /hydramem-gauntlet      # fixed interval

Invoking this skill **is** the user's request to fan out sub-agents. Do it.

## The aim (verbatim, do not soften)

> Build HydraMem, an agent memory and context-retrieval layer on HydraDB whose
> product is abstention, at the level of EverMemOS and Zep. It should be utterly
> perfect, structurally honest — it says "not in the history" instead of
> inventing, and it proves that with a calibrated threshold rather than a vibe —
> with every single thing done at ICLR-submission quality, from session-level
> fact extraction and deterministic node IDs, to bitemporal SUPERSEDES chains and
> current-value resolution, to the sufficiency gate and the abstention decision
> boundary, to the mem0-compatible API surface, to the reproducibility of every
> number in the results table.
>
> The critic sub-agent is a really harsh critic. If it isn't ICLR-submission
> quality, keep going.

## Read this before deciding anything

`CLAUDE.md` § *The measured result* is the ground truth for this repo. **Do not
re-derive the build state** — slices 01–17 are built, 328 tests, the verdict is
GO on 2 of 3 checks. Read it, don't rediscover it.

The original aim doc predates the build and carries three falsified premises.
They are corrected here and the corrections are **not negotiable**:

| Aim doc says | Reality | What the loop does instead |
|---|---|---|
| brake at "oracle accuracy < 0.80" | measured **0.4933** | that brake would fire every tick forever — use the table below |
| BEAM in the eval harness | not evaluated, and not going to be | LongMemEval oracle + `_s` only |
| beat Zep / EverMemOS side by side | different backbone, embedder and judge | **in-harness arms only**; cite published figures, never claim to beat them |

## The tick

1. **Pick one item** — the weakest measured thing, from
   `docs/eval/oracle-per-category.csv` and `CLAUDE.md` § *Known gaps*. One item.
   Not a sweep. If the last tick's item is unfinished, that is this tick's item.
2. **Fan out** sub-agents on independent parts of that one item.

   **Every sub-agent brief must forbid git write commands outright** — no
   `checkout`, `stash`, `reset`, `clean`, `restore`. Slices 10–17 are
   uncommitted, so for most files `HEAD` is slice 07 and `git checkout` is not a
   revert, it is a deletion of six slices with no object-database copy to
   recover from. This is not hypothetical: it happened on tick 1 to
   `hydramem/gates.py`, from a brief that said "use a scratch copy" and was not
   explicit enough. Say **"copy the file into the scratchpad and mutate the
   copy; never modify a repo file"**, in those words.
3. **Critic pass** — a *separate* sub-agent, told to be hostile. It re-scores
   against the real harness (`scripts/run_eval.py --oracle`,
   `scripts/cost_table.py --oracle`), per question category, reporting
   abstention precision/recall **alongside** answerable accuracy so a
   refuse-everything build cannot score well. It has authority to fail the item.
4. **Blind comparison** where the item touches answer quality: same questions,
   HydraMem's answer beside the full-context Nemotron 3 Ultra baseline's, labels
   stripped, judged by the per-category judge prompt. The data already exists in
   `.eval/oracle/*.jsonl` — mine it, don't re-run scoring to get it.
5. **Log the tick** — append one line to `.scratch/hydramem/gauntlet-log.md`:
   date, item, what moved, critic verdict. That log is the only state this loop
   keeps. Do not build more.

## Constraints the critic enforces, never negotiates

- **HydraDB's Cypher is a deliberate subset.** No `IN` / `CONTAINS` /
  `ENDS WITH` / `IS NULL` in `WHERE`. No `min`/`max`. No `ON CREATE` / `ON MATCH`
  — `MERGE` by id then `SET`. `UNWIND` batches are parameter-driven lists of
  maps, one hop, directed. Full list in `CLAUDE.md` § *verified constraints*. A
  sub-agent that writes an unsupported clause **has failed that item** — re-loop
  it, don't patch around it.
- **Every Cypher statement goes in `statements.py` and its `INVENTORY`.**
- **No model runs inside a gate.** An unrecognised question passes. That
  direction is fixed.
- **Re-ingesting after an extractor change needs a wiped node.** Fact ids are
  content-derived; skipping the wipe silently inflates every count.
- **Count, do not estimate.** `client.round_trips()` and `llm.usage()`.
- Session-level extraction windows, not sliding turn windows. Settled.
- Deterministic hash→int node ids, deterministic edge ids, idempotency keys,
  chains derived in Python and materialized after. Settled — implement, don't
  relitigate.
- **Do not build a capture suite, an orchestration state machine, or a scoring
  framework around this loop.** The eval harness is the only measurement layer.
- Every number in a results table is reproducible from a committed script and a
  named (system, backbone, judge, split) tuple. A number without that tuple is a
  failed item.
- Zero budget. No local large-model inference.

## Brakes — pre-committed, replacing the aim doc's 5-day table

The loop has no natural terminus, so it stops on these, not on satisfaction:

| Stop when | Because |
|---|---|
| `verdict()` regresses below 2 of 3 vs `full_context` | the tick made it worse; revert the tick, not the brake |
| a tick ends with no score change **twice running** | the item is exhausted; pick a different one |
| the known-red budget test is "fixed" by raising `BUDGET_SECONDS` | that deletes the only signal for read-cost-vs-store-size |
| a number appears anywhere that is not in `docs/eval/` | fabrication; kill the tick |
| a sub-agent dies on a **session usage limit** | not a verdict. The item is *unverified*, not failed — carry it to the next tick and say so, never record it as passed |
| **submission deadline** | a shipped 150-question stratified run beats an unshipped perfect one |

A critic that never returned is the dangerous case: the work looks finished, the
tick reads clean, and nothing independent ever checked it. Tick 2 hit this. When
it happens, answer the critic's most important question yourself before
re-spending on another one — on tick 3 that found a real defect the critic would
have caught, at a fraction of the cost.

Report honestly at each tick, including when nothing moved. `noop: true` on a
tick that found nothing is the correct outcome, not a failure.
