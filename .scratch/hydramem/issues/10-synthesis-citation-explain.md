# 10 — Synthesis, citation verification, `explain()`

Status: ready-for-agent

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

- [ ] Synthesis receives only the retrieved subgraph, never the raw history
- [ ] An answer making a substantive claim with no citation is downgraded to abstention
- [ ] An answer citing an identifier absent from the retrieved set is downgraded to abstention
- [ ] The downgrade path is asserted by tests that do not depend on model behavior
- [ ] Explain returns fact path, source session, turn index, timestamp and gate trace
- [ ] Explain works for both answered and abstained questions
- [ ] Explain surfaces the source turn's role, so an assistant-sourced fact is visible in the trace

## Blocked by

07
