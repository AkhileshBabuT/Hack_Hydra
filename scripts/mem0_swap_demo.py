"""The two-line swap, run end to end against a live node.

    python scripts/mem0_swap_demo.py

The swap an agent author makes is exactly this:

    # from mem0 import Memory
    from hydramem.memory import Memory

Everything below is the mem0 surface being used normally. What is *not* normal
is the last three sections: a search that is guaranteed to see the write that
preceded it, a history that still holds the value a revision replaced, and a
delete that leaves the fact in history rather than destroying it. A vector store
cannot do any of the three.
"""

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# --- the two lines -------------------------------------------------------
# from mem0 import Memory
from hydramem.memory import Memory  # noqa: E402

USER = f"demo-{int(time.time())}"      # a fresh tenant, so the demo is repeatable
DAY = 86_400


def show(title):
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def main() -> None:
    memory = Memory(user_id=USER)
    print(f"tenant: {USER}")

    show("1. add -- returns a HydraDB bookmark")
    # `employer` is functional, so this revises rather than accumulates. Phrased
    # plainly on purpose: "I work at Acme Corp as a data engineer" extracts as
    # `occupation = data engineer` and never fills the employer slot at all, so
    # the demo would abstain on its own question -- honestly, and uselessly.
    first = memory.add(
        [{"role": "user", "content": "I work at Acme Corp."}],
        timestamp=int(time.time()) - 200 * DAY,
    )
    print(f"  facts written : {first['facts_written']}")
    print(f"  bookmark      : {str(first['bookmark'])[:60]}")
    for row in first["results"]:
        print(f"    - {row['memory']}")

    show("2. search -- reads its own write, no sleep, no retry")
    # The bookmark is passed implicitly (memory.bookmark) but shown here
    # explicitly, because "is it visible yet" is the question a vector-store
    # memory layer cannot answer at all.
    hit = memory.search("Where do I work?", bookmarks=first["bookmark"])
    print(f"  answer    : {hit['answer']}")
    print(f"  abstained : {hit['abstained']}")
    print(f"  trips     : {hit['round_trips']}")

    show("3. a knowledge update -- the fact is revised, not overwritten")
    memory.add(
        [{"role": "user", "content": "I left Acme. I work at Globex now."}],
        timestamp=int(time.time()) - 10 * DAY,
    )
    print(f"  now        : {memory.search('Where do I work?')['answer']}")

    show("4. history -- the method mem0 structurally cannot implement")
    history = memory.history()
    if not history["results"]:
        print("  (no revision formed -- the extractor did not chain these two)")
    for row in history["results"]:
        print(f"  {row['predicate']}: {row['old_memory']!r} -> {row['new_memory']!r}")
        print(f"    old id {row['old_id']}  new id {row['id']}")

    # Before the delete, deliberately. Run after it and `explain` abstains
    # `not_in_graph` -- correct, because the fact it would cite is tombstoned,
    # but it reads as a broken demo rather than as the tombstone working.
    show("5. explain -- the whole trace of one question")
    print(memory.explain("Where do I work?"))

    show("6. delete -- a tombstone, not destruction")
    current = memory.get_all()["results"]
    if current:
        target = current[0]
        print(f"  deleting: {target['memory']}  ({target['id']})")
        print(f"  result  : {memory.delete(target['id'])['deleted']} tombstoned")
        after = {r["id"] for r in memory.get_all()["results"]}
        print(f"  still current : {target['id'] in after}   <- gone from current")
        print("  in history    : still a node, with its edges and its chain")
        print(f"  asking again  : {memory.search('Where do I work?')['reason'] or 'answered'}")


if __name__ == "__main__":
    main()
