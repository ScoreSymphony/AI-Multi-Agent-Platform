"""Provider-neutral error semantics for adapter and core boundaries."""

from __future__ import annotations

from enum import StrEnum

from .types import JsonValue


class ErrorCode(StrEnum):
    """Stable error categories that callers may handle without backend knowledge."""

    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    BACKEND_ERROR = "backend_error"


class ContractError(Exception):
    """Base exception crossing a platform provider boundary.

    Provider SDK exceptions must be translated to this type (or a future
    documented subtype) before they reach unrelated platform modules.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        provider_id: str | None = None,
        details: dict[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.provider_id = provider_id
        self.details = details or {}
