"""Session-level fact extraction against a strict schema, reasoning OFF.

One call per session, not per turn: it cuts call volume 4-5x and coreference
resolves better with the whole session in context ("it", "there", "my new job"
all have their antecedent in the same window).

Reasoning is off through the chat template (see llm.py). A reasoning model
emits thinking tokens ahead of the JSON and strict parsing then fails on most
outputs -- this is the single highest-value setting in the pipeline.
"""

import re

from pydantic import BaseModel, Field, ValidationError, field_validator

from . import llm

# Controlled vocabulary. An open vocabulary destroys retrieval precision: the
# predicate gate cannot match "current_place_of_employment" against "employer",
# so it abstains on a question the graph can answer. Anything outside the list
# lands on "other" and stays retrievable by entity alone.
PREDICATES = (
    # identity & circumstance
    "name", "age", "occupation", "employer", "job_title", "lives_in",
    "hometown", "birthday", "family_relation", "pet", "health_condition",
    "education", "skill", "language",
    # stance
    "likes", "dislikes", "prefers", "allergic_to", "dietary_restriction",
    "goal", "plan", "budget",
    # possessions
    "owns", "uses", "subscribes_to",
    # events, the temporal-reasoning workhorses
    "purchased", "visited", "attended", "scheduled", "started", "stopped",
    "completed", "experienced",
    # contact
    "email", "phone", "address", "social_account",
    "other",
)

# Predicates that hold at most one value at a time. Only these form supersession
# chains: an employer replaces an employer, but a new "likes" does not retract
# the last one. Getting this wrong is not cosmetic -- treating every predicate as
# functional marked 193 of 220 facts superseded on a real instance, which would
# have left "what do I like" answerable only with the most recent thing ever said.
FUNCTIONAL_PREDICATES = frozenset([
    "name", "age", "occupation", "employer", "job_title", "lives_in",
    "hometown", "birthday", "email", "phone", "address", "budget",
])

ENTITY_TYPES = ("person", "org", "place", "thing", "preference", "event")

_WS = re.compile(r"\s+")

# The extractor fills a required field rather than omitting the fact. These
# are absences dressed as values and must never reach the graph as facts.
EMPTY_VALUES = frozenset(
    ["none", "nothing", "unknown", "n/a", "na", "null", "unspecified", "not applicable"]
)
EMPTY_PREFIXES = ("none ", "nothing ", "not mentioned", "no specific", "not specified")


class ExtractedFact(BaseModel):
    # subject and predicate carry defaults because the extractor omits them on
    # roughly one session in ten: the corpus is a first-person log, so it treats
    # "the user" as obvious, and it occasionally misspells the field name. A
    # fact with a defaulted subject is still true and still retrievable; losing
    # the session's other 37 facts over it is not a trade worth making.
    subject: str = "user"
    subject_type: str = "person"
    predicate: str = "other"
    value: str = ""
    value_is_entity: bool = False
    valid_from_hint: str | None = None   # "2024-03", "last summer", None
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    turn_idx: int = 0
    evidence_span: str = ""              # verbatim, trimmed to 240 chars below

    # The model copies the "[7]" turn marker back verbatim and sometimes sends
    # a null span. Both are cosmetic, and rejecting a whole session's facts over
    # them would throw away good extractions -- coerce, don't fail.
    @field_validator("turn_idx", mode="before")
    @classmethod
    def _digits_only(cls, v):
        # A null turn is the extractor declining to say which turn -- the same
        # gesture as a null string, and it must cost the same: nothing. Left
        # unhandled it raised, and pydantic reports per-fact errors as one
        # failed Extraction, so a single null turn threw away all 27 facts in
        # the session. Worth 8 points of schema validity on the slice 06 sample
        # (86.8% -> 94.7%); the session-loss rate was never a model problem.
        if v is None:
            return 0
        if isinstance(v, str):
            digits = "".join(c for c in v if c.isdigit())
            return int(digits) if digits else 0
        return v

    @field_validator("subject", "value", "evidence_span", "subject_type", mode="before")
    @classmethod
    def _null_is_empty(cls, v):
        # A null value is the extractor declining to fill the field. clean()
        # drops the fact; rejecting the whole session over it would lose the
        # good facts alongside it.
        return "" if v is None else v


class Extraction(BaseModel):
    facts: list[ExtractedFact] = Field(default_factory=list)


SYSTEM = (
    "You extract durable facts about the user from one conversation session. "
    "Return JSON only, matching the schema exactly. No prose, no markdown.\n"
    "Rules:\n"
    "- Only facts stated in the session. Never infer, never generalise.\n"
    "- The user is the subject of most facts; use the subject 'user' unless a "
    "different person, org or place is explicitly named.\n"
    f"- predicate must be one of: {', '.join(PREDICATES)}\n"
    f"- subject_type must be one of: {', '.join(ENTITY_TYPES)}\n"
    "- value_is_entity is true only when the value names a person, org or place.\n"
    "- valid_from_hint copies the session's own words for when the fact became "
    "true ('last March', '2023-04-15'), or null if the session does not say.\n"
    "- turn_idx is the integer turn number the fact came from: 7 for turn [7].\n"
    "- evidence_span is a verbatim quote from that turn, at most 240 characters.\n"
    "- Assistant suggestions the user did not adopt are not facts.\n"
    "- Prefer concrete events with their dates over vague summaries.\n"
    "- Never emit a fact whose value says nothing was mentioned or is unknown.\n"
    "- Return an empty facts list rather than inventing anything."
)

USER = """Session date: {date}

{body}

Extract the durable facts as JSON: {{"facts": [...]}}"""

SCHEMA_HINT = {"type": "json_object"}
MAX_SPAN = 240
MAX_OUTPUT_TOKENS = 8192
# Enough to leave the collapsed greedy path, small enough that the output
# is still the session's facts and not a paraphrase of them.
RETRY_TEMPERATURE = 0.3


def prompt(session, date: str) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": USER.format(date=date, body=session.text())},
    ]


def clean(facts: list[ExtractedFact], turn_count: int) -> list[ExtractedFact]:
    """Constrain a model's output to what the schema actually allows.

    Pure, so the repair rules are testable without a model call. Off-vocabulary
    predicates become "other" rather than being dropped: a fact with a wrong
    label is still reachable through its entity, a dropped fact is gone.
    """
    out = []
    for f in facts:
        value = f.value.strip()
        if not f.subject.strip() or not value:
            continue  # a fact with no subject or no value cannot be retrieved
        lowered = value.lower().rstrip(".")
        if lowered in EMPTY_VALUES or lowered.startswith(EMPTY_PREFIXES):
            continue  # "none mentioned" is the extractor filling the schema
        out.append(
            f.model_copy(
                update={
                    "subject": f.subject.strip(),
                    "value": value,
                    "predicate": f.predicate if f.predicate in PREDICATES else "other",
                    "subject_type": f.subject_type if f.subject_type in ENTITY_TYPES else "thing",
                    "turn_idx": f.turn_idx if 0 <= f.turn_idx < turn_count else 0,
                    "evidence_span": f.evidence_span[:MAX_SPAN],
                }
            )
        )
    return out


def grounded(span: str, session_text: str) -> bool:
    """Is the evidence span actually in the session, or did the model write it?

    Pure, so the hallucination check runs over fixtures with no model call. The
    comparison is whitespace- and case-insensitive because the extractor
    reflows quotes across line breaks; anything looser would stop catching
    invention, which is the only thing this measures.
    """
    if not span.strip():
        return False
    flat = _WS.sub(" ", session_text).lower()
    return _WS.sub(" ", span).strip().lower() in flat


def _call(session, date: str, model: str, temperature: float) -> Extraction:
    return llm.complete_json(
        prompt(session, date),
        Extraction,
        model=model or llm.EXTRACT_MODEL,
        reasoning=False,
        temperature=temperature,
        # A dense session yields 30+ facts. At 4096 the response was truncated
        # mid-string and the whole session was lost to a JSON parse error --
        # which looked like a model quality problem and was ours.
        max_tokens=MAX_OUTPUT_TOKENS,
        response_format=SCHEMA_HINT,
    )


def extract_raw(session, date: str, model: str = None) -> Extraction:
    """The model call alone, before clean() repairs anything.

    Slice 06 needs both sides: the difference between this and the cleaned
    output *is* the repair rate, and it is the evidence for whether the
    controlled predicate vocabulary is adequate.

    One retry at a nudged temperature, and only on unparseable output. Greedy
    decoding collapses outright on roughly one session in twenty -- measured:
    one returned the single character `{`, another a run of zero-width spaces --
    and temperature is the only lever that reaches it. Retrying the *identical*
    request cannot: the cache is keyed on the request, so a degenerate decode
    is otherwise cached forever and that session is permanently lost.
    """
    try:
        return _call(session, date, model, 0.0)
    except ValidationError:
        return _call(session, date, model, RETRY_TEMPERATURE)


def extract_session(session, date: str, model: str = None) -> list[ExtractedFact]:
    """One cached model call. Raises on unparseable output -- never swallows it.

    A parse failure is a quality signal that slice 06 measures; counting it as
    "zero facts" would hide it in the one place it must be visible.
    """
    return clean(extract_raw(session, date, model).facts, len(session.turns))
