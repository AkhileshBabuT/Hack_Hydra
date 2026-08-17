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
from collections import defaultdict

from pydantic import BaseModel, Field, ValidationError

from . import chain, client, extract, gates, ids, llm, paths, statements, temporal

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
    # Provenance for every cited fact: session, turn, role, timestamps, and the
    # fact it replaced. Built from rows already in hand, so it costs nothing.
    evidence: list = Field(default_factory=list)
    # One line per check that ran, gates 1-4 and the citation check. Present on
    # an answer as well as an abstention -- "which gates did this get past" is
    # the same question either way.
    gate_trace: list = Field(default_factory=list)
    # An appeal ran: the abstention got a second look at wider evidence. Counted
    # rather than inferred from the trace, because "how often did this fire and
    # how often did it win" is the only thing that separates an appeal path that
    # is working from one that is broken -- the failure mode every gate here has
    # had at least once.
    appealed: bool = False
    appeal_won: bool = False


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


def fact_line(fact: dict, width: int = None) -> str:
    """One fact as a line the model reads. The date is `valid_from`.

    Not `asserted_at`: the two are equal unless the extractor found a date in
    the text, and where they differ it is `valid_from` that answers "when was
    this true". The supersession marker is what makes the plain question return
    the current value -- without it the model sees a retracted employer and a
    current one as two equally live facts on the same line shape.

    `width` truncates the stored evidence snippet. `None` renders whatever is
    stored, which is what every existing caller gets and what the default has to
    keep meaning: introducing this parameter must change no output.

    It exists so the *storage* cap and the *render* cap can differ. The first
    call renders narrow and an appeal renders wide, which is the only thing that
    makes the appeal a different request -- model calls are cached on the
    request, so re-asking with identical evidence returns the identical refusal
    forever. Until `extract.MAX_SPAN` is raised (issue 23) the two widths are
    equal and the tier is inert by construction, not by accident.
    """
    ended = "" if fact.get("valid_to", temporal.OPEN) == temporal.OPEN else \
        f' [superseded {_day(fact["valid_to"])}]'
    snippet = fact["snippet"]
    if width is not None and len(snippet) > width:
        snippet = snippet[:width]
    return (
        f'{fact["fact_id"]} | {_day(fact["valid_from"])} | {fact["subject_name"]} '
        f'| {fact["predicate"]} | {fact["value_text"]} | "{snippet}"{ended}'
    )


# How many facts reach the model. Above this, the least relevant are dropped.
#
# `answer_question` has always sent every fact in the instance, narrowed only by
# a resolved window. That was right at 14.0 facts per instance. Slice 18's
# extraction prompt took it to 42.7, and the failures moved with the density:
# a question the model *declines* holds a median of 42 facts against 31 for one
# it answers, mean 61.2 against 37.9, max 236 against 127. That is the
# lost-in-the-middle degradation this project documents for the long-context
# baseline, now happening inside its own prompt.
#
# 60 was chosen from a distribution that no longer holds. At the time: p50 = 35,
# p75 = 55, p90 = 78, max = 236, biting on 21% of instances. **After slice 18e
# restored the user as the default subject the yield fell 42.7 -> 26.9 facts per
# instance**, and the live distribution is p50 = 24, p90 = 65, max = 183 -- the
# cap now fires on **15 of 150 instances (10%)**.
#
# Still live, not dead code, and deliberately left at 60 rather than re-tuned.
# Two reasons. The ablation measured narrowing as worth **zero accuracy** (93
# correct against 93, paired), so re-deriving the number optimises a lever with
# no demonstrated effect on the thing it was built for. And the tail it exists
# to stop is still there -- one instance holds 183 facts.
#
# If the extractor's yield moves again, re-derive from the *current*
# distribution rather than trusting either set of numbers above. Both are dated.
NARROW_CAP = 60


def relevance(question: str, fact: dict) -> int:
    """Word overlap between the question and a fact. Lexical, like the gates.

    No model and no embedding: a ranker that hallucinates relevance is the same
    class of failure as a gate that hallucinates absence, and this decides what
    the model is allowed to see.
    """
    words = set(gates._WORDS.findall(question.lower()))
    text = f'{fact["predicate"]} {fact["value_text"]} {fact.get("snippet", "")}'
    return len(words & set(gates._WORDS.findall(text.lower())))


def narrow(question: str, facts: list, cap: int = NARROW_CAP) -> list:
    """The `cap` most relevant facts, **in their original order**.

    Two properties matter more than the ranking itself:

    **Order is preserved.** Facts arrive oldest-first and the model reads
    supersession markers in sequence; re-sorting by relevance would scramble the
    one signal knowledge-update depends on, and that is the best-scoring
    category here (0.8846).

    **A supersession group is kept whole.** Dropping the newer half of a chain
    would show the model a retracted value with nothing replacing it -- strictly
    worse than showing neither. So a group is kept entirely or not at all, which
    can push the result slightly over `cap`. That is the correct direction: the
    cap exists to stop a haystack, not to hit a number.

    Inert below the cap, which is 79% of instances, and asserted as such.
    """
    if len(facts) <= cap:
        return facts

    by_group, scored = defaultdict(list), {}
    for i, fact in enumerate(facts):
        key = chain.group_key(ids.entity_id("", fact.get("subject_key", "")), fact)
        by_group[key].append(i)
        scored[i] = relevance(question, fact)

    ranked = sorted(by_group, key=lambda k: -max(scored[i] for i in by_group[k]))
    keep = set()
    for key in ranked:
        if len(keep) >= cap:
            break
        keep.update(by_group[key])
    return [f for i, f in enumerate(facts) if i in keep]


# Reasons an appeal can act on. Only gate 5's `not_in_graph`, deliberately: it
# is the one abstention raised *after* retrieval succeeded, so it is the only
# one with facts in hand to re-render. Gates 1-3 fire before there is anything
# to show, and a second look at nothing is a wasted call.
#
# 27 of the 60 false abstentions on the oracle slice carry this reason -- the
# largest single bucket (`docs/eval/oracle-abstentions.csv`).
APPEALABLE = frozenset({"not_in_graph"})

# Gate 5's other two. They also fire after retrieval, but they are the model
# breaking the output contract rather than declining, so they are refused for a
# different reason and say so -- "fires before retrieval" would be a false
# explanation attached to a correct decision.
CONTRACT_FAILURES = frozenset({"uncited_answer", "fabricated_citation"})

# What the first call renders. Equal to the storage cap today, so the first call
# is unchanged; issue 23 raises the cap and leaves this alone, which is what
# opens the gap the appeal reads into.
FIRST_WIDTH = extract.MAX_SPAN

# The answer plus every fact id it cites has to fit. This was 1024 and it broke
# the moment slice 18's extraction prompt landed: facts per instance went 14.0 ->
# 34.9 (max 76 -> 145), so the model cites more ids, and the JSON ran out of
# budget **mid-list** -- `..."3a8a48670b26", "8c5da9e` -- killing the arm twice.
#
# It reads as a model failure and is ours, which is exactly what CLAUDE.md
# already records for the extraction call at 4096. 145 ids is roughly 1,000
# tokens of citations alone before any prose, so 1024 was never enough once the
# graph got denser. Raising the extractor's yield is therefore an *answering*
# change too, whether or not anyone touches this file.
ANSWER_MAX_TOKENS = 4096


class Appeal(BaseModel):
    """Whether an abstention gets a second look, and what it gets to see.

    Pure data. `plan_appeal` decides it from a reason string and a list of
    dicts, with no driver and no model, so an appeal is reproducible in a unit
    test exactly as a gate decision is.
    """

    appeal: bool
    # Why not, when `appeal` is False. An appeal that silently does not happen
    # is indistinguishable from one that happened and found nothing.
    declined: str = ""
    # Render width for the retry. `None` means the whole stored snippet.
    width: int | None = None
    # Show the facts the window narrowed away. The first call sees only the
    # window it parsed; if that window was wrong, the answer is sitting in the
    # facts it excluded, and no amount of re-reading the narrowed set finds it.
    drop_window: bool = False


def plan_appeal(reason: str, facts: list, windowed: bool = False) -> Appeal:
    """Decide whether an abstention is worth a second look. No driver, no model.

    Deliberately blunt in the same direction as the gates: it appeals on a
    reason code, not on a score. There is no threshold here to calibrate and no
    confidence to mis-trust -- the question is only "did this abstention happen
    somewhere a retry could see more", and that is answerable structurally.
    """
    if reason in CONTRACT_FAILURES:
        # Not "fires before retrieval" -- these fire after, with facts in hand.
        # They are excluded for the opposite reason: the model answered and
        # failed the output contract, so a retry is asking it to try harder at
        # citing, which is the confabulation gate 5 exists to stop. A fabricated
        # id must never get a second chance.
        return Appeal(appeal=False,
                      declined=f"{reason} is a contract failure, not a decline")
    if reason not in APPEALABLE:
        return Appeal(appeal=False, declined=f"{reason} fires before retrieval")
    if not facts:
        return Appeal(appeal=False, declined="no facts in hand")

    # The retry has to be a *different request* or it is not a retry.
    # `llm._cache_key` hashes the messages, so identical evidence returns the
    # identical refusal from disk -- forever, and while counting tokens. There
    # are exactly two ways this call can differ from the first:
    #
    #   - a window to drop, so the retry sees facts the first call never got;
    #   - a snippet longer than the first render, so the retry sees more of one.
    #
    # The second is impossible until `extract.MAX_SPAN` rises above
    # `FIRST_WIDTH` (issue 23). With neither, appealing would fire, change
    # nothing, and record `appealed=True, appeal_won=False` -- which reads as
    # "we looked again and the answer really is not there" when nothing was
    # looked at. Declining is the honest outcome and keeps that counter meaning
    # what it says.
    widens = any(len(fact.get("snippet", "")) > FIRST_WIDTH for fact in facts)
    if not windowed and not widens:
        return Appeal(appeal=False,
                      declined="nothing new to show: no window to drop and no "
                               "snippet longer than the first render")
    return Appeal(appeal=True, width=None, drop_window=windowed)


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

    verdict = gates.run(question, entities, aliases, facts_for, asked_at, find_paths,
                        gates.text_reader(all_facts))
    return verdict, all_facts() if verdict.passed else []


def _norm(cited) -> list:
    """Citation ids as the graph writes them: lowercase, stripped, deduplicated.

    The model copies a `fact_id` back by hand, so a trailing space or a capital
    letter is a transcription slip and not a fabrication. Nothing else is
    repaired -- an id that is still not in the retrieved set after this fails
    the membership check on its own merits.
    """
    seen = []
    for c in cited:
        c = str(c).strip().lower()
        if c and c not in seen:
            seen.append(c)
    return seen


def citation_gate(text: str, cited, facts: list) -> gates.GateResult:
    """The last gate, and the only one that runs after the model.

    It is a `GateResult` because it is structurally a gate: it fires on shape,
    it names what was missing, and it consults no model to decide.

    Slice 09 left this lenient -- an answer survived if *any* cited id was in
    the retrieved set and invented ids were quietly filtered out. Slice 10
    makes an invented id fatal. The reason is that the two failures are not the
    same kind of thing: a missing citation is a model that did not follow the
    output contract, while an invented one is a model that produced a
    plausible-looking identifier it never read. A valid id sitting beside a
    fabricated one does not redeem it -- it means the answer was assembled from
    both, and there is no way to tell which half the claim rests on. The cost is
    recall on answers that were probably right; the thesis is that an answer
    nobody can audit is not an answer.
    """
    if text.upper().startswith(ABSTAIN):
        return gates.GateResult(False, "not_in_graph")

    retrieved = {f["fact_id"] for f in facts}
    cited = _norm(cited)
    invented = [c for c in cited if c not in retrieved]
    if invented:
        return gates.GateResult(False, "fabricated_citation", ", ".join(invented))
    if not cited:
        return gates.GateResult(False, "uncited_answer")
    return gates.GateResult(True, resolved=tuple(cited))


def predecessors(facts: list) -> dict:
    """`fact_id` -> the `fact_id` it replaced, from the rows already in hand.

    Same grouping and same total order as `chain.derive`, which is what the
    graph's SUPERSEDES edges were built from -- so explain agrees with the
    edges without paying a round trip to read them. Grouping by `subject_key`
    rather than by node id is the only difference, and it is the same partition:
    one key per entity.
    """
    groups = defaultdict(list)
    for fact in facts:
        groups[chain.group_key(fact.get("subject_key", ""), fact)].append(fact)

    replaced = {}
    for group in groups.values():
        ordered = sorted(group, key=lambda f: (f.get("asserted_at", 0),
                                               f.get("turn_idx", 0), f.get("id", 0)))
        for older, newer in zip(ordered, ordered[1:]):
            replaced[newer["fact_id"]] = older["fact_id"]
    return replaced


def evidence_for(facts: list, cited: list) -> list:
    """Provenance rows for the cited facts, in the order the model cited them.

    `role` is the role of the turn the fact was extracted from. It is carried
    all the way here because the extractor files assistant suggestions as user
    facts (`ec81a493`: three `prefers` facts quoting the assistant's own
    advice), grounding cannot see that -- a copy of assistant text is grounded
    by definition -- and a citation pointing at one is a correctly-cited wrong
    answer. Surfacing it is not the same as rejecting it: how often it happens
    has never been measured, so the trace shows it and a human decides.
    """
    by_id = {f["fact_id"]: f for f in facts}
    replaced = predecessors(facts)
    rows = []
    for fid in cited:
        fact = by_id.get(fid)
        if fact is None:
            continue                      # citation_gate already refused these
        rows.append({
            "fact_id": fid,
            "subject": fact.get("subject_name", ""),
            "predicate": fact.get("predicate", ""),
            "value": fact.get("value_text", ""),
            "session_id": fact.get("session_id", ""),
            "turn_idx": fact.get("turn_idx"),
            # Facts written before slice 10 have no role property at all, and
            # HydraDB returns null rather than failing. "unknown" says so.
            "role": fact.get("role") or "unknown",
            "valid_from": fact.get("valid_from", 0),
            "valid_to": fact.get("valid_to", temporal.OPEN),
            "snippet": fact.get("snippet", ""),
            "supersedes": replaced.get(fid, ""),
        })
    return rows


def answer_question(driver, instance_id: str, question: str, asked_at: int = None,
                    bookmarks=None, consistency: str = "causal", model: str = None,
                    appeal: bool = False) -> Result:
    window = temporal.parse_window(question, asked_at)
    scope = window.detail if window else ""
    before = client.round_trips()

    def done(**kw) -> Result:
        return Result(window=scope, round_trips=client.round_trips() - before, **kw)

    gate, facts = check_gates(driver, instance_id, question, asked_at,
                              bookmarks=bookmarks, consistency=consistency)
    trace = list(gate.trace)
    if not gate.passed:
        # The whole point of a structural gate: no model was asked, so there was
        # nothing for it to confabulate with.
        return done(answer=ABSTAIN, abstained=True, reason=gate.reason,
                    gate_detail=gate.detail, gate_trace=trace)

    if not facts:
        return done(answer=ABSTAIN, abstained=True, reason="empty_graph",
                    gate_trace=trace)

    retrieved = facts                      # before narrowing, for the appeal
    if window:
        # Narrowing, not filtering: the model never sees facts from outside the
        # window it was asked about, so "where did I work in 2019" cannot be
        # answered with the 2023 employer. Gate 3 has already established the
        # window is non-empty for the gated entity; `or facts` covers the case
        # where the retrieved set is wider than the entity the gate cleared.
        facts = temporal.in_window(facts, window) or facts

    # Cap what reaches the model. Above ~60 facts the model starts declining on
    # questions the graph answers -- see `NARROW_CAP`. The appeal below still
    # gets `retrieved`, so a question narrowed here can still be re-asked wider.
    shown_all = facts
    facts = narrow(question, facts)
    if len(facts) < len(shown_all):
        trace.append(f"narrow: {len(shown_all)} facts -> {len(facts)}")

    asked = "" if asked_at is None else dt.datetime.fromtimestamp(
        asked_at, dt.timezone.utc
    ).strftime("%Y-%m-%d")

    def ask(shown: list, width):
        """One model call over `shown`, then the citation check against it.

        The check is scoped to what was actually rendered, so an appeal that
        shows more facts is audited against the wider set and an id it cites
        from there resolves. Citing outside what you were shown is still fatal.
        """
        body = "\n".join(fact_line(f, width) for f in shown)
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content":
                f"Question asked on {asked}: {question}\n\n"
                f"Facts (fact_id | valid from | subject | predicate | value | "
                f"evidence):\n{body}"},
        ]
        kw = dict(model=model or llm.ANSWER_MODEL, reasoning=False,
                  max_tokens=ANSWER_MAX_TOKENS,
                  response_format={"type": "json_object"})
        try:
            reply = llm.complete_json(messages, Answer, **kw)
        except ValidationError:
            # Greedy decoding collapses on roughly one call in twenty, and the
            # disk cache is keyed on the request -- so retrying the *identical*
            # call returns the identical garbage forever and the question is
            # permanently lost. Temperature is the only lever that reaches it.
            #
            # `extract.extract_raw` and `eval.ask` have had this since slice 06.
            # This caller did not, and CLAUDE.md had already written down that
            # every new caller of `complete_json` needs it.
            reply = llm.complete_json(
                messages, Answer, temperature=extract.RETRY_TEMPERATURE, **kw)
        text = reply.answer.strip()
        return text, citation_gate(text, reply.cited_fact_ids, shown)

    # The citation check is the thesis: an id the model invented, or no id at
    # all, means the answer is not grounded in what was retrieved. It runs after
    # the model and cannot be talked past, because it does not ask the model
    # anything.
    text, verdict = ask(facts, FIRST_WIDTH)
    trace.append(f"5 citation: {'pass' if verdict.passed else verdict.detail}")

    if not verdict.passed:
        plan = (plan_appeal(verdict.reason, facts, windowed=bool(window))
                if appeal else Appeal(appeal=False, declined="appeal disabled"))
        if not plan.appeal:
            trace.append(f"appeal: skipped ({plan.declined})")
            return done(answer=ABSTAIN, abstained=True, reason=verdict.reason,
                        gate_detail=verdict.missing and verdict.detail,
                        gate_trace=trace, fact_count=len(facts))

        # The second look. Costs one model call and *zero* Bolt round trips --
        # every fact it shows was already read for the first call.
        shown = retrieved if plan.drop_window else facts
        seen = f"{len(shown)} facts" + (", window dropped" if plan.drop_window else "")
        try:
            retry, second = ask(shown, plan.width)
        except Exception as exc:                        # noqa: BLE001
            # The appeal must not be able to make a question worse, and an
            # uncaught exception is the worst outcome there is: no Result at
            # all, where `appeal=False` would have returned a clean abstention.
            # It also propagates -- `eval.run_hydramem` has no guard, and a
            # collapsed decode killing a 150-question arm is a failure this
            # project has already had once.
            #
            # Deliberately broad. `llm.complete_json` can raise a RuntimeError
            # on exhausted retries, a provider error on a non-retryable 4xx, or
            # a pydantic ValidationError on the ~5% greedy-decode collapse, and
            # the correct response to every one of them is identical: the
            # appeal did not happen, so report what the first call decided.
            #
            # Counted as `appealed`, because it fired. If an errored appeal
            # reported nothing, a run where every retry crashed would show
            # `appeals = 0` and read as "the plan always declines" -- the exact
            # ambiguity the two counters exist to separate.
            trace.append(f"appeal: errored ({type(exc).__name__}), abstention kept")
            return done(answer=ABSTAIN, abstained=True, reason=verdict.reason,
                        gate_detail=verdict.missing and verdict.detail,
                        gate_trace=trace, fact_count=len(facts), appealed=True)
        if second.passed:
            trace.append(f"appeal: won ({seen})")
            cited = list(second.resolved)
            return done(answer=retry, abstained=False, cited_fact_ids=cited,
                        evidence=evidence_for(shown, cited), gate_trace=trace,
                        fact_count=len(shown), appealed=True, appeal_won=True)

        # Lost. The *original* abstention is returned, not the retry's -- an
        # appeal may only ever overturn toward answering, so a second look that
        # fails differently must not change what the question reports. That is
        # the property that makes enabling this incapable of making any question
        # worse.
        trace.append(f"appeal: lost ({second.detail})")
        return done(answer=ABSTAIN, abstained=True, reason=verdict.reason,
                    gate_detail=verdict.missing and verdict.detail,
                    gate_trace=trace, fact_count=len(facts), appealed=True)

    cited = list(verdict.resolved)
    return done(answer=text, abstained=False, cited_fact_ids=cited,
                evidence=evidence_for(facts, cited), gate_trace=trace,
                fact_count=len(facts))


def explain(result: Result) -> str:
    """The whole trace of one question, answered or abstained, as text.

    This is the primary debugging tool for every slice after this one and the
    thing that lets a judge audit an answer without a database client: which
    gates ran, which fired and on what, what the answer cost, and for every
    cited fact where it came from -- session, turn, who said it, when it was
    true, and what it replaced.

    Pure: it reads a `Result` and touches neither driver nor model, so the
    explanation of an answer cannot disagree with the answer.
    """
    lines = [f"Q cost {result.round_trips} round trips over {result.fact_count} facts"]
    if result.window:
        lines.append(f"window: {result.window}")
    lines.extend(f"  {step}" for step in result.gate_trace)

    if result.abstained:
        lines.append(f"ABSTAIN {result.gate_detail or result.reason}")
        return "\n".join(lines)

    lines.append(f"answer: {result.answer}")
    for row in result.evidence:
        until = "" if row["valid_to"] == temporal.OPEN else \
            f' until {_day(row["valid_to"])}'
        # An assistant-sourced fact is a correctly-cited wrong answer waiting to
        # happen, so it is marked rather than merely reported.
        flag = "  <- ASSISTANT-SOURCED" if row["role"] == "assistant" else ""
        lines.append(
            f'  {row["fact_id"]}  {row["subject"]} {row["predicate"]} = {row["value"]}'
        )
        lines.append(
            f'      session {row["session_id"]} turn {row["turn_idx"]}'
            f' ({row["role"]}){flag}'
        )
        lines.append(f'      valid from {_day(row["valid_from"])}{until}')
        if row["supersedes"]:
            lines.append(f'      replaces {row["supersedes"]}')
        if row["snippet"]:
            lines.append(f'      "{row["snippet"]}"')
    return "\n".join(lines)
