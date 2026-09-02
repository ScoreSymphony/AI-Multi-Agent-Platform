# Issue #34 completion evidence

Issue #34 defines the core configuration, credential-reference and secret-management contracts. Integrations listed under its `Follow-up integrations` section remain outside the completion gate, including the #32 Control Plane exposure of safe configuration metadata.

## Completion hardening after PR #104

PR #104 established the main implementation. The completion review identified one contract-level ambiguity and two acceptance areas that needed explicit evidence.

### SecretProvider audit boundary

`SecretProvider.set_audit_hook(...)` is now part of the replaceable provider contract. A provider implementation can no longer satisfy the complete abstract contract while omitting the platform-defined audit hook surface. Audit events remain value-free and contain operation, secret ID, consumer reference, outcome and timestamp only.

`LocalSecretProvider` accepts an initial hook and supports replacing or clearing it at runtime through the same contract method.

### Local/reference backend baseline

`LocalSecretProvider` is the safe dependency-free reference backend for tests and minimal self-hosted operation. Its guarantees and limitations are explicit:

- secret material is held only in process memory;
- plaintext is never written to disk by this backend;
- canonical metadata and normal representations remain value-free/redacted;
- the backend is intentionally non-durable;
- process restart requires secrets to be reprovisioned;
- deployments requiring durable storage must replace the provider behind the same `SecretProvider` contract.

The provider descriptor reports this as `storage=memory_only`, `durable=false` and `restart_requires_reprovision=true`. A regression test verifies that a new provider instance cannot resolve state from a previous process instance.

This keeps the baseline safe without silently claiming persistence or introducing a mandatory Vault, KMS, cloud service, OS keyring or encrypted-file product into the core architecture.

### Redaction acceptance coverage

Regression coverage now exercises representative structured surfaces for logs, events, traces, API responses, exports, evaluation artifacts and diagnostic output. Sensitive keyed fields are removed through `redact_sensitive(...)`. Representative free-text exception, prompt, log and event strings are scrubbed through `redact_text(...)` when the sensitive value is known at the boundary.

The test verifies that the plaintext value is absent after redaction.

## Existing completion evidence

The implementation from PR #104 already covers:

- deterministic configuration precedence and provenance;
- schema validation before use;
- task/run override allowlists;
- explicit environment-variable mapping;
- canonical `SecretReference` values instead of plaintext configuration credentials;
- safe effective-configuration inspection;
- create/resolve/rotate/revoke/delete/metadata secret lifecycle;
- bounded resolved-material leases;
- consumer and purpose restrictions;
- normalized missing/unavailable/forbidden/invalid-request failures;
- redacted secret metadata and exception/text helpers;
- worker/plugin/authentication-independent core tests.

## Scope note

The #32 Control Plane integration is deliberately not added here. Issue #34 explicitly lists it as a follow-up integration rather than a prerequisite. The core `EffectiveConfiguration.inspect()` service surface remains the safe introspection boundary that #32 can expose without resolving secret material.
