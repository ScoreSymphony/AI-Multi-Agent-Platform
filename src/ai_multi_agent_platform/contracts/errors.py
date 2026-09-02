"""Provider-neutral error semantics for adapter and core boundaries."""

from __future__ import annotations

from enum import StrEnum

from .types import AdapterMetadata, JsonValue


class ErrorCode(StrEnum):
    """Stable error categories that callers may handle without backend knowledge."""

    INVALID_REQUEST = "invalid_request"
    INVALID_CONFIGURATION = "invalid_configuration"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    NOT_FOUND = "not_found"
    MODEL_UNAVAILABLE = "model_unavailable"
    NO_COMPATIBLE_ROUTE = "no_compatible_route"
    INPUT_TOO_LARGE = "input_too_large"
    INVALID_PROVIDER_RESPONSE = "invalid_provider_response"
    CONFLICT = "conflict"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    RATE_LIMITED = "rate_limited"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    TRANSIENT_FAILURE = "transient_failure"
    PERMANENT_FAILURE = "permanent_failure"
    CONTRACT_VIOLATION = "contract_violation"
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
        adapter_metadata: tuple[AdapterMetadata, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.provider_id = provider_id
        self.details = details or {}
        self.adapter_metadata = adapter_metadata
