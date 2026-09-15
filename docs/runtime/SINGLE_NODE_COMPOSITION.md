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
| Repository foundation | `deployment.single_node` | repository registry/catalog/provenance, workspaces, run bindings | repository service/management, workspace execution coordinator, event ingress | storage, authorization for service/management | deployment/process | repository stores | required | repository lifecycle/workspace tests | part of it is needed before kernel, part only after kernel |
| Agents/conversations | `deployment.single_node` | storage, models, auth gates | agent/conversation services and `agent_runtime` | repos, model runtime, capabilities | deployment/process | SQLite/JSON-backed repos | required | agent/runtime E2E and composition tests | cannot be built/tested as a unit |
| Models/routing | `deployment.single_node`; egress patched later in `durable_connectors` | model repo/adapters, routing profiles, telemetry | `ModelRuntime`, routing services | models, egress gate | deployment/process | local model/routing stores | required; adapters optional | model regressions/E2E | `egress_gate` is injected after construction |
| Capabilities/tools | `deployment.single_node`; host adapter adds repository capability providers | capability registry, auth/policy hooks | capability registry/assignments | agents, security, egress | deployment/process | local catalog/assignments | required; providers optional | capability/control-plane tests | provider registration is split across root and host adapter |
| Security/auth | `deployment.single_node` | auth DB, authorization provider, telemetry, optional secret provider | authentication, authorization, audit, approval gate, authorized secrets/workflows | storage, telemetry | deployment/process | SQLite/local policy state | required except secrets | security/control-plane regressions | construction is mixed with execution and service registration |
| Observability/health | `deployment.single_node` | optional exporter, runtime services | telemetry/exporter/health provider | most runtime authorities | process | usually non-durable exporter | required; exporter replaceable | health/runtime hardening tests | health is built near end from a broad object graph |
| Execution | `deployment.single_node` | orchestrator, executor, workspaces, repository workspace hooks, authorization, optional distributed runtime | workspace executor + lifecycle backend chain | security, workspaces, repository foundation, distributed runtime | kernel uses lifecycle backend | runtime persistence indirectly | required; distributed optional | runtime/E2E/distributed tests | useful inner execution objects are not public construction outputs |
| Verification/kernel | `deployment.single_node` | kernel repo, lifecycle, verification DB, coordination | verification service/runtime, completion authority, `PlatformKernel`, coordination | execution, security, persistence | kernel/lifecycle | SQLite | required | verification hardening/restart tests | completion authority is later read through private kernel state |
| Repository runtime integration | `deployment.single_node` | registry/provenance/workspaces/files/kernel | `RepositoryRunIntegration` bound into workspace execution | repository foundation + kernel | deployment/process | catalog/provenance/local repos | required | repository integration/restart tests | `configure_run_integration(...)` is post-kernel binding |
| Automatic review | `deployment.durable_connectors` | kernel, verification, agents/models/files | reviewer workflow/recovery/output coordinator | kernel + verification completion authority | kernel observer + startup reconciler | verification/task state | required in durable profile | verification/reviewer tests | reads `kernel._completion_authority` |
| Egress | `deployment.durable_connectors`, `egress_bindings` | authorization, approval, telemetry, egress profile path | one durable egress runtime/gate and outbound factories | security + persistence | deployment/process | JSON profile/policy state | required for durable profile | egress/security tests | model runtime is patched after construction |
| Connectors | `deployment.durable_connectors` | connector repo/registry + egress + security | connector service | egress, authorization | deployment/process | durable connector repository | required service; providers optional | connector/control-plane tests | control-plane registration happens immediately during construction |
| Application distribution | `durable_connectors` + public `deployment.__init__` | files/workspaces/run bindings/security/kernel repo/distributed runtime | release repository, build kernel, distribution service | execution, verification/evaluation gate | dedicated application build kernel | JSON + files | required; GitHub publisher optional | `test_release_gate_deployment_composition.py` | gate coordinator patched only after durable builder returns |
| Planning/replanning | `deployment.durable_connectors` | planning repo, agents/capabilities/models/security/coordination | planning kernel/service/replanning bridge | base kernel, coordination, model/service catalogs | dedicated planning kernel | JSON | required | planning single-node tests | directly registers resources/commands on an already-built control plane |
| Context | `context_operationalization.install_single_node_context()` | almost entire base deployment + egress | context stores/runtime/skills/research and context-aware lifecycle | execution, agents, models, repository foundation, verification | replaces kernel lifecycle after kernel construction | SQLite/JSON/domain stores | required | context restart/authorization tests | private lifecycle read/write and private wrapper introspection |
| Learning | `deployment.durable_connectors` | agents, routing, evaluation, verification, planning, context skills/research | learning composition | context + planning + kernel | deployment/process | durable learning repos | required | learning/control-plane tests | built after control plane exists and self-registers |
| Handoffs | `deployment.durable_connectors` | agents/runtime, coordination, kernel tasks, auth, verification, context research/skills | handoff runtime/repositories | context + coordination + verification | deployment/process | SQLite/JSON | required | `test_deployment_composition.py` | receives Control Plane during construction and self-registers |
| Control Plane | `deployment.single_node`, then extended by durable builder | broad set of services and modules | `ControlPlane` compatibility facade/modules | almost every service | deployment/process | through owned services | required | control-plane composition/release tests | created before several durable domains and then repeatedly extended |
| HTTP/ASGI | `deployment.single_node` | control plane + authentication/config | authenticated HTTP facade + ASGI app | security/control plane | ASGI server/process | none directly | required | runtime hardening/API tests | constructed before all durable Control Plane registrations are complete |
| Optional registry/plugin host integration | `adapters.single_node_app` | registry catalog config, signature keys, deployment | registry provider/install store/plugin registry/distribution integration | public control-plane/capability seams | host adapter | JSON/filesystem | optional | registry/plugin integration tests | correctly outside the core root; must stay gated |
| Startup/recovery | `deployment.server` | completed deployment | restore + startup reconciliation, uvicorn serving | kernel, coordinator, distributed runtime, reviewer recovery | server/process | reads durable state | command-dependent | `test_single_node_startup_recovery.py`, restart E2E | there is no central `SingleNodeDeployment.close()` contract today |

## Current construction dependency graph

Dependency types below are `C` construction, `R` runtime, `L` lifecycle, `P` persistence and `O` optional integration.

```text
SingleNodeConfig
  C/P -> storage foundation
  C   -> observability

storage foundation
  C/P -> repository foundation
  C/P -> agents/conversations/capabilities/models/routing
  C/P -> security/auth
  C/P -> verification

observability
  C/R -> security wrappers
  C/R -> model/execution/reviewer/learning event sinks
  C/R -> health

security/auth
  C/R -> repository service/management
  C/R -> workflows/research
  C/R -> execution authorization wrapper
  C/R -> planning/connectors/application distribution
  C/R -> control plane + HTTP
  O   -> authorized secrets

egress foundation
  C/R -> model runtime
  C/R -> connectors/context capability invocation

agents + capabilities + models
  C/R -> agent runtime
  C/R -> planning
  C/R -> context
  C/R -> learning/handoffs

repository foundation
  R   -> execution workspace resolver/result observer
  R   -> context repository source adapter
  C/R -> repository runtime integration after kernel exists

workspaces + files + run bindings
  C/R -> execution
  C/R -> repository foundation/runtime
  C/R -> application builds
  C/R -> context

execution lifecycle participants
  C/L -> context final lifecycle

verification
  C/R -> completion authority
  C/R -> kernel completion
  C/R -> automatic reviewer
  C/R -> release gate

context final lifecycle
  L   -> kernel

kernel
  C/R -> repository run integration
  R   -> coordination/reviewer/planning coordinator

planning
  R   -> context plan sources at runtime
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

### Current implicit cycles, staged dependencies and late binding

The current code contains one important **staged construction dependency**, but no unavoidable domain cycle once it is represented explicitly:

- `RepositoryWorkspaceExecutionCoordinator` must exist before the execution lifecycle because the executor consumes its workspace resolver and terminal-result observer.
- `RepositoryRunIntegration` cannot exist until the main `PlatformKernel` exists.
- The current root therefore constructs the repository workspace coordinator early and calls `configure_run_integration(...)` later. #890 must model this as an explicit `RepositoryFoundationBundle` followed by a post-kernel `RepositoryRuntimeBundle`, rather than pretending repository construction is one phase or hiding the late binding inside a generic builder.

Other construction-order debt is also late wiring rather than a necessary domain cycle:

- Context needs the execution lifecycle inputs, but is installed only after a completed kernel exists; it therefore reads and replaces the kernel lifecycle. Most Context construction already depends on `kernel_repository`, coordination repository, repository services and other public authorities rather than on the kernel object. The target must expose execution participants before kernel construction and pass the **final** context-aware lifecycle into the kernel once.
- Egress is built after `ModelRuntime`, so `model_runtime.egress_gate` is assigned after construction even though `EgressDeploymentBindings.model_runtime(...)` already demonstrates that the gate can be a constructor dependency.
- Application release distribution is created before the canonical gate coordinator and receives it through mutable state in the public wrapper.
- Control-plane resource/command/module registration continues after the base Control Plane and HTTP objects already exist.
- `durable_connectors` rebuilds the durable dataclass by reflecting every field from the base dataclass, coupling the two aggregate shapes implicitly.

## Private and hidden construction wiring findings

| Location | Consumer | Private/hidden dependency | Why it is used now | Current owner | Correct public construction seam | Risk | #892 overlap | Recommended #890 change |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `deployment/durable_connectors.py` | automatic reviewer composition | `base.kernel._completion_authority` | reviewer/repair/output coordination need the canonical Verification completion authority | Verification/kernel composition | `VerificationBundle.completion_authority` passed explicitly | high: kernel internals define another subsystem's construction | Verification offload is merged; wait for #892 close before touching kernel/persistence seams | return completion authority from Verification builder and inject it directly |
| `deployment/context_operationalization.py` | context composition | `base.kernel._lifecycle` read | context needs the existing execution participant as fallback | execution/kernel composition | `ExecutionBundle.base_lifecycle` passed explicitly | critical: construction depends on kernel internal mutable state | lifecycle itself is not changed by active #1019, but execution/persistence acceptance remains under #892 | build context-aware lifecycle before kernel; inject once |
| `deployment/context_operationalization.py` | context composition | `getattr(previous_lifecycle, "_inner", ...)` | unwrap authorization decorator to recover the underlying execution backend | execution composition | typed `ExecutionBundle.base_lifecycle` / `workspace_executor` outputs | critical: wrapper topology is an undocumented contract | indirect runtime overlap | never unwrap decorators in composition; carry required participant explicitly |
| `deployment/context_operationalization.py` | context composition | assignment to `base.kernel._lifecycle` | installs canonical context-aware execution after kernel already exists | kernel | kernel builder receives final lifecycle | critical: mutates runtime semantics after construction | indirect runtime overlap | remove post-construction lifecycle replacement |
| `deployment/durable_connectors.py` | egress composition | `base.model_runtime.egress_gate = ...` | egress is built after model runtime | model runtime/egress composition | explicit egress gate input to model-runtime builder | high: hidden mutable construction contract | no direct #1019 file overlap | build egress foundation before model runtime |
| `deployment/single_node.py` | repository execution composition | `repository_workspace_execution.configure_run_integration(...)` | run integration needs the kernel, while workspace execution is needed before kernel construction | repository composition | explicit two-stage `RepositoryFoundationBundle` -> `RepositoryRuntimeBundle` binding contract | medium-high: public API but hidden construction phase | repository catalog/offload was part of #892 | make the post-kernel bind explicit, one-time and directly tested; remove it later only if an immutable equivalent is possible without changing runtime semantics |
| `deployment/__init__.py` | application distribution | assignment to `application_releases.gate_coordinator` | evaluation/verification gate is added only by public wrapper | application distribution | release builder input `gate_coordinator` | high: public builder layers return partially constructed service | verification/evaluation persistence already merged | construct release gate before service is returned |
| `deployment/durable_connectors.py` | final durable aggregate | `fields(BaseSingleNodeDeployment)` + `getattr(base, ...)` | copies every base field into subclass aggregate | deployment composition | explicit typed aggregate/bundle construction | medium: dataclass shape becomes an implicit construction API | none direct | remove reflection once builders return typed bundles/final aggregate |

Test-only private field assertions (for example Verification hardening inspecting `_completion_authority`, and handoff restart coverage inspecting repository paths) are characterization debt, not production composition dependencies. Replace them only after equivalent public construction/test seams exist; do not delete coverage merely to remove underscore access.

## Target builder architecture

### Rules

- Preserve the public `ai_multi_agent_platform.deployment.build_single_node_deployment(...)` API and final `SingleNodeDeployment` surface during #890.
- Builders use typed dataclass/protocol inputs and typed bundle outputs; no dictionaries, service locator, container or runtime lookup registry.
- A builder may depend only on earlier bundles shown in the target DAG. It must not accept the whole `SingleNodeDeployment` aggregate.
- Domain services remain in their existing packages for #890. Builder code may initially remain under `deployment`; package movement is #895.
- Runtime services are fully constructed when returned. Mutation-based "finish wiring" is prohibited except for the explicitly named repository foundation/runtime binding phase until that existing two-stage dependency is safely removed.
- Optional adapters are passed as explicit optional inputs and must not be imported/started merely by importing the baseline deployment package.
- #890 must preserve current startup, recovery, persistence, authorization, health, API/schema and restart behavior. It must not invent a new shutdown protocol.
- Control Plane registration happens after domain construction. Domain builders return services/modules; they do not require a completed Control Plane merely to construct their runtime state.

### Proposed builders and public bundle contracts

The exact dataclass names may be adjusted during implementation, but the fields below are the minimum public construction contract needed to remove current private wiring.

#### `build_storage(config) -> StorageBundle`

**Responsibility:** canonical single-node storage primitives and durable path ownership.

**Exact inputs:** prepared `SingleNodeConfig`.

**Exact outputs/public types:** kernel repository; scope store/service; file provider; workspace provider; run-workspace binding repository; repository binding catalog; repository registry; repository provenance store; restored managed-local repository registrations; evaluation project identity where the current compatibility surface still requires it.

**Lifecycle ownership:** unchanged provider/process ownership; no new aggregate `close()` semantics.

**Allowed dependencies:** config, filesystem and existing persistence adapters.

**Forbidden dependencies:** Control Plane, HTTP, agents, model runtime, kernel, context, learning.

**Optional adapters:** none.

**Tests:** temporary-root construction, canonical path layout, same-root restart, existing #892 connection/executor regressions.

#### `build_observability(exporter=None) -> ObservabilityBundle`

**Responsibility:** exporter and telemetry foundation.

**Exact inputs:** optional supported observability exporter.

**Exact outputs:** effective exporter and `Telemetry`.

**Lifecycle ownership:** current exporter/process behavior.

**Allowed dependencies:** observability package only.

**Forbidden dependencies:** domain services and Control Plane.

**Optional adapters:** supplied exporter.

**Tests:** default in-memory exporter and explicit exporter paths.

#### `build_security(config, storage, observability, *, secret_provider=None) -> SecurityBundle`

**Responsibility:** authentication, authorization, audit, approval and protected secret access.

**Exact inputs:** config/database path; storage authorities needed by supported security services; telemetry; optional `SecretProvider`.

**Exact outputs:** authentication service/store; canonical authorization provider; observed authorization provider; authorization audit sink; approval service; `AuthorizationGate`; Control Plane authorization bridge; optional `AuthorizedSecretProvider` facade; security-owned canonical service policies.

**Lifecycle ownership:** current repositories/providers retain ownership.

**Allowed dependencies:** StorageBundle and ObservabilityBundle.

**Forbidden dependencies:** kernel internals, Control Plane registration, HTTP, planning/context.

**Optional adapters:** secret provider.

**Tests:** no-secret baseline, secret-provider path, canonical service-policy installation, audit/gate wiring, restart.

#### `build_egress(config, security, observability) -> EgressBundle`

**Responsibility:** one durable egress policy runtime/gate and outbound factories.

**Exact inputs:** egress persistence path from config; authorization provider; approval gate; telemetry/audit sink.

**Exact outputs:** durable egress runtime; `EgressDeploymentBindings`; canonical gate.

**Lifecycle ownership:** current durable egress provider/process ownership.

**Allowed dependencies:** SecurityBundle, ObservabilityBundle and config path.

**Forbidden dependencies:** ModelRuntime mutation, Control Plane registration, connector provider startup.

**Optional adapters:** none in the baseline foundation.

**Tests:** shared gate identity across model/capability/connector/context/file factory paths; persistence restart.

#### `build_runtime_services(storage, security, egress, observability, *, onboarding_model_adapters=()) -> RuntimeServicesBundle`

**Responsibility:** agents, conversations, capabilities, models, routing and onboarding.

**Exact inputs:** agent/conversation/model/routing storage paths; scopes; security authorization; canonical egress gate; telemetry; optional onboarding model adapters.

**Exact outputs:** agent service/repository; conversation service; capability registry; model registry; routing profile repository/service/assignment gate; agent runtime; onboarding service; `ModelRuntime` created with the final egress gate; conversation response provider.

**Lifecycle ownership:** current deployment/process ownership; onboarding restore remains part of construction.

**Allowed dependencies:** Storage, Security, Egress and Observability bundles.

**Forbidden dependencies:** kernel/private execution state and Control Plane.

**Optional adapters:** onboarding model adapters only.

**Tests:** no optional adapters, deterministic onboarding restore, model-runtime egress gate, routing/agent construction.

#### `build_repository_foundation(config, storage, security, *, repository_discovery_resolver=None) -> RepositoryFoundationBundle`

**Responsibility:** repository services and pre-kernel execution hooks that are required before the main kernel can exist.

**Exact inputs:** repository registry/catalog/provenance from StorageBundle; workspaces/run bindings/files; approval gate; managed-local repository root; optional repository discovery resolver.

**Exact outputs:** `RepositoryService`; `RepositoryManagementService`; `RepositoryWorkspaceExecutionCoordinator`; `RepositoryEventRuntimeIngress`; public references to registry/catalog/provenance needed by later builders.

**Lifecycle ownership:** current repository/provider ownership; no kernel binding yet.

**Allowed dependencies:** StorageBundle and SecurityBundle.

**Forbidden dependencies:** main kernel, Context, Control Plane.

**Optional adapters:** repository discovery resolver.

**Tests:** construction without kernel; workspace fallback behavior; management authorization; event-ingress construction; explicit proof that run integration is not silently available yet.

#### `build_execution(config, storage, security, observability, runtime_services, repository_foundation, *, distributed_runtime=None, enable_distributed_execution=False) -> ExecutionBundle`

**Responsibility:** reference execution/orchestration and explicit lifecycle participants.

**Exact inputs:** workspaces; repository workspace execution hooks; approval gate; telemetry; agent runtime/model runtime; optional distributed runtime and distributed-execution flag.

**Exact outputs:** reference orchestrator; reference executor; observed orchestrator/executor; local or distributed execution lifecycle; the undecorated/base lifecycle participant required by Context; workspace executor details required by tests; effective distributed runtime where the current public deployment exposes it.

**Lifecycle ownership:** defines lifecycle participants but does not construct or mutate the kernel.

**Allowed dependencies:** Storage, Security, Observability, RuntimeServices and RepositoryFoundation bundles.

**Forbidden dependencies:** Control Plane, private kernel state, Context mutation.

**Optional adapters:** distributed runtime/enable flag.

**Tests:** local baseline, distributed opt-in, missing distributed runtime behavior, public lifecycle descriptor/behavior, repository workspace hooks.

#### `build_verification(config, storage, security, observability) -> VerificationBundle`

**Responsibility:** canonical Verification persistence and completion authority.

**Exact inputs:** verification persistence path; files/agent repository only where evidence construction can remain independent; security/telemetry inputs required by current runtime.

**Exact outputs:** verification service; **publicly carried** `VerificationCompletionAuthority`; async completion adapter where needed; verification persistence/evidence prerequisites that do not require the main kernel.

**Lifecycle ownership:** existing verification repository/service ownership.

**Allowed dependencies:** Storage/Security/Observability.

**Forbidden dependencies:** reading the authority back from a kernel.

**Optional adapters:** none.

**Tests:** authority/service share canonical durable state; restart; #892 async persistence regressions.

#### `build_context_foundation(config, storage, security, egress, runtime_services, execution, verification, repository_foundation) -> ContextBundle`

**Responsibility:** context stores/assembly/runtime, memory/knowledge/skills/research source adapters and the canonical context-aware execution lifecycle.

**Exact inputs:** config paths; kernel repository for task/run projections; coordination repository or an explicit coordination prerequisite if it must be created before the main coordinator; agents/model runtime; capabilities; files; repository service/provenance; Verification service/evidence prerequisites; egress gate; execution base lifecycle; approval/authorization authorities; canonical Research service.

**Exact outputs:** current context repositories/services/runtime; skills/research authorities; reconciliation report; **final authorized/project-scoped context-aware lifecycle** to pass into the main kernel; Context Control Plane module/registration contribution rather than a Control Plane instance.

**Lifecycle ownership:** returns final lifecycle; does not install it into an existing kernel.

**Allowed dependencies:** public bundle fields only.

**Forbidden dependencies:** completed `SingleNodeDeployment`; `kernel._lifecycle`; wrapper `._inner`; Control Plane mutation.

**Optional adapters:** egress/capability turn only if the supported lower-level embedding intentionally omits egress; the normal public single-node path supplies it.

**Tests:** lifecycle wrapper order/behavior through public protocols; context bundle/run binding restart; source authorization/redaction; no private kernel access.

#### `build_kernel(storage, execution, verification, context, security, observability) -> KernelBundle`

**Responsibility:** main Task/Run kernel, canonical coordination and Verification evidence that truly needs the kernel.

**Exact inputs:** kernel repository; observed orchestrator; final lifecycle from ContextBundle; canonical completion authority from VerificationBundle; observability event sink; files/agents for verification evidence; coordination persistence path.

**Exact outputs:** `PlatformKernel`; coordination repository/service; final canonical Verification runtime/evidence resolver; first-run Task service inputs if retained here.

**Lifecycle ownership:** kernel owns use of the supplied lifecycle, but not construction-time mutation of it.

**Allowed dependencies:** public Storage, Execution, Verification, Context, Security and Observability bundle fields.

**Forbidden dependencies:** private fields of any supplied runtime object.

**Optional adapters:** none beyond already-selected execution mode.

**Tests:** kernel receives exact final lifecycle/completion authority; coordination construction; Task/Run golden path; restart.

#### `build_repository_runtime(storage, repository_foundation, kernel) -> RepositoryRuntimeBundle`

**Responsibility:** complete the explicitly staged repository construction after the kernel exists.

**Exact inputs:** repository registry/provenance; workspaces/files; main kernel; pre-kernel `RepositoryWorkspaceExecutionCoordinator`.

**Exact outputs:** `RepositoryRunIntegration`; the finalized repository workspace execution binding; any kernel-bound repository runtime authority.

**Lifecycle ownership:** deployment/process; completion of construction must happen before runtime use.

**Allowed dependencies:** StorageBundle, RepositoryFoundationBundle and KernelBundle.

**Forbidden dependencies:** Control Plane, Context internals, private kernel fields.

**Optional adapters:** none.

**Tests:** bind exactly once; clearly fail/forbid runtime use before required binding where the current type permits it; repository run/materialization/output behavior; restart; #892 final responsiveness regression.

**Migration rule:** retaining `configure_run_integration(...)` temporarily is acceptable only as this named, directly tested one-time construction phase. Removing that method is optional and must not force repository/runtime semantic changes into #890.

#### `build_evaluation(...) -> EvaluationBundle`

**Responsibility:** preserve the existing `build_single_node_evaluation(...)` composition behind an explicit single-node bundle boundary.

**Exact inputs:** database/asset paths; kernel; agents/agent runtime; models/model runtime; reference orchestrator/executor; files/workspaces/run bindings; evaluation project identity; evidence providers; approval reader; optional distributed runtime.

**Exact outputs:** current evaluation repository/service/composition plus resource/command Control Plane contribution.

**Lifecycle ownership:** existing evaluation composition ownership.

**Allowed dependencies:** completed Kernel, Execution, RuntimeServices, Storage and Security bundles.

**Forbidden dependencies:** Control Plane mutation during service construction.

**Optional adapters:** accounting evidence provider and distributed runtime.

**Tests:** existing evaluation single-node/E2E and evidence-provider composition tests.

#### `build_planning(config, storage, security, runtime_services, kernel, observability) -> PlanningBundle`

**Responsibility:** planning, replanning and its dedicated planning kernel.

**Exact inputs:** planning persistence path; shared kernel repository; main kernel and coordination authorities; agents; capabilities; models; approval gate; telemetry.

**Exact outputs:** planning repository; dedicated planning kernel; `ReferencePlanningService`; `ReplanningEvidenceBridge`; planning resource/command Control Plane contribution.

**Lifecycle ownership:** dedicated planning kernel remains deployment/process-owned as today.

**Allowed dependencies:** completed Storage, Security, RuntimeServices, Kernel and Observability bundles.

**Forbidden dependencies:** private kernel fields and an already-built Control Plane.

**Optional adapters:** none in the baseline.

**Tests:** proposal/replanning construction, environment resolver authorization, resource/command parity.

#### `build_integrations(config, storage, security, egress, runtime_services, execution, verification, kernel, repository_runtime, evaluation, *, distributed_runtime=None) -> IntegrationBundle`

**Responsibility:** durable connectors, automatic reviewer/recovery, and application distribution/release integration.

**Exact inputs:** connector persistence/registry; canonical egress bindings; authorization/secrets; kernel; **VerificationBundle.completion_authority**; final Verification runtime; agents/models/files; repository runtime; evaluation repository/service; workspaces/run bindings; optional distributed runtime.

**Exact outputs:** connector repository/registry/service; automatic reviewer workflow; reviewer recovery; reviewer output coordination/observer binding; application release repository; application build kernel; `ApplicationDistributionService`; `ApplicationReleaseGateCoordinator`; optional GitHub release publisher/provider state; connector/egress/application Control Plane contributions.

**Lifecycle ownership:** reviewer recovery remains startup-owned; dedicated application build kernel remains deployment/process-owned.

**Allowed dependencies:** public completed bundles only.

**Forbidden dependencies:** `kernel._completion_authority`, `model_runtime.egress_gate` mutation, Control Plane construction, post-return release-gate mutation.

**Optional adapters:** secrets-backed GitHub release provider and distributed build backend.

**Tests:** reviewer/repair/output flow; connector shared egress gate; local/distributed application build; release gate canonical authorities; optional GitHub provider absent/present paths.

#### `build_learning(config, security, runtime_services, verification, kernel, planning, evaluation, context, observability) -> LearningBundle`

**Responsibility:** canonical learning composition.

**Exact inputs:** learning persistence paths; agents; routing profiles; evaluation; verification; approval gate; kernel; planning; telemetry; Context skills and research services.

**Exact outputs:** current `SingleNodeLearningComposition`/learning services and its Control Plane module/registration contribution.

**Lifecycle ownership:** current learning repository/service ownership.

**Allowed dependencies:** listed public bundles only.

**Forbidden dependencies:** Control Plane instance and private runtime fields.

**Optional adapters:** none in normal single-node composition.

**Tests:** learning resource/command ownership, candidate/feedback/post-promotion persistence, restart.

#### `build_handoffs(config, security, runtime_services, verification, kernel, context, egress, observability) -> HandoffBundle`

**Responsibility:** durable handoff runtime plus its Context source contribution.

**Exact inputs:** handoff persistence paths; agents/agent runtime; coordination repository; task projection from kernel repository; authorization provider; Verification evidence; telemetry; Context research/skills/bundle/binding repositories; egress gate; model runtime.

**Exact outputs:** current handoff composition; handoff Control Plane contribution; incoming-handoff Context source adapter/binding factory required by the existing Context lifecycle.

**Lifecycle ownership:** current handoff repositories/runtime.

**Allowed dependencies:** public Security, RuntimeServices, Verification/Kernel, Context, Egress and Observability fields.

**Forbidden dependencies:** Control Plane instance merely for registration; private kernel fields.

**Optional adapters:** none in baseline.

**Tests:** durable runtime/service/repository identity, Context source contribution, restart, no handoff commands regression where current API intentionally exposes none.

#### `build_health(storage, execution, ...) -> HealthBundle`

**Responsibility:** aggregate the same readiness/health dependencies currently exposed by the single-node deployment.

**Exact inputs:** observed orchestrator; final lifecycle; file provider and any other currently required provider health dependencies.

**Exact outputs:** `AggregatedHealthProvider`.

**Lifecycle ownership:** none beyond underlying providers.

**Forbidden dependencies:** constructing or mutating domain services.

**Tests:** readiness/health parity and required-vs-optional dependency behavior.

#### `build_control_plane(...) -> ControlPlaneBundle`

**Responsibility:** create the canonical Control Plane once, then deterministically install every completed domain module/registration contribution under #982 ownership rules.

**Exact inputs:** KernelBundle; Storage scopes/workspaces/run bindings/files; Security authorization bridge/approval; RuntimeServices models/conversations/agents/routing; RepositoryFoundation/Runtime services; Evaluation; Planning; Context; Integration; Learning; Handoffs; templates/workflows/research/portability; Health; optional accounting service; all explicit resource/command/module contributions.

**Exact outputs:** `ControlPlane` plus an optional immutable registration summary used only for tests/diagnostics.

**Lifecycle ownership:** deployment/process.

**Allowed dependencies:** public services/modules from completed bundles and #982 registration contracts.

**Forbidden dependencies:** private service internals, persistence repository construction, domain behavior reimplementation, generic service locator/container.

**Optional adapters:** accounting/other already-supported optional public modules only.

**Tests:** each major registration contribution against explicit dependencies; duplicate ownership/dependency errors; registered collection/command/API manifest/OpenAPI compatibility.

#### `build_http(config, security, control_plane) -> HttpBundle`

**Responsibility:** northbound authenticated HTTP and ASGI façade only.

**Exact inputs:** completed `ControlPlane`; authentication service; secure-cookie/host-related config already required by the HTTP façade.

**Exact outputs:** `AuthenticatedControlPlaneHTTP`; `ControlPlaneASGI`.

**Lifecycle ownership:** ASGI server/process; no new deployment close protocol.

**Allowed dependencies:** SecurityBundle + ControlPlaneBundle + config.

**Forbidden dependencies:** domain construction and persistence.

**Optional adapters:** none.

**Tests:** HTTP/auth/API compatibility, ASGI lifespan startup/shutdown, health/readiness.

### Target top-level flow

```text
config.prepare_directories()

storage               = build_storage(config)
observability         = build_observability(exporter)
security              = build_security(config, storage, observability, secret_provider=...)
egress                = build_egress(config, security, observability)
runtime_services      = build_runtime_services(storage, security, egress, observability, ...)
repository_foundation = build_repository_foundation(config, storage, security, ...)
execution             = build_execution(
    config, storage, security, observability, runtime_services, repository_foundation, ...
)
verification          = build_verification(config, storage, security, observability)
context               = build_context_foundation(
    config,
    storage,
    security,
    egress,
    runtime_services,
    execution,
    verification,
    repository_foundation,
)
kernel                = build_kernel(
    storage, execution, verification, context, security, observability
)
repository_runtime    = build_repository_runtime(storage, repository_foundation, kernel)

evaluation            = build_evaluation(...)
planning              = build_planning(...)
integrations          = build_integrations(...)
learning              = build_learning(...)
handoffs              = build_handoffs(...)
health                = build_health(...)
control_plane         = build_control_plane(...all completed public bundles...)
http                  = build_http(config, security, control_plane)

deployment = SingleNodeDeployment(...explicit bundle fields...)
```

The exact source file hosting each builder can be chosen during implementation. The dependency direction above is the invariant. In particular:

- repository foundation precedes execution;
- context produces the final lifecycle before the main kernel is constructed;
- repository runtime integration follows the kernel;
- Control Plane and HTTP are assembled only after domain services are complete;
- no later builder receives the completed deployment aggregate as a service locator.

## #892 conflict matrix

At the audit baseline, #1017 is merged into `main`. The only active #892 PR is draft #1019, changing `tests/regression/persistence/test_sqlite_runtime_acceptance.py` only. Therefore there is no current textual production-code conflict, but persistence/executor semantics remain intentionally frozen until #892 closes.

| Area/file | #890 will change? | #892 current change? | Active PR/branch? | Safe to modify now? | Wait until #892? | Reason |
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
| Repository construction | repository lifecycle/workspace integration tests | runtime behavior covered; staging is implicit | pre-kernel foundation + post-kernel binding contract tests |
| Execution | single-node runtime/E2E/distributed tests | strong runtime coverage | local/distributed `ExecutionBundle` construction tests |
| Integration wiring | connector, egress, application distribution, handoff/learning composition tests | broad | direct integration bundle tests; assert explicit completion/egress inputs, not private identity |
| Control Plane construction | `docs/runtime/CONTROL_PLANE_COMPOSITION.md` contract tests plus release/control-plane tests | #982 ownership semantics established | isolated `build_control_plane` with explicit completed bundles/contributions |
| Optional adapter absent | default deployment paths run without optional registry/GitHub release providers | implicit/broad | one focused builder test per optional input when builder exists |
| Invalid dependency | ControlPlane module ownership/dependency validation exists | partial | builder-specific missing/incompatible dependency tests |
| Startup | `tests/integration/deployment/test_single_node_startup_recovery.py` | covered | keep green; no semantic change |
| Shutdown/lifespan | `tests/regression/deployment/test_runtime_hardening_v2.py` exercises ASGI lifespan startup/shutdown | API lifespan covered; no central deployment close contract | preserve; do **not** invent `SingleNodeDeployment.close()` in #890 |
| Health/readiness | runtime hardening/health provider coverage | covered | builder test that health consumes explicit authorities |
| Restart | `tests/e2e/recovery/test_durable_process_restart_e2e.py`, handoff/context restart coverage | strong | run after every stateful extraction slice |
| Persistence | #892 cohort tests and #1019 acceptance | very strong/currently active | mandatory regression gate for storage/kernel/repository slices |
| Schema/API compatibility | Control Plane/release/API integration tests | broad | compare registered resources/commands/routes before/after root simplification |
| Golden path | deployment runtime/E2E suites | broad | retain as final #890 acceptance |
| Release gate late wiring | `tests/integration/application_distribution/test_release_gate_deployment_composition.py` | already explicit | no duplicate characterization test needed |

### Characterization-test decision for this preparation branch

No new production-behavior test is added by the preparation commits. The initially suspected release-gate late-wiring gap is already explicitly covered by `test_release_gate_deployment_composition.py`, including default policy and canonical verification/evaluation/file authorities. Startup, restart, lifespan and persistence are also already represented. Adding tests against `_completion_authority`, `_lifecycle`, `_inner` or mutable object identity would freeze the implementation debt that #890 must remove. The missing tests are genuinely **new builder construction contracts**, so they belong in the implementation slices that introduce those public bundles.

## Proposed migration slices

Each slice must be mergeable independently and preserve the supported public deployment API. Re-run targeted tests plus the normal required CI after every slice. Do not combine physical package moves from #895.

### Slice 1 — Typed bundle contracts and builder test harness

**Files:** new/narrow deployment composition helper module(s), deployment tests; minimal imports in existing root.

**Goal:** define frozen typed bundle dataclasses/protocol-facing outputs for Storage, Observability, Security, Egress, RuntimeServices, RepositoryFoundation, Execution, Verification, Context, Kernel, RepositoryRuntime, Evaluation, Planning, Integration, Learning, Handoffs, Health, ControlPlane and HTTP. Add isolated test factories without moving construction yet.

**Prerequisite:** #892 closed and final persistence acceptance green.

**Expected conflicts:** low after #892; avoid broad package moves.

**Tests:** type/import construction smoke; invalid/missing dependency behavior where contracts already enforce it.

**Rollback/compatibility risk:** very low; contracts unused by production initially.

### Slice 2 — Observability, security and egress foundations

**Files:** `deployment/single_node.py`, `deployment/egress_bindings.py`, new builder module/tests.

**Goal:** extract low-cycle foundations; construct canonical egress gate before model runtime.

**Prerequisite:** Slice 1 contracts.

**Expected conflicts:** auth/egress are cross-cutting but #892 does not currently edit these files.

**Tests:** security baseline, secrets optionality, egress shared-gate contracts, existing egress/security integrations.

**Rollback/compatibility risk:** medium because authorization wrappers are cross-cutting; no API behavior changes.

### Slice 3 — Storage, runtime services and repository foundation

**Files:** `deployment/single_node.py`, builder tests; no repository implementation redesign.

**Goal:** extract settled #892 storage construction as-is; extract agents/conversations/capabilities/models/routing/onboarding; build repository service/management/workspace coordinator/event ingress before execution. `ModelRuntime` receives egress gate in constructor.

**Prerequisite:** #892 complete; Slices 1–2.

**Expected conflicts:** highest overlap with the historical #892 surface, hence this slice waits for closure.

**Tests:** #892 persistence suite, restart, model/agent/capability tests, repository-foundation construction without kernel.

**Rollback/compatibility risk:** high persistence/compatibility surface; mechanically preserve paths/types.

### Slice 4 — Execution and Verification bundles

**Files:** `deployment/single_node.py`, execution/verification builder tests.

**Goal:** execution consumes explicit repository-foundation hooks; expose `base_lifecycle`/workspace execution participants and canonical Verification completion authority as typed outputs.

**Prerequisite:** Slice 3.

**Expected conflicts:** lifecycle wrappers and verification are sensitive but #892 implementation is settled before starting.

**Tests:** execution local/distributed, verification hardening, authorization, restart.

**Rollback/compatibility risk:** high lifecycle surface; preserve wrapper order exactly.

### Slice 5 — Context lifecycle construction before kernel

**Files:** `deployment/context_operationalization.py`, `deployment/single_node.py`, context tests.

**Goal:** split current Context installer into service/lifecycle construction and later Control Plane contribution. Consume explicit execution/repository/verification authorities, return the final lifecycle, and construct the kernel with that lifecycle. Remove `_lifecycle` read/write and `_inner` introspection.

**Prerequisite:** Slice 4 explicit execution/verification outputs.

**Expected conflicts:** none textual after #892, but this is the most semantically sensitive runtime slice.

**Tests:** context operationalization, source authorization/redaction, agent execution golden path, authorization/audit event behavior, lifespan/restart.

**Rollback/compatibility risk:** highest slice; wrapper order can alter authorization/audit/context semantics. Keep focused.

### Slice 6 — Kernel and explicit repository runtime binding

**Files:** `deployment/single_node.py`, repository/kernel tests.

**Goal:** construct kernel/coordination from explicit bundles, then construct `RepositoryRunIntegration` and complete the named one-time repository runtime bind. No generic hidden second construction phase.

**Prerequisite:** Slice 5 final lifecycle.

**Expected conflicts:** repository persistence semantics are stable only after #892 closure.

**Tests:** kernel Task/Run, repository run integration/materialization/results, one-time bind contract, restart, #892 acceptance.

**Rollback/compatibility risk:** high but localized by prior slices.

### Slice 7 — Evaluation, Planning and reviewer/connector/application integrations

**Files:** `deployment/single_node.py`, `deployment/durable_connectors.py`, integration/evaluation/planning tests.

**Goal:** extract Evaluation and Planning bundles; automatic reviewer receives `VerificationBundle.completion_authority`; connector service uses EgressBundle; application release gate is passed during construction. Remove `kernel._completion_authority`, `model_runtime.egress_gate` mutation and public-wrapper gate mutation.

**Prerequisite:** Slices 4–6.

**Expected conflicts:** many services, but dependencies are explicit by this point.

**Tests:** evaluation E2E, reviewer/repair/output, release-gate deployment composition, connectors/egress, planning/replanning, optional GitHub provider absent/present.

**Rollback/compatibility risk:** medium-high.

### Slice 8 — Learning, handoffs and Context-source completion

**Files:** `deployment/durable_connectors.py`, learning/handoff/context tests.

**Goal:** extract Learning and Handoff builders; Handoffs return their incoming Context-source contribution explicitly instead of requiring Control Plane during construction. Context source registration remains an explicit pre-runtime assembly step, not private mutation.

**Prerequisite:** Planning, Integration and Context bundles.

**Expected conflicts:** low-to-medium; mostly public service wiring.

**Tests:** learning resources/persistence, handoff composition/restart, incoming handoff context, golden Agent handoff path.

**Rollback/compatibility risk:** medium.

### Slice 9 — Deterministic Control Plane, Health, HTTP and top-level root simplification

**Files:** `deployment/single_node.py`, `deployment/durable_connectors.py`, `deployment/__init__.py`, Control Plane/HTTP/deployment tests.

**Goal:** build Health, then create the Control Plane once from completed domain contributions following #982 ownership rules; build HTTP after registration is complete; public root becomes explicit linear orchestration; delete obsolete late-wiring and base-dataclass reflection paths.

**Prerequisite:** all domain builders.

**Expected conflicts:** registration/API compatibility, not persistence.

**Tests:** Control Plane ownership/dependencies, API manifest/OpenAPI/schema parity, release API, full deployment composition, runtime hardening/lifespan, health/readiness, startup recovery, process restart and golden path.

**Rollback/compatibility risk:** medium; broad diff but behavior should already be protected by prior slices.

### Slice 10 — Architecture guardrails and final handoff

**Files:** architecture tests/docs only unless a tiny cleanup remains.

**Goal:** add static guardrails that production deployment composition contains no approved private cross-component access (`._completion_authority`, `._lifecycle`, wrapper `._inner`), no hidden model/release late wiring, and no builder receives the completed deployment aggregate as a service locator. Update the final builder DAG.

**Prerequisite:** Slice 9.

**Expected conflicts:** low.

**Tests:** architecture guard + complete required CI.

**Rollback/compatibility risk:** low.

## Acceptance checklist for the eventual #890 implementation

- Public single-node builder and deployment surface remain compatible.
- Each major construction responsibility has a typed, independently testable builder/bundle.
- Repository construction explicitly models its pre-kernel foundation and post-kernel runtime integration instead of creating a false circular builder dependency.
- No builder receives the completed deployment aggregate as an implicit service locator.
- No production composition reads/writes another component's private attributes.
- Model egress, Verification completion, Context lifecycle and release gate dependencies are constructor/bundle inputs.
- Context returns the final execution lifecycle before the main kernel is constructed.
- Control Plane is assembled once from completed public modules/services under #982 ownership rules; HTTP follows it.
- Optional providers remain gated and absent by default without baseline import/startup impact.
- Existing startup/recovery/lifespan/health/API/restart/persistence behavior remains green.
- #892 persistence acceptance remains green; #890 does not redesign SQLite/offload semantics.
- No new `SingleNodeDeployment.close()` contract is invented merely for this structural refactor.
- Physical package/domain moves remain deferred to #895.
