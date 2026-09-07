# Connector persistence and restart recovery

Issue: #416

## Runtime storage

The normal single-node and shipped `platform-server` composition use `SqliteConnectorRepository`
behind the canonical `ConnectorRepository` contract. The concrete local store is:

```text
<AI_MAP_DATA_DIR>/db/connectors.sqlite3
```

SQLite is not part of the connector contract. Other deployment profiles may provide another
`ConnectorRepository` implementation while preserving the same canonical semantics.

`InMemoryConnectorRepository` remains available for unit/contract tests and deliberately ephemeral
profiles.

## Durable state

`connectors.sqlite3` contains the source state required to reconstruct connector discovery after a
process restart:

- `ConnectorDefinition` metadata;
- `Connection` lifecycle/configuration metadata;
- durable `ExternalResourceReference` wrappers;
- the provider-native identity mapping
  `(connection_id, resource_type, namespace, native_id) -> external_resource_*`;
- `SyncCheckpoint` state.

The database does **not** persist a Search index. Search remains derived state and is rebuilt from
the durable connector resources through the normal Control Plane resource/search registration.

Connection deletion uses database-enforced cascade semantics for external-resource wrappers and
checkpoints. Authoritative rebuild replaces one Connection's durable resource set transactionally,
and native identity uniqueness keeps the previously assigned canonical external-resource ID across
adapter/provider object recreation.

## Credential boundary

A persisted Connection contains canonical `SecretReference` objects only. The connector repository
does not resolve a secret provider and therefore never receives resolved credential values through
that path. Connection endpoint metadata remains subject to the existing credential-looking metadata
rejection before persistence, and `SecretReference` metadata is serialized through its redacted
canonical representation.

Secret-provider storage remains an independent #34 concern and must be backed up/restored according
to the selected secret provider's own policy. Restoring `connectors.sqlite3` restores references, not
the secret material those references address.

## Schema and migrations

Connector SQLite state uses `PRAGMA user_version` as the deterministic local schema revision. The
initial schema is version `1`; version `0` is migrated by creating the complete v1 schema. A runtime
fails closed when it encounters a Connector database schema newer than the version it understands.

Future schema changes must add an explicit ordered migration before increasing the supported schema
version. Migration must preserve canonical Connection IDs, external-resource IDs/native identity
mappings and synchronization checkpoints.

## Backup and restore

`db/connectors.sqlite3` is a required entry in the single-node durable-store inventory. Standard
platform backup therefore includes it with the rest of the data root, and restore validation expects
it for a deployment created with this runtime.

For a consistent manual/offline copy, stop the platform before copying the data root. Do not copy a
single Connector database in isolation when other durable platform stores are changing: Connections
may be referenced by repository bindings, authorization/audit state and derived Control Plane state.
Use the platform backup/restore flow so the durable-store inventory is handled as one recovery unit.

After restore/restart:

1. `SqliteConnectorRepository` reconstructs canonical connector source state from
   `db/connectors.sqlite3`;
2. connector providers/adapters are registered again by runtime/plugin composition;
3. provider-native resources reuse stored canonical wrapper IDs during synchronization;
4. Search is rebuilt from the restored canonical Connector resources rather than restored as an
   authority of its own.
