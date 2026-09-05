"""Deny-only live membership guard composed in front of canonical #15 authorization."""

from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.contracts.authorization import (
    AuthorizationDecision,
    AuthorizationOutcome,
    AuthorizationRequest,
    normalize_authorization_decision,
)
from ai_multi_agent_platform.contracts.interfaces import AuthorizationProvider
from ai_multi_agent_platform.contracts.types import (
    AuthorizationRequest as BaseAuthorizationRequest,
)
from ai_multi_agent_platform.contracts.types import JsonValue, ProviderDescriptor

from .models import Membership, MembershipStatus, OrganizationStatus, TeamStatus
from .repository import OrganizationRepository


class MembershipAuthorizationProvider(AuthorizationProvider):
    """Fail closed on stale scope and project trusted membership data into #15.

    This provider never grants an action by itself. Once live membership has been
    verified, the wrapped #15 provider remains authoritative for the actual action,
    resource, approval and policy decision. Active role/policy references are only
    projected into authoritative trust context for that provider to interpret.
    """

    def __init__(
        self,
        provider: AuthorizationProvider,
        repository: OrganizationRepository,
    ) -> None:
        self._provider = provider
        self._repository = repository

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._provider.descriptor

    async def authorize(self, request: BaseAuthorizationRequest) -> AuthorizationDecision:
        organization_id = getattr(request, "organization_id", None)
        team_id = getattr(request, "team_id", None)

        owner_type = request.context.owner_type
        owner_id = request.context.owner_id
        if organization_id is None and owner_type == "organization":
            organization_id = owner_id
        if team_id is None and owner_type == "team":
            team_id = owner_id
        if organization_id is None and team_id is not None:
            try:
                scoped_team = await self._repository.get_team(team_id)
            except LookupError:
                return _deny("team scope is not active")
            organization_id = scoped_team.organization_id

        if organization_id is None and team_id is None:
            return normalize_authorization_decision(await self._provider.authorize(request))
        if organization_id is None:
            return _deny("team-scoped request requires organization scope")

        try:
            organization = await self._repository.get_organization(organization_id)
        except LookupError:
            return _deny("organization scope is not active")
        if organization.status is not OrganizationStatus.ACTIVE:
            return _deny("organization scope is not active")

        if request.principal_ref in {
            organization.owner_actor_id,
            *organization.administrator_actor_ids,
        }:
            if team_id is not None:
                try:
                    team = await self._repository.get_team(team_id)
                except LookupError:
                    return _deny("team scope is not active")
                if team.organization_id != organization_id or team.status is not TeamStatus.ACTIVE:
                    return _deny("team scope is not active")
            return normalize_authorization_decision(await self._provider.authorize(request))

        memberships = await self._repository.list_memberships(
            actor_id=request.principal_ref,
            organization_id=organization_id,
        )
        active = tuple(
            membership for membership in memberships if membership.status is MembershipStatus.ACTIVE
        )
        if not active:
            return _deny("actor has no active organization membership")

        if team_id is not None:
            try:
                team = await self._repository.get_team(team_id)
            except LookupError:
                return _deny("team scope is not active")
            if team.organization_id != organization_id or team.status is not TeamStatus.ACTIVE:
                return _deny("team scope is not active")
            if not any(membership.team_id == team_id for membership in active):
                return _deny("actor has no active team membership")

        projected = _project_membership_context(
            request,
            memberships=active,
            organization_id=organization_id,
            team_id=team_id,
        )
        return normalize_authorization_decision(await self._provider.authorize(projected))


def _project_membership_context(
    request: BaseAuthorizationRequest,
    *,
    memberships: tuple[Membership, ...],
    organization_id: str,
    team_id: str | None,
) -> BaseAuthorizationRequest:
    if not isinstance(request, AuthorizationRequest):
        return request

    role_refs: list[JsonValue] = [
        value for value in sorted({value for item in memberships for value in item.role_refs})
    ]
    policy_refs: list[JsonValue] = [
        value for value in sorted({value for item in memberships for value in item.policy_refs})
    ]
    membership_ids: list[JsonValue] = [value for value in sorted(item.id for item in memberships)]
    authoritative_scope: dict[str, JsonValue] = {
        "organization_id": organization_id,
        "team_id": team_id,
        "membership_ids": membership_ids,
        "role_refs": role_refs,
        "policy_refs": policy_refs,
    }
    trust_context = dict(request.trust_context)
    # Always overwrite caller-supplied membership data with repository-backed values.
    trust_context["organization_membership"] = authoritative_scope
    return replace(request, trust_context=trust_context)


def _deny(reason: str) -> AuthorizationDecision:
    return AuthorizationDecision(
        AuthorizationOutcome.DENY,
        reason=reason,
        policy_id="organization.membership.active",
        audit_metadata={"membership_guard": "denied"},
    )
