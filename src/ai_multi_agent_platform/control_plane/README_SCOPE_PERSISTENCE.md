# Durable deployment scope store

`SqliteScopeStore` is the #39 durable implementation of the minimal #32 `ScopeStore` contract.
It preserves canonical Project identity and command idempotency across a single-node process
restart. #37 continues to own the richer Workspace lifecycle through `WorkspaceProvider`.

SQLite paths remain deployment configuration and are never canonical Project/Workspace IDs.
