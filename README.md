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

> Status snapshot: 2026-09-07, after closure of #500, #567 and #568 and while the next post-audit PRs are active

The repository is in late product integration, acceptance and operating-envelope work rather than foundational platform construction. The usable single-node prototype gate (#252) is complete, and `main` contains the canonical kernel and Control Plane, reference execution, model routing, capabilities/tools, Agents and Agent Teams, authentication and authorization/approvals, Workspaces/files/Artifacts, Memory and Knowledge, Search, Automations, Notifications, Organizations/Teams/Memberships, Chat, Browser and Terminal entry points, reusable workflow definitions, Verification/Review, accounting, durable Connectors, portable import/export, Repository/Git integration, optional HA/failover semantics, extensive Web/CLI coverage, distributed Worker/Workspace execution, the durable Plan/Step coordinator, Registry/Marketplace support and the release/update system.

Recent convergence materially moved the frontier forward:

- **#78 is closed**; Template frontend/documentation reconciliation is no longer an open work lane.
- **#421 is closed via PR #540**; Web and CLI expose canonical durable workflow progress through the versioned Control Plane. The narrower follow-up **#560** owns safe wait/retry projection semantics, reliable coordinator-driven live refresh and final Web conformance coverage.
- **#439 has substantial planning/replanning implementation merged through PRs #546 and #559**, including platform-owned planning contracts, durable proposals/revisions, #384 handoff and exact planned Step-to-Agent execution bindings. PR **#573** adds canonical runtime-evidence-to-replanning bridging but deliberately does not claim #439 complete.
- **#500 is closed**. The portable pressure/admission contract, Linux PSI/swap/zRAM/cgroup provider, observability, authenticated Worker reporting, deployment/doctor integration and bounded semantic benchmark path are now accepted. Further real-host pressure/stress evidence belongs to **#440**, not a reopened #500; PR **#576** is the current read-only observer follow-up.
- **#501 has a merged Proposal/Specification governance core via PR #541**, including exact-revision Approval binding and idempotent Task conversion. PR **#575** targets the remaining governance recovery/idempotency gaps and currently proposes to close #501.
- **#502 has a merged provider-neutral repository-intelligence foundation and policy-enforced production wiring**. Dirty-Workspace freshness, candidate evaluation/pilots, plugin/Registry packaging and resource/evaluation work remain open.
- **#440 includes substantial single-node, coordination, API-pressure, distributed Worker/Workspace fault, heterogeneous-placement and host-pressure benchmark evidence**, but still owns final operating-envelope, real-host pressure/planner profiles, endurance/stress evidence and regression-budget work.
- **#388 remains a narrow real-host transport acceptance gap**: the network adapter exists, but actual encrypted/authenticated two-host evidence and Artifact/evidence-reference return-path proof are still required.
- **#562 adds the broader production-shaped two-VPS/private-tunnel validation**. It can reuse the same hardened topology as #388, but its issue currently declares **#46 as a hard dependency**, so #562 remains blocked until #46 closes or that dependency is explicitly revised.
- **#566 remains the optional HA productionization follow-up** to #89. It owns a real multi-process/multi-host CoordinationProvider plus shared/replicated durable-state composition and must not become a hidden dependency of the single-node baseline.
- **#567 is closed via PR #574**. Canonical Task management and reassignment now use a supported narrow Task mutation boundary instead of external coupling to private kernel command primitives.
- **#568 is closed via PR #570**. The Python test suite now has stable suite categories/markers with path-sensitive tests deliberately preserved where required.

There are currently **9 open issues out of 113 repository issues** (**104 closed; about 92.0% closed by issue count**):

`#46, #388, #439, #440, #501, #502, #560, #562, #566`

The current frontier is split deliberately:

- **Operational-v1/core convergence:** #46 final conformance, #439 planning/replanning closure, #440 remaining performance/operating-envelope evidence and #560 workflow-progress semantics/live-refresh completion. #560 currently hard-depends on #439; an implementation PR may be prepared, but it must not be treated as dependency-clean for merge unless #439 closes or the dependency is explicitly revised.
- **Real distributed acceptance:** #388 transport-specific two-host acceptance plus #562 full two-VPS/private-tunnel deployment validation. Reuse one hardened private topology, but keep #562's current #46 dependency authoritative.
- **Optional ideal-end-state expansion:** #501 Proposal/Specification governance, #502 repository intelligence and #566 production-shaped Control Plane HA. They extend the platform without becoming mandatory for ordinary direct-Task, Git/ripgrep/LSP or single-node operation.

At this snapshot, active pull requests include this documentation refresh **#569** plus implementation PRs **#573, #575, #576 and #577**. PR state can change faster than this document; GitHub issue dependencies, current PR checks/reviews and merged `main` remain authoritative.

No GitHub release has been published yet. The release/update machinery is implemented, but a published release still requires the repository release process, validated evidence/manifest and an exact accepted release commit. The operational `1.0.0` baseline remains tied to the supported M3 profile and #46 conformance rather than to optional ecosystem or HA features.

Current implementation work should follow [`docs/IMPLEMENTATION_ROADMAP.md`](docs/IMPLEMENTATION_ROADMAP.md) and each issue's current wording/comments rather than numeric issue order. GitHub issue state and merged code remain the point-in-time source of truth when a documentation snapshot becomes stale.