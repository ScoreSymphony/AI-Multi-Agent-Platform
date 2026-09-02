import json
from enum import Enum

import pytest

from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.contracts import ToolInvocation as ContractToolInvocation
from ai_multi_agent_platform.contracts.domain_mapping import (
    map_tool_invocation_to_domain,
    tool_invocation_arguments_digest,
)
from ai_multi_agent_platform.domain import Approval, Event, OwnerRef, Tool, new_id

OWNER = OwnerRef(type="user", id="user-1")


class MutableValueEnum(Enum):
    VALUE = ["initial"]


def test_domain_freezer_copies_mutable_enum_value() -> None:
    mutable_value = MutableValueEnum.VALUE.value
    event = Event(
        event_type="enum.snapshot",
        subject_type="task",
        subject_id=new_id("task"),
        correlation_id="corr-enum",
        payload={"enum_value": MutableValueEnum.VALUE},
    )

    mutable_value.append("mutated")

    assert event.payload["enum_value"] == ("initial",)


def test_tool_invocation_arguments_are_frozen_and_digest_bound_to_canonical_identity() -> None:
    source_arguments = {
        "path": "notes.txt",
        "options": {"labels": ["approved"]},
    }
    invocation = ContractToolInvocation(
        invocation_id="provider-invoke-1",
        tool_ref="provider-tool-write",
        arguments=source_arguments,
        context=OperationContext(correlation_id="corr-tool-1"),
    )
    digest_before_mutation = tool_invocation_arguments_digest(invocation)

    source_arguments["path"] = "other.txt"
    source_arguments["options"]["labels"].append("tampered")

    assert invocation.arguments["path"] == "notes.txt"
    assert invocation.arguments["options"]["labels"] == ("approved",)
    assert tool_invocation_arguments_digest(invocation) == digest_before_mutation

    with pytest.raises(TypeError):
        invocation.arguments["path"] = "tampered.txt"  # type: ignore[index]
    with pytest.raises(TypeError):
        invocation.arguments["options"]["labels"] += ("tampered",)  # type: ignore[index,operator]

    tool = Tool(name="filesystem-write", owner_ref=OWNER)
    canonical_invocation = map_tool_invocation_to_domain(
        invocation,
        canonical_tool_id=tool.id,
        owner_ref=OWNER,
        provider_namespace="fake_tool_provider",
    )
    approval = Approval(
        subject_type="tool_invocation",
        subject_id=canonical_invocation.id,
        owner_ref=OWNER,
        reason="Approve exact immutable invocation payload",
    )

    assert canonical_invocation.provenance is not None
    assert canonical_invocation.provenance.details["arguments_sha256"] == digest_before_mutation
    assert approval.subject_id == canonical_invocation.id


def test_tool_invocation_arguments_json_is_nested_serializable_detached_copy() -> None:
    invocation = ContractToolInvocation(
        invocation_id="provider-invoke-json",
        tool_ref="provider-tool-write",
        arguments={
            "path": "notes.txt",
            "options": {"labels": ["approved"], "overwrite": False},
        },
        context=OperationContext(correlation_id="corr-tool-json"),
    )
    digest_before_export = tool_invocation_arguments_digest(invocation)

    exported = invocation.arguments_json()
    serialized = json.dumps(exported, sort_keys=True)

    assert json.loads(serialized) == {
        "options": {"labels": ["approved"], "overwrite": False},
        "path": "notes.txt",
    }

    exported["path"] = "changed-after-export.txt"
    options = exported["options"]
    assert isinstance(options, dict)
    labels = options["labels"]
    assert isinstance(labels, list)
    labels.append("changed-after-export")

    assert invocation.arguments["path"] == "notes.txt"
    assert invocation.arguments["options"]["labels"] == ("approved",)
    assert tool_invocation_arguments_digest(invocation) == digest_before_export


def test_tool_invocation_digest_changes_when_arguments_change() -> None:
    context = OperationContext(correlation_id="corr-tool-digest")
    first = ContractToolInvocation(
        invocation_id="invoke-1",
        tool_ref="tool-1",
        arguments={"path": "a.txt"},
        context=context,
    )
    second = ContractToolInvocation(
        invocation_id="invoke-2",
        tool_ref="tool-1",
        arguments={"path": "b.txt"},
        context=context,
    )

    assert tool_invocation_arguments_digest(first) != tool_invocation_arguments_digest(second)
