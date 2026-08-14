# HydraMem

Bitemporal agent memory on HydraDB, where **abstention is a first-class result**.
Hackathon submission, Track 03 — Memory + Context Retrieval.

Facts are never mutated: a revision writes a new `Fact` plus a `SUPERSEDES` edge,
so "what is X now" and "what did X used to be" are two traversals of one chain.
Before any model is asked to answer, four structural gates decide whether the
graph *can* — and each gate that fires returns a machine-readable reason. A
question costs **at most four Bolt round trips**, counted rather than estimated.

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
  temporal.py     windows, value-at-T, change history: pure, no driver, no model
  paths.py        gate 4's one batched MSpaths call, escaping and instance scoping
  gates.py        gates 1-4: lexical resolution, window, connectivity. No model
  answer.py       gate cascade -> narrow -> answer -> citation check -> abstain
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

Slices 01–09 are done; each issue in `.scratch/hydramem/issues/` carries its own
acceptance detail and a Result section recording what changed and why. **10 is
next** — synthesis and the citation explain path. All four gates are live in
`gates.run()`: `unknown_entity`, `no_such_relation`, `no_fact_in_window`,
`no_path`.

06 closed **GO** on three automatic gates — schema validity 100% (38/38),
grounding 96.9%, `other` share 24.4%. Its hand-check sheet
(`docs/extraction-review.md`) is generated and deterministic but **its tally is
unfilled**: fact precision is not measured and no number in this repo claims
otherwise. Slice 08 then found that the 24.4% `other` share is *not* benign —
see issue 17.

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

**Count, do not estimate.** The cost claim is "at most four Bolt round trips
per question", and it is only worth making because `client.round_trips()`
increments where the round trip happens and `answer.Result` reports the
difference. A number tallied by whoever thinks they know how many reads they
asked for is not a measurement.

**Anything interpolated into a query text is a trust boundary.** HydraDB's
native path parser will not take `$parameters` for its selector lists, so anchor
keys — which come from model-extracted entity names — go into the query as
literals. `paths.literal` escapes exactly (the lexer's own escape rule) and
refuses what it cannot escape. Escape, never whitelist: a charset filter drops
legitimate keys silently, which is the failure nobody notices.

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

## Repository

`origin` → **`https://github.com/AkhileshBabuT/Hack_Hydra`**, branch **`main`**
(renamed from `master` when the remote was added). Slices 01–07 are pushed;
`e495afe` is 06, `82181d4` is 07. **08 and 09 are complete in the working tree
and not yet committed.** They share four files (`gates.py`, `answer.py`,
`statements.py`, `CLAUDE.md`), so by the convention below they cannot be split
into two commits without `git add -p` — and splitting them anyway would produce
an 08 commit whose own tests do not pass, since `test_temporal.py` calls
`gates.temporal_gate`. One commit naming both slices is the honest shape.

Verified before the first push and worth re-checking before any later one:
`.env` has never been tracked, appears in no commit, and no `nvapi-` string
exists anywhere in history. Tracked-file count was 47 at slice 07 and is 53
now, with 5 files from slices 08-09 still untracked (`temporal.py`, `paths.py`,
their two test modules, and issue 17) — re-count rather than trusting this
line. `hydradb-data/` (which holds the dev auth token), `data/`, `.cache/` and
`.codegraph/` are all gitignored.

**Write source files with the Write tool, not a Bash heredoc.** A multi-line
`cat > file <<'EOF'` with a large Python body fails here with `unexpected EOF
while looking for matching`, leaving no file behind — silently, if you do not
check. Small in-place edits through `python - <<'PY'` are fine.

**Write a commit message to a file and use `git commit -F <file>`.** The Bash
tool runs sh, so a PowerShell here-string (`@'…'@`) is parsed as a pathspec and
the commit fails halfway through with a wall of "pathspec did not match" errors.

**A file whose changes span two slices goes in the later commit.** Splitting one
file across two commits needs `git add -p`, which is interactive and blocked
here, and rewriting the file twice to fake a clean split would misreport what
happened. CLAUDE.md landed whole in the slice-07 commit for this reason.

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
  to `:Entity` and its `label` parameter is ignored. The store currently also
  holds two *real* instances: `gpt4_2655b836` (3 sessions, 26 facts, 1 entity,
  all on one day) and `89941a93` (knowledge-update, 2 sessions 231 days apart,
  22 facts, 2 entities), ingested to verify gates 1–4 fire on something other
  than fixtures. `89941a93` is the only instance in the store where a temporal
  window can discriminate anything.
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

## Known gaps as of slice 09

Real, measured, and owned by a later slice. None of these are mysteries — they
are debts with an address. Prune a line when its slice closes it.

- **`other` is a sink, and slice 06's "the vocabulary is adequate" was wrong.**
  Measured on `89941a93`: 20 of 22 facts are `other`, including `three bikes`
  and `four bikes` — the knowledge update the instance is built around. Gate 2
  therefore abstains `no_such_relation: person:user has no owns` on the
  instance's own question, and the chain produces **0** `SUPERSEDES` edges on a
  knowledge-update instance. `other` is excluded from gate 2's wanted set by
  design *and* is non-functional, so anything filed there is structurally
  inert. Issue **17**; it needs a node wipe. Pinned in
  `test_gates.py::test_other_is_a_sink_that_silently_disables_gate_2`.
- **Gate 2 checks that a predicate slot is filled, never what is in it.** Live
  on `gpt4_2655b836`: "What is my sister's name?" passes because the instance's
  only `name` fact is the mis-slotted `silver Honda Civic`. Not fixable inside
  the gate — it checks shape by construction. Pinned in
  `test_gates.py::test_the_gate_cannot_see_a_mis_slotted_value`.
- **Gate 4 is close to vacuous on this corpus.** Its pass path is verified live;
  its abstention is verified only against the hop bound (`maxLen=1` on anchors
  that connect at 4). The corpus produces a star — 96.9% of facts sit on
  `person:user` and every other entity is only ever the OBJECT of one of the
  user's facts — so any two entities are ≤4 hops apart through the user and a
  genuinely unreachable pair may not exist in this data. Lowering `MAX_LEN` to 2
  would make it fire and would be wrong. It becomes load-bearing when entity
  resolution does.
- **The MSpaths traversal is not instance-scoped, only its results are.**
  `paths.scoped()` drops foreign paths, but the work is shared across tenants
  and most of the result budget is spent on paths that are then discarded (6
  tenants matched, 1 path ours). With enough tenants sharing an anchor key a
  real path could be crowded out and read as `no_path`. Needs an
  instance-scoped Entity property, which costs a node wipe.
- **The alias closure is still unproven on real data.** `ALIASES_FOR_INSTANCE`
  returns nothing on every instance measured so far, so gate 1's alias path is
  proven synthetically or not at all. `alias_pairs` produced **0** `ALIAS_OF`
  edges across 41 real sessions.
- **The citation check is lenient.** An answer survives if *any* cited id is in
  the retrieved set; invented ids are filtered out rather than being fatal.
  Slice 10 has to decide whether a fabricated id sitting beside a valid one is
  still `uncited_answer`.
- **Fact precision is not measured.** Slice 06 closed schema validity (100%,
  38/38) and grounding (96.9% — an automatic *floor* that catches invented
  quotes and nothing else). Precision needs a human reading
  `docs/extraction-review.md`, and that tally is unfilled. Do not quote a
  precision figure for this pipeline; there isn't one.
- **The extractor attributes assistant suggestions to the user.** Instance
  `ec81a493` filed three `prefers` facts quoting the assistant's own advice
  ("Choose a harmonious frame"), which the prompt forbids outright. Grounding is
  no defence — an exact copy of assistant text is grounded by definition. Slice
  10's citation check is the only place it can be caught; owned there.
- **Predicate assignment is unreliable** — e.g. `budget: '2-Day General
  Admission'`, and measured again in slice 06 as `name: 'silver Honda Civic'`.
  Because only functional predicates chain, a mis-slotted value can supersede a
  real one, so extraction noise is *amplified* by the chain rather than diluted
  by it, and the retraction is pinned in
  `test_chain.py::test_a_mis_slotted_functional_predicate_retracts_a_true_fact`.
- **Vague dates still collapse to assertion time at ingest, deliberately.**
  `resolve_valid_from` parses numeric shapes only; "last summer" falls back to
  the session timestamp. Slice 08 decided *not* to extend it: at ingest there is
  no reliable anchor for a seasonal or fiscal phrase, and a confidently wrong
  precise date is worse than an honest approximate one, since the temporal gate
  filters on exactly that field. Relative phrasing is resolved at **query** time
  instead, where `asked_at` is a real anchor.
- **The oracle split's sessions are often same-day.** On `gpt4_2655b836` all
  three sessions and all 26 facts sit on `2023-04-10`, so year and month windows
  cannot discriminate there at all. Any claim about temporal precision has to be
  made against multi-month instances like `89941a93` (231 days) or the `_s`
  split.
- **Same-turn ties are arbitrary.** Two facts sharing entity, predicate and turn
  are ordered by node id — deterministic across processes, but not meaningful.
- **`client.IdempotencyConflict` cannot fire** on the Cypher path. It is a
  deliberate net, not live protection; see the connection facts above.
- **The statement inventory probes with empty rows**, so it proves a statement
  parses, not that it runs. Edge statements in particular pass the probe and then
  fail on real rows if an endpoint is missing.
- **`round_trips()` is a module-level counter.** Correct for this single-threaded
  harness and wrong the moment two questions are answered concurrently.

## Gate cascade

`gates.run()` runs gates in order and short-circuits on the first failure, so a
question already lost costs no further round trips. Adding a gate is appending
to that cascade. The four, and what each costs:

| gate | reason code | round trips to reach it |
|---|---|---|
| 1 entity | `unknown_entity` | 2 (entities, aliases) |
| 2 predicate | `no_such_relation` | 3 (+ the shared instance fact read) |
| 3 window | `no_fact_in_window` | 3 (same read, already in hand) |
| 4 path | `no_path` | 4 (+ one batched `algo.MSpaths`), multi-entity only |

`gates.facts_reader(read, instance_id)` returns `(all_facts, facts_for)` from
**one lazy instance-wide read**, shared by gates 2 and 3 *and* the answer.
Slice 07 read per entity and then read the same rows again to answer; that is
what made four trips reachable. `FACTS_FOR_ENTITY` has no callers and is gone.
`client.round_trips()` counts them where they happen — `answer.Result` reports
`round_trips` per question, and the 2/3/4 budget is pinned in `test_paths.py`.

- **A predicate name's glue words are not part of its meaning.** Matching any
  word of `subscribes_to` meant every question containing "to" wanted it, so
  gate 2 found a held predicate on almost anything and silently stopped firing.
  Found by probing a live graph; the unit tests all passed. Cues match on word
  boundaries for the same reason — `"work" in "homework"` is true.
- **A capitalised calendar word is a date, not a name.** Gate 1 read "February"
  in *"How many bikes did I have in February 2023?"* as a proper noun, found no
  such entity, and abstained `unknown_entity: february` — so every temporal
  question was lost behind gate 1 and gate 3 could never run. `gates._CALENDAR`
  drops months, weekdays and a few holidays. Found by probing slice 08 live.
- **Probe a new gate against a real ingested instance before believing it.**
  A gate that never fires is indistinguishable from a gate that is broken.
  Every bug in the *lexical* layer across slices 07-09 was invisible to the
  suite — glue words in slice 07, calendar words in slice 08 — because a
  fixture exercising a recogniser is written by whoever wrote the recogniser,
  so it tests the same blind spot twice. The suite does catch structural
  mistakes: `test_statements.py` caught `MS_PATHS` being defined but not
  registered within a minute of it being written.

### Gate 3: the temporal gate

`temporal.parse_window` is a small ordered regex table and resolves only shapes
a reader would call unambiguous. **Directional forms are matched first** —
"before 2021" also contains the shape "<preposition> 2021" and matched later
resolves to the opposite window. There is deliberately **no bare-month form**:
"in May" is not distinguishable from the verb, and a wrong window abstains
silently. Relative phrasing needs `asked_at`; without an anchor it resolves to
nothing rather than to the wall clock, so an evaluation does not change answer
in January.

`valid_to == 0` means *unbounded*, not "ended at the epoch". Most facts are
open, so reading it as a real end date empties every window — this is the one
line in `temporal.overlaps` worth re-reading before changing anything.

A window narrows retrieval as well as gating it: `answer_question` sends the
model only the facts valid in the window, so the plain question returns the
current value and the scoped question returns the old one. Verified on
`89941a93`: "how many bikes do I have" → four, "…in February 2023" → three,
"…in 2020" → `no_fact_in_window`.

### Gate 4: one batched MSpaths call

`CALL algo.MSpaths({...}) YIELD path RETURN path` **is the whole query** —
HydraDB's native path parser ends with `parser.end()`, so no `WHERE`, no
`LIMIT`, nothing may follow. Consequences that are not optional:

- **`sourceValues` / `targetValues` / `relTypes` cannot be `$parameters`.**
  `config_string_list` does not resolve them, so anchor keys are interpolated
  into query text — and they derive from model-extracted entity names.
  `paths.literal` escapes backslash and quote (exact, per the lexer's escape
  rule) and refuses control characters. `maxLen` and `resultLimit` *are*
  parameterizable, so the bound stays a bound.
- **The selector is not instance-scoped.** It matches `(:Entity {key: …})` and
  every tenant has a `person:user`, so an unfiltered result connects entities
  through other people's graphs. Measured: one anchor pair matched **6 tenants,
  6 paths, 1 ours**. `paths.scoped()` drops the rest. The traversal work is
  still shared, so most of the result budget is spent on paths that get
  discarded; scoping it properly needs an instance-scoped Entity property,
  which is a node wipe.
- **Unknown config keys are rejected outright**, and `fairRelationshipVariants`
  requires pairwise MSpaths and rejects weightProp / costProp / maxCost. It is
  what round-robins the result budget across structural paths.
- `path` returns a **flat list**: `[node-map, 'EDGE_TYPE', node-map, …]`. Node
  maps carry every property except `id`; Fact maps carry `fact_id`.
- **An entity-valued fact needs `value_is_entity`, not just a predicate in
  `ingest.OBJECT_TYPES`.** `OBJECT_TYPES` only decides what *type* the object
  entity gets; the flag is what makes ingest create it and write the `OBJECT`
  edge at all. Without it "Acme" stays a literal string on the fact, `org:acme`
  never exists, and gate 4 reports `no_path` — a missing entity failing as a
  missing *route*, which sends you to the wrong module. It showed up in slice
  09 as a test-fixture bug; the fixture in `test_paths.py` says so in place.
- `MS_PATHS` is a template, so the inventory registers its **assembled** form —
  a statement whose assembled shape is never parsed is exactly what the
  inventory exists to catch, and `test_statements.py` now checks templates by
  their literal prefix.

## Traps

- **Never let a probe measure its own cache.** Both throughput and latency
  measurements silently reported cache speed once (166,330 RPM; 1M tokens in
  0.0s). Uncached calls need a nonce; real latencies persist to a sidecar.
- **The repo is public and `.env` is gitignored.** No longer aspirational — it
  is on GitHub as of slice 07. A key in git history survives deletion, so the
  check is worth repeating before a push, not just before the first one.
- **Slice 08 changed the answering prompt and the fact line, so no number
  measured before it is comparable.** `fact_line` now dates a fact by
  `valid_from` rather than `asserted_at` (the two differ only when the
  extractor found a date, and it is `valid_from` that answers "when was this
  true"), and marks a closed fact `[superseded <date>]` with a system-prompt
  rule telling the model that means *not true now*. Without that marker the
  model reads a retracted employer and a current one as two equally live facts.
  Every arm shares this line, so the comparison stays fair — but a score from
  before slice 08 is not the same measurement.
- **No commit may predate Aug 12 2026** — it is a disqualification criterion.
- Report only within-harness comparisons. Published LongMemEval figures were
  measured with different answering models, embedders and judges; cite them
  separately and never claim to beat them.
- HydraDB is AGPL-3.0 and is reached over Bolt as a separate service, never
  linked as a library. This repo ships Apache-2.0.
