"""Canonical Verification findings as #590 Context evidence.

Verification remains owned by #86. This adapter projects completed, task-scoped
Verification results into Context candidates without creating a second Verification
store or allowing reviewer prose to acquire instruction authority.
"""

from __future__ import annotations

import hashlib
import json

from ai_multi_agent_platform.verification import VerificationService

from .models import (
    ContextCandidate,
    ContextDataClassification,
    ContextEntryRole,
    ContextSourceRef,
    ContextSourceType,
    ContextTrust,
)
from .resolver import ContextSourceRequest


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class VerificationContextSourceAdapter:
    """Project completed canonical #86 findings into Context as untrusted evidence."""

    adapter_id = "platform.verification-context/v1"

    def __init__(self, verification: VerificationService) -> None:
        self.verification = verification

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        candidates: list[ContextCandidate] = []
        for verification_request, result in self.verification.history(task_id=request.task_id):
            if result is None:
                continue

            content = _canonical_json(
                {
                    "verification_id": verification_request.verification_id,
                    "verification_result_id": result.verification_result_id,
                    "task_id": verification_request.task_id,
                    "run_id": verification_request.run_id,
                    "project_id": verification_request.project_id,
                    "policy_id": verification_request.policy_id,
                    "policy_version": verification_request.policy_version,
                    "stage_id": verification_request.stage_id,
                    "subject": {
                        "type": result.subject.subject_type,
                        "id": result.subject.subject_id,
                        "revision": result.subject.revision,
                        "digest": result.subject.digest,
                    },
                    "outcome": result.outcome.value,
                    "verifier": {
                        "kind": result.verifier.kind.value,
                        "ref": result.verifier.verifier_ref,
                        "agent_id": result.verifier.agent_id,
                        "agent_revision": result.verifier.agent_revision,
                        "model_config_id": result.verifier.model_config_id,
                        "provider_id": result.verifier.provider_id,
                    },
                    "findings": [
                        {
                            "code": finding.code,
                            "message": finding.message,
                            "severity": finding.severity,
                            "location_ref": finding.location_ref,
                        }
                        for finding in result.findings
                    ],
                    "evidence_artifact_ids": list(result.evidence_artifact_ids),
                    "checks_executed": list(result.checks_executed),
                    "errors": [
                        {"code": error.code, "retryable": error.retryable}
                        for error in result.errors
                    ],
                    "completed_at": result.completed_at.isoformat(),
                }
            )
            digest = _digest(content)
            candidates.append(
                ContextCandidate(
                    source=ContextSourceRef(
                        ContextSourceType.VERIFICATION,
                        result.verification_result_id,
                        revision=verification_request.verification_id,
                        digest=digest,
                        locator=f"verification:{verification_request.verification_id}",
                    ),
                    role=ContextEntryRole.EVIDENCE,
                    selection_reason=(
                        "canonical completed Verification result/findings linked to Task history"
                    ),
                    inline_content=content,
                    content_digest=digest,
                    trust=ContextTrust.UNTRUSTED,
                    data_classification=ContextDataClassification.INTERNAL,
                    priority=65,
                    relevance=0.9,
                    project_id=verification_request.project_id,
                    conflict_key=f"verification:{verification_request.verification_id}",
                    metadata={
                        "verification_id": verification_request.verification_id,
                        "verification_result_id": result.verification_result_id,
                        "policy_id": verification_request.policy_id,
                        "policy_version": verification_request.policy_version,
                        "stage_id": verification_request.stage_id,
                        "outcome": result.outcome.value,
                    },
                )
            )
        return tuple(candidates)


__all__ = ["VerificationContextSourceAdapter"]
