# 11 — 25-case fixture suite, 10 abstention

Status: ready-for-agent

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

## Acceptance criteria

- [ ] Twenty-five fixtures exist and the suite completes in under ten seconds
- [ ] At least ten are abstention cases
- [ ] Each of the four gate reasons fires on its own dedicated fixture
- [ ] The uncited-answer downgrade has its own dedicated fixture
- [ ] A knowledge-update fixture asserts the current value plainly and the old value when scoped to a past window
- [ ] The suite runs from a single make target

## Blocked by

08, 09, 10
