from __future__ import annotations

import json
from dataclasses import replace

import pytest

from ai_multi_agent_platform.agents import AgentRevisionRef
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.workflows import (
    InMemoryWorkflowRepository,
    JsonWorkflowRepository,
    WorkflowCapabilityRequirement,
    WorkflowCompatibility,
    WorkflowContent,
    WorkflowParameter,
    WorkflowProvenance,
    WorkflowRevisionRef,
    WorkflowService,
    WorkflowStage,
)


def _owner() -> OwnerRef:
    return OwnerRef(type="user", id="alice")


def _content(name: str = "Research workflow") -> WorkflowContent:
    return WorkflowContent(
        name=name,
        description="Provider-neutral reusable workflow intent",
        parameters=(WorkflowParameter(name="topic"),),
        stages=(
            WorkflowStage(
                stage_id="collect",
                title="Collect sources",
                parameter_refs=("topic",),
                capabilities=(
                    WorkflowCapabilityRequirement(
                        capability_id="web.search",
                        version_constraint=">=1",
                    ),
                ),
                agent=AgentRevisionRef(agent_id=new_id("agent"), revision=1),
                permission_actions=("knowledge:read",),
            ),
            WorkflowStage(
                stage_id="synthesize",
                title="Synthesize findings",
                depends_on=("collect",),
                parameter_refs=("topic",),
                model_routing_policy_ref="model-routing-profile:balanced@1",
            ),
        ),
        provenance=WorkflowProvenance(creator="alice", source="local"),
        compatibility=WorkflowCompatibility(
            contract_versions={"task-run": "1"},
            provider_agnostic=True,
            orchestrator_agnostic=True,
        ),
    )


def test_create_version_exact_resolution_and_restart(tmp_path) -> None:
    path = tmp_path / "workflows.json"
    repository = JsonWorkflowRepository(path)
    service = WorkflowService(repository)

    first = service.create(owner_ref=_owner(), content=_content())
    second = service.revise(
        first.workflow_id,
        replace(_content("Research workflow v2"), description="Second immutable revision"),
        expected_revision=1,
    )

    assert first.revision == 1
    assert second.revision == 2
    assert service.resolve(WorkflowRevisionRef(first.workflow_id, 1)) == first
    assert service.resolve(WorkflowRevisionRef(first.workflow_id, 2)) == second

    restored = WorkflowService(JsonWorkflowRepository(path))
    assert restored.get(first.workflow_id).current_revision == 2
    assert restored.resolve(first.ref) == first
    assert restored.resolve(second.ref) == second
    assert tuple(item.revision for item in restored.list_revisions(first.workflow_id)) == (1, 2)


def test_dependency_and_placeholder_validation_is_fail_closed() -> None:
    with pytest.raises(ValueError, match="unknown stages"):
        WorkflowContent(
            name="Broken dependency",
            description="",
            stages=(WorkflowStage(stage_id="a", title="A", depends_on=("missing",)),),
        )

    with pytest.raises(ValueError, match="undeclared parameters"):
        WorkflowContent(
            name="Broken placeholder",
            description="",
            stages=(WorkflowStage(stage_id="a", title="A", parameter_refs=("missing",)),),
        )

    with pytest.raises(ValueError, match="acyclic"):
        WorkflowContent(
            name="Cycle",
            description="",
            stages=(
                WorkflowStage(stage_id="a", title="A", depends_on=("b",)),
                WorkflowStage(stage_id="b", title="B", depends_on=("a",)),
            ),
        )


def test_stage_tool_ids_require_canonical_tool_identity() -> None:
    canonical_tool_id = new_id("tool")
    stage = WorkflowStage(stage_id="a", title="A", tool_ids=(canonical_tool_id,))
    assert stage.tool_ids == (canonical_tool_id,)

    with pytest.raises(ValueError, match="expected canonical tool id"):
        WorkflowStage(stage_id="a", title="A", tool_ids=("provider-native-search",))


def test_exact_revision_admission_creates_task_bound_plan_without_mutating_workflow() -> None:
    service = WorkflowService(InMemoryWorkflowRepository())
    revision = service.create(owner_ref=_owner(), content=_content())
    before = service.resolve(revision.ref)

    admission = service.admit(
        revision.ref,
        task_id=new_id("task"),
        owner_ref=_owner(),
        parameters={"topic": "canonical workflows"},
    )

    assert admission.source == revision.ref
    assert admission.plan.task_id.startswith("task_")
    assert len(admission.steps) == 2
    collect, synthesize = admission.steps
    assert collect.plan_id == admission.plan.id
    assert synthesize.plan_id == admission.plan.id
    assert synthesize.depends_on == (collect.id,)
    assert admission.plan.provenance is not None
    assert admission.plan.provenance.source == f"workflow:{revision.workflow_id}@1"
    assert service.resolve(revision.ref) == before


def test_admission_requires_declared_required_parameters() -> None:
    service = WorkflowService(InMemoryWorkflowRepository())
    revision = service.create(owner_ref=_owner(), content=_content())

    with pytest.raises(ContractError) as exc_info:
        service.admit(revision.ref, task_id=new_id("task"), owner_ref=_owner())
    assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION

    with pytest.raises(ContractError) as exc_info:
        service.admit(
            revision.ref,
            task_id=new_id("task"),
            owner_ref=_owner(),
            parameters={"topic": "x", "undeclared": "y"},
        )
    assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION


def test_runtime_private_and_secret_bearing_metadata_is_rejected() -> None:
    service = WorkflowService(InMemoryWorkflowRepository())
    unsafe = replace(_content(), metadata={"orchestrator_session_id": "private-session"})

    with pytest.raises(ContractError) as exc_info:
        service.create(owner_ref=_owner(), content=unsafe)
    assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION


def test_canonical_serialization_contains_no_provider_or_runtime_private_state(tmp_path) -> None:
    path = tmp_path / "workflows.json"
    service = WorkflowService(JsonWorkflowRepository(path))
    revision = service.create(owner_ref=_owner(), content=_content())

    payload = json.loads(path.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, sort_keys=True)
    assert revision.workflow_id in serialized
    assert "provider_session_id" not in serialized
    assert "orchestrator_session_id" not in serialized
    assert "backend_session_id" not in serialized
    assert "provider_id" not in serialized
    assert "orchestrator_id" not in serialized
    assert payload["revisions"][0]["content"]["compatibility"]["provider_agnostic"] is True
    assert payload["revisions"][0]["content"]["compatibility"]["orchestrator_agnostic"] is True
