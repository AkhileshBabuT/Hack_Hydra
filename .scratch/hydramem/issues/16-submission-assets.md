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

## Result

Partially done. The documentation and upstream-issue criteria are closed; the
ones that need a human, a camera or a second machine are **not**, and are listed
as such rather than quietly marked complete.

### Done

- **`README.md`** — did not exist at all. Written: quickstart from a clean clone,
  the two-line mem0 swap, the gate table, layout, licensing. It deliberately
  quotes **no accuracy number**, pointing at `docs/eval/` instead, so it cannot
  drift out of agreement with the generated tables.
- **`docs/capability-map.md`** — every claimed HydraDB capability tied to the
  function that exercises it and the test that fails when it stops working,
  including a "deliberately not used" section (`DETACH DELETE`, `EXPLAIN`, index
  DDL, dynamic labels) so an absence reads as a decision rather than an omission.
- **`docs/architecture.md`** — the schema, both clocks, the three supersession
  modes, the write path and the read cascade.
- **`docs/upstream-issues.md`** — **four** drafts, not three. The fourth
  (writer-lease deadlock, issue 20) was found during this slice and is the most
  severe: it makes the documented single-node configuration permanently
  read-only after one unclean stop. Drafted but **not filed** — filing posts
  publicly under a real account.
- **The single-page UI** — served at `GET /` by `hydramem.server`. Shows the
  answer *or the abstention reason*, the gate trace, the facts the answer rests
  on, and the supersession chain. One inlined string, no build step, no
  framework, well inside the four-hour timebox.
- **Submission safety, re-checked rather than assumed:**
  - no commit predates Aug 12 2026 — verified across all refs;
  - `.env` appears in no commit — verified;
  - no `nvapi-` key in history. Note the naive check `git log -S'nvapi-'`
    **returns a hit**: CLAUDE.md's own sentence *about* `nvapi-` contains the
    string. Use the key shape `nvapi-[A-Za-z0-9_-]{20,}`, which is clean.
  - `LICENSE` is Apache-2.0.

### Not done — needs a human

- **Clean-clone verification on a different machine.** The README's instructions
  are written to be correct and have not been executed on a machine that has
  never built this. That is the only thing that actually tests them, and it
  cannot be done from inside this one.
- **Filing the four upstream issues.** Drafts are ready in
  `docs/upstream-issues.md`.
- **The video.** Under three minutes, problem → project → demo → HydraDB, and
  verified accessible in a private window. The demo section now has all three of
  its beats available live: a knowledge update with the chain on screen
  (`89941a93`, `three bikes → four bikes`), an abstention with its reason, and
  provenance per fact.
- **The submission form**, at six in the evening rather than at the deadline.

### Note for the video

The knowledge-update beat works end to end as of slice 17 and did not before:
`89941a93` now forms 2 SUPERSEDES edges where it formed 0, so the chain is
actually visible on screen rather than being described.
