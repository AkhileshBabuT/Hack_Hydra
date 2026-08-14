"""Gates 1-3 of the abstention cascade: does the graph *have* the subject, does
it have anything of the right shape to say about it, and does it have anything
inside the window the question asked about.

Both are pure functions of (question, rows). No model is asked anything here,
and that is the point rather than an optimisation: a gate whose job is to stop
confabulation cannot itself be a language model, or it inherits exactly the
failure it exists to prevent. The cost is that entity and predicate detection
are lexical and therefore blunt -- see `BIAS` below for which way they are
deliberately blunt.

`answer.py` calls `run()`; everything else here is testable with plain lists.

BIAS: **an unrecognised question passes.** Gate 1 fires only when the question
names something the graph does not have, and gate 2 only when the question
names a predicate the entity has no fact for. A question whose entities or
predicates these heuristics simply fail to spot goes through to the model,
where the citation check is still waiting. That direction is chosen: a false
abstention is an answer thrown away with no way to notice, while a false pass
costs one model call and is caught downstream.
"""

import functools
import re
from dataclasses import dataclass, field

from . import extract, statements, temporal

SELF_KEY = "person:user"

# First-person reference resolves to the user without going near the graph. The
# corpus is a first-person log, so this is the overwhelmingly common case: 96.9%
# of facts sit on `person:user` (docs/extraction-quality.md).
SELF_CUES = frozenset(
    ["i", "me", "my", "mine", "myself", "i'm", "im", "i've", "ive", "i'd", "i'll"]
)

# Capitalised runs, which is all a proper-noun detector needs to be here. It
# over-fires on a capitalised common noun and that is the safe direction: an
# extra candidate that resolves to nothing is only a candidate, while a missed
# one would let gate 1 pass on an entity the graph has never seen.
_PROPER_RUN = re.compile(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]+)*")
_WORDS = re.compile(r"[a-z0-9']+")

# Words that start a question and are therefore capitalised by grammar, not by
# being names. Without this every "What", "When" and "Did" is a candidate entity.
_SENTENCE_STARTERS = frozenset(
    ["what", "when", "where", "who", "why", "how", "which", "did", "do", "does",
     "is", "was", "are", "were", "has", "have", "had", "can", "could", "should",
     "would", "will", "the", "a", "an", "my", "i", "in", "on", "at", "if", "tell"]
)

# A capitalised month or weekday is a date, not a name. Found by probing slice
# 08 against instance `89941a93`: "How many bikes did I have in February 2023?"
# abstained `unknown_entity: february`, because gate 1 read the month as an
# entity the graph had never seen. Temporal questions are made of these, so the
# gate slice 08 adds was unreachable behind the gate slice 07 added.
#
# Dropping a candidate makes gate 1 *pass* more, which is the safe direction
# (see BIAS). The cost is that a person actually called May is invisible to
# gate 1, which is a trade the corpus does not care about.
_CALENDAR = frozenset(temporal.MONTHS) | frozenset(
    ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
     "today", "tomorrow", "yesterday", "christmas", "easter", "thanksgiving"]
)

# Question phrasing -> candidate predicates. Only paraphrases a reader would
# not guess from the predicate name itself; the name's own words are matched
# separately in `question_predicates`, so "what is my budget" needs no entry.
#
# ponytail: a literal dict, not a synonym model or an embedding lookup. It is
# wrong in a knowable direction (a phrasing that is missing simply passes the
# gate) and every entry is auditable in one screen. Replace it when a measured
# abstention-recall number says the misses matter, not before.
CUES = {
    "employer": ("work", "work for", "work at", "employer", "company", "employed"),
    "job_title": ("job title", "my title", "my role", "my position"),
    "occupation": ("for a living", "occupation", "profession", "career", "my job"),
    "lives_in": ("live", "living", "moved to", "residence", "reside", "what city"),
    "hometown": ("grew up", "come from", "originally from", "born in"),
    "family_relation": ("sister", "brother", "mother", "father", "wife",
                        "husband", "son", "daughter", "cousin", "parents"),
    "health_condition": ("diagnosed", "condition", "symptom", "illness", "injury"),
    "dietary_restriction": ("vegan", "vegetarian", "gluten", "diet"),
    "allergic_to": ("allergy", "allergies", "allergic"),
    "likes": ("favourite", "favorite", "enjoy", "into", "fan of"),
    "dislikes": ("hate", "dislike", "can't stand", "avoid"),
    "prefers": ("prefer", "preference", "rather"),
    "purchased": ("buy", "bought", "purchase", "ordered", "paid for"),
    "visited": ("visit", "went to", "travelled", "traveled", "trip to"),
    "attended": ("attend", "went to the", "conference", "concert", "wedding"),
    "scheduled": ("scheduled", "appointment", "booked", "upcoming"),
    "started": ("start", "started", "began", "took up"),
    "stopped": ("stop", "stopped", "quit", "gave up"),
    "completed": ("finish", "finished", "completed", "graduated"),
    "goal": ("goal", "aiming", "want to", "trying to"),
    "plan": ("plan", "planning", "going to"),
    "budget": ("budget", "spend", "afford", "how much"),
    "owns": ("own", "have a", "my car", "my house"),
    "uses": ("use", "using", "app", "tool"),
    "subscribes_to": ("subscribe", "subscription", "member of"),
    "education": ("study", "studied", "degree", "university", "school", "course"),
    "skill": ("skill", "good at", "learning", "practice"),
    "language": ("language", "speak", "fluent"),
    "pet": ("pet", "dog", "cat"),
    "age": ("how old", "age", "birthday"),
    "name": ("my name", "called"),
    "email": ("email", "e-mail"),
    "phone": ("phone", "number"),
    "address": ("address", "street", "postcode", "zip"),
}


@dataclass(frozen=True)
class GateResult:
    """A gate's verdict, carrying the specific thing that was missing.

    A bare reason code is not debuggable: `unknown_entity` tells you a gate
    fired, `unknown_entity: person:maya chen` tells you which assumption was
    wrong. The detail is the whole value of a structural abstention.
    """

    passed: bool
    reason: str = ""
    missing: str = ""
    resolved: tuple = field(default_factory=tuple)   # entity keys gate 2 reads

    @property
    def detail(self) -> str:
        return f"{self.reason}: {self.missing}" if self.missing else self.reason


PASS = GateResult(passed=True)


def mentions(question: str) -> list:
    """Surface forms the question names, lowercased. Self-reference included.

    Returns a list rather than a set so the reason string names entities in the
    order they were asked about, which is the order a human debugging an
    abstention reads them in.
    """
    found = []
    for run in _PROPER_RUN.findall(question):
        lowered = run.lower()
        if lowered.split()[0] in _SENTENCE_STARTERS:
            # Strip a grammatical capital, keep any real name behind it:
            # "Did Maya call" -> "maya".
            rest = " ".join(run.split()[1:])
            if not rest:
                continue
            lowered = rest.lower()
        if lowered in _CALENDAR:
            continue
        if lowered not in found:
            found.append(lowered)
    if any(w in SELF_CUES for w in _WORDS.findall(question.lower())):
        found.append(SELF_KEY)
    return found


def canonical(key: str, aliases: dict) -> str:
    """Follow the alias closure to the canonical key.

    Bounded rather than recursive: `ingest.alias_pairs` only ever points a
    shorter surface form at a longer one, so a cycle cannot be produced -- but
    this reads rows off a graph anyone can write to, and an unbounded walk over
    hostile input is a hang, not a bug report.
    """
    seen = set()
    while key in aliases and key not in seen:
        seen.add(key)
        key = aliases[key]
    return key


def entity_gate(question: str, entities: list, aliases: dict = None) -> GateResult:
    """Gate 1. Every entity the question names must exist in this instance.

    Resolution is by key first (`person:maya chen`), then by name, then through
    the alias closure -- the same normalisation `ingest.entity_key` applied on
    the way in, so a question and an ingest agree on what "Maya Chen" is.
    """
    aliases = aliases or {}
    if not entities:
        return GateResult(False, "unknown_entity", "<empty graph>")

    by_key = {e["key"]: e["key"] for e in entities}
    by_name = {}
    for e in entities:
        by_name.setdefault(e["name"].strip().lower(), e["key"])
        # `person:maya chen` is also findable as `maya chen`.
        by_name.setdefault(e["key"].split(":", 1)[-1], e["key"])

    named = mentions(question)
    resolved = []
    for mention in named:
        key = by_key.get(mention) or by_name.get(mention)
        if key is None and mention in aliases:
            key = by_key.get(canonical(mention, aliases))
        if key is None:
            return GateResult(False, "unknown_entity", mention)
        target = canonical(key, aliases)
        if target not in resolved:
            resolved.append(target)

    if not resolved:
        # The question named nothing this detector recognises. Treat it as being
        # about the user, which it is in a first-person log, and let the model
        # see the graph -- see BIAS.
        fallback = SELF_KEY if SELF_KEY in by_key else entities[0]["key"]
        return GateResult(True, resolved=(fallback,))
    return GateResult(True, resolved=tuple(resolved))


# Glue words inside a predicate name (`allergic_to`, `lives_in`) carry none of
# its meaning. Matching on them made every question containing the word "to"
# want `subscribes_to` and `allergic_to`, which meant gate 2 found a held
# predicate almost always and stopped firing -- caught on real data, not here.
_NAME_GLUE = frozenset(["to", "in", "of", "at", "on", "for", "is", "by"])


@functools.lru_cache(maxsize=64)
def _name_parts(predicate: str) -> frozenset:
    return frozenset(p for p in predicate.split("_") if p not in _NAME_GLUE)


@functools.lru_cache(maxsize=512)
def _cue(text: str):
    """A cue matches on word boundaries, never as a substring.

    `"work" in "homework"` is true and would put every question about homework
    on the employer predicate. Compiled once per cue: the whole table is walked
    for every question the gate sees.
    """
    return re.compile(rf"\b{re.escape(text)}\b")


def _cue_hit(cue: str, lowered: str) -> bool:
    return _cue(cue).search(lowered) is not None


def question_predicates(question: str) -> set:
    """Predicates the question could be asking for. Empty means "no idea"."""
    lowered = question.lower()
    words = set(_WORDS.findall(lowered))
    wanted = set()
    for predicate in extract.PREDICATES:
        if predicate == "other":
            continue          # matches everything, so it discriminates nothing
        if _name_parts(predicate) & words:
            wanted.add(predicate)
    for predicate, cues in CUES.items():
        if any(_cue_hit(cue, lowered) for cue in cues):
            wanted.add(predicate)
    return wanted


def predicate_gate(question: str, facts: list, entity_key: str = "") -> GateResult:
    """Gate 2. The resolved entity must hold a fact of a shape the question asks for.

    `facts` is every fact on the entity, fetched in one round trip, because
    HydraDB's `WHERE` has no `IN`. Filtering here rather than in Cypher is also
    what makes this gate debuggable without a database, which matters more than
    the round trip: it is the first suspect whenever abstention precision drops.

    KNOWN BLIND SPOT: this checks that a predicate *slot* is filled, never that
    its value belongs in it. Slice 06 measured `name: 'silver Honda Civic'` on
    real data, and this gate passes it -- the shape is right and the content is
    a car. Only a human or a later value check catches that.
    """
    wanted = question_predicates(question)
    if not wanted:
        return PASS               # cannot require what we could not name

    held = {f["predicate"] for f in facts}
    if held & wanted:
        return GateResult(True, resolved=(entity_key,) if entity_key else ())

    missing = ", ".join(sorted(wanted))
    where = f"{entity_key} has no " if entity_key else "no fact with "
    return GateResult(False, "no_such_relation", f"{where}{missing}")


def temporal_gate(question: str, facts: list, asked_at: int = None,
                  entity_key: str = "") -> GateResult:
    """Gate 3. A question scoped to a time window needs a fact valid in it.

    No window in the question means no opinion: `parse_window` returns None for
    everything it cannot read, and that passes. When a window *is* resolved,
    the abstention names it -- "no fact valid in 2019 (2019-01-01..2020-01-01)"
    is auditable in a way that "no" is not, and the resolved bounds are the
    first thing to check when the abstention is wrong.

    The window is applied to the predicates gate 2 named, when it named any, so
    "where did I work in 2019" asks about employment in 2019 rather than about
    any fact at all in 2019. If that intersection is empty the whole fact set
    is used instead: an empty scope is this gate failing to read the question,
    not evidence the graph is silent.
    """
    window = temporal.parse_window(question, asked_at)
    if window is None:
        return PASS

    wanted = question_predicates(question)
    scoped = [f for f in facts if f.get("predicate") in wanted] if wanted else []
    if temporal.in_window(scoped or facts, window):
        return GateResult(True, resolved=(entity_key,) if entity_key else ())

    where = f"{entity_key} has no " if entity_key else "no "
    return GateResult(False, "no_fact_in_window", f"{where}fact valid {window.detail}")


def path_gate(resolved, find_paths) -> GateResult:
    """Gate 4. Entities the question relates must actually be connected.

    A question naming one entity is not multi-hop and costs no path call: there
    is no pair to connect. With two or more, `find_paths` resolves *every* pair
    in one batched call rather than a traversal per pair, which is the whole
    point of the gate and the reason the round-trip count stays flat as the
    anchor count grows.

    The abstention names the pairs tried and the hop bound, because "no path"
    on its own cannot be told apart from a bound that was set too low.
    """
    if len(resolved) < 2:
        return PASS

    found = find_paths(resolved)
    if found.paths:
        return GateResult(True, resolved=tuple(resolved))
    return GateResult(False, "no_path", f"{', '.join(resolved)}: {found.detail}")


def run(question: str, entities: list, aliases: dict, facts_for,
        asked_at: int = None, find_paths=None) -> GateResult:
    """Gates 1-4 in order, short-circuiting on the first that fires.

    `facts_for(entity_key) -> list` and `find_paths(keys) -> paths.PathResult`
    are passed in rather than a driver, so the cascade is exercisable end to end
    with a dict, a stub and no database. `find_paths=None` skips gate 4, which
    is how every pure test of gates 1-3 stays a pure test.
    """
    gate1 = entity_gate(question, entities, aliases)
    if not gate1.passed:
        return gate1

    # Any one resolved entity clearing gates 2 and 3 is enough: "did Maya and I
    # go to Berlin" is answerable from either side of the pair. The verdict
    # carried out of the loop is the last *failure*, which is what names the
    # thing that was missing.
    verdict = PASS
    for key in gate1.resolved:
        facts = facts_for(key)
        verdict = predicate_gate(question, facts, key)
        if verdict.passed:
            verdict = temporal_gate(question, facts, asked_at, key)
        if verdict.passed:
            break
    if not verdict.passed:
        return verdict

    if find_paths is None:
        return GateResult(True, resolved=gate1.resolved)
    gate4 = path_gate(gate1.resolved, find_paths)
    return gate4 if not gate4.passed else GateResult(True, resolved=gate1.resolved)


def facts_reader(read, instance_id: str):
    """Returns `(all_facts, facts_for)`, both backed by one lazy instance read.

    Slice 07 fetched per entity, which cost a round trip for each entity gate 1
    resolved and then a *second* fetch of the same rows for the answer. One
    instance-wide read serves gates 2 and 3 and the answer, which is what makes
    the four-round-trip budget in slice 09 reachable at all.

    Lazy, not eager: a question that loses at gate 1 still costs no fact read.
    Splitting by subject in Python rather than in Cypher is the same trade gate
    2 already makes -- HydraDB's WHERE has no IN, and one read of tens of rows
    beats one read per entity.
    """
    cache = {}

    def all_facts() -> list:
        if "rows" not in cache:
            cache["rows"] = read(statements.FACTS_FOR_INSTANCE,
                                 {"instance_id": instance_id})
        return cache["rows"]

    def facts_for(entity_key: str) -> list:
        return [f for f in all_facts() if f.get("subject_key") == entity_key]

    return all_facts, facts_for
