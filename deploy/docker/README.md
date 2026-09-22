# Optional Docker Compose single-server profile

This directory provides a replaceable Docker Compose deployment of the maintained single-server
topology. Docker container IDs, service names, networks, image tags and host
ports are deployment metadata only; they do not become canonical platform identity.

The root `docker-compose.yml` defines two normal runtime services plus an operations-only backup helper:

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

curl --fail http://127.0.0.1:${AI_MAP_PUBLIC_PORT:-8080}/api/v1/readiness
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

The container profile deliberately keeps `AI_MAP_SECURE_COOKIE=true`. Production browser use
therefore requires HTTPS at the external edge. A VPS control panel, Traefik, Caddy, Nginx or
another trusted reverse proxy may terminate TLS and forward to the published Web port.

Direct `http://<server-ip>:8080` access is suitable for health diagnostics, but browser
authentication requires an HTTPS origin so Secure session cookies are transmitted. The Web UI
fails fast on non-loopback HTTP origins: first-user creation and sign-in forms are replaced with an
actionable **HTTPS required** message before credentials are submitted.

For a Hostinger/VPS deployment, complete the external edge before browser onboarding:

1. point a DNS name at the VPS;
2. configure the Hostinger proxy, Caddy, Nginx, Traefik or another trusted TLS edge to forward that
   HTTPS origin to the published Web port (default `8080`);
3. verify `https://<your-domain>/api/v1/health`;
4. open the same HTTPS origin for administrator bootstrap/sign-in.

Do not disable Secure cookies to make a public HTTP origin work.

For Hostinger/VPS deployments the repository provides two explicit TLS-ownership modes: keep the
existing external-edge profile when Hostinger or another reverse proxy already owns HTTPS, or use
the dedicated built-in HTTPS profile when ports 80/443 are available directly on the VPS. The
built-in profile adds a separate Caddy edge and leaves the Web and Control Plane containers private.

The internal Caddy process serves plain HTTP on port 8080 because TLS ownership belongs to the
operator's external edge in this profile. It preserves `/api` when proxying; the canonical
Control Plane route prefix is not stripped.

## Hostinger Docker Manager

Hostinger's **Compose from URL** flow expects a direct URL to a Docker Compose file rather than
only the repository root. The repository ships two standalone profiles so TLS ownership is explicit.

### Option A — existing external TLS edge

Use this profile when Hostinger's proxy, an already-running Caddy/Nginx/Traefik instance, or another
trusted edge already owns public ports 80/443 and the certificate lifecycle:

```text
https://raw.githubusercontent.com/ScoreSymphony/AI-Multi-Agent-Platform/main/deploy/docker/docker-compose.hostinger.yml
```

This profile publishes the Web service on host port `8080` by default. Route the external HTTPS
origin to that port. Set `AI_MAP_PUBLIC_PORT` only when the host-side Web port must differ.

### Option B — built-in automatic HTTPS edge

Use this profile when the VPS itself may bind public TCP ports 80 and 443 and no other service
already owns them:

```text
https://raw.githubusercontent.com/ScoreSymphony/AI-Multi-Agent-Platform/main/deploy/docker/docker-compose.hostinger-https.yml
```

Before deploying:

1. create a DNS `A` record (and an `AAAA` record only when IPv6 is intentionally configured)
   for the chosen hostname and point it at the VPS;
2. make sure inbound TCP ports 80 and 443 are reachable and not already bound by Hostinger's
   external proxy or another web server;
3. set the Docker Manager environment variable `AI_MAP_PUBLIC_DOMAIN` to the hostname only, for
   example `agents.example.com` — do not include `https://`, a path or a port;
4. deploy the Compose project.

The `https-edge` service runs pinned Caddy, obtains and renews the public certificate through its
normal ACME flow, redirects HTTP to HTTPS, and forwards the complete same-origin request to the
private `web:8080` service. The Web service then preserves `/api/*` while proxying to the private
Control Plane. Neither Web nor Control Plane has a host-published port in this profile.

Certificate and ACME state live in the named `caddy-data` and `caddy-config` volumes. Normal
container recreation keeps those volumes. Do not use `docker compose down -v` for a normal
restart because it removes those TLS volumes and the canonical `platform-data` volume.

After Caddy has obtained the certificate, verify the public origin:

```bash
curl --fail https://agents.example.com/api/v1/health
curl --fail https://agents.example.com/api/v1/readiness
```

Then open the same HTTPS origin in the browser and create/sign in to the administrator account.
If certificate issuance does not complete, first verify DNS, public reachability of ports 80/443,
and that another reverse proxy is not already occupying those ports. Do not work around issuance
failure by disabling Secure cookies.

Both Hostinger Compose files deliberately use the public Git repository itself as the Docker build
context. This keeps **Compose from URL** self-contained and avoids depending on Hostinger placing
sibling repository files next to the downloaded YAML. For CI or local validation from the
repository checkout, `AI_MAP_SOURCE_CONTEXT=../..` can replace the remote Git build context;
relative build contexts are resolved from `deploy/docker/`.

Hostinger is only an operator example. Neither profile contains Hostinger-specific API credentials,
VPS identifiers or canonical platform roles.

## Configuration

The checked-in Compose profile contains no credentials. Supported deployment overrides are kept
small intentionally:

- `AI_MAP_PUBLIC_PORT` — host-side Web port for the external-edge Hostinger/root Compose profile,
  default `8080`;
- `AI_MAP_PUBLIC_DOMAIN` — required hostname for `docker-compose.hostinger-https.yml`; the
  built-in Caddy edge uses it for automatic HTTPS;
- `AI_MAP_LOG_LEVEL` — Control Plane log level, default `info`;
- `AI_MAP_SHUTDOWN_TIMEOUT_SECONDS` — platform drain budget, default `30`, supported range
  `1–3600` seconds.

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
