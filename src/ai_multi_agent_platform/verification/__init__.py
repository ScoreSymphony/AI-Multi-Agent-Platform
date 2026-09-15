"""Canonical runtime verification, review and completion-policy subsystem."""

from .async_canonical_access import (
    AsyncCanonicalVerificationService,
    AsyncCanonicalVerificationServiceAdapter,
    runtime_canonical_verification_service,
)
from .async_persistence import (
    AsyncVerificationCompletionAuthority,
    AsyncVerificationCompletionAuthorityAdapter,
    AsyncVerificationService,
    AsyncVerificationServiceAdapter,
    VerificationPersistenceOffload,
    runtime_verification_completion,
    runtime_verification_service,
)
from .async_runtime import AsyncCanonicalVerificationRuntime
from .audit import VerificationAuditEvent, VerificationAuditEventType
from .canonical_access import CanonicalVerificationAccess
from .deterministic import (
    DeterministicCheck,
    DeterministicVerifier,
    ReferenceDeterministicVerifier,
)
from .evidence import CanonicalVerificationRuntime as SynchronousCanonicalVerificationRuntime
from .evidence import (
    KernelFileVerificationEvidenceResolver,
    VerificationEvidenceContext,
    VerificationEvidenceResolver,
)
from .gate import (
    AsyncOutputChangeAwareCompletionAuthority,
    CompletionAuthority,
    CompletionGateDecision,
    OutputChangeAwareCompletionAuthority,
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
from .verification_authority import VerificationService

CanonicalVerificationRuntime = AsyncCanonicalVerificationRuntime

__all__ = [
    "AsyncCanonicalVerificationRuntime",
    "AsyncCanonicalVerificationService",
    "AsyncCanonicalVerificationServiceAdapter",
    "AsyncOutputChangeAwareCompletionAuthority",
    "AsyncVerificationCompletionAuthority",
    "AsyncVerificationCompletionAuthorityAdapter",
    "AsyncVerificationService",
    "AsyncVerificationServiceAdapter",
    "CanonicalVerificationAccess",
    "CanonicalVerificationRuntime",
    "CompletionAssessment",
    "CompletionAuthority",
    "CompletionGateDecision",
    "CompletionState",
    "OutputChangeAwareCompletionAuthority",
    "DeterministicCheck",
    "DeterministicVerifier",
    "KernelFileVerificationEvidenceResolver",
    "ProducerIdentity",
    "ReferenceDeterministicVerifier",
    "ReviewerIndependence",
    "SqliteVerificationCompletionAuthority",
    "SqliteVerificationService",
    "SynchronousCanonicalVerificationRuntime",
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
    "VerificationPersistenceOffload",
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
    "runtime_canonical_verification_service",
    "runtime_verification_completion",
    "runtime_verification_service",
]
