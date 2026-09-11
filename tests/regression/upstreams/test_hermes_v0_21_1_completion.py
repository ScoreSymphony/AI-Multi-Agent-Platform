from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

import pytest

from ai_multi_agent_platform.adapters.hermes import (
    HermesAdapterConfig,
    HermesHttpResponse,
    HermesOrchestrator,
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


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    method: str
    url: str
    payload: Mapping[str, JsonValue] | None


class FakeHermesTransport:
    def __init__(self, responses: list[HermesHttpResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[RecordedRequest] = []

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        payload: Mapping[str, JsonValue] | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HermesHttpResponse:
        del headers, timeout_seconds
        self.calls.append(RecordedRequest(method, url, payload))
        if not self.responses:
            raise AssertionError("unexpected Hermes HTTP request")
        return self.responses.pop(0)


def _request() -> PlanRequest:
    return PlanRequest(
        task_id=new_id("task"),
        objective="Complete the Hermes v0.21.1 validation matrix",
        context=OperationContext(
            correlation_id="issue-733-completion",
            control=OperationControl(
                idempotency_key="issue-733-completion",
                timeout_seconds=1.0,
            ),
        ),
    )


def test_v0_21_1_failed_status_maps_to_canonical_backend_error() -> None:
    async def scenario() -> None:
        transport = FakeHermesTransport(
            [
                HermesHttpResponse(202, {"run_id": "run_failed", "status": "queued"}),
                HermesHttpResponse(
                    200,
                    {
                        "run_id": "run_failed",
                        "status": "failed",
                        "error": "candidate planner failed",
                    },
                ),
            ]
        )
        orchestrator = HermesOrchestrator(
            HermesAdapterConfig(enabled=True, poll_interval_seconds=0.001),
            transport=transport,
            secret_resolver=lambda _: None,
        )

        with pytest.raises(ContractError) as error:
            await orchestrator.plan(_request())

        assert error.value.code is ErrorCode.BACKEND_ERROR
        assert "candidate planner failed" in error.value.message

    asyncio.run(scenario())


def test_waiting_for_approval_fails_closed_without_upstream_approval_call() -> None:
    async def scenario() -> None:
        transport = FakeHermesTransport(
            [
                HermesHttpResponse(202, {"run_id": "run_waiting", "status": "started"}),
                HermesHttpResponse(
                    200,
                    {"run_id": "run_waiting", "status": "waiting_for_approval"},
                ),
            ]
        )
        orchestrator = HermesOrchestrator(
            HermesAdapterConfig(enabled=True, poll_interval_seconds=0.001),
            transport=transport,
            secret_resolver=lambda _: None,
        )

        with pytest.raises(ContractError) as error:
            await orchestrator.plan(_request())

        assert error.value.code is ErrorCode.FORBIDDEN
        assert all(not call.url.endswith("/approval") for call in transport.calls)
        assert [call.method for call in transport.calls] == ["POST", "GET"]

    asyncio.run(scenario())


def test_restart_while_waiting_reconciles_and_cancels_same_external_run() -> None:
    async def scenario() -> None:
        external_run_id = "run_waiting_restart"
        context = OperationContext(
            correlation_id="issue-733-restart",
            control=OperationControl(timeout_seconds=1.0),
        )

        before_restart = HermesOrchestrator(
            HermesAdapterConfig(enabled=True),
            transport=FakeHermesTransport(
                [HermesHttpResponse(200, {"run_id": external_run_id, "status": "waiting_for_approval"})]
            ),
            secret_resolver=lambda _: None,
        )
        waiting = await before_restart.reconcile_external_run(external_run_id, context)
        assert waiting.external_run_id == external_run_id
        assert waiting.status == "waiting_for_approval"

        restarted_transport = FakeHermesTransport(
            [
                HermesHttpResponse(200, {"run_id": external_run_id, "status": "waiting_for_approval"}),
                HermesHttpResponse(200, {"ok": True}),
                HermesHttpResponse(200, {"run_id": external_run_id, "status": "cancelled"}),
            ]
        )
        after_restart = HermesOrchestrator(
            HermesAdapterConfig(enabled=True),
            transport=restarted_transport,
            secret_resolver=lambda _: None,
        )

        reconciled = await after_restart.reconcile_external_run(external_run_id, context)
        cancelled = await after_restart.cancel_external_run(external_run_id, context)

        assert reconciled.external_run_id == external_run_id
        assert reconciled.status == "waiting_for_approval"
        assert cancelled.external_run_id == external_run_id
        assert cancelled.status == "cancelled"
        assert [call.method for call in restarted_transport.calls] == ["GET", "POST", "GET"]
        assert all("/approval" not in call.url for call in restarted_transport.calls)
        assert all(
            not (call.method == "POST" and call.url.endswith("/v1/runs"))
            for call in restarted_transport.calls
        )

    asyncio.run(scenario())
