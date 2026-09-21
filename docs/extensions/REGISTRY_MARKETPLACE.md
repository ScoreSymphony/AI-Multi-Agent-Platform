# Optional Registry and Marketplace

The Registry/Marketplace domain adds a distribution layer and a first-class graphical Marketplace for reusable platform assets without making a central registry service part of the platform runtime.

## Decision

The Marketplace is a required product surface. Registry connectivity is not. The platform owns canonical distribution contracts and the graphical `/marketplace` route, while a registry remains an optional provider behind `RegistryProvider`. Deployments may configure none, a local/offline filesystem catalog, a private organizational implementation, a public provider, or another compatible implementation. Core startup and all non-registry platform features remain independent from registry connectivity.

This is deliberately separate from the Plugin extension registry. The plugin registry owns installed runtime extensions and lifecycle state. The distribution registry owns discovery metadata, package acquisition, validation, distribution provenance and controlled installation/update handoff before content becomes owner-domain state.

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
        +-- plugin ----------> explicit Plugin artifact install/update owner
        |
        +-- canonical asset -> Portability package preview/import -> Template/domain owners
        |
        +-- documentation ---> manual consumption only
```

## Canonical metadata

`RegistryItem` records stable ID and type, version, publisher, source repository/package reference, license/provenance, optional canonical homepage/documentation links, platform compatibility, dependencies, requested permissions, required capabilities/plugins/connectors/models, release/changelog metadata, integrity/signature metadata, trust/review state, optional maturity (`experimental`, `beta`, `stable`), and deprecation/yank state.

The portable JSON contract is versioned separately as `REGISTRY_ITEM_SCHEMA_VERSION`. Registry trust status is informational input to a decision; it is never itself authorization.

Built-in component kinds include Agents, Agent Teams, Orchestrators, Executors, Model Providers, selected platform-provider roles, Tools, Skills, Plugins, Workflows, Templates, Model configurations, Connectors, Applications, Evaluation assets, and documentation/example assets. The component kind is the user-facing semantic role, not the package format: for example an `orchestrator`, `executor` or `model_provider` may be distributed as a Plugin manifest while remaining discoverable under its higher-level Marketplace role. Kinds are not a closed enum boundary: future canonical kind identifiers remain valid when they carry a kind-owned manifest reference, and dependency metadata can optionally constrain the required component kind without adding Marketplace switch statements.

## Discovery

`RegistryQuery` supports text, item type, tags/categories, license, publisher, required capabilities, platform compatibility, trust state, maturity/stability and update-item filtering. The shipped Control Plane composition maps the Marketplace query into this domain contract before generic pagination; provider-specific discovery filters are therefore not discarded or applied a second time as primitive field equality.

`LocalRegistryProvider` remains the deterministic in-memory reference provider. `FilesystemRegistryProvider` is the operator-facing local/offline catalog and loads versioned metadata and artifacts from a configured directory without a hosted service. Local text search covers canonical ID, name, description, publisher, license, maturity, tags and categories so the graphical search box matches the metadata it advertises. Ratings, popularity and recommendation ranking are not canonical requirements.

## Safe activation workflow

`DistributionService.preview()` fetches metadata and the exact artifact and validates it before mutation. Validation covers platform compatibility, OS/architecture/runtime constraints, yanked/deprecated releases, checksum integrity, authoritative signature verification, dependency availability, requested permissions, required capabilities/plugins/connectors/models, installed-version pins and license/provenance changes. Untrusted content remains visibly untrusted.

The preview also carries a typed `MarketplaceDecision`. Its dependency graph records the requiring component, required kind/range, installed and candidate versions, source identity and path. Required dependencies can be satisfied, available-but-not-installed, missing, version/kind-conflicting, source-ambiguous, self-dependent or cyclic; optional dependencies remain non-blocking but visible. Transitive constraints are evaluated together so two branches cannot silently select incompatible versions of the same dependency. Marketplace produces a plan/preview only; it does not recursively install dependency trees. When every required dependency is either already satisfied or has one unambiguous compatible catalog candidate, the typed decision exposes a deterministic leaf-first `install_order`; missing, conflicting, ambiguous or cyclic graphs deliberately expose no automatic order.

Compatibility, permission, provenance and update state are separate typed projections rather than display strings. Update previews expose previous/requested/added/removed/unchanged permissions, installed and candidate provenance, source/publisher/repository/signature-key/trust changes, installed/latest-compatible versions, pin blocking, incompatible/yanked/deprecated candidates and trust/integrity issues. Findings retain stable code, severity, category, subject and structured details for later API/UI projection.

`preview()` never activates content. Async `activate()` requires explicit authorization, re-fetches the exact metadata/artifact, re-runs server-side validation to prevent preview/apply drift, and only then delegates to the owner domain through `DistributionRouter`. Installation state is recorded only after the owner handoff succeeds. The durable installation snapshot records a SHA-256 digest of the exact bytes that were successfully handed to the owner, even when the Registry metadata did not require its own checksum.

`CanonicalDistributionRouter` remains the reference handoff for generic portable Registry assets. Those artifacts are UTF-8 JSON portable packages and flow through Portability `validate_package_document()` -> `preview_import()` -> `execute_import()`; the router never becomes their resource owner. First-class Marketplace `agent` and `agent_team` items deliberately use kind handlers instead: their artifact is the canonical Agent/Team portable snapshot payload, decoded by the existing Agent portability codecs and then materialized through `AgentService`. This gives Marketplace status/update/uninstall an explicit canonical owner seam without creating a second Agent model. Multi-resource Agent-pack import remains the generic Portability package concern; Marketplace does not invent a Marketplace-private bundle format.

Plugin artifacts are intentionally different. `PluginRegistryArtifactInstaller` validates the Registry artifact as a canonical Plugin manifest, requires Registry ID/version/license agreement, and delegates installation or an explicit newer-version update to `PluginRegistry`. Updates require a stopped runtime, repeat Plugin compatibility/configuration/state-version validation, clear old permission grants, and never silently re-enable code. A state-version change remains fail-closed until the declared owner-domain state migration has actually completed.

Documentation assets have no automatic activation path. A provider change or metadata change between preview and activation fails closed. Privileged content is never silently auto-installed or updated.

## Durable installations, restart reconciliation, updates and pinning

`JsonRegistryInstallationStore` persists Registry distribution state independently from the provider. Each installed item records current version, item type, the dependency declarations used for the installed component, exact source registry, publisher, source repository/package reference/revision, license, provenance, requested permissions, signature/key/trust/review metadata and the exact installed-artifact digest. Replacing a version appends the prior snapshot to durable history so source, security and rollback evidence survive restart. Installation-state schema v4 still reads the earlier v1-v3 documents and treats facts absent from older snapshots as unknown rather than inventing provenance.

Registry distribution state is not allowed to claim a plugin that the canonical Plugin owner has forgotten after a process restart. During Registry-enabled single-node startup, `reconcile_registry_plugins()` restores only previously persisted plugin installations into the same canonical `PluginRegistry`. Reconciliation requires the configured provider to reproduce the persisted item/version/source/license/provenance and exact artifact digest; for v4 snapshots it also checks dependencies, publisher, requested-permission, signature/key, trust and review evidence, while declared signatures are reverified cryptographically. A mismatch fails closed. Restoration never enables a runtime and never restores permission grants.

Lifecycle mutation and Marketplace evidence form an ordered recovery boundary rather than a second
owner transaction. Owner mutation always happens first. If the owner rejects install/update/uninstall,
Marketplace durable evidence is left unchanged. If the owner succeeds but writing Marketplace
evidence fails, `JsonRegistryInstallationStore` restores its prior in-memory record before
propagating the persistence error, so the process never reports an uncommitted Marketplace state.
The canonical owner may then be one step ahead of Marketplace evidence; this is an explicit
recoverable state, not a second source of truth. Registered owner handlers must therefore make an
already-applied identical mutation idempotent. Retrying the same source-qualified lifecycle request
(after a process restart if necessary) observes the canonical owner state and completes only the
missing Marketplace evidence write. Built-in Agent, Agent Team, Skill, Plugin-backed
Tool/Connector, Plugin and Application handlers implement this retry contract; Application version
update remains unsupported. The acceptance suite exercises durable evidence-write recovery with fresh owner repositories,
installation stores and `DistributionService` instances across Agent, Skill, Application and
Plugin-backed owners, including `JsonAgentRepository` and `JsonSkillRepository`.

Pins are explicit durable application policy. `registry.pin` can pin only the currently installed version; `registry.unpin` removes that constraint. A pin does **not** hide newer releases: discovery and `update_available` still report a newer candidate, while preview returns `version_pinned` and blocks application until the pin is removed. Updates are never applied automatically. License/provenance changes and pins are validated before activation.

`preview_uninstall()` provides the same pre-mutation decision shape for uninstall. For v4 installations it evaluates reverse dependencies from the persisted installed declaration rather than mutable current catalog metadata, and blocks removal while another installed Marketplace component has a required dependency on the target. If an installed Marketplace dependent can no longer be resolved from its recorded source, dependency safety is unknown and uninstall fails closed instead of assuming independence.

The graphical Marketplace shows installed version, pin state, update availability and changelog. The release/update experience may additionally surface the same update availability through the wider platform update experience; it does not change the no-silent-update rule.

## Multiple Registry sources

`MultiRegistryProvider` composes independent Registry providers without flattening source identity. Search results retain the originating provider on every item, and graphical card/detail identity uses the source-qualified identity so equal item/version pairs from separate catalogs cannot collapse into one UI record. An unqualified exact lookup that matches more than one source fails with an explicit source conflict; callers must choose a source. Updates default to the installed source, while an intentional source switch must be explicitly selected and is surfaced as provenance/security change. Restart reconciliation likewise re-fetches plugins from their recorded source rather than from the aggregate provider identity.

## Trust, signatures and supply chain

Registry content is not trusted merely because it is listed. Checksums are enforced when declared. A signed artifact is activation-blocking unless a deployment-owned `RegistrySignatureVerifier` verifies it. The bundled self-hosted reference verifier uses HMAC-SHA256 with keys stored outside canonical Registry state; public/private Registry providers can supply asymmetric implementations behind the same interface.

Requested permissions are compared with grantable permissions resolved from authoritative platform state. Dependency, license, provenance and compatibility changes are surfaced before activation. Security-sensitive deltas such as new permissions, source/publisher/repository changes, signature-key changes and trust downgrades are represented as typed policy-review inputs, while every mutation still requires the existing platform authorization boundary. These inputs do not themselves decide `require_approval`: the canonical `AuthorizationProvider`/`AuthorizationGate` remains authoritative for allow, deny and exact-action approval outcomes. Marketplace does not create or execute a competing policy engine.

## Owner-domain handoff matrix

Marketplace kind handlers are adapters, not lifecycle owners. The shipped Registry composition
uses the following existing authorities:

| Kind | Canonical owner | Marketplace handler / route | Install | Update | Uninstall | Restart / recovery | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Agent | `AgentService` / canonical Agent repository | `AgentMarketplaceKindHandler` / `KIND_HANDLER` | yes, ordinary immutable Agent revisions only | yes, exactly one canonical revision at a time without changing ownership scope | yes, `AgentService.delete_agent()` | canonical Agent repository remains authoritative across restart; Marketplace evidence reloads independently and no `AgentRun` is created or reconstructed | current canonical Agent revision |
| Agent Team | `AgentService` / canonical Agent repository | `AgentTeamMarketplaceKindHandler` / `KIND_HANDLER` | yes, ordinary Team revisions after canonical member validation | yes, exactly one canonical Team revision at a time without changing ownership scope | yes, `AgentService.delete_team()` | canonical Team repository remains authoritative across restart; Marketplace does not reconstruct live team/runtime state | current canonical Team revision |

Agent Packs use this same owner model rather than introducing a package-owned Team lifecycle. A
Marketplace Team item can depend on multiple first-class Agent items; the cross-kind dependency model exposes a
deterministic leaf-first install plan, each Agent is installed through `AgentService`, and the Team
is installed only after its canonical member revisions exist. This preserves ordinary Agent/Team
revision, update, status and uninstall semantics. The separate Portability workflow still
supports atomic multi-resource Agent+Team packages for generic import/export, but Marketplace does
not duplicate that workflow as a second long-lived owner for an installed Team.
| Orchestrator | `PluginRegistry`; `ORCHESTRATOR` binder into canonical `OrchestratorRegistry` on explicit enable | `PluginExtensionMarketplaceKindHandler` / `KIND_HANDLER` | yes, installs the implementation package only | yes, canonical Plugin update | yes, `PluginRegistry.remove()` after normal disable rules | persisted package evidence is reconciled into the same Plugin owner; runtime is never auto-enabled after restart | Plugin package state plus canonical Orchestrator inventory after enable |
| Executor | `PluginRegistry`; `EXECUTOR` binder into canonical `ExecutorRegistry` on explicit enable | `PluginExtensionMarketplaceKindHandler` / `KIND_HANDLER` | yes, installs the implementation package only | yes, canonical Plugin update | yes, `PluginRegistry.remove()` after normal disable rules | persisted package evidence is reconciled into the same Plugin owner; runtime is never auto-enabled after restart | Plugin package state plus canonical Executor inventory after enable |
| Model Provider | `PluginRegistry`; `MODEL_PROVIDER` binder into canonical `ModelRegistry` on explicit enable | `PluginExtensionMarketplaceKindHandler` / `KIND_HANDLER` | yes, implementation package only; no endpoint, secret or configured model is created | yes, canonical Plugin update | yes, `PluginRegistry.remove()` after normal disable rules | package recovery never restores credentials, endpoints, configured provider instances or model configurations | Plugin package state plus canonical provider inventory after enable |
| Capability Provider | `PluginRegistry`; `CAPABILITY_PROVIDER` binder into canonical `CapabilityRegistry` on explicit enable | `PluginExtensionMarketplaceKindHandler` / `KIND_HANDLER` | yes, implementation package only | yes, canonical Plugin update | yes, `PluginRegistry.remove()` after normal disable rules | same Plugin reconciliation path; no capability runtime is auto-enabled | canonical Plugin/provider state |
| Tool / Capability (manifest-backed) | Plugin package lifecycle; normal `CAPABILITY_PROVIDER` binder into `CapabilityRegistry` on enable | `PluginExtensionMarketplaceKindHandler` / `KIND_HANDLER` | yes | yes, canonical Plugin update | yes, `PluginRegistry.remove()` | persisted Marketplace evidence is replayed by `reconcile_registry_plugins()` into the canonical Plugin owner; no runtime is auto-enabled | canonical Plugin snapshot/manifest |
| Tool / Capability (legacy manifestless) | Portability workflow and imported resource owner | `CanonicalDistributionRouter` / `PORTABLE_IMPORT` | **first-class Marketplace: no; fail closed**; legacy `registry.activate` may still delegate import | **first-class Marketplace: no; fail closed** | **no Marketplace uninstall port; fail closed** | Portability has no restart-safe identical-import replay contract after a Marketplace evidence-write failure, so the first-class Marketplace does not advertise the route as lifecycle-available | **no Marketplace owner-handler status; fail closed** |
| Skill | `SkillService` / Skill repository | `SkillMarketplaceKindHandler` / `KIND_HANDLER` | yes, canonical revision 1 through third-party intake | yes, exactly one canonical revision at a time | yes, `SkillService.delete_skill()` | owner repository and Marketplace evidence reload independently; idempotent retry closes an evidence-write split after restart | current canonical Skill revision |
| Plugin | `PluginRegistry` | existing Plugin install route plus `PluginMarketplaceKindHandler` for owner inspection/removal | yes, existing Plugin route | yes, existing Plugin route | yes, `PluginRegistry.remove()` | `reconcile_registry_plugins()` restores persisted packages into the same canonical `PluginRegistry` | canonical Plugin snapshot/manifest |
| Connector (manifest-backed) | Plugin package lifecycle; normal `CONNECTOR_PROVIDER` binder into `ConnectorService` / `ConnectorRegistry` on enable | `PluginExtensionMarketplaceKindHandler` / `KIND_HANDLER` | yes | yes, canonical Plugin update | yes, `PluginRegistry.remove()` | same plugin reconciliation as manifest-backed Tools; connector authorization is never synthesized | canonical Plugin snapshot/manifest |
| Connector (legacy manifestless) | Portability workflow and imported resource owner | `CanonicalDistributionRouter` / `PORTABLE_IMPORT` | **first-class Marketplace: no; fail closed**; legacy `registry.activate` may still delegate import | **first-class Marketplace: no; fail closed** | **no Marketplace uninstall port; fail closed** | Portability has no restart-safe identical-import replay contract after a Marketplace evidence-write failure, so the first-class Marketplace does not advertise the route as lifecycle-available | **no Marketplace owner-handler status; fail closed** |
| Application (manifest-backed) | `ApplicationLifecycleService`, `ApplicationRepository`, `ApplicationRuntimeRegistry` | `ApplicationMarketplaceKindHandler` / `KIND_HANDLER` | yes when no unresolved install-time bindings remain | **no; fail closed** because the Application owner exposes no artifact-version migration | yes, `ApplicationLifecycleService.remove()` | Application repository reconstructs definitions/instances; Marketplace evidence reloads separately; identical install/remove retries are idempotent | canonical Application instance status and installed definition |
| Application (legacy manifestless) | no automatic Marketplace lifecycle; legacy catalog entry remains discovery-only | `MANUAL` | **no; fail closed** | **no; fail closed** | **no; fail closed** | catalog compatibility only; no Marketplace-owned Application state is created | no automatic owner status |
| Template / Workflow / other portable assets | Portability workflow and resource-specific import owners | `CanonicalDistributionRouter` / `PORTABLE_IMPORT` | **first-class Marketplace: no; fail closed**; legacy `registry.activate` may still delegate import | **first-class Marketplace: no; fail closed** | **no generic Marketplace uninstall port; fail closed** | canonical resource owners retain their own state, but Portability does not yet provide a durable completed-import receipt/replay contract sufficient to repair Marketplace evidence after restart | existing resource-owner surfaces, not Marketplace handler state |
| Registered future/custom kind | the subsystem supplied by the registrant | registered `MarketplaceKindHandler` / descriptor-selected route | only when descriptor + handler support it | only when descriptor + handler support it | only when descriptor + handler support it | canonical owner supplies durable recovery; handler mutation contract requires idempotent identical retries while Marketplace evidence reloads from its store | registered handler status (mandatory read operation) |

Model Provider package validation is value-free at the Marketplace boundary. Sensitive
credential keys in package metadata are rejected, and a configuration schema may describe fields
such as API-key or credential-reference inputs only when it does not embed secret-bearing
`default`, `const`, `example`, `examples` or `enum` values. Endpoint configuration and
`SecretReference` binding happen after installation through the canonical owner/configuration
surface; Marketplace projections never expose the package artifact as configured provider state.

The semantic-kind audit intentionally does **not** turn every Plugin extension enum into a top-level Marketplace category. `memory_provider`, `file_provider`, `knowledge_provider`, `observability_exporter`, `automation_provider` and `evaluator` are exposed as dedicated higher-level kinds because they represent reusable operator/user choices, but remain fail-closed until a deployment wires a stable canonical owner handler. Lower-level `event_provider`, `transport_provider`, `authorization_provider`, `node_provider`, `worker_provider`, `frontend_extension`, `configuration_extension` and routing-policy internals remain discoverable as generic Plugins unless a later owner contract makes a separate product-facing lifecycle meaningful. This avoids multiplying Marketplace lifecycle owners merely because the Plugin SDK has an extension enum.

Marketplace Skill artifacts must carry canonical `SkillSource` metadata. Installation therefore
enters the existing third-party Skill intake/review lifecycle instead of allowing Marketplace
metadata to make a Skill first-party or trusted. A release update delegates to
`SkillService.update_skill()`; the Skill owner may reject it when the artifact attempts to rewrite
immutable source provenance, in which case Marketplace does not synthesize a replacement lifecycle.

Plugin activation remains on the pre-existing `DistributionRoute.PLUGIN` handoff. The registered
Plugin kind handler is used only to expose the same owner for requirements/status/describe/removal;
it does not replace or fork the established install/update route.

Manifest-backed v3 Tool and Connector Marketplace artifacts use canonical Plugin manifests that
declare, respectively, at least one `capability_provider` or `connector_provider` extension.
Existing manifestless Tool/Connector catalog entries retain their portable-import route for backward
compatibility. The compatibility-only `registry.activate` command may still delegate those artifacts
through Portability, but the first-class Marketplace marks these legacy Tool/Connector portable routes as
lifecycle-unavailable and fails install/update closed. Portability currently keeps completed-import replay coordination in process
memory; after restart it cannot generically prove that a prior package mutation already completed
when the subsequent Marketplace evidence write failed. Marketplace therefore does not create a
shadow receipt or second copy of imported owner state. Template/Workflow portable assets use the
same fail-closed first-class lifecycle boundary until the canonical portability owner exposes a
restart-safe replay/reconciliation contract.

Marketplace installation never enables a plugin and never constructs a provider runtime.
Enable/disable, configuration, permission grants and runtime binding remain the existing Plugin
lifecycle. Enabling an Orchestrator extension makes that implementation available through the
canonical `OrchestratorRegistry`; it does not hot-swap an already constructed kernel. Orchestrator
selection remains the configuration-driven orchestrator concern, so canonical Agent/Team definitions
stay orchestrator-independent and can be reused with any compatible registered implementation.

Executable plugin code is also not discovered from a Marketplace manifest entrypoint. The shipped
single-node composition supplies Hermes as an explicit trusted `PluginSource` candidate with a
platform-owned runtime factory. A Marketplace-installed Hermes package can therefore be configured
and enabled through the normal Plugin Control Plane only when its manifest exactly matches that
composed candidate. Its declared `network_access` and `secret_consumption` permissions are granted
by the server-side single-node policy only for that exact governed manifest. A different or
third-party Marketplace plugin may still be installed as package evidence, but activation fails
closed unless platform composition separately supplies a trusted runtime candidate and permission
policy; Marketplace installation never turns manifest metadata into executable code.

Application runtime selection also remains inside the Application domain.  `ApplicationRuntimeRegistry` selects only
when exactly one registered runtime can satisfy the manifest; an unavailable or ambiguous runtime
fails through canonical owner errors.  Marketplace does not implement placement or lifecycle state.

The handler capability flags are intentionally limited to install/update/uninstall.  Status and
describe are mandatory read operations on a registered handler, so no second capability matrix is
needed.  Unsupported mutations fail with `ErrorCode.UNSUPPORTED_CAPABILITY`; owner
`ContractError` values and cancellation/shutdown exceptions are not translated into Marketplace
parallel error types.

## Production composition

The shipped single-node entrypoint keeps Registry support opt-in. When no registry catalog is configured, no `registry-items` collection or Registry mutation commands are registered and ordinary self-hosted operation is unchanged.

When an operator configures a local catalog, the default composition connects:

`FilesystemRegistryProvider -> DistributionService -> CanonicalDistributionRouter -> Plugin/Portability`

and supplies durable installation state plus `PlatformRegistryValidationContextResolver`. The resolver derives platform version, host OS/architecture, available application runtimes, installed component evidence, capabilities, plugins, **ConnectorDefinition IDs**, models and grantable permissions from live server-side platform state rather than accepting those claims from a client. Canonical owner/local installed items may have no Marketplace source (`source_registry=None`); they are merged with Marketplace installation evidence by canonical item ID. Persisted Marketplace evidence takes precedence for the same ID, while unrelated local items remain visible to dependency decisions. Connector and runtime requirements therefore use the same canonical inventories as the rest of the single-node platform. Optional signature keys add the reference HMAC verifier at the same composition boundary.

The configured single-node composition attaches exactly one canonical `PluginRegistry` to the Control Plane and gives that same instance to both the Registry plugin artifact installer and restart reconciliation. Marketplace-installed plugins therefore appear through the normal `plugins` resource and remain represented there after restart instead of creating a Registry-private or distribution-only plugin state.

## Control Plane, CLI and graphical Marketplace

The unified Marketplace keeps one canonical northbound catalog rather than adding a second Marketplace API. `register_distribution_control_plane()` exposes the provider-neutral `registry-items` collection for both the legacy Registry namespace and the unified Marketplace product surface. The read-only `marketplace-kinds` collection projects the registered kind descriptors and their supported lifecycle operations; it is component-type metadata, not a second catalog. Search and filtering cover text, extensible component kind, tags, categories, publisher, source registry, license, trust, maturity/stability, installed/update state, deprecated/yanked state and platform compatibility. Ordering remains server-side and deterministic, including numeric version ordering and a source-qualified identity tie-breaker. Source-qualified item references use `<source>::<item>@<version>` only when disambiguation is needed; the canonical Marketplace item ID itself remains provider-neutral.

The common detail projection contains canonical identity/kind/name/description/version/publisher, source registry and upstream provenance, license/trust/maturity, tags/categories, dependencies, required capabilities/plugins/connectors/models, requested permissions, integrity, installation/update state, route availability, environment compatibility constraints and the kind-specific manifest reference. Kind-owned requirements/details/status stay nested under `owner_extension`; Marketplace does not move runtime ownership out of the component subsystem.

`registry.preview`, `registry.activate`, `registry.pin` and `registry.unpin` remain compatibility contracts. The first-class Marketplace namespace adds `marketplace.preview`, `marketplace.install`, `marketplace.update` and `marketplace.uninstall`. Marketplace preview serializes the typed `MarketplaceDecision`, including dependency resolution, environment compatibility, permission/provenance deltas, approval-review inputs and update state. Install/update create a fresh source-qualified canonical preview and then reuse `DistributionService.activate()`, which revalidates metadata, artifact digest and the decision immediately before the owner handoff. First-class Marketplace route availability is stricter than the legacy Registry compatibility surface for portable Tool/Connector/Template/Workflow entries: those `PORTABLE_IMPORT` routes are reported unavailable because Portability does not yet expose restart-safe identical-import recovery after an evidence-write split, while `registry.activate` remains backward compatible. Uninstall first consumes `preview_uninstall()` so persisted reverse-dependency blockers are enforced, then revalidates source/metadata/install-state/decision before dispatching only through the registered `MarketplaceKindHandler`. Durable installation state is removed only after successful owner uninstall; legacy plugin/portable routes without a canonical uninstall port fail closed.

Validation and owner failures reuse canonical platform `ErrorCode` values with a stable `marketplace_reason` detail. The generic Control Plane performs the existing Authorization check before resource reads or commands execute; successful command authorization is the explicit northbound authorization supplied to the distribution service, which does not implement a competing authorization system.

The CLI exposes `platform marketplace search|list|show|status|preview|install|update|uninstall|updates|kinds`. Search can filter by arbitrary current/future kinds and source registry, `updates` reuses the canonical `update_available=true` catalog filter, and `--source` makes show/preview/install/update source-qualified when multiple catalogs contain the same identity. Install/update/uninstall retain the global `--yes` safeguard and `--json` continues to emit the standard Control Plane envelope. Existing `platform registry ...` commands remain supported for compatibility.

The web application exposes `/marketplace` as a first-class graphical product route. It is manifest-gated on `registry-items`, so deployments without Registry support show the canonical unavailable state rather than attempting a private backend fallback. When available it provides text search plus graphical item-type, trust, maturity, tag, category, license, publisher, required-capability, platform-version and update-only filters; cursor-based continued browsing; install/update/pin badges; source/license/provenance/signature metadata; generic Registry dependencies and required permissions/capabilities/plugins/connectors/models; changelog; server-side validation findings; explicit preview; explicit activation/update; and pin/unpin controls. Browser session, CSRF, idempotency and server-side authorization remain the same boundaries used by other Control Plane mutations.
