# 09 — Path gate — one MSpaths call, round-trip counter

Status: done

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

- [x] Multi-hop retrieval issues exactly one batched path call regardless of anchor count
- [x] Zero paths within the bound abstains with pairs-tried and the bound in the detail
- [x] A round-trip counter is instrumented and reports at most four round trips per question
- [x] Result budget is distributed fairly across structural paths
- [x] New statements are registered in the statement inventory and pass the verify target

## Blocked by

07

---

## Result

`hydramem/paths.py` builds and runs the one batched call; `gates.path_gate` is
gate 4; `client.round_trips()` is the counter. `statements.MS_PATHS` is the new
statement, registered in its *assembled* form because its anchor list cannot be
a parameter.

### The round-trip budget, counted

| question | trips | why |
|---|---|---|
| Did Maya Chen ride with me? | **2** | lost at gate 1; facts never read, no path call |
| How many bikes do I have? | **3** | one entity, so nothing to connect |
| …did I have in February 2023? | **3** | same, plus window narrowing (22 facts → 8) |
| Where in the San Francisco Bay Area do I ride? | **4** | two entities → one batched path call |

Measured on the live instance `89941a93` through `answer.answer_question`, which
reports `round_trips` on every `Result`. Pinned in `test_paths.py` at 2 / 3 / 4.

Getting to four meant a deletion, not an addition: slice 07 fetched facts *per
resolved entity* and then fetched the same rows again for the answer.
`gates.facts_reader` now hands back one lazy instance-wide fetch shared by gates
2 and 3 and the answer itself. `FACTS_FOR_ENTITY` had no callers left and is
gone. Twenty anchors cost the same four trips as two.

### Verified against the parser, then live

`src/query/path_procedure.rs` decides the shape and all of it was confirmed on
a live node:

- The call is the **whole query** — `parse_native_path_call` ends with
  `parser.end()`, so no `WHERE`, no `LIMIT`, nothing may follow the `RETURN`.
- `sourceValues` / `targetValues` / `relTypes` are **literal lists**;
  `config_string_list` never resolves a `$parameter`. `maxLen` and
  `resultLimit` *are* parameterizable, so the bound stays a bound.
- Unknown config keys are rejected outright, which is why the inventory entry
  matters and why it registers the assembled statement.
- `fairRelationshipVariants` requires pairwise MSpaths and rejects
  weightProp / costProp / maxCost. It is what round-robins the result budget
  across structural paths (`fair_relationship_variant_candidates`, base quota
  plus remainder), so one hyper-connected entity cannot take the response.
- `path` comes back as a **flat list**: `[node-map, 'EDGE_TYPE', node-map, …]`.
  Node maps carry every property except `id`; Fact maps carry `fact_id`.

### The security finding this slice had to handle

The MSpaths selector matches `(:Entity {key: …})` and `key` **is not
instance-scoped** — every tenant in the store has a `person:user`. With no
`WHERE` available in the statement, an unfiltered result connects entities
through other people's graphs. Measured on this node: the anchor pair
`person:user` / `org:acme` matched **6 tenants and returned 6 paths, 1 of them
ours**. `paths.scoped()` drops the rest and is not optional.

The ceiling this leaves: the traversal *work* is still shared across tenants, so
roughly (N-1)/N of the result budget is spent on paths that are then discarded,
and with enough tenants sharing an anchor key a real path could be crowded out
of the budget and read as `no_path`. At the measured 6 tenants against a budget
of 64 there is a wide margin. Closing it needs an instance-scoped property on
Entity, which costs a node wipe.

Separately, anchor keys are interpolated into query text because they cannot be
parameters, and they derive from model-extracted entity names. `paths.literal`
escapes backslash and quote — exact, per the lexer's escape rule — and refuses
control characters rather than stripping them. Pinned with an injection attempt
(`user', targetValues: ['admin`) in `test_paths.py`.

### Where gate 4 is weak, measured

Gate 4's **pass** path is verified live on `89941a93`. Its **abstention** path
is verified live only against the hop bound: the same anchors that connect at
`maxLen=4` return nothing at `maxLen=1`, and the gate then reports
`no_path: person:user, org:acme: 1 pairs, no path within 1 hops`.

What is *not* proven is a genuinely unreachable pair on real data, and the
reason is structural. This corpus produces a star: 96.9% of facts sit on
`person:user`, and every other entity exists only as the OBJECT of a fact whose
SUBJECT is the user. Any two entities are therefore ≤4 hops apart through the
user, so at the default bound gate 4 is close to vacuous on this data.
Measured: `89941a93` holds 2 entities (1 pair, connected at length 2),
`gpt4_2655b836` holds 1 (no pair at all, so no path call is issued).

Lowering `MAX_LEN` to 2 would make it fire, and would be wrong — it would
abstain on "did I use Strava in San Francisco", which the graph answers through
the user. The honest position is that gate 4 is the cheapest of the four and
currently the least load-bearing, and that it becomes load-bearing when entity
resolution does (issue: the alias closure is still unproven on real data).
