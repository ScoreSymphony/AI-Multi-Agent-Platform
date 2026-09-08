"""Safe read-only operator projection for compensation state."""

from __future__ import annotations

from dataclasses import dataclass

from .models import CompensationStatus
from .repository import CompensationRepository


@dataclass(frozen=True, slots=True)
class CompensationActionView:
    action_id: str
    group_id: str
    task_id: str
    plan_id: str
    plan_revision: int
    step_id: str
    original_run_id: str
    original_tool_invocation_id: str
    original_capability_id: str
    reversibility: str
    compensating_capability_id: str | None
    compensation_id: str | None
    status: CompensationStatus | None
    compensation_run_id: str | None
    compensation_tool_invocation_id: str | None
    approval_id: str | None
    original_result_ref: str | None
    external_resource_ref: str | None
    result_ref: str | None
    artifact_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    error_code: str | None
    error_message: str | None
    manual_intervention_required: bool


@dataclass(frozen=True, slots=True)
class CompensationGroupView:
    group_id: str
    task_id: str
    plan_id: str
    plan_revision: int
    project_id: str | None
    automation: str
    failure_mode: str
    require_human_approval: bool
    manual_intervention_required: bool
    actions: tuple[CompensationActionView, ...]


class CompensationControlPlaneProjection:
    """Project compensation state without exposing stored invocation arguments."""

    def __init__(self, repository: CompensationRepository) -> None:
        self.repository = repository

    def get_group(self, group_id: str) -> CompensationGroupView:
        group = self.repository.get_group(group_id)
        requests = {
            request.action_id: request for request in self.repository.list_requests(group.group_id)
        }
        views: list[CompensationActionView] = []
        for action in self.repository.list_actions(group.group_id):
            request = requests.get(action.action_id)
            result = (
                None
                if request is None
                else self.repository.get_result(request.compensation_id)
            )
            descriptor = action.compensation
            views.append(
                CompensationActionView(
                    action_id=action.action_id,
                    group_id=action.group_id,
                    task_id=action.task_id,
                    plan_id=action.plan_id,
                    plan_revision=action.plan_revision,
                    step_id=action.step_id,
                    original_run_id=action.run_id,
                    original_tool_invocation_id=action.tool_invocation_id,
                    original_capability_id=action.capability_id,
                    reversibility=action.reversibility.value,
                    compensating_capability_id=(
                        None if descriptor is None else descriptor.capability_id
                    ),
                    compensation_id=None if request is None else request.compensation_id,
                    status=None if result is None else result.status,
                    compensation_run_id=None if result is None else result.execution_run_id,
                    compensation_tool_invocation_id=(
                        None if result is None else result.canonical_tool_invocation_id
                    ),
                    approval_id=None if result is None else result.approval_id,
                    original_result_ref=action.original_result_ref,
                    external_resource_ref=action.external_resource_ref,
                    result_ref=None if result is None else result.result_ref,
                    artifact_refs=() if result is None else result.artifact_refs,
                    evidence_refs=(
                        action.evidence_refs if result is None else result.evidence_refs
                    ),
                    error_code=None if result is None else result.error_code,
                    error_message=None if result is None else result.error_message,
                    manual_intervention_required=(
                        False if result is None else result.manual_intervention_required
                    ),
                )
            )
        return CompensationGroupView(
            group_id=group.group_id,
            task_id=group.task_id,
            plan_id=group.plan_id,
            plan_revision=group.plan_revision,
            project_id=group.project_id,
            automation=group.policy.automation.value,
            failure_mode=group.policy.failure_mode.value,
            require_human_approval=group.policy.require_human_approval,
            manual_intervention_required=any(
                view.manual_intervention_required for view in views
            ),
            actions=tuple(views),
        )

    def list_for_plan(self, plan_id: str) -> tuple[CompensationGroupView, ...]:
        return tuple(
            self.get_group(group.group_id)
            for group in self.repository.list_groups_for_plan(plan_id)
        )
