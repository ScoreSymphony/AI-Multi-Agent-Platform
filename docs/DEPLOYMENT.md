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

The `server` extra installs the HTTP server needed by `platform-server serve`. Hermes, Forge,
LiteLLM, MCP and model-provider extras remain optional and are not required by this baseline.

## Generate and load configuration

Start from the checked-in credential-free example rather than editing a source-controlled
secret file:

```bash
cp config/single-node.env.example .env.single-node
```

Review `.env.single-node`, especially `AI_MAP_DATA_DIR`, host and port. The platform does not
silently load dotenv files: supported values enter through the process environment and then
go through the #34 configuration resolver.

For a POSIX shell:

```bash
set -a
. ./.env.single-node
set +a
```

For PowerShell, set the corresponding `AI_MAP_*` environment variables in the current
process before invoking `platform-server`.

The supported Stage-1 settings are:

```bash
export AI_MAP_DATA_DIR="$PWD/.data/single-node"
export AI_MAP_HOST="127.0.0.1"
export AI_MAP_PORT="8000"
export AI_MAP_SECURE_COOKIE="true"
export AI_MAP_LOG_LEVEL="info"
```

The deployment loader imports only explicitly supported environment variables. It does not
forward the complete process environment to the platform, and the example contains no
credentials.

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

## Canonical Task/Run smoke test

After the first administrator exists, run the built-in baseline smoke:

```bash
platform-server smoke
```

This creates a small Project and executes one canonical Task/Run through the in-process
`ReferenceOrchestrator` and `ReferenceExecutor`. It requires no paid API, model endpoint,
remote Worker, MCP server, LiteLLM, Hermes or Forge.

The smoke uses stable idempotency keys. Re-running it, including after a process restart,
reuses the same canonical smoke Task/Run instead of duplicating work. Success prints the
canonical Task/Run IDs and terminal statuses.

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
curl http://127.0.0.1:8000/api/v1/readiness
platform --endpoint http://127.0.0.1:8000 doctor
```

`platform doctor` is the canonical operator diagnostic path from #38. It consumes only the
Control Plane manifest, health and readiness endpoints; deployment profiles must not add a
second backend-probing diagnostic authority.

Required persistence/configuration failures block composition/startup and are reported as
configuration failures. Optional external adapters are not required by this profile and
therefore cannot make the baseline unready merely by being absent. Later profiles that enable
optional adapters may report their degradation through the progressive #16 health model.

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
- local administrator authorization policy;
- the retry-safe canonical deployment smoke.

The ReferenceExecutor itself remains replaceable and does not become canonical lifecycle
storage.

## Shutdown

Use the service manager's normal graceful stop or `Ctrl+C` for a foreground process. Uvicorn
handles ASGI process shutdown; canonical durable state has already been committed through the
platform persistence boundaries rather than being owned by the web-server process.

## Update and backup hooks

#40 owns the tested backup/restore and hardware-relocation contract, while #41 owns platform
and schema upgrade/migration rules. Until those issues are complete, do not claim a live
snapshot or cross-version migration guarantee from this Stage-1 profile.

For a conservative same-version operator copy today:

1. stop `platform-server` cleanly;
2. preserve the deployment configuration separately;
3. copy the complete `AI_MAP_DATA_DIR` as one unit, including every SQLite database and the
   `files/`, `workspaces/` and `executor/` directories;
4. retain that copy before changing package/version state;
5. restart with the original data root and verify `/api/v1/health`, `/api/v1/readiness` and
   `platform-server smoke`.

This is an operational hook, not a substitute for the future #40 relocation/restore tests or
#41 migration guarantees.

## Uninstall and data retention

Application removal and data deletion are intentionally separate operations.

To remove the installed process/package while retaining platform data, stop the service and
remove the virtual environment or installed package plus any local launch/service definition.
Do **not** delete `AI_MAP_DATA_DIR`; it remains the retained local state for a later compatible
reinstallation.

To intentionally erase the single-node installation's persisted local state, first stop the
process and then remove the configured `AI_MAP_DATA_DIR`. That deletes the local canonical
state, authentication/authorization stores, files and workspaces contained there. Keep an
operator copy first when retention is required.

## Networking baseline

Stage 1 needs only the client/frontend-to-Control-Plane flow. SQLite and local file/workspace
storage have no network listener. The reference orchestrator and executor are in-process and
expose no private admin port.

Later profiles may add explicit internal flows for remote Workers, model endpoints, message
transport, tools, browser services and connectors. Those services must not be made public by
default simply because deployment tooling can expose a port.

## Stage 2 — single-server operational hardening

Stage 2 extends the same single-machine architecture with optional process and network
boundaries suitable for a longer-running server. It does **not** replace the Stage-1 profile
and does not introduce a second Task/Run/Worker architecture.

The recommended same-origin web composition is:

```text
browser
  |
  | HTTPS
  v
reverse proxy / static frontend server
  |-- /api/* --------------------------> 127.0.0.1:8000
  |                                      authenticated Control Plane
  `-- all other routes ----------------> static frontend + index.html fallback
```

Important properties:

- the Control Plane remains loopback-bound by default;
- only the reverse-proxy/TLS endpoint is public in this composition;
- `/api` is preserved when proxying because it is part of the canonical northbound route;
- SQLite, FileProvider and WorkspaceProvider stay local and expose no network ports;
- the frontend is a static replaceable client and may be omitted completely;
- the frontend uses its empty/default `VITE_CONTROL_PLANE_URL` for same-origin requests;
- browser `/api` traffic and SPA route fallback satisfy the accepted frontend deployment ADR;
- no proxy, service manager or static-file implementation becomes a canonical dependency.

A concrete Caddy + systemd reference implementation is under `deploy/single-server/`. It is
an operator example, not a requirement: an equivalent reverse proxy, service manager or
container/process composition may preserve the same boundaries.

### Frontend build

The optional frontend uses the Node/npm requirements declared by `frontend/package.json`:

```bash
cd frontend
npm ci
npm run build
```

Publish `frontend/dist/` through the selected static server. Do not point the browser directly
at a backend-private service or provider endpoint; every canonical operation continues to use
the Control Plane.

### Least privilege and filesystem ownership

Consume `docs/SECURE_DEPLOYMENT.md` as the baseline. For a typical service-manager deployment:

- run `platform-server` as a dedicated non-root/non-administrator service identity;
- make the application source/virtual environment and static frontend read-only to that
  identity where practical;
- grant write access only to the configured `AI_MAP_DATA_DIR` and required temporary paths;
- keep deployment configuration outside untrusted writable Workspaces;
- use a restrictive process umask for newly created state;
- do not expose the SQLite databases or local storage roots through the web server;
- keep optional model/tool/browser/connector services private unless their explicit contract
  requires a network boundary.

The reference systemd unit demonstrates these controls with `UMask=0077`,
`NoNewPrivileges=true`, filesystem protection and one explicit writable data root.

### TLS and proxy trust

TLS termination belongs to the deployment boundary. The included proxy example terminates
HTTPS before forwarding canonical `/api/*` traffic to the loopback Control Plane. The current
`platform-server` deliberately does not blindly trust forwarded proxy headers
(`proxy_headers=False`); deployment topology must not turn client-supplied forwarding headers
into an authentication/security authority.

If a future feature requires trusted proxy-derived scheme/client information, it needs an
explicit trusted-proxy configuration contract rather than enabling arbitrary forwarded-header
trust by default.

### Logs, retention and resource limits

`platform-server` emits normal process logs to stdout/stderr. A service manager/container
runtime may collect and rotate them, but deployment logs are not canonical Event history.
Apply the platform redaction rules before exporting diagnostics and keep debug verbosity off by
default.

Choose log retention and CPU/memory/open-file/process/storage limits from measured workloads,
operator recovery needs and incident-response policy. Do not encode one VPS class or hardware
SKU as the platform requirement. Resource-manager limits are operational constraints, not
canonical Node/Task capacity metadata.

### Optionality/failure behavior

Frontend, static-file server and reverse proxy are optional components. Their absence must not
prevent the Stage-1 Control Plane from starting, becoming ready or executing
`platform-server smoke`. When they are enabled, failures in the public edge may make the web
surface unreachable without changing canonical Task/Run state.

Multiple schedulable local Worker processes are intentionally not defined by this Stage-2
slice. #14 owns the shared local/remote Node/Worker registration, capability declaration,
reservation and scheduling contracts; #39 will package those contracts after they are stable.

## Resource guidance

There is intentionally no VPS SKU or fixed hardware requirement. Start with the resources
needed by the chosen local workloads and measure:

- resident memory of the Control Plane plus enabled adapters;
- SQLite/file growth;
- workspace working-set size;
- CPU concurrency and model/runtime requirements;
- optional accelerator memory only when an enabled model backend needs it.

The reference path itself is CPU-only and requires no accelerator.

## Current progressive boundary

The repository now has a production-shaped Stage-1 single-node baseline plus the Stage-2
single-server process/network hardening reference. The remaining #39 profiles consume their
own canonical dependencies rather than being invented inside deployment tooling:

- #14 + completed #35 extend the same installation model with local/remote Workers and
  capability-based placement;
- #40 adds tested backup/restore and relocation;
- #41 adds schema/platform upgrade lifecycle;
- #89 may later add an optional HA profile.

Single-node production remains a valid topology after those additions.
