# Platform feature roles and API stability

This document defines the repository-wide vocabulary for two **orthogonal** questions:

1. **Architectural role** — what place does a capability have in the platform architecture?
2. **Stability level** — what compatibility guarantee does a named public surface currently carry?

Neither axis implies the other. A Core capability may still be Beta. A Platform Extension may be Stable. An Optional / Advanced capability is not Experimental merely because it is optional.

The machine-readable audit lives in [`FEATURE_CLASSIFICATION.toml`](FEATURE_CLASSIFICATION.toml). Architecture and package ownership remain authoritative in [`ARCHITECTURE_PRINCIPLES.md`](ARCHITECTURE_PRINCIPLES.md), [`DOMAIN_MODEL.md`](DOMAIN_MODEL.md), [`PACKAGE_BOUNDARIES.md`](PACKAGE_BOUNDARIES.md) and [`PACKAGE_BOUNDARIES.toml`](PACKAGE_BOUNDARIES.toml). Public contract/version semantics remain authoritative in the relevant contract, API and release documents. This document connects those authorities; it does not replace them.

## Architectural role taxonomy

### Core (`core`)

A canonical platform foundation or lifecycle/control-plane capability that belongs to the baseline architecture. Core identifies architectural responsibility, not operational maturity.

Typical Core responsibilities include canonical lifecycle/domain contracts, the Control Plane, durable coordination, provider contracts, Agents, Models, Capabilities/Tools, execution, Workspaces, security, verification, evaluation, the provider-neutral repository-intelligence baseline and cross-cutting observability/accounting foundations.

A concrete optional adapter can implement a Core boundary without becoming Core architecture itself.

### Platform Extension (`platform_extension`)

A first-class optional capability that extends the platform through canonical platform-owned boundaries and is expected to compose with the baseline without redefining its lifecycle authority.

Examples include Memory/Knowledge/Search, Automations, Notifications, Templates, reusable Workflows, Connectors, collaboration/product entrypoints and repository integrations.

An extension can be Stable, Beta or Experimental.

### Optional / Advanced (`optional_advanced`)

A capability that is not required for the ordinary reference single-node baseline and usually represents an advanced deployment, ecosystem, learning/improvement or specialized operational/product subsystem.

Examples include distributed/HA profiles, Registry/Marketplace functionality, named enhanced repository-intelligence providers and advanced learning/research/governance subsystems.

Optional / Advanced is a role classification only. It does not mean unsupported, immature or Experimental.

## Stability taxonomy

Stability applies only to **named public surfaces**: versioned HTTP APIs, canonical schemas/contracts, documented CLI behavior, documented extension interfaces, or specifically documented public Python interfaces. Merely being importable from `ai_multi_agent_platform` does not make a module a supported public API.

### Stable (`stable`)

Stable means the named public surface has a compatibility commitment within its documented versioning mechanism.

Rules:

- backward-compatible additions are allowed;
- a breaking change must use the surface's explicit major-version mechanism where one exists, such as `/api/v2`, a new canonical schema major, or a new provider-contract major;
- a supported Stable surface without its own major namespace must receive a documented deprecation/migration path before removal or incompatible replacement;
- removal of a deprecated Stable behavior requires at least one intervening non-patch release carrying the deprecation notice unless a security/correctness emergency makes continued support unsafe;
- patch releases must not intentionally introduce breaking public behavior;
- release notes/changelog entries must call out deprecations, migrations and compatibility-impacting changes;
- conformance/compatibility evidence must identify the exact Stable surface/version it validates.

Stable describes compatibility, not completeness. A Stable API may expose only a subset of the platform.

### Beta (`beta`)

Beta means the surface is supported, documented and intended for real use, but bounded contract evolution is still expected.

Rules:

- additive changes are normal;
- incompatible changes may occur in a non-patch release when documented with migration guidance;
- deprecation before a Beta breaking change is preferred when practical but is not mandatory;
- compatibility/conformance tests should protect behavior that the documentation currently promises;
- patch releases remain non-breaking except for unavoidable security/correctness remediation;
- Beta must be visible in integrator-facing documentation and, when a user directly encounters the evolving feature in product UI, may receive a contextual Beta marker.

### Experimental (`experimental`)

Experimental means the public surface is intentionally unstable and is not covered by the normal Stable/Beta compatibility guarantees.

Rules:

- incompatible changes may occur in a non-patch release without a deprecation window;
- the surface must be discoverably marked Experimental wherever users or integrators would otherwise reasonably infer compatibility;
- release notes should still mention user-visible incompatible changes;
- Experimental must not be silently promoted to Stable or Beta merely because implementation coverage improves;
- release/conformance evidence can prove that an Experimental profile works for an exact tested revision, but that evidence does not create a forward compatibility guarantee.

Patch releases should still avoid intentional breakage; an Experimental label is not permission to make release semantics unpredictable.

## Internal-only implementation code

Internal modules, private helpers, persistence internals, adapter-private types, generated intermediates and implementation details that are not named as public surfaces are **unclassified for public compatibility**.

They may evolve without public deprecation as long as changes preserve the compatibility promises of any public surface they implement. Package location alone does not promote internal code into Stable/Beta/Experimental API.

## Relationship to repository `0.x` versioning

The repository currently declares package version `0.0.1`, and no formal GitHub release has yet been published. The repository/package version and feature stability therefore answer different questions.

- The repository version describes the release train as a whole.
- A feature stability label describes the compatibility promise for a specific named public surface.
- `0.x` must **not** be interpreted as “every public surface is Experimental”.
- A Stable surface can have stricter compatibility rules than the repository's package-level `0.x` version would imply in generic Semantic Versioning.
- Before the first published release, Stable classifications are contributor-facing compatibility commitments for changes landing on `main`; after publication they also become release-to-release commitments for releases containing the surface.
- A `0.x` minor release may contain intentional breaking changes to Beta/Experimental surfaces under the rules above, but must not silently break a Stable surface. Stable surfaces use their own versioned replacement/deprecation mechanism.
- Repository patch releases remain backward-compatible fixes across all documented public surfaces except unavoidable security/correctness emergencies.

The operational `1.0.0` release gate remains governed by [`RELEASE_PROCESS.md`](RELEASE_PROCESS.md) and platform conformance. A surface does not become Stable merely because the repository reaches `1.0.0`, and a Stable surface does not by itself make the whole platform `1.0.0`-ready.

## Relationship to release and conformance profiles

Role, stability, enablement and conformance evidence are separate dimensions.

- **Role** says where a capability belongs architecturally.
- **Stability** says what compatibility promise its named public surface carries.
- **Profile inclusion** says whether a release/deployment claims the capability is enabled/supported in that profile.
- **Conformance evidence** says whether the exact release/revision + configuration + provider combination passed the required acceptance path.

A Stable Optional / Advanced capability can still be absent from the baseline profile. Conversely, a Core Beta capability can be required by an operational profile while its public contract is still evolving.

Compatibility must never be inferred from a role label alone. #46-style evidence remains required for concrete profile/version claims.

## Current major public-surface audit

The table below is a human-readable view of the canonical machine-readable registry. It intentionally classifies major user/integrator surfaces rather than every package or source file. `Canonical owner(s)` records the authoritative `owner` values from [`PACKAGE_BOUNDARIES.toml`](PACKAGE_BOUNDARIES.toml), not necessarily the names of the packages that implement or expose the surface.

The Stable `control-plane-v1` row covers the shared `v1` protocol/foundation conventions. It does **not** automatically make every later-domain resource registered below `/api/v1` Stable; the resource-family rows below retain their own declared stability. Likewise, the generic CLI row covers shared CLI framework conventions rather than overriding a more specific feature classification such as Experimental Learning commands.

| ID | Capability / public surface | Canonical owner(s) | Role | Stability |
| --- | --- | --- | --- | --- |
| `task-run-lifecycle` | canonical Task/Run/Plan/Step lifecycle schemas and kernel behavior | `domain`, `kernel`, `planning` | Core | Stable |
| `durable-coordination` | `plan-coordination`, reconcile/cancel/repair and workflow progress | `coordination` | Core | Beta |
| `control-plane-v1` | shared HTTP `/api/v1` protocol/foundation + generated OpenAPI conventions | `control_plane` | Core | Stable |
| `provider-contracts-v2` | provider/adapter contracts `2.0` | `contracts` | Core | Stable |
| `agents-teams` | Agent and Agent Team public resources/behavior | `agents` | Core | Beta |
| `models-routing` | model/provider registry, routing and assignments | `models` | Core | Beta |
| `capabilities-tools` | capability/tool declaration, discovery and invocation | `capabilities` | Core | Beta |
| `execution-runtime` | executor/reference execution public contracts | `execution` | Core | Beta |
| `workspaces-data` | Workspaces plus canonical file/data boundaries | `workspaces`, `data` | Core | Beta |
| `security-governance` | authentication, authorization, approvals and governed actions | `security`, `governance` | Core | Beta |
| `verification-review` | verification/review records and completion gates | `verification` | Core | Beta |
| `evaluation-regression` | Evaluation suites/runs plus regression CLI/browser workflows | `evaluation` | Core | Beta |
| `repository-intelligence-core` | provider-neutral repository intelligence + deterministic local baseline | `repositories` | Core | Beta |
| `observability-accounting` | public telemetry/health/usage/accounting views | `observability`, `accounting` | Core | Beta |
| `cli-surface` | shared `platform` CLI framework/global conventions | `control_plane` | Core | Beta |
| `onboarding-first-run` | first-run status/commands plus CLI and browser onboarding workflow | `onboarding` | Platform Extension | Beta |
| `memory-knowledge-search` | Memory, Knowledge, Context and Search capabilities | `data`, `context`, `search` | Platform Extension | Beta |
| `conversations-browser-terminal` | conversational, browser and terminal product capabilities | `conversations`, `browser`, `terminal` | Platform Extension | Beta |
| `automation-notifications` | schedules/triggers and notification delivery surfaces | `automation`, `notifications` | Platform Extension | Beta |
| `templates-workflows-portability` | Templates, reusable Workflows and import/export | `templates`, `workflows`, `portability` | Platform Extension | Beta |
| `repositories-connectors` | Repository/Git and external connector integration | `repositories`, `repository` | Platform Extension | Beta |
| `organizations-collaboration` | Organizations, Teams, Memberships and sharing | `organizations` | Platform Extension | Beta |
| `goals-task-management` | Goals plus priority/deadline/dependency/assignment work management | `goals`, `task_management` | Platform Extension | Beta |
| `plugin-skill-ecosystem` | plugin/skill discovery and loading extension points | `repository`, `skills` | Platform Extension | Beta |
| `distributed-workers` | multi-node Node/Worker scheduling and distributed execution profile | `distributed` | Optional / Advanced | Beta |
| `registry-marketplace` | component registry/catalog/marketplace distribution | `distribution` | Optional / Advanced | Beta |
| `research-evidence` | Research Item/Source/Claim/Evidence resources and research workflow | `research` | Optional / Advanced | Beta |
| `decision-compensation` | advanced decision and compensation/rollback workflows | `decisions`, `compensation` | Optional / Advanced | Beta |
| `application-distribution` | application build/distribution state and release integration | `application_distribution` | Optional / Advanced | Beta |
| `high-availability` | Control Plane leadership/fencing/failover profile | `distributed` | Optional / Advanced | Experimental |
| `learning` | governed learning/improvement resources and workflows | `learning` | Optional / Advanced | Experimental |
| `repository-intelligence-enhanced-providers` | named enhanced repository-intelligence provider integrations | `repositories` | Optional / Advanced | Experimental |

The registry carries the compatibility note, canonical documentation links and user-signaling expectation for each entry. Experimental entries additionally declare `signaling_docs`: concrete public documentation entrypoints that must visibly state the Experimental maturity. New public capabilities should be added at the same granularity.

Repository Intelligence is deliberately split across role boundaries per ADR 0011: the provider-neutral layer and deterministic local baseline are Core, while named enhanced providers remain optional integrations. Classifying a provider as Experimental does not downgrade the canonical baseline.

The same independence applies to other advanced capabilities: Research, Decision/Compensation and Application Distribution are Optional / Advanced because the reference baseline does not require them, but their current canonical public contracts are supported as Beta rather than being labelled Experimental merely because they are advanced. Governed Learning, HA and named enhanced repository-intelligence providers retain explicit Experimental status where current compatibility commitments are intentionally weaker.

## API, schema and DTO policy

### Control Plane

`/api/v1` is the first Stable major for the **shared Control Plane protocol/foundation conventions**. The Stable promise covers those conventions and any separately Stable-classified resource contract; namespace membership alone does not upgrade a resource's maturity.

A Beta or Experimental domain resource may compose under `/api/v1` while retaining its own compatibility policy. Such a resource may receive the bounded incompatible non-patch changes allowed by its stability level without forcing a rename of the entire Control Plane namespace, provided it does not break the shared Stable `v1` foundation or another separately Stable surface.

Conversely, an incompatible change to the shared Stable `v1` foundation or to a separately Stable `v1` resource must use the applicable versioned replacement/deprecation mechanism, normally a new Control Plane major such as `/api/v2` when the incompatibility is part of the HTTP foundation.

### Canonical schemas and provider contracts

Canonical `schema_version` and provider `contract_version` are independent version spaces. Their own major-version rules remain authoritative. The feature registry records the stability of the named family; it does not replace the concrete version field carried by payloads/providers.

### Generated frontend DTOs

Generated DTOs must derive from the canonical transport contract/version. Generation does not promote maturity: a generated type for a Beta resource remains Beta, and a generated type for an Experimental resource remains Experimental.

A generator may emit role/stability metadata for documentation or product signaling, but clients must not use those labels as lifecycle, authorization or availability state.

### CLI and Python

The `cli-surface` classification applies to shared CLI invocation/profile/authentication/rendering/global-option conventions. A feature-specific command group keeps the stability declared by its owning feature; for example, `platform learning` remains Experimental rather than inheriting the generic CLI framework's Beta level.

CLI commands are public only where documented as supported. Python objects are public only when the owning documentation or this registry explicitly claims them as a public contract. Internal importability is not a compatibility promise.

## Product/UI signaling

Use maturity labels where they prevent a wrong compatibility inference, not as general visual decoration.

- Stable everyday workflows normally receive no badge.
- Beta product areas may show a small contextual `Beta` marker when the user is likely to depend on evolving behavior or configuration.
- Experimental areas must be visibly marked in the relevant UI/documentation entrypoint before users enable or depend on them.
- Navigation grouping may reflect architectural/product role for clarity, but navigation is never the source of truth for role or stability.
- Availability, health, permissions and conformance status must remain separate from maturity labels.

The machine-readable registry field `user_signaling` documents the minimum expected signaling for each major surface. Experimental registry entries additionally name one or more concrete `signaling_docs`; architecture tests require those files to contain an explicit `Experimental` marker rather than trusting registry metadata alone. UI/CLI-specific Experimental entrypoints still need their own contextual marker when documentation alone would not be encountered first.

The current browser shell marks the Learning route as Experimental while leaving ordinary Stable/Beta navigation uncluttered. The first-class Learning CLI and its dedicated documentation likewise mark that surface Experimental.

## Contributor and review guard

Every new **public platform capability** or newly public surface must declare, in the same change:

- canonical owner/domain;
- architectural role (`core`, `platform_extension`, or `optional_advanced`);
- stability (`stable`, `beta`, or `experimental`);
- the concrete public surface being promised;
- its compatibility/breaking-change expectation;
- authoritative documentation;
- for Experimental surfaces, concrete labeled entrypoint documentation and any required UI/CLI contextual marker.

Prefer extending an existing registry entry when the new surface is part of an existing capability. Add a new entry only when the capability has a distinct public compatibility boundary.

A pull request that changes a Stable public contract must explain why the change is backward compatible or identify the required versioned replacement/deprecation path. A Beta breaking change must include migration guidance. An Experimental change must preserve discoverable Experimental labeling.

This guard complements top-level package ownership rules; it does not authorize new packages or change canonical ownership.

## Change policy for this taxonomy

Changing a feature's **role** is an architecture decision and must agree with canonical ownership/package-boundary documentation; material ownership changes may require an ADR.

Changing a feature's **stability** is a compatibility/release decision:

- `experimental -> beta` requires supported documentation and representative compatibility/conformance coverage;
- `beta -> stable` requires a concrete versioning story, compatibility tests and release/conformance evidence appropriate to the surface;
- any downgrade from `stable` requires explicit migration/deprecation handling and must not be used to escape an existing compatibility promise.

The registry must be updated together with the corresponding normative documentation and release notes when the change is user-visible.
