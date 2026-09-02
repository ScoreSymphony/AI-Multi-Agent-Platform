# Model System

Issue #10 defines the platform-owned model system as three separate responsibilities:

```text
ModelProvider -> ModelRegistry -> ModelRouter -> Agents / Tasks / Orchestrators
```

These responsibilities must remain replaceable and backend-neutral. LiteLLM, OpenAI-compatible endpoints, commercial APIs and future local runtimes are adapters or provider implementations, not the canonical model system.

## Canonical identity

`ModelConfiguration.config_id` is the stable platform-owned identity used by routing and future Agent/Task configuration.

Provider-native model names are deliberately not canonical IDs. They belong in namespaced `AdapterMetadata` owned by the relevant provider adapter. This allows an endpoint, native model name, runtime host or provider implementation to change without rewriting unrelated canonical resources.

## ModelRegistry

`ModelRegistry` owns runtime provider instances and canonical model configurations.

Baseline behavior:

- register, replace and unregister provider instances;
- register, update and unregister model configurations;
- exact canonical ID lookup;
- alias lookup;
- enable/disable with revision increment;
- deterministic duplicate and alias conflict handling;
- provider health refresh;
- effective model health from provider + configuration state;
- provider removal without deleting canonical model configurations.

Provider removal intentionally makes affected models unroutable while preserving their canonical configuration. Re-registering or replacing the provider instance restores availability without changing model IDs.

## ModelRouter

`DeterministicModelRouter` implements the first-pass routing policy.

Routing order:

1. honor a valid explicit canonical model assignment;
2. load enabled registry candidates;
3. exclude unavailable or unhealthy provider targets;
4. filter by context-window, tool-calling, structured-output, streaming and modality requirements;
5. enforce local-only or self-hosted-only policy;
6. sort candidates by descending configured priority and then canonical model ID;
7. return the selected provider ID and canonical model configuration ID;
8. fail with `ErrorCode.NO_COMPATIBLE_ROUTE` when no candidate qualifies.

The router exposes its decision reason and candidate IDs through namespaced adapter metadata. Selection is intentionally deterministic; opaque autonomous model selection is a non-goal for the baseline.

## Current request compatibility

Issue #5 introduced the baseline `ModelRequest.requirements` mapping before the richer #10 model system existed. The router currently parses the following canonical requirement keys into typed `RoutingRequirements`:

- `model_config_id`
- `min_context_window`
- `tool_calling`
- `structured_output`
- `streaming`
- `modalities`
- `local_only`
- `self_hosted_only`

This preserves the existing provider contract while #10 incrementally introduces richer canonical request/response structures.

## Remaining #10 work

This baseline does not close issue #10. Follow-up implementation still includes:

- persistent/reference registry storage;
- richer canonical message/content/tool/structured-output request and response types;
- local/self-hosted OpenAI-compatible reference provider;
- timeout/cancellation and provider error mapping for that adapter;
- provider model discovery where supported;
- configuration examples and secret-reference integration;
- Control Plane model/provider inventory endpoints once the relevant API foundation is available;
- reusable provider/registry/router contract tests beyond the initial issue-specific tests;
- final end-to-end test covering router -> registry -> local provider invocation.
