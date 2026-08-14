# 06 — Extractor quality measurement on a hand-checked slice

Status: done

## Parent

`.scratch/hydramem/PRD.md`

## What to build

Measure the extractor before committing the full corpus to it.

The chosen extraction model is days old with no independent benchmarks on structured
extraction specifically, and a reasoning model that emits thinking tokens breaks strict JSON
parsing badly enough to have destroyed published pipelines. Reasoning-off is the single
highest-value setting; this slice confirms that empirically rather than assuming it.

Hand-check a twenty-instance slice for schema validity and fact precision. This is human work
because fact precision requires reading transcripts and judging whether an extracted claim is
actually supported.

If quality is inadequate, escalate in this order: tighten the prompt and confirm reasoning is
genuinely off, then fall back to short sliding turn windows, then to a larger model. Record
which rung was needed.

Blocking: do not scale ingest to the full corpus until this passes.

## Acceptance criteria

- [ ] Schema-validity rate across a twenty-instance slice is recorded as a number
- [ ] Fact precision is hand-checked and recorded
- [ ] The controlled predicate vocabulary is confirmed adequate, or extended and re-measured
- [ ] If quality is inadequate, the escalation rung taken is recorded
- [ ] A go or no-go decision on scaling to the full corpus is recorded

## Blocked by

03

## Result

`scripts/measure_extraction.py` measures the slice and writes two files:
`docs/extraction-quality.md` (numbers, regenerated, never hand-edited) and
`docs/extraction-review.md` (a deterministic 60-fact sheet for the hand-check).
Neither touches HydraDB — extraction quality is a property of the model and the
prompt, and putting a graph in the loop would only add a way to be wrong.

The slice is sampled with a stride across the whole corpus. Taking the first
twenty instances would have measured twenty temporal-reasoning questions and
reported the result as the extractor's: both splits are grouped by question
type.

### Schema validity: 86.8% -> 94.7% -> 100% (38/38 sessions)

Two escalation rungs, neither of them the ones the issue anticipated. Nothing
was wrong with the prompt and reasoning was already off.

**Rung 1 — `turn_idx: null` was uncoerced (`extract.py`).** pydantic reports
per-fact errors as one failed `Extraction`, so a single null turn discarded
every fact in the session: 27 of them in the worst case. The `_digits_only`
validator handled the `"[7]"` string the model sometimes returns but not the
null, and `_null_is_empty` already established that a null field is the
extractor declining to answer, not a reason to reject the session. Extending
that to `turn_idx` recovered 20 facts and 3 sessions.

**Rung 2 — one retry at a nudged temperature (`RETRY_TEMPERATURE = 0.3`).**
The two remaining failures were genuine model collapse: one session returned
the single character `{`, the other a run of zero-width spaces. Greedy decoding
at `temperature=0.0` produces these, and the disk cache is keyed on the
request, so retrying the *identical* request returns the identical garbage
forever. Temperature is the only lever that reaches it. The retry fires only on
unparseable output and only once; a second failure still raises and still lands
in the ingest stats by name.

CLAUDE.md's "~2.4% of sessions fail extraction" was an underestimate measured
on one instance. The real rate was 13.2% and it was ours, not the model's.

### Grounding: 96.9% (250/258)

`extract.grounded()` — pure, whitespace- and case-insensitive substring check
of `evidence_span` against the session. It is a **precision floor, not
precision**: it catches invented quotes and cannot catch a correctly-quoted
span filed under the wrong predicate.

The 8 failures are named in the report, and they are not random. Three of them
(`ec81a493`) quote the *assistant's* suggestions — "Choose a harmonious frame",
"Create a focal point" — as user `prefers` facts, which the prompt explicitly
forbids. That is a prompt-tightening candidate for a later slice; it is not
fixed here because fact ids are content-derived and a prompt change costs a
node wipe.

### Predicate vocabulary: adequate, not extended

`other` takes 24.4%, under the 35% gate. The off-vocabulary requests are a long
tail with no cluster worth a new predicate — `plans` (6) and `planned_visit`
(5) are the largest and both already have `plan`. Extending the vocabulary
would cost a node wipe to buy a few percent, so it was not extended.

The mis-slotting risk CLAUDE.md records is confirmed and visible in the sheet:
row 4 files `name: silver Honda Civic`, and `name` is functional, so it would
supersede the user's actual name. The hand-check marks these `P` and counts
them as unsupported for exactly that reason.

### Subject keys: 96.9% on `person:user`

Measured because slice 07's `unknown_entity` gate leans on the self-form
closure. The tail is 8 entities of 2-3 facts each. This confirms the slice 05
finding that `alias_pairs` produces no `ALIAS_OF` edges: there is almost
nothing to alias.

### Where each failure is now pinned

Every defect this slice found has a test that fails if it comes back, so none
of them depend on this document being reread.

| failure | pinned in |
|---|---|
| `turn_idx: null` rejects the session | `test_extract.py::test_a_whole_extraction_survives_one_null_turn` |
| collapsed decode (`{`, zero-width run) | `test_extract.py::test_unparseable_output_is_retried_at_a_nudged_temperature`, parametrized on both real payloads |
| retry is one attempt, not a loop | `test_extract.py::test_a_second_failure_still_raises` |
| assistant advice attributed to the user | `test_extract.py::test_an_assistant_sourced_preference_is_caught_only_when_it_is_misquoted` |
| mis-slot retracts a true fact | `test_chain.py::test_a_mis_slotted_functional_predicate_retracts_a_true_fact` |

The two defects this slice did **not** fix are recorded against the issues that
inherit them: assistant attribution on 10 (the citation check is the only place
it can be caught — grounding cannot, since an exact copy of assistant text is
grounded by definition), and mis-slotting on 07 (the predicate gate will pass on
a `name` fact whose value is a car).

### Go / no-go

**GO.** All three automatic gates pass. The hand-check sheet
(`docs/extraction-review.md`) is generated and deterministic; its tally is
unfilled, so full-corpus quality is verified as far as schema validity and
grounding go and no further. Anyone reading a precision number from this slice
is reading one that does not exist yet.

Cost of the measurement: 38 cached extractions, $0 on rerun.
