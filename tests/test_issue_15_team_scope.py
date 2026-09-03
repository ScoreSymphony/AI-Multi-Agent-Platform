from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts import AuthorizationOutcome, OperationContext
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationContext,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)


def test_local_authorization_policy_enforces_team_scope() -> None:
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref="user:team-member",
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.READ}),
                resource_types=frozenset({ResourceType.TEAM}),
                organization_ids=frozenset({"org:example"}),
                team_ids=frozenset({"team:alpha"}),
            ),
        )
    )
    actor = ActorIdentity(
        "user:team-member",
        ActorType.HUMAN,
        organization_id="org:example",
        team_ids=("team:alpha",),
    )
    operation = OperationContext(
        correlation_id="issue-15-team-scope",
        owner_type="organization",
        owner_id="example",
    )

    async def decide(team_id: str | None):
        request = AuthorizationContext(
            actor=actor,
            action=AuthorizationAction.READ,
            resource_type=ResourceType.TEAM,
            resource_id=team_id or "team:unspecified",
            operation=operation,
            organization_id="org:example",
            team_id=team_id,
        ).to_request()
        return await provider.authorize(request)

    allowed = asyncio.run(decide("team:alpha"))
    wrong_team = asyncio.run(decide("team:beta"))
    missing_team = asyncio.run(decide(None))

    assert allowed.outcome is AuthorizationOutcome.ALLOW
    assert wrong_team.outcome is AuthorizationOutcome.DENY
    assert wrong_team.reason == "team scope is outside principal policy"
    assert missing_team.outcome is AuthorizationOutcome.DENY
    assert missing_team.reason == "team scope is outside principal policy"
