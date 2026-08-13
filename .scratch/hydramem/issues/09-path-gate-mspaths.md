# 09 — Path gate — one MSpaths call, round-trip counter

Status: ready-for-agent

## Parent

`.scratch/hydramem/PRD.md`

## What to build

Multi-hop questions resolved by a single batched path call rather than one round trip per
candidate anchor. This is the central HydraDB-specific performance claim and it needs a
number attached.

The call resolves all candidate anchor entities against all targets in one pairwise
invocation, bounded in length and in result count, distributing the result budget fairly
across structural paths so that one hyper-connected entity cannot consume the whole
response. Zero paths within the bound means abstain, with the number of pairs tried and the
bound in the detail.

Config keys are validated by HydraDB and unknown keys are rejected outright, so the
inventory check matters here. Fair-variant distribution requires an unweighted pairwise
query.

Instrument Bolt round trips per question explicitly. It is the single best number in the
cost story and it only means something if it is counted rather than estimated.

## Acceptance criteria

- [ ] Multi-hop retrieval issues exactly one batched path call regardless of anchor count
- [ ] Zero paths within the bound abstains with pairs-tried and the bound in the detail
- [ ] A round-trip counter is instrumented and reports at most four round trips per question
- [ ] Result budget is distributed fairly across structural paths
- [ ] New statements are registered in the statement inventory and pass the verify target

## Blocked by

07
