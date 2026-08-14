"""Ingest tests: the row builder without a database, then one live re-ingest.

Nearly everything worth getting wrong about ingest is decided in build_rows, so
that is where the tests are. They construct facts directly and never call a
model: extraction quality is measured in slice 06, not asserted here.
"""

import pytest

from hydramem import corpus, extract, ingest, statements


def session(idx, session_id, timestamp, n_turns=4):
    return corpus.Session(
        session_id=session_id, idx=idx, timestamp=timestamp,
        turns=tuple(
            corpus.Turn(i, "user" if i % 2 == 0 else "assistant", f"turn {i} text", False)
            for i in range(n_turns)
        ),
    )


def instance(sessions):
    return corpus.Instance(
        instance_id="test-instance", question_type="knowledge-update",
        question="where do I work?", answer="Acme", asked_at=2_000_000,
        sessions=tuple(sessions), answer_session_ids=(),
    )


def fact(subject="user", predicate="employer", value="Acme", **kw):
    return extract.ExtractedFact(subject=subject, predicate=predicate, value=value, **kw)


# --- pure: identity and normalization -------------------------------------

@pytest.mark.parametrize("surface", ["user", "I", "me", "  The User ", "Myself"])
def test_first_person_forms_are_one_entity(surface):
    assert ingest.entity_key("person", surface) == ingest.SELF_KEY


def test_normalization_strips_honorifics_and_punctuation():
    assert ingest.entity_key("person", "Dr. Maya Chen!") == "person:maya chen"


def test_alias_is_an_edge_not_a_merge():
    entities = [
        {"key": "person:maya", "name": "Maya", "type": "person"},
        {"key": "person:maya chen", "name": "Maya Chen", "type": "person"},
        {"key": "org:maya", "name": "Maya", "type": "org"},
    ]
    # The short form points at the long one, and the same surface form under a
    # different type is a different entity, not an alias.
    assert ingest.alias_pairs(entities) == [("person:maya", "person:maya chen")]


# --- pure: the temporal axis ----------------------------------------------

SAID = 1_681_138_020  # 2023-04-10


@pytest.mark.parametrize("hint,expected_date", [
    ("2023-04-15", "2023-04-15"),
    ("2023/04/15", "2023-04-15"),
    ("2023-04", "2023-04-01"),
    ("3/22/2021", "2021-03-22"),
    ("3/22", "2023-03-22"),      # bare month/day resolves inside the said year
    ("12/25", "2022-12-25"),     # ... and backwards when that would be future
])
def test_valid_from_resolves_date_hints(hint, expected_date):
    import datetime as dt
    got = ingest.resolve_valid_from(hint, SAID)
    assert dt.datetime.fromtimestamp(got, dt.timezone.utc).strftime("%Y-%m-%d") == expected_date


@pytest.mark.parametrize("hint", [None, "", "last summer", "13/45", "2023-99-99"])
def test_unparseable_hint_falls_back_to_assertion_time(hint):
    assert ingest.resolve_valid_from(hint, SAID) == SAID


# --- pure: extractor output repair ----------------------------------------

def test_clean_drops_absences_dressed_as_values():
    facts = [fact(value="nothing mentioned"), fact(value="  "), fact(value="Acme")]
    assert [f.value for f in extract.clean(facts, 4)] == ["Acme"]


def test_clean_forces_off_vocabulary_predicates_to_other():
    [cleaned] = extract.clean([fact(predicate="current_place_of_employment")], 4)
    assert cleaned.predicate == "other"


def test_clean_repairs_turn_index_out_of_range():
    [cleaned] = extract.clean([fact(turn_idx=99)], 4)
    assert cleaned.turn_idx == 0


# --- pure: the row builder ------------------------------------------------

def test_rows_are_identical_across_runs():
    """Content-derived ids are the first idempotency layer. If they drift,
    nothing downstream is idempotent, whatever the mutation key says."""
    inst = instance([session(0, "s0", 1000)])
    extractions = [(inst.sessions[0], [fact()])]
    assert ingest.build_rows(inst, extractions) == ingest.build_rows(inst, extractions)


def test_session_order_does_not_change_fact_identity():
    sessions = [session(0, "s0", 1000), session(1, "s1", 2000)]
    inst = instance(sessions)
    forward = ingest.build_rows(inst, [(sessions[0], [fact()]), (sessions[1], [fact(value="Globex")])])
    reverse = ingest.build_rows(inst, [(sessions[1], [fact(value="Globex")]), (sessions[0], [fact()])])
    assert {f["vid"] for f in forward.facts} == {f["vid"] for f in reverse.facts}
    assert {e["key"] for e in forward.entities} == {e["key"] for e in reverse.entities}


def test_entity_first_seen_is_the_earliest_session():
    sessions = [session(0, "s0", 1000), session(1, "s1", 2000)]
    inst = instance(sessions)
    rows = ingest.build_rows(inst, [(sessions[1], [fact()]), (sessions[0], [fact()])])
    [user] = [e for e in rows.entities if e["key"] == ingest.SELF_KEY]
    assert (user["first_seen"], user["last_seen"]) == (0, 1)


def test_entity_valued_facts_get_an_object_edge():
    inst = instance([session(0, "s0", 1000)])
    rows = ingest.build_rows(inst, [(inst.sessions[0], [fact(value="Acme", value_is_entity=True)])])
    assert len(rows.object) == 1
    assert {e["key"] for e in rows.entities} == {ingest.SELF_KEY, "org:acme"}


def test_every_fact_gets_subject_and_provenance_edges():
    inst = instance([session(0, "s0", 1000)])
    rows = ingest.build_rows(inst, [(inst.sessions[0], [fact(), fact(predicate="lives_in", value="Berlin")])])
    assert len(rows.facts) == len(rows.subject) == len(rows.asserted_in) == 2


def test_batch_key_is_content_derived_and_within_hydradb_limits():
    key = ingest.batch_key("upsert_fact", [{"vid": 1}])
    assert key != ingest.batch_key("upsert_fact", [{"vid": 2}])
    assert len(key) <= 128 and all(c.isalnum() or c in "._-" for c in key)


# --- live: writes converge ------------------------------------------------

def test_reingest_changes_no_counts(driver, instance_id):
    """Re-running a completed ingest must be a no-op, including when the second
    pass groups rows differently -- which is what a crash-and-resume does."""
    sessions = [session(0, "s0", 1000), session(1, "s1", 2000)]
    inst = corpus.Instance(
        instance_id=instance_id, question_type="knowledge-update",
        question="q", answer="a", asked_at=3000,
        sessions=tuple(sessions), answer_session_ids=(),
    )
    extractions = [
        (sessions[0], [fact(), fact(predicate="lives_in", value="Berlin", value_is_entity=True)]),
        (sessions[1], [fact(value="Globex")]),
    ]
    rows = ingest.build_rows(inst, extractions)

    def counts():
        return {
            name: client_read(driver, name, instance_id)
            for name in ("count_entities", "count_facts", "count_edges_subject")
        }

    ingest.write_rows(driver, rows)
    first = counts()
    assert first["count_facts"] == len(rows.facts) > 0

    ingest.write_rows(driver, rows, batch_size=1)   # deliberately regrouped
    assert counts() == first


def test_interrupted_ingest_resumes_to_the_same_graph(driver, instance_id):
    """A crash partway through must not leave a graph a resume cannot repair.

    The interruption is modelled by writing a prefix of each payload -- which is
    what a killed process leaves behind -- and then running the whole ingest.
    """
    sessions = [session(0, "s0", 1000), session(1, "s1", 2000)]
    inst = corpus.Instance(
        instance_id=instance_id, question_type="knowledge-update",
        question="q", answer="a", asked_at=3000,
        sessions=tuple(sessions), answer_session_ids=(),
    )
    rows = ingest.build_rows(inst, [
        (sessions[0], [fact(), fact(predicate="lives_in", value="Berlin", value_is_entity=True)]),
        (sessions[1], [fact(value="Globex"), fact(predicate="pet", value="cat")]),
    ])

    partial = ingest.Rows(
        sessions=rows.sessions[:1], entities=rows.entities[:1],
        facts=rows.facts[:1], subject=rows.subject[:1],
        asserted_in=rows.asserted_in[:1],
    )
    ingest.write_rows(driver, partial)
    ingest.write_rows(driver, rows)

    assert client_read(driver, "count_facts", instance_id) == len(rows.facts)
    assert client_read(driver, "count_entities", instance_id) == len(rows.entities)
    assert client_read(driver, "count_edges_subject", instance_id) == len(rows.subject)


def test_a_batch_key_cannot_carry_two_different_payloads(driver, instance_id):
    """The mutation key is derived from the rows it sends.

    HydraDB does not police this on the Cypher path -- reusing a key with a
    different payload is accepted, verified against a live node -- so the
    guarantee has to come from the key itself. Two payloads, two keys, always.
    """
    a = [{"vid": 1, "instance_id": instance_id}]
    b = [{"vid": 2, "instance_id": instance_id}]
    assert ingest.batch_key("upsert_entity", a) != ingest.batch_key("upsert_entity", b)
    assert ingest.batch_key("upsert_entity", a) == ingest.batch_key("upsert_entity", list(a))


def client_read(driver, statement_name, instance_id):
    from hydramem import client
    statement, _ = statements.INVENTORY[statement_name]
    return client.read(driver, statement, {"instance_id": instance_id},
                       consistency="strong")[0]["total"]
