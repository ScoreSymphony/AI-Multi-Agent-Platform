"""Compatibility import for the canonical verification authority.

Canonical implementation lives in
:mod:`ai_multi_agent_platform.verification.verification_authority`.
New production imports must use that module or the supported package-level exports.
"""

from .verification_authority import *  # noqa: F403
