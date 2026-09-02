# Domain Contract Versioning

Canonical domain contracts are versioned independently from concrete adapters and provider SDKs.

## Rules

- Every serialized canonical contract carries `schema_version`.
- Backward-compatible additive changes keep the current major schema version.
- Removing or changing the meaning/type of a required field requires a new major schema version.
- Adapter-specific version numbers never replace canonical schema versions.
- External/provider identifiers belong in `external_refs` and may evolve without changing canonical identity.
- Readers should reject unsupported major versions explicitly rather than silently reinterpret data.
- Migration logic, when needed, belongs at persistence/API boundaries and must preserve canonical IDs and provenance.

## Initial version

The initial canonical domain schemas introduced by issue #4 use `1.0`.

This document does not define HTTP/API versioning or adapter protocol versioning; those belong to the core-interface and API contract work in later issues.
