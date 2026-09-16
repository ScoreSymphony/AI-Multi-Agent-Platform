"""Shared safety helpers for the public Control Plane/ASGI boundary.

The canonical error taxonomy remains ``ContractError`` / ``ErrorCode``. This
module only owns translation and diagnostics at the outer northbound boundary;
it deliberately does not introduce another exception hierarchy.
"""

from __future__ import annotations

import logging

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode

from .models import APIException, api_exception_from_contract

_LOGGER = logging.getLogger(__name__)
_PUBLIC_BACKEND_MESSAGE = "internal platform operation failed"


def api_exception_for_boundary(error: Exception) -> APIException:
    """Map one boundary exception to the canonical public API contract.

    Existing typed errors retain their status/code/retryability/details. Unknown
    implementation failures collapse to the existing ``BACKEND_ERROR`` code and
    expose only the implementation type, never the exception message/cause.
    """

    if isinstance(error, APIException):
        return error
    if isinstance(error, ContractError):
        return api_exception_from_contract(error)
    return api_exception_from_contract(
        ContractError(
            ErrorCode.BACKEND_ERROR,
            _PUBLIC_BACKEND_MESSAGE,
            details={"exception_type": type(error).__name__},
        )
    )


def log_unexpected_boundary_error(
    error: Exception,
    *,
    boundary: str,
    request_id: str | None = None,
    correlation_id: str | None = None,
) -> None:
    """Emit secret-safe ownership diagnostics without serializing ``str(error)``.

    A traceback or raw exception message can contain credentials/provider payloads,
    so the boundary log intentionally contains only type plus already-canonical
    correlation identifiers. Typed/expected API errors should not call this helper.
    """

    _LOGGER.error(
        "unexpected platform boundary failure boundary=%s exception_type=%s "
        "request_id=%s correlation_id=%s",
        boundary,
        type(error).__name__,
        request_id,
        correlation_id,
    )


def safe_lifespan_failure_message(
    operation: str,
    error: Exception,
    *,
    cleanup_error: Exception | None = None,
) -> str:
    """Return an operator-visible ASGI lifespan message without raw exception text."""

    message = f"{operation} failed ({type(error).__name__})"
    if cleanup_error is not None:
        message += f"; rollback failed ({type(cleanup_error).__name__})"
    return message
