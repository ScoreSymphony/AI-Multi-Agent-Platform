from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.automation.models import RetryPolicy
from ai_multi_agent_platform.automation.retry_policy import (
    RetryDisposition,
    classify_delivery_failure,
    next_retry_at,
    retry_delay_seconds,
    retry_exhausted,
)
from ai_multi_agent_platform.contracts.errors import ErrorCode


def test_retryable_operational_failures_are_classified_explicitly() -> None:
    for code in (
        ErrorCode.UNAVAILABLE.value,
        ErrorCode.TIMEOUT.value,
        ErrorCode.RATE_LIMITED.value,
        ErrorCode.RESOURCE_EXHAUSTED.value,
        ErrorCode.TRANSIENT_FAILURE.value,
        ErrorCode.BACKEND_ERROR.value,
        "automation_task_creation_failed",
    ):
        assert classify_delivery_failure(code) is RetryDisposition.RETRYABLE


def test_stable_configuration_and_authorization_failures_are_terminal() -> None:
    for code in (
        ErrorCode.INVALID_REQUEST.value,
        ErrorCode.INVALID_CONFIGURATION.value,
        ErrorCode.UNSUPPORTED_CAPABILITY.value,
        ErrorCode.NOT_FOUND.value,
        ErrorCode.UNAUTHORIZED.value,
        ErrorCode.FORBIDDEN.value,
        ErrorCode.PERMANENT_FAILURE.value,
        ErrorCode.CONTRACT_VIOLATION.value,
    ):
        assert classify_delivery_failure(code) is RetryDisposition.TERMINAL


def test_unknown_failure_code_fails_closed_but_retryable_hint_overrides() -> None:
    assert classify_delivery_failure("future_unknown_code") is RetryDisposition.TERMINAL
    assert (
        classify_delivery_failure("future_unknown_code", retryable_hint=True)
        is RetryDisposition.RETRYABLE
    )


def test_exponential_backoff_uses_failed_attempt_number() -> None:
    policy = RetryPolicy(max_attempts=5, base_backoff_seconds=2.5)

    assert retry_delay_seconds(policy, failed_attempt=1) == 2.5
    assert retry_delay_seconds(policy, failed_attempt=2) == 5.0
    assert retry_delay_seconds(policy, failed_attempt=3) == 10.0


def test_next_retry_at_is_deterministic_and_normalized_to_utc() -> None:
    policy = RetryPolicy(max_attempts=3, base_backoff_seconds=4)
    failed_at = datetime(2026, 9, 4, 1, 30, tzinfo=UTC)

    first = next_retry_at(policy, failed_attempt=2, failed_at=failed_at)
    second = next_retry_at(policy, failed_attempt=2, failed_at=failed_at)

    assert first == second == failed_at + timedelta(seconds=8)
    assert first.tzinfo is UTC


def test_zero_base_backoff_is_preserved_for_runtime_to_clamp_safely() -> None:
    policy = RetryPolicy(max_attempts=3, base_backoff_seconds=0)
    failed_at = datetime(2026, 9, 4, tzinfo=UTC)

    assert retry_delay_seconds(policy, failed_attempt=1) == 0
    assert next_retry_at(policy, failed_attempt=1, failed_at=failed_at) == failed_at


def test_retry_exhaustion_counts_initial_processing_as_attempt_one() -> None:
    policy = RetryPolicy(max_attempts=3, base_backoff_seconds=1)

    assert retry_exhausted(policy, completed_attempts=0) is False
    assert retry_exhausted(policy, completed_attempts=1) is False
    assert retry_exhausted(policy, completed_attempts=2) is False
    assert retry_exhausted(policy, completed_attempts=3) is True
    assert retry_exhausted(policy, completed_attempts=4) is True


def test_invalid_attempt_numbers_are_rejected() -> None:
    policy = RetryPolicy()

    with pytest.raises(ValueError, match="failed_attempt must be at least 1"):
        retry_delay_seconds(policy, failed_attempt=0)
    with pytest.raises(ValueError, match="completed_attempts must not be negative"):
        retry_exhausted(policy, completed_attempts=-1)
