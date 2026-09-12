"""Parallel coding-batch composition for issue #872."""

from .authorization import AuthorizedCodingBatchIntegration, CodingBatchAuthorizationContext
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
    "CanonicalCodingVerificationCoordinator",
    "CanonicalCodingWorkstreamDispatcher",
    "CanonicalWorkstreamMaterializer",
    "CheckState",
    "CodingBatch",
    "CodingBatchAuthorizationContext",
    "CodingBatchCoordinator",
    "CodingBatchRepairCoordinator",
    "CodingBatchStore",
    "CodingBatchTelemetry",
    "CodingDispatchSlot",
    "CodingWorkItem",
    "CodingWorkstream",
    "CodingWorkstreamDispatch",
    "CombinedValidationEvidence",
    "ConservativeOverlapClassifier",
    "DEFAULT_MAX_REPAIR_ATTEMPTS",
    "InMemoryCodingBatchStore",
    "IntegrationCandidate",
    "IntegrationConflict",
    "IntegrationRepairAttempt",
    "IntegrationState",
    "OverlapDecision",
    "OverlapKind",
    "RepairAttemptState",
    "RequiredCheck",
    "SqliteCodingBatchStore",
    "VerificationEvidence",
    "WorkstreamProvenance",
    "WorkstreamResult",
    "WorkstreamState",
    "agent_revision_ref",
    "coding_batch_resource",
    "deterministic_branch_ref",
    "deterministic_workspace_id",
]
