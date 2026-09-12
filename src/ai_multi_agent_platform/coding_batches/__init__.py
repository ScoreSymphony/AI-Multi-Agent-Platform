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
    IntegrationState,
    OverlapDecision,
    OverlapKind,
    RequiredCheck,
    VerificationEvidence,
    WorkstreamProvenance,
    WorkstreamResult,
    WorkstreamState,
)
from .overlap import ConservativeOverlapClassifier
from .projection import coding_batch_resource
from .service import (
    CodingBatchCoordinator,
    CodingBatchStore,
    InMemoryCodingBatchStore,
    deterministic_branch_ref,
)

__all__ = [
    "AuthorizedCodingBatchIntegration",
    "BatchAggregationPolicy",
    "CanonicalWorkstreamMaterializer",
    "CheckState",
    "CodingBatch",
    "CodingBatchAuthorizationContext",
    "CodingBatchCoordinator",
    "CodingBatchStore",
    "CodingWorkItem",
    "CodingWorkstream",
    "CombinedValidationEvidence",
    "ConservativeOverlapClassifier",
    "InMemoryCodingBatchStore",
    "IntegrationCandidate",
    "IntegrationConflict",
    "IntegrationState",
    "OverlapDecision",
    "OverlapKind",
    "RequiredCheck",
    "VerificationEvidence",
    "WorkstreamProvenance",
    "WorkstreamResult",
    "WorkstreamState",
    "coding_batch_resource",
    "deterministic_branch_ref",
    "deterministic_workspace_id",
]
