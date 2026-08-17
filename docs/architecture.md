# Architecture and schema

What the graph holds, how a fact gets there, and how a question gets out.

## The schema

Six node kinds' worth of meaning across two labels, and **six edge types** —
kept few deliberately, because HydraDB builds CSC generations per edge type and
`SUBJECT`/`OBJECT` carry the traversal load.

```
(:Entity {id, key, name, type, instance_id, first_seen, last_seen})
(:Fact   {id, fact_id, instance_id, session_id, turn_idx, role,
          predicate, value_text, snippet,
          asserted_at, valid_from, valid_to, status})
(:Session {id, key, idx, timestamp, instance_id})

(:Fact)-[:SUBJECT]->(:Entity)        who the fact is about
(:Fact)-[:OBJECT]->(:Entity)         an entity-valued object, when value_is_entity
(:Fact)-[:ASSERTED_IN]->(:Session)   provenance
(:Session)-[:NEXT]->(:Session)       chronological order
(:Fact)-[:SUPERSEDES]->(:Fact)       a revision points at what it replaced
(:Entity)-[:ALIAS_OF]->(:Entity)     surface-form closure
```

`id` is an integer on every node because HydraDB requires it and `MERGE` matches
on `id` alone. Every string identity is hashed by `ids.nid` — 60 bits of SHA-256
— and the canonical string is kept as a property (`key`) for display and as the
MSpaths selector. This is what makes `MERGE` idempotent with **no id allocator
and no lookup round trip**: the same logical entity computes the same id in any
process, on any run.

`instance_id` is the tenant partition. In the corpus it is the LongMemEval
`question_id`; through the mem0 surface it is `user_id`. They are the same thing.

### Two clocks, hence "bitemporal"

| field | meaning |
|---|---|
| `asserted_at` | when the system was told — the session timestamp |
| `valid_from` | when the fact became true in the world |
| `valid_to` | when it stopped being true; **`0` means unbounded** |

`valid_to == 0` means *open*, not "ended at the epoch". Most facts are open, so
reading it as a real end date empties every window — the single line in
`temporal.overlaps` worth re-reading before changing anything.

Vague dates collapse to assertion time at ingest, deliberately:
`resolve_valid_from` parses numeric shapes only, and "last summer" falls back to
the session timestamp. At ingest there is no reliable anchor for a seasonal
phrase, and a confidently wrong precise date is worse than an honest approximate
one because gate 3 filters on exactly that field. Relative phrasing is resolved
at *query* time instead, where `asked_at` is a real anchor.

## Writing: extraction → rows → guarded batches

```
Session text
  -> extract.extract_session      one cached model call, controlled predicates
  -> ingest.build_rows            PURE: rows for every statement, ids computed
  -> chain.derive / materialize    PURE: supersession pairs -> close + edge rows
  -> ingest.write_rows            batched UNWIND, guarded, idempotency-keyed
```

`build_rows` and `chain.derive` are **pure functions over plain dicts**, so
supersession is reproducible from a fact list with no database and no model.
That is why `test_chain.py` can assert permutation invariance directly: shuffle
the facts, get an identical chain.

### Why nothing is ever mutated

A revision is a new `Fact` node plus a `SUPERSEDES` edge. The old fact is closed
— `status='superseded'`, `valid_to` set — and stays in the graph. So:

- *"where do I work"* is the head of the chain,
- *"where did I work"* is the rest of it,

and they are two traversals of one structure rather than two prompting
strategies. `delete` is the same move with `status='deleted'`: a tombstone, not
destruction. Deletion also happens to be impractical here (`DETACH DELETE` runs
at ~0.3s per node and a few hundred exceed the 30s query timeout) but that is
not the reason.

### What supersedes what

`chain.group_key` decides the slot, and has three modes:

| predicate kind | slot | effect |
|---|---|---|
| functional (`employer`, `age`, …) | `(entity, predicate)` | a new value retracts the old |
| counted (`quantity`) | `(entity, predicate, thing-counted)` | `three bikes → four bikes` chains; `17 cameras` does not |
| everything else | `(entity, predicate, value)` | a new `likes` accumulates; a restatement collapses |

Treating every predicate as functional marked 193 of 220 facts superseded on a
real instance. Giving counts their own mode was slice 17: as plain `other` they
formed **0** SUPERSEDES edges on the very category supersession exists for.

### Idempotence comes from content, not from headers

Fact ids are content-derived and `UPSERT_FACT` is a **guarded merge** on
`asserted_at`, which is always equal on replay — and HydraDB applies a guarded
patch strictly when stored < incoming. So re-ingesting identical input writes
nothing, and a re-ingest cannot resurrect a superseded fact to `current`.

The corollary is a trap: **changing the extractor requires a wiped node.** New
prompt → new content → new ids → a second generation of facts beside the first,
with every count silently inflated.

## Reading: the cascade

```
question
  -> gate 1 entity      lexical mentions must be something the graph has heard of
  -> gate 2 predicate   the entity must hold a fact of a shape the question asks
  -> gate 3 window      a resolved time window must contain a fact
  -> gate 4 path        multi-entity only: one batched algo.MSpaths call
  -> [model]            every fact in the instance, narrowed only by the window
  -> gate 5 citation    every cited fact_id must be in what was retrieved
```

Gates 1–4 run **before** the model and short-circuit, so a question already lost
costs no further round trips. `gates.facts_reader` returns `(all_facts,
facts_for)` from **one lazy instance-wide read** shared by gates 2 and 3 *and*
the answer — reading per entity and then re-reading to answer is what used to
make four trips reachable.

**No model runs inside a gate.** A gate whose job is to stop confabulation
cannot itself be a language model without inheriting the failure it exists to
prevent. The price is bluntness, and its direction is fixed: an unrecognised
question **passes**. A false abstention is an answer thrown away with nothing to
notice it; a false pass costs one model call and still has to survive gate 5.

### Cost

| gate | reason code | round trips to reach |
|---|---|---|
| 1 entity | `unknown_entity` | 2 empty instance, else 3 |
| 2 predicate | `no_such_relation` | 3 |
| 3 window | `no_fact_in_window` | 3 (read already in hand) |
| 4 path | `no_path` | 4 |
| 5 citation | `not_in_graph`, `uncited_answer`, `fabricated_citation` | 0 |

Counted rather than estimated: `client.round_trips()` increments where the round
trip happens, and `answer.Result` reports the difference across the call.

## The trace

`gates.run` accumulates one line per check that ran; `answer.explain` renders it
beside the evidence. It is **pure** — it reads a `Result` and touches neither
driver nor model — so an explanation cannot disagree with the answer it explains.
Gate 4 traces as `skipped`, not `pass`, on a single-entity question, because the
trace is an audit record of what was *spent*, not only of what was decided.
