# Contributor architecture and code-navigation guide

This guide is the fast path from **“I understand what the platform does”** to **“I know where to make this change.”** It is navigational, not normative. Canonical lifecycle semantics, contracts, package ownership, security policy and compatibility rules remain owned by the linked architecture documents and source modules.

If you only need to run the project, start with [`README.md`](../README.md) and [`DEVELOPMENT.md`](DEVELOPMENT.md). If you are changing the implementation, read this guide before editing a package that merely looks related by name.

## Platform in one page

The platform is a general-purpose, self-hostable, local-first multi-agent platform. It owns the canonical work lifecycle and exposes that state through one Control Plane. Concrete orchestrators, execution backends, model systems, tools, storage implementations and external services integrate behind platform-owned contracts.

At the contributor level, the important shape is:

```text
Web / CLI / external client
            |
            v
       Control Plane
            |
            v
 domain/application services
            |
   +--------+---------+
   |        |         |
   v        v         v
 kernel  planning/   provider-neutral
Task/Run coordination contracts
   |        |         |
   +--------+---------+
            |
            v
 execution / models / capabilities / data
            |
            v
 local executor or distributed Nodes/Workers
            |
            v
 Artifact / Result -> Verification / completion
```

The browser and CLI are clients of canonical server state. They do not own a shadow Task/Run lifecycle. The kernel is authoritative for canonical Task/Run lifecycle state. Planning owns Plan/Step proposal and activation concerns; coordination owns durable Plan/Step progression; execution backends perform attempts but do not redefine canonical lifecycle. Provider-specific implementations remain replaceable behind contracts in [`src/ai_multi_agent_platform/contracts/`](../src/ai_multi_agent_platform/contracts/).

Single-node operation is the baseline architecture, not a separate product. Distributed execution extends the same Task/Run/Workspace model through [`src/ai_multi_agent_platform/distributed/`](../src/ai_multi_agent_platform/distributed/) rather than introducing a second workflow model.

Read the normative sources when changing semantics:

- [`ARCHITECTURE_PRINCIPLES.md`](ARCHITECTURE_PRINCIPLES.md) — non-negotiable ownership and dependency invariants;
- [`DOMAIN_MODEL.md`](DOMAIN_MODEL.md) — canonical domain objects and relationships;
- [`CONTRACTS.md`](CONTRACTS.md) — provider-neutral interfaces and replaceability;
- [`KERNEL.md`](KERNEL.md) — Task/Run/Event lifecycle, recovery and authority;
- [`runtime/CONTROL_PLANE.md`](runtime/CONTROL_PLANE.md) — northbound API and explicit extension composition;
- [`PACKAGE_BOUNDARIES.md`](PACKAGE_BOUNDARIES.md) and [`PACKAGE_BOUNDARIES.toml`](PACKAGE_BOUNDARIES.toml) — current top-level package ownership.

## Canonical owner, façade, adapter or presentation state?

Before editing code, classify the location you found.

| Kind | Meaning | May define canonical semantics? |
| --- | --- | --- |
| Canonical domain owner | Package that owns durable platform responsibility and state/contract semantics. | Yes, within its responsibility. |
| Application/service façade | Stable entry point that delegates to focused canonical services. | It may expose semantics owned by its domain, but must not create a parallel authority. |
| Compatibility façade / migration namespace | Retained import or transition surface that forwards to the canonical owner. | No new behavior belongs here. |
| Integration / adapter | Concrete upstream or provider implementation behind platform contracts. | No; it implements platform-owned contracts. |
| Surface | Web, CLI or another client of canonical APIs. | Presentation/interaction state only. |
| Operations / quality | Deployment, release, backup, testing and conformance support. | No new runtime lifecycle authority. |

When in doubt, check the `kind`, `owner` and `disposition` fields in [`PACKAGE_BOUNDARIES.toml`](PACKAGE_BOUNDARIES.toml). A directory existing at the repository root does **not** by itself make it a canonical domain.

## Primary domain objects

This table is deliberately compact. Follow the linked owner/document rather than copying object definitions into this guide.

| Object | Canonical owner / implementation area | Read next |
| --- | --- | --- |
| Goal | [`goals/`](../src/ai_multi_agent_platform/goals/) | [`DOMAIN_MODEL.md`](DOMAIN_MODEL.md) |
| Task | kernel lifecycle + task-management metadata | [`KERNEL.md`](KERNEL.md), [`task_management/`](../src/ai_multi_agent_platform/task_management/) |
| Plan / Step | [`planning/`](../src/ai_multi_agent_platform/planning/) for proposal/activation; [`coordination/`](../src/ai_multi_agent_platform/coordination/) for durable progression | [`DOMAIN_MODEL.md`](DOMAIN_MODEL.md), [`BACKEND_RESPONSIBILITY_MAP.md`](BACKEND_RESPONSIBILITY_MAP.md) |
| Run | [`kernel/`](../src/ai_multi_agent_platform/kernel/) | [`KERNEL.md`](KERNEL.md) |
| Agent / Agent Team | [`agents/`](../src/ai_multi_agent_platform/agents/) | [`AGENT_MATCHING.md`](AGENT_MATCHING.md), [`runtime/AGENT_HANDOFFS.md`](runtime/AGENT_HANDOFFS.md) |
| Model / Provider | [`models/`](../src/ai_multi_agent_platform/models/) plus provider contracts in [`contracts/`](../src/ai_multi_agent_platform/contracts/) | [`runtime/MODELS.md`](runtime/MODELS.md), [`CONTRACTS.md`](CONTRACTS.md) |
| Capability / Tool | [`capabilities/`](../src/ai_multi_agent_platform/capabilities/) | [`runtime/CAPABILITIES.md`](runtime/CAPABILITIES.md) |
| Workspace / File | [`workspaces/`](../src/ai_multi_agent_platform/workspaces/) and [`data/`](../src/ai_multi_agent_platform/data/) | [`DOMAIN_MODEL.md`](DOMAIN_MODEL.md), [`BACKEND_RESPONSIBILITY_MAP.md`](BACKEND_RESPONSIBILITY_MAP.md) |
| Artifact / Result | canonical domain/kernel completion path | [`DOMAIN_MODEL.md`](DOMAIN_MODEL.md), [`KERNEL.md`](KERNEL.md) |
| Verification / Approval | [`verification/`](../src/ai_multi_agent_platform/verification/) and [`security/`](../src/ai_multi_agent_platform/security/) | [`SECURITY_THREAT_MODEL.md`](SECURITY_THREAT_MODEL.md) |
| Event | canonical domain + kernel event repository/sinks | [`KERNEL.md`](KERNEL.md), [`CONTRACTS.md`](CONTRACTS.md) |
| Node / Worker / WorkerJob | [`distributed/`](../src/ai_multi_agent_platform/distributed/) | [`runtime/DISTRIBUTED_RUNTIME.md`](runtime/DISTRIBUTED_RUNTIME.md) |

Foundational dataclasses/value objects shared across domains live in [`src/ai_multi_agent_platform/domain/`](../src/ai_multi_agent_platform/domain/). Do not infer from that shared location that every lifecycle operation belongs there.

## Lifecycle of a representative task

The reference path is one lifecycle with optional planning, agent and distributed layers — not separate “UI”, “agent” and “worker” workflows.

```text
1. Web / CLI / external client
       |
       v
2. versioned Control Plane request
       |
       v
3. canonical domain/application service
       |
       +--> Task lifecycle mutations -> PlatformKernel
       |
       +--> planning request -> PlanningService
                                |
                                v
                           canonical Plan/Steps
                                |
                                v
                     DurablePlanStepCoordinator
                                |
       +------------------------+--------------------+
       |                                             |
       v                                             v
4. canonical Run attempt                    Agent/AgentTeam binding
       |                                             |
       +----------------------+----------------------+
                              v
5. execution backend / AgentRuntime
       |
       +--> ModelRouter -> ModelProvider
       |
       +--> CapabilityRegistry / CapabilityInvoker
       |
       v
6. executor or distributed Worker
       |
       v
7. Workspace outputs -> Artifact / Result
       |
       v
8. Verification / approval policy where required
       |
       v
9. canonical Run/Task completion + Events
       |
       v
10. Control Plane projection -> Web / CLI / client
```

Implementation consequences:

- `PlatformKernel` remains the authority for Task/Run terminal state even when a Worker or provider reports success/failure.
- `PlanningService` proposes/activates work but does not execute Steps or dispatch Workers.
- `DurablePlanStepCoordinator` progresses dependencies/retries/waits and reconciles against canonical Run truth; it does not replace the kernel.
- Agent selection/execution uses canonical Agent/AgentTeam revisions and provider-neutral model/capability boundaries.
- Workers and distributed transports report/execute through platform protocols; they do not become client-facing lifecycle authorities.
- Artifacts/results enter the canonical completion path, including Verification where configured.

For the complete acceptance view, see [`PLATFORM_CONFORMANCE.md`](PLATFORM_CONFORMANCE.md). For responsibility-level implementation seams, see [`BACKEND_RESPONSIBILITY_MAP.md`](BACKEND_RESPONSIBILITY_MAP.md).

## Repository and package map

Do not try to learn the architecture by memorizing every top-level Python package. Start with these responsibility groups, then use [`PACKAGE_BOUNDARIES.toml`](PACKAGE_BOUNDARIES.toml) for the exact current owner.

| Responsibility group | Primary areas | What belongs here |
| --- | --- | --- |
| Canonical lifecycle and northbound control | `domain`, `kernel`, `control_plane`, `task_management`, `goals` | canonical resources, Task/Run/Event authority, API composition |
| Planning and multi-agent coordination | `planning`, `coordination`, `agents`, `handoffs` | Plan/Step proposals, durable progression, Agent/Team behavior, handoffs |
| Replaceable runtime providers | `contracts`, `models`, `capabilities`, `execution`, `orchestration`, `data` | platform-owned interfaces, registries, routers and reference implementations |
| Work/data context | `workspaces`, `repositories`, `context`, `search`, `data` | workspace isolation/materialization, repositories, memory/knowledge/files/search |
| Security and governed completion | `security`, `verification`, `governance`, `organizations` | authn/authz, approvals, verification, governance and ownership context |
| Distributed runtime | `distributed` | Node/Worker/WorkerJob, scheduling, transport and distributed execution |
| User surfaces | `cli`, `frontend/` | clients/presentation over Control Plane state |
| Integrations and ecosystem | `adapters`, `connectors`, `plugins`, `distribution` | replaceable upstreams, external services, plugin/registry integration |
| Operations and quality | `deployment`, `configuration`, `backup`, `upgrade`, `release`, `testing`, `conformance`, `acceptance`, `benchmarking` | composition, operations, CI, acceptance and evidence |

The repository still contains migration/compatibility namespaces. New behavior belongs under the owner named by [`PACKAGE_BOUNDARIES.toml`](PACKAGE_BOUNDARIES.toml), not under a package whose `kind` is `migration`.

### Adding a new top-level package

Do this only when an existing canonical owner cannot hold the responsibility without mixing materially different lifecycle/state ownership. A new root package must satisfy every criterion in [`PACKAGE_BOUNDARIES.md`](PACKAGE_BOUNDARIES.md) and update the TOML inventory in the same change. The architecture test [`tests/architecture/test_top_level_package_boundaries.py`](../tests/architecture/test_top_level_package_boundaries.py) rejects unregistered root-package growth.

## Where do I change X?

The entries below name the **first place to inspect**, not every file a change may touch.

| Change | Canonical owner / entry point | Contracts/docs | Required targeted validation |
| --- | --- | --- | --- |
| Add or change Agent / Agent Team behavior | [`agents/`](../src/ai_multi_agent_platform/agents/); use `service.py`, `runtime.py`, matching/persistence modules according to responsibility | [`DOMAIN_MODEL.md`](DOMAIN_MODEL.md), [`AGENT_MATCHING.md`](AGENT_MATCHING.md), [`runtime/AGENT_HANDOFFS.md`](runtime/AGENT_HANDOFFS.md) | affected agent unit/integration tests; planning/coordination tests when assignment/progression changes; representative golden-path/conformance test |
| Add a model provider | implement platform `ModelProvider`; register through [`models/`](../src/ai_multi_agent_platform/models/) rather than teaching callers a provider SDK | [`CONTRACTS.md`](CONTRACTS.md), [`runtime/MODELS.md`](runtime/MODELS.md), [`ADAPTER_SUPPORT_MATRIX.md`](ADAPTER_SUPPORT_MATRIX.md) | provider contract/conformance tests, model routing/registry tests, optional-adapter isolation, targeted integration test |
| Add a Capability / Tool | [`capabilities/registry.py`](../src/ai_multi_agent_platform/capabilities/registry.py) and provider/invocation types under [`capabilities/`](../src/ai_multi_agent_platform/capabilities/) | [`runtime/CAPABILITIES.md`](runtime/CAPABILITIES.md), [`CONTRACTS.md`](CONTRACTS.md) | capability registry/invocation/policy tests; MCP/adaptor test if applicable; agent capability-turn regression when exposed to agents |
| Add a Control Plane resource / command / special route | owning domain service + [`control_plane/extensions.py`](../src/ai_multi_agent_platform/control_plane/extensions.py) `ControlPlaneModule`/registration path | [`runtime/CONTROL_PLANE.md`](runtime/CONTROL_PLANE.md), [`FEATURE_CLASSIFICATION.md`](FEATURE_CLASSIFICATION.md) | [`tests/unit/control_plane/test_explicit_module_composition.py`](../tests/unit/control_plane/test_explicit_module_composition.py), API/OpenAPI/manifest tests, auth/idempotency/error tests for affected surface |
| Add a frontend feature | domain page/module under `frontend/src/pages/` plus typed domain client under [`frontend/src/api/`](../frontend/src/api/); generic HTTP stays in [`transport.ts`](../frontend/src/api/transport.ts) | [`FRONTEND.md`](FRONTEND.md), [`frontend/src/api/README.md`](../frontend/src/api/README.md) | frontend typecheck, unit tests, build; backend API/contract tests if wire behavior changes |
| Add/change authentication, authorization or approval policy | [`security/`](../src/ai_multi_agent_platform/security/) and the owning domain’s policy integration point | [`SECURITY_THREAT_MODEL.md`](SECURITY_THREAT_MODEL.md), [`ARCHITECTURE_PRINCIPLES.md`](ARCHITECTURE_PRINCIPLES.md) | allow/deny/fail-closed tests, scope isolation, authn/authz regressions, affected Control Plane tests |
| Add an execution backend | implement platform execution/lifecycle contracts under [`execution/`](../src/ai_multi_agent_platform/execution/) or a replaceable adapter; preserve kernel authority | [`CONTRACTS.md`](CONTRACTS.md), [`KERNEL.md`](KERNEL.md), [`ADAPTER_SUPPORT_MATRIX.md`](ADAPTER_SUPPORT_MATRIX.md) | execution/lifecycle conformance, cancellation/retry/recovery tests, artifact/result integration, optional adapter profile |
| Add a connector / external integration | [`connectors/`](../src/ai_multi_agent_platform/connectors/) or [`adapters/`](../src/ai_multi_agent_platform/adapters/) according to whether it is an external-service connector or provider adapter | [`CONTRACTS.md`](CONTRACTS.md), [`UPSTREAMS.md`](UPSTREAMS.md), [`LICENSE_POLICY.md`](../LICENSE_POLICY.md) | connector/adapter tests, auth/secret-redaction tests, offline/optional import isolation, upstream/provenance checks when architecture-significant |
| Change Task / Run lifecycle | [`kernel/`](../src/ai_multi_agent_platform/kernel/); focused command/lifecycle modules behind `PlatformKernel` | [`KERNEL.md`](KERNEL.md), [`DOMAIN_MODEL.md`](DOMAIN_MODEL.md), [`BACKEND_RESPONSIBILITY_MAP.md`](BACKEND_RESPONSIBILITY_MAP.md) | kernel transition/idempotency/recovery/cancellation tests, event-history tests, representative end-to-end conformance |
| Change Plan / Step progression | [`planning/`](../src/ai_multi_agent_platform/planning/) for proposal/activation or [`coordination/`](../src/ai_multi_agent_platform/coordination/) for durable progression | [`BACKEND_RESPONSIBILITY_MAP.md`](BACKEND_RESPONSIBILITY_MAP.md), [`DOMAIN_MODEL.md`](DOMAIN_MODEL.md) | graph/dependency/retry/wait/cancel/reconciliation tests plus kernel integration |
| Change Memory / Knowledge / File behavior | canonical provider contracts plus [`data/`](../src/ai_multi_agent_platform/data/); reference implementations are split by responsibility | [`CONTRACTS.md`](CONTRACTS.md), [`BACKEND_RESPONSIBILITY_MAP.md`](BACKEND_RESPONSIBILITY_MAP.md) | persistence/restart/scope tests and provider conformance; relevant search/context tests |
| Change Repository behavior / repository intelligence | [`repositories/`](../src/ai_multi_agent_platform/repositories/); do not grow the migration namespace `repository_intelligence` | [`PACKAGE_BOUNDARIES.md`](PACKAGE_BOUNDARIES.md) | repository service/integration tests, authorization/scope tests, exact-source/provenance regressions |
| Change schema / public API | canonical owning domain + Control Plane schema/OpenAPI; frontend transport remains derived/client-side, not a second canonical domain | [`runtime/CONTROL_PLANE.md`](runtime/CONTROL_PLANE.md), [`FEATURE_CLASSIFICATION.md`](FEATURE_CLASSIFICATION.md), [`CONTRACTS.md`](CONTRACTS.md) | OpenAPI/API snapshots, compatibility/version tests, frontend typecheck/tests/build, generated-contract drift check when generated DTOs are present |
| Change distributed behavior | [`distributed/`](../src/ai_multi_agent_platform/distributed/); deployment code only composes it | [`runtime/DISTRIBUTED_RUNTIME.md`](runtime/DISTRIBUTED_RUNTIME.md), [`PACKAGE_BOUNDARIES.md`](PACKAGE_BOUNDARIES.md) | scheduler/worker/transport/restart tests, distributed E2E profile, workspace materialization regressions |
| Change package ownership / create a bounded context | [`PACKAGE_BOUNDARIES.toml`](PACKAGE_BOUNDARIES.toml) plus canonical owner/module move | [`PACKAGE_BOUNDARIES.md`](PACKAGE_BOUNDARIES.md), relevant ADR | [`tests/architecture/test_top_level_package_boundaries.py`](../tests/architecture/test_top_level_package_boundaries.py), import/build tests, affected public compatibility tests |

## Extension rules contributors must not violate

1. **Keep canonical owners canonical.** An adapter, client, compatibility namespace or deployment profile must not become a second owner of lifecycle/domain state.
2. **Implement providers behind platform contracts.** Provider SDK types and private IDs stay behind the boundary in [`CONTRACTS.md`](CONTRACTS.md).
3. **Do not create a parallel lifecycle, orchestration or verification system.** Planning, coordination, kernel execution and Verification already have distinct owners; extend those owners.
4. **Prefer modules inside an existing owner.** A new package directly below `src/ai_multi_agent_platform/` is exceptional and requires the package-boundary process.
5. **Compose Control Plane domains explicitly.** Use `ControlPlaneModule`, resource/command registration and explicit route/OpenAPI contributions in [`control_plane/extensions.py`](../src/ai_multi_agent_platform/control_plane/extensions.py). Do not stack Control Plane subclasses to accumulate domain behavior.
6. **Keep frontend state presentational.** Canonical Tasks, Runs, approvals, provider health, Memory, Knowledge and other server resources remain server-owned. All generic browser HTTP calls go through [`ApiTransport`](../frontend/src/api/transport.ts).
7. **Treat generated/derived artifacts as derived.** When a transport/schema artifact is generated, change the canonical schema/source and regenerate; do not fork semantics in generated code. Until a surface is generated, the backend/API contract remains canonical and frontend types remain transport clients, not domain authority.
8. **Respect public role/stability separately from ownership.** `Core`, `Platform Extension` and `Optional / Advanced` are architecture roles; `Stable`, `Beta` and `Experimental` are compatibility levels. See [`FEATURE_CLASSIFICATION.md`](FEATURE_CLASSIFICATION.md).
9. **Compatibility façades stay behavior-free.** A migration namespace may preserve imports, but new implementation belongs in its declared owner.

## Common change recipes

### Add a model provider

1. Read the `ModelProvider`/routing contracts in [`CONTRACTS.md`](CONTRACTS.md) and [`runtime/MODELS.md`](runtime/MODELS.md).
2. Implement the provider without exposing its SDK types to canonical callers.
3. Register it through the existing model registry/router path under [`models/`](../src/ai_multi_agent_platform/models/).
4. If it is an architecture-significant upstream, update provenance/support documentation according to [`UPSTREAMS.md`](UPSTREAMS.md) and [`LICENSE_POLICY.md`](../LICENSE_POLICY.md).
5. Add provider conformance plus routing/health/error-path tests. Keep the dependency optional unless it is genuinely baseline infrastructure.

### Add a Control Plane capability

1. Implement or identify the canonical owning domain service first.
2. Expose it with a `ControlPlaneModule` in the owning domain or its Control Plane integration module.
3. Register owned resource collections, commands and only the special routes that cannot use the generic collection/command path.
4. Contribute OpenAPI through the same explicit module. Duplicate ownership must fail at composition time.
5. Declare the public owner, architectural role and stability if the change creates a new public platform capability.
6. Run explicit-composition, API/OpenAPI, authorization, idempotency and affected domain tests.

### Add a frontend feature

1. Confirm the canonical resource/command already exists in the Control Plane; do not call a Worker/provider/private service directly.
2. Put domain-specific HTTP wrapping under [`frontend/src/api/`](../frontend/src/api/) and reuse [`ApiTransport`](../frontend/src/api/transport.ts).
3. Put route/page orchestration under `frontend/src/pages/<feature>/` and keep feature-local components/state next to it.
4. Model view/form state separately when presentation semantics differ from the wire DTO.
5. Do not cache or synthesize a competing canonical lifecycle state machine in React state.
6. Run frontend typecheck, tests and build; if the API changed, run the backend schema/API checks too.

### Add an execution backend

1. Start from platform execution/lifecycle contracts, not from an upstream executor API.
2. Keep canonical Run IDs/status transitions in the kernel; the backend returns execution evidence/status through the contract.
3. Keep workspace/artifact handling platform-shaped and avoid leaking backend-private identities into canonical resources.
4. Add cancellation, retry, recovery/reconciliation and invalid-provider-output coverage.
5. Add the adapter/conformance profile appropriate to the backend; distributed execution additionally needs Worker/transport coverage.

### Modify a canonical domain contract

1. Identify the canonical owner in [`PACKAGE_BOUNDARIES.toml`](PACKAGE_BOUNDARIES.toml) and the object/contract in [`DOMAIN_MODEL.md`](DOMAIN_MODEL.md) or [`CONTRACTS.md`](CONTRACTS.md).
2. Check the surface role/stability in [`FEATURE_CLASSIFICATION.md`](FEATURE_CLASSIFICATION.md).
3. Update canonical source/schema first; then update Control Plane/OpenAPI and clients/derived artifacts.
4. Preserve supported compatibility or introduce the required versioned/deprecation path.
5. Update focused unit/integration tests, architecture/contract checks and the representative golden-path/conformance coverage affected by the change.

## Validation map

Use the smallest targeted tests while iterating, but finish with the repository validation contract for merge-ready work.

### Global checks

From a development environment installed with `.[dev]`:

```bash
ruff format --check .
ruff check .
mypy
pytest
python -m build
```

These commands are maintained in [`DEVELOPMENT.md`](DEVELOPMENT.md) and [`CONTRIBUTING.md`](../CONTRIBUTING.md).

### Change-specific checks

| Change class | Add to the global checks |
| --- | --- |
| Package/ownership change | architecture tests, especially top-level package-boundary guards; import/package build |
| Control Plane/API change | Control Plane unit/integration tests, manifest/OpenAPI/schema/versioning checks, auth/idempotency/error regressions |
| Frontend change | frontend typecheck, tests and production build; API contract checks when transport changes |
| Provider/adapter change | contract/conformance tests, optional-import isolation, adapter profile and relevant integration fixture |
| Kernel/lifecycle change | transition/idempotency/event/recovery/cancellation tests plus representative E2E/conformance path |
| Planning/coordination change | graph/dependency/retry/wait/replan/reconciliation tests plus kernel integration |
| Security policy change | explicit allow/deny/fail-closed tests, scope isolation and affected northbound/integration tests |
| Distributed change | scheduler/Worker/transport/restart/workspace tests plus distributed E2E/conformance profile |
| Public contract change | compatibility/versioning/schema tests, role/stability review, client/derived-artifact checks |

The machine-readable adapter coverage is documented in [`ADAPTER_SUPPORT_MATRIX.md`](ADAPTER_SUPPORT_MATRIX.md), and platform-wide acceptance profiles live in [`PLATFORM_CONFORMANCE.md`](PLATFORM_CONFORMANCE.md).

## Documentation map: read next

Use this order rather than reading the documentation corpus alphabetically.

| Question | Authoritative next document |
| --- | --- |
| What is the product and baseline? | [`PRODUCT_VISION.md`](PRODUCT_VISION.md) |
| What architecture rules may I not violate? | [`ARCHITECTURE_PRINCIPLES.md`](ARCHITECTURE_PRINCIPLES.md) |
| Which domain object owns this concept? | [`DOMAIN_MODEL.md`](DOMAIN_MODEL.md) |
| Which provider interface should an integration implement? | [`CONTRACTS.md`](CONTRACTS.md) |
| Who owns Task/Run transitions and recovery? | [`KERNEL.md`](KERNEL.md) |
| How do I expose a resource/command northbound? | [`runtime/CONTROL_PLANE.md`](runtime/CONTROL_PLANE.md) |
| Which package is the canonical owner? | [`PACKAGE_BOUNDARIES.md`](PACKAGE_BOUNDARIES.md) + [`PACKAGE_BOUNDARIES.toml`](PACKAGE_BOUNDARIES.toml) |
| How are large backend façades decomposed? | [`BACKEND_RESPONSIBILITY_MAP.md`](BACKEND_RESPONSIBILITY_MAP.md) |
| How should frontend state/API access be structured? | [`FRONTEND.md`](FRONTEND.md) + [`frontend/src/api/README.md`](../frontend/src/api/README.md) |
| How mature/stable is a public surface? | [`FEATURE_CLASSIFICATION.md`](FEATURE_CLASSIFICATION.md) |
| How do models/providers work? | [`runtime/MODELS.md`](runtime/MODELS.md) |
| How do capabilities/tools work? | [`runtime/CAPABILITIES.md`](runtime/CAPABILITIES.md) |
| How does distributed execution extend the baseline? | [`runtime/DISTRIBUTED_RUNTIME.md`](runtime/DISTRIBUTED_RUNTIME.md) |
| What security boundaries matter? | [`SECURITY_THREAT_MODEL.md`](SECURITY_THREAT_MODEL.md) |
| What does platform-wide acceptance exercise? | [`PLATFORM_CONFORMANCE.md`](PLATFORM_CONFORMANCE.md) |
| What commands must pass before a PR is merge-ready? | [`DEVELOPMENT.md`](DEVELOPMENT.md) + [`CONTRIBUTING.md`](../CONTRIBUTING.md) |
| Why was an architecture decision made? | [`adr/README.md`](adr/README.md) |

## Final navigation rule

When a change appears to require editing an adapter, compatibility façade, frontend store or deployment module **before** editing a canonical owner, stop and verify ownership. In this repository, the most common architecture mistake is not choosing the wrong implementation technique; it is changing the wrong authority.
