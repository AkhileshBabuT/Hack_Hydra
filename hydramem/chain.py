"""Supersession as a pure function of the fact set.

Facts are never mutated. A revision is a new Fact plus a SUPERSEDES edge, so
"where do I work" and "where did I work" are two traversals of one chain rather
than two prompting strategies.

The chain is *derived*, not accumulated: group by (subject entity, predicate),
sort by (asserted_at, turn_idx, id), pair adjacent facts. Permutation invariance
is therefore a property of a function that can be tested with no database, which
is the whole reason it lives here rather than emerging from write order.

Only *functional* predicates chain by predicate alone: an employer replaces an
employer, but a new "likes" does not retract the last one. For everything else
the chain runs per distinct value, so a restatement collapses to one current
fact while genuinely different values coexist.
"""

from collections import defaultdict

from . import extract, ids


def group_key(entity: int, fact: dict):
    """What counts as "the same slot", and therefore what can supersede what.

    A functional predicate owns one slot per entity, so any newer value replaces
    the older one. A non-functional predicate opens a slot per value, so two
    different likes coexist while the same like said twice collapses.
    """
    if fact["predicate"] in extract.FUNCTIONAL_PREDICATES:
        return (entity, fact["predicate"])
    return (entity, fact["predicate"], fact["value_text"].strip().lower())


def derive(rows) -> list:
    """Rows -> [(older_fact, newer_fact)] over every (entity, predicate) group.

    Ordering is total and content-derived: assertion time, then turn within the
    session, then node id. Two facts asserted in the same turn about the same
    predicate are a tie the corpus cannot break, so id decides and does so
    identically in every process.
    """
    subject_of = {row["fid"]: row["eid"] for row in rows.subject}
    groups = defaultdict(list)
    for fact in rows.facts:
        entity = subject_of.get(fact["vid"])
        if entity is None:
            continue          # a fact with no subject cannot belong to a chain
        groups[group_key(entity, fact)].append(fact)

    pairs = []
    for key in sorted(groups):
        ordered = sorted(
            groups[key],
            key=lambda f: (f["asserted_at"], f["turn_idx"], f["vid"]),
        )
        pairs.extend(zip(ordered, ordered[1:]))
    return pairs


def materialize(rows, instance_id: str):
    """Chain pairs -> (close rows, SUPERSEDES edge rows).

    `close` re-upserts the superseded fact with a status and a validity end. It
    is guarded on `valid_to`, which only ever moves 0 -> timestamp, and HydraDB
    applies a guarded patch strictly when the stored value is *less* than the
    incoming one. So a replay is a no-op, an older close cannot move the end
    date backwards, and -- because the fact upsert is itself guarded on an
    always-equal `asserted_at` -- a later re-ingest cannot resurrect `current`.
    """
    close, supersedes = [], []
    for older, newer in derive(rows):
        close.append({
            "vid": older["vid"],
            "status": "superseded",
            # The revision's own start date closes the fact it replaces. Clamped
            # so a mis-dated revision cannot produce an interval that ends before
            # it begins -- the temporal gate filters on exactly these two fields.
            "valid_to": max(newer["valid_from"], older["valid_from"]),
        })
        supersedes.append({
            "src": newer["vid"], "dst": older["vid"],
            "rid": ids.edge_id(newer["vid"], "SUPERSEDES", older["vid"]),
            "instance_id": instance_id,
        })
    return close, supersedes


def current_facts(facts: list) -> list:
    """The head of every chain, from a plain fact list. No database.

    Mirrors what `status = 'current'` means in the graph, so the gate logic can
    be exercised over fixtures.
    """
    return [f for f in facts if f.get("status") == "current"]
