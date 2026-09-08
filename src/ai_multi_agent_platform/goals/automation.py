"""Issue #597 integration between #18 trigger deliveries and durable Goal reviews.

The Goal layer deliberately does not own a scheduler. This module consumes the canonical
Automation/TriggerDelivery primitives after #18 has admitted and deduplicated a delivery,
then asks GoalService to review the exact Goal revision named by the Automation template.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ai_multi_agent_platform.automation import Automation, TriggerDelivery, TriggerType
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .models import GoalEvidence, GoalState
from .service import GoalService

GOAL_OBSERVATION_PAYLOAD_KEY = "goal_observation"

GoalEvidenceResolver = Callable[
    [GoalState, Automation, TriggerDelivery],
    Awaitable[tuple[GoalEvidence, ...]],
]


@dataclass(frozen=True, slots=True)
class GoalAutomationDispatchResult:
    """Result of attempting to route one #18 delivery through the Goal subsystem."""

    handled: bool
    generated_task_id: str | None = None


async def dispatch_goal_automation_delivery(
    goals: GoalService,
    automation: Automation,
    delivery: TriggerDelivery,
    rendered_payload: dict[str, JsonValue],
    idempotency_key: str,
    *,
    evidence_resolver: GoalEvidenceResolver | None = None,
) -> GoalAutomationDispatchResult:
    """Route an explicitly marked Automation delivery into one idempotent Goal review.

    Ordinary Automations are not intercepted. Goal-observation Automations opt in by putting
    ``{"goal_observation": {"goal_id": "goal_...", "goal_revision": N}}`` in their canonical
    TaskTemplate payload. The static Goal revision is intentional: a delivery created for an old
    objective cannot silently mutate a newer revision.

    Delivery payloads are never trusted as verified Goal evidence. Deployments that want an event
    or webhook to contribute evidence must provide an evidence resolver backed by the canonical
    verification boundary (for example #86). Without one, the delivery is still a valid review
    trigger but contributes no new evidence.
    """

    raw_binding = rendered_payload.get(GOAL_OBSERVATION_PAYLOAD_KEY)
    if raw_binding is None:
        return GoalAutomationDispatchResult(handled=False)
    if not isinstance(raw_binding, dict):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "goal_observation must be an object",
        )

    goal_id = raw_binding.get("goal_id")
    expected_revision = raw_binding.get("goal_revision")
    if not isinstance(goal_id, str) or not goal_id.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "goal_observation.goal_id must be a non-blank string",
        )
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 1
    ):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "goal_observation.goal_revision must be a positive integer",
        )

    current = await goals.get_goal(goal_id)
    configured_automation_id = current.observation_policy.automation_id
    if configured_automation_id != automation.id:
        raise ContractError(
            ErrorCode.CONFLICT,
            "Goal observation Automation does not match the Goal revision policy",
        )

    if current.observation_policy.event_types:
        if automation.trigger.type is not TriggerType.PLATFORM_EVENT:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Goal observation policy requires a platform-event Automation",
            )
        event_type = automation.trigger.event_type
        if event_type not in current.observation_policy.event_types:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Automation event type is not allowed by the Goal observation policy",
            )

    evidence = (
        ()
        if evidence_resolver is None
        else await evidence_resolver(current, automation, delivery)
    )
    reviewed = await goals.review_goal(
        goal_id=goal_id,
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
        trigger_ref=f"automation-delivery:{delivery.id}",
        evidence=evidence,
        actor_ref=automation.identity.principal_ref,
    )
    generated_task_id = None
    if reviewed.reviews:
        generated = reviewed.reviews[-1].generated_task_ids
        if generated:
            generated_task_id = generated[0]
    return GoalAutomationDispatchResult(
        handled=True,
        generated_task_id=generated_task_id,
    )


__all__ = [
    "GOAL_OBSERVATION_PAYLOAD_KEY",
    "GoalAutomationDispatchResult",
    "GoalEvidenceResolver",
    "dispatch_goal_automation_delivery",
]
