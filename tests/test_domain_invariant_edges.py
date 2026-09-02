from collections import deque

import pytest

from ai_multi_agent_platform.domain import Event, ModelAssignment, OwnerRef, PolicyScope, Task

OWNER = OwnerRef(type="user", id="user-1")


@pytest.mark.parametrize(
    "mutable_value",
    [bytearray(b"mutable"), deque(["mutable"])],
)
def test_domain_metadata_rejects_unsupported_mutable_leaves(mutable_value: object) -> None:
    with pytest.raises(TypeError):
        Task(
            title="Reject mutable metadata leaf",
            owner_ref=OWNER,
            metadata={"value": mutable_value},
        )


class MutableBox:
    def __init__(self) -> None:
        self.value = "mutable"


def test_event_payload_rejects_arbitrary_mutable_custom_objects() -> None:
    with pytest.raises(TypeError):
        Event(
            event_type="task.updated",
            subject_type="task",
            subject_id="task_123e4567-e89b-12d3-a456-426614174000",
            correlation_id="corr-custom-mutable",
            payload={"box": MutableBox()},
        )


def test_policy_alias_is_confined_to_model_assignments() -> None:
    policy_scope = PolicyScope(name="sensitive", owner_ref=OWNER)

    assignment = ModelAssignment(
        subject_type="policy",
        subject_id=policy_scope.id,
        owner_ref=OWNER,
        requirements={"local_only": True},
    )
    assert assignment.subject_id == policy_scope.id

    with pytest.raises(ValueError):
        Event(
            event_type="policy.updated",
            subject_type="policy",
            subject_id=policy_scope.id,
            correlation_id="corr-policy-alias",
        )

    canonical_event = Event(
        event_type="policy_scope.updated",
        subject_type="policy_scope",
        subject_id=policy_scope.id,
        correlation_id="corr-policy-scope",
    )
    assert canonical_event.subject_id == policy_scope.id
