# File and Artifact portability

Issue #79 treats canonical File and Artifact identity separately from storage-provider locations. Portable packages may carry file bytes and canonical metadata, but never make a filesystem path, object key, signed URL or source-provider URI canonical.

## File resource

A portable `file` resource contains:

- schema version;
- canonical `FileRecord` metadata;
- file bytes encoded as Base64 for the JSON package transport;
- the canonical SHA-256 digest and byte length already present on `FileRecord`;
- typed dependencies on linked Artifacts and Project scope.

Only `FileState.READY` files are exportable. Pending uploads and tombstoned files are operational state rather than portable file content.

`snapshot_file()` reads the bytes through the platform `FileProvider` streaming contract, invokes provider checksum verification, and checks the resulting bytes against canonical size/checksum metadata before a package resource is created. Host paths and provider object identifiers never cross this boundary.

## Artifact resource

A portable `artifact` resource carries the canonical Artifact ID, name, media type, version, owner/project metadata, provenance and durable external references.

`Artifact.uri` is deliberately omitted. A URI may encode a local filesystem path, provider object location, temporary signed URL or another deployment-private locator. The portable payload records only whether such a source URI was omitted; the value itself is not transported. Imported Artifacts therefore deserialize with `uri=None` until the destination materialization layer assigns an appropriate canonical/provider-backed location.

## ID remapping and dependency order

Both File and Artifact resources participate in the same #79 `ImportPreview` mapping used by Agent/Team portability. When IDs are regenerated:

- the Artifact canonical ID is remapped;
- the File canonical ID is remapped;
- `FileRecord.artifact_ids` are rewritten to imported Artifact IDs;
- Project scope is remapped when a Project mapping exists.

A File declares linked Artifacts as resource dependencies, so the preview orders Artifact resources before Files that reference them.

## Destination provider materialization

`materialize_file()` writes deserialized bytes through the destination installation's `FileProvider`; it never copies the source provider's internal path or object key.

The destination operation:

1. requires destination project scope to match the already remapped portable File scope;
2. creates the File using the planned canonical target ID;
3. compares destination size and SHA-256 with the portable canonical record;
4. invokes destination provider checksum verification;
5. recreates Artifact links using already remapped Artifact IDs.

If creation succeeds but checksum verification or Artifact linking fails, the reference materializer performs a compensating File delete before propagating the failure. A failure of that compensation is surfaced as a backend error rather than silently claiming rollback succeeded.

This per-File compensation is not the final package transaction boundary. #79 still needs a package-level executor that coordinates rollback across multiple resource types.

## Integrity layers

File portability intentionally has two integrity layers:

- the existing portable-resource/package SHA-256 binds the serialized package structure and Base64 payload;
- `FileRecord.sha256` independently binds the decoded file bytes across source and destination FileProviders.

This allows package tampering and storage/provider byte corruption to be distinguished.
