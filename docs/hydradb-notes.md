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

## A stale writer lease deadlocks every write, permanently (slice 17)

The most severe rough edge found, and the only one that stops the project dead.

**Symptom.** After `docker compose up -d` recreated the container, the suite went
from 312 passing to 23 failed / 6 errors / 283 passed with no source change.
Every failure was a write — all eleven mutating statements in the inventory
(`upsert_*`, `link_*`, `close_fact`) failed and every read passed. Over Bolt the
client is told only:

```
Neo.DatabaseError.General.UnknownError: internal query execution error
```

which reads as a query defect and is not one. The real cause appears **only** in
`docker logs`:

```
object store error: Operation `put_opts` with mode `PutMode::Update`
not yet implemented by LocalFileSystem(file:///data/store)
```

**Cause.** Not the data, not the image, not our Cypher — the writer lease.
In `src/engine/writer_lease.rs` @ `6a2fbb1`:

- acquiring uses `PutMode::Update(version)` when a lease object already exists,
  `PutMode::Create` when it does not (`:266-269`);
- `LocalFileSystem` implements no conditional update, so that returns
  `NotImplemented`;
- there is a fallback that overwrites unconditionally, guarded `if same_holder`
  (`:270-276`), commented *"stale takeovers remain fail-closed because they
  require real compare-and-swap"*;
- `process_holder_id()` is `Ulid::new()` **per process** (`:760-764`).

A restarted node therefore can never be `same_holder` with the lease its
predecessor left behind, the fallback cannot apply, and the takeover needs a
compare-and-swap the backend does not have. Fail-closed, permanently.

A clean shutdown releases the lease — the release path deletes the object when it
sees `NotImplemented` (`:687-695`) — so this bites only after an ungraceful stop:
a `down` that reaches SIGKILL, a crash, a host reboot, a container recreate.

**Verified both ways**, same image (`sha256:db78309a`, created 2026-08-12):

| store | create | update | repeat |
|---|---|---|---|
| existing store, stale lease present | FAIL | FAIL | FAIL |
| fresh volume, no lease | ok | ok | ok |

A brand-new node id fails on the real store and an update of an existing node
succeeds on the fresh one, which rules out both store size and the image.

**Fix — one file, not the store:**

```
hydradb-data/store/graph/data/namespaces/default/graphs/default/_writer_leases/v2/cell-0
```

Stop the node first. It is a lock and holds no data.

**Upstream issue candidate — the most severe of the four.** The documented
single-node quickstart configuration (`CLOUD_PROVIDER=local`) becomes permanently
read-only after one unclean stop, and nothing the client can see says so. The
fail-closed guard is correct for correctness; the defects are that
`LocalFileSystem` offers no recovery path at all, and that the error surfaced
over Bolt names neither the lease nor the object store.

## The MSpaths selector is the only scopable part of the query (slice 17)

`CALL algo.MSpaths({...}) YIELD path RETURN path` **is** the whole query — the
native path parser ends with `parser.end()`, so no `WHERE` and no `LIMIT` may
follow. There is therefore nowhere to put a tenant filter, and the selector's
property match is the only lever.

On the obvious property (`Entity.key`) every tenant's `person:user` is a source,
so a nominally two-anchor traversal is pairwise over one node **per tenant in the
store**. Two distinct failures followed, both measured here:

- **Timeout.** At ~53 ingested tenants, under `hydradb.consistency = strong`, the
  call exceeded the 30s query timeout and killed a 150-question evaluation arm
  mid-run. The client sees `Neo.ClientError.Transaction.Terminated`.
- **Silent wrong answers.** At ~160 tenants the call still *returned*, but
  `resultLimit: 64` filled with other tenants' paths and the caller's own path
  was crowded out. Filtering results client-side then yields an empty list, which
  reads as `no_path` — a structural claim, from a query that simply ran out of
  budget. Two fixture tests that had passed for weeks began failing with no code
  change.

The second is the dangerous one: no error, no warning, and the failure mode is an
*abstention*, which looks like the system working correctly.

Fixed by adding an instance-scoped selector property, `skey = "<instance_id>|<key>"`,
and matching `sourceProperty: 'skey'`. The traversal now starts and stays inside
one tenant.

Two costs worth recording:

- Adding the property to `UPSERT_ENTITY` broke **every existing caller
  immediately** — `UNWIND row 0 is missing field skey`, at parse time. Loud and
  early, which is the good case.
- It needed a **node wipe**. `UPSERT_ENTITY` is a guarded merge on `last_seen`,
  and the guard is strictly less-than, so re-ingesting unchanged data has an
  *equal* guard value and writes nothing — the new property would never reach
  existing nodes. A guarded upsert makes schema addition a migration, not a
  backfill.
