"""The mem0-compatible surface: `Memory()` with add / search / get_all /
history / delete, plus `explain`, which is ours.

Slice 15's deliverable is a **two-line swap**, so this is a facade and nothing
more. Every capability it exposes already existed one layer down:
`client.write` has always returned a bookmark, `client.read` and
`answer.answer_question` have always accepted one, and `ingest.ingest_instance`
already hands the bookmark back. Read-your-own-writes is therefore not built
here -- it is *exposed* here, which is the honest description and the reason
this module is thin.

Two methods are not compatibility shims and are the argument for the design:

- `history` walks the supersession chain. mem0 cannot implement it, because a
  vector store overwrites a memory in place and the previous value is gone. Here
  a revision is a new Fact plus a SUPERSEDES edge, so the old value is still a
  node and "what did I used to think" is a traversal.
- `delete` is a **tombstone**. Facts are immutable and deletion does not get an
  exception: the fact keeps its node and its edges, takes `status='deleted'` and
  an end date, and drops out of current reads while staying in history. It also
  reuses `CLOSE_FACT` unchanged -- a tombstone is a close whose status happens
  to be 'deleted', so there is no new Cypher and no new way for the parser to
  reject something at 3am.
"""

import hashlib
import json
import time

from . import answer, chain, client, corpus, ingest, statements

# `user_id` is the tenant. HydraMem partitions by `instance_id`, mem0 partitions
# by `user_id`, and they mean the same thing, so the mapping is identity rather
# than a translation table nobody would be able to debug.
DEFAULT_USER = "default"


def _session_key(messages: list) -> str:
    """Content-derived, so adding the same messages twice is idempotent.

    Fact ids are already content-derived; giving the session a random id would
    be the one part of the write that replays as a duplicate.
    """
    blob = json.dumps(messages, sort_keys=True, ensure_ascii=False)
    return "mem-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _to_instance(messages: list, user_id: str, idx: int, timestamp: int):
    """Wrap raw messages as the one-session Instance the pipeline already takes."""
    turns = tuple(
        corpus.Turn(i, m.get("role", "user"), m.get("content", ""), False)
        for i, m in enumerate(messages)
    )
    session = corpus.Session(
        session_id=_session_key(messages), idx=idx, timestamp=timestamp, turns=turns
    )
    return corpus.Instance(
        instance_id=user_id,
        question_type="live",
        question="",
        answer="",
        asked_at=timestamp,
        sessions=(session,),
        answer_session_ids=(),
    )


class Memory:
    """Drop-in for `mem0.Memory`. The swap is the import and the constructor."""

    def __init__(self, driver=None, user_id: str = DEFAULT_USER, model: str = None):
        self.driver = driver or client.connect()
        self.user_id = user_id
        self.model = model
        # The bookmark of the most recent write, so a search that is given no
        # explicit bookmark still reads its own writes. This is the correctness
        # property a vector store cannot offer, so defaulting it off would be
        # shipping the guarantee and hiding it.
        self.bookmark = None
        # ponytail: an in-process counter, so session `idx` restarts at 0 in a
        # new process and NEXT edges between separately-added sessions can
        # repeat an ordinal. Harmless for the demo surface, where recency comes
        # from the timestamp. Read the tenant's session count first if NEXT
        # ordering across restarts ever has to be exact.
        self._seq = 0

    # --- mem0 surface ------------------------------------------------------

    def add(self, messages, user_id: str = None, timestamp: int = None) -> dict:
        """Extract durable facts from a turn list and write them.

        Returns the HydraDB bookmark alongside the counts. The bookmark is the
        whole point: hand it to `search` and the read is guaranteed to see this
        write, which is what makes "write a memory then immediately ask about
        it" a property rather than a race.
        """
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        stats = ingest.ingest_instance(
            self.driver,
            _to_instance(messages, user_id or self.user_id, self._seq,
                         timestamp or int(time.time())),
            model=self.model,
        )
        self._seq += 1
        self.bookmark = stats["bookmarks"]
        self._reconcile_chain(user_id or self.user_id)
        return {
            "results": [{"memory": f"{r['predicate']} = {r['value_text']}",
                         "id": r["fact_id"]}
                        for r in self._facts(user_id, self.bookmark)],
            "bookmark": self.bookmark,
            "facts_written": stats.get("facts", 0),
            "parse_failures": stats["parse_failures"],
        }

    def search(self, query: str, user_id: str = None, bookmarks=None,
               asked_at: int = None) -> dict:
        """The gate cascade. Abstention is a result, not an error.

        `bookmarks` defaults to the last write's, so read-your-own-writes holds
        without the caller knowing bookmarks exist.
        """
        result = answer.answer_question(
            self.driver, user_id or self.user_id, query,
            asked_at=asked_at or int(time.time()),
            bookmarks=bookmarks or self.bookmark,
            model=self.model,
        )
        return {
            "results": [
                {"id": e.get("fact_id"),
                 "memory": f"{e.get('predicate', '')} = {e.get('value', '')}",
                 # `role` rides along because a citation can be correct and
                 # still point at something the *assistant* said. answer.py
                 # surfaces it rather than rejecting it; dropping it here would
                 # undo that decision silently.
                 "role": e.get("role", "unknown"),
                 "snippet": e.get("snippet", "")}
                for e in (result.evidence or [])
            ],
            "answer": result.answer,
            "abstained": result.abstained,
            "reason": result.reason,
            "gate_trace": result.gate_trace,
            "round_trips": result.round_trips,
        }

    def get_all(self, user_id: str = None, bookmarks=None) -> dict:
        """Every fact still current for the tenant. Tombstoned facts are gone
        from here and still present in `history`, which is the difference
        between deletion and destruction."""
        rows = self._facts(user_id, bookmarks or self.bookmark)
        return {"results": [
            {"id": r["fact_id"], "memory": f"{r['predicate']} = {r['value_text']}",
             "subject": r["subject_key"], "created_at": r["valid_from"]}
            for r in rows if r["status"] == "current"
        ]}

    def history(self, memory_id: str = None, user_id: str = None,
                bookmarks=None) -> dict:
        """The revisions of a memory, oldest first.

        The method mem0 structurally cannot have. Filtered client-side because
        HydraDB's `WHERE` has no `IN` and the chain read is already one round
        trip for the whole tenant.
        """
        rows = client.read(
            self.driver, statements.SUPERSESSION_CHAIN_FOR_INSTANCE,
            {"instance_id": user_id or self.user_id},
            bookmarks=bookmarks or self.bookmark,
        )
        if memory_id:
            wanted = memory_id.strip().lower()
            rows = [r for r in rows
                    if wanted in (str(r["new_fact_id"]).lower(),
                                  str(r["old_fact_id"]).lower())]
        return {"results": [
            {"id": r["new_fact_id"], "predicate": r["predicate"],
             "old_memory": r["old_value"], "new_memory": r["new_value"],
             "changed_at": r["changed_at"], "old_id": r["old_fact_id"]}
            for r in rows
        ]}

    def delete(self, memory_id: str, user_id: str = None) -> dict:
        """Tombstone. The node and its edges survive; the fact stops being current.

        Reuses CLOSE_FACT, which already takes `status` and `valid_to` and is
        guarded on `valid_to` -- so a second delete is a no-op rather than an
        error, and a delete cannot move an end date backwards.
        """
        tenant = user_id or self.user_id
        target = memory_id.strip().lower()
        # fact_id is the caller-facing 16-hex handle; the node key is the int.
        # One read to map it beats reversing a hash, and it also proves the
        # memory belongs to this tenant before anything is written.
        rows = [r for r in self._facts(tenant, self.bookmark)
                if str(r["fact_id"]).lower() == target]
        if not rows:
            return {"deleted": 0, "reason": "not_in_graph", "id": memory_id}
        # Already-tombstoned facts are filtered *here* rather than left to the
        # guard. CLOSE_FACT's guard is strictly-less-than on `valid_to`, so a
        # second delete carries a later timestamp and would pass it -- the write
        # would land, the end date would move, and the call would report a
        # deletion that had already happened. Idempotence has to be decided on
        # status, which the guard cannot see.
        match = [r for r in rows if r["status"] != "deleted"]
        if not match:
            return {"deleted": 0, "reason": "already_deleted", "id": memory_id}
        now = int(time.time())
        _, self.bookmark = client.write(
            self.driver, statements.CLOSE_FACT,
            {"rows": [{"vid": r["id"], "status": "deleted", "valid_to": now}
                      for r in match]},
            bookmarks=self.bookmark,
        )
        return {"deleted": len(match), "id": memory_id, "valid_to": now,
                "bookmark": self.bookmark}

    # --- ours --------------------------------------------------------------

    def explain(self, query: str, user_id: str = None, asked_at: int = None) -> str:
        """The whole trace of one question, answered or abstained."""
        return answer.explain(answer.answer_question(
            self.driver, user_id or self.user_id, query,
            asked_at=asked_at or int(time.time()),
            bookmarks=self.bookmark, model=self.model,
        ))

    # --- internal ----------------------------------------------------------

    def _reconcile_chain(self, tenant: str) -> None:
        """Re-derive supersession over *every* fact the tenant holds.

        `ingest_instance` chains only the rows of the call that writes them,
        which is right for corpus ingest -- a whole instance arrives at once --
        and wrong for an incremental `add`. Two calls each saw one fact, so
        `employer = Acme` then `employer = Globex` left two current facts and no
        SUPERSEDES edge at all. `history` returning nothing is not a cosmetic
        gap: it is the single method that argues for this design over a vector
        store.

        Costs one read and at most two writes, and is safe to repeat: CLOSE_FACT
        is guarded on `valid_to` so a replay writes nothing, and the SUPERSEDES
        edge is MERGEd on a content-derived id.
        """
        stored = self._facts(tenant, self.bookmark)
        close, supersedes = chain.materialize(
            chain.rows_from_stored(stored, tenant), tenant
        )
        for payload, statement in ((close, statements.CLOSE_FACT),
                                   (supersedes, statements.LINK_SUPERSEDES)):
            if payload:
                _, self.bookmark = client.write(
                    self.driver, statement, {"rows": payload},
                    bookmarks=self.bookmark,
                )

    def _facts(self, user_id, bookmarks) -> list:
        return client.read(
            self.driver, statements.FACTS_FOR_INSTANCE,
            {"instance_id": user_id or self.user_id}, bookmarks=bookmarks,
        )
