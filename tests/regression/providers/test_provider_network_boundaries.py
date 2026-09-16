from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping

import pytest

from ai_multi_agent_platform.adapters.hermes import (
    HermesAdapterConfig,
    HermesHttpResponse,
    HermesOrchestrator,
)
from ai_multi_agent_platform.adapters.openai_compatible import (
    HttpJsonResponse,
    OpenAICompatibleProviderConfig,
)
from ai_multi_agent_platform.adapters.openai_compatible_streaming import (
    OpenAICompatibleModelProvider,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    JsonValue,
    ModelRequest,
    OperationContext,
    OperationControl,
    PlanRequest,
)
from ai_multi_agent_platform.domain import new_id


class RaisingStreamingTransport:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> HttpJsonResponse:
        del method, url, headers, payload, timeout_seconds
        raise AssertionError("streaming test must not use request_json")

    def stream_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> AsyncIterator[HttpJsonResponse]:
        del method, url, headers, payload, timeout_seconds

        async def iterate() -> AsyncIterator[HttpJsonResponse]:
            raise self.exc
            yield HttpJsonResponse(200, None)

        return iterate()


def _stream_request(request_id: str) -> ModelRequest:
    return ModelRequest(
        request_id=request_id,
        messages=("hello",),
        context=OperationContext(correlation_id="corr-983-stream"),
        requirements={"model_config_id": "model-local"},
    )


def _stream_provider(exc: BaseException) -> OpenAICompatibleModelProvider:
    return OpenAICompatibleModelProvider(
        OpenAICompatibleProviderConfig(
            provider_id="openai-compatible-983",
            base_url="http://127.0.0.1:8000/v1",
            models={"model-local": "native-model"},
        ),
        transport=RaisingStreamingTransport(exc),
    )


def test_streaming_unknown_transport_failure_is_non_retryable_and_redacted() -> None:
    secret = "stream-sensitive-provider-secret"
    provider = _stream_provider(RuntimeError(f"sdk failed bearer={secret}"))

    async def scenario() -> None:
        with pytest.raises(ContractError) as captured:
            async for _ in provider.stream(_stream_request("req-stream-unknown")):
                pass
        assert captured.value.code is ErrorCode.BACKEND_ERROR
        assert captured.value.retryable is False
        assert captured.value.details == {"exception_type": "RuntimeError"}
        assert secret not in captured.value.message
        assert secret not in repr(captured.value.details)

    asyncio.run(scenario())


def test_streaming_invalid_transport_payload_is_invalid_provider_response() -> None:
    provider = _stream_provider(ValueError("provider framing was invalid"))

    async def scenario() -> None:
        with pytest.raises(ContractError) as captured:
            async for _ in provider.stream(_stream_request("req-stream-invalid")):
                pass
        assert captured.value.code is ErrorCode.INVALID_PROVIDER_RESPONSE
        assert captured.value.retryable is False

    asyncio.run(scenario())


def test_streaming_propagates_task_cancellation() -> None:
    provider = _stream_provider(asyncio.CancelledError())

    async def scenario() -> None:
        with pytest.raises(asyncio.CancelledError):
            async for _ in provider.stream(_stream_request("req-stream-cancel")):
                pass

    asyncio.run(scenario())


class HermesFailureTransport:
    def __init__(self, outcome: BaseException | HermesHttpResponse) -> None:
        self.outcome = outcome

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
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _hermes_request() -> PlanRequest:
    return PlanRequest(
        task_id=new_id("task"),
        objective="verify provider boundary",
        context=OperationContext(
            correlation_id="corr-983-hermes",
            control=OperationControl(timeout_seconds=0.5),
        ),
    )


def _hermes(outcome: BaseException | HermesHttpResponse) -> HermesOrchestrator:
    return HermesOrchestrator(
        HermesAdapterConfig(enabled=True),
        transport=HermesFailureTransport(outcome),
        secret_resolver=lambda _: None,
    )


def test_hermes_network_failure_is_retryable_and_redacted() -> None:
    secret = "hermes-network-secret-983"
    orchestrator = _hermes(ConnectionError(f"socket bearer={secret}"))

    with pytest.raises(ContractError) as captured:
        asyncio.run(orchestrator.plan(_hermes_request()))

    assert captured.value.code is ErrorCode.UNAVAILABLE
    assert captured.value.retryable is True
    assert captured.value.details == {"exception_type": "ConnectionError"}
    assert secret not in captured.value.message
    assert secret not in repr(captured.value.details)


def test_hermes_unknown_transport_failure_is_non_retryable_and_redacted() -> None:
    secret = "hermes-sdk-secret-983"
    orchestrator = _hermes(RuntimeError(f"sdk bearer={secret}"))

    with pytest.raises(ContractError) as captured:
        asyncio.run(orchestrator.plan(_hermes_request()))

    assert captured.value.code is ErrorCode.BACKEND_ERROR
    assert captured.value.retryable is False
    assert captured.value.details == {"exception_type": "RuntimeError"}
    assert secret not in captured.value.message
    assert secret not in repr(captured.value.details)


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, ErrorCode.UNAUTHORIZED, False),
        (429, ErrorCode.RATE_LIMITED, True),
        (503, ErrorCode.UNAVAILABLE, True),
    ],
)
def test_hermes_http_status_translation_does_not_expose_provider_payload(
    status: int,
    code: ErrorCode,
    retryable: bool,
) -> None:
    secret = "raw-provider-payload-secret-983"
    orchestrator = _hermes(HermesHttpResponse(status, {"detail": secret}))

    with pytest.raises(ContractError) as captured:
        asyncio.run(orchestrator.plan(_hermes_request()))

    assert captured.value.code is code
    assert captured.value.retryable is retryable
    assert captured.value.details == {"http_status": status}
    assert secret not in captured.value.message
    assert secret not in repr(captured.value.details)


@pytest.mark.parametrize(
    "process_signal", [asyncio.CancelledError(), KeyboardInterrupt(), SystemExit(9)]
)
def test_hermes_propagates_cancellation_and_process_control(process_signal: BaseException) -> None:
    orchestrator = _hermes(process_signal)

    with pytest.raises(type(process_signal)):
        asyncio.run(orchestrator.plan(_hermes_request()))
