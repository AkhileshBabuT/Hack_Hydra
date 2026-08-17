"""Slice 11: the development loop. Twenty-five cases, live node, under ten seconds.

The full benchmark is far too slow to iterate against, so every slice after this
one needs a signal that comes back before you have lost the thread. These cases
run against real HydraDB -- real writes, real reads, real `algo.MSpaths`, real
round trips counted -- and stub only the answering model.

**Why the model is stubbed and the graph is not.** The thing under test is the
cascade's guarantee: which gate fires, on what, and at what cost. A fixture whose
verdict depends on what a 550B model happened to emit proves nothing about a
guarantee, and it cannot run in ten seconds. So `llm.complete` is scripted per
case and everything else is live. This is the same line slice 10 drew in
`test_answer.py`; the difference is that there the graph was a dict and here it
is the database.

**Tenants are content-addressed.** Fact ids are content-derived, so editing a
fixture's facts would otherwise write a *second* generation beside the first in
the same tenant and inflate every count -- the same trap that makes an extractor
change need a node wipe. Hashing the world into its `instance_id` means an edited
world simply lands in a new tenant and the stale one is orphaned, which costs a
few kilobytes and saves a wipe.

Run it alone: `make fixtures`.
"""

import datetime as dt
import hashlib
import json
import time
from dataclasses import dataclass

import pytest

from hydramem import answer, client, corpus, extract, ingest, llm, statements

BUDGET_SECONDS = 10.0
# Set once the worlds are in the graph. The budget is what the *loop* costs;
# a world edited since the last run pays a one-off ~6s ingest ahead of it, and
# folding that into the same number would make the test fail once at random
# after every fixture change.
_CASES_BEGAN = []


def _ts(day: str) -> int:
    return int(dt.datetime.strptime(day, "%Y-%m-%d")
               .replace(tzinfo=dt.timezone.utc).timestamp())


def _fact(predicate, value, turn, entity=False, subject="user", subject_type="person"):
    return extract.ExtractedFact(
        subject=subject, subject_type=subject_type, predicate=predicate,
        value=value, value_is_entity=entity, turn_idx=turn,
        evidence_span=f"{subject} {predicate} {value}",
    )


def _session(key, idx, day, facts, n_turns=4):
    """Turns alternate user/assistant, so `turn` picks who said the fact."""
    session = corpus.Session(
        session_id=key, idx=idx, timestamp=_ts(day),
        turns=tuple(
            corpus.Turn(i, "user" if i % 2 == 0 else "assistant", f"[{i}] text", False)
            for i in range(n_turns)
        ),
    )
    return (session, facts)


# --- the worlds ------------------------------------------------------------
#
# Three, not one: `no_path` needs two entities that genuinely do not connect,
# and an empty-graph abstention needs a tenant that was never written to. Every
# other case shares `main`, which is what keeps the suite inside its budget.

MAIN = [
    _session("fx-main-1", 0, "2019-03-15", [
        _fact("name", "Akhil", 0),
        _fact("employer", "Acme Corp", 0, entity=True),
        _fact("lives_in", "Berlin", 0, entity=True),
        _fact("likes", "jazz", 2),
        _fact("family_relation", "Maya Chen", 2, entity=True),
    ]),
    _session("fx-main-2", 1, "2021-06-01", [
        # Functional predicate: this retracts Acme rather than sitting beside it.
        _fact("employer", "Globex", 0, entity=True),
        _fact("pet", "Rufus", 2),
        # Issue 19, both halves. The extractor emits the assistant's own advice
        # as a `prefers` fact with the default subject; turn 3 is an assistant
        # turn, so slice 12 attributes it to `person:assistant` rather than
        # letting it impersonate the user. Kept as an *assistant* fact, not
        # deleted -- discarding it is what cost the whole
        # `single-session-assistant` category.
        _fact("prefers", "Choose a harmonious frame", 3),
    ]),
    _session("fx-main-3", 2, "2023-09-10", [
        _fact("job_title", "Staff Engineer", 0),
        _fact("owns", "a road bike", 0),
        # Slice 06 defect, reproduced: a car in the `name` slot. `name` is
        # functional, so the mis-slot does not merely add noise -- it supersedes
        # the true name asserted in 2019.
        _fact("name", "silver Honda Civic", 2),
    ]),
]

# Two people, one fact each, no shared object. MS_PATHS walks SUBJECT and OBJECT
# only, so nothing connects them at any hop count and gate 4 has something real
# to fire on -- which the corpus itself cannot supply (its graph is a star).
ISLANDS = [
    _session("fx-isl-1", 0, "2020-01-01", [
        _fact("occupation", "dentist", 0, subject="Rosalind Okonkwo"),
    ]),
    _session("fx-isl-2", 1, "2021-01-01", [
        _fact("occupation", "pilot", 0, subject="Tobias Vantablack"),
    ]),
]

WORLDS = {"main": MAIN, "islands": ISLANDS, "void": []}


def _tenant(name: str, extractions: list) -> str:
    """`fx-<name>-<hash of the world>`. See the module docstring."""
    payload = repr([(s.session_id, s.timestamp, [f.model_dump() for f in facts])
                    for s, facts in extractions])
    return f"fx-{name}-{hashlib.sha256(payload.encode()).hexdigest()[:8]}"


@pytest.fixture(scope="session")
def worlds(driver):
    """Every world ingested once. `void` is named but deliberately never written.

    The write is skipped when the tenant already holds its facts, which costs one
    read instead of fifteen writes -- and the tenant name *is* the hash of the
    world, so "already there" and "identical" are the same statement. Measured:
    5.7s of setup becomes 0.1s, which is most of the difference between a suite
    you run on every change and one you stop running.
    """
    out = {}
    for name, extractions in WORLDS.items():
        instance_id = _tenant(name, extractions)
        out[name] = instance_id
        if not extractions:
            continue
        want = sum(len(facts) for _, facts in extractions)
        held = client.read(driver, statements.COUNT_FACTS,
                           {"instance_id": instance_id})[0]["total"]
        if held >= want:
            continue
        instance = corpus.Instance(
            instance_id=instance_id, question_type="fixture", question="",
            answer="", asked_at=_ts("2024-01-01"),
            sessions=tuple(s for s, _ in extractions), answer_session_ids=(),
        )
        ingest.write_rows(driver, ingest.build_rows(instance, extractions))
    _CASES_BEGAN.append(time.monotonic())
    return out


# --- the scripted model ----------------------------------------------------

@dataclass(frozen=True)
class Case:
    name: str
    world: str
    question: str
    reason: str = ""            # "" means the case must answer
    trips: int = 3              # Bolt round trips, counted not estimated
    say: str = ""               # what the stubbed model replies
    cite: tuple = ()            # needles resolved to fact_ids from the prompt
    cite_raw: tuple = ()        # ids passed through verbatim, i.e. fabricated
    detail: str = ""            # substring required in gate_detail
    trace: tuple = ()           # substrings required in the gate trace


def _fact_id_for(body: str, needle: str) -> str:
    """The fact_id of the prompt line containing `needle`.

    Resolving citations out of the prompt rather than out of the database is
    deliberate: it proves the fact the case cites was actually in the context
    the model was given, which is the precondition the citation check tests.
    """
    for line in body.splitlines():
        if needle in line:
            return line.split(" | ", 1)[0].strip()
    raise AssertionError(f"no fact line matching {needle!r} in:\n{body}")


@pytest.fixture
def script(monkeypatch):
    def install(case: Case):
        def complete(messages, **kw):
            body = messages[-1]["content"]
            cited = [_fact_id_for(body, n) for n in case.cite] + list(case.cite_raw)
            return {
                "text": json.dumps({"answer": case.say, "cited_fact_ids": cited}),
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                "model": "stub", "cached": True,
            }
        monkeypatch.setattr(llm, "complete", complete)
    return install


# --- the cases -------------------------------------------------------------

ABSTAIN = [
    # Three, not two: since slice 12 gate 1 checks stored values and snippets
    # before rejecting a name, and that read is the third trip. Only a question
    # that was about to abstain here pays it -- see `gates.text_reader`.
    Case("gate1 names an entity the instance has never held", "main",
         "What did Priya Sharma recommend?",
         reason="unknown_entity", detail="priya sharma", trips=3),
    Case("gate1 on a tenant that was never written to", "void",
         "Where do I work?",
         reason="unknown_entity", detail="<empty graph>", trips=2),
    # The entity exists -- in another tenant. If resolution ever stopped being
    # instance-scoped this is the case that would start passing.
    Case("gate1 does not see another tenant's entity", "main",
         "What does Rosalind Okonkwo do for a living?",
         reason="unknown_entity", detail="rosalind okonkwo", trips=3),
    # These two abstained at gate 2 until slice 18. They still abstain -- the
    # guarantee is intact -- but the *defence moved to gate 5*, and what changed
    # is the price: a model call to refuse, where gate 2 refused for free.
    #
    # Two separate guards now let them past gate 2. Slice 12's `other` guard
    # fires first (the trace reads "pass (holds unlabelled `other` facts)"), and
    # slice 18's hub exemption would have anyway. Both are justified by the same
    # measurement: gate 2 fired 18 times on the oracle slice, all 18 about
    # `person:user`, and 16 were false abstentions.
    #
    # Kept as gate-5 cases rather than deleted, because "does the system still
    # refuse a question it cannot answer" is the property worth pinning, and it
    # is now pinned end to end through a model call.
    Case("a predicate the user does not hold now falls to gate 5", "main",
         "What is my email address?", reason="uncited_answer"),
    Case("same, on a single predicate", "main",
         "What language do I speak?", reason="uncited_answer"),
    Case("gate3 window predates every employer fact", "main",
         "Where did I work in 2015?",
         reason="no_fact_in_window", detail="2015-01-01"),
    Case("gate3 window predates the move", "main",
         "Where did I live in 2010?",
         reason="no_fact_in_window", detail="2010-01-01"),
    # Gate 2 is NOT vacuous after slice 18's hub exemption -- it still fires on a
    # specific entity. `person:rosalind okonkwo` holds one `occupation` fact and
    # nothing unlabelled, so neither the `other` guard nor the hub exemption
    # applies and absence really is evidence. This is the only remaining
    # `no_such_relation` coverage and it is deliberately on a non-hub entity,
    # which is exactly the distinction slice 18 drew.
    Case("gate2 still fires on a specific entity", "islands",
         "Where does Rosalind Okonkwo live?",
         reason="no_such_relation", detail="has no lives_in"),
    Case("gate4 finds no route between two real entities", "islands",
         "Did Rosalind Okonkwo and Tobias Vantablack ever meet?",
         reason="no_path", detail="no path within 4 hops", trips=4),
    Case("gate5 downgrades an answer that cites nothing", "main",
         "Where do I live?", reason="uncited_answer", say="Berlin"),
    Case("gate5 downgrades an invented citation", "main",
         "Where do I live?", reason="fabricated_citation",
         say="Berlin", cite_raw=("deadbeefdeadbeef",), detail="deadbeefdeadbeef"),
    # Slice 10's strictness in one case: a real id beside an invented one does
    # not redeem the answer, because nothing can say which half the claim rests
    # on. Lenient slice-09 behaviour would return "Berlin" here.
    Case("gate5 refuses a valid citation standing beside a fabricated one", "main",
         "Where do I live?", reason="fabricated_citation",
         say="Berlin", cite=("lives_in | Berlin",),
         cite_raw=("deadbeefdeadbeef",), detail="deadbeefdeadbeef"),
    Case("the model itself abstains", "main",
         "Do I have a pet?", reason="not_in_graph", say="ABSTAIN"),
    # Slice 12 closed the other half of issue 19. `Choose a harmonious frame`
    # was the assistant's advice, filed against the assistant's own turn, and it
    # used to answer "what do I prefer" as though the user had said it -- a
    # correctly cited wrong answer the citation check could never catch. It is
    # now attributed to `person:assistant`, so the user simply holds no `prefers`
    # fact and gate 2 says so.
    # Issue 19's property, still held, by a different gate. The advice is
    # attributed to `person:assistant`, so the user holds no `prefers` fact --
    # but gate 2 no longer says so, and the refusal now happens at the citation
    # check instead. Defence in depth doing exactly what it is for: the
    # structural gate that used to catch this was measured wrong far more often
    # than right, and removing it did not let the failure through.
    Case("assistant advice is still not the user's preference", "main",
         "What do I prefer when framing a photo?", reason="uncited_answer"),
]

ANSWER = [
    Case("single-session recall", "main", "Where do I live?",
         say="Berlin", cite=("lives_in | Berlin",)),
    Case("knowledge update: the plain question returns the current value", "main",
         "Where do I work?", say="Globex", cite=("employer | Globex",)),
    Case("knowledge update: the scoped question returns the old value", "main",
         "Where did I work in 2019?", say="Acme Corp",
         cite=("employer | Acme Corp",)),
    Case("an open fact is still valid inside a later window", "main",
         "Where did I live in 2020?", say="Berlin", cite=("lives_in | Berlin",)),
    Case("preference", "main", "What is my favourite music?",
         say="jazz", cite=("likes | jazz",)),
    Case("possession", "main", "Do I have a pet?",
         say="Rufus", cite=("pet | Rufus",)),
    Case("job title", "main", "What is my job title?",
         say="Staff Engineer", cite=("job_title | Staff Engineer",)),
    Case("ownership", "main", "What do I own?",
         say="a road bike", cite=("owns | a road bike",)),
    Case("a cue phrase the predicate name does not contain", "main",
         "What city did I move to?", say="Berlin", cite=("lives_in | Berlin",)),
    # Two anchors, so gate 4 issues the MSpaths call and the question costs four.
    Case("multi-hop: two entities that do connect", "main",
         "How is Maya Chen related to me?", trips=4,
         say="Maya Chen is family", cite=("family_relation | Maya Chen",),
         trace=("4 path: pass",)),
    # One anchor, so gate 4 is traced as skipped rather than passed: the trace
    # records what was spent, not only what was decided.
    Case("one anchor traces gate 4 as skipped, not passed", "main",
         "Who is Maya Chen?", say="Maya Chen is family",
         cite=("family_relation | Maya Chen",), trace=("4 path: skipped",)),
    # Slice 06 defect, live: `name` is functional, so a car in the name slot
    # retracts the true name rather than sitting beside it.
    Case("a mis-slotted functional predicate retracted a true fact", "main",
         "What is my name?", say="silver Honda Civic",
         cite=("name | silver Honda Civic",)),
]

CASES = ABSTAIN + ANSWER


def _run(driver, worlds, script, case: Case) -> answer.Result:
    script(case)
    return answer.answer_question(driver, worlds[case.world], case.question,
                                  asked_at=_ts("2024-01-01"))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_fixture(driver, worlds, script, case):
    result = _run(driver, worlds, script, case)
    assert result.abstained is bool(case.reason), answer.explain(result)
    assert result.reason == case.reason, answer.explain(result)
    if case.detail:
        assert case.detail in result.gate_detail, answer.explain(result)
    if not case.reason:
        assert case.say in result.answer
        assert result.cited_fact_ids
    for wanted in case.trace:
        assert any(wanted in step for step in result.gate_trace), result.gate_trace
    # Counted, never estimated. This is the whole cost claim, per case.
    assert result.round_trips == case.trips, answer.explain(result)


# --- what the fixtures are there to prove ----------------------------------

def test_the_suite_covers_every_reason_code():
    """A gate with no fixture proving it fires is a gate that will stop working
    silently -- every lexical bug in slices 07-10 was invisible to the suite."""
    required = {"unknown_entity", "no_such_relation", "no_fact_in_window",
                "no_path", "uncited_answer", "fabricated_citation", "not_in_graph"}
    assert required <= {c.reason for c in ABSTAIN}


def test_the_suite_is_big_enough_and_abstains_enough():
    assert len(CASES) >= 25
    assert len(ABSTAIN) >= 10


def test_the_knowledge_update_pair_disagrees_on_purpose(driver, worlds, script):
    """The whole bitemporal claim in two questions over one graph."""
    now = _run(driver, worlds, script,
               next(c for c in ANSWER if c.question == "Where do I work?"))
    then = _run(driver, worlds, script,
                next(c for c in ANSWER if c.question == "Where did I work in 2019?"))
    assert now.answer != then.answer
    assert then.window                      # the abstention-free path still names it
    # Derived from rows already in hand; slice 10 asserts it agrees with the edges.
    assert now.evidence[0]["supersedes"] == then.cited_fact_ids[0]


def test_the_mis_slotted_name_retracted_the_true_one(driver, worlds, script):
    """Slice 06's defect, end to end on a live graph rather than in a note.

    `silver Honda Civic` is not merely noise in the `name` slot: because `name`
    is functional it supersedes `Akhil`, so extraction error is amplified by the
    chain instead of diluted by it. If this ever starts failing, either the
    extractor stopped mis-slotting or `name` stopped being functional -- both
    worth knowing.
    """
    result = _run(driver, worlds, script,
                  next(c for c in ANSWER if c.question == "What is my name?"))
    assert result.evidence[0]["value"] == "silver Honda Civic"
    assert result.evidence[0]["supersedes"], "the car did not retract the name"
    assert "Akhil" not in result.answer


def test_an_assistant_sourced_fact_is_attributed_not_absorbed(driver, worlds):
    """Issue 19, closed by slice 12, asserted on the live graph.

    It used to be a *correctly cited wrong answer*: the fact really was in the
    retrieved set, so the citation check had nothing to fire on, and only the
    role said the user never claimed it. Attribution is now derived from the
    turn, so the advice lands on `person:assistant` and the user holds no
    `prefers` fact at all.

    The fact is not discarded -- that was the old mistake, and it cost the whole
    `single-session-assistant` category. It is still in the instance, still
    carries `role=assistant`, and is still retrieved for every question; it just
    no longer impersonates the user.
    """
    facts = client.read(driver, statements.FACTS_FOR_INSTANCE,
                        {"instance_id": worlds["main"]})
    advice = [f for f in facts if f["value_text"] == "Choose a harmonious frame"]
    assert advice, "the assistant's fact was discarded rather than attributed"
    assert advice[0]["subject_key"] == "person:assistant"
    assert advice[0]["role"] == "assistant"
    assert not [f for f in facts
                if f["subject_key"] == "person:user" and f["predicate"] == "prefers"]


def test_the_suite_stays_inside_its_ten_second_budget(worlds):
    """ponytail: wall clock, asserted in the last test rather than measured by
    a plugin. It is the number the issue asks for and it fails where a developer
    will read it. Runs last because pytest keeps file order.

    It measures the *node* as well as the suite, and that is not a flaw to
    engineer away -- the budget exists to keep the development loop usable, and a
    loaded node makes it unusable whatever the code does. Measured: 8.4s idle,
    23.0s while three evaluation arms were writing to the same node. If this
    fails, check what else is talking to HydraDB before reading it as a
    regression.
    """
    elapsed = time.monotonic() - _CASES_BEGAN[0]
    assert elapsed < BUDGET_SECONDS, f"{elapsed:.1f}s over a {BUDGET_SECONDS}s budget"
