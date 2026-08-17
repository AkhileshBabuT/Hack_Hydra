# HydraMem

Bitemporal agent memory on HydraDB, where **abstention is a first-class result**.
Hackathon submission, Track 03 — Memory + Context Retrieval.

Facts are never mutated: a revision writes a new `Fact` plus a `SUPERSEDES` edge,
so "what is X now" and "what did X used to be" are two traversals of one chain.
Before any model is asked to answer, four structural gates decide whether the
graph *can* — and each gate that fires returns a machine-readable reason. After
the model answers, a fifth check verifies every citation against what was
actually retrieved and downgrades anything else to abstention. A question costs
**at most four Bolt round trips**, counted rather than estimated;
`answer.explain()` renders the whole trace of one, answered or abstained.

Planning artifacts live in `.scratch/hydramem/`. Do not restate their contents here.

## Commands

```bash
docker compose up -d                      # HydraDB: bolt 7687, http 8443, admin 9090
./.venv/Scripts/python.exe scripts/fetch_corpus.py                 # LongMemEval -> data/
./.venv/Scripts/python.exe scripts/ingest_one.py <id> --oracle     # one instance, end to end
./.venv/Scripts/python.exe -m pytest -q                            # full suite
./.venv/Scripts/python.exe -m pytest tests/test_fixtures.py -q     # dev loop, <10s
./.venv/Scripts/python.exe -m pytest tests/test_statements.py -q   # Cypher parse check
./.venv/Scripts/python.exe scripts/probe_budget.py                 # -> docs/budget.md
./.venv/Scripts/python.exe scripts/measure_extraction.py           # -> docs/extraction-quality.md
./.venv/Scripts/python.exe scripts/run_eval.py --oracle            # every arm over the slice
./.venv/Scripts/python.exe scripts/cost_table.py --oracle          # -> docs/eval/*.csv, results.md
```

**Pass `--oracle` unless you mean the benchmark arm.** Without it the loader
takes `longmemeval_s_cleaned.json`, where one `question_id` carries 30-50
haystack sessions instead of the oracle split's 1-3 — tens of uncached extraction
calls on a cold cache, with nothing to warn you. `gpt4_2487a7cb` ran past a
10-minute timeout on `_s` and finished in 280s on oracle.

`make` is **not installed on this machine** — the Makefile mirrors these commands
for Linux/macOS judges, but run the Python directly here. Compose has an `init`
service that creates `LOCAL_PATH` and seeds the auth token, so `docker compose up -d`
is enough from a clean clone.

## Layout

```
hydramem/
  ids.py          deterministic int node identity
  statements.py   EVERY Cypher template, + INVENTORY registry
  client.py       Bolt: bookmarks, consistency, idempotency keys, metrics
  llm.py          model access: cache, backoff, token counting
  chain.py        supersession: pure derivation + materialization rows
  corpus.py       LongMemEval loader: streaming, whole sessions, epoch timestamps
  extract.py      session-level extraction, controlled predicates, output repair
  ingest.py       pure row builder + batched guarded writes
  temporal.py     windows, value-at-T, change history: pure, no driver, no model
  paths.py        gate 4's one batched MSpaths call, escaping, instance scoping
  gates.py        gates 1-4: lexical resolution, window, connectivity. No model
  answer.py       cascade -> narrow -> answer -> citation check -> abstain,
                  plus provenance and `explain()`
  eval.py         stratified slice, the three arms, the judge, the rate maths
  vectors.py      the vector-RAG baseline's local CPU embedder and top-k search
  memory.py       the mem0 surface: add/search/get_all/history/delete + explain
  server.py       that surface over HTTP, on stdlib http.server. No framework
scripts/  probe_budget · fetch_corpus · ingest_one · measure_extraction
          run_eval (arms, resumable) · cost_table (metrics + timings -> CSV)
          mem0_swap_demo (the two-line swap, end to end)
tests/test_fixtures.py    the dev loop: 25 cases, live node, model stubbed
docs/eval/                generated: per-category CSV, cost CSV, results.md
docs/budget.md · extraction-quality.md · extraction-review.md   generated
docs/hydradb-notes.md     rough edges found; upstream issue candidates
```

## Where the build is

Slices 01–18 are built. **357 tests, 356 passing** against a live node holding
the evaluation slice — the one red is the known-red budget test below, measured
at 11.6s against its 10.0s budget. Each issue in `.scratch/hydramem/issues/` carries its own acceptance
detail and a Result section; issues **15, 17, 18, 19 and 20 are closed**, and
**16 is closed except for what needs a human** — clean-clone verification on a
second machine, filing the four upstream drafts in `docs/upstream-issues.md`, the
video, and the submission form. Read `docs/eval/oracle-results.md` before
believing anything about accuracy.

**One test is red on a store that holds the full evaluation slice**, by design
rather than by defect: `test_fixtures.py::test_the_suite_stays_inside_its_ten_second_budget`
measures **12.4s against a 10.0s budget** once the 150-instance oracle slice is
resident. It passed twice in the same session on a clean store (328/328). The
budget guards the *development loop*, and the development loop is meant to run on
a development store — the reset unit is a wipe, as everywhere else here. Do not
raise `BUDGET_SECONDS` to make it green: the number it is failing on is the real
"read cost scales with store size" property, and hiding it removes the only
signal that exists for it.

Two test files draw the same line at different levels, both deliberately:

- `tests/test_answer.py` is **pure** — no driver, no model. A test of the
  citation downgrade that depends on what a model happens to return proves
  nothing about the guarantee.
- `tests/test_fixtures.py` is **live graph, stubbed model**: 25 cases in ~10s,
  real writes, real reads, a real `algo.MSpaths` call. `Case.trips` asserts the
  round-trip count on every one, so the budget table below is pinned end to end.
  Run it on every change; run the full suite before a commit.

## The measured result

Oracle split, n=150 per arm, generated in `docs/eval/`. Regenerate with
`make eval`. **Never quote a number that is not in there.**

| arm | acc | ans acc | coverage | selective | abs prec | abs recall | tokens/q |
|---|---|---|---|---|---|---|---|
| full_context | 0.6200 | 0.5750 | 0.6200 | 0.7419 | **0.4211** | 0.8000 | 5,540 |
| vector_rag | **0.6467** | **0.5833** | 0.5667 | 0.8235 | 0.4154 | 0.9000 | **2,494** |
| hydramem | 0.6000 | 0.5167 | 0.5000 | 0.8267 | 0.3733 | **0.9333** | 2,608 |

Per category, and this is where the thesis lives or dies:

| category | hydramem | full_context | vector_rag |
|---|---|---|---|
| knowledge-update | **0.8846** | 0.5769 | 0.6538 |
| temporal-reasoning | **0.6923** | 0.3846 | 0.4615 |
| multi-session | **0.5000** | 0.5000 | 0.4688 |
| single-session-preference | 0.4000 | 0.4500 | **0.5000** |
| single-session-user | 0.7308 | 0.8846 | **0.9615** |
| single-session-assistant | 0.3000 | **1.0000** | 0.9000 |

**HydraMem wins the two categories the architecture exists for, by wide
margins** — knowledge-update by 23 points over the better baseline and temporal
reasoning by 23 over vector RAG. Both are supersession and bitemporal
resolution doing exactly what they were built for. It loses all three
single-session recall categories, which are the ones a flat reader is best at
and a graph is worst at.

**Two slices landed here. Both moved everything, and the second broke a claim.**

| | slice 17 | 18: gate 2 hub exemption | 18: extraction prompt |
|---|---|---|---|
| accuracy | 0.4933 | 0.5467 | **0.6000** |
| answerable accuracy | 0.4000 | 0.4667 | **0.5167** |
| selective accuracy | — | 0.7568 | **0.8267** |
| abstention precision | 0.3023 | 0.3421 | **0.3733** |
| abstention recall | 0.8667 | 0.8667 | **0.9333** |
| knowledge-update | 0.6923 | 0.7692 | **0.8846** |
| tokens/q | **893** | 1,004 | 2,608 |

Paired against the pre-slice-18 prompt on the identical 150 instances: **90
correct against 82**.

**The token claim is dead and must not be repeated.** 893 → **2,608**, because
the extraction prompt raised facts per instance from 14.0 to **42.7**. HydraMem
is now *more* expensive per question than vector RAG (2,608 against 2,494) and
only 2.1x cheaper than full context, not 6.2x. Every sentence anywhere claiming
6.2x, or claiming a cost advantage over vector RAG, is now false.

**Selective accuracy is nominally the best of the three arms and that is a tie,
not a win.** 0.8267 against vector_rag's 0.8235 is 0.32 of a percentage point
over 75 answered questions — **less than one question**. Report it as
"comparable to vector RAG, clearly better than full context (0.7419)". Anyone
writing "the most accurate arm when it answers" is over-reading noise.

**`ans acc` is accuracy over the answerable questions only, and it is in this
table to kill one specific sentence.** The tempting framing for an
abstention-first system that loses on accuracy is *"it answers less often, but
it is more reliable when it does."* **That is false here.** HydraMem is the
least accurate arm on the subset it chose to answer — 0.4667 against 0.5750 and
0.5833 — so it is not trading coverage for reliability, it is behind on both.
Slice 18 narrowed it and did not close it.
The column was generated all along and simply never read. Do not write that
sentence, and do not write anything that implies it.

**GO on the current build**, and read the next paragraph before repeating that
word anywhere. `verdict()` compares against **full_context** and clears two of
three:

| check | hydramem | full_context | |
|---|---|---|---|
| abstention recall | 0.8667 | 0.8000 | win |
| abstention precision | 0.3023 | 0.4211 | **loss** |
| knowledge-update accuracy | **0.6923** | 0.5769 | win |

Knowledge-update was the *tie* that blocked GO at slice 14. Slice 17's counted
predicate targeted exactly it, and it now beats **both** baselines
(vector_rag 0.6538) — the thesis category, won outright.

**What is still not true:** vector RAG remains the most accurate arm overall
(0.6467 against 0.4933) and HydraMem still **loses abstention precision**. GO
means the harness's own three-check test passes, not that this is the best
retrieval layer in the harness. Do not write a sentence implying otherwise.

**The accuracy gap is one number: HydraMem abstains on 86 of 150 questions and
60 of those are wrong.** Not a spread of small losses — one failure, repeated.
Generated per reason in `docs/eval/oracle-abstentions.csv`:

| reason | fired | of which false | where |
|---|---|---|---|
| `not_in_graph` | 39 | **22** | after the model, holding the facts, declined |
| `unknown_entity` | 33 | **22** | gate 1, before retrieval |
| `no_fact_in_window` | 2 | 2 | gate 3, before retrieval |
| `empty_graph` | 1 | 1 | the instance extracted nothing at all |
| `no_such_relation` | **0** | 0 | gate 2, exempted on the hub — see below |
| `no_path` | **0** | 0 | gate 4 never fires |
| `uncited_answer`, `fabricated_citation` | **0** | 0 | gate 5 costs nothing |

75 abstentions, 47 false — down from 86 and 60 before slice 18. The baselines
abstain 57 and 65 times, so the count was never the outlier; the false half is.
`empty_graph` is down to **1** instance from 8, which is the extraction prompt
working on the gap that used to present as a gate-1 problem and never was.

**Two of the four pre-model gates now fire zero times on this corpus, and that
has to be said plainly rather than buried.** Gate 4 never fired at all. Gate 2
fired 18 times before slice 18 and was wrong 16, so it was exempted on
`person:user` and now fires 0. What is actually carrying the cascade is gate 1
(25 firings), gate 3 (2), and gate 5 (49) — and gate 5 is the model declining,
not a structural check.

That is a real finding about the thesis, not only about the gates. The claim
"four structural gates decide whether the graph *can* answer" is, on this
corpus, **two gates and a model**. The machinery is not wrong — gate 2 still
fires on a specific entity, gate 4's constraints are real — but on a corpus
where every fact hangs off one hub and 38 predicates are assigned unreliably,
structural absence is nearly unmeasurable. Do not write a sentence implying four
gates are load-bearing here.

Cost: **2.1x fewer tokens than full-context and slightly MORE than vector RAG**
(2,608 against 2,494). This was 6.2x and 2.8x before slice 18's extraction
prompt tripled facts per instance, 14.0 → 42.7. The cost advantage over vector
RAG is gone; the latency advantage is not. Latency moved twice in slice 17 and
both moves
were gate 4: dropping `person:assistant` took p95 12,233 → 6,641 ms, and scoping
the MSpaths selector to one tenant took median 1,483 → **860 ms** and p95 6,641 →
**1,483 ms**. The gate had been paying to traverse every tenant in the store on
every multi-entity question. Round trips are a measured distribution, not an
average: **7 questions cost 2, 141 cost 3, 2 cost 4. Never five.** Ingest costs
0.419 Bolt round trips per fact and 590 extraction tokens per fact.

**`skey` changed no score at all** — 0.4933 / 0.3023 / 0.8667 before and after,
identical. Gate 4 is near-vacuous on this corpus, so instance-scoping it was a
correctness and cost fix, not an accuracy one. Recorded because "we fixed the
traversal and accuracy went up" is the sentence somebody will otherwise write.

**The five stages, so nobody repeats them:**

| stage | acc | abs precision | abs recall |
|---|---|---|---|
| 0 as slice 12 first ran it (n=62) | 0.3387 | 0.2400 | 0.9231 |
| 1 gate 1 resolves stored text | 0.4333 | 0.2800 | 0.9333 |
| 2 gate 2 stops firing over `other` | 0.4800 | 0.3043 | 0.9333 |
| 3 assistant attribution (needed a wipe) | 0.4533 | 0.3034 | 0.9000 |
| 4 slice 17: `quantity` + named specifics + gate 4 | 0.4933 | 0.3023 | 0.8667 |
| 5 slice 18: gate 2 exempted on the hub | **0.5467** | **0.3421** | 0.8667 |

Stage 4 is the best-scoring build measured and the first to reach GO. It bundles
three changes and they are **not separately attributed** — one wipe, one
re-ingest, one re-score. If the next slice needs to know which of the three
carried the knowledge-update gain, that is another cycle, not a re-read of this
table.

Abstention recall fell 0.9000 → 0.8667 and precision is flat. That is the
expected direction: gate 4 abstains less now, so both rates move toward answering.

**The remaining gap is extraction quality, not the cascade, and slice 17 barely
moved it.** `single-session-assistant` went 1 of 20 → **2 of 20**, against 20/20
for full_context and 18/20 for vector RAG. It is the worst category by a wide
margin and the single largest remaining accuracy lever.

The slice-17 prompt rule ("copy names, titles, brands and numbers exactly; never
replace a specific with its category") **does** work where it is measured —
`docs/extraction-review.md` now holds `The Witcher 3: Wild Hunt`,
`Horizon Zero Dawn`, `@jessica_poole_jewellery` verbatim where the old build
produced themes. It did not convert into accuracy on this category, so the
failure is downstream of storing the right string and has not been located. Do
not assume another prompt round will fix it; find out where those 18 questions
actually die first.

`other` is **31.3%** of facts, against 24.4% at slice 06 — but that comparison
spans slice 12's assistant attribution as well, so **neither slice owns the
movement** and it is not evidence that slice 17 made labelling worse.

## Non-negotiable conventions

**No model runs inside a gate.** Entity and predicate detection are lexical —
capitalised runs, first-person cues, a literal cue table. A gate whose job is to
stop confabulation cannot itself be a language model without inheriting the
failure it exists to prevent. The price is bluntness, and its direction is fixed:
**an unrecognised question passes**. A false abstention is an answer thrown away
with no way to notice; a false pass costs one model call and still meets the
citation check.

**Every Cypher statement goes in `statements.py` and its `INVENTORY`.** The
inventory is executed against a live node by `tests/test_statements.py`. HydraDB
rejects unsupported syntax at parse time and `EXPLAIN` is unreachable over Bolt,
so this is the only early warning that exists.

**Only functional predicates supersede.** `extract.FUNCTIONAL_PREDICATES`
(employer, lives_in, age, …) chain by predicate; everything else chains per
distinct value, so a new `likes` accumulates instead of retracting the last one.
Treating every predicate as functional marked 193 of 220 facts superseded on a
real instance.

**`chain.group_key` has a third mode: counted.** `extract.COUNTED_PREDICATES`
(`quantity`) keys on *the thing being counted* — the value with its leading
count stripped by `extract.LEADING_COUNT`. Neither other mode describes a count:
functional gives one count per entity, so `17 cameras` retracts `four bikes`;
per-value gives each count its own slot, so `four bikes` never retracts `three
bikes` and the knowledge-update instance forms no chain at all. Measured on
`89941a93` before slice 17: 22 facts, **0** SUPERSEDES edges, on the one category
supersession exists for. After: 20 facts, **2** SUPERSEDES edges, and the
question answers `four bikes` against gold `4`. A bare number (`other | 2` was
real) has nothing left after stripping, so it falls back to the whole value and
keeps its own slot rather than collapsing every number on the entity into one.

**Every measured failure gets a test before its slice closes.** Not a note in a
document — a test that fails if the failure returns, holding the real payload
rather than a description of it. Slice 06's collapsed decodes are pinned as the
literal strings the model emitted (`{`, a zero-width run) because a paraphrase
would not notice a decoder that stops collapsing the same way. A defect a slice
chooses *not* to fix goes on the issue that inherits it, with its test.

**Count, do not estimate.** `client.round_trips()` and `llm.usage()` increment
where the round trip and the tokens actually happen; `answer.Result` and the cost
table report differences around a call. A number tallied by whoever thinks they
know how many reads they asked for is not a measurement. A cache hit still counts
tokens — they were real when spent, and a per-question cost that falls to zero on
a rerun describes the cache rather than the system.

**Anything interpolated into a query text is a trust boundary.** HydraDB's native
path parser will not take `$parameters` for its selector lists, so anchor keys —
which come from model-extracted entity names — go into the query as literals.
`paths.literal` escapes exactly (the lexer's own escape rule) and refuses what it
cannot escape. Escape, never whitelist: a charset filter drops legitimate keys
silently, which is the failure nobody notices.

**Push logic into pure functions.** Gate predicates, chain derivation and temporal
filters are tested over plain fact lists with no database and no model call, so an
abstention is reproducible from a question string and a list of dicts.

**Re-ingesting after an extractor change needs a wiped node.** Fact ids are
content-derived, so a new prompt or model writes a *second* generation of facts
beside the first and every count silently inflates. Deletion is impractical (see
below). `docker compose down`, `rm -rf hydradb-data`, `docker compose up -d`.

**All model access goes through `llm.py`.** Constructing a provider client
elsewhere breaks the disk cache, and the cache is what keeps reruns at $0.

**One answering model across every evaluation arm.** Full-context, vector RAG and
HydraMem must differ *only* in their retrieval layer, or the comparison measures
model quality instead of retrieval quality.

## Repository and harness

`origin` → **`https://github.com/AkhileshBabuT/Hack_Hydra`**, branch **`main`**.
Slices 01–09 are on `origin/main` (`e495afe` = 06, `82181d4` = 07, `7a42e18` =
08+09). **Slices 10–14 are all uncommitted working tree**, not unpushed commits —
`git log origin/main..main` is empty and says nothing about them. Check
`git status`, and split commits from the diff rather than from history.

`.env` has never been tracked and no `nvapi-` string exists anywhere in history —
worth re-checking before every push, not just the first. `hydradb-data/` (holds
the dev auth token), `data/`, `.cache/`, `.codegraph/` and `.eval/` are
gitignored; `.eval/` is the per-question resume log, and the committed artefacts
are the CSVs in `docs/eval/`.

**A file whose changes span two slices goes in the later commit.** Splitting one
file needs `git add -p`, which is interactive and blocked here, and rewriting it
twice to fake a clean split would misreport what happened.

**`git checkout -- <file>` is a destructive command in this repo, not a revert.**
Slices 10–17 are uncommitted working tree, so for most source files `HEAD` is
`82181d4` (slice 07) or older and there is **no committed baseline to revert
to**. `git checkout` on `hydramem/gates.py` silently reset it past six slices of
work; `git fsck` recovered nothing, because the content was never staged and
therefore never entered the object database. It was recoverable only from the
editor-side snapshot cache at `~/.claude/file-history/<session>/`, which is not
a backup and is not guaranteed to be there.

Measured 2026-08-16, by a review sub-agent mutation-testing a function and
"reverting" afterwards. Two rules follow:

- **Never hand a sub-agent a brief that implies reverting via git.** Say
  *"copy the file to the scratchpad, mutate the copy"* explicitly. "Use a scratch
  copy" was in the brief and was not enough — the agent mutated a second file it
  had not been told about and reached for `git checkout` on reflex.
- **Verify a restored file by diffing it against the snapshot, not by running
  the tests.** A passing suite proves the file parses and satisfies its
  assertions, not that a comment, a docstring or an unexercised branch survived.
  The check that settled it was `difflib.unified_diff(snapshot, current)`
  showing one additive hunk and **zero removed lines**.

**Bash-tool hazards, all measured here:**

- **Write source files with the Write tool, not a heredoc.** A multi-line
  `cat > file <<'EOF'` with a large Python body fails with `unexpected EOF`,
  leaving no file behind — silently, if you do not check.
- **A backslash does not survive the heredoc, quoted or not.** `"\n"` inside a
  `python - <<'PY'` script arrives as a real newline; a line-continuation `\`
  vanishes and silently joins two lines. The second is the dangerous one: no
  error, no diff, and the script prints whatever it was going to print. Build
  backslashes from `chr(92)`, or use Edit/Write. This has bitten four times.
- **`cmd | tail` reports `tail`'s exit code**, so a crashed Python run looks like
  success. Redirect to a file and check `$?` when the exit code matters.
- **Commit messages go in a file**, `git commit -F <file>` — a PowerShell
  here-string is parsed as a pathspec and fails with pathspec errors.

## HydraDB source reference

A checkout of `hydra-db/hydradb` @ `6a2fbb1` lives at **`C:\Projects\hydradb-ref`**,
indexed with codegraph (101 Rust files, ~5.6k nodes):

```
codegraph_search / codegraph_explore   with projectPath: "C:\\Projects\\hydradb-ref"
```

Prefer this over grep when verifying a Cypher constraint or a procedure
signature — the Cypher surface is undocumented in places and the parser is the
only authority. This project is indexed too; run `codegraph sync .` after adding
a module.

## HydraDB: verified constraints

Verified against `6a2fbb1`. The Cypher surface is a deliberate subset; assume
nothing beyond it without testing.

- **Node `id` must be a non-negative integer.** `MERGE` matches on `id` alone.
  String identities are hashed to ints in `ids.py`; the canonical string is kept
  as a property.
- **No `ON CREATE` / `ON MATCH`.** A vertex upsert is `MERGE` by id then `SET`.
- **No `IN`, `CONTAINS`, `ENDS WITH`, `IS NULL`** in `WHERE`. Filter client-side.
- **No `min`/`max`** aggregates — only `count`, `sum`, `avg`, `collect`.
- **`ORDER BY`** takes a projected alias, `<binding>.id`, or `count(*)`.
- **`UNWIND` batches are narrow**: one relationship pattern, one hop, directed;
  `UNWIND … CREATE` cannot be followed by another clause. Input must be a
  parameter holding a list of maps.
- **No explicit transactions.** Metadata rides on the `Query` object. Ingest is
  idempotent batched writes under at-least-once delivery, never transactional
  units.
- **No index DDL.** Property indexes are maintained automatically.
- **`EXPLAIN`** exists only on the in-process shard API, not over Bolt or HTTP.
- **UNION**: read-only, ≤256 arms, per-arm `ORDER BY`/`LIMIT`.
- **A node with a label or a non-id property must be named.**
  `(:Fact {id: 1})-[r]->(e)` is rejected; `(f:Fact {id: 1})-[r]->(e)` is not.
- **An edge batch is rejected whole if an endpoint is missing.** Node batches must
  land first; a dangling row fails loudly.
- **An `UNWIND` row must carry every field its statement names.** Adding a
  property to an upsert breaks every existing caller — adding `role` to
  `UPSERT_FACT` failed four `test_tracer.py` tests immediately with `UNWIND row 0
  is missing field role`. Loud and at parse time, which is the good case.
- **One `UNWIND` batch may not carry two values for the same vertex property.**
  Two facts sharing (session, turn, predicate, subject, value) but differing in
  evidence span hash to one `vid` with two `snippet`s, and HydraDB rejects the
  **whole batch** as `conflicting metadata values for vertex <id> property
  snippet` — one duplicate loses the entire instance. `ingest.build_rows`
  deduplicates by `vid` before `chain.materialize`, so the chain derives from what
  is actually written. Pinned in `test_ingest.py`.
- **Filter the node, then join. Clause order is the only performance lever this
  database gives you.** An instance-scoped read written as one pattern with the
  filter in the `WHERE` — `MATCH (f:Fact)-[:SUBJECT]->(e:Entity) WHERE
  f.instance_id = $id` — makes HydraDB build the join across **every tenant in the
  store** and filter afterwards, so read latency scales with the whole store
  rather than the tenant. Measured at 2,122 Fact nodes: **`FACTS_FOR_INSTANCE`
  took 7,635 ms to return 11 rows.** Filtering a single-node scan first, so the
  automatic `instance_id` index drives — `MATCH (f:Fact) WHERE f.instance_id =
  $id` then `MATCH (f)-[:SUBJECT]->(e)` — returns the same 11 rows in **250 ms**.
  Identical answers, 30x, and its blast radius was everything: the fixture suite
  went 228s → 10s and the full suite 524s → 148s. There is no index DDL and no
  `EXPLAIN`, so **nothing would ever catch a regression** — the query keeps
  returning correct rows, just slower as the store fills. Hence the shape
  assertion in `test_statements.py::test_an_instance_read_filters_before_it_joins`
  over all five joining reads. Do not collapse one back into a single pattern.
- **Deletion is impractical**: `DETACH DELETE` costs ~0.3s/node and a few hundred
  nodes exceed the 30s query timeout; the `UNWIND` batch form is rejected. There
  is no per-tenant reset — wipe `hydradb-data/` instead.
- **The test suite writes into the live node** under `test-<test name>` tenants
  (`tests/conftest.py`), and those accumulate forever. A store with a few dozen
  entities is test residue, not a real ingest: after a wipe it refills with test
  data on the next `pytest` run and looks populated while holding no corpus.
  Check a real `instance_id` with `count_facts` before believing a graph has data.
  `count_label` cannot help — HydraDB has no dynamic labels, so it is hardcoded to
  `:Entity` and its `label` parameter is ignored.
  **Current store** (rebuilt by the slice-12 wipes): 1,980 Fact and 390 Entity
  nodes; **1,936 facts across 142 instances** of the evaluation slice, plus `fx-*`
  fixture tenants and `test-*` residue. Roles are 1,543 user / 393 assistant, with
  93 `person:assistant` entities and **no null-role facts left**. 142 rather than
  150 because eight instances extracted nothing — see the gaps.
- **Keep to 6 edge types.** CSC generations are built per edge type; `SUBJECT` and
  `OBJECT` carry the traversal load and stay dense because of it.

### Guarded merge — real but undocumented

Absent from `cypher-compat.md`, implemented in the parser
(`opencypher.rs:18-20`, test at `:3928`):

```cypher
UNWIND $rows AS row MERGE (n {id: row.vid})
  SET n:Fact,
      n.__hydradb_update_if_newer_by     = row.asserted_at,
      n.__hydradb_create_only_first_seen = row.first_seen
```

Verified live: every guarded property must also be `SET` from a row field of the
*same name*; a create-only marker requires an update guard present; the guard
property may not itself be create-only. The comparison is **strictly less-than**,
so an *equal* guard value writes nothing — a fact upsert guarded on `asserted_at`
is immutable on replay. Supersession is therefore materialized by `CLOSE_FACT`,
guarded on `valid_to` (0 → timestamp), and that same strictness stops a re-ingest
resetting `status` to `current`. Guard markers work only inside the `UNWIND`
vertex upsert form — `MATCH … SET` has no guarded equivalent.

### A stale writer lease deadlocks every write, permanently

**Symptom: the whole suite fails on writes and passes on reads, with no source
change.** Measured 2026-08-15 — 23 failed, 6 errors, 283 passed, immediately
after `docker compose up -d` recreated the container. Every `test_statements.py`
mutation (`upsert_*`, `link_*`, `close_fact`) failed; every read passed.

Over Bolt the client sees only `Neo.DatabaseError.General.UnknownError: internal
query execution error`, which reads as a query bug and is not one. **The real
error is only in `docker logs`:**

```
object store error: Operation `put_opts` with mode `PutMode::Update`
not yet implemented by LocalFileSystem(file:///data/store)
```

It is the **writer lease**, not the data and not the image. In
`src/engine/writer_lease.rs` @ `6a2fbb1`: acquiring uses `PutMode::Update` when a
lease object exists (`:266-269`); `LocalFileSystem` has no conditional update, so
that returns `NotImplemented`; the fallback that overwrites unconditionally is
guarded `if same_holder` (`:270-276`) — *"stale takeovers remain fail-closed
because they require real compare-and-swap"*. And `process_holder_id()` is
`Ulid::new()` **per process** (`:760-764`), so a restarted node is never
`same_holder` with the lease its predecessor left. Fail-closed, forever.

A *clean* shutdown releases the lease (`:687-695` deletes it on `NotImplemented`),
so this only bites after an ungraceful stop: a `down` that reaches SIGKILL, a
crash, a host reboot, a container recreate.

**Fix — delete one file, not the store:**

```
hydradb-data/store/graph/data/namespaces/default/graphs/default/_writer_leases/v2/cell-0
```

Stop the node first. It is a lock and holds no data. Verified both ways on the
same image: the real store failed even a brand-new node id, a fresh volume
accepted create, update and repeat. **Do not wipe the store over this** — losing
the corpus to a stuck lock is a bad trade made from a wrong diagnosis. Issue
**20**, and the most severe of the upstream candidates.

**It recurred on 2026-08-17 and the surgical fix was applied end to end.** Same
signature — 28 failed, 6 errors, 323 passed, every mutation failing and every
read passing. The lease file held `node-0 / 01M020SHT9SNM5F41YNMGZ0CAJ / active`
in 76 bytes; `docker compose stop`, delete the one file, `docker compose up -d`,
and **the corpus survived: 2,254 Fact and 373 Entity nodes still there.** This
is now a procedure with two clean runs behind it, not a hypothesis — reach for
it on sight of the writes-fail-reads-pass signature and do not diagnose further
before trying it. Expect the recurrence after any ungraceful stop; nothing in
the workflow prevents one.

### Connection facts

- Routing URI `neo4j://127.0.0.1:7687`; bearer auth with the auth-token contents.
- Idempotency key: tx metadata `hydradb.idempotency_key`, ≤128 chars,
  `[A-Za-z0-9._-]`. **It does not police conflicts on the Cypher path** —
  verified: one key with two different payloads is accepted, both applied.
  Uniqueness comes from `ingest.batch_key` hashing the rows it sends.
- Consistency: `hydradb.consistency` = `causal` (demo, real hot path) or `strong`
  (evaluation, so scores reproduce).
- `RUST_MIN_STACK=33554432` is required — without it the node serves `/readyz`
  then aborts on the first query.
- Use the published image `ghcr.io/hydra-db/hydradb:latest`. Do **not** build from
  source: it needs Rust 1.91 + libcypher-parser + GraphBLAS and the documented
  recipe covers Ubuntu/WSL and macOS only.

## NVIDIA NIM: verified behavior

Measured, in `docs/budget.md`. Regenerate rather than hand-edit.

| Role | Model |
|---|---|
| Extraction | `nvidia/nemotron-3.5-lightning-30b-a3b` |
| Answering — every arm | `nvidia/nemotron-3-ultra-550b-a55b` |
| Answering fallback, and the eval judge | `nvidia/nemotron-3-super-120b-a12b` |

- **Reasoning is ON by default.** Only `chat_template_kwargs={"thinking": false}`
  disables it. A `/no_think` system prompt and `extra_body={"reasoning": false}`
  both silently leave thinking tokens in the output, which destroys strict JSON
  parsing. The highest-value single setting in the pipeline.
- **No context ceiling found.** 1,000,007 tokens accepted in 39.4s, so there is no
  overflow tail and `GEMINI_API_KEY` is unused. The routing branch in
  `eval.run_full_context` still runs and counts: **0 of 150 overflowed.**
- **Throughput 35.8–189.5 RPM** across runs. Plan against ~40 as a floor.
- **NIM returns transient 404s** for a model id that is valid and answers again
  minutes later. `llm.py` retries 404/408/409/429, and **`retries` is 8, not 5**:
  the backoff doubles from 1s, so five attempts give up after ~15s while this
  recovers on the order of minutes. An arm died on exactly this and the model
  answered a probe seconds later. A *persistent* 404 on the answering model means
  switching **every** arm to the fallback, never falling back per call — that
  would break the single-answering-model rule silently.
- **`models.list()` is public** — it returns 102 models for a deliberately invalid
  key. Only `chat/completions` proves authorization.
- **Extraction needs `max_tokens=8192`.** A dense session yields 30+ facts; at
  4096 the JSON truncated mid-string and the session was lost to a parse error
  that looked like a model-quality problem and was ours.
- **The extractor omits `subject` and occasionally misspells a field name.** The
  schema defaults `subject`/`predicate`/`value` for that reason and `clean()`
  drops what is left empty. A per-session parse failure is counted and named in
  the ingest stats, never swallowed.
- **A null in any field used to cost the whole session.** pydantic reports
  per-fact errors as one failed `Extraction`, so one `turn_idx: null` discarded 27
  good facts. Coerce nulls in a validator. Worth 8 points of schema validity.
- **Greedy decoding collapses on ~5% of calls** — one returned the single
  character `{`, another a run of zero-width spaces, and an *answering* call
  returned a truncated run of Arabic text. Because the disk cache is keyed on the
  request, retrying the identical request returns the identical garbage forever;
  **temperature is the only lever that reaches this.** `extract.extract_raw` and
  `eval.ask` each retry once at `RETRY_TEMPERATURE = 0.3`. Schema validity 86.8% →
  94.7% → **100%**. Any new caller of `complete_json` needs the same treatment —
  one collapsed decode killed a 150-question arm outright.
- **The model will not attribute on request.** Asking the extraction prompt to set
  `subject: 'assistant'` for assistant-stated content produced 16 facts with
  `role=assistant` and **zero** `person:assistant` entities. Attribution is now
  derived from the turn role in `ingest.build_rows`, which is not an opinion.
- `tiktoken`'s `cl100k_base` tracks the provider's own count within ~0.01% at
  scale, so it is sound for pre-flight routing decisions.

## Corpus

`xiaowu0162/longmemeval-cleaned` on HuggingFace (MIT), not the original release —
its author marks the original deprecated. `data/` is gitignored; fetch with
`scripts/fetch_corpus.py`. `longmemeval_s_cleaned.json` is the benchmark arm
(277 MB, ~500 instances, 30-50 sessions each); `longmemeval_oracle.json` is
evidence sessions only and is the fast loop.

- The corpus lists haystack sessions **out of chronological order**. `corpus.py`
  sorts by timestamp once, so session `idx`, `NEXT` edges and recency agree.
- **Gold answers are sometimes numbers**, not strings. Coerced in `to_instance`.
- Abstention instances carry an `_abs` suffix on `question_id` (30 of 500 in the
  oracle split).
- **Oracle sessions are often same-day**, so year and month windows cannot
  discriminate there. Any temporal-precision claim needs a multi-month instance or
  the `_s` split.

## Gate cascade

`gates.run()` runs gates in order and short-circuits on the first failure, so a
question already lost costs no further round trips. Adding a gate is appending to
that cascade.

| gate | reason code | round trips to reach it |
|---|---|---|
| 1 entity | `unknown_entity` | 2 on an empty instance, otherwise 3 |
| 2 predicate | `no_such_relation` | 3 (+ the shared instance fact read) |
| 3 window | `no_fact_in_window` | 3 (same read, already in hand) |
| 4 path | `no_path` | 4 (+ one batched `algo.MSpaths`), multi-entity only |
| 5 citation | `not_in_graph`, `uncited_answer`, `fabricated_citation` | 0 — after the model |

Gate 5 is a gate by construction, not by analogy: it fires on shape, names what
was missing, and returns a `gates.GateResult`. It is the only one that runs
*after* the model, and it is the reason a false pass upstream is affordable.

`gates.facts_reader(read, instance_id)` returns `(all_facts, facts_for)` from
**one lazy instance-wide read**, shared by gates 2 and 3 *and* the answer. Slice
07 read per entity and then re-read the same rows to answer; that is what made
four trips reachable.

**`answer_question` sends the model every fact in the instance**, narrowed only by
a resolved window — never only the gated entity's facts. That is why gate 1 does
not need to *resolve* a mention for its facts to be visible; it only needs to not
abstain on it.

### What the lexical layer got wrong, four times

Every bug here was invisible to the unit suite, because a fixture exercising a
recogniser is written by whoever wrote the recogniser and tests the same blind
spot twice. **Probe a new gate against a real ingested instance before believing
it** — a gate that never fires is indistinguishable from a gate that is broken.

- **Glue words.** Matching any word of `subscribes_to` meant every question
  containing "to" wanted it, so gate 2 found a held predicate on almost anything
  and silently stopped firing. Cues now match on word boundaries — `"work" in
  "homework"` is true.
- **Calendar words.** Gate 1 read "February" as a proper noun and abstained
  `unknown_entity: february`, so every temporal question was lost behind gate 1
  and gate 3 could never run. `gates._CALENDAR` drops months and weekdays.
- **Chat openers.** `"Any tips?"` abstained `unknown_entity: any`. The corpus is
  chat, so a question opens with a request or filler far more often than a name.
  Second-person words are dropped too — `"You mentioned…"` abstained
  `unknown_entity: you`.
- **Gate 1's whole entity-node contract, and it cost 61% false abstention.**
  Ingest creates an `Entity` for a fact's subject and for an object with
  `value_is_entity` set, and nothing else — so the graph holds `uses | Fitbit
  Charge 3` with no `thing:fitbit charge` node, and requiring one abstained on
  questions the graph answers. `gates.text_reader` now checks fact values and
  evidence snippets before rejecting a mention, resolving it to that fact's
  subject. Snippets count deliberately: gate 1's job is to catch a question about
  something the graph has never heard of, not to police how well the extractor
  slotted it. It still fires where it should — `air fryer` and `miami` hit nothing
  at all on their instances. The read is lazy and shared, so a passing question
  costs nothing extra; only one about to abstain pays, 2 trips → 3. A pass by this
  route traces as `1 entity: pass (via stored text: …)`.

The suite *does* catch structural mistakes — `test_statements.py` caught
`MS_PATHS` being defined but not registered within a minute. It is only the
lexical layer it cannot see. Assume the fifth one is there.

### Gate 2 cannot assert absence over `other`

`other` is the extractor's "captured this, could not label it" and is ~30% of
every fact in the corpus. Firing `no_such_relation` while holding unlabelled
content about that very entity is this gate mistaking its own vocabulary gap for
the graph's silence — a false abstention by construction.

Measured before the fix: gate 2 fired 30 times and **only 2 were correct.** Of the
16 firing on entities that held `other` facts, 15 were false abstentions. The
guard recovers 15 at the cost of 1, and the gate still fires on the 14 whose
entity holds nothing unlabelled, so it is not vacuous. A pass by this route traces
as `2 predicate: pass (holds unlabelled 'other' facts)`.

It still checks that a predicate *slot* is filled, never what is in it — `name:
'silver Honda Civic'` passes, by construction. Pinned as a characterisation test.

### Gate 5: the citation check, and why it is strict

Slice 09 kept an answer if *any* cited id was in the retrieved set and filtered
invented ids out silently. Slice 10 makes an invented id **fatal**, with its own
reason code. The two failures are not the same kind of thing: a missing citation
is a model that did not follow the output contract, while a fabricated one is a
model that produced a plausible-looking identifier it never read. A valid id
beside a fabricated one does not redeem the answer — the claim was assembled from
both and nothing can say which half rests on the id nobody can resolve.
`fabricated_citation` names the invented ids, because "the model made one up" is
not debuggable and "the model made up `deadbeef`" is.

The one repair kept is transcription: case and surrounding whitespace are
normalised before the membership test, because the model copies a 16-hex
`fact_id` back by hand. Repairing further would start guessing which fact was
meant, which is the confabulation the check exists to stop.

**Strictness costs nothing here, measured.** Across the 150-question oracle
slice, `fabricated_citation` fired **0** times and `uncited_answer` fired **0**
times — generated in `docs/eval/oracle-abstentions.csv`. Every one of gate 5's
41 abstentions is `not_in_graph`, which is the model declining rather than a
citation failing. So the part of the cascade most easily read as
over-engineering throws away no answers at all, and the strict-versus-lenient
argument of slice 10 is settled on this corpus by never arising. Do not soften
the check to buy recall it is not costing.

### Gate 3: the temporal gate

`temporal.parse_window` is a small ordered regex table resolving only shapes a
reader would call unambiguous. **Directional forms are matched first** — "before
2021" also contains the shape "<preposition> 2021" and matched later resolves to
the opposite window. There is deliberately **no bare-month form**: "in May" is not
distinguishable from the verb. Relative phrasing needs `asked_at`; without an
anchor it resolves to nothing rather than to the wall clock, so an evaluation does
not change answer in January.

`valid_to == 0` means *unbounded*, not "ended at the epoch". Most facts are open,
so reading it as a real end date empties every window — the one line in
`temporal.overlaps` worth re-reading before changing anything.

A window narrows retrieval as well as gating it, so the plain question returns the
current value and the scoped question returns the old one. Verified live: "how
many bikes do I have" → four, "…in February 2023" → three, "…in 2020" →
`no_fact_in_window`.

**Vague dates collapse to assertion time at ingest, deliberately.**
`resolve_valid_from` parses numeric shapes only; "last summer" falls back to the
session timestamp. At ingest there is no reliable anchor for a seasonal phrase,
and a confidently wrong precise date is worse than an honest approximate one since
this gate filters on exactly that field. Relative phrasing is resolved at **query**
time, where `asked_at` is a real anchor.

### Gate 4: one batched MSpaths call

**It fires `no_path` exactly 0 times** on the 150-question oracle slice —
generated in `docs/eval/oracle-abstentions.csv`, not estimated. Every constraint
below is real and was expensive to find, but none of it currently buys an
abstention: the gate costs one round trip on every multi-entity question and
returns nothing on all of them. Read this section as what the traversal *would*
have to obey, not as a description of load-bearing behaviour. The Known gaps
entry carries what that means for removing it.

`CALL algo.MSpaths({...}) YIELD path RETURN path` **is the whole query** —
HydraDB's native path parser ends with `parser.end()`, so no `WHERE`, no `LIMIT`,
nothing may follow. Consequences that are not optional:

- **`sourceValues` / `targetValues` / `relTypes` cannot be `$parameters`.**
  `config_string_list` does not resolve them, so anchor keys are interpolated into
  query text — and they derive from model-extracted entity names. `paths.literal`
  escapes backslash and quote and refuses control characters. `maxLen` and
  `resultLimit` *are* parameterizable, so the bound stays a bound.
- **The selector is not instance-scoped.** It matches `(:Entity {key: …})` and
  every tenant has a `person:user`. Measured: one anchor pair matched **6 tenants,
  6 paths, 1 ours**. `paths.scoped()` drops the rest, so the *work* is shared
  across tenants even though the *answer* is not.
- **Only `SUBJECT` and `OBJECT` are traversed**, `relDirection: 'both'`. Two
  entities linked only through sessions are unreachable at any hop count — which
  is what makes the fixture suite's `no_path` case possible at all.
- **Unknown config keys are rejected outright**, and `fairRelationshipVariants`
  requires pairwise MSpaths and rejects weightProp / costProp / maxCost.
- `path` returns a **flat list**: `[node-map, 'EDGE_TYPE', node-map, …]`.
- **An entity-valued fact needs `value_is_entity`, not just a predicate in
  `ingest.OBJECT_TYPES`.** `OBJECT_TYPES` only decides what *type* the object
  entity gets; the flag is what makes ingest create it and write the `OBJECT` edge.
  Without it "Acme" stays a literal string, `org:acme` never exists, and gate 4
  reports `no_path` — a missing entity failing as a missing *route*.
- `MS_PATHS` is a template, so the inventory registers its **assembled** form.
- **`person:assistant` is never an anchor.** `paths.NON_TOPICAL_KEYS` drops it
  before the call. Slice 12's attribution gave assistant-stated facts their own
  subject, which makes a **second star** beside `person:user` sharing no node
  with it — so a question whose mention resolves to assistant-sourced content,
  plus the implicit `person:user`, asked gate 4 whether the user and the
  assistant are connected. They never are. Measured on `7401057b`: anchors
  `['person:assistant', 'person:user']` → `no_path`, with the gold answer sitting
  in the graph. A false abstention *by construction*, on the category that
  already scores worst. `person:user` **stays** — questions are about the user,
  and dropping it makes gate 4 skip every "is the user connected to X" question,
  which is vacuous rather than blunt. Four fixture tests assert that distinction.
- **A timed-out traversal passes the gate, it does not abstain.** Both hub keys
  match one Entity per tenant (the selector is not instance-scoped), so pairwise
  traversal under `strong` consistency exceeded the 30s query timeout and
  **killed a 150-question arm at question 54** by propagating. `paths.find`
  catches it and sets `timed_out`; gate 4 passes. A traversal that ran out of
  time established nothing, and `no_path` from it would assert a structural
  absence the database never checked.

### The fifth lexical bug was gate 4's, and it was not lexical

CLAUDE.md predicted "assume the fifth one is there". It was, and it came from a
different direction: not a recogniser mis-firing, but **one slice's fix becoming
another gate's false premise**. Slice 12 added `person:assistant` for provenance;
gate 4 read it as a topical entity. Neither slice was wrong alone.

The lesson generalises past the lexical layer: a gate that reasons over entity
*identity* inherits every change to what an entity is. Adding a subject kind is
therefore a gate-4 change whether or not anyone touches `paths.py`.

### `explain()` and the trace

`gates.run` accumulates one line per check that ran; `answer.Result` reports it as
`gate_trace` alongside `evidence`, and `answer.explain(result)` renders both.
Pure — it reads a `Result` and touches neither driver nor model, so an explanation
cannot disagree with the answer it explains. `scripts/ingest_one.py` prints it, so
the demo path and the debugging path are the same code.

- **Gate 4 is traced as `skipped`, not `pass`, on a single-entity question.** The
  trace is an audit record of what was *spent*, not only of what was decided, and
  "pass" would read as a traversal that happened. Same reason the two weak passes
  above name themselves.
- **What a fact replaced is derived from rows already in hand**, using
  `chain.group_key` and the same total order `chain.derive` uses, so it agrees
  with the SUPERSEDES edges without paying to read them. Asserted directly in
  `test_answer.py`: a cheap derivation that can silently describe a chain the
  graph does not have is worse than the round trip it saves.

## The evaluation harness

`make eval` is the single target: three arms over one stratified slice, then the
cost table and the write-up. Everything under `docs/eval/` is generated.

**The slice is stratified, not random, and that changes what accuracy means.**
`eval.stratify` takes *every* abstention instance and caps each answerable
question type at `PER_TYPE` — on oracle that is exactly 150 (6 × 20 + all 30
abstention). The natural abstention rate is ~6% and the slice runs it at 20%,
deliberately, because a precision computed over six questions is not a number
anyone should act on. Every row of every table carries its `n` for that reason.

**Reporting categories are not sampling strata.** Sampling splits
`temporal-reasoning` from `temporal-reasoning_abs` so coverage is guaranteed;
reporting folds them back, because inside an all-abstention bucket precision is
trivially 1.0 and outside it is undefined. A table that cannot be wrong says
nothing.

**Every arm gets the same permission to abstain.** `eval.BASELINE_SYSTEM` is
`answer.SYSTEM` with the citation clauses removed and nothing else changed,
asserted in `test_eval.py`. The gates and the citation check are what is being
measured; the *opportunity* to say "I don't know" is not, and withholding it from
the baseline would rig the one comparison this submission turns on.

**The judge is not the answering model**, so no arm marks its own homework. A
gold-abstention question is never judged — correctness there is "did the arm
abstain", which is structural.

**A Bolt driver created before the extraction warm pass goes stale and kills the
run.** Measured 2026-08-17 on a cold cache after a wipe: `warm_extraction` ran
~350 sessions over several minutes with the driver idle, and the first read
after it failed `ServiceUnavailable: Unable to retrieve routing information`.
Every extraction call had been paid for and **not one question was scored**.

The node was `Up ... (healthy)` throughout with **zero errors in its logs**, so
this presents as an outage and is a stale routing table on the client side. It
is invisible on a warm cache, because warming then takes seconds and the idle
window never opens — which is why it survived every earlier run and only
appeared on the first wipe-and-re-ingest. `run_eval.main` now warms **before**
`client.connect()`. Do not move warming back inside `run_arm`.

**Runs are resumable; arms are independent processes.** Each scored question is
appended to `.eval/<split>/<arm>.jsonl` as it lands. Arms write separate files so
they can run in parallel, but **two processes must never share an arm** — use
`--arms`, and `--summarise-only` to rebuild tables without scoring anything.

### The histogram units, a factor-of-a-million trap

Verified from `crates/telemetry/src/meter.rs` @ `6a2fbb1`, not assumed:

- The kernel measures in microseconds and **only** in microseconds.
  `HistogramUnit` converts at the export boundary, and the same enum value picks
  both the name suffix and the scaling (`render_bound`, `scale_sum`). **The suffix
  is therefore authoritative** — a `_seconds` series cannot be microseconds by
  accident.
- `HistogramUnit::Seconds` "exists for exactly one instrument":
  `db.client.operation.duration`, whose OTel semantic convention fixes seconds.
- `CounterUnit` has **no** seconds variant, deliberately: scaling a counter would
  make it disagree with the `_microseconds` series rendered from the same field,
  "and nothing downstream could detect the factor of a million".

The endpoint serves `graph_client_operation_read_duration_seconds` and
`graph_query_rows_duration_microseconds` side by side **on the same bucket ladder
scaled by 1e6** — `le="0.0001"` and `le="100"` are the same bound.
`client.histograms` reads the suffix and states it in the CSV; `client.metrics`
still skips every labelled sample, which is why it drops histograms entirely and
why this lives beside it rather than inside it. Cross-checked once against an
independent hand timing (139.6 ms against 131–150 ms), and the two read series
carry identical sample counts, which is the parse checking itself.

### Published figures are cited, never claimed

The mem0 arm is **not reproduced in-harness** and `cost_table.MEM0_NOTE` says so
in every generated results document: its published LongMemEval figure was measured
with a different answering model, embedder and judge. Same rule for every other
published score.

## Known gaps

Real, measured, owned by a later slice. Prune a line when its slice closes it.

- **The extractor captures themes, not named specifics** — the single largest
  remaining accuracy lever. Asked for `mountain meditation` the graph holds
  `likes | mindfulness techniques`; asked for a framerate figure, `other | 2`. It
  costs `single-session-assistant` 19 of 20 and needs a prompt-and-wipe cycle
  aimed at preserving names, titles and numbers.
- **Some instances extract zero facts and nothing counts it as a failure.** 8 of
  150 instances hold nothing, with only 4 parse failures recorded — so the model
  returned valid JSON containing an empty list and the pipeline logged a
  successful ingest of nothing. Downstream it surfaces as `unknown_entity: <empty
  graph>`, which reads as a gate problem and is not one.
  `ingest.extract_instance` has no notion of a session that parsed fine and
  yielded nothing; median elsewhere is 7.6 facts per session.
- **`other` is still structurally inert in the chain.** Gate 2 no longer abstains
  over it, but `other` is non-functional, so a knowledge update filed there forms
  **0** SUPERSEDES edges. Issue **17**, and it needs the vocabulary fixed rather
  than the gate. Pinned in
  `test_gates.py::test_other_is_a_sink_that_silently_disables_gate_2`.
- **Fact precision is not measured.** Schema validity (100%) and grounding (96.9%,
  an automatic *floor* that catches invented quotes and nothing else) are closed;
  precision needs a human reading `docs/extraction-review.md` and that tally is
  unfilled. **Do not quote a precision figure; there isn't one.**
- **Predicate assignment is unreliable** — `budget: '2-Day General Admission'`,
  `name: 'silver Honda Civic'`. Because only functional predicates chain, a
  mis-slotted value can supersede a real one, so extraction noise is *amplified*
  by the chain rather than diluted. Pinned in
  `test_chain.py::test_a_mis_slotted_functional_predicate_retracts_a_true_fact`.
- **Gate 4 is vacuous on this corpus — `no_path` fires exactly 0 times** across
  the 150-question oracle slice, generated in `docs/eval/oracle-abstentions.csv`.
  Not "close to vacuous": zero. It still costs a round trip on every
  multi-entity question, so it is pure overhead on this data. Removing it would
  be wrong for a different reason — its pass path is verified live and the
  corpus is a star, so the gate is untested rather than disproven — but no
  accuracy claim may rest on it. It briefly became harmful, too. Its
  pass path is verified live; its abstention only against the hop bound. The
  corpus produces a star — most facts sit on `person:user` — so a genuinely
  unreachable pair may not exist in this data. Lowering `MAX_LEN` would make it
  fire and would be wrong. "It becomes load-bearing when entity resolution does"
  was written as a prediction and came true in slice 17: preserving named
  specifics created more entities, gate 4 started firing where it used to skip
  for want of a second anchor, and every firing against `person:assistant` was a
  false abstention. Excluding that key returns it to mostly-skipping.
- ~~The MSpaths traversal is not instance-scoped~~ — **closed in slice 17.**
  `Entity.skey = "<instance_id>|<key>"` with `sourceProperty: 'skey'`, so the
  traversal starts and stays inside one tenant. It had become a measured outage
  twice over: at ~53 tenants it **exceeded the 30s timeout and killed a
  150-question arm at question 54**, and at ~160 tenants it silently returned
  other tenants' paths, crowding the caller's own out of `resultLimit` so a real
  path read as `no_path` — two fixture tests began failing with no code change.
  That second mode is the dangerous one: the failure presents as an *abstention*,
  which looks like the system working. Adding the property broke every existing
  caller at parse time (`UNWIND row 0 is missing field skey`) and needed a node
  wipe, because `UPSERT_ENTITY` is guarded on `last_seen` and an equal guard
  value writes nothing — **a guarded upsert makes a schema addition a migration,
  not a backfill.**
- **The alias closure is unproven on real data.** `alias_pairs` produced **0**
  `ALIAS_OF` edges across 41 real sessions, so gate 1's alias path is proven
  synthetically or not at all.
- **Same-turn ties are arbitrary.** Two facts sharing entity, predicate and turn
  are ordered by node id — deterministic across processes, but not meaningful.
- **`client.IdempotencyConflict` cannot fire** on the Cypher path. A deliberate
  net, not live protection.
- **The statement inventory probes with empty rows**, so it proves a statement
  parses, not that it runs. Edge statements pass the probe and then fail on real
  rows if an endpoint is missing.
- **`round_trips()` and `llm.usage()` are module-level.** Correct for this
  single-threaded harness; `usage()` takes a lock because the extraction warm pass
  is a thread pool, but the round-trip counter is wrong the moment two questions
  are answered concurrently.

## Traps

- **Never let a probe measure its own cache.** Throughput and latency both
  silently reported cache speed once (166,330 RPM; 1M tokens in 0.0s). Uncached
  calls need a nonce.
- **The repo is public and `.env` is gitignored.** A key in git history survives
  deletion, so the check is worth repeating before every push.
- **No number measured before slice 08 is comparable.** `fact_line` now dates a
  fact by `valid_from` rather than `asserted_at` and marks a closed fact
  `[superseded <date>]`, with a system-prompt rule saying that means *not true
  now*. Every arm shares this line, so the comparison stays fair — but an older
  score is not the same measurement.
- **No commit may predate Aug 12 2026** — a disqualification criterion.
- Report only within-harness comparisons. Published LongMemEval figures used
  different answering models, embedders and judges; cite them separately and never
  claim to beat them.
- HydraDB is AGPL-3.0 and is reached over Bolt as a separate service, never linked
  as a library. This repo ships Apache-2.0.
