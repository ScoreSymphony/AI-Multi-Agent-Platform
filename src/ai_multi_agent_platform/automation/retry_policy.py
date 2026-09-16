"""Deterministic Automation delivery retry semantics.

This module deliberately contains policy only. Durable retry persistence and runtime wakeups
consume these helpers so the canonical Automation model does not depend on one scheduler or
workflow engine implementation.

Historical context: introduced for #241.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum

from ai_multi_agent_platform.contracts.errors import ErrorCode

from .models import RetryPolicy, require_aware


class RetryDisposition(StrEnum):
    """Stable classification for delivery-processing failures."""

    RETRYABLE = "retryable"
    TERMINAL = "terminal"


# Operational failures that are safe to retry through the same canonical Task-admission path.
# BACKEND_ERROR is intentionally absent: an unknown implementation/backend failure is terminal
# unless its owning boundary explicitly sets ContractError.retryable.
_RETRYABLE_ERROR_CODES = frozenset(
    {
        ErrorCode.MODEL_UNAVAILABLE.value,
        ErrorCode.UNAVAILABLE.value,
        ErrorCode.TIMEOUT.value,
        ErrorCode.RATE_LIMITED.value,
        ErrorCode.RESOURCE_EXHAUSTED.value,
        ErrorCode.TRANSIENT_FAILURE.value,
    }
)


# These canonical categories are semantically stable for an unchanged delivery. A stale or buggy
# boundary must not turn them into automatic retries merely by setting retryable=True.
_HARD_TERMINAL_ERROR_CODES = frozenset(
    {
        ErrorCode.INVALID_REQUEST.value,
        ErrorCode.INVALID_CONFIGURATION.value,
        ErrorCode.UNSUPPORTED_CAPABILITY.value,
        ErrorCode.NOT_FOUND.value,
        ErrorCode.NO_COMPATIBLE_ROUTE.value,
        ErrorCode.INPUT_TOO_LARGE.value,
        ErrorCode.INVALID_PROVIDER_RESPONSE.value,
        ErrorCode.CONFLICT.value,
        ErrorCode.CANCELLED.value,
        ErrorCode.UNAUTHORIZED.value,
        ErrorCode.FORBIDDEN.value,
        ErrorCode.PERMANENT_FAILURE.value,
        ErrorCode.CONTRACT_VIOLATION.value,
    }
)


# The AutomationService catch-all TaskCreator category represents an unexpected implementation
# failure rather than a typed operational condition. It remains terminal even if older durable
# evidence or callers still carry retryable_hint=True.
_UNEXPECTED_IMPLEMENTATION_ERROR_CODES = frozenset({"automation_task_creation_failed"})


def classify_delivery_failure(
    error_code: str | None,
    *,
    retryable_hint: bool = False,
) -> RetryDisposition:
    """Classify one failed TriggerDelivery for automatic retry.

    Stable canonical contract/configuration/authorization failures and cancellation always remain
    terminal. For generic backend or forward-compatible custom categories, an explicit retryable
    hint may still carry boundary-local knowledge that the concrete failure is transient. Unknown
    categories without that hint fail closed as terminal.
    """

    if error_code in _UNEXPECTED_IMPLEMENTATION_ERROR_CODES:
        return RetryDisposition.TERMINAL
    if error_code in _HARD_TERMINAL_ERROR_CODES:
        return RetryDisposition.TERMINAL
    if retryable_hint:
        return RetryDisposition.RETRYABLE
    if error_code in _RETRYABLE_ERROR_CODES:
        return RetryDisposition.RETRYABLE
    return RetryDisposition.TERMINAL


def retry_delay_seconds(policy: RetryPolicy, *, failed_attempt: int) -> float:
    """Return deterministic exponential backoff for the next attempt.

    Attempt numbering follows TriggerDelivery.attempt: the initial processing pass is attempt 1.
    Therefore a failure of attempt 1 waits ``base_backoff_seconds``; attempt 2 waits twice the
    base; attempt 3 waits four times the base, and so on.

    A base of zero remains valid canonical policy. The reference runtime must still clamp a due
    zero-delay retry to a non-spinning wakeup path after a repeated failure.
    """

    if failed_attempt < 1:
        raise ValueError("failed_attempt must be at least 1")
    multiplier = float(2 ** (failed_attempt - 1))
    return policy.base_backoff_seconds * multiplier


def next_retry_at(
    policy: RetryPolicy,
    *,
    failed_attempt: int,
    failed_at: datetime,
) -> datetime:
    """Compute the deterministic UTC retry deadline for one failed attempt."""

    occurred = require_aware(failed_at, "failed_at").astimezone(UTC)
    try:
        delay = timedelta(seconds=retry_delay_seconds(policy, failed_attempt=failed_attempt))
    except OverflowError as exc:
        raise ValueError("retry backoff exceeds datetime range") from exc
    try:
        return occurred + delay
    except OverflowError as exc:
        raise ValueError("retry deadline exceeds datetime range") from exc


def retry_exhausted(policy: RetryPolicy, *, completed_attempts: int) -> bool:
    """Return whether no further automatic or manual processing attempt is permitted."""

    if completed_attempts < 0:
        raise ValueError("completed_attempts must not be negative")
    return completed_attempts >= policy.max_attempts
