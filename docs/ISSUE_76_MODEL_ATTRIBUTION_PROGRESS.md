# Issue #76 — Canonical model attribution progress

This follow-up closes the model-attribution gap between the canonical #10 router/runtime and #76 usage accounting.

## Source of truth

`ModelRuntime` resolves the actual canonical `ModelConfiguration`, validates its selected provider and injects that configuration ID into the routed `ModelRequest` before invoking the provider. The progressive observability wrapper now copies only that routed canonical ID into `TelemetryContext.model_config_id`.

This means automatic routing is attributable without guessing from provider-native response fields. A provider-reported/native `model_ref` never becomes the canonical accounting model identity.

## Accounting result

The existing observability-to-accounting bridge already maps `TelemetryContext.model_config_id` and `model_provider_id` into `UsageScope`. Therefore model call count, duration and provider/token usage records now share the actually selected canonical model configuration and provider attribution.

## Boundaries

- Direct provider calls without a routed canonical `model_config_id` remain unattributed at the model-configuration level rather than fabricated.
- No Agent/AgentTeam execution attribution is inferred from #88 planning assignments.
- Provider-native model names remain non-canonical metadata.
- External monetary cost is not inferred from arbitrary provider usage or untyped `cost_metadata`.

Issue #76 remains open for dependency-bound Worker/Node, authorization/admission, organization/team, notification and external/browser/repository integrations.
