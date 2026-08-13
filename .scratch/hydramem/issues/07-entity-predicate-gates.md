# 07 — Entity and predicate gates with reason strings

Status: ready-for-agent

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

## Acceptance criteria

- [ ] The entity gate resolves against stored entities and the alias closure, and abstains naming the missing entity
- [ ] The predicate gate fetches an entity's facts in one round trip and filters the candidate set in Python
- [ ] The predicate gate abstains naming both the entity and the missing predicate
- [ ] Both gates are unit-tested as pure functions over fact lists with no database and no model call
- [ ] Each gate's reason string is asserted by a dedicated test
- [ ] New statements are registered in the statement inventory and pass the verify target

## Blocked by

03
