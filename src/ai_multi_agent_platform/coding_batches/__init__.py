"""Parallel coding-batch composition for issue #872."""

from .authorization import AuthorizedCodingBatchIntegration, CodingBatchAuthorizationContext
from .integration_runtime import (
    CanonicalCodingIntegrationDispatcher,
    CodingIntegrationDispatch,
    IntegrationDispatchSlot,
    deterministic_integration_branch_ref,
    deterministic_integration_workspace_id,
)
from .materialization import CanonicalWorkstreamMaterializer, deterministic_workspace_id
from .models import (
    BatchAggregationPolicy,
    CheckState,
    CodingBatch,
    CodingWorkItem,
    CodingWorkstream,
    CombinedValidationEvidence,
    IntegrationCandidate,
    IntegrationConflict,
    IntegrationExecutionProvenance,
    IntegrationRepairAttempt,
    IntegrationState,
    OverlapDecision,
    OverlapKind,
    RepairAttemptState,
    RequiredCheck,
    VerificationEvidence,
    WorkstreamProvenance,
    WorkstreamResult,
    WorkstreamState,
)
from .overlap import ConservativeOverlapClassifier
from .projection import coding_batch_resource
from .repair import DEFAULT_MAX_REPAIR_ATTEMPTS, CodingBatchRepairCoordinator
from .repository_verification import (
    CanonicalCombinedValidationCoordinator,
    CanonicalRepairVerificationCoordinator,
    CanonicalRepositoryOutputVerifier,
    RepositoryRunEvidenceReader,
)
from .review import CanonicalCodingVerificationCoordinator
from .runtime import (
    CanonicalCodingWorkstreamDispatcher,
    CodingDispatchSlot,
    CodingWorkstreamDispatch,
    agent_revision_ref,
)
from .secured import CodingBatchCoordinator
from .service import CodingBatchStore, InMemoryCodingBatchStore, deterministic_branch_ref
from .sqlite_store import SqliteCodingBatchStore
from .telemetry import CodingBatchTelemetry

__all__ = [
    "AuthorizedCodingBatchIntegration",
    "BatchAggregationPolicy",
    "CanonicalCodingIntegrationDispatcher",
    "CanonicalCodingVerificationCoordinator",
    "CanonicalCodingWorkstreamDispatcher",
    "CanonicalCombinedValidationCoordinator",
    "CanonicalRepairVerificationCoordinator",
    "CanonicalRepositoryOutputVerifier",
    "CanonicalWorkstreamMaterializer",
    "CheckState",
    "CodingBatch",
    "CodingBatchAuthorizationContext",
    "CodingBatchCoordinator",
    "CodingBatchRepairCoordinator",
    "CodingBatchStore",
    "CodingBatchTelemetry",
    "CodingDispatchSlot",
    "CodingIntegrationDispatch",
    "CodingWorkItem",
    "CodingWorkstream",
    "CodingWorkstreamDispatch",
    "CombinedValidationEvidence",
    "ConservativeOverlapClassifier",
    "DEFAULT_MAX_REPAIR_ATTEMPTS",
    "InMemoryCodingBatchStore",
    "IntegrationCandidate",
    "IntegrationConflict",
    "IntegrationDispatchSlot",
    "IntegrationExecutionProvenance",
    "IntegrationRepairAttempt",
    "IntegrationState",
    "OverlapDecision",
    "OverlapKind",
    "RepairAttemptState",
    "RepositoryRunEvidenceReader",
    "RequiredCheck",
    "SqliteCodingBatchStore",
    "VerificationEvidence",
    "WorkstreamProvenance",
    "WorkstreamResult",
    "WorkstreamState",
    "agent_revision_ref",
    "coding_batch_resource",
    "deterministic_branch_ref",
    "deterministic_integration_branch_ref",
    "deterministic_integration_workspace_id",
    "deterministic_workspace_id",
]
