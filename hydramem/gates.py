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
from dataclasses import dataclass, field, replace

from . import extract, statements, temporal

SELF_KEY = "person:user"

# Entities that hold so much that absence of one label on them means nothing.
# Gate 2 may not assert `no_such_relation` about these -- see `predicate_gate`
# for the measurement. Deliberately the same shape as `paths.NON_TOPICAL_KEYS`:
# both are hub keys that make a gate structurally wrong rather than merely
# blunt, and both were found by reading which entity the false abstentions
# actually named rather than by reasoning about the recogniser.
HUB_KEYS = frozenset({SELF_KEY})

# First-person reference resolves to the user without going near the graph. The
# corpus is a first-person log, so this is the overwhelmingly common case: 96.9%
# of facts sit on `person:user` (docs/extraction-quality.md).
SELF_CUES = frozenset(
    ["i", "me", "my", "mine", "myself", "i'm", "im", "i've", "ive", "i'd", "i'll"]
)

# Second person means the assistant, and it is never a name. Measured live in
# slice 12: "You mentioned that companies use two-factor authentication..."
# abstained `unknown_entity: you`, because `_PROPER_RUN` reads a capitalised
# "You" as a proper noun and no entity is called that.
#
# Deliberately *dropped* rather than resolved to `person:assistant`. Resolving it
# would put two anchors on most of these questions, and `MS_PATHS` walks only
# SUBJECT and OBJECT -- `person:user` and `person:assistant` share neither, so
# gate 4 would report `no_path` on exactly the questions this was meant to fix.
# Dropping costs nothing, because `answer_question` sends the model every fact in
# the instance regardless of which entity gate 1 resolved.
_SECOND_PERSON = frozenset(
    ["you", "your", "yours", "yourself", "you've", "youve", "you'd", "you'll",
     "you're", "youre"]
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
     "would", "will", "the", "a", "an", "my", "i", "in", "on", "at", "if", "tell",
     # Found by slice 12 on live data: "Any tips?" abstained `unknown_entity:
     # any`. The corpus is chat, so questions open with a request or a filler far
     # more often than with a name, and every one of those is a false abstention
     # -- the one direction the stated bias exists to avoid. Dropping a candidate
     # only ever makes gate 1 pass more, which is the safe direction.
     "any", "some", "please", "let", "also", "just", "given", "based",
     "recently", "lately", "there", "these", "those", "after", "before",
     "since", "during", "while", "hey", "thanks", "recommend", "suggest"]
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
    # Without this, slice 17 would have traded one false abstention for another:
    # moving counts out of `other` into `quantity` removes the `via_other` pass
    # that was carrying "how many bikes do I currently own?", and gate 2 would
    # fire `no_such_relation: owns` on an entity that now holds the answer under
    # a label it was not told to ask for.
    "quantity": ("how many", "how much", "number of", "count", "how long"),
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
    # Mentions gate 1 could only resolve against stored *text* rather than
    # against an Entity node. Traced separately because it is the blunter path
    # and it costs the instance fact read -- "passed because the entity exists"
    # and "passed because a snippet contained the word" are different claims.
    via_text: tuple = field(default_factory=tuple)
    # Gate 2 passed only because the entity holds unlabelled `other` facts, so
    # absence could not be asserted. Traced separately for the same reason
    # `via_text` is: it is a weaker pass than "the entity holds this predicate".
    via_other: bool = False
    # One line per gate that actually ran, in order. The cascade short-circuits,
    # so the trace is also the record of which gates were never reached -- which
    # is the question you ask first when an abstention is wrong.
    trace: tuple = field(default_factory=tuple)

    @property
    def detail(self) -> str:
        return f"{self.reason}: {self.missing}" if self.missing else self.reason


PASS = GateResult(passed=True)

# Every reason an abstention can carry, in cascade order. Gates 1-4 are here,
# gate 5's three are here, and `empty_graph` is `answer`'s -- it is not a gate
# but it is a reason a question returns ABSTAIN, and a breakdown that omits it
# would not sum to the abstention count.
#
# This exists so a reason that fires *zero* times is still a row in the
# generated breakdown. A code that only appears when observed cannot be
# distinguished from one that does not exist, and the two findings this registry
# makes visible are both zeros: `no_path` never fires at all, and gate 5's
# `fabricated_citation` costs no false abstentions. Neither is reportable from a
# table built only from what happened.
REASONS = (
    "unknown_entity",       # 1 entity
    "no_such_relation",     # 2 predicate
    "no_fact_in_window",    # 3 window
    "no_path",              # 4 path
    "not_in_graph",         # 5 citation -- the model declined
    "uncited_answer",       # 5 citation -- answered, cited nothing
    "fabricated_citation",  # 5 citation -- cited an id it never read
    "empty_graph",          # not a gate: the instance holds no facts at all
)

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
        if lowered in _CALENDAR or lowered in _SECOND_PERSON:
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


def text_reader(all_facts):
    """`find_text(mention) -> entity key | None`, over what the graph stores as text.

    Gate 1's contract was "every entity the question names must exist as an
    `Entity` node", and that contract is wrong for this graph. Ingest creates an
    Entity only for a fact's *subject* and for an object whose `value_is_entity`
    flag is set; everything else stays a literal string on the fact. So the
    graph routinely holds `uses | Fitbit Charge 3` with no `thing:fitbit charge`
    node anywhere, and gate 1 abstained `unknown_entity: fitbit charge` on a
    question it could answer.

    Measured on the slice-12 run before this existed: **7 of 13 HydraMem
    abstentions were gate 1 firing on a name the graph did hold** -- `fitbit
    charge`, `tokyo`, `starbucks rewards` among them. That is a false abstention
    rate the thesis cannot survive, and false abstention is the one direction
    `BIAS` exists to rule out.

    So a mention is resolved if the instance holds it as a value or in an
    evidence snippet, and it resolves to the *subject* of that fact, which is
    who the question is about. Snippets count: a name in the evidence means the
    graph saw the text, and gate 1's job is to catch a question about something
    the graph has never heard of, not to police how well the extractor slotted
    it. A mention that appears in neither still abstains -- verified, `air
    fryer` and `miami` hit nothing at all on their instances.

    Lazy. `all_facts` is the same cached instance read gates 2 and 3 and the
    answer already share, so a question that passes pays nothing extra; only a
    question that was *about* to abstain at gate 1 triggers the read, and that
    moves its cost from 2 round trips to 3.
    """
    def find(mention: str):
        for fact in all_facts():
            for field_name in ("value_text", "snippet"):
                if mention in (fact.get(field_name) or "").lower():
                    return fact.get("subject_key") or SELF_KEY
        return None
    return find


def entity_gate(question: str, entities: list, aliases: dict = None,
                find_text=None) -> GateResult:
    """Gate 1. Every name the question uses must be something this instance holds.

    Resolution is by key first (`person:maya chen`), then by name, then through
    the alias closure -- the same normalisation `ingest.entity_key` applied on
    the way in, so a question and an ingest agree on what "Maya Chen" is -- and
    finally, if `find_text` is supplied, against what the instance stores as
    fact values and evidence text. See `text_reader` for why that last step is
    not optional in practice.
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
    resolved, via_text = [], []
    for mention in named:
        key = by_key.get(mention) or by_name.get(mention)
        if key is None and mention in aliases:
            key = by_key.get(canonical(mention, aliases))
        if key is None and find_text is not None:
            key = find_text(mention)
            if key is not None:
                via_text.append(mention)
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
    return GateResult(True, resolved=tuple(resolved), via_text=tuple(via_text))


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

    if "other" in held:
        # `other` is the extractor's "I captured this and could not label it",
        # and it is 30.3% of every fact in the slice-12 corpus. Firing
        # `no_such_relation` while holding unlabelled content about this very
        # entity is not evidence of absence -- it is this gate mistaking its own
        # vocabulary gap for the graph's silence, which is a false abstention by
        # construction and the one direction `BIAS` exists to rule out.
        #
        # Measured on the slice-12 run before this existed: gate 2 fired 30
        # times and **only 2 were correct**. Of the 16 firing on entities that
        # held `other` facts, 15 were false abstentions and 1 was right -- so
        # this recovers 15 answers at the cost of 1 abstention, and the gate
        # still fires on the 14 questions where the entity holds nothing
        # unlabelled at all. It is not made vacuous, it is made honest.
        return GateResult(True, resolved=(entity_key,) if entity_key else (),
                          via_other=True)

    if entity_key in HUB_KEYS:
        # The hub holds the user's whole life across 38 predicates the extractor
        # assigns unreliably, so a missing *label* there is a labelling gap and
        # not the graph's silence. This is the `other` argument above, extended
        # from one predicate to one entity, and the corpus says it holds:
        #
        # Measured on the oracle slice, `docs/eval/oracle-abstentions.csv`: gate
        # 2 fired **18 times, all 18 about `person:user`, and 16 were false.**
        # Both survivors are accidents rather than signal -- "how much time do I
        # dedicate to practising violin" abstained off a missing `quantity`
        # label, and "what did I bake for my uncle's birthday party" wanted a
        # cake while the cue table matched `birthday`. Precision for the right
        # reason is 0 of 18.
        #
        # This does not make the gate vacuous, it makes it honest about where
        # its premise holds. On a specific entity -- `org:acme` with two facts --
        # absence of a predicate is still evidence, and the gate still fires
        # there. It is `person:user` that it can say nothing about, for the same
        # structural reason gate 4 can say nothing about `person:assistant`.
        return GateResult(True, resolved=(entity_key,) if entity_key else (),
                          via_other=True)

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
    if found.paths or found.timed_out:
        # A timed-out traversal established nothing. Abstaining `no_path` on it
        # would report a structural absence the database never actually looked
        # for, which is the one thing this gate must never do.
        return GateResult(True, resolved=tuple(resolved))
    if not found.pairs_tried:
        # Every anchor was a speaker hub, so there was no topical pair to
        # connect and no call was made. Same shape as `len(resolved) < 2`.
        return PASS
    return GateResult(False, "no_path", f"{', '.join(resolved)}: {found.detail}")


def run(question: str, entities: list, aliases: dict, facts_for,
        asked_at: int = None, find_paths=None, find_text=None) -> GateResult:
    """Gates 1-4 in order, short-circuiting on the first that fires.

    `facts_for(entity_key) -> list`, `find_paths(keys) -> paths.PathResult` and
    `find_text(mention) -> key` are passed in rather than a driver, so the
    cascade is exercisable end to end with a dict, a stub and no database.
    `find_paths=None` skips gate 4 and `find_text=None` skips gate 1's text
    resolution, which is how every pure test of gates 1-3 stays a pure test.
    """
    trace = []

    def step(name, result):
        trace.append(f"{name}: {'pass' if result.passed else result.detail}")
        return result

    gate1 = step("1 entity", entity_gate(question, entities, aliases, find_text))
    if not gate1.passed:
        return replace(gate1, trace=tuple(trace))
    if gate1.via_text:
        # The trace records what was *spent* as well as what was decided: this
        # path consulted the instance fact read, and it is a weaker resolution
        # than an Entity node. Both matter when a gate-1 pass looks wrong.
        trace[-1] = f"1 entity: pass (via stored text: {', '.join(gate1.via_text)})"

    # Any one resolved entity clearing gates 2 and 3 is enough: "did Maya and I
    # go to Berlin" is answerable from either side of the pair. The verdict
    # carried out of the loop is the last *failure*, which is what names the
    # thing that was missing.
    verdict = PASS
    for key in gate1.resolved:
        facts = facts_for(key)
        verdict = step("2 predicate", predicate_gate(question, facts, key))
        if verdict.passed and verdict.via_other:
            trace[-1] = "2 predicate: pass (holds unlabelled `other` facts)"
        if verdict.passed:
            verdict = step("3 window", temporal_gate(question, facts, asked_at, key))
        if verdict.passed:
            break
    if not verdict.passed:
        return replace(verdict, trace=tuple(trace))

    if find_paths is None:
        return GateResult(True, resolved=gate1.resolved, trace=tuple(trace))
    if len(gate1.resolved) < 2:
        # Traced as skipped rather than passed: gate 4 costs a round trip when
        # it runs, so "pass" on a question that never called MSpaths reads as
        # a traversal that happened. The trace is an audit record of what was
        # spent, not only of what was decided.
        trace.append("4 path: skipped (one entity, nothing to connect)")
        return GateResult(True, resolved=gate1.resolved, trace=tuple(trace))

    gate4 = step("4 path", path_gate(gate1.resolved, find_paths))
    if not gate4.passed:
        return replace(gate4, trace=tuple(trace))
    return GateResult(True, resolved=gate1.resolved, trace=tuple(trace))


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
