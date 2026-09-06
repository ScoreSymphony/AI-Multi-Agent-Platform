from __future__ import annotations

from dataclasses import replace

import pytest

from ai_multi_agent_platform.capability_assignments import (
    CapabilityAssignmentContent,
    CapabilityAssignmentPolicy,
    CapabilityAssignmentRevision,
    CapabilityAssignmentRule,
    CapabilityAssignmentTarget,
    CapabilityAssignmentTargetType,
    InMemoryCapabilityAssignmentRepository,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef, new_id

OWNER = OwnerRef(type="user", id="issue-366-owner")


def test_required_allowed_denied_conflicts_are_rejected_deterministically() -> None:
    target = CapabilityAssignmentTarget(
        subject_type=CapabilityAssignmentTargetType.AGENT,
        subject_id=new_id("agent"),
    )
    with pytest.raises(ValueError, match="must be disjoint"):
        CapabilityAssignmentContent(
            target=target,
            required=(CapabilityAssignmentRule("tool.echo"),),
            denied=(CapabilityAssignmentRule("tool.echo"),),
        )


def test_assignment_target_cannot_change_across_revisions() -> None:
    repository = InMemoryCapabilityAssignmentRepository()
    assignment_id = new_id("cap_assignment")
    first_target = CapabilityAssignmentTarget(
        subject_type=CapabilityAssignmentTargetType.AGENT,
        subject_id=new_id("agent"),
    )
    second_target = CapabilityAssignmentTarget(
        subject_type=CapabilityAssignmentTargetType.AGENT,
        subject_id=new_id("agent"),
    )
    first = CapabilityAssignmentRevision(
        assignment_id=assignment_id,
        revision=1,
        owner_ref=OWNER,
        content=CapabilityAssignmentContent(target=first_target),
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
        content=CapabilityAssignmentContent(target=second_target),
    )

    with pytest.raises(ContractError) as exc_info:
        repository.append_revision(
            replace(policy, current_revision=2, updated_at=second.created_at),
            second,
        )
    assert exc_info.value.code is ErrorCode.CONTRACT_VIOLATION
