# The HydraMem Verification Cascade
### An abstention-first runtime memory architecture for LongMemEval, LongMemEval-V2 and BEAM

> **Terminology note.** "Gauntlet Loop" is Matt Shumer's aim-prompt technique for *software
> development* — fan-out builder sub-agents, a separate harsh critic, blind A/B against an
> unreachable reference, looping until a human stops it. That lives in the build orchestrator
> (`hydramem-gauntlet-loop.md`), not here. This document is the **runtime**: what HydraMem
> executes per question at query time. The two are deliberately named apart so neither
> contaminates the other.

---

## TL;DR

- **Abstention is winnable structurally, not statistically.** Zep reaches 71.2% overall on
  LongMemEval with GPT-4o but only 62.4% temporal, 57.9% multi-session, 56.7% preference.
  The open-source ceiling (EverMemOS) is 83.0%. No primary source publishes a clean
  abstention F1, because abstention is trivially gamed by refusing everything. HydraMem wins
  by making abstention a first-class terminal gated by structural evidence-sufficiency over
  the graph, calibrated on held-out data — not by asking a model "are you sure?"
- **The runtime is a bounded state machine** around the committed four-gate cascade:
  Gate 1 structural admissibility → Gate 2 entity/fact fetch → Gate 3 temporal/supersession
  resolution → Gate 4 sufficiency scoring → synthesis → critique → {ANSWER, HEDGED, ABSTAIN}.
  Max 2 re-query cycles.
- **Highest-leverage in-budget techniques:** the Google sufficient-context autorater
  (0.94 F1, no candidate answer needed); conformal threshold calibration; bi-temporal
  SUPERSEDES resolution; verbatim assistant-turn preservation; semantic-entropy-lite
  self-consistency.
- **Three benchmarks, three harnesses.** Running only LongMemEval leaves two of the three
  track datasets unaddressed. LongMemEval-V2 is a *different* benchmark (web-agent
  trajectories, context-gathering formulation). BEAM adds contradiction resolution, event
  ordering and rubric-nugget partial credit at up to 10M tokens. See **Evaluation Cycles**.

---

## 1. What the three benchmarks actually are

### 1.1 LongMemEval (Wu, Wang, Yu, Zhang, Chang, Yu; ICLR 2025; arXiv:2410.10813)

500 curated questions over timestamped user–assistant chat histories. Five abilities:
information extraction, multi-session reasoning, temporal reasoning, knowledge updates,
abstention.

| Question type | Count |
|---|---|
| single-session-user | 70 |
| single-session-assistant | 56 |
| single-session-preference | 30 |
| multi-session | 133 |
| knowledge-update | 78 |
| temporal-reasoning | 133 |
| abstention subset (`_abs` suffix) | 30 |

Three files: `longmemeval_s` (~115k tokens, 30–40 sessions, fits a 128k window),
`longmemeval_m` (~500 sessions, ~1.5M tokens, does not fit), `longmemeval_oracle`
(evidence sessions only). Retrieval eval **always skips the 30 abstention instances** —
they have no ground-truth location. Judge is `gpt-4o-2024-08-06` with question-type-specific
prompts, >97% agreement with human experts (weaker on preference and abstention, but ≥90%
under all settings). A cleaned version was released 2025/09 to prevent history interference.

### 1.2 LongMemEval-V2 (Wu, Ji, Kawatkar, Kwan, Gu, Peng, Chang; UCLA; arXiv:2605.12493)

**Not a bigger V1 — a different benchmark.** 451 manually curated questions over *web-agent
trajectories* (WebArena / WorkArena: Magento shopping, shopping admin, Postmill forum,
ServiceNow), up to 500 trajectories and 115M tokens. Five agent-specific abilities:

| Ability | What it tests |
|---|---|
| static state recall | facts about the environment that don't change |
| dynamic state tracking | state mutated by the agent's own actions |
| workflow knowledge | learned multi-step procedures |
| environment gotchas | quirks/failure modes discovered by experience |
| premise awareness | detecting false premises in the question → **this is the abstention analogue** |

Uses a **context-gathering formulation**: memory returns compact evidence for a downstream
reader, rather than answering directly. Published baselines: AgentRunbook-C (coding agent,
files + augmented sandbox) 72.5% avg; AgentRunbook-R (RAG over three knowledge pools — raw
observations, state-transition events, strategy notes) 48.5%; off-the-shelf coding agent
69.3%. High accuracy comes at high latency.

### 1.3 BEAM (Tavakoli, Salemi, Ye, Abdalla, Zamani, Mitchell; ICLR 2026; arXiv:2510.27246)

100 coherent conversations at **128K / 500K / 1M / 10M tokens** across general, coding and
math domains; 2,000 human-validated probing questions. Ten abilities:

information extraction · multi-hop/multi-session reasoning · knowledge update ·
temporal reasoning · abstention · **contradiction resolution** (new) · **event ordering**
(new) · **instruction following** (new) · preference following · summarization

Scoring uses **rubric nuggets**, allowing partial credit — so BEAM numbers are not directly
comparable to LongMemEval's binary judge. Companion method LIGHT (episodic memory + working
memory + scratchpad) improves 3.50%–12.69% on average over strong baselines. A **BEAM-100K
subset** (20 dialogues, 400 questions) exists and is what follow-on papers actually run —
this is the practical entry point.

---

## 2. Where existing systems collapse

- **Long-context baselines drop 30–60%.** GPT-4o full-context on LongMemEval_S = 0.606
  (0.640 with Chain-of-Note) vs 0.870 oracle (0.924 with CoN) — a 30.3–30.7% drop.
  Llama-3.1-70B-Instruct collapses to 0.334 (0.286 with CoN), a 55–66% drop; Phi-3-Medium
  0.380 (45.9% drop). Worst on multi-session and knowledge-update.
- **Per-category, GPT-4o full-context** (Zep paper Table 3): single-session-preference 20.0%,
  temporal 45.1%, multi-session 44.3%, knowledge-update 78.2%, single-session-user 81.4%,
  single-session-assistant 94.6%.
- **Zep** (Rasmussen, Paliychuk, Beauvais, Ryan & Chalef; arXiv:2501.13956; gpt-4o): 71.2%
  overall vs 60.2% full-context; up to 18.5% accuracy gain and ~90% latency reduction
  (~2.6s vs ~29s), ~1.6k context tokens from top-20 edges+nodes. Per category: preference
  56.7% (+184%), temporal 62.4% (+38.4%), multi-session 57.9% (+30.7%), knowledge-update
  83.3% (+6.5%), single-session-user 92.9% (+14.1%), **single-session-assistant 80.4%
  (−17.7%)**.
- **Mem0** (self-reported, docs.mem0.ai): 94.4% overall at ~6,787 tokens/query, but
  "Knowledge update (93.6) remains the hardest category for an additive, ADD-only
  architecture: older facts are preserved rather than overwritten." Independent tests
  diverge sharply (Bench'd: 32.4% for Mem0 OSS).
- **MemReader paper** (arXiv:2604.07877, unified open-source comparison): EverMemOS 83.0%,
  MemReader-0.6B 80.2%, Zep 63.8%. "Strongest published" is a moving target — always name
  the (system, backbone, judge, split) tuple.
- **BEAM:** contradiction resolution and summarization remain unsolved for everyone; even
  1M-context models degrade as conversations lengthen.

### The three findings that should change your architecture

**(a) Structured extraction actively destroys one category.** Zep loses 17.7 points on
single-session-assistant (80.4% vs 94.6% full-context). Extracting facts from assistant
turns discards exactly what that category asks about. **Fix: store verbatim assistant turns
alongside extracted facts and route that category to raw text.** Cheap, ~2 hours, recovers
points on 56 questions.

**(b) Abstention is gameable in both directions.** A degenerate truncation baseline scored
93.3 on the abstention subset purely by answering almost nothing (arXiv:2601.00821).
Conversely long-context models over-refuse: Claude Sonnet refused on 63% of its full-context
errors, ~70% on single-session-user and multi-session (arXiv:2606.29914). **Fix: report the
risk-coverage curve and AURC, not abstention F1 alone.**

**(c) Retrieved context suppresses abstention.** From the sufficient-context paper: Gemma
went from 10.2% wrong with no context to **66.1% wrong with insufficient context**. Adding
irrelevant retrieval makes models *more* confident. **Fix: the sufficiency check must run
before facts reach the synthesis prompt, never as a self-check inside it.**

---

## 3. Techniques worth adopting

### 3.1 Sufficiency detection — the core mechanism

**Sufficient Context** (Joren, Zhang, Ferng, Juan, Taly & Rashtchian, Google; ICLR 2025;
arXiv:2411.06037). An LLM autorater classifies (query, context) as sufficient/insufficient
*without a ground-truth answer*. Gemini-1.5-Pro at 1-shot CoT: **93% accuracy / 0.94 F1** on
115 human-labeled instances; FLAMe is a cheaper alternative at 89.2% F1. Their selective
generation fuses the sufficiency signal with self-rated confidence, fits a logistic
regression to predict hallucination, and sets a coverage-accuracy threshold — improving
correct-when-answered by 2–10%.

### 3.2 Calibration

**Learn-Then-Test** (Angelopoulos, Bates, Candès, Jordan, Lei; *Annals of Applied Statistics*
2025) and **Conformal Risk Control** (Angelopoulos et al., ICLR 2024) give a
distribution-free, finite-sample guarantee: among questions the system answers, the error
rate is bounded at α. **Conformal factuality** (Mohri & Hashimoto, 2024) scores sub-claims
and backs off to the most specific claim meeting P(factual) ≥ 1−α. Abbasi-Yadkori et al.
(2024) coined "conformal abstention."

*Scoping note:* full LTT with Hoeffding–Bentkus p-values is 4–6 hours if the statistics go
smoothly, and a subtle error produces a guarantee that is **wrong** rather than absent. A
plain threshold sweep on a held-out slice, honestly described, is the safe fallback.

### 3.3 Confidence signals

**Semantic entropy** (Farquhar, Kossen, Kuhn & Gal; *Nature* 630, 625–630, 2024;
doi:10.1038/s41586-024-07421-0): cluster sampled generations by meaning (NLI), take entropy
of the cluster distribution; detects confabulations unsupervised. Costs 5–10× inference.
Semantic Entropy Probes (arXiv:2406.15927) do it in one forward pass but **require hidden
states — unavailable via NIM**. A Bayesian variant (arXiv:2504.03579) matches the AUROC at
53% of the samples, bringing k=5 down to ~k=3.

**Verbalized confidence is miscalibrated** (Xiong et al., arXiv:2306.13063; "On Verbalized
Confidence Scores," arXiv:2412.14737): ECE ~0.1 even at ≥70B; models imitate human confidence
patterns rather than tracking accuracy. **Never use it as the primary gate.**

### 3.4 Iterative retrieval

**IRCoT** (Trivedi, Balasubramanian, Khot & Sabharwal; ACL 2023; arXiv:2212.10509) interleaves
retrieval with each CoT step: +11.3 retrieval recall, +7.1 QA F1 (60.7 vs 53.6) on HotpotQA
with GPT-3, and **50% fewer factual errors** in the generated chain. **CRAG** (corrective
RAG, external evaluator, three paths) and **Adaptive-RAG** (query-complexity routing) are
promptable and in scope. **Self-RAG** (Asai et al., ICLR 2024) requires fine-tuning — out.

### 3.5 Graph memory

- **Zep/Graphiti** — bi-temporal edge invalidation: facts carry valid-time intervals;
  superseded facts marked invalid, not deleted. Validates the SUPERSEDES design.
- **HippoRAG / HippoRAG 2** (Gutiérrez, Shu, Qi, Zhou, Su; NeurIPS 2024 / ICML 2025;
  arXiv:2405.14831, 2502.14802) — OpenIE triples → open KG; Personalized PageRank from
  query-entity seeds; **node-specificity as graph-native IDF**; synonymy edges (cosine >
  0.8). HippoRAG 2: 59.8 avg F1 across six QA sets with Llama-3.3-70B.
  **It does not evaluate on LongMemEval** — do not attribute one to it.
- **A-MEM** (Xu et al., NeurIPS 2025; arXiv:2502.12110) — Zettelkasten notes with
  LLM-driven linking and memory evolution; released impl uses 2 LLM calls per event.
- **LIGHT** (BEAM companion) — episodic + working memory + scratchpad; the only published
  method touching BEAM's summarization and instruction-following.

---

## 4. The runtime cascade

### 4.1 State object

```python
class CascadeState(TypedDict):
    question: str
    question_date: str
    question_type_hint: str
    normalized_entities: list[dict]      # [{surface, canonical, node_id:int}]
    time_window: dict | None
    cypher_admissible: bool              # Gate 1
    candidate_facts: list[dict]          # Gate 2
    verbatim_spans: list[dict]           # raw turns, for assistant/preference routing
    resolved_facts: list[dict]           # Gate 3
    sufficiency_label: str               # SUFFICIENT | PARTIAL | INSUFFICIENT
    sufficiency_score: float             # Gate 4, calibrated 0..1
    selfconsistency_agreement: float
    draft_answer: str | None
    critique_verdict: str | None         # grounded | needs_more | ungrounded
    iteration: int
    max_iterations: int                  # 2
    terminal: str | None                 # ANSWER | HEDGED | ABSTAIN
```

### 4.2 Nodes and edges

`ROUTE` → `GATE1_ADMIT` → `GATE2_FETCH` → `GATE3_TEMPORAL` → `GATE4_SUFFICIENCY` →
`SYNTHESIZE` → `CRITIQUE` → terminals.

- `GATE1_ADMIT` → `GATE2_FETCH` if `cypher_admissible`; else `ABSTAIN`
  (reason=unsupported-pattern; should never fire — inventory verified Day 1).
- `GATE4` → `SYNTHESIZE` if `score ≥ τ_high`.
- `GATE4` → `REQUERY` if `τ_low ≤ score < τ_high` and `iteration < 2`.
- `GATE4` → `ABSTAIN` if `score < τ_low`, or borderline with iterations exhausted.
- `CRITIQUE` → `ANSWER` if grounded and `agreement ≥ κ`.
- `CRITIQUE` → `HEDGED` if grounded and `agreement < κ`.
- `CRITIQUE` → `REQUERY` if needs_more and `iteration < 2`; else `ABSTAIN`.
- `CRITIQUE` → `ABSTAIN` if ungrounded.

Worst case ≈ 5 NIM calls/question (route, sufficiency ≤3, synth, critique).

### 4.3 Mapping to the four gates

| Gate | Role |
|---|---|
| 1 — structural admissibility | Day-1 Cypher inventory as a blocking check; entity presence guard (HippoRAG's "path-finding problem") |
| 2 — entity + fact fetch | Maximize recall; fetch all facts per entity, filter in Python; also fetch verbatim spans |
| 3 — temporal / supersession | Python-derived SUPERSEDES chain; CURRENT vs AS_OF_T vs COUNT_CHANGES vs ORDER |
| 4 — sufficiency scoring | Sufficient-context autorater + self-consistency + coverage flag, thresholded |

---

## 5. Prompts

*(Seven production prompts — router, session extraction, sufficiency autorater, temporal
resolution, synthesis, grounding critique, re-query expansion — as previously specified.
Two amendments below.)*

**Amendment A — router must emit a `use_verbatim` flag.** For
`single-session-assistant` and `single-session-preference`, the router sets
`use_verbatim: true` and synthesis receives raw turns rather than extracted facts. This is
the Zep −17.7pp fix.

**Amendment B — sufficiency prompt gets V2 and BEAM category language.** Add to the
sufficiency rubric:

```
- If the question presupposes an entity, event or state that does not appear in the facts
  at all, the premise is false → INSUFFICIENT, and set "false_premise": true.
- If two non-superseded facts assert conflicting values for the same predicate, set
  "contradiction": true and list both fact ids. Do not silently pick one.
```

`false_premise` maps to LongMemEval-V2's *premise awareness*; `contradiction` maps to BEAM's
*contradiction resolution*. Both are free — the autorater is already reading the facts.

---

## 6. HydraDB Cypher patterns

All obey the documented subset: no `IN` / `CONTAINS` / `ENDS WITH` / `IS NULL` / `min` /
`max` / `RETURN *`; no unbounded variable-length paths; MERGE-by-id then SET (no
ON CREATE/ON MATCH); directed single-type patterns; `WITH` pass-through only;
parameter-driven `UNWIND` of lists-of-maps via Bolt/HTTP, not the in-process shard API.

**Batch upsert entities and facts:**
```cypher
UNWIND $entities AS e
MERGE (n:Entity {id: e.id})
SET n.canonical = e.canonical, n.updated = e.ts
```
```cypher
UNWIND $facts AS f
MERGE (fact:Fact {id: f.id})
SET fact.predicate = f.predicate, fact.value = f.value,
    fact.valid_from = f.valid_from, fact.valid_to = f.valid_to,
    fact.session_id = f.session_id, fact.session_date = f.session_date,
    fact.speaker = f.speaker, fact.kind = f.kind
MERGE (n:Entity {id: f.entity_id})
MERGE (n)-[r:HAS_FACT {id: f.edge_id}]->(fact)
```

**Verbatim span storage (the assistant-turn fix):**
```cypher
UNWIND $spans AS s
MERGE (sp:Span {id: s.id})
SET sp.text = s.text, sp.speaker = s.speaker,
    sp.session_id = s.session_id, sp.session_date = s.session_date, sp.turn_index = s.turn_index
MERGE (n:Entity {id: s.entity_id})
MERGE (n)-[r:MENTIONED_IN {id: s.edge_id}]->(sp)
```

**Supersession edges (Python-derived, materialized after):**
```cypher
UNWIND $supersedes AS s
MERGE (newf:Fact {id: s.new_id})
MERGE (oldf:Fact {id: s.old_id})
MERGE (newf)-[r:SUPERSEDES {id: s.edge_id}]->(oldf)
SET oldf.valid_to = s.invalidate_at
```

**Gate 2 fetch — all facts per entity (drives the batch with UNWIND, avoiding `IN`):**
```cypher
UNWIND $entity_ids AS eid
MATCH (n:Entity {id: eid})-[:HAS_FACT]->(fact:Fact)
RETURN eid AS entity_id,
       collect({id: fact.id, predicate: fact.predicate, value: fact.value,
                valid_from: fact.valid_from, valid_to: fact.valid_to,
                session_id: fact.session_id, session_date: fact.session_date,
                kind: fact.kind, speaker: fact.speaker}) AS facts
```

**Supersession chain (bounded hops, single type, directed):**
```cypher
MATCH p = (f:Fact {id: $fact_id})-[:SUPERSEDES*1..8]->(old:Fact)
RETURN nodes(p) AS chain
```

**Multi-source associative scoring (HippoRAG-PPR role, HydraDB-native):**
```cypher
CALL algo.MSpaths({
  sources: $seed_node_ids,
  maxHops: 3,
  relTypes: ["HAS_FACT","SUPERSEDES"],
  limitPerSource: 25
}) YIELD source, target, path, score
RETURN source, target, score, path
```

**Current value (knowledge-update) — fetch, then select in Python** (cannot use `IS NULL`):
```cypher
MATCH (n:Entity {id: $entity_id})-[:HAS_FACT]->(fact:Fact)
WHERE fact.predicate = $predicate
RETURN collect({id: fact.id, value: fact.value,
                valid_from: fact.valid_from, valid_to: fact.valid_to}) AS candidates
```

---

## 7. Scoring and calibration

**Sufficiency score** = logistic fusion of four features:
`[autorater_score, selfconsistency_agreement, predicate_coverage_flag, num_candidate_facts]`.

**Self-consistency (semantic-entropy-lite):** sample synthesis k=3 at T≈0.7, cluster answers
by normalized-string / lightweight equivalence, take majority-cluster fraction. Gate this to
borderline sufficiency scores only, or it triples cost on every question.

**Calibration:** fit weights and pick τ_low / τ_high on a category-stratified held-out slice.
Apply Learn-Then-Test at α (e.g. 0.15) if time permits; otherwise a documented threshold
sweep. State α and the procedure explicitly.

**Headline figure:** the **risk-coverage curve** — selective accuracy (accuracy among
answered) vs coverage (fraction answered) — plus **AURC**. This is the honest version of the
abstention thesis and the thing a refuse-everything baseline cannot fake.

---

## 8. Evaluation cycles

Three benchmarks, three harnesses, three separate cycles. **Do not merge them into one
number** — the judges, the scoring, and the task formulations differ.

### 8.1 Cycle A — LongMemEval (primary)

| | |
|---|---|
| **Splits** | `oracle` (thesis-validation gate), `_s` (final numbers) |
| **Slice** | ~150 questions, category-stratified across all 7 types + 30 `_abs` |
| **Harness** | Official `xiaowu0162/LongMemEval` eval scripts, question-type-specific judge prompts |
| **Judge** | `gpt-4o-2024-08-06` as specified; if unavailable, Nemotron 3 Ultra as judge — **and say so in the results table**, since judge substitution breaks comparability |
| **Metrics** | Overall accuracy; per-category accuracy (7 types); abstention P/R/F1 over `_abs` **plus** a sample of answerable questions; selective accuracy @ coverage; AURC; tokens/question; NIM calls/question; p50/p95 latency |
| **Baselines** | Nemotron 3 Ultra full-context on `_s` (Day-4 thesis gate); BGE-base-en-v1.5 flat vector RAG |
| **Run mode** | Sequential |
| **Gate** | Oracle accuracy ≥ 0.80 before touching `_s`. Below that, the pipeline is broken, not under-tuned |

### 8.2 Cycle B — LongMemEval-V2

This is a **context-gathering** benchmark, not a direct-QA one. HydraMem's memory layer
returns compact evidence; a downstream reader answers. That means the adapter, not the
cascade, is the work.

| | |
|---|---|
| **Slice** | 100–150 questions stratified across the 5 abilities (static state, dynamic state, workflow, gotchas, premise awareness) |
| **Ingest** | Web-agent trajectories, not chat. Session-level extraction windows become **trajectory-step windows**: one extraction call per coherent action-observation block |
| **Schema delta** | Add `:Action` and `:Observation` node labels; dynamic state tracking is a SUPERSEDES chain over environment state, which the existing Gate 3 already handles |
| **Formulation** | Return top-k evidence spans + resolved facts; evaluate the downstream reader's answer |
| **Metrics** | Per-ability accuracy; **premise-awareness treated as the abstention metric** — report P/R/F1 there; evidence tokens returned (the context-gathering efficiency claim); latency |
| **Baselines** | AgentRunbook-R (RAG) at 48.5% is the realistic comparison; AgentRunbook-C at 72.5% and the coding agent at 69.3% are the unreachable bar |
| **Gate** | Beat 48.5% on the stratified slice, or report honestly and scope V2 as partial support |
| **Honest note** | V2 at full scale is up to 115M tokens. Run a bounded trajectory subset and state the subset size in the table |

### 8.3 Cycle C — BEAM

| | |
|---|---|
| **Slice** | **BEAM-100K** (20 dialogues, 400 questions) — the practical entry point that follow-on papers use. Do not attempt 1M/10M |
| **Harness** | Rubric-nugget scoring with partial credit. **Not comparable to LongMemEval's binary judge** — never average across the two |
| **New categories to handle** | contradiction resolution, event ordering, instruction following, summarization |
| **Cascade coverage** | contradiction resolution → Gate 3 emits `contradiction: true` when two non-superseded facts conflict; the critique step surfaces both and either reconciles by recency or routes to HEDGED. Event ordering → Gate 3 `ORDER` mode over `valid_from`. Instruction following and summarization → **partial support only**; flag as such rather than claiming coverage |
| **Metrics** | Per-ability nugget score across all 10 abilities; abstention P/R/F1; contradiction-detection precision (a distinct, reportable number nobody else publishes cleanly); tokens; latency |
| **Baselines** | LIGHT reports 3.50%–12.69% average improvement over strong baselines. Long-context models degrade with conversation length — that degradation curve is itself worth plotting |
| **Gate** | Contradiction resolution and event ordering are unsolved for everyone. Even a modest number here is a differentiator; a good number is the headline |

### 8.4 Cycle budget

Each cycle is a sequential run against hosted NIM over a stratified slice. Realistic
throughput is **4–6 full cycles per two days**, one of which is consumed by the Day-4
baseline. Budget backwards from cycles, not from coding hours:

| Priority | Cycle | Cost |
|---|---|---|
| 1 | LME oracle (correctness gate) | 1 |
| 2 | LME `_s` + sufficiency autorater | 1 |
| 3 | LME `_s` baseline (Nemotron Ultra full-context) | 1 |
| 4 | BEAM-100K | 1 |
| 5 | LME-V2 stratified subset | 1 |
| — | Threshold sweep, AURC, risk-coverage | **0** — replots cached scores |

If only three cycles are available: run 1, 2, 3 and report V2/BEAM as scoped-out with the
adapter design documented. A working LongMemEval result with an honest per-category table
beats three half-finished benchmarks.

---

## 9. Per-category routing

| Benchmark | Category | Path |
|---|---|---|
| LME | single-session-user | ROUTE → G1 → G2 → G4 → SYNTH → CRITIQUE |
| LME | single-session-assistant | **`use_verbatim: true`** — raw turns, not extracted facts |
| LME | single-session-preference | `kind=preference` facts + verbatim; lenient sufficiency |
| LME | multi-session | multi-entity G2 → `algo.MSpaths` → G4; re-query enabled |
| LME | knowledge-update | G3 CURRENT mode; non-superseded fact only |
| LME | temporal-reasoning | G3 AS_OF_T / ORDER / COUNT modes |
| LME | abstention (`_abs`) | G4 INSUFFICIENT → ABSTAIN; critique double-checks |
| V2 | static state recall | G2 over `:Observation` nodes |
| V2 | dynamic state tracking | G3 supersession over environment state |
| V2 | workflow knowledge | `algo.MSpaths` over `:Action` sequences |
| V2 | environment gotchas | G2 + verbatim observation spans |
| V2 | premise awareness | G4 `false_premise: true` → ABSTAIN |
| BEAM | contradiction resolution | G3 conflict detection → CRITIQUE → reconcile or HEDGED |
| BEAM | event ordering | G3 ORDER mode over `valid_from` |
| BEAM | instruction following | SYNTH with scratchpad — **partial support** |
| BEAM | summarization | LIGHT-style scratchpad — **partial support, deferred** |

---

## 10. Ablations

1. Full cascade vs Gate 4 off (always answer) — isolates abstention value
2. Supersession resolution on/off — isolates knowledge-update and temporal gains
3. Sufficiency autorater vs verbalized-confidence-only — the calibration win
4. Verbatim assistant turns on/off — the Zep −17.7pp fix
5. Re-query 0 vs 2 iterations — multi-session gain vs token cost
6. Self-consistency k=1 vs k=3
7. `algo.MSpaths` vs flat BGE vector retrieval

Ablations 1, 3 and 4 are the ones that carry the thesis. If cycles are scarce, run those.

---

## 11. Caveats

- **Cross-system numbers are not comparable.** Backbones, judges, dataset variants
  (cleaned vs original, `_s` vs oracle) and vendor-vs-independent runs diverge sharply
  (Mem0: 94.4% self-reported vs 32.4% independent). Every number is a
  (system, backbone, judge, split) tuple.
- **Abstention F1 alone is meaningless.** Report it jointly with answerable coverage.
- **HippoRAG 2 has no native LongMemEval number.** Any such figure is third-party.
- **BEAM's rubric-nugget partial credit is not LongMemEval's binary judge.** Never average.
- **LongMemEval-V2 is a different benchmark from V1**, not a harder version. Do not present
  them as one progression.
- **Verbalized confidence: ECE ~0.1 even at ≥70B.** Never the sole gate.
- Several cited 2026 arXiv IDs (2601.x, 2604.x, 2605.x, 2606.x) are very recent preprints;
  treat their figures as preliminary and not peer-reviewed.
- **Full Learn-Then-Test carries implementation risk.** A subtle error yields a guarantee
  that is wrong rather than absent. Ship the threshold sweep if time is short.
