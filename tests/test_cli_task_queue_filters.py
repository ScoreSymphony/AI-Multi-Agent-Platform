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


class ControlPlaneTransport:
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


def _transport() -> ControlPlaneTransport:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(kernel=kernel, events=repository)
    return ControlPlaneTransport(ControlPlaneHTTP(control_plane))


def _invoke(
    config: Path,
    transport: ControlPlaneTransport,
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


def _create_task(config: Path, transport: ControlPlaneTransport, title: str) -> str:
    code, payload, error = _invoke(
        config,
        transport,
        "task",
        "create",
        "--title",
        title,
        "--objective",
        "Exercise canonical queue filtering",
        "--owner-type",
        "user",
        "--owner-id",
        "cli-filter-test",
    )
    assert code == 0 and not error
    task = payload["data"]
    assert isinstance(task, dict)
    task_id = task["id"]
    assert isinstance(task_id, str)
    return task_id


def _update(
    config: Path,
    transport: ControlPlaneTransport,
    task_id: str,
    changes: dict[str, Any],
    key: str,
) -> None:
    code, _, error = _invoke(
        config,
        transport,
        "--yes",
        "task",
        "update-management",
        task_id,
        "--changes-json",
        json.dumps(changes, separators=(",", ":")),
        "--idempotency-key",
        key,
    )
    assert code == 0 and not error


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload["data"]
    assert isinstance(data, dict)
    items = data["items"]
    assert isinstance(items, list)
    assert all(isinstance(item, dict) for item in items)
    return items


def test_task_list_filters_use_canonical_task_management_queue_contract(tmp_path: Path) -> None:
    config = tmp_path / "cli.json"
    transport = _transport()
    assigned_due_soon = _create_task(config, transport, "Assigned due soon")
    unassigned_due_later = _create_task(config, transport, "Unassigned due later")
    no_deadline = _create_task(config, transport, "No deadline")

    _update(
        config,
        transport,
        assigned_due_soon,
        {
            "due_at": "2026-09-04T12:00:00+00:00",
            "responsibility": {"kind": "user", "id": "alice"},
        },
        "queue-filter-assigned",
    )
    _update(
        config,
        transport,
        unassigned_due_later,
        {"due_at": "2026-09-10T12:00:00+00:00"},
        "queue-filter-unassigned",
    )

    code, payload, error = _invoke(
        config,
        transport,
        "task",
        "list",
        "--filter",
        "due_after=2026-09-03T00:00:00+00:00",
        "--filter",
        "due_before=2026-09-05T00:00:00+00:00",
        "--filter",
        "assignment_state=assigned",
    )
    assert code == 0 and not error
    assert [item["id"] for item in _items(payload)] == [assigned_due_soon]

    code, payload, error = _invoke(
        config,
        transport,
        "task",
        "list",
        "--filter",
        "assignment_state=unassigned",
    )
    assert code == 0 and not error
    assert {item["id"] for item in _items(payload)} == {unassigned_due_later, no_deadline}


def test_task_list_invalid_deadline_range_is_rejected_by_control_plane(tmp_path: Path) -> None:
    config = tmp_path / "cli.json"
    transport = _transport()
    _create_task(config, transport, "Range validation")

    code, payload, error = _invoke(
        config,
        transport,
        "task",
        "list",
        "--filter",
        "due_after=2026-09-10T00:00:00+00:00",
        "--filter",
        "due_before=2026-09-05T00:00:00+00:00",
    )

    assert code == 3
    assert payload == {}
    assert "due_after" in error
    assert "due_before" in error
