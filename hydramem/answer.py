"""Answer a question from the graph, or abstain with a reason.

Slice 03 built the end-to-end path: retrieve this instance's subgraph, answer
only from it, and downgrade to abstention when the answer is not supported.
Slice 07 puts gates 1 and 2 in front of it, so a question the graph provably
cannot answer costs no model call at all. Slice 08 adds gate 3 and, with it,
the first *narrowing* of what gets retrieved: a question scoped to a window
sends the model only the facts valid in that window. Gate 4 (slice 09) joins
the same cascade. The citation check below is the last line of defence and
stays where it is either way.

An answer that cites a fact id absent from the retrieved subgraph is not an
answer, it is a fluent guess wearing a citation. It is downgraded, never shown.
"""

import datetime as dt

from pydantic import BaseModel, Field

from . import client, gates, llm, paths, statements, temporal

ABSTAIN = "ABSTAIN"


class Answer(BaseModel):
    answer: str
    cited_fact_ids: list = Field(default_factory=list)


class Result(BaseModel):
    answer: str
    abstained: bool
    reason: str = ""
    cited_fact_ids: list = Field(default_factory=list)
    fact_count: int = 0
    # Which gate fired and on what. `unknown_entity: person:maya chen` is a bug
    # report; `unknown_entity` alone is a shrug. Empty when no gate fired.
    gate_detail: str = ""
    # Bolt round trips this question cost, counted in `client`, not estimated.
    # The cost claim is only worth making if this number is measured.
    round_trips: int = 0
    # The window the question was read as scoping itself to, if any. Recorded
    # on the answer as well as on the abstention: a wrong answer to a temporal
    # question is usually a right answer to the wrong window.
    window: str = ""


SYSTEM = (
    "You answer questions about a user from their stored memory graph. "
    "Return JSON only: {\"answer\": ..., \"cited_fact_ids\": [...]}\n"
    "Rules:\n"
    "- Use only the facts listed. Never use outside knowledge.\n"
    "- Cite the fact_id of every fact your answer rests on.\n"
    "- A fact marked [superseded ...] was true until that date and is not true "
    "now. Use it only when the question asks about the past.\n"
    "- If the facts do not contain the answer, answer exactly ABSTAIN and cite "
    "nothing. An honest ABSTAIN is worth more than a plausible guess."
)


def _day(epoch: int) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%d")


def fact_line(fact: dict) -> str:
    """One fact as a line the model reads. The date is `valid_from`.

    Not `asserted_at`: the two are equal unless the extractor found a date in
    the text, and where they differ it is `valid_from` that answers "when was
    this true". The supersession marker is what makes the plain question return
    the current value -- without it the model sees a retracted employer and a
    current one as two equally live facts on the same line shape.
    """
    ended = "" if fact.get("valid_to", temporal.OPEN) == temporal.OPEN else \
        f' [superseded {_day(fact["valid_to"])}]'
    return (
        f'{fact["fact_id"]} | {_day(fact["valid_from"])} | {fact["subject_name"]} '
        f'| {fact["predicate"]} | {fact["value_text"]} | "{fact["snippet"]}"{ended}'
    )


def retrieve(driver, instance_id: str, bookmarks=None, consistency: str = "causal") -> list:
    """This instance's whole fact set, newest last.

    Kept as the standalone read for callers outside the cascade. Inside it,
    `check_gates` shares one lazy fetch of these rows with gates 2 and 3 rather
    than issuing this a second time -- see the round-trip budget there.
    """
    return client.read(
        driver, statements.FACTS_FOR_INSTANCE,
        {"instance_id": instance_id}, bookmarks=bookmarks, consistency=consistency,
    )


def check_gates(driver, instance_id: str, question: str, asked_at: int = None,
                bookmarks=None, consistency: str = "causal"):
    """The whole cascade against the live graph. Returns `(verdict, facts)`.

    The round-trip budget, and why it is four:

      1. `ENTITIES_FOR_INSTANCE` -- pulled whole, because an instance holds
         tens of entities and one read beats deciding which to ask about.
      2. `ALIASES_FOR_INSTANCE`  -- same.
      3. `FACTS_FOR_INSTANCE`    -- fetched lazily and *once*, then shared by
         gate 2, gate 3 and the answer itself.
      4. `algo.MSpaths`          -- one batched pairwise call for every anchor,
         and only when the question named more than one entity.

    A question lost at gate 1 costs 2; a single-entity question costs 3; a
    multi-hop question costs 4, whether it names two entities or twenty. The
    facts come back with the verdict so the caller does not re-read them, which
    is the difference between four round trips and six.
    """
    def read(statement, params):
        return client.read(driver, statement, params,
                           bookmarks=bookmarks, consistency=consistency)

    entities = read(statements.ENTITIES_FOR_INSTANCE, {"instance_id": instance_id})
    aliases = {
        row["alias_key"]: row["canonical_key"]
        for row in read(statements.ALIASES_FOR_INSTANCE, {"instance_id": instance_id})
    }
    all_facts, facts_for = gates.facts_reader(read, instance_id)

    def find_paths(anchors):
        return paths.find(driver, instance_id, anchors,
                          bookmarks=bookmarks, consistency=consistency)

    verdict = gates.run(question, entities, aliases, facts_for, asked_at, find_paths)
    return verdict, all_facts() if verdict.passed else []


def answer_question(driver, instance_id: str, question: str, asked_at: int = None,
                    bookmarks=None, consistency: str = "causal", model: str = None) -> Result:
    window = temporal.parse_window(question, asked_at)
    scope = window.detail if window else ""
    before = client.round_trips()

    def done(**kw) -> Result:
        return Result(window=scope, round_trips=client.round_trips() - before, **kw)

    gate, facts = check_gates(driver, instance_id, question, asked_at,
                              bookmarks=bookmarks, consistency=consistency)
    if not gate.passed:
        # The whole point of a structural gate: no model was asked, so there was
        # nothing for it to confabulate with.
        return done(answer=ABSTAIN, abstained=True, reason=gate.reason,
                    gate_detail=gate.detail)

    if not facts:
        return done(answer=ABSTAIN, abstained=True, reason="empty_graph")

    if window:
        # Narrowing, not filtering: the model never sees facts from outside the
        # window it was asked about, so "where did I work in 2019" cannot be
        # answered with the 2023 employer. Gate 3 has already established the
        # window is non-empty for the gated entity; `or facts` covers the case
        # where the retrieved set is wider than the entity the gate cleared.
        facts = temporal.in_window(facts, window) or facts

    asked = "" if asked_at is None else dt.datetime.fromtimestamp(
        asked_at, dt.timezone.utc
    ).strftime("%Y-%m-%d")
    body = "\n".join(fact_line(f) for f in facts)
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content":
            f"Question asked on {asked}: {question}\n\n"
            f"Facts (fact_id | valid from | subject | predicate | value | evidence):\n{body}"},
    ]

    reply = llm.complete_json(
        messages, Answer, model=model or llm.ANSWER_MODEL, reasoning=False,
        max_tokens=1024, response_format={"type": "json_object"},
    )

    text = reply.answer.strip()
    if text.upper().startswith(ABSTAIN):
        return done(answer=ABSTAIN, abstained=True, reason="not_in_graph",
                    fact_count=len(facts))

    # The citation check is the thesis in five lines: an id the model invented,
    # or no id at all, means the answer is not grounded in what was retrieved.
    retrieved = {f["fact_id"] for f in facts}
    cited = [c for c in reply.cited_fact_ids if c in retrieved]
    if not cited:
        return done(answer=ABSTAIN, abstained=True, reason="uncited_answer",
                    fact_count=len(facts))

    return done(answer=text, abstained=False, cited_fact_ids=cited,
                fact_count=len(facts))
