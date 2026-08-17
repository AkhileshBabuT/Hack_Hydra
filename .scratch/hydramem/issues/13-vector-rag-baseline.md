# 13 — Vector RAG baseline and the per-category table

Status: done

## Parent

`.scratch/hydramem/PRD.md`

## What to build

The second baseline arm, and the one most easily rigged by accident.

Use a strong modern embedding model. Published work shows embedding choice alone can swing a
RAG baseline by more than ten points, which is enough to flip whether a memory system appears
to win at all. Using a weak embedder here would be rigging the comparison, and a judge who
knows the literature will notice. Name the embedding model explicitly in the results table.

Embeddings run locally on CPU, so this arm costs nothing.

The mem0 arm is **not** reproduced — its published figure is cited as context only, clearly
labelled as measured with a different answering model, embedder and judge. The README must
say plainly that it was not reproduced in-harness. Published figures from the literature may
be cited alongside but never claimed as beaten, because they were not measured under these
conditions.

## Acceptance criteria

- [ ] A vector RAG baseline runs the same stratified slice using the same answering model
- [ ] The embedding model is named explicitly in the results table
- [ ] The full per-category table across all arms is committed as a CSV
- [ ] The mem0 published figure is cited as context and explicitly labelled as not reproduced in-harness
- [ ] No claim anywhere asserts beating a published figure measured under different conditions
- [ ] Every number in the README regenerates from a single make target

## Blocked by

12

## Result

Done. `hydramem/vectors.py`, wired as `eval.run_vector_rag`. Ran the same 150
stratified questions with the same answering model; only the retrieval layer
differs.

**Embedding model: `BAAI/bge-base-en-v1.5`**, named in every generated table
(`embedder` column on the per-category CSV and a bullet in `oracle-results.md`).
Local CPU via sentence-transformers, so the arm costs nothing but time. Kept out
of the core install as a `[eval]` extra because it pulls torch (~2 GB), and
`vectors.py` imports it inside the function that loads the model so the suite
runs without it.

### It is a strong baseline, and it beat HydraMem on accuracy

| arm | acc | abs precision | abs recall | tokens/q |
|---|---|---|---|---|
| vector_rag | **0.6467** | 0.4154 | 0.9000 | 2,494 |
| full_context | 0.6200 | 0.4211 | 0.8000 | 5,540 |
| hydramem | 0.4333 | 0.2800 | **0.9333** | **605** |

Vector RAG is the **most accurate arm in the harness**. That is the honest
result and it is reported as the headline of this issue rather than buried:
this baseline was not weakened to make the memory layer look good.

### The two places a RAG baseline gets rigged, and what was done instead

- **BGE's query instruction.** `bge-*-en-v1.5` is trained asymmetrically; the
  prefix goes on the query and never on the passages. Omitting it is a silent
  retrieval loss that would show up as "vector RAG scored badly". Pinned in
  `test_vectors.py::test_the_query_gets_bge_s_instruction_and_the_passages_do_not`.
- **Undated chunks.** A quarter of this benchmark is temporal reasoning. Chunks
  are one turn each, carrying the session date and the speaker role, so the
  retriever can answer "which came first". Pinned.

`TOP_K = 10` turns, which keeps the prompt in the same order of magnitude as
HydraMem's fact list so the comparison is about *what* was retrieved. Retrieval
is a dot product over a few hundred normalized rows — no FAISS, which would be
slower than the scan it replaces at this size and would add a dependency to a
baseline arm. Embeddings cached per instance keyed by model name, so changing
the embedder invalidates rather than silently reuses.

### mem0

Not reproduced. `cost_table.MEM0_NOTE` is emitted into every generated results
document and says so explicitly: its published LongMemEval figure was measured
with a different answering model, a different embedder and a different judge, so
it is not comparable to anything here. No claim in this repository asserts
beating it, or any other published figure.

### One number that is not flattering and stays

Vector RAG's **p95 latency is 97,734 ms** — the worst of any arm, against
HydraMem's 2,358 ms. Median is 23,718 ms. The tail is the answering model on a
2.5k-token prompt, not the embedder, which runs in ~90 ms cached.

## Acceptance criteria

- [x] A vector RAG baseline runs the same stratified slice using the same answering model
- [x] The embedding model is named explicitly in the results table
- [x] The full per-category table across all arms is committed as a CSV
- [x] The mem0 published figure is cited as context and explicitly labelled as not reproduced in-harness
- [x] No claim anywhere asserts beating a published figure measured under different conditions
- [x] Every number in the README regenerates from a single make target (`make eval`)
