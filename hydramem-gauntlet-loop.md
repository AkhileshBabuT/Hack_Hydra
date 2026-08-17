# HydraMem — Gauntlet Loop

Matt Shumer's aim prompt (via `duolahypercho/gauntlet-loop`), filled for the Hack Hydra
Track 3 build. Three paragraphs. Paste into a fresh Claude Code session at the repo root.
No harness, no state machine, no scoring framework wrapped around it — the prompt is the
method. **You are the brake.** The loop will not finish on its own; the Aug 20 deadline is
the only stop condition.

---

## 1. Main loop — the memory core

```
I want you to build HydraMem, an agent memory and context-retrieval layer on HydraDB whose
product is abstention, at the level of EverMemOS (83.0% on LongMemEval-S) and Zep (71.2%,
62.4% temporal, 57.9% multi-session). It should be utterly perfect, structurally honest —
it says "not in the history" instead of inventing, and it proves that with a calibrated
threshold rather than a vibe — with every single thing done at ICLR-submission quality,
from session-level fact extraction and deterministic node IDs, to bitemporal SUPERSEDES
chains and current-value resolution, to the sufficiency gate and the abstention decision
boundary, to the mem0-compatible API surface, to the reproducibility of every number in
the results table, to anything you could think of.

Fan out sub-agents and have sub-agents tackle each one individually so that HydraMem is
utterly perfect. You should /loop on each item and have a separate sub-agent check it by
running the official LongMemEval judge harness on the oracle split and the _s split,
per question category, and reporting abstention precision/recall/F1 alongside answerable
coverage so a refuse-everything system cannot score well, to ensure it is
ICLR-submission quality. That separate sub-agent should be a really harsh critic, and if
it isn't ICLR-submission quality, it should keep going.

Don't stop until each sub-agent is utterly wowed with the quality when compared with
EverMemOS and Zep. It should literally compare them side by side blind — same questions,
HydraMem's answer and the full-context Nemotron 3 Ultra baseline's answer with the labels
stripped, judged by the official per-category judge prompt — and say which one is better.
Do this in Python 3.11 against HydraDB over Bolt/HTTP with NVIDIA NIM (Nemotron 3.5
Lightning for extraction, Nemotron 3 Ultra for synthesis) and local BGE-base-en-v1.5
embeddings. /loop until it's utterly perfect. Fan out sub-agents and ultracode.
```

---

## 2. What not to invent (paste as a follow-up before it starts fanning out)

```
Constraints the critic must enforce, not negotiate:

- HydraDB accepts a deliberate OpenCypher SUBSET. No IN, CONTAINS, ENDS WITH, IS NULL in
  WHERE. No min/max (only count/sum/avg/collect). No RETURN *. No unbounded variable-length
  paths — every hop range needs an upper bound. No ON CREATE / ON MATCH on MERGE: it is
  MERGE-by-id then SET. Undirected patterns and multi-type relationship patterns are
  rejected. WITH is pass-through only. UNWIND batches must be parameter-driven lists of
  maps and must go through the Bolt/HTTP client transport, not the in-process shard API.
  Any sub-agent that writes an unsupported clause has failed that item — re-loop it.
- Zero budget. No local large-model inference (16GB RAM, integrated GPU). Nemotron 3 Nano
  4B local is an emergency fallback only, never a primary path.
- Session-level extraction windows, not sliding turn windows. Do not silently regress this.
- Deterministic hash→int node IDs with the 60-bit/JSON boundary caveat, deterministic edge
  IDs AND HydraDB idempotency keys, supersession chains derived in Python and materialized
  after. These are settled; do not relitigate them, implement them.
- Do not build a capture suite, an orchestration state machine, or a scoring framework
  around this loop. The eval harness is the only measurement layer.
- Every number in the results table must be reproducible from a committed script and a
  named (system, backbone, judge, split) tuple. A number without that tuple is a failed item.
```

---

## 3. Sub-loops (run one per work stream, after the main loop has spawned)

**Eval harness**

```
/gauntlet-loop the LongMemEval + BEAM evaluation harness for HydraMem at the level of the
official xiaowu0162/LongMemEval eval scripts, in Python
```

**Abstention calibration**

```
/gauntlet-loop the sufficiency-scoring and threshold-calibration layer at the level of the
Google sufficient-context autorater (0.94 F1) with Learn-Then-Test conformal threshold
selection, in Python
```

**Submission artifacts**

```
/gauntlet-loop the HydraMem README, results table and demo at the level of the Zep paper's
presentation, in Markdown
```

---

## 4. Running it against a 5-day clock

The loop has no natural terminus, so pre-commit the brake:

| Day | Brake condition — stop the loop and ship what exists |
|---|---|
| 1 | Cypher inventory green, write path lands facts + SUPERSEDES edges |
| 2 | Oracle split runs end-to-end; if oracle accuracy < 0.80 the loop is chasing the wrong item |
| 3 | Sufficiency gate + threshold calibrated on the held-out slice |
| 4 | Nemotron 3 Ultra full-context baseline run — this is the thesis gate, not an optimization |
| 5 | Freeze. Loop only on README, results table and demo |

If the critic is still failing an item at the day's brake, cut the item — a shipped 150-question
stratified run beats an unshipped perfect one.
