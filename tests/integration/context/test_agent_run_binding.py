from __future__ import annotations

import asyncio

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRuntime,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.context import (
    ContextAssemblyRequest,
    ContextBoundAgentRuntime,
    ContextBudget,
    ContextCandidate,
    ContextEntryRole,
    ContextResolver,
    ContextSourceRef,
    ContextSourceType,
)
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.security import ActorIdentity, ActorType
from ai_multi_agent_platform.testing import FakeAuthorizationProvider


def test_agent_run_is_bound_to_exact_canonical_context_bundle() -> None:
    repository = InMemoryAgentRepository()
    service = AgentService(repository)
    agent = service.create_agent(
        AgentProfile(
            name="Context-bound worker",
            role="worker",
            instructions=AgentInstructions(
                role=InstructionSource(content="Use only the supplied canonical context."),
            ),
        ),
        owner_ref=OwnerRef(type="user", id="issue-590-owner"),
    )
    task_id = new_id("task")
    run_id = new_id("run")
    context_text = "Canonical task context for the AgentRun."
    candidate = ContextCandidate(
        source=ContextSourceRef(
            source_type=ContextSourceType.TASK,
            source_id=task_id,
            revision="task-r1",
            digest="task-source-r1-digest",
        ),
        role=ContextEntryRole.INSTRUCTION,
        selection_reason="canonical task intent",
        mandatory=True,
        inline_content=context_text,
    )
    bundle = asyncio.run(
        ContextResolver(FakeAuthorizationProvider()).resolve(
            ContextAssemblyRequest(
                task_id=task_id,
                run_id=run_id,
                agent_id=agent.agent_id,
                agent_revision=agent.revision,
                actor=ActorIdentity("user:issue-590", ActorType.HUMAN),
                operation=OperationContext(correlation_id="issue-590-agent-run"),
                candidates=(candidate,),
                budget=ContextBudget(max_tokens=1024, max_bytes=4096, max_items=8),
            )
        )
    )

    runtime = ContextBoundAgentRuntime(AgentRuntime(service))
    record, binding = asyncio.run(runtime.start_agent(bundle=bundle))

    assert record.task_id == task_id
    assert record.run_id == run_id
    assert binding.agent_run_id == record.agent_run_id
    assert binding.context_bundle_id == bundle.context_bundle_id
    assert binding.context_bundle_digest == bundle.digest
    assert runtime.bundle_repository.get(bundle.context_bundle_id) == bundle
    assert runtime.binding_repository.get(record.agent_run_id) == binding

    mapping = record.telemetry["orchestrator_mapping"]
    assert isinstance(mapping, dict)
    assert mapping["context_bundle_id"] == bundle.context_bundle_id
    assert mapping["context_bundle_digest"] == bundle.digest
    assert mapping["context_renderer_id"] == "reference-context-renderer/v1"

    persisted_run = repository.get_agent_run(record.agent_run_id)
    assert persisted_run == record
