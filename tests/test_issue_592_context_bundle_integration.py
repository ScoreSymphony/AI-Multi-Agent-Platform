from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.agents.models import AgentRevisionRef
from ai_multi_agent_platform.context import (
    ContextAssemblyRequest,
    ContextAssemblyService,
    ContextBlockerReason,
    ContextBudget,
    ContextResolutionError,
    ContextResolver,
    ContextSourceRequest,
    ContextSourceType,
    ContextTrust,
    InMemoryContextBundleRepository,
)
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.handoffs import (
    ConsumedHandoffContextAdapter,
    HandoffConsumption,
    HandoffContent,
    HandoffRuntimeContext,
    build_handoff,
    handoff_context_source,
)
from ai_multi_agent_platform.security.authorization import ActorIdentity, ActorType
from ai_multi_agent_platform.testing import FakeAuthorizationProvider


def _runtime_context() -> tuple[HandoffRuntimeContext, AgentRevisionRef]:
    producer = AgentRevisionRef(new_id("agent"), 1)
    consumer = AgentRevisionRef(new_id("agent"), 2)
    content = HandoffContent(
        task_id=new_id("task"),
        plan_id=new_id("plan"),
        producer_step_id=new_id("step"),
        consumer_step_id=new_id("step"),
        producer_run_id=new_id("run"),
        producer=producer,
        intended_consumer=consumer,
        objective="Transfer reviewed implementation work.",
        completed_work_summary="Implementation and local verification completed.",
        recommended_next_action="Review the canonical artifacts and continue the dependent step.",
        requested_output="Produce the dependent step result with preserved provenance.",
    )
    handoff = build_handoff(
        handoff_id=new_id("handoff"),
        revision=1,
        content=content,
    )
    consumption = HandoffConsumption(
        handoff_id=handoff.handoff_id,
        handoff_revision=handoff.revision,
        handoff_digest=handoff.content_digest,
        consuming_run_id=new_id("run"),
        consumer=consumer,
    )
    return (
        HandoffRuntimeContext(
            handoff=handoff,
            consumption=consumption,
            context_source=handoff_context_source(handoff),
        ),
        consumer,
    )


def _assembly_request(
    runtime_context: HandoffRuntimeContext,
    consumer: AgentRevisionRef,
) -> ContextAssemblyRequest:
    return ContextAssemblyRequest(
        task_id=runtime_context.handoff.task_id,
        run_id=runtime_context.consumption.consuming_run_id,
        agent_id=consumer.agent_id,
        agent_revision=consumer.revision,
        actor=ActorIdentity(consumer.agent_id, ActorType.AGENT),
        operation=OperationContext(correlation_id="issue-592-context-bundle"),
        candidates=(),
        budget=ContextBudget(max_items=4, max_tokens=256, max_bytes=4096),
        plan_id=runtime_context.handoff.content.plan_id,
        step_id=runtime_context.handoff.content.consumer_step_id,
    )


def test_consumed_handoff_is_a_canonical_context_bundle_source() -> None:
    runtime_context, consumer = _runtime_context()
    authorization = FakeAuthorizationProvider()
    service = ContextAssemblyService(
        ContextResolver(authorization),
        InMemoryContextBundleRepository(),
    )
    adapter = ConsumedHandoffContextAdapter((runtime_context,))

    bundle = asyncio.run(
        service.assemble(
            _assembly_request(runtime_context, consumer),
            adapters=(adapter,),
        )
    )

    assert len(bundle.entries) == 1
    entry = bundle.entries[0]
    assert entry.source.source_type is ContextSourceType.AGENT_HANDOFF
    assert entry.source.source_id == runtime_context.handoff.handoff_id
    assert entry.source.revision == str(runtime_context.handoff.revision)
    assert entry.source.digest == runtime_context.handoff.content_digest
    assert entry.content_digest == runtime_context.handoff.content_digest
    assert entry.content_ref == (
        f"handoff:{runtime_context.handoff.handoff_id}@{runtime_context.handoff.revision}"
    )
    assert entry.selection_reason == "intentional_agent_handoff"
    assert entry.trust is ContextTrust.UNTRUSTED
    assert entry.metadata["consuming_run_id"] == runtime_context.consumption.consuming_run_id
    assert bundle.reproducibility_limited is False

    assert len(authorization.calls) == 1
    auth_request = authorization.calls[0]
    assert auth_request.resource_type == "generic"
    assert auth_request.resource_ref == runtime_context.handoff.handoff_id
    assert auth_request.trust_context["context_source_type"] == "agent_handoff"


def test_handoff_context_inclusion_fails_closed_when_read_is_denied() -> None:
    runtime_context, consumer = _runtime_context()
    authorization = FakeAuthorizationProvider(allowed=False)
    service = ContextAssemblyService(
        ContextResolver(authorization),
        InMemoryContextBundleRepository(),
    )

    with pytest.raises(ContextResolutionError) as error:
        asyncio.run(
            service.assemble(
                _assembly_request(runtime_context, consumer),
                adapters=(ConsumedHandoffContextAdapter((runtime_context,)),),
            )
        )

    assert error.value.blocker.reason is ContextBlockerReason.MANDATORY_UNAUTHORIZED
    assert error.value.blocker.source.source_type is ContextSourceType.AGENT_HANDOFF
    assert error.value.blocker.source.source_id == runtime_context.handoff.handoff_id


def test_handoff_context_adapter_is_bound_to_exact_consuming_run_and_agent_revision() -> None:
    runtime_context, consumer = _runtime_context()
    adapter = ConsumedHandoffContextAdapter((runtime_context,))
    handoff = runtime_context.handoff

    wrong_run = ContextSourceRequest(
        task_id=handoff.task_id,
        run_id=new_id("run"),
        agent_id=consumer.agent_id,
        agent_revision=consumer.revision,
        project_id=None,
        workspace_id=None,
        plan_id=handoff.content.plan_id,
        step_id=handoff.content.consumer_step_id,
    )
    wrong_agent = ContextSourceRequest(
        task_id=handoff.task_id,
        run_id=runtime_context.consumption.consuming_run_id,
        agent_id=new_id("agent"),
        agent_revision=consumer.revision,
        project_id=None,
        workspace_id=None,
        plan_id=handoff.content.plan_id,
        step_id=handoff.content.consumer_step_id,
    )

    assert asyncio.run(adapter.collect(wrong_run)) == ()
    assert asyncio.run(adapter.collect(wrong_agent)) == ()
