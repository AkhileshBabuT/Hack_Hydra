"""Slice 14: read and write cost that would survive real usage.

    python scripts/cost_table.py --oracle

Joins three sources into one long-format CSV:

  1. **Per-question harness rows** from `.eval/<split>/<arm>.jsonl` -- tokens,
     latency and Bolt round trips, all counted where they happened rather than
     tallied afterwards (`llm.usage()`, `client.round_trips()`).
  2. **The ingest ledger** from the same directory -- what it cost to put an
     instance in the graph, per fact and per session.
  3. **HydraDB's admin metrics**, parsed with `client.histograms`, which resolves
     each histogram's unit from its name rather than assuming one.

**The unit warning is not a formality.** The endpoint serves
`graph_client_operation_read_duration_seconds` and
`graph_query_rows_duration_microseconds` side by side, on the *same* bucket
ladder scaled by 1e6 -- `le="0.0001"` and `le="100"` are the same bound. Read
either as the other's unit and the table is wrong by a factor of a million while
still looking like a table. The units are verified from the metric definitions
in `crates/telemetry/src/meter.rs`; see the note on `client.histograms`.

The headline is round trips per question. HydraMem costs at most four whatever
the question names, because gate 4 resolves every anchor pair in one batched
`algo.MSpaths` call: n anchors are n(n-1)/2 pairs, and a naive graph memory
issues a traversal per pair. That structural claim is pinned in
`test_paths.py`; what this table adds is the measured distribution.

Consistency: evaluation pins **strong** so scores reproduce, while the demo path
(`scripts/ingest_one.py`) runs **causal**, which is the real hot path. Both are
stated in the output because a latency number without its consistency mode is
not comparable to anything.
"""

import argparse
import csv
import json
import pathlib
import statistics
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hydramem import client, eval as ev, llm  # noqa: E402

# Where each arm's latency is spent. Only HydraMem touches Bolt at all, so
# "round trips per question" is a comparison of shapes, not of two like numbers.
CONSISTENCY = {"hydramem": "strong (evaluation)", "full_context": "n/a (no graph)",
               "vector_rag": "n/a (no graph)"}


def load(path: pathlib.Path) -> list:
    """Scored rows, deduplicated by instance, last-wins.

    The resume log is append-only and a re-run appends a *second* row for every
    question it re-scores -- `full_context.jsonl` holds 162 lines for 150
    questions.

    **This dedupe is belt-and-braces, not a bug fix.** `query_cost` has always
    deduplicated by `(arm, instance_id)` and the published cost figures were
    never affected. It was added here on 2026-08-17 after a wrong diagnosis:
    the loader was correctly identified as not deduplicating, and the *impact*
    was then asserted without checking that the consumer already did. An
    ad-hoc script averaging the raw 162 rows produced a number the system never
    produced, and that number was reported as a regression. It was not one.

    Kept because `ingest_cost` consumes this loader too and does not dedupe on
    its own, and because one rule applied at the boundary is easier to hold than
    one rule applied at each consumer. Not kept because it fixed anything.
    """
    if not path.exists():
        return []
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row.get("instance_id", len(rows))] = row
    return list(rows.values())


def percentile(values: list, fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def row(section, arm, metric, value, unit, note=""):
    return {"section": section, "arm": arm, "metric": metric,
            "value": value, "unit": unit, "note": note}


def query_cost(rows: list) -> list:
    """Per-question cost per arm. Tail is p95: a median hides the thing that hurts."""
    out = []
    by_arm = {}
    # One row per (arm, question). Two processes resuming the same arm append
    # the same question twice -- `load_done` absorbs that on the scoring side,
    # and without the same dedupe here the table reports 162 questions for a
    # 150-question slice and divides every per-question cost by the wrong n.
    for record in {(r["arm"], r["instance_id"]): r for r in rows}.values():
        by_arm.setdefault(record["arm"], []).append(record)

    for arm, members in sorted(by_arm.items()):
        n = len(members)
        latencies = [r["latency_ms"] for r in members]
        prompt = sum(r["prompt_tokens"] for r in members)
        completion = sum(r["completion_tokens"] for r in members)
        trips = [r["round_trips"] for r in members]
        out += [
            row("query", arm, "questions", n, "count"),
            row("query", arm, "prompt_tokens_per_question", round(prompt / n), "tokens"),
            row("query", arm, "completion_tokens_per_question",
                round(completion / n), "tokens"),
            row("query", arm, "tokens_per_question",
                round((prompt + completion) / n), "tokens"),
            row("query", arm, "median_latency", round(statistics.median(latencies)), "ms",
                CONSISTENCY.get(arm, "")),
            row("query", arm, "p95_latency", round(percentile(latencies, 0.95)), "ms",
                CONSISTENCY.get(arm, "")),
            row("query", arm, "bolt_round_trips_per_question",
                round(sum(trips) / n, 2), "round trips",
                "at most four by construction; one batched MSpaths resolves every pair"),
        ]
        # The distribution *is* the cost story: 2 means the question was lost at
        # gate 1, 4 means gate 4 issued its call. An average alone hides that.
        for cost, count in sorted(Counter(trips).items()):
            out.append(row("query", arm, f"questions_costing_{cost}_round_trips",
                           count, "count"))
    return out


def ingest_cost(rows: list) -> list:
    """Write cost per unit. Zero rows means every instance was already in the graph.

    Deduplicated by instance, because "what it cost to ingest X" has one answer.
    Two arms' processes racing on the same instance, or a re-ingest after a wipe,
    would otherwise inflate every per-fact figure by the number of attempts --
    and the resulting table looks entirely reasonable.
    """
    rows = list({r["instance_id"]: r for r in rows}.values())
    if not rows:
        return [row("ingest", "hydramem", "instances_ingested", 0, "count",
                    "graph already populated; rerun against a wiped node to measure")]

    facts = sum(r["facts"] for r in rows) or 1
    sessions = sum(r["sessions"] for r in rows) or 1
    tokens = sum(r["prompt_tokens"] + r["completion_tokens"] for r in rows)
    trips = sum(r["round_trips"] for r in rows)
    millis = sum(r["latency_ms"] for r in rows)
    return [
        row("ingest", "hydramem", "instances_ingested", len(rows), "count"),
        row("ingest", "hydramem", "sessions_ingested", sessions, "count"),
        row("ingest", "hydramem", "facts_written", facts, "count"),
        row("ingest", "hydramem", "facts_per_session", round(facts / sessions, 2), "facts"),
        row("ingest", "hydramem", "extraction_tokens_per_fact",
            round(tokens / facts), "tokens",
            "cache hits counted: the tokens were spent once, whoever spent them"),
        row("ingest", "hydramem", "bolt_round_trips_per_fact",
            round(trips / facts, 3), "round trips",
            "batched UNWIND writes, so this falls as an instance grows"),
        row("ingest", "hydramem", "latency_per_fact", round(millis / facts, 1), "ms"),
        row("ingest", "hydramem", "parse_failures",
            sum(r["parse_failures"] for r in rows), "count"),
    ]


def server_cost() -> list:
    """HydraDB's own view, units resolved from the metric names. See the docstring."""
    out = []
    try:
        found = client.histograms()
    except Exception as exc:                       # noqa: BLE001 - node may be down
        return [row("server", "hydradb", "metrics_scrape", "unavailable", "", str(exc))]

    for metric, stats in sorted(found.items()):
        out.append(row("server", "hydradb", metric, stats["mean_ms"], "ms",
                       f"mean over {int(stats['count'])} samples; "
                       f"exported in {stats['unit']}, verified from the metric "
                       f"definition and converted here"))
    scalars = client.metrics()
    for name in ("graph_queries_total", "graph_mutations_total", "graph_vertices_total"):
        if name in scalars:
            out.append(row("server", "hydradb", name, scalars[name], "count"))
    return out


# Context only, and labelled as such wherever it appears. It was measured with a
# different answering model, a different embedder and a different judge, so it is
# not comparable to anything in this table and the arm was never reproduced here.
# The rule this enforces: cite published figures, never claim to beat them.
MEM0_NOTE = """\
## What is *not* in this table

**mem0 was not reproduced in-harness.** Its published LongMemEval figure is
cited in the literature and can be read alongside these numbers, but it was
measured with a different answering model, a different embedder and a different
judge. None of those are the ones used here, so the two are not comparable and
no claim anywhere in this repository asserts beating it. The same applies to
every other published LongMemEval score: cite them, never claim them.

Only the arms above were measured under identical conditions, and identical
conditions is the only thing that makes a comparison mean anything.
"""


def _md_table(rows: list, columns: list) -> str:
    head = "| " + " | ".join(columns) + " |"
    rule = "|" + "|".join("---" for _ in columns) + "|"
    body = ["| " + " | ".join(str(r.get(c, "")) for c in columns) + " |" for r in rows]
    return "\n".join([head, rule, *body])


def write_markdown(split: str, cost: list) -> pathlib.Path:
    """Every number in the write-up, regenerated from the CSVs beside it.

    Generated, never hand-edited: a results document that can drift from the
    tables it summarises is worse than no results document. Slice 16's README
    includes this rather than restating it.
    """
    per_category = list(csv.DictReader(
        (ev.RESULTS / f"{split}-per-category.csv").open(encoding="utf-8")))
    reasons = list(csv.DictReader(
        (ev.RESULTS / f"{split}-abstentions.csv").open(encoding="utf-8")))
    arms = sorted({r["arm"] for r in per_category})
    embedders = sorted({r["embedder"] for r in per_category if r["embedder"]})

    parts = [
        f"# Results — `{split}` split",
        "",
        "Generated by `make eval`. Do not hand-edit.",
        "",
        f"- **Answering model, every arm:** `{llm.ANSWER_MODEL}`",
        f"- **Judge:** `{ev.JUDGE_MODEL}` — not the answering model, so no arm "
        "grades its own output.",
        f"- **Embedding model (vector RAG arm):** "
        f"`{embedders[0] if embedders else 'not run'}`",
        f"- **Arms:** {', '.join(f'`{a}`' for a in arms)}",
        "- **Consistency:** evaluation pins `strong` so scores reproduce; the "
        "demo path runs `causal`, which is the real hot path.",
        "",
        "The slice is **stratified, not random**: every abstention instance in "
        "the split is taken and each answerable question type is capped, so "
        "abstention is deliberately over-represented against its natural ~6%. "
        "Read `n` on every row before reading any rate.",
        "",
        "## Accuracy and abstention, per category",
        "",
        "`answerable_accuracy` is accuracy over the questions that have a gold "
        "answer. Read it beside `accuracy`: an arm that abstains more looks "
        "better on one and worse on the other, and only the pair says whether "
        "it is trading coverage for reliability or simply losing both.",
        "",
        "`appeals` / `appeals_won` count second looks at an abstention. They are "
        "separate columns because an appeal path that never fires and one that "
        "fires and never wins are opposite defects, and a single number cannot "
        "tell them apart.",
        "",
        "`coverage` is the fraction of questions the arm chose to answer and "
        "`selective_accuracy` is how often it was right when it did. Read them "
        "as a pair: abstention precision alone is gameable in both directions — "
        "a published truncation baseline scored 93.3 on an abstention subset by "
        "answering almost nothing — and an arm that abstains on everything "
        "scores no coverage rather than perfect anything.",
        "",
        _md_table(per_category,
                  ["arm", "category", "n", "n_gold_abstention", "accuracy",
                   "answerable_accuracy", "coverage", "selective_accuracy",
                   "abstain_precision", "abstain_recall",
                   "appeals", "appeals_won"]),
        "",
        "## Why each abstention happened",
        "",
        "Abstention precision says how often an abstention was right. It cannot "
        "say *why* a wrong one happened, and the four causes below need four "
        "different fixes — so the aggregate rate is the wrong number to act on.",
        "",
        "`count_false` is the defect: an abstention on a question that had a "
        "gold answer. `count` alone is not, because catching a genuinely "
        "unanswerable question is the system working.",
        "",
        "A reason with **count 0** is a measurement, not a gap in the table — "
        "the gated arm reports every reason it *can* return, fired or not. "
        "Arms with no cascade say so rather than reporting zeros against gates "
        "they do not have.",
        "",
        _md_table([r for r in reasons if r["category"] == "ALL"],
                  ["arm", "reason", "count", "count_false", "n_abstained"]),
        "",
        f"Full per-category breakdown: `{split}-abstentions.csv`.",
        "",
        "## Cost",
        "",
        _md_table(cost, ["section", "arm", "metric", "value", "unit", "note"]),
        "",
        MEM0_NOTE,
    ]
    path = ev.RESULTS / f"{split}-results.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oracle", action="store_true")
    args = parser.parse_args()

    split = "oracle" if args.oracle else "s"
    runs = ev.RUNS / split
    rows = [r for arm in ev.ARMS for r in load(runs / f"{arm}.jsonl")]
    if not rows:
        sys.exit(f"no scored questions in {runs}; run scripts/run_eval.py first")

    table = (
        [row("models", "all arms", "answering_model", llm.ANSWER_MODEL, "",
             "one answering model across every arm, so the comparison measures "
             "retrieval and not model quality"),
         row("models", "all arms", "judge_model", ev.JUDGE_MODEL, "",
             "not the answering model, so no arm grades its own output")]
        + query_cost(rows) + ingest_cost(load(runs / "ingest.jsonl")) + server_cost()
    )

    out = ev.to_csv(table, ev.RESULTS / f"{split}-cost.csv")
    doc = write_markdown(split, table)
    print(f"wrote {out}")
    print(f"wrote {doc}")
    print()
    for record in table:
        print(f"  {record['section']:<8} {record['arm']:<14} "
              f"{record['metric']:<38} {record['value']} {record['unit']}")


if __name__ == "__main__":
    main()
