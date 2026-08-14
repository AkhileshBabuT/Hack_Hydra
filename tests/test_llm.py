"""LLM layer: caching, token counting, overflow routing, provider swap.

None of these touch the network. The cache is what keeps reruns free, and a
cache that silently misses is a budget bug, so it is tested directly.
"""

import pytest

from hydramem import llm


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "llm")


def test_cache_roundtrip():
    key = llm._cache_key("m", [{"role": "user", "content": "hi"}])
    assert llm.cache_get(key) is None
    llm.cache_put(key, {"text": "hello", "usage": {}})
    assert llm.cache_get(key)["text"] == "hello"


def test_cache_key_is_content_addressed():
    a = llm._cache_key("m", [{"role": "user", "content": "hi"}])
    b = llm._cache_key("m", [{"role": "user", "content": "hi"}])
    c = llm._cache_key("m", [{"role": "user", "content": "bye"}])
    assert a == b and a != c


def test_cache_key_separates_models():
    msgs = [{"role": "user", "content": "hi"}]
    assert llm._cache_key("model-a", msgs) != llm._cache_key("model-b", msgs)


def test_cache_hit_short_circuits_the_network(monkeypatch):
    """A cached call must not construct a client at all."""
    def explode(*a, **kw):
        raise AssertionError("network touched on a cache hit")

    monkeypatch.setattr(llm, "_client", explode)
    msgs = [{"role": "user", "content": "hi"}]
    key = llm._cache_key("m", msgs, temperature=0.0, max_tokens=2048,
                         reasoning=False, response_format=None)
    llm.cache_put(key, {"text": "cached", "usage": {}, "model": "m"})

    out = llm.complete(msgs, model="m")
    assert out["text"] == "cached" and out["cached"] is True


def test_token_counting_is_monotonic():
    assert llm.count_tokens("") == 0
    assert llm.count_tokens("hello") < llm.count_tokens("hello world " * 50)


def test_overflow_raises_when_ceiling_measured(monkeypatch):
    monkeypatch.setattr(llm, "measured_ceiling", lambda: 10)
    monkeypatch.setattr(llm, "_client", lambda *a, **k: pytest.fail("should not call"))
    with pytest.raises(llm.ContextOverflow):
        llm.complete([{"role": "user", "content": "word " * 500}], model="m")


def test_no_ceiling_means_no_preflight_block(monkeypatch):
    """Unmeasured ceiling must not silently default to a spec-sheet number."""
    monkeypatch.setattr(llm, "measured_ceiling", lambda: None)
    calls = []
    monkeypatch.setattr(llm, "_client", lambda *a, **k: calls.append(1) or _Boom())
    with pytest.raises(RuntimeError):
        llm.complete([{"role": "user", "content": "word " * 500}], model="m", retries=1)
    assert calls, "pre-flight blocked a request with no measured ceiling"


class _Boom:
    class chat:
        class completions:
            @staticmethod
            def create(**kw):
                raise RuntimeError("no network in tests")


def test_provider_registry_has_documented_fallbacks():
    assert set(llm.PROVIDERS) >= {"nim", "gemini", "ollama"}
    assert llm.PROVIDERS["ollama"]["api_key_env"] is None


def test_answering_model_is_shared_across_arms():
    """The methodological rule: only the retrieval layer may differ."""
    assert llm.ANSWER_MODEL and llm.ANSWER_MODEL != llm.EXTRACT_MODEL


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="NVIDIA_API_KEY"):
        llm._client("nim")
