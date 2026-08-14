# 07 — Entity and predicate gates with reason strings

Status: done

## Parent

`.scratch/hydramem/PRD.md`

## What to build

The first two gates of the cascade, each returning a machine-readable reason rather than a
boolean.

The entity gate resolves the question's named entities against stored entities and the alias
closure for the instance. Any required entity absent means abstain with the missing entity
named. This gate catches a large share of unanswerable questions at near-zero cost with no
hallucination surface at all.

The predicate gate asks whether the resolved entity has any fact with a required predicate.
HydraDB has no `IN` operator, so one match pulls every fact attached to the entity and the
candidate predicate set is applied in Python. This is not merely a workaround: this gate is
the primary suspect whenever abstention precision fails, so it must be testable without a
database. An entity in a chat history carries few enough facts that overfetching is free.

Both gates must return the specific entity or predicate that was missing, not just a reason
code — that detail is what makes an abstention debuggable.

## Measured input from slice 06

Numbers, not guesses. `docs/extraction-quality.md`, 20 instances / 38 sessions.

- **96.9% of facts sit on `person:user`.** The tail is 8 entities of 2-3 facts
  each. The entity gate therefore fires on almost nothing in this corpus, and
  the alias closure it resolves through is empty for the same reason: slice 05
  produced 0 `ALIAS_OF` edges. Verify `unknown_entity` against real data before
  trusting it — a gate that never fires is indistinguishable from a gate that is
  broken.
- **The predicate gate's input is unreliable in a way it cannot see.** 24.4% of
  facts land on `other`, and mis-slotting is confirmed, not theoretical:
  `name: 'silver Honda Civic'` and `budget: '2-Day General Admission'`. A
  question asking for `name` will find a fact with that predicate and the gate
  will pass on a value that is not a name. Pinned in
  `tests/test_chain.py::test_a_mis_slotted_functional_predicate_retracts_a_true_fact`.

## Acceptance criteria

- [x] The entity gate resolves against stored entities and the alias closure, and abstains naming the missing entity
- [x] The predicate gate fetches an entity's facts in one round trip and filters the candidate set in Python
- [x] The predicate gate abstains naming both the entity and the missing predicate
- [x] Both gates are unit-tested as pure functions over fact lists with no database and no model call
- [x] Each gate's reason string is asserted by a dedicated test
- [x] A test covers the measured case where the required predicate is present but mis-slotted
- [x] New statements are registered in the statement inventory and pass the verify target

## Blocked by

03

## Result

`hydramem/gates.py`, wired into `answer.py` through `check_gates()`. Two reads
per question — `ENTITIES_FOR_INSTANCE` and `ALIASES_FOR_INSTANCE`, both new and
both in the inventory — then the cascade runs over plain lists. When a gate
fires no model is called at all, which is the whole point: there is nothing for
it to confabulate with.

`Result` carries a new `gate_detail` field. `unknown_entity` says a gate fired;
`unknown_entity: person:maya chen` says which assumption was wrong.

### No model in the gates, deliberately

Entity and predicate detection are lexical: capitalised runs for names,
first-person cues for the user, and a literal cue table for predicates. A gate
whose job is to stop confabulation cannot itself be a language model without
inheriting the failure it exists to prevent.

The cost is bluntness, and the module documents which way it is blunt.
**An unrecognised question passes.** A false abstention is an answer thrown away
with no way to notice; a false pass costs one model call and still meets the
citation check. `"What is my blood type?"` passes for exactly this reason —
nothing in the cue table reaches it.

### Both gates verified firing on real data

The issue's own warning was that a gate which never fires cannot be told apart
from a broken one. Ingested `gpt4_2655b836` (3 sessions, 26 facts, 1 entity) and
probed the live node:

| question | verdict |
|---|---|
| What was the first issue I had with my new car…? | PASS → `person:user` |
| Did Maya Chen ever call me back? | **ABSTAIN** `unknown_entity: maya chen` |
| Where does Priya work? | **ABSTAIN** `unknown_entity: priya` |
| Where do I work? | **ABSTAIN** `no_such_relation: person:user has no employer` |
| Am I allergic to anything? | **ABSTAIN** `no_such_relation: person:user has no allergic_to` |

Slice 05's finding holds and is now load-bearing rather than incidental: this
instance has exactly **one** entity, `person:user`, so gate 1 fires only on
entities the question introduces. The alias closure is empty on real data, so
`test_an_alias_resolves_to_its_canonical_entity` proves it synthetically or
nothing does.

### A bug the unit tests could not have caught

`question_predicates` matched a predicate if *any* word of its name appeared in
the question. `to` is a word in both `subscribes_to` and `allergic_to`, so every
question containing "to" wanted both — gate 2 found a held predicate on almost
anything and stopped firing. Found by probing the live graph, not by the suite.
Fixed by dropping glue words from predicate names, pinned in
`test_gates.py::test_a_glue_word_in_a_predicate_name_does_not_match`.

The same shape appears in the cue table: `"work" in "homework"` is true, so cues
match on word boundaries rather than as substrings.

### The blind spot, observed live

`"What is my sister's name?"` **passes** gate 2 on this instance. Its only
`name` fact is the one slice 06 measured: `name: 'silver Honda Civic'`. The slot
is filled, so the gate is satisfied, and its contents are a car.

This is not fixable inside gate 2 — it checks shape by construction. Recorded as
a characterisation test
(`test_gates.py::test_the_gate_cannot_see_a_mis_slotted_value`) and inherited by
whatever eventually does a value check. Gates 3 and 4 do not close it either.

### What slice 08 and 09 join

`gates.run()` is the cascade and short-circuits on the first failure; adding a
gate is appending to it. `gates.facts_reader()` already memoises the per-entity
fetch, so a later gate re-reading the same entity costs nothing.
