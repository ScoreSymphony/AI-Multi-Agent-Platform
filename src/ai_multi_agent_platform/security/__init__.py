"""Cross-cutting platform security baseline."""

from .paths import PathSecurityError, resolve_within
from .policy import baseline_decision
from .types import ExternalSideEffect, SecurityAuditEvent, SecurityContext, SecurityDecision
from .validation import UntrustedInputError, validate_untrusted_json

__all__ = [
    "ExternalSideEffect",
    "PathSecurityError",
    "SecurityAuditEvent",
    "SecurityContext",
    "SecurityDecision",
    "UntrustedInputError",
    "baseline_decision",
    "resolve_within",
    "validate_untrusted_json",
]
