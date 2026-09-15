# Single-node composition architecture (#890)

This document is the implementation handoff for #890, **Decompose the single-node composition root into explicit testable builders**. It records the current production composition, construction dependencies, private/late wiring, target builder contracts, #892 conflict surface, test coverage, and the migration order.

The audit baseline is `main` at `80978f13a36df8ae666ef9497ac13a0eda93982f` (`refactor(persistence): isolate SQLite executors after verification (#1017)`). Issue #892 is still open at this baseline; its remaining active PR, #1019, adds a system-level persistence responsiveness regression and no production code. The builder refactor must still wait for #892 to close before changing persistence/executor ownership.

This document is normative for the logical #890 construction boundaries. It is **not** a package-reorganization plan. Physical domain/package movement remains #895.

## Current Composition Inventory

The supported single-node composition is currently layered rather than owned by one function:

1. `deployment.single_node.build_single_node_deployment()` builds the base runtime and `SingleNodeDeployment`.
2. `deployment.durable_connectors.build_single_node_deployment()` calls the base builder, then adds automatic review, egress, connectors, application distribution, planning, context, learning and handoffs before copying the base aggregate into the durable aggregate.
3. `deployment.build_single_node_deployment()` is the public wrapper and installs the application release gate coordinator after the durable builder returns.
4. `adapters.single_node_app.build_default_single_node_deployment()` is the supported host adapter. It adds local secrets, model adapters, onboarding component setup, repository capabilities and optional registry/plugin distribution integration.
5. `deployment.server` owns process startup/recovery orchestration around the returned deployment and its ASGI application.

| Composition responsibility | Current owner / entry point | Inputs | Outputs | Main dependencies | Lifecycle owner | Persistence | Optional? | Representative tests | Known debt |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Config/directories | `SingleNodeConfig`, base builder | `data_dir`, host/port, registry/release policy config | canonical data/db/files/workspace paths | filesystem | process/bootstrap | directories | required | deployment/runtime tests | directory preparation is embedded in root |
| Storage foundation | `deployment.single_node` | config paths | kernel repo, scope store, files, workspaces, run bindings, repository catalog/provenance/registry | SQLite/local filesystem | individual providers; no aggregate close contract | heavy | required | restart/recovery and persistence regressions | construction is interleaved with domain services |
| Agents/conversations | `deployment.single_node` | storage, models, auth gates | agent/conversation services and `agent_runtime` | repos, model runtime, capabilities | deployment/process | SQLite/JSON-backed repos | required | agent/runtime E2E and composition tests | cannot be built/tested as a unit |
| Models/routing | `deployment.single_node`; egress patched later in `durable_connectors` | model repo/adapters, routing profiles, telemetry | `ModelRuntime`, routing services | models, egress gate | deployment/process | local model/routing stores | required; adapters optional | model regressions/E2E | `egress_gate` is injected after construction |
| Capabilities/tools | `deployment.single_node`; host adapter adds repository capability providers | capability registry, auth/policy hooks | capability registry/assignments | agents, security, egress | deployment/process | local catalog/assignments | required; providers optional | capability/control-plane tests | provider registration is split across root and host adapter |
| Security/auth | `deployment.single_node` | auth DB, authorization provider, telemetry, optional secret provider | authentication, authorization, audit, approval gate, authorized secrets/workflows | storage, telemetry | deployment/process | SQLite/local policy state | required except secrets | security/control-plane regressions | construction is mixed with execution and service registration |
| Observability/health | `deployment.single_node` | optional exporter, runtime services | telemetry/exporter/health provider | most runtime authorities | process | usually non-durable exporter | required; exporter replaceable | health/runtime hardening tests | health is built near end from a broad object graph |
| Execution | `deployment.single_node` | orchestrator, executor, workspaces, authorization, optional distributed runtime | workspace executor + lifecycle backend chain | security, workspaces, distributed runtime | kernel uses lifecycle backend | runtime persistence indirectly | required; distributed optional | runtime/E2E/distributed tests | useful inner execution objects are not public construction outputs |
| Verification/kernel | `deployment.single_node` | kernel repo, lifecycle, verification DB, coordination | verification service/runtime, completion authority, `PlatformKernel`, coordination | execution, security, persistence | kernel/lifecycle | SQLite | required | verification hardening/restart tests | completion authority is later read through private kernel state |
| Repository runtime | `deployment.single_node` | repository catalog/registry/workspaces/kernel | repository services, run integration, event ingress | storage, execution, kernel | deployment/process | catalog/provenance/local repos | required | repository integration/restart tests | `configure_run_integration(...)` is a post-construction binding |
| Automatic review | `deployment.durable_connectors` | kernel, verification, agents/models/files | reviewer workflow/recovery/output coordinator | kernel + verification completion authority | kernel observer + startup reconciler | verification/task state | required in durable profile | verification/reviewer tests | reads `kernel._completion_authority` |
| Egress | `deployment.durable_connectors`, `egress_bindings` | authorization, approval, telemetry, egress profile path | one durable egress runtime/gate and outbound factories | security + persistence | deployment/process | JSON profile/policy state | required for durable profile | egress/security tests | model runtime is patched after construction |
| Connectors | `deployment.durable_connectors` | connector repo/registry + egress + security | connector service | egress, authorization | deployment/process | durable connector repository | required service; providers optional | connector/control-plane tests | control-plane registration happens immediately during construction |
| Application distribution | `durable_connectors` + public `deployment.__init__` | files/workspaces/run bindings/security/kernel repo/distributed runtime | release repository, build kernel, distribution service | execution, verification/evaluation gate | dedicated application build kernel | JSON + files | required; GitHub publisher optional | `test_release_gate_deployment_composition.py` | gate coordinator patched only after durable builder returns |
| Planning/replanning | `deployment.durable_connectors` | planning repo, agents/capabilities/models/security/coordination | planning kernel/service/replanning bridge | base kernel, coordination, model/service catalogs | dedicated planning kernel | JSON | required | planning single-node tests | directly registers resources/commands on an already-built control plane |
| Context | `context_operationalization.install_single_node_context()` | almost entire base deployment + egress | context stores/runtime/skills/research and context-aware lifecycle | execution, kernel, agents, models, repositories, verification | replaces kernel lifecycle after kernel construction | SQLite/JSON/domain stores | required | context restart/authorization tests | private lifecycle read/write and private wrapper introspection |
| Learning | `deployment.durable_connectors` | agents, routing, evaluation, verification, planning, context skills/research | learning composition | context + planning + kernel | deployment/process | durable learning repos | required | learning/control-plane tests | built after control plane exists and self-registers |
| Handoffs | `deployment.durable_connectors` | agents/runtime, coordination, kernel tasks, auth, verification, context research/skills | handoff runtime/repositories | context + coordination + verification | deployment/process | SQLite/JSON | required | `test_deployment_composition.py` | built after control plane exists and self-registers |
| Control Plane | `deployment.single_node`, then extended by durable builder | broad set of services and modules | `ControlPlane` compatibility facade/modules | almost every service | deployment/process | through owned services | required | control-plane composition/release tests | construction and module installation are spread over multiple files |
| HTTP/ASGI | `deployment.single_node` | control plane + authentication/config | authenticated HTTP facade + ASGI app | security/control plane | ASGI server/process | none directly | required | runtime hardening/API tests | cannot be constructed independently |
| Optional registry/plugin host integration | `adapters.single_node_app` | registry catalog config, signature keys, deployment | registry provider/install store/plugin registry/distribution integration | public control-plane/capability seams | host adapter | JSON/filesystem | optional | registry/plugin integration tests | correctly outside the core root; must stay gated |
| Startup/recovery | `deployment.server` | completed deployment | restore + startup reconciliation, uvicorn serving | kernel, coordinator, distributed runtime, reviewer recovery | server/process | reads durable state | command-dependent | `test_single_node_startup_recovery.py`, restart E2E | there is no central `SingleNodeDeployment.close()` contract today |

## Current construction dependency graph

Dependency types below are `C` construction, `R` runtime, `L` lifecycle, `P` persistence and `O` optional integration.

```text
SingleNodeConfig
  C/P -> storage foundation
  C   -> observability

storage foundation
  C/P -> agents/conversations/capabilities/models/routing
  C/P -> security/auth
  C/P -> verification
  C/P -> repository runtime

observability
  C/R -> security wrappers
  C/R -> model/execution/reviewer/learning event sinks
  C/R -> health

security/auth
  C/R -> workflows/research
  C/R -> execution authorization wrapper
  C/R -> planning/connectors/application distribution
  C/R -> control plane + HTTP
  O   -> authorized secrets

agents + capabilities + models
  C/R -> agent runtime
  C/R -> planning
  C/R -> context
  C/R -> learning/handoffs

workspaces + files + run bindings
  C/R -> execution
  C/R -> repositories
  C/R -> application builds
  C/R -> context

execution lifecycle
  L   -> kernel

verification
  C/R -> completion authority
  C/R -> kernel completion
  C/R -> automatic reviewer
  C/R -> release gate

kernel
  R   -> coordination/repository run integration/reviewer/planning coordinator
  L   -> context currently replaces kernel lifecycle after construction

repository runtime
  R   -> context repository source adapters
  R   -> control plane

egress
  R   -> connectors/context exporters/capability invocation
  R   -> model runtime (currently patched after model runtime construction)

planning
  R   -> context plan sources
  R   -> learning

context
  R   -> learning
  R   -> handoffs

all completed domain services
  C   -> control-plane module installation
  C   -> authenticated HTTP/ASGI
  C   -> final SingleNodeDeployment aggregate

optional host adapters
  O   -> secret provider/model adapters/registry/plugin/distribution providers
```

### Current implicit cycles and late binding

There is no unavoidable domain cycle demonstrated by the code; the problematic cycles are mostly construction-order artifacts:

- Context needs the execution lifecycle inputs, but is installed only after a completed kernel exists; it therefore reads and replaces the kernel lifecycle. The target must expose execution participants before kernel construction and pass the **final** lifecycle into the kernel once.
- Egress is built after `ModelRuntime`, so `model_runtime.egress_gate` is assigned after construction even though `EgressDeploymentBindings.model_runtime(...)` already demonstrates that the gate can be a constructor dependency.
- Repository workspace execution exists before repository run integration and is later completed with `configure_run_integration(...)`. This is a deliberate late public seam today and must be resolved or isolated as an explicit binding contract rather than hidden in a builder.
- Application release distribution is created before the canonical gate coordinator and receives it through mutable state in the public wrapper.
- Control-plane resource/command/module registration continues after the base Control Plane and HTTP objects already exist.
- `durable_connectors` rebuilds the durable dataclass by reflecting every field from the base dataclass, coupling the two aggregate shapes implicitly.

## Private and hidden construction wiring findings

| Location | Consumer | Private/hidden dependency | Why it is used now | Correct public construction seam | Risk | #892 overlap | Recommended #890 change |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `deployment/durable_connectors.py` | automatic reviewer composition | `base.kernel._completion_authority` | reviewer/repair/output coordination need the canonical Verification completion authority | `VerificationBundle.completion_authority` passed explicitly | high: kernel internals define another subsystem's construction | Verification offload is merged; wait for #892 close before touching kernel/persistence seams | return completion authority from verification/kernel builder and inject it directly |
| `deployment/context_operationalization.py` | context composition | `base.kernel._lifecycle` read | context needs the existing execution participant as fallback | `ExecutionBundle.lifecycle` and explicit execution participants | critical: construction depends on kernel internal mutable state | lifecycle itself is not changed by active #1019, but execution/persistence acceptance remains under #892 | build context-aware lifecycle before kernel; inject once |
| `deployment/context_operationalization.py` | context composition | `getattr(previous_lifecycle, "_inner", ...)` | unwrap authorization decorator to recover the underlying execution backend | typed `ExecutionBundle.workspace_executor` / `base_lifecycle` output | critical: wrapper topology is an undocumented contract | indirect runtime overlap | never unwrap decorators in composition; carry required participant explicitly |
| `deployment/context_operationalization.py` | context composition | assignment to `base.kernel._lifecycle` | installs canonical context-aware execution after kernel already exists | kernel builder receives final lifecycle | critical: mutates runtime semantics after construction | indirect runtime overlap | remove post-construction lifecycle replacement |
| `deployment/durable_connectors.py` | egress composition | `base.model_runtime.egress_gate = ...` | egress is built after model runtime | explicit egress gate input to model-runtime builder | high: hidden mutable construction contract | no direct #1019 file overlap | order egress policy/gate before model runtime or split gate foundation from connector integration |
| `deployment/single_node.py` | repository execution composition | `repository_workspace_execution.configure_run_integration(...)` | run integration is unavailable at initial coordinator construction | explicit `RepositoryRuntimeBundle` construction/binding contract | medium: public API but hidden construction phase | repository catalog offload was part of #892 | resolve ordering after #892; if a cycle is real, name the binding phase explicitly |
| `deployment/__init__.py` | application distribution | assignment to `application_releases.gate_coordinator` | evaluation/verification gate is added only by public wrapper | release builder input `gate_coordinator` | high: public builder layers return partially constructed service | verification/evaluation persistence already merged | construct release gate before service is returned |
| `deployment/durable_connectors.py` | final durable aggregate | `fields(BaseSingleNodeDeployment)` + `getattr(base, ...)` | copies every base field into subclass aggregate | explicit typed aggregate/bundle construction | medium: dataclass shape becomes an implicit construction API | none direct | remove reflection once builders return typed bundles/final aggregate |

Test-only private field assertions (for example Verification hardening inspecting `_completion_authority`, and handoff restart coverage inspecting repository paths) are characterization debt, not production composition dependencies. Replace them only after equivalent public construction/test seams exist; do not delete coverage merely to remove underscore access.

## Target builder architecture

### Rules

- Preserve the public `ai_multi_agent_platform.deployment.build_single_node_deployment(...)` API and final `SingleNodeDeployment` surface during #890.
- Builders use typed dataclass/protocol inputs and typed bundle outputs; no dictionaries, service locator, container or runtime lookup registry.
- A builder may depend only on earlier bundles shown in the target DAG. It must not accept the whole `SingleNodeDeployment` aggregate.
- Domain services remain in their existing packages for #890. Builder code may initially remain under `deployment`; package movement is #895.
- Runtime services are fully constructed when returned. Mutation-based "finish wiring" is prohibited except for an explicitly named unavoidable binding phase with its own test.
- Optional adapters are passed as explicit optional inputs and must not be imported/started merely by importing the baseline deployment package.
- #890 must preserve current startup, recovery, persistence, authorization, health, API/schema and restart behavior. It must not invent a new shutdown protocol.

### Proposed builders and public bundle contracts

The exact dataclass names may be adjusted during implementation, but the fields below are the minimum public construction contract needed to remove current private wiring.

#### `build_storage(config) -> StorageBundle`

**Owns:** kernel repository, scopes, files, workspaces, run-workspace bindings, repository binding catalog/registry/provenance and initial managed-local-repository restoration.

**Inputs:** prepared `SingleNodeConfig` only.

**Outputs/public types:** `KernelRepository`, scope store/service contract, `FileProvider`, workspace provider, run binding repository, `RepositoryBindingCatalog`, `RepositoryRegistry`, provenance store. Concrete SQLite classes remain implementation details except where the existing public deployment surface already exposes them.

**Lifecycle:** no new aggregate close semantics; retain current provider/process ownership.

**Allowed dependencies:** config/filesystem/persistence adapters. **Forbidden:** Control Plane, HTTP, agents, model runtime, kernel, context, learning.

**Tests:** storage construction with temporary data root; restart opens same canonical paths; #892 executor/connection regressions remain green.

#### `build_observability(exporter) -> ObservabilityBundle`

**Owns:** exporter and telemetry foundation.

**Inputs:** optional supported exporter.

**Outputs:** exporter + `Telemetry`.

**Allowed:** observability package only. **Forbidden:** domain services/Control Plane.

#### `build_security(config, storage, observability, *, secret_provider=None) -> SecurityBundle`

**Owns:** authentication, authorization, authorization audit, approval service/gate and authorized secret provider facade.

**Inputs:** config paths, required storage primitives, telemetry, optional secret provider.

**Outputs:** authentication service, authorization provider, audit sink, approval gate, optional authorized secrets.

**Lifecycle:** repositories/providers retain current ownership.

**Forbidden:** kernel internals, Control Plane registration, HTTP.

**Tests:** no-secret baseline, secret-provider path, policy/audit wiring, restart.

#### `build_egress(security, observability, config) -> EgressBundle`

**Owns:** the one durable egress runtime/gate and `EgressDeploymentBindings` factories.

**Inputs:** authorization, approval gate, telemetry/audit sink, egress persistence path.

**Outputs:** durable egress runtime and public gate/bindings.

**Reason for early construction:** model/capability/connector runtimes must receive the canonical gate during construction, not by later mutation.

**Forbidden:** Control Plane registration and connector provider startup. Those are later integration steps.

#### `build_runtime_services(storage, security, egress, observability, *, onboarding_model_adapters=...) -> RuntimeServicesBundle`

**Owns:** agents, conversations, capabilities/assignments, models, routing profiles, onboarding, model runtime and agent runtime.

**Inputs:** storage/catalog providers, security gates, canonical egress gate, telemetry and optional model adapters.

**Outputs:** typed services currently exposed on `SingleNodeDeployment`, including `ModelRuntime` constructed with the final egress gate.

**Forbidden:** kernel/private execution state, Control Plane.

#### `build_execution(config, storage, security, observability, runtime_services, *, distributed_runtime=None) -> ExecutionBundle`

**Owns:** reference orchestrator/executor, observed wrappers, workspace executor and lifecycle participants.

**Outputs must include explicitly:** `workspace_executor`, the undecorated/base lifecycle participant required by Context, and the normal authorized lifecycle inputs. Nothing downstream may recover these through `_inner`.

**Lifecycle:** this bundle defines execution lifecycle participants but does not construct the kernel.

**Tests:** local vs distributed construction; no execution before kernel; authorization wrapper identity/behavior through public protocols.

#### `build_verification(config, storage, security, observability) -> VerificationBundle`

**Owns:** verification service, canonical `VerificationCompletionAuthority`, evidence resolver/runtime.

**Outputs must include explicitly:** `completion_authority` for both kernel construction and automatic reviewer composition.

**Forbidden:** reading the authority back from a kernel.

**Tests:** completion authority/service use same durable verification state; restart; async persistence regressions.

#### `build_context_foundation(config, storage, security, egress, runtime_services, execution, verification, repository_services) -> ContextBundle`

**Owns:** context stores/assembly/runtime, memory/knowledge/skills/research source adapters and the canonical context-aware execution lifecycle.

**Outputs:** current `SingleNodeContextComposition` plus a **final lifecycle** (or a clearly named lifecycle output) that includes context and authorization exactly once.

**Critical rule:** this builder must not receive `SingleNodeDeployment` and must not receive an already-built kernel merely to introspect/replace its lifecycle. If a small kernel callback is truly required by context, split the existing installer into a pre-kernel foundation and post-kernel runtime attachment with explicit protocol inputs rather than private mutation.

#### `build_kernel(storage, execution, verification, context, security, observability) -> KernelBundle`

**Owns:** the main `PlatformKernel`, coordination service/repository and kernel-bound execution authority.

**Inputs:** final lifecycle from Context/Execution, canonical completion authority from Verification, kernel repository, orchestrator and required policy/runtime services.

**Outputs:** kernel plus coordination authorities needed by later builders.

**Forbidden:** exposing private kernel fields as composition API.

#### `build_repository_services(storage, kernel, execution, security, observability) -> RepositoryServicesBundle`

**Owns:** repository service/management/run integration/workspace execution/event ingress.

**Goal:** remove anonymous late binding. Prefer constructing the coordinator with all dependencies. If the dependency graph proves a real two-phase cycle, expose one explicit `bind_run_integration(...)` construction phase on the bundle and test that it can happen exactly once before runtime use.

#### `build_integrations(...) -> IntegrationBundle`

**Owns:** automatic reviewer/recovery, connector service, application build kernel/distribution service, optional GitHub publisher, and any integration-specific providers.

**Inputs:** runtime services, kernel, verification bundle (including completion authority), egress, repository services, security, files/workspaces, optional distributed runtime/secrets.

**Outputs:** reviewer/recovery, connectors, application distribution. The application release gate coordinator is constructed here from files + canonical verification access + evaluation authorities and passed into the release service before return.

**Forbidden:** private kernel reads and mutation of `ModelRuntime`.

#### `build_planning(...) -> PlanningBundle`, `build_learning(...) -> LearningBundle`, `build_handoffs(...) -> HandoffBundle`

These remain separate because their actual dependency order is real: planning depends on core kernel/runtime services; learning depends on planning + context; handoffs depend on context + coordination + verification. Each returns its current public composition/service bundle and **does not** register directly on an already-running HTTP surface.

#### `build_control_plane(...) -> ControlPlaneBundle`

**Owns:** creation of the canonical Control Plane plus deterministic installation of all `ControlPlaneModule` owners and compatibility registrations established by #982.

**Inputs:** all completed domain/service bundles, health/portability/template/environment authorities.

**Outputs:** `ControlPlane`, module ownership map/registered runtime surface if needed for tests.

**Allowed:** public module/registration contracts. **Forbidden:** private service internals, creating persistence repositories, constructing domain services.

**Tests:** each domain module can be composed against explicit fake/minimal dependencies; duplicate ownership/dependency errors remain deterministic; full registered collection/command compatibility remains unchanged.

#### `build_http(config, security, control_plane) -> HttpBundle`

**Owns:** `AuthenticatedControlPlaneHTTP` and `ControlPlaneASGI` only.

**Outputs:** HTTP facade + ASGI app.

**Forbidden:** domain construction or persistence.

### Target top-level flow

```text
config.prepare_directories()

storage       = build_storage(config)
observability = build_observability(exporter)
security      = build_security(config, storage, observability, secret_provider=...)
egress        = build_egress(config, security, observability)
runtime       = build_runtime_services(storage, security, egress, observability, ...)
execution     = build_execution(config, storage, security, observability, runtime, ...)
verification  = build_verification(config, storage, security, observability)
repositories  = build_repository_services(storage, execution, security, observability, ...)
context       = build_context_foundation(
    config, storage, security, egress, runtime, execution, verification, repositories
)
kernel        = build_kernel(storage, execution, verification, context, security, observability)
# Complete repository/kernel bindings only through an explicit typed phase if still necessary.

planning      = build_planning(...)
evaluation    = build_evaluation(...)
integrations  = build_integrations(..., verification=verification, ...)
learning      = build_learning(..., planning=planning, context=context, ...)
handoffs      = build_handoffs(..., context=context, kernel=kernel, ...)
health        = build_health(...)
control_plane = build_control_plane(...all completed public bundles...)
http          = build_http(config, security, control_plane)

deployment = SingleNodeDeployment(...explicit bundle fields...)
```

The exact point at which evaluation/research authorities are built may stay with their current domain helper during early slices. The invariant is more important than file placement: no later builder may need the completed deployment aggregate or private fields of another runtime object.

## #892 conflict matrix

At the audit baseline, #1017 is merged into `main`. The only active #892 PR is draft #1019, changing `tests/regression/persistence/test_sqlite_runtime_acceptance.py` only. Therefore there is no current textual production-code conflict, but persistence/executor semantics remain intentionally frozen until #892 closes.

| Area/file | #890 will change? | #892 current change? | Active PR/branch | Safe now? | Wait for #892? | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| `deployment/single_node.py` storage/kernel construction | yes | no textual change after #1017; semantic acceptance still open | #1019 / `issue-892-final-runtime-acceptance` | audit/docs only | **yes for refactor** | central SQLite/repository/executor construction surface |
| `deployment/durable_connectors.py` | yes | no | #1019 indirect | audit/docs only | yes for runtime rewiring | reviewer/verification/kernel and persistent integrations |
| `deployment/context_operationalization.py` | yes | no | none direct | audit/docs only | yes for lifecycle rewrite | lifecycle semantics are runtime-critical even without textual conflict |
| `deployment/egress_bindings.py` | likely small/none | no | none | docs/test analysis | not strictly, but implement with main #890 slices | existing helper already models a good explicit gate seam |
| `deployment/__init__.py` public wrapper | yes | no | none | characterization/doc only | preferably | preserve public composition while #892 acceptance is running |
| `adapters/single_node_app.py` | probably no; compatibility only | no | none | yes | no | optional host composition should remain outside #890 core refactor |
| `deployment/server.py` | probably no; compatibility only | no | none | yes | no | startup/recovery is an acceptance surface, not builder ownership |
| SQLite/offload helpers and repository adapters | no direct #890 redesign | #1017 just merged | merged | no new #890 changes | **yes** | #890 consumes settled public repository semantics; it must not reopen #892 |
| `tests/regression/persistence/test_sqlite_runtime_acceptance.py` | run as acceptance | **yes** | #1019 | do not edit | yes | active #892 final evidence |
| CI workflows | no runtime changes | no | PR #1018 `ci/consolidate-actions-workflows` | avoid unrelated edits | no | separate active CI consolidation with unresolved review threads at audit time |
| `docs/runtime/SINGLE_NODE_COMPOSITION.md` | preparation artifact | no | this branch | **yes** | no | documentation-only, no semantic collision |

## Test matrix

The current suite already gives strong aggregate behavior coverage. #890 should add **builder-unit construction tests as each builder appears**, not tests of today's private fields merely to make the refactor easier.

| Category | Existing representative coverage | Current assessment | #890 addition |
| --- | --- | --- | --- |
| Storage construction/persistence | restart/recovery suites; #892 per-domain persistence regressions; #1019 final mixed runtime acceptance | strong behavior, no isolated builder test | `test_build_storage_*` contract tests after #892 closes |
| Security construction | security/control-plane regressions and authenticated deployment E2E | behavior covered, construction entangled | direct `build_security` tests with/without secrets |
| Agent/model/planning construction | agent/model regressions, planning single-node tests | behavior covered | direct runtime/planning bundle tests |
| Execution | single-node runtime/E2E/distributed tests | strong runtime coverage | local/distributed `ExecutionBundle` construction tests |
| Integration wiring | connector, egress, application distribution, handoff/learning composition tests | broad | direct integration bundle tests; assert explicit completion/egress inputs, not private identity |
| Control Plane construction | `docs/runtime/CONTROL_PLANE_COMPOSITION.md` contract tests plus release/control-plane tests | #982 ownership semantics established | isolated `build_control_plane` with minimal explicit bundles |
| Optional adapter absent | default deployment paths run without optional registry/GitHub release providers | implicit/broad | one focused builder test per optional input when builder exists |
| Invalid dependency | ControlPlane module ownership/dependency validation exists | partial | builder-specific missing/incompatible dependency tests |
| Startup | `tests/integration/deployment/test_single_node_startup_recovery.py` | covered | keep green; no semantic change |
| Shutdown/lifespan | `tests/regression/deployment/test_runtime_hardening_v2.py` exercises ASGI lifespan startup/shutdown | API lifespan covered; no central deployment close contract | preserve; do **not** invent `SingleNodeDeployment.close()` in #890 |
| Health/readiness | runtime hardening/health provider coverage | covered | builder test that health consumes explicit authorities |
| Restart | `tests/e2e/recovery/test_durable_process_restart_e2e.py`, handoff/context restart coverage | strong | run after every stateful extraction slice |
| Persistence | #892 cohort tests and #1019 acceptance | very strong/currently active | mandatory regression gate for storage/kernel slices |
| Schema/API compatibility | Control Plane/release/API integration tests | broad | compare registered resources/commands/routes before/after root simplification |
| Golden path | deployment runtime/E2E suites | broad | retain as final #890 acceptance |
| Release gate late wiring | `tests/integration/application_distribution/test_release_gate_deployment_composition.py` | already explicit | no duplicate characterization test needed |

### Characterization-test decision for this preparation branch

No new production-behavior test is added by the preparation commit. The initially suspected release-gate late-wiring gap is already explicitly covered by `test_release_gate_deployment_composition.py`, including default policy and canonical verification/evaluation/file authorities. Startup, restart, lifespan and persistence are also already represented. Adding tests against `_completion_authority`, `_lifecycle`, `_inner` or mutable object identity would freeze the implementation debt that #890 must remove. The missing tests are genuinely **new builder construction contracts**, so they belong in the implementation slices that introduce those public bundles.

## Proposed migration slices

Each slice must be mergeable independently and preserve the supported public deployment API. Re-run targeted tests plus the normal required CI after every slice. Do not combine physical package moves from #895.

### Slice 1 — Typed bundle contracts and builder test harness

**Files:** new/narrow deployment composition helper module(s), deployment tests; minimal imports in existing root.

**Goal:** define frozen typed bundle dataclasses/protocol-facing outputs for Storage, Observability, Security, Egress, RuntimeServices, Execution, Verification, Kernel, RepositoryServices, ControlPlane and HTTP. Add isolated test factories without moving construction yet.

**Prerequisite:** #892 closed and final persistence acceptance green.

**Conflict risk:** low after #892; avoid broad package moves.

**Rollback risk:** very low; contracts unused by production initially.

### Slice 2 — Observability, security and egress foundations

**Files:** `deployment/single_node.py`, `deployment/egress_bindings.py`, new builder module/tests.

**Goal:** extract low-cycle foundations; construct canonical egress gate before model runtime.

**Tests:** security baseline, secrets optionality, egress shared-gate contracts, existing egress/security integrations.

**Risk:** medium because auth wrappers are cross-cutting; no API behavior changes.

### Slice 3 — Storage and runtime-service builders

**Files:** `deployment/single_node.py`, builder tests; no repository implementation redesign.

**Goal:** extract settled #892 storage construction as-is, then agents/conversations/capabilities/models/routing/onboarding. `ModelRuntime` receives egress gate in constructor.

**Prerequisite:** #892 complete.

**Tests:** #892 persistence suite, restart, model/agent/capability tests.

**Risk:** high compatibility/persistence surface; mechanically preserve paths/types.

### Slice 4 — Execution and verification bundles

**Files:** `deployment/single_node.py`, execution/verification builder tests.

**Goal:** expose `workspace_executor`/base lifecycle and canonical completion authority as typed outputs. Do not yet remove context patch until Slice 5.

**Tests:** execution local/distributed, verification hardening, authorization, restart.

**Risk:** high lifecycle surface; preserve wrapper order exactly.

### Slice 5 — Context lifecycle construction before kernel

**Files:** `deployment/context_operationalization.py`, `deployment/single_node.py`, context tests.

**Goal:** split current context installer as necessary so it consumes explicit execution/verification/repository inputs and returns the final lifecycle. Construct kernel once with that lifecycle. Remove `_lifecycle` read/write and `_inner` introspection.

**Prerequisite:** Slice 4 explicit execution outputs.

**Tests:** context operationalization, source authorization/redaction, agent execution golden path, lifespan/restart.

**Rollback risk:** highest slice; wrapper order can alter authorization/audit/context semantics. Keep this slice focused.

### Slice 6 — Kernel/repository explicit binding and reviewer wiring

**Files:** `deployment/single_node.py`, `deployment/durable_connectors.py`, repository/reviewer tests.

**Goal:** construct kernel/coordination from explicit bundles; resolve or explicitly name repository run-integration binding; automatic reviewer receives `VerificationBundle.completion_authority`. Remove `kernel._completion_authority` composition access.

**Tests:** verification reviewer/repair, repository integration, kernel task/run, restart, #892 acceptance.

**Risk:** high but now localized by Slices 3–5.

### Slice 7 — Durable integrations, application distribution and planning

**Files:** `deployment/durable_connectors.py`, `deployment/__init__.py`, integration/planning tests.

**Goal:** extract connector/reviewer/application distribution/planning builders; pass release gate coordinator during construction; remove public-wrapper service mutation and base-dataclass reflection copy.

**Tests:** release gate deployment composition, connectors/egress, planning/replanning, GitHub provider optionality.

**Risk:** medium-high; many services but mostly explicit existing authorities.

### Slice 8 — Learning, handoffs and deterministic Control Plane composition

**Files:** `deployment/durable_connectors.py`, `deployment/single_node.py`, Control Plane builder/tests.

**Goal:** build remaining domain compositions first, then install all ControlPlane modules/registrations in one deterministic builder following #982 ownership rules. Preserve registered resource/command/route surface.

**Tests:** ControlPlane composition/ownership, learning, handoffs, templates, schema/API compatibility.

**Risk:** medium; registration ordering/duplicate ownership are the main failure mode.

### Slice 9 — HTTP and top-level root simplification

**Files:** `deployment/single_node.py`, `deployment/durable_connectors.py`, `deployment/__init__.py`, HTTP/deployment tests.

**Goal:** top-level public builder becomes an explicit linear orchestration of builders and final aggregate construction. Extract HTTP/ASGI builder. Delete obsolete late-wiring/reflection paths.

**Tests:** full deployment composition, runtime hardening/lifespan, health/readiness, startup recovery, process restart and golden path.

**Risk:** medium; broad diff but behavior should already be protected by earlier slices.

### Slice 10 — Architecture guardrails and handoff

**Files:** architecture tests/docs only unless a tiny cleanup remains.

**Goal:** add static guardrails that production deployment composition contains no approved private cross-component access (`._completion_authority`, `._lifecycle`, wrapper `._inner`) and document final builder DAG. Do not ban legitimate class-internal private state repository-wide.

**Tests:** architecture guard + complete required CI.

**Risk:** low.

## Acceptance checklist for the eventual #890 implementation

- Public single-node builder and deployment surface remain compatible.
- Each major construction responsibility has a typed, independently testable builder/bundle.
- No builder receives the completed deployment aggregate as an implicit service locator.
- No production composition reads/writes another component's private attributes.
- Model egress, Verification completion, Context lifecycle and release gate dependencies are constructor/bundle inputs.
- Control Plane is assembled once from completed public modules/services under #982 ownership rules.
- Optional providers remain gated and absent by default without baseline import/startup impact.
- Existing startup/recovery/lifespan/health/API/restart/persistence behavior remains green.
- #892 persistence acceptance remains green; #890 does not redesign SQLite/offload semantics.
- Physical package/domain moves remain deferred to #895.
