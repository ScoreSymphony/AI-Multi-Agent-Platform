# Configuration and Secrets

This document defines the Issue #34 baseline for platform-wide configuration and credentials. Ordinary configuration and sensitive material are separate systems. Neither boundary mandates Vault, KMS, a cloud provider, a worker runtime or a particular authentication implementation.

## Deterministic configuration hierarchy

Configuration resolves from lowest to highest precedence:

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

Layers at the same scope retain caller order. Task/run overrides are denied by default and may only touch paths explicitly allowlisted by the active schema. Every effective leaf records the complete source chain so the winning value is explainable.

## Schema and validation

`ConfigurationSchema` owns a schema version, JSON Schema Draft 2020-12 validation, secret paths, run-override allowlists and per-path `live`, `reload` or `restart` requirements.

`ConfigurationResolver.resolve(...)` merges layers, rejects plaintext values on declared secret paths and validates the complete effective configuration before returning it. Dependent components should start only from a successfully resolved configuration.

`environment_layer(...)` maps only explicitly approved environment variable names into configuration paths. The platform does not treat the complete process environment as an implicit configuration or credential payload.

## Canonical secret reference

Issue #43 already established `ai_multi_agent_platform.security.SecretReference`. Issue #34 reuses that type rather than defining a competing secret identity. It contains provider/backend identity, secret ID, owner/scope, optional version and non-secret metadata. Plaintext material is intentionally absent.

`SecretReference` defensively sanitizes its metadata through the central structured-redaction rules at construction time, recursively freezes the sanitized metadata, and returns a fresh redacted copy from `to_dict()`. This means known sensitive metadata keys such as tokens, credentials, passwords, API keys and private keys are not retained in plaintext by the canonical reference object and cannot be reintroduced by mutating the original input mapping or the stored metadata after construction.

`SecretMetadata` adds logical purpose, lifecycle timestamps, active/revoked state, expiry/rotation data and optional allowed-consumer/purpose hooks. Its safe serialization recursively redacts sensitive reference metadata.

## SecretProvider boundary

`SecretProvider` is the platform-owned replaceable boundary for:

- create/store;
- resolve for one explicit requesting context;
- rotate/update;
- revoke;
- delete;
- value-free metadata lookup;
- normalized provider health;
- audit hooks through the reference implementation.

`LocalSecretProvider` is the dependency-free reference backend. It stores material only in process memory, never writes plaintext to disk, replaces the current value on rotation and removes material on revocation. It supports tests and minimal self-hosted baselines. Durable or hardware-backed stores can replace it through the same contract.

## Least-privilege delivery

Every resolution carries `SecretAccessContext` with:

- consumer/service/worker identity reference;
- project and workspace references;
- task and run references;
- action;
- capability reference;
- purpose;
- requested lifetime.

The reference backend already enforces optional consumer and purpose restrictions. Final authorization can later be supplied by Issue #15 without redesigning this request shape.

Resolved material is wrapped in `SecretMaterial`. Normal string/repr output is always redacted; access requires the explicit `reveal()` call at the authorized operation boundary. The reference backend caps leases at one hour and also respects secret expiry.

## Rotation and revocation

- New authorized requests receive the current value after rotation.
- The local backend does not retain the prior plaintext value.
- Revocation removes material immediately and prevents further resolution.
- Expired material cannot be resolved.
- Missing references map to `ErrorCode.NOT_FOUND`.
- Backend outages map to retryable `ErrorCode.UNAVAILABLE`.
- Consumer/purpose/revocation denials map to `ErrorCode.FORBIDDEN`.
- Provider/reference mismatches map to `ErrorCode.INVALID_REQUEST`.

Downstream adapters may reinitialize when configuration or credential state changes; the core contract does not require those integrations to exist yet.

## Safe introspection

`EffectiveConfiguration.inspect()` exposes only metadata needed for diagnostics:

- path;
- schema version and validation status;
- source/precedence chain;
- reload/restart requirement;
- whether the field is secret-backed;
- whether a secret reference is configured;
- redacted reference metadata.

Ordinary introspection never invokes `SecretProvider.resolve(...)`.

## Redaction

Issue #34 extends the central security redaction boundary rather than introducing a duplicate implementation:

- `redact_sensitive(...)` recursively masks common sensitive mapping keys and safely serializes `SecretReference` metadata;
- `redact_text(...)` removes explicitly known sensitive values from free-text surfaces;
- `redact_exception(...)` applies the same treatment to exception messages.

These helpers are intended for logs, events, traces, API responses, prompts, exports, evaluation artifacts and diagnostics. Redaction is defense in depth; components must still avoid placing raw secret material into canonical state or telemetry.

## Follow-up integration points

The core contract intentionally does not require workers, plugins, final authorization, authentication, connectors or observability. Later integrations should consume this boundary:

- #10 model providers use secret references for provider credentials;
- #12 tool/capability invocation requests scoped delivery;
- #14 worker dispatch receives only operation-required credential material;
- #15 authorizes resolution decisions;
- #16 applies central redaction at telemetry boundaries;
- #20 plugins declare configuration and secret requirements;
- #32 exposes only safe configuration metadata;
- #36 stores service/API/worker credentials through the secret boundary;
- #44 connectors use per-connection secret references.
