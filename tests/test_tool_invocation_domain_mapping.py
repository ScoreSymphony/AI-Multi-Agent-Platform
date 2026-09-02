import pytest

from ai_multi_agent_platform.contracts import (
    map_tool_invocation_to_domain,
    tool_arguments_digest,
    validate_tool_invocation_binding,
)
from ai_multi_agent_platform.contracts.types import OperationContext, ToolInvocation
from ai_multi_agent_platform.domain import Approval, OwnerRef, Project, Tool, validate_id

OWNER = OwnerRef(type="user", id="user-1")
PROVIDER = "fake_tool_provider"


def context(
    *,
    owner_id: str = OWNER.id,
    project_id: str | None = None,
    correlation_id: str = "corr-invoke-1",
    causation_id: str | None = "event-previous",
) -> OperationContext:
    return OperationContext(
        correlation_id=correlation_id,
        causation_id=causation_id,
        owner_type=OWNER.type,
        owner_id=owner_id,
        project_id=project_id,
    )


def test_provider_tool_invocation_maps_to_canonical_approval_subject() -> None:
    tool = Tool(name="filesystem-write", owner_ref=OWNER)
    provider_invocation = ToolInvocation(
        invocation_id="invoke-1",
        tool_ref="provider-tool-write",
        arguments={"path": "notes.txt"},
        context=context(),
    )

    canonical_invocation = map_tool_invocation_to_domain(
        provider_invocation,
        canonical_tool_id=tool.id,
        owner_ref=OWNER,
        provider_namespace=PROVIDER,
    )
    approval = Approval(
        subject_type="tool_invocation",
        subject_id=canonical_invocation.id,
        owner_ref=OWNER,
        reason="Approve this exact provider call",
    )

    validate_id(canonical_invocation.id, "tool_invocation")
    validate_tool_invocation_binding(
        provider_invocation,
        canonical_invocation,
        provider_namespace=PROVIDER,
    )
    assert canonical_invocation.tool_id == tool.id
    assert canonical_invocation.correlation_id == provider_invocation.context.correlation_id
    assert canonical_invocation.causation_id == provider_invocation.context.causation_id
    assert canonical_invocation.external_refs[0].value == "invoke-1"
    assert canonical_invocation.external_refs[1].value == "provider-tool-write"
    assert canonical_invocation.arguments_digest == tool_arguments_digest({"path": "notes.txt"})
    assert approval.subject_id == canonical_invocation.id
    assert approval.subject_id != provider_invocation.invocation_id


def test_contract_tool_arguments_are_deeply_immutable() -> None:
    source = {
        "path": "approved.txt",
        "options": {"overwrite": False},
        "items": ["a"],
    }
    invocation = ToolInvocation(
        invocation_id="invoke-frozen",
        tool_ref="provider-tool-write",
        arguments=source,
        context=context(),
    )

    source["path"] = "mutated-source.txt"
    nested_source = source["options"]
    assert isinstance(nested_source, dict)
    nested_source["overwrite"] = True
    source_items = source["items"]
    assert isinstance(source_items, list)
    source_items.append("b")

    assert invocation.arguments["path"] == "approved.txt"
    assert invocation.arguments["options"] == {"overwrite": False}
    assert invocation.arguments["items"] == ("a",)

    with pytest.raises(TypeError, match="immutable"):
        invocation.arguments["path"] = "mutated-directly.txt"
    frozen_options = invocation.arguments["options"]
    assert isinstance(frozen_options, dict)
    with pytest.raises(TypeError, match="immutable"):
        frozen_options["overwrite"] = True


def test_governed_invocation_rejects_reconstructed_different_arguments() -> None:
    tool = Tool(name="filesystem-write", owner_ref=OWNER)
    approved = ToolInvocation(
        invocation_id="invoke-arguments",
        tool_ref="provider-tool-write",
        arguments={"path": "approved.txt"},
        context=context(),
    )
    canonical_invocation = map_tool_invocation_to_domain(
        approved,
        canonical_tool_id=tool.id,
        owner_ref=OWNER,
        provider_namespace=PROVIDER,
    )
    changed = ToolInvocation(
        invocation_id=approved.invocation_id,
        tool_ref=approved.tool_ref,
        arguments={"path": "different.txt"},
        context=approved.context,
    )

    with pytest.raises(ValueError, match="arguments changed"):
        validate_tool_invocation_binding(
            changed,
            canonical_invocation,
            provider_namespace=PROVIDER,
        )


def test_governed_invocation_rejects_replaced_provider_handle() -> None:
    tool = Tool(name="filesystem-write", owner_ref=OWNER)
    approved = ToolInvocation(
        invocation_id="invoke-original",
        tool_ref="provider-tool-write",
        arguments={"path": "approved.txt"},
        context=context(),
    )
    canonical_invocation = map_tool_invocation_to_domain(
        approved,
        canonical_tool_id=tool.id,
        owner_ref=OWNER,
        provider_namespace=PROVIDER,
    )
    replaced = ToolInvocation(
        invocation_id="invoke-replaced",
        tool_ref=approved.tool_ref,
        arguments=approved.arguments,
        context=approved.context,
    )

    with pytest.raises(ValueError, match="invocation id/provider"):
        validate_tool_invocation_binding(
            replaced,
            canonical_invocation,
            provider_namespace=PROVIDER,
        )


def test_governed_invocation_is_bound_to_provider_namespace() -> None:
    tool = Tool(name="filesystem-write", owner_ref=OWNER)
    approved = ToolInvocation(
        invocation_id="invoke-shared",
        tool_ref="tool.write",
        arguments={"path": "approved.txt"},
        context=context(),
    )
    canonical_invocation = map_tool_invocation_to_domain(
        approved,
        canonical_tool_id=tool.id,
        owner_ref=OWNER,
        provider_namespace="provider-a",
    )

    with pytest.raises(ValueError, match="provider"):
        validate_tool_invocation_binding(
            approved,
            canonical_invocation,
            provider_namespace="provider-b",
        )


def test_governed_invocation_is_bound_to_owner_context() -> None:
    tool = Tool(name="filesystem-write", owner_ref=OWNER)
    approved = ToolInvocation(
        invocation_id="invoke-owner",
        tool_ref="tool.write",
        arguments={"path": "approved.txt"},
        context=context(),
    )
    canonical_invocation = map_tool_invocation_to_domain(
        approved,
        canonical_tool_id=tool.id,
        owner_ref=OWNER,
        provider_namespace=PROVIDER,
    )
    other_owner = ToolInvocation(
        invocation_id=approved.invocation_id,
        tool_ref=approved.tool_ref,
        arguments=approved.arguments,
        context=context(owner_id="user-2"),
    )

    with pytest.raises(ValueError, match="ownership context"):
        validate_tool_invocation_binding(
            other_owner,
            canonical_invocation,
            provider_namespace=PROVIDER,
        )


def test_governed_invocation_is_bound_to_project_context() -> None:
    project = Project(name="Project A", owner_ref=OWNER)
    tool = Tool(name="filesystem-write", owner_ref=OWNER, project_id=project.id)
    approved = ToolInvocation(
        invocation_id="invoke-project",
        tool_ref="tool.write",
        arguments={"path": "approved.txt"},
        context=context(project_id=project.id),
    )
    canonical_invocation = map_tool_invocation_to_domain(
        approved,
        canonical_tool_id=tool.id,
        owner_ref=OWNER,
        canonical_project_id=project.id,
        provider_namespace=PROVIDER,
    )
    wrong_project = ToolInvocation(
        invocation_id=approved.invocation_id,
        tool_ref=approved.tool_ref,
        arguments=approved.arguments,
        context=context(project_id=None),
    )

    with pytest.raises(ValueError, match="project context"):
        validate_tool_invocation_binding(
            wrong_project,
            canonical_invocation,
            provider_namespace=PROVIDER,
        )


def test_mapping_rejects_context_that_does_not_match_canonical_owner() -> None:
    tool = Tool(name="filesystem-write", owner_ref=OWNER)
    mismatched = ToolInvocation(
        invocation_id="invoke-map-owner",
        tool_ref="tool.write",
        arguments={"path": "approved.txt"},
        context=context(owner_id="user-2"),
    )

    with pytest.raises(ValueError, match="ownership context"):
        map_tool_invocation_to_domain(
            mismatched,
            canonical_tool_id=tool.id,
            owner_ref=OWNER,
            provider_namespace=PROVIDER,
        )
