from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import RunStatus
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


class InProcessTransport:
    def __init__(self, http: ControlPlaneHTTP) -> None:
        self.http = http

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RawResponse:
        del timeout
        parsed = urlsplit(url)
        decoded: dict[str, Any] = {}
        if body:
            loaded = json.loads(body.decode("utf-8"))
            assert isinstance(loaded, dict)
            decoded = loaded
        response = asyncio.run(
            self.http.handle(
                HTTPRequest(
                    method=method,
                    path=parsed.path,
                    headers=headers,
                    query=dict(parse_qsl(parsed.query)),
                    body=decoded,
                )
            )
        )
        return RawResponse(
            status=response.status,
            body=json.dumps(response.body, default=str).encode("utf-8"),
            headers=response.headers,
        )


def _stack() -> tuple[PlatformKernel, InProcessTransport]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(kernel=kernel, events=repository)
    return kernel, InProcessTransport(ControlPlaneHTTP(control_plane))


def _invoke(
    config: Path,
    transport: InProcessTransport,
    *arguments: str,
) -> tuple[int, dict[str, Any], str]:
    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        ["--config", str(config), "--json", *arguments],
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )
    payload = json.loads(stdout.getvalue()) if stdout.getvalue() else {}
    assert isinstance(payload, dict)
    return exit_code, payload, stderr.getvalue()


def test_cli_task_run_and_timeline_flow_uses_control_plane_only(tmp_path: Path) -> None:
    config = tmp_path / "cli.json"
    kernel, transport = _stack()

    code, created, error = _invoke(
        config,
        transport,
        "task",
        "create",
        "--title",
        "CLI task",
        "--objective",
        "Exercise canonical CLI",
        "--owner-type",
        "user",
        "--owner-id",
        "test",
    )
    assert code == 0 and not error
    task = created["data"]
    assert isinstance(task, dict)
    task_id = task["id"]
    assert isinstance(task_id, str)

    code, listed, _ = _invoke(config, transport, "task", "list")
    assert code == 0
    assert listed["data"]["total"] == 1

    code, shown, _ = _invoke(config, transport, "task", "show", task_id)
    assert code == 0
    assert shown["data"]["id"] == task_id

    assert _invoke(config, transport, "task", "queue", task_id)[0] == 0
    code, started, _ = _invoke(config, transport, "task", "start", task_id)
    assert code == 0
    run = started["data"]
    assert isinstance(run, dict)
    run_id = run["id"]
    assert isinstance(run_id, str)

    code, run_list, _ = _invoke(config, transport, "run", "list", "--task-id", task_id)
    assert code == 0
    assert run_list["data"]["total"] == 1
    code, run_show, _ = _invoke(config, transport, "run", "show", run_id)
    assert code == 0
    assert run_show["data"]["id"] == run_id

    asyncio.run(
        kernel.record_run_outcome(
            idempotency_key="cli-test-failure",
            task_id=task_id,
            run_id=run_id,
            status=RunStatus.FAILED,
        )
    )
    code, retried, _ = _invoke(config, transport, "task", "retry", task_id)
    assert code == 0
    assert retried["data"]["attempt"] == 2

    code, timeline, _ = _invoke(config, transport, "task", "timeline", task_id)
    assert code == 0
    assert timeline["data"]["total"] > 0
    assert timeline["meta"]["request_id"].startswith("request_")
    assert timeline["meta"]["correlation_id"].startswith("corr_")


def test_cli_status_doctor_project_workspace_and_canonical_error_output(tmp_path: Path) -> None:
    config = tmp_path / "cli.json"
    _, transport = _stack()

    code, status, _ = _invoke(config, transport, "status")
    assert code == 0
    assert status["data"]["api_version"] == "v1"

    code, doctor, _ = _invoke(config, transport, "doctor")
    assert code == 0
    assert doctor["data"]["summary"] == "healthy"

    code, project, _ = _invoke(
        config,
        transport,
        "project",
        "create",
        "--name",
        "CLI project",
        "--owner-type",
        "user",
        "--owner-id",
        "test",
    )
    assert code == 0
    project_id = project["data"]["id"]
    assert isinstance(project_id, str)

    code, workspace, _ = _invoke(
        config,
        transport,
        "workspace",
        "create",
        "--project-id",
        project_id,
    )
    assert code == 0
    assert workspace["data"]["project_id"] == project_id

    stdout = StringIO()
    stderr = StringIO()
    missing_task_id = "task_00000000-0000-0000-0000-000000000001"
    code = run_cli(
        ["--config", str(config), "--json", "task", "show", missing_task_id],
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )
    assert code == 3
    assert not stdout.getvalue()
    error = json.loads(stderr.getvalue())
    assert error["code"] == "not_found"
    assert error["category"] == "resource"
    assert error["request_id"].startswith("request_")
    assert error["correlation_id"].startswith("corr_")
