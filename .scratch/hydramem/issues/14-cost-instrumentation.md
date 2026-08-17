# 14 — Cost and latency instrumentation table

Status: done

## Parent

`.scratch/hydramem/PRD.md`

## What to build

The brief asks for read and write cost that would survive real usage, so this is a scored
deliverable rather than a nicety.

Scrape HydraDB's admin metrics endpoint and join it with the harness's own timings. **Read
carefully before building any latency view — the duration histograms deliberately use
different units, and getting this wrong produces confidently wrong numbers.** The runbook the
plan expected to cover this does not exist in the HydraDB repository; determine the units
from the metric definitions directly and note the documentation gap for slice 16.

Report tokens per question, median and tail latency, Bolt round trips per question, and
ingest and query cost per unit for every arm.

Round trips per question is the headline: a single batched path call should put HydraMem far
below what a naive graph implementation needs, and that gap is the HydraDB argument in one
number.

Run the live demo path on causal consistency so latency reflects the real hot path, in
contrast to evaluation which pins strong.

## Acceptance criteria

- [ ] Metrics are scraped from the admin endpoint and joined with harness timings
- [ ] Histogram units are verified from the metric definitions rather than assumed, and the unit used is stated
- [ ] Tokens per question, median and tail latency, and round trips per question are recorded for every arm
- [ ] Ingest and query cost per unit are recorded
- [ ] The demo path runs on causal consistency while evaluation pins strong, and both are stated
- [ ] The cost table is committed as a CSV

## Blocked by

09, 12

## Result

Done. `scripts/cost_table.py` + `client.histograms`. One long-format CSV
(`docs/eval/oracle-cost.csv`) joining three sources: per-question harness rows,
the ingest ledger, and HydraDB's admin metrics.

### The histogram units, verified rather than assumed

The issue warns the duration histograms use different units. They do, and it is
worse than "different" — they are the **same bucket ladder scaled by 1e6**:
`graph_client_operation_read_duration_seconds` has `le="0.0001"` where
`graph_query_rows_duration_microseconds` has `le="100"`. Same bound. Assume one
unit for both and every latency is wrong by six orders of magnitude while the
table still looks like a table.

The runbook this issue expected does not exist, as predicted. Determined from the
metric definitions instead — `crates/telemetry/src/meter.rs` @ `6a2fbb1`:

- The kernel measures in microseconds and **only** in microseconds.
  `HistogramUnit` converts at the export boundary and nowhere else, and **the
  same enum value picks both the name suffix and the scaling** (`render_bound`,
  `scale_sum`). So the suffix is authoritative, not conventional.
- `HistogramUnit::Seconds` "exists for exactly one instrument" —
  `db.client.operation.duration`, whose OTel semantic convention fixes seconds.
- `CounterUnit` has **no** seconds variant, deliberately: scaling a counter would
  make it disagree with the `_microseconds` series rendered from the same field,
  "and nothing downstream could detect the factor of a million".

`client.histograms` therefore reads the suffix and states the unit it used in the
`note` column of every server row. It sums over label sets, because
`graph_query_rows_duration_microseconds` labels every sample with scope and
cell_id and `client.metrics` skips labelled samples on purpose. Pinned in
`test_cost.py` with both units on the same ladder.

Cross-checked rather than trusted: the read-histogram mean matched an independent
direct timing (139.6 ms vs 131-150 ms measured by hand), and the two read series
carry identical sample counts.

### What it found — a 30x read-path defect

Building this table is what exposed the largest performance defect in the
project. Every instance-scoped read was written as one pattern with the filter in
the WHERE:

```cypher
MATCH (f:Fact)-[:SUBJECT]->(e:Entity) WHERE f.instance_id = $instance_id
```

HydraDB builds that join across **every tenant in the store** and filters
afterwards, so **read latency scales with the size of the whole store, not the
tenant being read**. Measured on a store holding 2,122 Fact nodes:
**7,635 ms to return 11 rows.** Splitting the MATCH so the automatic property
index on `instance_id` drives a single-node scan first returns the same 11 rows
in **250 ms**.

There is no index DDL and `EXPLAIN` is unreachable over Bolt, so clause order is
the only lever available, and nothing else would ever catch a regression — the
query keeps returning correct rows, just slower as the store fills. Applied to
all five joining reads and pinned as a shape assertion in `test_statements.py`.

Effect, with **identical accuracy (0.4333) and identical answers**:

| | median | p95 | fixture suite | full suite |
|---|---|---|---|---|
| before | 9,335 ms | 59,250 ms | 228 s | 524 s |
| after | **1,858 ms** | **2,358 ms** | **9.96 s** | **148 s** |

### The table

Query cost, n=150 per arm:

| arm | tokens/q | round trips/q | median | p95 |
|---|---|---|---|---|
| full_context | 5,540 | 0 | 26,445 ms | 65,046 ms |
| vector_rag | 2,494 | 0 | 23,718 ms | 97,734 ms |
| hydramem | **605** | **2.96** | **1,858 ms** | **2,358 ms** |

**Round trips per question is the headline, and it is a distribution, not a
mean:** 10 questions cost 2, 136 cost 3, 4 cost 4. **Never five.** The bound
holds because gate 4 resolves every anchor pair in one batched `algo.MSpaths`
call — n anchors are n(n-1)/2 pairs in one call, where a naive graph memory
issues a traversal per pair. That structural claim is pinned in `test_paths.py`;
this table is the measured distribution behind it.

The baselines show 0 round trips because they hold no graph. That is a
comparison of shapes, not of two like numbers, and the CSV says so in the note
column rather than leaving a bare 0.

Ingest cost per unit, 149 instances / 255 sessions / 1,798 facts:

| metric | value |
|---|---|
| facts per session | 7.05 |
| extraction tokens per fact | 675 |
| Bolt round trips per fact | **0.513** |
| latency per fact | 332 ms |
| parse failures | 0 |

Round trips per fact is below one because writes are batched `UNWIND` statements,
so it falls as an instance grows — the opposite of the naive per-fact write.

Server-side, units resolved and stated:
`graph_client_operation_read_duration_seconds` (seconds),
`graph_client_operation_write_duration_seconds` (seconds),
`graph_query_rows_duration_microseconds` (microseconds).

### Consistency

Stated on every latency row rather than in prose: evaluation pins **strong** so
scores reproduce, the demo path (`scripts/ingest_one.py`) runs **causal** because
that is the real hot path. Pinned in
`test_cost.py::test_the_consistency_mode_rides_on_every_latency_row`.

Ingest rows are deduplicated by instance and query rows by (arm, question) —
two processes resuming one arm append the same row twice, and without that the
table divides every per-unit cost by the wrong n and still looks reasonable.

## Acceptance criteria

- [x] Metrics are scraped from the admin endpoint and joined with harness timings
- [x] Histogram units are verified from the metric definitions rather than assumed, and the unit used is stated
- [x] Tokens per question, median and tail latency, and round trips per question are recorded for every arm
- [x] Ingest and query cost per unit are recorded
- [x] The demo path runs on causal consistency while evaluation pins strong, and both are stated
- [x] The cost table is committed as a CSV
