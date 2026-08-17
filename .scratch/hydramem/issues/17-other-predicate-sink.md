# 17 — The `other` predicate is a sink that disables gate 2 and the chain

Status: done — closed by slice 17; counted predicate, 0 -> 2 SUPERSEDES on 89941a93

## Parent

`.scratch/hydramem/PRD.md`

## Why this exists

Slice 06 measured `other` at 24.4% of facts and closed it as *"the controlled
vocabulary was confirmed adequate, not extended — the off-vocabulary tail has no
cluster worth a new predicate."* That reading was about the tail's *shape*. It
did not look at what `other` does to the rest of the system, and slice 08's live
probe found that out.

Measured on instance `89941a93` (knowledge-update, 2 sessions 231 days apart,
22 facts, gold answer `4`):

- **20 of 22 facts are `other`.** Among them `three bikes` (Feb 2023) and
  `four bikes` (Oct 2023) — which *are* the knowledge update the instance is
  built around.
- **Gate 2 abstains on the instance's own question.** "How many bikes do I
  currently own?" wants `owns`; the entity holds no `owns` fact, so the answer
  is `no_such_relation: person:user has no owns`. A false abstention on a
  question the graph demonstrably answers — with the window narrowing from
  slice 08 in place, the model returns "four bikes" correctly.
- **The chain never forms.** `other` is not in `FUNCTIONAL_PREDICATES`, so it
  chains per distinct value; `three bikes` and `four bikes` are distinct
  values, so both stay `current`. 22 facts, **0** `SUPERSEDES` edges, on a
  knowledge-update instance. The supersession machinery slice 05 built is
  invisible on exactly the category it exists for.

So `other` is not a neutral overflow bucket. It is a hole that both gate 2 and
the chain fall through, and its 24.4% share is a measure of how much of the
graph is structurally inert.

## What to build

Not "add more predicates" reflexively — measure first, then decide.

1. Re-run `scripts/measure_extraction.py` and cluster the `other` values by
   what a question would *ask* for, not by surface similarity. Slice 06
   clustered by the latter and found nothing; `three bikes` and `four bikes`
   only cluster once you ask "which predicate would a question reach for".
2. Decide between two fixes, or both:
   - extend `extract.PREDICATES` with the clusters that recur (`owns` already
     exists and would have taken the bikes — this may be a prompt problem, not
     a vocabulary problem), or
   - make gate 2 aware that `other` is a sink, so an entity whose facts are
     overwhelmingly `other` cannot produce a confident `no_such_relation`.
     Note this widens the gate and costs abstention precision; measure it.
3. Whatever changes, the chain consequence has to be re-measured: does a
   knowledge-update instance now produce `SUPERSEDES` edges?

## Cost

**Changing the extractor prompt or vocabulary needs a wiped node.** Fact ids are
content-derived, so a new prompt writes a second generation of facts beside the
first and every count silently inflates. `docker compose down`,
`rm -rf hydradb-data`, `docker compose up -d`, then re-ingest. That is why slice
08 did not take this on.

## Acceptance criteria

- [ ] The `other` tail is clustered by *asked-for predicate*, and the result is written down
- [ ] A knowledge-update instance produces at least one `SUPERSEDES` edge
- [ ] "How many bikes do I currently own?" on `89941a93` does not abstain `no_such_relation`
- [ ] Whatever the gate-2 decision is, its effect on abstention precision is measured, not assumed
- [ ] `docs/extraction-quality.md` is regenerated against the wiped-and-reingested node

## Pinned by

`tests/test_gates.py::test_other_is_a_sink_that_silently_disables_gate_2`

## Blocked by

06, 08

## Result

Done, and it flipped the thesis gate from NO-GO to **GO**. The `other` tail was
clustered first, as the issue demanded, and the fix that followed was not the one
"extend the vocabulary" would have suggested.

### 1. The `other` tail, clustered by asked-for predicate (n=633, live store)

| n | share | cluster | what a question asks for |
|---|---|---|---|
| 355 | 56.1% | descriptor / unclassified (heuristic is crude) | mixed |
| 121 | 19.1% | named specifics — `City View Rooftop`, `Fender Mustang I V2` | the *theme-not-specific* bug |
| 68 | 10.7% | **bare counts** — `4 engineers`, `3,000 points`, `three bikes` | the chain hole |
| 61 | 9.6% | recommendations / advice | `single-session-assistant` |
| 15 | 2.4% | topic discussed | — |
| 13 | 2.1% | plan | **vocabulary already existed** — a prompt problem, not a vocabulary one |

`other` was 32.7% of 1,936 facts, not the 24.4% slice 06 recorded.

### 2. The fix, which is a chain fix and not a vocabulary fix

Adding predicates was **not** the lever. `owns` already existed and would have
taken the bikes; the counts were mis-routed, not unnameable. And re-labelling
alone would have changed nothing, because the reason `three bikes` and
`four bikes` do not chain is not their label — it is that **both grouping modes
are wrong for a count**:

- functional → one count per entity, so `17 cameras` retracts `four bikes`;
- per-value → every count its own slot, so nothing ever supersedes.

So `chain.group_key` gained a **third mode**. `extract.COUNTED_PREDICATES`
(`quantity`) keys on the value with its leading count stripped
(`extract.LEADING_COUNT`), making the slot *the thing being counted*. A bare
number falls back to the whole value so numbers do not collapse together.

Gate 2 needed `CUES["quantity"]` in the same change, or the fix would have traded
one false abstention for another: moving counts out of `other` removes the
`via_other` pass that was carrying the bikes question.

### 3. Acceptance

- [x] The `other` tail is clustered by asked-for predicate and written down — above
- [x] A knowledge-update instance produces at least one SUPERSEDES edge —
      `89941a93`: **0 → 2**, on 20 facts
- [x] "How many bikes do I currently own?" does not abstain `no_such_relation` —
      answers `four bikes` against gold `4`, 3 round trips. (It had already
      stopped abstaining: slice 12's `other` guard fixed the gate, and the chain
      is what was still broken.)
- [x] Gate-2 effect measured, not assumed — see the table below
- [x] `docs/extraction-quality.md` regenerated against the wiped node

### 4. Measured (oracle, n=150 per arm)

| arm | acc | abs prec | abs rec | tok/q | trips/q | median | p95 |
|---|---|---|---|---|---|---|---|
| full_context | 0.6200 | **0.4211** | 0.8000 | 5,540 | 0 | 26,445 ms | 65,046 ms |
| vector_rag | **0.6467** | 0.4154 | 0.9000 | 2,494 | 0 | 23,718 ms | 97,734 ms |
| hydramem | 0.4933 | 0.3023 | 0.8667 | **893** | 2.97 | **1,483 ms** | **6,641 ms** |

`verdict()` clears **2 of 3** against full_context → **GO**. The check that
flipped is knowledge-update accuracy, **0.6923** against 0.5769 — previously a
tie, and now ahead of vector_rag's 0.6538 as well. That is the category this
slice aimed at, so the result is causally coherent rather than a lucky sample.

Still lost: abstention precision, 0.3023 against 0.4211. Still true: vector RAG
is the most accurate arm overall.

Three changes shipped in one wipe-and-rescore cycle — counted predicate, named
specifics in the prompt, gate 4's anchor fix — and they are **not separately
attributed**.

### 5. Two defects found on the way, both worse than the one being fixed

**Gate 4 was abstaining on a false premise** (now `paths.NON_TOPICAL_KEYS`).
Slice 12's `person:assistant` is a second star sharing no node with
`person:user`, so any question resolving to assistant-sourced content asked gate
4 whether the user and the assistant are connected. They never are. This slice
*surfaced* it: preserving named specifics made more entities, so gate 4 started
firing where it used to skip for want of a second anchor.

**And it killed the run.** Both keys are hubs, the MSpaths selector is not
instance-scoped, and pairwise traversal under `strong` consistency blew the 30s
query timeout at question 54 of 150. A timeout now sets `timed_out` and **passes**
the gate — a traversal that ran out of time established nothing, and `no_path`
from it would assert an absence nothing checked. p95 fell 12,233 ms → 6,641 ms.

### Inherited by a later slice

- **A count filed under `owns` still does not chain.** Found by running
  `scripts/mem0_swap_demo.py`: "I own three bikes" extracts as `owns = three
  bikes`, and `owns` is neither functional nor counted, so the hole simply moved
  from `other` to `owns`. Adding `owns` to `COUNTED_PREDICATES` is *strictly*
  safe — `LEADING_COUNT` only strips when a count is present, so a value without
  one groups exactly as it does today — but it changes chain derivation, so it
  needs its own wipe-and-rescore rather than being smuggled into this one.
- **`single-session-assistant` is 2 of 20** against 20/20 and 18/20. The prompt
  rule demonstrably preserves the names now (`The Witcher 3: Wild Hunt`,
  `@jessica_poole_jewellery` are in the review sheet verbatim) and it did **not**
  convert to accuracy, so the failure is downstream of storing the right string
  and has not been located. Find where those 18 die before writing another prompt.
- **7 of 150 instances still extract zero facts** (was 8), with 2 parse failures.
