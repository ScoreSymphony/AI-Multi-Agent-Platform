# Connector discovery in global Search

This document defines the canonical Search integration for the Connector framework from issue #44, including durable `ExternalResourceReference` discovery from issue #292.

## Canonical source boundary

Search consumes connector resources only through canonical Connector Control Plane registrations. It does not query connector adapters, remote services, provider SDKs, transient `list_resources` results or synchronization response payloads directly.

The searchable canonical resource types are:

- `connector-definition` from `/api/v1/connector-definitions/{id}`;
- `connection` from `/api/v1/connections/{id}`;
- `external-resource` from `/api/v1/external-resources/{id}` when the wrapper has first been persisted by the Connector domain.

Search remains derived state. Connector Definitions, Connections and durable External Resource References remain authoritative in the Connector domain.

## Connector Definitions

Search may derive discovery text from safe flat Connector Definition metadata already exposed northbound, including:

- canonical definition ID;
- display name and description;
- supported operations that are already part of the generic Search keyword projection.

Nested configuration schemas, health-semantics objects, authentication configuration and adapter/source metadata are not recursively traversed by Search. Their contents therefore do not automatically become global Search text.

## Connections

Normal Connection collection reads remain actor-filtered by `ConnectorService` and the Control Plane authorization boundary.

A Search rebuild needs a complete derived index and cannot invent a privileged synthetic actor. `ConnectionResourceService.list_search_resources()` therefore exposes a dedicated internal rebuild projection containing only safe canonical metadata and scope information. Caller-visible Search results are still authorized per result before counts or exact-ID matches are returned.

The rebuild projection intentionally excludes:

- `secret_references`;
- endpoint metadata;
- account/adapter metadata;
- credential material;
- provider-private payloads.

Organization-scoped Connections participate in Search only through the live Organization visibility seam from #87. Suspended or removed Memberships therefore lose future discovery visibility without rewriting canonical Connection ownership.

## Durable External Resource References

`ExternalResourceReference` is a platform-owned wrapper around provider-native identity. Its canonical `external_resource_*` ID is the only primary platform/Search identity. Provider-native identity remains namespaced metadata (`namespace` + `native_id`) and may be used as safe discovery text without replacing the canonical ID.

Durable wrapper creation/update occurs only at an explicit canonical persistence seam:

- validated Connector synchronization results are persisted after contract validation;
- incremental/resync modes upsert returned wrappers;
- an authoritative `rebuild` replaces the owning Connection's durable wrapper set so disappeared remote resources do not leave stale local discovery state.

Live adapter `list_resources`, provider `read_resource` responses and action-returned references are not silently promoted into durable Search resources. A provider result becomes globally discoverable only after the Connector domain persists it canonically.

### Control Plane projection

`/api/v1/external-resources` and `/api/v1/external-resources/{id}` expose a privacy-minimal wrapper projection:

- canonical wrapper ID/type;
- owning Connection ID;
- declared external resource type;
- namespaced provider-native reference;
- safe version/revision metadata;
- safe canonical URL only when it contains no userinfo, query or fragment;
- owner, Project and Organization scope inherited from the canonical Connection.

Arbitrary provider metadata, provenance payloads, adapter metadata, credentials and secret references are deliberately absent from this northbound discovery projection.

### Search projection

Search indexes only the durable wrapper projection and may use the following safe fields for discovery:

- canonical wrapper ID;
- Connection ID;
- external resource type;
- native namespace and native ID;
- external version/revision;
- owner/Project/Organization scope.

Arbitrary remote content, provider-private metadata/provenance, credential material and unsafe URLs are not indexed.

## Authorization and non-disclosure

Direct Control Plane list/read and global Search re-evaluate canonical scope before caller-visible counts or exact-ID existence are returned.

For External Resource References this includes:

- owning Connection visibility;
- owner/Project authorization through the canonical Control Plane authorization provider;
- live Organization membership/visibility from #87 when `organization_id` is present.

An unauthorized canonical wrapper therefore does not appear in list totals, exact reads or Search results. Search's actor-independent rebuild enumerator is an indexing seam only; it does not grant access.

## Detach, deletion and rebuild

`external-resource.detach` removes only the platform-owned canonical wrapper. It never deletes or mutates the provider-native remote resource.

Removing a Connection cascades removal of its durable External Resource References. A subsequent Search rebuild therefore cannot retain stale wrappers. `remove_connection_if_unused` also refuses compensation once durable references exist, preserving import/lifecycle safety.

A full Search rebuild reconstructs External Resource Search state solely from `ConnectorRepository.list_external_resources()`. Detach, authoritative Connector rebuild and Connection removal are therefore reflected deterministically without Search becoming lifecycle authority.

## Remote-provider search remains separate

Provider-native or remote search/list operations remain Connector capabilities and may return transient results for the current operation. They are not merged into the local canonical Search index unless their references pass through the explicit durable persistence lifecycle above.

## Cost and provider constraints

This integration adds no external Search service, vector database, embedding requirement or paid dependency. Connector discovery works with the existing local `SearchProvider` baseline and remains compatible with replaceable future Search providers.