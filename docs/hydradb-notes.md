# HydraDB notes

Rough edges and corrections found while building. Verified against
`hydra-db/hydradb` at commit `6a2fbb1`. Upstream issues get filed in slice 16.

## Corrections to our implementation plan

The plan was written against assumed behaviour. These are the assumptions that
did not survive contact.

| Plan assumed | Reality |
|---|---|
| `MERGE … ON CREATE SET` | `ON CREATE` / `ON MATCH` do not exist |
| String node ids (`Entity.key`, ULID `Fact.id`) | Node `id` must be a non-negative integer |
| `WHERE f.predicate IN $predicates` | No `IN` (nor `CONTAINS`, `ENDS WITH`, `IS NULL`) |
| Single `UNWIND … MERGE … CREATE … CREATE` write | One relationship pattern per batch, one hop; `UNWIND … CREATE` cannot be followed by another clause |
| `MATCH…MATCH…WHERE…SET…CREATE` supersession | `CREATE` cannot follow another clause |
| `schema.py` creates property indexes | No index DDL exists; property indexes are automatic |
| `docs/runbooks/duration-histograms.md` | No `docs/` directory in the repository |
| Explicit transactions | Rejected: *"explicit transactions are not supported; use auto-commit RUN queries"* |

## Confirmed working

- `algo.MSpaths` accepts `pairwise`, `fairRelationshipVariants`, `resultLimit`
  (`src/query/path_procedure.rs:182`). Unknown config keys are rejected outright.
  `fairRelationshipVariants` requires an unweighted pairwise query.
- Bolt auth accepts `bearer` and `basic` (`src/client/bolt.rs:1164`). We use bearer
  with the contents of the auth-token file.
- Bookmarks work as documented: a causal read supplied with a write's bookmark sees
  the write.
- Admin metrics on `:9090` return a parseable Prometheus exposition (63 metrics on a
  fresh node).
- UNION allows up to 256 arms (`src/query/opencypher.rs:16`), read-only, with per-arm
  `ORDER BY` and `LIMIT`.

## Undocumented but real: guarded merge from Cypher

`cypher-compat.md` does not mention it, but guarded merge is reachable through
marker properties inside an `UNWIND` vertex upsert:

```cypher
UNWIND $rows AS row MERGE (n {id: row.vid})
  SET n:Fact,
      n.__hydradb_update_if_newer_by     = row.asserted_at,
      n.__hydradb_create_only_first_seen = row.first_seen
```

Markers are `__hydradb_update_if_newer_by` and `__hydradb_create_only_<property>`
(`src/query/opencypher.rs:18-20`, test at `:3928`). The full rule set, from
`validate_merge_policy` (`:1722`) and verified live in slice 03:

- Every guarded property must also be SET from a row field of the **same name**:
  `n.last_seen = row.last_seen` alongside `__hydradb_update_if_newer_by = row.last_seen`.
- A create-only marker requires an update guard to be present as well.
- The guard property may not itself be create-only. Using one row field for both
  produces two contradictory errors depending on which check runs first
  (*"create-only markers require an update guard"* / *"update guard cannot also
  be create-only"*), which is a confusing pair to hit.

Behaviour is as advertised: replaying a row with an older guard value leaves every
property untouched, and a create-only property keeps its value from first write.

**The comparison is strictly less-than** (`guarded_metadata_patch`,
`src/shard/write.rs:5225` — `if ordering != Ordering::Less { return Ok(None) }`).
An *equal* guard value applies nothing, which is not a detail:

- A fact upsert guarded on `asserted_at` becomes immutable on replay, because a
  replay always carries the same `asserted_at`. That is why supersession is
  materialized by a second statement guarded on `valid_to` (0 -> timestamp,
  strictly increasing) rather than by rewriting the fact.
- The same property makes the materialization durable: a later re-ingest cannot
  reset `status` back to `current`, because its patch is rejected as not-newer.

Guard markers are honoured **only inside the `UNWIND` vertex upsert form**. A
`MATCH ... SET` write has no guarded equivalent, so any write that must not move
a value backwards has to be expressed as a batched upsert.

**Upstream issue candidate:** implemented and tested, absent from the compatibility
document. Anyone reading only the docs would conclude the feature is unreachable —
which is exactly the conclusion we reached before reading the parser.

## Constraints found by running, not by reading (slice 03)

Three that the compatibility document does not state and that a parse-time probe
with empty parameters will not catch:

- **A node carrying a label or a non-id property must be named.**
  `MATCH (:Fact {id: 1})-[r:SUBJECT]->(e)` is rejected with *"node labels and
  non-id properties require a named node"*; binding it as `(f:Fact {id: 1})`
  is accepted. Anonymous nodes are fine when they carry neither.
- **An edge batch is rejected whole if any endpoint does not exist**, with
  *"MATCH endpoint vertex N with label L does not exist"* — a syntax-class error
  for a data-class condition. Node batches must therefore land before edge
  batches, and a dangling row fails loudly rather than being skipped. That is
  the behaviour we want, but it means an edge statement that passes the empty-row
  inventory probe can still fail on real rows.
- **An `UNWIND` row must carry every field the statement names** (*"UNWIND row 0
  is missing field last_seen"*). Adding a property to an upsert is a breaking
  change for every caller, not an optional extension.

## The mutation idempotency key does not police conflicts on the Cypher path

The plan assumed that reusing `hydradb.idempotency_key` with different content
returns an explicit non-retryable conflict. Verified against a live node: it does
not. Two `UNWIND` upserts sent under one key with genuinely different rows were
both accepted and both applied.

`GraphError::IdempotencyConflict` is real (`src/codec.rs`, mapped to
`Neo.ClientError.Transaction.Invalid` in `src/client/bolt/values.rs:243`), but it
is raised by storage-level operations — batch import dedupe, segment compaction,
indexer scopes — not by a Bolt Cypher mutation. The Bolt layer derives a
per-principal key (`src/client/bolt.rs:1336`) and uses it for durable write
deduplication only.

Consequence for us: the second idempotency layer has to come from the key itself.
`ingest.batch_key` hashes the rows it sends, so one key cannot name two payloads.
The `IdempotencyConflict` mapping in `client.py` stays as a net in case a future
build starts enforcing it. **The plan's claim should not be repeated in the
README as a HydraDB guarantee.**

## Deletion is not a usable primitive at our scale

`MATCH (n:Fact) WHERE n.instance_id = $i DETACH DELETE n` parses and runs, but it
costs roughly **0.3 seconds per node**: 17 entities took 10.0s, 40 sessions 13.2s,
and 207 facts hit the server's own limit — *"client_query_runtime exceeded query
timeout after 29999 ms"* — after deleting part of the set. The `UNWIND` batch form
is rejected outright (*"UNWIND batch node patterns do not support labels"*), so
there is no batched delete to fall back to.

Consequence: there is no per-tenant reset. Since fact ids are content-derived, a
changed extractor adds a second generation of facts rather than replacing the
first, and the only reset is the whole node — stop the container, remove
`hydradb-data/`, start it again. Worth knowing before designing anything that
assumes cheap retraction.

## `EXPLAIN` is not reachable over the wire

`cypher-compat.md` describes `EXPLAIN` as "a cheap way to check a query before it
goes near data", but `explain_opencypher_rows` exists only on the in-process shard
API (`src/shard/query.rs:521`) — there is no Bolt or HTTP path to it.

Consequence for us: statement validation needs a live node, which is why the
statement inventory executes rather than explains.

**Upstream issue candidate:** exposing it over Bolt would make client-side query
linting possible without a running write path.

## Setup: the published image beats the source build

The plan budgeted eight hours of Day 1 for building from source (Rust 1.91,
`libcypher-parser`, SuiteSparse GraphBLAS) and flagged toolchain failure as a
Medium risk. A published image exists at `ghcr.io/hydra-db/hydradb:latest` with a
working `docker run` recipe in the README, which takes about forty seconds and
needs no Rust at all. On Windows this is the difference between working and not:
the source recipe covers Ubuntu/WSL and macOS only.

We drive it from `docker-compose.yml`, with an init service that creates
`LOCAL_PATH` and seeds the auth token so `docker compose up -d` is sufficient from
a clean clone.

`RUST_MIN_STACK=33554432` is genuinely required — without it the node serves
`/readyz` and then aborts on the first query.
