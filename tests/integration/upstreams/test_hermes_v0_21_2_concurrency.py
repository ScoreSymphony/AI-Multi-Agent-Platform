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
    HermesAdapterConfig,
    HermesCompatibilityStatus,
    HermesHttpResponse,
    HermesOrchestrator,
    UrllibHermesHttpTransport,
)
from ai_multi_agent_platform.contracts import (
    JsonValue,
    OperationContext,
    OperationControl,
    PlanRequest,
)
from ai_multi_agent_platform.domain import new_id

HERMES_V0_21_2_REVISION = "939e45c91d751fadd94dcd1b873ac3cb44846213"


def _planner_output(summary: str) -> str:
    return json.dumps(
        {
            "summary": summary,
            "steps": [
                {
                    "key": "validate",
                    "title": "Validate isolation",
                    "objective": "Preserve independent candidate runs",
                    "depends_on": [],
                }
            ],
        }
    )


def _request(marker: str) -> PlanRequest:
    return PlanRequest(
        task_id=new_id("task"),
        objective=f"Hermes v0.21.2 concurrency {marker}",
        context=OperationContext(
            correlation_id=f"issue-959-concurrency-{marker}",
            control=OperationControl(
                idempotency_key=f"issue-959-concurrency-{marker}",
                timeout_seconds=15.0,
            ),
        ),
    )


def _config(base_url: str) -> HermesAdapterConfig:
    return HermesAdapterConfig(
        enabled=True,
        base_url=base_url,
        pinned_revision=HERMES_V0_21_2_REVISION,
        compatibility_status=HermesCompatibilityStatus.VERIFIED_PIN,
        request_timeout_seconds=5.0,
        plan_timeout_seconds=15.0,
        poll_interval_seconds=0.01,
    )


def _fast_agent(summary: str) -> MagicMock:
    agent = MagicMock()
    agent.run_conversation.return_value = {"final_response": _planner_output(summary)}
    agent.session_prompt_tokens = 0
    agent.session_completion_tokens = 0
    agent.session_total_tokens = 0
    return agent


class InterruptiblePlannerAgent:
    """Agent stand-in with deterministic start/interrupt/finish synchronization."""

    def __init__(self) -> None:
        self.ready = threading.Event()
        self.interrupted = threading.Event()
        self.finished = threading.Event()
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0
        self._gateway_turn_process_task_id = None
        self._gateway_turn_process_baseline = None

    def interrupt(self, message: str | None = None) -> None:
        del message
        self.interrupted.set()

    def run_conversation(self, *, user_message: str, **_: Any) -> dict[str, Any]:
        del user_message
        self.ready.set()
        if not self.interrupted.wait(timeout=10.0):
            raise TimeoutError("candidate cancellation did not reach the active run")
        self.finished.set()
        return {"final_response": "interrupted", "interrupted": True}


class RecordingTransport:
    """Delegate to the real platform HTTP transport while recording run admission."""

    def __init__(self) -> None:
        self._delegate = UrllibHermesHttpTransport()
        self.admitted = asyncio.Event()
        self.external_run_id: str | None = None

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        payload: Mapping[str, JsonValue] | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HermesHttpResponse:
        response = await self._delegate.request_json(
            method,
            url,
            payload=payload,
            headers=headers,
            timeout_seconds=timeout_seconds,
        )
        if method == "POST" and url.endswith("/v1/runs") and isinstance(response.payload, Mapping):
            run_id = response.payload.get("run_id")
            if isinstance(run_id, str):
                self.external_run_id = run_id
                self.admitted.set()
        return response


async def _terminal_status(
    orchestrator: HermesOrchestrator,
    external_run_id: str,
    marker: str,
) -> str:
    context = OperationContext(
        correlation_id=f"issue-959-terminal-{marker}",
        control=OperationControl(timeout_seconds=5.0),
    )
    async with asyncio.timeout(5.0):
        while True:
            snapshot = await orchestrator.reconcile_external_run(external_run_id, context)
            if snapshot.status not in {"queued", "started", "running", "stopping"}:
                return snapshot.status
            await asyncio.sleep(0.01)


def test_parallel_candidate_completion_and_cancellation_remain_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream_value = os.environ.get("HERMES_UPSTREAM_DIR")
    if not upstream_value:
        pytest.skip("set HERMES_UPSTREAM_DIR to run Hermes v0.21.2 concurrency tests")
    upstream = Path(upstream_value).resolve()
    assert os.environ.get("HERMES_UPSTREAM_REVISION") == HERMES_V0_21_2_REVISION
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.syspath_prepend(str(upstream))

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
            slow_agent = InterruptiblePlannerAgent()
            fast_agent = _fast_agent("independent completion")
            agents = iter((slow_agent, fast_agent))
            base_url = str(server.make_url("")).rstrip("/")
            slow_transport = RecordingTransport()
            fast_transport = RecordingTransport()
            slow_orchestrator = HermesOrchestrator(
                _config(base_url),
                transport=slow_transport,
                secret_resolver=lambda _: None,
            )
            fast_orchestrator = HermesOrchestrator(
                _config(base_url),
                transport=fast_transport,
                secret_resolver=lambda _: None,
            )

            with patch.object(
                upstream_adapter,
                "_create_agent",
                side_effect=lambda **_: next(agents),
            ):
                slow_task = asyncio.create_task(slow_orchestrator.plan(_request("cancel")))
                assert await asyncio.to_thread(slow_agent.ready.wait, 5.0)
                await asyncio.wait_for(slow_transport.admitted.wait(), timeout=5.0)

                completed = await fast_orchestrator.plan(_request("complete"))
                await asyncio.wait_for(fast_transport.admitted.wait(), timeout=5.0)

                slow_run_id = slow_transport.external_run_id
                fast_run_id = fast_transport.external_run_id
                assert isinstance(slow_run_id, str)
                assert isinstance(fast_run_id, str)
                assert slow_run_id != fast_run_id
                assert completed.summary == "independent completion"

                slow_task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await slow_task

                assert await asyncio.to_thread(slow_agent.interrupted.wait, 5.0)
                assert await asyncio.to_thread(slow_agent.finished.wait, 5.0)

                slow_status = await _terminal_status(slow_orchestrator, slow_run_id, "cancel")
                fast_status = await _terminal_status(fast_orchestrator, fast_run_id, "complete")
                assert slow_status == "cancelled"
                assert fast_status == "completed"
                assert completed.adapter_metadata[0].values["external_run_id"] == fast_run_id
                assert (
                    completed.adapter_metadata[0].values["upstream_revision"]
                    == HERMES_V0_21_2_REVISION
                )
        finally:
            await server.close()

    asyncio.run(scenario())
