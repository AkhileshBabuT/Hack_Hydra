"""Provider-agnostic LLM access: caching, backoff, pre-flight token counting.

Every model call in the project goes through here, so swapping providers is a
one-line change (`PROVIDER`) and the disk cache guarantees a rerun costs zero
network calls -- which is what keeps the project at $0.

Model assignment (see docs/budget.md for how these were chosen):

  EXTRACT  high volume, one call per session, reasoning OFF.
  ANSWER   one call per question, used by *every* evaluation arm.

The single-answering-model rule is methodological, not incidental: full-context,
vector RAG and HydraMem must differ only in their retrieval layer, or the
comparison measures model quality instead of retrieval quality.
"""

import hashlib
import json
import os
import pathlib
import time

import tiktoken
from dotenv import load_dotenv

load_dotenv()

# --- model registry -------------------------------------------------------

EXTRACT_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"   # 30B total / 3B active
ANSWER_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"        # every arm uses this
ANSWER_FALLBACK = "nvidia/nemotron-3-super-120b-a12b"     # if Ultra is ungated

PROVIDER = os.environ.get("HYDRAMEM_PROVIDER", "nim")

PROVIDERS = {
    "nim": {
        "base_url": os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        "api_key_env": "NVIDIA_API_KEY",
    },
    # Documented fallback for the context-overflow tail and for total NIM outage.
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key_env": "GEMINI_API_KEY",
    },
    # Emergency local fallback. No key, no network.
    "ollama": {
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key_env": None,
    },
}

CACHE_DIR = pathlib.Path(os.environ.get("HYDRAMEM_CACHE", ".cache/llm"))

# tiktoken has no Nemotron encoding. cl100k_base is a stand-in for *routing*
# decisions only -- it is close enough to decide "does this exceed the ceiling",
# and every number that gets reported comes from the API's own usage field.
_ENC = tiktoken.get_encoding("cl100k_base")


# 429 rate limit, 408/409 transport races, 404 transient routing (measured).
RETRYABLE_4XX = frozenset([404, 408, 409, 429])


class ContextOverflow(RuntimeError):
    """Request exceeds the measured ceiling. Route to the overflow provider."""


def count_tokens(text: str) -> int:
    return len(_ENC.encode(text))


def measured_ceiling() -> int | None:
    """The ceiling probed in slice 02, or None if it has not been measured.

    Deliberately not defaulted to a spec-sheet number. A published context
    window describes model capability, not what the endpoint enforces, and the
    full-context baseline arm is designed around this value.
    """
    raw = os.environ.get("HYDRAMEM_CONTEXT_CEILING")
    return int(raw) if raw else None


# --- cache ----------------------------------------------------------------

def _cache_key(model: str, messages: list, **kw) -> str:
    payload = json.dumps(
        {"model": model, "messages": messages, **kw}, sort_keys=True, default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_path(key: str) -> pathlib.Path:
    return CACHE_DIR / key[:2] / f"{key}.json"


def cache_get(key: str):
    path = _cache_path(key)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def cache_put(key: str, value: dict) -> None:
    path = _cache_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


# --- client ---------------------------------------------------------------

def _client(provider: str = None):
    import openai

    spec = PROVIDERS[provider or PROVIDER]
    key_env = spec["api_key_env"]
    api_key = os.environ.get(key_env, "") if key_env else "not-needed"
    if key_env and not api_key:
        raise RuntimeError(f"{key_env} is not set (see .env)")
    return openai.OpenAI(api_key=api_key, base_url=spec["base_url"])


def complete(
    messages: list,
    model: str = None,
    provider: str = None,
    reasoning: bool = False,
    max_tokens: int = 2048,
    temperature: float = 0.0,
    response_format: dict = None,
    retries: int = 5,
) -> dict:
    """One chat completion, cached by content hash.

    Returns {"text": str, "usage": {...}, "cached": bool}.

    `reasoning=False` is the default and the single highest-value setting in the
    pipeline: reasoning models emit thinking tokens that break strict JSON
    parsing, which is fatal for extraction.
    """
    model = model or EXTRACT_MODEL

    extra_body = {}
    if not reasoning:
        # Nemotron 3 exposes reasoning on/off through the chat template.
        extra_body["chat_template_kwargs"] = {"thinking": False}

    key = _cache_key(
        model, messages, temperature=temperature, max_tokens=max_tokens,
        reasoning=reasoning, response_format=response_format,
    )
    hit = cache_get(key)
    if hit is not None:
        return {**hit, "cached": True}

    ceiling = measured_ceiling()
    if ceiling is not None:
        size = sum(count_tokens(m.get("content", "")) for m in messages)
        if size > ceiling:
            raise ContextOverflow(f"{size} tokens exceeds measured ceiling {ceiling}")

    kwargs = dict(model=model, messages=messages, temperature=temperature,
                  max_tokens=max_tokens)
    if response_format:
        kwargs["response_format"] = response_format
    if extra_body:
        kwargs["extra_body"] = extra_body

    client = _client(provider)
    delay = 1.0
    last = None
    for attempt in range(retries):
        try:
            response = client.chat.completions.create(**kwargs)
            usage = response.usage
            result = {
                "text": response.choices[0].message.content or "",
                "usage": {
                    "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                    "completion_tokens": getattr(usage, "completion_tokens", 0),
                },
                "model": model,
            }
            cache_put(key, result)
            return {**result, "cached": False}
        except Exception as exc:  # noqa: BLE001 - provider SDKs raise varied types
            last = exc
            status = getattr(exc, "status_code", None)
            # A 4xx that names a client mistake will not fix itself by waiting.
            # 404 is not one of them here: NIM returns it transiently mid-run
            # for a model whose id is valid and which answers again seconds
            # later, and an unattended multi-hour ingest must survive that.
            if status and 400 <= status < 500 and status not in RETRYABLE_4XX:
                raise
            if attempt == retries - 1:
                break
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"{model} failed after {retries} attempts: {last}")


def complete_json(messages: list, schema: type, **kw) -> object:
    """Completion parsed into a pydantic model. Raises on invalid JSON."""
    raw = complete(messages, **kw)["text"]
    text = raw.strip()
    # Some deployments still wrap JSON in a fenced block despite reasoning off.
    if text.startswith("```"):
        text = text.split("```")[1].removeprefix("json").strip()
    return schema.model_validate_json(text)
