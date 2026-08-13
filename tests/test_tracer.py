"""The tracer: one fact written over Bolt, read back through its bookmark.

The bookmark assertion is the point. HydraDB refreshes the reader until the
write's storage sequence is visible before pinning the snapshot, so an agent
that writes a memory and immediately searches for it is guaranteed to see it.
"""

from hydramem import client, ids, statements


def _write_fact(driver, instance_id, subject="person:maya", value="Acme"):
    eid = ids.entity_id(instance_id, subject)
    key = ids.idempotency_key(instance_id, "s1", "0", "employer", subject, value)
    fid = ids.fact_id(key)
    rid = ids.edge_id(fid, "SUBJECT", eid)

    _, bm = client.write(
        driver,
        statements.UPSERT_ENTITY,
        {"rows": [{"vid": eid, "key": subject, "name": "Maya",
                   "type": "person", "first_seen": 0, "instance_id": instance_id}]},
        idempotency_key=f"{key}.e",
    )
    _, bm = client.write(
        driver,
        statements.UPSERT_FACT,
        {"rows": [{"vid": fid, "fact_id": key[:26], "predicate": "employer",
                   "value_text": value, "value_type": "literal",
                   "valid_from": 1700000000, "valid_to": 0,
                   "asserted_at": 1700000000, "session_id": "s1", "turn_idx": 0,
                   "snippet": f"I work at {value}", "confidence": 0.9,
                   "status": "current", "instance_id": instance_id}]},
        idempotency_key=f"{key}.f",
        bookmarks=bm,
    )
    _, bm = client.write(
        driver,
        statements.LINK_SUBJECT,
        {"rows": [{"fid": fid, "eid": eid, "rid": rid, "instance_id": instance_id}]},
        idempotency_key=f"{key}.r",
        bookmarks=bm,
    )
    return eid, fid, bm


def test_write_returns_a_bookmark(driver, instance_id):
    _, _, bm = _write_fact(driver, instance_id)
    assert list(bm.raw_values), "write produced no bookmark"


def test_causal_read_with_bookmark_sees_the_write(driver, instance_id):
    _, fid, bm = _write_fact(driver, instance_id)
    rows = client.read(driver, statements.FACT_BY_ID, {"fid": fid}, bookmarks=bm)
    assert len(rows) == 1
    assert rows[0]["predicate"] == "employer"
    assert rows[0]["value_text"] == "Acme"
    assert rows[0]["instance_id"] == instance_id


def test_subject_edge_traverses_to_the_entity(driver, instance_id):
    _, fid, bm = _write_fact(driver, instance_id)
    rows = client.read(driver, statements.SUBJECT_OF_FACT, {"fid": fid}, bookmarks=bm)
    assert rows == [{"key": "person:maya", "name": "Maya", "type": "person"}]


def test_rewriting_the_same_fact_is_a_no_op(driver, instance_id):
    """Deterministic ids make MERGE idempotent. Slice 04 hardens this."""
    eid_a, fid_a, _ = _write_fact(driver, instance_id)
    eid_b, fid_b, bm = _write_fact(driver, instance_id)
    assert (eid_a, fid_a) == (eid_b, fid_b)
    rows = client.read(driver, statements.FACT_BY_ID, {"fid": fid_a}, bookmarks=bm)
    assert len(rows) == 1, "duplicate Fact node after re-write"


def test_metrics_endpoint_parses():
    parsed = client.metrics()
    assert parsed["graph_runtime_ready"] == 1.0
    assert len(parsed) > 10
