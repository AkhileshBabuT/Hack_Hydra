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
    # how many of something. Slice 17 measured 68 of 633 `other` values (10.7%)
    # as a bare count -- "4 engineers", "3,000 points", "three bikes". They are
    # the values a knowledge-update question asks for, and while they sat in
    # `other` they could not chain: `three bikes` and `four bikes` are distinct
    # values under a non-functional predicate, so both stayed current and the
    # instance the supersession machinery exists for produced 0 SUPERSEDES edges.
    "quantity",
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

# Predicates whose slot is "the thing being counted", not the whole value.
#
# `quantity` is deliberately NOT functional. Functional would give one count per
# entity, so "17 cameras" would retract "four bikes" -- exactly the mis-slotting
# amplification pinned in test_chain.py. Instead the slot is the value with its
# leading count stripped, so `three bikes` -> `four bikes` chains and `17
# cameras` opens its own slot. See chain.group_key.
COUNTED_PREDICATES = frozenset(["quantity"])

# A leading cardinal, digits or the small words the corpus actually writes out.
# Anchored, so "four bikes" loses the count and "bike for four people" does not.
LEADING_COUNT = re.compile(
    r"^\s*(?:\d[\d,.]*|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    r"\s+(?:of\s+)?",
    re.IGNORECASE,
)

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
    # Slice 18 located why `single-session-assistant` scores 0.15 against 1.00,
    # and it was this opening line rather than the extraction rules below it.
    # Of the 20 questions: 10 die at gate 1 and **zero** are answered-wrong, so
    # nothing is being mis-extracted -- it is not being extracted. Five of those
    # instances hold no facts at all and eight hold facts with **zero** from an
    # assistant turn, while all three that answer correctly hold four or five.
    #
    # "Facts about the user" plus "the user is the subject of most facts" is a
    # strong enough prior that a session where the assistant explains a recipe
    # or names a hostel reads as having no facts in it. The assistant rule below
    # was already correct and was being outvoted by the framing above it.
    # `Fact.role` comes from the turn the fact cites, so a fact the model never
    # emits from an assistant turn can never be assistant-sourced downstream.
    "You extract durable facts from one conversation session. Both speakers "
    "state facts worth keeping, and a session where the assistant does most of "
    "the explaining is full of them. "
    "Return JSON only, matching the schema exactly. No prose, no markdown.\n"
    "Rules:\n"
    "- Only facts stated in the session. Never infer, never generalise.\n"
    # Slice 18b removed "the user is the subject of most facts" because that
    # framing was suppressing assistant facts entirely. It over-corrected:
    # measured across the 150-instance slice, **20 instances ended with no
    # `person:user` entity at all and 14 had *only* `person:assistant`** -- the
    # user erased from their own memory graph. "Where does my sister Emily
    # live?" landed on an instance holding 23 facts, one entity, and not a
    # single mention of Emily, while both baselines answered it.
    #
    # The anchor is restored *without* demoting the assistant: the user is the
    # default subject for anything the user said, and assistant-supplied content
    # keeps the first-class rule below. Both halves are now stated, which is
    # what neither version managed.
    "- A turn spoken by the user is about the user unless it explicitly names "
    "someone else as the subject. Use subject 'user' for those. Use "
    "'assistant' only for content the assistant itself supplied, and a named "
    "person, org or place when the session makes one the subject.\n"
    "- Almost every session contains facts about the user. If you have "
    "extracted nothing with subject 'user', re-read the user's turns before "
    "returning.\n"
    f"- predicate must be one of: {', '.join(PREDICATES)}\n"
    f"- subject_type must be one of: {', '.join(ENTITY_TYPES)}\n"
    "- value_is_entity is true only when the value names a person, org or place.\n"
    "- valid_from_hint copies the session's own words for when the fact became "
    "true ('last March', '2023-04-15'), or null if the session does not say.\n"
    "- turn_idx is the integer turn number the fact came from: 7 for turn [7].\n"
    "- evidence_span is a verbatim quote from that turn, at most 240 characters.\n"
    # Slice 12 measured what the old rule ("assistant suggestions the user did
    # not adopt are not facts") actually cost: on a session that is "user asks a
    # generic question, assistant explains", there are no facts about the user
    # at all, so the extractor returned an empty list and the graph stored
    # nothing. The whole `single-session-assistant` category -- 20 of the 150
    # evaluated questions -- scored **0**, because those questions ask the user
    # to recall what the *assistant* said.
    #
    # The rule was right about the failure it was preventing (issue 19: assistant
    # advice filed as a user `prefers` fact) and wrong about the remedy.
    # Attribute it, do not discard it: the subject says who it belongs to and
    # `Fact.role` already records which turn it came from, so `explain` can still
    # show a human that a claim rests on something the assistant said.
    "- The session has two speakers and both can state facts worth keeping. "
    "Something the *user* stated about themselves takes subject 'user'. "
    "Something the *assistant* stated -- an explanation, a recommendation, a "
    "figure or duration it supplied -- takes subject 'assistant'.\n"
    "- Never file assistant-stated content under subject 'user'. The user did "
    "not say it, and a preference the assistant suggested is not the user's "
    "preference unless the user agreed to it in their own turn.\n"
    # Affirmative, not a filter. The old wording ("keep ... only where") read as
    # a restriction on top of a framing that already discouraged assistant
    # facts, and the two together produced sessions with nothing extracted at
    # all. The exclusions are still here -- they are just no longer the point of
    # the sentence.
    "- Extract what the assistant supplied whenever the user could later ask "
    "about it: a recommendation, an instruction, a named place or product, a "
    "figure, a duration, a quantity, a step in a method. These are facts even "
    "though they are not about the user. Set turn_idx to the assistant's turn "
    "they came from. Skip only pleasantries and restatements of the user's own "
    "question.\n"
    "- A session in which the user asks and the assistant answers is not an "
    "empty session. If the assistant named anything specific, that is a fact.\n"
    "- Prefer concrete events with their dates over vague summaries.\n"
    # Slice 17, measured on the 633 `other` values in the slice-12 store: 121
    # (19.1%) were a named specific the extractor had generalised away, and
    # `single-session-assistant` scored 1 of 20 because of it. Asked for
    # `mountain meditation` the graph held `likes | mindfulness techniques`;
    # asked for a framerate it held `other | 2`. The theme is never the answer to
    # the question -- the name is.
    "- Copy names, titles, brands, places and numbers exactly as the session "
    "writes them. Never replace a specific with the category it belongs to: "
    "'Fender Mustang I V2' is the fact, 'a guitar' is not; 'Morning Glass "
    "Coffee + Cafe' is the fact, 'a cafe' is not.\n"
    # 68 of the 633 (10.7%) were bare counts sitting in `other`, where they
    # cannot chain. See COUNTED_PREDICATES.
    "- Use predicate 'quantity' when the fact is how many or how much of "
    "something, and keep the count and the thing counted together in the "
    "value: 'four bikes', '17 cameras', '3,000 points'.\n"
    "- Use 'other' only when no listed predicate fits. It is a last resort, "
    "not a default.\n"
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
