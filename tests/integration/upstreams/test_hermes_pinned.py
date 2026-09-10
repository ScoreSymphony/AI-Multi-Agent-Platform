from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_multi_agent_platform.adapters.hermes import (
    HERMES_PINNED_REVISION,
    HermesAdapterConfig,
    HermesOrchestrator,
)
from ai_multi_agent_platform.contracts import OperationContext, OperationControl, PlanRequest
from ai_multi_agent_platform.domain import RunStatus, TaskStatus, new_id
from ai_multi_agent_platform.execution import ExecutorLifecycleBackend, ReferenceExecutor
from ai_multi_agent_platform.kernel import PlatformKernel


def _pinned_upstream() -> Path:
    upstream_value = os.getenv("HERMES_UPSTREAM_DIR")
    if not upstream_value:
        pytest.skip("set HERMES_UPSTREAM_DIR to run the pinned Hermes compatibility test")

    upstream = Path(upstream_value).resolve()
    if not (upstream / "gateway" / "platforms" / "api_server.py").is_file():
        pytest.fail(f"HERMES_UPSTREAM_DIR is not a Hermes source checkout: {upstream}")

    expected_revision = os.getenv("HERMES_UPSTREAM_REVISION")
    if expected_revision != HERMES_PINNED_REVISION:
        pytest.fail(
            "pinned Hermes compatibility test must declare the exact adapter revision: "
            f"expected {HERMES_PINNED_REVISION}, got {expected_revision!r}"
        )
    return upstream


def _mock_agent() -> MagicMock:
    mock_agent = MagicMock()
    mock_agent.run_conversation.return_value = {
        "final_response": json.dumps(
            {
                "summary": "Pinned Hermes compatibility",
                "steps": [
                    {
                        "key": "execute",
                        "title": "Execute canonically",
                        "objective": "Keep execution outside Hermes",
                        "depends_on": [],
                    }
                ],
            }
        )
    }
    mock_agent.session_prompt_tokens = 0
    mock_agent.session_completion_tokens = 0
    mock_agent.session_total_tokens = 0
    return mock_agent


def test_adapter_against_pinned_hermes_runs_api() -> None:
    upstream = _pinned_upstream()
    sys.path.insert(0, str(upstream))

    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter

    async def scenario() -> None:
        upstream_adapter = APIServerAdapter(
            PlatformConfig(
                enabled=True,
                extra={"host": "127.0.0.1", "port": 0},
            )
        )
        app = web.Application()
        app["api_server_adapter"] = upstream_adapter
        app.router.add_post("/v1/runs", upstream_adapter._handle_runs)
        app.router.add_get("/v1/runs/{run_id}", upstream_adapter._handle_get_run)
        app.router.add_post("/v1/runs/{run_id}/stop", upstream_adapter._handle_stop_run)

        server = TestServer(app)
        await server.start_server()
        try:
            mock_agent = _mock_agent()
            platform_adapter = HermesOrchestrator(
                HermesAdapterConfig(
                    enabled=True,
                    base_url=str(server.make_url("")).rstrip("/"),
                    pinned_revision=HERMES_PINNED_REVISION,
                    request_timeout_seconds=5.0,
                    plan_timeout_seconds=10.0,
                    poll_interval_seconds=0.01,
                ),
                secret_resolver=lambda _: None,
            )
            request = PlanRequest(
                task_id=new_id("task"),
                objective="Verify the real pinned Hermes /v1/runs contract",
                context=OperationContext(
                    correlation_id="issue-8-pinned-hermes",
                    control=OperationControl(
                        idempotency_key="issue-8-pinned-hermes-plan",
                        timeout_seconds=10.0,
                    ),
                ),
            )

            with patch.object(upstream_adapter, "_create_agent", return_value=mock_agent):
                response = await platform_adapter.plan(request)

            assert response.summary == "Pinned Hermes compatibility"
            assert len(response.steps) == 1
            assert response.steps[0].key == "execute"
            assert response.steps[0].title == "Execute canonically"
            assert response.steps[0].depends_on == ()
            assert response.adapter_metadata[0].namespace == "hermes"
            assert response.adapter_metadata[0].values["canonical_task_id"] == request.task_id
            assert (
                response.adapter_metadata[0].values["upstream_revision"] == HERMES_PINNED_REVISION
            )
            external_run_id = response.adapter_metadata[0].values["external_run_id"]
            assert isinstance(external_run_id, str)
            assert external_run_id.startswith("run_")
            assert external_run_id != request.task_id
        finally:
            await server.close()

    asyncio.run(scenario())


def test_kernel_uses_real_pinned_hermes_and_reference_executor(tmp_path: Path) -> None:
    upstream = _pinned_upstream()
    sys.path.insert(0, str(upstream))

    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter

    async def scenario() -> None:
        upstream_adapter = APIServerAdapter(
            PlatformConfig(
                enabled=True,
                extra={"host": "127.0.0.1", "port": 0},
            )
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
            mock_agent = _mock_agent()
            hermes = HermesOrchestrator(
                HermesAdapterConfig(
                    enabled=True,
                    base_url=str(server.make_url("")).rstrip("/"),
                    pinned_revision=HERMES_PINNED_REVISION,
                    request_timeout_seconds=5.0,
                    plan_timeout_seconds=10.0,
                    poll_interval_seconds=0.01,
                ),
                secret_resolver=lambda _: None,
            )
            lifecycle = ExecutorLifecycleBackend(
                ReferenceExecutor(workspace_root),
                workspace=task_id,
                action="write_artifact",
            )
            kernel = PlatformKernel(orchestrator=hermes, lifecycle=lifecycle)

            with patch.object(upstream_adapter, "_create_agent", return_value=mock_agent):
                await kernel.create_task(
                    idempotency_key="issue-46-hermes:create",
                    task_id=task_id,
                    title="Pinned Hermes conformance",
                    objective="Plan through real Hermes and execute through the reference executor",
                    owner_type="user",
                    owner_id="issue-46",
                )
                await kernel.ready_task(
                    idempotency_key="issue-46-hermes:ready",
                    task_id=task_id,
                )
                run = await kernel.start_task(
                    idempotency_key="issue-46-hermes:start",
                    task_id=task_id,
                )
                run = await kernel.refresh_run(
                    idempotency_key="issue-46-hermes:refresh",
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
            assert hermes_metadata["upstream_revision"] == HERMES_PINNED_REVISION
            assert isinstance(external_run_id, str)
            assert external_run_id.startswith("run_")
            assert run.run_id != external_run_id
        finally:
            await server.close()

    asyncio.run(scenario())
