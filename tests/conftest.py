import pytest

from hydramem import client, corpus, extract


@pytest.fixture(scope="session")
def driver():
    d = client.connect()
    yield d
    d.close()


@pytest.fixture
def instance_id(request):
    """A unique tenant partition per test, so tests never see each other."""
    return f"test-{request.node.name}"


# --- builders shared by the ingest and chain tests -------------------------
#
# Plain functions, not fixtures: the tests compose several of them per case and
# parametrize over them, which fixtures make clumsy.

def make_session(idx, session_id, timestamp, n_turns=4):
    return corpus.Session(
        session_id=session_id, idx=idx, timestamp=timestamp,
        turns=tuple(
            corpus.Turn(i, "user" if i % 2 == 0 else "assistant", f"turn {i} text", False)
            for i in range(n_turns)
        ),
    )


def make_instance(sessions, instance_id="test-instance"):
    return corpus.Instance(
        instance_id=instance_id, question_type="knowledge-update",
        question="where do I work?", answer="Acme", asked_at=2_000_000,
        sessions=tuple(sessions), answer_session_ids=(),
    )


def make_fact(subject="user", predicate="employer", value="Acme", **kw):
    return extract.ExtractedFact(subject=subject, predicate=predicate, value=value, **kw)
