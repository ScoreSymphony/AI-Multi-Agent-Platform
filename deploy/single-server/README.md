# Single-server reference composition

This directory contains one concrete deployment implementation for issue #39. It is an
operator example over the canonical platform contracts, not a new platform architecture.

The reference layout is:

```text
browser
  |
  | HTTPS
  v
reverse proxy / static frontend server
  |-- /api/* --------------------------> 127.0.0.1:8000
  |                                      platform-server serve
  `-- all other routes -> static Vite build + index.html fallback
```

The Control Plane remains the only canonical northbound API. SQLite and local file/workspace
persistence have no network listener. The frontend and reverse proxy are optional: removing
them returns to the Stage-1 Control-Plane-only profile without changing Task/Run state or
contracts.

## Files

- `single-server.env.example` — credential-free process configuration for the existing
  `platform-server` entry point.
- `platform-control-plane.service.example` — hardened systemd service example using a
  dedicated unprivileged account and a restricted writable data root.
- `Caddyfile.example` — optional same-origin HTTPS/static frontend/reverse-proxy example.

Caddy and systemd are replaceable deployment choices. Equivalent service managers, reverse
proxies, static servers or container compositions may implement the same boundaries.

## Host preparation

The paths below are examples, not canonical identities:

```bash
sudo useradd --system --home /var/lib/ai-multi-agent-platform \
  --shell /usr/sbin/nologin ai-map
sudo install -d -o ai-map -g ai-map -m 0700 /var/lib/ai-multi-agent-platform
sudo install -d -o root -g ai-map -m 0750 /etc/ai-multi-agent-platform
sudo install -d -o root -g root -m 0755 /opt/ai-multi-agent-platform
sudo install -d -o root -g root -m 0755 /srv/ai-multi-agent-platform/frontend
```

Clone/install the application under `/opt/ai-multi-agent-platform`, create its virtual
environment and install `.[server]` as described in `docs/DEPLOYMENT.md`. The application
source and virtual environment should not be writable by untrusted executor workloads.

Copy the example environment file outside the repository:

```bash
sudo install -o root -g ai-map -m 0640 \
  deploy/single-server/single-server.env.example \
  /etc/ai-multi-agent-platform/single-server.env
```

The example contains no secrets. If future deployment configuration needs secret references,
keep their resolved material out of source control and follow #34 rather than embedding
plaintext values in this file.

## Build the optional frontend

The current frontend requires the Node/npm versions declared in `frontend/package.json`.
Build it without `VITE_CONTROL_PLANE_URL` for the recommended same-origin profile:

```bash
cd frontend
npm ci
npm run build
sudo rsync -a --delete dist/ /srv/ai-multi-agent-platform/frontend/
```

With the empty/default frontend base URL, browser requests remain same-origin. The reverse
proxy routes `/api/*` to the Control Plane and serves the static build for all other paths.

## Install the service example

```bash
sudo install -o root -g root -m 0644 \
  deploy/single-server/platform-control-plane.service.example \
  /etc/systemd/system/ai-multi-agent-platform.service
sudo systemctl daemon-reload
sudo systemctl enable --now ai-multi-agent-platform.service
```

The service binds the Control Plane to loopback through `single-server.env`. Do not change
that to a public listener merely because a reverse proxy is present; the public boundary in
this composition is the proxy/TLS endpoint.

## Install the optional proxy example

Copy `Caddyfile.example` into the Caddy configuration managed by the host and replace
`platform.example.com` with an operator-controlled hostname. The example intentionally uses
`handle /api/*`, not `handle_path`, because `/api` is part of the canonical Control Plane
route and must not be stripped.

The proxy must preserve streaming and upgrade-capable HTTP semantics used by canonical SSE and
Terminal/WebSocket surfaces. Caddy's reverse proxy handles those protocols without exposing a
separate backend port publicly.

## Operator verification

Verify the private Control Plane locally first:

```bash
curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:8000/api/v1/readiness
platform --endpoint http://127.0.0.1:8000 doctor
platform-server smoke
```

Then verify the public same-origin endpoint through the proxy:

```bash
platform --endpoint https://platform.example.com doctor
```

A browser should load both `/` and a client-side route directly while `/api/v1/health`
continues to resolve to the canonical Control Plane.

## Logs and retention

`platform-server` writes normal process logs to stdout/stderr. In the reference systemd
composition, journald/service-manager policy owns collection, rotation and retention. Do not
create a second canonical logging store from deployment logs. Keep verbose/debug logging off
by default and apply the platform redaction rules before exporting diagnostics.

Size retention from measured request/error volume and the incident-response window rather
than a fixed VPS/server assumption. Durable canonical Event history is distinct from service
manager logs.

## Resource and failure boundaries

Set CPU, memory, open-file, process and storage limits according to measured workloads and the
host service manager. A limit failure may make the service degraded/unready, but host resource
limits do not become canonical Node/Task identity or scheduling metadata.

The Stage-1 profile remains the fallback path: if the optional frontend or proxy is absent,
the loopback/local Control Plane can still start, pass readiness and execute the canonical
reference smoke. Multiple schedulable Worker processes are intentionally not defined here;
#14 owns the shared local/remote Worker and scheduling contracts.
