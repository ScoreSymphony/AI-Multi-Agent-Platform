# Plugin extension compatibility and lifecycle hardening

This document complements `docs/PLUGINS.md` with the strict compatibility, failure-containment and permission rules required by issue #20.

## Activation is transactional at the registry boundary

A plugin is not considered enabled until all of the following have completed successfully:

1. manifest, platform and extension-interface compatibility validation;
2. effective configuration validation against the installed manifest schema;
3. dependency validation;
4. requested/granted permission validation;
5. runtime initialization;
6. extension binder registration;
7. the initial runtime health check.

The Registry commits `ENABLED`, runtime ownership and extension ownership only after those steps succeed. If initialization, registration or the initial health check fails, already registered extensions are unregistered and the runtime is shut down where possible. The plugin enters `FAILED`; incomplete rollback is exposed through unavailable health/detail rather than being silently treated as a successful activation.

## Disable and cleanup failures

Disable is also a controlled lifecycle transition rather than a best-effort loop.

- Extension ownership is not removed from Registry state until all unregister operations and runtime shutdown succeed.
- If an unregister operation fails before shutdown begins, already unregistered extensions are registered again where possible so the prior enabled state can remain coherent.
- If rollback is incomplete, or shutdown itself fails, the plugin enters `FAILED` with unavailable health.
- Residual runtime/extension state blocks re-enable and removal. Operators can retry `disable()` to finish cleanup.

This does not claim process-level rollback for arbitrary hostile plugin code. It guarantees that the platform-owned Registry does not silently report a clean lifecycle state after a known partial transition.

## Configuration compatibility

`configuration_version` is a compatibility dimension independent from plugin, manifest, platform, interface and plugin-owned state versions.

Rules:

- the declared `configuration_schema` must itself be a valid Draft 2020-12 JSON Schema before installation/activation;
- every activation validates the effective stored configuration, including the default `{}` when `configure()` was never called;
- changing the configuration schema without changing `configuration_version` is rejected during update validation;
- when `configuration_version` changes, the current stored configuration is validated against the candidate schema;
- if the current configuration is incompatible, update validation fails explicitly with `requires_reconfiguration=true`;
- a compatible configuration may cross a configuration-version change without an automatic migration;
- plugin-owned state migration remains separate and uses the explicit `PluginStateMigrator` hooks.

Automatic configuration migration is intentionally not invented by #20. #41 may orchestrate reconfiguration or future migration hooks during a platform/plugin upgrade, but it must consume the compatibility result rather than silently activating an incompatible configuration.

## Permission semantics

Plugin permissions are declarations and grants for access to platform extension surfaces. They are not direct authorization to perform a privileged resource operation.

For example, granting `workspace_access` means a plugin may be composed with the canonical Workspace/File access surface where that surface exists. The concrete read/write operation must still pass the canonical identity, authorization, approval, scope and path checks owned by the platform. The same rule applies to network, secrets, Worker execution, administrative APIs and frontend extension registration.

Security rules:

- every requested permission must be granted before activation;
- a caller may not grant permissions the manifest did not request;
- the northbound Control Plane resolves grants server-side;
- `PluginRegistry.enable()` independently rejects undeclared over-grants so internal callers cannot bypass that rule;
- `PluginContext` carries configuration and the bounded grant set, not raw authority to canonical resources;
- installing or enabling an in-process plugin does not make arbitrary plugin code trusted;
- process/container sandboxing remains a separate hardening layer for code that must be treated as actively hostile.

## Extension-interface deprecation and removal

Extension interface versions are explicit compatibility contracts.

- New behavior that is backward compatible should prefer additive evolution within the existing interface version.
- An incompatible contract change requires a new interface version.
- A deprecated interface version should remain listed as supported for an overlap window while its replacement is available.
- Deprecation must be documented before removal and should identify the replacement version when one exists.
- Removal of a previously supported interface version is a platform compatibility change and belongs in the controlled platform upgrade/release process (#41/#42).
- Before activation after an upgrade, each installed plugin must be checked against the platform's currently supported interface-version set.
- A plugin requiring a removed or unsupported interface must fail compatibility validation before runtime initialization.
- Core must never silently reinterpret an old extension instance as a newer interface contract.

These rules allow extension points to evolve without promising indefinite compatibility or creating plugin-private replacement contracts.
