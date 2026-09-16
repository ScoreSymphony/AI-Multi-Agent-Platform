from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass

import pytest

from ai_multi_agent_platform.adapters.hermes import (
    HermesAdapterConfig,
    HermesCompatibilityStatus,
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

HERMES_V0_21_2_REVISION = "939e45c91d751fadd94dcd1b873ac3cb44846213"


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    method: str
    url: str
    payload: Mapping[str, JsonValue] | None


class CandidateTransport:
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


class TimeoutTransport:
    async def request_json(
        self,
        method: str,
        url: str,
        *,
        payload: Mapping[str, JsonValue] | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HermesHttpResponse:
        del method, url, payload, headers, timeout_seconds
        raise TimeoutError("Hermes v0.21.2 candidate timeout")


def _candidate_config(**overrides: object) -> HermesAdapterConfig:
    values: dict[str, object] = {
        "enabled": True,
        "pinned_revision": HERMES_V0_21_2_REVISION,
        "compatibility_status": HermesCompatibilityStatus.VERIFIED_PIN,
        "poll_interval_seconds": 0.001,
    }
    values.update(overrides)
    return HermesAdapterConfig(**values)  # type: ignore[arg-type]


def _request(marker: str) -> PlanRequest:
    return PlanRequest(
        task_id=new_id("task"),
        objective=f"Validate Hermes v0.21.2 baseline compatibility: {marker}",
        context=OperationContext(
            correlation_id=f"hermes-v0-21-2-baseline-{marker}",
            control=OperationControl(
                idempotency_key=f"hermes-v0-21-2-baseline-{marker}",
                timeout_seconds=1.0,
            ),
        ),
    )


def _completed_output() -> str:
    return json.dumps(
        {
            "summary": "Hermes v0.21.2 candidate compatible",
            "steps": [
                {
                    "key": "validate",
                    "title": "Validate",
                    "objective": "Keep canonical ownership unchanged",
                    "depends_on": [],
                }
            ],
        }
    )


@pytest.mark.parametrize(
    ("status_code", "expected_code", "retryable"),
    [
        (400, ErrorCode.INVALID_REQUEST, False),
        (401, ErrorCode.UNAUTHORIZED, False),
        (403, ErrorCode.FORBIDDEN, False),
        (404, ErrorCode.NOT_FOUND, False),
        (409, ErrorCode.CONFLICT, False),
        (429, ErrorCode.RATE_LIMITED, True),
        (500, ErrorCode.UNAVAILABLE, True),
    ],
)
def test_candidate_preserves_baseline_http_error_mapping(
    status_code: int,
    expected_code: ErrorCode,
    retryable: bool,
) -> None:
    assert HermesOrchestrator._http_error(status_code) == (expected_code, retryable)


def test_candidate_completed_status_preserves_canonical_plan_contract() -> None:
    async def scenario() -> None:
        transport = CandidateTransport(
            [
                HermesHttpResponse(202, {"run_id": "run_candidate", "status": "queued"}),
                HermesHttpResponse(
                    200,
                    {
                        "run_id": "run_candidate",
                        "status": "completed",
                        "output": _completed_output(),
                    },
                ),
            ]
        )
        response = await HermesOrchestrator(
            _candidate_config(),
            transport=transport,
            secret_resolver=lambda _: None,
        ).plan(_request("completed"))

        assert response.summary == "Hermes v0.21.2 candidate compatible"
        assert response.steps[0].key == "validate"
        metadata = response.adapter_metadata[0].values
        assert metadata["external_run_id"] == "run_candidate"
        assert metadata["upstream_revision"] == HERMES_V0_21_2_REVISION

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
def test_candidate_terminal_and_unknown_status_mapping_is_explicit(
    status: str,
    expected_code: ErrorCode,
) -> None:
    async def scenario() -> None:
        transport = CandidateTransport(
            [
                HermesHttpResponse(202, {"run_id": "run_status", "status": "queued"}),
                HermesHttpResponse(200, {"run_id": "run_status", "status": status}),
            ]
        )
        orchestrator = HermesOrchestrator(
            _candidate_config(),
            transport=transport,
            secret_resolver=lambda _: None,
        )

        with pytest.raises(ContractError) as error:
            await orchestrator.plan(_request(f"status-{status}"))
        assert error.value.code is expected_code
        assert (
            error.value.adapter_metadata[0].values["upstream_revision"] == HERMES_V0_21_2_REVISION
        )

    asyncio.run(scenario())


def test_candidate_failed_status_maps_to_canonical_backend_error() -> None:
    async def scenario() -> None:
        transport = CandidateTransport(
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
            _candidate_config(),
            transport=transport,
            secret_resolver=lambda _: None,
        )

        with pytest.raises(ContractError) as error:
            await orchestrator.plan(_request("failed"))

        assert error.value.code is ErrorCode.BACKEND_ERROR
        assert error.value.message == "Hermes planning run failed with status failed"
        assert "candidate planner failed" not in error.value.message
        assert (
            error.value.adapter_metadata[0].values["upstream_revision"] == HERMES_V0_21_2_REVISION
        )

    asyncio.run(scenario())


def test_candidate_waiting_for_approval_fails_closed_without_upstream_approval() -> None:
    async def scenario() -> None:
        transport = CandidateTransport(
            [
                HermesHttpResponse(202, {"run_id": "run_waiting", "status": "started"}),
                HermesHttpResponse(
                    200,
                    {"run_id": "run_waiting", "status": "waiting_for_approval"},
                ),
            ]
        )
        orchestrator = HermesOrchestrator(
            _candidate_config(),
            transport=transport,
            secret_resolver=lambda _: None,
        )

        with pytest.raises(ContractError) as error:
            await orchestrator.plan(_request("approval-wait"))

        assert error.value.code is ErrorCode.FORBIDDEN
        assert [call.method for call in transport.calls] == ["POST", "GET"]
        assert all(not call.url.endswith("/approval") for call in transport.calls)

    asyncio.run(scenario())


def test_candidate_restart_while_waiting_reconciles_and_cancels_same_external_run() -> None:
    async def scenario() -> None:
        external_run_id = "run_waiting_restart"
        context = OperationContext(
            correlation_id="hermes-v0-21-2-restart-waiting",
            control=OperationControl(timeout_seconds=1.0),
        )

        before_restart = HermesOrchestrator(
            _candidate_config(),
            transport=CandidateTransport(
                [
                    HermesHttpResponse(
                        200,
                        {"run_id": external_run_id, "status": "waiting_for_approval"},
                    )
                ]
            ),
            secret_resolver=lambda _: None,
        )
        waiting = await before_restart.reconcile_external_run(external_run_id, context)
        assert waiting.external_run_id == external_run_id
        assert waiting.status == "waiting_for_approval"

        restarted_transport = CandidateTransport(
            [
                HermesHttpResponse(
                    200,
                    {"run_id": external_run_id, "status": "waiting_for_approval"},
                ),
                HermesHttpResponse(200, {"ok": True}),
                HermesHttpResponse(200, {"run_id": external_run_id, "status": "cancelled"}),
            ]
        )
        after_restart = HermesOrchestrator(
            _candidate_config(),
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


def test_candidate_timeout_and_disabled_paths_remain_canonical() -> None:
    async def scenario() -> None:
        timed = HermesOrchestrator(
            _candidate_config(),
            transport=TimeoutTransport(),
            secret_resolver=lambda _: None,
        )
        with pytest.raises(ContractError) as timeout_error:
            await timed.plan(_request("timeout"))
        assert timeout_error.value.code is ErrorCode.TIMEOUT
        assert timeout_error.value.retryable is True
        assert (
            timeout_error.value.adapter_metadata[0].values["upstream_revision"]
            == HERMES_V0_21_2_REVISION
        )

        disabled = HermesOrchestrator(
            _candidate_config(enabled=False),
            transport=CandidateTransport([]),
            secret_resolver=lambda _: None,
        )
        with pytest.raises(ContractError) as disabled_error:
            await disabled.plan(_request("disabled"))
        assert disabled_error.value.code is ErrorCode.UNAVAILABLE

    asyncio.run(scenario())
