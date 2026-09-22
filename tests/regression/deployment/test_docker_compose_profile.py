from __future__ import annotations

import re
from pathlib import Path

from ai_multi_agent_platform.deployment.config import (
    DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    MAX_SHUTDOWN_TIMEOUT_SECONDS,
)

COMPOSE = Path("docker-compose.yml")
DOCKER_DIR = Path("deploy/docker")
HOSTINGER_COMPOSE = DOCKER_DIR / "docker-compose.hostinger.yml"

_STOP_GRACE_RE = re.compile(r"^\s*stop_grace_period:\s*(\d+)s\s*$", re.MULTILINE)
_SHUTDOWN_DEFAULT_RE = re.compile(
    r"AI_MAP_SHUTDOWN_TIMEOUT_SECONDS:\s*"
    r"\$\{AI_MAP_SHUTDOWN_TIMEOUT_SECONDS:-(\d+)\}"
)


def _control_plane_block(compose: str) -> str:
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
    assert "AI_MAP_HOST: 0.0.0.0" in compose
    assert 'AI_MAP_SECURE_COOKIE: "true"' in compose
    assert "platform-data:/var/lib/ai-multi-agent-platform" in compose
    assert '      - "8000"' in compose
    assert '"${AI_MAP_PUBLIC_PORT:-8080}:8080"' in compose

    control_plane = _control_plane_block(compose)
    assert "ports:" not in control_plane
    assert "expose:" in control_plane
    assert "no-new-privileges:true" in control_plane
    assert "cap_drop:" in control_plane


def test_compose_stop_grace_exceeds_every_supported_shutdown_budget() -> None:
    root_compose = COMPOSE.read_text(encoding="utf-8")
    hostinger_compose = HOSTINGER_COMPOSE.read_text(encoding="utf-8")

    root_default, root_grace = _shutdown_contract(root_compose)
    hostinger_default, hostinger_grace = _shutdown_contract(hostinger_compose)

    assert root_default == DEFAULT_SHUTDOWN_TIMEOUT_SECONDS
    assert hostinger_default == DEFAULT_SHUTDOWN_TIMEOUT_SECONDS
    assert root_grace > root_default
    assert hostinger_grace > hostinger_default
    assert root_grace > MAX_SHUTDOWN_TIMEOUT_SECONDS
    assert hostinger_grace > MAX_SHUTDOWN_TIMEOUT_SECONDS
    assert root_grace == hostinger_grace


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
    assert f"{grace_seconds}-second Compose hard-stop grace period" in normalized
    assert (
        f"maximum supported {MAX_SHUTDOWN_TIMEOUT_SECONDS}-second shutdown/drain budget"
        in normalized
    )


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

    control_plane = _control_plane_block(compose)
    assert "ports:" not in control_plane
    assert 'AI_MAP_SECURE_COOKIE: "true"' in compose


def test_hostinger_runbook_points_to_standalone_compose_file() -> None:
    runbook = (DOCKER_DIR / "README.md").read_text(encoding="utf-8")

    assert "main/deploy/docker/docker-compose.hostinger.yml" in runbook
    assert "repository root" in runbook
    assert "public Git repository itself as the Docker build context" in runbook
