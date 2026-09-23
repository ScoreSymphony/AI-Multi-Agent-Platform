<p align="center">
  <img src="frontend/src/assets/brand-icon.png" alt="AI Multi-Agent Platform icon" width="96" />
</p>

# AI Multi-Agent Platform

A general-purpose, self-hostable AI Multi-Agent Platform for turning goals into durable, observable work carried out by one or more specialized agents.

The reference baseline is local-first and self-hosted. Models, orchestrators, executors and capabilities remain replaceable behind platform-owned contracts, so the platform is not defined by Hermes, LiteLLM, MCP or any single model vendor. The retired Forge integration remains historical architecture evidence rather than an active runtime. A single-agent wrapper can submit one model-driven action; this platform additionally owns the Task/Plan/Step/Run lifecycle, multi-agent coordination, Artifacts, Results, Verification and traceable history around that work.

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
- for the browser UI: Node.js 22.22.2+ and npm 11.6.x

No GPU, paid AI/API service, Hermes, LiteLLM, MCP server or remote Worker is required for the reference single-node first run. The retired Forge runtime is not part of this baseline.

### Optional Docker Compose deployment

For production/self-hosting, the repository-root `docker-compose.yml` is HTTPS-first. Point a DNS
hostname at the server and supply it before deployment:

```bash
export AI_MAP_PUBLIC_DOMAIN=agents.example.com
docker compose -f docker-compose.yml build
docker compose -f docker-compose.yml up -d
```

The root profile publishes only ports 80/443 through the repository-owned Caddy edge, keeps Web and
Control Plane private, retains Secure cookies, and keeps canonical state in `platform-data`.
Missing `AI_MAP_PUBLIC_DOMAIN` fails closed instead of exposing a public HTTP `:8080` UI.

For a **new** Hostinger Docker Manager deployment, use this zero-configuration Compose-file URL:

```text
https://raw.githubusercontent.com/ScoreSymphony/AI-Multi-Agent-Platform/main/deploy/docker/docker-compose.hostinger-zero-config.yml
```

Copy that URL into Hostinger's **Compose from URL** field. The repository landing page itself is not
the Compose URL. The zero-config Hostinger path assumes Hostinger's Traefik project is already
running and owns ports 80/443. On current Hostinger deployments Traefik may itself use Docker
`host` networking and therefore does not require a shared `traefik-proxy` network. The platform
does **not** bind 80/443 itself and does not require an external Docker network. The dedicated
gateway stays on the private platform bridge and reads the VPS hostname through the host UTS
namespace. Hostinger may expose that kernel hostname as either `srvNNNNNN` or
`srvNNNNNN.hstgr.cloud`; the numeric short form is normalized to the managed
`srvNNNNNN.hstgr.cloud` DNS hostname before deriving
`${COMPOSE_PROJECT_NAME}.srvNNNNNN.hstgr.cloud`. The gateway is then discovered through Traefik's
Docker provider labels. Web and Control Plane remain private.

No `TRAEFIK_HOST`, `AI_MAP_PUBLIC_DOMAIN`, `AI_MAP_TRAEFIK_NETWORK`, or
`AI_MAP_TRAEFIK_EXTERNAL` value is required for this zero-config profile. The platform publishes
no host port of its own: public access is exclusively through Hostinger Traefik on ports 80/443,
so no additional firewall rule for an arbitrary high port is required. The zero-config profile
advertises no fake exact fallback hostname; Hostinger's normal **Open** action is expected to use
the managed `<project>.srvNNNNNN.hstgr.cloud` route directly.

For backward compatibility, an existing zero-config deployment may continue to set
`AI_MAP_PUBLIC_DOMAIN=<your-hostname>`. That optional override is routed through literal,
case-insensitive HostRegexp/HostSNIRegexp compatibility rules. When the override is absent those
compatibility routers render an impossible matcher, so they neither match empty SNI nor
reintroduce a concrete `setup.invalid` host. For new custom-domain deployments, the explicit
`docker-compose.hostinger-custom-domain.yml` profile remains the clearer strict configuration.

Existing installations already tracking
`deploy/docker/docker-compose.hostinger.yml` or
`deploy/docker/docker-compose.hostinger-https.yml` keep their previous migration-safe topology.
For a custom DNS hostname on the same host-network Traefik topology, use the explicit
[`docker-compose.hostinger-custom-domain.yml`](deploy/docker/docker-compose.hostinger-custom-domain.yml)
profile and set `AI_MAP_PUBLIC_DOMAIN`. If the VPS does **not** run Hostinger Traefik and ports
80/443 are free, use
[`docker-compose.hostinger-direct.yml`](deploy/docker/docker-compose.hostinger-direct.yml)
instead. Detailed Hostinger, backup/restore and alternate-edge guidance lives in
[`deploy/docker/README.md`](deploy/docker/README.md).

### Linux/macOS

From a clean checkout, install and start the Control Plane:

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
platform-server serve
```

In a second terminal, start the Web UI:

```bash
cd AI-Multi-Agent-Platform/frontend
npm install
npm run dev
```

### Windows PowerShell

From a clean checkout, install and start the Control Plane:

```powershell
git clone https://github.com/ScoreSymphony/AI-Multi-Agent-Platform.git
Set-Location AI-Multi-Agent-Platform

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install ".[server]"

Copy-Item config\single-node.env.example .env.single-node

$env:AI_MAP_DATA_DIR = ".data\single-node"
$env:AI_MAP_HOST = "127.0.0.1"
$env:AI_MAP_PORT = "8000"
$env:AI_MAP_SECURE_COOKIE = "true"
$env:AI_MAP_LOG_LEVEL = "info"
$env:AI_MAP_SHUTDOWN_TIMEOUT_SECONDS = "30"

platform-server serve
```

In a second PowerShell window, start the Web UI:

```powershell
Set-Location AI-Multi-Agent-Platform\frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. On a fresh installation the server-owned bootstrap state routes directly to **Create your administrator account**. Creating the account installs the explicit initial administrator policy, establishes the browser session, and continues into the persistent setup wizard. The wizard discovers the environment, lets you review component/application choices, previews compatibility and dependency actions before mutation, resumes persisted progress after reload/restart, and validates the actual backend state. Onboarding readiness does not lock the authenticated shell: installation and configuration surfaces remain reachable while setup is incomplete, with an advisory callout linking back to the wizard. Operations that genuinely need unfinished prerequisites still remain unavailable until those prerequisites are configured.

The normal single-node profile also ships a conservative starter Registry, so optional reviewed packages can appear in this component step without an operator first configuring `AI_MAP_REGISTRY_CATALOG`. The current starter catalog exposes the platform-owned **Hermes adapter** as an optional orchestration package. Installing that package does not install or start the Hermes API server: the UI reports `adapter_installed` and keeps the separately managed self-hosted runtime as an explicit prerequisite. No remote or paid provider is selected implicitly. An explicitly configured `registry_catalog` remains the operator-controlled replacement distribution source, while `AI_MAP_REGISTRY_ENABLED=false` preserves an explicit no-Registry deployment when desired.

`platform-server bootstrap-admin --username ...` remains available as an operator/recovery alternative, but it is not a prerequisite for the normal browser-first flow. `platform-server smoke` remains available after an administrator exists and runs a canonical Task/Run with the in-process reference orchestrator/executor without requiring a model endpoint or external service.

The maintained deployment guide contains the complete operator instructions. On Windows, the `AI_MAP_*` values above apply to the current PowerShell process; set them again in a new shell before starting the Control Plane after a reboot or terminal restart.

With the server running, the public health surfaces remain available from another terminal.

Linux/macOS:

```bash
curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:8000/api/v1/readiness
platform --endpoint http://127.0.0.1:8000 doctor
```

Windows PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/readiness
platform --endpoint http://127.0.0.1:8000 doctor
```

The reference Control Plane listener is `127.0.0.1:8000`; the local Vite Web UI is `127.0.0.1:5173` and proxies `/api` to that Control Plane. For configuration, restart behavior, secure exposure and the supported single-server topology, continue with [`docs/operations/DEPLOYMENT.md`](docs/operations/DEPLOYMENT.md). Frontend-specific development and production-build details are in [`frontend/README.md`](frontend/README.md).

## Run your first multi-agent task

The maintained first product workflow is a real multi-agent run, not a UI-only demo. It uses the same canonical Control Plane state as the rest of the platform.

The browser-first setup initially lands incomplete installations on `/onboarding` after authentication, but it does not trap the authenticated user there. Marketplace, Applications, Models, Integrations, Settings and other product surfaces remain reachable while setup is incomplete; the shell shows a resumable onboarding callout instead of redirecting every route. CLI users can inspect the same canonical onboarding state after authenticating:

```bash
platform auth login --username admin
platform onboarding status
```

The server baseline needs no model. The multi-agent workflow does need one explicitly configured, healthy text model at location `local` or `self_hosted`, plus one owned Project and Workspace. Onboarding never selects a remote or paid provider implicitly. Use the `/onboarding` Web flow, or follow [`docs/product/MULTI_AGENT_FIRST_RUN.md`](docs/product/MULTI_AGENT_FIRST_RUN.md) for the exact CLI/API setup against your actual local model endpoint.

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

Hermes, LiteLLM, MCP, the standard Agent catalog and paid providers are not baseline requirements for this workflow. The retired Forge runtime is not an optional prerequisite. The maintained walkthrough, model setup contract, recovery guidance and acceptance fixture are in [`docs/product/MULTI_AGENT_FIRST_RUN.md`](docs/product/MULTI_AGENT_FIRST_RUN.md).

## How it works

Canonical platform state is owned by the platform itself:

```text
Goal -> Task -> Plan -> Steps -> Runs -> Artifacts -> Result -> Verification
```

A Planner may decompose a Task into dependent or parallel-ready Steps. Exact Agent revisions are bound to planned work, while model and capability selection stays behind platform-owned provider contracts. Execution may remain local or extend to Workers on other machines without changing the canonical Task/Run model. Results and Artifacts stay attached to durable platform state, and Verification/Approvals remain explicit rather than being inferred from provider output.

For normative details, use [`docs/ARCHITECTURE_PRINCIPLES.md`](docs/ARCHITECTURE_PRINCIPLES.md), [`docs/DOMAIN_MODEL.md`](docs/DOMAIN_MODEL.md), [`docs/CONTRACTS.md`](docs/CONTRACTS.md), [`docs/KERNEL.md`](docs/KERNEL.md) and the ADR index under [`docs/adr/`](docs/adr/README.md). Contributor-oriented code navigation lives in [`docs/CONTRIBUTOR_ARCHITECTURE.md`](docs/CONTRIBUTOR_ARCHITECTURE.md).

## Current maturity

The usable single-node prototype gate and maintained multi-agent first-run path exist on `main`. The repository is still under active development and has not published the formal platform/V1 release; the first formal platform publication still targets `1.0.0`. Separately versioned optional Android client artifacts are already published under `mobile-v*` GitHub Releases. Those mobile artifacts do not constitute the platform `1.0.0` release or make Mobile part of the required V1 baseline.

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
