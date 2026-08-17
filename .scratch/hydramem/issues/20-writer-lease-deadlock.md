# 20 — A stale writer lease deadlocks every write, permanently, on `CLOUD_PROVIDER=local`

Status: done — closed by slice 17; upstream draft ready to file

## Parent

`.scratch/hydramem/PRD.md`

## Why this exists

Found on 2026-08-15 by running the full suite after `docker compose up -d`
recreated the containers. The suite went from the recorded **312 passing** to
**23 failed, 283 passed, 6 errors** with no source change in between — the
working tree was untouched since the last green run.

Every single failure was a **write**. `test_statements.py` failed on exactly the
eleven mutating statements (`upsert_entity`, `upsert_fact`, `upsert_session`,
`close_fact`, and every `link_*`) and passed every read. `test_paths.py` errored
in fixture setup, which writes. Reads were never affected.

The node reports it as an opaque server error:

```
neo4j.exceptions.DatabaseError: {code: Neo.DatabaseError.General.UnknownError}
{message: internal query execution error}
```

The real error is only in the container log:

```
object store error: Operation `put_opts` with mode `PutMode::Update`
not yet implemented by LocalFileSystem(file:///data/store)
```

## What it actually is

Not the data, not the image, not our Cypher. It is the **writer lease**, and the
mechanism is in `src/engine/writer_lease.rs` @ `6a2fbb1`:

- Acquiring the lease writes `.../\_writer\_leases/v2/cell-0` with
  `PutMode::Update(version)` when a lease already exists, `PutMode::Create` when
  it does not (`:266-269`).
- `LocalFileSystem` does not implement conditional update, so that call returns
  `NotImplemented`.
- There **is** a fallback, and it is guarded: `Err(NotImplemented) if same_holder`
  overwrites unconditionally (`:270-276`). The comment states the intent —
  *"Overwrite is safe only for the still-valid incumbent; stale takeovers remain
  fail-closed because they require real compare-and-swap."*
- `same_holder` compares `holder_id`, and `process_holder_id()` is
  `PROCESS_HOLDER_ID.get_or_init(|| Ulid::new().to_string())` (`:760-764`) — **a
  fresh random ULID per process**.

So a new process can never be `same_holder` with the lease its predecessor left
behind. The fallback cannot apply, the takeover needs compare-and-swap,
`LocalFileSystem` cannot provide it, and the node is **fail-closed for writes
forever**.

A clean shutdown releases the lease — the release path deletes the object when it
sees `NotImplemented` (`:687-695`). So this only bites when the previous process
did *not* exit gracefully: a `docker compose down` that reaches SIGKILL, a crash,
a host reboot, or a container recreate.

## Verified, not assumed

Same image (`ghcr.io/hydra-db/hydradb:latest`, created 2026-08-12,
`sha256:db78309a`), two stores:

| store | create | update | repeat |
|---|---|---|---|
| existing `hydradb-data/` (stale lease present) | FAIL | FAIL | FAIL |
| fresh volume, no lease | ok | ok | ok |

The fresh-volume probe ran on a throwaway container on port 7688 and was removed
afterwards. It rules out the image and rules out store size — a brand-new node id
fails on the real store, and an update of an existing node succeeds on the fresh
one.

## The fix

Delete the single lease object; the next acquisition takes the `PutMode::Create`
branch:

```
hydradb-data/store/graph/data/namespaces/default/graphs/default/_writer_leases/v2/cell-0
```

Stop the node first. It holds no data — it is a lock. Wiping the whole store also
works and is what slice 17 needs anyway, but it is not required *for this*, and
the distinction matters: losing 1,936 facts to a stuck lock would be a bad trade
made out of a wrong diagnosis.

## Acceptance criteria

- [x] The lease is cleared and the full suite returns to 312 passing
- [x] `docs/hydradb-notes.md` records the symptom, the log line and the one-file fix
- [x] CLAUDE.md warns that an ungraceful stop deadlocks writes, and names the file
- [ ] Filed upstream (drafted, needs a human account) — this is the fourth upstream issue and the most severe

## Why it is worth filing upstream

The failure is silent, permanent and misattributed. Over Bolt the client sees
only `internal query execution error`, which reads as a query problem; the actual
cause is a lock file and a backend capability gap, and nothing in the client-side
error names either. A single-node local deployment — the documented quickstart
configuration — becomes permanently read-only after one unclean stop, with no
diagnostic pointing at the lease. The guard comment shows the fail-closed
behaviour is deliberate for correctness, so the bug is not the guard: it is that
`LocalFileSystem` offers no recovery path and the error surfaced to the client
does not mention the lease.

## Blocked by

Nothing. It blocks **15** and **17**, which both write.

## Result

Closed. Diagnosed, fixed, documented, and drafted for upstream.

- The store was cleared and the suite returned to **312 passing in 28.6s**,
  confirming the diagnosis end to end: all 23 failures and 6 errors were one
  stale lock file, not 29 defects.
- `docs/hydradb-notes.md` records the symptom, the log line, the source
  references and the one-file fix.
- CLAUDE.md warns that an ungraceful stop deadlocks writes and names the file.
- `docs/upstream-issues.md` #1 is the draft, filed as the most severe of four.

### Worth keeping

The client-visible error (`internal query execution error`) named neither the
lease nor the object store, and every failing test was a *write* while every read
passed. That shape — reads fine, writes uniformly dead, no source change — is the
signature. Check `docker logs` before reading it as a code regression, and check
it before wiping: the fix is one file, and wiping the store over a stuck lock
loses the corpus for nothing.
