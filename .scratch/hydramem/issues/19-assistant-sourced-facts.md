# 19 — Assistant-sourced facts are visible but not rejected

Status: done — attributed, not discarded; accuracy neutral (within CI)

## Parent

`.scratch/hydramem/PRD.md`

## Why this exists

The extraction prompt forbids filing an assistant suggestion as a user fact.
The extractor does it anyway. Slice 06 spotted three such facts on `ec81a493`
and only because the model reflowed the quote — an exact copy of assistant text
is *grounded* by definition, so the grounding floor cannot see them at all.

Slice 10 carried the source turn's role onto the Fact node and confirmed it on
live data. Freshly ingested, `ec81a493` holds 8 facts and **3 of them are
assistant-sourced**, filed against turns 5 and 7 (the assistant's turns) as user
`prefers` facts:

```
  ed835c2fccfa83ff  user prefers = displaying items with complementary frames
      session answer_ed1982fc turn 7 (assistant)  <- ASSISTANT-SOURCED
      "Choose a harmonious frame: Select a frame for your poster that
       complements the style and era of your antique clock."
```

So the trace now shows them. Nothing rejects them.

## Why slice 10 did not reject them

The citation gate has nothing to fire on: the answer cites a fact that really is
in the retrieved set, so it is a **correctly cited wrong answer**. Rejecting on
role would be a new policy, not an enforcement of an existing one, and it would
trade recall for precision in a direction nobody has measured. 3 of 8 on one
instance is an observation, not a rate.

## What to build

1. **Measure the rate.** How many facts across the corpus carry
   `role == "assistant"`, and how many of those are wrong — an assistant turn
   can legitimately be the evidence span for a fact the user stated (the
   assistant repeating it back). Role alone does not prove the fact is bad.
2. **Then choose**, with the number in hand:
   - drop assistant-sourced facts at ingest (cheapest, loses any fact whose
     best evidence span the assistant happened to phrase),
   - down-weight them so the answering model sees them marked, or
   - refuse the answer when *every* cited fact is assistant-sourced, which is
     the narrowest rule and the only one that cannot lose a correct answer that
     also rests on a user-sourced fact.
3. Whichever is chosen, re-measure abstention precision — this is a gate that
   fires on content, and every gate that fires on content so far has been
   blunter than it looked.

## Cost

Requires `role` on the facts being measured, and `role` is null on every fact
written before slice 10 (48 of them). Wipe and re-ingest — the same wipe issue
17 needs.

## Acceptance criteria

- [ ] The assistant-sourced share is measured across more than one instance
- [ ] The share that is *actually wrong* is separated from the share that merely
      cites an assistant turn as evidence
- [ ] The chosen rule is enforced somewhere a model cannot talk past
- [ ] Abstention precision is re-measured after the rule lands

## Blocked by

10, 17

## Result

Closed structurally. **It did not buy the accuracy it was predicted to**, and
that is recorded here rather than smoothed over.

### What was wrong, and what the old rule cost

The extraction prompt said *"Assistant suggestions the user did not adopt are not
facts."* That rule was right about the failure it prevented and wrong about the
remedy: it **discarded** rather than **attributed**. Two costs, both measured in
slice 12:

- Advice the model kept anyway was filed under subject `user` — a *correctly
  cited wrong answer*, since the fact really is in the retrieved set and only the
  turn role says the user never claimed it.
- On a session that is "user asks a generic question, assistant explains" there
  are no facts about the user at all, so the extractor returned an empty list and
  **the graph stored nothing**. `single-session-assistant` scored **0 of 20**
  while full-context scored 20/20 and vector RAG 18/20.

### Attribution is derived from the turn, not requested from the model

First attempt was to ask: the prompt now tells the extractor to use subject
`assistant` for assistant-stated content. Re-ingested on a wiped node and
measured: **16 facts came back with `role=assistant` and zero
`person:assistant` entities existed.** The model emitted the content and stamped
`user` on it anyway — issue 19's original failure, unchanged, one prompt revision
later.

So `ingest.build_rows` decides it. A fact extracted from an assistant turn whose
subject is still the *default* self-form is retagged to `person:assistant`; a
subject the model named explicitly is untouched, so "you mentioned Maya moved"
still lands on Maya. The turn role is already on hand and is not an opinion.

After the wipe: **169 assistant-subject facts across 44 instances**, 18.9% of the
corpus, where there had been none.

### What it changed, measured at n=150

| stage | accuracy | abs precision | abs recall |
|---|---|---|---|
| gate 1 + gate 2 fixes | **0.4800** | 0.3043 | 0.9333 |
| + assistant attribution | 0.4533 | 0.3034 | 0.9000 |

**Accuracy went down 2.7 points.** The confidence intervals overlap heavily
([0.402, 0.559] against [0.376, 0.533]) so the difference is not established in
either direction, but it is certainly not the +9 points predicted from the
0-of-20 category.

Per category, against the same 150 questions: knowledge-update 18→15,
multi-session 16→13, single-session-user 16→15, single-session-assistant 2→1;
temporal-reasoning 11→14 and single-session-preference 9→10. The regression is
spread across categories, which points at the re-extraction (the prompt changed,
so every fact in the corpus is new) rather than at attribution itself.

### Why the target category did not move, diagnosed

`single-session-assistant` went 2/20 to 1/20. The extraction fix demonstrably
worked — instance `3e321797` held **zero** facts before and now holds 15 (12
assistant) and answers its question correctly. But 13 of the remaining 20 now
abstain `unknown_entity`, and probing the graph shows why:

| question asks about | graph holds |
|---|---|
| `mountain meditation` | `likes \| mindfulness techniques for individuals…` |
| `tanqueray` | `likes \| regular prayer and discipline` |
| `hardware`-aware modular training | `other \| 2`, `other \| high level of confidence` |

The extractor is capturing assistant content as **themes rather than named
specifics**, and these questions ask for exactly the specifics — a name, a
figure, a chapter title. Those abstentions are **honest**: the answer genuinely
is not in the graph. The remaining loss is extraction quality, not a gate, and it
needs another prompt-and-wipe cycle aimed at preserving named entities and
numbers.

### Kept anyway

The accuracy difference is inside the noise; the correctness difference is not.
Assistant advice no longer impersonates the user, which is the failure this issue
was opened for, and provenance is now derivable from structure rather than from a
flag a reader has to notice. Pinned live in `test_fixtures.py`
(`test_an_assistant_sourced_fact_is_attributed_not_absorbed`, plus a case
asserting "what do I prefer" now abstains `no_such_relation` rather than
returning the assistant's advice) and in `test_ingest.py`, including
`test_the_user_and_the_assistant_do_not_share_a_supersession_chain` — without
attribution an assistant suggestion could retract the user's own fact.

**Reverting is a live option** and costs one wipe cycle: the stage-2 graph scored
0.4800. Recorded so the choice is informed rather than forgotten.
