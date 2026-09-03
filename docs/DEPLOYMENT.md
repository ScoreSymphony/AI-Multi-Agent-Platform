# Deployment profiles and self-hosted installation

Issue: #39

## Architecture boundary

Deployment profiles are compositions over canonical platform contracts. They do not define
new Task, Run, Project, Workspace, Agent, Node, Worker, authentication or authorization
identity. Paths, process IDs, ports, container IDs and hostnames are deployment metadata,
never canonical platform identity.

The first supported production topology is deliberately one ordinary machine:

```text
client / optional frontend
        |
        v
Authenticated Control Plane
        |
        +-- canonical Task / Run kernel ---- SQLite
        +-- Project scope identities ------- SQLite
        +-- local authentication ----------- SQLite verifiers/metadata
        +-- local authorization ------------ SQLite policies
        +-- FileProvider ------------------- filesystem bytes + SQLite metadata
        +-- WorkspaceProvider -------------- local materialization + SQLite metadata
        +-- ReferenceOrchestrator
        `-- ReferenceExecutor
```

Hermes, Forge, LiteLLM, MCP, remote Workers, Kubernetes, cloud services and paid external
AI/API services are not required for this profile. Later profiles add replaceable services
without changing canonical Task/Run contracts.

## Prerequisites

Stage 1 currently targets a host with:

- Python 3.12 or newer;
- Git;
- local write access for the platform data directory;
- enough storage for the operator's canonical state, files and workspaces.

No GPU is required. CPU-only is the reference baseline.

## Install

From a fresh clone:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install '.[server]'
```

On Windows PowerShell, activate the environment with `.venv\Scripts\Activate.ps1`.

## Configuration

`config/single-node.env.example` lists the supported Stage-1 environment settings. They are
ordinary configuration only; credentials do not belong in that file.

```bash
export AI_MAP_DATA_DIR="$PWD/.data/single-node"
export AI_MAP_HOST="127.0.0.1"
export AI_MAP_PORT="8000"
export AI_MAP_SECURE_COOKIE="true"
export AI_MAP_LOG_LEVEL="info"
```

The deployment loader imports only explicitly supported environment variables through the
#34 configuration boundary. It does not forward the complete process environment to the
platform.

`AI_MAP_SECURE_COOKIE=false` is accepted only for a loopback-only deployment. External
exposure should keep secure cookies enabled and terminate TLS at an explicitly configured
reverse proxy or equivalent trusted boundary.

## First administrator bootstrap

Authentication and authorization remain separate by design. The bootstrap command creates
or verifies the first local human identity and separately installs an explicit #15 local
administrator policy:

```bash
platform-server bootstrap-admin --username admin
```

The password is read with a hidden interactive prompt. For controlled automation, pass
`--password-stdin` and provide exactly one line on standard input. There is intentionally no
`--password` command-line option, avoiding routine leakage through shell history/process
arguments.

The operation is retry-safe for the same first username/password. If a process interruption
occurs after identity creation but before policy creation, re-running the command repairs the
missing policy instead of creating another user.

## Start

```bash
platform-server serve
```

The default listener is `127.0.0.1:8000`. This is deliberate minimal exposure. Binding an
externally reachable address is an operator decision and should be paired with TLS/reverse
proxy policy appropriate to that environment.

## Health and readiness

Public liveness/readiness surfaces remain available through the canonical Control Plane:

```bash
curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:8000/api/v1/ready
```

Required SQLite/file configuration failures fail during composition/startup. Optional
external adapters are not required by this profile and therefore cannot make the baseline
unready merely by being absent.

## Persistent layout

The default data root is `.data/single-node`. Its current implementation layout is:

```text
.data/single-node/
├── db/
│   ├── kernel.sqlite3
│   ├── scopes.sqlite3
│   ├── authentication.sqlite3
│   ├── authorization.sqlite3
│   ├── files.sqlite3
│   └── workspaces.sqlite3
├── files/
├── workspaces/
└── executor/
    └── reference/
```

These paths are implementation configuration, not canonical resource IDs. Moving the data
through the future #40 backup/restore flow must not require preserving a hostname, machine ID
or filesystem path as canonical identity.

Authentication SQLite stores password/token verifiers and safe metadata only. Raw passwords,
browser-session secrets and bearer-token secrets are not persisted.

## Restart

A clean restart uses the same `AI_MAP_DATA_DIR` and runs the same command:

```bash
platform-server serve
```

The Stage-1 regression suite verifies restart persistence for:

- canonical Task/Run state;
- Project identity and idempotency state;
- local user accounts;
- browser sessions;
- API credentials including credential-local scope metadata;
- local administrator authorization policy.

The ReferenceExecutor itself remains replaceable and does not become canonical lifecycle
storage.

## Shutdown

Use the service manager's normal graceful stop or `Ctrl+C` for a foreground process. Uvicorn
handles ASGI process shutdown; canonical durable state has already been committed through the
platform persistence boundaries rather than being owned by the web-server process.

## Networking baseline

Stage 1 needs only the client/frontend-to-Control-Plane flow. SQLite and local file/workspace
storage have no network listener. The reference orchestrator and executor are in-process and
expose no private admin port.

Later profiles may add explicit internal flows for remote Workers, model endpoints, message
transport, tools, browser services and connectors. Those services must not be made public by
default simply because deployment tooling can expose a port.

## Resource guidance

There is intentionally no VPS SKU or fixed hardware requirement. Start with the resources
needed by the chosen local workloads and measure:

- resident memory of the Control Plane plus enabled adapters;
- SQLite/file growth;
- workspace working-set size;
- CPU concurrency and model/runtime requirements;
- optional accelerator memory only when an enabled model backend needs it.

The reference path itself is CPU-only and requires no accelerator.

## Current Stage-1 boundary

This baseline intentionally does not package distributed Worker topology or Control Plane HA.

- #14 + #35 extend the same installation model with remote Workers and capability-based
  placement.
- #40 adds tested backup/restore and relocation.
- #41 adds schema/platform upgrade lifecycle.
- #89 may later add an optional HA profile.

Single-node production remains a valid topology after those additions.
