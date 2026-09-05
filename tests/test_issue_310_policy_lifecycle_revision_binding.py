from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    AuthorizationPolicyProfileCallContext,
    AuthorizationPolicyProfileContent,
    AuthorizationPolicyProfileDefinition,
    AuthorizationPolicyProfileRevision,
    AuthorizationPolicyProfileService,
    AuthorizationPolicyProvenance,
    InMemoryAuthorizationPolicyProfileRepository,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)


def _context(*, approval_id: str | None = None) -> AuthorizationPolicyProfileCallContext:
    return AuthorizationPolicyProfileCallContext(
        operation=OperationContext(
            correlation_id="issue-310-lifecycle-revision-binding",
            owner_type="user",
            owner_id="operator",
        ),
        actor_ref="user:operator",
        approval_id=approval_id,
    )


def _content(name: str) -> AuthorizationPolicyProfileContent:
    return AuthorizationPolicyProfileContent(
        name=name,
        allowed_actions=(AuthorizationAction.READ,),
        resource_types=(ResourceType.GENERIC,),
        provenance=AuthorizationPolicyProvenance(
            created_by="user:operator",
            source="local",
        ),
    )


def test_enable_approval_is_invalid_after_policy_revision_changes() -> None:
    repository = InMemoryAuthorizationPolicyProfileRepository()
    profile_id = new_id("authorization_policy_profile")
    owner = OwnerRef(type="user", id="operator")
    definition = AuthorizationPolicyProfileDefinition(
        policy_profile_id=profile_id,
        owner_ref=owner,
        current_revision=1,
        enabled=False,
    )
    repository.create_profile(
        definition,
        AuthorizationPolicyProfileRevision(
            policy_profile_id=profile_id,
            revision=1,
            owner_ref=owner,
            content=_content("v1"),
            created_at=definition.created_at,
        ),
    )

    approval_gate = AuthorizationGate(
        LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref="user:operator",
                    actor_types=frozenset({ActorType.HUMAN}),
                    approval_actions=frozenset({AuthorizationAction.ADMINISTER}),
                    resource_types=frozenset({ResourceType.GENERIC}),
                ),
                LocalPrincipalPolicy(
                    principal_ref="user:reviewer",
                    actor_types=frozenset({ActorType.HUMAN}),
                    allowed_actions=frozenset({AuthorizationAction.APPROVE}),
                    resource_types=frozenset({ResourceType.GENERIC}),
                ),
            )
        )
    )
    lifecycle = AuthorizationPolicyProfileService(repository, approval_gate)
    with pytest.raises(ContractError) as pending:
        asyncio.run(lifecycle.enable(profile_id, _context()))
    assert pending.value.code is ErrorCode.FORBIDDEN
    first = approval_gate.approvals.all()[0]
    asyncio.run(
        approval_gate.decide_approval(
            first.approval_id,
            approver=ActorIdentity("user:reviewer", ActorType.HUMAN),
            approve=True,
            operation=OperationContext(
                correlation_id="issue-310-lifecycle-review",
                owner_type="user",
                owner_id="reviewer",
            ),
        )
    )

    revise_gate = AuthorizationGate(
        LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref="user:operator",
                    actor_types=frozenset({ActorType.HUMAN}),
                    allowed_actions=frozenset({AuthorizationAction.MODIFY}),
                    resource_types=frozenset({ResourceType.GENERIC}),
                ),
            )
        )
    )
    revised = asyncio.run(
        AuthorizationPolicyProfileService(repository, revise_gate).revise(
            profile_id,
            _content("v2"),
            _context(),
            expected_revision=1,
        )
    )
    assert revised.current_revision == 2

    with pytest.raises(ContractError) as stale:
        asyncio.run(
            lifecycle.enable(
                profile_id,
                _context(approval_id=first.approval_id),
            )
        )
    assert stale.value.code is ErrorCode.FORBIDDEN
    approvals = approval_gate.approvals.all()
    assert len(approvals) == 2
    assert approvals[0].requested_action_digest != approvals[1].requested_action_digest
    assert repository.get_profile(profile_id).enabled is False
