"""Mirror canonical resource owners into issue #87 ownership/share metadata."""

from __future__ import annotations

from collections.abc import Mapping

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.organizations import OrganizationService, ResourceOwnership

STRICT_MIRRORED_OWNERSHIP_RESOURCE_TYPES = frozenset(
    {
        "project",
        "workspace",
        "memory",
        "knowledge_source",
        "connection",
        "file",
        "artifact",
    }
)
AUTHORITATIVE_MIRRORED_OWNERSHIP_RESOURCE_TYPES = frozenset(
    {"agent", "agent_team", "automation"}
)
MIRRORED_OWNERSHIP_RESOURCE_TYPES = (
    STRICT_MIRRORED_OWNERSHIP_RESOURCE_TYPES | AUTHORITATIVE_MIRRORED_OWNERSHIP_RESOURCE_TYPES
)


class CanonicalOwnershipMirror:
    """Keep #87 ownership metadata aligned with a resource-owned canonical OwnerRef."""

    def __init__(self, organizations: OrganizationService) -> None:
        self._organizations = organizations

    async def validate_owner(self, owner_ref: OwnerRef) -> str | None:
        """Validate a canonical owner and resolve its Organization where applicable."""

        if owner_ref.type == "organization":
            await self._organizations.repository.get_organization(owner_ref.id)
            return owner_ref.id
        if owner_ref.type == "team":
            team = await self._organizations.repository.get_team(owner_ref.id)
            return team.organization_id
        return None

    async def mirror(
        self,
        *,
        resource_type: str,
        resource_id: str,
        owner_ref: OwnerRef,
        actor_ref: str,
    ) -> ResourceOwnership:
        """Create one strict mirror, replay it idempotently, and reject split-brain ownership."""

        if resource_type not in MIRRORED_OWNERSHIP_RESOURCE_TYPES:
            raise ValueError(f"unsupported mirrored ownership resource type: {resource_type}")
        organization_id = await self.validate_owner(owner_ref)
        existing = await self._existing(resource_type, resource_id)
        if existing is None:
            return await self._organizations.set_resource_owner(
                resource_type=resource_type,
                resource_id=resource_id,
                owner_ref=owner_ref,
                organization_id=organization_id,
                created_by_actor_id=actor_ref,
            )
        if existing.owner_ref == owner_ref and existing.organization_id == organization_id:
            return existing
        raise ContractError(
            ErrorCode.CONFLICT,
            "resource ownership mirror disagrees with the canonical resource owner",
            details={
                "resource_type": resource_type,
                "resource_id": resource_id,
                "canonical_owner_type": owner_ref.type,
                "canonical_owner_id": owner_ref.id,
                "mirrored_owner_type": existing.owner_ref.type,
                "mirrored_owner_id": existing.owner_ref.id,
            },
        )

    async def mirror_authoritative(
        self,
        *,
        resource_type: str,
        resource_id: str,
        owner_ref: OwnerRef,
        actor_ref: str,
    ) -> ResourceOwnership:
        """Mirror a resource whose canonical API is authoritative for owner changes."""

        if resource_type not in AUTHORITATIVE_MIRRORED_OWNERSHIP_RESOURCE_TYPES:
            raise ValueError(
                f"resource type does not support authoritative mirroring: {resource_type}"
            )
        organization_id = await self.validate_owner(owner_ref)
        existing = await self._existing(resource_type, resource_id)
        if existing is None:
            return await self._organizations.set_resource_owner(
                resource_type=resource_type,
                resource_id=resource_id,
                owner_ref=owner_ref,
                organization_id=organization_id,
                created_by_actor_id=actor_ref,
            )
        if existing.owner_ref == owner_ref and existing.organization_id == organization_id:
            return existing
        return await self._organizations.transfer_resource(
            resource_type=resource_type,
            resource_id=resource_id,
            new_owner_ref=owner_ref,
            organization_id=organization_id,
        )

    async def _existing(self, resource_type: str, resource_id: str) -> ResourceOwnership | None:
        try:
            return await self._organizations.repository.get_ownership(resource_type, resource_id)
        except LookupError:
            return None


def reject_direct_mirror_owner_mutation(
    command: str,
    payload: Mapping[str, object],
) -> None:
    """Prevent generic #87 commands from creating a second owner truth for mirrored resources."""

    if command not in {"resource-ownership.set", "resource-ownership.transfer"}:
        return
    resource_type = payload.get("resource_type")
    if not isinstance(resource_type, str) or resource_type not in MIRRORED_OWNERSHIP_RESOURCE_TYPES:
        return
    raise ContractError(
        ErrorCode.CONFLICT,
        f"{resource_type} ownership is managed by its canonical resource API",
        details={"resource_type": resource_type, "command": command},
    )
