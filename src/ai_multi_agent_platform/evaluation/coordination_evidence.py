"""Evaluation evidence projection for platform-owned durable coordination."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.coordination.models import CoordinationPhase
from ai_multi_agent_platform.coordination.repository import CoordinatorRepository

from .evidence import EvaluationEvidence


class CoordinationEvaluationEvidenceProvider:
    """Project backend-neutral coordinator state into the existing #19 evidence model."""

    def __init__(self, repository: CoordinatorRepository) -> None:
        self._repository = repository

    def collect(self, *, task_id: str, run_id: str) -> EvaluationEvidence:
        matches = tuple(
            state
            for state in self._repository.list_active_plans()
            if state.plan.task_id == task_id and state.plan.active
        )
        if not matches:
            return EvaluationEvidence()
        if len(matches) != 1:
            raise ValueError(f"multiple active coordination Plans found for task {task_id}")

        state = matches[0]
        records = {
            record.step_id: record for record in self._repository.list_step_records(state.plan.id)
        }
        step_data: list[JsonValue] = []
        for step in state.steps:
            record = records[step.id]
            wait = record.wait
            step_data.append(
                {
                    "step_id": step.id,
                    "status": step.status.value,
                    "phase": record.phase.value,
                    "dependency_ids": list(record.dependency_ids),
                    "satisfied_dependency_ids": list(record.satisfied_dependency_ids),
                    "latest_run_id": record.latest_run_id,
                    "current_attempt": record.current_attempt,
                    "retry_policy_version": record.retry_policy.version,
                    "retry_due_at": (
                        record.retry_due_at.isoformat() if record.retry_due_at is not None else None
                    ),
                    "wait_type": wait.wait_type.value if wait is not None else None,
                    "wait_deadline_at": (
                        wait.deadline_at.isoformat()
                        if wait is not None and wait.deadline_at is not None
                        else None
                    ),
                    "reconciliation": record.reconciliation.value,
                }
            )

        waiting = sum(record.phase is CoordinationPhase.WAITING for record in records.values())
        retry_scheduled = sum(
            record.phase is CoordinationPhase.RETRY_SCHEDULED for record in records.values()
        )
        inconsistent = sum(
            record.phase is CoordinationPhase.INCONSISTENT for record in records.values()
        )
        partial_barriers = sum(
            bool(record.satisfied_dependency_ids)
            and set(record.satisfied_dependency_ids) != set(record.dependency_ids)
            for record in records.values()
        )

        return EvaluationEvidence(
            data={
                "coordination_evidence": {
                    "task_id": task_id,
                    "observed_run_id": run_id,
                    "plan_id": state.plan.id,
                    "plan_revision": state.plan.revision,
                    "store_revision": state.store_revision,
                    "steps": step_data,
                }
            },
            metrics={
                "coordination:step_count": float(len(state.steps)),
                "coordination:waiting_steps": float(waiting),
                "coordination:retry_scheduled_steps": float(retry_scheduled),
                "coordination:inconsistent_steps": float(inconsistent),
                "coordination:partial_barriers": float(partial_barriers),
            },
        )
