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
    """A *specific* entity. The hub is exempt -- see the hub-key tests below."""
    result = gates.predicate_gate(
        "Where do I work?", [fact("likes"), fact("pet")], "org:acme"
    )
    assert not result.passed
    assert result.reason == "no_such_relation"
    assert result.detail.startswith("no_such_relation: org:acme has no ")
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

    **Half of this is closed by slice 12 and half is not**, which is why the
    original payload is kept rather than rewritten.

    The *gate* half is fixed: gate 2 no longer abstains here, because the entity
    holds unlabelled `other` facts and absence cannot be asserted over them. The
    question now reaches the model, which can read "three bikes" and "four
    bikes" for itself.

    The *chain* half is untouched and still wrong: `other` is non-functional and
    the two values differ, so a knowledge-update instance still forms **0**
    SUPERSEDES edges out of 22 facts. That needs the extractor to stop filing
    countable possessions under `other`, which costs a node wipe. Still issue 17.
    """
    facts = [fact("other", "three bikes"), fact("other", "four bikes"),
             fact("goal", "century ride")]
    result = gates.predicate_gate("How many bikes do I currently own?", facts,
                                  gates.SELF_KEY)
    assert result.passed, "slice 12: unlabelled facts block the absence claim"
    assert result.via_other
    # The chain half of the defect is unchanged, and this is what still hurts.
    assert "owns" not in {f["predicate"] for f in facts}



def test_a_quoted_event_title_is_read_as_an_entity_and_loses_the_question():
    """Measured live on instance `gpt4_2487a7cb` while probing slice 10.

    The instance holds `visited | workshop on "Effective Time Management"` and
    the question names the workshop by its title. `mentions` sees a capitalised
    run, gate 1 looks for an entity called `effective time management`, finds
    none, and abstains -- so a question the graph demonstrably answers costs 2
    round trips and returns nothing.

    This is the same shape as the calendar-word bug slice 08 fixed and the glue
    -word bug slice 07 fixed: the lexical layer over-firing on a capitalised
    phrase that is not a name. It is *not* the same fix. Dropping calendar words
    is a closed list; a quoted title is open, and the title is often exactly the
    thing the question is about. Owned by issue 18.

    Pinned as the live payload, so the day gate 1 learns to read a quoted span
    as a value rather than a name, this test fails and says so.
    """
    question = ("Which event did I attend first, the 'Effective Time Management' "
                "workshop or the 'Data Analysis using Python' webinar?")
    assert "effective time management" in gates.mentions(question)

    result = gates.entity_gate(question, [USER])
    assert not result.passed
    assert result.detail == "unknown_entity: effective time management"


def test_the_quoted_title_resolves_once_gate_1_can_read_stored_text():
    """Issue 18, closed by slice 12. The graph held the title all along.

    `visited | workshop on "Effective Time Management"` is on the instance as a
    fact *value*, and gate 1 could not see it because ingest creates an `Entity`
    only for a subject and for an object with `value_is_entity` set. The fix is
    not a list of exceptions -- a quoted title is an open set -- it is that the
    gate resolves against what the instance stores as text before rejecting a
    name.
    """
    question = ("Which event did I attend first, the 'Effective Time Management' "
                "workshop or the 'Data Analysis using Python' webinar?")
    stored = [{"predicate": "visited", "subject_key": gates.SELF_KEY,
               "value_text": 'workshop on "Effective Time Management"',
               "snippet": 'I went to the "Data Analysis using Python" webinar'}]

    result = gates.entity_gate(question, [USER],
                               find_text=gates.text_reader(lambda: stored))
    assert result.passed
    assert result.resolved == (gates.SELF_KEY,)
    # Every one of them resolved by the weaker route, and the trace says so.
    # Note the second title arrives as two mentions, not one: `_PROPER_RUN`
    # breaks a capitalised run at the lowercase "using". That is why the fix
    # cannot be "recognise quoted spans" -- the recogniser never sees the quotes
    # as a unit, and matching fragments against stored text does not care.
    assert set(result.via_text) == {"effective time management",
                                    "data analysis", "python"}


# --- gate 1 against stored text (slice 12) --------------------------------
#
# Payloads are the live ones from the slice-12 run, where 7 of the first 13
# HydraMem abstentions were gate 1 rejecting a name the graph did hold.

@pytest.mark.parametrize("mention, stored", [
    # `uses | Fitbit Charge 3` -- a literal value, so no Entity node exists.
    ("How long have I been using my Fitbit Charge 3?",
     {"value_text": "Fitbit Charge 3", "snippet": ""}),
    # `goal | reach the Gold level on the Starbucks Rewards app` -- the name is
    # buried inside a longer value, which is the common shape.
    ("How many stars do I need for gold on my Starbucks Rewards app?",
     {"value_text": "reach the Gold level on the Starbucks Rewards app",
      "snippet": ""}),
    # Held only in the evidence span. Gate 1 asks whether the graph has heard of
    # this at all, not whether the extractor slotted it well.
    ("Any tips for getting around Tokyo?",
     {"value_text": "sightseeing", "snippet": "I am flying to Tokyo in March"}),
])
def test_a_name_the_graph_holds_as_text_resolves(mention, stored):
    stored = [{"predicate": "uses", "subject_key": gates.SELF_KEY, **stored}]
    result = gates.entity_gate(mention, [USER],
                               find_text=gates.text_reader(lambda: stored))
    assert result.passed, result.detail
    assert result.resolved == (gates.SELF_KEY,)


def test_a_name_the_graph_has_never_seen_still_abstains():
    """Verified live: `air fryer` and `miami` hit nothing at all on their own
    instances. Those abstentions are honest and the loss is upstream in
    extraction -- the gate must not swallow them to look better."""
    stored = [{"predicate": "owns", "subject_key": gates.SELF_KEY,
               "value_text": "Instant Pot", "snippet": "I bought an Instant Pot"}]
    result = gates.entity_gate("What did I buy before the Air Fryer?", [USER],
                               find_text=gates.text_reader(lambda: stored))
    assert not result.passed
    assert result.detail == "unknown_entity: air fryer"


def test_a_capitalised_chat_opener_is_not_a_name():
    """`"Any tips?"` abstained `unknown_entity: any` on live data. The corpus is
    chat, so a question opens with a request or a filler far more often than
    with a name, and each one of those was a false abstention."""
    for question in ("Any tips for my phone battery?",
                     "Please remind me what I ordered.",
                     "Recently I changed jobs, where do I work now?"):
        assert gates.mentions(question) == [gates.SELF_KEY] or not [
            m for m in gates.mentions(question) if m != gates.SELF_KEY
        ], question


def test_resolving_by_text_is_lazy(monkeypatch):
    """A question whose names all resolve as entities must not trigger the fact
    read. That laziness is the whole reason the fix costs nothing on the path
    that matters."""
    def explode():
        raise AssertionError("the fact read was issued for a resolvable name")

    result = gates.entity_gate("Did Maya Chen call?", [USER, MAYA],
                               find_text=gates.text_reader(explode))
    assert result.passed and result.via_text == ()


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


# --- gate 2 and the `other` sink (slice 12) --------------------------------

def test_gate_2_cannot_assert_absence_while_holding_unlabelled_facts():
    """Measured: gate 2 fired 30 times on the slice-12 run and only 2 were right.

    `other` is the extractor's "captured, could not label", and it is 30.3% of
    every fact in the corpus. Abstaining `no_such_relation` about an entity that
    holds `other` facts mistakes this gate's own vocabulary gap for the graph's
    silence. Of the 16 such firings, 15 were false abstentions.
    """
    facts = [fact("other", "reached the Gold level on the Starbucks Rewards app")]
    result = gates.predicate_gate("What is my email address?", facts,
                                  gates.SELF_KEY)
    assert result.passed
    assert result.via_other, "the weaker pass must be distinguishable"


def test_gate_2_still_fires_when_nothing_is_unlabelled():
    """The guard must not make the gate vacuous. On the same run it still fired
    on the 14 questions whose entity held no `other` fact at all."""
    facts = [fact("likes", "jazz"), fact("pet", "Rufus")]
    result = gates.predicate_gate("What is my email address?", facts,
                                  "org:acme")
    assert not result.passed
    assert result.reason == "no_such_relation"
    assert not result.via_other


def test_a_held_predicate_still_passes_the_strong_way():
    """An entity that actually holds the predicate must not be reported as the
    weak `other` pass -- the trace would then misdescribe why it got through."""
    facts = [fact("employer", "Globex"), fact("other", "something")]
    result = gates.predicate_gate("Where do I work?", facts, gates.SELF_KEY)
    assert result.passed and not result.via_other


# --- gate 2 cannot assert absence about the hub ----------------------------
#
# Measured on the oracle slice: gate 2 fired 18 times, **all 18 about
# `person:user`**, and 16 were false abstentions. The two survivors are
# accidents, not signal -- one abstained off a missing `quantity` label, and the
# other ("what did I bake for my uncle's birthday party") wanted a cake while
# the cue table matched `birthday`. Precision for the right reason is 0 of 18.


def test_gate_2_does_not_assert_absence_about_the_hub():
    """`person:user` holds the whole life across 38 unreliable labels.

    A missing label there is the extractor's vocabulary gap, not the graph's
    silence -- the same argument the `other` guard already makes, extended from
    one predicate to one entity because the corpus says it holds.
    """
    facts = [fact("likes", "jazz"), fact("pet", "Rufus")]
    result = gates.predicate_gate("Where do I work?", facts, gates.SELF_KEY)
    assert result.passed
    assert result.via_other, "a weak pass has to be distinguishable in the trace"


def test_gate_2_still_fires_on_a_specific_entity():
    """Not vacuous. On an entity that holds two facts, absence is still
    evidence -- it is the hub, and only the hub, that it can say nothing about.
    """
    facts = [fact("likes", "jazz"), fact("pet", "Rufus")]
    result = gates.predicate_gate("Where do I work?", facts, "org:acme")
    assert not result.passed
    assert result.reason == "no_such_relation"


def test_the_hub_exemption_and_gate_4s_are_the_same_shape():
    """Both are hub keys that make a gate structurally wrong rather than blunt,
    and both were found by reading which entity the false abstentions named."""
    from hydramem import paths
    assert gates.SELF_KEY in gates.HUB_KEYS
    assert "person:assistant" in paths.NON_TOPICAL_KEYS
    # person:user stays a valid gate-4 anchor -- the two exemptions are about
    # different failures and must not be collapsed into one list.
    assert gates.SELF_KEY not in paths.NON_TOPICAL_KEYS
