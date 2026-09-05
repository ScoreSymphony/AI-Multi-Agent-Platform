from __future__ import annotations

import pytest

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentModelPolicy,
    AgentProfile,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.agents.routing_profile_control_plane import (
    RoutingProfileAwareAgentCommandHandlers,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.control_plane import ActorContext, RequestContext
from ai_multi_agent_platform.control_plane.models import json_object
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.models import (
    JsonModelRoutingProfileRepository,
    ModelRoutingProfileAssignmentGate,
    ModelRoutingProfilePolicy,
    ModelRoutingProfileService,
)
from ai_multi_agent_platform.testing import FakeAuthorizationProvider

OWNER = OwnerRef(type="user", id="user-routing-assignment")


def _operation(project_id: str | None = None) -> OperationContext:
    return OperationContext(
        correlation_id="corr-routing-assignment",
        owner_type=OWNER.type,
        owner_id=OWNER.id,
        project_id=project_id,
    )


def _request() -> RequestContext:
    return RequestContext(
        request_id="request-routing-assignment",
        correlation_id="corr-routing-assignment",
        actor=ActorContext(
            principal_ref=OWNER.id,
            owner_type=OWNER.type,
            owner_id=OWNER.id,
            actor_type="human",
        ),
    )


def _agent_profile(routing_profile_ref: str) -> AgentProfile:
    return AgentProfile(
        name="Authorized routing consumer",
        role="researcher",
        instructions=AgentInstructions(
            role=InstructionSource(content="Use the assigned model-routing profile.")
        ),
        model=AgentModelPolicy(routing_profile_ref=routing_profile_ref),
    )


@pytest.mark.asyncio
async def test_assignment_gate_uses_distinct_assign_authorization_action(tmp_path) -> None:
    repository = JsonModelRoutingProfileRepository(tmp_path / "profiles.json")
    profile = await ModelRoutingProfileService(repository).create_profile(
        name="Assignable",
        policy=ModelRoutingProfilePolicy(),
        owner_ref=OWNER,
        principal_ref=OWNER.id,
        context=_operation(),
    )
    authorization = FakeAuthorizationProvider(allowed=False)
    gate = ModelRoutingProfileAssignmentGate(repository, authorization=authorization)

    with pytest.raises(ContractError) as caught:
        await gate.authorize(
            profile.ref,
            principal_ref=OWNER.id,
            context=_operation(),
            actor_type="human",
        )

    assert caught.value.code is ErrorCode.FORBIDDEN
    assert authorization.calls[-1].action == "model-routing-profile:assign"
    assert authorization.calls[-1].resource_ref == profile.ref.canonical_ref
    assert authorization.calls[-1].actor_type == "human"


@pytest.mark.asyncio
async def test_assignment_without_authorization_provider_requires_exact_owner_context(
    tmp_path,
) -> None:
    repository = JsonModelRoutingProfileRepository(tmp_path / "profiles.json")
    profile = await ModelRoutingProfileService(repository).create_profile(
        name="Owner scoped",
        policy=ModelRoutingProfilePolicy(),
        owner_ref=OWNER,
        principal_ref=OWNER.id,
        context=_operation(),
    )
    gate = ModelRoutingProfileAssignmentGate(repository)

    with pytest.raises(ContractError) as caught:
        await gate.authorize(
            profile.ref,
            principal_ref=OWNER.id,
            context=OperationContext(correlation_id="corr-missing-owner"),
            actor_type="human",
        )

    assert caught.value.code is ErrorCode.FORBIDDEN


@pytest.mark.asyncio
async def test_agent_create_fails_closed_when_profile_assignment_is_denied(tmp_path) -> None:
    project_id = new_id("project")
    repository = JsonModelRoutingProfileRepository(tmp_path / "profiles.json")
    profile = await ModelRoutingProfileService(repository).create_profile(
        name="Project routing",
        policy=ModelRoutingProfilePolicy(),
        owner_ref=OWNER,
        principal_ref=OWNER.id,
        context=_operation(project_id),
        project_id=project_id,
    )
    authorization = FakeAuthorizationProvider(allowed=False)
    agents_repository = InMemoryAgentRepository()
    handlers = RoutingProfileAwareAgentCommandHandlers(
        AgentService(agents_repository),
        ModelRoutingProfileAssignmentGate(repository, authorization=authorization),
    )

    with pytest.raises(ContractError) as caught:
        await handlers.create_agent(
            _request(),
            "agents",
            {
                "profile": json_object(_agent_profile(profile.ref.canonical_ref)),
                "project_id": project_id,
            },
        )

    assert caught.value.code is ErrorCode.FORBIDDEN
    assert agents_repository.list_agents() == ()
    assert authorization.calls[-1].action == "model-routing-profile:assign"
    assert authorization.calls[-1].actor_type == "human"
