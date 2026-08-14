"""Slice 06 quality gate. Writes docs/extraction-quality.md + a hand-check sheet.

    python scripts/measure_extraction.py [--instances 20] [--full] [--sample 60]

Measures what can be measured without a human, and leaves exactly one thing a
human still has to supply: fact precision, which needs someone to read the
transcript and judge whether the claim is supported.

Nothing here touches HydraDB. Extraction quality is a property of the model and
the prompt; putting a graph in the loop would only add a way to be wrong.
"""

import argparse
import collections
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from pydantic import ValidationError  # noqa: E402

from hydramem import corpus, extract, ingest  # noqa: E402

REPORT = pathlib.Path("docs/extraction-quality.md")
SHEET = pathlib.Path("docs/extraction-review.md")

# Gates. Below any of these, scaling to 500 instances buys 500 instances of the
# same defect, so the slice says no-go and names the escalation rung.
MIN_SCHEMA_VALIDITY = 0.95
MIN_GROUNDING = 0.90
MAX_OTHER_SHARE = 0.35


def sample_instances(path, count):
    """Spread the slice across the whole corpus, not the front of it.

    Both splits are grouped by question type: the first twenty instances are
    twenty temporal-reasoning questions, which would measure the extractor on
    one sixth of the benchmark and report it as the whole.
    """
    all_ids = [i.instance_id for i in corpus.iter_instances(path)]
    stride = max(1, len(all_ids) // count)
    wanted = set(all_ids[::stride][:count])
    return [i for i in corpus.iter_instances(path) if i.instance_id in wanted]


def measure(instances):
    stats = collections.Counter()
    predicates = collections.Counter()
    subjects = collections.Counter()
    off_vocab = collections.Counter()
    failures, rows, ungrounded = [], [], []

    for instance in instances:
        for session in instance.sessions:
            stats["sessions"] += 1
            date = dt.datetime.fromtimestamp(
                session.timestamp, dt.timezone.utc
            ).strftime("%Y-%m-%d %H:%M")
            try:
                raw = extract.extract_raw(session, date)
            except (ValidationError, ValueError) as exc:
                failures.append((instance.instance_id, session.session_id,
                                 str(exc).splitlines()[0][:120]))
                continue
            stats["parsed"] += 1

            text = session.text()
            kept = extract.clean(raw.facts, len(session.turns))
            stats["raw_facts"] += len(raw.facts)
            stats["kept_facts"] += len(kept)

            for fact in raw.facts:
                if fact.predicate not in extract.PREDICATES:
                    off_vocab[fact.predicate] += 1
                if not 0 <= fact.turn_idx < len(session.turns):
                    stats["bad_turn_idx"] += 1
            for fact in kept:
                predicates[fact.predicate] += 1
                subjects[ingest.entity_key(fact.subject_type, fact.subject)] += 1
                is_grounded = extract.grounded(fact.evidence_span, text)
                if is_grounded:
                    stats["grounded"] += 1
                elif not fact.evidence_span.strip():
                    stats["no_span"] += 1
                else:
                    stats["invented_span"] += 1
                    ungrounded.append((instance.instance_id, fact))
                rows.append((instance.instance_id, session.session_id, fact, is_grounded))

    return stats, predicates, subjects, off_vocab, failures, rows, ungrounded


def pct(part, whole):
    return 0.0 if not whole else 100.0 * part / whole


def verdict_for(broken):
    if not broken:
        return (
            "**GO** on the automatic gates -- every one passes. Scaling to the "
            "full corpus is blocked only on the hand-checked precision number "
            "above.\n\nThe escalation rungs this slice needed are recorded in "
            "`.scratch/hydramem/issues/06-extractor-quality.md`."
        )
    return (
        "**NO-GO.** Failing gates: " + ", ".join("`%s`" % b for b in broken) + ".\n\n"
        "Escalate in order -- tighten the prompt and re-confirm reasoning is "
        "off, then short sliding turn windows, then a larger extraction model "
        "-- and record the rung that fixed it here."
    )


def write_report(path, split, instances, stats, predicates, subjects, off_vocab,
                 failures, ungrounded, verdict):
    kept = stats["kept_facts"]
    validity = pct(stats["parsed"], stats["sessions"])
    grounding = pct(stats["grounded"], kept)
    other = pct(predicates["other"], kept)

    def mark(ok):
        return "PASS" if ok else "FAIL"

    top_predicates = "\n".join(
        "| `%s` | %d | %.1f%% |" % (n, c, pct(c, kept))
        for n, c in predicates.most_common(12)
    )
    top_subjects = "\n".join(
        "| `%s` | %d | %.1f%% |" % (n, c, pct(c, kept))
        for n, c in subjects.most_common(8)
    )
    off = "\n".join(
        "| `%s` | %d |" % (n, c) for n, c in off_vocab.most_common(15)
    ) or "| _(none)_ | 0 |"
    failed = "\n".join(
        "- `%s` / `%s` -- %s" % (i, s, m) for i, s, m in failures
    ) or "- none"
    invented = "\n".join(
        "| `%s` | `%s` | %s | %s |"
        % (i, f.predicate, f.value.replace("|", "\\|")[:40],
           f.evidence_span.replace("|", "\\|").replace("\n", " ")[:90])
        for i, f in ungrounded[:12]
    ) or "| _(none)_ | | | |"

    path.write_text(
        f"""# Extraction quality (slice 06)

Generated by `scripts/measure_extraction.py`. Do not hand-edit the numbers.
The hand-checked precision section is the one exception and is marked as such.

Split `{split}`, {len(instances)} instances, {stats['sessions']} sessions.

## Gates

| measure | value | gate | |
|---|---|---|---|
| schema validity | {validity:.1f}% ({stats['parsed']}/{stats['sessions']}) | >= {MIN_SCHEMA_VALIDITY:.0%} | {mark(validity >= MIN_SCHEMA_VALIDITY * 100)} |
| span grounding | {grounding:.1f}% ({stats['grounded']}/{kept}) | >= {MIN_GROUNDING:.0%} | {mark(grounding >= MIN_GROUNDING * 100)} |
| `other` share | {other:.1f}% | <= {MAX_OTHER_SHARE:.0%} | {mark(other <= MAX_OTHER_SHARE * 100)} |

**Grounding is a precision floor, not precision.** It asks whether the quoted
evidence exists in the session, so it catches invention and cannot catch a
correctly-quoted span filed under the wrong predicate. That failure mode is
what the hand-check below is for.

## Volume and repair

| | |
|---|---|
| facts emitted | {stats['raw_facts']} |
| facts kept after `clean()` | {kept} ({pct(kept, stats['raw_facts']):.1f}%) |
| facts per parsed session | {kept / max(1, stats['parsed']):.1f} |
| out-of-range `turn_idx` | {stats['bad_turn_idx']} ({pct(stats['bad_turn_idx'], stats['raw_facts']):.1f}%) |
| empty evidence span | {stats['no_span']} ({pct(stats['no_span'], kept):.1f}%) |
| span not found in session | {stats['invented_span']} ({pct(stats['invented_span'], kept):.1f}%) |

Spans the model quoted that are not in the transcript -- the only invention
this measurement can catch on its own:

| instance | predicate | value | quoted span |
|---|---|---|---|
{invented}

## Predicate vocabulary

| predicate | facts | share |
|---|---|---|
{top_predicates}

Off-vocabulary predicates the model asked for -- each becomes `other`:

| requested | count |
|---|---|
{off}

## Subject keys

Entity resolution reads these. Slice 07's `unknown_entity` gate leans on the
self-form closure, so the share sitting on `person:user` is the number that
decides whether alias edges matter at all.

| entity key | facts | share |
|---|---|---|
{top_subjects}

## Sessions rejected

{failed}

## Fact precision -- hand-checked

Fill this in from `extraction-review.md` after checking the sheet. It is the
only number here a script cannot produce.

- checked: _n_ facts
- supported by the transcript: _n_
- **precision: _n_%**

## Verdict

{verdict}
""",
        encoding="utf-8",
    )


def write_sheet(path, rows, sample):
    stride = max(1, len(rows) // sample)
    picked = rows[::stride][:sample]
    lines = [
        "# Extraction hand-check sheet (slice 06)",
        "",
        "Generated by `scripts/measure_extraction.py`. %d facts, every %dth of %d,"
        % (len(picked), stride, len(rows)),
        "so the sample is deterministic and reproduces exactly on a rerun.",
        "",
        "Mark each: `Y` supported by the transcript, `N` not supported, `P` supported",
        "but filed under the wrong predicate. `P` counts as unsupported -- a fact in",
        "the wrong slot supersedes the wrong chain, which is worse than being absent.",
        "",
        "Tally into the precision section of `extraction-quality.md`.",
        "",
        "A span marked **NOT IN TRANSCRIPT** already failed the grounding check.",
        "",
        "| # | ? | instance | session | turn | subject | predicate | value | evidence |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for n, (instance_id, session_id, fact, is_grounded) in enumerate(picked, 1):
        span = fact.evidence_span.replace("|", "\\|").replace("\n", " ")[:120]
        value = fact.value.replace("|", "\\|")[:60]
        lines.append(
            "| %d |  | `%s` | `%s` | %d | %s | `%s` | %s | %s%s |"
            % (n, instance_id, session_id[:12], fact.turn_idx,
               fact.subject[:24], fact.predicate, value,
               "" if is_grounded else "**NOT IN TRANSCRIPT** ", span)
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--instances", type=int, default=20)
    parser.add_argument("--sample", type=int, default=60)
    parser.add_argument("--full", action="store_true", help="use the _s split")
    args = parser.parse_args(argv)

    path = corpus.S_CORPUS if args.full else corpus.ORACLE_CORPUS
    if not path.exists():
        print(f"{path} missing -- run: python scripts/fetch_corpus.py")
        return 1

    instances = sample_instances(path, args.instances)
    print("%d instances from %s, %d sessions"
          % (len(instances), path.name, sum(len(i.sessions) for i in instances)))

    stats, predicates, subjects, off_vocab, failures, rows, ungrounded = measure(instances)

    validity = pct(stats["parsed"], stats["sessions"])
    grounding = pct(stats["grounded"], stats["kept_facts"])
    other = pct(predicates["other"], stats["kept_facts"])
    gates = {
        "schema validity": validity >= MIN_SCHEMA_VALIDITY * 100,
        "span grounding": grounding >= MIN_GROUNDING * 100,
        "predicate vocabulary": other <= MAX_OTHER_SHARE * 100,
    }
    broken = [name for name, ok in gates.items() if not ok]

    write_report(REPORT, path.name, instances, stats, predicates, subjects,
                 off_vocab, failures, ungrounded, verdict_for(broken))
    write_sheet(SHEET, rows, args.sample)

    print("schema validity %.1f%%  grounding %.1f%%  other %.1f%%  kept %d facts"
          % (validity, grounding, other, stats["kept_facts"]))
    for name, ok in gates.items():
        print("  %s  %s" % ("PASS" if ok else "FAIL", name))
    print("wrote %s and %s" % (REPORT, SHEET))
    return 0 if not broken else 2


if __name__ == "__main__":
    raise SystemExit(main())
