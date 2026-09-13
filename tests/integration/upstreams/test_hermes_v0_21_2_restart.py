from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from ai_multi_agent_platform.adapters.hermes import (
    HermesAdapterConfig,
    HermesCompatibilityStatus,
    HermesHttpResponse,
    HermesOrchestrator,
    UrllibHermesHttpTransport,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    JsonValue,
    OperationContext,
    OperationControl,
    PlanRequest,
)
from ai_multi_agent_platform.domain import new_id

HERMES_V0_21_2_REVISION = "939e45c91d751fadd94dcd1b873ac3cb44846213"


def _prepare_upstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    upstream_value = os.environ.get("HERMES_UPSTREAM_DIR")
    if not upstream_value:
        pytest.skip("set HERMES_UPSTREAM_DIR to run Hermes v0.21.2 restart tests")
    upstream = Path(upstream_value).resolve()
    assert os.environ.get("HERMES_UPSTREAM_REVISION") == HERMES_V0_21_2_REVISION
    assert (upstream / "gateway" / "platforms" / "api_server.py").is_file()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.syspath_prepend(str(upstream))
    return upstream


def _candidate_config(base_url: str) -> HermesAdapterConfig:
    return HermesAdapterConfig(
        enabled=True,
        base_url=base_url,
        pinned_revision=HERMES_V0_21_2_REVISION,
        compatibility_status=HermesCompatibilityStatus.VERIFIED_PIN,
        request_timeout_seconds=5.0,
        plan_timeout_seconds=15.0,
        poll_interval_seconds=0.01,
    )


def _request() -> PlanRequest:
    return PlanRequest(
        task_id=new_id("task"),
        objective="Reconcile one adapter-managed Hermes v0.21.2 run after runtime restart",
        context=OperationContext(
            correlation_id="issue-959-active-runtime-restart",
            control=OperationControl(
                idempotency_key="issue-959-active-runtime-restart",
                timeout_seconds=15.0,
            ),
        ),
    )


def _planner_output() -> str:
    return json.dumps(
        {
            "summary": "old runtime should not publish this after restart",
            "steps": [
                {
                    "key": "verify",
                    "title": "Verify restart recovery",
                    "objective": "Preserve the original external run mapping",
                    "depends_on": [],
                }
            ],
        }
    )


class BlockingPlannerAgent:
    """Keep a real admitted run active until the test has recreated the runtime."""

    def __init__(self) -> None:
        self.ready = threading.Event()
        self.release = threading.Event()
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0
        self._gateway_turn_process_task_id = None
        self._gateway_turn_process_baseline = None

    def run_conversation(self, *, user_message: str, **_: Any) -> dict[str, Any]:
        del user_message
        self.ready.set()
        if not self.release.wait(timeout=10.0):
            raise TimeoutError("restart test did not release the pre-restart run")
        return {"final_response": _planner_output()}


class RecordingTransport:
    """Use the real HTTP transport while exposing the admitted Hermes run id."""

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


def test_adapter_admitted_active_run_reconciles_after_runtime_restart_without_reexecution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_upstream(tmp_path, monkeypatch)

    from aiohttp import web
    from aiohttp.test_utils import TestServer, make_mocked_request
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter
    from gateway.platforms.api_server_run_idempotency import RunIdempotencyStore

    async def scenario() -> None:
        store_path = tmp_path / "runs_idempotency.db"
        request = _request()
        blocking_agent = BlockingPlannerAgent()

        before_restart = APIServerAdapter(
            PlatformConfig(enabled=True, extra={"host": "127.0.0.1", "port": 0})
        )
        before_restart._run_idempotency_store.close()
        before_restart._run_idempotency_store = RunIdempotencyStore(str(store_path))
        # The test stays in one Python process, so persist an intentionally dead owner
        # identity to model the process incarnation that disappears during a real restart.
        # Admission itself still happens through the real platform adapter -> /v1/runs seam.
        before_restart._run_owner_pid = 999_999_999
        before_restart._run_owner_started = 1

        before_app = web.Application()
        before_app["api_server_adapter"] = before_restart
        before_app.router.add_post("/v1/runs", before_restart._handle_runs)
        before_app.router.add_get("/v1/runs/{run_id}", before_restart._handle_get_run)
        before_app.router.add_post("/v1/runs/{run_id}/stop", before_restart._handle_stop_run)
        before_server = TestServer(before_app)
        await before_server.start_server()

        before_transport = RecordingTransport()
        before_orchestrator = HermesOrchestrator(
            _candidate_config(str(before_server.make_url("")).rstrip("/")),
            transport=before_transport,
            secret_resolver=lambda _: None,
        )
        active_upstream_task: asyncio.Task[None] | None = None

        try:
            with patch.object(before_restart, "_create_agent", return_value=blocking_agent):
                plan_task = asyncio.create_task(before_orchestrator.plan(request))
                assert await asyncio.to_thread(blocking_agent.ready.wait, 5.0)
                await asyncio.wait_for(before_transport.admitted.wait(), timeout=5.0)

                external_run_id = before_transport.external_run_id
                assert isinstance(external_run_id, str)
                active_upstream_task = before_restart._active_run_tasks[external_run_id]

                scope_request = make_mocked_request("GET", f"/v1/runs/{external_run_id}")
                scope = before_restart._run_idempotency_scope(scope_request)
                persisted_before = before_restart._run_idempotency_store.status_for_run(
                    scope,
                    external_run_id,
                )
                assert persisted_before is not None
                assert persisted_before["status"]["status"] == "running"
                assert persisted_before["owner_pid"] == 999_999_999

                # Drop the serving runtime while the admitted run is still blocked. The
                # platform-side planner observes an unavailable runtime rather than a fake
                # terminal success/cancellation from the pre-restart process.
                await before_server.close()
                with pytest.raises(ContractError) as unavailable_error:
                    await asyncio.wait_for(plan_task, timeout=5.0)
                assert unavailable_error.value.code is ErrorCode.UNAVAILABLE

                # A dead process cannot publish a later terminal status. Closing this handle
                # before releasing the stand-in models that crash boundary in-process.
                before_restart._run_idempotency_store.close()

                after_restart = APIServerAdapter(
                    PlatformConfig(enabled=True, extra={"host": "127.0.0.1", "port": 0})
                )
                after_restart._run_idempotency_store.close()
                after_restart._run_idempotency_store = RunIdempotencyStore(str(store_path))

                after_app = web.Application()
                after_app["api_server_adapter"] = after_restart
                after_app.router.add_post("/v1/runs", after_restart._handle_runs)
                after_app.router.add_get("/v1/runs/{run_id}", after_restart._handle_get_run)
                after_app.router.add_post("/v1/runs/{run_id}/stop", after_restart._handle_stop_run)
                after_server = TestServer(after_app)
                await after_server.start_server()
                try:
                    after_orchestrator = HermesOrchestrator(
                        _candidate_config(str(after_server.make_url("")).rstrip("/")),
                        secret_resolver=lambda _: None,
                    )
                    context = OperationContext(
                        correlation_id="issue-959-active-runtime-reconcile",
                        control=OperationControl(timeout_seconds=5.0),
                    )
                    reconciled = await after_orchestrator.reconcile_external_run(
                        external_run_id,
                        context,
                    )

                    assert reconciled.external_run_id == external_run_id
                    assert reconciled.status == "interrupted"

                    persisted_after = after_restart._run_idempotency_store.status_for_run(
                        scope,
                        external_run_id,
                    )
                    assert persisted_after is not None
                    assert persisted_after["status"]["status"] == "interrupted"
                    assert "restarted before this run settled" in persisted_after["status"]["error"]

                    # Replaying the same canonical planning request must recover the existing
                    # Hermes run rather than allocate or execute a second upstream run.
                    with patch.object(
                        after_restart,
                        "_create_agent",
                        side_effect=AssertionError("restart replay must not execute a second run"),
                    ):
                        with pytest.raises(ContractError) as replay_error:
                            await after_orchestrator.plan(request)
                    assert replay_error.value.code is ErrorCode.CANCELLED
                    assert (
                        replay_error.value.adapter_metadata[0].values["external_run_id"]
                        == external_run_id
                    )

                    replayed_status = after_restart._run_idempotency_store.status_for_run(
                        scope,
                        external_run_id,
                    )
                    assert replayed_status is not None
                    assert replayed_status["status"]["status"] == "interrupted"
                finally:
                    await after_server.close()
                    after_restart._run_idempotency_store.close()
        finally:
            # Let the thread-backed pre-restart stand-in unwind so asyncio's executor can
            # shut down. Its durable handle is already closed, so it cannot overwrite the
            # reconciled terminal record produced by the recreated runtime.
            blocking_agent.release.set()
            if active_upstream_task is not None:
                await asyncio.wait_for(asyncio.shield(active_upstream_task), timeout=5.0)
            if not before_server.closed:
                await before_server.close()
            try:
                before_restart._run_idempotency_store.close()
            except Exception:
                pass

    asyncio.run(scenario())


def test_persisted_active_run_reconciles_after_runtime_recreation_without_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_upstream(tmp_path, monkeypatch)

    from aiohttp import web
    from aiohttp.test_utils import TestServer, make_mocked_request
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter
    from gateway.platforms.api_server_run_idempotency import RunIdempotencyStore

    async def scenario() -> None:
        store_path = tmp_path / "runs_idempotency-direct.db"
        external_run_id = "run_issue959_active_restart"
        idempotency_key = "issue-959-active-restart"
        fingerprint = "issue-959-active-restart-fingerprint"
        status = {
            "object": "hermes.run",
            "run_id": external_run_id,
            "status": "running",
            "created_at": 1.0,
            "updated_at": 1.0,
        }

        before_restart = APIServerAdapter(
            PlatformConfig(enabled=True, extra={"host": "127.0.0.1", "port": 0})
        )
        before_restart._run_idempotency_store.close()
        before_restart._run_idempotency_store = RunIdempotencyStore(str(store_path))
        scope_request = make_mocked_request("GET", f"/v1/runs/{external_run_id}")
        scope = before_restart._run_idempotency_scope(scope_request)
        outcome, stored = before_restart._run_idempotency_store.reserve(
            scope,
            idempotency_key,
            fingerprint,
            external_run_id,
            status,
            owner_pid=999_999_999,
            owner_started=1,
        )
        assert outcome == "created"
        assert stored["run_id"] == external_run_id
        before_restart._run_idempotency_store.close()

        after_restart = APIServerAdapter(
            PlatformConfig(enabled=True, extra={"host": "127.0.0.1", "port": 0})
        )
        after_restart._run_idempotency_store.close()
        after_restart._run_idempotency_store = RunIdempotencyStore(str(store_path))

        app = web.Application()
        app["api_server_adapter"] = after_restart
        app.router.add_get("/v1/runs/{run_id}", after_restart._handle_get_run)
        server = TestServer(app)
        await server.start_server()
        try:
            orchestrator = HermesOrchestrator(
                _candidate_config(str(server.make_url("")).rstrip("/")),
                secret_resolver=lambda _: None,
            )
            reconciled = await orchestrator.reconcile_external_run(
                external_run_id,
                OperationContext(
                    correlation_id="issue-959-active-restart-reconcile",
                    control=OperationControl(timeout_seconds=5.0),
                ),
            )

            assert reconciled.external_run_id == external_run_id
            assert reconciled.status == "interrupted"

            persisted = after_restart._run_idempotency_store.status_for_run(
                scope,
                external_run_id,
            )
            assert persisted is not None
            assert persisted["status"]["status"] == "interrupted"
            assert "restarted before this run settled" in persisted["status"]["error"]

            replay_outcome, replay = after_restart._run_idempotency_store.lookup(
                scope,
                idempotency_key,
                fingerprint,
            )
            assert replay_outcome == "reused"
            assert replay is not None
            assert replay["run_id"] == external_run_id
            assert replay["status"]["status"] == "interrupted"
        finally:
            await server.close()
            after_restart._run_idempotency_store.close()

    asyncio.run(scenario())
