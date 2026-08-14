"""Answer a question from the graph, or abstain with a reason.

Slice 03 built the end-to-end path: retrieve this instance's subgraph, answer
only from it, and downgrade to abstention when the answer is not supported.
Slice 07 puts gates 1 and 2 in front of it, so a question the graph provably
cannot answer costs no model call at all. Gates 3 and 4 (slices 08-09) join the
same cascade. The citation check below is the last line of defence and stays
where it is either way.

An answer that cites a fact id absent from the retrieved subgraph is not an
answer, it is a fluent guess wearing a citation. It is downgraded, never shown.
"""

import datetime as dt

from pydantic import BaseModel, Field

from . import client, gates, llm, statements

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


SYSTEM = (
    "You answer questions about a user from their stored memory graph. "
    "Return JSON only: {\"answer\": ..., \"cited_fact_ids\": [...]}\n"
    "Rules:\n"
    "- Use only the facts listed. Never use outside knowledge.\n"
    "- Cite the fact_id of every fact your answer rests on.\n"
    "- If the facts do not contain the answer, answer exactly ABSTAIN and cite "
    "nothing. An honest ABSTAIN is worth more than a plausible guess."
)


def fact_line(fact: dict) -> str:
    when = dt.datetime.fromtimestamp(fact["asserted_at"], dt.timezone.utc).strftime("%Y-%m-%d")
    return (
        f'{fact["fact_id"]} | {when} | {fact["subject_name"]} | {fact["predicate"]} '
        f'| {fact["value_text"]} | "{fact["snippet"]}"'
    )


def retrieve(driver, instance_id: str, bookmarks=None, consistency: str = "causal") -> list:
    """This instance's whole fact set, newest last.

    Slice 03 retrieves the instance rather than a gated subgraph -- the gates
    are what slices 07-09 add. The point being proved here is that the answer
    comes from the graph, not from the transcript.
    """
    return client.read(
        driver, statements.FACTS_FOR_INSTANCE,
        {"instance_id": instance_id}, bookmarks=bookmarks, consistency=consistency,
    )


def check_gates(driver, instance_id: str, question: str,
                bookmarks=None, consistency: str = "causal") -> gates.GateResult:
    """Gates 1 and 2 against the live graph. Two reads before the cascade runs.

    The entity list and the alias closure are pulled whole rather than queried
    per candidate: an instance holds tens of entities, so one round trip beats
    deciding which ones to ask about.
    """
    def read(statement, params):
        return client.read(driver, statement, params,
                           bookmarks=bookmarks, consistency=consistency)

    entities = read(statements.ENTITIES_FOR_INSTANCE, {"instance_id": instance_id})
    aliases = {
        row["alias_key"]: row["canonical_key"]
        for row in read(statements.ALIASES_FOR_INSTANCE, {"instance_id": instance_id})
    }
    return gates.run(question, entities, aliases,
                     gates.facts_reader(driver, entities, read))


def answer_question(driver, instance_id: str, question: str, asked_at: int = None,
                    bookmarks=None, consistency: str = "causal", model: str = None) -> Result:
    gate = check_gates(driver, instance_id, question,
                       bookmarks=bookmarks, consistency=consistency)
    if not gate.passed:
        # The whole point of a structural gate: no model was asked, so there was
        # nothing for it to confabulate with.
        return Result(answer=ABSTAIN, abstained=True, reason=gate.reason,
                      gate_detail=gate.detail)

    facts = retrieve(driver, instance_id, bookmarks=bookmarks, consistency=consistency)
    if not facts:
        return Result(answer=ABSTAIN, abstained=True, reason="empty_graph")

    asked = "" if asked_at is None else dt.datetime.fromtimestamp(
        asked_at, dt.timezone.utc
    ).strftime("%Y-%m-%d")
    body = "\n".join(fact_line(f) for f in facts)
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content":
            f"Question asked on {asked}: {question}\n\n"
            f"Facts (fact_id | asserted | subject | predicate | value | evidence):\n{body}"},
    ]

    reply = llm.complete_json(
        messages, Answer, model=model or llm.ANSWER_MODEL, reasoning=False,
        max_tokens=1024, response_format={"type": "json_object"},
    )

    text = reply.answer.strip()
    if text.upper().startswith(ABSTAIN):
        return Result(answer=ABSTAIN, abstained=True, reason="not_in_graph",
                      fact_count=len(facts))

    # The citation check is the thesis in five lines: an id the model invented,
    # or no id at all, means the answer is not grounded in what was retrieved.
    retrieved = {f["fact_id"] for f in facts}
    cited = [c for c in reply.cited_fact_ids if c in retrieved]
    if not cited:
        return Result(answer=ABSTAIN, abstained=True, reason="uncited_answer",
                      fact_count=len(facts))

    return Result(answer=text, abstained=False, cited_fact_ids=cited, fact_count=len(facts))
