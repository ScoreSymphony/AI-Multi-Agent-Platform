# AI Multi-Agent Platform

A general-purpose, self-hostable, model-agnostic and hardware-agnostic AI multi-agent platform.

The platform is designed around canonical tasks, runs, agents, tools, models, workers, nodes, artifacts and events. Concrete systems such as Hermes, Forge, LiteLLM, MCP-compatible tool servers, model runtimes, storage engines or workflow engines are integrations behind replaceable contracts rather than the definition of the platform itself.

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

The platform-owned kernel under `src/ai_multi_agent_platform/kernel/` is the authoritative source for externally visible Task/Run lifecycle state. Orchestrators and executors integrate through contracts and must not become implicit lifecycle owners.

## Development

A fresh-clone setup and the validation commands are documented in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md). Contribution and architecture-change rules live in [`CONTRIBUTING.md`](CONTRIBUTING.md). Coding-agent execution and dependency-selection rules live in [`AGENTS.md`](AGENTS.md).

Repository-level integration boundaries are reserved under `adapters/`, `workers/` and `frontend/`. Concrete integrations must implement platform-owned contracts rather than redefine canonical domain types.

## Licensing and upstream components

Project-owned source is distributed under the MIT License in [`LICENSE`](LICENSE). Rules for third-party source, dependencies, services, adapters, vendored/forked source, selective ports and reference-only influence live in [`LICENSE_POLICY.md`](LICENSE_POLICY.md).

Before approving an architecture-significant upstream, complete [`docs/UPSTREAM_ADOPTION_CHECKLIST.md`](docs/UPSTREAM_ADOPTION_CHECKLIST.md). Approved/integrated upstream provenance is recorded in [`docs/UPSTREAMS.md`](docs/UPSTREAMS.md) using the machine-readable starting format in [`upstream/PROVENANCE_TEMPLATE.yaml`](upstream/PROVENANCE_TEMPLATE.yaml). Updates and periodic reviews follow [`docs/UPSTREAM_UPDATE_WORKFLOW.md`](docs/UPSTREAM_UPDATE_WORKFLOW.md).

The policy is exercised against multiple integration models in [`docs/UPSTREAM_POLICY_VALIDATION.md`](docs/UPSTREAM_POLICY_VALIDATION.md).

## Status

As of 2026-09-03, the completed substantive platform baseline includes #1–#7, #9–#13, #15–#17, #32, #34, #35, #37, #43, #76 and #88. This means the repository has already established the canonical domain/kernel/execution foundation, models, capabilities, data boundaries, authorization/approvals, observability, the initial Web UI shell, Control Plane, configuration/secrets, messaging, Workspaces, the security baseline, usage accounting and practical Task-management metadata.

The current highest-leverage parallel implementation frontier is #36 Authentication, #33 canonical Agents/Teams, #14 Nodes/Workers, #18 Automations and #20 Plugins. #19 Evaluation, #45 Search, #73 Terminal and #74 Browser are also ready to begin independently, while #38 CLI remains a progressive open client lane whose full completion consumes later Authentication/Agent/Worker APIs.

Current implementation work should follow [`docs/IMPLEMENTATION_ROADMAP.md`](docs/IMPLEMENTATION_ROADMAP.md) and each issue's explicit hard dependencies rather than simple numeric issue order. GitHub issue state and current issue wording remain the point-in-time source of truth when the roadmap and an issue diverge.