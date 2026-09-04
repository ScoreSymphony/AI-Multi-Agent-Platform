"""Canonical runtime verification, review and completion-policy subsystem."""

from .audit import VerificationAuditEvent, VerificationAuditEventType
from .deterministic import DeterministicCheck, ReferenceDeterministicVerifier
from .evidence import (
    CanonicalVerificationRuntime,
    KernelFileVerificationEvidenceResolver,
    VerificationEvidenceContext,
    VerificationEvidenceResolver,
)
from .gate import (
    CompletionAuthority,
    CompletionGateDecision,
    TaskVerificationRequirement,
    VerificationCompletionAuthority,
)
from .models import (
    CompletionAssessment,
    CompletionState,
    ProducerIdentity,
    ReviewerIndependence,
    VerificationError,
    VerificationFailurePolicy,
    VerificationFinding,
    VerificationOutcome,
    VerificationPolicy,
    VerificationRequest,
    VerificationRequestStatus,
    VerificationResult,
    VerificationScope,
    VerificationStage,
    VerificationSubject,
    VerifierIdentity,
    VerifierKind,
)
from .persistence import (
    VERIFICATION_PERSISTENCE_SCHEMA_VERSION,
    SqliteVerificationCompletionAuthority,
    SqliteVerificationService,
)
from .service import VerificationService

__all__ = [
    "CompletionAssessment",
    "CompletionAuthority",
    "CompletionGateDecision",
    "CompletionState",
    "CanonicalVerificationRuntime",
    "DeterministicCheck",
    "KernelFileVerificationEvidenceResolver",
    "ProducerIdentity",
    "ReferenceDeterministicVerifier",
    "ReviewerIndependence",
    "SqliteVerificationCompletionAuthority",
    "SqliteVerificationService",
    "TaskVerificationRequirement",
    "VERIFICATION_PERSISTENCE_SCHEMA_VERSION",
    "VerificationAuditEvent",
    "VerificationAuditEventType",
    "VerificationCompletionAuthority",
    "VerificationError",
    "VerificationEvidenceContext",
    "VerificationEvidenceResolver",
    "VerificationFailurePolicy",
    "VerificationFinding",
    "VerificationOutcome",
    "VerificationPolicy",
    "VerificationRequest",
    "VerificationRequestStatus",
    "VerificationResult",
    "VerificationScope",
    "VerificationService",
    "VerificationStage",
    "VerificationSubject",
    "VerifierIdentity",
    "VerifierKind",
]
