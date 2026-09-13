from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from ai_multi_agent_platform.adapters.hermes import (
    HERMES_PINNED_REVISION,
    HermesAdapterConfig,
    HermesCompatibilityStatus,
    HermesOrchestrator,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    HealthStatus,
    OperationContext,
    OperationControl,
    PlanRequest,
)
from ai_multi_agent_platform.domain import RunStatus, TaskStatus, new_id
from ai_multi_agent_platform.execution import ExecutorLifecycleBackend, ReferenceExecutor
from ai_multi_agent_platform.kernel import PlatformKernel

HERMES_V0_21_1_REVISION = "2237be355906fbe6065ce1815711eee52b2d646e"
HERMES_V0_21_2_TAG = "v2026.9.11"
HERMES_V0_21_2_REVISION = "939e45c91d751fadd94dcd1b873ac3cb44846213"


def _candidate_upstream() -> Path:
    upstream_value = os.getenv("HERMES_UPSTREAM_DIR")
    if not upstream_value:
        pytest.skip("set HERMES_UPSTREAM_DIR to run the Hermes v0.21.2 candidate tests")
    upstream = Path(upstream_value).resolve()
    if not (upstream / "gateway" / "platforms" / "api_server.py").is_file():
        pytest.fail(f"HERMES_UPSTREAM_DIR is not a Hermes source checkout: {upstream}")
    declared_revision = os.getenv("HERMES_UPSTREAM_REVISION")
    assert declared_revision == HERMES_V0_21_2_REVISION
    return upstream


def _prepare_upstream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    upstream = _candidate_upstream()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.syspath_prepend(str(upstream))
    return upstream


def _request(marker: str) -> PlanRequest:
    return PlanRequest(
        task_id=new_id("task"),
        objective=f"Validate Hermes v0.21.2 candidate {marker}",
        context=OperationContext(
            correlation_id=f"issue-959-{marker}",
            control=OperationControl(
                idempotency_key=f"issue-959-{marker}",
                timeout_seconds=15.0,
            ),
        ),
    )


def _candidate_config(base_url: str) -> HermesAdapterConfig:
    return HermesAdapterConfig(
        enabled=True,
        base_url=base_url,
        pinned_revision=HERMES_V0_21_2_REVISION,
        compatibility_status=HermesCompatibilityStatus.UNVERIFIED_PIN,
        request_timeout_seconds=5.0,
        plan_timeout_seconds=15.0,
        poll_interval_seconds=0.01,
    )


def _planner_output(summary: str) -> str:
    return json.dumps(
        {
            "summary": summary,
            "steps": [
                {
                    "key": "validate",
                    "title": "Validate candidate",
                    "objective": "Keep canonical ownership in the platform",
                    "depends_on": [],
                }
            ],
        }
    )


def _mock_agent(summary: str) -> MagicMock:
    mock_agent = MagicMock()
    mock_agent.run_conversation.return_value = {"final_response": _planner_output(summary)}
    mock_agent.session_prompt_tokens = 0
    mock_agent.session_completion_tokens = 0
    mock_agent.session_total_tokens = 0
    return mock_agent


class BarrierPlannerAgent:
    """Tiny AIAgent stand-in that proves two real /v1/runs execute concurrently."""

    def __init__(self, session_id: str, barrier: threading.Barrier) -> None:
        self.session_id = session_id
        self._barrier = barrier
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0
        self._gateway_turn_process_task_id = None
        self._gateway_turn_process_baseline = None

    def run_conversation(self, *, user_message: str, **_: Any) -> dict[str, Any]:
        self._barrier.wait(timeout=5.0)
        return {"final_response": _planner_output(user_message)}


def test_candidate_is_exact_and_does_not_preemptively_replace_accepted_pin() -> None:
    _candidate_upstream()
    assert HERMES_PINNED_REVISION == HERMES_V0_21_1_REVISION
    candidate = HermesAdapterConfig(
        enabled=True,
        pinned_revision=HERMES_V0_21_2_REVISION,
        compatibility_status=HermesCompatibilityStatus.UNVERIFIED_PIN,
    )
    assert candidate.pinned_revision == HERMES_V0_21_2_REVISION
    assert candidate.compatibility_status is HermesCompatibilityStatus.UNVERIFIED_PIN


def test_candidate_profile_prefix_stays_namespaced_external_routing() -> None:
    orchestrator = HermesOrchestrator(
        HermesAdapterConfig(
            enabled=True,
            base_url="http://127.0.0.1:8642",
            profile="profile with/slash",
            pinned_revision=HERMES_V0_21_2_REVISION,
            compatibility_status=HermesCompatibilityStatus.UNVERIFIED_PIN,
        ),
        secret_resolver=lambda _: None,
    )
    assert orchestrator._base_url == "http://127.0.0.1:8642/p/profile%20with%2Fslash"
    assert "profile" not in orchestrator.descriptor.capabilities[0].attributes


def test_candidate_declares_required_run_lifecycle_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_upstream(tmp_path, monkeypatch)

    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter
    from gateway.platforms.api_server_run_idempotency import TERMINAL_STATUSES

    upstream_adapter = APIServerAdapter(
        PlatformConfig(enabled=True, extra={"host": "127.0.0.1", "port": 0})
    )
    routes = {(method, path) for method, path, _handler in upstream_adapter._http_route_table()}

    assert {
        ("POST", "/v1/runs"),
        ("GET", "/v1/runs/{run_id}"),
        ("GET", "/v1/runs/{run_id}/events"),
        ("POST", "/v1/runs/{run_id}/approval"),
        ("POST", "/v1/runs/{run_id}/steer"),
        ("POST", "/v1/runs/{run_id}/stop"),
    }.issubset(routes)
    assert set(TERMINAL_STATUSES) == {"completed", "failed", "cancelled", "interrupted"}


def test_candidate_startup_auth_and_health_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_upstream(tmp_path, monkeypatch)

    from aiohttp import web
    from aiohttp.test_utils import TestServer, make_mocked_request
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter

    async def scenario() -> None:
        api_key = "issue-959-hermes-key-0123456789abcdef"
        upstream_adapter = APIServerAdapter(
            PlatformConfig(
                enabled=True,
                extra={"host": "127.0.0.1", "port": 0, "key": api_key},
            )
        )
        assert upstream_adapter._api_key_passes_startup_guard() is True

        weak_key_adapter = APIServerAdapter(
            PlatformConfig(
                enabled=True,
                extra={"host": "127.0.0.1", "port": 0, "key": "too-short"},
            )
        )
        assert weak_key_adapter._api_key_passes_startup_guard() is False

        good_request = make_mocked_request(
            "GET",
            "/v1/runs/run_auth",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        bad_request = make_mocked_request(
            "GET",
            "/v1/runs/run_auth",
            headers={"Authorization": "Bearer incorrect-key"},
        )
        assert upstream_adapter._check_auth(good_request) is None
        auth_error = upstream_adapter._check_auth(bad_request)
        assert auth_error is not None
        assert auth_error.status == 401

        routes = {(method, path) for method, path, _handler in upstream_adapter._http_route_table()}
        assert ("GET", "/health") in routes
        assert ("GET", "/health/detailed") in routes

        app = web.Application()
        app["api_server_adapter"] = upstream_adapter
        app.router.add_get("/health", upstream_adapter._handle_health)
        server = TestServer(app)
        await server.start_server()
        try:
            platform_adapter = HermesOrchestrator(
                _candidate_config(str(server.make_url("")).rstrip("/")),
                secret_resolver=lambda name: api_key if name == "API_SERVER_KEY" else None,
            )
            assert await platform_adapter.health() is HealthStatus.HEALTHY
        finally:
            await server.close()

    asyncio.run(scenario())


def test_candidate_adapter_and_recreation_reconcile_same_external_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_upstream(tmp_path, monkeypatch)

    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter

    async def scenario() -> None:
        upstream_adapter = APIServerAdapter(
            PlatformConfig(enabled=True, extra={"host": "127.0.0.1", "port": 0})
        )
        app = web.Application()
        app["api_server_adapter"] = upstream_adapter
        app.router.add_post("/v1/runs", upstream_adapter._handle_runs)
        app.router.add_get("/v1/runs/{run_id}", upstream_adapter._handle_get_run)
        app.router.add_post("/v1/runs/{run_id}/stop", upstream_adapter._handle_stop_run)

        server = TestServer(app)
        await server.start_server()
        try:
            base_url = str(server.make_url("")).rstrip("/")
            request = _request("adapter-recreation")
            before_restart = HermesOrchestrator(
                _candidate_config(base_url),
                secret_resolver=lambda _: None,
            )
            with patch.object(
                upstream_adapter,
                "_create_agent",
                return_value=_mock_agent("Candidate-compatible"),
            ):
                plan = await before_restart.plan(request)

            metadata = plan.adapter_metadata[0].values
            external_run_id = metadata["external_run_id"]
            assert plan.summary == "Candidate-compatible"
            assert metadata["canonical_task_id"] == request.task_id
            assert metadata["upstream_revision"] == HERMES_V0_21_2_REVISION
            assert isinstance(external_run_id, str)
            assert external_run_id != request.task_id

            after_restart = HermesOrchestrator(
                _candidate_config(base_url),
                secret_resolver=lambda _: None,
            )
            reconciled = await after_restart.reconcile_external_run(
                external_run_id,
                OperationContext(
                    correlation_id="issue-959-reconcile",
                    control=OperationControl(timeout_seconds=5.0),
                ),
            )
            assert reconciled.external_run_id == external_run_id
            assert reconciled.status == "completed"
        finally:
            await server.close()

    asyncio.run(scenario())


def test_candidate_kernel_keeps_execution_and_canonical_identity_platform_owned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_upstream(tmp_path, monkeypatch)

    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter

    async def scenario() -> None:
        upstream_adapter = APIServerAdapter(
            PlatformConfig(enabled=True, extra={"host": "127.0.0.1", "port": 0})
        )
        app = web.Application()
        app["api_server_adapter"] = upstream_adapter
        app.router.add_post("/v1/runs", upstream_adapter._handle_runs)
        app.router.add_get("/v1/runs/{run_id}", upstream_adapter._handle_get_run)
        app.router.add_post("/v1/runs/{run_id}/stop", upstream_adapter._handle_stop_run)

        server = TestServer(app)
        await server.start_server()
        try:
            task_id = new_id("task")
            workspace_root = tmp_path / "workspaces"
            workspace = workspace_root / task_id
            workspace.mkdir(parents=True)
            hermes = HermesOrchestrator(
                _candidate_config(str(server.make_url("")).rstrip("/")),
                secret_resolver=lambda _: None,
            )
            lifecycle = ExecutorLifecycleBackend(
                ReferenceExecutor(workspace_root),
                workspace=task_id,
                action="write_artifact",
            )
            kernel = PlatformKernel(orchestrator=hermes, lifecycle=lifecycle)

            with patch.object(
                upstream_adapter,
                "_create_agent",
                return_value=_mock_agent("Candidate kernel plan"),
            ):
                await kernel.create_task(
                    idempotency_key="issue-959:create",
                    task_id=task_id,
                    title="Hermes v0.21.2 candidate",
                    objective="Plan through candidate Hermes and execute canonically",
                    owner_type="user",
                    owner_id="issue-959",
                )
                await kernel.ready_task(idempotency_key="issue-959:ready", task_id=task_id)
                run = await kernel.start_task(
                    idempotency_key="issue-959:start",
                    task_id=task_id,
                )
                run = await kernel.refresh_run(
                    idempotency_key="issue-959:refresh",
                    task_id=task_id,
                    run_id=run.run_id,
                )

            task = await kernel.get_task(task_id)
            history = await kernel.history(task_id)
            plan_event = next(event for event in history if event.event_type == "plan.created")
            adapter_metadata = plan_event.payload["adapter_metadata"]
            assert isinstance(adapter_metadata, Mapping)
            hermes_metadata = adapter_metadata["hermes"]
            assert isinstance(hermes_metadata, Mapping)
            external_run_id = hermes_metadata["external_run_id"]

            assert run.status is RunStatus.SUCCEEDED
            assert task.status is TaskStatus.SUCCEEDED
            assert (workspace / "artifact.txt").exists()
            assert hermes_metadata["canonical_task_id"] == task_id
            assert hermes_metadata["upstream_revision"] == HERMES_V0_21_2_REVISION
            assert isinstance(external_run_id, str)
            assert external_run_id.startswith("run_")
            assert run.run_id != external_run_id
        finally:
            await server.close()

    asyncio.run(scenario())


def test_parallel_candidate_runs_keep_results_and_external_ids_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_upstream(tmp_path, monkeypatch)

    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter

    async def scenario() -> None:
        upstream_adapter = APIServerAdapter(
            PlatformConfig(enabled=True, extra={"host": "127.0.0.1", "port": 0})
        )
        app = web.Application()
        app["api_server_adapter"] = upstream_adapter
        app.router.add_post("/v1/runs", upstream_adapter._handle_runs)
        app.router.add_get("/v1/runs/{run_id}", upstream_adapter._handle_get_run)
        app.router.add_post("/v1/runs/{run_id}/stop", upstream_adapter._handle_stop_run)

        server = TestServer(app)
        await server.start_server()
        try:
            barrier = threading.Barrier(2)

            def make_agent(**kwargs: Any) -> BarrierPlannerAgent:
                return BarrierPlannerAgent(
                    str(kwargs.get("session_id") or "missing"),
                    barrier,
                )

            config = _candidate_config(str(server.make_url("")).rstrip("/"))
            first_request = _request("parallel-alpha")
            second_request = _request("parallel-beta")

            with patch.object(upstream_adapter, "_create_agent", side_effect=make_agent):
                first, second = await asyncio.gather(
                    HermesOrchestrator(config, secret_resolver=lambda _: None).plan(first_request),
                    HermesOrchestrator(config, secret_resolver=lambda _: None).plan(second_request),
                )

            assert "parallel-alpha" in first.summary
            assert "parallel-beta" in second.summary
            first_meta = first.adapter_metadata[0]
            second_meta = second.adapter_metadata[0]
            assert first_meta.namespace == second_meta.namespace == "hermes"
            assert first_meta.values["canonical_task_id"] == first_request.task_id
            assert second_meta.values["canonical_task_id"] == second_request.task_id
            assert first_meta.values["external_run_id"] != second_meta.values["external_run_id"]
            assert first_meta.values["upstream_revision"] == HERMES_V0_21_2_REVISION
            assert second_meta.values["upstream_revision"] == HERMES_V0_21_2_REVISION
        finally:
            await server.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        ("cancelled", ErrorCode.CANCELLED),
        ("interrupted", ErrorCode.CANCELLED),
        ("waiting_for_approval", ErrorCode.FORBIDDEN),
        ("future_unknown_status", ErrorCode.BACKEND_ERROR),
    ],
)
def test_candidate_status_mapping_remains_fail_closed(
    status: str,
    expected_code: ErrorCode,
) -> None:
    class Transport:
        async def request_json(
            self,
            method: str,
            url: str,
            *,
            payload: Any,
            headers: Any,
            timeout_seconds: float,
        ):
            from ai_multi_agent_platform.adapters.hermes import HermesHttpResponse

            del url, payload, headers, timeout_seconds
            if method == "POST":
                return HermesHttpResponse(202, {"run_id": "run_issue959", "status": "queued"})
            return HermesHttpResponse(200, {"run_id": "run_issue959", "status": status})

    async def scenario() -> None:
        orchestrator = HermesOrchestrator(
            HermesAdapterConfig(
                enabled=True,
                pinned_revision=HERMES_V0_21_2_REVISION,
                compatibility_status=HermesCompatibilityStatus.UNVERIFIED_PIN,
                poll_interval_seconds=0.001,
            ),
            transport=Transport(),
            secret_resolver=lambda _: None,
        )
        with pytest.raises(ContractError) as error:
            await orchestrator.plan(_request(f"status-{status}"))
        assert error.value.code is expected_code
        assert (
            error.value.adapter_metadata[0].values["upstream_revision"] == HERMES_V0_21_2_REVISION
        )

    asyncio.run(scenario())
