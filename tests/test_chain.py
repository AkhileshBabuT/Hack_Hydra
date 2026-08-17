"""Supersession: derived without a database, then materialized against one.

The pure tests are the ones that matter. If the chain is a function of the fact
set, permutation invariance is provable here in milliseconds; if it is an
emergent property of write ordering, it can only be probed against a live node
and only for the orderings someone thought to try.
"""

import random

from conftest import make_fact as fact, make_instance as instance, make_session as session

from hydramem import chain, client, corpus, ingest, statements

SESSIONS = [
    session(0, "s0", 1_000_000),
    session(1, "s1", 2_000_000),
    session(2, "s2", 3_000_000),
]


def rows_for(extractions, instance_id="test-instance"):
    return ingest.build_rows(instance(SESSIONS, instance_id), extractions)


def employer_history():
    """Acme -> Globex -> Initech, one revision per session."""
    return [
        (SESSIONS[0], [fact(value="Acme")]),
        (SESSIONS[1], [fact(value="Globex")]),
        (SESSIONS[2], [fact(value="Initech")]),
    ]


# --- pure derivation ------------------------------------------------------

def test_adjacent_revisions_are_paired_oldest_first():
    pairs = chain.derive(rows_for(employer_history()))
    assert [(a["value_text"], b["value_text"]) for a, b in pairs] == [
        ("Acme", "Globex"), ("Globex", "Initech"),
    ]


def test_shuffled_facts_yield_an_identical_chain():
    """The permutation case. A chain that depends on input order is not derived."""
    ordered = rows_for(employer_history())
    expected = [(a["vid"], b["vid"]) for a, b in chain.derive(ordered)]

    for seed in range(5):
        shuffled = ingest.Rows(
            facts=random.Random(seed).sample(ordered.facts, len(ordered.facts)),
            subject=random.Random(seed + 100).sample(ordered.subject, len(ordered.subject)),
        )
        assert [(a["vid"], b["vid"]) for a, b in chain.derive(shuffled)] == expected


def test_different_predicates_are_different_chains():
    rows = rows_for([
        (SESSIONS[0], [fact(value="Acme"), fact(predicate="lives_in", value="Berlin")]),
        (SESSIONS[1], [fact(value="Globex"), fact(predicate="lives_in", value="Munich")]),
    ])
    pairs = chain.derive(rows)
    assert {(a["predicate"], a["value_text"], b["value_text"]) for a, b in pairs} == {
        ("employer", "Acme", "Globex"), ("lives_in", "Berlin", "Munich"),
    }


def test_different_subjects_do_not_chain():
    rows = rows_for([
        (SESSIONS[0], [fact(value="Acme"), fact(subject="Maya", value="Globex")]),
    ])
    assert chain.derive(rows) == []


def test_a_restatement_collapses_to_one_current_fact():
    """The same value said twice leaves only the newest current, functional or not."""
    for predicate in ("employer", "likes"):
        pairs = chain.derive(rows_for([
            (SESSIONS[0], [fact(predicate=predicate, value="Acme")]),
            (SESSIONS[1], [fact(predicate=predicate, value="ACME ")]),
        ]))
        assert len(pairs) == 1, predicate


def test_a_non_functional_predicate_accumulates_instead_of_superseding():
    """Liking hiking does not retract liking tea. Treating every predicate as
    functional marked 193 of 220 facts superseded on a real instance."""
    rows = rows_for([
        (SESSIONS[0], [fact(predicate="likes", value="tea")]),
        (SESSIONS[1], [fact(predicate="likes", value="hiking")]),
        (SESSIONS[2], [fact(predicate="owns", value="a bike")]),
    ])
    assert chain.derive(rows) == []
    assert rows.close == []


def test_a_functional_predicate_still_supersedes():
    rows = rows_for([
        (SESSIONS[0], [fact(predicate="lives_in", value="Berlin")]),
        (SESSIONS[1], [fact(predicate="lives_in", value="Munich")]),
    ])
    assert [(a["value_text"], b["value_text"]) for a, b in chain.derive(rows)] == [
        ("Berlin", "Munich"),
    ]


# --- pure materialization -------------------------------------------------

def test_close_rows_end_the_old_fact_where_the_new_one_starts():
    rows = rows_for(employer_history())
    by_vid = {f["vid"]: f for f in rows.facts}
    for row in rows.close:
        assert row["status"] == "superseded"
        assert row["valid_to"] > by_vid[row["vid"]]["valid_from"]


def test_only_the_newest_fact_in_a_chain_is_left_open():
    rows = rows_for(employer_history())
    closed = {row["vid"] for row in rows.close}
    still_open = [f for f in rows.facts if f["vid"] not in closed]
    assert [f["value_text"] for f in still_open] == ["Initech"]


def test_a_misdated_revision_cannot_invert_the_interval():
    """A revision whose valid_from predates the fact it replaces would otherwise
    close an interval before it opened, and the temporal gate filters on exactly
    those two fields."""
    rows = rows_for([
        (SESSIONS[0], [fact(value="Acme", valid_from_hint="2020-01-01")]),
        (SESSIONS[1], [fact(value="Globex", valid_from_hint="1999-01-01")]),
    ])
    by_vid = {f["vid"]: f for f in rows.facts}
    for row in rows.close:
        assert row["valid_to"] >= by_vid[row["vid"]]["valid_from"]


def test_supersedes_edges_point_from_the_newer_fact():
    rows = rows_for(employer_history())
    by_vid = {f["vid"]: f for f in rows.facts}
    for edge in rows.supersedes:
        assert by_vid[edge["src"]]["asserted_at"] > by_vid[edge["dst"]]["asserted_at"]


# --- live materialization -------------------------------------------------

def counts(driver, instance_id):
    out = {}
    for name in ("count_facts", "count_edges_supersedes"):
        statement, _ = statements.INVENTORY[name]
        out[name] = client.read(driver, statement, {"instance_id": instance_id},
                                consistency="strong")[0]["total"]
    return out


def statuses(driver, instance_id):
    statement, _ = statements.INVENTORY["facts_for_instance"]
    rows = client.read(driver, statement, {"instance_id": instance_id}, consistency="strong")
    return sorted((r["value_text"], r["status"], r["valid_to"]) for r in rows)


def test_materialized_chain_leaves_one_current_fact(driver, instance_id):
    rows = ingest.build_rows(instance(SESSIONS, instance_id), employer_history())
    ingest.write_rows(driver, rows)

    current = [row for row in statuses(driver, instance_id) if row[1] == "current"]
    assert [row[0] for row in current] == ["Initech"]
    assert counts(driver, instance_id)["count_edges_supersedes"] == 2


def first_seen(driver, instance_id):
    statement, _ = statements.INVENTORY["entity_by_id"]
    eid = __import__("hydramem.ids", fromlist=["ids"]).entity_id(instance_id, ingest.SELF_KEY)
    return client.read(driver, statement, {"eid": eid}, consistency="strong")[0]["first_seen"]


def test_reverse_order_ingest_produces_the_same_chain(driver, instance_id):
    """Delivery order is not a correctness dependency -- for the chain, and for
    the create-only `first_seen` the entity upsert pins on first write."""
    inst = instance(SESSIONS, instance_id)
    ingest.write_rows(driver, ingest.build_rows(inst, employer_history()))
    forward = (statuses(driver, instance_id), counts(driver, instance_id), first_seen(driver, instance_id))

    ingest.write_rows(driver, ingest.build_rows(inst, list(reversed(employer_history()))))
    after = (statuses(driver, instance_id), counts(driver, instance_id), first_seen(driver, instance_id))
    assert after == forward
    assert forward[2] == 0, "first_seen should be the earliest session, not the first written"


def test_reingest_cannot_resurrect_a_superseded_fact(driver, instance_id):
    """UPSERT_FACT is guarded on asserted_at, which is always equal on replay, so
    the re-ingest patch is rejected and `status` stays superseded. Without that
    guard every re-ingest would silently reopen every closed fact."""
    inst = instance(SESSIONS, instance_id)
    rows = ingest.build_rows(inst, employer_history())
    ingest.write_rows(driver, rows)
    after_first = statuses(driver, instance_id)

    ingest.write_rows(driver, rows)
    assert statuses(driver, instance_id) == after_first
    assert sum(1 for row in after_first if row[1] == "superseded") == 2


# --- extraction noise the chain amplifies (measured, slice 06) -------------

def test_a_mis_slotted_functional_predicate_retracts_a_true_fact():
    """Instance `gpt4_2655b836`, hand-check sheet row 4: the extractor filed
    `name: 'silver Honda Civic'`.

    `name` is functional, so the car does not sit harmlessly beside the user's
    real name -- it takes the slot and supersedes it. This is the amplification
    CLAUDE.md records: only functional predicates chain, so a mis-slotted value
    is not a cosmetic mislabel, it is a retraction of something true. It is also
    why the hand-check counts a mis-slot as unsupported rather than as a minor
    labelling error.
    """
    pairs = chain.derive(rows_for([
        (SESSIONS[0], [fact(predicate="name", value="Ana")]),
        (SESSIONS[1], [fact(predicate="name", value="silver Honda Civic")]),
    ]))
    assert [(a["value_text"], b["value_text"]) for a, b in pairs] == [
        ("Ana", "silver Honda Civic"),
    ]


def test_the_same_mis_slot_on_a_non_functional_predicate_is_harmless():
    """The contrast that makes the rule worth keeping. Had the extractor
    reached for `likes` instead, both facts would coexist and nothing true
    would have been retracted.
    """
    pairs = chain.derive(rows_for([
        (SESSIONS[0], [fact(predicate="likes", value="Ana")]),
        (SESSIONS[1], [fact(predicate="likes", value="silver Honda Civic")]),
    ]))
    assert pairs == []


# --- slice 17: counts chain on what is counted -----------------------------


def test_a_knowledge_update_filed_as_other_forms_no_chain():
    """Instance `89941a93`, the failure slice 17 exists to fix.

    22 facts, 20 of them `other`, among them the two that ARE the knowledge
    update the instance is built around. `other` is non-functional, so the slot
    is the whole value; `three bikes` and `four bikes` are distinct values, so
    both stay current and the instance forms **0** SUPERSEDES edges -- on the
    one category supersession exists for.

    Kept as a characterisation test: this is what `other` still does, and it is
    why counts had to leave it rather than `other` being made to chain.
    """
    pairs = chain.derive(rows_for([
        (SESSIONS[0], [fact(predicate="other", value="three bikes")]),
        (SESSIONS[1], [fact(predicate="other", value="four bikes")]),
    ]))
    assert pairs == []


def test_a_count_supersedes_the_earlier_count_of_the_same_thing():
    """The same two facts under `quantity` chain, because the slot is `bikes`."""
    pairs = chain.derive(rows_for([
        (SESSIONS[0], [fact(predicate="quantity", value="three bikes")]),
        (SESSIONS[1], [fact(predicate="quantity", value="four bikes")]),
    ]))
    assert [(a["value_text"], b["value_text"]) for a, b in pairs] == [
        ("three bikes", "four bikes"),
    ]


def test_counts_of_different_things_do_not_retract_each_other():
    """The reason `quantity` is not functional.

    Functional would give one count slot per entity, so the camera count would
    supersede the bike count -- the mis-slot amplification pinned above, but
    built in by design rather than arrived at by extractor noise.
    """
    pairs = chain.derive(rows_for([
        (SESSIONS[0], [fact(predicate="quantity", value="four bikes")]),
        (SESSIONS[1], [fact(predicate="quantity", value="17 cameras")]),
    ]))
    assert pairs == []


def test_a_bare_number_keeps_its_own_slot():
    """`other | 2` was a real extracted value. Stripping the count leaves
    nothing to key on, so a bare number must not collapse into one slot with
    every other bare number on the entity."""
    pairs = chain.derive(rows_for([
        (SESSIONS[0], [fact(predicate="quantity", value="2")]),
        (SESSIONS[1], [fact(predicate="quantity", value="6")]),
    ]))
    assert pairs == []


def test_a_restated_count_still_collapses():
    """Saying "four bikes" twice is one fact, not a revision of itself."""
    pairs = chain.derive(rows_for([
        (SESSIONS[0], [fact(predicate="quantity", value="four bikes")]),
        (SESSIONS[1], [fact(predicate="quantity", value="Four Bikes")]),
    ]))
    assert len(pairs) == 1
