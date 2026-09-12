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
from .service import (
    CodingBatchCoordinator,
    CodingBatchStore,
    InMemoryCodingBatchStore,
    deterministic_branch_ref,
)
from .sqlite_store import SqliteCodingBatchStore

__all__ = [
    "AuthorizedCodingBatchIntegration",
    "BatchAggregationPolicy",
    "CanonicalWorkstreamMaterializer",
    "CheckState",
    "CodingBatch",
    "CodingBatchAuthorizationContext",
    "CodingBatchCoordinator",
    "CodingBatchRepairCoordinator",
    "CodingBatchStore",
    "CodingWorkItem",
    "CodingWorkstream",
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
    "coding_batch_resource",
    "deterministic_branch_ref",
    "deterministic_workspace_id",
]
