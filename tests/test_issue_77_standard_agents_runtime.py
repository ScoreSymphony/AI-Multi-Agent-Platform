from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.agents import (
    AgentRuntime,
    AgentService,
    InMemoryAgentRepository,
    bootstrap_standard_agents,
    get_standard_agent_template,
)
from ai_multi_agent_platform.capabilities import (
    CapabilityRegistration,
    CapabilityRegistry,
    NativeEchoProvider,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, HealthStatus
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
)
from ai_multi_agent_platform.testing import FakeModelProvider

FILE_READ_CAPABILITY_ID = "tool.file.read"
SHELL_CAPABILITY_ID = "tool.shell.execute"
SHELL_APPROVAL_REF = "approval:standard-shell-execution"


class _StandardDeveloperCapabilityProvider(NativeEchoProvider):
    protect_shell = True

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        registrations = await super().capability_registrations()
        base = registrations[0]
        file_read = replace(
            base,
            capability=replace(
                base.capability,
                capability_id=FILE_READ_CAPABILITY_ID,
                name="File Read",
                required_approvals=(),
            ),
        )
        shell = replace(
            base,
            capability=replace(
                base.capability,
                capability_id=SHELL_CAPABILITY_ID,
                name="Shell Execute",
                required_approvals=(SHELL_APPROVAL_REF,) if self.protect_shell else (),
            ),
        )
        return (file_read, shell)


class _UnprotectedStandardDeveloperCapabilityProvider(_StandardDeveloperCapabilityProvider):
    protect_shell = False


def _model_registry() -> ModelRegistry:
    registry = ModelRegistry()
    provider = FakeModelProvider()
    registry.register_provider(provider)
    registry.register_model(
        ModelConfiguration(
            config_id="model-standard-agent-test",
            display_name="Standard Agent Test Model",
            provider_id=provider.descriptor.provider_id,
            capabilities=ModelCapabilities(
                context_window=32_768,
                modalities=("text",),
            ),
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
        )
    )
    return registry


def test_standard_developer_rejects_shell_capability_without_matching_approval_contract() -> None:
    async def scenario() -> None:
        service = AgentService(InMemoryAgentRepository())
        bootstrap_standard_agents(service)
        developer = get_standard_agent_template("developer")
        registry = CapabilityRegistry()
        await registry.register_provider(_UnprotectedStandardDeveloperCapabilityProvider())
        runtime = AgentRuntime(
            service,
            model_registry=_model_registry(),
            capability_registry=registry,
        )

        with pytest.raises(ContractError) as exc_info:
            runtime.prepare_agent(
                task_id=new_id("task"),
                run_id=new_id("run"),
                agent_id=developer.agent_id,
                requested_capability_ids=(SHELL_CAPABILITY_ID,),
            )

        assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION
        assert exc_info.value.details["approval_ref"] == SHELL_APPROVAL_REF
        assert exc_info.value.details["capability_id"] == SHELL_CAPABILITY_ID

    asyncio.run(scenario())


def test_standard_developer_shell_action_preserves_canonical_approval_binding_in_run() -> None:
    async def scenario() -> None:
        service = AgentService(InMemoryAgentRepository())
        bootstrap_standard_agents(service)
        developer = get_standard_agent_template("developer")
        registry = CapabilityRegistry()
        await registry.register_provider(_StandardDeveloperCapabilityProvider())
        runtime = AgentRuntime(
            service,
            model_registry=_model_registry(),
            capability_registry=registry,
        )

        record = await runtime.start_agent(
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=developer.agent_id,
            requested_capability_ids=(SHELL_CAPABILITY_ID,),
        )

        shell_constraint = next(
            constraint
            for constraint in developer.profile.capabilities.constraints
            if constraint.capability_id == SHELL_CAPABILITY_ID
        )
        assert FILE_READ_CAPABILITY_ID in record.capability_ids
        assert SHELL_CAPABILITY_ID in record.capability_ids
        assert shell_constraint.approval_ref == SHELL_APPROVAL_REF

    asyncio.run(scenario())
