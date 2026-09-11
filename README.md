# AI Multi-Agent Platform

A general-purpose, self-hostable, model-agnostic and hardware-agnostic AI multi-agent platform.

The platform is built around canonical tasks, plans, steps, runs, agents, tools, models, workers, nodes, workspaces, files, artifacts, results, verification, approvals and events. Concrete systems such as Hermes, Forge, LiteLLM, MCP-compatible tool servers, model runtimes, storage engines or workflow engines integrate behind replaceable platform-owned contracts rather than defining the platform itself.

## Core principles

- General purpose; not tied to ScoreSymphony or another application domain.
- Supports both single-agent and multi-agent workloads.
- Task-centric rather than chat-centric.
- Model/provider and hardware/deployment agnostic.
- Single-node operation is a first-class baseline; distributed multi-node operation extends the same conceptual model.
- Orchestration, execution, models, tools, memory, files, knowledge, persistence, messaging, authorization, scheduling, observability and automation remain replaceable behind platform-owned boundaries.
- API-first, plugin-friendly, self-hostable and local-first.
- Baseline operation must not require recurring paid AI/API services.

## Architecture at a glance

The platform-owned kernel is the authority for externally visible Task/Run lifecycle state. Orchestrators, executors and other integrations consume platform contracts and must not become implicit owners of canonical lifecycle or domain state.

The authoritative product direction lives in [`docs/PRODUCT_VISION.md`](docs/PRODUCT_VISION.md). Non-negotiable architecture boundaries and invariants live in [`docs/ARCHITECTURE_PRINCIPLES.md`](docs/ARCHITECTURE_PRINCIPLES.md). The canonical domain model is defined in [`docs/DOMAIN_MODEL.md`](docs/DOMAIN_MODEL.md), replaceable provider boundaries in [`docs/CONTRACTS.md`](docs/CONTRACTS.md), Task/Run lifecycle ownership and recovery in [`docs/KERNEL.md`](docs/KERNEL.md), and top-level Python package ownership in [`docs/PACKAGE_BOUNDARIES.md`](docs/PACKAGE_BOUNDARIES.md).

Material architecture decisions are recorded under [`docs/adr/`](docs/adr/README.md). Implementations must not silently contradict the normative product or architecture documents.

## Start here

For a fresh checkout, environment setup and the CI-equivalent local validation path, follow [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md). Contribution and architecture-change rules live in [`CONTRIBUTING.md`](CONTRIBUTING.md), and coding-agent execution/dependency rules live in [`AGENTS.md`](AGENTS.md).

Platform-owned Python runtime, domain, adapter and Worker implementations live under `src/ai_multi_agent_platform/`; `frontend/` contains the web client; `tests/` contains automated validation; and `docs/` contains product, architecture, operations and acceptance documentation.

The canonical single-node prototype profiles are documented in [`docs/PROTOTYPE_ACCEPTANCE.md`](docs/PROTOTYPE_ACCEPTANCE.md). Wider platform acceptance and optional-profile reporting live in [`docs/PLATFORM_CONFORMANCE.md`](docs/PLATFORM_CONFORMANCE.md).

## Documentation map

| Need | Canonical location |
| --- | --- |
| Product identity and goals | [`docs/PRODUCT_VISION.md`](docs/PRODUCT_VISION.md) |
| Architecture invariants | [`docs/ARCHITECTURE_PRINCIPLES.md`](docs/ARCHITECTURE_PRINCIPLES.md) |
| Domain model | [`docs/DOMAIN_MODEL.md`](docs/DOMAIN_MODEL.md) |
| Replaceable contracts | [`docs/CONTRACTS.md`](docs/CONTRACTS.md) |
| Kernel lifecycle and recovery | [`docs/KERNEL.md`](docs/KERNEL.md) |
| Top-level package ownership | [`docs/PACKAGE_BOUNDARIES.md`](docs/PACKAGE_BOUNDARIES.md) |
| Current project status | [`docs/STATUS.md`](docs/STATUS.md) |
| Dependency-driven implementation planning | [`docs/IMPLEMENTATION_ROADMAP.md`](docs/IMPLEMENTATION_ROADMAP.md) |
| Release and operations process | [`docs/RELEASE_PROCESS.md`](docs/RELEASE_PROCESS.md) |
| Development setup and validation | [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) |
| Contribution rules | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| Coding-agent rules | [`AGENTS.md`](AGENTS.md) |
| Architecture decisions | [`docs/adr/`](docs/adr/README.md) |
| Platform acceptance/conformance | [`docs/PLATFORM_CONFORMANCE.md`](docs/PLATFORM_CONFORMANCE.md) |

## Project status

This repository is under active development. The curated point-in-time implementation and integration status lives in [`docs/STATUS.md`](docs/STATUS.md); the dependency-driven planning view lives in [`docs/IMPLEMENTATION_ROADMAP.md`](docs/IMPLEMENTATION_ROADMAP.md).

GitHub issue state, explicit issue dependencies, pull-request reviews/checks and merged repository state remain authoritative for individual work items. Published releases, once available, are authoritative for release history. The README intentionally does not duplicate issue-by-issue progress or dated implementation ledgers.

## Licensing and upstream components

Project-owned source is distributed under the MIT License in [`LICENSE`](LICENSE). Rules for third-party source, dependencies, services, adapters, vendored/forked source, selective ports and reference-only influence live in [`LICENSE_POLICY.md`](LICENSE_POLICY.md).

Before approving an architecture-significant upstream, complete [`docs/UPSTREAM_ADOPTION_CHECKLIST.md`](docs/UPSTREAM_ADOPTION_CHECKLIST.md). Approved/integrated upstream provenance is recorded in [`docs/UPSTREAMS.md`](docs/UPSTREAMS.md) using the machine-readable starting format in [`upstream/PROVENANCE_TEMPLATE.yaml`](upstream/PROVENANCE_TEMPLATE.yaml). Updates and periodic reviews follow [`docs/UPSTREAM_UPDATE_WORKFLOW.md`](docs/UPSTREAM_UPDATE_WORKFLOW.md).
