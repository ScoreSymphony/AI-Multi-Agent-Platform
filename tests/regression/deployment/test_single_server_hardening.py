from __future__ import annotations

from pathlib import Path

from ai_multi_agent_platform.deployment import load_single_node_config

DEPLOY_DIR = Path("deploy/single-server")


def _active_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        result[key] = value
    return result


def test_single_server_example_keeps_control_plane_private_and_secret_free() -> None:
    environ = _active_env(DEPLOY_DIR / "single-server.env.example")
    config = load_single_node_config(environ)

    assert config.host == "127.0.0.1"
    assert config.port == 8000
    assert config.secure_cookie is True
    assert config.data_dir == Path("/var/lib/ai-multi-agent-platform")

    sensitive_markers = ("password", "secret", "token", "api_key", "apikey", "credential")
    assert not any(marker in key.casefold() for key in environ for marker in sensitive_markers)


def test_single_server_proxy_preserves_canonical_api_prefix_and_spa_fallback() -> None:
    caddy = (DEPLOY_DIR / "Caddyfile.example").read_text(encoding="utf-8")

    assert "handle /api/*" in caddy
    assert "handle_path /api" not in caddy
    assert "reverse_proxy 127.0.0.1:8000" in caddy
    assert "reverse_proxy 0.0.0.0" not in caddy
    assert "try_files {path} /index.html" in caddy
    assert "file_server" in caddy


def test_single_server_service_example_enforces_least_privilege_boundary() -> None:
    unit = (DEPLOY_DIR / "platform-control-plane.service.example").read_text(encoding="utf-8")

    required = (
        "User=ai-map",
        "Group=ai-map",
        "UMask=0077",
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "ProtectSystem=strict",
        "ProtectHome=true",
        "RestrictSUIDSGID=true",
        "ReadWritePaths=/var/lib/ai-multi-agent-platform",
        "ExecStart=/opt/ai-multi-agent-platform/.venv/bin/platform-server serve",
    )
    for directive in required:
        assert directive in unit

    assert "User=root" not in unit
    assert "0.0.0.0:8000" not in unit


def test_single_server_reference_keeps_frontend_and_proxy_optional() -> None:
    runbook = (DEPLOY_DIR / "README.md").read_text(encoding="utf-8")

    assert "frontend and reverse proxy are optional" in runbook
    assert "platform-server serve" in runbook
    assert "platform-server smoke" in runbook
    assert "platform --endpoint http://127.0.0.1:8000 doctor" in runbook
    assert "/api/v1/readiness" in runbook
    assert "#14" in runbook
