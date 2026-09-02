# Configuration and Secrets

This document defines the Issue #34 baseline for platform-wide configuration and credentials. The design keeps ordinary configuration separate from sensitive material and makes both systems replaceable without coupling the platform to Vault, KMS, a cloud vendor, a worker runtime or a particular authentication implementation.

## Configuration precedence

The resolver applies the following deterministic low-to-high precedence order:

1. `platform_defaults`
2. `deployment`
3. `organization`
4. `team`
5. `project`
6. `workspace`
7. `agent`
8. `agent_team`
9. `adapter`
10. `provider`
11. `connector`
12. `task_run_override`

Layers at the same scope retain caller order. `task_run_override` is denied by default and may only modify paths explicitly listed by the active `ConfigurationSchema`.

Every effective leaf stores its complete source chain (`scope`, `source_id`, `source_type`). This allows safe diagnostics to explain which source won without exposing a secret value.

## Schema and validation

`ConfigurationSchema` contains:

- a schema version;
- a JSON Schema Draft 2020-12 document;
- declared secret paths;
- task/run override allowlists;
- per-path `live`, `reload` or `restart` requirements.

`ConfigurationResolver.resolve(...)` merges layers, rejects plaintext values on declared secret paths, serializes secret references safely and validates the complete effective configuration before returning it. Invalid configuration therefore fails before a dependent component is expected to start.

`environment_layer(...)` turns only explicitly mapped environment variables into a deployment layer. The platform does not pass the entire process environment to agents, tools or workers.

## Secret references

`SecretReference` is the canonical serializable identity for a credential. It includes only:

- secret ID;
- logical purpose;
- owner/scope;
- optional backend reference.

It never contains the secret value. `SecretMetadata` adds creation/update timestamps, state, expiry/rotation metadata and optional allowed-consumer/purpose hooks. Its serialization is also value-free.

Resolved values are returned as `SecretMaterial`. `repr(...)` and `str(...)` are always redacted; a caller must explicitly call `reveal()` inside the authorized operation boundary.

## SecretProvider boundary

`SecretProvider` is a platform-owned replaceable provider contract with operations for:

- create/store;
- resolve;
- rotate;
- revoke;
- delete;
- metadata lookup;
- normalized provider health.

`LocalSecretProvider` is the safe dependency-free reference backend. It keeps material only in process memory, does not persist plaintext to disk, replaces values on rotation and removes material on revocation. It is suitable for tests and minimal single-process/self-hosted baselines where secrets are injected at startup. Durable production backends can replace it without changing canonical references or callers.

The reference backend supports an audit hook. Audit events contain operation, secret ID, requesting consumer (for resolve), outcome and timestamp, never the resolved value.

## Least-privilege delivery context

Every resolve request carries `SecretAccessContext`, including:

- consumer/service/worker identity reference;
- project and workspace references;
- task and run references;
- action;
- capability reference;
- purpose;
- requested lifetime.

The reference backend already enforces optional consumer and purpose restrictions. Issue #15 can later supply the authoritative authorization decision without changing this request contract.

A successful resolve produces a bounded lease. The reference backend caps requested lifetime at one hour and also respects secret expiry. Callers must not cache resolved material beyond the lease.

## Rotation and revocation semantics

- Rotation atomically replaces the in-memory current value; previous material is not retained by the provider.
- New resolve requests receive the current value.
- Revocation removes material immediately and marks metadata revoked.
- Revoked or expired secrets cannot be resolved.
- Backend outages surface as canonical `ErrorCode.UNAVAILABLE` and are retryable.
- Missing references surface as `ErrorCode.NOT_FOUND`.
- Consumer/purpose denial or revoked/expired state surfaces as `ErrorCode.FORBIDDEN`.
- Downstream adapters may reinitialize after configuration/secret change; the core contract does not require those adapters to exist yet.

## Safe introspection

`EffectiveConfiguration.inspect()` exposes, per leaf:

- path;
- schema/validation status;
- source/precedence chain;
- reload/restart requirement;
- whether the path is a secret;
- whether a secret reference is configured;
- safe reference metadata instead of raw material.

Ordinary configuration display never calls `SecretProvider.resolve(...)`.

## Redaction

`redact_text`, `redact_value` and `redact_exception` are generic helpers intended for logs, events, traces, API responses, prompts, exports, evaluation artifacts and diagnostics. They redact caller-supplied known sensitive values and recursively mask values under common sensitive keys such as tokens, passwords, API keys, credentials and authorization fields.

Redaction is defense in depth; components must still avoid putting plaintext secret material into canonical objects or telemetry in the first place.

## Follow-up integration boundaries

This issue deliberately does not require workers, plugins, final authorization, authentication, connectors or observability to exist. Those systems should consume these contracts later:

- #10 model providers reference credentials through `SecretReference`;
- #12 tool/capability invocation requests scoped secret delivery;
- #14 worker dispatch receives only operation-required references/material;
- #15 authorizes `SecretAccessContext` resolution;
- #16 applies redaction helpers at telemetry boundaries;
- #20 plugins declare config/secret requirements;
- #32 exposes only safe configuration introspection;
- #36 stores service/API/worker credentials behind `SecretProvider`;
- #44 connectors use per-connection secret references.
