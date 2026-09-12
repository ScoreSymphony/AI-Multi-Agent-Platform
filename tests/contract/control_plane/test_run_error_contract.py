from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest, build_openapi
from ai_multi_agent_platform.control_plane.models import api_exception_from_contract
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
    run_id = started.body["id"]
    assert isinstance(run_id, str)
    return kernel, http, task_id, run_id


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
    assert paths["/api/v1/runs/{run_id}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/Run"


def test_all_current_contract_error_codes_have_intentional_http_mapping() -> None:
    expected = {ErrorCode.INVALID_CONFIGURATION: 422, ErrorCode.MODEL_UNAVAILABLE: 503, ErrorCode.NO_COMPATIBLE_ROUTE: 503, ErrorCode.INPUT_TOO_LARGE: 413, ErrorCode.INVALID_PROVIDER_RESPONSE: 502}
    for code, status in expected.items():
        error = api_exception_from_contract(ContractError(code, f"test {code.value}"))
        assert error.status == status
        assert error.code == code.value
