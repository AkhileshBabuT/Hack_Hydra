"""Slice 03 end to end: one question instance, raw corpus -> answer.

    python scripts/ingest_one.py [question_id] [--oracle]

Prints the ingest counts, the answer, and the gold answer side by side. Re-runs
are free: extraction is served from the disk cache and every write is a no-op.
"""

import sys
import time

sys.path.insert(0, ".")

from hydramem import answer, client, corpus, ingest, statements  # noqa: E402


def main(argv: list) -> int:
    path = corpus.ORACLE_CORPUS if "--oracle" in argv else corpus.S_CORPUS
    wanted = [a for a in argv[1:] if not a.startswith("-")]

    if not path.exists():
        print(f"{path} missing -- run: python scripts/fetch_corpus.py")
        return 1

    instance = (
        corpus.load_instance(wanted[0], path) if wanted
        else next(corpus.iter_instances(path))
    )
    print(f"instance {instance.instance_id}  ({instance.question_type}, "
          f"{len(instance.sessions)} sessions)")
    print(f"Q: {instance.question}")

    driver = client.connect()
    started = time.perf_counter()
    stats = ingest.ingest_instance(driver, instance)
    bookmarks = stats.pop("bookmarks")
    failed = stats.pop("failed_sessions")
    print(f"ingested in {time.perf_counter() - started:.1f}s: "
          + ", ".join(f"{k}={v}" for k, v in stats.items() if k != "instance_id"))
    for session_id, message in failed:
        print(f"  extraction rejected: {session_id}: {message[:90]}")

    # Read back through the write's bookmark: HydraDB refreshes the reader until
    # this write is visible before pinning, so an ingest is never read stale.
    for name in ("count_entities", "count_facts", "count_edges_subject"):
        statement, _ = statements.INVENTORY[name]
        total = client.read(driver, statement, {"instance_id": instance.instance_id},
                            bookmarks=bookmarks)[0]["total"]
        print(f"  graph {name}: {total}")

    result = answer.answer_question(
        driver, instance.instance_id, instance.question,
        asked_at=instance.asked_at, bookmarks=bookmarks,
    )
    print(f"\nanswer:   {result.answer}")
    print(f"gold:     {instance.answer}")
    if result.abstained:
        print(f"abstained: {result.reason}")
    else:
        print(f"cited:    {result.cited_fact_ids} of {result.fact_count} facts")
    driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
