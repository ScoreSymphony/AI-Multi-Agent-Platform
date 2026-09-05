from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    AuthorizationPolicyProfileCallContext,
    AuthorizationPolicyProfileContent,
    AuthorizationPolicyProfileRef,
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
            correlation_id="issue-310-mutation-approval-binding",
            owner_type="user",
            owner_id="operator",
        ),
        actor_ref="user:operator",
        approval_id=approval_id,
    )


def _gate(*actions: AuthorizationAction) -> AuthorizationGate:
    return AuthorizationGate(
        LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref="user:operator",
                    actor_types=frozenset({ActorType.HUMAN}),
                    approval_actions=frozenset(actions),
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


def _content(name: str, action: AuthorizationAction = AuthorizationAction.READ):
    return AuthorizationPolicyProfileContent(
        name=name,
        allowed_actions=(action,),
        resource_types=(ResourceType.GENERIC,),
        provenance=AuthorizationPolicyProvenance(
            created_by="user:operator",
            source="local",
        ),
    )


def _approve(gate: AuthorizationGate, approval_id: str) -> None:
    asyncio.run(
        gate.decide_approval(
            approval_id,
            approver=ActorIdentity("user:reviewer", ActorType.HUMAN),
            approve=True,
            operation=OperationContext(
                correlation_id="issue-310-mutation-review",
                owner_type="user",
                owner_id="reviewer",
            ),
        )
    )


def test_create_retry_reuses_approved_generated_id_and_rejects_changed_content() -> None:
    repository = InMemoryAuthorizationPolicyProfileRepository()
    gate = _gate(AuthorizationAction.CREATE)
    service = AuthorizationPolicyProfileService(repository, gate)
    owner = OwnerRef(type="user", id="operator")

    with pytest.raises(ContractError) as pending:
        asyncio.run(service.create(owner_ref=owner, content=_content("approved"), context=_context()))
    assert pending.value.code is ErrorCode.FORBIDDEN
    first = gate.approvals.all()[0]
    _approve(gate, first.approval_id)

    with pytest.raises(ContractError) as changed:
        asyncio.run(
            service.create(
                owner_ref=owner,
                content=_content("changed", AuthorizationAction.ADMINISTER),
                context=_context(approval_id=first.approval_id),
            )
        )
    assert changed.value.code is ErrorCode.FORBIDDEN
    assert len(gate.approvals.all()) == 2
    assert repository.list_profiles() == ()

    created = asyncio.run(
        service.create(
            owner_ref=owner,
            content=_content("approved"),
            context=_context(approval_id=first.approval_id),
        )
    )
    assert created.policy_profile_id == first.resource_id


def test_revise_approval_cannot_be_reused_for_changed_revision_content() -> None:
    repository = InMemoryAuthorizationPolicyProfileRepository()
    create_service = AuthorizationPolicyProfileService(
        repository,
        AuthorizationGate(
            LocalAuthorizationProvider(
                (
                    LocalPrincipalPolicy(
                        principal_ref="user:operator",
                        actor_types=frozenset({ActorType.HUMAN}),
                        allowed_actions=frozenset({AuthorizationAction.CREATE}),
                        resource_types=frozenset({ResourceType.GENERIC}),
                    ),
                )
            )
        ),
    )
    created = asyncio.run(
        create_service.create(
            owner_ref=OwnerRef(type="user", id="operator"),
            content=_content("v1"),
            context=_context(),
        )
    )

    gate = _gate(AuthorizationAction.MODIFY)
    service = AuthorizationPolicyProfileService(repository, gate)
    with pytest.raises(ContractError):
        asyncio.run(service.revise(created.policy_profile_id, _content("v2-a"), _context(), expected_revision=1))
    first = gate.approvals.all()[0]
    _approve(gate, first.approval_id)

    with pytest.raises(ContractError):
        asyncio.run(
            service.revise(
                created.policy_profile_id,
                _content("v2-b", AuthorizationAction.ADMINISTER),
                _context(approval_id=first.approval_id),
                expected_revision=1,
            )
        )
    assert len(gate.approvals.all()) == 2
    assert repository.get_profile(created.policy_profile_id).current_revision == 1

    revised = asyncio.run(
        service.revise(
            created.policy_profile_id,
            _content("v2-a"),
            _context(approval_id=first.approval_id),
            expected_revision=1,
        )
    )
    assert revised.current_revision == 2


def test_assignment_approval_is_bound_to_target_principal_and_actor_types() -> None:
    repository = InMemoryAuthorizationPolicyProfileRepository()
    create_service = AuthorizationPolicyProfileService(
        repository,
        AuthorizationGate(
            LocalAuthorizationProvider(
                (
                    LocalPrincipalPolicy(
                        principal_ref="user:operator",
                        actor_types=frozenset({ActorType.HUMAN}),
                        allowed_actions=frozenset({AuthorizationAction.CREATE}),
                        resource_types=frozenset({ResourceType.GENERIC}),
                    ),
                )
            )
        ),
    )
    created = asyncio.run(
        create_service.create(
            owner_ref=OwnerRef(type="user", id="operator"),
            content=_content("assignable"),
            context=_context(),
        )
    )
    ref = AuthorizationPolicyProfileRef(created.policy_profile_id, 1)

    gate = _gate(AuthorizationAction.ADMINISTER)
    service = AuthorizationPolicyProfileService(repository, gate)
    with pytest.raises(ContractError):
        asyncio.run(
            service.assign(
                profile_ref=ref,
                principal_ref="agent:approved",
                actor_types=(ActorType.AGENT,),
                context=_context(),
            )
        )
    first = gate.approvals.all()[0]
    _approve(gate, first.approval_id)

    with pytest.raises(ContractError):
        asyncio.run(
            service.assign(
                profile_ref=ref,
                principal_ref="agent:changed",
                actor_types=(ActorType.WORKER,),
                context=_context(approval_id=first.approval_id),
            )
        )
    assert len(gate.approvals.all()) == 2
    assert repository.list_assignments() == ()

    assignment = asyncio.run(
        service.assign(
            profile_ref=ref,
            principal_ref="agent:approved",
            actor_types=(ActorType.AGENT,),
            context=_context(approval_id=first.approval_id),
        )
    )
    assert assignment.principal_ref == "agent:approved"
