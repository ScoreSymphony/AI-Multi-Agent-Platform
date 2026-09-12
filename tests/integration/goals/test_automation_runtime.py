from __future__ import annotations

import asyncio
from typing import Any

from ai_multi_agent_platform.automation import (
    DeliveryStatus,
    IdentityContext,
    TaskTemplate,
    TriggerDefinition,
    TriggerType,
)
from ai_multi_agent_platform.control_plane.approval_portability_composition import ControlPlane
from ai_multi_agent_platform.control_plane.goal_contract import GOAL_COMMANDS
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.goals import (
    GOAL_OBSERVATION_PAYLOAD_KEY,
    GoalCriterionKind,
    GoalStatus,
    ObservationPolicy,
    SuccessCriterion,
    TaskGenerationPolicy,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)

_OWNER_ID = "goal-runtime-owner"
_OWNER = OwnerRef(type="user", id=_OWNER_ID)
_METRIC = SuccessCriterion(
    criterion_id="quality",
    kind=GoalCriterionKind.METRIC,
    description="Quality reaches the required threshold",
    operator="gte",
    target=90,
)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _control_plane() -> tuple[ControlPlane, PlatformKernel]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    return (
        ControlPlane(
            kernel=kernel,
            events=repository,
            authorization=FakeAuthorizationProvider(),
        ),
        kernel,
    )


async def _observed_goal(
    control_plane: ControlPlane,
    *,
    automatic_tasks: bool,
) -> tuple[str, str]:
    goal_id = new_id("goal")
    automation = await control_plane.automation_service.create_automation(
        name="Review durable Goal",
        description="Use canonical #18 delivery semantics to review one exact Goal revision",
        identity=IdentityContext(
            principal_ref=f"user:{_OWNER_ID}",
            owner_type="user",
            owner_id=_OWNER_ID,
        ),
        trigger=TriggerDefinition(type=TriggerType.MANUAL),
        task_template=TaskTemplate(
            title="Review durable Goal",
            objective="Re-evaluate explicit Goal success criteria",
            payload={
                GOAL_OBSERVATION_PAYLOAD_KEY: {
                    "goal_id": goal_id,
                    "goal_revision": 1,
                }
            },
        ),
    )
    created = await control_plane.goals.create_goal(
        idempotency_key=f"create:{goal_id}",
        goal_id=goal_id,
        title="Maintain quality",
        objective="Keep the explicit quality criterion satisfied",
        owner_ref=_OWNER,
        success_criteria=(_METRIC,),
        observation_policy=ObservationPolicy(automation_id=automation.id),
        task_generation_policy=TaskGenerationPolicy(enabled=automatic_tasks),
    )
    active = await control_plane.goals.activate_goal(
        goal_id=created.goal_id,
        idempotency_key=f"activate:{goal_id}",
    )
    assert active.status is GoalStatus.ACTIVE
    return active.goal_id, automation.id


def test_composed_control_plane_registers_and_executes_goal_contract() -> None:
    async def scenario() -> None:
        control_plane, _ = _control_plane()
        assert "goals" in control_plane.registered_collections
        assert set(GOAL_COMMANDS).issubset(control_plane.registered_commands)

        context = RequestContext(
            request_id="goal-create-request",
            correlation_id="goal-create-correlation",
            actor=ActorContext(
                principal_ref=f"user:{_OWNER_ID}",
                owner_type="user",
                owner_id=_OWNER_ID,
            ),
            idempotency_key="goal-control-plane-create",
        )
        resource = await control_plane.execute_command(
            context,
            "goal.create",
            "goals",
            {
                "title": "Control Plane Goal",
                "objective": "Prove the canonical Goal surface is actually composed",
                "success_criteria": [
                    {
                        "criterion_id": "quality",
                        "kind": "metric",
                        "description": "Quality reaches 90",
                        "operator": "gte",
                        "target": 90,
                    }
                ],
            },
        )
        goal_id = resource["id"]
        assert isinstance(goal_id, str)
        assert resource["type"] == "goal"
        assert (await control_plane.goals.get_goal(goal_id)).title == "Control Plane Goal"

    _run(scenario())


def test_automation_delivery_generates_goal_task_once_and_replay_is_deduplicated() -> None:
    async def scenario() -> None:
        control_plane, kernel = _control_plane()
        goal_id, automation_id = await _observed_goal(control_plane, automatic_tasks=True)

        first = await control_plane.automation_service.test_trigger(
            automation_id,
            occurrence_id="goal-review-1",
        )
        replay = await control_plane.automation_service.test_trigger(
            automation_id,
            occurrence_id="goal-review-1",
        )

        assert first.status is DeliveryStatus.SUCCEEDED
        assert replay.id == first.id
        assert first.generated_task_id is not None
        goal = await control_plane.goals.get_goal(goal_id)
        assert len(goal.reviews) == 1
        assert goal.active_task_ids == (first.generated_task_id,)
        assert goal.reviews[0].trigger_ref == f"automation-delivery:{first.id}"
        assert (await kernel.get_task(first.generated_task_id)).task_id == first.generated_task_id

    _run(scenario())


def test_automation_review_can_succeed_without_fabricating_a_task() -> None:
    async def scenario() -> None:
        control_plane, _ = _control_plane()
        goal_id, automation_id = await _observed_goal(control_plane, automatic_tasks=False)

        delivery = await control_plane.automation_service.test_trigger(
            automation_id,
            occurrence_id="monitor-only",
            payload={
                "untrusted_evidence": {
                    "criterion_id": "quality",
                    "value": 100,
                    "verified": True,
                }
            },
        )

        assert delivery.status is DeliveryStatus.SUCCEEDED
        assert delivery.generated_task_id is None
        goal = await control_plane.goals.get_goal(goal_id)
        assert goal.status is GoalStatus.WAITING
        assert len(goal.reviews) == 1
        assert goal.evidence == ()

    _run(scenario())


def test_stale_goal_revision_in_automation_delivery_cannot_mutate_new_revision() -> None:
    async def scenario() -> None:
        control_plane, _ = _control_plane()
        goal_id, automation_id = await _observed_goal(control_plane, automatic_tasks=False)
        revised = await control_plane.goals.revise_goal(
            goal_id=goal_id,
            idempotency_key="revise-before-old-delivery",
            expected_revision=1,
            objective="Keep the revised quality objective satisfied",
        )
        assert revised.revision == 2

        delivery = await control_plane.automation_service.test_trigger(
            automation_id,
            occurrence_id="stale-goal-revision",
        )

        assert delivery.status is DeliveryStatus.FAILED
        current = await control_plane.goals.get_goal(goal_id)
        assert current.revision == 2
        assert current.reviews == ()

    _run(scenario())
