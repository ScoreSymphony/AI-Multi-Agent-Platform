# Issue #34 completion evidence

Issue #34 defines the core configuration, credential-reference and secret-management contracts. Integrations listed under its `Follow-up integrations` section remain outside the completion gate, including the #32 Control Plane exposure of safe configuration metadata.

## Implementation history

PR #104 established the main configuration and secrets implementation.

A first completion review then identified three areas that needed stronger evidence or contracts. PR #110 completed that hardening by:

- making `SecretProvider.set_audit_hook(...)` part of the replaceable provider boundary;
- making the local reference backend's memory-only and restart-reprovision semantics explicit and testable;
- adding representative redaction regression coverage for operational, API and export-style surfaces.

A stricter second review subsequently found one remaining canonical-resource gap: `SecretReference.metadata` was mutable and `SecretReference.to_dict()` could return caller-supplied sensitive metadata directly unless a separate redaction helper was applied. PR #112 closes that final gap.

## Final canonical SecretReference guarantee

After PR #112:

- `SecretReference` sanitizes metadata through the central structured-redaction rules during construction;
- known sensitive keys such as tokens, credentials, passwords, API keys and private keys are not retained in plaintext by the canonical reference object;
- sanitized metadata is recursively frozen, so the caller cannot mutate the canonical object after construction;
- the reference is detached from caller-owned input mappings and lists;
- `SecretReference.to_dict()` returns a fresh recursively redacted copy;
- direct regression tests cover sensitive top-level and nested metadata, deep immutability and detached serialization copies.

This directly satisfies the Issue #34 requirement that canonical objects and serialized canonical/API representations do not contain plaintext secret values by default.

## SecretProvider audit boundary

`SecretProvider.set_audit_hook(...)` is part of the replaceable provider contract. A provider implementation cannot satisfy the complete abstract contract while omitting the platform-defined audit hook surface. Audit events remain value-free and contain operation, secret ID, consumer reference, outcome and timestamp only.

`LocalSecretProvider` accepts an initial hook and supports replacing or clearing it at runtime through the same contract method.

## Local/reference backend baseline

`LocalSecretProvider` is the safe dependency-free reference backend for tests and minimal self-hosted operation. Its guarantees and limitations are explicit:

- secret material is held only in process memory;
- plaintext is never written to disk by this backend;
- canonical metadata and normal representations remain value-free/redacted;
- the backend is intentionally non-durable;
- process restart requires secrets to be reprovisioned;
- deployments requiring durable storage must replace the provider behind the same `SecretProvider` contract.

The provider descriptor reports this as `storage=memory_only`, `durable=false` and `restart_requires_reprovision=true`. A regression test verifies that a new provider instance cannot resolve state from a previous process instance.

## Redaction acceptance coverage

Regression coverage exercises representative structured surfaces for logs, events, traces, API responses, exports, evaluation artifacts and diagnostic output. Sensitive keyed fields are removed through `redact_sensitive(...)`. Representative free-text exception, prompt, log and event strings are scrubbed through `redact_text(...)` when the sensitive value is known at the boundary.

The current observability layer also applies its own default-deny capture policy at telemetry boundaries. That later integration does not replace the Issue #34 core redaction contract.

## Existing completion evidence

The implementation covers:

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
- a replaceable audit boundary;
- a safe local/reference backend with explicit durability semantics;
- canonical secret-reference metadata sanitization and immutability;
- redacted secret metadata, structured redaction and free-text/exception helpers;
- worker/plugin/authentication-independent core tests.

## Verification

PR #110 passed Ruff format, Ruff lint, Mypy, Pytest and package build after its hardening changes.

PR #112 also passed the complete repository CI after the final canonical-serialization fix:

- Ruff format: passed;
- Ruff lint: passed;
- Mypy: passed;
- Pytest: passed;
- package build: passed.

The first #112 CI attempt stopped only on a formatting difference before lint/type/tests ran; the formatting was corrected and the subsequent full run passed all gates.

## Scope note

The #32 Control Plane integration is deliberately not part of the Issue #34 completion gate. Issue #34 explicitly lists it as a follow-up integration rather than a prerequisite. The core `EffectiveConfiguration.inspect()` service surface remains the safe introspection boundary that #32 can expose without resolving secret material.

With PRs #104, #110 and #112 merged, the original core scope and acceptance criteria of Issue #34 are satisfied without relying on its explicitly deferred follow-up integrations.
