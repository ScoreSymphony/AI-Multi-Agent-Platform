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
file. Use this maintained Compose URL:

```text
https://raw.githubusercontent.com/ScoreSymphony/AI-Multi-Agent-Platform/main/deploy/docker/docker-compose.hostinger.yml
```

Copy that URL into Hostinger's **Compose from URL** field. The GitHub repository page itself is the
project source, not the Compose-file URL for this flow.

### Zero-configuration HTTPS on Hostinger

The maintained default Hostinger profile owns its own HTTPS edge. Only the dedicated
`hostinger-gateway` publishes host ports 80 and 443; the Web service and Control Plane remain
private on the `platform` network.

For a normal Hostinger VPS, the default kernel hostname has the managed form
`srvNNNNNN.hstgr.cloud`. The gateway joins only the host UTS namespace with `uts: host`, reads
that hostname, validates the numeric server-id pattern, and derives:

```text
${COMPOSE_PROJECT_NAME}.srvNNNNNN.hstgr.cloud
```

Caddy then obtains and renews public HTTPS automatically. Hostinger's initial **Open** action may
start from the VPS IP/HTTP port; the gateway redirects that request to the derived canonical HTTPS
origin. No Hostinger project Environment variables are required for this default path.

The gateway does **not** receive the host network namespace, Docker socket, host filesystem mounts,
privileged mode, or any external IP/hostname discovery service. It runs read-only, drops all
capabilities, receives only `NET_BIND_SERVICE` so it can bind 80/443, and stores TLS state in
dedicated named volumes.

The normal flow is therefore:

1. paste the Compose URL above;
2. click **Deploy**;
3. wait for the three containers to become healthy/running;
4. click **Open**;
5. the HTTP/IP entry point redirects to
   `https://<project>.srvNNNNNN.hstgr.cloud`.

If you set `AI_MAP_PUBLIC_DOMAIN=agents.example.com`, that explicit DNS hostname overrides the
automatic Hostinger hostname. Point the DNS record at the VPS before redeploying so Caddy can
complete public certificate validation.

If the host hostname is not a recognized Hostinger-managed `srvNNNNNN.hstgr.cloud` value and no
explicit public domain is configured, the gateway fails closed: it serves only HTTP 503 setup
guidance and never proxies Web/API traffic.

### Advanced shared-Traefik profile

If ports 80/443 are already owned by Hostinger's shared Traefik because several public Docker
projects share one edge, do **not** deploy a second direct TLS owner. Use:

```text
https://raw.githubusercontent.com/ScoreSymphony/AI-Multi-Agent-Platform/main/deploy/docker/docker-compose.hostinger-shared-traefik.yml
```

That advanced profile preserves the explicit shared-edge contract:

```text
TRAEFIK_HOST=srv123456.hstgr.cloud
AI_MAP_TRAEFIK_NETWORK=traefik-proxy
AI_MAP_TRAEFIK_EXTERNAL=true
```

The Hostinger Traefik project must already be running and its shared external network must exist.
An optional `AI_MAP_PUBLIC_DOMAIN` still overrides the derived
`${COMPOSE_PROJECT_NAME}.${TRAEFIK_HOST}` hostname.

The `docker-compose.hostinger-https.yml` file remains a compatibility alias for the zero-config
default profile. The explicit `docker-compose.hostinger-external-edge.yml` alternative remains
available only for operators using a separately managed non-Traefik reverse proxy.

Canonical platform state remains in `platform-data`. TLS state for the default Hostinger gateway
is retained in `hostinger-gateway-data` and `hostinger-gateway-config`. Do not use
`docker compose down -v` for ordinary redeployments.

## Configuration

The checked-in default Hostinger profile contains no credentials. Supported deployment overrides
are intentionally small:

- `AI_MAP_PUBLIC_DOMAIN` — optional explicit public hostname. When absent, the zero-config
  Hostinger profile derives the application hostname from the validated VPS hostname;
- `AI_MAP_LOG_LEVEL` — Control Plane log level, default `info`;
- `AI_MAP_SHUTDOWN_TIMEOUT_SECONDS` — platform drain budget, default `30`, supported range
  `1–3600` seconds.

The following variables are only for the advanced
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
