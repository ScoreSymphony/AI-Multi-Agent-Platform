"""#86 adapter for trusted Research portability binding restoration."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.verification import VerificationOutcome, VerificationService

from .models import ResearchVerificationBinding


def canonical_verification_binding_validator(
    verification: VerificationService,
):
    """Return a validator that accepts only an exact, locally completed #86 PASS.

    Serialized Research metadata is insufficient. The target deployment must already possess the
    canonical Verification request/result and its exact subject revision/digest must match the
    binding being restored.
    """

    def validate(binding: ResearchVerificationBinding) -> bool:
        try:
            request = verification.get_request(binding.verification_id)
            result = verification.result_for(binding.verification_id)
        except ContractError:
            return False
        if result is None or result.outcome is not VerificationOutcome.PASS:
            return False
        subject = request.subject
        return (
            result.subject == subject
            and subject.subject_type == binding.subject_type.value
            and subject.subject_id == binding.subject_id
            and subject.revision == binding.subject_revision
            and subject.digest == binding.subject_digest
        )

    return validate


__all__ = ["canonical_verification_binding_validator"]
