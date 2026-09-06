from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ai_multi_agent_platform.capabilities import CapabilityCompatibilityRequest
from ai_multi_agent_platform.capability_assignments import (
    CapabilityAssignmentContent,
    CapabilityAssignmentPolicy,
    CapabilityAssignmentProvenance,
    CapabilityAssignmentRevision,
    CapabilityAssignmentRule,
    CapabilityAssignmentTarget,
    CapabilityAssignmentTargetType,
    JsonCapabilityAssignmentRepository,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id

OWNER = OwnerRef(type="user", id="issue-366-owner")


def test_json_repository_survives_restart_with_exact_revision_history(tmp_path: Path) -> None:
    path = tmp_path / "capability-assignments.json"
    repository = JsonCapabilityAssignmentRepository(path)
    assignment_id = new_id("cap_assignment")
    target = CapabilityAssignmentTarget(
        subject_type=CapabilityAssignmentTargetType.AGENT,
        subject_id=new_id("agent"),
    )
    first = CapabilityAssignmentRevision(
        assignment_id=assignment_id,
        revision=1,
        owner_ref=OWNER,
        content=CapabilityAssignmentContent(
            target=target,
            required=(CapabilityAssignmentRule("tool.echo", exact_version="1.0"),),
            provenance=CapabilityAssignmentProvenance("test", "user:creator"),
        ),
    )
    policy = CapabilityAssignmentPolicy(
        assignment_id=assignment_id,
        owner_ref=OWNER,
        current_revision=1,
        created_at=first.created_at,
        updated_at=first.created_at,
    )
    repository.create(policy, first)

    second = CapabilityAssignmentRevision(
        assignment_id=assignment_id,
        revision=2,
        owner_ref=OWNER,
        content=CapabilityAssignmentContent(
            target=target,
            allowed=(
                CapabilityAssignmentRule(
                    "tool.echo",
                    compatibility=CapabilityCompatibilityRequest(
                        minimum_version="1.0",
                        maximum_version="2.0",
                        required_features=("text",),
                    ),
                ),
            ),
            provenance=CapabilityAssignmentProvenance("test-revision", "user:creator"),
        ),
    )
    updated = replace(policy, current_revision=2, updated_at=second.created_at)
    repository.append_revision(updated, second)

    restored = JsonCapabilityAssignmentRepository(path)

    assert restored.get(assignment_id) == updated
    assert restored.get_revision(assignment_id, 1) == first
    assert restored.get_revision(assignment_id, 2) == second
    assert restored.list_revisions(assignment_id) == (first, second)


def test_persisted_shape_excludes_provider_runtime_and_secret_state(tmp_path: Path) -> None:
    path = tmp_path / "capability-assignments.json"
    repository = JsonCapabilityAssignmentRepository(path)
    assignment_id = new_id("cap_assignment")
    revision = CapabilityAssignmentRevision(
        assignment_id=assignment_id,
        revision=1,
        owner_ref=OWNER,
        content=CapabilityAssignmentContent(
            target=CapabilityAssignmentTarget(
                subject_type=CapabilityAssignmentTargetType.PROJECT,
                subject_id=new_id("project"),
            ),
            denied=(CapabilityAssignmentRule("tool.blocked"),),
            provenance=CapabilityAssignmentProvenance("portable-policy", "user:creator"),
        ),
    )
    repository.create(
        CapabilityAssignmentPolicy(
            assignment_id=assignment_id,
            owner_ref=OWNER,
            current_revision=1,
            created_at=revision.created_at,
            updated_at=revision.created_at,
        ),
        revision,
    )

    serialized = path.read_text(encoding="utf-8").lower()
    for forbidden in (
        "provider_id",
        "provider_tool_ref",
        "runtime_session",
        "plaintext_secret",
        "api_key",
        "credential_value",
    ):
        assert forbidden not in serialized
