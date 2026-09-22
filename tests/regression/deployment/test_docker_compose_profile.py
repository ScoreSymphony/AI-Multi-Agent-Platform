from __future__ import annotations

import re
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
HOSTINGER_EXTERNAL_EDGE_COMPOSE = DOCKER_DIR / "docker-compose.hostinger-external-edge.yml"
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
    assert "requires HTTPS" in runbook
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


def test_hostinger_default_url_profile_is_https_first_and_self_contained() -> None:
    compose = HOSTINGER_COMPOSE.read_text(encoding="utf-8")

    remote_context = (
        "${AI_MAP_SOURCE_CONTEXT:-"
        "https://github.com/ScoreSymphony/AI-Multi-Agent-Platform.git#main}"
    )
    assert compose.count(remote_context) == 3
    assert "dockerfile: deploy/docker/control-plane.Dockerfile" in compose
    assert "dockerfile: deploy/docker/web.Dockerfile" in compose
    assert "dockerfile: deploy/docker/https-edge.Dockerfile" in compose
    assert "platform-data:/var/lib/ai-multi-agent-platform" in compose
    assert 'AI_MAP_SECURE_COOKIE: "true"' in compose
    assert (
        "AI_MAP_PUBLIC_DOMAIN: "
        "${AI_MAP_PUBLIC_DOMAIN:?set AI_MAP_PUBLIC_DOMAIN to the public DNS hostname}" in compose
    )

    control_plane = _control_plane_block(compose)
    web = compose.split("\n  web:", 1)[1].split("\n  https-edge:", 1)[0]
    edge = compose.split("\n  https-edge:", 1)[1].split("\nvolumes:", 1)[0]

    assert "ports:" not in control_plane
    assert "ports:" not in web
    assert '      - "80:80"' in edge
    assert '      - "443:443"' in edge
    assert "caddy-data:/data" in edge
    assert "caddy-config:/config" in edge


def test_hostinger_runbook_points_to_standalone_compose_file() -> None:
    runbook = (DOCKER_DIR / "README.md").read_text(encoding="utf-8")
    normalized = " ".join(runbook.split())

    assert "main/deploy/docker/docker-compose.hostinger.yml" in runbook
    assert "repository root" in runbook
    assert "public Git repository itself as the Docker build context" in normalized


def test_hostinger_https_compatibility_profile_publishes_only_the_tls_edge() -> None:
    compose = HOSTINGER_HTTPS_COMPOSE.read_text(encoding="utf-8")

    remote_context = (
        "${AI_MAP_SOURCE_CONTEXT:-"
        "https://github.com/ScoreSymphony/AI-Multi-Agent-Platform.git#main}"
    )
    assert compose.count(remote_context) == 3
    assert "dockerfile: deploy/docker/control-plane.Dockerfile" in compose
    assert "dockerfile: deploy/docker/web.Dockerfile" in compose
    assert "dockerfile: deploy/docker/https-edge.Dockerfile" in compose
    assert 'AI_MAP_SECURE_COOKIE: "true"' in compose
    assert (
        "AI_MAP_PUBLIC_DOMAIN: "
        "${AI_MAP_PUBLIC_DOMAIN:?set AI_MAP_PUBLIC_DOMAIN to the public DNS hostname}" in compose
    )

    control_plane = _control_plane_block(compose)
    web = compose.split("\n  web:", 1)[1].split("\n  https-edge:", 1)[0]
    edge = compose.split("\n  https-edge:", 1)[1].split("\nvolumes:", 1)[0]

    assert "ports:" not in control_plane
    assert "ports:" not in web
    assert '      - "8000"' in control_plane
    assert '      - "8080"' in web
    assert "ports:" in edge
    assert '      - "80:80"' in edge
    assert '      - "443:443"' in edge
    assert "reverse_proxy" not in compose
    assert "caddy-data:/data" in edge
    assert "caddy-config:/config" in edge
    assert "cap_drop:\n      - ALL" in edge
    assert "cap_add:\n      - NET_BIND_SERVICE" in edge


def test_hostinger_https_edge_is_pinned_and_proxies_only_to_web() -> None:
    dockerfile = (DOCKER_DIR / "https-edge.Dockerfile").read_text(encoding="utf-8")
    caddy = (DOCKER_DIR / "Caddyfile.public-https").read_text(encoding="utf-8")

    assert "FROM caddy:2.11.4-alpine" in dockerfile
    assert "COPY deploy/docker/Caddyfile.public-https /etc/caddy/Caddyfile" in dockerfile
    assert "EXPOSE 80 443" in dockerfile
    assert "{$AI_MAP_PUBLIC_DOMAIN}" in caddy
    assert "reverse_proxy web:8080" in caddy
    assert "control-plane:8000" not in caddy
    assert "auto_https off" not in caddy
    assert "admin off" in caddy


def test_hostinger_runbook_documents_both_tls_ownership_modes() -> None:
    runbook = (DOCKER_DIR / "README.md").read_text(encoding="utf-8")
    normalized = " ".join(runbook.split())

    assert "main/deploy/docker/docker-compose.hostinger.yml" in runbook
    assert "main/deploy/docker/docker-compose.hostinger-external-edge.yml" in runbook
    assert "docker-compose.hostinger-https.yml" in runbook
    assert "AI_MAP_PUBLIC_DOMAIN" in runbook
    assert "ports 80 and 443" in normalized
    assert "DNS" in runbook
    assert "Caddy" in runbook
    assert "caddy-data" in runbook
    assert "caddy-config" in runbook


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
