from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import (
    ControlPlane,
    ControlPlaneHTTP,
    HTTPRequest,
    build_openapi,
)
from ai_multi_agent_platform.control_plane.models import api_exception_from_contract
from ai_multi_agent_platform.domain import RunStatus
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def _headers(key: str | None = None) -> dict[str, str]:
    headers = {
        "content-type": "application/json",
        "x-principal-ref": "user:test",
        "x-owner-type": "user",
        "x-owner-id": "test",
    }
    if key is not None:
        headers["idempotency-key"] = key
    return headers


async def _started_run() -> tuple[PlatformKernel, ControlPlaneHTTP, str, str]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(kernel=kernel, events=repository)
    http = ControlPlaneHTTP(control_plane)

    created = await http.handle(
        HTTPRequest(
            method="POST",
            path="/api/v1/tasks",
            headers=_headers("create-run-error-task"),
            body={
                "title": "Run error contract",
                "objective": "Verify failed run inspection",
                "owner_type": "user",
                "owner_id": "test",
            },
        )
    )
    assert created.status == 201
    assert isinstance(created.body, dict)
    task_id = created.body["id"]
    assert isinstance(task_id, str)

    queued = await http.handle(
        HTTPRequest(
            method="POST",
            path=f"/api/v1/tasks/{task_id}:queue",
            headers=_headers("queue-run-error-task"),
        )
    )
    assert queued.status == 200

    started = await http.handle(
        HTTPRequest(
            method="POST",
            path=f"/api/v1/tasks/{task_id}:start",
            headers=_headers("start-run-error-task"),
        )
    )
    assert started.status == 200
    assert isinstance(started.body, dict)
    assert started.body["error"] is None
    run_id = started.body["id"]
    assert isinstance(run_id, str)
    return kernel, http, task_id, run_id


def test_failed_run_exposes_stable_canonical_error_in_read_and_list() -> None:
    async def scenario() -> None:
        kernel, http, task_id, run_id = await _started_run()
        await kernel.record_run_outcome(
            idempotency_key="fail-run-error-task",
            task_id=task_id,
            run_id=run_id,
            status=RunStatus.FAILED,
            output={"error": {"message": "executor rejected request"}},
        )

        loaded = await http.handle(HTTPRequest(method="GET", path=f"/api/v1/runs/{run_id}"))
        assert loaded.status == 200
        assert isinstance(loaded.body, dict)
        assert loaded.body["error"] == {
            "code": "run_failed",
            "category": "execution",
            "message": "executor rejected request",
            "retryable": False,
        }

        listed = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/runs",
                query={"filter[status]": "failed", "fields": "status,error"},
            )
        )
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
        await kernel.record_run_outcome(
            idempotency_key="timeout-run-error-task",
            task_id=task_id,
            run_id=run_id,
            status=RunStatus.TIMED_OUT,
            output={"reason": "executor deadline exceeded"},
        )

        loaded = await http.handle(
            HTTPRequest(method="GET", path=f"/api/v1/tasks/{task_id}/runs/{run_id}")
        )
        assert loaded.status == 200
        assert isinstance(loaded.body, dict)
        assert loaded.body["error"] == {
            "code": "run_timed_out",
            "category": "timeout",
            "message": "executor deadline exceeded",
            "retryable": True,
        }

    asyncio.run(scenario())


def test_generated_openapi_documents_run_error_contract() -> None:
    async def scenario() -> None:
        _, http, _, _ = await _started_run()
        response = await http.handle(HTTPRequest(method="GET", path="/api/v1/openapi.json"))
        assert response.status == 200
        assert isinstance(response.body, dict)
        assert "x-run-error-contract" in response.body
        components = response.body["components"]
        assert isinstance(components, dict)
        schemas = components["schemas"]
        assert isinstance(schemas, dict)
        assert "RunError" in schemas
        assert "Run" in schemas
        assert "RunPage" in schemas

    asyncio.run(scenario())

    specification = build_openapi()
    paths = specification["paths"]
    assert (
        paths["/api/v1/runs/{run_id}"]["get"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]["$ref"]
        == "#/components/schemas/Run"
    )


def test_all_current_contract_error_codes_have_intentional_http_mapping() -> None:
    expected = {
        ErrorCode.INVALID_CONFIGURATION: 422,
        ErrorCode.MODEL_UNAVAILABLE: 503,
        ErrorCode.NO_COMPATIBLE_ROUTE: 503,
        ErrorCode.INPUT_TOO_LARGE: 413,
        ErrorCode.INVALID_PROVIDER_RESPONSE: 502,
    }
    for code, status in expected.items():
        error = api_exception_from_contract(ContractError(code, f"test {code.value}"))
        assert error.status == status
        assert error.code == code.value
