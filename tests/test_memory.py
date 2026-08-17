"""Slice 15: the mem0-compatible surface.

The model is stubbed and the graph is not, for the reason `test_fixtures.py`
gives: the thing under test is a write reaching a read, and a stubbed graph
would prove nothing about read-your-own-writes. Extraction is stubbed because
these tests are about the surface, not about the extractor.
"""

import time

import pytest
from conftest import make_fact as fact

from hydramem import extract, memory as mem


@pytest.fixture
def extracted(monkeypatch):
    """Replace the extraction call with a fixed fact list."""
    def install(facts):
        monkeypatch.setattr(extract, "extract_session", lambda *a, **k: list(facts))
    return install


@pytest.fixture
def store(driver, instance_id, extracted):
    extracted([fact(predicate="employer", value="Acme")])
    return mem.Memory(driver=driver, user_id=instance_id)


# --- pure ------------------------------------------------------------------


def test_the_same_messages_produce_the_same_session_key():
    """Content-derived, so re-adding an identical turn list is idempotent
    rather than writing a second session with a random id."""
    messages = [{"role": "user", "content": "I work at Acme."}]
    assert mem._session_key(messages) == mem._session_key(list(messages))
    assert mem._session_key(messages) != mem._session_key(
        [{"role": "user", "content": "I work at Globex."}]
    )


def test_a_bare_string_is_accepted_as_one_user_turn():
    """mem0 callers pass a string as often as a message list."""
    built = mem._to_instance([{"role": "user", "content": "hi"}], "u1", 0, 1_000)
    assert built.instance_id == "u1"
    assert built.sessions[0].turns[0].content == "hi"


# --- live: the guarantee ---------------------------------------------------


def test_add_returns_a_bookmark(store):
    written = store.add([{"role": "user", "content": "I work at Acme."}])
    assert written["bookmark"], "add must return the read-your-own-writes handle"
    assert written["facts_written"] >= 1


def test_a_memory_is_readable_immediately_via_its_bookmark(store):
    """Acceptance criterion: write a memory, read it straight back.

    No sleep, no poll, no retry. The bookmark is handed to the read and HydraDB
    refreshes until the write is visible before pinning, so this is a guarantee
    rather than a race that usually wins.
    """
    written = store.add([{"role": "user", "content": "I work at Acme."}])
    back = store.get_all(bookmarks=written["bookmark"])["results"]
    assert any("Acme" in row["memory"] for row in back), back


def test_history_returns_the_revisions_a_vector_store_would_have_lost(
    driver, instance_id, extracted
):
    """`employer` is functional, so the second value supersedes the first --
    and the first is still a node, which is the whole argument."""
    store = mem.Memory(driver=driver, user_id=instance_id)
    day = 86_400
    extracted([fact(predicate="employer", value="Acme")])
    store.add([{"role": "user", "content": "I work at Acme."}],
              timestamp=int(time.time()) - 100 * day)
    extracted([fact(predicate="employer", value="Globex")])
    store.add([{"role": "user", "content": "I moved to Globex."}],
              timestamp=int(time.time()) - 10 * day)

    revisions = store.history()["results"]
    assert revisions, "a functional predicate revised twice must leave a chain"
    assert ("Acme", "Globex") in [(r["old_memory"], r["new_memory"]) for r in revisions]


def test_delete_tombstones_rather_than_destroying(store):
    """The fact leaves `get_all` and keeps its node.

    Facts are immutable and deletion does not get an exception. A destructive
    delete would also be impractical here -- DETACH DELETE runs at ~0.3s a node
    -- but that is not why this is a tombstone.
    """
    written = store.add([{"role": "user", "content": "I work at Acme."}])
    target = store.get_all(bookmarks=written["bookmark"])["results"][0]

    assert store.delete(target["id"])["deleted"] == 1
    assert target["id"] not in {r["id"] for r in store.get_all()["results"]}

    # the node is still there, just not current
    rows = store._facts(store.user_id, store.bookmark)
    survivor = [r for r in rows if r["fact_id"] == target["id"]]
    assert survivor, "delete destroyed the node instead of tombstoning it"
    assert survivor[0]["status"] == "deleted"


def test_deleting_an_unknown_memory_says_so_rather_than_writing(store):
    store.add([{"role": "user", "content": "I work at Acme."}])
    assert store.delete("deadbeefdeadbeef") == {
        "deleted": 0, "reason": "not_in_graph", "id": "deadbeefdeadbeef",
    }


def test_delete_is_idempotent(store):
    """CLOSE_FACT is guarded on `valid_to`, so a second delete writes nothing."""
    written = store.add([{"role": "user", "content": "I work at Acme."}])
    target = store.get_all(bookmarks=written["bookmark"])["results"][0]
    assert store.delete(target["id"])["deleted"] == 1
    second = store.delete(target["id"])
    assert second["deleted"] == 0 and second["reason"] == "already_deleted"
