from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_multi_agent_platform.adapters.hermes import (
    HERMES_PINNED_REVISION,
    HermesAdapterConfig,
    HermesOrchestrator,
)
from ai_multi_agent_platform.contracts import (
    HealthStatus,
    OperationContext,
    OperationControl,
    PlanRequest,
)
from ai_multi_agent_platform.domain import new_id


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
                "summary": "Restart-safe pinned Hermes plan",
                "steps": [
                    {
                        "key": "verify",
                        "title": "Verify",
                        "objective": "Preserve canonical identity across adapter recreation",
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


def test_pinned_hermes_startup_auth_and_health_contract() -> None:
    upstream = _pinned_upstream()
    sys.path.insert(0, str(upstream))

    from aiohttp import web
    from aiohttp.test_utils import TestServer, make_mocked_request
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter

    async def scenario() -> None:
        api_key = "issue-733-hermes-key-0123456789abcdef"
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
                HermesAdapterConfig(
                    enabled=True,
                    base_url=str(server.make_url("")).rstrip("/"),
                    pinned_revision=HERMES_PINNED_REVISION,
                    request_timeout_seconds=5.0,
                ),
                secret_resolver=lambda name: api_key if name == "API_SERVER_KEY" else None,
            )
            assert await platform_adapter.health() is HealthStatus.HEALTHY
        finally:
            await server.close()

    asyncio.run(scenario())


def test_pinned_hermes_reconciliation_survives_adapter_recreation() -> None:
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
            base_url = str(server.make_url("")).rstrip("/")
            canonical_task_id = new_id("task")
            request = PlanRequest(
                task_id=canonical_task_id,
                objective="Prove restart-safe reconciliation against the exact pinned Hermes runtime",
                context=OperationContext(
                    correlation_id="issue-733-pinned-restart",
                    control=OperationControl(
                        idempotency_key="issue-733-pinned-restart",
                        timeout_seconds=10.0,
                    ),
                ),
            )
            before_restart = HermesOrchestrator(
                HermesAdapterConfig(
                    enabled=True,
                    base_url=base_url,
                    pinned_revision=HERMES_PINNED_REVISION,
                    request_timeout_seconds=5.0,
                    plan_timeout_seconds=10.0,
                    poll_interval_seconds=0.01,
                ),
                secret_resolver=lambda _: None,
            )

            with patch.object(upstream_adapter, "_create_agent", return_value=_mock_agent()):
                plan = await before_restart.plan(request)

            metadata = plan.adapter_metadata[0].values
            external_run_id = metadata["external_run_id"]
            assert metadata["canonical_task_id"] == canonical_task_id
            assert isinstance(external_run_id, str)
            assert external_run_id != canonical_task_id

            after_restart = HermesOrchestrator(
                HermesAdapterConfig(
                    enabled=True,
                    base_url=base_url,
                    pinned_revision=HERMES_PINNED_REVISION,
                    request_timeout_seconds=5.0,
                ),
                secret_resolver=lambda _: None,
            )
            reconciled = await after_restart.reconcile_external_run(
                external_run_id,
                OperationContext(
                    correlation_id="issue-733-pinned-restart-reconcile",
                    control=OperationControl(timeout_seconds=5.0),
                ),
            )

            assert reconciled.external_run_id == external_run_id
            assert reconciled.status == "completed"
            assert metadata["canonical_task_id"] == canonical_task_id
        finally:
            await server.close()

    asyncio.run(scenario())
