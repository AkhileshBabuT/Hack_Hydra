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
./.venv/Scripts/python.exe -m pytest -q   # full suite
./.venv/Scripts/python.exe -m pytest tests/test_statements.py -q   # Cypher parse check
./.venv/Scripts/python.exe scripts/probe_budget.py                 # regenerate docs/budget.md
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
  corpus.py       LongMemEval loader: streaming, whole sessions, epoch timestamps
  extract.py      session-level extraction, controlled predicates, output repair
  ingest.py       pure row builder + batched guarded writes
  answer.py       retrieve -> answer -> citation check -> abstain
scripts/probe_budget.py   slice 02 measurement gates -> docs/budget.md
scripts/fetch_corpus.py   corpus download into data/ (gitignored)
scripts/ingest_one.py     slice 03 demo: one instance, corpus -> answer
docs/hydradb-notes.md     rough edges found; upstream issue candidates
docs/budget.md            measured limits (generated, do not hand-edit)
```

## Non-negotiable conventions

**Every Cypher statement goes in `statements.py` and its `INVENTORY`.** The
inventory is executed against a live node by `tests/test_statements.py`. HydraDB
rejects unsupported syntax at parse time and `EXPLAIN` is unreachable over Bolt,
so this test is the only early warning that exists. Adding a query anywhere else
means nobody validates it.

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
places and the parser is the only authority. This project is indexed too, but at
four modules it is small enough that Read and Grep are usually faster.

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
the guard property may not itself be create-only. Replaying a row with an older
guard value leaves all properties untouched — which is what makes an out-of-order
re-ingest safe.

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
