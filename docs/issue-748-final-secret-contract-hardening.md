# Issue #748 final secret-contract hardening

This note records the final compatibility/security contract added after the strict #748 audit.

- `BuildSpecification.secret_references` is legacy reference-only metadata. It accepts only opaque `secret_ref_*` identifiers and must never contain credential material. Secret values belong behind canonical `SecretReference` resolution, with `secret_environment` used for environment delivery.
- Structured observability remains subject to the central capture/redaction policy. The #748 regression suite exercises structured logs, metrics, timeline entries and spans with secret-backed attributes and verifies that resolved material does not reach the exporter.
- Remote application-build dispatch remains owned by #749. The existing Worker `secret_refs`/`SecretDeliveringWorkerDispatcher` boundary remains the intended reference-only transport seam.
