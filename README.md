# AI Multi-Agent Platform

A general-purpose, self-hostable, model-agnostic and hardware-agnostic AI multi-agent platform.

The platform is designed around canonical tasks, plans, steps, runs, agents, tools, models, workers, nodes, workspaces, files, artifacts, results, verification, approvals and events. Concrete systems such as Hermes, Forge, LiteLLM, MCP-compatible tool servers, model runtimes, storage engines or workflow engines are integrations behind replaceable contracts rather than the definition of the platform itself.

## Core principles

- General purpose; not tied to ScoreSymphony or another application domain.
- Supports both single-agent and multi-agent workloads.
- Task-centric rather than chat-centric.
- Model/provider agnostic.
- Hardware/deployment agnostic.
- Single-node and distributed multi-node operation use the same conceptual model and are both first-class architecture targets.
- Replaceable orchestration, execution, model, tool, memory, file, knowledge, persistence, event/messaging, authorization/policy, scheduling, observability and automation layers.
- API-first and plugin-friendly.
- Self-hostable and local-first.
- Baseline operation must not require recurring paid AI/API services.

## Architecture

The authoritative product direction lives in [`docs/PRODUCT_VISION.md`](docs/PRODUCT_VISION.md). Non-negotiable architectural boundaries and explicit invariants live in [`docs/ARCHITECTURE_PRINCIPLES.md`](docs/ARCHITECTURE_PRINCIPLES.md). The canonical domain model is defined in [`docs/DOMAIN_MODEL.md`](docs/DOMAIN_MODEL.md), replaceable provider boundaries are defined in [`docs/CONTRACTS.md`](docs/CONTRACTS.md), and canonical Task/Run lifecycle ownership and recovery are defined in [`docs/KERNEL.md`](docs/KERNEL.md).

The dependency-driven implementation order, current parallel work lanes and convergence gates live in [`docs/IMPLEMENTATION_ROADMAP.md`](docs/IMPLEMENTATION_ROADMAP.md). GitHub issue numbers are identifiers, not the implementation sequence.

Material implementation choices and architecture refinements are recorded through [`docs/adr/`](docs/adr/README.md). Implementations must not silently contradict the normative product or architecture documents.

The platform-owned kernel under `src/ai_multi_agent_platform/kernel/` is the authoritative source for externally visible Task/Run lifecycle state. Orchestrators and executors integrate through contracts and must not become implicit lifecycle owners. The platform-owned durable Plan/Step coordinator from #384 is accepted for restart-safe task-bound workflow progression, including durable dependency/wait/retry state, canonical Run reconciliation, distributed Worker cancellation/recovery evidence, orchestrator-replacement invariance and conservative authorized repair of inconsistent coordination state. Genuine multi-Control-Plane authority remains a deployment/HA concern behind the existing replaceable coordination/fencing contracts rather than a second workflow lifecycle.

## Development

A fresh-clone setup and the validation commands are documented in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md). Contribution and architecture-change rules live in [`CONTRIBUTING.md`](CONTRIBUTING.md). Coding-agent execution and dependency-selection rules live in [`AGENTS.md`](AGENTS.md).

Repository-level integration boundaries are reserved under `adapters/`, `workers/` and `frontend/`. Concrete integrations must implement platform-owned contracts rather than redefine canonical domain types.

The repository contains dedicated validation for the canonical single-node path, optional adapter compatibility, frontend/browser behavior, deterministic evaluation, security scanning, performance/load evidence and reusable prototype/platform conformance gates. See [`docs/PROTOTYPE_ACCEPTANCE.md`](docs/PROTOTYPE_ACCEPTANCE.md) for the single-node prototype profiles and [`docs/PLATFORM_CONFORMANCE.md`](docs/PLATFORM_CONFORMANCE.md) for the wider conformance framework.

## Licensing and upstream components

Project-owned source is distributed under the MIT License in [`LICENSE`](LICENSE). Rules for third-party source, dependencies, services, adapters, vendored/forked source, selective ports and reference-only influence live in [`LICENSE_POLICY.md`](LICENSE_POLICY.md).

Before approving an architecture-significant upstream, complete [`docs/UPSTREAM_ADOPTION_CHECKLIST.md`](docs/UPSTREAM_ADOPTION_CHECKLIST.md). Approved/integrated upstream provenance is recorded in [`docs/UPSTREAMS.md`](docs/UPSTREAMS.md) using the machine-readable starting format in [`upstream/PROVENANCE_TEMPLATE.yaml`](upstream/PROVENANCE_TEMPLATE.yaml). Updates and periodic reviews follow [`docs/UPSTREAM_UPDATE_WORKFLOW.md`](docs/UPSTREAM_UPDATE_WORKFLOW.md).

The policy is exercised against multiple integration models in [`docs/UPSTREAM_POLICY_VALIDATION.md`](docs/UPSTREAM_POLICY_VALIDATION.md).

## Status

> Status snapshot: 2026-09-06, after PR #494

The project is now in late integration, hardening and acceptance work rather than foundational platform construction. The usable single-node prototype acceptance gate (#252) is complete, and `main` contains the canonical kernel and Control Plane, reference execution, model routing, capabilities/tools, Agents and Agent Teams, authentication and authorization/approvals, Workspaces/files, Memory and Knowledge, Search, Automations, Notifications, Organizations/Teams/Memberships, Chat, Browser and Terminal entry points, reusable workflow definitions, Verification/Review, accounting, durable Connector state, portable import/export, Repository/Git integration, optional HA/failover support, extensive Web/CLI coverage and concrete distributed Workspace/Worker paths.

The latest hardening wave also completed the routing-profile follow-ups #443–#447, Node/Worker timestamp semantics (#414), durable Connector persistence (#416) and the remaining accounting integration work (#171). Significant implementation has landed for durable task-bound Plan/Step coordination (#384), distributed deployment (#240), Templates (#78), the optional Registry (#81), performance/load evidence (#440), release/update mechanics (#42) and platform conformance (#46), but those issues remain open until their remaining acceptance or operationalization work is finished.

At this snapshot there are **9 open issues out of 98 repository issues** (89 closed; about 90.8% closed by issue count):

`#42, #46, #78, #81, #240, #384, #421, #439, #440`

The current implementation frontier is:

- **Completion/audit:** #78 Templates, #81 optional Registry and #384 durable Plan/Step coordination have major merged implementations and now need their remaining Definition-of-Done/acceptance reconciliation.
- **Distributed deployment:** #240 has the real distributed integration path merged; remaining work is focused on deployment hardening and final acceptance rather than inventing new canonical Worker/Workspace contracts.
- **Workflow product layer:** #421 Web/CLI workflow-progress surfaces and #439 autonomous goal decomposition/planning/replanning are the main newly unlocked product/runtime capabilities downstream of #384.
- **Performance:** #440 already has deterministic single-node, concurrency, history/restart, endurance/stress and transport-fault profiles; workflow/distributed/further fault-under-load evidence remains.
- **Release/update:** #42 has the release/provenance/compatibility foundation and hardening merged; deterministic manifest generation and persistent operator-facing update discovery remain among the operationalization gaps.
- **Final convergence:** #46 already has a release-profile conformance framework and substantial canonical scenario evidence, but remains the final platform-wide acceptance gate and must consume the finished production paths rather than substitute test-only shortcuts.

No GitHub release has been published yet. The prototype gate required for the planned `0.1.0` release has passed, while the full operational `1.0.0` target remains tied to #46 and the release process in [`docs/RELEASE_PROCESS.md`](docs/RELEASE_PROCESS.md).

Current implementation work should follow [`docs/IMPLEMENTATION_ROADMAP.md`](docs/IMPLEMENTATION_ROADMAP.md) and each issue's explicit hard dependencies rather than numeric issue order. GitHub issue state and current issue wording remain the point-in-time source of truth when a status snapshot becomes stale.
