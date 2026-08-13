# 12 — Thesis gate — stratified slice, eval harness, full-context baseline

Status: ready-for-human

## Parent

`.scratch/hydramem/PRD.md`

## What to build

**This is the highest-stakes slice in the plan and it runs early on purpose.** If the central
claim is wrong, this must surface with days left to react, not hours.

Build a category-stratified slice of roughly one hundred and fifty questions. Stratified, not
random: the abstention, temporal-reasoning and knowledge-update buckets carry the entire
thesis and each needs enough cases to say anything at all. Record the count per category in
every table produced.

Run the strongest full-context baseline the free tier permits, with pre-flight token counting
and the overflow tail routed to the fallback long-context provider. The most likely attack on
this submission is that it only beat a weak baseline, so use the strongest one available. Use
the **same answering model** for every arm — only the retrieval layer may differ.

Evaluation pins strong consistency so scores are reproducible.

Marked for human implementation because it ends in a go or no-go judgement. If HydraMem does
not beat the baseline on abstention and knowledge-update, everything stops: no UI, no README,
no new features. Diagnose in this order — predicate gate too permissive, then entity gate
missing aliases, then citation check failing to downgrade. Those three account for nearly all
abstention-precision failures.

## Acceptance criteria

- [ ] A category-stratified slice of roughly one hundred and fifty questions exists with counts recorded per category
- [ ] Both arms run the slice unattended end to end
- [ ] The same answering model is used across arms and this is stated in the results
- [ ] Questions exceeding the measured context ceiling are counted and routed to the fallback provider, and the split is recorded
- [ ] A per-category results table is committed as a CSV
- [ ] Abstention precision and recall are reported per category alongside accuracy
- [ ] Evaluation runs pin strong consistency
- [ ] A go or no-go decision against the thesis is recorded, with diagnosis if it failed

## Blocked by

11
