# AI Multi-Agent Platform

A general-purpose, self-hostable, local-first, model-agnostic and hardware-agnostic platform for defining, orchestrating, executing, observing and evaluating AI-assisted work across one or many machines.

The platform is built around canonical Tasks, Plans, Steps, Runs, Agents, Tools, Models, Workers, Nodes, Workspaces, Files, Artifacts, Results, Verification, Approvals and Events. Concrete systems such as Hermes, Forge, LiteLLM, MCP-compatible tool servers, model runtimes, storage engines or workflow engines integrate behind replaceable platform-owned contracts rather than defining the platform itself.

## Core principles

- General purpose; not tied to ScoreSymphony or another application domain.
- Supports both single-agent and multi-agent workloads.
- Task-centric rather than chat-centric, while still supporting interactive chat as one product surface.
- Model/provider and hardware/deployment agnostic.
- Local and self-hosted operation are first-class; baseline operation must not require recurring paid AI/API services.
- Single-node operation is a first-class baseline; distributed multi-node operation extends the same conceptual model.
- Orchestration, execution, models, tools, memory, files, knowledge, persistence, messaging, authorization, scheduling, observability and automation remain replaceable behind platform-owned boundaries.
- API-first, plugin-friendly and designed so Web UI, CLI and external applications consume the same canonical platform state.

## Architecture at a glance

The platform owns the canonical workflow and lifecycle model:

```text
Goal
  ↓
Task
  ↓
Plan
  ↓
Steps / Subtasks
  ↓
Runs / Worker Jobs
  ↓
Artifacts
  ↓
Result
```

The platform-owned kernel is authoritative for externally visible Task/Run lifecycle state. Orchestrators, executors, model providers, tool providers, storage systems and other integrations consume platform contracts and must not become implicit owners of canonical lifecycle or domain state.

```text
Clients / External Applications
            │
            ▼
      Control Plane API
            │
            ▼
      Platform Kernel
            │
   ┌────────┼────────┐
   ▼        ▼        ▼
Orchestration  Execution  Providers
   │           │       models / tools /
   │           │       memory / files /
   │           │       knowledge / events
   └───────────┴───────────────┐
                               ▼
                       Nodes / Workers
```

The authoritative product direction lives in [`docs/PRODUCT_VISION.md`](docs/PRODUCT_VISION.md). Non-negotiable architecture boundaries and invariants live in [`docs/ARCHITECTURE_PRINCIPLES.md`](docs/ARCHITECTURE_PRINCIPLES.md). The canonical domain model is defined in [`docs/DOMAIN_MODEL.md`](docs/DOMAIN_MODEL.md), replaceable provider boundaries in [`docs/CONTRACTS.md`](docs/CONTRACTS.md), Task/Run lifecycle ownership and recovery in [`docs/KERNEL.md`](docs/KERNEL.md), and top-level Python package ownership in [`docs/PACKAGE_BOUNDARIES.md`](docs/PACKAGE_BOUNDARIES.md).

Material architecture decisions are recorded under [`docs/adr/`](docs/adr/README.md). Implementations must not silently contradict the normative product or architecture documents.

## What is implemented today

The repository has moved beyond foundational scaffolding into active product integration, acceptance, hardening and operating-envelope work. The merged platform already contains substantial end-to-end functionality across the following areas.

### Core workflow and control plane

- canonical Tasks, Plans, Steps, Runs, Events, Results and Artifacts;
- versioned Control Plane APIs used by supported clients;
- durable Plan/Step coordination with dependencies, fan-out/fan-in, waits, retries, cancellation, reconciliation and recovery semantics;
- Projects, Workspaces, Files and portable result/artifact handling;
- Web and CLI client paths over the canonical Control Plane.

### Agents, models and capabilities

- Agents and Agent Teams;
- autonomous planning and bounded replanning foundations;
- exact planned Step-to-Agent execution bindings;
- model-provider and model-routing contracts, including durable routing-profile revisions;
- tools/capabilities and MCP-compatible integration boundaries;
- Chat, Browser and Terminal product capabilities;
- reference and distributed execution paths behind platform-owned execution contracts.

### Memory, knowledge and repository intelligence

- Memory and Knowledge domains;
- Search and discovery over supported platform resources;
- provider-neutral repository intelligence with repository maps, deterministic search, exact source slices and revision provenance;
- Repository/Git integration through authorization-aware platform services;
- reusable workflow definitions, Templates and portable import/export.

### Governance, review and user-attention flows

- authentication and session handling;
- authorization, policy enforcement and human approvals;
- Verification/Review workflows;
- Notifications and user-attention surfaces;
- Organizations, Teams and Memberships;
- optional Proposal/Specification governance foundations with immutable revisions and exact Approval binding;
- usage/resource accounting and related policy integrations.

### Distributed execution and operations

- Node/Worker and resource abstractions;
- authenticated remote Worker reporting and distributed Workspace materialization;
- network-capable distributed transport while preserving the same canonical lifecycle model;
- host-pressure telemetry and pressure-aware admission foundations;
- observability and diagnostic paths;
- backup, upgrade, release-manifest and compatibility/provenance foundations;
- deterministic performance, fault, restart, endurance and distributed-execution test tooling.

### Extension and ecosystem foundations

- Registry/Marketplace foundations with local/offline discovery, compatibility, integrity and trust validation;
- Connector persistence with restart-stable identities and sync checkpoints;
- architecture-significant upstream provenance and update workflows;
- platform-wide conformance profiles and machine-readable acceptance evidence.

This is deliberately a stable capability summary rather than an issue-by-issue progress ledger. For the curated current integration state, use [`docs/STATUS.md`](docs/STATUS.md). For individual work items, GitHub issues, dependencies, pull-request checks and the exact merged repository state remain authoritative.

## Current maturity

The usable single-node prototype acceptance gate has passed, and the repository now concentrates on convergence, conformance, hardening, measured operating envelopes and release readiness rather than basic platform construction.

No formal GitHub release has been published yet. Release claims are therefore intentionally conservative: merged functionality is real, but operational-version acceptance still depends on the repository's release and conformance gates. See [`CHANGELOG.md`](CHANGELOG.md), [`docs/STATUS.md`](docs/STATUS.md), [`docs/PLATFORM_CONFORMANCE.md`](docs/PLATFORM_CONFORMANCE.md) and [`docs/RELEASE_PROCESS.md`](docs/RELEASE_PROCESS.md) for the corresponding evidence and process.

## Quickstart for development

Requirements:

- Python 3.12+
- Git

Clone the repository and create an isolated environment:

```bash
git clone https://github.com/ScoreSymphony/AI-Multi-Agent-Platform.git
cd AI-Multi-Agent-Platform
python -m venv .venv
```

Activate it on Linux/macOS:

```bash
source .venv/bin/activate
```

or in Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install the project with development dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Run the CI-equivalent validation path:

```bash
ruff format --check .
ruff check .
mypy
pytest
python -m build
```

The complete development setup and validation contract lives in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md).

## Repository layout

```text
AI-Multi-Agent-Platform/
├── src/ai_multi_agent_platform/   # platform runtime, domain and adapters
├── frontend/                      # web/control-plane client
├── tests/                         # automated validation and conformance tests
├── docs/                          # product, architecture, operations and acceptance docs
├── upstream/                      # upstream provenance material
├── pyproject.toml
├── CONTRIBUTING.md
└── AGENTS.md
```

Runtime integrations implement platform-owned contracts under `src/ai_multi_agent_platform/` rather than creating parallel ownership of canonical domain state.

## Start here

For a fresh checkout, environment setup and the CI-equivalent local validation path, follow [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md). Contribution and architecture-change rules live in [`CONTRIBUTING.md`](CONTRIBUTING.md), and coding-agent execution/dependency rules live in [`AGENTS.md`](AGENTS.md).

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
| User-visible accumulated changes | [`CHANGELOG.md`](CHANGELOG.md) |
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
