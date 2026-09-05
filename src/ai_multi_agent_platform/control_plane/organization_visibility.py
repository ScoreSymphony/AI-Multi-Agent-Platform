"""Administrative metadata visibility for organization-owned resources."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.organizations import OrganizationService, ResourceOwnership

from .extensions import ResourceService
from .models import PageQuery, RequestContext
from .organization_api import (
    RESOURCE_OWNERSHIP_COLLECTION,
    RESOURCE_SHARE_COLLECTION,
    _is_organization_admin,
    _ownership_by_id,
    _ownership_resource,
    _share_resource,
)


class AdministrativeOwnershipVisibility(ResourceService):
    search_indexable = False
    """Add org owner/admin visibility to non-secret ownership/share metadata."""

    def __init__(
        self,
        service: OrganizationService,
        collection: str,
        base: ResourceService,
    ) -> None:
        if collection not in {RESOURCE_OWNERSHIP_COLLECTION, RESOURCE_SHARE_COLLECTION}:
            raise ValueError(f"unsupported administrative visibility collection: {collection}")
        self._service = service
        self._collection = collection
        self._base = base

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        resources = {
            resource["id"]: resource
            for resource in await self._base.list_resources(context, query)
            if isinstance(resource.get("id"), str)
        }
        principal = context.actor.principal_ref
        if self._collection == RESOURCE_OWNERSHIP_COLLECTION:
            for ownership in await self._service.repository.list_ownerships():
                if await _is_admin_for_ownership(self._service, principal, ownership):
                    resources.setdefault(ownership.id, _ownership_resource(ownership))
        else:
            for share in await self._service.repository.list_all_shares():
                share_ownership = await _ownership_by_id(self._service, share.ownership_id)
                if share_ownership is not None and await _is_admin_for_ownership(
                    self._service, principal, share_ownership
                ):
                    resources.setdefault(share.id, _share_resource(share))
        return tuple(resources.values())

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        try:
            return await self._base.get_resource(context, resource_id)
        except ContractError as exc:
            if exc.code is not ErrorCode.NOT_FOUND:
                raise

        principal = context.actor.principal_ref
        if self._collection == RESOURCE_OWNERSHIP_COLLECTION:
            requested_ownership = next(
                (
                    item
                    for item in await self._service.repository.list_ownerships()
                    if item.id == resource_id
                ),
                None,
            )
            if requested_ownership is not None and await _is_admin_for_ownership(
                self._service, principal, requested_ownership
            ):
                return _ownership_resource(requested_ownership)
        else:
            try:
                share = await self._service.repository.get_share(resource_id)
            except LookupError:
                share = None
            if share is not None:
                share_ownership = await _ownership_by_id(self._service, share.ownership_id)
                if share_ownership is not None and await _is_admin_for_ownership(
                    self._service, principal, share_ownership
                ):
                    return _share_resource(share)
        raise ContractError(ErrorCode.NOT_FOUND, f"resource not found: {resource_id}")


async def _is_admin_for_ownership(
    service: OrganizationService,
    principal_ref: str,
    ownership: ResourceOwnership,
) -> bool:
    organization_id = await _ownership_organization_id(service, ownership)
    if organization_id is None:
        return False
    return await _is_organization_admin(service, principal_ref, organization_id)


async def _ownership_organization_id(
    service: OrganizationService,
    ownership: ResourceOwnership,
) -> str | None:
    if ownership.organization_id is not None:
        return ownership.organization_id
    if ownership.owner_ref.type == "organization":
        return ownership.owner_ref.id
    if ownership.owner_ref.type == "team":
        try:
            team = await service.repository.get_team(ownership.owner_ref.id)
        except LookupError:
            return None
        return team.organization_id
    return None
