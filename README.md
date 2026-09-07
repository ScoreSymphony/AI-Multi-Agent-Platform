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

> Status snapshot: 2026-09-07, after the post-closure architecture audits and the test-structure follow-up #568

The repository is in late product integration, architecture hardening, acceptance and operating-envelope work rather than foundational platform construction. The usable single-node prototype gate (#252) is complete, and `main` contains the canonical kernel and Control Plane, reference execution, model routing, capabilities/tools, Agents and Agent Teams, authentication and authorization/approvals, Workspaces/files/Artifacts, Memory and Knowledge, Search, Automations, Notifications, Organizations/Teams/Memberships, Chat, Browser and Terminal entry points, reusable workflow definitions, Verification/Review, accounting, durable Connectors, portable import/export, Repository/Git integration, optional HA/failover semantics, extensive Web/CLI coverage, distributed Worker/Workspace execution, the durable Plan/Step coordinator, Registry/Marketplace support and the release/update system.

Recent convergence materially moved the frontier forward:

- **#78 is closed**; Template frontend/documentation reconciliation is no longer an open work lane.
- **#421 is closed via PR #540**; Web and CLI expose canonical durable workflow progress through the versioned Control Plane. The narrower follow-up **#560** owns safe wait/retry projection semantics, reliable coordinator-driven live refresh and final Web conformance coverage.
- **#439 has substantial planning/replanning implementation merged through PRs #546 and #559**, including platform-owned planning contracts, durable proposals/revisions, #384 handoff and exact planned Step-to-Agent execution bindings. The issue remains open until its current completion audit and end-to-end replanning requirements are fully satisfied.
- **#500 has progressed through portable pressure/admission, the Linux PSI/swap/zRAM/cgroup provider, observability, authenticated Worker reporting and deployment/doctor integration**. Remaining work is concentrated in final issue-owned acceptance/operator evidence and downstream pressure benchmark integration rather than the original core architecture.
- **#501 has a merged Proposal/Specification governance core via PR #541**, including exact-revision Approval binding and idempotent Task conversion, while the parent issue remains open for its remaining issue-owned completion audit/integrations.
- **#502 has a merged provider-neutral repository-intelligence foundation and policy-enforced production wiring**. Dirty-Workspace freshness, candidate evaluation/pilots, plugin/Registry packaging and resource/evaluation work remain open.
- **#440 now includes substantial single-node, coordination, API-pressure, distributed Worker/Workspace fault and heterogeneous-placement benchmark evidence**, but still owns the final operating-envelope, host-pressure/planner profile and regression-budget work.
- **#388 remains a narrow real-host transport acceptance gap**: the network adapter exists, but actual encrypted/authenticated two-host evidence and Artifact/evidence-reference return-path proof are still required.
- **#562 adds the broader production-shaped two-VPS/private-tunnel validation**. It should share infrastructure and evidence with #388 rather than creating a duplicate deployment path.
- **#566 is a new optional HA productionization follow-up** to #89. It owns a real multi-process/multi-host CoordinationProvider plus shared/replicated durable-state composition and must not become a hidden dependency of the single-node baseline.
- **#567 is a new focused core architecture-hardening follow-up**. It replaces external application coupling to the private `PlatformKernel._commit_task_command()` primitive with a narrow supported Task-mutation boundary and an automated encapsulation guard.
- **#568 is a behavior-neutral maintainability follow-up** to reorganize the growing Python test suite into stable suite categories while preserving discovery, fixture/path semantics and exact-path CI references.

There are currently **12 open issues out of 113 repository issues** (**101 closed; about 89.4% closed by issue count**):

`#46, #388, #439, #440, #500, #501, #502, #560, #562, #566, #567, #568`

The raw closure percentage is lower than the previous snapshot because follow-up audits and maintainability work expanded the tracked scope; it is not evidence that merged implementation regressed.

The current frontier is split deliberately:

- **Operational-v1/core convergence:** #46 final conformance, #439 planning/replanning closure, #440 remaining performance/operating-envelope evidence, #500 host-pressure completion, #560 workflow-progress semantics/live-refresh completion and #567 kernel Task-mutation boundary hardening.
- **Real distributed acceptance:** #388 transport-specific two-host acceptance plus #562 full two-VPS/private-tunnel deployment validation. These should use one hardened private test topology where practical.
- **Optional ideal-end-state expansion:** #501 Proposal/Specification governance, #502 repository intelligence and #566 production-shaped Control Plane HA. They extend the platform without becoming mandatory for ordinary direct-Task, Git/ripgrep/LSP or single-node operation.
- **Repository maintainability:** #568 test-suite layout refactoring is behavior-neutral and should be coordinated with CI/path-sensitive work rather than treated as a new product capability.

There are **no open pull requests** at this snapshot. Current implementation work should therefore be selected from the open issues and their actual dependencies rather than from stale PR references or numeric issue order.

No GitHub release has been published yet. The release/update machinery is implemented, but a published release still requires the repository release process, validated evidence/manifest and an exact accepted release commit. The operational `1.0.0` baseline remains tied to the supported M3 profile and #46 conformance rather than to optional ecosystem or HA features.

Current implementation work should follow [`docs/IMPLEMENTATION_ROADMAP.md`](docs/IMPLEMENTATION_ROADMAP.md) and each issue's current wording/comments rather than numeric issue order. GitHub issue state and merged code remain the point-in-time source of truth when a documentation snapshot becomes stale.