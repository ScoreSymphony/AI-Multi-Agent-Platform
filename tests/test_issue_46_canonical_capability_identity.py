from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilityInvoker,
    CapabilityRegistration,
    CapabilityRegistry,
    CapabilitySpec,
    InvocationRecord,
    InvocationStatus,
    InvocationTrace,
    NativeEchoProvider,
    bind_canonical_capability_invocation,
    canonical_tool_id,
    canonical_tool_invocation_id,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.contracts.types import ToolInvocation
from ai_multi_agent_platform.domain import new_id, validate_id


class _RecordingObserver:
    def __init__(self) -> None:
        self.records: list[InvocationRecord] = []

    async def record(self, record: InvocationRecord) -> None:
        self.records.append(record)


def _request(*, arguments: dict[str, str] | None = None) -> CapabilityInvocation:
    task_id = new_id("task")
    run_id = new_id("run")
    agent_id = new_id("agent")
    project_id = new_id("project")
    context = OperationContext(
        correlation_id=task_id,
        owner_type="user",
        owner_id="issue-46-owner",
        project_id=project_id,
        causation_id=f"{run_id}:model",
    )
    return CapabilityInvocation(
        invocation_id=f"{run_id}:capability:1",
        capability_id="tool.echo",
        version="1.0",
        arguments=arguments or {"message": "hello"},
        context=context,
        trace=InvocationTrace(
            correlation_id=task_id,
            task_id=task_id,
            run_id=run_id,
            agent_id=agent_id,
            project_id=project_id,
            causation_id=context.causation_id,
        ),
    )


def _registration(provider_id: str, provider_tool_ref: str) -> CapabilityRegistration:
    return CapabilityRegistration(
        capability=CapabilitySpec(
            capability_id="tool.echo",
            name="Echo",
            version="1.0",
        ),
        provider_id=provider_id,
        provider_tool_ref=provider_tool_ref,
    )


def _provider_invocation(
    request: CapabilityInvocation,
    *,
    invocation_id: str,
    tool_ref: str,
) -> ToolInvocation:
    return ToolInvocation(
        invocation_id=invocation_id,
        tool_ref=tool_ref,
        arguments=request.arguments,
        context=request.context,
    )


def test_canonical_tool_identity_is_stable_across_provider_handles() -> None:
    request = _request()
    first_registration = _registration("provider-a", "native.echo")
    second_registration = _registration("provider-b", "mcp.echo")
    first_provider_call = _provider_invocation(
        request,
        invocation_id="provider-call-a",
        tool_ref=first_registration.provider_tool_ref,
    )
    second_provider_call = _provider_invocation(
        request,
        invocation_id="provider-call-b",
        tool_ref=second_registration.provider_tool_ref,
    )

    first = asyncio.run(
        bind_canonical_capability_invocation(
            request,
            first_registration,
            first_provider_call,
        )
    )
    second = asyncio.run(
        bind_canonical_capability_invocation(
            request,
            second_registration,
            second_provider_call,
        )
    )

    assert first.tool_id == second.tool_id == canonical_tool_id("tool.echo", "1.0")
    assert canonical_tool_id("tool.echo", "2.0") != first.tool_id
    assert first.id == second.id
    validate_id(first.tool_id, "tool")
    validate_id(first.id, "tool_invocation")
    assert {ref.value for ref in first.external_refs} == {"provider-call-a", "native.echo"}
    assert {ref.value for ref in second.external_refs} == {"provider-call-b", "mcp.echo"}


def test_canonical_binding_preserves_owner_project_trace_and_external_refs() -> None:
    request = _request()
    registration = _registration("provider-a", "native.echo")
    provider_call = _provider_invocation(
        request,
        invocation_id="provider-call-a",
        tool_ref=registration.provider_tool_ref,
    )

    bound = asyncio.run(
        bind_canonical_capability_invocation(
            request,
            registration,
            provider_call,
        )
    )

    assert bound.owner_ref.type == "user"
    assert bound.owner_ref.id == "issue-46-owner"
    assert bound.project_id == request.context.project_id
    assert bound.correlation_id == request.context.correlation_id
    assert bound.causation_id == request.context.causation_id
    assert {(ref.system, ref.kind, ref.value) for ref in bound.external_refs} == {
        ("tool_provider", "invocation_id", "provider-call-a"),
        ("tool_provider", "tool_ref", "native.echo"),
    }


def test_changed_arguments_change_canonical_invocation_identity() -> None:
    request = _request()
    registration = _registration("provider-a", "native.echo")
    first_call = _provider_invocation(
        request,
        invocation_id="provider-call-a",
        tool_ref=registration.provider_tool_ref,
    )
    changed_call = ToolInvocation(
        invocation_id="provider-call-a",
        tool_ref=registration.provider_tool_ref,
        arguments={"message": "changed"},
        context=request.context,
    )

    first_id = canonical_tool_invocation_id(request, registration, first_call)
    changed_id = canonical_tool_invocation_id(request, registration, changed_call)

    assert first_id != changed_id
    validate_id(first_id, "tool_invocation")
    validate_id(changed_id, "tool_invocation")


def test_invocation_observer_retains_canonical_tool_invocation_identity() -> None:
    async def scenario() -> None:
        registry = CapabilityRegistry()
        await registry.register_provider(NativeEchoProvider())
        request = _request(arguments={"message": "canonical observer evidence"})
        observer = _RecordingObserver()

        result = await CapabilityInvoker(
            registry,
            canonical_binding_hook=bind_canonical_capability_invocation,
            observer=observer,
        ).invoke(request)

        canonical_id = result.canonical_tool_invocation_id
        assert canonical_id is not None
        assert canonical_id != request.invocation_id
        assert validate_id(canonical_id, "tool_invocation") == canonical_id
        assert result.output == {"message": "canonical observer evidence"}
        assert [record.status for record in observer.records] == [
            InvocationStatus.RUNNING,
            InvocationStatus.SUCCEEDED,
        ]
        assert {record.canonical_tool_invocation_id for record in observer.records} == {
            canonical_id
        }
        assert all(record.trace == request.trace for record in observer.records)

    asyncio.run(scenario())


def test_canonical_binding_fails_closed_without_owner_context() -> None:
    request = _request()
    context = replace(
        request.context,
        owner_type=None,
        owner_id=None,
        project_id=None,
    )
    request = replace(
        request,
        context=context,
        trace=replace(request.trace, project_id=None),
    )
    registration = _registration("provider-a", "native.echo")
    provider_call = _provider_invocation(
        request,
        invocation_id="provider-call-a",
        tool_ref=registration.provider_tool_ref,
    )

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            bind_canonical_capability_invocation(
                request,
                registration,
                provider_call,
            )
        )

    assert exc_info.value.code is ErrorCode.CONTRACT_VIOLATION
