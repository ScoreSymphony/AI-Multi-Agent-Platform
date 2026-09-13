"""Canonical Research Evidence layer."""

from .async_repository import (
    AsyncResearchRepository,
    AsyncResearchRepositoryAdapter,
    ResearchPersistenceOffload,
)
from .control_plane import (
    RESEARCH_CLAIM_COLLECTION,
    RESEARCH_COLLECTIONS,
    RESEARCH_COMMANDS,
    RESEARCH_EVIDENCE_COLLECTION,
    RESEARCH_ITEM_COLLECTION,
    RESEARCH_OBSERVATION_COLLECTION,
    RESEARCH_SOURCE_COLLECTION,
    ResearchClaimResourceService,
    ResearchEvidenceResourceService,
    ResearchItemResourceService,
    ResearchObservationResourceService,
    ResearchSourceResourceService,
    register_research_control_plane,
)
from .decision import ResearchDecisionBridge, ResearchDecisionReferences
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
from .portability import (
    RESEARCH_BUNDLE_KIND,
    RESEARCH_BUNDLE_SCHEMA_VERSION,
    VerificationBindingValidator,
    export_research_bundle,
    import_research_bundle,
)
from .portability_verification import canonical_verification_binding_validator
from .repository import (
    RESEARCH_PERSISTENCE_SCHEMA_VERSION,
    InMemoryResearchRepository,
    ResearchRepository,
    SqliteResearchRepository,
)
from .runtime_service import AsyncResearchService
from .search import (
    ResearchClaimSearchResourceService,
    ResearchEvidenceSearchResourceService,
    ResearchItemSearchResourceService,
    ResearchObservationSearchResourceService,
    ResearchSourceSearchResourceService,
    register_searchable_research_control_plane,
    research_search_resource_services,
)
from .service import ResearchService as SynchronousResearchService
from .verification import ResearchVerificationBridge, ResearchVerificationSubject

# The package-level service is the runtime-safe composition. The synchronous base remains an
# explicit setup/offline/test compatibility seam for callers that deliberately need it.
ResearchService = AsyncResearchService

__all__ = [
    "RESEARCH_BUNDLE_KIND",
    "RESEARCH_BUNDLE_SCHEMA_VERSION",
    "RESEARCH_CLAIM_COLLECTION",
    "RESEARCH_COLLECTIONS",
    "RESEARCH_COMMANDS",
    "RESEARCH_EVIDENCE_COLLECTION",
    "RESEARCH_ITEM_COLLECTION",
    "RESEARCH_OBSERVATION_COLLECTION",
    "RESEARCH_PERSISTENCE_SCHEMA_VERSION",
    "RESEARCH_SOURCE_COLLECTION",
    "AsyncResearchRepository",
    "AsyncResearchRepositoryAdapter",
    "AsyncResearchService",
    "Claim",
    "ClaimConfidence",
    "ClaimStatus",
    "EvidenceFreshness",
    "EvidenceRecord",
    "EvidenceRelation",
    "FreshnessPolicy",
    "InMemoryResearchRepository",
    "ResearchActionContext",
    "ResearchClaimResourceService",
    "ResearchClaimSearchResourceService",
    "ResearchClass",
    "ResearchDecisionBridge",
    "ResearchDecisionReferences",
    "ResearchEvidenceResourceService",
    "ResearchEvidenceSearchResourceService",
    "ResearchItem",
    "ResearchItemResourceService",
    "ResearchItemSearchResourceService",
    "ResearchObservationResourceService",
    "ResearchObservationSearchResourceService",
    "ResearchPersistenceOffload",
    "ResearchPlanningBridge",
    "ResearchPromotionBridge",
    "ResearchRepository",
    "ResearchService",
    "ResearchSourceResourceService",
    "ResearchSourceSearchResourceService",
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
    "SynchronousResearchService",
    "UntrustedResearchExecutionProfile",
    "VerificationBindingValidator",
    "canonical_verification_binding_validator",
    "export_research_bundle",
    "import_research_bundle",
    "register_research_control_plane",
    "register_searchable_research_control_plane",
    "research_search_resource_services",
]
