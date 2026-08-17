# 10 — Synthesis, citation verification, `explain()`

Status: done

## Parent

`.scratch/hydramem/PRD.md`

## What to build

The step that converts abstention from a prompt hope into a structural property.

Only after all gates pass is a model asked to answer, and it receives only the retrieved fact
subgraph. It is instructed to cite a fact identifier for every claim and to emit an explicit
abstention token when the facts do not answer the question.

Then the answer is **verified**: cited identifiers are parsed and checked against the
retrieved set. A substantive claim with no citation, or a citation naming an identifier not
in the retrieved set, is downgraded to abstention. A model cannot talk its way past this
because the check does not consult the model.

The explain method returns the fact path, source session, turn index, timestamp and the full
gate trace for any answer. Build it here rather than late — it is the primary debugging tool
for every slice that follows, and it is what lets a judge audit an answer in seconds.

## Measured input from slice 06

**The extractor attributes assistant suggestions to the user.** Instance
`ec81a493` produced three `prefers` facts quoting the assistant's own advice —
"Choose a harmonious frame", "Create a focal point" — as user preferences, which
the extraction prompt forbids outright. They reached the graph anyway.

This lands here rather than in 06 because the citation check is the only place
it can be caught. Grounding does not catch it: those three were flagged only
because the model reflowed the quote, and an exact copy of assistant text is
grounded by definition. A citation pointing at an assistant-sourced fact is a
correctly-cited wrong answer. Pinned in
`tests/test_extract.py::test_an_assistant_sourced_preference_is_caught_only_when_it_is_misquoted`.

## Acceptance criteria

- [x] Synthesis receives only the retrieved subgraph, never the raw history
- [x] An answer making a substantive claim with no citation is downgraded to abstention
- [x] An answer citing an identifier absent from the retrieved set is downgraded to abstention
- [x] The downgrade path is asserted by tests that do not depend on model behavior
- [x] Explain returns fact path, source session, turn index, timestamp and gate trace
- [x] Explain works for both answered and abstained questions
- [x] Explain surfaces the source turn's role, so an assistant-sourced fact is visible in the trace

## Blocked by

07

## Result

Five files: `answer.py` (the citation gate, provenance, `explain`), `gates.py`
(the trace), `statements.py` and `ingest.py` (the source turn's role),
`scripts/ingest_one.py` (the demo now prints `explain` instead of printing the
same fields by hand). Plus `tests/test_answer.py`, 14 pure tests.

### The citation check is now strict, and that was a decision

Slice 09 left it lenient: an answer survived if *any* cited id was in the
retrieved set, and invented ids were filtered out silently. CLAUDE.md recorded
the open question — "whether a fabricated id sitting beside a valid one is
still `uncited_answer`". It is not `uncited_answer`; it is worse, and it now
has its own reason code.

A missing citation is a model that did not follow the output contract. A
fabricated one is a model that produced a plausible-looking identifier it never
read, and a valid id beside it does not redeem the answer — it means the claim
was assembled from both and there is no way to tell which half rests on the id
nobody can resolve. `fabricated_citation` names the invented ids in its detail,
because "the model made one up" is not debuggable and "the model made up
`deadbeef`" is.

The one repair kept is transcription: the model copies a 16-hex `fact_id` back
by hand, so case and surrounding whitespace are normalised before the
membership test. Repairing further would start guessing which fact was meant,
which is the confabulation the check exists to stop.

The check is a `gates.GateResult` rather than a new type, because it is
structurally a gate: it fires on shape, it names what was missing, and it
consults no model. It appears in the trace as `5 citation`.

### The trace

`gates.run` accumulates one line per check that actually ran and carries it on
the verdict. The cascade short-circuits, so the trace is also the record of
which gates were never reached — the first thing to look at when an abstention
is wrong.

Gate 4 is traced as `skipped (one entity, nothing to connect)` rather than as a
pass. `path_gate` returns PASS for a single-entity question without issuing the
MSpaths call, and tracing that as "pass" would read as a traversal that
happened. The trace is an audit record of what was *spent*, not only of what
was decided.

### Provenance without a round trip

`explain` derives what each cited fact replaced from the rows already in hand,
using `chain.group_key` and the same total order `chain.derive` uses — so the
explanation agrees with the SUPERSEDES edges the graph actually holds without
reading them. That agreement is asserted directly
(`test_predecessors_agrees_with_the_supersedes_edges_the_graph_holds`), because
a cheap derivation that can silently describe a chain the graph does not have
is worse than the round trip it saves.

### The source turn's role, and what it found

`Fact.role` is new: `UPSERT_FACT` writes it, `FACTS_FOR_INSTANCE` returns it,
and `explain` marks an assistant-sourced fact. Adding a field to an `UNWIND`
statement broke every existing caller exactly as CLAUDE.md says it does — four
`test_tracer.py` failures, `UNWIND row 0 is missing field role`.

Verified live on `ec81a493`, freshly ingested: **3 of its 8 facts are
assistant-sourced**, and they are precisely the three slice 06 flagged —
"Choose a harmonious frame", "Create a focal point", "Balance and symmetry" —
filed as user `prefers` facts against turns 5 and 7, which are the assistant's.
The trace marks all three `<- ASSISTANT-SOURCED`.

They are *surfaced*, not rejected. A citation pointing at one is a correctly
cited wrong answer, so the citation gate has nothing to fire on, and how often
this happens across the corpus has never been measured — 3 of 8 on one instance
is an observation, not a rate. Rejecting on role would trade unmeasured
precision for unmeasured recall. Owned by issue 19.

### What the role field does not cover

`UPSERT_FACT` is guarded on `asserted_at` with a strictly-less-than comparison,
so re-ingesting an existing fact writes nothing at all. Every fact written
before this slice — 48 across `gpt4_2655b836` and `89941a93` — returns null for
`role` and `explain` shows `(unknown)`. Reading null as "user" would be a silent
lie about who said the thing, so it is not read as anything. The wipe issue 17
already needs fixes this as a side effect.

### Found while probing, not fixed here

`gpt4_2487a7cb` abstains `unknown_entity: effective time management` on its own
question, because gate 1 reads a quoted event title as a proper noun. A false
abstention on a question the graph answers, and the third lexical bug in a row
that the unit suite could not see. Issue **18**, pinned in
`test_gates.py::test_a_quoted_event_title_is_read_as_an_entity_and_loses_the_question`.
