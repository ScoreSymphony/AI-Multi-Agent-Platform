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

The platform-owned kernel under `src/ai_multi_agent_platform/kernel/` is the authoritative source for externally visible Task/Run lifecycle state. Orchestrators and executors integrate through contracts and must not become implicit lifecycle owners. The platform-owned durable Plan/Step coordinator from #384 is accepted for restart-safe task-bound workflow progression, including durable dependency/wait/retry state, canonical Run reconciliation, distributed Worker cancellation/recovery evidence, orchestrator-replacement invariance, backup/restore and upgrade integration, observability and conservative authorized repair of inconsistent coordination state. Genuine multi-Control-Plane authority remains a deployment/HA concern behind replaceable coordination/fencing contracts rather than a second workflow lifecycle.

## Development

A fresh-clone setup and the validation commands are documented in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md). Contribution and architecture-change rules live in [`CONTRIBUTING.md`](CONTRIBUTING.md). Coding-agent execution and dependency-selection rules live in [`AGENTS.md`](AGENTS.md).

Platform-owned Python runtime, adapter and Worker implementations live under `src/ai_multi_agent_platform/`; `frontend/` contains the web client. Concrete integrations must implement platform-owned contracts rather than redefine canonical domain types.

The repository contains dedicated validation for the canonical single-node path, optional adapter compatibility, frontend/browser behavior, deterministic evaluation, security scanning, performance/load evidence and reusable prototype/platform conformance gates. See [`docs/PROTOTYPE_ACCEPTANCE.md`](docs/PROTOTYPE_ACCEPTANCE.md) for the single-node prototype profiles and [`docs/PLATFORM_CONFORMANCE.md`](docs/PLATFORM_CONFORMANCE.md) for the wider conformance framework.

## Licensing and upstream components

Project-owned source is distributed under the MIT License in [`LICENSE`](LICENSE). Rules for third-party source, dependencies, services, adapters, vendored/forked source, selective ports and reference-only influence live in [`LICENSE_POLICY.md`](LICENSE_POLICY.md).

Before approving an architecture-significant upstream, complete [`docs/UPSTREAM_ADOPTION_CHECKLIST.md`](docs/UPSTREAM_ADOPTION_CHECKLIST.md). Approved/integrated upstream provenance is recorded in [`docs/UPSTREAMS.md`](docs/UPSTREAMS.md) using the machine-readable starting format in [`upstream/PROVENANCE_TEMPLATE.yaml`](upstream/PROVENANCE_TEMPLATE.yaml). Updates and periodic reviews follow [`docs/UPSTREAM_UPDATE_WORKFLOW.md`](docs/UPSTREAM_UPDATE_WORKFLOW.md).

The policy is exercised against multiple integration models in [`docs/UPSTREAM_POLICY_VALIDATION.md`](docs/UPSTREAM_POLICY_VALIDATION.md).

## Status

> Status snapshot: 2026-09-07, after the latest planning, workflow-client, host-pressure and distributed-benchmark merges

The repository is in late product integration, acceptance and operating-envelope work rather than foundational platform construction. The usable single-node prototype gate (#252) is complete, and `main` contains the canonical kernel and Control Plane, reference execution, model routing, capabilities/tools, Agents and Agent Teams, authentication and authorization/approvals, Workspaces/files/Artifacts, Memory and Knowledge, Search, Automations, Notifications, Organizations/Teams/Memberships, Chat, Browser and Terminal entry points, reusable workflow definitions, Verification/Review, accounting, durable Connectors, portable import/export, Repository/Git integration, optional HA/failover, extensive Web/CLI coverage, distributed Worker/Workspace execution, the durable Plan/Step coordinator, Registry/Marketplace support and the release/update system.

Recent convergence materially moved the frontier forward:

- **#78 is closed**; the remaining Template frontend/documentation reconciliation is no longer an open work lane.
- **#421 is closed via PR #540**; Web and CLI now expose canonical durable workflow progress through the versioned Control Plane. The narrower follow-up **#560** owns the remaining safe wait/retry projection semantics, reliable coordinator-driven live refresh and final Web conformance coverage.
- **#439 has substantial planning/replanning implementation merged through PRs #546 and #559**, including platform-owned planning contracts, durable proposals/revisions, #384 handoff and exact planned Step-to-Agent execution bindings. The issue remains open until its current completion audit and end-to-end replanning requirements are fully satisfied.
- **#500 has progressed through portable pressure/admission, observability, authenticated Worker reporting and deployment/doctor integration**, while the issue remains open for its remaining Definition-of-Done work and pressure benchmark integration.
- **#501 has a merged Proposal/Specification governance core via PR #541**, while the parent issue remains open for any remaining issue-owned completion work.
- **#502 has a merged provider-neutral repository-intelligence foundation and policy-enforced production wiring**, while dirty-Workspace freshness, candidate evaluation/pilots, plugin/Registry packaging and resource/evaluation work remain open.
- **#440 now includes additional distributed fault and heterogeneous-placement benchmark evidence**, but still owns the wider operating-envelope, stress/soak and regression-budget work.

There are currently **9 open issues out of 109 repository issues** (**100 closed; about 91.7% closed by issue count**):

`#46, #388, #439, #440, #500, #501, #502, #560, #562`

The current frontier is split deliberately:

- **Operational-v1/core convergence:** #46 final conformance, #439 planning/replanning closure, #440 remaining performance/operating-envelope evidence, #500 host-pressure completion and #560 workflow-progress semantics/live-refresh completion.
- **Real distributed acceptance:** #388 still needs explicit real two-host encrypted transport/result-reference evidence. #562 adds a production-shaped two-VPS private-tunnel validation covering registration, heartbeat, dispatch, interruption, recovery and security on real hosts.
- **Optional ideal-end-state expansion:** #501 Proposal/Specification governance and #502 repository intelligence remain optional extensions; neither may become a hidden prerequisite for the ordinary direct-Task or Git/ripgrep/LSP baseline paths.

There are **no open pull requests** at this snapshot. Current implementation work should therefore be selected from the open issues and their current hard dependencies rather than from stale PR references or numeric issue order.

No GitHub release has been published yet. The release/update machinery is implemented, but a published release still requires the repository release process, validated evidence/manifest and an exact accepted release commit. The operational `1.0.0` baseline remains tied to the supported M3 profile and #46 conformance rather than to optional ecosystem features.

Current implementation work should follow [`docs/IMPLEMENTATION_ROADMAP.md`](docs/IMPLEMENTATION_ROADMAP.md) and each issue's current wording/comments rather than numeric issue order. GitHub issue state and merged code remain the point-in-time source of truth when a documentation snapshot becomes stale.
