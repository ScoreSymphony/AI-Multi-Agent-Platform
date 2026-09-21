from __future__ import annotations

from pathlib import Path

COMPOSE = Path("docker-compose.yml")
DOCKER_DIR = Path("deploy/docker")
HOSTINGER_COMPOSE = DOCKER_DIR / "docker-compose.hostinger.yml"
RECOVERY_COMPOSE = DOCKER_DIR / "docker-compose.recovery.yml"


def test_compose_keeps_control_plane_private_and_state_durable() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")

    assert "dockerfile: deploy/docker/control-plane.Dockerfile" in compose
    assert "dockerfile: deploy/docker/web.Dockerfile" in compose
    assert "AI_MAP_HOST: 0.0.0.0" in compose
    assert 'AI_MAP_SECURE_COOKIE: "true"' in compose
    assert "platform-data:/var/lib/ai-multi-agent-platform" in compose
    assert '      - "8000"' in compose
    assert '"${AI_MAP_PUBLIC_PORT:-8080}:8080"' in compose

    control_plane = compose.split("\n\n  backup:", 1)[0]
    assert "ports:" not in control_plane
    assert "expose:" in control_plane
    assert "no-new-privileges:true" in control_plane
    assert "cap_drop:" in control_plane
    assert "stop_grace_period: 40s" in control_plane


def test_compose_backup_service_exports_quiesced_backup_outside_data_volume() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")

    assert "  backup:" in compose
    assert "profiles:\n      - operations" in compose
    assert "entrypoint:\n      - platform-backup" in compose
    assert "source: platform-data" in compose
    assert "target: /var/lib/ai-multi-agent-platform" in compose
    assert "read_only: true" in compose
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
    assert "read_only: true" in recovery


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
    assert "deletes the named volume" in normalized
    assert "raw.githubusercontent.com/ScoreSymphony/AI-Multi-Agent-Platform" in runbook
    assert "docker-compose.recovery.yml" in runbook
    assert "recover-restore" in runbook
    assert "restored-data" in runbook


def test_hostinger_url_profile_is_self_contained_and_uses_remote_source_context() -> None:
    compose = HOSTINGER_COMPOSE.read_text(encoding="utf-8")

    remote_context = (
        "${AI_MAP_SOURCE_CONTEXT:-"
        "https://github.com/ScoreSymphony/AI-Multi-Agent-Platform.git#main}"
    )
    assert compose.count(remote_context) == 2
    assert "dockerfile: deploy/docker/control-plane.Dockerfile" in compose
    assert "dockerfile: deploy/docker/web.Dockerfile" in compose
    assert "platform-data:/var/lib/ai-multi-agent-platform" in compose

    control_plane = compose.split("\n  web:", 1)[0]
    assert "ports:" not in control_plane
    assert 'AI_MAP_SECURE_COOKIE: "true"' in compose


def test_hostinger_runbook_points_to_standalone_compose_file() -> None:
    runbook = (DOCKER_DIR / "README.md").read_text(encoding="utf-8")

    assert "main/deploy/docker/docker-compose.hostinger.yml" in runbook
    assert "repository root" in runbook
    assert "public Git repository itself as the Docker build context" in runbook
