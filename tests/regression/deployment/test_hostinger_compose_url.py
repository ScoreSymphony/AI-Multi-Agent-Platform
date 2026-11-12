"""Contract for the single-step Hostinger Compose URL candidate."""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DOCKER = ROOT / "deploy" / "docker"


def test_hostinger_compose_url_keeps_application_private() -> None:
    compose = (DOCKER / "docker-compose.hostinger-install.yml").read_text(encoding="utf-8")
    assert compose.count("dockerfile: deploy/docker/") == 3
    assert 'AI_MAP_SECURE_COOKIE: "true"' in compose
    assert "AI_MAP_HOSTINGER_EDGE_MODE: traefik-alpn" in compose
    assert "uts: host" in compose
    assert "platform-data:/var/lib/ai-multi-agent-platform" in compose
    assert "ports:" not in compose
    assert "/var/run/docker.sock" not in compose
    assert "TRAEFIK_HOST" not in compose
    assert "setup.invalid" not in compose
    assert "HostRegexp(" in compose
    assert "HostSNIRegexp(" in compose
    assert "HostSNI(`*`)" not in compose
    assert "srv1887380" not in compose
    assert "healthcheck:" in compose.split("  hostinger-gateway:", 1)[1]


@pytest.mark.parametrize("project", ["agents", "test-platform"])
def test_compose_renders_without_vps_environment(project: str) -> None:
    environment = os.environ.copy()
    for key in ("TRAEFIK_HOST", "VPS_ID", "PUBLIC_IP", "AI_MAP_PUBLIC_DOMAIN"):
        environment.pop(key, None)
    environment["COMPOSE_PROJECT_NAME"] = project
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(DOCKER / "docker-compose.hostinger-install.yml"),
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    config = json.loads(result.stdout)
    services = config["services"]
    assert set(services) == {"control-plane", "web", "hostinger-gateway"}
    assert all("ports" not in service for service in services.values())
    assert services["control-plane"]["environment"]["AI_MAP_SECURE_COOKIE"] == "true"
    labels = services["hostinger-gateway"]["labels"]
    assert project in labels[f"traefik.tcp.routers.{project}-hostinger-tls.rule"]
    assert config["volumes"]["platform-data"]["name"] == f"{project}_platform-data"


def test_hostinger_gateway_uses_tls_alpn_only_for_compose_url() -> None:
    entrypoint = (DOCKER / "hostinger-gateway-entrypoint.sh").read_text(encoding="utf-8")
    caddyfile = (DOCKER / "Caddyfile.hostinger-compose-url").read_text(encoding="utf-8")
    dockerfile = (DOCKER / "hostinger-gateway.Dockerfile").read_text(encoding="utf-8")
    assert "traefik-alpn" in entrypoint
    assert "Caddyfile.hostinger-compose-url" in entrypoint
    assert "Caddyfile.hostinger-compose-url" in dockerfile
    assert "disable_http_challenge" in caddyfile
    assert "{$AI_MAP_PUBLIC_DOMAIN}" in caddyfile
    assert "http://{$AI_MAP_PUBLIC_DOMAIN}" in caddyfile
    assert "http:// {" not in caddyfile
    assert ":8081" in caddyfile


@pytest.mark.parametrize(
    ("hostname", "expected"),
    [
        ("srv123456", "agents.srv123456.hstgr.cloud"),
        ("srv123456.hstgr.cloud", "agents.srv123456.hstgr.cloud"),
        ("srv123456.other.example", None),
        ("srvabc", None),
    ],
)
@pytest.mark.skipif(os.name == "nt", reason="POSIX command stubs require Linux")
def test_runtime_gateway_normalizes_or_rejects_hostnames(
    tmp_path: Path, hostname: str, expected: str | None
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "hostname").write_text(f"#!/bin/sh\nprintf '%s\\n' '{hostname}'\n", encoding="utf-8")
    (bin_dir / "caddy").write_text(
        '#!/bin/sh\nprintf \'%s %s\\n\' "$AI_MAP_PUBLIC_DOMAIN" "$*"\n',
        encoding="utf-8",
    )
    (bin_dir / "hostname").chmod(0o755)
    (bin_dir / "caddy").chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "AI_MAP_HOSTINGER_EDGE_MODE": "traefik-alpn",
        "AI_MAP_COMPOSE_PROJECT_NAME": "agents",
    }
    result = subprocess.run(
        ["sh", str(DOCKER / "hostinger-gateway-entrypoint.sh")],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if expected is None:
        assert result.returncode == 64
        assert "validated Hostinger hostname" in result.stderr
    else:
        assert result.returncode == 0
        assert expected in result.stdout
        assert "Caddyfile.hostinger-compose-url" in result.stderr + result.stdout
