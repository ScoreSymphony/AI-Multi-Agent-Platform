from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass

import pytest

from ai_multi_agent_platform.adapters.hermes import (
    HERMES_PINNED_REVISION,
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

HERMES_V0_21_1_REVISION = "2237be355906fbe6065ce1815711eee52b2d646e"


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
        raise TimeoutError("candidate timeout")


def _request() -> PlanRequest:
    return PlanRequest(
        task_id=new_id("task"),
        objective="Validate the Hermes v0.21.1 adapter contract",
        context=OperationContext(
            correlation_id="issue-733-hermes-v0-21-1",
            control=OperationControl(
                idempotency_key="issue-733-hermes-v0-21-1",
                timeout_seconds=1.0,
            ),
        ),
    )


def _completed_output() -> str:
    return json.dumps(
        {
            "summary": "Compatible",
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


def test_repository_pin_is_exact_hermes_v0_21_1_commit() -> None:
    assert HERMES_PINNED_REVISION == HERMES_V0_21_1_REVISION
    config = HermesAdapterConfig(enabled=True)
    assert config.pinned_revision == HERMES_V0_21_1_REVISION
    assert config.compatibility_status.value == "verified_pin"


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
def test_v0_21_1_http_error_contract_remains_fail_closed(
    status_code: int,
    expected_code: ErrorCode,
    retryable: bool,
) -> None:
    assert HermesOrchestrator._http_error(status_code) == (expected_code, retryable)


def test_v0_21_1_completed_status_preserves_canonical_plan_contract() -> None:
    async def scenario() -> None:
        transport = FakeHermesTransport(
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
            HermesAdapterConfig(enabled=True, poll_interval_seconds=0.001),
            transport=transport,
            secret_resolver=lambda _: None,
        ).plan(_request())

        assert response.summary == "Compatible"
        assert response.steps[0].key == "validate"
        assert response.adapter_metadata[0].values["external_run_id"] == "run_candidate"
        assert response.adapter_metadata[0].values["upstream_revision"] == HERMES_V0_21_1_REVISION

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
def test_v0_21_1_terminal_and_unknown_status_mapping_is_explicit(
    status: str,
    expected_code: ErrorCode,
) -> None:
    async def scenario() -> None:
        transport = FakeHermesTransport(
            [
                HermesHttpResponse(202, {"run_id": "run_status", "status": "queued"}),
                HermesHttpResponse(200, {"run_id": "run_status", "status": status}),
            ]
        )
        orchestrator = HermesOrchestrator(
            HermesAdapterConfig(enabled=True, poll_interval_seconds=0.001),
            transport=transport,
            secret_resolver=lambda _: None,
        )

        with pytest.raises(ContractError) as error:
            await orchestrator.plan(_request())
        assert error.value.code is expected_code

    asyncio.run(scenario())


def test_v0_21_1_timeout_and_disabled_paths_remain_canonical() -> None:
    async def scenario() -> None:
        timed = HermesOrchestrator(
            HermesAdapterConfig(enabled=True),
            transport=TimeoutTransport(),
            secret_resolver=lambda _: None,
        )
        with pytest.raises(ContractError) as timeout_error:
            await timed.plan(_request())
        assert timeout_error.value.code is ErrorCode.TIMEOUT
        assert timeout_error.value.retryable is True

        disabled = HermesOrchestrator(
            HermesAdapterConfig(enabled=False),
            transport=FakeHermesTransport([]),
            secret_resolver=lambda _: None,
        )
        with pytest.raises(ContractError) as disabled_error:
            await disabled.plan(_request())
        assert disabled_error.value.code is ErrorCode.UNAVAILABLE

    asyncio.run(scenario())
