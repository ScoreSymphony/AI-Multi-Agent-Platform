from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilityInvoker,
    CapabilityRegistry,
    InvocationTrace,
)
from ai_multi_agent_platform.connectors import Connection
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.repositories import (
    LocalGitRepositoryProvider,
    RepositoryBinding,
    RepositoryCapabilityProvider,
    RepositoryConnection,
    RepositoryRegistry,
    RepositoryService,
)
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)


def test_repository_operations_run_through_capability_registry_and_service_policy(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        actor_ref = new_id("user")
        operation = OperationContext(
            correlation_id="issue-82-repository-capability",
            owner_type="user",
            owner_id=actor_ref,
            project_id=project_id,
        )
        connection = RepositoryConnection(
            connection=Connection(
                id=new_id("connection"),
                connector_type_id="local-git",
                connector_version="1.0",
                owner_type="user",
                owner_id=actor_ref,
                display_name="Repository capability fixture",
                project_id=project_id,
            ),
            provider_id="local-git",
            local=True,
        )
        root = tmp_path / "repo"
        provider = LocalGitRepositoryProvider(root, connection)
        repository = await provider.initialize(operation)
        (root / "README.md").write_text("baseline\n", encoding="utf-8")
        await provider.commit(
            repository,
            "initial",
            operation,
            author_name="Repository Test",
            author_email="repository@example.invalid",
        )
        repository = await provider.read(repository, operation)

        repositories = RepositoryRegistry()
        repositories.register(RepositoryBinding(connection, repository, provider))
        authorization = AuthorizationGate(
            LocalAuthorizationProvider(
                (
                    LocalPrincipalPolicy(
                        principal_ref=actor_ref,
                        actor_types=frozenset({ActorType.HUMAN}),
                        allowed_actions=frozenset({AuthorizationAction.READ}),
                        resource_types=frozenset({ResourceType.GENERIC}),
                        project_ids=frozenset({project_id}),
                    ),
                )
            )
        )
        service = RepositoryService(repositories, authorization)
        capabilities = CapabilityRegistry()
        await capabilities.register_provider(
            RepositoryCapabilityProvider(service, actor_resolver=lambda context: actor_ref)
        )
        invoker = CapabilityInvoker(capabilities)
        trace = InvocationTrace(
            correlation_id=operation.correlation_id,
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=new_id("agent"),
            project_id=project_id,
        )

        inventory = {
            capability.capability_id: capability
            for capability in capabilities.inventory_capabilities()
        }
        assert inventory["repository.inspect_refs"].required_permissions == (
            "repository.inspect_refs",
        )
        assert inventory["repository.status"].required_permissions == ("repository.status",)
        assert inventory["repository.commit"].side_effects.value == "local_write"
        assert inventory["repository.push"].side_effects.value == "external"

        branches = await invoker.invoke(
            CapabilityInvocation(
                invocation_id="repository-branches-capability",
                capability_id="repository.inspect_refs",
                arguments={"repository_id": repository.id, "kind": "branches"},
                context=operation,
                trace=trace,
                granted_permissions=frozenset({"repository.inspect_refs"}),
            )
        )
        assert isinstance(branches.output, dict)
        assert branches.output["branches"] == ["main"]

        history = await invoker.invoke(
            CapabilityInvocation(
                invocation_id="repository-commits-capability",
                capability_id="repository.inspect_refs",
                arguments={
                    "repository_id": repository.id,
                    "kind": "commits",
                    "revision": "HEAD",
                    "limit": 1,
                },
                context=operation,
                trace=trace,
                granted_permissions=frozenset({"repository.inspect_refs"}),
            )
        )
        assert isinstance(history.output, dict)
        commits = history.output["commits"]
        assert isinstance(commits, list)
        assert len(commits) == 1
        assert commits[0]["message"] == "initial"

        with pytest.raises(ContractError) as invalid_limit:
            await invoker.invoke(
                CapabilityInvocation(
                    invocation_id="repository-invalid-commit-limit",
                    capability_id="repository.inspect_refs",
                    arguments={
                        "repository_id": repository.id,
                        "kind": "commits",
                        "limit": 0,
                    },
                    context=operation,
                    trace=trace,
                    granted_permissions=frozenset({"repository.inspect_refs"}),
                )
            )
        assert invalid_limit.value.code is ErrorCode.INVALID_REQUEST

        status = await invoker.invoke(
            CapabilityInvocation(
                invocation_id="repository-status-capability",
                capability_id="repository.status",
                arguments={"repository_id": repository.id},
                context=operation,
                trace=trace,
                granted_permissions=frozenset({"repository.status"}),
            )
        )
        assert status.provider_id == "platform.repository-bridge"
        assert isinstance(status.output, dict)
        assert status.output["repository_id"] == repository.id
        assert status.output["clean"] is True

        (root / "README.md").write_text("changed\n", encoding="utf-8")
        with pytest.raises(ContractError) as exc_info:
            await invoker.invoke(
                CapabilityInvocation(
                    invocation_id="repository-commit-capability",
                    capability_id="repository.commit",
                    arguments={
                        "repository_id": repository.id,
                        "message": "must remain denied",
                        "author_name": "Repository Test",
                        "author_email": "repository@example.invalid",
                    },
                    context=operation,
                    trace=trace,
                    granted_permissions=frozenset({"repository.commit"}),
                )
            )
        assert exc_info.value.code is ErrorCode.FORBIDDEN
        assert (await provider.status(repository, operation)).modified_paths == ("README.md",)

    asyncio.run(scenario())
