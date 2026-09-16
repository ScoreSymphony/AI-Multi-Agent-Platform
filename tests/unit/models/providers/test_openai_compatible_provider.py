"""OpenAI-compatible provider unit coverage."""

import asyncio

import openai_provider_cases as cases
import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, ModelRequest

test_local_provider_lists_models_without_paid_credentials = (
    cases.test_local_provider_lists_models_without_paid_credentials
)
test_missing_secret_reference_fails_as_configuration_error = (
    cases.test_missing_secret_reference_fails_as_configuration_error
)
test_provider_maps_http_failures_to_canonical_errors = (
    cases.test_provider_maps_http_failures_to_canonical_errors
)
test_provider_maps_timeout_without_leaking_transport_exception = (
    cases.test_provider_maps_timeout_without_leaking_transport_exception
)
test_provider_rejects_invalid_provider_response_canonically = (
    cases.test_provider_rejects_invalid_provider_response_canonically
)
test_provider_requires_canonical_target_when_multiple_models_are_configured = (
    cases.test_provider_requires_canonical_target_when_multiple_models_are_configured
)


def _request(request_id: str) -> ModelRequest:
    return ModelRequest(
        request_id=request_id,
        messages=("hello",),
        context=cases.CTX,
        requirements={"model_config_id": "model-local-coder"},
    )


def test_provider_maps_network_failure_without_secret_leak() -> None:
    secret = "provider-token-sensitive-value"
    transport = cases.RecordingTransport()
    transport.fail_with = ConnectionError(f"connection failed bearer={secret}")
    provider = cases.make_provider(transport)

    with pytest.raises(ContractError) as captured:
        asyncio.run(provider.generate(_request("req-network-failure")))

    assert captured.value.code is ErrorCode.UNAVAILABLE
    assert captured.value.retryable is True
    assert captured.value.details == {"exception_type": "ConnectionError"}
    assert secret not in captured.value.message
    assert secret not in repr(captured.value.details)


def test_provider_contains_unknown_transport_failure_as_non_retryable() -> None:
    secret = "provider-token-unknown-sensitive-value"
    transport = cases.RecordingTransport()
    transport.fail_with = RuntimeError(f"unexpected sdk state bearer={secret}")
    provider = cases.make_provider(transport)

    with pytest.raises(ContractError) as captured:
        asyncio.run(provider.generate(_request("req-unknown-transport")))

    assert captured.value.code is ErrorCode.BACKEND_ERROR
    assert captured.value.retryable is False
    assert captured.value.details == {"exception_type": "RuntimeError"}
    assert secret not in captured.value.message
    assert secret not in repr(captured.value.details)


def test_provider_propagates_task_cancellation() -> None:
    transport = cases.RecordingTransport()
    transport.fail_with = asyncio.CancelledError()
    provider = cases.make_provider(transport)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(provider.generate(_request("req-cancelled")))


@pytest.mark.parametrize("process_signal", [KeyboardInterrupt(), SystemExit(7)])
def test_provider_propagates_process_control(process_signal: BaseException) -> None:
    transport = cases.RecordingTransport()
    transport.fail_with = process_signal
    provider = cases.make_provider(transport)

    with pytest.raises(type(process_signal)):
        asyncio.run(provider.generate(_request("req-process-control")))
