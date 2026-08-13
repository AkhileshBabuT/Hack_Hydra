# 08 — Temporal gate — value-at-T and change history

Status: ready-for-agent

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

- [ ] Current value resolves via the head of the supersession chain
- [ ] Value-at-a-past-time filters on the validity interval and treats an open interval as unbounded
- [ ] Change history walks the chain and returns an ordered sequence with change times
- [ ] A question with an unsatisfiable window abstains with the resolved window in the detail
- [ ] The same knowledge-update question returns the current value plainly and the old value when scoped to a past window
- [ ] Temporal filters are unit-tested as pure functions over fact lists
- [ ] New statements are registered in the statement inventory and pass the verify target

## Blocked by

05, 07
