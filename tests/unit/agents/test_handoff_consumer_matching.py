from __future__ import annotations

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.handoffs import CanonicalConsumerRequirementEvaluator


def test_scoped_exact_handoff_consumer_remains_eligible() -> None:
    repository = InMemoryAgentRepository()
    revision = AgentService(repository).create_agent(
        AgentProfile(
            name="Scoped researcher",
            role="researcher",
            instructions=AgentInstructions(role=InstructionSource(content="Research.")),
        ),
        owner_ref=OwnerRef(type="user", id="issue-903-scoped-handoff-owner"),
        project_id=new_id("project"),
        workspace_id=new_id("workspace"),
    )
    consumer = AgentRevisionRef(revision.agent_id, revision.revision)

    evaluator = CanonicalConsumerRequirementEvaluator(repository)

    assert evaluator.accepts(("role:researcher",), consumer)
