# 18 — Gate 1 reads a quoted title as an entity and loses the question

Status: done — closed by slice 12

## Parent

`.scratch/hydramem/PRD.md`

## Why this exists

Found by probing slice 10 against a freshly ingested instance, which is the
third time the lexical layer has been wrong in a way the unit suite could not
see (glue words in 07, calendar words in 08). The pattern is stable enough to
name: **a fixture exercising a recogniser is written by whoever wrote the
recogniser, so it tests the same blind spot twice.** Only a live instance finds
these.

Measured on `gpt4_2487a7cb` (temporal-reasoning, 2 sessions, 5 facts):

```
Q: Which event did I attend first, the 'Effective Time Management' workshop
   or the 'Data Analysis using Python' webinar?

Q cost 2 round trips over 0 facts
  1 entity: unknown_entity: effective time management
ABSTAIN unknown_entity: effective time management
gold:     'Data Analysis using Python' webinar
```

The graph holds the answer. One of the instance's five facts is
`visited | workshop on "Effective Time Management"`, and the webinar is in the
other session. `gates.mentions` sees a capitalised run, gate 1 looks for an
entity named `effective time management`, finds none, and the question is over
before gate 2 reads a row.

This is a **false abstention**, which CLAUDE.md's stated bias exists to avoid:
"an unrecognised question passes... a false abstention is an answer thrown away
with no way to notice." Gate 1 is the one gate that can violate that bias,
because it fires on the *presence* of an unrecognised name rather than on the
absence of a recognised one.

## Why it is not the calendar-word fix again

Slice 08 fixed the same *shape* by dropping a closed list of words
(`gates._CALENDAR`). That does not generalise here:

- A quoted title is an open set. There is no list to drop.
- The title is usually exactly what the question is *about*, so it cannot be
  discarded — it has to be recognised as a **value**, not as a name.
- The values live on facts (`value_text`), which gate 1 does not read: it sees
  only the entity list, and reading facts at gate 1 would cost the round trip
  the cascade is ordered to avoid.

## What to build

Measure before choosing. Candidates, cheapest first:

1. **A quoted span is a value, not a name.** `'...'` and `"..."` runs are
   dropped from `mentions` outright. One regex, no new round trip, and it is
   correct for every case in the corpus checked so far — a person's name is not
   quoted. Risk: a question quoting a person ("did 'Maya' call") passes gate 1
   instead of firing, which is the safe direction.
2. **Gate 1 falls through to the fact values it already has.** The facts are
   read lazily *after* gate 1 today, so this reorders the cascade and changes
   the round-trip budget for the losing case from 2 to 3. Measure whether the
   recall is worth the trip.
3. **Do nothing and accept the abstention.** Only defensible with a number:
   how many instances lose a question this way.

Whichever is chosen, the decision needs the number from (3) either way.

## Acceptance criteria

- [ ] The share of corpus questions lost to `unknown_entity` on a capitalised
      non-entity phrase is measured, not estimated
- [ ] `gpt4_2487a7cb`'s own question does not abstain `unknown_entity`
- [ ] Abstention precision is re-measured after the widening, since every fix
      here makes gate 1 fire less
- [ ] The fix is probed against a live instance, not only against fixtures —
      this is the third lexical bug the suite could not see

## Pinned by

`tests/test_gates.py::test_a_quoted_event_title_is_read_as_an_entity_and_loses_the_question`

## Blocked by

07

## Result

Closed by slice 12, and it turned out to be much larger than a quoted title.

### What it actually was

Gate 1's contract was "every name the question uses must exist as an `Entity`
node". That contract is wrong for this graph. `ingest.build_rows` creates an
Entity for a fact's **subject**, and for an **object whose `value_is_entity` flag
is set** — and for nothing else. Every other value stays a literal string on the
Fact. So the graph routinely holds

```
person:user | uses | Fitbit Charge 3
```

with no `thing:fitbit charge` node anywhere, and gate 1 abstained
`unknown_entity: fitbit charge` on a question the graph answers.

The quoted workshop title was one instance of this, not a category of its own.

### Measured, on the slice-12 run

Found by the thesis gate's own diagnostic mandate, not by the suite — the fourth
lexical bug in a row that the unit tests could not see, exactly as CLAUDE.md
predicts.

At n=13: **7 of 13 HydraMem abstentions were gate 1 firing on a name the graph
did hold.** At n=46: **28 of 46 answers were false abstentions, 19 of them gate
1.** A 61% false-abstention rate is not a gate being blunt, it is a gate being
wrong, and false abstention is the one direction `gates.BIAS` exists to rule out.

Probed against the live instances to separate the gate's fault from extraction's:

| mention | in a fact value | in a snippet | verdict |
|---|---|---|---|
| `fitbit charge` | yes (`uses \| Fitbit Charge 3`) | yes | gate's fault |
| `tokyo` | yes (`other \| Tokyo Tower`) | yes | gate's fault |
| `starbucks rewards` | yes (`goal \| ...Starbucks Rewards app`) | yes | gate's fault |
| `air fryer` | **no** | **no** | honest abstention |
| `miami` | **no** | **no** | honest abstention |
| `any` | no | yes | not a name at all |

### The fix

`gates.text_reader(all_facts)` returns `find_text(mention) -> entity key`. Gate 1
consults it **only when key, name and alias resolution have all failed**, and
resolves the mention to the **subject** of the fact whose `value_text` or
`snippet` contains it — which is who the question is about.

Snippets count, deliberately. Gate 1's job is to catch a question about something
the graph has never heard of, not to police how well the extractor slotted it.
The table above is the evidence that this does not make the gate vacuous: `air
fryer` and `miami` still abstain because they are genuinely absent.

Not a list of exceptions. A quoted title is an open set, and — measured while
writing the test — `_PROPER_RUN` does not even see a quoted span as one unit:
*"Data Analysis using Python"* arrives as two mentions, `data analysis` and
`python`, because the run breaks at the lowercase "using". Matching fragments
against stored text does not care; a quote-aware recogniser would have had to.

Also: `_SENTENCE_STARTERS` gained the capitalised words a chat message opens
with. `"Any tips?"` abstained `unknown_entity: any`. Dropping a candidate only
ever makes gate 1 pass more, which is the safe direction.

### What it costs

One round trip, and only on the path that was about to be lost anyway.

`find_text` closes over the same lazy, cached instance fact read that gates 2 and
3 and the answer already share, so **a question that passes costs nothing
extra**. A question that would have abstained at gate 1 now issues that read, and
moves from 2 round trips to 3. An instance with no entities at all still costs 2
— there is nothing to resolve and no read worth issuing.

Budget table updated in CLAUDE.md. Pinned in
`test_paths.py::test_a_question_lost_at_gate_1_costs_three_round_trips` and
`::test_an_empty_instance_still_costs_two`.

A pass by this route traces as `1 entity: pass (via stored text: fitbit charge)`
rather than plain `pass`, carried on `GateResult.via_text`. It is the blunter
resolution and it spent a read; the trace is an audit record of both.

### Verified on the live instances that failed

Re-ran the 19 gate-1 false abstentions through the fixed cascade, gates only, no
model call:

- **9 recovered outright.** `fitbit charge`, `tokyo`, `emily`, `rachel`,
  `united airlines`, `alex`, and three `any` questions.
- **3 now fail at gate 2** (`no_such_relation`) instead — a different and more
  defensible gate. Carried to the gate-2 work, not claimed as fixed here.
- **5 remain honest gate-1 abstentions** on names absent from the graph
  (`air fryer`, `miami`, `radiation amplified`, `spiritual life`,
  `mountain meditation`). The loss is upstream in extraction.
- **2 are `<empty graph>`** — instances that extracted **zero facts**. Not a gate
  problem at all; recorded as a new gap.

### Tests

`test_gates.py` — the original defect pin is kept and joined by
`test_the_quoted_title_resolves_once_gate_1_can_read_stored_text`, three
parametrised live payloads (`fitbit charge`, `starbucks rewards`, `tokyo`),
`test_a_name_the_graph_has_never_seen_still_abstains` (so the gate cannot be made
vacuous quietly), `test_a_capitalised_chat_opener_is_not_a_name`, and
`test_resolving_by_text_is_lazy` (which fails loudly if the fact read is ever
issued for a name that resolves as an entity).
