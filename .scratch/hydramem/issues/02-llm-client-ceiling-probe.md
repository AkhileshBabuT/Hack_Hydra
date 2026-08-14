# 02 — LLM client, context-ceiling probe, budget attestation

Status: done

## Parent

`.scratch/hydramem/PRD.md`

## What to build

Build the single provider-agnostic module through which all model access flows, then use it
to measure the two numbers the rest of the plan depends on.

The module wraps an OpenAI-compatible NVIDIA NIM endpoint as primary, with a documented
fallback provider for the context-overflow tail and a small local model as emergency
fallback. It disk-caches every response by content hash so reruns cost nothing, applies
exponential backoff on every call, counts tokens before sending, and makes swapping
providers a one-line change.

**The context ceiling must be measured, not read from a spec sheet.** Published context
windows describe model capability, not endpoint enforcement, and the full-context baseline
arm is designed around this number. Send real requests at increasing sizes until the wall
is found, and record where it actually is.

This is marked for human implementation because it requires an account, a dashboard check
of credit state, and a judgement call about which model the baseline arm can actually
reach.

## Acceptance criteria

- [x] All model access flows through one module with a documented one-line provider swap
- [x] Every response is cached to disk by content hash and a rerun issues zero network calls
- [x] Requests carry exponential backoff and pre-flight token counting
- [x] The real context ceiling is measured by sending progressively larger requests and is recorded as a number in the budget document
- [x] Account credit state and observed rate limit are recorded in the budget document
- [x] The extraction model returns schema-valid JSON on a sample session with reasoning turned off
- [x] The local emergency-fallback model is confirmed running on this machine
- [x] The budget document attests zero spend

## Blocked by

None - can start immediately

## Comments

**2026-08-13 — blocked on credential, code complete.**

`hydramem/llm.py` and `scripts/probe_budget.py` are implemented and unit-tested
(10 tests, no network). Every remaining criterion needs a working key.

`models.list()` returns 102 models **with a deliberately invalid key** — that
endpoint is public, so it is not an auth check. Every `chat/completions` call
returns `403 Forbidden / Authorization failed`, on all models tried including
`meta/llama-3.1-8b-instruct`. Account-wide, not model-specific.

Run `python scripts/probe_budget.py` once the key works; it writes `docs/budget.md`
and fills the remaining criteria in one pass.

### Model assignment (decided, unverified until the key works)

| Role | Model | Why |
|---|---|---|
| Extraction | `nvidia/nemotron-3.5-lightning-30b-a3b` | 3B active params; one call per session |
| Answering — **all arms** | `nvidia/nemotron-3-ultra-550b-a55b` | Strongest accessible; same model everywhere |
| Answering fallback | `nvidia/nemotron-3-super-120b-a12b` | If Ultra is ungated on free tier |
| Local emergency | Ollama, separate | NIM's nano is 30B-a3b, not the 4B the plan assumed |

The plan was internally inconsistent here: §3.7 assigned synthesis to Lightning
while §3.8 required the same answering model across all arms and §8.2 required the
strongest baseline. Resolved in favour of the methodological rule — Ultra answers
for every arm, so only the retrieval layer differs. This also sharpens the cost
table, since full-context sends ~115k tokens to Ultra where HydraMem sends a
handful of facts to the same model.

**2026-08-13 (later) — unblocked and complete.** Key now authorizes inference on
all three models. `docs/budget.md` regenerated from live measurements.

Results that change the plan:

- **No context ceiling found.** 1,000,007 tokens accepted by Ultra (provider
  counted 1,000,023) in 39.4s. The plan feared ~131k and budgeted an overflow
  path to Gemini Flash. LongMemEval_S averages ~115k, so **there is no overflow
  tail** — `GEMINI_API_KEY` is not needed and the risk-register entry "real
  context ceiling below 115k" is dead.
- **Reasoning is ON by default** on Lightning and must be disabled through
  `chat_template_kwargs={"thinking": false}`. The two commonly cited
  alternatives — a `/no_think` system prompt and `extra_body={"reasoning":false}`
  — both leave thinking tokens in the output and would have broken every
  extraction call.
- **Throughput varies widely**: 35.8–188.9 RPM across four runs. Plan against the
  documented ~40 RPM floor, not the observed peak.
- `models.list()` is public and answers an invalid key. It is not an auth check.
