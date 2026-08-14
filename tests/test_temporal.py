"""Slice 08: window resolution, the three bitemporal reads, and gate 3.

Everything except the last two tests runs on plain dicts. That is the same
constraint gates 1 and 2 are held to and for the same reason: when a temporal
abstention is wrong, the window that produced it has to be reproducible from a
question string and a list of facts, with no node running.

The window bounds are asserted as dates, not as epoch integers. A window that
is off by a year is the failure mode this gate actually has, and an integer
mismatch does not say which year.
"""

import datetime as dt

import pytest
from conftest import make_fact, make_instance, make_session

from hydramem import client, gates, ingest, statements, temporal


def epoch(text: str) -> int:
    return int(dt.datetime.strptime(text, "%Y-%m-%d")
               .replace(tzinfo=dt.timezone.utc).timestamp())


ASKED = epoch("2024-06-15")


def fact(value, valid_from, valid_to=temporal.OPEN, predicate="employer",
         subject="person:user", fid=None, turn=0, node_id=0):
    """A fact row shaped like `FACTS_FOR_INSTANCE` returns them."""
    return {
        "id": node_id, "fact_id": fid or f"{predicate}:{value}",
        "predicate": predicate, "value_text": value,
        "valid_from": epoch(valid_from),
        "valid_to": temporal.OPEN if valid_to is temporal.OPEN else epoch(valid_to),
        "asserted_at": epoch(valid_from), "turn_idx": turn,
        "status": "current" if valid_to is temporal.OPEN else "superseded",
        "subject_key": subject, "subject_name": "user", "snippet": "",
    }


ACME = fact("Acme", "2019-03-01", "2021-07-01")
GLOBEX = fact("Globex", "2021-07-01", "2023-02-01", node_id=1)
INITECH = fact("Initech", "2023-02-01", node_id=2)
CAREER = [ACME, GLOBEX, INITECH]


# --- window resolution ----------------------------------------------------

@pytest.mark.parametrize("question,start,end", [
    ("Where did I work in 2019?", "2019-01-01", "2020-01-01"),
    ("What did I buy in March 2021?", "2021-03-01", "2021-04-01"),
    ("Where did I live in Dec 2020?", "2020-12-01", "2021-01-01"),
    ("What was my job on 2021-07-04?", "2021-07-04", "2021-07-05"),
    ("Where did I work last year?", "2023-01-01", "2024-01-01"),
    ("What did I do two years ago?", "2022-01-01", "2023-01-01"),
    ("What was my budget last month?", "2024-05-01", "2024-06-01"),
])
def test_a_window_resolves_to_the_interval_a_reader_would_mean(question, start, end):
    window = temporal.parse_window(question, ASKED)
    assert (window.start, window.end) == (epoch(start), epoch(end)), window.label


def test_before_a_year_is_not_the_year_itself():
    """`before` contains the shape "<preposition> <year>". Matched in the wrong
    order it resolves to 2021 -- the exact opposite of the window asked for.
    """
    window = temporal.parse_window("Where did I work before 2021?", ASKED)
    assert (window.start, window.end) == (0, epoch("2021-01-01"))


def test_since_a_year_is_open_ended():
    window = temporal.parse_window("Where have I worked since 2021?", ASKED)
    assert window.end == temporal.OPEN
    assert window.label.endswith("onwards")


@pytest.mark.parametrize("question", [
    "Where do I work?",
    "What is my budget for the trip?",
    "I have 2000 dollars, what can I buy?",     # a bare number is not a year
    "Who did I meet at the conference?",
])
def test_a_question_naming_no_window_resolves_to_none(question):
    """None is the safe answer: no window means gate 3 has no opinion and the
    question is answered against the current value. See `gates.BIAS`.
    """
    assert temporal.parse_window(question, ASKED) is None


def test_relative_phrasing_without_an_anchor_resolves_to_nothing():
    """Not to today. Resolving "last year" against the wall clock would make an
    evaluation return a different answer next January.
    """
    assert temporal.parse_window("Where did I work last year?") is None


def test_an_impossible_date_names_no_window():
    assert temporal.parse_window("What happened on 2021-02-31?", ASKED) is None


# --- the three reads ------------------------------------------------------

def test_current_is_the_head_of_the_chain():
    assert [f["value_text"] for f in temporal.current(CAREER)] == ["Initech"]


def test_value_at_a_past_time_is_the_fact_whose_interval_contains_it():
    assert [f["value_text"] for f in temporal.at_time(CAREER, epoch("2020-01-01"))] == ["Acme"]
    assert [f["value_text"] for f in temporal.at_time(CAREER, epoch("2022-01-01"))] == ["Globex"]


def test_an_open_interval_is_unbounded_not_a_zero_length_one():
    """`valid_to == 0` is the common case -- most facts are never revised. Read
    as a real end date it would exclude every one of them from every window.
    """
    far_future = epoch("2024-06-15") + 10 * 365 * 86400
    assert temporal.at_time(CAREER, far_future) == [INITECH]


def test_a_window_that_no_fact_satisfies_is_empty():
    window = temporal.parse_window("Where did I work in 2015?", ASKED)
    assert temporal.in_window(CAREER, window) == []


def test_a_window_spanning_a_change_returns_both_sides():
    window = temporal.parse_window("Where did I work in 2021?", ASKED)
    assert [f["value_text"] for f in temporal.in_window(CAREER, window)] == ["Acme", "Globex"]


def test_change_history_is_ordered_and_carries_the_time_of_each_change():
    changes = temporal.history(CAREER)[("person:user", "employer")]
    assert [(temporal.Window._day(when), value) for when, value, _ in changes] == [
        ("2019-03-01", "Acme"), ("2021-07-01", "Globex"), ("2023-02-01", "Initech"),
    ]


def test_a_restatement_is_not_a_change():
    restated = CAREER + [fact("Initech", "2024-01-01", node_id=3)]
    changes = temporal.history(restated)[("person:user", "employer")]
    assert [value for _, value, _ in changes] == ["Acme", "Globex", "Initech"]


def test_a_restatement_does_not_move_the_start_date_forward():
    """The gap slice 08 inherited: the newest identical assertion becomes the
    head of the chain, so "since when has X been true" answered with the most
    recent mention rather than the first.

    Decided here rather than at ingest. Fact ids are content-derived, so
    changing what `valid_from` is written as would need a wiped node; `since`
    walks the unchanged run back at read time, where the answer is observable
    and the graph does not have to be rebuilt to change the decision.
    """
    restated = [INITECH, fact("Initech", "2024-01-01", node_id=3)]
    assert temporal.since(restated) == epoch("2023-02-01")
    assert temporal.since([]) == 0


# --- gate 3 ---------------------------------------------------------------

def test_a_question_with_no_window_passes_the_gate_untouched():
    assert gates.temporal_gate("Where do I work?", CAREER, ASKED, "person:user").passed


def test_an_unsatisfiable_window_abstains_naming_the_resolved_window():
    result = gates.temporal_gate("Where did I work in 2015?", CAREER, ASKED, "person:user")
    assert not result.passed
    assert result.reason == "no_fact_in_window"
    assert result.detail == (
        "no_fact_in_window: person:user has no fact valid "
        "in 2015 (2015-01-01..2016-01-01)"
    )


def test_the_window_is_applied_to_the_predicate_the_question_asked_for():
    """A `likes` fact from 2015 is not an answer to "where did I work in 2015",
    so its presence must not stop the gate firing.
    """
    noise = fact("jazz", "2015-01-01", predicate="likes", node_id=9)
    result = gates.temporal_gate("Where did I work in 2015?", CAREER + [noise],
                                 ASKED, "person:user")
    assert not result.passed


def test_an_unreadable_predicate_widens_the_gate_rather_than_narrowing_it():
    """When gate 2 named no predicate there is nothing to intersect with, so
    the whole fact set is used. An empty scope is this gate failing to read the
    question, not evidence the graph is silent -- see `gates.BIAS`.
    """
    result = gates.temporal_gate("What was going on in 2019?", CAREER, ASKED, "person:user")
    assert result.passed


def test_the_cascade_reaches_gate_3_only_after_gates_1_and_2_pass():
    entities = [{"id": 1, "key": "person:user", "name": "user", "type": "person"}]
    result = gates.run("Where did I work in 2015?", entities, {},
                       lambda key: CAREER, ASKED)
    assert result.reason == "no_fact_in_window"

    lost_earlier = gates.run("Where did Maya Chen work in 2015?", entities, {},
                             lambda key: CAREER, ASKED)
    assert lost_earlier.reason == "unknown_entity"


def test_the_same_question_returns_now_plainly_and_then_when_scoped():
    """The property slice 08 exists to deliver, at the level the retrieval
    layer decides it: `answer.answer_question` narrows on exactly this.
    """
    assert temporal.parse_window("Where do I work?", ASKED) is None
    assert [f["value_text"] for f in temporal.current(CAREER)] == ["Initech"]

    window = temporal.parse_window("Where did I work back in 2020?", ASKED)
    assert [f["value_text"] for f in temporal.in_window(CAREER, window)] == ["Acme"]


# --- against a live node --------------------------------------------------

SESSIONS = [make_session(i, f"s{i}", ts) for i, ts in
            enumerate((1_000_000, 2_000_000, 3_000_000))]


def test_the_derived_history_matches_the_materialized_chain(driver, instance_id):
    """`temporal.history` and the SUPERSEDES edges are two derivations of one
    thing. If they disagree, the pure tests above are testing a fiction.
    """
    extractions = [
        (SESSIONS[0], [make_fact(value="Acme")]),
        (SESSIONS[1], [make_fact(value="Globex")]),
        (SESSIONS[2], [make_fact(value="Initech")]),
    ]
    ingest.write_rows(driver, ingest.build_rows(
        make_instance(SESSIONS, instance_id), extractions))

    facts = client.read(driver, statements.FACTS_FOR_INSTANCE,
                        {"instance_id": instance_id}, consistency="strong")
    edges = client.read(driver, statements.SUPERSESSION_CHAIN_FOR_INSTANCE,
                        {"instance_id": instance_id}, consistency="strong")

    derived = temporal.history(facts)[("person:user", "employer")]
    assert [value for _, value, _ in derived] == ["Acme", "Globex", "Initech"]

    # The same two transitions, read off the graph instead of computed.
    assert sorted((e["old_value"], e["new_value"]) for e in edges) == [
        ("Acme", "Globex"), ("Globex", "Initech"),
    ]
    assert all(e["old_status"] == "superseded" for e in edges)


def test_value_at_a_past_time_reads_the_same_off_the_graph(driver, instance_id):
    extractions = [
        (SESSIONS[0], [make_fact(value="Acme")]),
        (SESSIONS[2], [make_fact(value="Initech")]),
    ]
    ingest.write_rows(driver, ingest.build_rows(
        make_instance(SESSIONS, instance_id), extractions))

    facts = client.read(driver, statements.FACTS_FOR_INSTANCE,
                        {"instance_id": instance_id}, consistency="strong")
    assert [f["value_text"] for f in temporal.current(facts)] == ["Initech"]
    assert [f["value_text"] for f in temporal.at_time(facts, 1_500_000)] == ["Acme"]
