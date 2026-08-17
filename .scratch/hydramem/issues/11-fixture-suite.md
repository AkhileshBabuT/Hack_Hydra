# 11 — 25-case fixture suite, 10 abstention

Status: done

## Parent

`.scratch/hydramem/PRD.md`

## What to build

The development loop. Twenty-five hand-written cases running against a live node in under
ten seconds, because the full benchmark is far too slow to iterate against and every slice
after this one needs a fast signal.

At least ten cases are abstention cases, with each of the four gate reasons plus the
uncited-answer downgrade covered by a dedicated case. A gate that has no fixture proving it
fires is a gate that will silently stop working.

The remaining cases cover the answerable categories: single-session recall, a
knowledge-update pair asserting current-versus-past, a multi-hop path, and a preference.

## Seed the suite from what actually failed

Slice 06 measured three defects on real data. They are cheaper to turn into
fixtures than to rediscover:

- a mis-slotted functional predicate retracting a true fact (`name: 'silver
  Honda Civic'`)
- a fact sourced from an assistant turn rather than a user turn (`ec81a493`)
- a session whose extraction collapsed and was recovered only by the retry

## Acceptance criteria

- [ ] Twenty-five fixtures exist and the suite completes in under ten seconds
- [ ] At least ten are abstention cases
- [ ] Each of the four gate reasons fires on its own dedicated fixture
- [ ] The uncited-answer downgrade has its own dedicated fixture
- [ ] A knowledge-update fixture asserts the current value plainly and the old value when scoped to a past window
- [ ] The suite runs from a single make target
- [ ] At least one fixture reproduces a defect slice 06 measured on real data

## Blocked by

08, 09, 10

## Result

Done. `tests/test_fixtures.py`, 25 cases + 6 assertions about the suite itself,
**31 passed in 8.4s** against the live node. `make fixtures`. Full suite 223 →
**254**.

13 abstention cases and 13 answering cases. Every reason code has its own
dedicated fixture and that coverage is asserted rather than eyeballed
(`test_the_suite_covers_every_reason_code`): `unknown_entity`,
`no_such_relation`, `no_fact_in_window`, `no_path`, `uncited_answer`,
`fabricated_citation`, `not_in_graph`.

### Live graph, stubbed model — and why that split

Real HydraDB throughout: real writes, real reads, a real `algo.MSpaths` call,
real round trips counted. Only `llm.complete` is scripted, per case.

The thing under test is the cascade's *guarantee* — which gate fires, on what,
at what cost. A fixture whose verdict depends on what a 550B model happened to
emit proves nothing about a guarantee and cannot run in ten seconds. Same line
slice 10 drew in `test_answer.py`; the difference is that there the graph was a
dict and here it is the database.

Citations are resolved out of the *prompt body* rather than out of the database,
so a case citing `employer | Globex` also proves that fact was in the context
the model was handed — which is the precondition the citation check tests.

### Every case asserts its round-trip count

`Case.trips` is checked on all 25, so the budget table in CLAUDE.md is now
pinned end to end rather than at two points in `test_paths.py`:

| case shape | trips |
|---|---|
| gate 1 fires | 2 |
| gates 2, 3, 5 fire, or a single-entity answer | 3 |
| gate 4 fires, or a two-entity answer | 4 |

### Three worlds, content-addressed

`main` (3 sessions, 11 facts) carries every question. `islands` exists because
gate 4 needs two entities that genuinely do not connect and **the corpus cannot
supply that** — its graph is a star, which is the standing gap. `MS_PATHS` walks
`SUBJECT` and `OBJECT` only, so two people with one literal-valued fact each are
unreachable at any hop count. `void` is a tenant deliberately never written to.

`instance_id` is `fx-<world>-<sha256 of the world>[:8]`. Fact ids are
content-derived, so editing a fixture would otherwise write a second generation
beside the first in the same tenant and inflate every count — the trap that makes
an extractor change need a node wipe. Hashing the world into the tenant means an
edited world lands in a *new* tenant and the stale one is orphaned. It costs a
few kilobytes and removes a whole class of wipe.

### Slice 06's defects reproduced live, not described

Two of the three, both through real ingest → chain → answer:

- **The mis-slotted functional predicate.** `name: 'silver Honda Civic'` asserted
  in 2023 against `name: 'Akhil'` asserted in 2019. `name` is functional, so the
  car does not add noise beside the true name — it **supersedes** it, and the
  test asserts `evidence[0]["supersedes"]` is non-empty. Extraction error
  amplified by the chain rather than diluted by it.
- **The assistant-sourced fact.** `prefers: 'Choose a harmonious frame'` filed
  against an assistant turn. It answers, it cites correctly, `explain` marks it
  `<- ASSISTANT-SOURCED`, and nothing rejects it. Issue 19 pinned live in a
  fixture rather than only on instance `ec81a493`.

The third (a collapsed decode recovered by the retry) is already pinned as its
literal payload in `test_extract.py` and is an extractor-level failure, not a
cascade-level one. Not duplicated here.

### The ten seconds, and where they were going

First cut ran **15.8s**. Measured rather than guessed:

| read | cost | rows |
|---|---|---|
| `ENTITIES_FOR_INSTANCE` | 15.6 ms | 5 |
| `ALIASES_FOR_INSTANCE` | **150.0 ms** | **0** |
| `FACTS_FOR_INSTANCE` | 131.2 ms | 11 |

Trivial single-node read: 35 ms. So the two edge-pattern reads cost ~4× a scalar
read, and **half the latency of every question in this project is an alias read
that has returned zero rows on every instance ever measured**. That is the
already-known alias gap showing up as a cost number for the first time — carried
to slice 14 rather than fixed here, because the fix is a gate-1 change and not a
measurement.

Setup was the other half: 15 write batches at ~380 ms each = 5.7s. The tenant is
the hash of the world, so "already there" and "identical" are the same
statement — one `COUNT_FACTS` read replaces the writes when the tenant is
already populated. 5.7s → 0.1s.

The budget is asserted from **after** world setup, not from module import. A
world edited since the last run pays the one-off ingest ahead of the loop, and
folding that in would make the test fail once at random after every fixture
change.

## Acceptance criteria

- [x] Twenty-five fixtures exist and the suite completes in under ten seconds
- [x] At least ten are abstention cases
- [x] Each of the four gate reasons fires on its own dedicated fixture
- [x] The uncited-answer downgrade has its own dedicated fixture
- [x] A knowledge-update fixture asserts the current value plainly and the old value when scoped to a past window
- [x] The suite runs from a single make target
- [x] At least one fixture reproduces a defect slice 06 measured on real data
