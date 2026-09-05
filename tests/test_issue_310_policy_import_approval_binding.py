from __future__ import annotations

import asyncio
from dataclasses import replace

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
            correlation_id="issue-310-import-approval-binding",
            owner_type="user",
            owner_id="importer",
        ),
        actor_ref="user:importer",
        approval_id=approval_id,
    )


def _candidate(
    *,
    allowed_action: AuthorizationAction,
) -> tuple[AuthorizationPolicyProfileDefinition, tuple[AuthorizationPolicyProfileRevision, ...]]:
    profile_id = new_id("authorization_policy_profile")
    owner = OwnerRef(type="user", id="destination-owner")
    definition = AuthorizationPolicyProfileDefinition(
        policy_profile_id=profile_id,
        owner_ref=owner,
        current_revision=1,
        enabled=False,
    )
    revision = AuthorizationPolicyProfileRevision(
        policy_profile_id=profile_id,
        revision=1,
        owner_ref=owner,
        content=AuthorizationPolicyProfileContent(
            name="Imported approval-bound policy",
            allowed_actions=(allowed_action,),
            resource_types=(ResourceType.GENERIC,),
            provenance=AuthorizationPolicyProvenance(
                created_by="user:source-owner",
                source="portable-package",
                source_reference="package:test",
                imported=True,
                trusted=False,
            ),
        ),
        created_at=definition.created_at,
    )
    return definition, (revision,)


def test_import_approval_cannot_be_reused_for_changed_policy_content() -> None:
    repository = InMemoryAuthorizationPolicyProfileRepository()
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref="user:importer",
                actor_types=frozenset({ActorType.HUMAN}),
                approval_actions=frozenset({AuthorizationAction.CREATE}),
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
    gate = AuthorizationGate(provider)
    service = AuthorizationPolicyProfileService(repository, gate)
    definition, revisions_a = _candidate(allowed_action=AuthorizationAction.READ)
    revisions_b = (
        replace(
            revisions_a[0],
            content=replace(
                revisions_a[0].content,
                allowed_actions=(AuthorizationAction.ADMINISTER,),
            ),
        ),
    )

    with pytest.raises(ContractError) as first_pending:
        asyncio.run(
            service.import_profile(
                definition=definition,
                revisions=revisions_a,
                context=_context(),
            )
        )
    assert first_pending.value.code is ErrorCode.FORBIDDEN
    first = gate.approvals.all()[0]

    asyncio.run(
        gate.decide_approval(
            first.approval_id,
            approver=ActorIdentity("user:reviewer", ActorType.HUMAN),
            approve=True,
            operation=OperationContext(
                correlation_id="issue-310-import-approval-review",
                owner_type="user",
                owner_id="reviewer",
            ),
        )
    )

    with pytest.raises(ContractError) as changed_pending:
        asyncio.run(
            service.import_profile(
                definition=definition,
                revisions=revisions_b,
                context=_context(approval_id=first.approval_id),
            )
        )
    assert changed_pending.value.code is ErrorCode.FORBIDDEN
    approvals = gate.approvals.all()
    assert len(approvals) == 2
    assert approvals[0].requested_action_digest != approvals[1].requested_action_digest
    assert repository.list_profiles() == ()

    imported = asyncio.run(
        service.import_profile(
            definition=definition,
            revisions=revisions_a,
            context=_context(approval_id=first.approval_id),
        )
    )
    assert imported.policy_profile_id == definition.policy_profile_id
    assert repository.get_revision(imported.policy_profile_id, 1).content.allowed_actions == (
        AuthorizationAction.READ,
    )
