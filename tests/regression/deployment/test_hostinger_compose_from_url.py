from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROFILE = Path("deploy/docker/docker-compose.hostinger-compose-from-url.yml")
ENTRYPOINT = Path("deploy/docker/hostinger-gateway-entrypoint.sh")


@pytest.mark.parametrize("project", ["agents", "test-platform", "ai-multi-agent-platform"])
def test_compose_without_provider_environment(project: str) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose CLI unavailable")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("AI_MAP_", "COMPOSE_", "TRAEFIK_", "VPS_", "HOSTINGER_", "PUBLIC_"))
        and key != "HOSTNAME"
    }
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            os.devnull,
            "-p",
            project,
            "-f",
            str(PROFILE),
            "config",
            "--format",
            "json",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    config = json.loads(result.stdout)
    assert config["name"] == project
    services = config["services"]
    assert set(services) == {"control-plane", "web", "hostinger-gateway"}
    for service in services.values():
        assert not service.get("ports")
        assert not service.get("privileged", False)
        assert service["restart"] == "unless-stopped"
        assert service["healthcheck"]["test"]
        assert "no-new-privileges:true" in service["security_opt"]
        assert service["cap_drop"] == ["ALL"]
    assert services["control-plane"]["environment"]["AI_MAP_SECURE_COOKIE"] == "true"
    compile(services["control-plane"]["healthcheck"]["test"][-1], "<healthcheck>", "exec")
    gateway = services["hostinger-gateway"]
    assert gateway["environment"]["AI_MAP_COMPOSE_PROJECT_NAME"] == project
    assert gateway["uts"] == "host"
    assert gateway["read_only"] is True
    assert gateway["cap_add"] == ["NET_BIND_SERVICE"]
    labels = gateway["labels"]
    pattern = labels[f"traefik.http.routers.{project}-hostinger-http.rule"].split("`")[1]
    assert labels[f"traefik.tcp.routers.{project}-hostinger-tls.rule"].split("`")[1] == pattern
    assert re.fullmatch(pattern, f"{project}.srv123456.hstgr.cloud")
    for foreign in (
        "example.com",
        "other.srv123456.hstgr.cloud",
        f"{project}.srv123456.hstgr.cloud.evil.test",
    ):
        assert re.fullmatch(pattern, foreign) is None
    assert labels[f"traefik.tcp.routers.{project}-hostinger-tls.tls.passthrough"] == "true"
    assert set(config["volumes"]) == {
        "platform-data",
        "hostinger-gateway-data",
        "hostinger-gateway-config",
    }


def test_candidate_has_no_provider_specific_inputs_or_host_access() -> None:
    text = PROFILE.read_text(encoding="utf-8")
    for forbidden in (
        "TRAEFIK_HOST",
        "setup.invalid",
        "docker.sock",
        "privileged:",
        "network_mode: host",
        "ports:",
        "AI_MAP_PUBLIC_DOMAIN",
    ):
        assert forbidden not in text
    assert not re.search(r"srv\d+", text)
    assert set(re.findall(r"[0-9]{1,3}[.][0-9]{1,3}[.][0-9]{1,3}[.][0-9]{1,3}", text)) <= {
        "0.0.0.0",
        "127.0.0.1",
    }
    assert not re.search(r"^name:", text, re.MULTILINE)


@pytest.mark.parametrize("project", ["agents", "test-platform", "ai-multi-agent-platform"])
@pytest.mark.parametrize("hostname", ["srv123456", "srv123456.hstgr.cloud"])
def test_runtime_derives_project_domain(tmp_path: Path, project: str, hostname: str) -> None:
    result = run_gateway(
        tmp_path, {"FAKE_HOSTNAME": hostname, "AI_MAP_COMPOSE_PROJECT_NAME": project}
    )
    assert result.returncode == 0
    assert f"domain={project}.srv123456.hstgr.cloud" in result.stdout
    assert "Caddyfile.hostinger-direct " in result.stdout
    assert "setup-pending" not in result.stdout


@pytest.mark.parametrize(
    "hostname", ["srv", "srvabc", "srv123.evil.test", "srv123.hstgr.cloud.evil.test", "localhost"]
)
def test_runtime_invalid_host_fails_closed(tmp_path: Path, hostname: str) -> None:
    result = run_gateway(
        tmp_path, {"FAKE_HOSTNAME": hostname, "AI_MAP_COMPOSE_PROJECT_NAME": "agents"}
    )
    assert result.returncode == 0
    assert "Caddyfile.hostinger-direct-setup-pending" in result.stdout
    assert "domain=agents." not in result.stdout


@pytest.mark.parametrize("project", ["my_project", "-agents", "agents-", "a" * 64])
def test_runtime_invalid_dns_project_is_rejected(tmp_path: Path, project: str) -> None:
    result = run_gateway(
        tmp_path, {"FAKE_HOSTNAME": "srv123456", "AI_MAP_COMPOSE_PROJECT_NAME": project}
    )
    assert result.returncode == 64


def run_gateway(tmp_path: Path, values: dict[str, str]) -> subprocess.CompletedProcess[str]:
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("POSIX shell unavailable")
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    for name, body in {
        "hostname": 'printf "%s\\n" "$FAKE_HOSTNAME"',
        "caddy": 'printf "domain=%s\\nargs=%s\\n" "$AI_MAP_PUBLIC_DOMAIN" "$*"',
    }.items():
        executable = binary_dir / name
        executable.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        executable.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if not key.startswith("AI_MAP_")}
    env.update(values)
    env["AI_MAP_HOSTINGER_EDGE_MODE"] = "traefik-passthrough"
    directory = binary_dir.as_posix()
    if os.name == "nt":
        directory = "/" + directory[0].lower() + directory[2:]
    env["PATH"] = directory + ":" + env["PATH"]
    return subprocess.run(
        [shell, str(ENTRYPOINT)], env=env, capture_output=True, text=True, check=False
    )
