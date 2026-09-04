"""Deterministic reference verifier for canonical runtime verification."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .models import (
    VerificationFinding,
    VerificationOutcome,
    VerificationRequest,
    VerificationResult,
    VerifierIdentity,
    VerifierKind,
)


@dataclass(frozen=True, slots=True)
class DeterministicCheck:
    """One local deterministic predicate over an exact VerificationRequest."""

    name: str
    predicate: Callable[[VerificationRequest], bool]
    failure_message: str
    severity: str = "error"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("deterministic check name must not be blank")
        if not self.failure_message.strip():
            raise ValueError("deterministic check failure_message must not be blank")
        if not self.severity.strip():
            raise ValueError("deterministic check severity must not be blank")


class ReferenceDeterministicVerifier:
    """LLM-free verifier that passes only when every configured predicate passes."""

    def __init__(self, verifier_id: str, checks: Sequence[DeterministicCheck]) -> None:
        if not verifier_id.strip():
            raise ValueError("deterministic verifier_id must not be blank")
        if not checks:
            raise ValueError("deterministic verifier requires at least one check")
        self._verifier_id = verifier_id
        self._checks = tuple(checks)

    def verify(self, request: VerificationRequest) -> VerificationResult:
        if request.requested_verifier_kind is not VerifierKind.DETERMINISTIC:
            raise ValueError("verification request does not require deterministic verification")
        findings: list[VerificationFinding] = []
        checks_executed: list[str] = []
        for check in self._checks:
            checks_executed.append(check.name)
            if not check.predicate(request):
                findings.append(
                    VerificationFinding(
                        code="deterministic_check_failed",
                        message=check.failure_message,
                        severity=check.severity,
                    )
                )
        outcome = VerificationOutcome.PASS if not findings else VerificationOutcome.FAIL
        return VerificationResult(
            verification_id=request.verification_id,
            verifier=VerifierIdentity(
                verifier_ref=self._verifier_id,
                kind=VerifierKind.DETERMINISTIC,
                read_only=True,
            ),
            outcome=outcome,
            subject=request.subject,
            findings=tuple(findings),
            checks_executed=tuple(checks_executed),
        )
