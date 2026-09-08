"""Canonical Research Evidence layer."""

from .integrations import ResearchPlanningBridge, ResearchPromotionBridge
from .models import (
    Claim,
    ClaimConfidence,
    ClaimStatus,
    EvidenceFreshness,
    EvidenceRecord,
    EvidenceRelation,
    FreshnessPolicy,
    ResearchActionContext,
    ResearchClass,
    ResearchItem,
    ResearchSourceType,
    ResearchStatus,
    ResearchVerificationBinding,
    ResearchVerificationSubjectType,
    SourceObservation,
    SourceObservationState,
    SourceRecord,
    UntrustedResearchExecutionProfile,
)
from .repository import (
    RESEARCH_PERSISTENCE_SCHEMA_VERSION,
    InMemoryResearchRepository,
    ResearchRepository,
    SqliteResearchRepository,
)
from .service import ResearchService
from .verification import ResearchVerificationBridge, ResearchVerificationSubject

__all__ = [
    "RESEARCH_PERSISTENCE_SCHEMA_VERSION",
    "Claim",
    "ClaimConfidence",
    "ClaimStatus",
    "EvidenceFreshness",
    "EvidenceRecord",
    "EvidenceRelation",
    "FreshnessPolicy",
    "InMemoryResearchRepository",
    "ResearchActionContext",
    "ResearchClass",
    "ResearchItem",
    "ResearchPlanningBridge",
    "ResearchPromotionBridge",
    "ResearchRepository",
    "ResearchService",
    "ResearchSourceType",
    "ResearchStatus",
    "ResearchVerificationBinding",
    "ResearchVerificationBridge",
    "ResearchVerificationSubject",
    "ResearchVerificationSubjectType",
    "SourceObservation",
    "SourceObservationState",
    "SourceRecord",
    "SqliteResearchRepository",
    "UntrustedResearchExecutionProfile",
]
