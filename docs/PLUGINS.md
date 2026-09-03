# Plugin runtime and extension SDK

Issue #20 defines the versioned runtime-extension boundary for optional platform integrations.

## Architectural rule

Plugins extend existing platform contracts. They do not redefine canonical Task, Run, Agent, Workspace, Approval or other domain models, and they do not gain permission to bypass authorization, secrets, execution, capability or file gates merely because they run in-process.

The runtime path is:

`explicit discovery source -> manifest/compatibility/security validation -> plugin runtime -> extension binder -> existing platform registry/contract`

The first reference binder proves this with the existing #12 Capability Registry. The bundled reference plugin registers `plugin.echo` as a normal `CapabilityToolProvider`; it does not create a plugin-private tool invocation path.

## Manifest v1

`ai_multi_agent_platform.plugins.manifest.PLUGIN_MANIFEST_SCHEMA` is the Draft 2020-12 schema for manifest version `1`.

A manifest declares:

- stable plugin ID independent from a directory name;
- plugin, manifest and supported platform versions;
- author and #3 provenance/license metadata;
- provided extension IDs/types/interface versions and descriptive entrypoints;
- requested permissions;
- configuration version and JSON Schema;
- plugin dependencies;
- optional external services;
- plugin-owned state version and explicit migration edges;
- optional UI metadata.

Initial compatibility ranges use deterministic one-to-three-part numeric dotted versions. The platform refuses to guess ordering for opaque version labels.

The manifest `entrypoint` field is metadata. Core does **not** import arbitrary module strings from manifests. Executable runtime factories come only from explicit `PluginSource` composition.

## Supported extension types

The SDK reserves versioned types for orchestrators, executors, model providers/routing policy, capability providers, Memory/File/Knowledge providers, Event providers, authorization providers, observability exporters, automation providers, evaluators, Node/Worker providers, connectors and optional frontend extensions.

Declaring a type does not automatically wire it into core. A platform composition supplies the supported interface versions and, where runtime registration is required, an `ExtensionBinder` for the owning canonical registry.

## Discovery and installation

Discovery is intentionally separate from a marketplace or package manager.

- `PluginSource` is the discovery contract.
- `StaticPluginSource` is the deterministic reference source for bundled or explicitly composed candidates.
- `DiscoveredPlugin` couples an isolated manifest, an explicit runtime factory and an `install_source` identifier.
- `PluginCatalog.refresh()` rejects duplicate plugin IDs across sources.
- `PluginCatalog.install()` installs through `PluginRegistry`; it does not bypass compatibility validation.
- `PluginCatalog.create_runtime()` invokes the source-supplied factory rather than importing the manifest entrypoint.

The Registry records the actual `install_source` separately from manifest provenance. Direct `PluginRegistry.install()` calls default that field to `manifest.provenance.source`.

A future #81 Registry/Marketplace may implement another `PluginSource`; it does not own the runtime lifecycle itself.

## Lifecycle

`PluginRegistry` provides the current in-process lifecycle foundation:

1. `install()` validates manifest/platform/interface compatibility and records the plugin;
2. `configure()` validates plugin configuration against the declared JSON Schema and records explicit configuration state;
3. `enable()` validates dependencies and authoritative granted permissions before initialization;
4. the runtime returns exactly the extensions declared by the manifest;
5. duplicate extension IDs fail before becoming visible;
6. binders register extensions through existing platform-owned registries;
7. initialization/registration failure rolls back already registered extensions and shuts the runtime down;
8. `refresh_health()` records normalized plugin health;
9. `disable()` unregisters extensions before shutting the runtime down;
10. `validate_update()` performs compatibility validation and requires a deterministic declared state-migration path when `state_version` changes;
11. `remove()` refuses enabled plugins and can be guarded against canonical references.

Registry snapshots expose plugin/extension IDs, extension types, dependencies, provenance source/license, actual install source, requested/granted permissions, configuration/state versions, configuration status, compatibility, lifecycle state and health.

Package acquisition/distribution is intentionally not defined by this runtime layer. #81 owns a future optional Registry/Marketplace, while #79 owns portable canonical object import/export.

## Plugin-owned state and migrations

`PluginStateStore` is the backend-neutral persistence boundary for plugin-owned JSON state. The core runtime does not prescribe SQLite, filesystem, object storage or another concrete persistence engine.

`InMemoryPluginStateStore` exists only as the deterministic reference adapter and test implementation.

A plugin-owned state record contains:

- canonical plugin ID;
- explicit `state_version`;
- plugin-namespaced JSON payload.

`PluginStateMigrationSpec` declares one directed migration edge with a stable migration ID, source version and target version. A `PluginStateMigration` hook implements the corresponding transformation.

`PluginStateMigrator`:

1. loads state through `PluginStateStore`;
2. requires a deterministic manifest-declared path from the stored version to the target `state_version`;
3. verifies every supplied hook exactly matches its manifest declaration;
4. migrates isolated payload copies in order;
5. writes the new state only after the entire migration path succeeds.

If a hook is missing, mismatched or fails, the original stored state remains unchanged. Migration hooks therefore do not silently commit partial version transitions at the state-store boundary.

## Configuration versioning

`configuration_version` is explicit in Manifest v1 and visible in Registry snapshots. Configuration payloads are always revalidated against the installed manifest schema when configured.

This slice does not invent an automatic configuration migration mechanism. A future update workflow must either preserve compatible configuration or require explicit reconfiguration when a plugin changes its configuration contract. Plugin-owned state migration hooks are deliberately separate from configuration handling.

## Permissions

The manifest may request privileges such as network, Workspace access, capability registration, secret consumption, Worker execution, administrative APIs and frontend extension registration.

`PluginRegistry.enable()` requires the caller to supply the permissions actually granted by the authoritative security/configuration composition. Requested permissions are metadata, not grants. Runtime operations remain subject to the normal #15/#34 gates after activation.

## Isolation and failure containment

- importing `ai_multi_agent_platform` does not import the optional plugin subsystem;
- platform core never imports plugin-private manifest entrypoints automatically;
- plugin instances are supplied behind the `PluginRuntime` protocol by explicit discovery composition;
- manifests are copied on installation and inspection, so mutable plugin metadata cannot mutate registry-owned state by aliasing;
- plugin metadata stays under plugin/extension records rather than being copied into canonical domain objects;
- undeclared or duplicate runtime extensions are rejected;
- partial binder registration is rolled back on enable failure;
- plugin-owned state is written only after complete migration success;
- optional plugin absence leaves normal platform imports and reference operation unchanged;
- removal may be blocked while canonical resources still reference the plugin.

This is logical contract isolation. Process/container sandboxing for untrusted code is a separate future hardening layer.

## Reference plugin

`ReferenceCapabilityPlugin` is deterministic and bundled with the repository only to exercise the lifecycle. It requests `capability_registration`, accepts an optional string `prefix`, registers `plugin.echo`, reports normalized health, disables cleanly and can be removed without leaving Capability Registry state behind.

Because registration goes through the canonical Capability Registry, enabled plugin capabilities/providers also appear through its administrative capability/provider inventory surfaces; plugins do not maintain a parallel inventory.

## Remaining #20 work

The remaining issue work should compose on this foundation rather than create another plugin model:

- authorized Control Plane plugin lifecycle resources/actions;
- CLI lifecycle commands over the same Control Plane contract;
- integration of plugin lifecycle permission decisions with #15 authorization/Approval surfaces;
- binders for additional stable extension registries as their owning domains expose them;
- richer checksum/signature verification when install-source implementations exist;
- upgrade/release integration with #41/#42.
