# HydraMem

**Bitemporal agent memory on HydraDB, where abstention is a first-class result.**

Hackathon submission, Track 03 — Memory + Context Retrieval.

Most memory layers answer every question. This one decides, structurally and
before any model is called, whether the graph *can* answer — and when it cannot,
it says so and names the reason in a machine-readable code.

Two properties follow from that, and neither is available from a vector store:

- **Facts are never mutated.** A revision writes a new `Fact` plus a `SUPERSEDES`
  edge, so *"where do I work"* and *"where did I used to work"* are two
  traversals of one chain rather than two prompting strategies. The old value is
  still a node.
- **A question costs at most four Bolt round trips**, counted at the point the
  round trip happens rather than estimated. `explain()` renders the whole trace
  of one — answered or abstained.

## Quickstart

Needs Docker and Python 3.11+. Nothing else — no Rust toolchain, no build.

```bash
git clone https://github.com/AkhileshBabuT/Hack_Hydra && cd Hack_Hydra

python -m venv .venv                       # Windows: .venv\Scripts\activate
./.venv/Scripts/python.exe -m pip install -e .

docker compose up -d                       # bolt 7687, http 8443, admin 9090
./.venv/Scripts/python.exe -m pytest -q    # should report all tests passing
```

`docker compose up -d` is sufficient from a clean clone: an `init` service
creates `LOCAL_PATH` and seeds the dev auth token before the node starts.

To run anything that calls a model, put an NVIDIA NIM key in `.env`
(gitignored, never committed):

```
NVIDIA_API_KEY=nvapi-...
```

Then fetch the corpus and run one instance end to end:

```bash
./.venv/Scripts/python.exe scripts/fetch_corpus.py
./.venv/Scripts/python.exe scripts/ingest_one.py 89941a93 --oracle
```

That prints the ingest counts, the gate trace, the answer and its provenance —
including what each cited fact replaced.

> **Pass `--oracle` unless you mean the benchmark arm.** Without it the loader
> takes the `_s` split, where one question carries 30–50 haystack sessions
> instead of 1–3: tens of uncached extraction calls, with nothing to warn you.

`make` mirrors these commands for Linux/macOS judges (`make up`, `make test`,
`make eval`); on Windows run the Python directly.

## The drop-in

The mem0 method surface, so an existing agent swaps in two lines:

```python
# from mem0 import Memory
from hydramem.memory import Memory

memory = Memory(user_id="alice")

written = memory.add([{"role": "user", "content": "I own three bikes."}])
memory.search("How many bikes do I own?", bookmarks=written["bookmark"])
```

`add` returns a HydraDB bookmark and `search` accepts one, so an agent that
writes a memory and immediately searches for it is **guaranteed** to see it —
a correctness property vector-store memory layers do not have.

Two methods are not compatibility shims:

- **`history()`** walks the supersession chain and returns the ordered
  revisions. mem0 cannot implement it: a vector store overwrites in place and
  the previous value is gone.
- **`delete()`** is a **tombstone**. The fact keeps its node and its edges, takes
  `status='deleted'`, and leaves current reads while staying in history.

Same surface over HTTP, on the standard library:

```bash
python -m hydramem.server        # API + demo UI on 127.0.0.1:8800
python scripts/mem0_swap_demo.py # all of the above, end to end
```

Opening <http://127.0.0.1:8800> gives a one-page demo: ask a tenant a question
and see the answer **or the abstention reason**, the gate trace that produced
it, the facts it rests on, and the supersession chain. It is a demo surface, not
the product — one inlined page, no build step.

## How a question is answered

Five gates. The first four run before the model and short-circuit, so a question
already lost costs no further round trips. The fifth runs after it.

| gate | reason code | trips to reach |
|---|---|---|
| 1 entity | `unknown_entity` | 2 on an empty instance, else 3 |
| 2 predicate | `no_such_relation` | 3 |
| 3 window | `no_fact_in_window` | 3 (same read) |
| 4 path | `no_path` | 4, multi-entity only |
| 5 citation | `not_in_graph`, `uncited_answer`, `fabricated_citation` | 0 — after the model |

**No model runs inside a gate.** Entity and predicate detection are lexical — a
literal cue table, capitalised runs, first-person cues. A gate whose job is to
stop confabulation cannot itself be a language model without inheriting the
failure it exists to prevent. The price is bluntness, and its direction is
fixed: an unrecognised question *passes*. A false abstention is an answer thrown
away with nothing to notice it; a false pass costs one model call and still has
to survive the citation check.

Gate 5 verifies every cited `fact_id` against what was actually retrieved. A
fabricated id is fatal and names the invented id, because *"the model made one
up"* is not debuggable and *"the model made up `deadbeef`"* is.

## Results

Oracle split, **n = 150 per arm**, generated into `docs/eval/` by
`scripts/run_eval.py --oracle` and `scripts/cost_table.py --oracle`. Regenerate
with `make eval`. Every figure below is transcribed from that output;
`docs/eval/oracle-results.md` is authoritative if the two ever disagree.

| arm | accuracy | answerable | coverage | selective acc | abstain prec | abstain recall | tokens/q |
|---|---|---|---|---|---|---|---|
| full_context | 0.6200 | 0.5750 | 0.6200 | 0.7419 | **0.4211** | 0.8000 | 5,540 |
| vector_rag | **0.6467** | **0.5833** | 0.5667 | **0.8235** | 0.4154 | 0.9000 | **2,494** |
| **hydramem** | 0.6200 | 0.5417 | 0.5333 | 0.8125 | 0.4000 | **0.9333** | 2,743 |

- **coverage** — the fraction of questions the arm chose to answer.
- **selective accuracy** — how often it was right when it did answer.
- **answerable** — accuracy over questions that have a gold answer, whether or
  not the arm attempted them.

Coverage and selective accuracy belong together. Abstention precision alone is
gameable in both directions — a published truncation baseline scored 93.3 on an
abstention subset by answering almost nothing — and an arm that refuses
everything scores no coverage rather than perfect anything.

### Per category

| category | hydramem | full_context | vector_rag |
|---|---|---|---|
| knowledge-update | **0.8846** | 0.5769 | 0.6538 |
| temporal-reasoning | **0.6923** | 0.3846 | 0.4615 |
| multi-session | **0.5000** | 0.5000 | 0.4688 |
| single-session-assistant | 0.4500 | **1.0000** | 0.9000 |
| single-session-preference | 0.4000 | 0.4500 | **0.5000** |
| single-session-user | 0.7308 | 0.8846 | **0.9615** |

**HydraMem wins the two categories it was built for, by 23 points each.**
Knowledge-update is the SUPERSEDES chain resolving to the current value;
temporal reasoning is the bitemporal window. It loses the three single-session
recall categories — what a flat reader does best and a graph does worst.

### Why each abstention happened

Every abstention carries a machine-readable reason, generated per reason into
`docs/eval/oracle-abstentions.csv`. `count_false` is the defect: an abstention
on a question that had an answer.

| reason | fired | of which false | where |
|---|---|---|---|
| `not_in_graph` | 46 | 29 | after the model, holding the facts, declined |
| `unknown_entity` | 18 | 7 | gate 1, before retrieval |
| `no_fact_in_window` | 2 | 2 | gate 3 |
| `no_such_relation` | 1 | 1 | gate 2 |
| `no_path` | 1 | 1 | gate 4 |
| `fabricated_citation` | 1 | 1 | gate 5 |
| `empty_graph` | 1 | 1 | the instance extracted nothing |

Two thirds of abstentions are the answering step declining, not the cascade
firing. That is the current bottleneck and it is stated rather than hidden.

### How it got here

Each row is one change, re-scored on its own, so these are separately
attributed rather than bundled. **Only the last column is current**; the earlier
ones are superseded measurements kept for attribution and are not in
`docs/eval/` any more:

| | slice 17 | gate 2 hub exempt | extraction prompt | gate 1 self-ref |
|---|---|---|---|---|
| accuracy | 0.4933 | 0.5467 | 0.6000 | **0.6200** |
| abstention precision | 0.3023 | 0.3421 | 0.3733 | **0.4000** |
| abstention recall | 0.8667 | 0.8667 | **0.9333** | **0.9333** |
| knowledge-update | 0.6923 | 0.7692 | **0.8846** | **0.8846** |
| single-session-assistant | — | 0.1500 | 0.3000 | **0.4500** |
| tokens/q | **893** | 1,004 | 2,608 | 2,743 |

### What is not true

Four things this project measured about itself and will not paper over:

- **It is not the most accurate arm.** Vector RAG is, 0.6467 against 0.6200. It
  is now tied with full-context.
- **It has no token advantage over vector RAG** — 2,743 against 2,494, worse.
  It is 2.0x cheaper than full context, not the 6.2x an earlier build measured;
  raising extraction yield to fix `single-session-assistant` spent that margin.
- **It does not win selective accuracy either** — 0.8125 against vector RAG's
  0.8235. It is clearly ahead of full context (0.7419) and behind vector RAG.
- **Two of the four pre-model gates barely fire on this corpus.** Gate 4 fired
  once across 150 questions and gate 2 once. Where every fact hangs off one hub
  and predicates are assigned unreliably, structural absence is close to
  unmeasurable.

What it does have: the two thesis categories won outright, the highest
abstention recall of the three arms, a machine-readable reason for every
abstention, and `answer.explain()` rendering the whole trace of any question,
answered or abstained.

### How the comparison is kept fair

Three arms over one stratified slice, differing **only** in the retrieval layer,
with one answering model across all three and a judge that is not the answering
model. Every arm gets the same permission to abstain — `eval.BASELINE_SYSTEM` is
the HydraMem system prompt with the citation clauses removed and nothing else
changed, asserted in `test_eval.py`. Withholding that from the baselines would
rig the one comparison this submission turns on.

The slice is stratified rather than random: every abstention instance is taken
and each answerable question type is capped, so abstention is deliberately
over-represented against its natural ~6%. Read the `n` on every row before
reading any rate.

Published LongMemEval figures from other systems used different answering
models, embedders and judges. They are cited where relevant and **never claimed
as beaten** — every number is a (system, backbone, judge, split) tuple.

## Layout

```
hydramem/
  statements.py   EVERY Cypher template, + the INVENTORY executed against a live node
  client.py       Bolt: bookmarks, consistency, idempotency keys, metrics
  chain.py        supersession: pure derivation + materialization rows
  gates.py        gates 1-4, lexical only, no model
  answer.py       cascade -> narrow -> answer -> citation check, and explain()
  temporal.py     windows, value-at-T, change history: pure
  memory.py       the mem0-compatible surface
  server.py       that surface over HTTP, on http.server
tests/            312+ tests; test_fixtures.py is the <10s dev loop
docs/eval/        generated: per-category CSV, cost CSV, results.md
docs/hydradb-notes.md   rough edges found, with upstream issue candidates
```

## Licensing

This repository is **Apache-2.0** (see `LICENSE`).

HydraDB is **AGPL-3.0** and is reached over Bolt as a separate service. It is
never linked as a library, and no HydraDB source is vendored here.
