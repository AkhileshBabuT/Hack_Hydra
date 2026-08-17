"""Slice 10: the citation check, provenance, and `explain`.

Every test here is pure. No driver, no model -- which is the point rather than
a convenience: the downgrade path is the thesis, and a test of it that depends
on what a model happens to return proves nothing about the guarantee. The
model's contribution is reduced to two values (the answer text and the list of
ids it claims), and both are supplied by hand.
"""

from conftest import make_fact, make_instance, make_session
from hydramem import answer, chain, ingest, temporal


def row(fact_id, **kw):
    """A fact in the shape `FACTS_FOR_INSTANCE` returns it."""
    base = {
        "id": kw.pop("id", abs(hash(fact_id)) % 10**9),
        "fact_id": fact_id,
        "predicate": "employer",
        "value_text": "Acme",
        "valid_from": 1_700_000_000,
        "valid_to": temporal.OPEN,
        "asserted_at": 1_700_000_000,
        "status": "current",
        "session_id": "s1",
        "turn_idx": 0,
        "role": "user",
        "snippet": "I work at Acme",
        "subject_key": "person:user",
        "subject_name": "user",
    }
    base.update(kw)
    return base


FACTS = [row("aaaa0000"), row("bbbb1111", value_text="Globex", predicate="lives_in")]


# --- the citation check ----------------------------------------------------

def test_an_answer_with_no_citation_is_downgraded():
    verdict = answer.citation_gate("I work at Acme.", [], FACTS)
    assert not verdict.passed
    assert verdict.reason == "uncited_answer"


def test_an_answer_citing_an_id_not_retrieved_is_downgraded():
    verdict = answer.citation_gate("I work at Initech.", ["deadbeef"], FACTS)
    assert not verdict.passed
    assert verdict.reason == "fabricated_citation"
    assert "deadbeef" in verdict.detail, "the abstention has to name the invented id"


def test_a_fabricated_id_beside_a_valid_one_is_still_fatal():
    """The slice-10 decision, pinned so it cannot be relaxed by accident.

    Slice 09 filtered invented ids out and kept the answer if anything valid
    survived. That is a different guarantee: it says "some of this was read",
    not "this was read". There is no way to tell which half of the claim rests
    on the id nobody can resolve, so the whole answer goes.
    """
    verdict = answer.citation_gate("Acme, and also Initech.",
                                   ["aaaa0000", "deadbeef"], FACTS)
    assert not verdict.passed
    assert verdict.reason == "fabricated_citation"
    assert "aaaa0000" not in verdict.detail, "only the invented id is the finding"


def test_a_transcription_slip_is_not_a_fabrication():
    """The model copies the id by hand, so case and whitespace are repaired.

    Repairing more than this would start guessing which fact was meant, which
    is exactly the confabulation the check exists to stop.
    """
    verdict = answer.citation_gate("Acme.", ["  AAAA0000 "], FACTS)
    assert verdict.passed
    assert verdict.resolved == ("aaaa0000",)


def test_the_abstain_token_is_honoured_before_any_citation_check():
    verdict = answer.citation_gate("ABSTAIN", ["deadbeef"], FACTS)
    assert not verdict.passed
    assert verdict.reason == "not_in_graph", \
        "a model that abstains has not fabricated anything"


def test_a_duplicate_citation_is_counted_once():
    verdict = answer.citation_gate("Acme.", ["aaaa0000", "aaaa0000"], FACTS)
    assert verdict.resolved == ("aaaa0000",)


# --- provenance ------------------------------------------------------------

def test_evidence_carries_session_turn_timestamp_and_role():
    [ev] = answer.evidence_for(FACTS, ["aaaa0000"])
    assert ev["session_id"] == "s1"
    assert ev["turn_idx"] == 0
    assert ev["valid_from"] == 1_700_000_000
    assert ev["role"] == "user"
    assert ev["snippet"] == "I work at Acme"


def test_a_fact_written_before_slice_10_reports_an_unknown_role():
    """`role` is a slice-10 property and the guarded merge will not backfill it.

    UPSERT_FACT is guarded on `asserted_at` with a strictly-less-than
    comparison, so re-ingesting an existing fact writes nothing at all -- the
    property is added to new generations only. HydraDB returns null for the
    missing property rather than failing, and null read as "user" would be a
    silent lie about who said the thing.
    """
    [ev] = answer.evidence_for([row("aaaa0000", role=None)], ["aaaa0000"])
    assert ev["role"] == "unknown"


def test_predecessors_agrees_with_the_supersedes_edges_the_graph_holds():
    """`explain` derives the chain from rows in hand instead of reading edges.

    That saves a round trip and creates a way to be wrong: the explanation
    could describe a chain the graph does not have. This asserts the two
    derivations agree on the same input, which is the only thing that makes the
    cheap one safe.
    """
    session = make_session(0, "s1", 1_700_000_000)
    later = make_session(1, "s2", 1_700_100_000)
    instance = make_instance([session, later])
    rows = ingest.build_rows(instance, [
        (session, [make_fact(value="Acme")]),
        (later, [make_fact(value="Globex")]),
    ])

    pairs = chain.derive(rows)
    assert pairs, "fixture must actually produce a chain or this proves nothing"
    from_edges = {new["fact_id"]: old["fact_id"] for old, new in pairs}

    key_of = {e["vid"]: e["key"] for e in rows.entities}
    subject_of = {r["fid"]: key_of[r["eid"]] for r in rows.subject}
    read_shape = [
        row(f["fact_id"], id=f["vid"], predicate=f["predicate"],
            value_text=f["value_text"], asserted_at=f["asserted_at"],
            turn_idx=f["turn_idx"], subject_key=subject_of[f["vid"]])
        for f in rows.facts
    ]
    assert answer.predecessors(read_shape) == from_edges


def test_a_non_functional_predicate_does_not_claim_a_predecessor():
    """`likes` accumulates, so two of them are not a revision of each other."""
    facts = [
        row("aaaa0000", predicate="likes", value_text="tea"),
        row("bbbb1111", predicate="likes", value_text="coffee",
            asserted_at=1_700_100_000),
    ]
    assert answer.predecessors(facts) == {}


# --- explain ---------------------------------------------------------------

def answered():
    return answer.Result(
        answer="You work at Acme.", abstained=False, cited_fact_ids=["aaaa0000"],
        evidence=answer.evidence_for(FACTS, ["aaaa0000"]), fact_count=2,
        round_trips=3, gate_trace=["1 entity: pass", "2 predicate: pass",
                                   "3 window: pass", "5 citation: pass"],
    )


def test_explain_shows_the_gate_trace_and_the_provenance_of_an_answer():
    text = answer.explain(answered())
    assert "3 round trips" in text
    assert "1 entity: pass" in text
    assert "aaaa0000" in text
    assert "session s1 turn 0" in text
    assert "valid from 2023-11-14" in text


def test_explain_works_on_an_abstention_and_names_the_gate_that_fired():
    result = answer.Result(
        answer=answer.ABSTAIN, abstained=True, reason="no_such_relation",
        gate_detail="no_such_relation: person:user has no owns",
        gate_trace=["1 entity: pass",
                    "2 predicate: no_such_relation: person:user has no owns"],
        round_trips=3,
    )
    text = answer.explain(result)
    assert "ABSTAIN" in text
    assert "person:user has no owns" in text
    assert "2 predicate" in text


def test_explain_marks_a_fact_the_assistant_said():
    """The failure this exists for: `ec81a493` filed the assistant's own advice
    as three user `prefers` facts. A citation pointing at one is a correctly
    cited wrong answer, and the trace is the only place it becomes visible.
    """
    facts = [row("aaaa0000", role="assistant", predicate="prefers",
                 value_text="a harmonious frame")]
    result = answer.Result(
        answer="You prefer a harmonious frame.", abstained=False,
        cited_fact_ids=["aaaa0000"], evidence=answer.evidence_for(facts, ["aaaa0000"]),
        fact_count=1, round_trips=3, gate_trace=["5 citation: pass"],
    )
    assert "ASSISTANT-SOURCED" in answer.explain(result)


def test_explain_shows_what_a_superseded_fact_replaced():
    facts = [
        row("aaaa0000", valid_to=1_700_100_000),
        row("bbbb1111", value_text="Globex", asserted_at=1_700_100_000,
            valid_from=1_700_100_000),
    ]
    result = answer.Result(
        answer="Globex.", abstained=False, cited_fact_ids=["bbbb1111"],
        evidence=answer.evidence_for(facts, ["bbbb1111"]), fact_count=2,
        round_trips=3, gate_trace=["5 citation: pass"],
    )
    text = answer.explain(result)
    assert "replaces aaaa0000" in text


# --- the appeal ------------------------------------------------------------
#
# `not_in_graph` is 27 of the 60 false abstentions on the oracle slice -- the
# largest single bucket, and the only one raised *after* retrieval succeeded, so
# the only one with facts in hand for a second look
# (`docs/eval/oracle-abstentions.csv`).
#
# Everything here is pure. An appeal that could only be tested through a live
# node and a real model would be the first decision in this cascade that is not,
# and a test of "the retry helped" that depends on what a model happens to
# return proves nothing about the guarantee.


def test_the_appeal_fires_on_the_reason_raised_after_retrieval():
    """`not_in_graph` is the only reason that clears the reason check.

    Passing the reason check is necessary and not sufficient -- the plan still
    has to have something new to show, which is asserted separately below. Here
    the question is windowed so that second condition is satisfied and this test
    is measuring the reason and nothing else.
    """
    plan = answer.plan_appeal("not_in_graph", FACTS, windowed=True)
    assert plan.appeal


def test_the_appeal_declines_every_reason_that_fires_before_retrieval():
    """Gates 1-3 abstain with nothing retrieved, so there is nothing to re-show.

    Asserted per reason rather than as a set difference: widening the appealable
    set later should be a visible one-line change against a test that already
    names what it is changing.
    """
    for reason in ("unknown_entity", "no_such_relation", "no_fact_in_window",
                   "no_path", "empty_graph"):
        plan = answer.plan_appeal(reason, FACTS)
        assert not plan.appeal, reason
        assert "before retrieval" in plan.declined


def test_the_appeal_declines_when_it_would_have_nothing_to_show():
    plan = answer.plan_appeal("not_in_graph", [])
    assert not plan.appeal
    assert plan.declined == "no facts in hand"


def test_a_declined_appeal_says_why():
    """An appeal that silently does not happen is indistinguishable from one
    that happened and found nothing, and those are opposite diagnoses."""
    assert answer.plan_appeal("unknown_entity", FACTS).declined
    assert answer.plan_appeal("not_in_graph", []).declined


def test_a_windowed_question_appeals_against_the_facts_the_window_removed():
    """The first call sees only the window it parsed. If that window was wrong
    the answer is in the facts it excluded, and re-reading the narrowed set
    forever cannot find it."""
    assert answer.plan_appeal("not_in_graph", FACTS, windowed=True).drop_window
    assert not answer.plan_appeal("not_in_graph", FACTS, windowed=False).drop_window


# --- the tiered render -----------------------------------------------------

def test_the_default_render_is_byte_identical_to_no_width_at_all():
    """Introducing the parameter must change no existing output."""
    for fact in FACTS:
        assert answer.fact_line(fact) == answer.fact_line(fact, None)


def test_a_narrow_render_truncates_the_evidence_and_a_wide_one_does_not():
    long = row("cccc2222", snippet="x" * 400)
    assert '"' + "x" * 100 + '"' in answer.fact_line(long, 100)
    assert '"' + "x" * 400 + '"' in answer.fact_line(long, None)


def test_widening_changes_nothing_for_a_snippet_that_was_never_truncated():
    """Most facts are shorter than any cap, so the tier must be a no-op on them
    -- otherwise the appeal's token cost would scale with the whole fact set
    rather than with the few facts that actually lost text."""
    short = row("dddd3333", snippet="I work at Acme")
    assert answer.fact_line(short, 10_000) == answer.fact_line(short, 14)


def test_the_first_call_renders_at_the_storage_cap_so_today_the_tier_is_inert():
    """`FIRST_WIDTH` equals `extract.MAX_SPAN` until issue 23 raises the cap.

    Pinned deliberately. The appeal cannot show more than the first call while
    these are equal, and a reader who assumes otherwise would credit the appeal
    with a recovery it structurally cannot have made yet.
    """
    from hydramem import extract
    assert answer.FIRST_WIDTH == extract.MAX_SPAN


def test_only_gate_fives_declined_answer_is_appealable():
    """A fabricated citation must never get a second chance.

    `not_in_graph` is the model saying the answer is not there. The other two
    gate-5 codes are the model failing the output contract -- retrying those
    would be asking it to try harder at citing, which is the confabulation the
    check exists to stop.
    """
    assert answer.APPEALABLE == {"not_in_graph"}
    assert not answer.plan_appeal("fabricated_citation", FACTS).appeal
    assert not answer.plan_appeal("uncited_answer", FACTS).appeal


def test_a_contract_failure_is_refused_for_the_right_reason():
    """The decline message is part of the audit record, so it has to be true.

    `fabricated_citation` fires *after* retrieval, with facts in hand -- calling
    it "fires before retrieval" would be a false explanation attached to a
    correct decision, and the trace is where somebody debugs this.
    """
    for reason in answer.CONTRACT_FAILURES:
        plan = answer.plan_appeal(reason, FACTS)
        assert not plan.appeal
        assert "contract failure" in plan.declined
        assert "before retrieval" not in plan.declined





def test_an_appeal_with_nothing_new_to_show_declines_instead_of_firing():
    """A retry that sends the identical request is not a retry.

    `llm._cache_key` hashes the messages, so identical evidence returns the
    identical refusal from disk -- and still counts its tokens. Firing anyway
    would record `appealed=True, appeal_won=False`, which reads as "we looked
    again and it is not there" when nothing was looked at. That is the one
    reading the counters exist to prevent.
    """
    plan = answer.plan_appeal("not_in_graph", FACTS, windowed=False)
    assert not plan.appeal
    assert "nothing new to show" in plan.declined


def test_a_snippet_longer_than_the_first_render_is_something_new_to_show():
    """This is what issue 23 unlocks: raise the storage cap and the retry has
    text the first call was never shown."""
    long = row("eeee4444", snippet="x" * (answer.FIRST_WIDTH + 1))
    assert answer.plan_appeal("not_in_graph", [long], windowed=False).appeal


def test_a_window_to_drop_is_something_new_to_show_even_with_short_snippets():
    assert answer.plan_appeal("not_in_graph", FACTS, windowed=True).appeal


def test_today_the_appeal_can_only_fire_on_a_windowed_question():
    """Pinned so the inertness is a measurement, not a surprise.

    `FIRST_WIDTH == extract.MAX_SPAN`, so no stored snippet can exceed the
    first render and the width tier cannot contribute. Until issue 23 raises
    the cap, dropping the window is the appeal's only mechanism -- and a reader
    of the appeal counters is entitled to know that before attributing a
    recovery to wider evidence.
    """
    from hydramem import extract
    assert answer.FIRST_WIDTH == extract.MAX_SPAN
    stored_ceiling = row("ffff5555", snippet="x" * extract.MAX_SPAN)
    assert not answer.plan_appeal("not_in_graph", [stored_ceiling]).appeal


# --- the appeal branch, driven for real ------------------------------------
#
# Still pure: `check_gates` and `llm.complete_json` are stubbed, so there is no
# driver and no model, but `answer_question`'s appeal branch actually executes.
# The three tests this replaced constructed a `Result` by hand and asserted on
# fields they had just set -- they passed whether or not the feature existed,
# which is the definition of test theatre.


class _Reply:
    def __init__(self, answer, cited):
        self.answer, self.cited_fact_ids = answer, cited


def drive(monkeypatch, replies, facts=None, windowed=False, **kw):
    """Run `answer_question` over `replies`, one per model call."""
    facts = FACTS if facts is None else facts
    monkeypatch.setattr(answer, "check_gates",
                        lambda *a, **k: (answer.gates.PASS, list(facts)))
    if windowed:
        monkeypatch.setattr(answer.temporal, "parse_window",
                            lambda *a, **k: temporal.Window(0, 9_999_999_999, "2023"))
        monkeypatch.setattr(answer.temporal, "in_window",
                            lambda rows, w: list(rows)[:1])
    calls = iter(replies)

    def fake(*a, **k):
        nxt = next(calls)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    monkeypatch.setattr(answer.llm, "complete_json", fake)
    return answer.answer_question(None, "inst", "where do I work?", **kw)


def test_an_appeal_that_errors_keeps_the_abstention_instead_of_raising(monkeypatch):
    """The safety property's real hole, and the one that could kill an arm.

    `llm.complete_json` raises on exhausted retries, on a non-retryable 4xx, and
    on the ~5% greedy-decode collapse. Nothing upstream catches it -- not
    `eval.run_hydramem`, not the mem0 surface. An uncaught exception here is
    strictly worse than the abstention `appeal=False` would have returned: no
    Result at all, and a 150-question arm dead. That has happened once already.
    """
    result = drive(monkeypatch,
                   [_Reply("ABSTAIN", []), RuntimeError("model gave up")],
                   windowed=True, appeal=True)
    assert result.abstained
    assert result.reason == "not_in_graph", "the original abstention, unchanged"
    assert result.appealed, "it fired, so it must be counted -- otherwise a run "\
                            "where every retry crashed reports appeals=0"
    assert any("errored" in step for step in result.gate_trace)


def test_a_won_appeal_answers_and_is_counted(monkeypatch):
    result = drive(monkeypatch,
                   [_Reply("ABSTAIN", []), _Reply("Acme.", ["aaaa0000"])],
                   windowed=True, appeal=True)
    assert not result.abstained
    assert result.answer == "Acme."
    assert result.appealed and result.appeal_won
    assert any("appeal: won" in step for step in result.gate_trace)


def test_a_lost_appeal_returns_the_first_calls_reason_not_the_retrys(monkeypatch):
    """An appeal may only overturn toward answering.

    The retry here fabricates an id, so its own verdict would be
    `fabricated_citation` -- a *different* and more damning reason than the
    `not_in_graph` the question actually earned. Reporting the retry's verdict
    would let the appeal make a question look worse, which is the one thing it
    must never do.
    """
    result = drive(monkeypatch,
                   [_Reply("ABSTAIN", []), _Reply("Initech.", ["deadbeef"])],
                   windowed=True, appeal=True)
    assert result.abstained
    assert result.reason == "not_in_graph"
    assert result.appealed and not result.appeal_won


def test_a_fabricated_citation_on_appeal_is_still_discarded(monkeypatch):
    """Gate 5 is not relaxed for the retry. A second chance cannot launder an
    ungrounded answer."""
    result = drive(monkeypatch,
                   [_Reply("ABSTAIN", []), _Reply("Initech.", ["deadbeef"])],
                   windowed=True, appeal=True)
    assert result.answer == answer.ABSTAIN


def test_the_appeal_does_not_run_when_it_is_switched_off(monkeypatch):
    """One model call, not two. The ablation depends on this being real."""
    result = drive(monkeypatch, [_Reply("ABSTAIN", [])],
                   windowed=True, appeal=False)
    assert result.abstained and not result.appealed
    assert any("appeal: skipped (appeal disabled)" in s for s in result.gate_trace)


def test_an_answered_question_never_reaches_the_appeal(monkeypatch):
    """Structurally impossible, and asserted rather than assumed."""
    result = drive(monkeypatch, [_Reply("Acme.", ["aaaa0000"])],
                   windowed=True, appeal=True)
    assert not result.abstained and not result.appealed


def test_a_collapsed_decode_is_retried_at_temperature_not_lost(monkeypatch):
    """The failure that has now killed a 150-question arm twice.

    Greedy decoding collapses on ~5% of calls, and the disk cache is keyed on
    the request -- so retrying the identical call returns the identical garbage
    forever and that question is permanently lost. Temperature is the only lever
    that reaches it. `extract` and `eval` have had this since slice 06; this
    caller did not, and CLAUDE.md had already written down that every caller
    needs it.
    """
    from pydantic import ValidationError as VE
    seen = []

    def flaky(messages, schema, **kw):
        seen.append(kw.get("temperature"))
        if len(seen) == 1:
            raise VE.from_exception_data("Answer", [])
        return _Reply("Acme.", ["aaaa0000"])

    monkeypatch.setattr(answer, "check_gates",
                        lambda *a, **k: (answer.gates.PASS, list(FACTS)))
    monkeypatch.setattr(answer.llm, "complete_json", flaky)
    result = answer.answer_question(None, "inst", "where do I work?")

    assert result.answer == "Acme.", "the question must not be lost to one bad decode"
    assert seen == [None, answer.extract.RETRY_TEMPERATURE], \
        "the retry has to change temperature, or the cache returns the same collapse"


def test_the_answer_budget_fits_the_citations_a_dense_instance_produces():
    """1024 was silently enough until the graph got denser, then killed an arm.

    Slice 18's extraction prompt took facts per instance from 14.0 to 34.9, max
    76 to 145. A 16-hex id plus quotes and a comma is ~22 characters, so 145 of
    them is ~3,200 characters of citations before a word of prose -- and the
    JSON truncated mid-list rather than failing loudly.

    Pinned as arithmetic against the measured worst case, not as a round number,
    so that raising the extractor's yield again shows up here as a failure
    instead of as another dead arm.
    """
    worst_case_ids = 145
    chars = worst_case_ids * 22          # "0123456789abcdef", ", "
    assert answer.ANSWER_MAX_TOKENS > chars / 4 + 256, \
        "budget must cover the citations plus prose, not just the citations"


# --- narrowing what reaches the model --------------------------------------
#
# Measured on the oracle slice: a question the model DECLINES holds a median of
# 42 facts against 31 for one it answers, mean 61.2 against 37.9. Slice 18's
# extraction prompt tripled density (14.0 -> 42.7) and the failures moved with
# it. This is the lost-in-the-middle effect the project already documents for
# the long-context baseline, happening inside its own prompt.


def dense(n, **kw):
    return [row(f"{i:016x}", **kw) for i in range(n)]


def test_narrowing_is_inert_below_the_cap():
    """79% of instances are under it, and must be provably unaffected."""
    facts = dense(answer.NARROW_CAP)
    assert answer.narrow("where do I work?", facts) is facts


def test_narrowing_keeps_the_original_order():
    """Facts arrive oldest-first and the model reads supersession markers in
    sequence. Re-sorting by relevance would scramble the one signal
    knowledge-update depends on, and that is the best-scoring category here."""
    facts = dense(answer.NARROW_CAP + 20)
    kept = answer.narrow("where do I work?", facts)
    ids_kept = [f["fact_id"] for f in kept]
    assert ids_kept == sorted(ids_kept), "order must survive narrowing"


def test_narrowing_prefers_facts_the_question_is_about():
    facts = (dense(answer.NARROW_CAP + 10, predicate="likes", value_text="jazz",
                   snippet="I like jazz")
             + [row("ffffffffffffffff", predicate="employer",
                    value_text="Globex", snippet="I work at Globex")])
    kept = answer.narrow("where do I work?", facts)
    assert any(f["fact_id"] == "ffffffffffffffff" for f in kept), \
        "the one relevant fact must survive a haystack of irrelevant ones"


def test_a_supersession_group_is_kept_whole():
    """Dropping the newer half of a chain shows the model a retracted value with
    nothing replacing it -- strictly worse than showing neither."""
    chain_pair = [
        row("aaaaaaaaaaaaaaa1", predicate="employer", value_text="Acme",
            valid_to=1_700_500_000, snippet="I work at Acme"),
        row("aaaaaaaaaaaaaaa2", predicate="employer", value_text="Globex",
            snippet="I work at Globex"),
    ]
    facts = chain_pair + dense(answer.NARROW_CAP + 10, predicate="likes",
                               value_text="jazz", snippet="jazz")
    kept = {f["fact_id"] for f in answer.narrow("where do I work?", facts)}
    both = {"aaaaaaaaaaaaaaa1", "aaaaaaaaaaaaaaa2"}
    assert both <= kept or not (both & kept), "a chain is kept whole or not at all"


def test_relevance_uses_no_model_and_no_embedding():
    """A ranker that hallucinates relevance is the same class of failure as a
    gate that hallucinates absence, and this decides what the model may see."""
    hit = row("a" * 16, predicate="employer", value_text="Globex",
              snippet="I work at Globex")
    miss = row("b" * 16, predicate="likes", value_text="jazz", snippet="jazz")
    assert answer.relevance("where do I work?", hit) >         answer.relevance("where do I work?", miss)
