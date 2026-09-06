# Optional Registry and Marketplace

Issue #81 adds a distribution layer for reusable platform assets without making a central service part of the platform runtime.

## Decision

The platform owns canonical distribution contracts. A registry is an optional provider behind `RegistryProvider`; deployments may configure none, the bundled local/offline provider, a private organizational implementation, or a public provider. Core startup and all non-registry platform features remain independent from registry connectivity.

This is deliberately separate from the issue #20 plugin extension registry. The plugin registry owns installed runtime extensions and lifecycle state. The distribution registry owns discovery metadata and package acquisition before anything becomes installed platform state.

```text
RegistryProvider (optional)
        |
        v
RegistryItem metadata + artifact
        |
        v
preview / validation
        |
        +-- plugin ----------> explicit #20 artifact installer
        |
        +-- canonical asset -> #79 package preview/import -> #78/domain owners
        |
        +-- documentation ---> manual consumption only
```

## Canonical metadata

`RegistryItem` records stable ID and type, version, publisher, source repository/package reference, license/provenance, platform compatibility, dependencies, requested permissions, required capabilities/plugins/connectors/models, release/changelog metadata, integrity/signature metadata, trust/review state, and deprecation/yank state.

The portable JSON contract is versioned separately as `REGISTRY_ITEM_SCHEMA_VERSION`. Registry trust status is informational input to a decision; it is never itself authorization.

Supported initial item types are Agents, Agent Teams, Tools, Plugins, Workflows, Templates, Model configurations, Connectors, Evaluation assets, and documentation/example assets.

## Discovery

`RegistryQuery` supports text, item type, tags/categories, license, publisher, required capabilities, platform compatibility, trust state and update-item filtering. Ratings, popularity and recommendation ranking are intentionally not canonical requirements.

`LocalRegistryProvider` is the reference provider and works entirely offline. It proves that discovery does not require a paid or hosted registry.

## Safe activation workflow

`DistributionService.preview()` fetches metadata and the exact artifact and validates it before mutation. Validation covers platform compatibility, yanked/deprecated releases, checksum integrity, dependency availability, requested permissions, required capabilities/plugins/connectors/models, installed-version pins and license/provenance changes. Untrusted content remains visibly untrusted.

`preview()` never activates content. Async `activate()` requires an explicit authorization result, re-fetches the exact metadata/artifact, re-runs validation to prevent preview/apply drift, and only then delegates to the owner domain through `DistributionRouter`.

`CanonicalDistributionRouter` is the reference owner handoff. Portable Registry assets must be UTF-8 JSON portable packages. The router sends them through the canonical #79 `validate_package_document()` -> `preview_import()` -> `execute_import()` workflow; it never writes Templates, Agents, Teams, Workflows or other imported resources itself.

Plugin artifacts are intentionally different. The Registry layer does not deserialize plugin manifests, resolve entrypoints, construct runtimes or call `PluginRegistry.install()` directly. A deployment must provide a `PluginArtifactInstaller` that composes the verified artifact through the canonical #20 plugin packaging/discovery boundary. Without that owner adapter plugin activation fails closed as an unsupported capability.

Documentation assets have no automatic activation path. A provider change or metadata change between preview and activation fails closed. Privileged content is never silently auto-installed.

## Updates and pinning

`InstalledRegistryItem` stores local source/version/provenance metadata and an optional pinned version. New registry versions may be discovered, but discovery never applies them. A pin blocks a different candidate version. License and provenance changes are surfaced during validation before any update can proceed.

Issue #42 may later expose these available versions through the platform update experience; it does not change the no-silent-update rule.

## Trust and supply chain

Registry content is not trusted merely because it is listed. Checksums are enforced when declared. Signature metadata is retained for verifier integrations. Requested permissions are compared with the permissions the authoritative platform layer is willing to grant. Dependency, license, provenance and compatibility changes are surfaced before activation.

The distribution layer does not replace repository/package provenance, plugin sandboxing, authorization policy or the broader supply-chain threat model in #43.

## Control Plane / CLI / UI integration seam

Northbound clients consume registry operations through the provider-neutral `DistributionService`; concrete providers are not exposed to CLI/UI consumers.

`register_distribution_control_plane()` is optional by construction. When no provider is configured it registers no collection or command, so registry availability is not part of core startup. With a provider it registers the read-only `registry-items` collection. `registry.preview` is registered only when the deployment supplies a `RegistryValidationContextResolver`, which resolves platform version, installed dependencies, capabilities, models, connectors and grantable permissions from authoritative server-side state rather than trusting client-supplied compatibility inputs.

`registry.activate` is stricter: it is registered only when both the authoritative validation-context resolver and an activation router are configured. The handler performs a fresh server-side preview immediately before activation, rejects validation failures, and then awaits the canonical owner-domain handoff.

The generic Control Plane performs the existing #15 authorization check before registered resource reads or commands execute. A successful Control Plane command authorization is the explicit northbound authorization supplied to `DistributionService.activate()`; the distribution adapter does not implement a parallel authorization system.

CLI and UI integrations can therefore discover exact versioned resources (`<item-id>@<version>`), request canonical preview results, and invoke activation only on deployments that deliberately expose a safe owner-domain composition. Deployments without those adapters remain fully functional offline and expose no Registry mutation command.
