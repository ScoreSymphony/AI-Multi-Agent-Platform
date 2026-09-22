from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from ai_multi_agent_platform.deployment.config import (
    DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    MAX_SHUTDOWN_TIMEOUT_SECONDS,
)

COMPOSE = Path("docker-compose.yml")
LOCAL_COMPOSE = Path("docker-compose.local.yml")
DOCKER_DIR = Path("deploy/docker")
HOSTINGER_COMPOSE = DOCKER_DIR / "docker-compose.hostinger.yml"
HOSTINGER_HTTPS_COMPOSE = DOCKER_DIR / "docker-compose.hostinger-https.yml"
HOSTINGER_ZERO_CONFIG_COMPOSE = DOCKER_DIR / "docker-compose.hostinger-zero-config.yml"
HOSTINGER_CUSTOM_DOMAIN_COMPOSE = DOCKER_DIR / "docker-compose.hostinger-custom-domain.yml"
HOSTINGER_DIRECT_COMPOSE = DOCKER_DIR / "docker-compose.hostinger-direct.yml"
HOSTINGER_SHARED_TRAEFIK_COMPOSE = DOCKER_DIR / "docker-compose.hostinger-shared-traefik.yml"
HOSTINGER_EXTERNAL_EDGE_COMPOSE = DOCKER_DIR / "docker-compose.hostinger-external-edge.yml"
HOSTINGER_GATEWAY_DOCKERFILE = DOCKER_DIR / "hostinger-gateway.Dockerfile"
HOSTINGER_GATEWAY_CADDY = DOCKER_DIR / "Caddyfile.hostinger-gateway"
HOSTINGER_GATEWAY_PENDING_CADDY = DOCKER_DIR / "Caddyfile.hostinger-setup-pending"
HOSTINGER_DIRECT_CADDY = DOCKER_DIR / "Caddyfile.hostinger-direct"
HOSTINGER_DIRECT_PENDING_CADDY = DOCKER_DIR / "Caddyfile.hostinger-direct-setup-pending"
HOSTINGER_GATEWAY_ENTRYPOINT = DOCKER_DIR / "hostinger-gateway-entrypoint.sh"
RECOVERY_COMPOSE = DOCKER_DIR / "docker-compose.recovery.yml"

_STOP_GRACE_RE = re.compile(r"^\s*stop_grace_period:\s*(\d+)s\s*$", re.MULTILINE)
_SHUTDOWN_DEFAULT_RE = re.compile(
    r"AI_MAP_SHUTDOWN_TIMEOUT_SECONDS:\s*"
    r"\$\{AI_MAP_SHUTDOWN_TIMEOUT_SECONDS:-(\d+)\}"
)


def _control_plane_block(compose: str) -> str:
    if "\n\n  backup:" in compose:
        return compose.split("\n\n  backup:", 1)[0]
    return compose.split("\n  web:", 1)[0]


def _shutdown_contract(compose: str) -> tuple[int, int]:
    control_plane = _control_plane_block(compose)
    grace_match = _STOP_GRACE_RE.search(control_plane)
    timeout_match = _SHUTDOWN_DEFAULT_RE.search(control_plane)
    assert grace_match is not None
    assert timeout_match is not None
    return int(timeout_match.group(1)), int(grace_match.group(1))


def test_compose_keeps_control_plane_private_and_state_durable() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")

    assert "dockerfile: deploy/docker/control-plane.Dockerfile" in compose
    assert "dockerfile: deploy/docker/web.Dockerfile" in compose
    assert "dockerfile: deploy/docker/https-edge.Dockerfile" in compose
    assert "AI_MAP_HOST: 0.0.0.0" in compose
    assert 'AI_MAP_SECURE_COOKIE: "true"' in compose
    assert "platform-data:/var/lib/ai-multi-agent-platform" in compose
    assert '      - "8000"' in compose
    assert (
        "AI_MAP_PUBLIC_DOMAIN: "
        "${AI_MAP_PUBLIC_DOMAIN:?set AI_MAP_PUBLIC_DOMAIN to the public DNS hostname}" in compose
    )

    control_plane = _control_plane_block(compose)
    web = compose.split("\n  web:", 1)[1].split("\n  https-edge:", 1)[0]
    edge = compose.split("\n  https-edge:", 1)[1].split("\nvolumes:", 1)[0]

    assert "ports:" not in control_plane
    assert "ports:" not in web
    assert "expose:" in control_plane
    assert "no-new-privileges:true" in control_plane
    assert "cap_drop:" in control_plane
    assert '      - "80:80"' in edge
    assert '      - "443:443"' in edge
    assert "caddy-data:/data" in edge
    assert "caddy-config:/config" in edge


def test_local_compose_preserves_loopback_http_workflow_without_public_tls_edge() -> None:
    compose = LOCAL_COMPOSE.read_text(encoding="utf-8")

    assert "dockerfile: deploy/docker/control-plane.Dockerfile" in compose
    assert "dockerfile: deploy/docker/web.Dockerfile" in compose
    assert "dockerfile: deploy/docker/https-edge.Dockerfile" not in compose
    assert 'AI_MAP_SECURE_COOKIE: "true"' in compose
    assert '"${AI_MAP_PUBLIC_PORT:-8080}:8080"' in compose
    assert "AI_MAP_PUBLIC_DOMAIN" not in compose
    assert "  backup:" in compose
    assert "platform-data:/var/lib/ai-multi-agent-platform" in compose

    control_plane = _control_plane_block(compose)
    assert "ports:" not in control_plane


def test_compose_stop_grace_exceeds_every_supported_shutdown_budget() -> None:
    root_compose = COMPOSE.read_text(encoding="utf-8")
    local_compose = LOCAL_COMPOSE.read_text(encoding="utf-8")
    hostinger_compose = HOSTINGER_COMPOSE.read_text(encoding="utf-8")
    hostinger_https_compose = HOSTINGER_HTTPS_COMPOSE.read_text(encoding="utf-8")
    hostinger_external_compose = HOSTINGER_EXTERNAL_EDGE_COMPOSE.read_text(encoding="utf-8")

    root_default, root_grace = _shutdown_contract(root_compose)
    local_default, local_grace = _shutdown_contract(local_compose)
    hostinger_default, hostinger_grace = _shutdown_contract(hostinger_compose)
    hostinger_https_default, hostinger_https_grace = _shutdown_contract(hostinger_https_compose)
    hostinger_external_default, hostinger_external_grace = _shutdown_contract(
        hostinger_external_compose
    )

    assert root_default == DEFAULT_SHUTDOWN_TIMEOUT_SECONDS
    assert local_default == DEFAULT_SHUTDOWN_TIMEOUT_SECONDS
    assert hostinger_default == DEFAULT_SHUTDOWN_TIMEOUT_SECONDS
    assert hostinger_https_default == DEFAULT_SHUTDOWN_TIMEOUT_SECONDS
    assert hostinger_external_default == DEFAULT_SHUTDOWN_TIMEOUT_SECONDS
    assert root_grace > root_default
    assert local_grace > local_default
    assert hostinger_grace > hostinger_default
    assert hostinger_https_grace > hostinger_https_default
    assert hostinger_external_grace > hostinger_external_default
    assert root_grace > MAX_SHUTDOWN_TIMEOUT_SECONDS
    assert local_grace > MAX_SHUTDOWN_TIMEOUT_SECONDS
    assert hostinger_grace > MAX_SHUTDOWN_TIMEOUT_SECONDS
    assert hostinger_https_grace > MAX_SHUTDOWN_TIMEOUT_SECONDS
    assert hostinger_external_grace > MAX_SHUTDOWN_TIMEOUT_SECONDS
    assert (
        root_grace
        == local_grace
        == hostinger_grace
        == hostinger_https_grace
        == hostinger_external_grace
    )


def test_compose_backup_service_exports_quiesced_backup_outside_data_volume() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")

    assert "  backup:" in compose
    assert "profiles:\n      - operations" in compose
    assert "entrypoint:\n      - platform-backup" in compose
    assert "source: platform-data" in compose
    assert "target: /var/lib/ai-multi-agent-platform" in compose

    backup_service = compose.split("\n\n  backup:", 1)[1].split("\n  web:", 1)[0]
    data_mount = backup_service.split("      - type: volume", 1)[1].split("      - type: bind", 1)[
        0
    ]
    assert "read_only:" not in data_mount
    assert "read_only: true" in backup_service
    assert "source: ${AI_MAP_BACKUP_DIR:-./backups}" in compose
    assert "target: /backups" in compose
    assert "create_host_path: false" in compose
    assert "network_mode: none" in compose


def test_recovery_override_restores_into_clean_volume_subpath() -> None:
    recovery = RECOVERY_COMPOSE.read_text(encoding="utf-8")

    assert "subpath: restored-data" in recovery
    assert "external: true" in recovery
    assert (
        "name: ${AI_MAP_DATA_VOLUME:?set AI_MAP_DATA_VOLUME to the replacement volume name}"
        in recovery
    )
    assert "  restore:" in recovery
    assert "entrypoint:\n      - platform-backup" in recovery
    assert "  recover-restore:" in recovery
    assert "platform-server\n      - recover-restore" in recovery

    backup_service = recovery.split("\n\n  backup:", 1)[1].split("\n\n  restore:", 1)[0]
    data_mount = backup_service.split("      - type: volume", 1)[1].split("      - type: bind", 1)[
        0
    ]
    assert "read_only:" not in data_mount

    restore_service = recovery.split("\n\n  restore:", 1)[1].split("\n\n  recover-restore:", 1)[0]
    assert "read_only: true" in restore_service
    assert "network_mode: none" in restore_service


def test_recovery_override_keeps_canonical_data_path_for_replacement_runtime() -> None:
    recovery = RECOVERY_COMPOSE.read_text(encoding="utf-8")
    control_plane = recovery.split("\n\n  backup:", 1)[0]

    assert "target: /var/lib/ai-multi-agent-platform" in control_plane
    assert "subpath: restored-data" in control_plane
    assert "AI_MAP_DATA_DIR" not in control_plane


def test_container_edge_preserves_api_prefix_and_spa_fallback() -> None:
    caddy = (DOCKER_DIR / "Caddyfile").read_text(encoding="utf-8")

    assert "handle /api/*" in caddy
    assert "handle_path /api" not in caddy
    assert "reverse_proxy control-plane:8000" in caddy
    assert "try_files {path} /index.html" in caddy
    assert "file_server" in caddy
    assert "admin off" in caddy


def test_control_plane_image_is_server_only_and_non_root() -> None:
    dockerfile = (DOCKER_DIR / "control-plane.Dockerfile").read_text(encoding="utf-8")

    assert '".[server]"' in dockerfile
    assert ".[dev]" not in dockerfile
    assert "USER ai-map" in dockerfile
    assert 'CMD ["platform-server", "serve"]' in dockerfile
    assert "/var/lib/ai-multi-agent-platform" in dockerfile


def test_web_image_builds_frontend_and_serves_static_output() -> None:
    dockerfile = (DOCKER_DIR / "web.Dockerfile").read_text(encoding="utf-8")

    assert "node:22.22.2-alpine" in dockerfile
    assert "npm@11.6.0" in dockerfile
    assert "npm ci --no-audit --no-fund" in dockerfile
    assert "npm run build" in dockerfile
    assert "caddy:2.11.4-alpine" in dockerfile
    assert "setcap -r /usr/bin/caddy" in dockerfile
    assert "COPY --from=builder /app/dist /srv/frontend" in dockerfile
    assert "npm run dev" not in dockerfile


def test_docker_runbook_documents_secure_external_edge_and_volume_retention() -> None:
    runbook = (DOCKER_DIR / "README.md").read_text(encoding="utf-8")

    assert "AI_MAP_SECURE_COOKIE=true" in runbook
    assert "https://<your-domain>" in runbook
    assert "AI_MAP_PUBLIC_DOMAIN" in runbook
    assert "docker compose down -v" in runbook
    normalized = " ".join(runbook.split())
    _, grace_seconds = _shutdown_contract(COMPOSE.read_text(encoding="utf-8"))
    assert "deletes the named volume" in normalized
    assert "raw.githubusercontent.com/ScoreSymphony/AI-Multi-Agent-Platform" in runbook
    assert "docker-compose.recovery.yml" in runbook
    assert "recover-restore" in runbook
    assert "restored-data" in runbook
    assert f"{grace_seconds}-second Compose hard-stop grace period" in normalized
    assert (
        f"maximum supported {MAX_SHUTDOWN_TIMEOUT_SECONDS}-second shutdown/drain budget"
        in normalized
    )


def test_hostinger_zero_config_profile_coexists_with_shared_traefik() -> None:
    compose = HOSTINGER_ZERO_CONFIG_COMPOSE.read_text(encoding="utf-8")

    remote_context = (
        "${AI_MAP_SOURCE_CONTEXT:-"
        "https://github.com/ScoreSymphony/AI-Multi-Agent-Platform.git#main}"
    )
    assert compose.count(remote_context) == 3
    assert "dockerfile: deploy/docker/control-plane.Dockerfile" in compose
    assert "dockerfile: deploy/docker/web.Dockerfile" in compose
    assert "dockerfile: deploy/docker/hostinger-gateway.Dockerfile" in compose
    assert 'AI_MAP_SECURE_COOKIE: "true"' in compose

    control_plane = _control_plane_block(compose)
    web = compose.split("\n  web:", 1)[1].split("\n  hostinger-gateway:", 1)[0]
    gateway = compose.split("\n  hostinger-gateway:", 1)[1].split("\nvolumes:", 1)[0]

    assert "ports:" not in control_plane
    assert "ports:" not in web
    assert '      - "8000"' in control_plane
    assert '      - "8080"' in web

    assert "AI_MAP_HOSTINGER_EDGE_MODE: traefik-passthrough" in gateway
    assert 'AI_MAP_PUBLIC_DOMAIN: ""' in gateway
    assert (
        "AI_MAP_COMPOSE_PROJECT_NAME: ${COMPOSE_PROJECT_NAME:-ai-multi-agent-platform}" in gateway
    )
    assert "AI_MAP_HOSTINGER_TRAEFIK_HOST" not in gateway
    assert "uts: host" in gateway
    assert "network_mode: host" not in gateway
    assert "privileged:" not in gateway
    assert "/var/run/docker.sock" not in gateway

    assert "ports:" not in gateway
    assert '"80:80"' not in gateway
    assert '"443:443"' not in gateway
    assert "AI_MAP_HOSTINGER_BOOTSTRAP_PORT" not in gateway
    assert "traefik.enable=true" in gateway
    assert "traefik.docker.network" not in gateway
    assert "HostRegexp(" in gateway
    assert "HostSNIRegexp(" in gateway
    assert "srv[0-9]+" in gateway
    assert ".entrypoints=web" in gateway
    assert ".entrypoints=websecure" in gateway
    assert ".tls.passthrough=true" in gateway
    assert "HostSNI(`*`)" not in gateway
    assert "HostSNIRegexp(`^.*$`)" not in gateway
    assert ".loadbalancer.server.port=80" in gateway
    assert ".loadbalancer.server.port=443" in gateway
    assert "setup.invalid" not in gateway
    assert "-custom-http.rule=Host(" not in gateway
    assert "-custom-tls.rule=HostSNI(" not in gateway

    assert "      - hostinger-gateway-data:/data" in gateway
    assert "      - hostinger-gateway-config:/config" in gateway
    assert "      - no-new-privileges:true" in gateway
    assert "    cap_drop:\n      - ALL" in gateway
    assert "    cap_add:\n      - NET_BIND_SERVICE" in gateway
    assert "      - platform" in gateway
    assert "      - traefik-proxy" not in gateway

    assert "hostinger-gateway-data:" in compose
    assert "hostinger-gateway-config:" in compose
    assert "traefik-proxy:" not in compose
    assert "TRAEFIK_HOST" not in compose
    assert "AI_MAP_TRAEFIK_NETWORK" not in compose
    assert "AI_MAP_TRAEFIK_EXTERNAL" not in compose


def test_hostinger_custom_domain_profile_requires_exact_domain() -> None:
    compose = HOSTINGER_CUSTOM_DOMAIN_COMPOSE.read_text(encoding="utf-8")
    control_plane = _control_plane_block(compose)
    web = compose.split("\n  web:", 1)[1].split("\n  hostinger-gateway:", 1)[0]
    gateway = compose.split("\n  hostinger-gateway:", 1)[1].split("\nvolumes:", 1)[0]

    assert "ports:" not in control_plane
    assert "ports:" not in web
    assert "ports:" not in gateway
    assert "AI_MAP_HOSTINGER_EDGE_MODE: traefik-passthrough" in gateway
    assert (
        "AI_MAP_PUBLIC_DOMAIN: "
        "${AI_MAP_PUBLIC_DOMAIN:?set AI_MAP_PUBLIC_DOMAIN to the public DNS hostname}"
        in gateway
    )
    assert "setup.invalid" not in gateway
    assert "HostRegexp(" not in gateway
    assert "HostSNIRegexp(" not in gateway
    assert (
        "rule=Host(`${AI_MAP_PUBLIC_DOMAIN:?set AI_MAP_PUBLIC_DOMAIN to the public DNS hostname}`)"
        in gateway
    )
    assert (
        "rule=HostSNI(`${AI_MAP_PUBLIC_DOMAIN:?set AI_MAP_PUBLIC_DOMAIN to the public DNS hostname}`)"
        in gateway
    )
    assert ".tls.passthrough=true" in gateway
    assert ".loadbalancer.server.port=80" in gateway
    assert ".loadbalancer.server.port=443" in gateway
    assert "traefik.docker.network" not in gateway
    assert "      - platform" in gateway


def test_hostinger_direct_profile_preserves_direct_caddy_edge() -> None:
    compose = HOSTINGER_DIRECT_COMPOSE.read_text(encoding="utf-8")
    control_plane = _control_plane_block(compose)
    web = compose.split("\n  web:", 1)[1].split("\n  hostinger-gateway:", 1)[0]
    gateway = compose.split("\n  hostinger-gateway:", 1)[1].split("\nvolumes:", 1)[0]

    assert "ports:" not in control_plane
    assert "ports:" not in web
    assert "AI_MAP_HOSTINGER_EDGE_MODE: direct" in gateway
    assert "uts: host" in gateway
    assert '      - "80:80"' in gateway
    assert '      - "443:443"' in gateway
    assert "traefik.enable=true" not in gateway
    assert "traefik-proxy" not in gateway
    assert "hostinger-gateway-data:" in compose
    assert "hostinger-gateway-config:" in compose


def test_hostinger_legacy_entry_points_remain_migration_safe() -> None:
    legacy = HOSTINGER_COMPOSE.read_text(encoding="utf-8")
    https_alias = HOSTINGER_HTTPS_COMPOSE.read_text(encoding="utf-8")

    assert https_alias == legacy
    assert "uts: host" not in legacy
    assert '"80:80"' not in legacy
    assert '"443:443"' not in legacy
    assert "traefik.enable=true" in legacy
    assert "traefik.docker.network=${AI_MAP_TRAEFIK_NETWORK:-ai-map-hostinger-edge}" in legacy
    assert "external: ${AI_MAP_TRAEFIK_EXTERNAL:-false}" in legacy
    assert "${TRAEFIK_HOST:-setup.invalid}" in legacy


def test_hostinger_shared_traefik_profile_remains_explicit_and_private() -> None:
    compose = HOSTINGER_SHARED_TRAEFIK_COMPOSE.read_text(encoding="utf-8")
    control_plane = _control_plane_block(compose)
    web = compose.split("\n  web:", 1)[1].split("\n  hostinger-gateway:", 1)[0]
    gateway = compose.split("\n  hostinger-gateway:", 1)[1].split("\nvolumes:", 1)[0]

    assert "ports:" not in control_plane
    assert "ports:" not in web
    assert "ports:" not in gateway
    assert "uts: host" not in gateway
    assert "AI_MAP_HOSTINGER_EDGE_MODE: shared-traefik" in gateway
    assert "AI_MAP_HOSTINGER_TRAEFIK_HOST: ${TRAEFIK_HOST:-}" in gateway
    assert "traefik.enable=true" in gateway
    assert "traefik.docker.network=${AI_MAP_TRAEFIK_NETWORK:-traefik-proxy}" in gateway
    assert (
        "rule=Host(`${AI_MAP_PUBLIC_DOMAIN:-"
        "${COMPOSE_PROJECT_NAME:-ai-multi-agent-platform}."
        "${TRAEFIK_HOST:-setup.invalid}}`)" in gateway
    )
    assert ".entrypoints=websecure" in gateway
    assert ".tls.certresolver=letsencrypt" in gateway
    assert ".loadbalancer.server.port=8080" in gateway
    assert "traefik-proxy:\n    external: true" in compose
    assert "name: ${AI_MAP_TRAEFIK_NETWORK:-traefik-proxy}" in compose
    assert '"80:80"' not in compose
    assert '"443:443"' not in compose


def test_hostinger_runbook_documents_zero_config_default_and_shared_edge() -> None:
    runbook = (DOCKER_DIR / "README.md").read_text(encoding="utf-8")
    normalized = " ".join(runbook.split())

    compose_url = (
        "https://raw.githubusercontent.com/ScoreSymphony/AI-Multi-Agent-Platform/"
        "main/deploy/docker/docker-compose.hostinger-zero-config.yml"
    )
    custom_url = (
        "https://raw.githubusercontent.com/ScoreSymphony/AI-Multi-Agent-Platform/"
        "main/deploy/docker/docker-compose.hostinger-custom-domain.yml"
    )
    direct_url = (
        "https://raw.githubusercontent.com/ScoreSymphony/AI-Multi-Agent-Platform/"
        "main/deploy/docker/docker-compose.hostinger-direct.yml"
    )
    shared_url = (
        "https://raw.githubusercontent.com/ScoreSymphony/AI-Multi-Agent-Platform/"
        "main/deploy/docker/docker-compose.hostinger-shared-traefik.yml"
    )
    assert compose_url in runbook
    assert custom_url in runbook
    assert direct_url in runbook
    assert shared_url in runbook
    assert "Copy that URL into Hostinger's **Compose from URL** field." in normalized
    assert "https://assets.hostinger.com/vps/deploy.svg" not in runbook
    assert "Zero-configuration" in runbook
    assert "uts: host" in runbook
    assert "srvNNNNNN.hstgr.cloud" in runbook
    assert "${COMPOSE_PROJECT_NAME}.srvNNNNNN.hstgr.cloud" in runbook
    assert "No `TRAEFIK_HOST`" in runbook
    assert "setup.invalid" in runbook
    assert "docker-compose.hostinger-custom-domain.yml" in runbook
    assert "Docker `host` networking" in runbook
    assert "does not require any external Docker network" in runbook
    assert "traefik-proxy" in runbook
    assert "HostRegexp" in runbook
    assert "HostSNIRegexp" in runbook
    assert "TLS passthrough" in runbook
    assert "publishes **no application host port**" in runbook
    assert "No additional firewall rule" in runbook
    assert "NET_BIND_SERVICE" in runbook
    assert "hostinger-gateway-data" in runbook
    assert "hostinger-gateway-config" in runbook
    assert "docker-compose.hostinger-direct.yml" in runbook
    assert "docker-compose.hostinger-shared-traefik.yml" in runbook
    assert "TRAEFIK_HOST=srv123456.hstgr.cloud" in runbook
    assert "AI_MAP_TRAEFIK_NETWORK=traefik-proxy" in runbook
    assert "AI_MAP_TRAEFIK_EXTERNAL=true" in runbook
    assert "fail-closed" in runbook


def test_hostinger_gateway_supports_direct_passthrough_and_shared_traefik_modes() -> None:
    dockerfile = HOSTINGER_GATEWAY_DOCKERFILE.read_text(encoding="utf-8")
    shared_active = HOSTINGER_GATEWAY_CADDY.read_text(encoding="utf-8")
    shared_pending = HOSTINGER_GATEWAY_PENDING_CADDY.read_text(encoding="utf-8")
    direct_active = HOSTINGER_DIRECT_CADDY.read_text(encoding="utf-8")
    direct_pending = HOSTINGER_DIRECT_PENDING_CADDY.read_text(encoding="utf-8")
    entrypoint = HOSTINGER_GATEWAY_ENTRYPOINT.read_text(encoding="utf-8")

    assert "FROM caddy:2.11.4-alpine" in dockerfile
    assert "setcap -r /usr/bin/caddy" in dockerfile
    assert "Caddyfile.hostinger-direct" in dockerfile
    assert "Caddyfile.hostinger-direct-setup-pending" in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/ai-map-hostinger-gateway-entrypoint"]' in dockerfile
    assert "EXPOSE 80 443 8080" in dockerfile

    assert "http:// {" in direct_active
    assert ":8080 {" in direct_active
    assert direct_active.count("redir https://{$AI_MAP_PUBLIC_DOMAIN}{uri} 308") == 2
    assert "{$AI_MAP_PUBLIC_DOMAIN} {" in direct_active
    assert "reverse_proxy web:8080" in direct_active
    assert "control-plane:8000" not in direct_active

    assert ":80 {" in direct_pending
    assert ":8080 {" in direct_pending
    assert "setup pending" in direct_pending
    assert "503" in direct_pending
    assert "reverse_proxy" not in direct_pending
    assert "web:8080" not in direct_pending

    assert "http://{$AI_MAP_PUBLIC_DOMAIN}:8080" in shared_active
    assert "reverse_proxy web:8080" in shared_active
    assert ":8080 {" in shared_pending
    assert "reverse_proxy" not in shared_pending

    assert 'edge_mode="${AI_MAP_HOSTINGER_EDGE_MODE:-shared-traefik}"' in entrypoint
    assert "direct|shared-traefik|traefik-passthrough" in entrypoint
    assert 'explicit_domain="${AI_MAP_PUBLIC_DOMAIN:-}"' in entrypoint
    assert 'traefik_host="${AI_MAP_HOSTINGER_TRAEFIK_HOST:-}"' in entrypoint
    assert 'project_name="${AI_MAP_COMPOSE_PROJECT_NAME:-ai-multi-agent-platform}"' in entrypoint
    assert 'host_hostname="$(hostname 2>/dev/null || true)"' in entrypoint
    assert 'vps_id="${host_hostname#srv}"' in entrypoint
    assert 'vps_id="${vps_id%.hstgr.cloud}"' in entrypoint
    assert 'managed_hostname="$host_hostname.hstgr.cloud"' in entrypoint
    assert 'domain="$project_name.$managed_hostname"' in entrypoint
    assert 'domain="$project_name.$traefik_host"' in entrypoint
    assert 'export AI_MAP_PUBLIC_DOMAIN="$domain"' in entrypoint
    assert "Caddyfile.hostinger-direct-setup-pending" in entrypoint
    assert "Caddyfile.hostinger-direct" in entrypoint
    assert "Caddyfile.hostinger-setup-pending" in entrypoint
    assert "Caddyfile.hostinger-gateway" in entrypoint


def _run_hostinger_gateway_entrypoint(
    tmp_path: Path, env_overrides: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_caddy = fake_bin / "caddy"
    fake_caddy.write_text(
        '#!/bin/sh\nprintf "domain=%s\\n" "$AI_MAP_PUBLIC_DOMAIN"\nprintf "args=%s\\n" "$*"\n',
        encoding="utf-8",
    )
    fake_caddy.chmod(0o755)

    fake_hostname = fake_bin / "hostname"
    fake_hostname.write_text(
        '#!/bin/sh\nprintf "%s\\n" "${FAKE_HOSTNAME:-ci-runner.invalid}"\n',
        encoding="utf-8",
    )
    fake_hostname.chmod(0o755)

    env = os.environ.copy()
    for key in (
        "AI_MAP_HOSTINGER_EDGE_MODE",
        "AI_MAP_PUBLIC_DOMAIN",
        "AI_MAP_HOSTINGER_TRAEFIK_HOST",
        "AI_MAP_COMPOSE_PROJECT_NAME",
        "FAKE_HOSTNAME",
    ):
        env.pop(key, None)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env.update(env_overrides)

    return subprocess.run(
        ["sh", str(HOSTINGER_GATEWAY_ENTRYPOINT)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_hostinger_direct_gateway_derives_managed_vps_hostname(tmp_path: Path) -> None:
    result = _run_hostinger_gateway_entrypoint(
        tmp_path,
        {
            "AI_MAP_HOSTINGER_EDGE_MODE": "direct",
            "FAKE_HOSTNAME": "srv123456.hstgr.cloud",
            "AI_MAP_COMPOSE_PROJECT_NAME": "ai-multi-agent-platform",
        },
    )

    assert result.returncode == 0
    assert "domain=ai-multi-agent-platform.srv123456.hstgr.cloud" in result.stdout
    assert "Caddyfile.hostinger-direct" in result.stdout
    assert "source: Hostinger VPS hostname" in result.stderr
    assert "mode: direct" in result.stderr


def test_hostinger_traefik_passthrough_gateway_derives_short_hostinger_hostname(
    tmp_path: Path,
) -> None:
    result = _run_hostinger_gateway_entrypoint(
        tmp_path,
        {
            "AI_MAP_HOSTINGER_EDGE_MODE": "traefik-passthrough",
            "FAKE_HOSTNAME": "srv1940023",
            "AI_MAP_COMPOSE_PROJECT_NAME": "ai-multi-agent-platform",
        },
    )

    assert result.returncode == 0
    assert "domain=ai-multi-agent-platform.srv1940023.hstgr.cloud" in result.stdout
    assert "Caddyfile.hostinger-direct" in result.stdout
    assert "source: Hostinger VPS hostname" in result.stderr
    assert "mode: traefik-passthrough" in result.stderr


def test_hostinger_traefik_passthrough_gateway_derives_managed_vps_hostname(
    tmp_path: Path,
) -> None:
    result = _run_hostinger_gateway_entrypoint(
        tmp_path,
        {
            "AI_MAP_HOSTINGER_EDGE_MODE": "traefik-passthrough",
            "FAKE_HOSTNAME": "srv123456.hstgr.cloud",
            "AI_MAP_COMPOSE_PROJECT_NAME": "ai-multi-agent-platform",
        },
    )

    assert result.returncode == 0
    assert "domain=ai-multi-agent-platform.srv123456.hstgr.cloud" in result.stdout
    assert "Caddyfile.hostinger-direct" in result.stdout
    assert "source: Hostinger VPS hostname" in result.stderr
    assert "mode: traefik-passthrough" in result.stderr


def test_hostinger_direct_gateway_explicit_domain_overrides_host_hostname(
    tmp_path: Path,
) -> None:
    result = _run_hostinger_gateway_entrypoint(
        tmp_path,
        {
            "AI_MAP_HOSTINGER_EDGE_MODE": "direct",
            "AI_MAP_PUBLIC_DOMAIN": "agents.example.com",
            "FAKE_HOSTNAME": "srv123456.hstgr.cloud",
            "AI_MAP_COMPOSE_PROJECT_NAME": "ai-multi-agent-platform",
        },
    )

    assert result.returncode == 0
    assert "domain=agents.example.com" in result.stdout
    assert "Caddyfile.hostinger-direct" in result.stdout
    assert "source: AI_MAP_PUBLIC_DOMAIN" in result.stderr


def test_hostinger_direct_gateway_rejects_unmanaged_host_hostname(tmp_path: Path) -> None:
    result = _run_hostinger_gateway_entrypoint(
        tmp_path,
        {
            "AI_MAP_HOSTINGER_EDGE_MODE": "direct",
            "FAKE_HOSTNAME": "custom.example.com",
            "AI_MAP_COMPOSE_PROJECT_NAME": "ai-multi-agent-platform",
        },
    )

    assert result.returncode == 0
    assert "Caddyfile.hostinger-direct-setup-pending" in result.stdout
    assert "fail-closed setup-pending mode" in result.stderr
    assert "domain=" in result.stdout
    assert "web:8080" not in result.stdout


def test_hostinger_gateway_rejects_invalid_short_hostinger_hostname(tmp_path: Path) -> None:
    result = _run_hostinger_gateway_entrypoint(
        tmp_path,
        {
            "AI_MAP_HOSTINGER_EDGE_MODE": "traefik-passthrough",
            "FAKE_HOSTNAME": "srvabc",
            "AI_MAP_COMPOSE_PROJECT_NAME": "ai-multi-agent-platform",
        },
    )

    assert result.returncode == 0
    assert "Caddyfile.hostinger-direct-setup-pending" in result.stdout
    assert "fail-closed setup-pending mode" in result.stderr


def test_hostinger_gateway_rejects_hostinger_hostname_lookalike(tmp_path: Path) -> None:
    result = _run_hostinger_gateway_entrypoint(
        tmp_path,
        {
            "AI_MAP_HOSTINGER_EDGE_MODE": "traefik-passthrough",
            "FAKE_HOSTNAME": "srv123.hstgr.cloud.evil.example",
            "AI_MAP_COMPOSE_PROJECT_NAME": "ai-multi-agent-platform",
        },
    )

    assert result.returncode == 0
    assert "Caddyfile.hostinger-direct-setup-pending" in result.stdout
    assert "fail-closed setup-pending mode" in result.stderr


def test_hostinger_shared_gateway_derives_temporary_hostname_from_traefik_host(
    tmp_path: Path,
) -> None:
    result = _run_hostinger_gateway_entrypoint(
        tmp_path,
        {
            "AI_MAP_HOSTINGER_EDGE_MODE": "shared-traefik",
            "AI_MAP_HOSTINGER_TRAEFIK_HOST": "srv123456.hstgr.cloud",
            "AI_MAP_COMPOSE_PROJECT_NAME": "ai-multi-agent-platform",
            "FAKE_HOSTNAME": "custom.example.com",
        },
    )

    assert result.returncode == 0
    assert "domain=ai-multi-agent-platform.srv123456.hstgr.cloud" in result.stdout
    assert "Caddyfile.hostinger-gateway" in result.stdout
    assert "source: Hostinger TRAEFIK_HOST" in result.stderr
    assert "mode: shared-traefik" in result.stderr


def test_hostinger_shared_gateway_without_hostname_stays_setup_pending(
    tmp_path: Path,
) -> None:
    result = _run_hostinger_gateway_entrypoint(
        tmp_path,
        {
            "AI_MAP_HOSTINGER_EDGE_MODE": "shared-traefik",
            "FAKE_HOSTNAME": "srv123456.hstgr.cloud",
        },
    )

    assert result.returncode == 0
    assert "Caddyfile.hostinger-setup-pending" in result.stdout
    assert "fail-closed setup-pending mode" in result.stderr


def test_generic_https_edge_remains_pinned_for_non_hostinger_root_profile() -> None:
    dockerfile = (DOCKER_DIR / "https-edge.Dockerfile").read_text(encoding="utf-8")
    caddy = (DOCKER_DIR / "Caddyfile.public-https").read_text(encoding="utf-8")
    entrypoint = (DOCKER_DIR / "https-edge-entrypoint.sh").read_text(encoding="utf-8")

    assert "FROM caddy:2.11.4-alpine" in dockerfile
    assert "COPY deploy/docker/Caddyfile.public-https /etc/caddy/Caddyfile" in dockerfile
    assert (
        "COPY deploy/docker/Caddyfile.setup-pending /etc/caddy/Caddyfile.setup-pending"
        in dockerfile
    )
    assert "COPY deploy/docker/https-edge-entrypoint.sh" in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/ai-map-https-edge-entrypoint"]' in dockerfile
    assert "EXPOSE 80 443" in dockerfile
    assert "{$AI_MAP_PUBLIC_DOMAIN}" in caddy
    assert "reverse_proxy web:8080" in caddy
    assert "control-plane:8000" not in caddy
    assert 'domain="${AI_MAP_PUBLIC_DOMAIN:-}"' in entrypoint


def test_hostinger_runbook_documents_direct_shared_and_alternate_edges() -> None:
    runbook = (DOCKER_DIR / "README.md").read_text(encoding="utf-8")
    normalized = " ".join(runbook.split())

    assert "main/deploy/docker/docker-compose.hostinger-zero-config.yml" in runbook
    assert "docker-compose.hostinger-direct.yml" in runbook
    assert "docker-compose.hostinger.yml" in runbook
    assert "docker-compose.hostinger-shared-traefik.yml" in runbook
    assert "docker-compose.hostinger-external-edge.yml" in runbook
    assert "docker-compose.hostinger-https.yml" in runbook
    assert "AI_MAP_PUBLIC_DOMAIN" in runbook
    assert "AI_MAP_TRAEFIK_NETWORK" in runbook
    assert "AI_MAP_TRAEFIK_EXTERNAL" in runbook
    assert "traefik-proxy" in runbook
    assert "srvNNNNNN.hstgr.cloud" in runbook
    assert "hostinger-gateway" in runbook
    assert "fail-closed" in runbook
    assert "NET_BIND_SERVICE" in runbook
    assert "ports 80 and 443" in normalized
    assert "Caddy" in runbook
    hostinger_section = runbook[
        runbook.index("## Hostinger Docker Manager") : runbook.index("## Configuration")
    ]
    assert "Caddy" in hostinger_section


def test_hostinger_external_edge_profile_remains_explicit_and_non_default() -> None:
    compose = HOSTINGER_EXTERNAL_EDGE_COMPOSE.read_text(encoding="utf-8")

    remote_context = (
        "${AI_MAP_SOURCE_CONTEXT:-"
        "https://github.com/ScoreSymphony/AI-Multi-Agent-Platform.git#main}"
    )
    assert compose.count(remote_context) == 2
    assert "dockerfile: deploy/docker/control-plane.Dockerfile" in compose
    assert "dockerfile: deploy/docker/web.Dockerfile" in compose
    assert "dockerfile: deploy/docker/https-edge.Dockerfile" not in compose
    assert 'AI_MAP_SECURE_COOKIE: "true"' in compose
    assert '"${AI_MAP_PUBLIC_PORT:-8080}:8080"' in compose
    assert "AI_MAP_PUBLIC_DOMAIN" not in compose

    control_plane = _control_plane_block(compose)
    assert "ports:" not in control_plane


def test_hostinger_default_and_https_compatibility_profiles_match() -> None:
    default_compose = HOSTINGER_COMPOSE.read_text(encoding="utf-8")
    compatibility_compose = HOSTINGER_HTTPS_COMPOSE.read_text(encoding="utf-8")

    assert default_compose == compatibility_compose


def test_readme_exposes_hostinger_compose_url_directly() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    normalized = " ".join(readme.split())
    compose_url = (
        "https://raw.githubusercontent.com/ScoreSymphony/AI-Multi-Agent-Platform/"
        "main/deploy/docker/docker-compose.hostinger-zero-config.yml"
    )

    assert compose_url in readme
    assert "Copy that URL into Hostinger's **Compose from URL** field." in normalized
    assert "https://assets.hostinger.com/vps/deploy.svg" not in readme
    assert "repository landing page itself is not the Compose URL" in normalized
