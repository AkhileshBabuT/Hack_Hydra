# Upstream issue drafts for `hydra-db/hydradb`

Ready to file. Not filed by the agent — filing posts publicly under a real
account, which is the maintainer's call.

All four were found by building against `6a2fbb1` and are reproducible. Ordered
by severity, which is not the order they were discovered.

---

## 1. A stale writer lease makes a `CLOUD_PROVIDER=local` node permanently read-only

**Severity: high.** The documented single-node quickstart configuration stops
accepting writes after one unclean shutdown, and nothing the client can see says
why.

### Reproduce

1. Run the published image with `CLOUD_PROVIDER=local`, `LOCAL_PATH=/data/store`.
2. Write anything.
3. Kill the container without a graceful stop (`docker compose down` that reaches
   SIGKILL, `docker rm -f`, a host reboot, or a container recreate).
4. Start it again and write anything at all — even a brand-new node id.

### Expected

The new process takes over the lease and writes succeed.

### Actual

Every write fails. Reads are unaffected. Over Bolt the client is told only:

```
Neo.DatabaseError.General.UnknownError: internal query execution error
```

The cause appears only in the server log:

```
object store error: Operation `put_opts` with mode `PutMode::Update`
not yet implemented by LocalFileSystem(file:///data/store)
```

### Analysis

In `src/engine/writer_lease.rs`:

- lease acquisition uses `PutMode::Update(version)` when a lease object exists,
  `PutMode::Create` otherwise (`:266-269`);
- `LocalFileSystem` implements no conditional update, returning `NotImplemented`;
- the unconditional-overwrite fallback is guarded `if same_holder` (`:270-276`),
  commented *"stale takeovers remain fail-closed because they require real
  compare-and-swap"*;
- `process_holder_id()` is `Ulid::new()` per process (`:760-764`).

A restarted process therefore can **never** be `same_holder` with the lease its
predecessor left, so the fallback cannot apply and the takeover needs a
compare-and-swap the backend does not provide. The state is unrecoverable
without deleting `…/_writer_leases/v2/<cell_id>` by hand.

The graceful-release path already handles the same gap correctly — it deletes the
object on `NotImplemented` (`:687-695`) — so the failure is specific to unclean
exits.

### Verified

Same image (`sha256:db78309a`), two stores: the existing store failed even a
brand-new node id (create, update, repeat — all fail); a fresh volume accepted
all three. That rules out store size and the image build.

### Suggested fixes, in preference order

1. Treat a lease whose expiry has passed as takeable on backends without CAS,
   since a stale lease from a dead process is exactly the case that cannot be
   resolved otherwise.
2. Surface the object-store cause in the Bolt error. `internal query execution
   error` sends every user looking at their Cypher.
3. Document the recovery step (delete the lease object) in the local-deployment
   section.

---

## 2. Guarded-merge markers are implemented but absent from `cypher-compat.md`

**Severity: medium (documentation).**

`__hydradb_update_if_newer_by` and `__hydradb_create_only_first_seen` work from
Cypher inside the `UNWIND … MERGE … SET` vertex-upsert form — implemented in the
parser (`opencypher.rs:18-20`, test at `:3928`) — and appear nowhere in the
compatibility document.

They are the difference between idempotent ingest and duplicated state under
at-least-once delivery, so a user who cannot find them will build a read-then-write
cycle instead, which is both slower and racy.

Behaviour worth documenting explicitly, all verified live:

- every guarded property must also be `SET` from a row field of the **same name**;
- a create-only marker requires an update guard to be present;
- the guard property may not itself be create-only;
- the comparison is **strictly less-than**, so an equal guard value writes
  nothing — which makes a replay a no-op and is the property most likely to
  surprise (adding a new property to an existing node via re-upsert silently does
  nothing if the guard value is unchanged);
- the markers work **only** in the `UNWIND` vertex-upsert form; `MATCH … SET` has
  no guarded equivalent.

---

## 3. `EXPLAIN` is unreachable over Bolt and HTTP

**Severity: medium.**

Query plans are available only on the in-process shard API. Over the wire there
is no way to check a plan, and since there is also no index DDL, clause order is
the only performance lever a client has.

That combination makes a specific regression undetectable: an instance-scoped
read written as one pattern with the filter in the `WHERE` returns *correct rows*
and scales with the whole store instead of the tenant. Measured at 2,122 Fact
nodes: 7,635 ms versus 250 ms for the same 11 rows, purely from filtering the
node before joining — a 30x difference with no error, no warning, and no plan to
inspect.

Exposing `EXPLAIN` over Bolt would make client-side query linting possible
without a running write path.

---

## 4. Histogram unit suffixes are authoritative but the referenced runbook does not exist

**Severity: low (documentation).**

The metrics endpoint serves `graph_client_operation_read_duration_seconds` and
`graph_query_rows_duration_microseconds` side by side **on the same bucket ladder
scaled by 1e6**, so `le="0.0001"` and `le="100"` denote the same bound.

The behaviour is correct and deliberate — `HistogramUnit` converts at the export
boundary and the same enum value picks both the name suffix and the scaling
(`render_bound`, `scale_sum`), which is what makes the suffix authoritative. The
problem is that the runbook the code refers to for this
(`docs/runbooks/duration-histograms.md`) does not exist in the repository; there
is no `docs/` directory at all.

Anyone scraping these without reading `crates/telemetry/src/meter.rs` has a
factor-of-a-million error available to them, and the code's own comment notes
that "nothing downstream could detect" it.
