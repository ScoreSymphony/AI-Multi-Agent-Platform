# Issue #76 storage accounting progress

This progressive slice connects the completed #13 FileProvider contract to the #76 accounting foundation without making filesystem paths or one storage backend canonical.

## Added

- explicit `additive` versus `latest` aggregation semantics;
- latest-value budget semantics for point-in-time gauges;
- SQLite round-trip support for the aggregation mode;
- `FileStorageAccounting` over canonical `FileRecord.size_bytes`;
- current file-storage metric `storage.file.bytes.current` with unit `bytes`;
- project-scope validation and explicit ownership attribution;
- no actor-to-owner inference;
- tombstone/removal decreases current storage rather than accumulating historical bytes;
- unavailable latest values remain unavailable rather than zero.

## Boundary

This does not yet account for WorkspaceSnapshot logical storage, Knowledge/index storage, network transfer, remote Worker/Node storage, or physical backend allocation/replication overhead. Those quantities require their owning domains to expose reliable semantics before #76 records them.

Issue #76 remains open for the other progressive measurement sources and Resources UI.
