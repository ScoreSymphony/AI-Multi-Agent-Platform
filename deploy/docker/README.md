# Docker Compose single-server profile

Issue: #1378

This directory implements the existing #39 single-server topology as a replaceable Docker
Compose deployment choice. Docker container IDs, service names, networks, image tags and host
ports are deployment metadata only; they do not become canonical platform identity.

The root `docker-compose.yml` builds two services:

```text
browser / external TLS edge
          |
          v
      web :8080
      |       |
      |       +-- static Vite build
      |
      `-- /api/* --> control-plane:8000
                         |
                         +-- canonical platform state
                         `-- /var/lib/ai-multi-agent-platform
                                |
                                `-- named volume: platform-data
```

Only the Web edge is published to the host. The Control Plane uses Docker-network-only
`expose` and is not mapped to a host port. SQLite, File and Workspace persistence remain
private implementation storage.

## Start with Docker Compose

From the repository root:

```bash
docker compose -f docker-compose.yml build
docker compose -f docker-compose.yml up -d
```

The default host mapping is `8080 -> web:8080`. Override only the host-side port when needed:

```bash
AI_MAP_PUBLIC_PORT=18080 docker compose -f docker-compose.yml up -d
```

Check the same-origin public surface:

```bash
curl http://127.0.0.1:8080/api/v1/health
curl http://127.0.0.1:8080/api/v1/readiness
```

The Control Plane itself is intentionally not reachable through a separate host port.

## Persistent data

Canonical local state is stored under
`/var/lib/ai-multi-agent-platform` inside the Control Plane container and mounted from the
named `platform-data` volume. Normal container recreation therefore keeps platform state:

```bash
docker compose -f docker-compose.yml down
docker compose -f docker-compose.yml up -d
```

Do not use `docker compose down -v` for a normal restart. The `-v` option deletes the named
volume and therefore deletes the local deployment data.

Backup, restore and upgrade procedures remain the canonical procedures documented under
`docs/operations/`; containerization does not introduce a second lifecycle authority.

## HTTPS and browser authentication

The container profile deliberately keeps `AI_MAP_SECURE_COOKIE=true`. Production browser use
therefore requires HTTPS at the external edge. A VPS control panel, Traefik, Caddy, Nginx or
another trusted reverse proxy may terminate TLS and forward to the published Web port.

Direct `http://<server-ip>:8080` access is suitable for health diagnostics, but browser
authentication should use an HTTPS origin so Secure session cookies are transmitted.

The internal Caddy process serves plain HTTP on port 8080 because TLS ownership belongs to the
operator's external edge in this profile. It preserves `/api` when proxying; the canonical
Control Plane route prefix is not stripped.

## Hostinger Docker Manager

Hostinger's **Compose from URL** flow expects a direct URL to a Docker Compose file rather than
only the repository root. Use the dedicated standalone URL profile:

```text
https://raw.githubusercontent.com/ScoreSymphony/AI-Multi-Agent-Platform/main/deploy/docker/docker-compose.hostinger.yml
```

That file deliberately uses the public Git repository itself as the Docker build context. This
means the deployment does not depend on Hostinger placing sibling repository files next to the
downloaded Compose YAML. The ordinary root `docker-compose.yml` continues to use the local
checkout as its build context.

Choose any project name, deploy the composition, and route an HTTPS domain/reverse proxy to the
published Web port. The default host port is 8080; set `AI_MAP_PUBLIC_PORT` in the Docker
Manager environment if that port is already occupied.

For CI or local validation from the repository checkout, `AI_MAP_SOURCE_CONTEXT=../..` can replace
the remote Git build context. Relative build contexts are resolved from `deploy/docker/`, where
the Hostinger Compose file lives.

Hostinger is only an operator example. The Compose file contains no Hostinger-specific API,
metadata, hostname, VPS class or canonical role.

## Configuration

The checked-in Compose profile contains no credentials. Supported deployment overrides are kept
small intentionally:

- `AI_MAP_PUBLIC_PORT` — host-side Web port, default `8080`;
- `AI_MAP_LOG_LEVEL` — Control Plane log level, default `info`;
- `AI_MAP_SHUTDOWN_TIMEOUT_SECONDS` — platform drain budget, default `30`.

The container-internal data directory, Control Plane port and secure-cookie setting are fixed by
the reference composition because changing them is not required for ordinary operator use.

Secrets for optional providers or integrations must continue to use the platform's canonical
configuration/SecretReference boundaries rather than being committed to the Compose file.

## Lifecycle and diagnostics

Show status and logs:

```bash
docker compose -f docker-compose.yml ps
docker compose -f docker-compose.yml logs --tail=200 control-plane
docker compose -f docker-compose.yml logs --tail=200 web
```

Stop gracefully:

```bash
docker compose -f docker-compose.yml stop
```

The Control Plane gets a 40-second Compose stop grace period around the default 30-second
platform shutdown/drain budget.

Remove containers and the private network while retaining canonical state:

```bash
docker compose -f docker-compose.yml down
```

The existing systemd/venv single-server profile under `deploy/single-server/` remains fully
supported. Docker Compose extends that deployment surface; it does not replace it.
