# Issue #20 discovery and migration slice

This document records the scope of the second plugin-runtime slice so later Control Plane/CLI work can depend on explicit contracts rather than reinterpreting implementation details.

## Added in this slice

- explicit `PluginSource` / `PluginCatalog` discovery contracts;
- source-supplied runtime factories instead of automatic imports from manifest entrypoint strings;
- actual install-source tracking separate from manifest provenance;
- explicit configuration and plugin-owned-state versions;
- structured deterministic state-migration declarations;
- backend-neutral `PluginStateStore` and deterministic in-memory reference adapter;
- atomic `PluginStateMigrator` behavior at the state-store boundary;
- richer Registry inspection metadata for extension types, dependencies, provenance and versions;
- isolated manifest copies on install and inspection;
- explicit configuration-state tracking independent from lifecycle state;
- regression tests for optional-core startup, metadata isolation, discovery collisions and migration failures.

## Intentionally still outside this slice

- package-manager or marketplace acquisition;
- arbitrary manifest-driven module loading;
- a fixed database/filesystem implementation for plugin state;
- automatic configuration migration;
- Control Plane and CLI lifecycle resources/actions;
- process/container sandboxing for untrusted code.

Those boundaries remain progressive follow-up work under #20 and its linked platform/security issues.
