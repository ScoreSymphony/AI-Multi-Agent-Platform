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
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


class RecordingTransport:
    def __init__(self, http: ControlPlaneHTTP) -> None:
        self.http = http
        self.calls: list[tuple[str, str, dict[str, str], dict[str, Any]]] = []

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
        self.calls.append((method, parsed.path, dict(headers), decoded))
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


def _transport() -> RecordingTransport:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(kernel=kernel, events=repository)
    return RecordingTransport(ControlPlaneHTTP(control_plane))


def _invoke(
    config: Path,
    transport: RecordingTransport,
    *arguments: str,
) -> tuple[int, dict[str, Any], str]:
    stdout = StringIO()
    stderr = StringIO()
    code = run_cli(
        ["--config", str(config), "--json", *arguments],
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )
    payload = json.loads(stdout.getvalue()) if stdout.getvalue() else {}
    assert isinstance(payload, dict)
    return code, payload, stderr.getvalue()


def _create_task(config: Path, transport: RecordingTransport, title: str) -> str:
    code, payload, error = _invoke(
        config,
        transport,
        "task",
        "create",
        "--title",
        title,
        "--objective",
        "Exercise canonical Task management",
        "--owner-type",
        "user",
        "--owner-id",
        "cli-test",
    )
    assert code == 0 and not error
    task = payload["data"]
    assert isinstance(task, dict)
    task_id = task["id"]
    assert isinstance(task_id, str)
    return task_id


def test_task_management_update_requires_confirmation_and_uses_native_command(
    tmp_path: Path,
) -> None:
    config = tmp_path / "cli.json"
    transport = _transport()
    task_id = _create_task(config, transport, "Managed CLI task")
    calls_before = len(transport.calls)

    code, payload, error = _invoke(
        config,
        transport,
        "task",
        "update-management",
        task_id,
        "--changes-json",
        '{"priority":"urgent"}',
    )
    assert code == 2
    assert payload == {}
    assert "requires confirmation" in error
    assert len(transport.calls) == calls_before

    code, payload, error = _invoke(
        config,
        transport,
        "--yes",
        "task",
        "update-management",
        task_id,
        "--changes-json",
        '{"priority":"urgent","labels":["cli","managed"]}',
        "--idempotency-key",
        "cli-task-management-update",
    )
    assert code == 0 and not error
    assert payload["data"]["priority"] == "urgent"
    assert payload["data"]["labels"] == ["cli", "managed"]

    method, path, headers, body = transport.calls[-1]
    assert method == "POST"
    assert path == "/api/v1/commands/task-management.update"
    assert headers["idempotency-key"] == "cli-task-management-update"
    assert body == {
        "resource_ref": task_id,
        "priority": "urgent",
        "labels": ["cli", "managed"],
    }


def test_task_management_bulk_update_uses_authorization_preflight_contract(
    tmp_path: Path,
) -> None:
    config = tmp_path / "cli.json"
    transport = _transport()
    first = _create_task(config, transport, "First managed task")
    second = _create_task(config, transport, "Second managed task")
    updates = json.dumps(
        [
            {"task_id": first, "changes": {"priority": "high"}},
            {"task_id": second, "changes": {"archived": True}},
        ],
        separators=(",", ":"),
    )

    code, payload, error = _invoke(
        config,
        transport,
        "--yes",
        "task",
        "bulk-update-management",
        "--updates-json",
        updates,
        "--idempotency-key",
        "cli-task-management-bulk",
    )

    assert code == 0 and not error
    result = payload["data"]
    assert result["type"] == "task-management-bulk-result"
    assert result["atomic"] is False
    assert result["authorization_preflighted"] is True
    assert result["count"] == 2

    method, path, headers, body = transport.calls[-1]
    assert method == "POST"
    assert path == "/api/v1/commands/task-management.bulk-update"
    assert headers["idempotency-key"] == "cli-task-management-bulk"
    assert body == {"resource_ref": "tasks", "updates": json.loads(updates)}

    assert _invoke(config, transport, "task", "show", first)[1]["data"]["priority"] == "high"
    assert _invoke(config, transport, "task", "show", second)[1]["data"]["archived"] is True


def test_task_management_json_shape_errors_are_local(tmp_path: Path) -> None:
    config = tmp_path / "cli.json"
    transport = _transport()
    task_id = _create_task(config, transport, "Invalid payload task")
    calls_before = len(transport.calls)

    code, payload, error = _invoke(
        config,
        transport,
        "--yes",
        "task",
        "update-management",
        task_id,
        "--changes-json",
        "[]",
    )
    assert code == 2
    assert payload == {}
    assert "non-empty JSON object" in error
    assert len(transport.calls) == calls_before

    code, payload, error = _invoke(
        config,
        transport,
        "--yes",
        "task",
        "bulk-update-management",
        "--updates-json",
        "{}",
    )
    assert code == 2
    assert payload == {}
    assert "non-empty JSON array" in error
    assert len(transport.calls) == calls_before
