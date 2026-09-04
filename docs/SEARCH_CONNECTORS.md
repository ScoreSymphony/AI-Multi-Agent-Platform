# Connector discovery in global Search

This document defines the issue #45 Search integration for the canonical connector framework from issue #44.

## Canonical source boundary

Search consumes connector resources only through the canonical Connector Control Plane registration. It does not query connector adapters, remote services, provider SDKs or synchronization payloads directly.

The currently searchable canonical resource types are:

- `connector-definition` from `/api/v1/connector-definitions/{id}`;
- `connection` from `/api/v1/connections/{id}`.

Connector Definitions and Connections now carry explicit canonical `type` fields so they can participate in the generic registered-resource Search contract without Search inferring identity from URL collection names.

## Connector Definitions

Search may derive discovery text from safe flat Connector Definition metadata already exposed northbound, including:

- canonical definition ID;
- display name and description;
- supported operations that are already part of the generic Search keyword projection.

Nested configuration schemas, health-semantics objects, authentication configuration and adapter/source metadata are not recursively traversed by Search. Their contents therefore do not automatically become global Search text.

## Connections

Normal Connection collection reads remain actor-filtered by `ConnectorService`.

A Search rebuild needs a complete derived index and cannot invent a privileged synthetic actor. `ConnectionResourceService.list_search_resources()` therefore exposes a dedicated internal rebuild projection containing only:

- canonical Connection ID and type;
- connector type/version identity;
- owner and Project scope;
- display name;
- requested/granted scope names;
- enabled/status/health state;
- update/revision metadata.

The rebuild projection intentionally excludes:

- `secret_references`;
- endpoint metadata;
- account/adapter metadata;
- credential material;
- provider-native account IDs;
- arbitrary remote resource payloads.

Search authorization is still evaluated per result before result counts or exact-ID matches become caller-visible. The rebuild enumerator is therefore an indexing seam, not an authorization bypass.

## Organization-scoped Connections

Organization-scoped Connections are intentionally excluded from the Search rebuild in this slice.

Issue #87 owns Organization/Team/Membership visibility, including removed and suspended memberships. Until that authorization context is available to the global Search result check, indexing Organization-scoped Connections would risk treating owner/Project visibility as a substitute for Organization membership.

After #87 is integrated with Search, Organization-scoped Connections can be added without changing their canonical identity.

## External Resource References

Issue #44 defines `ExternalResourceReference`, but the current Connector repository persists Connector Definitions, Connections and synchronization checkpoints only. External Resource References currently appear in connector synchronization results and are not exposed as a durable listable canonical `ResourceService` collection.

Global Search therefore does **not** crawl sync responses, adapter state or remote resources to manufacture an external-resource index. External Resource References become eligible for Search only after their owning domain exposes a durable privacy-aware northbound enumeration contract.

## Rebuild and deletion

Connections are derived from the canonical Connector repository on every full Search rebuild. Removing a Connection removes it from the next rebuilt index. Connector Definitions likewise come from the canonical definition repository.

The Search index remains derived and reconstructable; connector adapters and Search backends remain replaceable.

## Cost and provider constraints

This integration adds no external Search service, vector database, embedding requirement or paid dependency. Connector discovery works with the existing local SearchProvider baseline and remains compatible with optional future Search providers.
