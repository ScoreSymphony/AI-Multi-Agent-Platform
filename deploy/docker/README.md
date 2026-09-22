# Optional Docker Compose single-server profile

This directory provides a replaceable Docker Compose deployment of the maintained single-server
topology. Docker container IDs, service names, networks, image tags and host
ports are deployment metadata only; they do not become canonical platform identity.

The repository-root `docker-compose.yml` is the secure production/self-hosting default. It
defines the Control Plane, Web service, repository-owned Caddy HTTPS edge, and an operations-only
backup helper:

```text
browser
  |
  | HTTPS :443
  v
https-edge / Caddy
  |
  v
web :8080 (private)
  |
  `-- /api/* --> control-plane:8000 (private)
                         |
                         +-- canonical platform state
                         `-- /var/lib/ai-multi-agent-platform
                                |
                                `-- named volume: platform-data
```

Only ports 80/443 on the HTTPS edge are published to the host. Web and Control Plane use
Docker-network-only `expose` and are not mapped to public host ports.

## Start with Docker Compose

For a production/self-hosted deployment, point a DNS hostname at the server, set the hostname, and
start the root profile:

```bash
export AI_MAP_PUBLIC_DOMAIN=agents.example.com
docker compose -f docker-compose.yml build
docker compose -f docker-compose.yml up -d
```

The root profile deliberately fails before deployment when `AI_MAP_PUBLIC_DOMAIN` is absent. It
does not fall back to a public HTTP `:8080` UI.

For local development or CI where loopback HTTP is intentional, use the explicit local profile:

```bash
docker compose -f docker-compose.local.yml build
docker compose -f docker-compose.local.yml up -d
curl http://127.0.0.1:8080/api/v1/health
curl http://127.0.0.1:8080/api/v1/readiness
```

The local profile preserves the previous `AI_MAP_PUBLIC_PORT` override. It is not the production
or Hostinger default.

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

Container restart persistence is not a backup. The generic local Compose profile therefore ships
an explicit operations service plus `deploy/docker/docker-compose.recovery.yml` for the canonical
quiesced backup -> clean replacement-volume restore -> recovery/readiness sequence.

### Canonical backup

Choose a durable host directory that is outside the named platform-data volume and writable by the
container runtime user (UID/GID `10001` in the reference image):

```bash
export AI_MAP_BACKUP_DIR="$PWD/backups"
mkdir -p "$AI_MAP_BACKUP_DIR"
# Linux example when the directory is not already writable by UID/GID 10001:
sudo chown 10001:10001 "$AI_MAP_BACKUP_DIR"

export AI_MAP_BUILD_COMMIT="$(git rev-parse HEAD)"
export AI_MAP_BACKUP_NAME="ai-map-$(date -u +%Y%m%dT%H%M%SZ)"

# Quiesce every writer before invoking the offline backup service.
docker compose -f docker-compose.yml stop

docker compose -f docker-compose.yml --profile operations run --rm --no-deps --build backup \
  create \
  --data-dir /var/lib/ai-multi-agent-platform \
  --destination "/backups/$AI_MAP_BACKUP_NAME" \
  --quiesced \
  --platform-commit "$AI_MAP_BUILD_COMMIT"

docker compose -f docker-compose.yml --profile operations run --rm --no-deps --build backup \
  verify "/backups/$AI_MAP_BACKUP_NAME"
```

The backup helper keeps its container root filesystem read-only and has no network access. Its
quiesced data volume retains normal volume filesystem semantics because the canonical SQLite backup
API may need WAL/SHM sidecar access even though the database connection itself is opened read-only.
The helper writes the verified backup only to the operator-controlled backup bind mount, so the
manifest/payload leaves the platform-data volume. Do not restart the Control Plane until backup
creation has completed.

### Canonical restore to a replacement named volume

Restoration deliberately does **not** copy files back into the active mounted data root. Instead,
create a new named volume and select it through the recovery override. The override mounts the full
replacement volume only into the offline restore service, which lets `platform-backup restore`
atomically publish a new `restored-data` directory inside that otherwise clean volume. Runtime
services then mount only that restored subdirectory at the unchanged canonical data path.

```bash
docker compose -f docker-compose.yml down

export AI_MAP_DATA_VOLUME="ai-map-restored-$(date -u +%Y%m%dT%H%M%SZ)"

# Fail closed instead of silently reusing a pre-existing volume name.
if docker volume inspect "$AI_MAP_DATA_VOLUME" >/dev/null 2>&1; then
  echo "replacement volume already exists: $AI_MAP_DATA_VOLUME" >&2
  exit 1
fi
docker volume create "$AI_MAP_DATA_VOLUME"

docker compose \
  -f docker-compose.yml \
  -f deploy/docker/docker-compose.recovery.yml \
  --profile operations run --rm --no-deps --build restore \
  restore "/backups/$AI_MAP_BACKUP_NAME" \
  --target-data-dir /var/lib/ai-multi-agent-platform/restored-data \
  --expected-platform-commit "$AI_MAP_BUILD_COMMIT"

docker compose \
  -f docker-compose.yml \
  -f deploy/docker/docker-compose.recovery.yml \
  --profile operations run --rm --no-deps --build recover-restore

docker compose \
  -f docker-compose.yml \
  -f deploy/docker/docker-compose.recovery.yml \
  up -d

curl --fail https://${AI_MAP_PUBLIC_DOMAIN}/api/v1/readiness
```

A second restore to the same selected replacement volume fails because
`/var/lib/ai-multi-agent-platform/restored-data` already exists. This preserves the canonical
clean-target contract and the sibling `.restore-partial` atomic-publication semantics. The
`recover-restore` operation uses the same post-restore reconciliation/integrity/readiness gate as
normal `platform-server serve`; normal startup remains blocked if that gate is not ready.

Keep using both Compose files, together with the selected `AI_MAP_DATA_VOLUME`, for the recovered
deployment. That same pair also makes the `backup` operations service read the restored subvolume
rather than the replacement volume root. Docker volume names and the `restored-data` transport
layout remain deployment metadata; canonical Task, Run, User and other resource IDs are unchanged.

The authoritative format, manifest/checksum, compatibility and recovery contracts remain in
[`docs/operations/BACKUP_RESTORE.md`](../../docs/operations/BACKUP_RESTORE.md). Upgrades continue
to use the canonical procedure under `docs/operations/`; containerization does not create a
second lifecycle authority.

## HTTPS and browser authentication

The root production profile deliberately keeps `AI_MAP_SECURE_COOKIE=true` and includes its own
Caddy TLS edge. Production browser authentication therefore enters through the configured HTTPS
hostname; Web and Control Plane remain private Compose services.

Direct public `http://<server-ip>:8080` is not part of the root production topology. The explicit
`docker-compose.local.yml` profile retains loopback HTTP for local development and CI only. The
Web UI still fails fast on insecure non-loopback HTTP origins if an operator chooses an alternate
external-edge topology incorrectly.

For the normal production path:

1. point a DNS hostname at the VPS;
2. set `AI_MAP_PUBLIC_DOMAIN` to that hostname;
3. ensure ports 80 and 443 are reachable and not already owned by another edge;
4. deploy the repository-root `docker-compose.yml`;
5. open `https://<your-domain>` for administrator bootstrap/sign-in.

Do not disable Secure cookies to make public HTTP work.

## Hostinger Docker Manager

Hostinger Docker Manager's **Compose from URL** flow expects the direct URL of a Docker Compose
file. For a **new installation**, use this maintained zero-configuration Compose URL:

```text
https://raw.githubusercontent.com/ScoreSymphony/AI-Multi-Agent-Platform/main/deploy/docker/docker-compose.hostinger-zero-config.yml
```

Copy that URL into Hostinger's **Compose from URL** field. The GitHub repository page itself is the
project source, not the Compose-file URL for this flow.

The historical `docker-compose.hostinger.yml` and `docker-compose.hostinger-https.yml` URLs are
kept migration-safe for existing deployments that already follow the shared-Traefik-compatible
topology. They intentionally do not start binding host ports 80/443 during an ordinary redeploy.

### Zero-configuration HTTPS on Hostinger

The maintained zero-config profile is designed for the currently observed Hostinger
multi-project topology: Hostinger's Traefik project already owns host ports 80 and 443 and runs
with Docker `host` networking. In that topology there is no external `traefik-proxy` network.
The platform does **not** compete for 80/443 and does not require any external Docker network.

The dedicated `hostinger-gateway` stays on the private platform bridge only. Host-network
Traefik can reach that bridge address directly and discovers the gateway through Docker provider
labels. The gateway joins only the host UTS namespace with `uts: host`. It reads the VPS kernel
hostname and, when it matches Hostinger's managed `srvNNNNNN.hstgr.cloud` form, derives:

```text
${COMPOSE_PROJECT_NAME}.srvNNNNNN.hstgr.cloud
```

No `TRAEFIK_HOST`, `AI_MAP_TRAEFIK_NETWORK`, or `AI_MAP_TRAEFIK_EXTERNAL` value is required.
The Compose labels use project-scoped `HostRegexp` and `HostSNIRegexp` rules:

- HTTP on Traefik's `web` entrypoint is forwarded to Caddy port 80 so ACME HTTP-01 and redirects
  work;
- HTTPS on Traefik's `websecure` entrypoint uses **TLS passthrough** only for the platform's
  `<project>.srv<digits>.hstgr.cloud` SNI pattern, so Caddy terminates TLS for the exact derived
  hostname;
- there is no catch-all TLS router, so unrelated HTTPS projects remain untouched.

The gateway also publishes a bootstrap HTTP mapping on
`${AI_MAP_HOSTINGER_BOOTSTRAP_PORT:-18080}:8080`. That listener performs **only** a redirect to
the canonical HTTPS origin and never proxies Web/API traffic. This gives Hostinger an ordinary
published port for initial access while Traefik remains the sole owner of 80/443.

The normal flow is therefore:

1. deploy Hostinger's Traefik project if it is not already running;
2. paste the zero-config Compose URL above;
3. click **Deploy**;
4. wait for the three platform containers to become healthy/running;
5. open the published bootstrap access or Hostinger's **Open** action;
6. the request redirects to `https://<project>.srvNNNNNN.hstgr.cloud`.

The gateway does **not** receive the host network namespace, Docker socket, host filesystem mounts,
privileged mode, or an external IP/hostname discovery service. It runs read-only, drops all
capabilities and receives only `NET_BIND_SERVICE` so Caddy can listen on internal ports 80/443.
TLS state is stored in dedicated named volumes.

If `AI_MAP_PUBLIC_DOMAIN=agents.example.com` is set, that explicit hostname overrides the
automatic Hostinger hostname. Point DNS at the VPS before redeploying so Caddy can complete public
certificate validation.

If the host hostname is not a recognized Hostinger-managed `srvNNNNNN.hstgr.cloud` value and no
explicit public domain is configured, the gateway remains fail-closed and serves only setup
guidance.

### Direct Hostinger edge without Traefik

If Hostinger Traefik is **not** running and host ports 80/443 are free, use:

```text
https://raw.githubusercontent.com/ScoreSymphony/AI-Multi-Agent-Platform/main/deploy/docker/docker-compose.hostinger-direct.yml
```

That profile preserves the direct Caddy edge: the gateway itself publishes 80/443, derives the
same managed Hostinger hostname through `uts: host`, and owns certificate issuance directly.

### Explicit shared-Traefik profile

For Hostinger installations that actually provide the documented external `traefik-proxy`
network, the older environment-driven shared-edge variant remains available at:

```text
https://raw.githubusercontent.com/ScoreSymphony/AI-Multi-Agent-Platform/main/deploy/docker/docker-compose.hostinger-shared-traefik.yml
```

That advanced profile keeps the explicit contract:

```text
TRAEFIK_HOST=srv123456.hstgr.cloud
AI_MAP_TRAEFIK_NETWORK=traefik-proxy
AI_MAP_TRAEFIK_EXTERNAL=true
```

The historical `docker-compose.hostinger.yml` and `docker-compose.hostinger-https.yml` files
remain migration-safe compatibility entry points. The explicit
`docker-compose.hostinger-external-edge.yml` alternative remains available for operators using a
separately managed non-Traefik reverse proxy.

Canonical platform state remains in `platform-data`. TLS state for zero-config/direct Caddy is
retained in `hostinger-gateway-data` and `hostinger-gateway-config`. Do not use
`docker compose down -v` for ordinary redeployments.

## Configuration

The checked-in zero-configuration Hostinger profile contains no credentials. Supported deployment
overrides are intentionally small:

- `AI_MAP_PUBLIC_DOMAIN` — optional explicit public hostname. When absent, the zero-config
  Hostinger profile derives the application hostname from the validated VPS hostname;
- `AI_MAP_LOG_LEVEL` — Control Plane log level, default `info`;
- `AI_MAP_SHUTDOWN_TIMEOUT_SECONDS` — platform drain budget, default `30`, supported range
  `1–3600` seconds.

The zero-config profile additionally supports
`AI_MAP_HOSTINGER_BOOTSTRAP_PORT` to override the redirect-only bootstrap port (default
`18080`).

The following variables are only for the explicit
`docker-compose.hostinger-shared-traefik.yml` profile:

- `TRAEFIK_HOST` — Hostinger VPS hostname, for example `srv123456.hstgr.cloud`;
- `AI_MAP_TRAEFIK_NETWORK` — shared Traefik network name, normally `traefik-proxy`;
- `AI_MAP_TRAEFIK_EXTERNAL` — set to `true` for the existing shared Docker network.

`AI_MAP_PUBLIC_PORT` remains specific to
`docker-compose.hostinger-external-edge.yml`, defaulting to `8080`.

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

The Control Plane gets a 3610-second Compose hard-stop grace period. This is deliberately
strictly greater than the platform's maximum supported 3600-second shutdown/drain budget, with
10 seconds of container-level overhead. The application drain budget remains the lifecycle
authority: with the default 30-second budget, the Control Plane normally exits on its own within
that bound rather than waiting for the outer Compose grace period. Any supported
`AI_MAP_SHUTDOWN_TIMEOUT_SECONDS` value therefore remains shorter than Compose's hard-kill
deadline.

Remove containers and the private network while retaining canonical state:

```bash
docker compose -f docker-compose.yml down
```

The existing systemd/venv single-server profile under `deploy/single-server/` remains fully
supported. Docker Compose extends that deployment surface; it does not replace it.
