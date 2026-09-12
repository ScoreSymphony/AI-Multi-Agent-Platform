"""Parallel coding-batch composition for issue #872."""

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
from .service import (
    CodingBatchCoordinator,
    CodingBatchStore,
    InMemoryCodingBatchStore,
    deterministic_branch_ref,
)

__all__ = [
    "BatchAggregationPolicy",
    "CheckState",
    "CodingBatch",
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
    "deterministic_branch_ref",
]
