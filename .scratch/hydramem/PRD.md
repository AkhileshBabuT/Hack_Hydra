# PRD — HydraMem: bitemporal agent memory with abstention as a first-class result

Status: ready-for-agent
Source: HYDRAMEM_IMPLEMENTATION_PLAN.md + grill session 2026-08-13
Track: Hack Hydra 03 — Memory + Context Retrieval. Build window Aug 13–20, 2026.

## Problem Statement

An agent with a long conversational history cannot reliably answer questions about it.
Long-context models lose 30–60% of their accuracy on this task, and the dominant failure
is not retrieval — it is **abstention**. The model cannot tell that the answer simply is
not in the history, so it invents one. A confident wrong answer about a user's own past
is worse than no answer.

The second failure is **revision**. Facts change: an employer, an address, a preference.
Memory layers that overwrite on update can answer "where do I work" but cannot answer
"where did I used to work", and cannot say when the change happened. Questions in the
`knowledge-update` and `temporal-reasoning` categories are the hardest in LongMemEval
for exactly this reason.

## Solution

HydraMem is an agent memory layer built on HydraDB where **abstention is a returned
result, not a prompt outcome**.

Before any model is asked to answer, a four-gate structural cascade decides whether the
graph *can* answer. Each gate that fires returns a machine-readable reason
(`unknown_entity`, `no_such_relation`, `no_connecting_path`, `no_fact_in_window`). An
answer that passes the gates must cite fact identifiers present in the retrieved
subgraph, or it is downgraded to abstention with reason `uncited_answer`.

Facts are never mutated. A revision writes a new Fact and a SUPERSEDES edge, so "what is
X now" and "what did X used to be" are two traversals of the same chain rather than two
prompting strategies.

The layer ships as a mem0-compatible drop-in, so it can be swapped into an existing agent
in two lines, with one method — `history()` — that mem0 structurally cannot provide.

## User Stories

1. As an agent developer, I want the memory layer to return `ABSTAIN` with a reason when the answer is not in the history, so that my agent can say "I don't know" instead of confabulating.
2. As an agent developer, I want each abstention to name the specific entity or predicate that was missing, so that I can debug why a question failed without reading logs.
3. As an agent developer, I want an answer to be rejected when it cites no supporting fact, so that a fluent-but-unsupported response never reaches my user.
4. As an agent developer, I want an answer rejected when it cites a fact identifier absent from the retrieved subgraph, so that citation cannot be faked by the model.
5. As an agent user, I want "where do I work" to return my current employer, so that stale facts do not surface as current ones.
6. As an agent user, I want "where did I work before" to return prior employers in order, so that I can ask about my own history.
7. As an agent user, I want to ask what was true at a past point in time, so that time-scoped questions resolve against the state as of that time.
8. As an agent user, I want to see when a fact changed, so that a revision has a date attached rather than being silently replaced.
9. As a judge, I want every answer to expose the fact path, source session, turn index and timestamp, so that I can audit any single answer in seconds.
10. As a judge, I want the gate trace on an abstention, so that I can see which gate fired and why.
11. As a judge, I want setup to be one command, so that I can reach a working demo without debugging a toolchain.
12. As a judge, I want to see which HydraDB capability maps to which piece of real code, so that "uses HydraDB meaningfully" is verifiable rather than asserted.
13. As a judge, I want per-category accuracy against baselines, so that a single aggregate number cannot hide a weak category.
14. As a judge, I want abstention precision and recall reported as headline metrics, so that the central claim is measured rather than described.
15. As a judge, I want read and write cost reported, so that I can judge whether the design survives real usage.
16. As an agent developer, I want to write a memory and immediately read it back, so that my agent never misses a fact it just stored.
17. As an agent developer, I want re-running ingest after a crash to be a no-op, so that a resumed run does not duplicate facts or corrupt the supersession chain.
18. As an agent developer, I want ingesting sessions in any order to produce the same final state, so that delivery order is not a correctness dependency.
19. As an agent developer, I want to swap HydraMem into an existing mem0 agent in two lines, so that adoption costs nothing.
20. As an agent developer, I want multi-hop questions resolved in a bounded number of round trips, so that latency does not scale with the number of candidate anchors.
21. As a maintainer, I want every Cypher statement validated against a live node before the code using it is written, so that parse-time rejections surface on day one.
22. As a maintainer, I want evaluation runs to be reproducible, so that a reported score can be regenerated.
23. As a maintainer, I want LLM responses cached to disk, so that reruns cost nothing and the project stays at zero spend.
24. As a maintainer, I want the measured context ceiling recorded rather than assumed, so that the baseline arm is not designed around a spec-sheet number.

## Implementation Decisions

These supersede the corresponding sections of HYDRAMEM_IMPLEMENTATION_PLAN.md, which was
written against assumed HydraDB behavior. Each decision below was verified against the
HydraDB source at commit `6a2fbb1`.

### Verified HydraDB constraints that drove the redesign

- `MERGE` matches on `id` only, and node `id` **must be a non-negative integer**. `ON CREATE` / `ON MATCH` do not exist.
- `WHERE` has no `IN`, `CONTAINS`, `ENDS WITH`, or `IS NULL`. Aggregates are `count`, `sum`, `avg`, `collect` only — no `min`/`max`.
- `ORDER BY` accepts a projected alias, `<binding>.id`, or `count(*)`.
- `UNWIND` batches are narrow: one relationship pattern, one hop, directed; `UNWIND … CREATE` cannot be followed by another clause. Vertex upsert must be `MERGE` by id followed by `SET`.
- There is **no index DDL**. Property indexes are maintained automatically on every mutation, so the planned index-bootstrap work does not exist.
- `EXPLAIN` is reachable only through the in-process shard API, not over Bolt or HTTP.
- Guarded merge **is** reachable from Cypher, via marker properties inside an `UNWIND` vertex upsert. This is undocumented in `cypher-compat.md`.
- Mutation idempotency keys are set as Bolt transaction metadata `hydradb.idempotency_key`, max 128 chars, `[A-Za-z0-9._-]`.
- `algo.MSpaths` accepts `pairwise`, `fairRelationshipVariants` and `resultLimit`; `fairRelationshipVariants` requires an unweighted pairwise query. Unknown config keys are rejected.
- UNION supports up to 256 arms, read-only, with per-arm `ORDER BY` and `LIMIT`.

### Node identity

String identities are hashed to integer node ids. The canonical string is retained as a
property for display and as an `MSpaths` selector. This keeps `MERGE` idempotent with no
id allocator and no lookup round trip.

```
nid(kind, key) = int.from_bytes(sha256(f"{kind}:{key}").digest()[:8], "big") >> 4   # 60 bits

Entity  -> nid("E", f"{instance_id}|{entity_key}")
Fact    -> nid("F", idempotency_key)
Session -> nid("S", f"{instance_id}|{session_id}")
Edge    -> nid("R", f"{src}|{TYPE}|{dst}")
```

### Graph schema

Node labels `Entity`, `Fact`, `Session` and exactly six edge types (`SUBJECT`, `OBJECT`,
`SUPERSEDES`, `ASSERTED_IN`, `NEXT`, `ALIAS_OF`) are retained from the plan. `SUBJECT` and
`OBJECT` carry the traversal load so their per-edge-type compiled topology stays dense.
Every node carries `instance_id`. The plan's index-bootstrap module is deleted — indexes
are automatic.

### Write path

Writes are batched `UNWIND` statements, one statement per node label and one per edge
type, ~500 rows each. A session ingest is roughly eight statements rather than one.
Idempotency is enforced twice: deterministic ids make `MERGE` a row-level no-op under any
batching, and `hydradb.idempotency_key` makes the request a no-op on replay and surfaces
an explicit non-retryable conflict when a key is reused with different content.

Guarded merge is used where a replayed session must not move a timestamped value
backward, via the marker-property form inside the vertex upsert.

### Supersession

The chain is a **pure function of the fact set**: group by `(entity, predicate)`, sort by
`(asserted_at, id)`, and pair adjacent facts. Permutation-invariance is therefore a
property of a function that can be unit-tested with no database. A post-ingest batched
pass materializes the derivation into `SUPERSEDES` edges, `Fact.status` and
`Fact.valid_to`, so the chain still exists as graph structure for traversal, for
`history()`, and for the demo.

### Retrieval cascade

Four gates, each returning a structured reason, evaluated before any answering call.

- **Gate 1, `unknown_entity`** — resolve question entities against `Entity` and the `ALIAS_OF` closure for the instance.
- **Gate 2, `no_such_relation`** — one `MATCH` pulls every Fact attached to the resolved Entity; the candidate predicate set is applied in Python, because `IN` does not exist and because this gate is the primary suspect when abstention precision fails, so it must be testable without a database.
- **Gate 3, `no_connecting_path`** — a single `algo.MSpaths` pairwise call resolves all candidate anchors at once, replacing per-anchor round trips.
- **Gate 4, `no_fact_in_window`** — resolve the window, then filter on the two temporal axes: current is `status = 'current'`; value-at-T is `valid_from <= T AND (valid_to = 0 OR valid_to > T)`; change history walks `SUPERSEDES`.

Synthesis receives only the retrieved subgraph and must cite fact identifiers. Cited ids
are parsed and checked against the retrieved set; an uncited or falsely-cited answer is
downgraded to abstention with reason `uncited_answer`.

### Statement inventory

Every Cypher template the codebase can emit lives in a single module and is executed once
against a throwaway local node by a dedicated make target, before the code that uses it
is written. This exists because every HydraDB incompatibility found during planning was a
parse-time rejection that would otherwise have surfaced days later, tangled with
extraction and gate failures.

### LLM access

All model access goes through one provider-agnostic module: NVIDIA NIM primary
(OpenAI-compatible), Gemini Flash documented fallback for the context-overflow tail,
Nemotron 3 Nano 4B local via Ollama as emergency fallback. Extraction runs at session
granularity with reasoning OFF and a strict schema. Every call is disk-cached by content
hash, so reruns cost nothing. Pre-flight token counting routes oversized requests to the
overflow path rather than discovering the ceiling as a runtime error.

The same answering model is used across every evaluation arm; only the retrieval layer
differs.

### API surface

mem0-compatible: `add`, `search`, `get_all`, `history`, `delete`, plus `explain`. `add`
returns a HydraDB bookmark; `search` accepts one, giving read-your-own-writes through
causal consistency. Evaluation pins strong consistency for reproducibility; the live demo
uses causal so latency reflects the real hot path.

## Testing Decisions

A good test here asserts external behavior — the reason string returned, the final graph
state, the parse acceptance of a statement — never internal call sequences or private
structure. The bulk of the logic is deliberately pushed into pure functions so that the
tests that matter need no database.

- **Statement inventory** — every emittable Cypher template executed against a live throwaway node with empty parameters, asserting no parse rejection. Runs in seconds and is the primary defense against the class of bug found during planning.
- **Chain derivation** — pure-function tests over fact sets, including the permutation case: shuffled input must yield an identical chain. This is the unit-test form of the plan's ordering-independence gate.
- **Gate logic** — the predicate gate and temporal filters tested as pure functions over fact lists, with no database and no LLM.
- **Identity** — hashing tested for determinism and stability across processes.
- **Idempotency** — integration test that ingests, re-ingests with deliberately different batch groupings, and asserts identical node and edge counts.
- **Fixture suite** — 25 hand-written cases against a live node, at least 10 of them abstention cases covering each gate reason. This is the development loop; the full benchmark is too slow to iterate against.
- **Evaluation reproducibility** — every reported number regenerable by a single make target, with LLM responses served from the committed disk cache.

Extraction quality is measured, not unit-tested: a hand-checked slice establishes
schema-validity rate and fact precision before the full corpus is committed to.

## Out of Scope

- Reproducing mem0 in-harness. Its published figure is cited as context, clearly labelled as measured under different conditions.
- The BEAM benchmark.
- LongMemEval-V2 — a multimodal web-agent benchmark, a materially different task.
- Deployment or hosting of any kind. The demo is local and recorded; the submission form makes a deployed link optional.
- Any paid service. The project must cost zero.
- Sophisticated entity resolution. Normalization plus exact-key match plus a conservative alias edge is the ceiling; disambiguation is a different track's problem.
- Multi-tenancy beyond the `instance_id` partition already required by the benchmark.
- UI beyond a single page showing answer-or-abstention-reason, fact path, and supersession chain, held to a four-hour timebox.

## Further Notes

**Judging-relevant honesty constraints.** Published LongMemEval figures were measured with
different answering models, embedders and judges. Only within-harness comparisons may be
claimed. The context-overflow split must be disclosed by count. If the full corpus is not
ingested, the measured subset and its throughput must be stated rather than projected
silently.

**Upstream contribution surface.** Planning surfaced three genuine gaps in the HydraDB
repository: guarded-merge marker syntax is implemented but undocumented; `EXPLAIN` is not
reachable over Bolt or HTTP; and the runbook referenced for duration-histogram units does
not exist in the repository. Filing these is explicitly a judging asset.

**The thesis is falsifiable and is tested early.** The strongest available full-context
baseline runs on day four, not day six, so that a failure of the central claim leaves days
to react rather than hours. If HydraMem does not beat it on abstention and
knowledge-update, the diagnostic order is: predicate gate too permissive, then entity gate
missing aliases, then citation check failing to downgrade.

**Feature freeze is end of day seven.** Day eight is verification and submission only.
