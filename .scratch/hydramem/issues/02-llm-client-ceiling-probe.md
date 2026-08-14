# 02 — LLM client, context-ceiling probe, budget attestation

Status: blocked — NIM key not authorized for inference

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
- [ ] The real context ceiling is measured by sending progressively larger requests and is recorded as a number in the budget document
- [ ] Account credit state and observed rate limit are recorded in the budget document
- [ ] The extraction model returns schema-valid JSON on a sample session with reasoning turned off
- [ ] The local emergency-fallback model is confirmed running on this machine
- [ ] The budget document attests zero spend

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
