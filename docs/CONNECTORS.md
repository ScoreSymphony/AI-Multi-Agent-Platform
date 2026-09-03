# Connector and External Integration Framework

Issue: #44

## Purpose

The connector domain represents configured relationships with externally owned systems without
turning any vendor API, SDK, protocol, account identifier, webhook identifier or synchronization
cursor into canonical platform identity.

A connector is broader than one tool invocation. It can describe a configured account or endpoint,
external resources, actions, events, health and synchronization state. External actions still pass
through the platform's existing capability and security boundaries.

## Ownership model

The platform owns these canonical resources and records:

- `ConnectorDefinition` (`connector_definition_<uuid>`): a stable platform identity for one
  connector type/version definition;
- `Connection` (`connection_<uuid>`): one configured account/endpoint and its platform scope;
- `ExternalResourceReference` (`external_resource_<uuid>`): a stable platform reference to an
  externally owned object;
- `ConnectorEvent` (`connector_event_<uuid>`): normalized external-event evidence and dedupe
  metadata;
- `SyncCheckpoint`: connector-owned checkpoint/recovery metadata keyed by Connection and stream.

The external system continues to own repositories, messages, calendar events, files, records,
tickets and similar objects. A connector reference does not copy those objects into canonical Task,
File, Knowledge or Agent ownership.

## Native identifiers and adapter metadata

Provider-native identities are wrapped by `ExternalNativeReference(namespace, native_id)`. The
namespace is mandatory. A GitHub issue number, email provider thread ID, calendar event ID or
vendor record key must therefore never be mistaken for a platform ID.

Backend-private diagnostics remain in explicit `AdapterMetadata` namespaces. They must not become
routing keys or public platform identity.

Removing an adapter may make future reads/actions unavailable, but it does not change previously
stored canonical IDs or serialized historical external-resource references.

## Connector definitions

`ConnectorDefinition` declares the supported surface for one connector type/version:

- operations and features;
- authentication requirements;
- external resource types;
- actions that can be surfaced as canonical capabilities;
- event types;
- configuration schema;
- normalized health semantics;
- optional namespaced adapter metadata.

Operations are capability declarations, not assumptions. Callers must not infer that every
connector supports mutation, webhooks, synchronization, file transfer or knowledge ingestion.
Unsupported operations fail canonically.

The connector domain is independent from the plugin runtime. A provider may be registered directly
today and packaged by #20 later without changing any connector resource contract.

## Connections and credentials

A `Connection` binds one connector type/version to an owner and optional organization/project scope.
It contains only safe endpoint/account metadata and `SecretReference` objects. Secret material is
not a Connection field and must not be copied into Agent definitions, Tasks, events or API payloads.

The reference provider demonstrates the intended flow:

1. the Connection contains a `SecretReference`;
2. connector validation resolves that reference through the replaceable #34 `SecretProvider`;
3. resolution supplies a narrow `SecretAccessContext` for that connector operation;
4. only the adapter receives short-lived resolved material;
5. canonical serialization exposes the reference, never the material.

OAuth/OIDC tokens, API tokens, service-account credentials, local credentials and webhook-signing
secrets can all use this same reference boundary. Authentication flow implementations remain
adapter-specific; configured authenticated actor context comes from #36 and authorization remains
owned by #15.

## Authorization and approvals

`ConnectorService` is the canonical lifecycle/security boundary. When composed with an
`AuthorizationGate`, connection creation, enable/disable, deletion, health/read operations,
external resource reads, synchronization and external actions produce #15 authorization requests.

Approval digests are bound to the exact safe proposed operation. In particular an external action
binds its Connection, capability/action name, invocation identifier and arguments; an approval for
one action payload cannot authorize a different payload.

The service can run without a gate only for deterministic reference/contract tests or deliberately
minimal internal composition. Production/northbound composition must supply the normal #15
server-side enforcement path rather than treating a client check as authority.

## Canonical capability bridge

Connector actions use the existing #12 capability system:

```text
CapabilityInvocation
    -> CapabilityRegistry
    -> ConnectorCapabilityProvider
    -> selected Connection
    -> ConnectorService authorization boundary
    -> ConnectorProvider.invoke_action()
    -> external system
```

`ConnectorCapabilityProvider` publishes connector actions as normal `CapabilitySpec` entries.
Connector actions are classified as external side effects and declare credential requirements when
the underlying connector requires authentication.

The invocation arguments contain the canonical `connection_id`; the bridge removes that routing
field before delivering provider action arguments. Provider-private request/response classes never
replace `CapabilityInvocation`, `ToolInvocation` or `ToolResult`.

There is intentionally **no generic `connector.invoke` Control Plane command**. A northbound client
must use the canonical capability invocation path, preserving validation, policy, approvals,
traceability and future worker placement.

## External events

`ConnectorEvent` is a provider-neutral hook with:

- connector type and Connection identity;
- namespaced native event identity;
- event type and schema version;
- deduplication key;
- receive timestamp;
- project and external-resource context where known;
- verification state;
- provenance and normalized payload.

An external event is evidence/input, not execution authority. Receiving or verifying a connector
event must never directly perform privileged work. #18 may consume verified events as automation
triggers later, and #35 may transport them across processes, without changing the connector event
contract.

## Synchronization

`SyncCheckpoint` keeps connector-owned synchronization state explicit:

- stream and cursor/checkpoint;
- last successful sync;
- remote revision/etag equivalent;
- status and retry count;
- canonical error code where relevant;
- dedupe mapping;
- conflict policy;
- update timestamp.

`ConnectorService.synchronize()` reloads the saved checkpoint and passes it back to the provider.
The returned checkpoint must belong to the same Connection and stream before it is persisted. This
supports deterministic resume/rebuild behavior without making connector state authoritative for
canonical Task, File or Knowledge history.

## Reference connector

`ReferenceConnectorProvider` is dependency-free apart from platform components and uses no network,
paid API, broker, automation runtime or plugin loader. It demonstrates:

- connection creation and validation;
- scoped SecretReference resolution;
- health failure/recovery;
- external resource listing and reading;
- one external action (`connector.reference.echo`);
- connector event provenance/dedupe data;
- resumable synchronization;
- disable/removal behavior.

It exists for tests and development, not as a production SaaS integration.

## Control Plane extension

`register_connector_control_plane()` uses the registration seam from #32 instead of modifying the
Control Plane foundation. It registers:

Resources:

- `connector-definitions`;
- `connections`.

Lifecycle commands:

- `connection.create`;
- `connection.enable`;
- `connection.disable`;
- `connection.remove`;
- `connection.health`;
- `connector.sync`.

Mutating commands retain the standard `Idempotency-Key` requirement from the Control Plane.
Connection creation allocates the canonical Connection ID server-side. Secret values are not
accepted as Connection fields; callers pass already-created secret references.

Connector actions are deliberately absent from this command list and remain behind #12.

## File and knowledge boundaries

A connector may later bridge an external object into the #13 `FileProvider` or `KnowledgeProvider`,
but the connector contract does not redefine either resource. The integration should preserve both
identities and provenance:

```text
ExternalResourceReference -> explicit import/ingestion operation -> canonical File/Knowledge ref
```

Likewise, exporting a canonical file to an external system produces or updates an
`ExternalResourceReference`; it does not replace the canonical File identity. Concrete transfer and
ingestion adapters can be added only for connectors that declare those operations.

## Failure semantics

Connector implementations translate provider failures to the shared `ContractError` vocabulary.
Typical mappings are:

- malformed setup -> `INVALID_CONFIGURATION`;
- unsupported connector operation -> `UNSUPPORTED_CAPABILITY`;
- missing external resource -> `NOT_FOUND`;
- missing adapter or disabled/unhealthy connection -> `UNAVAILABLE`;
- authorization rejection -> `FORBIDDEN`;
- invalid checkpoint/request -> `INVALID_REQUEST`;
- adapter returning mismatched canonical identity -> `CONTRACT_VIOLATION`;
- unclassified backend failure -> `BACKEND_ERROR`.

Provider SDK exception types must not cross the connector boundary.

## Replacement and extension rules

1. Canonical Connector/Connection/resource/event/sync contracts are platform-owned.
2. Vendor implementations depend on these contracts, never the reverse.
3. A connector may be direct, bundled, plugin-packaged or remote later without redefining canonical
   IDs.
4. MCP is not the connector architecture; an MCP-backed integration may exist only as an adapter.
5. Automation and messaging consume connector hooks but do not own connector lifecycle.
6. File/Knowledge bridges preserve both source provenance and their existing canonical boundaries.
7. No recurring paid service is required by the reference implementation.
