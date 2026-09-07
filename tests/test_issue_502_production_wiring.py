from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.adapters.single_node_app import build_default_single_node_deployment
from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilityInvoker,
    CapabilityRegistry,
    InvocationTrace,
)
from ai_multi_agent_platform.connectors import Connection
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.deployment.config import SingleNodeConfig
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.repositories import (
    LocalGitRepositoryProvider,
    RepositoryBinding,
    RepositoryConnection,
    RepositoryRegistry,
    RepositoryService,
)
from ai_multi_agent_platform.repository_intelligence import BaselineRepositoryIntelligenceProvider
from ai_multi_agent_platform.repository_intelligence.wiring import AuthorizedRepositorySnapshotLoader
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)


def _actor_ref(context: OperationContext) -> str:
    if context.owner_id is None:
        raise ContractError(ErrorCode.INVALID_REQUEST, "owner identity required")
    return context.owner_id


def test_shipped_single_node_registers_repository_intelligence_baseline(tmp_path: Path) -> None:
    deployment = build_default_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "single-node", secure_cookie=False)
    )

    providers = {
        provider.provider_id: provider for provider in deployment.capabilities.inventory_providers()
    }
    inventory = {
        capability.capability_id: capability
        for capability in deployment.capabilities.inventory_capabilities()
    }

    assert "platform.repository-intelligence.baseline" in providers
    assert providers["platform.repository-intelligence.baseline"].provider_type == (
        "repository_intelligence"
    )
    assert "repository.map" in inventory
    assert "repository.text_search" in inventory
    assert "repository.source_slice" in inventory


def test_authorized_loader_reads_exact_tree_through_repository_policy(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        actor_ref = new_id("user")
        operation = OperationContext(
            correlation_id="issue-502-production-wiring",
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
                display_name="Repository intelligence fixture",
                project_id=project_id,
            ),
            provider_id="local-git",
            local=True,
        )
        root = tmp_path / "repo"
        provider = LocalGitRepositoryProvider(root, connection)
        repository = await provider.initialize(operation)
        (root / "README.md").write_text("# Exact snapshot\nneedle\n", encoding="utf-8")
        commit = await provider.commit(
            repository,
            "initial",
            operation,
            author_name="Repository Intelligence Test",
            author_email="repository-intelligence@example.invalid",
        )
        repository = await provider.read(repository, operation)

        repository_registry = RepositoryRegistry()
        repository_registry.register(RepositoryBinding(connection, repository, provider))
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
        repository_service = RepositoryService(repository_registry, authorization)
        capabilities = CapabilityRegistry()
        await capabilities.register_provider(
            BaselineRepositoryIntelligenceProvider(
                AuthorizedRepositorySnapshotLoader(
                    repository_service,
                    repository_registry,
                    actor_resolver=_actor_ref,
                )
            )
        )
        invoker = CapabilityInvoker(capabilities)
        trace = InvocationTrace(
            correlation_id=operation.correlation_id,
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=new_id("agent"),
            project_id=project_id,
        )

        result = await invoker.invoke(
            CapabilityInvocation(
                invocation_id="issue-502-production-map",
                capability_id="repository.map",
                arguments={"repository_id": repository.id, "revision": commit.revision},
                context=operation,
                trace=trace,
                granted_permissions=frozenset({"repository.map"}),
            )
        )

        assert result.provider_id == "platform.repository-intelligence.baseline"
        assert isinstance(result.output, dict)
        assert result.output["entries"] == [
            {"path": "README.md", "size_bytes": len(b"# Exact snapshot\nneedle\n")}
        ]
        provenance = result.output["provenance"]
        assert isinstance(provenance, dict)
        assert provenance["repository_id"] == repository.id
        assert provenance["resolved_revision"] == commit.revision

        unauthorized = OperationContext(
            correlation_id="issue-502-production-denied",
            owner_type="user",
            owner_id=new_id("user"),
            project_id=project_id,
        )
        denied_trace = InvocationTrace(
            correlation_id=unauthorized.correlation_id,
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=new_id("agent"),
            project_id=project_id,
        )
        with pytest.raises(ContractError) as denied:
            await invoker.invoke(
                CapabilityInvocation(
                    invocation_id="issue-502-production-denied-map",
                    capability_id="repository.map",
                    arguments={"repository_id": repository.id},
                    context=unauthorized,
                    trace=denied_trace,
                    granted_permissions=frozenset({"repository.map"}),
                )
            )
        assert denied.value.code is ErrorCode.FORBIDDEN

    asyncio.run(scenario())
