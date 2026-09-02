# ADR 0004: Configuration and secret boundaries

- Status: Accepted
- Date: 2026-09-02
- Issue: #34

## Context

The platform needs deterministic configuration resolution and credential delivery across adapters, models, tools, connectors and future workers without coupling the core to one secret store or deployment system.

Issue #43 already established the canonical `security.SecretReference` type and structured redaction utilities. Defining a second secret-reference model in the configuration subsystem would create conflicting security identities and serialization rules.

## Decision

1. Configuration is resolved through platform-owned `ConfigurationSchema`, `ConfigLayer` and `ConfigurationResolver` contracts with explicit precedence, provenance and reload/restart metadata.
2. Task/run overrides are denied unless a schema explicitly allowlists the affected path.
3. Secret-backed configuration stores only the canonical `ai_multi_agent_platform.security.SecretReference`; plaintext secret material is not valid configuration state.
4. Secret storage and resolution use the replaceable `SecretProvider` boundary. The initial `LocalSecretProvider` is an in-memory reference implementation, not a mandatory production backend.
5. Secret resolution requires an explicit `SecretAccessContext` carrying consumer, scope, operation/purpose and requested lifetime information so future authorization and worker systems can enforce least privilege without changing the contract.
6. Resolved plaintext exists only as short-lived `SecretMaterial` at the authorized operation boundary. Canonical serialization, metadata, introspection and audit events remain value-free.
7. Redaction remains a single cross-cutting security facility. Issue #34 extends the existing `security.redaction` helpers for free-text and exception surfaces and recursively redacts sensitive `SecretReference` metadata.
8. Environment variables are imported only through explicit name-to-config-path mappings; broad process-environment forwarding is not part of the platform contract.

## Consequences

- The platform has one canonical secret-reference identity shared by security and configuration.
- Vault, KMS, OS keyrings, encrypted files or other secret stores can be added later without changing configuration resources.
- Authorization, worker dispatch, plugins, connectors and Control Plane introspection can integrate through stable contracts rather than introducing provider-private credential flows.
- Local reference operation remains dependency-free but intentionally does not claim durable encrypted secret storage.
- Components must treat `SecretReference.metadata` as non-secret metadata; redaction is defense in depth, not permission to store plaintext credentials there.
