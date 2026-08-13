# 13 — Vector RAG baseline and the per-category table

Status: ready-for-agent

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
