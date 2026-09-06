# Optional Registry and Marketplace

Issue #81 adds a distribution layer and a first-class graphical Marketplace for reusable platform assets without making a central registry service part of the platform runtime.

## Decision

The Marketplace is a required product surface. Registry connectivity is not. The platform owns canonical distribution contracts and the graphical `/marketplace` route, while a registry remains an optional provider behind `RegistryProvider`. Deployments may configure none, a local/offline filesystem catalog, a private organizational implementation, a public provider, or another compatible implementation. Core startup and all non-registry platform features remain independent from registry connectivity.

This is deliberately separate from the issue #20 plugin extension registry. The plugin registry owns installed runtime extensions and lifecycle state. The distribution registry owns discovery metadata, package acquisition, validation, distribution provenance and controlled installation/update handoff before content becomes owner-domain state.

```text
Graphical Marketplace / CLI
        |
        v
versioned Control Plane
        |
        v
RegistryProvider (optional)
        |
        v
RegistryItem metadata + artifact
        |
        v
preview / validation
        |
        +-- plugin ----------> explicit #20 artifact install/update owner
        |
        +-- canonical asset -> #79 package preview/import -> #78/domain owners
        |
        +-- documentation ---> manual consumption only
```

## Canonical metadata

`RegistryItem` records stable ID and type, version, publisher, source repository/package reference, license/provenance, platform compatibility, dependencies, requested permissions, required capabilities/plugins/connectors/models, release/changelog metadata, integrity/signature metadata, trust/review state, and deprecation/yank state.

The portable JSON contract is versioned separately as `REGISTRY_ITEM_SCHEMA_VERSION`. Registry trust status is informational input to a decision; it is never itself authorization.

Supported item types are Agents, Agent Teams, Tools, Plugins, Workflows, Templates, Model configurations, Connectors, Evaluation assets, and documentation/example assets.

## Discovery

`RegistryQuery` supports text, item type, tags/categories, license, publisher, required capabilities, platform compatibility, trust state and update-item filtering. The Control Plane maps the Marketplace query into this domain contract before generic pagination; provider-specific discovery filters are therefore not discarded or applied a second time as primitive field equality.

`LocalRegistryProvider` remains the deterministic in-memory reference provider. `FilesystemRegistryProvider` is the operator-facing local/offline catalog and loads versioned metadata and artifacts from a configured directory without a hosted service. Ratings, popularity and recommendation ranking are not canonical requirements.

## Safe activation workflow

`DistributionService.preview()` fetches metadata and the exact artifact and validates it before mutation. Validation covers platform compatibility, yanked/deprecated releases, checksum integrity, authoritative signature verification, dependency availability, requested permissions, required capabilities/plugins/connectors/models, installed-version pins and license/provenance changes. Untrusted content remains visibly untrusted.

`preview()` never activates content. Async `activate()` requires explicit authorization, re-fetches the exact metadata/artifact, re-runs server-side validation to prevent preview/apply drift, and only then delegates to the owner domain through `DistributionRouter`. Installation state is recorded only after the owner handoff succeeds.

`CanonicalDistributionRouter` is the reference owner handoff. Portable Registry assets must be UTF-8 JSON portable packages. The router sends them through the canonical #79 `validate_package_document()` -> `preview_import()` -> `execute_import()` workflow; it never writes Templates, Agents, Teams, Workflows or other imported resources itself.

Plugin artifacts are intentionally different. `PluginRegistryArtifactInstaller` validates the Registry artifact as a canonical #20 manifest, requires Registry ID/version/license agreement, and delegates installation or an explicit newer-version update to `PluginRegistry`. Updates require a stopped runtime, repeat #20 compatibility/configuration/state-version validation, clear old permission grants, and never silently re-enable code. A state-version change remains fail-closed until the declared owner-domain state migration has actually completed.

Documentation assets have no automatic activation path. A provider change or metadata change between preview and activation fails closed. Privileged content is never silently auto-installed or updated.

## Durable installations, updates and pinning

`JsonRegistryInstallationStore` persists Registry distribution state independently from the provider. Each installed item records current version, provider, source repository/package reference/revision, license and provenance. Replacing a version appends the prior snapshot to durable history so source and rollback evidence survive restart.

Pins are explicit durable state. `registry.pin` can pin only the currently installed version; `registry.unpin` removes that constraint. Discovery can expose `update_available`, but it never applies a candidate automatically. License/provenance changes and pins are validated before activation.

The graphical Marketplace shows installed version, pin state, update availability and changelog. Issue #42 may additionally surface the same update availability through the wider platform update experience; it does not change the no-silent-update rule.

## Trust, signatures and supply chain

Registry content is not trusted merely because it is listed. Checksums are enforced when declared. A signed artifact is activation-blocking unless a deployment-owned `RegistrySignatureVerifier` verifies it. The bundled self-hosted reference verifier uses HMAC-SHA256 with keys stored outside canonical Registry state; public/private Registry providers can supply asymmetric implementations behind the same interface.

Requested permissions are compared with grantable permissions resolved from authoritative platform state. Dependency, license, provenance and compatibility changes are surfaced before activation. The distribution layer does not replace repository/package provenance, plugin sandboxing, authorization policy or the broader supply-chain threat model in #43.

## Production composition

The shipped single-node entrypoint keeps Registry support opt-in. When no registry catalog is configured, no `registry-items` collection or Registry mutation commands are registered and ordinary self-hosted operation is unchanged.

When an operator configures a local catalog, the default composition connects:

`FilesystemRegistryProvider -> DistributionService -> CanonicalDistributionRouter -> #20/#79`

and supplies durable installation state plus `PlatformRegistryValidationContextResolver`. The resolver derives platform version, installed Registry dependencies, capabilities, plugins, models and grantable permissions from server-side platform state rather than accepting those claims from a client. Optional signature keys add the reference HMAC verifier at the same composition boundary.

The configured single-node composition attaches exactly one canonical #20 `PluginRegistry` to the Control Plane and gives that same instance to the Registry plugin artifact installer. Marketplace-installed plugins therefore appear through the normal `plugins` resource and lifecycle instead of creating a Registry-private parallel plugin state.

## Control Plane, CLI and graphical Marketplace

`register_distribution_control_plane()` exposes provider-neutral Registry operations only when the required server-side pieces exist. With a provider it registers the read-only `registry-items` collection. `registry.preview` requires the authoritative validation resolver. `registry.activate` additionally requires the owner-domain router. `registry.pin` and `registry.unpin` additionally require durable installation state.

The generic Control Plane performs the existing #15 authorization check before registered resource reads or commands execute. A successful Control Plane command authorization is the explicit northbound authorization supplied to `DistributionService.activate()`; the distribution layer does not implement a parallel authorization system.

The installed CLI exposes discovery, exact-version inspection, preview, activation, pin and unpin through the same API boundary.

The web application exposes `/marketplace` as a first-class graphical product route. It is manifest-gated on `registry-items`, so deployments without Registry support show the canonical unavailable state rather than attempting a private backend fallback. When available it provides search/filter discovery, item type and trust filters, update-only discovery, install/update/pin badges, source/license/provenance/signature metadata, dependencies and requested privileges, changelog, server-side validation findings, explicit preview, explicit activation/update, and pin/unpin controls. Browser session, CSRF, idempotency and server-side authorization remain the same boundaries used by other Control Plane mutations.
