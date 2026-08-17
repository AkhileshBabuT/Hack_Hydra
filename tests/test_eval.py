"""Slice 12: the sampler and the scorer, with no corpus, no driver and no model.

The numbers this harness produces are the submission's central claim, so the
arithmetic behind them has to be checkable without running the benchmark. Every
test here builds plain rows and asserts on plain floats.

The arms themselves are not tested here -- they are one model call each and
`test_fixtures.py` already covers the cascade end to end against a live node.
What is testable, and what would silently produce a confidently wrong table, is
the stratification and the four rates.
"""

import pathlib
import re

import pytest

from hydramem import corpus, eval as ev, gates


def instance(instance_id, question_type, answer="a"):
    return corpus.Instance(
        instance_id=instance_id, question_type=question_type,
        question="q", answer=answer, asked_at=0, sessions=(),
        answer_session_ids=(),
    )


def population(per_type=50):
    out = []
    for question_type in ("temporal-reasoning", "knowledge-update", "multi-session"):
        out += [instance(f"{question_type}-{i}", question_type) for i in range(per_type)]
        out += [instance(f"{question_type}-{i}_abs", question_type) for i in range(4)]
    return out


# --- the slice -------------------------------------------------------------

def test_every_abstention_instance_is_taken_however_small_the_cap():
    """The whole thesis rests on abstention, and the oracle split has 30 cases.
    Sampling them down to the same cap as the answerable types would leave a
    precision figure computed over two questions."""
    chosen = ev.stratify(population(), per_type=3)
    assert sum(i.is_abstention for i in chosen) == 12
    assert sum(not i.is_abstention for i in chosen) == 9


def test_the_cap_is_per_type_not_overall():
    chosen = ev.stratify(population(), per_type=5)
    answerable = ev.counts(chosen)
    for question_type in ("temporal-reasoning", "knowledge-update", "multi-session"):
        assert answerable[(question_type, False)] == 5


def test_a_type_smaller_than_the_cap_is_taken_whole():
    small = [instance("only-1", "rare"), instance("only-2", "rare")]
    assert len(ev.stratify(small, per_type=20)) == 2


def test_the_slice_is_the_same_slice_next_time():
    a = ev.stratify(population(), per_type=7)
    b = ev.stratify(population(), per_type=7)
    assert [i.instance_id for i in a] == [i.instance_id for i in b]


def test_corpus_order_does_not_change_the_slice():
    """Sampling is from a list sorted by instance_id, so the slice survives a
    change to the loader, the file or the filesystem."""
    forward = ev.stratify(population(), per_type=7)
    backward = ev.stratify(list(reversed(population())), per_type=7)
    assert [i.instance_id for i in forward] == [i.instance_id for i in backward]


def test_the_oracle_slice_is_a_hundred_and_fifty(): # noqa: D103
    pytest.importorskip("json")
    if not corpus.ORACLE_CORPUS.exists():
        pytest.skip("corpus not fetched")
    chosen = ev.stratify(corpus.iter_instances(corpus.ORACLE_CORPUS))
    assert len(chosen) == 150
    assert sum(i.is_abstention for i in chosen) == 30


# --- scoring ---------------------------------------------------------------

def row(arm="hydramem", category="temporal-reasoning", gold_abstention=False,
        abstained=False, correct=False, **kw):
    base = {
        "arm": arm, "instance_id": "x", "category": category,
        "gold_abstention": gold_abstention, "question": "q", "gold": "g",
        "answer": "a", "abstained": abstained, "correct": correct,
        "prompt_tokens": 10, "completion_tokens": 2, "round_trips": 3,
        "latency_ms": 100, "overflow": False, "model": "m",
    }
    base.update(kw)
    return base


def test_an_unanswerable_question_is_scored_structurally(monkeypatch):
    """No judge call: on a gold-abstention question the only correct behaviour
    is abstaining, and asking a model to confirm that spends a call to
    re-derive something already known."""
    monkeypatch.setattr(ev, "judge", lambda *a: pytest.fail("judge was called"))
    assert ev.score(row(gold_abstention=True, abstained=True))["correct"]
    assert not ev.score(row(gold_abstention=True, abstained=False))["correct"]


def test_abstaining_on_an_answerable_question_is_simply_wrong(monkeypatch):
    """An answer the system had and threw away. There is nothing to judge."""
    monkeypatch.setattr(ev, "judge", lambda *a: pytest.fail("judge was called"))
    assert not ev.score(row(gold_abstention=False, abstained=True))["correct"]


def test_an_answered_question_goes_to_the_judge(monkeypatch):
    monkeypatch.setattr(ev, "judge", lambda *a: True)
    assert ev.score(row())["correct"]


def test_abstention_precision_and_recall_are_the_usual_ones():
    rows = [
        row(gold_abstention=True, abstained=True, correct=True),    # true positive
        row(gold_abstention=True, abstained=False),                 # missed
        row(gold_abstention=False, abstained=True),                 # false alarm
        row(gold_abstention=False, abstained=False, correct=True),
    ]
    summary = {r["category"]: r for r in ev.summarise(rows)}["temporal-reasoning"]
    assert summary["abstain_precision"] == 0.5    # 1 of 2 abstentions was right
    assert summary["abstain_recall"] == 0.5       # 1 of 2 unanswerables caught
    assert summary["accuracy"] == 0.5
    # Accuracy over the answerable half only, which is the number a reader who
    # distrusts the abstention over-sampling will want.
    assert summary["answerable_accuracy"] == 0.5


def test_a_rate_with_no_denominator_is_blank_not_zero():
    """An arm that never abstained has undefined precision, not perfect
    precision and not zero. A zero here would read as a failure in a table."""
    summary = ev.summarise([row(gold_abstention=False, abstained=False)])[0]
    assert summary["abstain_precision"] == ""
    assert summary["abstain_recall"] == ""


def test_every_row_carries_its_n():
    """The slice deliberately over-represents abstention, so an accuracy without
    its denominator is not interpretable."""
    for summary in ev.summarise([row(), row(category="knowledge-update")]):
        assert summary["n"] >= 1


def test_the_all_row_spans_categories_and_the_category_rows_do_not():
    rows = [row(category="a"), row(category="b"), row(category="b")]
    summary = {r["category"]: r for r in ev.summarise(rows)}
    assert summary["ALL"]["n"] == 3
    assert summary["a"]["n"] == 1 and summary["b"]["n"] == 2


def test_arms_are_summarised_separately():
    rows = [row(arm="hydramem", correct=True), row(arm="full_context")]
    summary = {(r["arm"], r["category"]): r for r in ev.summarise(rows)}
    assert summary[("hydramem", "ALL")]["accuracy"] == 1.0
    assert summary[("full_context", "ALL")]["accuracy"] == 0.0


def test_the_baseline_is_told_it_may_abstain_in_the_same_words():
    """Withholding the permission to abstain from the baseline would rig the one
    comparison the submission turns on."""
    assert "ABSTAIN" in ev.BASELINE_SYSTEM
    assert "honest ABSTAIN is worth more than a plausible guess" in ev.BASELINE_SYSTEM
    assert "honest ABSTAIN is worth more than a plausible guess" in __import__(
        "hydramem.answer", fromlist=["SYSTEM"]).SYSTEM


def test_the_csv_carries_the_answering_model(tmp_path):
    path = ev.to_csv(ev.summarise([row()]), tmp_path / "t.csv")
    assert "model" in path.read_text(encoding="utf-8").splitlines()[0]


# --- the abstention breakdown ----------------------------------------------
#
# The existing table says how often each arm abstained and how often it was
# right to. It does not say *why*, and the why is what decides which defect to
# fix -- 60 of hydramem's 86 abstentions on the oracle slice were false, spread
# across four causes that need four different fixes.


def abstention(reason, gold_abstention=False, **kw):
    return row(abstained=True, reason=reason, gold_abstention=gold_abstention,
               correct=gold_abstention, **kw)


def all_row(rows, arm, reason):
    return next(r for r in ev.abstention_reasons(rows)
                if r["arm"] == arm and r["category"] == "ALL"
                and r["reason"] == reason)


def test_each_reason_carries_its_total_and_its_false_half():
    rows = [abstention("unknown_entity"),                          # answerable
            abstention("unknown_entity", gold_abstention=True),    # correct
            abstention("not_in_graph")]
    entity = all_row(rows, "hydramem", "unknown_entity")
    assert (entity["count"], entity["count_false"]) == (2, 1)
    assert all_row(rows, "hydramem", "not_in_graph")["count_false"] == 1


def test_a_reason_that_never_fired_is_still_a_row():
    """`no_path` is 0 on the whole oracle slice, and that is the finding.

    A breakdown assembled only from what happened cannot express "gate 4 never
    fires" -- the row would simply be absent, which reads as an oversight
    rather than as a measurement.
    """
    rows = [abstention("unknown_entity")]
    assert all_row(rows, "hydramem", "no_path")["count"] == 0
    assert all_row(rows, "hydramem", "fabricated_citation")["count"] == 0


def test_the_counts_sum_to_the_arms_abstention_count():
    rows = [abstention("unknown_entity"), abstention("no_such_relation"),
            abstention("not_in_graph"), abstention("not_in_graph"),
            row(abstained=False, reason="")]
    breakdown = [r for r in ev.abstention_reasons(rows)
                 if r["arm"] == "hydramem" and r["category"] == "ALL"]
    assert sum(r["count"] for r in breakdown) == 4
    assert {r["n_abstained"] for r in breakdown} == {4}


def test_an_unregistered_reason_is_surfaced_not_dropped():
    """Adding a gate must not be able to make its abstentions invisible.

    The sum check above is what catches it, so the new code has to appear as a
    row of its own rather than being filtered out for not being in the registry.
    """
    rows = [abstention("unknown_entity"), abstention("no_such_premise")]
    breakdown = [r for r in ev.abstention_reasons(rows)
                 if r["arm"] == "hydramem" and r["category"] == "ALL"]
    assert all_row(rows, "hydramem", "no_such_premise")["count"] == 1
    assert sum(r["count"] for r in breakdown) == 2


def test_a_gated_abstention_with_no_reason_is_surfaced_too():
    """It would be a defect, so it must not be silently absorbed."""
    rows = [abstention("")]
    breakdown = [r for r in ev.abstention_reasons(rows)
                 if r["arm"] == "hydramem" and r["category"] == "ALL"]
    assert sum(r["count"] for r in breakdown) == 1


def test_an_arm_with_no_gates_says_so_rather_than_reporting_zeros():
    """A baseline has no gates; zeros would claim it has gates that never fire.

    Those are opposite facts and the table has to be able to tell them apart --
    `no_path: 0` against hydramem is a measurement, `no_path: 0` against
    full_context would be a category error.
    """
    rows = [abstention("", arm="full_context"),
            abstention("", arm="full_context", gold_abstention=True)]
    breakdown = [r for r in ev.abstention_reasons(rows)
                 if r["arm"] == "full_context" and r["category"] == "ALL"]
    assert [r["reason"] for r in breakdown] == [ev.NO_GATES]
    assert breakdown[0]["count"] == 2
    assert breakdown[0]["count_false"] == 1


def test_the_breakdown_is_per_category_as_well_as_overall():
    rows = [abstention("unknown_entity", category="temporal-reasoning"),
            abstention("not_in_graph", category="knowledge-update")]
    per = {(r["category"], r["reason"]): r["count"]
           for r in ev.abstention_reasons(rows) if r["arm"] == "hydramem"}
    assert per[("temporal-reasoning", "unknown_entity")] == 1
    assert per[("knowledge-update", "unknown_entity")] == 0
    assert per[("ALL", "unknown_entity")] == 1


def test_every_reason_the_cascade_can_return_is_registered():
    """The registry is what makes a zero visible, so it has to be complete.

    A gate that returns a code absent from `gates.REASONS` would still be
    counted -- the unregistered path above covers that -- but it would never
    show a zero, so its *absence* would stop being reportable.
    """

    source = pathlib.Path("hydramem/gates.py").read_text(encoding="utf-8")
    returned = set(re.findall(r'GateResult\(False,\s*"([a-z_]+)"', source))
    assert returned <= set(gates.REASONS), returned - set(gates.REASONS)


# --- coverage and selective accuracy ---------------------------------------
#
# Abstention F1 is gameable in both directions: a published truncation baseline
# scored 93.3 on an abstention subset by answering almost nothing. Coverage
# paired with selective accuracy is what that trick cannot survive.


def test_coverage_is_the_fraction_the_arm_chose_to_answer():
    rows = [row(abstained=True), row(abstained=True),
            row(abstained=False, correct=True), row(abstained=False)]
    summary = {r["category"]: r for r in ev.summarise(rows)}["temporal-reasoning"]
    assert summary["coverage"] == 0.5


def test_selective_accuracy_is_scored_only_over_what_was_answered():
    """Distinct from `answerable_accuracy`, which is over questions that HAVE an
    answer whether or not the arm attempted them. Both are needed: one says how
    often it is right when it speaks, the other how much it left on the table.
    """
    rows = [row(abstained=False, correct=True),
            row(abstained=False, correct=False),
            row(abstained=True, gold_abstention=False)]   # an answer thrown away
    summary = {r["category"]: r for r in ev.summarise(rows)}["temporal-reasoning"]
    assert summary["selective_accuracy"] == 0.5, "1 of the 2 it answered"
    assert summary["answerable_accuracy"] == round(1 / 3, 4), "1 of the 3 answerable"


def test_a_refuse_everything_arm_scores_no_coverage_rather_than_perfect_anything():
    """The degenerate baseline this pair exists to catch. Abstaining on
    everything gives it nothing to be right about, and selective accuracy is
    blank rather than 1.0 -- an undefined rate must not read as a perfect one.
    """
    rows = [row(abstained=True, gold_abstention=True, correct=True) for _ in range(4)]
    summary = {r["category"]: r for r in ev.summarise(rows)}["temporal-reasoning"]
    assert summary["coverage"] == 0.0
    assert summary["selective_accuracy"] == ""
    assert summary["abstain_precision"] == 1.0, "the gameable number, for contrast"
