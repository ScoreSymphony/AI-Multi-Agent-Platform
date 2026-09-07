# Issue #39 — Stage-1 implementation record

This branch establishes the first production-shaped single-node deployment slice.

Implemented in this slice:

- platform-owned `ReferenceOrchestrator` for production/reference operation without test fakes;
- durable SQLite local authentication store preserving #36 verifier-only secret handling;
- durable SQLite local authorization policy store preserving #15 separation from authentication;
- durable SQLite Project/ScopeStore baseline preserving #32 project identity/idempotency;
- existing durable SQLite Task/Run/Event kernel composition;
- existing local FileProvider + SQLite metadata;
- existing SQLite WorkspaceProvider;
- existing ReferenceExecutor through the canonical lifecycle bridge;
- schema-validated deployment configuration using the #34 resolver;
- explicit blocking failure when required persistence paths cannot be prepared;
- authenticated Control Plane + ASGI application composition;
- retry-safe first-admin identity + explicit authorization-policy bootstrap;
- side-effect-free module/console entrypoints and optional Uvicorn server packaging;
- credential-free configuration example with regression validation;
- `platform-server smoke` for retry-safe canonical local Task/Run execution;
- operator documentation covering install, configuration loading, bootstrap, smoke, startup,
  health/readiness, restart, conservative backup hooks and data-retaining uninstall;
- restart regression covering Task/Run, Project, authentication session/token and admin-policy
  state;
- smoke regression proving a restart/retry reuses canonical smoke Task/Run IDs rather than
  duplicating work.

Still progressive under the parent issue:

- richer single-server process isolation/reverse-proxy packaging;
- distributed Worker profile after #14/#35 integration;
- heterogeneous multi-device examples;
- #40 tested backup/restore relocation integration;
- #41 upgrade/migration integration;
- optional #89 HA packaging.

The Stage-1 profile remains independent from Hermes, Forge, LiteLLM, MCP, Kubernetes, a cloud
provider or a particular VPS/hardware SKU. Those optional components are not canonical
requirements for the baseline topology.
