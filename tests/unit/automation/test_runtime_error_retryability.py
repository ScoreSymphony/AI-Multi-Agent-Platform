from __future__ import annotations

from ai_multi_agent_platform.automation.runtime import _runtime_event_error_is_retryable
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode


def test_backend_error_is_terminal_without_explicit_retryable_hint() -> None:
    error = ContractError(ErrorCode.BACKEND_ERROR, "unexpected backend failure")

    assert _runtime_event_error_is_retryable(error) is False


def test_backend_error_respects_explicit_retryable_hint() -> None:
    error = ContractError(
        ErrorCode.BACKEND_ERROR,
        "known transient backend failure",
        retryable=True,
    )

    assert _runtime_event_error_is_retryable(error) is True


def test_known_transient_event_error_remains_retryable() -> None:
    error = ContractError(ErrorCode.UNAVAILABLE, "temporarily unavailable")

    assert _runtime_event_error_is_retryable(error) is True
