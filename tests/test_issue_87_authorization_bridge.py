from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.contracts import AuthorizationOutcome, OperationContext
from ai_multi_agent_platform.contracts.types import (
    AuthorizationDecision,
    AuthorizationRequest,
    JsonValue,
)
from ai_multi_agent_platform.organizations import (
    InMemoryOrganizationRepository,
    MembershipAuthorizationProvider,
    OrganizationService,
)
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationContext,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.testing import FakeAuthorizationProvider


class _MembershipPolicyAuthorization(FakeAuthorizationProvider):
    """Test #15 provider that interprets repository-backed membership policy context."""

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        trust_context = getattr(request, "trust_context", {})
        membership = trust_context.get("organization_membership")
        policy_refs: object = ()
        if isinstance(membership, Mapping):
            policy_refs = membership.get("policy_refs", ())
        allowed = isinstance(policy_refs, (list, tuple)) and "policy:allow-read" in policy_refs
        return AuthorizationDecision(allowed=allowed, reason="membership-policy-test")


def test_team_update_is_canonical_and_preserves_identity() -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=UTC)
        repository = InMemoryOrganizationRepository()
        service = OrganizationService(repository)
        organization = await service.create_organization(
            name="Example",
            owner_actor_id="user:owner",
            now=now,
        )
        team = await service.create_team(
            organization_id=organization.id,
            name="Old name",
            description="Old description",
            now=now,
        )
        updated = await service.update_team(
            team.id,
            name="New name",
            description="New description",
            now=now + timedelta(minutes=5),
        )
        assert updated.id == team.id
        assert updated.organization_id == organization.id
        assert updated.name == "New name"
        assert updated.description == "New description"
        assert updated.updated_at == now + timedelta(minutes=5)

    asyncio.run(scenario())


def test_live_membership_guard_revokes_stale_team_scope_before_issue_15_policy() -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=UTC)
        repository = InMemoryOrganizationRepository()
        service = OrganizationService(repository)
        organization = await service.create_organization(
            name="Example",
            owner_actor_id="user:owner",
            now=now,
        )
        team = await service.create_team(
            organization_id=organization.id,
            name="Platform",
            now=now,
        )
        membership = await service.add_member(
            actor_id="user:member",
            actor_type=ActorType.HUMAN,
            organization_id=organization.id,
            team_id=team.id,
            role_refs=("role:reader",),
            policy_refs=("policy:team-read",),
            now=now,
        )
        actor = await service.actor_identity_for_scope(
            actor_id="user:member",
            actor_type=ActorType.HUMAN,
            organization_id=organization.id,
        )
        canonical = LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref="user:member",
                    actor_types=frozenset({ActorType.HUMAN}),
                    allowed_actions=frozenset({AuthorizationAction.READ}),
                    resource_types=frozenset({ResourceType.TEAM}),
                    organization_ids=frozenset({organization.id}),
                    team_ids=frozenset({team.id}),
                ),
            )
        )
        provider = MembershipAuthorizationProvider(canonical, repository)
        request = AuthorizationContext(
            actor=actor,
            action=AuthorizationAction.READ,
            resource_type=ResourceType.TEAM,
            resource_id=team.id,
            operation=OperationContext(
                correlation_id="issue-87-live-membership",
                owner_type="organization",
                owner_id=organization.id,
            ),
            organization_id=organization.id,
            team_id=team.id,
        ).to_request()

        allowed = await provider.authorize(request)
        assert allowed.outcome is AuthorizationOutcome.ALLOW

        await service.suspend_member(
            membership.id,
            now=now + timedelta(minutes=1),
        )
        denied = await provider.authorize(request)
        assert denied.outcome is AuthorizationOutcome.DENY
        assert denied.policy_id == "organization.membership.active"
        assert denied.reason == "actor has no active organization membership"

    asyncio.run(scenario())


def test_membership_policy_assignment_changes_canonical_authorization_input() -> None:
    async def scenario() -> None:
        repository = InMemoryOrganizationRepository()
        service = OrganizationService(repository)
        organization = await service.create_organization(
            name="Policy Org",
            owner_actor_id="user:owner",
        )
        team = await service.create_team(
            organization_id=organization.id,
            name="Policy Team",
        )
        membership = await service.add_member(
            actor_id="user:member",
            actor_type=ActorType.HUMAN,
            organization_id=organization.id,
            team_id=team.id,
            role_refs=("role:reader",),
            policy_refs=("policy:deny-read",),
        )
        actor = await service.actor_identity_for_scope(
            actor_id="user:member",
            actor_type=ActorType.HUMAN,
            organization_id=organization.id,
        )
        canonical = _MembershipPolicyAuthorization()
        provider = MembershipAuthorizationProvider(canonical, repository)
        request = AuthorizationContext(
            actor=actor,
            action=AuthorizationAction.READ,
            resource_type=ResourceType.TEAM,
            resource_id=team.id,
            operation=OperationContext(
                correlation_id="issue-87-policy-projection",
                owner_type="organization",
                owner_id=organization.id,
            ),
            organization_id=organization.id,
            team_id=team.id,
            trust_context={
                "organization_membership": cast_json({"policy_refs": ["policy:caller-spoof"]})
            },
        ).to_request()

        denied = await provider.authorize(request)
        assert denied.outcome is AuthorizationOutcome.DENY

        await service.set_membership_assignments(
            membership.id,
            role_refs=("role:reader",),
            policy_refs=("policy:allow-read",),
        )
        allowed = await provider.authorize(request)
        assert allowed.outcome is AuthorizationOutcome.ALLOW

        projected = canonical.calls[-1]
        trust_context = getattr(projected, "trust_context", {})
        scope = trust_context["organization_membership"]
        assert isinstance(scope, Mapping)
        assert scope["policy_refs"] == ["policy:allow-read"]
        assert "policy:caller-spoof" not in repr(scope)

    asyncio.run(scenario())


def cast_json(value: dict[str, JsonValue]) -> JsonValue:
    return value
