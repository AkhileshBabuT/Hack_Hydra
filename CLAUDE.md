# HydraMem

Bitemporal agent memory on HydraDB, where **abstention is a first-class result**.
Hackathon submission, Track 03 — Memory + Context Retrieval.

Facts are never mutated: a revision writes a new `Fact` plus a `SUPERSEDES` edge,
so "what is X now" and "what did X used to be" are two traversals of one chain.
Before any model is asked to answer, four structural gates decide whether the
graph *can* — and each gate that fires returns a machine-readable reason.

Planning artifacts live in `.scratch/hydramem/`. Do not restate their contents here.

## Commands

```bash
docker compose up -d                      # HydraDB: bolt 7687, http 8443, admin 9090
./.venv/Scripts/python.exe scripts/fetch_corpus.py                 # LongMemEval -> data/
./.venv/Scripts/python.exe scripts/ingest_one.py <question_id>     # one instance, end to end
./.venv/Scripts/python.exe scripts/ingest_one.py --oracle          # same, on the small split
./.venv/Scripts/python.exe -m pytest -q   # full suite
./.venv/Scripts/python.exe -m pytest tests/test_statements.py -q   # Cypher parse check
./.venv/Scripts/python.exe scripts/probe_budget.py                 # regenerate docs/budget.md
./.venv/Scripts/python.exe scripts/measure_extraction.py           # slice 06 gate -> docs/extraction-quality.md
```

`make` is **not installed on this machine** — the Makefile mirrors these commands
for Linux/macOS judges, but run the Python directly here. Compose has an `init`
service that creates `LOCAL_PATH` and seeds the auth token, so `docker compose up -d`
is sufficient from a clean clone with no other setup.

## Layout

```
hydramem/
  ids.py          deterministic int node identity
  statements.py   EVERY Cypher template, + INVENTORY registry
  client.py       Bolt: bookmarks, consistency, idempotency keys, metrics scrape
  llm.py          provider-agnostic model access: cache, backoff, token counting
  chain.py        supersession: pure derivation + materialization rows
  corpus.py       LongMemEval loader: streaming, whole sessions, epoch timestamps
  extract.py      session-level extraction, controlled predicates, output repair
  ingest.py       pure row builder + batched guarded writes
  gates.py        gates 1-2: lexical entity/predicate resolution, no model
  answer.py       gate cascade -> retrieve -> answer -> citation check -> abstain
scripts/probe_budget.py   slice 02 measurement gates -> docs/budget.md
scripts/fetch_corpus.py   corpus download into data/ (gitignored)
scripts/ingest_one.py     slice 03 demo: one instance, corpus -> answer
scripts/measure_extraction.py  slice 06 extractor gate -> docs/extraction-quality.md
docs/hydradb-notes.md     rough edges found; upstream issue candidates
docs/budget.md            measured limits (generated, do not hand-edit)
docs/extraction-quality.md     extractor gate numbers (generated)
docs/extraction-review.md      60-fact hand-check sheet (generated)
```

## Where the build is

Slices 01–07 are done; each issue in `.scratch/hydramem/issues/` carries its own
acceptance detail and a Result section recording what changed and why. **08 is
next** — the temporal gate. Gates 1 and 2 (`unknown_entity`, `no_such_relation`)
are live in `answer.py`; gates 3 and 4 join the same `gates.run()` cascade.

06 closed **GO** on three automatic gates — schema validity 100% (38/38),
grounding 96.9%, `other` share 24.4%. Its hand-check sheet
(`docs/extraction-review.md`) is generated and deterministic but **its tally is
unfilled**: fact precision is not measured and no number in this repo claims
otherwise. The two defects 06 measured and did not fix are recorded against the
issues that inherit them, 07 and 10, not left as folklore here.

## Non-negotiable conventions

**No model runs inside a gate.** Entity and predicate detection are lexical —
capitalised runs, first-person cues, a literal cue table. A gate whose job is to
stop confabulation cannot itself be a language model without inheriting the
failure it exists to prevent. The price is bluntness, and the direction of that
bluntness is fixed: **an unrecognised question passes**. A false abstention is an
answer thrown away with no way to notice; a false pass costs one model call and
still meets the citation check.

**Every Cypher statement goes in `statements.py` and its `INVENTORY`.** The
inventory is executed against a live node by `tests/test_statements.py`. HydraDB
rejects unsupported syntax at parse time and `EXPLAIN` is unreachable over Bolt,
so this test is the only early warning that exists. Adding a query anywhere else
means nobody validates it.

**Only functional predicates supersede.** `extract.FUNCTIONAL_PREDICATES` (employer,
lives_in, age, …) chain by predicate; everything else chains per distinct value, so
a new `likes` accumulates instead of retracting the last one. Treating every
predicate as functional marked 193 of 220 facts superseded on a real instance and
would have made "what do I like" answerable only with the most recent mention.

**Every measured failure gets a test before its slice closes.** Not a note in a
document — a test that fails if the failure returns, holding the real payload
rather than a description of it. Slice 06's collapsed decodes are pinned as the
literal strings the model emitted (`{`, a zero-width run) because a paraphrase
would not notice a decoder that stops collapsing the same way. A defect a slice
chooses *not* to fix goes on the issue that inherits it, with the test that
demonstrates it.

**Push logic into pure functions.** Gate predicates, chain derivation and temporal
filters are tested over plain fact lists with no database and no model call. This
is deliberate: the predicate gate is the primary suspect whenever abstention
precision fails, so it must be debuggable without infrastructure.

**Re-ingesting after an extractor change needs a wiped node.** Fact ids are
content-derived, so a new prompt or model writes a *second* generation of facts
beside the first and every count silently inflates. HydraDB cannot delete them
affordably. `docker compose down`, `rm -rf hydradb-data`, `docker compose up -d`.

**All model access goes through `llm.py`.** Constructing a provider client
elsewhere breaks the disk cache, and the cache is what keeps reruns at $0.

**One answering model across every evaluation arm.** Full-context, vector RAG and
HydraMem must differ *only* in their retrieval layer, or the comparison measures
model quality instead of retrieval quality.

## HydraDB source reference

A checkout of `hydra-db/hydradb` @ `6a2fbb1` lives at **`C:\Projects\hydradb-ref`**,
indexed with codegraph. Query it without leaving this project:

```
codegraph_search  / codegraph_explore   with projectPath: "C:\\Projects\\hydradb-ref"
```

101 Rust files, ~5.6k nodes. Prefer this over grep when verifying a Cypher
constraint or a procedure signature — the Cypher surface is undocumented in
places and the parser is the only authority. This project is indexed too — run
`codegraph sync .` after adding a module — and `codegraph_explore` answers
"how does X work" in one call where Read and Grep take several.

## HydraDB: verified constraints

Verified against `hydra-db/hydradb` @ `6a2fbb1`. The Cypher surface is a
deliberate subset; assume nothing beyond it without testing.

- **Node `id` must be a non-negative integer.** `MERGE` matches on `id` alone.
  String identities are hashed to ints in `ids.py`; the canonical string is kept
  as a property.
- **No `ON CREATE` / `ON MATCH`.** A vertex upsert is `MERGE` by id then `SET`.
  Folding other properties into the MERGE pattern is rejected.
- **No `IN`, `CONTAINS`, `ENDS WITH`, `IS NULL`** in `WHERE`. Filter client-side.
- **No `min`/`max`** aggregates — only `count`, `sum`, `avg`, `collect`.
- **`ORDER BY`** takes a projected alias, `<binding>.id`, or `count(*)`.
- **`UNWIND` batches are narrow**: one relationship pattern, one hop, directed;
  `UNWIND … CREATE` cannot be followed by another clause. Input must be a
  parameter holding a list of maps.
- **No explicit transactions.** *"explicit transactions are not supported; use
  auto-commit RUN queries."* Metadata rides on the `Query` object. Ingest is
  therefore idempotent batched writes under at-least-once delivery, never
  transactional units.
- **No index DDL.** Property indexes are maintained automatically.
- **`EXPLAIN`** exists only on the in-process shard API, not over Bolt or HTTP.
- **UNION**: read-only, ≤256 arms, per-arm `ORDER BY`/`LIMIT`.
- **A node with a label or a non-id property must be named.**
  `(:Fact {id: 1})-[r]->(e)` is rejected; `(f:Fact {id: 1})-[r]->(e)` is not.
- **An edge batch is rejected whole if an endpoint is missing**, as a syntax
  error. Node batches must land first; a dangling row fails loudly.
- **An `UNWIND` row must carry every field its statement names.** Adding a
  property to an upsert breaks every existing caller.
- **Deletion is impractical**: `DETACH DELETE` costs ~0.3s/node and a few
  hundred nodes exceed the server's 30s query timeout; the `UNWIND` batch form
  is rejected. There is no per-tenant reset — wipe `hydradb-data/` instead.
- **The test suite writes into the live node** under `test-<test name>` tenants
  (`tests/conftest.py`), and since deletion is impractical those tenants
  accumulate forever. So a store with a few dozen entities is test residue, not
  a real ingest: after a wipe, `hydradb-data/` refills with test data on the
  next `pytest` run and looks populated while holding no corpus at all. Check a
  real `instance_id` with `count_facts` before believing a graph has data.
  `count_label` cannot help — HydraDB has no dynamic labels, so it is hardcoded
  to `:Entity` and its `label` parameter is ignored.
- **Keep to 6 edge types.** CSC generations are built per edge type; `SUBJECT`
  and `OBJECT` carry the traversal load and stay dense because of it.

### Guarded merge — real but undocumented

Absent from `cypher-compat.md`, implemented in the parser
(`opencypher.rs:18-20`, test at `:3928`):

```cypher
UNWIND $rows AS row MERGE (n {id: row.vid})
  SET n:Fact,
      n.__hydradb_update_if_newer_by     = row.asserted_at,
      n.__hydradb_create_only_first_seen = row.first_seen
```

Verified live, full rules: every guarded property must also be `SET` from a row
field of the *same name*; a create-only marker requires an update guard present;
the guard property may not itself be create-only. The comparison is **strictly
less-than**, so an *equal* guard value writes nothing — a fact upsert guarded on
`asserted_at` is immutable on replay. Supersession is therefore materialized by
`CLOSE_FACT`, guarded on `valid_to` (0 → timestamp), and that same strictness is
what stops a re-ingest resetting `status` to `current`. Guard markers work only
inside the `UNWIND` vertex upsert form — `MATCH … SET` has no guarded equivalent.

### Connection facts

- Routing URI `neo4j://127.0.0.1:7687`; bearer auth with the auth-token contents.
- Idempotency key: tx metadata `hydradb.idempotency_key`, ≤128 chars,
  `[A-Za-z0-9._-]`. **It does not police conflicts on the Cypher path** —
  verified: one key with two different payloads is accepted, both applied.
  Uniqueness comes from `ingest.batch_key` hashing the rows it sends. Do not
  repeat the plan's "explicit non-retryable conflict" claim as a HydraDB
  guarantee.
- Consistency: `hydradb.consistency` = `causal` (demo, real hot path) or `strong`
  (evaluation, so scores reproduce).
- `RUST_MIN_STACK=33554432` is required — without it the node serves `/readyz`
  then aborts on the first query.
- Setup uses the published image `ghcr.io/hydra-db/hydradb:latest`. Do **not**
  build from source: it needs Rust 1.91 + libcypher-parser + GraphBLAS and the
  documented recipe covers Ubuntu/WSL and macOS only.

## NVIDIA NIM: verified behavior

Measured, in `docs/budget.md`. Regenerate rather than hand-edit.

| Role | Model |
|---|---|
| Extraction | `nvidia/nemotron-3.5-lightning-30b-a3b` |
| Answering — every arm | `nvidia/nemotron-3-ultra-550b-a55b` |
| Answering fallback | `nvidia/nemotron-3-super-120b-a12b` |

- **Reasoning is ON by default.** Only `chat_template_kwargs={"thinking": false}`
  disables it. A `/no_think` system prompt and `extra_body={"reasoning": false}`
  both silently leave thinking tokens in the output, which destroys strict JSON
  parsing. This is the highest-value single setting in the pipeline.
- **No context ceiling found.** 1,000,007 tokens accepted (provider-counted
  1,000,023) in 39.4s. LongMemEval_S averages ~115k, so there is no overflow tail
  and no fallback provider is needed. `GEMINI_API_KEY` is unused.
- **Throughput 35.8–189.5 RPM** across runs. Plan against ~40 as a floor.
- **NIM returns transient 404s** for a model id that is valid and answers again
  minutes later. `llm.py` retries 404/408/409/429; a persistent 404 on the
  answering model means switching *every* arm to the fallback, never falling
  back per call — that would break the single-answering-model rule silently.
- **`models.list()` is public** — it returns 102 models for a deliberately invalid
  key. It is not an auth check. Only `chat/completions` proves authorization.
- **Extraction needs `max_tokens=8192`.** A dense session yields 30+ facts; at
  4096 the JSON was truncated mid-string and the session was lost to a parse
  error that looked like a model-quality problem and was ours.
- **The extractor omits `subject` and occasionally misspells a field name.** The
  schema defaults `subject`/`predicate`/`value` for that reason and `clean()`
  drops what is left empty. A per-session parse failure is counted and named in
  the ingest stats, never swallowed.
- **A null in any field used to cost the whole session.** pydantic reports
  per-fact errors as one failed `Extraction`, so one `turn_idx: null` discarded
  27 good facts. Coerce nulls in a validator; never let one field reject a
  session. Measured: this alone was 8 points of schema validity.
- **Greedy decoding collapses on ~5% of sessions** — one returned the single
  character `{`, another a run of zero-width spaces. Because the disk cache is
  keyed on the request, retrying the identical request returns the identical
  garbage forever. `extract.extract_raw` retries once at
  `RETRY_TEMPERATURE = 0.3`; temperature is the only lever that reaches this.
  Schema validity 86.8% → 94.7% → **100%** (38/38) across the two fixes.
- `tiktoken`'s `cl100k_base` tracks the provider's own count within ~0.01% at
  scale, so it is sound for pre-flight routing decisions.

## Corpus

`xiaowu0162/longmemeval-cleaned` on HuggingFace (MIT), not the original release —
its author marks the original deprecated. `data/` is gitignored; fetch it with
`scripts/fetch_corpus.py`. `longmemeval_s_cleaned.json` is the benchmark arm
(277 MB, ~500 instances, 30-50 sessions each); `longmemeval_oracle.json` is
evidence sessions only and is the fast loop.

- The corpus lists haystack sessions **out of chronological order**. `corpus.py`
  sorts by timestamp once, so session `idx`, `NEXT` edges and recency agree.
- **Gold answers are sometimes numbers**, not strings. Coerced in `to_instance`.
- Abstention instances carry an `_abs` suffix on `question_id` (30 of 500 in the
  oracle split).

## Known gaps as of slice 06

Real, measured, and owned by a later slice. None of these are mysteries — they
are debts with an address. Prune a line when its slice closes it.

- **Retrieval is still ungated.** Gates 1–2 decide *whether* to answer; once
  they pass, `answer.py` still feeds the whole instance subgraph to the model.
  Narrowing what gets retrieved is slices 08–09.
- **Gate 2 checks that a predicate slot is filled, never what is in it.** Live on
  `gpt4_2655b836`: "What is my sister's name?" passes because the instance's only
  `name` fact is the mis-slotted `silver Honda Civic`. Not fixable inside the
  gate — it checks shape by construction. Pinned in
  `test_gates.py::test_the_gate_cannot_see_a_mis_slotted_value`.
- **The alias closure is still unproven on real data.** `ALIASES_FOR_INSTANCE`
  returns nothing on every instance measured so far, so gate 1's alias path is
  proven synthetically or not at all.
- **The citation check is lenient.** An answer survives if *any* cited id is in
  the retrieved set; invented ids are filtered out rather than being fatal.
  Slice 10 has to decide whether a fabricated id sitting beside a valid one is
  still `uncited_answer`.
- **Entity resolution is unexercised, and gate 1 works anyway.** `alias_pairs`
  produced **0** `ALIAS_OF` edges across 41 real sessions; 96.9% of facts sit on
  `person:user`. Slice 07 verified `unknown_entity` on a live node — it fires on
  `maya chen` and `priya` against a graph holding one entity — so the gate is
  load-bearing precisely *because* the graph is that sparse. The alias path
  itself remains synthetic-only.
- **Fact precision is not measured.** Slice 06 closed schema validity (100%,
  38/38) and grounding (96.9% — an automatic *floor* that catches invented
  quotes and nothing else). Precision needs a human reading
  `docs/extraction-review.md`, and that tally is unfilled. Do not quote a
  precision figure for this pipeline; there isn't one.
- **The extractor attributes assistant suggestions to the user.** Instance
  `ec81a493` filed three `prefers` facts quoting the assistant's own advice
  ("Choose a harmonious frame"), which the prompt forbids outright. Grounding is
  no defence — those three were flagged only because the quote was reflowed, and
  an exact copy of assistant text is grounded by definition. Slice 10's citation
  check is the only place it can be caught; owned there.
- **Predicate assignment is unreliable** — e.g. `budget: '2-Day General
  Admission'`, and measured again in slice 06 as `name: 'silver Honda Civic'`.
  Because only functional predicates chain, a mis-slotted value can supersede a
  real one, so extraction noise is *amplified* by the chain rather than diluted
  by it, and the retraction is pinned in
  `test_chain.py::test_a_mis_slotted_functional_predicate_retracts_a_true_fact`.
  The hand-check sheet marks these `P` and counts them as unsupported. Slice 07
  confirmed the consequence live rather than closing it: gate 2 passes on a
  `name` fact whose value is a car, and no gate can catch that.
- **The controlled vocabulary was confirmed adequate, not extended.** `other`
  takes 24.4% of facts; the off-vocabulary tail has no cluster worth a new
  predicate. Extending it costs a node wipe, so it stays as it is.
- **A restatement moves `valid_from` forward.** The newest identical assertion
  becomes the current fact, so "since when has X been true" answers the latest
  mention, not the first. Slice 08 must decide whether an unchanged value should
  keep the earliest `valid_from`.
- **Vague dates collapse to assertion time.** `resolve_valid_from` parses numeric
  shapes only; "last summer" falls back to the session timestamp. Slice 08.
- **Same-turn ties are arbitrary.** Two facts sharing entity, predicate and turn
  are ordered by node id — deterministic across processes, but not meaningful.
- **`client.IdempotencyConflict` cannot fire** on the Cypher path. It is a
  deliberate net, not live protection; see the connection facts above.
- **The statement inventory probes with empty rows**, so it proves a statement
  parses, not that it runs. Edge statements in particular pass the probe and then
  fail on real rows if an endpoint is missing.

## Gate cascade

`gates.run()` runs gates in order and short-circuits on the first failure, so a
question already lost costs no further round trips. `gates.facts_reader()`
memoises the per-entity fetch, so a later gate re-reading the same entity is
free. Adding a gate is appending to that cascade.

- **A predicate name's glue words are not part of its meaning.** Matching any
  word of `subscribes_to` meant every question containing "to" wanted it, so
  gate 2 found a held predicate on almost anything and silently stopped firing.
  Found by probing a live graph; the unit tests all passed. Cues match on word
  boundaries for the same reason — `"work" in "homework"` is true.
- **Probe a new gate against a real ingested instance before believing it.**
  A gate that never fires is indistinguishable from a gate that is broken, and
  both of this slice's real bugs were invisible to the suite.

## Traps

- **Never let a probe measure its own cache.** Both throughput and latency
  measurements silently reported cache speed once (166,330 RPM; 1M tokens in
  0.0s). Uncached calls need a nonce; real latencies persist to a sidecar.
- **`.env` is gitignored and the repo is public.** A key in git history survives
  deletion.
- **No commit may predate Aug 12 2026** — it is a disqualification criterion.
- Report only within-harness comparisons. Published LongMemEval figures were
  measured with different answering models, embedders and judges; cite them
  separately and never claim to beat them.
- HydraDB is AGPL-3.0 and is reached over Bolt as a separate service, never
  linked as a library. This repo ships Apache-2.0.
