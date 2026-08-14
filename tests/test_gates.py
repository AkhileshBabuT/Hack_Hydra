"""Gates 1 and 2 over plain lists. No database, no model call.

That constraint is the design, not a testing convenience: the predicate gate is
the first suspect whenever abstention precision drops, so every one of its
decisions has to be reproducible from a question string and a list of dicts.

The reason strings are asserted verbatim. A gate that fires with the wrong
detail is worse than one that does not fire at all — it sends whoever is
debugging it to the wrong place.
"""

import pytest

from hydramem import gates


def entity(key, name, entity_type="person", eid=1):
    return {"id": eid, "key": key, "name": name, "type": entity_type}


def fact(predicate, value="x"):
    return {"predicate": predicate, "value_text": value}


USER = entity(gates.SELF_KEY, "user")
MAYA = entity("person:maya chen", "Maya Chen", eid=2)


# --- mention detection ----------------------------------------------------

def test_first_person_resolves_to_the_user_without_touching_the_graph():
    assert gates.SELF_KEY in gates.mentions("Where do I work?")


@pytest.mark.parametrize("question", [
    "What did I say?", "When was my appointment?", "Did I book it?",
])
def test_a_grammatical_capital_is_not_a_name(question):
    """Without this every question is about an entity called "What"."""
    assert gates.mentions(question) == [gates.SELF_KEY]


def test_a_name_behind_a_grammatical_capital_survives():
    assert "maya chen" in gates.mentions("Did Maya Chen call me back?")


def test_a_name_is_found_mid_sentence():
    assert "berlin" in gates.mentions("How long did I stay in Berlin?")


@pytest.mark.parametrize("question", [
    "How many bikes did I have in February 2023?",
    "What did I buy on Monday?",
    "Where was I at Christmas?",
])
def test_a_calendar_word_is_a_date_not_an_entity(question):
    """Slice 08's real bug, found by probing instance `89941a93` and invisible
    to this suite until then: gate 1 read "February" as a proper noun, found no
    such entity, and abstained `unknown_entity: february` on a question the
    graph could answer. Every temporal question carries one of these, so gate 3
    was unreachable behind gate 1.
    """
    assert gates.mentions(question) == [gates.SELF_KEY]


def test_a_real_name_beside_a_month_still_resolves():
    """The fix drops calendar words, not every capitalised run near one."""
    assert "maya chen" in gates.mentions("Did Maya Chen call me in February?")


# --- gate 1: unknown_entity -----------------------------------------------

def test_an_entity_the_graph_never_saw_abstains_and_names_itself():
    result = gates.entity_gate("Did Maya Chen call?", [USER])
    assert not result.passed
    assert result.reason == "unknown_entity"
    assert result.detail == "unknown_entity: maya chen"


def test_a_stored_entity_resolves_by_name():
    result = gates.entity_gate("Did Maya Chen call?", [USER, MAYA])
    assert result.passed
    assert result.resolved == ("person:maya chen",)


def test_a_question_naming_someone_else_and_the_user_resolves_both():
    result = gates.entity_gate("Did Maya Chen call me back?", [USER, MAYA])
    assert result.resolved == ("person:maya chen", gates.SELF_KEY)


def test_a_cue_does_not_match_inside_a_longer_word():
    """`"work" in "homework"` is true, and would file every homework question
    under the employer predicate.
    """
    assert "employer" not in gates.question_predicates("What homework did I have?")
    assert "employer" in gates.question_predicates("Where do I work?")


def test_an_empty_graph_abstains_before_anything_else():
    result = gates.entity_gate("Where do I work?", [])
    assert result.detail == "unknown_entity: <empty graph>"


def test_an_unrecognised_question_falls_back_to_the_user():
    """See gates.BIAS. A question this detector cannot read is not evidence the
    graph lacks the answer, so it passes and the citation check stays on watch.
    """
    result = gates.entity_gate("what about that thing from before?", [USER])
    assert result.passed
    assert result.resolved == (gates.SELF_KEY,)


# --- gate 1: the alias closure --------------------------------------------

def test_an_alias_resolves_to_its_canonical_entity():
    """The case slice 05 built `ALIAS_OF` for: "Maya" and "Maya Chen" are one
    person. Measured on real data this fires never — 0 alias edges across 41
    sessions — so it is proven here or it is not proven at all.
    """
    maya_short = entity("person:maya", "Maya", eid=3)
    result = gates.entity_gate(
        "Did Maya call?", [USER, MAYA, maya_short],
        aliases={"person:maya": "person:maya chen"},
    )
    assert result.passed
    assert "person:maya chen" in result.resolved
    assert "person:maya" not in result.resolved


def test_a_cyclic_alias_closure_terminates():
    """The rows come off a graph, so the walk is bounded rather than trusted."""
    assert gates.canonical("a", {"a": "b", "b": "a"}) in {"a", "b"}


# --- gate 2: predicate detection ------------------------------------------

@pytest.mark.parametrize("question,expected", [
    ("Where do I work?", "employer"),
    ("What city do I live in?", "lives_in"),
    ("How old am I?", "age"),
    ("What did I buy last March?", "purchased"),
    ("What is my budget for the trip?", "budget"),
    ("Am I allergic to anything?", "allergic_to"),
])
def test_a_paraphrase_reaches_its_predicate(question, expected):
    assert expected in gates.question_predicates(question)


def test_other_is_never_a_wanted_predicate():
    """`other` holds 24.4% of real facts, so requiring it would pass every
    entity that has any fact at all — a gate that always passes.
    """
    assert "other" not in gates.question_predicates("what else did I say about other things")


# --- gate 2: no_such_relation ---------------------------------------------

def test_an_entity_with_no_fact_of_that_shape_abstains_naming_both():
    result = gates.predicate_gate(
        "Where do I work?", [fact("likes"), fact("pet")], gates.SELF_KEY
    )
    assert not result.passed
    assert result.reason == "no_such_relation"
    assert result.detail.startswith("no_such_relation: person:user has no ")
    assert "employer" in result.detail


def test_one_matching_predicate_is_enough():
    result = gates.predicate_gate("Where do I work?", [fact("employer")], gates.SELF_KEY)
    assert result.passed


def test_an_unreadable_question_passes_rather_than_abstaining():
    assert gates.predicate_gate("hmm?", [fact("likes")], gates.SELF_KEY).passed


def test_the_gate_cannot_see_a_mis_slotted_value():
    """Slice 06 measured `name: 'silver Honda Civic'` on instance
    `gpt4_2655b836`. Asked "what is my name", this gate finds a `name` fact and
    passes — the slot is filled and its contents are a car.

    Pinned as a characterisation test, not a bug: gate 2 checks shape by
    construction and no amount of tightening here would catch it. The retraction
    it causes is pinned in
    `test_chain.py::test_a_mis_slotted_functional_predicate_retracts_a_true_fact`,
    and catching it needs a value check nobody has built yet.
    """
    result = gates.predicate_gate(
        "What is my name?", [fact("name", "silver Honda Civic")], gates.SELF_KEY
    )
    assert result.passed


def test_other_is_a_sink_that_silently_disables_gate_2():
    """Measured live on instance `89941a93` while probing slice 08. The
    extractor filed "three bikes" and "four bikes" as `other`, so:

      - gate 2 abstains `no_such_relation: person:user has no owns` on "how
        many bikes do I currently own", a question the graph *does* answer, and
      - `other` is non-functional and the two values differ, so the chain never
        forms: 22 facts, 0 SUPERSEDES edges, on a knowledge-update instance.

    Both halves are pinned here as the real payload, not a paraphrase. The gate
    is behaving as specified -- it is the 24.4% `other` share that is not
    benign, and CLAUDE.md's "confirmed adequate" reading of it was wrong. Owned
    by issue 17, which costs a node wipe and so is not slice 08's to close.
    """
    facts = [fact("other", "three bikes"), fact("other", "four bikes"),
             fact("goal", "century ride")]
    result = gates.predicate_gate("How many bikes do I currently own?", facts,
                                  gates.SELF_KEY)
    assert not result.passed
    assert result.detail == "no_such_relation: person:user has no owns"


# --- the cascade ----------------------------------------------------------

def test_gate_1_short_circuits_before_gate_2_reads_anything():
    """No facts fetched means no round trip spent on a question already lost."""
    def facts_for(key):
        raise AssertionError("gate 2 ran after gate 1 fired")

    result = gates.run("Did Maya Chen call?", [USER], {}, facts_for)
    assert result.detail == "unknown_entity: maya chen"


def test_any_one_resolved_entity_holding_the_predicate_is_enough():
    facts = {gates.SELF_KEY: [fact("likes")], "person:maya chen": [fact("employer")]}
    result = gates.run("Where does Maya Chen work?", [USER, MAYA], {}, facts.get)
    assert result.passed


def test_the_cascade_reports_the_last_predicate_failure():
    facts = {gates.SELF_KEY: [fact("likes")], "person:maya chen": [fact("pet")]}
    result = gates.run("Where does Maya Chen work?", [USER, MAYA], {}, facts.get)
    assert result.reason == "no_such_relation"
    assert "employer" in result.detail


def test_a_glue_word_in_a_predicate_name_does_not_match():
    """Caught on real data, not here: `to` is a word in `subscribes_to` and
    `allergic_to`, so every question containing "to" wanted both. Gate 2 then
    found a held predicate on almost any question and stopped firing at all.
    """
    wanted = gates.question_predicates("Am I allergic to anything?")
    assert wanted == {"allergic_to"}

    assert "lives_in" not in gates.question_predicates("What did I put in the box?")
