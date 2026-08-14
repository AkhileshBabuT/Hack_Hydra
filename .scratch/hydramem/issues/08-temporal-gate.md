# 08 — Temporal gate — value-at-T and change history

Status: done

## Parent

`.scratch/hydramem/PRD.md`

## What to build

The gate that makes the two hardest benchmark categories answerable by structure.

Three distinct queries over the same bitemporal fact set. The current value is the head of
the supersession chain. The value at a past time filters on the validity interval, treating
an open interval as unbounded. The change history walks the chain and returns the ordered
sequence with the time of each change.

When a question carries a time window and no fact satisfies it, abstain with the resolved
window in the detail — an abstention that names the window it searched is auditable; one
that just says no is not.

The demonstrable property this slice delivers: the same question asked plainly returns the
current value, and asked scoped to a past window returns the old one.

## Acceptance criteria

- [x] Current value resolves via the head of the supersession chain
- [x] Value-at-a-past-time filters on the validity interval and treats an open interval as unbounded
- [x] Change history walks the chain and returns an ordered sequence with change times
- [x] A question with an unsatisfiable window abstains with the resolved window in the detail
- [x] The same knowledge-update question returns the current value plainly and the old value when scoped to a past window
- [x] Temporal filters are unit-tested as pure functions over fact lists
- [x] New statements are registered in the statement inventory and pass the verify target

## Blocked by

05, 07

---

## Result

`hydramem/temporal.py` — window resolution and the three bitemporal reads, all
pure functions of a fact list. Gate 3 (`gates.temporal_gate`) wraps them and
joins `gates.run()`; `answer.py` narrows retrieval to the resolved window.
`statements.SUPERSESSION_CHAIN_FOR_INSTANCE` is the new statement, registered
and passing the inventory probe.

### The demonstrable property, on real data

Ingested `89941a93` (knowledge-update, 2 sessions 231 days apart, 22 facts,
gold `4`). Same graph, same model, three questions:

| question | window resolved | facts sent | answer |
|---|---|---|---|
| How many bikes do I have? | — | 22 | **four bikes** |
| …did I have in February 2023? | `2023-02-01..2023-03-01` | 8 | **three bikes** |
| …did I have in October 2023? | `2023-10-01..2023-11-01` | 22 | **4** |
| …did I have in 2020? | `2020-01-01..2021-01-01` | 0 | **ABSTAIN** `no_fact_in_window` |

The plain question returns the current value and the scoped one returns the old
value, which is the property this slice exists to deliver. Note the third row:
a February fact is open-ended, so it is still valid in October — `valid_to == 0`
means unbounded, and reading it as a real end date would empty every window.

### Two decisions the slice inherited

**A restatement no longer moves the start date forward.** Decided in favour of
keeping the earliest, and implemented at *read* time (`temporal.since` walks
back over the unchanged run) rather than at ingest. Fact ids are
content-derived, so changing what `valid_from` is written as needs a wiped node;
deciding it at read time means the decision can be revisited without rebuilding
the graph. Pinned in
`test_temporal.py::test_a_restatement_does_not_move_the_start_date_forward`.

**Vague dates still collapse to assertion time at ingest, deliberately.**
`resolve_valid_from` parses numeric shapes only and this slice did not extend
it. "Last summer" has no reliable anchor at ingest — hemisphere, fiscal year,
and the speaker's own vagueness all bear on it, and a confidently wrong precise
date is worse than an honest approximate one, since the temporal gate filters on
exactly that field. Relative phrasing is instead resolved at *query* time, where
`asked_at` is a real anchor: "last year", "two years ago", "last month" all
resolve, and without an `asked_at` they resolve to nothing rather than to the
wall clock, so an evaluation does not change answer in January.

### A real bug, invisible to the suite

`"How many bikes did I have in February 2023?"` abstained
**`unknown_entity: february`**. Gate 1's proper-noun detector read the month as
a name, found no such entity, and killed the question before gate 3 could see
it — so every temporal question carrying a capitalised month was lost behind
the gate slice 07 added. Fixed with a calendar stop-list in `gates.mentions`;
pinned in `test_gates.py::test_a_calendar_word_is_a_date_not_an_entity` and
`::test_a_real_name_beside_a_month_still_resolves`.

This is the second time the "probe a new gate against a real instance" rule has
paid for itself, and the second bug of that kind the unit tests could not have
found: both slices' real defects were in what the lexical layer *recognises*,
which fixtures cannot exercise because fixtures are written by whoever wrote the
recogniser.

### Measured and not fixed — handed to 17

`other` is a sink. On `89941a93`, 20 of 22 facts are `other`, including
`three bikes` and `four bikes`; gate 2 therefore abstains `no_such_relation:
person:user has no owns` on the instance's own question, and the chain produces
**0** `SUPERSEDES` edges on a knowledge-update instance. Slice 06's "the
vocabulary is adequate" was measured on the tail's shape and did not look at
what `other` does downstream. Written up as issue 17 (it needs a node wipe) and
pinned in `test_gates.py::test_other_is_a_sink_that_silently_disables_gate_2`.

### Also worth knowing

The oracle split's evidence sessions are frequently **same-day** — on
`gpt4_2655b836` all three sessions and all 26 facts sit on `2023-04-10`, so year
and month windows cannot discriminate there at all. Gate 3 still fires
correctly on that instance for windows *outside* the day, but any claim about
temporal precision has to be made against multi-month instances like
`89941a93`, or against the `_s` split.
