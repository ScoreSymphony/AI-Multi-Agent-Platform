"""Read-only Control Plane projection for immutable Skill SecurityEvidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.domain import OwnerRef

from .models import SkillRevision
from .security_evidence import (
    SecurityEvidence,
    SecurityEvidenceService,
    security_evidence_to_json,
)
from .service import SkillService

SKILL_SECURITY_EVIDENCE_COLLECTION = "skill-security-evidence"


class _ScopedControlPlane(Protocol):
    async def _authorize(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        owner_type: str | None = None,
        owner_id: str | None = None,
        project_id: str | None = None,
        request_payload_digest: str | None = None,
    ) -> None: ...

    async def _allowed(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        owner_type: str | None = None,
        owner_id: str | None = None,
        project_id: str | None = None,
        request_payload_digest: str | None = None,
    ) -> bool: ...


@dataclass(slots=True)
class SkillSecurityEvidenceScopeAccess:
    """Authorize evidence against the canonical immutable Skill revision scope."""

    control_plane: ControlPlane

    async def authorize(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        owner_ref: OwnerRef,
        project_id: str | None,
    ) -> None:
        scoped = cast(_ScopedControlPlane, self.control_plane)
        await scoped._authorize(
            context,
            action,
            resource_ref,
            owner_type=owner_ref.type,
            owner_id=owner_ref.id,
            project_id=project_id,
        )

    async def allowed(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        owner_ref: OwnerRef,
        project_id: str | None,
    ) -> bool:
        scoped = cast(_ScopedControlPlane, self.control_plane)
        return await scoped._allowed(
            context,
            action,
            resource_ref,
            owner_type=owner_ref.type,
            owner_id=owner_ref.id,
            project_id=project_id,
        )


class SkillSecurityEvidenceResourceService:
    """Expose review evidence without giving the scanner mutation authority."""

    def __init__(
        self,
        service: SecurityEvidenceService,
        skills: SkillService,
        scope_access: SkillSecurityEvidenceScopeAccess,
    ) -> None:
        self.service = service
        self.skills = skills
        self.scope_access = scope_access

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        resources: list[dict[str, JsonValue]] = []
        for item in self.service.repository.list_all():
            revision = self._revision_for(item)
            if not await self.scope_access.allowed(
                context,
                "skill-security-evidence:list",
                item.evidence_id,
                owner_ref=revision.owner_ref,
                project_id=revision.project_id,
            ):
                continue
            resources.append(security_evidence_resource(item, revision))
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        evidence = self.service.repository.get(resource_id)
        revision = self._revision_for(evidence)
        await self.scope_access.authorize(
            context,
            "skill-security-evidence:read",
            resource_id,
            owner_ref=revision.owner_ref,
            project_id=revision.project_id,
        )
        return security_evidence_resource(evidence, revision)

    def _revision_for(self, evidence: SecurityEvidence) -> SkillRevision:
        return self.skills.repository.get_skill_revision(
            evidence.candidate_id,
            evidence.candidate_revision,
        )


def security_evidence_resource(
    evidence: SecurityEvidence,
    revision: SkillRevision | None = None,
) -> dict[str, JsonValue]:
    """Return reviewer-facing provenance, findings, degradation and canonical scope."""

    resource: dict[str, JsonValue] = {
        "id": evidence.evidence_id,
        "type": "skill_security_evidence",
        **security_evidence_to_json(evidence),
    }
    if revision is not None:
        resource["candidate_scope"] = {
            "owner_type": revision.owner_ref.type,
            "owner_id": revision.owner_ref.id,
            "project_id": revision.project_id,
            "workspace_id": revision.workspace_id,
        }
    return resource


__all__ = [
    "SKILL_SECURITY_EVIDENCE_COLLECTION",
    "SkillSecurityEvidenceResourceService",
    "SkillSecurityEvidenceScopeAccess",
    "security_evidence_resource",
]
