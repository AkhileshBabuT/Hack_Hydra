# Gauntlet log

One line per tick. The only state the loop keeps.

| date | item | what moved | critic |
|---|---|---|---|
| 2026-08-16 | 21 per-reason abstention counts | `docs/eval/oracle-abstentions.csv` generated, 70 rows. `no_path` = **0**, `fabricated_citation`/`uncited_answer` = **0**, `not_in_graph` 41/27-false. `gates.REASONS` registry added. 97 driver-free tests pass. | FAIL → fixed: CLAUDE.md gate-4 section had no measurement (only Known gaps did). Re-verified. |
| 2026-08-16 | 22 appeal path | `plan_appeal` pure, `fact_line(width)`, appeal branch in `answer_question`, `--appeal` flag, `appeals`/`appeals_won` columns. Scores unchanged (appeal off by default). 108 tests. | **critic never ran** — sub-agent died on session usage limit. Item carried to next tick. |
| 2026-08-17 | 22 appeal path (carried) | Self-answered the critic's crux: for a non-windowed question the retry was **byte-identical** to the first call, so the disk cache returned the same refusal while recording `appealed=True`. `plan_appeal` now declines with "nothing new to show". 115 tests. | FAIL → all four findings fixed, see below |
| 2026-08-17 | 25 honest reporting | Audit found the refuted claim in **no** document. Real gap was `answerable_accuracy` generated and never read — now in CLAUDE.md's table with the refutation, plus the 86/60 abstention breakdown. 118 tests. | self-verified; every figure traced back to both CSVs |

## Tick 1 incident — a review sub-agent destroyed `hydramem/gates.py`

Recorded because the loop's own method caused it and the next tick must not
repeat it.

The critic sub-agent mutation-tested `abstention_reasons`, mutated `gates.py`
as well, and ran `git checkout -- hydramem/gates.py` to undo it. There was no
committed baseline: `HEAD` for that file is `82181d4` (slice 07), so the
checkout reset it past six slices of uncommitted work. `git fsck` found nothing
— it was never staged.

Recovered from `~/.claude/file-history/<session>/15a2ead19e4a381b@v2`. Verified
by the maintainer independently rather than on the agent's word:
`difflib.unified_diff(snapshot, current)` returns **one hunk, `@@ -177,0 +178,21
@@`, zero removed lines** — precisely the `REASONS` block and nothing else. The
agent's own reconstruction of that block's comment was replaced with the
original wording; the tuple was byte-exact.

Written up as a convention in CLAUDE.md under the repository section. The two
rules: brief sub-agents to copy into the scratchpad explicitly, and verify a
restored file by diffing against the snapshot, never by running the tests — a
green suite does not prove a comment or an unexercised branch survived.

**The standing risk is that the working tree is the work.** Slices 10–17 are
uncommitted. Any `git checkout`, `git stash`, `git reset --hard` or `git clean`
is unrecoverable for them. This came within one editor cache of losing six
slices.

## Tick 6 — gate 2 was the worst component, and it was measurable

`no_such_relation` fired **18 times, all 18 about `person:user`, 16 false**. Both
survivors were accidents: one abstained off a missing `quantity` label, the
other ("what did I bake for my uncle's birthday party") wanted a cake while the
cue table matched `birthday`. Precision *for the right reason* was 0 of 18.

Cause is structural — 2,254 facts across 38 predicates, half the mass in `other`
(25%) and `likes` (24%). The hub holds the whole life, so a missing label there
is the extractor's vocabulary gap, not the graph's silence. Same argument the
`other` guard already made, extended from one predicate to one entity.

**One lexical change, every accuracy number moved:**

| | before | after |
|---|---|---|
| accuracy | 0.4933 | **0.5467** |
| answerable accuracy | 0.4000 | **0.4667** |
| abstention precision | 0.3023 | **0.3421** |
| knowledge-update | 0.6923 | **0.7692** |

Abstention precision had never improved before. Gap to vector_rag 15.3 → 9.0.
No wipe needed — gate logic only, graph untouched, model calls cached.

Cost: gate 2 now fires 0 times, so two of four pre-model gates are inert on this
corpus. Recorded in CLAUDE.md rather than glossed.

## Tick 7 — the assistant-extraction fix, and three crashes on the way

**Diagnosis first, per CLAUDE.md's instruction not to guess.** Of the 20
`single-session-assistant` questions: 10 died at gate 1, 7 at gate 5, 3 correct,
and **zero answered-wrong**. Nothing was being mis-extracted; it was not being
extracted. Five instances held no facts at all, eight held facts with zero from
an assistant turn, and all three that answered correctly held four or five.

Cause was the extraction prompt's **opening line** — "facts about the user" plus
"the user is the subject of most facts ... unless a person, org or place" — which
outvoted the correct assistant rule eight lines below it.

**Result, paired on the identical 150 instances: 90 correct against 82.**

| | before | after |
|---|---|---|
| accuracy | 0.5467 | **0.6000** |
| selective accuracy | 0.7568 | **0.8267** |
| abstention recall | 0.8667 | **0.9333** |
| knowledge-update | 0.7692 | **0.8846** |
| single-session-assistant | 0.15 | **0.30** |
| `empty_graph` instances | 8 | **1** |
| tokens/q | 1,004 | **2,608** |

**The token claim died here.** Facts per instance 14.0 → 42.7, so HydraMem is now
more expensive than vector RAG. Recorded in CLAUDE.md; the 6.2x sentence is gone.

**Three crashes, three real defects**, each invisible until a cold-cache wipe:

1. Bolt driver created before the multi-minute extraction warm pass → stale
   routing table → `ServiceUnavailable` after paying for all 350 calls, with the
   node healthy and its logs clean. `main` now warms before connecting.
2. `answer.answer_question` was the one `complete_json` caller without a
   collapse retry, which CLAUDE.md had already mandated for every caller.
3. `max_tokens=1024` on the answering call, overrun **mid-citation-list** once
   the graph got denser. I first misdiagnosed this as (2). Raising the
   extractor's yield is an *answering* change too.

Bundled: prompt + token budget + retry. Not separately attributable, same as
slice 17.
