# Domain Contract Versioning

Canonical domain contracts are versioned independently from concrete adapters and provider SDKs.

## Rules

- Every serialized canonical contract carries `schema_version`.
- Backward-compatible additive changes keep the current major schema version.
- Removing a required field, changing a required field type, or changing field meaning requires a new major schema version.
- Adapter/provider version numbers never replace canonical schema versions.
- External/provider identifiers belong in `external_refs` and may evolve without changing canonical identity.
- Readers reject unsupported major versions explicitly rather than silently reinterpret data.
- Migration logic belongs at persistence/API boundaries and must preserve canonical IDs and provenance.

## Initial version

The initial canonical domain schemas introduced by issue #4 use `1.0`.

HTTP/API and adapter protocol versioning are intentionally deferred to later contract work.
