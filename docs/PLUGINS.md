# Plugin runtime and extension SDK

Issue #20 introduces a versioned runtime extension boundary for optional platform integrations.

## Architectural rule

Plugins extend existing platform contracts. They do not redefine canonical Task, Run, Agent, Workspace, Approval or other domain models, and they do not gain permission to bypass authorization, secrets, execution, capability or file gates merely because they run in-process.

The runtime path is:

`plugin manifest -> compatibility/security validation -> plugin runtime -> extension registration -> existing platform registry/contract`

The first reference binder proves this with the existing #12 Capability Registry. The bundled reference plugin registers `plugin.echo` as a normal `CapabilityToolProvider`; it does not create a plugin-private tool invocation path.

## Manifest v1

`ai_multi_agent_platform.plugins.manifest.PLUGIN_MANIFEST_SCHEMA` is the Draft 2020-12 schema for manifest version `1`.

A manifest declares:

- stable plugin ID independent from a directory name;
- plugin, manifest and supported platform versions;
- author and #3 provenance/license metadata;
- provided extension IDs/types/interface versions and entrypoints;
- requested permissions;
- configuration schema;
- plugin dependencies;
- optional external services;
- state-migration identifiers;
- optional UI metadata.

Initial compatibility ranges use deterministic one-to-three-part numeric dotted versions. The platform refuses to guess ordering for opaque version labels.

## Supported extension types

The SDK reserves versioned types for orchestrators, executors, model providers/routing policy, capability providers, Memory/File/Knowledge providers, Event providers, authorization providers, observability exporters, automation providers, evaluators, Node/Worker providers, connectors and optional frontend extensions.

Declaring a type does not automatically wire it into core. A platform composition supplies the supported interface versions and, where runtime registration is required, an `ExtensionBinder` for the owning canonical registry.

## Lifecycle

`PluginRegistry` provides the current in-process lifecycle foundation:

1. `install()` validates manifest/platform/interface compatibility and records the plugin disabled;
2. `configure()` validates plugin configuration against the declared JSON Schema;
3. `enable()` validates dependencies and authoritative granted permissions before initialization;
4. the runtime returns exactly the extensions declared by the manifest;
5. duplicate extension IDs fail before becoming visible;
6. binders register extensions through existing platform-owned registries;
7. initialization/registration failure rolls back already registered extensions and shuts the runtime down;
8. `refresh_health()` records normalized plugin health;
9. `disable()` unregisters extensions before shutting the runtime down;
10. `validate_update()` performs compatibility validation without mutating the installed plugin;
11. `remove()` refuses enabled plugins and can be guarded against canonical references.

Package acquisition/distribution is intentionally not defined by this foundation. #81 owns a future optional Registry/Marketplace, while #79 owns portable canonical object import/export.

## Permissions

The manifest may request privileges such as network, Workspace access, capability registration, secret consumption, Worker execution, administrative APIs and frontend extension registration.

`PluginRegistry.enable()` requires the caller to supply the permissions actually granted by the authoritative security/configuration composition. Requested permissions are metadata, not grants. Runtime operations remain subject to the normal #15/#34 gates after activation.

## Isolation and failure containment

- platform core never imports third-party plugin-private modules;
- plugin instances are supplied behind the `PluginRuntime` protocol;
- plugin metadata stays under plugin/extension records rather than being copied into canonical domain objects;
- undeclared or duplicate runtime extensions are rejected;
- partial binder registration is rolled back on enable failure;
- optional plugin absence leaves normal platform imports and reference operation unchanged;
- removal may be blocked while canonical resources still reference the plugin.

This is logical contract isolation. Process/container sandboxing for untrusted code is a separate future hardening layer.

## Reference plugin

`ReferenceCapabilityPlugin` is deterministic and bundled with the repository only to exercise the lifecycle. It requests `capability_registration`, accepts an optional string `prefix`, registers `plugin.echo`, reports normalized health, disables cleanly and can be removed without leaving Capability Registry state behind.

## Next #20 slices

The remaining issue work should compose on this foundation rather than create another plugin model:

- package/entrypoint discovery and explicit install-source handling;
- persistent plugin/config/state storage and explicit migration hooks;
- Control Plane and CLI plugin lifecycle surfaces with #15 authorization/Approval enforcement;
- binders for additional stable extension registries as their owning domains expose them;
- richer provenance/checksum/signature handling;
- update validation across real platform upgrade flows (#41/#42).
