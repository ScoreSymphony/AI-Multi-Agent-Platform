from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

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
    OperationContext,
    OperationControl,
    PlanRequest,
)
from ai_multi_agent_platform.domain import new_id

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


def _request(marker: str) -> PlanRequest:
    return PlanRequest(
        task_id=new_id("task"),
        objective=f"Validate concurrent Hermes candidate session {marker}",
        context=OperationContext(
            correlation_id=f"issue-959-{marker}",
            control=OperationControl(
                idempotency_key=f"issue-959-{marker}",
                timeout_seconds=15.0,
            ),
        ),
    )


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
        return {
            "final_response": json.dumps(
                {
                    "summary": user_message,
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
        }


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


def test_parallel_candidate_runs_keep_results_and_external_ids_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream = _candidate_upstream()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    sys.path.insert(0, str(upstream))

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
                return BarrierPlannerAgent(str(kwargs.get("session_id") or "missing"), barrier)

            config = HermesAdapterConfig(
                enabled=True,
                base_url=str(server.make_url("")).rstrip("/"),
                pinned_revision=HERMES_V0_21_2_REVISION,
                compatibility_status=HermesCompatibilityStatus.UNVERIFIED_PIN,
                request_timeout_seconds=5.0,
                plan_timeout_seconds=15.0,
                poll_interval_seconds=0.01,
            )
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
def test_candidate_status_mapping_remains_fail_closed(status: str, expected_code: ErrorCode) -> None:
    class Transport:
        def __init__(self) -> None:
            self.calls = 0

        async def request_json(self, method, url, *, payload, headers, timeout_seconds):
            from ai_multi_agent_platform.adapters.hermes import HermesHttpResponse

            del url, payload, headers, timeout_seconds
            self.calls += 1
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
        assert error.value.adapter_metadata[0].values["upstream_revision"] == HERMES_V0_21_2_REVISION

    asyncio.run(scenario())
