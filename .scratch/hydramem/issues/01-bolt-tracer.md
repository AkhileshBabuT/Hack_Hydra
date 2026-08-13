# 01 — Bolt tracer — write a fact, read it back by bookmark

Status: done

## Parent

`.scratch/hydramem/PRD.md`

## What to build

Stand up the repository and prove the full client path works end to end against a local
HydraDB node: connect over Bolt, write one Entity, one Fact and one SUBJECT edge, then read
the fact back on a causal read pinned to the bookmark returned by the write.

This slice also establishes the **statement inventory** — the single module where every
Cypher template the codebase can emit is declared, plus the make target that executes each
one against a throwaway node and asserts it is not rejected at parse time. Every later slice
adds its statements here. This exists because every HydraDB incompatibility found during
planning was a parse-time rejection, and `EXPLAIN` is not reachable over Bolt.

Node identity is a deterministic hash, so `MERGE` is idempotent with no allocator. This
shape is fixed and later slices depend on it:

```
nid(kind, key) = int.from_bytes(sha256(f"{kind}:{key}").digest()[:8], "big") >> 4   # 60 bits
Entity -> nid("E", f"{instance_id}|{entity_key}")
Fact   -> nid("F", idempotency_key)
Edge   -> nid("R", f"{src}|{TYPE}|{dst}")
```

Repository hygiene is part of this slice because it is a disqualification risk: Apache-2.0
license, public repo, and no commit dated before Aug 12 2026. There is no index DDL in
HydraDB and property indexes are automatic, so no index bootstrap is written.

## Acceptance criteria

- [x] A one-command script brings up a local HydraDB node from a clean shell, with every required environment variable set including the large minimum stack size
- [x] Writing an Entity, a Fact and a SUBJECT edge over Bolt succeeds and the write returns a bookmark
- [x] A causal read supplying that bookmark returns the written fact
- [x] The statement inventory module exists and the verify target executes every declared template against a throwaway node, failing loudly on any parse rejection
- [x] Identity hashing is covered by a test asserting the same input yields the same id across separate processes
- [x] The admin metrics endpoint is reachable and its output parses
- [x] Repository carries an Apache-2.0 license and no commit predates Aug 12 2026

## Blocked by

None - can start immediately
