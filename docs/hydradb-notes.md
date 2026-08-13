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
(`src/query/opencypher.rs:18-20`, test at `:3928`). A create-only marker must read
the same row field as the property it names.

**Upstream issue candidate:** implemented and tested, absent from the compatibility
document. Anyone reading only the docs would conclude the feature is unreachable —
which is exactly the conclusion we reached before reading the parser.

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
