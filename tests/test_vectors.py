"""Slice 13: the two ways a vector baseline gets rigged by accident.

The model itself is not exercised here. Its retrieval quality is what the eval
arm measures, and a unit test asserting that BGE ranks one toy sentence above
another would test the model rather than this code.

What *is* tested is everything that can silently weaken the baseline while the
arm still runs and still produces a plausible number: dropping BGE's query
instruction, and handing the retriever undated turns on a benchmark that is a
quarter temporal reasoning. Both are invisible at runtime and both would show up
as "vector RAG scored badly", which is the wrong conclusion.
"""

import pytest

from hydramem import corpus, vectors


def instance(n_sessions=2, n_turns=3):
    sessions = tuple(
        corpus.Session(
            session_id=f"s{i}", idx=i,
            timestamp=corpus.parse_date(f"202{i}/03/15 (Wed) 10:00"),
            turns=tuple(corpus.Turn(t, "user" if t % 2 == 0 else "assistant",
                                    f"content {i}-{t}", False)
                        for t in range(n_turns)),
        )
        for i in range(n_sessions)
    )
    return corpus.Instance(instance_id="v", question_type="temporal-reasoning",
                           question="q", answer="a", asked_at=0, sessions=sessions,
                           answer_session_ids=())


def test_a_chunk_carries_its_date():
    """A quarter of this benchmark is temporal reasoning. A retriever handed
    undated turns cannot answer "which came first" however good its embeddings
    are, and reporting that as a retrieval loss measures the harness."""
    for chunk in vectors.chunks(instance()):
        assert "202" in chunk.split("]")[0], chunk


def test_a_chunk_carries_who_said_it():
    found = vectors.chunks(instance())
    assert any("(user)" in c for c in found)
    assert any("(assistant)" in c for c in found)


def test_chunking_is_per_turn_not_per_session():
    """A session is tens of thousands of characters; embedding it whole averages
    the answer away."""
    assert len(vectors.chunks(instance(n_sessions=2, n_turns=3))) == 6


def test_the_query_gets_bge_s_instruction_and_the_passages_do_not(monkeypatch):
    """bge-*-en-v1.5 is trained asymmetrically. Putting the prefix on both sides,
    or on neither, is the classic way to publish a weak baseline by accident."""
    seen = []

    class Fake:
        def encode(self, texts, **kw):
            seen.append(list(texts))
            return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(vectors, "model", lambda: Fake())
    vectors.embed(["a passage"])
    vectors.embed(["a question"], is_query=True)
    assert seen[0] == ["a passage"]
    assert seen[1] == [vectors.QUERY_PREFIX + "a question"]


def test_the_cache_key_changes_with_the_model():
    """Swapping the embedder must invalidate, never silently reuse: the whole
    point of naming it in the table is that it changes the numbers."""
    texts = ["a", "b"]
    before = vectors._cache_path("i", texts)
    monkey = vectors.EMBED_MODEL
    try:
        vectors.EMBED_MODEL = "some/other-model"
        assert vectors._cache_path("i", texts) != before
    finally:
        vectors.EMBED_MODEL = monkey


def test_search_returns_the_nearest_chunks_first(monkeypatch, tmp_path):
    numpy = pytest.importorskip("numpy")
    inst = instance(n_sessions=1, n_turns=4)

    # Chunk i sits at distance i from the query, so the expected order is known
    # without asking a real model anything.
    class Fake:
        def encode(self, texts, normalize_embeddings=True, **kw):
            out = []
            for text in texts:
                out.append([0.0, 1.0] if text.startswith(vectors.QUERY_PREFIX)
                           else [1.0, float(text.split("turn ")[1][0])])
            return numpy.array(out, dtype="float32")

    monkeypatch.setattr(vectors, "model", lambda: Fake())
    monkeypatch.setattr(vectors, "CACHE", tmp_path)
    found = vectors.search(inst, "which turn", k=2)
    assert "turn 3" in found[0] and "turn 2" in found[1]


def test_an_instance_with_no_sessions_retrieves_nothing(monkeypatch):
    monkeypatch.setattr(vectors, "model", lambda: pytest.fail("no model needed"))
    empty = corpus.Instance(instance_id="e", question_type="t", question="q",
                            answer="a", asked_at=0, sessions=(), answer_session_ids=())
    assert vectors.search(empty, "q") == []
