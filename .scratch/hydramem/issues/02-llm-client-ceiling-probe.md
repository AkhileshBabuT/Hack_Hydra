# 02 — LLM client, context-ceiling probe, budget attestation

Status: ready-for-human

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

- [ ] All model access flows through one module with a documented one-line provider swap
- [ ] Every response is cached to disk by content hash and a rerun issues zero network calls
- [ ] Requests carry exponential backoff and pre-flight token counting
- [ ] The real context ceiling is measured by sending progressively larger requests and is recorded as a number in the budget document
- [ ] Account credit state and observed rate limit are recorded in the budget document
- [ ] The extraction model returns schema-valid JSON on a sample session with reasoning turned off
- [ ] The local emergency-fallback model is confirmed running on this machine
- [ ] The budget document attests zero spend

## Blocked by

None - can start immediately
