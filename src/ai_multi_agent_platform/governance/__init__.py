"""Optional Proposal/Specification governance layer (#501)."""

from .control_plane import (
    GOVERNANCE_AUDIT_COLLECTION,
    GOVERNANCE_COMMANDS,
    PROPOSAL_COLLECTION,
    PROPOSAL_REVISION_COLLECTION,
    SPECIFICATION_COLLECTION,
    SPECIFICATION_REVISION_COLLECTION,
    ProposalResourceService,
    SpecificationResourceService,
    proposal_resource,
    register_governance_control_plane,
    specification_resource,
)
from .models import (
    GOVERNANCE_SCHEMA_VERSION,
    ConversionStatus,
    GovernanceAuditEvent,
    Proposal,
    ProposalStatus,
    SpecificationRevision,
    TaskConversion,
    specification_content_digest,
)
from .repository import GovernanceRepository, SqliteGovernanceRepository
from .service import (
    ApprovedSpecificationPlanningInput,
    GovernanceCallContext,
    GovernanceService,
)

__all__ = [
    "GOVERNANCE_AUDIT_COLLECTION",
    "GOVERNANCE_COMMANDS",
    "GOVERNANCE_SCHEMA_VERSION",
    "PROPOSAL_COLLECTION",
    "PROPOSAL_REVISION_COLLECTION",
    "SPECIFICATION_COLLECTION",
    "SPECIFICATION_REVISION_COLLECTION",
    "ApprovedSpecificationPlanningInput",
    "ConversionStatus",
    "GovernanceAuditEvent",
    "GovernanceCallContext",
    "GovernanceRepository",
    "GovernanceService",
    "Proposal",
    "ProposalResourceService",
    "ProposalStatus",
    "SpecificationResourceService",
    "SpecificationRevision",
    "SqliteGovernanceRepository",
    "TaskConversion",
    "proposal_resource",
    "register_governance_control_plane",
    "specification_content_digest",
    "specification_resource",
]
