# Issue #11 Completion Audit

Issue: `Add LiteLLM as an optional model gateway adapter`

## Acceptance criteria

- [x] LiteLLM is enabled or disabled through provider configuration.
- [x] Library and OpenAI-compatible proxy modes are supported behind the platform-owned `ModelProvider` contract.
- [x] Core imports and tests do not require the optional LiteLLM dependency.
- [x] Canonical requests, responses, tool calls, structured output, usage and correlation metadata are translated at the adapter boundary.
- [x] Platform `ModelRouter` policy remains authoritative for aliases, capabilities and local/self-hosted restrictions.
- [x] LiteLLM and provider failures map to canonical `ErrorCode` values.
- [x] Provider health, capability and adapter metadata are exposed without credential values.
- [x] Local/self-hosted library and proxy examples are committed without secrets.
- [x] LiteLLM provenance, version pin, license boundary and replacement strategy are recorded in `upstream/litellm.yaml` and `docs/LITELLM_ADAPTER.md`.
- [x] The optional dependency is isolated in the `litellm` extra; the baseline deployment has no recurring paid AI/API requirement.

## Implementation boundary

`LiteLLMModelProvider` owns only translation and LiteLLM-specific configuration. Library mode calls LiteLLM's asynchronous completion function directly; proxy mode delegates the OpenAI-compatible HTTP surface to the existing provider. LiteLLM-native request and response objects do not cross canonical platform APIs, and LiteLLM Router fallbacks are not enabled by default.

Removing the extra, adapter and optional proxy leaves canonical Task, Run, Agent, model configuration, registry and router contracts intact.

## Regression coverage

Issue-specific tests cover:

- canonical message, tool-call and structured-response translation;
- correlation/task/run/agent metadata preservation;
- timeout, cancellation, credential and common provider-error mapping;
- missing optional dependency and disabled-adapter behavior;
- local OpenAI-compatible proxy health and generation;
- alias resolution and local/self-hosted routing policy preservation.

The normal CI suite remains the baseline validation path; the optional CI job checks that the pinned LiteLLM package exposes the required asynchronous completion entry point.
