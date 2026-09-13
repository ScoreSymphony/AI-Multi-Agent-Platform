# Domain Contract Versioning

Canonical domain contracts are versioned independently from concrete adapters and provider SDKs.

Public-surface maturity is classified separately in [`../FEATURE_CLASSIFICATION.md`](../FEATURE_CLASSIFICATION.md). The canonical domain schema family is currently classified as **Core + Stable**. That stability label does not replace `schema_version`; it states the compatibility promise that the schema versioning rules below enforce.

## Rules

- Every serialized canonical contract carries `schema_version`.
- Backward-compatible additive changes keep the current major schema version.
- Removing a required field, changing a required field type, or changing field meaning requires a new major schema version.
- Adapter/provider version numbers never replace canonical schema versions.
- External/provider identifiers belong in `external_refs` and may evolve without changing canonical identity.
- Readers reject unsupported major versions explicitly rather than silently reinterpret data.
- Migration logic belongs at persistence/API boundaries and must preserve canonical IDs and provenance.
- A Stable schema major must not be broken in place merely because the repository package version is still `0.x`; incompatible changes use a new schema major plus migration guidance.

## Initial version

The initial canonical domain schemas introduced by issue #4 use `1.0`.

HTTP/API and adapter protocol versioning are separate version spaces. Their current role/stability classifications and compatibility policies are documented in [`../FEATURE_CLASSIFICATION.md`](../FEATURE_CLASSIFICATION.md) and their owning contract documentation.
