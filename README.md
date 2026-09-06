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

The platform-owned kernel under `src/ai_multi_agent_platform/kernel/` is the authoritative source for externally visible Task/Run lifecycle state. Orchestrators and executors integrate through contracts and must not become implicit lifecycle owners. Durable task-bound Plan/Step progression is likewise being kept platform-owned rather than delegated to an external workflow engine.

## Development

A fresh-clone setup and the validation commands are documented in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md). Contribution and architecture-change rules live in [`CONTRIBUTING.md`](CONTRIBUTING.md). Coding-agent execution and dependency-selection rules live in [`AGENTS.md`](AGENTS.md).

Repository-level integration boundaries are reserved under `adapters/`, `workers/` and `frontend/`. Concrete integrations must implement platform-owned contracts rather than redefine canonical domain types.

The repository already contains dedicated validation for the canonical single-node path, optional adapter compatibility, frontend/browser behavior, deterministic evaluation, security scanning and a reusable prototype-acceptance gate. See [`docs/PROTOTYPE_ACCEPTANCE.md`](docs/PROTOTYPE_ACCEPTANCE.md) for the single-node acceptance profiles.

## Licensing and upstream components

Project-owned source is distributed under the MIT License in [`LICENSE`](LICENSE). Rules for third-party source, dependencies, services, adapters, vendored/forked source, selective ports and reference-only influence live in [`LICENSE_POLICY.md`](LICENSE_POLICY.md).

Before approving an architecture-significant upstream, complete [`docs/UPSTREAM_ADOPTION_CHECKLIST.md`](docs/UPSTREAM_ADOPTION_CHECKLIST.md). Approved/integrated upstream provenance is recorded in [`docs/UPSTREAMS.md`](docs/UPSTREAMS.md) using the machine-readable starting format in [`upstream/PROVENANCE_TEMPLATE.yaml`](upstream/PROVENANCE_TEMPLATE.yaml). Updates and periodic reviews follow [`docs/UPSTREAM_UPDATE_WORKFLOW.md`](docs/UPSTREAM_UPDATE_WORKFLOW.md).

The policy is exercised against multiple integration models in [`docs/UPSTREAM_POLICY_VALIDATION.md`](docs/UPSTREAM_POLICY_VALIDATION.md).

## Status

> Status snapshot: 2026-09-06

The project is well beyond its initial architecture baseline. The usable single-node prototype acceptance gate (#252) is complete, and the main branch now includes the canonical kernel and Control Plane, reference execution, model routing, capabilities/tools, Agents and Agent Teams, authentication and authorization/approvals, Workspaces/files, Memory and Knowledge, Search, Automations, Notifications, Organizations/Teams/Memberships, Chat, Browser and Terminal entry points, reusable workflow definitions, Verification/Review, accounting foundations, HA/failover support, portable import/export, repository integration and extensive Web/CLI coverage.

Several domains remain intentionally open because post-merge audits extracted concrete hardening or integration work rather than treating the first implementation as final. In particular, Templates (#78) and accounting integration (#171) have substantial shipped implementations but still have explicit remaining acceptance work.

There are currently 17 open issues. The highest-leverage implementation frontier is:

- **Durable task-bound workflows:** #384, followed by #421 client workflow progress and #439 autonomous planning/replanning.
- **Distributed deployment:** #240, with the advanced deployment PR #386 still open; the concrete remote Workspace materialization prerequisite #433 is already merged. #414 owns the remaining canonical Node/Worker state-change timestamp semantics.
- **Persistence/integration completion:** #416 durable Connector persistence and the remaining #171 accounting integration gaps.
- **Template completion:** #78 remaining rollback, workflow-template and create-from-existing integration work.
- **Model-routing hardening:** #443–#447 cover authorized management, chronology, cross-consumer assignment, provenance/schema consistency and compensation-reference safety after the completed routing-profile core.
- **Quality and release:** #440 performance/load/scalability evidence, #42 release/upstream synchronization and #46 final end-to-end platform conformance.
- **Optional ecosystem:** #81 Registry/Marketplace remains optional and must not become a baseline dependency.

No GitHub release has been published yet. The prototype gate required for the planned `0.1.0` release has passed, while the full operational `1.0.0` target remains tied to #46 and the release process in [`docs/RELEASE_PROCESS.md`](docs/RELEASE_PROCESS.md).

Current implementation work should follow [`docs/IMPLEMENTATION_ROADMAP.md`](docs/IMPLEMENTATION_ROADMAP.md) and each issue's explicit hard dependencies rather than numeric issue order. GitHub issue state and current issue wording remain the point-in-time source of truth when a status snapshot becomes stale.
