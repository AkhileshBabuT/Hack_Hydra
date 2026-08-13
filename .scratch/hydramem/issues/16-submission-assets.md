# 16 — Submission assets — docs, upstream issues, video, fresh-clone verify

Status: ready-for-human

## Parent

`.scratch/hydramem/PRD.md`

## What to build

Everything the judges actually receive. Feature freeze applies: no new functionality in this
slice.

Documentation covers a README whose setup instructions genuinely work on a clean machine, a
capability-mapping document tying each HydraDB feature used to the real code that uses it, an
architecture note with the schema, and a notes file recording the rough edges found.

Three genuine upstream issues were surfaced during planning and filing them is explicitly a
judging asset: guarded-merge marker syntax is implemented but absent from the compatibility
document; the query-plan check is reachable only in-process and not over the wire; and the
runbook referenced for metric histogram units does not exist in the repository.

The single-page UI is held to a hard four-hour timebox — it is a demo surface, not the
product. Ask a question, see the answer or the abstention reason, see the fact path, see the
supersession chain.

The video is capped at three minutes because content past that may not be reviewed, and it
must follow problem, project, demo, then HydraDB in that order. The demo section shows a
knowledge-update with the chain on screen, an abstention with its reason beside the baseline
confidently hallucinating the same question, and a multi-hop path with provenance on each
hop.

Submit at six in the evening, not at the deadline.

## Acceptance criteria

- [ ] README setup instructions are verified by a clean clone on a different machine reaching a working demo
- [ ] The capability-mapping document ties every claimed HydraDB feature to real code
- [ ] Three upstream issues are filed against the HydraDB repository
- [ ] The single-page UI ships within a four-hour timebox showing answer or abstention reason, fact path and supersession chain
- [ ] The video is under three minutes, follows problem then project then demo then HydraDB, and is accessible without login verified in a private window
- [ ] The repository is public, Apache-2.0 licensed, and carries no commit before Aug 12 2026
- [ ] The submission form is completed and submitted ahead of the deadline

## Blocked by

13, 14, 15
