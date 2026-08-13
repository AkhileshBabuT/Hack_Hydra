# HydraMem — Hack Hydra Track 3 Implementation Plan

> **A memory graph that can say no.**
> Bitemporal agent memory on HydraDB, with abstention as a first-class result.

---

## 0. How to use this document (instructions for the coding agent)

You are building a hackathon submission that must be **complete and working**, not
maximally featured. Read this entire file before writing code.

**Working rules:**

1. Work **phase by phase**. Do not start Phase N+1 until every acceptance gate in
   Phase N passes. The gates are not suggestions; they are the plan.
2. **Commit after every passing gate**, with a message naming the gate.
   Example: `feat(ingest): gate 2.3 — single instance ingests and answers`.
3. **No commits dated before August 12, 2026.** This is a disqualification criterion.
   If you initialize the repo, initialize it now, not with backdated history.
4. When something in the HydraDB API does not behave as this document assumes,
   **do not silently work around it**. Record it in `docs/hydradb-notes.md`, implement
   the documented fallback, and open an upstream issue at
   `github.com/hydra-db/hydradb/issues`. Upstream issues and PRs are a judging asset.
5. **Feature freeze is end of Day 7 (Aug 19).** Day 8 is verification and submission only.
   The official guide's closing advice is literally "stop adding features before the
   deadline; test what you already built."
6. Prefer boring, testable code. Every retrieval decision must be explainable by a
   returned reason string, not by an LLM's opinion.

---

## 1. Mission and the thesis we are betting on

### The competition context

| Item | Value |
|---|---|
| Track | 03 — Memory + Context Retrieval |
| Build window | Aug 12–20, 2026 (we start Aug 13 — **8 days**) |
| Deadline | Aug 20, 11:59 PM PT (target submission: **Aug 20, 6:00 PM PT**) |
| Prizes | $5,000 Grand Champion / $3,000 Runner-Up / $1,500 Third / $500 Best Use of HydraDB |
| Judging | Technical execution, use of HydraDB and graph-native approaches, product completeness, quality of results, originality |

### The thesis

The track brief states the problem precisely: long-context models drop 30–60% in
accuracy on this task, **mostly by failing at abstention** — not knowing when the
answer simply is not in the history.

Most teams will build a better retriever and report one accuracy number. We are
building a system where **abstention is the product**:

- A four-gate structural cascade decides whether the graph *can* answer before any
  LLM is asked to try.
- Every gate that fires returns a machine-readable reason (`unknown_entity`,
  `no_such_relation`, `no_connecting_path`, `no_fact_in_window`).
- Answers that pass the gates must cite fact IDs that exist in the retrieved
  subgraph, or they are downgraded to abstention.
- We report **abstention precision and recall as headline metrics**, next to accuracy,
  broken out per question category.

The second bet is **bitemporality as graph structure**. Facts are never mutated. An
update creates a new `Fact` node plus a `SUPERSEDES` edge. "What is X now" and "what
did X used to be" become two different traversals of the same chain. That single
decision handles LongMemEval's `knowledge-update` and `temporal-reasoning` categories
— the two hardest — by construction rather than by prompt engineering.

The third bet is **shipping a drop-in replacement**. A mem0-compatible API
(`add` / `search` / `get_all` / `history` / `delete`) means a judge can swap us into an
existing agent in two lines. Cheap to build, enormous signal for "product completeness."

---

## 2. What HydraDB actually gives us (and how we will use each thing)

This section is the source material for `WHY_HYDRADB.md`. Every row must end up
mapped to real code.

| HydraDB capability | Where we use it | What we lose without it |
|---|---|---|
| `algo.MSpaths` — batched multi-source/multi-target path resolution sharing selector hydration, topology, reverse pruning, and adjacency across pairs | `retrieve/paths.py` — all candidate anchor entities resolved in **one** call instead of N round trips | N× Bolt round trips per question; the whole latency story |
| `algo.SPpaths` / `algo.SSpaths` | Single-anchor fast path and the `explain()` trace | Slower explain path |
| **Bulk guarded merge** (incoming metadata must be strictly newer; create-only properties preserved) | `ingest/writer.py` — a replayed session can never move a fact's `asserted_at` backward | Correctness bug on re-ingest; the whole `knowledge-update` category becomes order-dependent |
| **Idempotency keys on mutations** (caller-scoped, reusing a key with different content is an explicit conflict) | `ingest/writer.py` — key = `sha256(instance|session|turn|fact)` | Crash-resume duplicates facts and corrupts supersession chains |
| **Causal reads with bookmarks** | `api/mem0_compat.py` — `add()` returns a bookmark, `search()` accepts it, so an agent reads back what it just wrote | Read-your-own-writes violation — a real, demoable agent-memory bug |
| **Strong reads** | `eval/run.py` — evaluation pins strong consistency so scores are reproducible | Flaky benchmark numbers |
| **Per-edge-type immutable CSC index generations + WAL overlay** | Schema deliberately limited to **6 edge types**, with the hot traversal types isolated | Sparse, useless compiled matrices; traversal falls back to canonical scans |
| **Snapshot-pinned queries** (metadata and topology never mix sequences) | Time-travel demo: answer the same question at two storage sequences | The before/after merge demo |
| **Epoch-keyed native path result cache** | Repeated identical retrieval within a session is free; mutation naturally invalidates | Wasted compute on eval reruns |
| **Prometheus metrics + query fingerprints, access paths, cache outcomes, planner decisions** on admin `:9090` | `eval/report.py` scrapes them into the cost/latency table | No cost story; Track 3 explicitly asks for "read and write cost that would survive real usage" |
| **Namespace / graph / cell scope hierarchy** | One graph per benchmark split; multi-tenant demo uses namespace-per-user | The production multi-tenancy story |

### Constraints we must design around

- **OpenCypher is a subset.** Supported: typed relationships, bounded variable-length
  paths, property and label predicates, ordering, pagination, aggregation,
  `OPTIONAL MATCH`, `UNION`, batched `UNWIND` writes. Assume nothing beyond this
  without testing it first.
- **No explicit multi-statement transactions.** Each accepted mutation commits as one
  bounded server operation. Ingest must therefore be **idempotent batched writes**, not
  transactional units. Design for at-least-once delivery.
- **Keep edge types few.** CSC generations are built per edge type. Forty edge types
  means forty sparse, cold matrices.
- **HydraDB is AGPL-3.0.** We talk to it **over Bolt/HTTP as a separate service** and
  never link it as a library. Our repo ships **Apache-2.0**, and the README explains
  this choice explicitly.
- The repo is young (~28 commits at open-sourcing). Expect rough edges. Budget for them.

---

## 3. Environment setup (Phase 1)

### 3.1 Prerequisites

HydraDB requires **Rust 1.91+**, a C/C++ toolchain, `libcypher-parser`, and
SuiteSparse GraphBLAS.

**Ubuntu / WSL:**

```bash
sudo apt-get update
sudo apt-get install -y \
  build-essential clang libclang-dev cmake pkg-config \
  libcypher-parser-dev libgraphblas-dev \
  curl git python3 python3-venv
```

**macOS:**

```bash
xcode-select --install
brew install just cmake pkg-config llvm suite-sparse
brew install cleishm/neo4j/libcypher-parser   # NOT in homebrew-core; the tap is required
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh   # not `brew install rustup`
```

### 3.2 Build and verify HydraDB

```bash
git clone https://github.com/hydra-db/hydradb.git ~/hydradb
cd ~/hydradb
just native-check    # verifies cypher-parser and GraphBLAS are discoverable
just smoke           # local object-store write, traversal, reopen, verify
```

### 3.3 Run the local node

```bash
mkdir -p .hydradb/store .hydradb/cache
printf '%s\n' 'local-development-token-32-bytes' > .hydradb/auth-token

export CLOUD_PROVIDER=local
export LOCAL_PATH="$PWD/.hydradb/store"
export GRAPH_NAMESPACE=default
export GRAPH_ID=default
export GRAPH_CELL_ID=cell-0
export GRAPH_CELLS=cell-0
export GRAPH_NODE_ID=node-0
export GRAPH_BOLT_NODE_ADDRESSES=node-0=127.0.0.1:7687
export GRAPH_ADVERTISED_BOLT_ADDR=127.0.0.1:7687
export GRAPH_DATA_CACHE_DIR="$PWD/.hydradb/cache"
export GRAPH_AUTH_TOKEN_FILE="$PWD/.hydradb/auth-token"
export GRAPH_ALLOW_PLAINTEXT=true

# CRITICAL: without this the node serves /readyz and then aborts on the first query
export RUST_MIN_STACK=33554432

if command -v brew >/dev/null; then
  export BINDGEN_EXTRA_CLANG_ARGS="-I$(brew --prefix)/include"
  export LIBRARY_PATH="$(brew --prefix)/lib"
fi

cargo run --locked --features server-runtime --bin graph-node
```

The node holds the foreground. That is it working, not hanging. Ports:
Bolt `127.0.0.1:7687`, HTTP `127.0.0.1:8443`, admin/metrics `127.0.0.1:9090`.

Put all of this in `scripts/hydradb-up.sh` in **our** repo on day one so the setup is
one command for a judge.

### 3.4 Known failure modes — put these in our README troubleshooting section

| Symptom | Fix |
|---|---|
| `No available formula with the name "libcypher-parser"` | Use the tap: `brew install cleishm/neo4j/libcypher-parser` |
| `command not found: rustup-init` | Homebrew's rustup is keg-only; use the official installer |
| `invalid environment variable CLOUD_PROVIDER value 'null'` | `CLOUD_PROVIDER` unset. `local` also needs an existing `LOCAL_PATH` directory |
| `'cypher-parser.h' file not found` | `BINDGEN_EXTRA_CLANG_ARGS` unset on macOS when invoking cargo directly |
| Node answers `/readyz`, then `has overflowed its stack` on first query | `RUST_MIN_STACK=33554432` |
| `curl: (7) Failed to connect ... port 9090` | Node not running; it holds the foreground, so use a second shell |

### 3.5 Our client stack

Python 3.11+, connecting over **Bolt** with the official `neo4j` driver using a routing
URI (`neo4j://127.0.0.1:7687`). Direct `bolt://` is for diagnostics only.

```
neo4j>=5.0        # Bolt driver — HydraDB is Bolt 5.1–5.4 compatible
fastapi, uvicorn  # mem0-compatible API
pydantic          # strict extraction schemas
openai            # NVIDIA NIM is OpenAI-compatible — see §3.6
                  #   base_url = https://integrate.api.nvidia.com/v1
ollama            # EMERGENCY FALLBACK ONLY (Nemotron 3 Nano 4B) — see §3.7
rank-bm25, faiss-cpu, sentence-transformers   # baselines only, all run locally
pytest, rich, pandas, tiktoken                # tiktoken for pre-flight token counting
```

### 3.6 Zero-budget constraint — READ THIS BEFORE DESIGNING ANY LLM CALL

**Hard rule: this project must cost $0. No paid API tiers, no cloud hosting, no
managed services.**

**What is already free and needs no thought:**

| Component | Why it's free |
|---|---|
| HydraDB | Runs locally with `CLOUD_PROVIDER=local`. No S3, no cloud |
| LongMemEval / BEAM | Public research datasets |
| Every Python dependency | Open source |
| Embeddings (vector baseline) | `sentence-transformers` runs locally on CPU |
| CI | GitHub Actions is free on public repos |
| Demo video | OBS Studio + DaVinci Resolve (free tiers), unlisted YouTube |
| Deployment | **Skip it.** The form says "deployed project link, *if you have one*." It is optional |

**All LLM inference runs on NVIDIA NIM (`build.nvidia.com`), free tier.**

This is the decision that makes the whole plan work. NVIDIA's free tier is governed by a
**rate limit (~40 RPM), not a daily request cap** — which removes the constraint that
would otherwise force us to shrink the evaluation. Endpoints are OpenAI-compatible at
`https://integrate.api.nvidia.com/v1`, so the client is the standard `openai` SDK with a
changed `base_url`.

Three further reasons this beats the alternatives:

- **The terms of service fit exactly.** NVIDIA permits "development, testing, research or
  evaluation" and restricts only *production* use serving real end-users. A benchmark
  extraction and evaluation run is squarely inside the permitted category. (This is also
  a reason not to ship a live hosted demo — keep the demo local and recorded.)
- **NVIDIA states it does not train on prompts or responses.** Gemini's free tier does.
- **Same open-weight family end to end.** Every model in the stack is open-weight under
  a permissive license. Combined with an AGPL database at an open-source hackathon, this
  is a narrative asset — say it in the video.

### 3.7 Model assignment

| Role | Model | Where | Why |
|---|---|---|---|
| **Fact extraction** (high volume) | **Nemotron 3.5 Lightning** (30B total / 3B active), reasoning **OFF** | NIM hosted | NVIDIA's own framing is "tool calls, validation, and formatting" — that *is* fact extraction. 3B active params = throughput |
| **Answer synthesis** (1 call/question) | Nemotron 3.5 Lightning, or Nemotron 3 Super if quality demands | NIM hosted | Low volume; use the same model as the baseline arm |
| **Full-context baseline** | **Nemotron 3 Ultra** if free-tier accessible, else Nemotron 3 Super | NIM hosted | See §8.2 — a strong baseline is a credibility requirement |
| **Emergency fallback** | Nemotron 3 Nano 4B (~5GB) | Local, Ollama | Only if NIM throttles or deprecates a model mid-hackathon |

**Why extraction is hosted, not local.** An earlier draft of this plan ran extraction on
a local model because the then-planned provider had a hard daily request cap. NVIDIA's
rate-limit-only model removes that constraint, and hosted is strictly better: it runs
unattended, does not compete with the dev machine, and is faster.

**Do not attempt to run Lightning 30B locally on a 16GB laptop.** MoE reduces *compute*
per token, not *memory* — all 30B parameters must be resident regardless of how few
activate. At Q4 that is ~17–18GB against ~13.8GB usable, and an integrated GPU (e.g.
Radeon 780M) carves its VRAM out of that same pool. It will thrash the page file and be
useless. The local fallback is **Nano 4B only**.

### 3.8 Rules for every LLM call

- **Verify the real context ceiling on Day 1** (Gate 1.6). Published context windows are
  *model capabilities*, not endpoint limits. Nemotron 3 is advertised at 1M tokens, but a
  community test against `build.nvidia.com` found the deployed NIM API enforcing ~131K,
  and OpenRouter's route caps output generation at 64K. **Send a real ~120K-token request
  and see what comes back.** Never infer this from a spec sheet — the full-context
  baseline depends on it.
- **Pre-flight token counting.** Count every full-context request before sending. Route
  anything over the measured ceiling to the overflow path (§8.2) rather than discovering
  it as a runtime error mid-run.
- **Exponential backoff on everything** (1s, 2s, 4s, 8s). The rate limit is documented as
  varying with model, use case, and current overall traffic — treat ~40 RPM as a ceiling
  you approach, not a guarantee.
- **Reasoning OFF for extraction.** Nemotron 3 exposes reasoning ON/OFF plus a thinking
  budget. Reasoning models emit thinking tokens that break strict JSON parsing — one
  published study saw 80% of outputs fail to parse for this reason. Turning it off is the
  single highest-value setting in the pipeline.
- **Provider stays swappable.** All model access goes through `hydramem/llm.py`. Gemini
  Flash (1M context) is the documented fallback for the overflow tail and for total NIM
  outage. Swapping providers must be a one-line change.
- **Verify account state on Day 1.** Sources conflict on whether NIM still applies signup
  credits (reports range from "1,000/5,000 credits, HTTP 402 on exhaustion" to "credit
  limits removed"). Check your own dashboard and record what you find in `docs/budget.md`.

**The methodological rule that keeps this rigorous:**
> Use the **same answering model** across all four arms — full-context, vector RAG,
> mem0, and HydraMem. Only the retrieval layer differs.

**Caching is a budget mechanism, not an optimization.** Cache to disk, keyed by content
hash: every extraction, every baseline answer, every synthesis output. Reruns must cost
zero calls. Commit the caches where dataset licenses allow.

**Model freshness caveat.** Nemotron 3.5 Lightning was released **August 11, 2026** — two
days before this build starts. Its published numbers (86% PinchBench, Intelligence Index
placement) come from NVIDIA's own launch materials with no independent replication, and
nobody has benchmarked it on structured extraction specifically. **Measure it yourself on
a 20-instance slice (Gate 2.5) before committing the full corpus.**

**Free credits:** ask in the Hack Hydra Discord whether sponsors are offering API credits.

---

## 4. The graph schema

Deliberately small. **6 edge types.** Every node carries `instance_id` (the benchmark
question instance / tenant) and it is property-indexed.

### 4.1 Node labels

**`Entity`** — a canonical subject or object.
```
key         string   normalized identity, e.g. "person:maya"   [INDEXED]
name        string   surface form for display
type        string   person | org | place | thing | preference | event
instance_id string   tenant partition                          [INDEXED]
first_seen  int      session index of first mention
```

**`Fact`** — a reified, immutable assertion. Never updated in place.
```
id           string  ULID                                       [INDEXED]
predicate    string  e.g. "employer", "lives_in", "prefers"     [INDEXED]
value_text   string  literal value when the object is not an Entity
value_type   string  literal | entity
valid_from   int     epoch seconds — when the fact became true in the world
valid_to     int     epoch seconds or 0 for open — bitemporal axis 1
asserted_at  int     epoch seconds — when it was said            (bitemporal axis 2)
session_id   string  provenance
turn_idx     int     provenance
snippet      string  <=240 chars of the source turn, for the explain trace
confidence   float   0..1 from the extractor
status       string  current | superseded          [INDEXED]  (O(1) shortcut for the chain)
instance_id  string                                 [INDEXED]
```

**`Session`** — a conversation session.
```
id           string  [INDEXED]
idx          int     ordinal position in the history
timestamp    int     epoch seconds
instance_id  string  [INDEXED]
```

### 4.2 Edge types (exactly six — do not add a seventh without deleting one)

| Type | From → To | Purpose |
|---|---|---|
| `SUBJECT` | Fact → Entity | who/what the fact is about — **hot traversal type** |
| `OBJECT` | Fact → Entity | entity-valued object — **hot traversal type**, enables multi-hop |
| `SUPERSEDES` | Fact → Fact | newer fact replaces older; the bitemporal chain |
| `ASSERTED_IN` | Fact → Session | provenance |
| `NEXT` | Session → Session | chronological chain, so recency is structure not a sort |
| `ALIAS_OF` | Entity → Entity | surface-form resolution |

**Why this matters for HydraDB specifically:** `SUBJECT` and `OBJECT` carry essentially
all traversal load, so their per-edge-type CSC generations stay dense and the compiled
GraphBLAS topology is actually worth compiling. State this in `WHY_HYDRADB.md`.

### 4.3 Bootstrap

`hydramem/schema.py` creates property indexes on every field marked `[INDEXED]` and
exposes `bootstrap(driver, instance_id)`. Indexes matter twice over here: `MSpaths`
resolves its `sourceValues` through indexed selectors, so an unindexed `Entity.key`
silently degrades the hot path to a scan.

---

## 5. Ingest pipeline

### 5.1 Extraction (`ingest/extract.py`)

**Window at the SESSION level, not the turn level.** Send one whole session per call to
**Nemotron 3.5 Lightning on NIM** (reasoning OFF, see §3.7) with a strict Pydantic JSON
schema and no prose. Session-level windowing cuts call volume 4–5x versus 3-turn windows,
and a 1M-context model holds a full session comfortably — coreference resolution is
*better* with the whole session in context, not worse.

Only fall back to sliding 3-turn windows if a session exceeds the measured context
ceiling, or if Gate 2.5 shows session-level extraction quality is materially worse.

```python
class ExtractedFact(BaseModel):
    subject: str            # surface form
    predicate: str          # snake_case, from a controlled vocabulary + "other"
    value: str
    value_is_entity: bool
    valid_from_hint: str | None   # "2024-03", "last summer", None
    confidence: float
    evidence_span: str      # <= 240 chars, verbatim from the turn
```

**Throughput budget.** At session-level windowing the arithmetic is comfortable:

| Windowing | Calls (150 questions) | Wall-clock at ~40 RPM |
|---|---|---|
| 3-turn windows | 7,500–22,000 | 3–9 hours |
| **Session-level (default)** | **1,500–4,000** | **under 2 hours** |

An unattended afternoon run, with the dev machine free to keep building. This is the
payoff from hosting extraction rather than running it locally.

**Control rules — mandatory, not optional:**

- **Reasoning OFF.** Thinking tokens break strict JSON. Highest-value single setting.
- Cache every extraction to `.cache/extract/{sha256(window_text)}.json`. Reruns are free.
- **Idempotent and resumable.** A dropped connection at hour two must resume from the
  cache, not restart. This is what makes a multi-hour unattended run safe.
- Exponential backoff on every call; ~40 RPM is a ceiling to approach, not a guarantee.
- Run against the **stratified dev slice** (§8.2) until Gate 5.4. Only then scale.
- Controlled predicate vocabulary — an open vocabulary destroys retrieval precision
  because the predicate gate can't match anything, and it lets the extractor wander.

### 5.2 Entity resolution (`ingest/resolve.py`)

Keep this cheap. Track 3's hard part is **time**, not entity resolution — that is
Track 1's problem. Do not over-invest.

1. Normalize: lowercase, strip honorifics/punctuation, collapse whitespace.
2. Key = `{type}:{normalized}`.
3. Exact key match → same entity.
4. Fuzzy (token-set ratio > 0.92) or a first-name/full-name containment match → create
   an `ALIAS_OF` edge rather than merging. Reversible, auditable, and it costs nothing.

### 5.3 Writing (`ingest/writer.py`)

**Batched `UNWIND`, 500 rows per statement.** Never write one fact per round trip.

```cypher
UNWIND $rows AS row
MERGE (e:Entity {key: row.subject_key, instance_id: $instance})
  ON CREATE SET e.name = row.subject_name,
                e.type = row.subject_type,
                e.first_seen = row.session_idx
CREATE (f:Fact {
  id: row.fact_id, predicate: row.predicate, value_text: row.value,
  value_type: row.value_type, valid_from: row.valid_from, valid_to: 0,
  asserted_at: row.asserted_at, session_id: row.session_id,
  turn_idx: row.turn_idx, snippet: row.snippet,
  confidence: row.confidence, status: 'current', instance_id: $instance
})
CREATE (f)-[:SUBJECT]->(e)
```

Then a second pass links `OBJECT`, `ASSERTED_IN`, and `NEXT`.

**Idempotency key** on every mutation:
`sha256(f"{instance_id}|{session_id}|{turn_idx}|{predicate}|{subject_key}|{value}")`.
Re-running ingest after a crash must be a no-op. Reusing a key with different content
returns an explicit non-retryable conflict from HydraDB — surface that as a loud error,
never swallow it.

### 5.4 Supersession (`ingest/supersede.py`)

After each session's facts land, for every `(subject_key, predicate)` pair touched:

```cypher
MATCH (new:Fact {id: $new_id})-[:SUBJECT]->(e:Entity {key: $key, instance_id: $inst})
MATCH (old:Fact {predicate: $pred, status: 'current', instance_id: $inst})-[:SUBJECT]->(e)
WHERE old.id <> $new_id AND old.asserted_at < $new_asserted_at
SET old.status = 'superseded', old.valid_to = $new_valid_from
CREATE (new)-[:SUPERSEDES]->(old)
```

The `old.asserted_at < $new_asserted_at` predicate is the **guarded merge** discipline:
a delayed replay can never move a timestamped fact backward. Add a regression test that
ingests two sessions in reverse order and asserts the same final state.

**Acceptance:** ingesting sessions in any permutation yields an identical
supersession chain.

---

## 6. Retrieval and the abstention cascade — the core differentiator

`retrieve/gates.py` implements a four-gate cascade. **No LLM is asked to answer until
all four gates pass.** Every gate returns a structured reason.

```python
@dataclass
class Retrieval:
    status: Literal["answerable", "abstain"]
    reason: str | None      # unknown_entity | no_such_relation
                            # no_connecting_path | no_fact_in_window
                            # uncited_answer
    detail: dict            # which entity, which predicate, which window
    facts: list[Fact]
    paths: list[Path]
    latency_ms: dict        # per-stage
```

### Gate 1 — Entity gate (`unknown_entity`)

Parse the question's named entities (a cheap LLM call with a strict schema, cached).
Resolve each against `Entity.key` and the `ALIAS_OF` closure for this `instance_id`.
Any required entity absent → **abstain**, `detail={"missing_entity": "..."}`.

This single gate catches a large share of LongMemEval's abstention instances, at
near-zero cost, with no hallucination surface at all.

### Gate 2 — Predicate gate (`no_such_relation`)

The entity exists. Does it have any `Fact` with the required predicate (or a
vocabulary-mapped neighbor)?

```cypher
MATCH (f:Fact {instance_id: $inst})-[:SUBJECT]->(e:Entity {key: $key})
WHERE f.predicate IN $predicates
RETURN f ORDER BY f.asserted_at DESC LIMIT $k
```

Empty → **abstain**, `detail={"entity": key, "missing_predicate": pred}`.

### Gate 3 — Path gate (`no_connecting_path`)

For multi-hop questions, this is the **one MSpaths call** that replaces N round trips:

```cypher
CALL algo.MSpaths({
  sourceLabel: 'Entity',
  sourceProperty: 'key',
  sourceValues: $anchor_keys,
  targetValues: $target_keys,
  pairwise: true,
  relTypes: ['SUBJECT', 'OBJECT'],
  relDirection: 'both',
  maxLen: 4,
  pathCount: 8,
  fairRelationshipVariants: true,
  resultLimit: 200
})
YIELD path
RETURN path
```

`fairRelationshipVariants: true` matters: it distributes the result budget across
structural paths so one hyper-connected entity cannot consume the whole response.
Zero paths within `maxLen` → **abstain**, `detail={"pairs_tried": n, "max_len": 4}`.

### Gate 4 — Temporal gate (`no_fact_in_window`)

Facts exist, but the question asks about a window (`"when I was at Acme"`,
`"before I moved"`). Resolve the window, then filter on the bitemporal axes:

- **Current value:** `status = 'current'`, or equivalently the head of the
  `SUPERSEDES` chain.
- **Historical value at time T:** `valid_from <= T AND (valid_to = 0 OR valid_to > T)`.
- **Change history:** walk `SUPERSEDES` and return the ordered sequence.

No fact satisfies the window → **abstain**, `detail={"window": [t0, t1]}`.

### Synthesis and the citation check (`retrieve/answer.py`)

Only now call the LLM, and give it **only** the retrieved fact subgraph plus:

> Answer using only the numbered facts below. Every claim must cite a fact ID.
> If the facts do not answer the question, output exactly `ABSTAIN`.

Then **verify**: parse cited IDs out of the answer. If the answer makes a substantive
claim with no citation, or cites an ID not in the retrieved set, downgrade to
`abstain` with reason `uncited_answer`.

This turns abstention from a prompt hope into a structural property. That sentence goes
in the demo video.

### `explain()`

Every answer returns the fact path, the source session and turn, the timestamp, and the
gate trace. Judges can audit any answer in five seconds. Build this on day 3, not day 7
— it is also your primary debugging tool.

---

## 7. The mem0-compatible API

`api/mem0_compat.py`, served by FastAPI. Two-line drop-in replacement:

| Method | Signature | Notes |
|---|---|---|
| `add` | `add(messages, user_id, metadata=None) -> {ids, bookmark}` | Returns a HydraDB **bookmark** |
| `search` | `search(query, user_id, limit=10, bookmark=None) -> Retrieval` | Bookmark enforces read-your-own-writes via causal consistency |
| `get_all` | `get_all(user_id) -> list[Fact]` | Current facts only |
| `history` | `history(memory_id) -> list[Fact]` | **Walks `SUPERSEDES`. mem0 cannot do this.** |
| `delete` | `delete(memory_id)` | Tombstone, not destruction |
| `explain` | `explain(answer_id) -> Trace` | **Ours. The differentiator.** |

The `bookmark` parameter is worth a dedicated slide in the video: an agent that writes a
memory and immediately searches for it gets it back, guaranteed, because HydraDB refreshes
the reader until the bookmark's storage sequence is visible before pinning the snapshot.
That is a real correctness property that vector-store memory layers simply do not have.

---

## 8. Evaluation harness

### 8.1 Datasets

**Primary: LongMemEval_S** (~115k tokens/question, 30–50 sessions) —
`github.com/xiaowu0162/LongMemEval`, MIT.

**Optional, only if Day 6 finishes early: BEAM** — `github.com/mohammadtavakoli78/BEAM`,
CC BY-SA 4.0. It tests abstention and contradiction resolution explicitly, so it is the
most on-thesis secondary benchmark.

**Do not use LongMemEval-V2.** It is a multimodal web/enterprise-agent benchmark, not a
chat-memory benchmark — a materially different task that would cost days for no gain.

**Sampling.** Build a **category-stratified slice of ~150 questions** and use it for all
head-to-head comparisons. Stratified beats random here: you need enough n in the
abstention, temporal-reasoning, and knowledge-update buckets to say anything, and those
are exactly the buckets that carry the thesis. Record n per category in every table.

### 8.2 Baselines

**Controlling rule: the same answering model is used for every arm.** Only the retrieval
layer differs. State this in the README and the video.

1. **Full-context** — the whole ~115k-token history in one request. **Use the strongest
   model the free tier gives you (Nemotron 3 Ultra if accessible).** See the box below.
2. **Vector RAG** — local embeddings + FAISS over turn chunks, top-k into the same
   answering model. **Use BGE-M3 or EmbeddingGemma, not MiniLM** — published work shows
   embedding choice alone can swing a RAG baseline from 61% to 74%, which is enough to
   flip whether a memory system appears to win. Using a weak embedder here would be
   rigging the comparison, and a judge who knows the literature will see it.
3. **mem0** — cite its published number (~66% overall on LongMemEval) as context. Only
   attempt to *reproduce* it if Day 6 has slack: it is a multi-service stack and the
   setup time is real. If you don't run it, say so plainly in the README.

> **Use the strongest baseline you can get.** The single most likely attack on this
> submission is "you only beat a weak baseline." Beating a frontier long-context model
> at 1M context is hard to dismiss. The bet is defensible because the abstention failure
> is *architectural*, not a capability ceiling — larger long-context models still
> confabulate rather than saying "not in the history," which is precisely what the track
> brief asserts. **But run this arm on Day 4, not Day 6.** If Ultra beats HydraMem on
> abstention, the thesis needs days to react, not hours.

**The overflow tail.** LongMemEval_S averages ~115k tokens but has a tail. Against a
measured ceiling near 131k, some instances will not fit. Token-count every question
before the run, route overflow to Gemini Flash (1M context) via `hydramem/llm.py`, and
**report the split honestly** in the README — "N of 150 questions exceeded the NIM
context ceiling and were run on Gemini Flash." Disclosed is fine; silent truncation is not.

**Publication honesty — non-negotiable.** Published LongMemEval figures (full-context
GPT-4o ~60–64%, Mem0 ~66%, Zep 71.2%, Hindsight 83.6%) were measured with different
answering models, embedders, and judges. **Do not claim to beat them.** Report only
within-harness comparisons, then cite the published numbers separately, clearly labelled
as measured under different conditions. Being the team that names this confound is a
credibility gain; being caught ignoring it is fatal.

### 8.3 The results table — this is what wins "quality of results"

Break out **by question category**. An aggregate number is forgettable; a per-category
table where your abstention column crushes both baselines is not.

| Category | Full-context | Vector RAG | mem0 | **HydraMem** |
|---|---|---|---|---|
| single-session-user | | | | |
| single-session-assistant | | | | |
| single-session-preference | | | | |
| multi-session | | | | |
| temporal-reasoning | | | | |
| knowledge-update | | | | |
| **abstention (precision)** | | | | |
| **abstention (recall)** | | | | |
| **Overall** | | | | |

Plus an operating-cost table, because the brief asks for read and write cost that
survives real usage:

| Metric | Full-context | Vector RAG | **HydraMem** |
|---|---|---|---|
| Tokens / question | | | |
| p50 latency (ms) | | | |
| p95 latency (ms) | | | |
| Bolt round trips / question | | | |
| Ingest cost / session ($) | | | |
| Query cost / question ($) | | | |

**Bolt round trips per question is our best single number.** MSpaths batching should put
us at ~3 where a naive graph implementation needs dozens. Instrument it explicitly.

### 8.4 Instrumentation

`eval/report.py` scrapes HydraDB's admin endpoint at `127.0.0.1:9090/metrics` and joins
it with our own timings. **Read `docs/runbooks/duration-histograms.md` in the HydraDB
repo before building any latency dashboard** — the Prometheus duration histograms
deliberately use different units, and getting this wrong produces confidently wrong
numbers.

Run evaluation with `"consistency": "strong"` so scores are reproducible; run the live
demo with `causal` so latency reflects the real hot path. Say this out loud in the video.

### 8.5 Fixture suite (build this on Day 3, before the real eval)

25 hand-written cases in `tests/fixtures/`, with 10 abstention cases covering each gate.
This suite runs in under 10 seconds and is your actual development loop. The full
benchmark is too slow to iterate against.

---

## 9. Repository layout

```
hydramem/
├── README.md                      # judge-facing; setup that actually works
├── WHY_HYDRADB.md                 # feature → file:line → what we'd lose
├── LICENSE                        # Apache-2.0
├── pyproject.toml
├── Makefile                       # make setup / ingest / eval / demo / verify
├── scripts/
│   ├── hydradb-up.sh              # one-command HydraDB with all env vars
│   └── download-datasets.sh
├── hydramem/
│   ├── schema.py                  # labels, 6 edge types, index bootstrap
│   ├── client.py                  # Bolt driver, bookmarks, consistency modes
│   ├── llm.py                     # provider-agnostic LLM client (NIM primary,
│   │                              #   Gemini fallback), backoff, disk cache,
│   │                              #   pre-flight token counting
│   ├── ingest/
│   │   ├── extract.py             # cached LLM extraction, strict schema
│   │   ├── resolve.py             # normalization + ALIAS_OF
│   │   ├── writer.py              # UNWIND batches, idempotency keys
│   │   └── supersede.py           # guarded merge, SUPERSEDES chain
│   ├── retrieve/
│   │   ├── gates.py               # the four-gate cascade
│   │   ├── paths.py               # MSpaths / SPpaths wrappers
│   │   ├── temporal.py            # validity windows, chain walking
│   │   └── answer.py              # synthesis + citation verification
│   ├── api/
│   │   ├── mem0_compat.py
│   │   └── server.py
│   └── eval/
│       ├── run.py
│       ├── baselines/{full_context,vector,mem0_ref}.py
│       └── report.py
├── ui/                            # single-page demo (Phase 7, timeboxed)
├── tests/
│   ├── fixtures/                  # 25 cases, 10 abstention
│   └── test_*.py
├── bench-results/                 # committed CSVs — evidence, not claims
└── docs/
    ├── architecture.md            # our design + a schema diagram
    ├── budget.md                  # verified quota/credit state, MEASURED context
    │                              #   ceiling (Gate 1.6), $0 attestation
    └── hydradb-notes.md           # rough edges found, issues filed
```

---

## 10. Day-by-day plan with acceptance gates

**8 days: Aug 13 → Aug 20.** Feature freeze end of Day 7.

### Day 1 — Aug 13 — Foundation

- Build HydraDB, get the node running, `just smoke` passes.
- Initialize our repo: Apache-2.0 LICENSE, `pyproject.toml`, `Makefile`, CI skeleton.
- `scripts/hydradb-up.sh` works from a clean shell.
- `hydramem/client.py`: Bolt connection over a routing URI, bookmark plumbing,
  consistency-mode switch.
- `hydramem/llm.py`: NIM client (OpenAI-compatible), disk cache, exponential backoff,
  pre-flight token counting, provider swap.
- `hydramem/schema.py`: create all 6 edge types and every property index.

**Gate 1.1** — `just smoke` passes in the HydraDB checkout.
**Gate 1.2** — Our smoke test writes an `Entity`, a `Fact`, and a `SUBJECT` edge over
Bolt, reads it back, and passes.
**Gate 1.3** — A write returns a bookmark; a causal read with that bookmark sees the write.
**Gate 1.4** — `curl 127.0.0.1:9090/metrics` returns data and we can parse it.
**Gate 1.5** — NIM account verified: `openai` SDK against
`https://integrate.api.nvidia.com/v1` returns a completion from Nemotron 3.5 Lightning
with reasoning OFF and schema-valid JSON on a sample session. Account credit state and
observed rate limits recorded in `docs/budget.md`. **Zero dollars spent.**
**Gate 1.6 — CONTEXT CEILING PROBE (blocking).** Send a real ~120K-token request to the
model chosen for the full-context baseline and record what comes back. Repeat at 130K and
140K to find the actual wall. Write the measured ceiling into `docs/budget.md`. The entire
baseline arm depends on this number and it **must not** be taken from a spec sheet.
**Gate 1.7** — Nemotron 3 Nano 4B pulled via Ollama and returning schema-valid JSON, as
the emergency fallback. Confirm it runs on this machine; do **not** attempt Lightning 30B
locally.

> If Day 1 slips past 8 hours on the HydraDB build, stop and use the local
> object-store path from the `examples/` directory to unblock schema work, then return
> to the server build. Do not lose a day to a toolchain.

### Day 2 — Aug 14 — Ingest end to end

- Download LongMemEval; write the loader.
- `extract.py` with disk cache; `resolve.py`; `writer.py` with UNWIND + idempotency;
  `supersede.py`.
- Ingest **one** full question instance (30–50 sessions).

**Gate 2.1** — One instance ingests with zero errors; node/edge counts are sane.
**Gate 2.2** — Re-running ingest is a **no-op** (idempotency verified by count).
**Gate 2.3** — A hand-picked single-session question is answered correctly from the graph.
**Gate 2.4** — Ingesting sessions in reverse order produces an identical supersession chain.
**Gate 2.5 — EXTRACTOR QUALITY (blocking).** On a 20-instance hand-checked slice,
session-level extraction with Lightning produces ≥95% schema-valid JSON and acceptable
fact precision. Lightning is two days old with no independent benchmarks on structured
extraction — **measure before committing the full corpus.** If it fails: first try
reasoning-OFF settings and prompt tightening, then fall back to 3-turn windows, then to
Nemotron 3 Super.

### Day 3 — Aug 15 — Abstention cascade and temporal logic

- All four gates, each with its reason string.
- `temporal.py`: current value, value-at-T, change history.
- `answer.py`: synthesis + citation verification.
- `explain()`.
- 25-case fixture suite, 10 of them abstention.

**Gate 3.1** — All 25 fixtures pass.
**Gate 3.2** — Each of the four gate reasons fires correctly on its dedicated fixture.
**Gate 3.3** — A knowledge-update fixture returns the *current* value; the same question
scoped to a past window returns the *old* value.
**Gate 3.4** — `explain()` returns the full path, session, turn, and gate trace.

### Day 4 — Aug 16 — Eval harness and the thesis test

This is the highest-stakes day in the plan. The **strongest** full-context baseline runs
today, not on Day 6, so there is time to react if the thesis is wrong.

- Build the category-stratified ~150-question slice (§8.1); record n per category.
- `eval/run.py` end to end.
- Full-context baseline on **Nemotron 3 Ultra** (or the strongest accessible model),
  with pre-flight token counting and the overflow tail routed to Gemini Flash.
- First per-category table in `bench-results/`.

**Gate 4.1** — Both arms run the stratified slice unattended, end to end.
**Gate 4.2** — A per-category table exists as a committed CSV, with n per category and
the overflow-tail split recorded.
**Gate 4.3 — THE THESIS GATE.** HydraMem beats the strongest full-context baseline on
**abstention** and **knowledge-update**.
*(If it does not, stop everything. Do not build the UI, do not write the README, do not
add features. This is the entire submission. Diagnose in this order: (a) is the predicate
gate too permissive, letting unanswerable questions through? (b) is the entity gate
missing aliases? (c) is the citation check failing to downgrade uncited answers? These
three account for nearly all abstention-precision failures.)*

### Day 5 — Aug 17 — MSpaths, multi-hop, and cost instrumentation

- Replace all per-anchor loops with batched MSpaths calls.
- Round-trip counter, latency histograms, token accounting.
- Scale ingest to the full split (run overnight if needed).

**Gate 5.1** — Multi-hop retrieval uses **one** MSpaths call; round trips per question ≤ 4.
**Gate 5.2** — `multi-session` category accuracy improves measurably vs. Day 4.
**Gate 5.3** — p50/p95 latency and tokens-per-question are recorded for all systems.
**Gate 5.4** — Full stratified slice ingested; extraction throughput (sessions/min and
calls/hour against the NIM rate limit) recorded.

### Day 6 — Aug 18 — Remaining baselines and the full numbers table

- Vector RAG baseline (**BGE-M3 or EmbeddingGemma — not MiniLM**).
- mem0: cite the published number; reproduce only if there is genuine slack.
- Optional if ahead of schedule: a BEAM subset. **Skip LongMemEval-V2 entirely.**

**Gate 6.1** — Vector RAG baseline complete on the same stratified slice, with the
embedding model named in the results table.
**Gate 6.2** — Full results table committed to `bench-results/`.
**Gate 6.3** — Cost table complete.
**Gate 6.4** — Every number in the README is reproducible by `make eval`.

### Day 7 — Aug 19 — Product surface and submission assets. **FEATURE FREEZE 11:59 PM.**

- mem0-compatible API + FastAPI server.
- Minimal single-page UI: ask a question, see the answer or the abstention reason,
  see the fact path, see the supersession chain. **Timebox to 4 hours.**
- README, `WHY_HYDRADB.md`, `docs/architecture.md`, `docs/hydradb-notes.md`.
- Record and edit the demo video.
- File any upstream HydraDB issues discovered.

**Gate 7.1** — A two-line mem0 swap works in a sample agent script.
**Gate 7.2** — Video is **under 3 minutes** and covers problem → project → demo → HydraDB.
**Gate 7.3** — `WHY_HYDRADB.md` maps every capability in §2 to a real `file:line`.
**Gate 7.4** — A clean clone on a different machine reaches a working demo using only
the README.

### Day 8 — Aug 20 — Verify and submit. **Target 6:00 PM PT, not 11:59.**

- Fresh-clone verification on a clean machine.
- Walk the guide's official pre-submission checklist item by item.
- Submit the Google Form: `forms.gle/GrMYKxLj9zPQcqqc8`.

**Gate 8.1** — Every item in §12 checked.
**Gate 8.2** — Form submitted before 6:00 PM PT.

---

## 11. Demo video script (3:00 hard limit — content after 3:00 may not be reviewed)

The guide specifies four sections in order. Follow it exactly.

**0:00–0:25 — The problem.** "Agents forget, and worse, they make things up. On
LongMemEval, long-context models lose 30 to 60 percent of their accuracy — and the
brief says it's mostly one failure: they can't tell when the answer just isn't in the
history. So they invent one."

**0:25–0:50 — The project.** "HydraMem is an agent memory layer on HydraDB where
abstention is a first-class result. Facts are never overwritten — an update creates a
new node and a SUPERSEDES edge, so time and revision are graph structure. And before we
ever ask a model to answer, four structural gates decide whether the graph *can*."

**0:50–2:10 — The demo.** Three questions, live:
1. **Knowledge-update** — "Where do I work?" → current answer, then the
   `SUPERSEDES` chain on screen showing the two previous employers and when each changed.
2. **Abstention** — a question about something never discussed → `ABSTAIN`, reason
   `no_such_relation`, and the specific entity and predicate that were missing. Then the
   full-context baseline confidently hallucinating the same question, side by side.
3. **Multi-hop** — the fact path rendered, with source session and timestamp on each hop.

**2:10–2:50 — HydraDB.** Results table on screen. "One MSpaths call resolves every
candidate anchor at once — three Bolt round trips per question instead of thirty.
Guarded merges mean a replayed session can't move a timestamped fact backward. Causal
bookmarks mean an agent reads back what it just wrote. Our schema has exactly six edge
types so the two hot ones get dense compiled CSC matrices. Without HydraDB this is a
pile of round trips and a correctness bug."

**2:50–3:00 — The close.** The two-line mem0 swap, plus the open-weight line: "Every
model in this stack is open-weight, the database is open source, and the whole thing runs
on free infrastructure. Drop it into any agent you already have."

Unlisted YouTube is fine as long as judges can open it. Verify in an incognito window.

---

## 12. Submission checklist (from the official guide)

**Repository**
- [ ] Public, no access request needed
- [ ] Open-source license present (Apache-2.0)
- [ ] **No participant-authored commits before Aug 12, 2026**
- [ ] Clear README; setup instructions that actually work on a clean machine
- [ ] Explanation of how HydraDB is used
- [ ] Dependencies and environment documented
- [ ] Attribution for datasets and borrowed code

**Video**
- [ ] 3 minutes or less
- [ ] Link accessible without login (test in incognito)
- [ ] Covers problem → project → demo → HydraDB, in that order

**Form** — `forms.gle/GrMYKxLj9zPQcqqc8`
- [ ] Project name and description
- [ ] Problem addressed
- [ ] What you built
- [ ] Deployed link (if any) — and it works
- [ ] How the project uses HydraDB
- [ ] Tech stack
- [ ] Team members and contributions (1–4 people, one team per person)
- [ ] Repo and video links

**Disqualification risks to actively verify against**
- [ ] No pre-existing work submitted
- [ ] HydraDB used meaningfully — we can state exactly what we'd lose without it
- [ ] Repo public and licensed
- [ ] Video present and accessible
- [ ] Submitted before 11:59 PM PT Aug 20

---

## 13. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| HydraDB build fails or eats Day 1 | Medium | Hard 8-hour timebox; fall back to the `examples/` local object-store path to unblock schema work; the README troubleshooting table covers the known failure modes |
| `MSpaths` rejects a parameter we assume | Medium | Fall back to bounded variable-length Cypher paths; log it in `docs/hydradb-notes.md`; file the issue upstream |
| OpenCypher subset lacks something we need | Medium | Test every construct on Day 1 against a toy graph before designing around it |
| **Real context ceiling below 115k**, breaking the full-context baseline | Medium | Gate 1.6 probes it on Day 1; pre-flight token counting; overflow tail routed to Gemini Flash (1M) and disclosed in the README |
| NIM throttles, deprecates a model, or credits turn out to apply | Medium | Exponential backoff; all access behind `hydramem/llm.py`; Gemini Flash and Nemotron 3 Nano 4B (local) as documented fallbacks; account state verified Day 1 |
| **Lightning underperforms on structured extraction** (2 days old, no independent benchmarks) | Medium | Gate 2.5 measures it on 20 instances before the full corpus; escalation path is reasoning-OFF tuning → 3-turn windows → Nemotron 3 Super |
| Ultra beats HydraMem on abstention | Low | Baseline runs Day 4, not Day 6, so there are 4 days to react; diagnostic order given in Gate 4.3 |
| Extraction run interrupted mid-flight | Medium | Idempotency keys + disk cache make it resumable, not restartable |
| Full dataset ingest too slow | Medium | Ingest a disclosed subset and publish measured throughput plus an honest projection. A stated boundary beats a hand-wave |
| Abstention gains don't materialize (Gate 4.3 red) | Low | This is the thesis — everything stops until it's fixed. Most likely cause is an over-broad predicate vocabulary letting Gate 2 pass when it shouldn't |
| Scope creep on the UI | High | 4-hour hard timebox on Day 7. The UI is a demo surface, not the product |
| Something breaks on Day 8 | Medium | Feature freeze Day 7; Day 8 is verification only; submit at 6 PM, not 11:59 |

---

## 14. What "winning" looks like on each judging criterion

| Criterion | Our evidence |
|---|---|
| **Technical execution** | Bitemporal schema, guarded merges, idempotent ingest, four-gate cascade, citation verification, reproducible eval harness |
| **Use of HydraDB and graph-native approaches** | `WHY_HYDRADB.md` mapping 12 capabilities to real code — MSpaths batching, bookmarks, guarded merges, per-edge-type CSC design, snapshot pinning. This is also the $500 Best Use play |
| **Product completeness and usability** | One-command setup, mem0-compatible drop-in, working UI, `explain()` on every answer |
| **Quality of results** | Per-category table against three baselines, plus a cost table — everything reproducible by `make eval` |
| **Originality** | Nobody else will make abstention the product. Everyone else reports one accuracy number |
| **Product completeness (bonus)** | Runs entirely on free and open infrastructure — $0 to operate, fully open-weight models, open-source database. A real operational property for a memory layer, and true |

The guide says it plainly: *"We care about working, thoughtful products, not just
benchmark scores."* So lead with the working product and let the numbers corroborate it —
not the other way around.
