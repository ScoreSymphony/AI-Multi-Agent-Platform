from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Never

import pytest

from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.deployment.config import SingleNodeConfig
from ai_multi_agent_platform.deployment.single_node import build_single_node_deployment
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.repositories import RepositoryCallContext


class _DiscoveryResolverReached(RuntimeError):
    pass


def test_single_node_forwards_provider_neutral_repository_discovery_resolver(
    tmp_path: Path,
) -> None:
    connection_id = new_id("connection")
    provider_id = "repository-test-provider"

    async def discovery_resolver(requested_connection_id: str, requested_provider_id: str) -> Never:
        assert requested_connection_id == connection_id
        assert requested_provider_id == provider_id
        raise _DiscoveryResolverReached

    deployment = build_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "single-node", secure_cookie=False),
        repository_discovery_resolver=discovery_resolver,
    )
    context = RepositoryCallContext(
        operation=OperationContext(
            correlation_id="issue-82-single-node-discovery-composition",
            owner_type="user",
            owner_id="repository-owner",
            project_id=new_id("project"),
        ),
        actor_ref="repository-owner",
    )

    with pytest.raises(_DiscoveryResolverReached):
        asyncio.run(
            deployment.repository_management.discover(
                connection_id,
                provider_id,
                context,
            )
        )
