"""Slice 13: the vector RAG baseline, and the one arm most easily rigged.

Published work shows embedding choice alone can swing a RAG baseline by more
than ten points, which is enough to flip whether a memory system appears to win
at all. So the embedder is a strong modern retrieval model, it is named in the
results table rather than in a footnote, and the two places a weak baseline
usually hides are handled explicitly:

  - **BGE needs its query instruction.** `bge-*-en-v1.5` is trained with an
    asymmetric prefix on the query side and dropping it costs real retrieval
    points. Passages take no prefix. Getting this backwards is the classic way
    to accidentally publish a weak baseline.
  - **Chunks carry their date.** A quarter of this benchmark is temporal
    reasoning. A retriever handed undated turns cannot answer "which came
    first" no matter how good its embeddings are, and reporting that as a
    retrieval loss would be measuring the harness.

Everything runs locally on CPU, so this arm costs nothing but time. Embeddings
are cached per instance in `.cache/embeddings`, keyed by the model name, so
changing the embedder invalidates rather than silently reuses.
"""

import functools
import hashlib
import pathlib

# The named baseline. Strong, standard, and on MTEB retrieval leaderboards,
# which is what makes it defensible to a judge who knows the literature.
EMBED_MODEL = "BAAI/bge-base-en-v1.5"

# bge-*-en-v1.5 is trained asymmetrically: this goes on the query, never on the
# passages. Documented by the model authors; omitting it is a silent loss.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# Chunks handed to the model per question. Ten turns of a chat log is a normal
# RAG budget and keeps the baseline's prompt in the same order of magnitude as
# HydraMem's fact list, so the comparison is about *what* was retrieved.
TOP_K = 10

CACHE = pathlib.Path(".cache/embeddings")


@functools.lru_cache(maxsize=1)
def model():
    """Loaded once per process. ~400 MB of weights; not something to reload."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBED_MODEL, device="cpu")


def chunks(instance) -> list:
    """One chunk per turn, dated and attributed.

    Per turn rather than per session: a session is tens of thousands of
    characters and embedding it whole averages the question's answer away. The
    date header is not decoration -- see the module docstring.
    """
    import datetime as dt

    out = []
    for session in instance.sessions:
        day = dt.datetime.fromtimestamp(
            session.timestamp, dt.timezone.utc).strftime("%Y-%m-%d")
        for turn in session.turns:
            out.append(f"[{day}] session {session.idx}, turn {turn.idx} "
                       f"({turn.role}): {turn.content}")
    return out


def _cache_path(instance_id: str, texts: list) -> pathlib.Path:
    digest = hashlib.sha256(
        (EMBED_MODEL + "\x00".join(texts)).encode("utf-8")).hexdigest()[:16]
    return CACHE / f"{instance_id}.{digest}.npy"


def embed(texts: list, is_query: bool = False):
    """Normalized embeddings, so cosine similarity is a dot product."""
    if is_query:
        texts = [QUERY_PREFIX + t for t in texts]
    return model().encode(texts, normalize_embeddings=True,
                          batch_size=32, show_progress_bar=False)


def index(instance):
    """`(chunks, matrix)` for one instance, cached on disk by content and model."""
    import numpy as np

    texts = chunks(instance)
    if not texts:
        return texts, None
    path = _cache_path(instance.instance_id, texts)
    if path.exists():
        return texts, np.load(path)

    matrix = embed(texts)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, matrix)
    return texts, matrix


def search(instance, question: str, k: int = TOP_K) -> list:
    """Top-k chunks by cosine similarity, most relevant first.

    ponytail: a dot product against a few hundred rows, not FAISS. One instance
    is 40-200 turns; an index structure would be slower than the scan it
    replaces and would add a dependency to a baseline arm.
    """
    import numpy as np

    texts, matrix = index(instance)
    if matrix is None:
        return []
    scores = matrix @ embed([question], is_query=True)[0]
    top = np.argsort(-scores)[:k]
    return [texts[i] for i in top]
