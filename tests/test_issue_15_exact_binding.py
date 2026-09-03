from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.configuration import LocalSecretProvider
from ai_multi_agent_platform.contracts import ContractError, OperationContext
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    AuthorizedDataFileProvider,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.security.enforced_providers import AuthorizedSecretProvider
from ai_multi_agent_platform.security.types import SecretReference


def test_refined_file_wrapper_also_secures_inherited_core_write(tmp_path) -> None:
    raw = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite")
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref="user:alice",
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.READ}),
                resource_types=frozenset({ResourceType.FILE}),
            ),
        )
    )
    secured = AuthorizedDataFileProvider(raw, AuthorizationGate(provider))
    context = OperationContext(
        correlation_id="corr-refined-core-write",
        owner_type="user",
        owner_id="alice",
    )

    with pytest.raises(ContractError):
        asyncio.run(secured.write(new_id("file"), b"blocked", context))
    assert asyncio.run(raw.list_files(_data_context(context))) == ()


def test_secret_create_approval_binds_changed_material_without_plaintext_leak() -> None:
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref="service:platform",
                actor_types=frozenset({ActorType.SERVICE}),
                approval_actions=frozenset({AuthorizationAction.MANAGE_CREDENTIALS}),
                resource_types=frozenset({ResourceType.SECRET_REFERENCE}),
            ),
            LocalPrincipalPolicy(
                principal_ref="user:reviewer",
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.APPROVE}),
                resource_types=frozenset({ResourceType.SECRET_REFERENCE}),
            ),
        )
    )
    gate = AuthorizationGate(provider)
    secured = AuthorizedSecretProvider(LocalSecretProvider(), gate)
    reference = SecretReference(
        provider="local-secrets",
        secret_id="api-token",
        scope="platform",
    )

    with pytest.raises(ContractError):
        asyncio.run(secured.create(reference, "secret-A", purpose="test"))
    first = gate.approvals.all()[0]
    asyncio.run(
        gate.decide_approval(
            first.approval_id,
            approver=ActorIdentity("user:reviewer", ActorType.HUMAN),
            approve=True,
            operation=OperationContext(
                correlation_id="corr-secret-review",
                owner_type="user",
                owner_id="reviewer",
            ),
        )
    )

    with pytest.raises(ContractError):
        asyncio.run(secured.create(reference, "secret-B", purpose="test"))
    assert len(gate.approvals.all()) == 2

    created = asyncio.run(secured.create(reference, "secret-A", purpose="test"))
    assert created.reference.secret_id == "api-token"
    combined = repr(gate.audit_records) + repr(gate.approvals.all())
    assert "secret-A" not in combined
    assert "secret-B" not in combined


def _data_context(operation: OperationContext):
    from ai_multi_agent_platform.data import DataAccessContext

    return DataAccessContext(operation=operation, actor_ref="user:alice")
