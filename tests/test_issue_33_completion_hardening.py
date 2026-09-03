from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.agents import (
    AgentCapabilityPolicy,
    AgentDataAccess,
    AgentInstructions,
    AgentProfile,
    AgentRuntime,
    AgentService,
    CapabilityConstraint,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.capabilities import (
    ECHO_CAPABILITY_ID,
    CapabilityRegistration,
    CapabilityRegistry,
    NativeEchoProvider,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.data import MemoryScope
from ai_multi_agent_platform.domain import OwnerRef, new_id

OWNER = OwnerRef(type="user", id="issue-33-hardening")
APPROVAL_REF = "approval:sensitive-tool"


class ApprovalEchoProvider(NativeEchoProvider):
    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        registrations = await super().capability_registrations()
        return tuple(
            replace(
                registration,
                capability=replace(
                    registration.capability,
                    required_approvals=(APPROVAL_REF,),
                ),
            )
            for registration in registrations
        )


def _approval_agent(service: AgentService) -> str:
    revision = service.create_agent(
        AgentProfile(
            name="Approval constrained agent",
            role="worker",
            instructions=AgentInstructions(role=InstructionSource(content="Work safely.")),
            capabilities=AgentCapabilityPolicy(
                allowed=(ECHO_CAPABILITY_ID,),
                constraints=(
                    CapabilityConstraint(
                        capability_id=ECHO_CAPABILITY_ID,
                        required=True,
                        exact_version="1.0",
                        approval_ref=APPROVAL_REF,
                    ),
                ),
            ),
        ),
        owner_ref=OWNER,
    )
    return revision.agent_id


def test_agent_approval_requirement_needs_canonical_capability_registry() -> None:
    service = AgentService(InMemoryAgentRepository())
    agent_id = _approval_agent(service)
    runtime = AgentRuntime(service)

    with pytest.raises(ContractError) as exc_info:
        runtime.prepare_agent(
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=agent_id,
            available_capability_ids=frozenset({ECHO_CAPABILITY_ID}),
        )

    assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION


def test_agent_approval_requirement_must_match_resolved_capability_policy() -> None:
    async def scenario() -> None:
        service = AgentService(InMemoryAgentRepository())
        agent_id = _approval_agent(service)
        registry = CapabilityRegistry()
        await registry.register_provider(NativeEchoProvider())
        runtime = AgentRuntime(service, capability_registry=registry)

        with pytest.raises(ContractError) as exc_info:
            runtime.prepare_agent(
                task_id=new_id("task"),
                run_id=new_id("run"),
                agent_id=agent_id,
            )

        assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION
        assert exc_info.value.details["approval_ref"] == APPROVAL_REF

    asyncio.run(scenario())


def test_agent_approval_requirement_is_accepted_when_canonical_capability_enforces_it() -> None:
    async def scenario() -> None:
        service = AgentService(InMemoryAgentRepository())
        agent_id = _approval_agent(service)
        registry = CapabilityRegistry()
        await registry.register_provider(ApprovalEchoProvider())
        runtime = AgentRuntime(service, capability_registry=registry)

        record = await runtime.start_agent(
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=agent_id,
        )

        assert record.capability_ids == (ECHO_CAPABILITY_ID,)

    asyncio.run(scenario())


def test_memory_and_knowledge_scope_enforcement_rejects_unassigned_access() -> None:
    service = AgentService(InMemoryAgentRepository())
    allowed_source = new_id("knowledge_source")
    denied_source = new_id("knowledge_source")
    revision = service.create_agent(
        AgentProfile(
            name="Scoped agent",
            role="researcher",
            instructions=AgentInstructions(role=InstructionSource(content="Research.")),
            data_access=AgentDataAccess(
                memory_scopes=(MemoryScope.AGENT,),
                knowledge_source_ids=(allowed_source,),
            ),
        ),
        owner_ref=OWNER,
    )

    service.ensure_memory_scope(revision.agent_id, revision.revision, MemoryScope.AGENT)
    service.ensure_knowledge_source(revision.agent_id, revision.revision, allowed_source)

    with pytest.raises(ContractError) as memory_error:
        service.ensure_memory_scope(revision.agent_id, revision.revision, MemoryScope.USER)
    assert memory_error.value.code is ErrorCode.FORBIDDEN

    with pytest.raises(ContractError) as knowledge_error:
        service.ensure_knowledge_source(revision.agent_id, revision.revision, denied_source)
    assert knowledge_error.value.code is ErrorCode.FORBIDDEN
