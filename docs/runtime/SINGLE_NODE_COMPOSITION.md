# Single-node composition architecture (#890)

This document is the implementation handoff for #890, **Decompose the single-node composition root into explicit testable builders**. It records the current production composition, construction dependencies, private/late wiring, target builder contracts, #892 conflict surface, test coverage, and the migration order.

The audit baseline is `main` at `80978f13a36df8ae666ef9497ac13a0eda93982f` (`refactor(persistence): isolate SQLite executors after verification (#1017)`). Issue #892 is still open at this baseline; its remaining active PR, #1019, adds the final system-level persistence responsiveness regression and no production code. The builder refactor must still wait for #892 to close before changing persistence/executor ownership.

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
| Repository foundation | `deployment.single_node` | repository registry/catalog/provenance, workspaces, run bindings | repository service/management, workspace execution coordinator, event ingress | storage, authorization for service/management | deployment/process | repository stores | required | repository lifecycle/workspace tests | part is needed before kernel; run integration only after kernel |
| Agents/conversations | `deployment.single_node` | storage, models, auth gates | agent/conversation services and `agent_runtime` | repos, model runtime, capabilities | deployment/process | SQLite/JSON-backed repos | required | agent/runtime E2E and composition tests | cannot be built/tested as a unit |
| Models/routing | `deployment.single_node`; egress patched later in `durable_connectors` | model repo/adapters, routing profiles, telemetry | `ModelRuntime`, routing services | models, egress gate | deployment/process | local model/routing stores | required; adapters optional | model regressions/E2E | `egress_gate` is injected after construction |
| Tools/capabilities | `deployment.single_node`; host adapter adds repository capability providers | capability registry, assignments, auth/policy hooks | capability registry/assignments/providers | agents, security, egress | deployment/process | local assignments | required; providers optional | capability/control-plane tests | assignments and providers are split across construction layers |
| Platform services | `deployment.single_node` | scopes/workspaces, security, agents, templates, persistence paths | workflows, research, templates/handlers/exporters, capability assignments, portability later | storage, security, runtime services, evaluation | deployment/process | SQLite/JSON | required | template/workflow/research/portability tests | no explicit construction boundary; portability is built later than the other services |
| Security/auth/secrets | `deployment.single_node` | auth DB, authorization DB, telemetry, optional secret provider | authentication, authorization, audit, approval gate, authorized secrets | storage, telemetry | deployment/process | SQLite/local policy state | required except secrets | security/control-plane regressions | construction is mixed with execution and service registration |
| Observability | `deployment.single_node` | optional exporter | telemetry/exporter | none initially; consumed broadly | process | exporter-specific | required; exporter replaceable | observability/runtime tests | built in middle of root rather than as foundation |
| Execution | `deployment.single_node` | orchestrator, executor, workspaces, repository workspace hooks, authorization, optional distributed runtime | reference executor/orchestrator and lifecycle backend chain | security, workspaces, repository foundation | kernel uses lifecycle backend | runtime persistence indirectly | required; distributed optional | runtime/E2E/distributed tests | useful inner lifecycle objects are not public construction outputs |
| Worker/distributed execution | `deployment.single_node` only when enabled | optional `DistributedRuntime`, authorization, run-workspace bindings | effective distributed runtime + `DistributedLifecycleBackend` | execution/security | external worker/runtime owners; root owns only binding | distributed runtime state outside local builder scope | optional | distributed/failover tests | the root does **not** construct Worker processes; do not invent Worker ownership in #890 |
| Verification | `deployment.single_node` | verification DB, files/agents/kernel later | verification service/runtime, completion authority | security, persistence, kernel for evidence | deployment/process | SQLite | required | verification hardening/restart tests | completion authority is later read through private kernel state |
| Kernel/coordination | `deployment.single_node` | kernel repo, lifecycle, completion authority, orchestrator | `PlatformKernel`, coordination repository/service | execution, verification, observability | kernel/lifecycle | SQLite | required | kernel/coordination tests | Context later mutates the kernel lifecycle |
| Repository runtime integration | `deployment.single_node` | registry/provenance/workspaces/files/kernel | `RepositoryRunIntegration` bound into workspace execution | repository foundation + kernel | deployment/process | repository stores | required | repository integration/restart tests | `configure_run_integration(...)` is post-kernel binding |
| Automatic review | `deployment.durable_connectors` | kernel, verification, agents/models/files | reviewer workflow/recovery/output coordinator | kernel + verification completion authority | kernel observer + startup reconciler | verification/task state | required in durable profile | verification/reviewer tests | reads `kernel._completion_authority` |
| Egress | `deployment.durable_connectors`, `egress_bindings` | authorization, approval, telemetry, egress profile path | one durable egress runtime/gate and outbound factories | security + persistence | deployment/process | JSON profile/policy state | required for durable profile | egress/security tests | model runtime is patched after construction |
| Connectors | `deployment.durable_connectors` | connector repo/registry + egress + security | connector service | egress, authorization | deployment/process | durable connector repository | required service; providers optional | connector/control-plane tests | Control Plane registration happens immediately during construction |
| Application distribution | `durable_connectors` + public `deployment.__init__` | files/workspaces/run bindings/security/kernel repo/distributed runtime | release repository, build kernel, distribution service | execution, verification/evaluation gate | dedicated application build kernel | JSON + files | required; GitHub publisher optional | `test_release_gate_deployment_composition.py` | gate coordinator patched only after durable builder returns |
| Planning/replanning | `deployment.durable_connectors` | planning repo, agents/capabilities/models/security/coordination | planning kernel/service/replanning bridge | main kernel, coordination, model/service catalogs | dedicated planning kernel | JSON | required | planning single-node tests | directly registers resources/commands on an already-built Control Plane |
| Context | `context_operationalization.install_single_node_context()` | almost entire base deployment + egress | context stores/runtime/skills/research and context-aware lifecycle | execution, agents, models, repository foundation, verification | replaces kernel lifecycle after kernel construction | SQLite/JSON/domain stores | required | context restart/authorization tests | private lifecycle read/write and private wrapper introspection |
| Learning | `deployment.durable_connectors` | agents, routing, evaluation, verification, planning, context skills/research | learning composition | context + planning + kernel | deployment/process | durable learning repos | required | learning/control-plane tests | built after Control Plane exists and self-registers |
| Handoffs/coordination | `deployment.durable_connectors` | agents/runtime, coordination, kernel tasks, auth, verification, context research/skills | handoff runtime/repositories and incoming-handoff Context source | context + coordination + verification | deployment/process | SQLite/JSON | required | `test_deployment_composition.py` | receives Control Plane during construction and self-registers |
| Automation/notifications | Control Plane runtime composition; root supplies state paths | event repo, workspace/run bindings, automation/notification SQLite paths | durable automation runtime/services/state and notification surface | kernel events, Control Plane, workspaces | Control Plane/ASGI lifespan | SQLite | required in supported deployment; lower-level embeddings may be ephemeral | automation/recovery/runtime security tests | single-node root does not own domain construction; #890 should preserve this owner and only make the path/config dependency explicit |
| Control Plane | `deployment.single_node`, then extended by durable builder | broad set of services/modules and automation state paths | canonical `ControlPlane` façade/modules | almost every service | deployment/process | through owned services and Automation state | required | Control Plane composition/release tests | created before several durable domains and then repeatedly extended |
| HTTP/API/ASGI | `deployment.single_node` | Control Plane + authentication/config | authenticated HTTP façade + ASGI app | security/Control Plane | ASGI server/process | none directly | required | runtime hardening/API tests | constructed before all durable Control Plane registrations are complete |
| Health/readiness | `deployment.single_node` | orchestrator, lifecycle, files | aggregated health provider | execution/files | deployment/process | none directly | required | runtime hardening/health tests | construction depends on final lifecycle but happens before Context replacement today |
| Optional registry/plugin host integration | `adapters.single_node_app` | registry catalog config, signature keys, deployment | registry provider/install store/plugin registry/distribution integration | public Control Plane/capability seams | host adapter | JSON/filesystem | optional | registry/plugin integration tests | correctly outside core root; must stay gated |
| Startup/recovery/shutdown | `deployment.server` + ASGI lifespan | completed deployment | restore/startup reconciliation, reviewer recovery, automation lifespan, uvicorn serving | kernel, coordinator, distributed runtime, reviewer recovery, Control Plane | server/process/ASGI | reads durable state | command-dependent | startup recovery, runtime hardening, restart E2E | there is no central `SingleNodeDeployment.close()` contract today |

### Ownership clarifications

- **Automation/Notifications:** `build_single_node_deployment()` currently supplies durable state paths; domain/runtime construction remains inside the existing Control Plane composition. #890 should not pull Automation ownership into the deployment root merely to create another builder.
- **Workers:** the root creates a `ReferenceExecutor` and can bind a supplied/locally created `DistributedRuntime`, but it does not create long-running Worker processes. #890 therefore owns the execution binding, not Worker lifecycle.
- **Optional adapters:** registry/plugin/distribution host additions live in `adapters.single_node_app` and already use public seams. They are compatibility/acceptance surfaces, not candidates for absorption into the core composition root.

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
  C/P -> platform services

observability
  C/R -> security wrappers
  C/R -> model/execution/reviewer/learning event sinks
  C/R -> health

security/auth
  C/R -> repository service/management
  C/R -> platform workflows/research/templates
  C/R -> execution authorization wrapper
  C/R -> planning/connectors/application distribution
  C/R -> control plane + HTTP
  O   -> authorized secrets

egress foundation
  C/R -> model runtime
  C/R -> connectors/context capability invocation

agents + capabilities + models
  C/R -> agent runtime
  C/R -> platform templates/capability assignments
  C/R -> planning
  C/R -> context
  C/R -> learning/handoffs

platform services
  R   -> context research source
  R   -> evaluation/portability
  R   -> control plane/template environment

repository foundation
  R   -> execution workspace resolver/result observer
  R   -> context repository source adapter
  C/R -> repository runtime integration after kernel exists

workspaces + files + run bindings
  C/R -> execution
  C/R -> repository foundation/runtime
  C/R -> application builds
  C/R -> context
  C/R -> automation workspace event scope

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
  C/R -> coordination
  C/R -> repository run integration
  R   -> reviewer/planning coordinator
  R   -> Control Plane/Automation events

planning
  R   -> context plan sources at runtime
  R   -> learning

context
  R   -> learning
  R   -> handoffs

all completed domain services
  C   -> Control Plane module/registration assembly
Control Plane
  C/P -> Automation/Notification runtime from explicit state paths
  C   -> authenticated HTTP/ASGI
all bundles
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
- Control Plane resource/command/module registration continues after the base Control Plane and HTTP objects already exist.
- Health is built against the pre-Context lifecycle even though Context later replaces the kernel lifecycle; the refactor must preserve observable readiness semantics while making the intended final lifecycle dependency explicit.
- `durable_connectors` rebuilds the durable dataclass by reflecting every field from the base dataclass, coupling the two aggregate shapes implicitly.

## Private and hidden construction wiring findings

| Location | Consumer | Private/hidden dependency | Why it is used now | Current owner | Correct public construction seam | Risk | #892 overlap | Recommended #890 change |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `deployment/durable_connectors.py` | automatic reviewer composition | `base.kernel._completion_authority` | reviewer/repair/output coordination need canonical Verification completion authority | Verification/kernel composition | `VerificationBundle.completion_authority` | high | Verification offload merged; wait for #892 close before kernel/persistence rewiring | inject the authority directly from Verification bundle |
| `deployment/context_operationalization.py` | Context composition | `base.kernel._lifecycle` read | Context needs the existing execution participant as fallback | execution/kernel composition | `ExecutionBundle.base_lifecycle` | critical | indirect runtime overlap | build Context-aware lifecycle before kernel |
| `deployment/context_operationalization.py` | Context composition | `getattr(previous_lifecycle, "_inner", ...)` | unwrap authorization decorator to recover underlying execution backend | execution composition | typed base lifecycle/workspace execution outputs | critical | indirect runtime overlap | never unwrap decorators in composition |
| `deployment/context_operationalization.py` | Context composition | assignment to `base.kernel._lifecycle` | installs canonical Context-aware execution after kernel already exists | kernel | kernel builder receives final lifecycle | critical | indirect runtime overlap | remove post-construction lifecycle replacement |
| `deployment/durable_connectors.py` | egress/model composition | `base.model_runtime.egress_gate = ...` | egress is built after model runtime | model runtime/egress composition | explicit gate constructor input | high | no direct #1019 overlap | build egress foundation before model runtime |
| `deployment/single_node.py` | repository execution composition | `repository_workspace_execution.configure_run_integration(...)` | run integration needs kernel while workspace execution is needed before kernel | repository composition | explicit `RepositoryFoundationBundle` -> `RepositoryRuntimeBundle` bind | medium-high | repository catalog/offload part of #892 | make named one-time post-kernel bind explicit/tested |
| `deployment/__init__.py` | application distribution | assignment to `application_releases.gate_coordinator` | gate is added only by public wrapper | application distribution | release builder constructor input | high | verification/evaluation persistence merged | construct release service with gate before return |
| `deployment/durable_connectors.py` | final durable aggregate | `fields(BaseSingleNodeDeployment)` + `getattr(base, ...)` | copies all base fields into subclass aggregate | deployment composition | explicit typed aggregate/bundle construction | medium | none direct | remove reflection after builders exist |

The systematic search did not find another production composition-time `kernel._...` dependency outside the two deployment files above. Other underscore matches are class-internal implementation state or test-only characterization. Test-only private assertions (for example Verification hardening inspecting `_completion_authority`) are debt, but should be replaced only after equivalent public construction/test seams exist.

## Target builder architecture

### Rules

- Preserve the public `ai_multi_agent_platform.deployment.build_single_node_deployment(...)` API and final `SingleNodeDeployment` surface during #890.
- Builders use typed dataclass/protocol inputs and typed bundle outputs; no dictionaries, service locator, container or runtime lookup registry.
- A builder may depend only on earlier bundles shown in the target DAG. It must not accept the whole `SingleNodeDeployment` aggregate.
- Domain services remain in their existing packages for #890. Builder code may initially remain under `deployment`; package movement is #895.
- Runtime services are fully constructed when returned. Mutation-based "finish wiring" is prohibited except for the explicitly named repository foundation/runtime binding phase until the existing two-stage dependency is safely removed.
- Optional adapters are explicit optional inputs and must not make baseline import/startup depend on them.
- #890 preserves startup, recovery, persistence, authorization, health, API/schema and restart behavior. It does not invent a new shutdown protocol.
- Control Plane registration happens after domain construction. Domain builders return services/modules/registration contributions; they do not require a completed Control Plane merely to construct runtime state.
- Automation/Notification runtime construction stays with its existing Control Plane owner unless a separate architecture issue changes that ownership; #890 only makes its durable path/config input explicit.

### Proposed builders and public bundle contracts

The exact dataclass names may be adjusted during implementation. The fields below are the minimum public construction contracts needed to remove current private wiring.

#### `build_storage(config) -> StorageBundle`

**Responsibility:** canonical single-node storage primitives and durable path ownership.

**Exact inputs:** prepared `SingleNodeConfig`.

**Exact outputs/public types:** kernel repository; scope store/service; file provider; workspace provider; run-workspace binding repository; repository binding catalog; repository registry; repository provenance store; restored managed-local repository registrations; evaluation project identity where compatibility requires it.

**Lifecycle ownership:** unchanged provider/process ownership; no new aggregate `close()` semantics.

**Allowed dependencies:** config, filesystem, existing persistence adapters.

**Forbidden dependencies:** Control Plane, HTTP, agents, model runtime, kernel, Context, Learning.

**Optional adapters:** none.

**Tests:** temporary-root construction, canonical path layout, same-root restart, #892 connection/executor regressions.

#### `build_observability(exporter=None) -> ObservabilityBundle`

**Responsibility:** exporter and telemetry foundation.

**Exact inputs:** optional supported observability exporter.

**Exact outputs:** effective exporter and `Telemetry`.

**Lifecycle ownership:** current exporter/process behavior.

**Allowed dependencies:** observability package only.

**Forbidden dependencies:** domain services and Control Plane.

**Optional adapters:** supplied exporter.

**Tests:** default in-memory and explicit exporter paths.

#### `build_security(config, storage, observability, *, secret_provider=None) -> SecurityBundle`

**Responsibility:** authentication, authorization, audit, approval and protected secret access.

**Exact inputs:** config/database paths; storage authorities required by security services; telemetry; optional `SecretProvider`.

**Exact outputs:** authentication service/store; canonical and observed authorization providers; authorization audit sink; approval service; `AuthorizationGate`; Control Plane authorization bridge; optional `AuthorizedSecretProvider`; canonical service policies.

**Lifecycle ownership:** current repositories/providers.

**Allowed dependencies:** StorageBundle and ObservabilityBundle.

**Forbidden dependencies:** kernel internals, Control Plane registration, HTTP, Planning/Context.

**Optional adapters:** secret provider.

**Tests:** no-secret baseline, secret-provider path, service policies, audit/gate wiring, restart.

#### `build_egress(config, security, observability) -> EgressBundle`

**Responsibility:** one durable egress policy runtime/gate and outbound factories.

**Exact inputs:** egress persistence path; authorization provider; approval gate; telemetry/audit sink.

**Exact outputs:** durable egress runtime; `EgressDeploymentBindings`; canonical gate.

**Lifecycle ownership:** current durable egress provider/process ownership.

**Allowed dependencies:** SecurityBundle, ObservabilityBundle, config path.

**Forbidden dependencies:** ModelRuntime mutation, Control Plane registration, connector provider startup.

**Optional adapters:** none in baseline foundation.

**Tests:** shared gate identity across model/capability/connector/context/file factories; persistence restart.

#### `build_runtime_services(storage, security, egress, observability, *, onboarding_model_adapters=()) -> RuntimeServicesBundle`

**Responsibility:** agents, conversations, capabilities, models, routing and onboarding.

**Exact inputs:** agent/conversation/model/routing storage paths; scopes; security authorization; canonical egress gate; telemetry; optional onboarding model adapters.

**Exact outputs:** agent service/repository; conversation service; capability registry; model registry; routing profile repository/service/assignment gate; agent runtime; onboarding service; `ModelRuntime` created with final egress gate; conversation response provider.

**Lifecycle ownership:** deployment/process; onboarding restore remains construction behavior.

**Allowed dependencies:** Storage, Security, Egress, Observability.

**Forbidden dependencies:** kernel/private execution state, Control Plane.

**Optional adapters:** onboarding model adapters only.

**Tests:** optional adapters absent, deterministic onboarding restore, model-runtime egress gate, routing/agent construction.

#### `build_platform_services(config, storage, security, runtime_services) -> PlatformServicesBundle`

**Responsibility:** pre-kernel application services that are currently scattered through the root but are neither Storage nor Execution.

**Exact inputs:** workflow/template/research/capability-assignment persistence paths; scopes/workspaces; approval gate; agents; capability registry; routing/model registries where current template handlers require them.

**Exact outputs:** authorized workflow service; Research service/repository; template handler registry; template service/repository and static exporters; capability assignment service; any pre-kernel registration contributions owned by those services.

**Lifecycle ownership:** current repository/service ownership.

**Allowed dependencies:** Storage, Security and RuntimeServices bundles.

**Forbidden dependencies:** main kernel, Control Plane instance, private runtime state.

**Optional adapters:** none.

**Tests:** workflow authorization, research persistence/authorization, template apply/export handlers, capability assignment authorization.

**Note:** portability is intentionally not built here because it depends on Evaluation. It is completed later in `build_integrations(...)` from the already-built template/research authorities.

#### `build_repository_foundation(config, storage, security, *, repository_discovery_resolver=None) -> RepositoryFoundationBundle`

**Responsibility:** repository services and pre-kernel execution hooks required before the main kernel can exist.

**Exact inputs:** registry/catalog/provenance; workspaces/run bindings/files; approval gate; managed-local root; optional discovery resolver.

**Exact outputs:** `RepositoryService`; `RepositoryManagementService`; `RepositoryWorkspaceExecutionCoordinator`; `RepositoryEventRuntimeIngress`; public registry/catalog/provenance references.

**Lifecycle ownership:** current repository/provider ownership; no kernel binding yet.

**Allowed dependencies:** StorageBundle and SecurityBundle.

**Forbidden dependencies:** main kernel, Context, Control Plane.

**Optional adapters:** repository discovery resolver.

**Tests:** construction without kernel; workspace fallback; management authorization; event-ingress construction; proof that run integration is not silently available yet.

#### `build_execution(config, storage, security, observability, runtime_services, repository_foundation, *, distributed_runtime=None, enable_distributed_execution=False) -> ExecutionBundle`

**Responsibility:** reference execution/orchestration and explicit lifecycle participants.

**Exact inputs:** workspaces; repository workspace execution hooks; approval gate; telemetry; agent/model runtime; optional distributed runtime and enable flag.

**Exact outputs:** reference orchestrator/executor; observed wrappers; local or distributed execution lifecycle; undecorated/base lifecycle participant required by Context; effective distributed runtime where public deployment exposes it.

**Lifecycle ownership:** defines execution lifecycle participants but does not construct/mutate kernel. Worker processes remain externally owned.

**Allowed dependencies:** Storage, Security, Observability, RuntimeServices, RepositoryFoundation.

**Forbidden dependencies:** Control Plane, private kernel state, Context mutation, Worker process construction.

**Optional adapters:** distributed runtime/enable flag.

**Tests:** local baseline, distributed opt-in, missing-runtime behavior, lifecycle protocol behavior, repository workspace hooks.

#### `build_verification(config, storage, security, observability) -> VerificationBundle`

**Responsibility:** canonical Verification persistence and completion authority.

**Exact inputs:** verification persistence path; public storage/security/telemetry prerequisites that do not require main kernel.

**Exact outputs:** verification service; **publicly carried** `VerificationCompletionAuthority`; async completion adapter; kernel-independent evidence prerequisites where applicable.

**Lifecycle ownership:** existing verification repository/service ownership.

**Allowed dependencies:** Storage/Security/Observability.

**Forbidden dependencies:** reading authority back from kernel.

**Optional adapters:** none.

**Tests:** authority/service canonical state, restart, #892 async persistence regressions.

#### `build_context_foundation(config, storage, security, egress, runtime_services, platform_services, execution, verification, repository_foundation) -> ContextBundle`

**Responsibility:** context stores/assembly/runtime, memory/knowledge/skills/research source adapters and canonical Context-aware execution lifecycle.

**Exact inputs:** config paths; kernel repository for task/run projections; coordination repository prerequisite; agents/model runtime; files; repository service/provenance; Verification service/evidence prerequisites; egress gate; execution base lifecycle; approval authorities; `PlatformServicesBundle.research`.

**Exact outputs:** Context repositories/services/runtime; skills/research authorities; reconciliation report; **final authorized/project-scoped Context-aware lifecycle**; Context Control Plane contribution.

**Lifecycle ownership:** returns final lifecycle; never installs it into an existing kernel.

**Allowed dependencies:** public bundle fields only.

**Forbidden dependencies:** completed `SingleNodeDeployment`; `kernel._lifecycle`; wrapper `._inner`; Control Plane mutation.

**Optional adapters:** lower-level embeddings may omit egress only where already supported; normal public composition supplies it.

**Tests:** lifecycle wrapper behavior via protocols; bundle/binding restart; source authorization/redaction; no private kernel access.

#### `build_kernel(storage, execution, verification, context, security, observability) -> KernelBundle`

**Responsibility:** main Task/Run kernel, canonical coordination and Verification evidence that requires the kernel.

**Exact inputs:** kernel repository; observed orchestrator; final lifecycle; canonical completion authority; event sink; files/agents for evidence; coordination persistence path; onboarding/scopes/agents for first-run Task service.

**Exact outputs:** `PlatformKernel`; coordination repository/service; final Verification runtime/evidence resolver; `FirstRunTaskService`.

**Lifecycle ownership:** kernel uses supplied final lifecycle without construction-time mutation.

**Allowed dependencies:** public Storage, Execution, Verification, Context, Security, Observability fields.

**Forbidden dependencies:** private fields of supplied runtime objects.

**Optional adapters:** none beyond selected execution mode.

**Tests:** exact final lifecycle/completion authority injection; coordination; Task/Run golden path; restart.

#### `build_repository_runtime(storage, repository_foundation, kernel) -> RepositoryRuntimeBundle`

**Responsibility:** complete explicitly staged repository construction after kernel exists.

**Exact inputs:** registry/provenance; workspaces/files; main kernel; pre-kernel workspace execution coordinator.

**Exact outputs:** `RepositoryRunIntegration`; finalized repository workspace execution binding; kernel-bound repository runtime authority.

**Lifecycle ownership:** deployment/process; construction must complete before runtime use.

**Allowed dependencies:** StorageBundle, RepositoryFoundationBundle, KernelBundle.

**Forbidden dependencies:** Control Plane, Context internals, private kernel fields.

**Optional adapters:** none.

**Tests:** bind exactly once; repository run/materialization/output behavior; restart; #892 final responsiveness regression.

**Migration rule:** retaining `configure_run_integration(...)` temporarily is acceptable only as this named, directly tested one-time construction phase. Removing that method is optional and must not force repository/runtime semantic changes into #890.

#### `build_evaluation(...) -> EvaluationBundle`

**Responsibility:** preserve existing `build_single_node_evaluation(...)` behind an explicit bundle boundary.

**Exact inputs:** database/asset paths; kernel; agents/agent runtime; models/model runtime; reference orchestrator/executor; files/workspaces/run bindings; evaluation project; evidence providers; approval reader; optional distributed runtime/accounting evidence.

**Exact outputs:** evaluation repository/service/composition plus resource/command Control Plane contribution.

**Lifecycle ownership:** existing evaluation composition.

**Allowed dependencies:** completed Kernel, Execution, RuntimeServices, Storage, Security, Observability bundles.

**Forbidden dependencies:** Control Plane mutation during service construction.

**Optional adapters:** accounting evidence and distributed runtime.

**Tests:** existing evaluation single-node/E2E/evidence-provider composition.

#### `build_planning(config, storage, security, runtime_services, kernel, observability) -> PlanningBundle`

**Responsibility:** planning, replanning and dedicated planning kernel.

**Exact inputs:** planning persistence path; shared kernel repository; main kernel/coordination; agents; capabilities; models; approval gate; telemetry.

**Exact outputs:** planning repository; dedicated planning kernel; `ReferencePlanningService`; `ReplanningEvidenceBridge`; planning resource/command Control Plane contribution.

**Lifecycle ownership:** dedicated planning kernel remains deployment/process-owned.

**Allowed dependencies:** Storage, Security, RuntimeServices, Kernel, Observability.

**Forbidden dependencies:** private kernel fields, already-built Control Plane.

**Optional adapters:** none in baseline.

**Tests:** proposal/replanning construction, environment resolver authorization, resource/command parity.

#### `build_integrations(config, storage, security, egress, runtime_services, platform_services, execution, verification, kernel, repository_runtime, evaluation, *, distributed_runtime=None) -> IntegrationBundle`

**Responsibility:** durable connectors, automatic reviewer/recovery, application distribution/release integration and post-Evaluation portability.

**Exact inputs:** connector persistence/registry; canonical egress; authorization/secrets; kernel; **VerificationBundle.completion_authority**; final Verification runtime; agents/models/files; repository runtime; evaluation; workspaces/run bindings; templates/research/scopes/capabilities/routing for portability; optional distributed runtime.

**Exact outputs:** connector repository/registry/service; automatic reviewer/recovery/output coordination; application release repository/build kernel/distribution service; `ApplicationReleaseGateCoordinator`; optional GitHub release publisher/provider state; canonical portability workflow; connector/egress/application/portability Control Plane contributions.

**Lifecycle ownership:** reviewer recovery is startup-owned; application build kernel remains deployment/process-owned.

**Allowed dependencies:** public completed bundles only.

**Forbidden dependencies:** `kernel._completion_authority`; model-runtime mutation; Control Plane construction; post-return release-gate mutation.

**Optional adapters:** secrets-backed GitHub release provider and distributed build backend.

**Tests:** reviewer/repair/output; shared connector egress; application build local/distributed; release gate authorities; portability; optional GitHub provider paths.

#### `build_learning(config, security, runtime_services, verification, kernel, planning, evaluation, context, observability) -> LearningBundle`

**Responsibility:** canonical Learning composition.

**Exact inputs:** learning paths; agents; routing profiles; evaluation; verification; approval gate; kernel; planning; telemetry; Context skills/research.

**Exact outputs:** current Learning composition/services and Control Plane contribution.

**Lifecycle ownership:** current repositories/services.

**Allowed dependencies:** listed public bundles.

**Forbidden dependencies:** Control Plane instance, private runtime fields.

**Optional adapters:** none in baseline.

**Tests:** resource/command ownership, feedback/candidate/post-promotion persistence, restart.

#### `build_handoffs(config, security, runtime_services, verification, kernel, context, egress, observability) -> HandoffBundle`

**Responsibility:** durable Handoff runtime and incoming Context-source contribution.

**Exact inputs:** handoff paths; agents/agent runtime; coordination repository; task projection; authorization provider; Verification evidence; telemetry; Context research/skills/bundle/binding repos; egress gate; model runtime.

**Exact outputs:** current Handoff composition; Control Plane contribution; incoming-handoff Context source adapter/binding factory.

**Lifecycle ownership:** current handoff repositories/runtime.

**Allowed dependencies:** public Security, RuntimeServices, Verification/Kernel, Context, Egress, Observability fields.

**Forbidden dependencies:** Control Plane instance merely for registration; private kernel fields.

**Optional adapters:** none.

**Tests:** durable identity, incoming Context source, restart, current command/resource parity.

#### `build_health(storage, execution, context) -> HealthBundle`

**Responsibility:** aggregate the same readiness/health dependencies exposed by supported single-node deployment, using the intended final lifecycle authority.

**Exact inputs:** observed orchestrator; final Context-aware lifecycle (or the explicitly selected final lifecycle); file provider.

**Exact outputs:** `AggregatedHealthProvider`.

**Lifecycle ownership:** none beyond underlying providers.

**Allowed dependencies:** Storage/Execution/Context public health-provider protocols.

**Forbidden dependencies:** constructing/mutating domain services.

**Optional adapters:** additional explicitly-supported health providers only.

**Tests:** readiness/health parity and required-vs-optional behavior.

#### `build_control_plane(config, ...) -> ControlPlaneBundle`

**Responsibility:** create canonical Control Plane once, deterministically install every completed domain module/registration under #982 ownership rules, and preserve the existing Control Plane-owned Automation/Notification runtime construction.

**Exact inputs:** Kernel; Storage scopes/workspaces/run bindings/files; Security authorization bridge/approval; RuntimeServices models/conversations/agents/routing; PlatformServices workflows/research/templates/capability assignments; Repository Foundation/Runtime; Evaluation; Planning; Context; Integration; Learning; Handoffs; Health; optional accounting service; explicit resource/command/module contributions; `automation.sqlite3` and `notifications.sqlite3` paths from config.

**Exact outputs:** canonical `ControlPlane`; canonical Automation/Notification runtime/services already owned by that Control Plane; optional immutable registration summary for diagnostics/tests.

**Lifecycle ownership:** deployment/process plus existing Control Plane/ASGI Automation lifecycle.

**Allowed dependencies:** public completed services/modules and #982 registration contracts.

**Forbidden dependencies:** private service internals, unrelated domain construction, generic service locator/container, Worker process ownership.

**Optional adapters:** accounting and already-supported optional public modules only.

**Tests:** module ownership/dependencies; automation durable vs lower-level ephemeral paths; duplicate ownership; collection/command/API manifest/OpenAPI compatibility.

#### `build_http(config, security, control_plane) -> HttpBundle`

**Responsibility:** northbound authenticated HTTP and ASGI façade only.

**Exact inputs:** completed Control Plane; authentication service; secure-cookie/host config.

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
platform_services     = build_platform_services(config, storage, security, runtime_services)
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
    platform_services,
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
health                = build_health(storage, execution, context)
control_plane         = build_control_plane(config, ...all completed public bundles...)
http                  = build_http(config, security, control_plane)

deployment = SingleNodeDeployment(...explicit bundle fields...)
```

The exact source file hosting each builder can be chosen during implementation. The dependency direction above is the invariant. In particular:

- repository foundation precedes execution;
- Platform services give Context its Research authority without depending on the kernel;
- Context produces the final lifecycle before the main kernel is constructed;
- repository runtime integration follows the kernel;
- actual Worker processes remain outside this root;
- Automation/Notifications remain owned by the Control Plane and receive explicit state paths;
- Control Plane and HTTP are assembled only after domain services are complete;
- no later builder receives the completed deployment aggregate as a service locator.

## #892 Conflict Matrix

At the audit baseline, #1017 is merged into `main`. The only active #892 PR is #1019, changing `tests/regression/persistence/test_sqlite_runtime_acceptance.py` only. Therefore there is no current textual production-code conflict, but persistence/executor semantics remain intentionally frozen until #892 closes.

| Area/file/responsibility | #890 will change it? | #892 currently changes it? | Active PR/branch? | Safe to modify now? | Wait until #892? | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| `deployment/single_node.py` storage/kernel construction | yes | no textual change after #1017; semantic acceptance still open | #1019 / `issue-892-final-runtime-acceptance` | audit/docs only | **yes for refactor** | central SQLite/repository/executor construction surface |
| `deployment/durable_connectors.py` | yes | no | #1019 indirect | audit/docs only | yes for runtime rewiring | reviewer/verification/kernel and persistent integrations |
| `deployment/context_operationalization.py` | yes | no | none direct | audit/docs only | yes for lifecycle rewrite | lifecycle semantics are runtime-critical even without textual conflict |
| `deployment/egress_bindings.py` | likely small/none | no | none | docs/test analysis | not strictly, but implement with main #890 slices | existing helper already models explicit gate seam |
| `deployment/__init__.py` public wrapper | yes | no | none | characterization/doc only | preferably | preserve public composition while #892 acceptance is running |
| `adapters/single_node_app.py` | probably compatibility-only | no | none | yes | no | optional host composition should remain outside core refactor |
| `deployment/server.py` | probably compatibility-only | no | none | yes | no | startup/recovery is acceptance surface, not builder ownership |
| SQLite/offload helpers and repository adapters | no direct redesign | #1017 just merged | merged | no new #890 changes | **yes** | #890 consumes settled repository semantics; must not reopen #892 |
| `tests/regression/persistence/test_sqlite_runtime_acceptance.py` | run as acceptance | **yes** | #1019 | do not edit | yes | active #892 final evidence |
| CI workflows | no runtime changes | no | PR #1018 `ci/consolidate-actions-workflows` | avoid unrelated edits | no | separate CI-only track |
| `docs/runtime/SINGLE_NODE_COMPOSITION.md` | preparation artifact | no | this branch | **yes** | no | documentation-only, no semantic collision |

## Test Matrix

The current suite already gives strong aggregate behavior coverage. #890 should add **builder-unit construction tests as each builder appears**, not tests of today's private fields merely to make the refactor easier.

| Category | Existing representative coverage | Current assessment | #890 addition |
| --- | --- | --- | --- |
| Storage construction/persistence | restart/recovery; #892 per-domain persistence regressions; #1019 final acceptance | strong behavior, no isolated builder test | direct StorageBundle tests after #892 closes |
| Security construction | security/control-plane regressions and authenticated deployment E2E | behavior covered, construction entangled | `build_security` with/without secrets |
| Agent/model construction | agent/model regressions | behavior covered | RuntimeServicesBundle tests |
| Platform services | template/workflow/research/capability-assignment tests | domain behavior covered separately | PlatformServicesBundle construction tests |
| Repository construction | repository lifecycle/workspace integration tests | runtime behavior covered; staging implicit | pre-kernel foundation + post-kernel binding tests |
| Planning construction | planning single-node tests | behavior covered | PlanningBundle construction tests |
| Execution | single-node runtime/E2E/distributed tests | strong | local/distributed ExecutionBundle tests |
| Worker/distributed path | distributed/failover tests | root does not own Worker process lifecycle | assert optional distributed binding; no Worker builder invented |
| Verification | verification hardening/restart | strong behavior | explicit completion-authority bundle tests |
| Integration wiring | connector, egress, app distribution, reviewer, handoff/learning tests | broad | direct Integration/Learning/Handoff bundle tests |
| Automation/notifications | automation recovery/runtime security + Control Plane composition | existing owner already tested | ControlPlane builder tests durable paths and preserves owner |
| Control Plane construction | `CONTROL_PLANE_COMPOSITION.md` contract tests + release/control-plane tests | #982 ownership established | isolated deterministic final assembly test |
| Optional adapter absent | default deployment works without registry/GitHub release providers | implicit/broad | focused absent path per optional builder input |
| Invalid dependency | Control Plane module ownership/dependency validation | partial | builder-specific missing/incompatible dependency tests |
| Startup | `tests/integration/deployment/test_single_node_startup_recovery.py` | covered | keep green |
| Shutdown/lifespan | `tests/regression/deployment/test_runtime_hardening_v2.py` | ASGI/automation lifespan covered; no deployment close contract | preserve; do not invent `SingleNodeDeployment.close()` |
| Health/readiness | runtime hardening/health tests | covered | HealthBundle final-lifecycle construction test |
| Restart | durable process restart + handoff/context restart coverage | strong | run after every stateful extraction slice |
| Persistence | #892 cohort tests + #1019 | very strong/currently active | mandatory gate for storage/kernel/repository slices |
| Schema/API compatibility | Control Plane/release/API integration | broad | registered resources/commands/routes/OpenAPI parity |
| Golden path | deployment runtime/E2E | broad | final #890 acceptance |
| Release gate late wiring | `test_release_gate_deployment_composition.py` | already explicit | no duplicate prep characterization needed |

### Characterization-test decision for this preparation branch

No new production-behavior test is added by the preparation commits. The initially suspected release-gate late-wiring gap is already explicitly covered by `test_release_gate_deployment_composition.py`, including default policy and canonical verification/evaluation/file authorities. Startup, restart, lifespan, Automation lifecycle and persistence are also represented. Adding tests against `_completion_authority`, `_lifecycle`, `_inner` or mutable object identity would freeze the implementation debt that #890 must remove. The missing tests are genuinely **new builder construction contracts**, so they belong in the implementation slices that introduce those public bundles.

## Proposed Migration Slices

Each slice must be independently mergeable and preserve the public deployment API. Re-run targeted tests plus required CI after every slice. Do not combine physical package moves from #895.

### Slice 1 — Typed bundle contracts and builder test harness

**Files:** new/narrow deployment composition helper module(s), deployment tests; minimal imports in existing root.

**Goal:** define typed bundle outputs for Storage, Observability, Security, Egress, RuntimeServices, PlatformServices, RepositoryFoundation, Execution, Verification, Context, Kernel, RepositoryRuntime, Evaluation, Planning, Integration, Learning, Handoffs, Health, ControlPlane and HTTP. Add isolated test factories without moving production construction yet.

**Prerequisites:** #892 closed; final persistence acceptance green.

**Expected conflicts:** low after #892; avoid package moves.

**Tests:** import/type construction smoke; invalid dependency behavior where contracts enforce it.

**Rollback/compatibility risk:** very low.

### Slice 2 — Observability, Security and Egress foundations

**Files:** `deployment/single_node.py`, `deployment/egress_bindings.py`, builder module/tests.

**Goal:** extract low-cycle foundations; construct canonical egress gate before model runtime.

**Prerequisites:** Slice 1.

**Expected conflicts:** auth/egress cross-cutting but no current #892 textual overlap.

**Tests:** security baseline, secret optionality, egress shared-gate, existing egress/security integration.

**Rollback/compatibility risk:** medium.

### Slice 3 — Storage, RuntimeServices, PlatformServices and RepositoryFoundation

**Files:** `deployment/single_node.py`, builder tests; no repository implementation redesign.

**Goal:** extract settled #892 storage as-is; agents/conversations/capabilities/models/routing/onboarding; workflows/research/templates/capability assignments; repository service/management/workspace coordinator/event ingress. `ModelRuntime` receives egress gate at construction.

**Prerequisites:** #892 complete; Slices 1–2.

**Expected conflicts:** highest historical #892 overlap, hence wait for closure.

**Tests:** #892 persistence suite, restart, model/agent/capability, workflow/research/template, repository foundation without kernel.

**Rollback/compatibility risk:** high persistence/compatibility surface; preserve paths/types mechanically.

### Slice 4 — Execution and Verification bundles

**Files:** `deployment/single_node.py`, execution/verification tests.

**Goal:** execution consumes explicit repository foundation hooks; expose base lifecycle and canonical Verification completion authority as typed outputs; preserve optional distributed binding without taking Worker ownership.

**Prerequisites:** Slice 3.

**Expected conflicts:** lifecycle/verification sensitive.

**Tests:** local/distributed execution, Verification hardening, authorization, restart.

**Rollback/compatibility risk:** high; preserve wrapper order.

### Slice 5 — Context lifecycle construction before Kernel

**Files:** `deployment/context_operationalization.py`, `deployment/single_node.py`, Context tests.

**Goal:** split current Context installer into service/lifecycle construction and later Control Plane contribution. Consume explicit Execution/PlatformServices/Repository/Verification authorities, return final lifecycle, construct kernel with it. Remove `_lifecycle` read/write and `_inner` introspection.

**Prerequisites:** Slice 4.

**Expected conflicts:** no textual #892 overlap after closure; highest semantic sensitivity.

**Tests:** Context operationalization, source authorization/redaction, execution golden path, authorization/audit events, lifespan/restart.

**Rollback/compatibility risk:** highest slice.

### Slice 6 — Kernel and explicit RepositoryRuntime binding

**Files:** `deployment/single_node.py`, repository/kernel tests.

**Goal:** construct kernel/coordination from explicit bundles; then construct `RepositoryRunIntegration` and complete named one-time repository runtime bind.

**Prerequisites:** Slice 5 final lifecycle.

**Expected conflicts:** repository persistence semantics must be settled by #892.

**Tests:** kernel Task/Run, repository run/materialization/results, one-time bind, restart, #892 acceptance.

**Rollback/compatibility risk:** high but localized.

### Slice 7 — Evaluation, Planning and durable Integrations

**Files:** `deployment/single_node.py`, `deployment/durable_connectors.py`, integration/evaluation/planning tests.

**Goal:** extract Evaluation/Planning; reviewer receives `VerificationBundle.completion_authority`; connectors consume EgressBundle; application gate is constructor input; portability built from completed Evaluation + PlatformServices. Remove private completion read, model egress mutation and public-wrapper gate mutation.

**Prerequisites:** Slices 4–6.

**Expected conflicts:** many services but explicit dependencies by this stage.

**Tests:** evaluation E2E, reviewer/repair/output, release gate, connectors/egress, planning/replanning, portability, optional GitHub provider.

**Rollback/compatibility risk:** medium-high.

### Slice 8 — Learning, Handoffs and Context-source completion

**Files:** `deployment/durable_connectors.py`, Learning/Handoff/Context tests.

**Goal:** extract Learning/Handoff builders; return incoming Handoff Context source contribution explicitly; no Control Plane argument needed for runtime construction.

**Prerequisites:** Planning, Integration and Context bundles.

**Expected conflicts:** low-to-medium.

**Tests:** Learning persistence/resources, Handoff composition/restart, incoming Context source, Agent handoff golden path.

**Rollback/compatibility risk:** medium.

### Slice 9 — Health, deterministic Control Plane, HTTP and root simplification

**Files:** `deployment/single_node.py`, `deployment/durable_connectors.py`, `deployment/__init__.py`, Control Plane/HTTP/deployment tests.

**Goal:** build Health from final lifecycle; create Control Plane once from completed contributions under #982; preserve Control Plane-owned Automation/Notification runtime with explicit durable paths; build HTTP after registration completes; make public root a linear orchestration; delete reflection/obsolete late wiring.

**Prerequisites:** all domain builders.

**Expected conflicts:** registration/API compatibility, not persistence.

**Tests:** Control Plane ownership/dependencies, Automation durable runtime/lifespan, API manifest/OpenAPI/schema parity, release API, full composition, health, startup recovery, restart, golden path.

**Rollback/compatibility risk:** medium; broad diff but earlier slices protect semantics.

### Slice 10 — Architecture guardrails and final handoff

**Files:** architecture tests/docs only unless tiny cleanup remains.

**Goal:** static guardrails: no production composition access to `._completion_authority`, `._lifecycle` or wrapper `._inner`; no hidden model/release late wiring; no builder accepts completed deployment as service locator; Worker ownership remains outside root; Automation stays on canonical Control Plane owner.

**Prerequisites:** Slice 9.

**Expected conflicts:** low.

**Tests:** architecture guard + complete required CI.

**Rollback/compatibility risk:** low.

## Acceptance Checklist for eventual #890 implementation

- Public single-node builder and deployment surface remain compatible.
- Each major construction responsibility has a typed, independently testable builder/bundle or an explicitly preserved existing owner (Automation/Notifications, Worker lifecycle).
- Repository construction explicitly models pre-kernel foundation and post-kernel runtime integration instead of creating a false circular builder dependency.
- Platform Workflows/Research/Templates/CapabilityAssignments have an explicit pre-kernel construction owner.
- No builder receives completed deployment as an implicit service locator.
- No production composition reads/writes another component's private attributes.
- Model egress, Verification completion, Context lifecycle and release gate dependencies are constructor/bundle inputs.
- Context returns final execution lifecycle before main kernel construction.
- Actual Worker processes remain outside single-node root ownership; distributed execution is only an explicit optional binding.
- Automation/Notification runtime remains on canonical Control Plane ownership with explicit durable state paths.
- Control Plane is assembled once from completed public modules/services under #982; HTTP follows it.
- Optional providers remain gated and absent by default without baseline import/startup impact.
- Existing startup/recovery/lifespan/health/API/restart/persistence behavior remains green.
- #892 persistence acceptance remains green; #890 does not redesign SQLite/offload semantics.
- No new `SingleNodeDeployment.close()` contract is invented solely for this structural refactor.
- Physical package/domain moves remain deferred to #895.
