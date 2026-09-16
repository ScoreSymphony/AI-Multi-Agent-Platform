"""Shared failure classification for distributed Worker delivery boundaries."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .registry import RegistryError


def delivery_failure_retryable(error: Exception) -> bool:
    """Return whether an escaped delivery-boundary failure is safe to redeliver.

    Unknown exceptions fail closed. Redelivery is reserved for failures that are explicitly
    retryable in the canonical ContractError taxonomy or for the narrow built-in transport
    failure families that can occur before a typed adapter boundary is reached.
    """

    if isinstance(error, ContractError):
        return error.retryable
    return isinstance(error, (TimeoutError, ConnectionError))


def workspace_error_semantics(error: Exception) -> tuple[ErrorCode, bool]:
    """Translate Worker Workspace failures without inventing a second error taxonomy."""

    if isinstance(error, ContractError):
        return error.code, error.retryable
    if isinstance(error, (TimeoutError, ConnectionError)):
        return ErrorCode.UNAVAILABLE, True
    if isinstance(error, (RegistryError, ValueError, TypeError)):
        return ErrorCode.CONTRACT_VIOLATION, False
    return ErrorCode.BACKEND_ERROR, False


__all__ = ["delivery_failure_retryable", "workspace_error_semantics"]
