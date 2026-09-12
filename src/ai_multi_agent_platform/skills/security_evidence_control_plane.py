"""Read-only Control Plane projection for immutable Skill SecurityEvidence."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .security_evidence import (
    SecurityEvidence,
    SecurityEvidenceService,
    security_evidence_to_json,
)

SKILL_SECURITY_EVIDENCE_COLLECTION = "skill-security-evidence"


class SkillSecurityEvidenceResourceService:
    """Expose review evidence without giving the scanner mutation authority."""

    def __init__(self, service: SecurityEvidenceService) -> None:
        self.service = service

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(
            security_evidence_resource(item) for item in self.service.repository.list_all()
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return security_evidence_resource(self.service.repository.get(resource_id))


def security_evidence_resource(evidence: SecurityEvidence) -> dict[str, JsonValue]:
    """Return reviewer-facing provenance, findings and degradation state verbatim."""
    return {
        "id": evidence.evidence_id,
        "type": "skill_security_evidence",
        **security_evidence_to_json(evidence),
    }


__all__ = [
    "SKILL_SECURITY_EVIDENCE_COLLECTION",
    "SkillSecurityEvidenceResourceService",
    "security_evidence_resource",
]
