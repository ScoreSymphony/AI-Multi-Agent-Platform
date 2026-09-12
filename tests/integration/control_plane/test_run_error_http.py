from __future__ import annotations

import asyncio

from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import RunStatus
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def _headers(key: str | None = None) -> dict[str, str]:
    headers = {"content-type": "application/json", "x-principal-ref": "user:test", "x-owner-type": "user", "x-owner-id": "test"}
    if key is not None:
        headers["idempotency-key"] = key
    return headers


async def _started_run() -> tuple[PlatformKernel, ControlPlaneHTTP, str, str]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(orchestrator=FakeOrchestrator(), lifecycle=FakeLifecycleBackend(), repository=repository)
    http = ControlPlaneHTTP(ControlPlane(kernel=kernel, events=repository))
    created = await http.handle(HTTPRequest(method="POST", path="/api/v1/tasks", headers=_headers("create-run-error-task"), body={"title": "Run error contract", "objective": "Verify failed run inspection", "owner_type": "user", "owner_id": "test"}))
    assert created.status == 201
    assert isinstance(created.body, dict)
    task_id = created.body["id"]
    assert isinstance(task_id, str)
    queued = await http.handle(HTTPRequest(method="POST", path=f"/api/v1/tasks/{task_id}:queue", headers=_headers("queue-run-error-task")))
    assert queued.status == 200
    started = await http.handle(HTTPRequest(method="POST", path=f"/api/v1/tasks/{task_id}:start", headers=_headers("start-run-error-task")))
    assert started.status == 200
    assert isinstance(started.body, dict)
    assert started.body["error"] is None
    run_id = started.body["id"]
    assert isinstance(run_id, str)
    return kernel, http, task_id, run_id


def test_failed_run_exposes_stable_canonical_error_in_read_and_list() -> None:
    async def scenario() -> None:
        kernel, http, task_id, run_id = await _started_run()
        await kernel.record_run_outcome(idempotency_key="fail-run-error-task", task_id=task_id, run_id=run_id, status=RunStatus.FAILED, output={"error": {"message": "executor rejected request"}})
        loaded = await http.handle(HTTPRequest(method="GET", path=f"/api/v1/runs/{run_id}"))
        assert loaded.status == 200
        assert isinstance(loaded.body, dict)
        assert loaded.body["error"] == {"code": "run_failed", "category": "execution", "message": "executor rejected request", "retryable": False}
        listed = await http.handle(HTTPRequest(method="GET", path="/api/v1/runs", query={"filter[status]": "failed", "fields": "status,error"}))
        assert listed.status == 200
        assert isinstance(listed.body, dict)
        items = listed.body["items"]
        assert isinstance(items, list)
        assert len(items) == 1
        item = items[0]
        assert isinstance(item, dict)
        assert item["status"] == "failed"
        assert item["error"] == loaded.body["error"]
    asyncio.run(scenario())


def test_timed_out_run_exposes_retryable_timeout_error() -> None:
    async def scenario() -> None:
        kernel, http, task_id, run_id = await _started_run()
        await kernel.record_run_outcome(idempotency_key="timeout-run-error-task", task_id=task_id, run_id=run_id, status=RunStatus.TIMED_OUT, output={"reason": "executor deadline exceeded"})
        loaded = await http.handle(HTTPRequest(method="GET", path=f"/api/v1/tasks/{task_id}/runs/{run_id}"))
        assert loaded.status == 200
        assert isinstance(loaded.body, dict)
        assert loaded.body["error"] == {"code": "run_timed_out", "category": "timeout", "message": "executor deadline exceeded", "retryable": True}
    asyncio.run(scenario())
