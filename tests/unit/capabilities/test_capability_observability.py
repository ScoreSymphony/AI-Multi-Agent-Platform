from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilityInvoker,
    CapabilityRegistration,
    CapabilityRegistry,
    CapabilitySpec,
    EventRepositoryInvocationObserver,
    InvocationTrace,
    NativeEchoProvider,
    PolicyDecision,
)
from ai_multi_agent_platform.contracts.domain_mapping import map_tool_invocation_to_domain
from ai_multi_agent_platform.contracts.types import OperationContext, ToolInvocation, ToolResult
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.domain import ToolInvocation as DomainToolInvocation
from ai_multi_agent_platform.kernel.sqlite_repository import SqliteKernelRepository


def _request(invocation_id: str = "audit-1") -> CapabilityInvocation:
    project_id = new_id("project")
    return CapabilityInvocation(
        invocation_id=invocation_id,
        capability_id="tool.echo",
        arguments={"message": "audit"},
        context=OperationContext(
            correlation_id="request-correlation",
            owner_type="user",
            owner_id="user-1",
            project_id=project_id,
        ),
        trace=InvocationTrace(
            correlation_id="request-correlation",
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=new_id("agent"),
            project_id=project_id,
        ),
    )


async def _governance_binding(
    request: CapabilityInvocation,
    registration: CapabilityRegistration,
    provider_invocation: ToolInvocation,
) -> DomainToolInvocation:
    return map_tool_invocation_to_domain(
        provider_invocation,
        canonical_tool_id=new_id("tool"),
        owner_ref=OwnerRef(type="user", id="user-1"),
        canonical_project_id=request.trace.project_id,
    )


class EvidenceEchoProvider(NativeEchoProvider):
    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        result = await super().invoke(invocation)
        return ToolResult(
            invocation_id=result.invocation_id,
            output=result.output,
            result_ref="result://echo/audit",
            artifact_refs=("artifact://echo/output",),
            evidence_refs=("evidence://echo/trace",),
        )


def test_invocation_observer_persists_trace_without_raw_payloads(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "capability-events.db"
        repository = SqliteKernelRepository(database)
        observer = EventRepositoryInvocationObserver(repository)
        registry = CapabilityRegistry()
        await registry.register_provider(NativeEchoProvider())
        request = _request()

        await CapabilityInvoker(registry, observer=observer).invoke(request)

        restarted = SqliteKernelRepository(database)
        events = await restarted.read_events(observer.stream_id(request.invocation_id))
        assert [event.event_type for event in events] == [
            "capability.invocation.running",
            "capability.invocation.succeeded",
        ]
        final = events[-1].payload
        assert final["task_id"] == request.trace.task_id
        assert final["run_id"] == request.trace.run_id
        assert final["agent_id"] == request.trace.agent_id
        assert final["request_correlation_id"] == "request-correlation"
        assert "arguments" not in final
        assert "output" not in final

    asyncio.run(scenario())


def test_approved_invocation_persists_approval_decision(tmp_path: Path) -> None:
    async def policy(
        request: CapabilityInvocation,
        capability: CapabilitySpec,
    ) -> PolicyDecision:
        return PolicyDecision.REQUIRE_APPROVAL

    async def approve(
        request: CapabilityInvocation,
        capability: CapabilitySpec,
        canonical_invocation: DomainToolInvocation,
    ) -> bool:
        return True

    async def scenario() -> None:
        database = tmp_path / "approved-events.db"
        repository = SqliteKernelRepository(database)
        observer = EventRepositoryInvocationObserver(repository)
        registry = CapabilityRegistry()
        await registry.register_provider(NativeEchoProvider())
        request = _request("audit-approved")

        result = await CapabilityInvoker(
            registry,
            policy_hook=policy,
            governance_binding_hook=_governance_binding,
            approval_hook=approve,
            observer=observer,
        ).invoke(request)

        events = await repository.read_events(observer.stream_id(request.invocation_id))
        assert result.canonical_tool_invocation_id is not None
        assert events[-1].payload["approval_decision"] == "approved"
        assert (
            events[-1].payload["canonical_tool_invocation_id"]
            == result.canonical_tool_invocation_id
        )

    asyncio.run(scenario())


def test_result_artifact_and_evidence_references_survive_invocation() -> None:
    async def scenario() -> None:
        registry = CapabilityRegistry()
        await registry.register_provider(EvidenceEchoProvider())

        result = await CapabilityInvoker(registry).invoke(_request("audit-evidence"))

        assert result.result_ref == "result://echo/audit"
        assert result.artifact_refs == ("artifact://echo/output",)
        assert result.evidence_refs == ("evidence://echo/trace",)

    asyncio.run(scenario())
