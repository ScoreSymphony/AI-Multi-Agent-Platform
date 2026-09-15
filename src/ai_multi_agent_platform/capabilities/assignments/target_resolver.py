"""Composition helper for exact Agent, Team and Project target lookup."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ai_multi_agent_platform.domain import OwnerRef

from .contracts import ResolvedCapabilityAssignmentTarget
from .models import CapabilityAssignmentTarget, CapabilityAssignmentTargetType


@dataclass(slots=True)
class CallableCapabilityAssignmentTargetResolver:
    """Compose exact target lookup from existing canonical repositories."""

    get_agent: Callable[[str], object]
    get_team: Callable[[str], object]
    get_project: Callable[[str], object]

    def resolve(
        self,
        target: CapabilityAssignmentTarget,
    ) -> ResolvedCapabilityAssignmentTarget:
        if target.subject_type is CapabilityAssignmentTargetType.AGENT:
            resource = self.get_agent(target.subject_id)
        elif target.subject_type is CapabilityAssignmentTargetType.AGENT_TEAM:
            resource = self.get_team(target.subject_id)
        else:
            resource = self.get_project(target.subject_id)

        project_id = (
            target.subject_id
            if target.subject_type is CapabilityAssignmentTargetType.PROJECT
            else _optional_string_attr(resource, "project_id")
        )
        owner_ref = getattr(resource, "owner_ref", None)
        organization_id = None
        if isinstance(owner_ref, OwnerRef) and owner_ref.type == "organization":
            organization_id = owner_ref.id
        return ResolvedCapabilityAssignmentTarget(
            project_id=project_id,
            organization_id=organization_id,
        )


def _optional_string_attr(resource: object, name: str) -> str | None:
    value = getattr(resource, name, None)
    return value if isinstance(value, str) else None
