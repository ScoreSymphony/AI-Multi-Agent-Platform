# AI Multi-Agent Platform

A general-purpose, self-hostable AI Multi-Agent Platform for turning goals into durable, observable work carried out by one or more specialized agents.

The reference baseline is local-first and self-hosted. Models, orchestrators, executors and capabilities remain replaceable behind platform-owned contracts, so the platform is not defined by Hermes, Forge, LiteLLM, MCP or any single model vendor. A single-agent wrapper can submit one model-driven action; this platform additionally owns the Task/Plan/Step/Run lifecycle, multi-agent coordination, Artifacts, Results, Verification and traceable history around that work.

## 30-second architecture

The primary product path is intentionally small:

```text
User / Web / CLI
       |
       v
  Control Plane
       |
       v
   Goal / Task
       |
       v
Planner -> Plan / Steps
       |
       v
Agent Team -> Models / Capabilities
       |
       v
Execution / Workers
       |
       v
Artifact / Result -> Verification
       |
       v
 Trace / durable history
```

The platform owns the canonical state in that path. Orchestrators, executors, model providers, capability/tool providers, storage implementations and distributed transports are replaceable implementations around it; they do not become a second lifecycle authority.

## Quick Start

### Prerequisites

- Python 3.12 or newer
- Git
- local write access for the platform data directory

No GPU, paid AI/API service, Hermes, Forge, LiteLLM, MCP server or remote Worker is required to install, start and smoke-test the reference single-node platform.

From a clean checkout on Linux/macOS:

```bash
git clone https://github.com/ScoreSymphony/AI-Multi-Agent-Platform.git
cd AI-Multi-Agent-Platform
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install '.[server]'
cp config/single-node.env.example .env.single-node
set -a
. ./.env.single-node
set +a
platform-server bootstrap-admin --username admin
platform-server smoke
platform-server serve
```

`bootstrap-admin` asks for the administrator password without putting it in shell history. `platform-server smoke` runs a canonical Task/Run with the in-process reference orchestrator/executor and requires no model endpoint or external service.

On Windows PowerShell, use `.\.venv\Scripts\Activate.ps1` and set the `AI_MAP_*` values from `config/single-node.env.example` in the current process instead of sourcing the POSIX env file. The maintained deployment guide contains the complete Windows and operator instructions.

With the server running, check the public Control Plane from a second terminal:

```bash
curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:8000/api/v1/readiness
platform --endpoint http://127.0.0.1:8000 doctor
```

The reference listener is `127.0.0.1:8000`. For configuration, restart behavior, secure exposure and the supported single-server topology, continue with [`docs/operations/DEPLOYMENT.md`](docs/operations/DEPLOYMENT.md).

## Run your first multi-agent task

The maintained first product workflow is a real multi-agent run, not a UI-only demo. It uses the same canonical Control Plane state as the rest of the platform.

First authenticate and inspect onboarding readiness:

```bash
platform auth login --username admin
platform onboarding status
```

The server and baseline smoke above need no model. The multi-agent workflow does need one explicitly configured, healthy text model at location `local` or `self_hosted`, plus one owned Project and Workspace. Onboarding never selects a remote or paid provider implicitly. Use the `/onboarding` Web flow when the optional frontend is deployed, or follow [`docs/product/MULTI_AGENT_FIRST_RUN.md`](docs/product/MULTI_AGENT_FIRST_RUN.md) for the exact CLI/API setup against your actual local model endpoint.

Once onboarding reports a usable scope, run the official scenario:

```bash
platform onboarding run-multi-agent \
  --objective "Research two viable approaches, produce a concise result from both inputs, and review the exact result."
```

If more than one owned Project or Workspace exists, pass the `--project-id` and `--workspace-id` values returned by onboarding. The visible workflow is:

```text
Goal
  |
  v
Task -> Plan
        |-- Gather evidence ---------------- researcher ----\
        |                                                  |
        |-- Prepare execution approach ------- developer ---+--> Produce result -- developer
        |                                                                    |
        +--------------------------------------------------------------------v
                                                               Verification -- reviewer
                                                                    |
                                                                    v
                                                         Artifact + Result + trace
```

The two preparation branches are parallel-ready, the producing Step waits for both, and Verification is bound to the exact produced Result. The Web onboarding projection and the CLI/API response expose the Plan, Step dependencies, assigned Agent revisions, Runs, Artifact, Result, Verification records and trace identifiers so the same run can be inspected rather than inferred from chat output.

Hermes, Forge, LiteLLM, MCP, the standard Agent catalog and paid providers are not baseline requirements for this workflow. The maintained walkthrough, model setup contract, recovery guidance and acceptance fixture are in [`docs/product/MULTI_AGENT_FIRST_RUN.md`](docs/product/MULTI_AGENT_FIRST_RUN.md).

## How it works

Canonical platform state is owned by the platform itself:

```text
Goal -> Task -> Plan -> Steps -> Runs -> Artifacts -> Result -> Verification
```

A Planner may decompose a Task into dependent or parallel-ready Steps. Exact Agent revisions are bound to planned work, while model and capability selection stays behind platform-owned provider contracts. Execution may remain local or extend to Workers on other machines without changing the canonical Task/Run model. Results and Artifacts stay attached to durable platform state, and Verification/Approvals remain explicit rather than being inferred from provider output.

For normative details, use [`docs/ARCHITECTURE_PRINCIPLES.md`](docs/ARCHITECTURE_PRINCIPLES.md), [`docs/DOMAIN_MODEL.md`](docs/DOMAIN_MODEL.md), [`docs/CONTRACTS.md`](docs/CONTRACTS.md), [`docs/KERNEL.md`](docs/KERNEL.md) and the ADR index under [`docs/adr/`](docs/adr/README.md). Contributor-oriented code navigation lives in [`docs/CONTRIBUTOR_ARCHITECTURE.md`](docs/CONTRIBUTOR_ARCHITECTURE.md).

## Current maturity

The usable single-node prototype gate and maintained multi-agent first-run path exist on `main`. The repository is still under active development and has not published a formal GitHub release, so release claims remain conservative while conformance, hardening and operational-version acceptance continue.

Architectural role and public compatibility maturity are separate concepts; the current taxonomy is documented in [`docs/FEATURE_CLASSIFICATION.md`](docs/FEATURE_CLASSIFICATION.md). Curated point-in-time integration status lives in [`docs/STATUS.md`](docs/STATUS.md), while GitHub issues, dependencies, pull-request checks and merged repository state remain authoritative for individual work items. The README intentionally does not duplicate an issue/PR ledger or dated progress history.

## Develop and contribute

For a development checkout, install the development extras instead of the server-only baseline:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Run the CI-equivalent local validation path:

```bash
ruff format --check .
ruff check .
mypy
pytest
python -m build
```

Development setup lives in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md). Contribution and architecture-change rules live in [`CONTRIBUTING.md`](CONTRIBUTING.md), coding-agent rules in [`AGENTS.md`](AGENTS.md), and the dependency-driven planning view in [`docs/IMPLEMENTATION_ROADMAP.md`](docs/IMPLEMENTATION_ROADMAP.md).

## Repository layout

```text
AI-Multi-Agent-Platform/
├── src/ai_multi_agent_platform/   # platform runtime, domain and adapters
├── frontend/                      # optional Web client
├── tests/                         # automated validation and conformance tests
├── docs/                          # product, architecture, operations and acceptance docs
├── upstream/                      # upstream provenance material
├── pyproject.toml
├── CONTRIBUTING.md
└── AGENTS.md
```

Runtime integrations implement platform-owned contracts under `src/ai_multi_agent_platform/` rather than creating parallel ownership of canonical domain state.

## Documentation map

| Need | Canonical location |
| --- | --- |
| First successful multi-agent run | [`docs/product/MULTI_AGENT_FIRST_RUN.md`](docs/product/MULTI_AGENT_FIRST_RUN.md) |
| Installation and single-node deployment | [`docs/operations/DEPLOYMENT.md`](docs/operations/DEPLOYMENT.md) |
| Security policy and threat model | [`SECURITY.md`](SECURITY.md), [`docs/SECURITY_THREAT_MODEL.md`](docs/SECURITY_THREAT_MODEL.md) |
| Product identity and goals | [`docs/PRODUCT_VISION.md`](docs/PRODUCT_VISION.md) |
| Architecture invariants | [`docs/ARCHITECTURE_PRINCIPLES.md`](docs/ARCHITECTURE_PRINCIPLES.md) |
| Contributor architecture and code navigation | [`docs/CONTRIBUTOR_ARCHITECTURE.md`](docs/CONTRIBUTOR_ARCHITECTURE.md) |
| Domain model | [`docs/DOMAIN_MODEL.md`](docs/DOMAIN_MODEL.md) |
| Replaceable contracts | [`docs/CONTRACTS.md`](docs/CONTRACTS.md) |
| Kernel lifecycle and recovery | [`docs/KERNEL.md`](docs/KERNEL.md) |
| Feature role and API stability taxonomy | [`docs/FEATURE_CLASSIFICATION.md`](docs/FEATURE_CLASSIFICATION.md) |
| Platform acceptance/conformance | [`docs/PLATFORM_CONFORMANCE.md`](docs/PLATFORM_CONFORMANCE.md) |
| Current integration status | [`docs/STATUS.md`](docs/STATUS.md) |
| Release and operations process | [`docs/RELEASE_PROCESS.md`](docs/RELEASE_PROCESS.md) |
| Development setup | [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) |
| Contribution rules | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| Architecture decisions | [`docs/adr/`](docs/adr/README.md) |

## Licensing and upstream components

Project-owned source is distributed under the MIT License in [`LICENSE`](LICENSE). Third-party source, dependencies, services, adapters, vendored/forked source and reference-only influence follow [`LICENSE_POLICY.md`](LICENSE_POLICY.md).

Architecture-significant upstream adoption and updates are governed by [`docs/UPSTREAM_ADOPTION_CHECKLIST.md`](docs/UPSTREAM_ADOPTION_CHECKLIST.md), [`docs/UPSTREAMS.md`](docs/UPSTREAMS.md) and [`docs/UPSTREAM_UPDATE_WORKFLOW.md`](docs/UPSTREAM_UPDATE_WORKFLOW.md).
