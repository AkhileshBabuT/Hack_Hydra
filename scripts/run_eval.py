"""Slice 12: run the stratified slice through every arm and score it.

    python scripts/run_eval.py --oracle                      # the thesis gate
    python scripts/run_eval.py --oracle --arms hydramem      # one arm
    python scripts/run_eval.py --oracle --per-type 3         # a smoke run
    python scripts/run_eval.py                               # the _s split

Resumable and unattended. Every scored question is appended to
`.eval/<split>-<arm>.jsonl` as it completes, and a rerun skips what is already
there -- so a rate limit, a transient 404 or a closed laptop costs the questions
in flight and nothing else. Model calls are disk-cached on top of that, so a
rerun of a finished arm is free.

The HydraMem arm needs its instances in the graph. Ingest is skipped for any
instance that already holds facts: fact ids are content-derived, so re-ingesting
identical input is a no-op that still costs nine write round trips per instance,
and at 150 instances that is most of the wall clock.
"""

import argparse
import json
import pathlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(line_buffering=True)   # unattended runs are watched by tail

from hydramem import client, corpus, eval as ev, ingest, llm, statements  # noqa: E402


def load_done(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["instance_id"]] = row
    return rows


def append(path: pathlib.Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def ensure_ingested(driver, instance, ledger: pathlib.Path) -> int:
    """Ingest if the tenant is empty, and record what it cost.

    Slice 14 wants ingest cost per unit and there is exactly one moment it can
    be measured honestly: here, around the write. Tokens are a difference of
    `llm.usage()`, which counts cache hits too -- the extraction was paid for
    once whether or not this process is the one that paid, and a per-fact cost
    that collapses on a rerun would describe the cache rather than the pipeline.
    """
    held = client.read(driver, statements.COUNT_FACTS,
                       {"instance_id": instance.instance_id})[0]["total"]
    if held:
        return 0

    before, trips, began = llm.usage(), client.round_trips(), time.monotonic()
    stats = ingest.ingest_instance(driver, instance)
    after = llm.usage()
    append(ledger, {
        "instance_id": instance.instance_id,
        "sessions": len(instance.sessions),
        "facts": stats["facts"], "entities": stats["entities"],
        "parse_failures": stats["parse_failures"],
        "prompt_tokens": after["prompt_tokens"] - before["prompt_tokens"],
        "completion_tokens": after["completion_tokens"] - before["completion_tokens"],
        "round_trips": client.round_trips() - trips,
        "latency_ms": int((time.monotonic() - began) * 1000),
    })
    return stats["facts"]


def warm_extraction(instances: list, workers: int = 8) -> None:
    """Fill the extraction cache concurrently before the HydraMem arm walks it.

    Extraction is one cached call per session and it dominates the wall clock --
    roughly 350 sessions across the oracle slice, against a sequential call that
    takes tens of seconds. Nothing here touches `client.round_trips()` or any
    per-question measurement: it only populates the disk cache, and the ingest
    that follows is still sequential and still counted. `llm._record` takes a
    lock so the token totals survive the pool.

    ponytail: a thread pool over the function that is already cached, not an
    async rewrite of the client. The calls are network-bound and a retry is free
    once the cache has the answer.
    """
    began = time.monotonic()
    with ThreadPoolExecutor(workers) as pool:
        for done, _ in enumerate(pool.map(ingest.extract_instance, instances), 1):
            if done % 25 == 0:
                print(f"  warmed {done}/{len(instances)} instances")
    print(f"  extraction cache warm in {time.monotonic() - began:.0f}s")


def run_arm(name: str, instances: list, driver, runs: pathlib.Path,
            appeal: bool = False) -> list:
    path = runs / f"{name}.jsonl"
    done = load_done(path)
    print(f"\n=== arm {name} === {len(done)}/{len(instances)} already scored")
    # Warming happens in `main` before the driver is created -- see the note
    # there. Doing it here would re-open the stale-driver window this fixed.
    began = time.monotonic()
    for i, instance in enumerate(instances, 1):
        if instance.instance_id in done:
            continue
        if name == "hydramem":
            written = ensure_ingested(driver, instance, runs / "ingest.jsonl")
            if written:
                print(f"  ingested {instance.instance_id}: {written} facts")
        row = ev.score(ev.ARMS[name](instance, driver=driver, appeal=appeal))
        append(path, row)
        done[instance.instance_id] = row
        mark = "ABSTAIN" if row["abstained"] else ("ok" if row["correct"] else "WRONG")
        print(f"  [{i:>3}/{len(instances)}] {instance.instance_id:<22}"
              f" {row['category']:<26} {mark}")
    print(f"  {name} done in {time.monotonic() - began:.0f}s")
    return [done[i.instance_id] for i in instances if i.instance_id in done]


def verdict(summary: list) -> tuple:
    """The go/no-go. The thesis is abstention and knowledge update, nothing else.

    Diagnosis order when it fails is not arbitrary -- predicate gate too
    permissive, then entity gate missing aliases, then the citation check failing
    to downgrade. Those three account for nearly all abstention-precision losses.
    """
    def cell(arm, category, field):
        for row in summary:
            if row["arm"] == arm and row["category"] == category:
                return row[field] or 0
        return 0

    checks = [
        ("abstention recall", cell("hydramem", "ALL", "abstain_recall"),
         cell("full_context", "ALL", "abstain_recall")),
        ("abstention precision", cell("hydramem", "ALL", "abstain_precision"),
         cell("full_context", "ALL", "abstain_precision")),
        ("knowledge-update accuracy", cell("hydramem", "knowledge-update", "accuracy"),
         cell("full_context", "knowledge-update", "accuracy")),
    ]
    lines = [f"  {name:<28} hydramem {ours:<8} baseline {theirs}"
             for name, ours, theirs in checks]
    won = [name for name, ours, theirs in checks if ours > theirs]
    return ("GO" if len(won) >= 2 else "NO-GO"), lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oracle", action="store_true",
                        help="oracle split (fast loop); default is the _s benchmark")
    parser.add_argument("--per-type", type=int, default=ev.PER_TYPE,
                        help="cap per answerable question type; every abstention "
                             "instance is taken regardless")
    parser.add_argument("--arms", default="full_context,hydramem")
    # Regenerating the combined table must never be able to start a run. Arms are
    # often in flight in other processes, and a second process on the same arm
    # duplicates its rows -- which `load_done` absorbs and the ingest ledger did
    # not, until it deduplicated by instance.
    # Off by default so the two ablation runs are two runs, not a run either
    # side of a code change. Slice 17 bundled three changes into one
    # wipe-and-rescore and could not attribute the result; this is that lesson
    # spent once.
    parser.add_argument("--appeal", action="store_true",
                        help="let a `not_in_graph` abstention have one second "
                             "look at wider evidence (hydramem arm only)")
    parser.add_argument("--summarise-only", action="store_true",
                        help="rebuild the CSV from scored rows; score nothing")
    args = parser.parse_args()

    split = "oracle" if args.oracle else "s"
    path = corpus.ORACLE_CORPUS if args.oracle else corpus.S_CORPUS
    runs = ev.RUNS / split
    instances = ev.stratify(corpus.iter_instances(path), per_type=args.per_type)

    print(f"split {split}: {len(instances)} questions, answering model "
          f"{llm.ANSWER_MODEL}, judge {ev.JUDGE_MODEL}")
    for (question_type, is_abstention), n in sorted(ev.counts(instances).items()):
        print(f"  {question_type:<28} {'ABSTAIN' if is_abstention else '':<8} {n:>3}")

    if args.summarise_only:
        rows = [r for name in args.arms.split(",")
                for r in load_done(runs / f"{name.strip()}.jsonl").values()]
        print(f"summarising {len(rows)} scored questions, running nothing")
    else:
        # Warm the extraction cache before the driver exists, not inside the arm
        # loop. `warm_extraction` runs for minutes on a cold cache -- roughly 350
        # sessions -- and a Bolt driver created before it sits idle throughout,
        # after which the first read fails `ServiceUnavailable: Unable to
        # retrieve routing information` and takes the whole run with it.
        #
        # Measured 2026-08-17: a full re-ingest died on exactly this, after
        # paying for all 350 extraction calls and before scoring one question.
        # The node was healthy the entire time and its logs were clean, so the
        # failure looks like an outage and is a stale routing table.
        for name in args.arms.split(","):
            todo = [i for i in instances
                    if i.instance_id not in load_done(runs / f"{name.strip()}.jsonl")]
            if name.strip() == "hydramem" and todo:
                warm_extraction(todo)
        driver = client.connect()
        rows = []
        try:
            for name in args.arms.split(","):
                rows.extend(run_arm(name.strip(), instances, driver, runs,
                                    appeal=args.appeal))
        finally:
            driver.close()

    summary = ev.summarise(rows)

    # `<split>-per-category.csv` is the THREE-ARM comparison table. A run over
    # one arm produces a summary of one arm, and writing it here silently
    # replaces the comparison with a fragment -- the baselines simply vanish.
    #
    # Measured 2026-08-17: a resume loop calling `--arms hydramem` a dozen times
    # left the file holding 7 rows and a single arm, while README.md and
    # CLAUDE.md went on citing baseline figures that existed in no generated
    # file at all. The remaining table stayed *plausible*, which is why it
    # survived several slices unnoticed. Rebuilding it is one `--summarise-only`
    # away; noticing it was luck.
    #
    # So a partial run refuses to write it. That is not an error -- scoring one
    # arm is the normal way to work here -- it simply may not claim to be the
    # comparison.
    covered = {r["arm"] for r in summary}
    if covered >= set(ev.ARMS):
        out = ev.to_csv(summary, ev.RESULTS / f"{split}-per-category.csv")
        print(f"\nwrote {out}")
    else:
        missing = ", ".join(sorted(set(ev.ARMS) - covered))
        print(f"\nNOT writing {split}-per-category.csv -- this run covers "
              f"{', '.join(sorted(covered)) or 'nothing'} and the comparison "
              f"also needs {missing}.")
        print(f"  Rebuild with: run_eval.py --oracle --summarise-only "
              f"--arms {','.join(sorted(ev.ARMS))}")
    for row in summary:
        print(f"  {row['arm']:<14} {row['category']:<26} n={row['n']:<4}"
              f" acc={row['accuracy']:<8} abs_p={row['abstain_precision']:<8}"
              f" abs_r={row['abstain_recall']:<8} tok/q={row['tokens_per_q']:<7}"
              f" rt/q={row['round_trips_per_q']}")

    # Same guard, same reason. This one bit while *testing* the guard above: a
    # single-arm `--summarise-only` correctly refused the comparison table and
    # then clobbered the abstention breakdown on the next line, taking both
    # baselines' `(no gates)` rows with it. One fix per file is not a fix.
    reasons = ev.abstention_reasons(rows)
    if covered >= set(ev.ARMS):
        out = ev.to_csv(reasons, ev.RESULTS / f"{split}-abstentions.csv")
        print(f"wrote {out}")
    else:
        print(f"NOT writing {split}-abstentions.csv either -- same reason.")
    for row in reasons:
        # ALL only: the per-category rows are for the CSV, not for a terminal.
        # A zero still prints -- `no_path: 0` is the finding, not the absence of
        # one, and a breakdown that hides its zeros cannot report it.
        if row["category"] == "ALL":
            print(f"  {row['arm']:<14} {row['reason']:<22}"
                  f" {row['count']:>3} of {row['n_abstained']:<4}"
                  f" false={row['count_false']}")

    call, lines = verdict(summary)
    print(f"\n{call} against the thesis")
    print("\n".join(lines))
    if call == "NO-GO":
        print("  diagnose in this order: predicate gate too permissive, "
              "entity gate missing aliases, citation check failing to downgrade")


if __name__ == "__main__":
    main()
