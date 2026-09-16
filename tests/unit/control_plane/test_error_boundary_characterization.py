from __future__ import annotations

import pytest

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.models import api_exception_from_contract


@pytest.mark.parametrize(
    ("code", "status", "retryable"),
    [
        (ErrorCode.UNAVAILABLE, 503, True),
        (ErrorCode.TIMEOUT, 504, True),
        (ErrorCode.RATE_LIMITED, 429, True),
        (ErrorCode.UNAUTHORIZED, 401, False),
        (ErrorCode.FORBIDDEN, 403, False),
        (ErrorCode.INVALID_PROVIDER_RESPONSE, 502, False),
        (ErrorCode.TRANSIENT_FAILURE, 503, True),
        (ErrorCode.PERMANENT_FAILURE, 422, False),
        (ErrorCode.CONTRACT_VIOLATION, 500, False),
        (ErrorCode.BACKEND_ERROR, 502, False),
    ],
)
def test_contract_error_northbound_status_and_retryability_are_stable(
    code: ErrorCode,
    status: int,
    retryable: bool,
) -> None:
    error = ContractError(
        code,
        "safe public message",
        retryable=retryable,
        details={"operation": "characterization"},
    )

    api_error = api_exception_from_contract(error)

    assert api_error.status == status
    assert api_error.code == code.value
    assert api_error.retryable is retryable
    assert api_error.message == "safe public message"
    assert api_error.details == {"operation": "characterization"}


def test_private_cause_is_not_serialized_into_control_plane_error() -> None:
    sensitive_marker = "provider-private-cause-must-not-cross-boundary"
    try:
        raise RuntimeError(sensitive_marker)
    except RuntimeError as exc:
        try:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "provider request failed",
                details={"operation": "generate", "exception_type": "RuntimeError"},
            ) from exc
        except ContractError as error:
            api_error = api_exception_from_contract(error)

    serialized = {
        "status": api_error.status,
        "code": api_error.code,
        "message": api_error.message,
        "retryable": api_error.retryable,
        "details": api_error.details,
    }
    assert sensitive_marker not in repr(serialized)
    assert api_error.details == {"operation": "generate", "exception_type": "RuntimeError"}
