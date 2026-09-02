"""Cross-cutting platform security baseline."""

from .paths import PathSecurityError, resolve_within
from .policy import baseline_decision
from .redaction import REDACTED, redact_sensitive
from .types import (
    ExternalSideEffect,
    SecretReference,
    SecurityAuditEvent,
    SecurityContext,
    SecurityDecision,
)
from .validation import UntrustedInputError, validate_untrusted_json

__all__ = [
    "REDACTED",
    "ExternalSideEffect",
    "PathSecurityError",
    "SecretReference",
    "SecurityAuditEvent",
    "SecurityContext",
    "SecurityDecision",
    "UntrustedInputError",
    "baseline_decision",
    "redact_sensitive",
    "resolve_within",
    "validate_untrusted_json",
]
