# Deployment profiles and self-hosted installation

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

Hermes, LiteLLM, MCP, remote Workers, Kubernetes, cloud services and paid external
AI/API services are not required for this profile. The retired Forge runtime is not shipped by this profile. Advanced profiles add replaceable services
without changing canonical Task/Run contracts.

## Prerequisites

The reference single-node profile targets a host with:

- Python 3.12 or newer;
- Git;
- local write access for the platform data directory;
- enough storage for the operator's canonical state, files and workspaces;
- for the browser-first UI, Node.js 22.22.2+ and npm 11.6.x.

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

The `server` extra installs the HTTP server needed by `platform-server serve`. Hermes,
LiteLLM, MCP and model-provider extras remain optional and are not required by this baseline. There is no active Forge runtime extra.

## Generate and load configuration

Start from the checked-in credential-free example rather than editing a source-controlled
secret file:

```bash
cp config/single-node.env.example .env.single-node
```

Review `.env.single-node`, especially `AI_MAP_DATA_DIR`, host and port. The platform does not
silently load dotenv files: supported values enter through the process environment and then
go through the platform configuration resolver.

For a POSIX shell:

```bash
set -a
. ./.env.single-node
set +a
```

For PowerShell, set the corresponding `AI_MAP_*` environment variables in the current
process before invoking `platform-server`.

The supported reference single-node settings are:

```bash
export AI_MAP_DATA_DIR="$PWD/.data/single-node"
export AI_MAP_HOST="127.0.0.1"
export AI_MAP_PORT="8000"
export AI_MAP_SECURE_COOKIE="true"
export AI_MAP_LOG_LEVEL="info"
export AI_MAP_SHUTDOWN_TIMEOUT_SECONDS="30"
```

The deployment loader imports only explicitly supported environment variables. It does not
forward the complete process environment to the platform, and the example contains no
credentials.

`AI_MAP_SECURE_COOKIE=false` is accepted only for a loopback-only deployment. External
exposure should keep secure cookies enabled and terminate TLS at an explicitly configured
reverse proxy or equivalent trusted boundary. The maintained Web UI rejects administrator
bootstrap and sign-in on non-loopback HTTP origins before credentials are submitted, with an
actionable HTTPS-required message; direct server-IP HTTP remains a diagnostics-only path. For repository-based VPS installs, the root `docker-compose.yml` is the HTTPS-first production
default. Hostinger Docker Manager's Compose-from-URL flow uses the direct maintained Compose file
`deploy/docker/docker-compose.hostinger.yml`, not the GitHub repository landing page. That
Hostinger profile does not bind host ports 80/443. Its first import is self-contained: the
dedicated ingress gateway joins a Compose-owned isolated ingress network and therefore does not
require Hostinger's shared Traefik project/network to exist yet. Web and Control Plane remain
private on the platform network and Secure cookies remain enabled. Before browser use, deploy
Hostinger Traefik and switch the ingress network to the shared external `traefik-proxy` network
with `AI_MAP_TRAEFIK_NETWORK=traefik-proxy` and `AI_MAP_TRAEFIK_EXTERNAL=true`; Traefik then
becomes the single TLS owner and routes `websecure` traffic to the gateway on internal port 8080.
Until `AI_MAP_PUBLIC_DOMAIN` is configured, the gateway stays in fail-closed setup-pending mode
and returns only HTTP 503 guidance without proxying Web/API traffic. Operators using a separately
managed non-Traefik reverse proxy may instead use the explicitly named
`deploy/docker/docker-compose.hostinger-external-edge.yml` alternative. The Docker runbook owns
the exact Traefik-network, DNS and profile prerequisites.

## Browser-first initial setup

The normal first-run path starts the Control Plane without requiring a pre-created account:

```bash
platform-server serve
```

For a source checkout, start the Web UI in a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. On a fresh data directory the server-owned bootstrap status
routes the browser to **Create your administrator account**. The browser submits the username
and password directly to the one-time bootstrap boundary; the server creates the first local
human identity, installs the explicit administrator authorization policy, establishes the
HttpOnly browser session and continues into the persistent setup wizard.

The wizard discovers the environment, lets the administrator choose the desired setup mode and
components, previews compatibility and dependency actions before mutation, configures only the
required backend-owned fields, validates live backend state and enables the dashboard only after
readiness succeeds. Setup progress and redacted provisioning outcomes are persisted server-side,
so reloads and process restarts resume incomplete setup instead of repeating completed operations.

For a production deployment, serve the built frontend through the same-origin composition
described below instead of running the Vite development server. The first-run semantics are the
same.

### Operator/recovery account bootstrap

`platform-server bootstrap-admin` remains available when browser bootstrap cannot be used or an
operator needs the explicit recovery path. It is not a prerequisite for the normal browser-first
installation:

```bash
platform-server bootstrap-admin --username admin
```

The password is read with a hidden interactive prompt. For controlled automation, pass
`--password-stdin` and provide exactly one line on standard input. There is intentionally no
`--password` command-line option, avoiding routine leakage through shell history/process
arguments.

The operation is retry-safe for the same first username/password. If a process interruption
occurs after identity creation but before policy creation, re-running the command repairs the
missing policy instead of creating another user. After recovery bootstrap, start or restart the
Control Plane normally and sign in through the browser or CLI.

## Canonical Task/Run smoke test

After the first administrator exists, run the built-in baseline smoke:

```bash
platform-server smoke
```

This creates a small Project and executes one canonical Task/Run through the in-process
`ReferenceOrchestrator` and `ReferenceExecutor`. It requires no paid API, model endpoint,
remote Worker, MCP server, LiteLLM or Hermes. The retired Forge runtime is not part of the deployment profile.

The smoke uses stable idempotency keys. Re-running it, including after a process restart,
reuses the same canonical smoke Task/Run instead of duplicating work. Success prints the
canonical Task/Run IDs and terminal statuses.

## Start and subsequent restarts

```bash
platform-server serve
```

The default listener is `127.0.0.1:8000`. This is deliberate minimal exposure. Binding an
externally reachable address is an operator decision and should be paired with TLS/reverse
proxy policy appropriate to that environment. Existing installations retain their account and
setup state; logged-out browser users sign in normally, incomplete setup resumes, and completed
setup routes to the dashboard.

## Health and readiness

Public liveness/readiness surfaces remain available through the canonical Control Plane:

```bash
curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:8000/api/v1/readiness
platform --endpoint http://127.0.0.1:8000 doctor
```

`platform doctor` is the canonical operator diagnostic path. It stays on the public
Control Plane boundary: manifest, health and readiness remain foundational inputs, provider and
dependency diagnostics come from the canonical health payload, and composed compute profiles may
add canonical Node/Worker and host-pressure reads. Deployment profiles must not add direct
backend-private probes or a second diagnostic authority.

Required configuration failures block composition/startup. Required persistence is additionally
a live readiness dependency: the single-node persistence probe verifies required roots, an
atomic write/fsync/rename/delete round-trip, free-space reserve, required durable-store presence
and local store readability/integrity. A blocking persistence result makes `/readiness` unready
and appears in `platform doctor` without exposing absolute host paths.

See `docs/operations/PERSISTENCE_FAILURE_RECOVERY.md` for diagnostic codes, automatic cleanup,
manual-recovery boundaries and the reference free-space thresholds.

Optional external adapters are not required by this profile and therefore cannot make the
baseline unready merely by being absent. Advanced profiles that enable optional adapters may
report their degradation through the progressive platform health model.

## Persistent layout

The default data root is `.data/single-node`. Durable stores are domain-owned and the exact
inventory evolves with the composed product surface. Representative core entries include:

```text
.data/single-node/
├── db/
│   ├── kernel.sqlite3
│   ├── scopes.sqlite3
│   ├── authentication.sqlite3
│   ├── authorization.sqlite3
│   ├── files.sqlite3
│   ├── workspaces.sqlite3
│   └── ... additional domain-owned durable stores
├── files/
├── workspaces/
└── executor/
    └── reference/
```

Do not use this illustrative tree as the backup manifest. The authoritative required/optional
durable-store inventory is owned by the backup inventory contract documented in
[`BACKUP_RESTORE.md`](BACKUP_RESTORE.md); physical persistence ownership is described in
[`../runtime/PERSISTENCE_TOPOLOGY.md`](../runtime/PERSISTENCE_TOPOLOGY.md).

These paths are implementation configuration, not canonical resource IDs. Moving the data
through the supported backup/restore path must not require preserving a hostname, machine ID or
filesystem path as canonical identity.

Authentication SQLite stores password/token verifiers and safe metadata only. Raw passwords,
browser-session secrets and bearer-token secrets are not persisted.

## Restart

A clean restart uses the same `AI_MAP_DATA_DIR` and runs the same command:

```bash
platform-server serve
```

Before serving after a restart, local File/Workspace providers also reconcile only storage state
whose ownership can be proven: `PENDING` File writes are tombstoned and owned stale Workspace
materializations are removed. Cleanup failure is fail-closed; unknown temp paths are preserved.

The single-node regression suite verifies restart persistence for:

- canonical Task/Run state;
- Project identity and idempotency state;
- local user accounts;
- browser sessions;
- API credentials including credential-local scope metadata;
- local administrator authorization policy;
- the retry-safe canonical deployment smoke.

The `single-node-install-smoke` CI job additionally installs only `.[server]` into a fresh
virtual environment, starts the real `platform-server` HTTP process, verifies canonical
readiness, performs a graceful foreground shutdown, restarts against the same data root and
verifies the retry-safe canonical smoke again. This is the installation/process-lifecycle
proof for the documented clean-install and restart lifecycle.

The ReferenceExecutor itself remains replaceable and does not become canonical lifecycle
storage.

## Shutdown

Use the service manager's normal graceful stop or `Ctrl+C` for a foreground process. The
single-node server first enters the process-local drain gate, rejects new authoritative
mutations, reports `draining`/not-ready, and gives already-admitted work only the configured
`AI_MAP_SHUTDOWN_TIMEOUT_SECONDS` budget to settle. Uvicorn connection shutdown and ASGI
lifespan/resource teardown share that bound.

If the bound expires, process-local teardown is forced without inventing canonical terminal
Task/Run/Step outcomes. The next process rebuilds from the same durable data root and the
startup reconciliation remains the sole ordinary restart authority.

See [Single-node graceful drain and shutdown recovery](SINGLE_NODE_DRAIN_SHUTDOWN.md) for the
in-flight disposition matrix, forced-stop behavior, telemetry and operator recovery procedure.

## Backup, restore, and upgrades

The supported single-node backup/restore path is implemented by
`platform-backup`; the maintained operator runbook is
[Backup, restore, and disaster recovery](BACKUP_RESTORE.md). Backup format v1 is deliberately
offline/quiesced: stop every process that can write the deployment data root before creating a
backup, pass `--quiesced`, and verify the completed backup before depending on it.

A minimal operator sequence is:

```bash
platform-backup create \
  --data-dir /srv/ai-map/data \
  --destination /srv/backups/ai-map \
  --quiesced
platform-backup verify /srv/backups/ai-map
platform-backup restore /srv/backups/ai-map \
  --target-data-dir /srv/ai-map-restored/data
AI_MAP_DATA_DIR=/srv/ai-map-restored/data platform-server recover-restore
```

Restore publishes only after manifest, checksum, SQLite-integrity and durable-layout validation,
then leaves the restored deployment behind the recovery/readiness gate until canonical recovery
and cross-store validation succeed. Follow the runbook for build-commit pinning, secret-provider
recovery, relocation, post-restore validation and failure handling. This profile does **not** claim
a live cross-provider snapshot while writers are running.

Platform/schema upgrades use the supported `platform-upgrade` lifecycle documented in
[Platform upgrades and migrations](UPGRADES.md). For a real release transition, preflight the
source/target version vectors, create and verify the required source-release backup, quiesce/drain
writers, apply the supported migration path, and validate readiness plus the canonical smoke before
resuming work. Cross-version behavior is guaranteed only for explicitly supported migration paths;
do not infer arbitrary downgrade or cross-version compatibility from the deployment profile alone.

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

The reference single-node profile needs only the client/frontend-to-Control-Plane flow. SQLite and local file/workspace
storage have no network listener. The reference orchestrator and executor are in-process and
expose no private admin port.

Advanced profiles may add explicit internal flows for remote Workers, model endpoints, message
transport, tools, browser services and connectors. Those services must not be made public by
default simply because deployment tooling can expose a port. The
[advanced deployment guide](ADVANCED_DEPLOYMENT.md) owns distributed packaging and heterogeneous-device networking examples.

## Single-server operational hardening

The hardened single-server profile extends the same single-machine architecture with optional process and network
boundaries suitable for a longer-running server. It does **not** replace the reference single-node profile
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

Consume [Secure Deployment Baseline](../security/SECURE_DEPLOYMENT.md) as the baseline. For a typical service-manager deployment:

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
prevent the reference single-node Control Plane from starting, becoming ready or executing
`platform-server smoke`. When they are enabled, failures in the public edge may make the web
surface unreachable without changing canonical Task/Run state.

Multiple schedulable local and remote Workers are not required by the hardened single-server profile.
The canonical Node/Worker contracts provide shared registration, capability declaration,
reservation and scheduling semantics, while the [advanced deployment guide](ADVANCED_DEPLOYMENT.md)
packages those contracts into distributed and heterogeneous deployment profiles. Operators may adopt those advanced profiles
without changing the single-server baseline documented here.

## Resource guidance

There is intentionally no VPS SKU or fixed hardware requirement. Start with the resources
needed by the chosen local workloads and measure:

- resident memory of the Control Plane plus enabled adapters;
- SQLite/file growth;
- workspace working-set size;
- CPU concurrency and model/runtime requirements;
- optional accelerator memory only when an enabled model backend needs it.

The reference path itself is CPU-only and requires no accelerator.

## Advanced deployment integrations

The repository has a production-shaped reference single-node baseline plus a hardened
single-server process/network profile. The following advanced operational foundations
are available without becoming prerequisites for the baseline:

- canonical Node/Worker registry, capability, reservation and scheduling contracts, with
  multiple-local-Worker, distributed-Worker and heterogeneous multi-device profiles documented in
  [Advanced distributed and heterogeneous deployment](ADVANCED_DEPLOYMENT.md);
- tested offline/quiesced backup, verified restore and hardware-relocation behavior through
  `platform-backup` and [the backup/restore runbook](BACKUP_RESTORE.md);
- explicit platform/schema upgrade and migration lifecycle through `platform-upgrade` and
  [the upgrade runbook](UPGRADES.md);
- optional Control Plane HA/fencing/promotion semantics documented in
  [Control Plane high availability](CONTROL_PLANE_HIGH_AVAILABILITY.md).

HA remains **Optional / Advanced** and is not a dependency of the ordinary single-node or
single-server topology. A production-shaped independent multi-process/multi-host Control Plane
profile remains outside the ordinary baseline and must be claimed only with its own compatibility
and conformance evidence.

Single-node/single-server production remains a valid topology independently of those optional HA
extensions. Formal release publication/version finalization remains a separate downstream release
operation rather than a prerequisite introduced by this deployment guide.


## Repository-root Docker deployment contract

The repository-root `docker-compose.yml` is intentionally production-oriented and HTTPS-first.
This matters for Docker managers that accept a Git repository URL and automatically consume the
root Compose file. It requires `AI_MAP_PUBLIC_DOMAIN`, publishes only the Caddy edge on ports
80/443, keeps Web/Control Plane private, and retains the operations backup service plus canonical
`platform-data`.

Use `docker-compose.local.yml` only for loopback development/CI where host port 8080 is
intentional. A missing production domain must fail composition rather than silently changing the
security boundary.
