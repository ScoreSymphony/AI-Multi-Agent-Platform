from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.verification.reviewer_recovery import _recovery_failure_reason


def test_recovery_failure_reason_preserves_contract_code_without_private_message() -> None:
    private_detail = "provider-secret-token"

    reason = _recovery_failure_reason(
        "automatic reviewer recovery blocked",
        ContractError(
            ErrorCode.UNAVAILABLE,
            f"reviewer provider failed with {private_detail}",
            retryable=True,
        ),
    )

    assert reason == "automatic reviewer recovery blocked: unavailable"
    assert private_detail not in reason


def test_recovery_failure_reason_uses_exception_type_for_untyped_failures() -> None:
    private_detail = "implementation-secret-token"

    reason = _recovery_failure_reason(
        "cannot resolve canonical Task during reviewer recovery",
        RuntimeError(private_detail),
    )

    assert reason == "cannot resolve canonical Task during reviewer recovery: RuntimeError"
    assert private_detail not in reason
