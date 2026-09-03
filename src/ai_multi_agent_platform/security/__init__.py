"""Cross-cutting platform security and authorization surface."""

from .approvals import ApprovalRecord, ApprovalService
from .authorization import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationAuditRecord,
    AuthorizationContext,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ProposedAction,
    ResourceType,
    RiskClassification,
    infer_actor_identity,
)
from .enforcement import AuthorizationAuditSink, AuthorizationGate
from .paths import PathSecurityError, resolve_within
from .policy import baseline_decision
from .redaction import REDACTED, redact_exception, redact_sensitive, redact_text
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
    "ActorIdentity",
    "ActorType",
    "ApprovalRecord",
    "ApprovalService",
    "AuthorizationAction",
    "AuthorizationAuditRecord",
    "AuthorizationAuditSink",
    "AuthorizationContext",
    "AuthorizationGate",
    "ExternalSideEffect",
    "LocalAuthorizationProvider",
    "LocalPrincipalPolicy",
    "PathSecurityError",
    "ProposedAction",
    "ResourceType",
    "RiskClassification",
    "SecretReference",
    "SecurityAuditEvent",
    "SecurityContext",
    "SecurityDecision",
    "UntrustedInputError",
    "baseline_decision",
    "infer_actor_identity",
    "redact_exception",
    "redact_sensitive",
    "redact_text",
    "resolve_within",
    "validate_untrusted_json",
]
