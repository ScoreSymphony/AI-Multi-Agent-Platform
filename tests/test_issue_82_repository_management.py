from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.repositories import (
    RepositoryCallContext,
    RepositoryManagementService,
    RepositoryRegistry,
    SqliteRepositoryBindingCatalog,
)
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)


def test_managed_local_repository_attach_persists_and_detaches_without_deleting_content(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        owner_id = new_id("user")
        project_id = new_id("project")
        operation = OperationContext(
            correlation_id="issue-82-repository-management",
            owner_type="user",
            owner_id=owner_id,
            project_id=project_id,
        )
        context = RepositoryCallContext(operation=operation, actor_ref=owner_id)
        registry = RepositoryRegistry()
        catalog = SqliteRepositoryBindingCatalog(tmp_path / "repository-bindings.sqlite3")
        authorization = AuthorizationGate(
            LocalAuthorizationProvider(
                (
                    LocalPrincipalPolicy(
                        principal_ref=owner_id,
                        actor_types=frozenset({ActorType.HUMAN}),
                        allowed_actions=frozenset(
                            {
                                AuthorizationAction.READ,
                                AuthorizationAction.CREATE,
                                AuthorizationAction.DELETE,
                            }
                        ),
                        resource_types=frozenset({ResourceType.GENERIC}),
                        project_ids=frozenset({project_id}),
                    ),
                )
            )
        )
        managed_root = tmp_path / "repositories"
        service = RepositoryManagementService(
            registry,
            catalog,
            authorization,
            managed_local_root=managed_root,
        )

        attached = await service.attach_local(
            "project-repository",
            context,
            initialize=True,
        )
        assert registry.resolve(attached.id).reference.id == attached.id
        record = catalog.get(attached.id)
        assert record.local is True
        assert record.provider_id == "local-git"
        assert record.adapter_configuration["root"] == str(
            (managed_root / "project-repository").resolve()
        )
        assert "root" not in attached.to_dict()["metadata"]

        detached = await service.detach(attached.id, context)
        assert detached.id == attached.id
        with pytest.raises(ContractError) as registry_error:
            registry.resolve(attached.id)
        assert registry_error.value.code is ErrorCode.NOT_FOUND
        with pytest.raises(ContractError) as catalog_error:
            catalog.get(attached.id)
        assert catalog_error.value.code is ErrorCode.NOT_FOUND
        assert (managed_root / "project-repository" / ".git").is_dir()

        with pytest.raises(ContractError) as unsafe_name:
            await service.attach_local("../outside", context, initialize=True)
        assert unsafe_name.value.code is ErrorCode.INVALID_REQUEST

    asyncio.run(scenario())
