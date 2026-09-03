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
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id

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


def test_standard_developer_rejects_shell_capability_without_matching_approval_contract() -> None:
    async def scenario() -> None:
        service = AgentService(InMemoryAgentRepository())
        bootstrap_standard_agents(service)
        developer = get_standard_agent_template("developer")
        registry = CapabilityRegistry()
        await registry.register_provider(_UnprotectedStandardDeveloperCapabilityProvider())
        runtime = AgentRuntime(service, capability_registry=registry)

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
        runtime = AgentRuntime(service, capability_registry=registry)

        record = await runtime.start_agent(
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=developer.agent_id,
            requested_capability_ids=(SHELL_CAPABILITY_ID,),
        )

        assert FILE_READ_CAPABILITY_ID in record.capability_ids
        assert SHELL_CAPABILITY_ID in record.capability_ids
        assert developer.profile.capabilities.constraints[2].approval_ref == SHELL_APPROVAL_REF

    asyncio.run(scenario())
