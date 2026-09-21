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
4. filter by context-window, tool-calling, structured-output, streaming, modality and reasoning requirements;
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
- `reasoning`
- `local_only`
- `self_hosted_only`

This keeps the stable #5 request envelope compatible with the richer #10 canonical model request/response layer.

## Durable routing profiles and default policy

The platform owns first-class durable, reusable and versioned Model Routing Profiles in addition to request-time `RoutingRequirements`. The canonical `model-routing-profiles` collection and `model-routing-profile.create|version|enable|disable` commands preserve immutable revisions and provider-neutral routing intent; Project scope is fixed after profile creation.

Agents and other canonical configuration surfaces may bind an exact routing-profile reference without turning provider-private model names or gateway policy into platform identity. The Models Web surface exposes the same canonical profiles through the Control Plane rather than maintaining client-local routing configuration.

Provider/runtime health remains live registry/runtime state and must not become durable routing-profile identity. A routing profile constrains canonical selection policy; it does not freeze or replace live provider availability.

## Control Plane inventory

The versioned Control Plane exposes the canonical model inventory without turning provider-native APIs into northbound contracts:

- `GET /api/v1/model-providers`
- `GET /api/v1/model-providers/{provider_id}`
- `POST /api/v1/model-providers/{provider_id}:enable`
- `POST /api/v1/model-providers/{provider_id}:disable`
- `POST /api/v1/model-providers/{provider_id}:refresh-health`
- `GET /api/v1/models`
- `GET /api/v1/models/{model_id_or_alias}`
- `POST /api/v1/models/{model_id_or_alias}:enable`
- `POST /api/v1/models/{model_id_or_alias}:disable`

Inventory mutations require the Control Plane idempotency key. Provider construction and provider-native configuration remain adapter/bootstrap responsibilities rather than generic HTTP object creation.

## Current model and routing surface

The model surface includes the provider/registry/router foundation together with durable Model Routing Profiles: distinct provider, registry and router contracts; stable canonical model configuration IDs; persistent reference storage; deterministic capability/location/health routing; rich canonical request/response types; local OpenAI-compatible execution; provider-neutral streaming with fallback; timeout/cancellation/error normalization; model/provider configuration examples; Control Plane inventory; and immutable routing-profile revisions.

The baseline remains local-first and does not require any recurring paid AI/API service. Optional gateways and additional commercial or local providers remain replaceable adapters.
