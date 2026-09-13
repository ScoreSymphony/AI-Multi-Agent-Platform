from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.adapters.single_node_app import build_default_single_node_deployment
from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilityInvoker,
    InvocationTrace,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.deployment.config import SingleNodeConfig
from ai_multi_agent_platform.domain import new_id


def test_shipped_single_node_registers_repository_capabilities_for_agent_runtime(
    tmp_path: Path,
) -> None:
    deployment = build_default_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "single-node", secure_cookie=False)
    )

    inventory = {
        capability.capability_id: capability
        for capability in deployment.capabilities.inventory_capabilities()
    }
    providers = {
        provider.provider_id: provider for provider in deployment.capabilities.inventory_providers()
    }

    assert deployment.agent_runtime.capability_registry is deployment.capabilities
    assert "platform.repository-bridge" in providers
    assert providers["platform.repository-bridge"].provider_type == "repository_bridge"
    assert "repository.read" in inventory
    assert "repository.inspect_refs" in inventory
    assert "repository.status" in inventory
    assert "repository.diff" in inventory
    assert "repository.commit" in inventory
    assert "repository.push" in inventory
    assert inventory["repository.commit"].required_permissions == ("repository.commit",)
    assert inventory["repository.push"].required_permissions == ("repository.push",)


def test_shipped_repository_capability_bridge_fails_closed_without_owner_identity(
    tmp_path: Path,
) -> None:
    deployment = build_default_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "single-node", secure_cookie=False)
    )

    async def scenario() -> None:
        invoker = CapabilityInvoker(deployment.capabilities)
        operation = OperationContext(correlation_id="issue-82-ownerless-repository-call")
        trace = InvocationTrace(
            correlation_id=operation.correlation_id,
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=new_id("agent"),
        )

        with pytest.raises(ContractError) as exc_info:
            await invoker.invoke(
                CapabilityInvocation(
                    invocation_id="issue-82-ownerless-read",
                    capability_id="repository.read",
                    arguments={"repository_id": new_id("external_resource")},
                    context=operation,
                    trace=trace,
                    granted_permissions=frozenset({"repository.read"}),
                )
            )

        assert exc_info.value.code is ErrorCode.INVALID_REQUEST
        assert "owner identity" in str(exc_info.value)

    asyncio.run(scenario())
