"""Trusted platform-domain access to strict canonical Verification (#86).

Strict Verification rejects caller-supplied raw subjects/results. Canonical platform domains
therefore need one owned seam through which their state resolvers can submit an exact subject and
later revalidate that exact subject before recording a result. The private capability tokens remain
owned by this package; external clients, Agents and provider adapters do not receive them.
"""

from __future__ import annotations

from datetime import datetime

from .models import ProducerIdentity, VerificationRequest, VerificationResult, VerificationSubject
from .service import (
    _CANONICAL_RESULT_TOKEN,
    _CANONICAL_SUBJECT_TOKEN,
    VerificationService,
)


class CanonicalVerificationAccess:
    """Narrow seam for platform-owned canonical subject resolvers.

    This class does not resolve evidence and does not weaken #86 policy. Callers remain responsible
    for deriving ``subject`` from canonical state and for re-resolving it before ``submit_result``.
    VerificationService still owns policy, reviewer independence, lifecycle and result acceptance.
    """

    def __init__(self, verification: VerificationService) -> None:
        self.verification = verification

    def request_verification(
        self,
        *,
        task_id: str,
        policy_id: str,
        policy_version: int,
        stage_id: str,
        subject: VerificationSubject,
        correlation_id: str,
        run_id: str | None = None,
        result_id: str | None = None,
        artifact_ids: tuple[str, ...] = (),
        project_id: str | None = None,
        capability_ids: tuple[str, ...] = (),
        producer: ProducerIdentity | None = None,
        repair_attempt: int = 0,
        causation_id: str | None = None,
        now: datetime | None = None,
    ) -> VerificationRequest:
        return self.verification.request_verification(
            task_id=task_id,
            policy_id=policy_id,
            policy_version=policy_version,
            stage_id=stage_id,
            subject=subject,
            correlation_id=correlation_id,
            run_id=run_id,
            result_id=result_id,
            artifact_ids=artifact_ids,
            project_id=project_id,
            capability_ids=capability_ids,
            producer=producer,
            repair_attempt=repair_attempt,
            causation_id=causation_id,
            now=now,
            _canonical_subject_token=_CANONICAL_SUBJECT_TOKEN,
        )

    def submit_result(self, result: VerificationResult) -> VerificationResult:
        return self.verification.submit_result(
            result,
            _canonical_result_token=_CANONICAL_RESULT_TOKEN,
        )
