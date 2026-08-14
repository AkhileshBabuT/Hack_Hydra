"""Extraction repair and grounding, over plain dicts. No model, no database.

The extractor's output is the widest untrusted input in the pipeline, and every
rule that narrows it lives in `clean()` or in a validator. Both are pure, so
the repair behaviour is pinned here rather than being rediscovered from a
mangled graph.
"""

import pytest

from hydramem import extract


def fact(**kw):
    return extract.ExtractedFact(**kw)


# --- validators: a malformed field must never cost the session ------------

def test_null_turn_idx_does_not_reject_the_fact():
    """One null turn used to fail the whole Extraction, losing every fact.

    pydantic reports per-fact errors as a single failed parent model, so this
    is not a one-fact loss -- it is the session.
    """
    assert fact(value="x", turn_idx=None).turn_idx == 0


def test_turn_marker_string_is_coerced():
    assert fact(value="x", turn_idx="[7]").turn_idx == 7
    assert fact(value="x", turn_idx="turn seven").turn_idx == 0


def test_a_whole_extraction_survives_one_null_turn():
    parsed = extract.Extraction.model_validate(
        {"facts": [{"value": "a", "turn_idx": None}, {"value": "b", "turn_idx": 2}]}
    )
    assert [f.value for f in parsed.facts] == ["a", "b"]


def test_null_strings_become_empty():
    f = fact(subject=None, value=None, evidence_span=None)
    assert (f.subject, f.value, f.evidence_span) == ("", "", "")


# --- clean(): constrain output to what the schema actually allows ---------

def test_clean_drops_facts_with_nothing_to_retrieve():
    kept = extract.clean([fact(subject="", value="x"), fact(value="")], turn_count=3)
    assert kept == []


@pytest.mark.parametrize("value", ["none", "Unknown", "N/A", "not specified yet", "nothing."])
def test_clean_drops_absences_dressed_as_values(value):
    assert extract.clean([fact(value=value)], turn_count=3) == []


def test_clean_relabels_off_vocabulary_predicates_instead_of_dropping():
    kept = extract.clean([fact(value="x", predicate="favourite_colour")], turn_count=3)
    assert kept[0].predicate == "other"          # still reachable by entity
    assert kept[0].value == "x"


def test_clean_clamps_an_impossible_turn_and_trims_the_span():
    kept = extract.clean(
        [fact(value="x", turn_idx=99, evidence_span="q" * 500)], turn_count=4
    )
    assert kept[0].turn_idx == 0
    assert len(kept[0].evidence_span) == extract.MAX_SPAN


def test_functional_predicates_are_a_subset_of_the_vocabulary():
    assert extract.FUNCTIONAL_PREDICATES <= set(extract.PREDICATES)


# --- grounded(): the automatic precision floor ---------------------------

SESSION = "[0] user: I moved to\n  Berlin last March.\n[1] assistant: Nice."


def test_grounded_ignores_reflowed_whitespace_and_case():
    assert extract.grounded("i moved to Berlin", SESSION)


def test_grounded_rejects_an_invented_span():
    assert not extract.grounded("I moved to Munich", SESSION)


def test_grounded_rejects_an_empty_span():
    assert not extract.grounded("   ", SESSION)


# --- the retry rung: a collapsed greedy decode is otherwise permanent ------

class FakeSession:
    turns = ()

    def text(self):
        return "[0] user: hi"


# The two collapsed outputs the extractor actually produced on the slice 06
# sample, verbatim. Kept as literals rather than as a description: a regression
# here is a decoder that stops collapsing the same way, and a paraphrase of the
# payload would not notice.
COLLAPSED_BRACE = "{"                       # gpt4_1916e0ea / answer_447052a5_2
COLLAPSED_ZERO_WIDTH = '{"\u200b\u200b??nhe\u200b\u200bex'  # bc149d6b / answer_92147866_1


@pytest.mark.parametrize("collapsed", [COLLAPSED_BRACE, COLLAPSED_ZERO_WIDTH])
def test_unparseable_output_is_retried_at_a_nudged_temperature(monkeypatch, collapsed):
    """Measured on the oracle slice: one session returned the single character
    `{`, another a run of zero-width spaces. Both are cached, so retrying the
    identical request returns the identical garbage forever -- the temperature
    is what makes the second call a different call.
    """
    seen = []

    def fake_complete(messages, **kw):
        seen.append(kw["temperature"])
        text = collapsed if len(seen) == 1 else '{"facts": [{"value": "berlin"}]}'
        return {"text": text, "usage": {}, "cached": False}

    monkeypatch.setattr(extract.llm, "complete", fake_complete)
    result = extract.extract_raw(FakeSession(), "2024-01-01 00:00")

    assert [f.value for f in result.facts] == ["berlin"]
    assert seen == [0.0, extract.RETRY_TEMPERATURE]


def test_a_first_pass_parse_costs_only_one_call(monkeypatch):
    calls = []

    def fake_complete(messages, **kw):
        calls.append(kw["temperature"])
        return {"text": '{"facts": []}', "usage": {}, "cached": True}

    monkeypatch.setattr(extract.llm, "complete", fake_complete)
    extract.extract_raw(FakeSession(), "2024-01-01 00:00")
    assert calls == [0.0]


def test_a_second_failure_still_raises(monkeypatch):
    """The retry is one attempt, not a loop. A session the model cannot parse
    twice must reach the ingest stats by name, never be silently zeroed.
    """
    monkeypatch.setattr(
        extract.llm, "complete",
        lambda messages, **kw: {"text": "{", "usage": {}, "cached": False},
    )
    with pytest.raises(extract.ValidationError):
        extract.extract_raw(FakeSession(), "2024-01-01 00:00")


# --- quality defects slice 06 measured but did not fix ---------------------
#
# These are not bugs in this module. They are the extractor's own failure
# modes, pinned here with the real spans so a prompt change can be checked
# against them instead of against a memory of them. Fixing either one changes
# content-derived fact ids and costs a node wipe, so both are deferred.

ASSISTANT_ADVICE = (
    "[3] user: Any tips for the shelf?\n"
    "[4] assistant: Choose a harmonious frame: Select a frame for your poster "
    "that complements the style and existing decor."
)


def test_an_assistant_sourced_preference_is_caught_only_when_it_is_misquoted():
    """Instance `ec81a493`: three `prefers` facts sourced from the assistant's
    own advice, which SYSTEM forbids outright. Grounding catches them here only
    because the model reflowed the quote -- an exact copy of assistant text
    would pass, so grounding is not a defence against this. Slice 10's citation
    check is where it has to be caught.
    """
    reflowed = "Select a frame for your poster that complements the style and existing style"
    assert not extract.grounded(reflowed, ASSISTANT_ADVICE)

    verbatim_assistant_text = "Choose a harmonious frame"
    assert extract.grounded(verbatim_assistant_text, ASSISTANT_ADVICE)


# The mis-slot this same sample produced -- `name: 'silver Honda Civic'` -- is
# pinned in tests/test_chain.py instead, because the damage it does is done by
# the chain, not by anything in this module.
