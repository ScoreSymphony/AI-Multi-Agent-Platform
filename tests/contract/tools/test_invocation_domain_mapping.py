from ai_multi_agent_platform.contracts.domain_mapping import map_tool_invocation_to_domain
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
    assert canonical_invocation.tool_id == tool.id
    assert canonical_invocation.correlation_id == provider_invocation.context.correlation_id
    assert canonical_invocation.causation_id == provider_invocation.context.causation_id
    assert canonical_invocation.external_refs[0].value == "invoke-1"
    assert canonical_invocation.external_refs[1].value == "provider-tool-write"
    assert approval.subject_id == canonical_invocation.id
    assert approval.subject_id != provider_invocation.invocation_id
