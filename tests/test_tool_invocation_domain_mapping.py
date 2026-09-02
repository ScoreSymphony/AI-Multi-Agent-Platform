import pytest

from ai_multi_agent_platform.contracts import (
    map_tool_invocation_to_domain,
    tool_arguments_digest,
    validate_tool_invocation_binding,
)
from ai_multi_agent_platform.contracts.types import OperationContext, ToolInvocation
from ai_multi_agent_platform.domain import Approval, OwnerRef, Tool, validate_id

OWNER = OwnerRef(type="user", id="user-1")


def test_provider_tool_invocation_maps_to_canonical_approval_subject() -> None:
    tool = Tool(name="filesystem-write", owner_ref=OWNER)
    provider_invocation = ToolInvocation(
        invocation_id="invoke-1",
        tool_ref="provider-tool-write",
        arguments={"path": "notes.txt"},
        context=OperationContext(
            correlation_id="corr-invoke-1",
            causation_id="event-previous",
        ),
    )

    canonical_invocation = map_tool_invocation_to_domain(
        provider_invocation,
        canonical_tool_id=tool.id,
        owner_ref=OWNER,
        provider_namespace="fake_tool_provider",
    )
    approval = Approval(
        subject_type="tool_invocation",
        subject_id=canonical_invocation.id,
        owner_ref=OWNER,
        reason="Approve this exact provider call",
    )

    validate_id(canonical_invocation.id, "tool_invocation")
    validate_tool_invocation_binding(provider_invocation, canonical_invocation)
    assert canonical_invocation.tool_id == tool.id
    assert canonical_invocation.correlation_id == provider_invocation.context.correlation_id
    assert canonical_invocation.causation_id == provider_invocation.context.causation_id
    assert canonical_invocation.external_refs[0].value == "invoke-1"
    assert canonical_invocation.external_refs[1].value == "provider-tool-write"
    assert canonical_invocation.external_refs[2].value == tool_arguments_digest(
        {"path": "notes.txt"}
    )
    assert approval.subject_id == canonical_invocation.id
    assert approval.subject_id != provider_invocation.invocation_id


def test_governed_invocation_rejects_arguments_mutated_after_approval() -> None:
    tool = Tool(name="filesystem-write", owner_ref=OWNER)
    arguments = {"path": "approved.txt", "options": {"overwrite": False}}
    provider_invocation = ToolInvocation(
        invocation_id="invoke-mutable",
        tool_ref="provider-tool-write",
        arguments=arguments,
        context=OperationContext(correlation_id="corr-binding"),
    )
    canonical_invocation = map_tool_invocation_to_domain(
        provider_invocation,
        canonical_tool_id=tool.id,
        owner_ref=OWNER,
    )

    validate_tool_invocation_binding(provider_invocation, canonical_invocation)
    arguments["path"] = "different.txt"

    with pytest.raises(ValueError, match="arguments changed"):
        validate_tool_invocation_binding(provider_invocation, canonical_invocation)


def test_governed_invocation_rejects_replaced_provider_handle() -> None:
    tool = Tool(name="filesystem-write", owner_ref=OWNER)
    provider_invocation = ToolInvocation(
        invocation_id="invoke-original",
        tool_ref="provider-tool-write",
        arguments={"path": "approved.txt"},
        context=OperationContext(correlation_id="corr-provider-handle"),
    )
    canonical_invocation = map_tool_invocation_to_domain(
        provider_invocation,
        canonical_tool_id=tool.id,
        owner_ref=OWNER,
    )
    replaced = ToolInvocation(
        invocation_id="invoke-replaced",
        tool_ref=provider_invocation.tool_ref,
        arguments=provider_invocation.arguments,
        context=provider_invocation.context,
    )

    with pytest.raises(ValueError, match="invocation id"):
        validate_tool_invocation_binding(replaced, canonical_invocation)
