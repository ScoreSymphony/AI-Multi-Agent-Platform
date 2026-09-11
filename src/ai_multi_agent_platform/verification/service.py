"""Compatibility imports for the canonical verification authority.

Canonical implementation lives in
:mod:`ai_multi_agent_platform.verification.verification_authority`.
New production imports must use that module or the supported package-level exports.
"""

from .verification_authority import _CANONICAL_RESULT_TOKEN as _CANONICAL_RESULT_TOKEN
from .verification_authority import _CANONICAL_SUBJECT_TOKEN as _CANONICAL_SUBJECT_TOKEN
from .verification_authority import VerificationService as VerificationService

__all__ = ["VerificationService"]
