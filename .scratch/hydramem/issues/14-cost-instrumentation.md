# 14 — Cost and latency instrumentation table

Status: ready-for-agent

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
