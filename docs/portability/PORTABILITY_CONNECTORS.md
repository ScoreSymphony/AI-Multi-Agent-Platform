# Connector portability

Issue #79 moves canonical connector configuration between compatible installations without pretending that provider implementations, credentials or synchronization runtime are portable state.

## Portable resource

The portable resource type is `connection` with schema version `1`.

A portable Connection preserves:

- canonical Connection ID according to the package `IdPolicy`;
- connector type and connector version;
- owner, Project and organization scope;
- display name;
- credential-free endpoint metadata;
- canonical `SecretReference` placeholders;
- requested scopes;
- source configuration revision and timestamps;
- whether the source Connection was enabled, as activation intent only.

The portable payload does not include a provider implementation or executable ConnectorDefinition. Instead it includes `ConnectorRequirementMetadata`, a provider-neutral compatibility projection of the required installed connector contract. The requirement contains the canonical ConnectorDefinition ID, type/version, supported operations/features, authentication requirements, resource/action/event vocabulary and configuration schema. Provider-private `adapter_metadata`, display text and health semantics are not compatibility authority.

## Connector dependency

Every Connection declares a required `DependencyKind.CONNECTOR` requirement using the canonical ConnectorDefinition ID and connector version. Import preview can therefore report a missing connector before mutation.

Import preflight resolves the actual target `ConnectorRegistry` entry and compares its provider-neutral requirement metadata with the package. A package cannot create a fake installed ConnectorDefinition by writing repository metadata. If the target implementation is absent or its same-version contract disagrees with the portable requirement, import fails before Connection mutation.

## Secrets

Plaintext connector credentials are never portable.

Each canonical `SecretReference` is serialized only as reference metadata and is also declared as a required `DependencyKind.SECRET` requirement. The target installation must bind a compatible local secret before import/provider validation can succeed. Secret material remains behind the normal #34 `SecretProvider` boundary.

Endpoint metadata is still subject to the global portability secret validator and to `ConnectorService` credential-bearing metadata checks.

## Runtime and provider state exclusions

The following source Connection state is intentionally omitted:

- `adapter_metadata` and provider-native account/session identifiers;
- `status`, `health` and `last_checked_at`;
- remote `granted_scopes`;
- `SyncCheckpoint` cursors, remote revisions, retry/error state and dedupe mappings;
- any provider-private caches, sessions, tokens or temporary materialization.

Exporters should add `connection_runtime_exclusions(connection_id)` to the package exclusion report so these omissions are visible rather than mistaken for data loss.

## Safe target lifecycle

A portable Connection always deserializes into a non-running target state:

- `enabled = false`;
- `status = disabled`;
- `health = unavailable`;
- `granted_scopes = ()`;
- `last_checked_at = None`;
- `adapter_metadata = ()`.

`source_enabled` is retained only so a UI/report can say that explicit reactivation is required. It does not grant execution authority.

`ConnectionImportMutationHandler` preflights Project, owner and organization boundaries, verifies the actually installed target connector contract, rejects a late ID conflict and then runs creation through `ConnectorService`. This retains normal authorization and provider credential/configuration validation. Any provider-derived health, granted scopes or adapter metadata returned during validation is stripped before the final imported Connection is persisted.

After import, normal connector reads/actions/synchronization fail with `UNAVAILABLE` until an authorized destination action explicitly enables the Connection through `ConnectorService.set_enabled(..., True, ...)`. That activation re-validates the destination connector and establishes target-local runtime state.

## ID and reference semantics

Connection IDs use the ordinary #79 preserve/regenerate policy and are rewritten through `ImportContext`.

Project IDs are canonical resource references and are remapped through the accepted Project mapping. Owner and organization transfer is rejected by default; callers that already obtained appropriate authorization must opt into the explicit `ConnectionImportPolicy` exceptions.

SecretReferences are portable placeholders, not secret values. This slice does not invent a second secret-ID mapping contract: target secret binding remains part of destination secret configuration and dependency resolution.

## Rollback

A successfully imported Connection returns its canonical target ID as the compensation token.

Rollback uses `ConnectorRepository.remove_connection_if_unused()`, not the ordinary destructive Connection lifecycle operation. The guarded compensation seam refuses to hard-remove a Connection that has been enabled/left the disabled state or that already has synchronization checkpoint history. This prevents a later package failure from deleting legitimate connector activity.

A failure inside the Connection apply operation self-compensates any fresh partially written Connection before propagating the error.

## Non-portable ConnectorDefinition installation

A ConnectorDefinition is evidence of a registered provider implementation. It is therefore not imported as an independently writable portable resource. Installing/enabling connector code remains plugin/deployment/provider responsibility and is deliberately outside #79 import authority.

This distinction is why package metadata can describe the connector contract required by a Connection while dependency resolution still fails when the target `ConnectorRegistry` has no matching implementation.
