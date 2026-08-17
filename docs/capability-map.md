# Capability map — HydraDB feature → the code that uses it

Every HydraDB capability this submission claims, tied to the function that
actually exercises it. Written for a judge checking that a claim is load-bearing
rather than decorative.

Nothing here is aspirational: if a row is listed, the named code runs it on the
demo path, and the test column names something that fails when it stops working.

## Graph model and storage

| HydraDB capability | Where it is used | Proven by |
|---|---|---|
| Labelled property graph, `MERGE` by integer id | `statements.UPSERT_FACT`, `UPSERT_ENTITY`, `UPSERT_SESSION`; ids hashed in `ids.nid` | `tests/test_statements.py`, `tests/test_ingest.py` |
| **Guarded merge** (`__hydradb_update_if_newer_by`, `__hydradb_create_only_first_seen`) | `statements.UPSERT_FACT` guards on `asserted_at`; `statements.CLOSE_FACT` guards on `valid_to` | `test_ingest.py::test_reingest_changes_no_counts`, `test_chain.py::test_reingest_cannot_resurrect_a_superseded_fact` |
| Batched `UNWIND` writes | `ingest.write_rows`, batched over `WRITE_PLAN` | `test_ingest.py::test_interrupted_ingest_resumes_to_the_same_graph` |
| Six edge types, kept deliberately few (`SUBJECT`, `OBJECT`, `ASSERTED_IN`, `NEXT`, `SUPERSEDES`, `ALIAS_OF`) | `statements.LINK_*` | `test_statements.py` inventory probe |
| Automatic property indexes (no DDL exists) | every instance-scoped read filters the node first so `instance_id` drives | `test_statements.py::test_an_instance_read_filters_before_it_joins` |

The guarded-merge markers are **undocumented** — absent from `cypher-compat.md`,
implemented in the parser. They are what makes re-ingest idempotent and what
stops a replay resetting a superseded fact to `current`. See
`docs/hydradb-notes.md`.

## Traversal

| HydraDB capability | Where it is used | Proven by |
|---|---|---|
| `CALL algo.MSpaths` — batched multi-source path search | `statements.MS_PATHS`, called once by gate 4 | `tests/test_paths.py::test_one_call_finds_the_path_between_two_entities` |
| `maxLen` / `resultLimit` as real parameters | passed as `$max_len` / `$result_limit`, so the bound stays a bound | `test_paths.py::test_the_hop_bound_is_real_and_the_abstention_names_it` |
| Selector lists that **cannot** be parameterised | anchor keys escaped by `paths.literal` before interpolation | `tests/test_paths.py` escaping cases |
| Result scoping across tenants | `paths.scoped` drops paths belonging to other tenants | `test_paths.py::test_no_returned_path_leaves_the_instance` |

`algo.MSpaths` is the single most load-bearing HydraDB-specific feature here:
gate 4's entire connectivity check is one batched call, and the four-round-trip
ceiling depends on it being one call rather than one per pair.

Because the native path parser ends with `parser.end()`, the `CALL … YIELD path
RETURN path` **is** the whole query — no `WHERE`, no `LIMIT` may follow — and
selector lists do not resolve `$parameters`. Anchor keys therefore reach the
query as literals, which makes `paths.literal` a **trust boundary**: it escapes
by the lexer's own rule and refuses what it cannot escape, rather than
whitelisting a charset that would silently drop legitimate keys.

## Consistency and transactions

| HydraDB capability | Where it is used | Proven by |
|---|---|---|
| Bookmarks / read-your-own-writes | `client.write` returns them, `client.read` and `answer.answer_question` accept them; surfaced by `memory.Memory.add` / `.search` | `test_tracer.py::test_causal_read_with_bookmark_sees_the_write`, `test_memory.py::test_a_memory_is_readable_immediately_via_its_bookmark` |
| Per-query consistency (`hydradb.consistency`) | `client.TX_CONSISTENCY` — `causal` on the demo path, `strong` for evaluation so scores reproduce | `eval.run_hydramem` pins `strong` |
| Mutation idempotency key (`hydradb.idempotency_key`) | `client.TX_IDEMPOTENCY_KEY`, keyed by `ingest.batch_key` over the rows sent | `test_ingest.py::test_reingest_changes_no_counts` |
| No explicit transactions — auto-commit RUN only | `client.write` carries metadata on the `Query` object; ingest is idempotent batched writes under at-least-once delivery | `docs/hydradb-notes.md` |

The idempotency key **does not police conflicts on the Cypher path** — verified:
one key with two different payloads is accepted and both apply. Uniqueness comes
from `ingest.batch_key` hashing the rows, not from the header. `client.IdempotencyConflict`
is a deliberate net that cannot currently fire, and is documented as such rather
than presented as live protection.

## Observability

| HydraDB capability | Where it is used | Proven by |
|---|---|---|
| Prometheus admin endpoint | `client.metrics` | `tests/test_cost.py` |
| Duration histograms, and their **unit suffix** | `client.histograms` reads the suffix and states it in the CSV | `tests/test_cost.py` |

The endpoint serves `..._duration_seconds` and `..._duration_microseconds` side
by side **on the same bucket ladder scaled by 1e6** — `le="0.0001"` and
`le="100"` are the same bound. The suffix is authoritative because the same enum
value picks both the name and the scaling at the export boundary. Reading it
wrong is a factor-of-a-million error that nothing downstream could detect, which
is why `client.histograms` reads the suffix rather than assuming a unit.

## Bitemporality — the part HydraDB does not give you

Worth stating plainly: HydraDB has no native bitemporal type. What it gives is
guarded merge, which makes a *derived* chain safe to materialise idempotently.

| Property | Where |
|---|---|
| Supersession derived as a pure function of the fact set | `chain.derive`, `chain.group_key` |
| Materialised as `SUPERSEDES` edges + guarded `CLOSE_FACT` | `chain.materialize`, `ingest.write_rows` |
| Valid-time windows, value-at-T, change history | `temporal.py` — pure, no driver, no model |
| `valid_to == 0` means *unbounded*, not "ended at the epoch" | `temporal.overlaps` |

`chain.group_key` has three modes, and the third exists because neither of the
other two describes a count: functional predicates own one slot per entity,
non-functional open a slot per value, and **counted** predicates key on the thing
being counted, so `three bikes → four bikes` chains while `17 cameras` opens its
own slot.

## Deliberately not used

| Feature | Why not |
|---|---|
| `DETACH DELETE` | ~0.3s per node; a few hundred nodes exceed the 30s query timeout, and the `UNWIND` batch form is rejected. The reset unit is the whole node. Deletion in the product is a **tombstone** instead (`memory.Memory.delete`). |
| `EXPLAIN` | exists only on the in-process shard API, not over Bolt or HTTP. `tests/test_statements.py` executes the inventory instead, which proves a statement parses rather than that it is fast. |
| Index DDL | none exists; property indexes are automatic. Clause order is the only performance lever, hence the shape assertion over all joining reads. |
| Dynamic labels | not supported, so `COUNT_LABEL` is hardcoded to `:Entity` and its `label` parameter is ignored. |
