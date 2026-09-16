"""Typed error translation for distributed application-build lifecycle boundaries."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.distributed.registry import RegistryError
from ai_multi_agent_platform.distributed.transport import RemoteWorkerTransportError


def registry_error(
    exc: RegistryError,
    *,
    provider_id: str,
    unknown_is_not_found: bool = False,
) -> ContractError:
    """Translate distributed runtime failures into stable application-build contracts."""

    if isinstance(exc, RemoteWorkerTransportError):
        if exc.retryable:
            return ContractError(
                ErrorCode.UNAVAILABLE,
                "remote Worker transport is temporarily unavailable",
                retryable=True,
                provider_id=provider_id,
            )
        if exc.category in {
            "worker_contract_error",
            "worker_identity_mismatch",
            "result_unsupported",
        }:
            return ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "remote Worker transport rejected the operation",
                provider_id=provider_id,
            )
        return ContractError(
            ErrorCode.BACKEND_ERROR,
            "remote Worker transport operation failed",
            provider_id=provider_id,
        )

    message = str(exc)
    if unknown_is_not_found and "unknown dispatched worker job" in message:
        return ContractError(ErrorCode.NOT_FOUND, message, provider_id=provider_id)
    if (
        "no attached dispatcher" in message
        or "not currently reachable" in message
        or "worker result is not currently reachable" in message
    ):
        return ContractError(
            ErrorCode.UNAVAILABLE,
            message,
            retryable=True,
            provider_id=provider_id,
        )
    return ContractError(ErrorCode.CONFLICT, message, provider_id=provider_id)


__all__ = ["registry_error"]
