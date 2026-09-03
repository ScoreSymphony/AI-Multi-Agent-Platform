# Issue #44 completion map

This document maps the implemented connector framework to the requirements and validation scenarios
of issue #44. `docs/CONNECTORS.md` is the normative design explanation for this domain.

## Deliverables

- [x] Canonical ConnectorDefinition model with deterministic platform-owned identity per
      connector type/version and registry enforcement against adapter-private IDs.
- [x] Canonical Connection model with ownership/scope, health/lifecycle, safe namespaced account
      metadata and SecretReference-only credential linkage.
- [x] Canonical ExternalResourceReference with namespaced provider-native identity and provenance.
- [x] Replaceable ConnectorProvider contract.
- [x] Explicit optional provider hooks for external search, event subscription/unsubscription,
      event/webhook normalization, file import/export handoff and knowledge-ingestion handoff, all
      fail-closed when unsupported.
- [x] Connector registry plus repository/service lifecycle boundaries.
- [x] #34 SecretProvider integration through per-connection SecretReference resolution.
- [x] #15 server-side AuthorizationGate integration for lifecycle/read/sync/subscription/action
      operations, including resource-specific Connection visibility in the Control Plane.
- [x] #12 CapabilityToolProvider bridge for connector actions.
- [x] ConnectorEvent hook with schema version, dedupe key, verification and provenance.
- [x] Explicit SyncCheckpoint state with cursor, last success, revision, error/retry and conflict
      policy plus SyncMode semantics for incremental, resync and rebuild behavior.
- [x] Deterministic local reference connector with no external service dependency.
- [x] Registration-based Control Plane resources and lifecycle commands.
- [x] Architecture and replacement documentation.
- [x] Unit/integration/Control Plane regression tests.

## Key invariants

- Provider-native IDs never become canonical platform IDs.
- ConnectorDefinition identity is derived platform-side from connector type/version and remains
  stable across provider object recreation/process restarts.
- ConnectorDefinition, Connection, ExternalResourceReference and ConnectorEvent identities are
  platform-owned.
- Provider-native Connection/account identifiers are namespaced adapter metadata rather than
  canonical IDs; credential-looking adapter metadata is rejected.
- Secret values are not Connection fields or northbound connector payloads.
- External connector actions are not exposed as a generic Control Plane execution command; they use
  the canonical capability invocation pipeline.
- Authorization evaluates the actual Connection owner/project/organization scope; Control Plane
  collection reads do not make all Connections visible merely because the collection route is
  readable.
- Authorization approval digests bind the exact safe proposed connector operation/action payload.
- External events are evidence/input and do not directly execute privileged work.
- Event subscription and inbound event normalization are explicit optional provider hooks and fail
  closed when unsupported.
- Unsupported optional integration operations fail with the canonical `UNSUPPORTED_CAPABILITY`
  error rather than being assumed universal.
- Incremental sync resumes from the stored checkpoint, resync performs a full refresh with the prior
  checkpoint available for reconciliation, and rebuild starts without prior checkpoint state.
- Adapter removal can make future operations unavailable without changing historical external
  references.
- Plugin packaging remains optional and does not own connector lifecycle.
- File/Knowledge bridges preserve the existing #13 canonical boundaries rather than redefining
  external objects as platform-owned resources.

## Validation coverage

Tests cover:

- connection create/configure/disable/re-enable;
- missing/invalid credential reference handling;
- health failure and recovery;
- resource list/read and safe namespaced serialization;
- connector action through CapabilityRegistry/CapabilityInvoker;
- permission denial at the connector service boundary;
- Connection Control Plane list/get visibility with project-scoped #15 policy;
- event provenance and deduplication;
- unsupported event subscription/unsubscription and webhook-normalization hooks;
- synchronization checkpoint resume plus explicit resync and rebuild;
- adapter removal with historical reference preservation;
- local-only operation without automation/broker/plugin loader;
- Control Plane resource/lifecycle exposure;
- server-side allocation of canonical Connection IDs;
- restart-stable canonical ConnectorDefinition identity;
- serialization of safe namespaced ConnectorDefinition source metadata and Connection account
  metadata;
- rejection of plaintext credential fields including nested endpoint metadata;
- explicit failure semantics for unsupported search/event/file/knowledge provider hooks.

#36 and its audit hardening are merged on `main`. #44 consumes those established authenticated actor
and security contracts directly and does not redefine authentication/session ownership.
