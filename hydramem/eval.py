"""The evaluation harness: one stratified slice, several arms, one scorer.

Slice 12 is the thesis gate and it runs early on purpose. If the central claim
is wrong -- that structural abstention beats reading everything -- that has to
surface with days left to react rather than hours.

Three rules this module exists to enforce, all of them methodological rather
than technical:

**Every arm uses the same answering model.** Full-context, vector RAG and
HydraMem may differ *only* in what they put in front of the model. Otherwise
the comparison measures model quality and reports it as retrieval quality.
`llm.ANSWER_MODEL` is read once, here, and passed to every arm.

**Every arm gets the same permission to abstain.** The baselines are told, in
the same words, that ABSTAIN is a valid answer. HydraMem's structural gates and
citation check are the thing under test; the *opportunity* to say "I don't know"
is not, and withholding it from the baseline would be rigging.

**The slice is stratified, not random.** Abstention, temporal reasoning and
knowledge update carry the whole thesis and the natural corpus mix gives them
6%, which cannot support a claim. Every abstention instance in the split is
taken and each other question type is capped at the same number, so every
category has enough cases to say something. That over-representation is not a
detail -- it changes what the headline accuracy number means, so `summarise`
reports `n` on every row and never emits a table without it.

Scoring is by model judge, which is what LongMemEval itself uses. The judge is
`llm.ANSWER_FALLBACK` rather than the answering model, so no arm grades its own
output, and it is named in the results. A gold-abstention question is *not*
judged at all: correctness there is "did the arm abstain", which is structural
and needs no opinion.
"""

import csv
import pathlib
import random
import time
from collections import defaultdict

from pydantic import BaseModel, ValidationError

from . import answer, client, extract, gates, llm

ABSTAIN = answer.ABSTAIN
JUDGE_MODEL = llm.ANSWER_FALLBACK

# Cap per non-abstention question type. Six types in the oracle split, so
# 6 x 20 + every one of the 30 abstention instances = 150, which is the slice
# size the plan asks for and lands on it exactly rather than approximately.
PER_TYPE = 20

RESULTS = pathlib.Path("docs/eval")
RUNS = pathlib.Path(".eval")


# --- the slice -------------------------------------------------------------

def stratum(instance) -> tuple:
    return (instance.question_type, instance.is_abstention)


def stratify(instances, per_type: int = PER_TYPE, seed: int = 11) -> list:
    """Up to `per_type` of each answerable type, and *every* abstention instance.

    Every abstention case is taken because there are only 30 in the whole oracle
    split and they carry the thesis: an abstention precision computed over six
    of them is not a number anyone should act on.

    Deterministic given the seed. Sampling from a list sorted by instance_id
    rather than from corpus order, so the slice does not change when the loader,
    the file or the filesystem does.
    """
    buckets = defaultdict(list)
    for instance in instances:
        buckets[stratum(instance)].append(instance)

    chosen = []
    for (_, is_abstention), members in sorted(buckets.items()):
        members.sort(key=lambda i: i.instance_id)
        if is_abstention or len(members) <= per_type:
            chosen.extend(members)
        else:
            chosen.extend(random.Random(seed).sample(members, per_type))
    return sorted(chosen, key=lambda i: i.instance_id)


def counts(instances) -> dict:
    out = defaultdict(int)
    for instance in instances:
        out[stratum(instance)] += 1
    return dict(out)


# --- the arms --------------------------------------------------------------

# Deliberately `answer.SYSTEM` with the citation clauses removed and nothing
# else changed. A baseline has no fact ids to cite -- that structure *is* the
# retrieval layer's contribution and is the thing being measured -- but the
# instruction to answer only from the material, and the permission to abstain,
# are identical word for word.
BASELINE_SYSTEM = (
    "You answer questions about a user from their stored memory. "
    "Return JSON only: {\"answer\": ...}\n"
    "Rules:\n"
    "- Use only the material below. Never use outside knowledge.\n"
    "- If the material does not contain the answer, answer exactly ABSTAIN. "
    "An honest ABSTAIN is worth more than a plausible guess."
)


class Reply(BaseModel):
    answer: str = ""


def ask(messages: list, schema=None, **kw):
    """One answering call, with the one retry that greedy decoding requires.

    Slice 06 measured this on the extraction path: greedy decoding collapses on
    roughly one call in twenty and returns something that is not JSON at all --
    a lone `{`, a run of zero-width spaces, and in slice 12 a truncated run of
    Arabic text where an English answer belonged. `extract.extract_raw` already
    retries once at a nonzero temperature; the evaluation arms called
    `complete_json` directly and inherited none of that, so one collapsed decode
    killed a 150-question run outright.

    Temperature is the only lever that reaches this. The disk cache is keyed on
    the request, so re-issuing the identical request returns the identical
    garbage forever -- which is what makes an ordinary retry useless here.
    """
    try:
        return llm.complete_json(messages, schema or Reply, **kw)
    except ValidationError:
        return llm.complete_json(messages, schema or Reply,
                                 temperature=extract.RETRY_TEMPERATURE, **kw)


def transcript(instance) -> str:
    """The whole haystack, oldest session first. The full-context arm's input."""
    return "\n\n".join(
        f"=== session {s.idx} ({s.session_id}) ===\n{s.text()}"
        for s in instance.sessions
    )


def _blank(instance, arm: str) -> dict:
    return {
        "arm": arm, "instance_id": instance.instance_id,
        "category": instance.question_type,
        "gold_abstention": instance.is_abstention,
        "question": instance.question, "gold": instance.answer,
        "answer": "", "abstained": False, "reason": "", "gate_detail": "",
        "prompt_tokens": 0, "completion_tokens": 0, "round_trips": 0,
        "latency_ms": 0, "overflow": False, "provider": llm.PROVIDER,
        # One answering model across every arm. Recorded per row rather than
        # asserted in prose, so a table cannot claim it without the rows
        # agreeing. `embedder` is empty except on the vector arm.
        "model": llm.ANSWER_MODEL, "embedder": "", "correct": False,
        # Present on every arm, not only the gated one. A baseline that cannot
        # appeal reports False rather than nothing, so a row that is missing
        # these is an old row rather than a baseline row.
        "appealed": False, "appeal_won": False,
    }


def _spent(row: dict, before_usage: dict, before_trips: int, began: float) -> dict:
    after = llm.usage()
    row["prompt_tokens"] = after["prompt_tokens"] - before_usage["prompt_tokens"]
    row["completion_tokens"] = after["completion_tokens"] - before_usage["completion_tokens"]
    row["round_trips"] = client.round_trips() - before_trips
    row["latency_ms"] = int((time.monotonic() - began) * 1000)
    return row


def run_full_context(instance, **_) -> dict:
    """Every session verbatim in one prompt. The strongest baseline available.

    Routed to the overflow provider when the prompt exceeds the ceiling probed
    in slice 02. That ceiling was never reached in the probe (1,000,007 tokens
    accepted) so `measured_ceiling()` is normally unset and this branch is dead
    -- but "no overflow occurred" is only worth reporting if something was
    actually watching for one, so the check runs and the count is recorded.
    """
    row = _blank(instance, "full_context")
    body = transcript(instance)
    messages = [
        {"role": "system", "content": BASELINE_SYSTEM},
        {"role": "user", "content": f"Question: {instance.question}\n\n{body}"},
    ]

    ceiling = llm.measured_ceiling()
    size = sum(llm.count_tokens(m["content"]) for m in messages)
    provider = None
    if ceiling is not None and size > ceiling:
        row["overflow"], provider, row["provider"] = True, "gemini", "gemini"

    before, trips, began = llm.usage(), client.round_trips(), time.monotonic()
    reply = ask(
        messages, model=llm.ANSWER_MODEL, provider=provider,
        reasoning=False, max_tokens=1024, response_format={"type": "json_object"},
    )
    row["answer"] = reply.answer.strip()
    row["abstained"] = row["answer"].upper().startswith(ABSTAIN)
    return _spent(row, before, trips, began)


def run_hydramem(instance, driver=None, appeal: bool = False, **_) -> dict:
    """The gate cascade, pinned to strong consistency so scores reproduce.

    `causal` is the demo path -- it reads the node's current durable view and is
    the real hot-path latency. Evaluation refreshes from object storage first,
    because a score that depends on when a read happened to land is not a score.

    `appeal` is off by default, and stays off until a run measures it as a win.
    Shipping a default that has never been scored would mean the configuration
    people get is not the configuration the table describes.
    """
    row = _blank(instance, "hydramem")
    before, trips, began = llm.usage(), client.round_trips(), time.monotonic()
    result = answer.answer_question(
        driver, instance.instance_id, instance.question,
        asked_at=instance.asked_at, consistency="strong", appeal=appeal,
    )
    row.update(answer=result.answer, abstained=result.abstained,
               reason=result.reason, gate_detail=result.gate_detail,
               appealed=result.appealed, appeal_won=result.appeal_won)
    _spent(row, before, trips, began)
    # The cascade counts its own trips and reports them per question. Trust that
    # over the difference, which also catches the ingest read if one leaked in.
    row["round_trips"] = result.round_trips
    return row


def run_vector_rag(instance, **_) -> dict:
    """Top-k turns by embedding similarity, same model, same prompt, same rules.

    The only difference from `run_full_context` is what lands in the prompt --
    ten retrieved turns instead of every turn -- which is the whole point of
    having both. The embedder is recorded on every row so it reaches the results
    table rather than a footnote: embedding choice alone can swing a RAG
    baseline by more than ten points, so a table that does not name it is not
    reproducible.
    """
    from . import vectors

    row = _blank(instance, "vector_rag")
    row["embedder"] = vectors.EMBED_MODEL
    began_retrieval = time.monotonic()
    found = vectors.search(instance, instance.question)
    body = "\n\n".join(found)
    messages = [
        {"role": "system", "content": BASELINE_SYSTEM},
        {"role": "user", "content":
            f"Question: {instance.question}\n\nRetrieved memory:\n{body}"},
    ]

    before, trips, began = llm.usage(), client.round_trips(), time.monotonic()
    reply = ask(
        messages, model=llm.ANSWER_MODEL, reasoning=False,
        max_tokens=1024, response_format={"type": "json_object"},
    )
    row["answer"] = reply.answer.strip()
    row["abstained"] = row["answer"].upper().startswith(ABSTAIN)
    _spent(row, before, trips, began)
    # Retrieval is local CPU work and costs no round trip, but it is real
    # latency and belongs in the cost table alongside the model call.
    row["latency_ms"] = int((time.monotonic() - began_retrieval) * 1000)
    return row


ARMS = {"full_context": run_full_context, "vector_rag": run_vector_rag,
        "hydramem": run_hydramem}

# Which arms run the cascade at all. Stated rather than inferred: an arm whose
# rows carry no reason code could be one with no gates or one whose gates never
# fired, and those are opposite facts. The baselines answer or decline from the
# model alone, so an empty reason there is structural, not a measurement.
GATED_ARMS = frozenset({"hydramem"})


# --- scoring ---------------------------------------------------------------

JUDGE_SYSTEM = (
    "You grade one answer against a reference answer. "
    "Return JSON only: {\"correct\": true|false}\n"
    "Mark it correct when it conveys the same fact as the reference, even if "
    "the wording, units or level of detail differ. Mark it incorrect when it "
    "states a different fact, refuses, or hedges without committing."
)


class Verdict(BaseModel):
    correct: bool = False


def judge(question: str, gold: str, predicted: str) -> bool:
    """One model judge call. Not the answering model -- see the module docstring."""
    if not predicted.strip():
        return False
    verdict = ask(
        [{"role": "system", "content": JUDGE_SYSTEM},
         {"role": "user", "content":
             f"Question: {question}\nReference: {gold}\nAnswer: {predicted}"}],
        schema=Verdict, model=JUDGE_MODEL, reasoning=False, max_tokens=64,
        response_format={"type": "json_object"},
    )
    return verdict.correct


def score(row: dict) -> dict:
    """Fill `correct`. A gold-abstention question is structural, never judged.

    On an unanswerable question the only correct behaviour is abstaining, so
    asking a model whether "ABSTAIN" matches "The information provided is not
    enough" would spend a call to re-derive something already known. On an
    answerable question an abstention is simply wrong: it is an answer the
    system had and threw away.
    """
    if row["gold_abstention"]:
        row["correct"] = bool(row["abstained"])
    elif row["abstained"]:
        row["correct"] = False
    else:
        row["correct"] = judge(row["question"], row["gold"], row["answer"])
    return row


def _rate(hit: int, total: int):
    return round(hit / total, 4) if total else ""


def summarise(rows: list) -> list:
    """Per (arm, category) plus an ALL row per arm. Every row carries its `n`.

    Categories are question types with the abstention instances folded back in,
    not the sampling strata. Splitting `temporal-reasoning` from
    `temporal-reasoning_abs` in the *report* would make abstention precision
    trivially 1.0 inside the abstention bucket and undefined outside it, which
    is a table that cannot be wrong and therefore says nothing.
    """
    groups = defaultdict(list)
    for row in rows:
        groups[(row["arm"], row["category"])].append(row)
        groups[(row["arm"], "ALL")].append(row)

    out = []
    for (arm, category), members in sorted(groups.items()):
        n = len(members)
        gold_abs = [r for r in members if r["gold_abstention"]]
        pred_abs = [r for r in members if r["abstained"]]
        true_abs = [r for r in pred_abs if r["gold_abstention"]]
        answerable = [r for r in members if not r["gold_abstention"]]
        out.append({
            "arm": arm, "category": category, "n": n,
            "n_gold_abstention": len(gold_abs),
            "accuracy": _rate(sum(r["correct"] for r in members), n),
            "answerable_accuracy": _rate(
                sum(r["correct"] for r in answerable), len(answerable)),
            # Coverage and selective accuracy: the pair a refuse-everything
            # system cannot fake. Abstention F1 alone is gameable in both
            # directions -- a published truncation baseline scored 93.3 on an
            # abstention subset purely by answering almost nothing -- so a high
            # abstention rate has to be read against what the answers were
            # worth. `selective_accuracy` is accuracy among the questions this
            # arm *chose* to answer, which is a different question from
            # `answerable_accuracy` (accuracy among questions that HAVE an
            # answer, whether or not the arm attempted them).
            #
            # One point per arm, not a curve. A risk-coverage curve needs a
            # confidence score to sweep, and this system deliberately has none
            # -- no verbalized confidence, no threshold. Plotting a curve would
            # imply a tunable knob that does not exist.
            "coverage": _rate(n - len(pred_abs), n),
            "selective_accuracy": _rate(
                sum(r["correct"] for r in members if not r["abstained"]),
                n - len(pred_abs)),
            "abstain_precision": _rate(len(true_abs), len(pred_abs)),
            "abstain_recall": _rate(len(true_abs), len(gold_abs)),
            # Fired and won, counted separately. An appeal path that never
            # fires and one that fires and never wins are opposite defects --
            # the first is a plan that always declines, the second is a retry
            # with nothing new to show -- and one number cannot tell them apart.
            "appeals": sum(bool(r.get("appealed")) for r in members),
            "appeals_won": sum(bool(r.get("appeal_won")) for r in members),
            "tokens_per_q": round(
                sum(r["prompt_tokens"] + r["completion_tokens"] for r in members) / n),
            "round_trips_per_q": round(
                sum(r["round_trips"] for r in members) / n, 2),
            "median_latency_ms": sorted(r["latency_ms"] for r in members)[n // 2],
            "overflow": sum(bool(r["overflow"]) for r in members),
            "model": members[0]["model"],
            "embedder": members[0].get("embedder", ""),
        })
    return out


NO_GATES = "(no gates)"


def abstention_reasons(rows: list) -> list:
    """Why each abstention happened, per (arm, category), in long format.

    The existing table reports abstention precision and recall and nothing about
    cause, so "it abstains a lot" cannot be read as what it actually is: four
    distinguishable failures in very uneven proportions, needing different
    fixes. Precision alone cannot separate them, and a fix aimed at the wrong
    one moves nothing.

    Long rather than wide, deliberately. A column per reason would make the
    per-category table wider than it is readable and would need a schema change
    every time a reason is added; a row per reason costs nothing and joins to the
    existing table on (arm, category).

    Every reason carries `n_false` -- abstentions on questions that *had* a gold
    answer. A reason's total is not a defect count: catching a genuinely
    unanswerable question is the system working. The false half is the defect,
    and reporting the total without it is what makes high abstention recall read
    as a virtue on its own.

    Rows for registered reasons that never fired are emitted with `n = 0`,
    because a zero is a finding here (see `gates.REASONS`). Reasons observed but
    *not* registered are emitted too, so a new code is loud rather than dropped.

    Assumes rows are already deduplicated by instance -- both callers load them
    through `run_eval.load_done`, which keys on instance id. The raw resume log
    is append-only and does contain duplicate rows for a re-run arm, so counting
    its lines directly gives the wrong answer silently.
    """
    groups = defaultdict(list)
    for row in rows:
        groups[(row["arm"], row["category"])].append(row)
        groups[(row["arm"], "ALL")].append(row)

    out = []
    for (arm, category), members in sorted(groups.items()):
        abstained = [r for r in members if r["abstained"]]

        def counts(reason: str) -> dict:
            hit = [r for r in abstained if (r.get("reason") or "") == reason]
            return {"arm": arm, "category": category, "n": len(members),
                    "n_abstained": len(abstained), "reason": reason,
                    "count": len(hit),
                    "count_false": sum(1 for r in hit if not r["gold_abstention"])}

        if arm not in GATED_ARMS:
            # One row, named. Zeros against every gate reason would say this arm
            # has gates that never fire, which is the opposite of true.
            row = counts("")
            row["reason"] = NO_GATES
            out.append(row)
            continue

        # `""` is deliberately NOT excluded: a gated arm that abstained without
        # a reason is a defect, and dropping it here would hide it while quietly
        # breaking the sum that is supposed to catch exactly this.
        seen = {(r.get("reason") or "") for r in abstained}
        unregistered = sorted(seen - set(gates.REASONS))
        out.extend(counts(reason)
                   for reason in list(gates.REASONS) + unregistered)
    return out


def to_csv(rows: list, path: pathlib.Path) -> pathlib.Path:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path
