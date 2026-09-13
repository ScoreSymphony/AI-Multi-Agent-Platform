"""Security-scope and observability acceptance coverage for #651.

Committed for the later consolidated integration branch; this issue branch does not execute it.
"""

from __future__ import annotations

import pytest

from ai_multi_agent_platform.agents import AgentRevisionRef
from ai_multi_agent_platform.context import InMemoryContextBundleRepository
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.handoffs import (
    CanonicalHandoffReferenceGateway,
    HandoffAuditEvent,
    HandoffSourceKind,
    HandoffSourceRef,
    TelemetryHandoffAuditSink,
)
from ai_multi_agent_platform.observability import InMemoryExporter, Telemetry, TelemetryOutcome
from ai_multi_agent_platform.research import (
    InMemoryResearchRepository,
    ResearchClass,
    ResearchService,
)
from ai_multi_agent_platform.security import ActorIdentity, ActorType
from ai_multi_agent_platform.skills import InMemorySkillRepository
from ai_multi_agent_platform.testing import FakeAuthorizationProvider


class _UnusedVerification:
    async def resolve_subject(self, *, task_id: str, subject_type: str, subject_id: str):
        del task_id, subject_type, subject_id
        raise AssertionError("verification is not used by this Research-reference test")

    async def resolve_context(self, *, task_id: str, subject_type: str, subject_id: str):
        del task_id, subject_type, subject_id
        raise AssertionError("verification context is not used by Handoffs")

    async def validate_evidence_artifacts(self, *, task_id: str, artifact_ids: tuple[str, ...]):
        del task_id
        return artifact_ids


@pytest.mark.asyncio
async def test_research_source_project_scope_cannot_be_replaced_by_caller_scope() -> None:
    task_id = new_id("task")
    source_project_id = new_id("project")
    other_project_id = new_id("project")
    agent = AgentRevisionRef(new_id("agent"), 1)
    actor = ActorIdentity(f"agent:{agent.agent_id}@{agent.revision}", ActorType.AGENT)
    research_repository = InMemoryResearchRepository()
    research = ResearchService(research_repository)
    item = await research.create_item(
        title="Scoped handoff research",
        question="Does source scope remain canonical?",
        research_class=ResearchClass.TASK_RESEARCH,
        owner_ref=OwnerRef(type="service", id="issue-651"),
        task_id=task_id,
        project_id=source_project_id,
    )
    claim = await research.add_claim(
        item.research_item_id,
        text="Canonical source scope must survive Handoff dereferencing.",
        category="security",
    )
    reference = HandoffSourceRef(
        HandoffSourceKind.RESEARCH_CLAIM,
        claim.claim_id,
        revision=str(claim.revision),
        digest=claim.digest.removeprefix("sha256:"),
    )
    gateway = CanonicalHandoffReferenceGateway(
        authorization=FakeAuthorizationProvider(),
        verification=_UnusedVerification(),  # type: ignore[arg-type]
        research=research_repository,
        skills=InMemorySkillRepository(),
        contexts=InMemoryContextBundleRepository(),
    )

    token = gateway.begin()
    try:
        with pytest.raises(ContractError) as blocked:
            await gateway.prepare_read(
                agent,
                reference,
                task_id=task_id,
                run_id=None,
                actor=actor,
                operation=OperationContext(
                    correlation_id="issue-651-cross-project",
                    project_id=other_project_id,
                ),
            )
        assert blocked.value.code is ErrorCode.NOT_FOUND
        assert not gateway.exists(reference)
        assert not gateway.can_read(agent, reference)
    finally:
        gateway.reset(token)


def test_production_handoff_audit_sink_uses_canonical_timeline() -> None:
    exporter = InMemoryExporter()
    sink = TelemetryHandoffAuditSink(Telemetry(exporter))
    task_id = new_id("task")
    run_id = new_id("run")
    handoff_id = new_id("handoff")

    sink.record(
        HandoffAuditEvent(
            event_type="handoff.consumed",
            handoff_id=handoff_id,
            revision=2,
            task_id=task_id,
            consuming_run_id=run_id,
            details={"consumer": "agent:example@1"},
        )
    )
    sink.record(
        HandoffAuditEvent(
            event_type="handoff.reference_denied",
            handoff_id=handoff_id,
            revision=2,
            task_id=task_id,
            consuming_run_id=run_id,
            details={"error_code": ErrorCode.FORBIDDEN.value},
        )
    )

    entries = exporter.query_timeline(task_id=task_id, run_id=run_id)
    assert [entry.event_name for entry in entries] == [
        "handoff.consumed",
        "handoff.reference_denied",
    ]
    assert entries[0].outcome is TelemetryOutcome.SUCCEEDED
    assert entries[1].outcome is TelemetryOutcome.FAILED
    assert entries[0].attributes["handoff_id"] == handoff_id
    assert entries[0].attributes["handoff_revision"] == 2
